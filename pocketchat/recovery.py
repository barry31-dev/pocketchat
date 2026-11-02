"""Checkpoint and rewind for completed conversation boundaries.

Recovery is deliberately conservative. A checkpoint records the exact
message prefix and its digest; a rewind creates a new branch/session from
that boundary and never rewrites the source session or trace. A prepared
transaction is written before the target is created, so a process that dies
mid-rewind can be retried and reconciled instead of leaving an unexplained
half-file behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .branching import derive_branch
from .session import new_run_id
from .sessions import (SessionFileError, create_branch,
                       new_workspace_id, read_context_messages, read_header,
                       read_turns, resolve_session_argument,
                       resolve_session_path)
from .tasking import (TaskError, load_record, mark_checkpoint, policy_digest,
                      record_step, task_summary, transition, update_record,
                      validate_checkpoint_boundary, validate_resume_record)
from .trace import utc_now


CHECKPOINT_SCHEMA = "pocketchat-checkpoint.v1"
REWIND_SCHEMA = "pocketchat-rewind.v1"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


class RecoveryError(ValueError):
    """The requested recovery boundary cannot be proven or created."""


def _relative(path: Path, root: Path) -> str:
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        return Path(os.path.relpath(str(path), str(root))).as_posix()
    except ValueError:
        return path.as_posix()


def _absolute(value: str, root: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _target_path(root: Path, name: str) -> Path:
    candidate = resolve_session_path(Path(name), str(Path(root) / "sessions"))
    return Path(candidate if candidate.is_absolute() else Path(root) / candidate).resolve()


def _validate_id(value: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise RecoveryError(
            "id must be 3-64 characters using letters, numbers, '.', '_' or '-'")
    return value


def _atomic_json(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise RecoveryError("cannot write recovery record %s: %s" % (path, exc))


def _read_json(path: Path, kind: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError("cannot read %s recovery record %s: %s"
                            % (kind, path, exc))
    if not isinstance(value, dict):
        raise RecoveryError("%s recovery record is not an object: %s"
                            % (kind, path))
    return value


def _digest_messages(messages: List[Dict[str, Any]]) -> str:
    encoded = json.dumps(messages, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _boundary(turns: List[Dict[str, Any]], turn_id: Optional[str]) -> tuple:
    if not turns:
        raise RecoveryError("session has no completed turn to checkpoint")
    if turn_id is None:
        candidates = [
            index for index, turn in enumerate(turns, 1)
            if turn.get("role") == "assistant" and index % 2 == 0
            and turns[index - 2].get("role") == "user"
        ]
        if not candidates:
            raise RecoveryError("session has no completed turn to checkpoint")
        index = candidates[-1]
    else:
        matches = [index for index, turn in enumerate(turns, 1)
                   if turn.get("turn_id") == turn_id]
        if len(matches) != 1:
            raise RecoveryError("session has no unique turn %r" % turn_id)
        index = matches[0]
        if (turns[index - 1].get("role") != "assistant"
                or index % 2 != 0
                or turns[index - 2].get("role") != "user"):
            raise RecoveryError("turn %r is not a completed exchange"
                                % turn_id)
    return index, turns[index - 1]["turn_id"], turns[:index]


def _source_boundary(root: Path, source_value: Path,
                     turn_id: Optional[str]) -> Dict[str, Any]:
    try:
        source = resolve_session_argument(
            Path(source_value), "sessions", base=Path(root))
        header = read_header(source)
        turns = read_turns(source)
    except SessionFileError as exc:
        raise RecoveryError(str(exc))
    count, selected_turn, prefix = _boundary(turns, turn_id)
    trace_value = header.get("trace")
    if not isinstance(trace_value, str) or not trace_value.strip():
        raise RecoveryError("session records no trace evidence")
    trace = (source.parent / trace_value).resolve()
    if not trace.is_file():
        raise RecoveryError("session trace evidence is missing: %s" % trace)
    return {
        "source": source,
        "header": header,
        "turns": turns,
        "messages": count,
        "turn_id": selected_turn,
        "prefix": prefix,
        "trace": trace,
        "boundary_sha256": _digest_messages(prefix),
    }


def _task_checkpoint_fields(task_id: Optional[str],
                            step_id: Optional[int],
                            policy_sha256: Optional[str]) -> Dict[str, Any]:
    values = (task_id, step_id, policy_sha256)
    if all(value is None for value in values):
        return {}
    if any(value is None for value in values):
        raise RecoveryError(
            "task checkpoint needs task_id, step_id and policy_sha256 together")
    if (not isinstance(task_id, str)
            or not re.fullmatch(r"task_[0-9a-f]{16,32}", task_id)):
        raise RecoveryError("checkpoint task_id is malformed")
    if isinstance(step_id, bool) or not isinstance(step_id, int) or step_id < 1:
        raise RecoveryError("checkpoint step_id must be a positive integer")
    if (not isinstance(policy_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", policy_sha256)):
        raise RecoveryError("checkpoint policy_sha256 is malformed")
    return {
        "task_id": task_id,
        "step_id": step_id,
        "policy_sha256": policy_sha256,
    }


def create_checkpoint(root: Path, session: Path,
                      turn_id: Optional[str] = None,
                      checkpoint_id: Optional[str] = None, *,
                      task_id: Optional[str] = None,
                      step_id: Optional[int] = None,
                      policy_sha256: Optional[str] = None) -> Dict[str, Any]:
    root = Path(root).resolve()
    checkpoint_id = _validate_id(
        checkpoint_id or new_workspace_id("checkpoint"))
    task_fields = _task_checkpoint_fields(task_id, step_id, policy_sha256)
    boundary = _source_boundary(root, session, turn_id)
    path = (root / ".pocketchat" / "recovery" / "checkpoints"
            / ("%s.json" % checkpoint_id))
    if path.exists():
        raise RecoveryError("checkpoint already exists: %s" % checkpoint_id)
    record = {
        "schema_version": CHECKPOINT_SCHEMA,
        "kind": "checkpoint",
        "checkpoint_id": checkpoint_id,
        "created": utc_now(),
        "source_session": _relative(boundary["source"], root),
        "source_trace": _relative(boundary["trace"], root),
        "run_id": boundary["header"].get("session"),
        "conversation_id": boundary["header"].get("conversation_id"),
        "branch_id": boundary["header"].get("branch_id"),
        "turn_id": boundary["turn_id"],
        "messages": boundary["messages"],
        "boundary_sha256": boundary["boundary_sha256"],
        "manifest": _relative(path, root),
    }
    record.update(task_fields)
    _atomic_json(path, record)
    return record


def _load_checkpoint(root: Path, checkpoint_id: str) -> Dict[str, Any]:
    checkpoint_id = _validate_id(checkpoint_id)
    path = (root / ".pocketchat" / "recovery" / "checkpoints"
            / ("%s.json" % checkpoint_id))
    if not path.is_file():
        raise RecoveryError("no such checkpoint: %s" % checkpoint_id)
    record = _read_json(path, "checkpoint")
    if (record.get("schema_version") != CHECKPOINT_SCHEMA
            or record.get("kind") != "checkpoint"
            or record.get("checkpoint_id") != checkpoint_id):
        raise RecoveryError("invalid checkpoint record: %s" % path)
    present = [key for key in ("task_id", "step_id", "policy_sha256")
               if key in record]
    if present and len(present) != 3:
        raise RecoveryError("partial task checkpoint record: %s" % path)
    return record


def _verify_checkpoint(root: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    source = _absolute(record.get("source_session", ""), root)
    try:
        header = read_header(source)
        turns = read_turns(source)
    except (SessionFileError, OSError) as exc:
        raise RecoveryError("checkpoint evidence is unreadable: %s" % exc)
    messages = record.get("messages")
    if not isinstance(messages, int) or messages <= 0 or len(turns) < messages:
        raise RecoveryError("checkpoint boundary is missing from the source")
    if _digest_messages(turns[:messages]) != record.get("boundary_sha256"):
        raise RecoveryError("checkpoint boundary digest does not match")
    for key in ("session", "conversation_id", "branch_id"):
        if header.get(key) != record.get("run_id" if key == "session" else key):
            raise RecoveryError("checkpoint source identity changed")
    trace_value = header.get("trace")
    if not isinstance(trace_value, str) or not trace_value.strip():
        raise RecoveryError("checkpoint source trace evidence is missing")
    trace = (source.parent / trace_value).resolve()
    if not trace.is_file() or _relative(trace, root) != record.get("source_trace"):
        raise RecoveryError("checkpoint source trace evidence changed")
    return {"source": source, "header": header, "turns": turns}


def _prepare_rewind(root: Path, checkpoint: Dict[str, Any], name: str) -> Dict[str, Any]:
    source = _absolute(checkpoint["source_session"], root)
    target = _target_path(root, name)
    sessions_dir = str(target.parent)
    described = derive_branch(source, checkpoint["turn_id"], target.name,
                              sessions_dir)
    run_id = new_run_id()
    trace = target.parent.parent / "traces" / ("%s.jsonl" % run_id)
    rewind_id = new_workspace_id("rewind")
    manifest = (root / ".pocketchat" / "recovery" / "rewinds"
                / ("%s.json" % rewind_id))
    return {
        "schema_version": REWIND_SCHEMA,
        "kind": "rewind",
        "rewind_id": rewind_id,
        "checkpoint_id": checkpoint["checkpoint_id"],
        "state": "prepared",
        "created": utc_now(),
        "target_session": _relative(target, root),
        "target_trace": _relative(trace, root),
        "run_id": run_id,
        "conversation_id": described["conversation_id"],
        "branch_id": described["branch_id"],
        "parent_branch_id": described["parent_branch_id"],
        "parent_turn_id": described["parent_turn_id"],
        "messages_written": described["turns"],
        "manifest": _relative(manifest, root),
    }


def _target_matches(root: Path, checkpoint: Dict[str, Any],
                    transaction: Dict[str, Any]) -> bool:
    target = _absolute(transaction["target_session"], root)
    try:
        header = read_header(target)
        turns = read_turns(target)
    except (SessionFileError, OSError):
        return False
    expected = checkpoint["messages"]
    if len(turns) < expected or _digest_messages(turns[:expected]) != checkpoint["boundary_sha256"]:
        return False
    return (header.get("session") == transaction.get("run_id")
            and header.get("conversation_id") == transaction.get("conversation_id")
            and header.get("branch_id") == transaction.get("branch_id")
            and header.get("parent_branch_id") == transaction.get("parent_branch_id")
            and header.get("parent_turn_id") == transaction.get("parent_turn_id"))


def _write_target(root: Path, checkpoint: Dict[str, Any],
                  transaction: Dict[str, Any]) -> Dict[str, Any]:
    source = _absolute(checkpoint["source_session"], root)
    target = _absolute(transaction["target_session"], root)
    sessions_dir = str(target.parent)
    described = derive_branch(source, checkpoint["turn_id"],
                              target.name,
                              sessions_dir)
    if target.exists():
        target.unlink()
    create_branch(
        target, transaction["run_id"], _absolute(transaction["target_trace"], root),
        "0.3.0", transaction["conversation_id"], transaction["branch_id"],
        transaction["parent_branch_id"], transaction["parent_turn_id"],
        described["messages"])
    return described


def _finish_transaction(root: Path, checkpoint: Dict[str, Any],
                        transaction: Dict[str, Any],
                        recovered: bool) -> Dict[str, Any]:
    target = _absolute(transaction["target_session"], root)
    if not _target_matches(root, checkpoint, transaction):
        try:
            target.unlink()
        except OSError:
            pass
        _write_target(root, checkpoint, transaction)
        if not _target_matches(root, checkpoint, transaction):
            raise RecoveryError("rewind target could not be verified")
    transaction["state"] = "completed"
    transaction["completed"] = utc_now()
    manifest = _absolute(transaction["manifest"], root)
    _atomic_json(manifest, transaction)
    return {**transaction, "ok": True, "recovered": recovered,
            "session": transaction["target_session"],
            "manifest": _relative(manifest, root)}


def rewind_to_checkpoint(root: Path, checkpoint_id: str,
                         name: str) -> Dict[str, Any]:
    root = Path(root).resolve()
    checkpoint = _load_checkpoint(root, checkpoint_id)
    _verify_checkpoint(root, checkpoint)
    target = _target_path(root, name)
    target_rel = _relative(target, root)
    manifest_dir = root / ".pocketchat" / "recovery" / "rewinds"
    pending = []
    if manifest_dir.is_dir():
        for path in sorted(manifest_dir.glob("*.json")):
            record = _read_json(path, "rewind")
            if record.get("schema_version") != REWIND_SCHEMA or record.get("kind") != "rewind":
                raise RecoveryError("invalid rewind record: %s" % path)
            if (record.get("checkpoint_id") == checkpoint["checkpoint_id"]
                    and record.get("target_session") == target_rel
                    and record.get("state") != "failed"):
                pending.append(record)
    if len(pending) > 1:
        raise RecoveryError("multiple rewind transactions name this target")
    if pending:
        transaction = pending[0]
        if transaction.get("state") == "completed":
            return {**transaction, "ok": True, "recovered": True,
                    "session": transaction["target_session"],
                    "already_completed": True}
        return _finish_transaction(root, checkpoint, transaction, True)
    if target.exists():
        raise RecoveryError("rewind target already exists: %s" % target_rel)
    sessions_dir = str(target.parent)
    derive_branch(_absolute(checkpoint["source_session"], root),
                  checkpoint["turn_id"], target.name, sessions_dir)
    transaction = _prepare_rewind(root, checkpoint, name)
    _atomic_json(_absolute(transaction["manifest"], root), transaction)
    try:
        _write_target(root, checkpoint, transaction)
    except OSError:
        raise
    return _finish_transaction(root, checkpoint, transaction, False)


def create_task_checkpoint(root: Path, session: Path, *,
                           task_id: str, step_id: int, policy_sha256: str,
                           turn_id: Optional[str] = None,
                           checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    """Checkpoint the latest completed task step at a completed session turn."""
    root = Path(root).resolve()
    record = load_record(root, task_id)
    if record.policy_sha256 != policy_sha256:
        raise RecoveryError("task record policy_sha256 does not match request")
    validate_checkpoint_boundary(record, step_id)
    actual_id = _validate_id(
        checkpoint_id or new_workspace_id("checkpoint"))
    checkpoint = create_checkpoint(
        root, session, turn_id, actual_id,
        task_id=task_id, step_id=step_id, policy_sha256=policy_sha256)
    mark_checkpoint(record, checkpoint_id=actual_id, step_id=step_id,
                    run_id=checkpoint.get("run_id"))
    update_record(root, record)
    return {**checkpoint, "task_state": record.state}


def resume_task(root: Path, checkpoint_id: str, policy, *, task_id: str,
                name: str) -> Dict[str, Any]:
    """Validate task evidence before the first write, then recover the branch."""
    root = Path(root).resolve()
    checkpoint = _load_checkpoint(root, checkpoint_id)
    if not all(key in checkpoint for key in (
            "task_id", "step_id", "policy_sha256")):
        raise RecoveryError("checkpoint has no task resume boundary")
    if checkpoint["task_id"] != task_id:
        raise RecoveryError(
            "checkpoint belongs to task %s, not %s"
            % (checkpoint["task_id"], task_id))
    _verify_checkpoint(root, checkpoint)
    expected_policy = policy_digest(getattr(policy, "source", None) or policy)
    if checkpoint["policy_sha256"] != expected_policy:
        raise RecoveryError("task resume policy hash does not match checkpoint")
    try:
        record = load_record(root, checkpoint["task_id"])
        validate_resume_record(
            record, checkpoint_id=checkpoint_id,
            step_id=checkpoint["step_id"],
            policy_sha256=checkpoint["policy_sha256"])
    except TaskError as exc:
        raise RecoveryError("task record is partial or conflicting: %s" % exc)
    recovered = rewind_to_checkpoint(root, checkpoint_id, name)
    target = root / recovered["session"]
    context = read_context_messages(target)
    transition(record, "resume", "resumed from %s" % checkpoint_id)
    record_step(
        record, "recovery", state="completed", checkpoint_id=checkpoint_id,
        run_id=recovered.get("run_id"), detail="resume")
    update_record(root, record)
    return {**recovered, "task_id": record.task_id,
            "step_id": checkpoint["step_id"], "context": context,
            "record": record, "report": task_summary(record)}
