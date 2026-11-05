"""The task record and its state machine: one owner for "where did it stop".

A bounded task run touches the model, the tools, the budgets and the recovery
boundary. Each of those already owns its own facts -- the trace owns what was
sent, the session owns the conversation, the checkpoint manifest owns the
recovery boundary. What none of them owns is the task's own shape: how many
steps it took, which state it is in, and why it stopped. That is this module,
and it is the only place those facts are written.

Two rules carry the weight here. The transition table is closed, so an event
the machine does not name is refused instead of ignored; and a terminal state
is terminal, so a finished task cannot be talked into running again. A report
that reads this record therefore never has to guess: an absent reason is
reported as absent.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from .trace import utc_now

TASK_RECORD_SCHEMA = "pocketchat-task.v1"
TASKS_DIR = Path(".pocketchat") / "tasks"
MAX_STEP_DETAIL_CHARS = 512

ACTIVE_STATES = frozenset({"created", "running", "checkpointed", "interrupted"})
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})

#: (state, event) -> next state. Anything absent from this table is refused.
TRANSITIONS: Dict[tuple, str] = {
    ("created", "start"): "running",
    ("running", "checkpoint"): "checkpointed",
    ("running", "interrupt"): "interrupted",
    ("running", "complete"): "completed",
    ("running", "fail"): "failed",
    ("running", "cancel"): "cancelled",
    ("checkpointed", "resume"): "running",
    ("checkpointed", "cancel"): "cancelled",
    ("interrupted", "resume"): "running",
    ("interrupted", "fail"): "failed",
    ("interrupted", "cancel"): "cancelled",
}

EVENTS = frozenset(event for _state, event in TRANSITIONS)
STEP_KINDS = frozenset({"model", "tool", "checkpoint", "recovery"})


class TaskError(ValueError):
    """The task cannot be described, transitioned, recorded or read."""


CANCELLATION_SCHEMA = "pocketchat-task-cancellation.v1"
_CANCELLATION_FIELDS = frozenset({
    "schema_version", "state", "spec_sha256", "plan_sha256", "owner_sha256",
    "boundary_index",
})


def _cancellation_hex(value: Any, length: int) -> bool:
    return (isinstance(value, str) and len(value) == length
            and all(char in "0123456789abcdef" for char in value))


class TaskCancellation:
    """One in-process owner can request cancellation of one authorized plan.

    The token is a caller capability, not a process lease or authentication
    credential. Polling occurs only at the executor's confirmed boundaries;
    this object never interrupts, executes or persists anything.
    """

    __slots__ = ("_spec_sha256", "_plan_sha256", "_owner_token", "_requested")

    def __init__(self, spec_sha256: str, plan_sha256: str, *, owner_token=None):
        if (not _cancellation_hex(spec_sha256, 64)
                or not _cancellation_hex(plan_sha256, 64)):
            raise TaskError("cancellation needs valid spec and plan digests")
        token = uuid4().hex if owner_token is None else owner_token
        if not _cancellation_hex(token, 32):
            raise TaskError("cancellation owner token must be 32 lowercase hex characters")
        self._spec_sha256 = spec_sha256
        self._plan_sha256 = plan_sha256
        self._owner_token = token
        self._requested = False

    @property
    def spec_sha256(self) -> str:
        return self._spec_sha256

    @property
    def plan_sha256(self) -> str:
        return self._plan_sha256

    @property
    def owner_token(self) -> str:
        return self._owner_token

    @property
    def owner_sha256(self) -> str:
        return hashlib.sha256(self._owner_token.encode("ascii")).hexdigest()

    @property
    def requested(self) -> bool:
        return self._requested

    def validate_for(self, spec_sha256: str, plan_sha256: str) -> None:
        if (spec_sha256 != self._spec_sha256
                or plan_sha256 != self._plan_sha256):
            raise TaskError("cancellation owner does not match the authorized plan")

    def request(self, *, spec_sha256: str, plan_sha256: str,
                owner_token: str) -> bool:
        """Request once; invalid owners cannot change the pending state."""
        self.validate_for(spec_sha256, plan_sha256)
        if owner_token != self._owner_token:
            raise TaskError("cancellation owner token does not match")
        changed = not self._requested
        self._requested = True
        return changed


def validate_cancellation_report(report: Any) -> Dict[str, Any]:
    """Validate the optional cancelled boundary using only supplied facts."""
    metadata = report.get("cancellation") if isinstance(report, dict) else None
    if (not isinstance(metadata, dict) or set(metadata) != _CANCELLATION_FIELDS
            or metadata.get("schema_version") != CANCELLATION_SCHEMA
            or metadata.get("state") != "cancelled"
            or not _cancellation_hex(metadata.get("owner_sha256"), 64)
            or any(not _cancellation_hex(metadata.get(key), 64)
                   or metadata[key] != report.get(key)
                   for key in ("spec_sha256", "plan_sha256"))):
        raise TaskError("execution report has invalid cancellation metadata")
    boundary = metadata["boundary_index"]
    steps = report.get("steps")
    if (isinstance(boundary, bool) or not isinstance(boundary, int)
            or not isinstance(steps, list) or not 0 <= boundary <= len(steps)
            or "error" in report or report.get("evidence_issues") != []):
        raise TaskError("cancelled report has no confirmed boundary")
    seen = set()
    for index, row in enumerate(steps):
        if (not isinstance(row, dict) or not isinstance(row.get("step_id"), str)
                or not row["step_id"] or len(row["step_id"]) > 64
                or row["step_id"] in seen
                or row.get("action") not in (
                    "read_file", "write_file", "memory_search", "run_command")):
            raise TaskError("cancelled report has invalid step identities")
        seen.add(row["step_id"])
        if index < boundary:
            required = ({"trace", "archive"} if row["action"] == "run_command"
                        else {"record", "trace", "session"})
            hashes = row.get("evidence_hashes")
            if (row.get("status") != "completed" or row.get("path_issue")
                    or not isinstance(hashes, dict) or set(hashes) != required
                    or any(not _cancellation_hex(value, 64)
                           for value in hashes.values())):
                raise TaskError("cancelled prefix lacks confirmed step evidence")
        elif (set(row) != {"step_id", "action", "status", "reason"}
              or row.get("status") != "not_executed"
              or row.get("reason") != "task_cancelled"):
            raise TaskError("cancelled suffix is not an unstarted boundary")
    return dict(metadata)


def policy_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical policy bytes.

    Key order does not matter, because the digest is over the canonical form
    (sorted keys, fixed separators) rather than over the file's bytes. A resume
    compares this digest against the one the record carries: the same task
    under a different policy is a different task.
    """
    if not isinstance(payload, dict):
        raise TaskError("a policy digest needs the policy object")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def next_state(state: str, event: str) -> str:
    """One transition, or a refusal naming what was asked for."""
    if not isinstance(state, str) or state not in ACTIVE_STATES | TERMINAL_STATES:
        raise TaskError("unknown task state %r" % (state,))
    if not isinstance(event, str) or event not in EVENTS:
        raise TaskError("unknown task event %r" % (event,))
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise TaskError("cannot apply event %r to a task in state %r"
                        % (event, state))


