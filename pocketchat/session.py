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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ConfigError
from .config import load_api_key
from .context import ContextBudgetExceeded, apply_budget, build_initial_messages
from .context import context_chars as _context_chars
from .provider import ProviderError, build_payload, complete
from .request import (CredentialInRequestError, next_request_turn,
                     request_snapshot_path, write_request_snapshot)
from .sessions import SessionFile, SessionFileError
from .trace import TraceRecorder, utc_now

import pocketchat

RUN_ID_PATTERN_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789_-"


def new_run_id() -> str:
    """Build a run id that is unique, readable and safe in a filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
    token = os.urandom(4).hex()
    return "run_%s_%s" % (stamp, token)


def _elapsed_ms(started_iso: str) -> int:
    """Milliseconds since an ISO timestamp, floored at zero."""
    try:
        started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
    except ValueError:
        return 0
    delta = datetime.now(timezone.utc) - started
    return max(0, int(delta.total_seconds() * 1000))


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
                 initial_messages: Optional[List[Dict[str, str]]] = None) -> None:
        self.config = config
        self.verbose = verbose
        self.api_key = api_key if api_key is not None else load_api_key(config)

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
            restored = list(self.session_file.messages)
        else:
            self.run_id = _sanitize_run_id(run_id or new_run_id())
            self.trace_path = Path(trace_path)
            resumed = False
            restored = []

        self.recorder = TraceRecorder(self.trace_path, self.run_id)
        self.messages: List[Dict[str, str]] = build_initial_messages(
            self.config.get("system_prompt") or ""
        )
        # A caller may seed the context with messages of its own -- a
        # comparison hands every profile the same base messages, so the runs
        # differ only by profile. They are copied here rather than shared,
        # because a run that trims or extends its context must not change what
        # the next one starts from.
        self.messages.extend(copy.deepcopy(list(initial_messages or [])))
        self.messages.extend(
            m for m in restored if m.get("role") in ("user", "assistant")
        )
        self.resumed = resumed
        self.turn_count = 0
        # A resumed session continues the numbering the trace already used, so
        # its snapshots extend the run instead of colliding with it.
        self.request_count = max(0, next_request_turn(self.trace_path) - 1)
        self.request_snapshots: List[Path] = []
        self.request_digests: List[str] = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.compacted_count = 0
        self.finished = False
        self.last_output_summary = ""

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
        if not self.api_key:
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
        # The snapshot is written before the socket opens, so a request that
        # never went out leaves no file claiming one did, and a request that
        # did go out is recorded even if it failed on the wire.
        self.request_count += 1
        snapshot_ref = ""
        snapshot_digest = ""
        try:
            snapshot_ref = self._write_request_snapshot(context, self.request_count)
            snapshot_digest = self.request_digests[-1]
        except OSError as exc:
            # A snapshot that cannot be written is not a reason to stop the
            # run: the trace still records the request. What it must not do is
            # point at a file that is not there, so the reference is left out
            # and the failure is recorded as an event a reader can see.
            self.recorder.append("error.recorded", {
                "error": {"type": "snapshot_unwritable",
                          "message": "request snapshot not written: %s" % exc},
                "fatal": False,
            })
        except CredentialInRequestError as exc:
            # The operator's own words carried the credential, so the body
            # cannot be written down. The request itself still goes out --
            # stopping the conversation would not un-send anything -- but the
            # snapshot is refused, and the trace records why it is missing
            # rather than leaving a gap a reader has to guess at.
            self.recorder.append("error.recorded", {
                "error": {"type": "credential_in_request",
                          "message": str(exc)},
                "fatal": False,
            })
        started_payload = {
            "model": self.config["model"],
            "messages": len(context),
            "role": "main",
            "prompt_chars": len(prompt),
            "context_chars": _context_chars(context),
            "budget_chars": int(self.config["max_context_chars"]),
        }
        if snapshot_ref:
            started_payload["request_snapshot"] = snapshot_ref
            started_payload["request_sha256"] = snapshot_digest
        started = self.recorder.append("model.request.started", started_payload)
        started_at = utc_now()
        try:
            response = complete(context, self.config, self.api_key)
        except ProviderError as exc:
            self.recorder.append(
                "model.response.failed",
                {
                    "model": self.config["model"],
                    "error": exc.as_trace_error(),
                    "duration_ms": _elapsed_ms(started_at),
                    "role": "main",
                },
            )
            raise SessionError(exc.error_type, exc.message)
        except KeyboardInterrupt:
            # The request was in flight when the user interrupted. run() writes
            # the terminal event; what has to be recorded here is that the
            # in-flight request never produced a response.
            self.recorder.append(
                "model.response.failed",
                {
                    "model": self.config["model"],
                    "error": {
                        "type": "interrupted",
                        "message": "request interrupted before it completed",
                    },
                    "role": "main",
                },
            )
            raise

        usage = response["usage"]
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        self.turn_count += 1

        self.messages.append({"role": "user", "content": prompt})
        self.messages.append({"role": "assistant", "content": response["content"]})
        self._record_turn(prompt, response["content"])

        payload: Dict[str, Any] = {
            "model": self.config["model"],
            "usage": usage,
            "duration_ms": _elapsed_ms(started_at),
            "finish_reason": response["finish_reason"] or "stop",
            "role": "main",
            "capture": "inline",
            "output_summary": response["content"] if self.verbose else _summarize(
                response["content"], self.config["summary_prefix_chars"]
            ),
            "related_event_id": started["event_id"],
        }
        self.recorder.append("model.response.completed", payload)
        self.last_output_summary = payload["output_summary"]
        return response["content"]

    def _write_request_snapshot(self, context: List[Dict[str, str]],
                                turn: int) -> str:
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
        payload = build_payload(context, self.config)
        digest = write_request_snapshot(
            path,
            run_id=self.run_id,
            profile=self.config.get("profile"),
            created=utc_now(),
            payload=payload,
            credential=self.api_key,
        )
        self.request_digests.append(digest)
        self.request_snapshots.append(path)
        return os.path.relpath(path, self.trace_path.parent)

    def _record_turn(self, prompt: str, reply: str) -> None:
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
            self.session_file.append("assistant", reply)
        except SessionFileError:
            # A conversation that cannot be written is not the same as a
            # conversation that did not happen, and the trace already carries
            # it. Failing the run here would lose a turn that succeeded.
            self.session_file = None

    def _context_with_budget(self, prompt: str) -> List[Dict[str, str]]:
        """Return the context to send, trimmed to the budget and recorded.

        The pending prompt is appended first and pinned, so the trim drops
        older turns rather than the request that is about to go out. Every trim
        is recorded as a ``context.compacted`` event with real counts.

        Only completed turns are ever in the context: a failed turn appended
        nothing, so "truncation never touches a failed turn" holds by
        construction rather than by a rule someone has to remember.
        """
        budget = int(self.config["max_context_chars"])
        context = list(self.messages) + [{"role": "user", "content": prompt}]
        trimmed, report = apply_budget(context, budget, keep_recent=1)
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
