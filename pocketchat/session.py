"""The conversation session: one entry point, one trace path.

Every command that talks to a model goes through :meth:`Session.run`, and that
is the whole design. There are several early-exit routes out of a turn -- Ctrl-C,
a provider error, a context that will not fit -- and the terminal trace event is
written once, in the ``finally`` of that single exit, so no entry point can
produce a run that starts and never ends. A trace carrying a ``run.started`` and
no terminal event is the one shape the tooling this package feeds complains
about first.
"""

from __future__ import annotations

import os
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ConfigError
from .config import load_api_key
from .context import ContextBudgetExceeded, apply_budget, build_initial_messages
from .context import context_chars as _context_chars
from .context_projection import ContextProjectionError, project_context
from .provider import DEFAULT_PROVIDER_ADAPTER, ProviderError
from .provider import estimate_tokens, tool_offer_summary
from .request_admission import AdmissionError, admit_for_config
from .task_budget import TaskBudgetError
from .tool_results import ResultProjectionError, project_result
from .memory import search_memory
from .tools import (ToolError, ToolPermissionError,
                    execute_read_file, execute_write_file)
from .request import (CredentialInRequestError, canonical_bytes,
                     next_request_turn, request_digest,
                     request_snapshot_path, write_request_snapshot)
from .sessions import SessionFile, SessionFileError
from .trace import TraceRecorder, utc_now

import pocketchat

RUN_ID_PATTERN_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"

REFUSED_TOOL_ERRORS = frozenset({
    "not_allowlisted", "unsupported_tool", "denylisted_path", "binary_file",
    "exists", "permission_denied", "path_escape",
})


def new_run_id() -> str:
    """Build a run id that is unique, readable and safe in a filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
    token = os.urandom(4).hex()
    return "run_%s_%s" % (stamp, token)


def _tool_result_state(result: str) -> str:
    try:
        payload = json.loads(result)
    except ValueError:
        return "failed"
    if not isinstance(payload, dict):
        return "failed"
    if payload.get("status") == "failed":
        error = payload.get("error") or {}
        if error.get("type") in REFUSED_TOOL_ERRORS:
            return "refused"
        return "failed"
    return "completed"


def _tool_result_detail(call: Dict[str, Any], result: str) -> str:
    try:
        payload = json.loads(result)
    except ValueError:
        return str(call.get("name") or "tool")
    if isinstance(payload, dict) and payload.get("status") == "failed":
        error = payload.get("error") or {}
        return str(error.get("type") or "tool_failed")
    arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    return str(arguments.get("path") or arguments.get("query") or call.get("name") or "tool")


def _elapsed_ms(started_iso: str) -> int:
    """Milliseconds since an ISO timestamp, floored at zero."""
    try:
        started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    delta = datetime.now(timezone.utc) - started
    return max(0, int(delta.total_seconds() * 1000))


def _nonnegative_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return default
    return value


def _attempt_budget(config: Dict[str, Any], key: str, default: int) -> int:
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return min(value, 5)


def _summarize(text: str, prefix_chars: int) -> str:
    """The default response record: a length plus a short prefix.

    Recording only the prefix keeps a trace from carrying a whole response body
    by default while still showing what the model actually said. The full body
    is the ``--verbose`` choice, made by the operator, not by this package.
    """
    if prefix_chars <= 0:
        return "(%d characters)" % len(text)
    head = text[:prefix_chars]
    if len(text) > prefix_chars:
        head = head.rstrip() + "..."
    return head


def _sanitize_run_id(run_id: str) -> str:
    cleaned = "".join(
        ch if ch in RUN_ID_PATTERN_CHARS else "_" for ch in run_id.strip().lower()
    ).strip("_")
    if not cleaned:
        raise ConfigError("session id must contain at least one usable character")
    if len(cleaned) < 3 or len(cleaned) > 64:
        raise ConfigError("session id must be 3 to 64 characters long")
    return cleaned


class SessionError(Exception):
    """The run could not continue. Carries the error class used in the trace."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class TokenBudgetExceeded(SessionError):
    """The configured token budget would be exceeded."""