@dataclass
class TaskRecord:
    """The task's own facts. Steps are appended; nothing here is rewritten."""

    schema_version: str
    task_id: str
    created: str
    objective: str
    policy_sha256: str
    state: str
    max_steps: int
    max_tool_calls: int
    workspace_root: str = "."
    terminal_reason: Optional[str] = None
    steps: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def tool_calls(self) -> int:
        return sum(1 for step in self.steps if step.get("kind") == "tool")


def create_record(policy) -> TaskRecord:
    """A new task in ``created``. The policy's bounds are copied, not linked."""
    return TaskRecord(
        schema_version=TASK_RECORD_SCHEMA,
        task_id="task_%s" % uuid4().hex,
        created=utc_now(),
        objective=policy.objective,
        policy_sha256=policy_digest(getattr(policy, "source", None) or policy),
        state="created",
        max_steps=int(policy.max_steps),
        max_tool_calls=int(policy.max_tool_calls),
        workspace_root=str(getattr(policy, "workspace_root", ".")),
    )


def transition(record: TaskRecord, event: str,
               reason: Optional[str] = None) -> str:
    """Move the record and, on a terminal event, keep the reason it gives.

    Everything is validated before anything is written. A terminal transition
    without a reason must refuse *without* moving the record, because a record
    left in a terminal state with no reason is the one thing the format says
    never happens: a reader would see "finished" and have no answer to "why",
    so it could only invent one.
    """
    target = next_state(record.state, event)
    terminal_reason = record.terminal_reason
    if target in TERMINAL_STATES:
        if not isinstance(reason, str) or not reason.strip():
            raise TaskError("a terminal transition needs a recorded reason")
        terminal_reason = reason.strip()[:MAX_STEP_DETAIL_CHARS]
    record.state = target
    record.terminal_reason = terminal_reason
    return record.state


