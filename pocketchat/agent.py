"""Agent entry points around the existing conversation kernel.

The interactive shell adds session-local switches, not a second agent runtime.
The bounded task entry point adds policy enforcement and task evidence while
leaving model requests and tool execution with ``Session``. A plain
message is sent through ``Session`` exactly as ``chat`` sends it; each message
is its own run so it gets its own terminal trace event, while ``--session``
carries the conversation across runs.

Slash commands are control input, never user turns: they do not open a trace,
append a message or contact a provider. This keeps the evidence honest about
what the model actually saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

REASONING_LEVELS = ("minimal", "low", "medium", "high")
DEFAULT_REASONING = "medium"
MIN_CONTEXT_CHARS = 1
MAX_CONTEXT_CHARS = 10 ** 9


class AgentCommandError(ValueError):
    """A slash command is malformed or names a value the agent cannot use."""


@dataclass
class AgentState:
    profile: Optional[str]
    model: str
    session: str
    reasoning: str = DEFAULT_REASONING
    context_chars: int = 24000
    reasoning_supported: bool = False

    def status_line(self) -> str:
        return ("profile=%s model=%s session=%s reasoning=%s "
                "reasoning_sent=%s context_chars=%d"
                % (self.profile or "default", self.model, self.session,
                   self.reasoning, "yes" if self.reasoning_supported else "no",
                   self.context_chars))


def apply_command(state: AgentState, line: str,
                  profiles: dict, models: dict) -> Optional[str]:
    """Apply one slash command and return a human response.

    ``profiles`` is the config's profile map; ``models`` maps names to the
    model each profile uses. Unknown names and invalid values are refused
    without mutating ``state``.
    """
    parts = line.strip().split()
    if not parts:
        return None
    command = parts[0].lower()
    args = parts[1:]
    if command in ("/exit", "/quit", "/bye"):
        return "exit"
    if command == "/status":
        return state.status_line()
    if command == "/model":
        if len(args) != 1:
            raise AgentCommandError("usage: /model <profile>")
        name = args[0]
        if name not in profiles:
            raise AgentCommandError(
                "unknown profile: %s (available: %s)"
                % (name, ", ".join(sorted(profiles))))
        state.profile = name
        state.model = models[name]
        state.reasoning_supported = bool(
            profiles[name].get("reasoning_effort_supported", False))
        return "model: %s (%s)" % (name, state.model)
    if command == "/session":
        if len(args) != 1:
            raise AgentCommandError("usage: /session <name>")
        name = args[0].strip()
        if not name or name.startswith("-") or "/" in name or "\\" in name:
            raise AgentCommandError("session must be a non-empty name")
        state.session = name
        return "session: %s" % name
    if command == "/reasoning":
        if len(args) != 1:
            raise AgentCommandError(
                "usage: /reasoning <%s>" % "|".join(REASONING_LEVELS))
        reasoning_value = args[0].lower()
        if reasoning_value not in REASONING_LEVELS:
            raise AgentCommandError(
                "reasoning must be one of: %s" % ", ".join(REASONING_LEVELS))
        state.reasoning = reasoning_value
        return "reasoning: %s" % reasoning_value
    if command == "/context":
        if len(args) != 1:
            raise AgentCommandError("usage: /context <characters>")
        try:
            context_value = int(args[0])
        except ValueError:
            raise AgentCommandError("context must be an integer")
        if not MIN_CONTEXT_CHARS <= context_value <= MAX_CONTEXT_CHARS:
            raise AgentCommandError(
                "context must be between %d and %d"
                % (MIN_CONTEXT_CHARS, MAX_CONTEXT_CHARS))
        state.context_chars = context_value
        return "context_chars: %d" % context_value
    if command.startswith("/"):
        raise AgentCommandError("unknown command: %s" % command)
    return None


@dataclass
class TaskRunResult:
    """The task record and the report a caller can print or serialize."""

    task_id: str
    status: str
    record: Any
    report: Dict[str, Any]
    run_id: str
    trace_path: Path
    session_path: Path
    acceptance: Optional[Dict[str, Any]] = None


class _TaskRunController:
    """Task-only orchestration around the existing session execution path."""

    def __init__(self, workspace: Path, record) -> None:
        self.workspace = Path(workspace)
        self.record = record
        self.task_id = record.task_id

    def _persist(self) -> None:
        from .tasking import update_record
        update_record(self.workspace, self.record)

    def before_step(self, kind: str, tool: Optional[str] = None) -> None:
        from .session import SessionError
        if len(self.record.steps) >= self.record.max_steps:
            raise SessionError(
                "step_budget_exceeded",
                "task step budget %d exceeded" % self.record.max_steps)
        if kind == "tool" and self.record.tool_calls >= self.record.max_tool_calls:
            raise SessionError(
                "tool_call_budget_exceeded",
                "task tool call budget %d exceeded"
                % self.record.max_tool_calls)

    def after_step(self, kind: str, state: str, *,
                   tool: Optional[str] = None,
                   detail: Optional[str] = None,
                   run_id: Optional[str] = None) -> None:
        from .tasking import record_step
        record_step(
            self.record, kind, state=state, tool=tool, detail=detail,
            run_id=run_id)
        self._persist()

    def before_terminal(self, status: str, error: Optional[Dict[str, str]],
                        reason: str) -> str:
        from .tasking import TERMINAL_STATES, transition
        if self.record.state in TERMINAL_STATES:
            return self.record.state
        event = {
            "completed": "complete",
            "failed": "fail",
            "cancelled": "cancel",
        }[status]
        if status == "completed":
            recorded_reason = "task run completed"
        elif status == "cancelled":
            recorded_reason = reason or "interrupted by the user"
        else:
            error_type = (error or {}).get("type") or "task_failed"
            message = (error or {}).get("message") or ""
            if error_type == "tool_call_limit":
                error_type = "tool_call_budget_exceeded"
            recorded_reason = "%s: %s" % (error_type, message) if message else error_type
        transition(self.record, event, recorded_reason)
        self._persist()
        return self.record.state


def _task_config(config: Dict[str, Any], policy, workspace_root: Path) -> Dict[str, Any]:
    """Copy the session config and narrow it to exactly the signed policy."""
    task_config = dict(config)
    task_config.update({
        "max_context_chars": policy.max_context_chars,
        "max_turn_tokens": policy.max_turn_tokens,
        "tool_call_max_count": policy.max_tool_calls,
        "task_workspace_root": str(workspace_root),
    })
    if policy.max_session_tokens is None:
        task_config.pop("max_session_tokens", None)
    else:
        task_config["max_session_tokens"] = policy.max_session_tokens

    grants = policy.tools
    task_config.update({
        "tool_read_enabled": "read_file" in grants,
        "tool_read_allow_paths": list(
            grants.get("read_file", {}).get("allow_paths", [])),
        "tool_write_enabled": "write_file" in grants,
        "tool_write_allow_paths": list(
            grants.get("write_file", {}).get("allow_paths", [])),
        "tool_memory_search_enabled": "memory_search" in grants,
        "tool_memory_search_max_results": grants.get(
            "memory_search", {}).get("max_results", 20),
    })
    fallback = task_config.get("fallback_config")
    if isinstance(fallback, dict):
        task_config["fallback_config"] = _task_config(
            fallback, policy, workspace_root)
    return task_config


def run_task(*, policy, workspace: Path, config: Dict[str, Any],
             trace_path: Path, session_path: Path,
             prompts: Iterable[str], acceptance=None) -> TaskRunResult:
    """Run one bounded task through the existing model and tool path."""
    from .session import Session
    from .tasking import (create_record, save_record, task_summary,
                         transition)

    if acceptance is not None:
        from .acceptance import parse_plan
        acceptance = parse_plan(acceptance.source)
    workspace = Path(workspace)
    workspace_root = (workspace / policy.workspace_root).resolve()
    try:
        workspace_root.relative_to(workspace.resolve())
    except ValueError:
        raise ValueError("task workspace root escapes the workspace")

    record = create_record(policy)
    transition(record, "start", "task run started")
    save_record(workspace, record)
    controller = _TaskRunController(workspace, record)
    try:
        session = Session(
            _task_config(config, policy, workspace_root),
            Path(trace_path),
            session_path=Path(session_path),
            api_key=config.get("api_key"),
            task_observer=controller,
        )
    except Exception as exc:
        transition(
            record, "fail",
            "session_setup_failed: %s: %s" % (type(exc).__name__, exc))
        from .tasking import update_record
        update_record(workspace, record)
        raise
    status = session.run(prompts)
    receipt = None
    if acceptance is not None:
        from .acceptance import AcceptanceError, verify_task
        from .tasking import TaskError
        if status != "completed":
            receipt = {"state": "not_run", "reason": "execution_not_completed"}
        else:
            try:
                receipt = verify_task(workspace, record.task_id, acceptance)
            except (AcceptanceError, TaskError, OSError):
                # Execution evidence remains valid even if acceptance storage
                # cannot be created. Never turn missing proof into a pass.
                receipt = {"state": "unknown", "reason": "acceptance_unavailable"}
    return TaskRunResult(
        task_id=record.task_id,
        status=status,
        record=record,
        report=task_summary(record),
        run_id=session.run_id,
        trace_path=session.trace_path,
        session_path=Path(session_path),
        acceptance=receipt,
    )