def _bounded_count(config: Dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionError("invalid_tool_limit", "%s must be a positive integer" % key)
    return value


def _token_budget(config: Dict[str, Any], key: str) -> Optional[int]:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SessionError("invalid_token_budget", "%s must be a positive integer" % key)
    return value


class Session:
    """One run: a config, a context, and the trace that records both.

    ``run_id`` doubles as the session id, so resuming a session resumes its
    trace: the run id is the link between ``chat``, ``send``, ``show`` and
    ``export``. The trace is opened once and appended to for the life of the
    session.

    A ``session_path`` opens the companion conversation file. An existing one
    is the authority: its run id and trace path are adopted, and its messages
    seed the context, so a resumed conversation continues the same run rather
    than starting a second one beside it.
    """

    def __init__(self, config: Dict[str, Any], trace_path: Path,
                 run_id: Optional[str] = None, session_path: Optional[Path] = None,
                 verbose: bool = False, api_key: Optional[str] = None,
                 initial_messages: Optional[List[Dict[str, Any]]] = None,
                 on_delta=None, task_observer=None) -> None:
        self.config = config
        self.verbose = verbose
        loaded_key = api_key if api_key is not None else load_api_key(config)
        self.api_key = loaded_key or ""
        self.provider_adapter = config.get("provider_adapter") or DEFAULT_PROVIDER_ADAPTER
        self.on_delta = on_delta
        self.task_observer = task_observer
        self.task_budget = None
        self.task_budget_request_id = ""

        self.session_file: Optional[SessionFile] = None
        if session_path is not None:
            try:
                self.session_file = SessionFile.open(
                    Path(session_path),
                    run_id or new_run_id(),
                    Path(trace_path),
                    pocketchat.__version__,
                )
            except SessionFileError as exc:
                raise ConfigError(str(exc))
        if self.session_file is not None:
            # The existing file decides: a resumed run keeps its original run id
            # and trace, and starting over would put a second run id in a file
            # the validator reads as one run.
            self.run_id = _sanitize_run_id(self.session_file.run_id)
            self.trace_path = Path(self.session_file.trace_path)
            resumed = self.session_file.resumed
            restored = list(self.session_file.context_messages)
        else:
            self.run_id = _sanitize_run_id(run_id or new_run_id())
            self.trace_path = Path(trace_path)
            resumed = False
            restored = []

        self.recorder = TraceRecorder(self.trace_path, self.run_id)
        self.messages: List[Dict[str, Any]] = build_initial_messages(
            self.config.get("system_prompt") or ""
        )
        # A caller may seed the context with messages of its own -- a
        # comparison hands every profile the same base messages, so the runs
        # differ only by profile. They are copied here rather than shared,
        # because a run that trims or extends its context must not change what
        # the next one starts from.
        self.messages.extend(copy.deepcopy(list(initial_messages or [])))
        self.messages.extend(
            m for m in restored if m.get("role") in ("user", "assistant", "tool")
        )
        self.resumed = resumed
        self.turn_count = 0
        # A resumed session continues the numbering the trace already used, so
        # its snapshots extend the run instead of colliding with it.
        self.request_count = max(0, next_request_turn(self.trace_path) - 1)
        # A physical request ordinal is scoped to one turn: it starts at 1
        # again for each prompt, and is paired with the result event for that
        # request. request_count remains the trace-wide snapshot counter.
        self.turn_attempt_count = 0
        self.request_snapshots: List[Path] = []
        self.request_digests: List[str] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.compacted_count = 0
        self.finished = False
        self.last_output_summary = ""
        self.last_error: Optional[Dict[str, str]] = None
        self.last_reason = ""
        self._context_projection: Optional[Dict[str, Any]] = None
        self._projection_sidecar = ""

    def config_summary(self) -> Dict[str, Any]:
        """The run configuration as the recorder sees it.

        It carries the environment variable *name*, never the value. A trace
        that recorded the key would be a credential on disk.
        """
        # The profile name is recorded alongside the model because two
        # profiles can point at the same model: without the name, a run's
        # config_summary would not say which of them ran.
        profile = self.config.get("profile")
        return {
            "profile": profile if isinstance(profile, str) else "",
            "model": self.config["model"],
            "base_url": self.config["base_url"],
            "timeout_seconds": self.config["timeout_seconds"],
            "max_context_chars": self.config["max_context_chars"],
            "api_key_env": self.config["api_key_env"],
            "provider_adapter": self.provider_adapter.name,
            "reasoning_effort": self.config.get("reasoning_effort") or "",
            "retry_max_attempts": _attempt_budget(
                self.config, "retry_max_attempts", 1),
            "retry_backoff_ms": _nonnegative_int(
                self.config.get("retry_backoff_ms"), 0),
            "fallback_max_attempts": _attempt_budget(
                self.config, "fallback_max_attempts", 1),
            "fallback_profile": self.config.get("fallback_profile") or "",
            "max_turn_tokens": self.config.get("max_turn_tokens"),
            "max_session_tokens": self.config.get("max_session_tokens"),
            "context_projection_enabled": self.config.get(
                "context_projection_enabled") is True,
            "context_projection_max_bytes": self.config.get(
                "context_projection_max_bytes", 65536),
            "tools": tool_offer_summary(self.config),
            "verbose": self.verbose,
            "pocketchat_version": pocketchat.__version__,
        }

    def run(self, prompts) -> str:
        """Run one session: emit every trace event for it and return a status.

        Returns one of the run statuses ``completed``, ``failed`` or
        ``cancelled``. ``KeyboardInterrupt`` and :class:`SessionError` are
        caught here, not by the callers, so the trace always gets a terminal
        event and the process exits with the matching code.

        ``prompts`` is any iterable of strings, including a generator. The
        interactive REPL passes a generator because it does not know how many
        lines the user will type, and the count is not known until the
        conversation ends. That is why ``run.started`` describes the source
        rather than a count: a count would have to consume the iterable before
        the first turn, which is exactly the moment an interrupt can land.
        """
        prompts = iter(prompts)
        started = self.recorder.append(
            "run.started",
            {
                "input_summary": self._describe_input(prompts),
                "config_summary": self.config_summary(),
            },
        )
        status = "completed"
        error: Optional[Dict[str, str]] = None
        reason = ""
        try:
            for prompt in prompts:
                self._turn(prompt)
        except KeyboardInterrupt:
            status = "cancelled"
            reason = "interrupted by the user"
        except SessionError as exc:
            status = "failed"
            error = {"type": exc.error_type, "message": exc.message}
        except OSError as exc:
            status = "failed"
            error = {"type": "io_error", "message": str(exc)}
        except Exception as exc:
            # Anything unclassified still ends the run, and it ends it as a
            # failure. Leaving the status at "completed" would write a trace
            # that says the run succeeded, which is the one failure mode this
            # package exists to prevent.
            status = "failed"
            error = {
                "type": "internal_error",
                "message": "%s: %s" % (type(exc).__name__, exc),
            }
        finally:
            self.last_error = error
            self.last_reason = reason
            self._close(status, error, reason, started["event_id"])
        return status

    def _describe_input(self, prompts: List[str]) -> str:
        """Describe where the prompts came from, not how many there are.

        A non-interactive run passes a list, so the count is real. An
        interactive one passes a generator whose length is unknown until it
        ends, and consuming it to find out would read the user's input before
        the first turn -- the one place an interrupt must not be swallowed.
        """
        if isinstance(prompts, (list, tuple)):
            if not prompts:
                return "no prompts supplied"
            if len(prompts) == 1:
                return "1 prompt"
            return "%d prompts" % len(prompts)
        return "interactive prompts"

    def _close(self, status: str, error: Optional[Dict[str, str]], reason: str,
               related_event_id: str) -> None:
        """Write the one terminal event this run gets.

        The conversation file is already complete by this point: every
        completed turn appended its own exchange as it happened. So the order
        is not arbitrary. A crash while writing a session line leaves the trace
        with no terminal event, which reads as "we do not know how this ended"
        and is recoverable. A crash while writing this event leaves a file that
        is short by one exchange, which reads as the truth. Of the two, only
        the second is safe to leave behind.
        """
        payload: Dict[str, Any] = {"status": status}
        if error is not None:
            payload["error"] = error
        if reason:
            payload["reason"] = reason
        payload["turns"] = self.turn_count
        payload["total_input_tokens"] = self.total_input_tokens
        payload["total_output_tokens"] = self.total_output_tokens
        payload["compactions"] = self.compacted_count
        if self.session_file is not None:
            payload["session"] = str(self.session_file.path)
            payload["resumed"] = self.session_file.resumed
        payload["related_event_id"] = related_event_id
        if self.task_observer is not None:
            payload["task_id"] = self.task_observer.task_id
            payload["task_state"] = self.task_observer.before_terminal(
                status, error, reason)
        event_type = {
            "completed": "run.finished",
            "failed": "run.failed",
            "cancelled": "run.cancelled",
        }[status]
        self.recorder.append(event_type, payload)
        self.finished = True

    def _turn(self, prompt: str) -> None:
        """Run one turn: request, response, and the events that bind them.

        The order is deliberate. ``model.request.started`` goes out before the
        socket is opened, so an interrupted request leaves the trace showing a
        request that never completed rather than one that never started. The
        assistant reply joins the context only after a successful response: a
        failed turn contributes nothing to what the model sees next round,
        because its failure is not information the model can act on.
        """
        self.turn_attempt_count = 0
        self._context_projection = None
        self._projection_sidecar = ""
        if self.config.get("requires_api_key", True) is not False and not self.api_key:
            raise SessionError(
                "missing_credential",
                "%s is not set" % self.config["api_key_env"],
            )

        # The pending prompt is part of the context being budgeted, so the
        # trim that happens here is decided with it included. A prompt is never
        # the thing that gets dropped to make room for itself.
        try:
            context = self._context_with_budget(prompt)
        except ContextBudgetExceeded as exc:
            # The prompt plus the system prompt does not fit the budget. That
            # is a configuration fact, not something to trim harder: the run
            # stops rather than sending a context the operator forbade.
            raise SessionError("context_too_large", str(exc))
        estimated_turn = estimate_tokens(context)
        turn_budget = _token_budget(self.config, "max_turn_tokens")
        session_budget = _token_budget(self.config, "max_session_tokens")
        if turn_budget is not None and estimated_turn > turn_budget:
            raise TokenBudgetExceeded(
                "token_budget_exceeded",
                "estimated turn tokens %d exceed max_turn_tokens %d"
                % (estimated_turn, turn_budget))
        used_tokens = self.total_input_tokens + self.total_output_tokens
        if session_budget is not None and used_tokens + estimated_turn > session_budget:
            raise TokenBudgetExceeded(
                "token_budget_exceeded",
                "reported usage %d plus estimated turn tokens %d exceed "
                "max_session_tokens %d"
                % (used_tokens, estimated_turn, session_budget))
        phase_config = self.config
        phase_api_key = self.api_key
        phase_attempts = _attempt_budget(phase_config, "retry_max_attempts", 1)
        is_primary = True
        retry_index = 0
        while True:
            try:
                self._complete_attempt(
                    prompt, context, phase_config, phase_api_key,
                    turn_budget=turn_budget, session_budget=session_budget,
                    used_tokens=used_tokens)
                return
            except ProviderError as exc:
                if not exc.retryable:
                    raise SessionError(exc.error_type, exc.message)
                if phase_attempts > 1:
                    retry_index += 1
                    from_attempt = self.turn_attempt_count
                    to_attempt = from_attempt + 1
                    backoff_ms = _nonnegative_int(
                        phase_config.get("retry_backoff_ms"), 0)
                    self.recorder.append("retry.attempt", {
                        "profile": phase_config.get("profile") or "",
                        "model": phase_config["model"],
                        "retry_index": retry_index,
                        "from_attempt_index": from_attempt,
                        "to_attempt_index": to_attempt,
                        "backoff_ms": backoff_ms,
                        "error": exc.as_trace_error(),
                        "retryable": True,
                    })
                    phase_attempts -= 1
                    time.sleep(backoff_ms / 1000.0)
                    continue
                fallback = (phase_config.get("fallback_config")
                            if is_primary else None)
                if not isinstance(fallback, dict):
                    raise SessionError(exc.error_type, exc.message)
                from_attempt = self.turn_attempt_count
                to_attempt = from_attempt + 1
                self.recorder.append("fallback.activated", {
                    "from_profile": phase_config.get("profile") or "",
                    "to_profile": fallback.get("profile") or "",
                    "from_model": phase_config["model"],
                    "to_model": fallback.get("model") or "",
                    "from_attempt_index": from_attempt,
                    "to_attempt_index": to_attempt,
                    "primary_attempts": _attempt_budget(
                        self.config, "retry_max_attempts", 1),
                    "fallback_attempts": _attempt_budget(
                        fallback, "retry_max_attempts", 1),
                    "error": exc.as_trace_error(),
                    "retryable": True,
                    "reason": exc.error_type,
                })
                phase_config = fallback
                phase_api_key = load_api_key(fallback) or ""
                if (fallback.get("requires_api_key", True) is not False
                        and not phase_api_key):
                    raise SessionError(
                        "fallback_missing_credential",
                        "%s is not set" % fallback.get("api_key_env", ""),
                    )
                phase_attempts = _attempt_budget(
                    self.config, "fallback_max_attempts", 1)
                turn_budget = _token_budget(fallback, "max_turn_tokens")
                session_budget = _token_budget(fallback, "max_session_tokens")
                if (turn_budget is not None
                        and estimated_turn > turn_budget):
                    raise TokenBudgetExceeded(
                        "token_budget_exceeded",
                        "estimated fallback turn tokens %d exceed "
                        "max_turn_tokens %d" % (estimated_turn, turn_budget))
                is_primary = False
                retry_index = 0

    def _complete_attempt(self, prompt: str, context: List[Dict[str, Any]],
                          config: Dict[str, Any], api_key: str,
                          turn_budget: Optional[int], session_budget: Optional[int],
                          used_tokens: int) -> str:
        """Send one turn, following only enabled local tool calls in that turn."""
        working_context = list(context)
        turn_usage = 0
        tool_calls_executed = 0
        tool_call_limit = _bounded_count(config, "tool_call_max_count", 2)
        tool_result_limit = _bounded_count(config, "tool_result_max_bytes", 16 * 1024)
        while True:
            self.request_count += 1
            self.turn_attempt_count += 1
            attempt_index = self.turn_attempt_count
            snapshot_ref = ""
            snapshot_digest = ""
            try:
                payload = self.provider_adapter.build_payload(
                    working_context, config)
                rebuilt = self.provider_adapter.build_payload(
                    working_context, config)
                if request_digest(payload) != request_digest(rebuilt):
                    raise SessionError(
                        "request_payload_mismatch",
                        "provider rebuilt a different request payload")
                admitted = admit_for_config(
                    payload,
                    {**config, "max_input_tokens": turn_budget},
                )
                if self.task_budget is not None:
                    self.task_budget_request_id = (
                        f"{self.run_id}:request:{self.request_count}")
                    try:
                        self.task_budget.reserve(
                            admitted["estimated_input_tokens"]
                            + int(config.get("output_reserve_tokens") or 0),
                            request_id=self.task_budget_request_id)
                    except TaskBudgetError as exc:
                        raise SessionError(exc.error_type, str(exc)) from exc
                snapshot_digest = admitted["payload_sha256"]
                snapshot_ref = self._write_request_snapshot(
                    working_context, self.request_count, config=config,
                    api_key=api_key, payload=payload)
            except AdmissionError as exc:
                raise SessionError(exc.error_type, str(exc)) from exc
            except OSError as exc:
                if self._context_projection is not None:
                    raise SessionError(
                        "request_snapshot_unwritable",
                        "request snapshot not written: %s" % exc)
                self.recorder.append("error.recorded", {
                    "error": {"type": "snapshot_unwritable",
                              "message": "request snapshot not written: %s" % exc},
                    "fatal": False,
                })
            except CredentialInRequestError as exc:
                if self._context_projection is not None:
                    raise SessionError("credential_in_request", str(exc))
                self.recorder.append("error.recorded", {
                    "error": {"type": "credential_in_request",
                              "message": str(exc)},
                    "fatal": False,
                })
            if self._context_projection is not None:
                try:
                    self._write_projection_sidecar(
                        request_snapshot_path(self.trace_path,
                                              self.request_count),
                        self.request_count, payload, snapshot_digest)
                except OSError as exc:
                    raise SessionError(
                        "context_projection_sidecar_unwritable",
                        "context projection sidecar not written: %s" % exc)
            started_payload = {
                "model": config["model"],
                "messages": len(working_context),
                "role": "main",
                "prompt_chars": len(prompt),
                "context_chars": _context_chars(working_context),
                "budget_chars": int(config["max_context_chars"]),
                "attempt_index": attempt_index,
                "attempt_scope": "turn",
            }
            if snapshot_ref:
                started_payload["request_snapshot"] = snapshot_ref
                started_payload["request_sha256"] = snapshot_digest
            if self._context_projection is not None:
                started_payload["context_projection"] = {
                    "strategy": self._context_projection["strategy"],
                    "projection_sha256":
                        self._context_projection["projection_sha256"],
                    "source_sha256":
                        self._context_projection["source_sha256"],
                }
                if self._projection_sidecar:
                    started_payload["context_projection_sidecar"] = (
                        self._projection_sidecar)
            self._task_before_step("model")
            started = self.recorder.append("model.request.started", started_payload)
            started_at = utc_now()
            try:
                if self.on_delta is None:
                    response = self.provider_adapter.complete(
                        working_context, config, api_key,
                        prepared_payload=payload)
                else:
                    response = self.provider_adapter.complete(
                        working_context, config, api_key, on_delta=self.on_delta,
                        prepared_payload=payload)
            except ProviderError as exc:
                self.recorder.append("model.response.failed", {
                    "model": config["model"],
                    "error": exc.as_trace_error(),
                    "retryable": exc.retryable,
                    "duration_ms": _elapsed_ms(started_at),
                    "role": "main",
                    "attempt_index": attempt_index,
                    "attempt_scope": "turn",
                })
                self._task_after_step("model", "failed", detail=exc.error_type)
                if self.task_budget is not None:
                    try:
                        self.task_budget.settle(
                            self.task_budget_request_id, input_tokens=0,
                            output_tokens=0, reported=False)
                    except TaskBudgetError as budget_error:
                        raise SessionError(budget_error.error_type,
                                           str(budget_error)) from exc
                raise
            except KeyboardInterrupt:
                self.recorder.append("model.response.failed", {
                    "model": config["model"],
                    "error": {"type": "interrupted",
                              "message": "request interrupted before it completed"},
                    "retryable": False,
                    "role": "main",
                    "attempt_index": attempt_index,
                    "attempt_scope": "turn",
                })
                self._task_after_step("model", "cancelled", detail="interrupted")
                if self.task_budget is not None:
                    try:
                        self.task_budget.settle(
                            self.task_budget_request_id, input_tokens=0,
                            output_tokens=0, reported=False)
                    except TaskBudgetError:
                        pass  # Keep the unknown reservation and the interrupt.
                raise

            usage = response.get("usage")
            try:
                input_tokens = usage["input_tokens"]
                output_tokens = usage["output_tokens"]
                if (isinstance(input_tokens, bool)
                        or not isinstance(input_tokens, int)
                        or input_tokens < 0
                        or isinstance(output_tokens, bool)
                        or not isinstance(output_tokens, int)
                        or output_tokens < 0):
                    raise ValueError("usage tokens must be non-negative integers")
            except (KeyError, TypeError, ValueError) as exc:
                if self.task_budget is not None:
                    try:
                        self.task_budget.settle(
                            self.task_budget_request_id, input_tokens=0,
                            output_tokens=0, reported=False)
                    except TaskBudgetError as budget_error:
                        raise SessionError(
                            budget_error.error_type,
                            str(budget_error)) from exc
                raise SessionError(
                    "invalid_provider_response",
                    "provider returned malformed token usage") from exc
            actual_turn = input_tokens + output_tokens
            payload: Dict[str, Any] = {
                "model": config["model"],
                "usage": usage,
                "usage_reported": bool(response.get("usage_reported", False)),
                "duration_ms": _elapsed_ms(started_at),
                "finish_reason": response["finish_reason"] or "stop",
                "role": "main",
                "capture": "inline",
                "output_summary": response["content"] if self.verbose else _summarize(
                    response["content"], config["summary_prefix_chars"]
                ),
                "related_event_id": started["event_id"],
                "attempt_index": attempt_index,
                "attempt_scope": "turn",
            }
            ttft = response.get("time_to_first_token_ms")
            if isinstance(ttft, int) and ttft >= 0:
                payload["time_to_first_token_ms"] = ttft
            for key in ("first_delta_kind", "time_to_first_reasoning_ms",
                        "time_to_first_content_ms"):
                if key in response:
                    payload[key] = response[key]
            self.recorder.append("model.response.completed", payload)
            if self.task_budget is not None:
                try:
                    self.task_budget.settle(
                        self.task_budget_request_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        reported=bool(response.get("usage_reported", False)),
                    )
                except TaskBudgetError as exc:
                    raise SessionError(exc.error_type, str(exc)) from exc
            if turn_budget is not None and turn_usage + actual_turn > turn_budget:
                raise TokenBudgetExceeded(
                    "token_budget_exceeded",
                    "reported turn tokens %d exceed max_turn_tokens %d"
                    % (turn_usage + actual_turn, turn_budget))
            if session_budget is not None and not bool(response.get("usage_reported", False)):
                raise SessionError(
                    "token_usage_unknown",
                    "provider did not report usage required by max_session_tokens")
            if (session_budget is not None
                    and used_tokens + turn_usage + actual_turn > session_budget):
                raise TokenBudgetExceeded(
                    "token_budget_exceeded",
                    "reported session tokens would reach %d, above max_session_tokens %d"
                    % (used_tokens + turn_usage + actual_turn, session_budget))
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            turn_usage += actual_turn
            self._task_after_step("model", "completed",
                                  detail=config["model"])
            tool_calls = response.get("tool_calls") or []
            if not tool_calls:
                self.turn_count += 1
                tool_context = copy.deepcopy(working_context[len(context):])
                self.messages.append({"role": "user", "content": prompt})
                self.messages.extend(tool_context)
                self.messages.append({"role": "assistant", "content": response["content"]})
                self._record_turn(prompt, response["content"], tool_context)
                self.last_output_summary = payload["output_summary"]
                return response["content"]
            if tool_calls_executed + len(tool_calls) > tool_call_limit:
                raise SessionError(
                    "tool_call_limit",
                    "tool call budget %d exceeded" % tool_call_limit)
            assistant_tool_message = {
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": [{
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(
                            call["arguments"], sort_keys=True),
                    },
                } for call in tool_calls],
            }
            working_context.append(assistant_tool_message)
            for call in tool_calls:
                self._task_before_step("tool", tool=str(call.get("name") or ""))
                result = self._execute_model_tool_call(
                    call, config, tool_result_limit)
                self._task_after_step(
                    "tool", _tool_result_state(result),
                    tool=str(call.get("name") or ""),
                    detail=_tool_result_detail(call, result))
                working_context.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": result,
                })
                tool_calls_executed += 1

    def _task_before_step(self, kind: str, tool: Optional[str] = None) -> None:
        if self.task_observer is not None:
            self.task_observer.before_step(kind, tool=tool)

    def _task_after_step(self, kind: str, state: str, *,
                         tool: Optional[str] = None,
                         detail: Optional[str] = None) -> None:
        if self.task_observer is not None:
            self.task_observer.after_step(
                kind, state, tool=tool, detail=detail, run_id=self.run_id)

    def _execute_model_tool_call(self, call: Dict[str, Any],
                                 config: Dict[str, Any],
                                 result_limit: int) -> str:
        tool_name = call.get("name")
        enabled = ((tool_name == "read_file"
                    and config.get("tool_read_enabled") is True)
                   or (tool_name == "write_file"
                       and config.get("tool_write_enabled") is True)
                   or (tool_name == "memory_search"
                       and config.get("tool_memory_search_enabled") is True))
        if not enabled:
            self.recorder.append("tool.call.failed", {
                "tool": str(tool_name or ""),
                "tool_call_id": str(call.get("id") or ""),
                "status": "failed",
                "error": {"type": "unsupported_tool",
                          "message": "tool is disabled for this profile"},
                "duration_ms": 0,
                "guardrail_refusal": True,
            })
            return self._tool_failure_result(
                call, "unsupported_tool",
                "tool is disabled for this profile", result_limit)
        if tool_name not in ("read_file", "write_file", "memory_search"):
            self.recorder.append("tool.call.failed", {
                "tool": str(call.get("name") or ""),
                "tool_call_id": str(call.get("id") or ""),
                "status": "failed",
                "error": {"type": "unsupported_tool",
                          "message": "tool is not in the supported set"},
                "duration_ms": 0,
                "guardrail_refusal": True,
            })
            return self._tool_failure_result(
                call, "unsupported_tool",
                "tool is not in the supported set", result_limit)
        arguments = call.get("arguments")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        workspace = Path.cwd()
        if config.get("task_workspace_root"):
            workspace = Path(config["task_workspace_root"])
        elif self.trace_path.parent.name == "traces":
            workspace = self.trace_path.parent.parent
        try:
            if tool_name == "read_file":
                if not isinstance(path, str) or not path.strip():
                    return self._record_invalid_arguments(
                        call, "read_file requires a path", result_limit)
                result = execute_read_file(
                    workspace, path,
                    allowed_paths=config.get("tool_read_allow_paths") or [],
                    max_bytes=result_limit,
                    recorder=self.recorder,
                    tool_call_id=str(call.get("id") or ""),
                )
            elif tool_name == "write_file":
                if not isinstance(path, str) or not path.strip():
                    return self._record_invalid_arguments(
                        call, "write_file requires a path", result_limit)
                content = arguments.get("content") if isinstance(arguments, dict) else None
                if not isinstance(content, str):
                    return self._record_invalid_arguments(
                        call, "write_file requires content", result_limit)
                result = execute_write_file(
                    workspace, path, content,
                    allowed_paths=config.get("tool_write_allow_paths") or [],
                    overwrite=False,
                    recorder=self.recorder,
                    tool_call_id=str(call.get("id") or ""),
                )
            else:
                query = arguments.get("query") if isinstance(arguments, dict) else None
                if not isinstance(query, str) or not query:
                    return self._record_invalid_arguments(
                        call, "memory_search requires a query", result_limit)
                self.recorder.append("permission.requested", {
                    "action": "search", "resource": query,
                    "tool": "memory_search",
                })
                self.recorder.append("permission.resolved", {
                    "decision": "approved", "decided_by": "workspace_policy",
                })
                self.recorder.append("tool.call.started", {
                    "tool": "memory_search",
                    "tool_call_id": str(call.get("id") or ""),
                    "args_summary": "search memory",
                })
                all_items = search_memory(workspace, query)
                items = all_items[:_bounded_count(
                    config, "tool_memory_search_max_results", 20)]
                def encode_items(value):
                    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"))
                result = {
                    "items": items,
                    "total_items": len(all_items),
                    "omitted_items": len(all_items) - len(items),
                    "bytes_omitted": len(encode_items(all_items).encode("utf-8"))
                    - len(encode_items(items).encode("utf-8")),
                    "truncated": len(items) < len(all_items),
                }
                bounded_memory = project_result(
                    str(call.get("id") or ""), "memory_search", result,
                    max_bytes=result_limit, effect_disposition="none")
                encoded = bounded_memory.encoded
                result = json.loads(encoded)
                self.recorder.append("tool.call.completed", {
                    "tool": "memory_search",
                    "tool_call_id": str(call.get("id") or ""),
                    "status": "completed",
                    "duration_ms": 0,
                    "result_bytes": len(encoded.encode("utf-8")),
                    "result_truncated": bool(result.get("truncated")),
                    "effect_disposition": "none",
                })
        except ResultProjectionError as exc:
            raise SessionError(exc.error_type, str(exc)) from exc
        except ToolPermissionError as exc:
            return self._tool_failure_result(
                call, exc.error_type, str(exc), result_limit)
        except ToolError as exc:
            if exc.error_type == "invalid_encoding":
                return self._tool_failure_result(
                    call, exc.error_type, str(exc), result_limit)
            raise SessionError("tool_failed", str(exc))
        try:
            if tool_name == "memory_search":
                return bounded_memory.encoded
            elif tool_name == "write_file":
                result = {
                    "path": result.get("path"),
                    "bytes": result.get("bytes"),
                    "overwritten": result.get("overwritten"),
                }
            projected = project_result(
                str(call.get("id") or ""),
                str(tool_name),
                result,
                max_bytes=result_limit,
                omit_content_echo=(tool_name == "write_file"),
                effect_disposition=("applied" if tool_name == "write_file"
                                    else "none"),
            )
            return projected.encoded
        except ResultProjectionError as exc:
            raise SessionError(exc.error_type, str(exc)) from exc

    def _record_invalid_arguments(self, call: Dict[str, Any], message: str,
                                  result_limit: int) -> str:
        self.recorder.append("tool.call.failed", {
            "tool": str(call.get("name") or ""),
            "tool_call_id": str(call.get("id") or ""),
            "status": "failed",
            "error": {"type": "invalid_arguments", "message": message},
            "duration_ms": 0,
        })
        return self._tool_failure_result(
            call, "invalid_arguments", message, result_limit)

    def _tool_failure_result(self, call: Dict[str, Any], error_type: str,
                             message: str, result_limit: int) -> str:
        try:
            projection = project_result(
                str(call.get("id") or ""),
                str(call.get("name") or "unknown"),
                {"status": "failed", "error": {
                    "type": error_type, "message": message}},
                max_bytes=result_limit,
            )
        except ResultProjectionError as exc:
            raise SessionError(exc.error_type, str(exc)) from exc
        return projection.encoded

    def _write_request_snapshot(self, context: List[Dict[str, str]],
                                turn: int, config: Optional[Dict[str, Any]] = None,
                                api_key: Optional[str] = None,
                                payload: Optional[Dict[str, Any]] = None) -> str:
        """Write the exact request body for this turn and return its path.

        The recorded path is relative to the trace itself, which is the file a
        reader has in hand: the trace is what says the request happened, and the
        snapshot is what says what it contained. Deriving it this way rather
        than from an assumed directory layout means a trace placed by
        ``--trace`` still points at the snapshot that was written beside it.

        The payload comes from the provider's own builder rather than being
        assembled here, so the snapshot cannot describe a body that differs
        from the one sent. The credential is not in the payload and never is;
        it is handed to the writer so the writer can refuse a body that
        quotes one, which this run already holds in order to send the request.
        """
        path = request_snapshot_path(self.trace_path, turn)
        config = config or self.config
        if payload is None:
            payload = self.provider_adapter.build_payload(context, config)
        digest = write_request_snapshot(
            path,
            run_id=self.run_id,
            profile=config.get("profile"),
            created=utc_now(),
            payload=payload,
            credential=self.api_key if api_key is None else api_key,
        )
        self.request_digests.append(digest)
        self.request_snapshots.append(path)
        return Path(os.path.relpath(path, self.trace_path.parent)).as_posix()

    def _write_projection_sidecar(self, snapshot: Path, turn: int,
                                  payload: Dict[str, Any],
                                  snapshot_digest: str) -> None:
        """Write a metadata-only sidecar bound to the exact request snapshot."""
        manifest = dict(self._context_projection)
        manifest["request_sha256"] = snapshot_digest
        manifest["request_snapshot"] = Path(
            os.path.relpath(snapshot, self.trace_path.parent)).as_posix()
        body = {
            "schema_version": "pocketchat-context-projection.v1",
            "run_id": self.run_id,
            "profile": self.config.get("profile") or "",
            "turn": turn,
            "payload_sha256": snapshot_digest,
            "messages": len(payload.get("messages") or []),
            "manifest": manifest,
        }
        path = snapshot.parent / (
            "%s.turn-%d.projection.json" % (self.trace_path.stem, turn))
        data = canonical_bytes(body)
        if len(data) > 64 * 1024:
            raise SessionError(
                "context_projection_sidecar_too_large",
                "context projection sidecar exceeds 64 KiB")
        descriptor = os.open(
            str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self._projection_sidecar = Path(
            os.path.relpath(path, self.trace_path.parent)).as_posix()

    def _record_turn(self, prompt: str, reply: str,
                     tool_context: Optional[List[Dict[str, Any]]] = None) -> None:
        """Write the exchange into the conversation file, one line per message.

        This runs after the response is in the trace and before the turn
        returns, so a crash between them leaves the conversation file short by
        one exchange rather than claiming an exchange the trace never saw. An
        interrupted or failed turn appends nothing, which is the same rule the
        context follows: a failed exchange is not part of what the model sees.
        """
        if self.session_file is None:
            return
        try:
            self.session_file.append("user", prompt)
            self.session_file.append("assistant", reply, tool_context=tool_context)
        except SessionFileError:
            # A conversation that cannot be written is not the same as a
            # conversation that did not happen, and the trace already carries
            # it. Failing the run here would lose a turn that succeeded.
            self.session_file = None

    def _context_with_budget(self, prompt: str) -> List[Dict[str, str]]:
        """Return the context to send and record the selection it came from.

        The pending prompt is appended first. The established character budget
        remains the text-only default. An explicitly enabled layered projection
        additionally bounds the complete projected JSON envelope; it never
        silently reinterprets the old character limit as an object-byte limit.

        Layered projection keeps the pending prompt outside the reusable
        history. It returns the selected history plus the prompt exactly once,
        which is the same shape the preview reports.
        """
        budget = int(self.config["max_context_chars"])
        context = list(self.messages) + [{"role": "user", "content": prompt}]
        layered = self.config.get("context_projection_enabled") is True
        if layered:
            projection_budget = self.config.get("context_projection_max_bytes")
            if (isinstance(projection_budget, bool)
                    or not isinstance(projection_budget, int)
                    or projection_budget < 1):
                raise SessionError(
                    "invalid_context_projection",
                    "context_projection_max_bytes must be a positive integer")
            instructions = [message for message in self.messages
                            if message.get("role") == "system"]
            history = [message for message in self.messages
                       if message.get("role") != "system"]
            pending = [{"role": "user", "content": prompt}]
            try:
                projected = project_context(
                    {"instructions": instructions, "messages": history,
                     "evidence": []},
                    max_chars=budget,
                    keep_recent_turns=1,
                    pending_messages=pending)
            except ContextProjectionError as exc:
                raise SessionError(exc.error_type, str(exc)) from exc
            projected_bytes = len(canonical_bytes(projected["messages"]))
            if projected_bytes > projection_budget:
                raise SessionError(
                    "context_projection_too_large",
                    "projected context is %d bytes, above the configured "
                    "projection budget %d" % (projected_bytes,
                                               projection_budget))
            projected_messages = projected["messages"]
            self._context_projection = projected["manifest"]
            projected_history = [
                message for message in projected_messages
                if message.get("role") != "system"][:-len(pending)]
            if projected_history != [
                    message for message in self.messages
                    if message.get("role") != "system"]:
                self.messages = projected_history
                self.compacted_count += 1
                payload = {"strategy": "layered_v1"}
                payload.update({
                    "dropped_messages": len(history) - len(projected_history),
                    "kept_messages": len(projected_messages),
                    "kept_chars": projected["manifest"]["kept_chars"],
                    "budget_chars": budget,
                    "estimated_tokens_before": estimate_tokens(context),
                    "estimated_tokens_after": estimate_tokens(projected_messages),
                    "projection_sha256":
                        projected["manifest"]["projection_sha256"],
                })
                self.recorder.append("context.compacted", payload)
            return projected_messages
        trimmed, report = apply_budget(context, budget, keep_recent=2)
        if report:
            # The pending prompt is not kept in the persisted context: it is
            # appended for real once the turn completes, so keeping it here
            # would count the same message twice. The last entry of ``trimmed``
            # is the prompt, and everything before it is the surviving history.
            self.messages = trimmed[:-1]
            self.compacted_count += 1
            payload = {"strategy": "drop_oldest_turns"}
            payload.update(report)
            self.recorder.append("context.compacted", payload)
        return trimmed

    def last_reply(self) -> str:
        """The most recent assistant reply, or an empty string."""
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                return message.get("content") or ""
        return ""