def _step_detail(detail: Any) -> Optional[str]:
    if detail is None:
        return None
    if not isinstance(detail, str):
        raise TaskError("a step detail must be a string")
    text = detail.strip()
    if not text:
        raise TaskError("a step detail must not be blank when present")
    return text[:MAX_STEP_DETAIL_CHARS]


def record_step(record: TaskRecord, kind: str, *, state: str,
                detail: Any = None, tool: Optional[str] = None,
                run_id: Optional[str] = None,
                checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    """Append one step, refusing a step the declared budget cannot hold.

    The step budget is checked here rather than only by the caller, because the
    record is the thing a resume reads: a record that grew past its own
    ``max_steps`` would be evidence for a run the policy never allowed.
    """
    if kind not in STEP_KINDS:
        raise TaskError("unknown step kind %r" % (kind,))
    step_state = _step_detail(state)
    if step_state is None:
        raise TaskError("a step must record how it ended")
    if len(record.steps) >= record.max_steps:
        raise TaskError("the task reached its step budget of %d"
                        % record.max_steps)
    step: Dict[str, Any] = {
        "index": len(record.steps) + 1,
        "kind": kind,
        "state": step_state,
        "recorded": utc_now(),
    }
    text = _step_detail(detail)
    if text is not None:
        step["detail"] = text
    if tool is not None:
        if kind != "tool":
            raise TaskError("only a tool step names a tool")
        if not isinstance(tool, str) or not tool.strip():
            raise TaskError("a tool step must name its tool")
        step["tool"] = tool.strip()
    if run_id is not None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise TaskError("a step run_id must be a non-empty string")
        step["run_id"] = run_id.strip()
    if checkpoint_id is not None:
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise TaskError("a step checkpoint_id must be a non-empty string")
        step["checkpoint_id"] = checkpoint_id.strip()
    record.steps.append(step)
    return step


def validate_resume_record(record: TaskRecord, *, checkpoint_id: str,
                           step_id: int, policy_sha256: str) -> None:
    """Prove a task record is the complete checkpoint boundary before resume."""
    if record.state != "checkpointed":
        raise TaskError("task record state %r does not name a checkpointed task"
                        % record.state)
    if record.policy_sha256 != policy_sha256:
        raise TaskError("task record policy_sha256 conflicts with checkpoint")
    if len(record.steps) >= record.max_steps:
        raise TaskError(
            "resume recovery step would exceed the task step budget of %d"
            % record.max_steps)
    if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id < 1:
        raise TaskError("checkpoint step_id must be a positive integer")
    if len(record.steps) != step_id + 1:
        raise TaskError("task record does not contain the checkpoint step")
    for expected, step in enumerate(record.steps, 1):
        if step.get("index") != expected:
            raise TaskError("task record steps are partial or out of order")
    boundary = record.steps[step_id - 1]
    if boundary.get("state") not in ("completed", "refused"):
        raise TaskError("checkpoint does not follow a completed step boundary")
    checkpoint = record.steps[step_id]
    if (checkpoint.get("kind") != "checkpoint"
            or checkpoint.get("checkpoint_id") != checkpoint_id
            or checkpoint.get("state") != "completed"):
        raise TaskError("task record checkpoint conflicts with manifest")


def validate_checkpoint_boundary(record: TaskRecord, step_id: int) -> None:
    """A checkpoint may only follow the latest completed step boundary."""
    if record.state != "running":
        raise TaskError("only a running task can create a checkpoint")
    if isinstance(step_id, bool) or not isinstance(step_id, int):
        raise TaskError("checkpoint step_id must be an integer")
    if step_id != len(record.steps):
        raise TaskError("checkpoint requires the latest completed step boundary")
    if not record.steps or record.steps[-1].get("state") not in (
            "completed", "refused"):
        raise TaskError("checkpoint requires a completed step boundary")
    if len(record.steps) >= record.max_steps:
        raise TaskError(
            "checkpoint would exceed the task step budget of %d"
            % record.max_steps)


def mark_checkpoint(record: TaskRecord, *, checkpoint_id: str, step_id: int,
                    run_id: Optional[str] = None) -> Dict[str, Any]:
    """Record a checkpoint after the latest completed step boundary."""
    validate_checkpoint_boundary(record, step_id)
    step = record_step(
        record, "checkpoint", state="completed", checkpoint_id=checkpoint_id,
        run_id=run_id)
    transition(record, "checkpoint", "checkpoint %s" % checkpoint_id)
    return step


def _record_path(root: Path, task_id: str) -> Path:
    """The record's path. The id is validated because it is a filename."""
    if not isinstance(task_id, str):
        raise TaskError("a task id must be a string")
    if (not task_id.startswith("task_") or "/" in task_id or "\\" in task_id
            or not 6 <= len(task_id) <= 64):
        raise TaskError("malformed task id %r" % (task_id,))
    return Path(root) / TASKS_DIR / ("%s.json" % task_id)


def save_record(root: Path, record: TaskRecord) -> Path:
    """Write one record, exclusively.

    Exclusive create is what makes the file evidence rather than a cache: a
    second write would mean the record a reader sees is not the one the run
    produced. Steps are appended by rewriting through a temporary file and
    ``os.replace``, which is atomic on the same filesystem, so a crash leaves
    either the old record or the new one and never half of either.
    """
    path = _record_path(root, record.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise TaskError("task record %s already exists" % path)
    _atomic_write(path, _as_payload(record))
    return path


def update_record(root: Path, record: TaskRecord) -> Path:
    """Replace an existing record with its current contents, atomically."""
    path = _record_path(root, record.task_id)
    if not path.exists():
        raise TaskError("no task record %s to update" % record.task_id)
    _atomic_write(path, _as_payload(record))
    return path


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise TaskError("cannot write task record %s: %s" % (path, exc))


def _as_payload(record: TaskRecord) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": record.schema_version,
        "task_id": record.task_id,
        "created": record.created,
        "objective": record.objective,
        "policy_sha256": record.policy_sha256,
        "state": record.state,
        "max_steps": record.max_steps,
        "max_tool_calls": record.max_tool_calls,
        "workspace_root": record.workspace_root,
        "steps": list(record.steps),
    }
    if record.terminal_reason is not None:
        payload["terminal_reason"] = record.terminal_reason
    return payload


def _required_record_field(payload: Mapping[str, Any], name: str,
                           path: Path) -> Any:
    if name not in payload:
        raise TaskError("task record %s is missing required field %s"
                        % (path, name))
    return payload[name]


def _record_int(payload: Mapping[str, Any], name: str, path: Path) -> int:
    value = _required_record_field(payload, name, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskError("task record %s field %s must be an integer"
                        % (path, name))
    return value


def _validate_step(payload: Mapping[str, Any], index: int,
                   path: Path) -> None:
    required = ("index", "kind", "state", "recorded")
    allowed = set(required) | {"tool", "detail", "run_id", "checkpoint_id"}
    if set(payload) - allowed:
        raise TaskError("task record %s step %d has unknown field(s): %s"
                        % (path, index,
                           ", ".join(sorted(set(payload) - allowed))))
    for key in required:
        if key not in payload:
            raise TaskError("task record %s step %d is missing required field %s"
                            % (path, index, key))
    if (isinstance(payload["index"], bool)
            or payload["index"] != index):
        raise TaskError("task record %s step index must be %d, got %r"
                        % (path, index, payload["index"]))
    if not isinstance(payload["kind"], str) or payload["kind"] not in STEP_KINDS:
        raise TaskError("task record %s step %d names unknown kind %r"
                        % (path, index, payload["kind"]))
    for key in ("state", "recorded"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise TaskError("task record %s step %d field %s must be a "
                            "non-empty string" % (path, index, key))
    if "tool" in payload:
        if payload["kind"] != "tool" or not isinstance(payload["tool"], str) \
                or not payload["tool"].strip():
            raise TaskError("task record %s step %d has an invalid tool"
                            % (path, index))
    for key in ("detail", "run_id", "checkpoint_id"):
        if key in payload and (not isinstance(payload[key], str)
                               or not payload[key].strip()):
            raise TaskError("task record %s step %d field %s must be a "
                            "non-empty string" % (path, index, key))


def load_record(root: Path, task_id: str) -> TaskRecord:
    """Read one record. Missing or malformed facts are named refusals."""
    path = _record_path(root, task_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskError("no such task %r (%s)" % (task_id, exc))
    except ValueError as exc:
        raise TaskError("task record %s is not valid JSON: %s" % (path, exc))
    return parse_record(payload, task_id=task_id, path=path)


def parse_record(payload: Any, *, task_id: str,
                 path: Optional[Path] = None) -> TaskRecord:
    """Validate a captured record without reopening its source file.

    ``load_record`` and bounded evidence readers share this single validator.
    The optional path is a diagnostic label, never an additional read.
    """
    path = path if path is not None else _record_path(Path("."), task_id)
    if not isinstance(payload, dict):
        raise TaskError("task record %s is not an object" % path)
    allowed = {
        "schema_version", "task_id", "created", "objective",
        "policy_sha256", "state", "max_steps", "max_tool_calls",
        "workspace_root", "terminal_reason", "steps",
    }
    if set(payload) - allowed:
        raise TaskError("task record %s has unknown field(s): %s"
                        % (path, ", ".join(sorted(set(payload) - allowed))))
    version = _required_record_field(payload, "schema_version", path)
    if version != TASK_RECORD_SCHEMA:
        raise TaskError("task record %s has an unsupported schema version %r; "
                        "this pocketchat reads %r"
                        % (path, version, TASK_RECORD_SCHEMA))
    record_task_id = _required_record_field(payload, "task_id", path)
    if not isinstance(record_task_id, str) or record_task_id != task_id:
        raise TaskError("task record %s task_id does not match its filename"
                        % path)
    created = _required_record_field(payload, "created", path)
    if not isinstance(created, str) or not created.strip():
        raise TaskError("task record %s field created must be a non-empty "
                        "string" % path)
    objective = _required_record_field(payload, "objective", path)
    if not isinstance(objective, str) or not objective.strip():
        raise TaskError("task record %s field objective must be a non-empty "
                        "string" % path)
    policy_sha256 = _required_record_field(payload, "policy_sha256", path)
    if (not isinstance(policy_sha256, str)
            or len(policy_sha256) != 64
            or any(char not in "0123456789abcdef"
                   for char in policy_sha256)):
        raise TaskError("task record %s field policy_sha256 must be a "
                        "64-character lowercase hex digest" % path)
    workspace_root = payload.get("workspace_root", ".")
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        raise TaskError("task record %s field workspace_root must be a "
                        "non-empty string" % path)
    state = _required_record_field(payload, "state", path)
    if not isinstance(state, str) or state not in ACTIVE_STATES | TERMINAL_STATES:
        raise TaskError("task record %s names an unknown state %r"
                        % (path, state))
    terminal_reason = payload.get("terminal_reason")
    if state in TERMINAL_STATES:
        if not isinstance(terminal_reason, str) or not terminal_reason.strip():
            raise TaskError("task record %s terminal state %r needs a "
                            "non-empty terminal_reason" % (path, state))
    elif terminal_reason is not None:
        raise TaskError("task record %s active state %r cannot carry "
                        "terminal_reason" % (path, state))
    steps = _required_record_field(payload, "steps", path)
    if not isinstance(steps, list):
        raise TaskError("task record %s field steps must be a list" % path)
    max_steps = _record_int(payload, "max_steps", path)
    if max_steps < 1 or len(steps) > max_steps:
        raise TaskError("task record %s has %d steps above max_steps %d"
                        % (path, len(steps), max_steps))
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            raise TaskError("task record %s field steps must contain objects"
                            % path)
        _validate_step(step, index, path)
    max_tool_calls = _record_int(payload, "max_tool_calls", path)
    tool_calls = sum(1 for step in steps if step.get("kind") == "tool")
    if tool_calls > max_tool_calls:
        raise TaskError("task record %s has %d tool steps above "
                        "max_tool_calls %d"
                        % (path, tool_calls, max_tool_calls))
    return TaskRecord(
        schema_version=version,
        task_id=record_task_id,
        created=created,
        objective=objective,
        policy_sha256=policy_sha256,
        state=state,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
        workspace_root=workspace_root,
        terminal_reason=terminal_reason,
        steps=list(steps),
    )


def list_records(root: Path) -> List[str]:
    """Task ids present in this workspace, sorted. An empty list is a result."""
    directory = Path(root) / TASKS_DIR
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("task_*.json"))


def task_summary(record: TaskRecord) -> Dict[str, Any]:
    """What a report prints: counts and names, copied from the record.

    Nothing here is computed a second way. Budgets that were never declared are
    reported as absent rather than as unlimited, and a terminal reason is only
    present when the record says one.
    """
    tool_steps = [step for step in record.steps if step.get("kind") == "tool"]
    checkpoints = [step for step in record.steps
                   if step.get("kind") == "checkpoint"]
    return {
        "task_id": record.task_id,
        "objective": record.objective,
        "state": record.state,
        "terminal_reason": record.terminal_reason,
        "policy_sha256": record.policy_sha256,
        "workspace_root": record.workspace_root,
        "created": record.created,
        "steps": len(record.steps),
        "max_steps": record.max_steps,
        "tool_calls": len(tool_steps),
        "max_tool_calls": record.max_tool_calls,
        "checkpoints": len(checkpoints),
        "runs": sorted({step["run_id"] for step in record.steps
                        if step.get("run_id")}),
        "budget": {},
    }
