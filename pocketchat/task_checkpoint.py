"""Explicit plan/command checkpoints derived from a bounded execution report.

A checkpoint is a small, closed record that names exactly which steps of an
authorized execution are known complete and which remain. It carries the spec
and plan digests, the completed/pending step partition, a bounded summary of
file and command side effects, and the digest of the report it was derived
from. It is evidence for a later resume, not a new execution authorization:
creating one never runs a step, and reading one never touches the workspace.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List

CHECKPOINT_SCHEMA = "pocketchat-task-checkpoint.v1"
MAX_CHECKPOINT_BYTES = 1024 * 1024
_HEX = frozenset("0123456789abcdef")
_ALLOWED_KEYS = {
    "schema_version", "checkpoint_id", "report_id", "spec_sha256",
    "plan_sha256", "completed_step_ids", "pending_step_ids", "boundary_index",
    "task_ids", "file_effects", "command_effects", "report_sha256",
    "context_bindings", "task_budget_sha256",
}
_REQUIRED_KEYS = _ALLOWED_KEYS - {"context_bindings", "task_budget_sha256"}
_CONTEXT_BINDING_KEYS = {
    "request_snapshot", "request_sha256", "projection_sidecar",
    "projection_sha256", "recall_sha256",
}
_FILE_KEYS = {"path", "new_sha256", "bytes", "baseline"}
_COMMAND_KEYS = {"step_id", "template_id", "exit_code", "timed_out",
                 "archive_sha256"}
_STEP_ID = __import__("re").compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_BASELINES = frozenset({"head", "absent", "unknown"})


class CheckpointError(ValueError):
    """A checkpoint record or the report it claims cannot be trusted."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(value: Any, what: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HEX for char in value)):
        raise CheckpointError("%s must be a lowercase SHA-256" % what)
    return value


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CheckpointError("%s must be an object" % what)
    return value


def _closed(value: Dict[str, Any], allowed, what: str) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    missing = sorted(key for key in allowed if key not in value)
    if unknown or missing:
        detail = []
        if unknown:
            detail.append("unknown field(s): %s" % ", ".join(unknown))
        if missing:
            detail.append("missing field(s): %s" % ", ".join(missing))
        raise CheckpointError("%s has %s" % (what, "; ".join(detail)))


def _closed_checkpoint(value: Dict[str, Any]) -> None:
    unknown = sorted(key for key in value if key not in _ALLOWED_KEYS)
    missing = sorted(key for key in _REQUIRED_KEYS if key not in value)
    if unknown or missing:
        detail = []
        if unknown:
            detail.append("unknown field(s): %s" % ", ".join(unknown))
        if missing:
            detail.append("missing field(s): %s" % ", ".join(missing))
        raise CheckpointError("checkpoint has %s" % "; ".join(detail))


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CheckpointError("%s must be an integer" % what)
    if not low <= value <= high:
        raise CheckpointError("%s must be between %d and %d" % (what, low, high))
    return value


def _step_ids(value: Any, what: str) -> List[str]:
    if not isinstance(value, list):
        raise CheckpointError("%s must be a list" % what)
    rows = []
    for item in value:
        if not isinstance(item, str) or not _STEP_ID.fullmatch(item):
            raise CheckpointError("%s entries must be bounded step ids" % what)
        rows.append(item)
    if len(rows) != len(set(rows)):
        raise CheckpointError("%s must not contain duplicates" % what)
    return rows


def _text(value: Any, what: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CheckpointError("%s must be a non-empty string <= %d characters"
                              % (what, maximum))
    return value


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CheckpointError("duplicate JSON member: %s" % key)
        value[key] = item
    return value


def _workspace_path(value: Any, what: str) -> str:
    path = _text(value, what, 1024)
    if path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise CheckpointError("%s must be workspace-relative" % what)
    return path


def _context_bindings(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or len(value) > 64:
        raise CheckpointError("context_bindings must be a bounded list")
    rows = []
    for index, raw in enumerate(value):
        row = _object(raw, "context_bindings[%d]" % index)
        _closed(row, _CONTEXT_BINDING_KEYS, "context_bindings[%d]" % index)
        projection = row["projection_sidecar"]
        if projection:
            projection = _workspace_path(
                projection, "projection sidecar path")
        projection_sha = row["projection_sha256"]
        recall_sha = row["recall_sha256"]
        if not projection:
            if projection_sha or recall_sha:
                raise CheckpointError(
                    "context binding projection digest has no sidecar")
            projection_sha = ""
            recall_sha = ""
        else:
            projection_sha = _sha(projection_sha, "projection_sha256")
            recall_sha = _sha(recall_sha, "recall_sha256") if recall_sha else ""
        rows.append({
            "request_snapshot": _workspace_path(
                row["request_snapshot"], "request snapshot path"),
            "request_sha256": _sha(row["request_sha256"], "request_sha256"),
            "projection_sidecar": projection,
            "projection_sha256": projection_sha,
            "recall_sha256": recall_sha,
        })
    keys = [row["request_snapshot"] for row in rows]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise CheckpointError(
            "context_bindings must be sorted and unique by request snapshot")
    return rows


def _file_effects(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise CheckpointError("file_effects must be a list")
    rows = []
    paths = []
    for index, raw in enumerate(value):
        row = _object(raw, "file_effects[%d]" % index)
        _closed(row, _FILE_KEYS, "file_effects[%d]" % index)
        path = _text(row["path"], "file effect path", 1024)
        if path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise CheckpointError("file effect path must be workspace-relative")
        if row["baseline"] not in _BASELINES:
            raise CheckpointError("file effect baseline is unknown")
        rows.append({
            "path": path,
            "new_sha256": _sha(row["new_sha256"], "file effect hash"),
            "bytes": _integer(row["bytes"], "file effect bytes", 0, 2 ** 40),
            "baseline": row["baseline"],
        })
        paths.append(path)
    if paths != sorted(paths):
        raise CheckpointError("file_effects must be sorted by path")
    if len(paths) != len(set(paths)):
        raise CheckpointError("file_effects must not repeat a path")
    return rows


def _command_effects(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise CheckpointError("command_effects must be a list")
    rows = []
    ids = []
    for index, raw in enumerate(value):
        row = _object(raw, "command_effects[%d]" % index)
        _closed(row, _COMMAND_KEYS, "command_effects[%d]" % index)
        step_id = _text(row["step_id"], "command effect step id", 64)
        if not isinstance(row["timed_out"], bool):
            raise CheckpointError("command effect timed_out must be a boolean")
        rows.append({
            "step_id": step_id,
            "template_id": _text(row["template_id"], "command template id", 64),
            "exit_code": _integer(row["exit_code"], "command exit code",
                                  -1, 2 ** 31 - 1),
            "timed_out": row["timed_out"],
            "archive_sha256": _sha(row["archive_sha256"], "command archive hash"),
        })
        ids.append(step_id)
    if ids != sorted(ids):
        raise CheckpointError("command_effects must be sorted by step id")
    if len(ids) != len(set(ids)):
        raise CheckpointError("command_effects must not repeat a step")
    return rows


def parse_checkpoint(payload: Any, *, expected_spec_sha256=None,
                     expected_plan_sha256=None) -> Dict[str, Any]:
    """Validate one complete checkpoint record without touching the workspace.

    ``expected_spec_sha256`` / ``expected_plan_sha256`` let a caller bind the
    checkpoint to the execution it is about to resume. Without them the record
    is only checked for shape; a digest that disagrees with the authorized
    plan must refuse rather than describe a different execution.
    """
    record = _object(payload, "checkpoint")
    _closed_checkpoint(record)
    if record["schema_version"] != CHECKPOINT_SCHEMA:
        raise CheckpointError("checkpoint has the wrong schema")
    checkpoint_id = _text(record["checkpoint_id"], "checkpoint id", 64)
    report_id = _text(record["report_id"], "report id", 128)
    spec_sha256 = _sha(record["spec_sha256"], "spec_sha256")
    plan_sha256 = _sha(record["plan_sha256"], "plan_sha256")
    completed = _step_ids(record["completed_step_ids"], "completed_step_ids")
    pending = _step_ids(record["pending_step_ids"], "pending_step_ids")
    if set(completed) & set(pending):
        raise CheckpointError("a step cannot be both completed and pending")
    boundary = _integer(record["boundary_index"], "boundary_index",
                        0, 4096)
    if boundary != len(completed):
        raise CheckpointError(
            "boundary_index must equal the completed step count")
    task_ids = _step_ids(record["task_ids"], "task_ids") if record["task_ids"] else []
    files = _file_effects(record["file_effects"])
    commands = _command_effects(record["command_effects"])
    command_ids = {row["step_id"] for row in commands}
    if not command_ids <= set(completed):
        raise CheckpointError("a command effect is not a completed step")
    context_bindings = (_context_bindings(record["context_bindings"])
                        if "context_bindings" in record else None)
    task_budget_sha256 = (_sha(record["task_budget_sha256"],
                               "task_budget_sha256")
                          if "task_budget_sha256" in record else None)
    parsed = {
        "schema_version": CHECKPOINT_SCHEMA,
        "checkpoint_id": checkpoint_id,
        "report_id": report_id,
        "spec_sha256": spec_sha256,
        "plan_sha256": plan_sha256,
        "completed_step_ids": completed,
        "pending_step_ids": pending,
        "boundary_index": boundary,
        "task_ids": task_ids,
        "file_effects": files,
        "command_effects": commands,
        "report_sha256": _sha(record["report_sha256"], "report_sha256"),
    }
    if context_bindings is not None:
        parsed["context_bindings"] = context_bindings
    if task_budget_sha256 is not None:
        parsed["task_budget_sha256"] = task_budget_sha256
    if expected_spec_sha256 is not None and parsed["spec_sha256"] != _sha(
            expected_spec_sha256, "expected spec_sha256"):
        raise CheckpointError("checkpoint spec digest does not match the plan")
    if expected_plan_sha256 is not None and parsed["plan_sha256"] != _sha(
            expected_plan_sha256, "expected plan_sha256"):
        raise CheckpointError("checkpoint plan digest does not match the plan")
    return parsed


def _load_json_file(path: Path, what: str) -> Any:
    target = Path(path)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if target.is_symlink():
            raise CheckpointError("%s must not be a symlink" % what)
        descriptor = os.open(str(target), flags)
    except CheckpointError:
        raise
    except OSError as exc:
        raise CheckpointError("cannot open %s: %s" % (what, exc))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise CheckpointError("%s must be a regular file" % what)
        if info.st_size > MAX_CHECKPOINT_BYTES:
            raise CheckpointError("%s exceeds %d bytes"
                                  % (what, MAX_CHECKPOINT_BYTES))
        chunks = []
        remaining = MAX_CHECKPOINT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise CheckpointError("%s exceeds %d bytes"
                                  % (what, MAX_CHECKPOINT_BYTES))
    except OSError as exc:
        raise CheckpointError("cannot read %s: %s" % (what, exc))
    finally:
        os.close(descriptor)
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except CheckpointError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise CheckpointError("%s is not valid UTF-8 JSON: %s" % (what, exc))


def load_checkpoint(path: Path) -> Dict[str, Any]:
    payload = _load_json_file(path, "checkpoint")
    if isinstance(payload, dict) and "ok" in payload:
        if payload["ok"] is not True:
            raise CheckpointError("checkpoint CLI envelope must have ok=true")
        payload = dict(payload)
        payload.pop("ok")
    return parse_checkpoint(payload)


def _report_digest(report: Dict[str, Any]) -> str:
    return _digest(_canonical(report).encode("utf-8"))


def _step_id(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _STEP_ID.fullmatch(value):
        raise CheckpointError("%s must be a bounded step id" % what)
    return value


def _command_archive(step: Dict[str, Any], evidence: Dict[str, Any]) -> str:
    """Accept the current top-level hash and the legacy nested declaration."""
    archive = step.get("archive_sha256", evidence.get("archive_sha256"))
    nested = evidence.get("archive_sha256")
    if nested is not None and nested != archive:
        raise CheckpointError("command archive digest declarations disagree")
    return _sha(archive, "command archive hash")


def create_checkpoint(report: Any, *, report_path, checkpoint_id=None,
                      expected_report_sha256=None,
                      context_bindings=None) -> Dict[str, Any]:
    """Derive one closed checkpoint from a validated execution report."""
    payload = _object(report, "execution report")
    if "cancellation" in payload:
        from .tasking import TaskError, validate_cancellation_report
        try:
            validate_cancellation_report(payload)
        except TaskError as exc:
            raise CheckpointError(str(exc)) from exc
    for field in ("schema_version", "report_id", "spec_sha256", "plan_sha256",
                  "steps", "changes", "evidence_issues"):
        if field not in payload:
            raise CheckpointError("execution report is missing %s" % field)
    if payload["schema_version"] != "pocketchat-task-execution-report.v1":
        raise CheckpointError("execution report has the wrong schema")
    if payload["evidence_issues"]:
        raise CheckpointError(
            "an execution report with evidence issues cannot bound a checkpoint")
    spec_sha256 = _sha(payload["spec_sha256"], "spec_sha256")
    plan_sha256 = _sha(payload["plan_sha256"], "plan_sha256")
    steps = payload["steps"]
    if not isinstance(steps, list):
        raise CheckpointError("execution report steps must be a list")
    changes = payload["changes"]
    if not isinstance(changes, list):
        raise CheckpointError("execution report changes must be a list")
    path = Path(report_path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CheckpointError("cannot read the execution report: %s" % exc)
    actual = _digest(data)
    if expected_report_sha256 is not None:
        expected = _sha(expected_report_sha256, "report_sha256")
        if expected != actual:
            raise CheckpointError(
                "execution report digest does not match the declared value")
    # The object handed in must be the very report on disk. A caller can
    # otherwise pass an edited spec/plan digest together with the digest of the
    # original file and have the checkpoint describe an execution that never
    # happened.
    try:
        stored = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeError, ValueError) as exc:
        raise CheckpointError("execution report is not valid UTF-8 JSON: %s" % exc)
    if stored != payload:
        raise CheckpointError(
            "the supplied execution report differs from the file it names")

    completed: List[str] = []
    pending: List[str] = []
    command_ids: List[str] = []
    for index, raw in enumerate(steps):
        row = _object(raw, "execution report step %d" % index)
        step_id = _step_id(row.get("step_id"), "step id")
        status = row.get("status")
        if status == "completed":
            if pending:
                raise CheckpointError(
                    "step %s is completed after an unexecuted step" % step_id)
            completed.append(step_id)
        elif status == "not_executed":
            pending.append(step_id)
        elif status == "failed":
            pending.append(step_id)
        else:
            raise CheckpointError("step %s has an unknown status" % step_id)
        if status != "completed":
            continue
        evidence = row.get("evidence")
        if isinstance(evidence, dict) and evidence.get("template_id"):
            archive = _command_archive(row, evidence)
            command_ids.append(step_id)
            if not _sha(archive, "command archive hash"):
                raise CheckpointError(
                    "completed command step %s lacks an archive digest" % step_id)

    task_ids = []
    for raw in steps:
        value = raw.get("task_id") if isinstance(raw, dict) else None
        if isinstance(value, str) and value and value not in task_ids:
            task_ids.append(value)

    files = []
    for raw in changes:
        row = _object(raw, "execution report change")
        if "path" not in row or "new_sha256" not in row:
            raise CheckpointError("execution report change lacks evidence")
        entry = {
            "path": row["path"],
            "new_sha256": row["new_sha256"],
            "bytes": row.get("bytes"),
            "baseline": row.get("baseline", "unknown"),
        }
        if not isinstance(entry["bytes"], int) or isinstance(entry["bytes"], bool):
            entry["bytes"] = 0
        files.append(entry)
    files = _file_effects(sorted(files, key=lambda item: item["path"]))

    commands = []
    for raw in steps:
        if not isinstance(raw, dict) or raw.get("status") != "completed":
            continue
        evidence = raw.get("evidence")
        if not isinstance(evidence, dict) or not evidence.get("template_id"):
            continue
        commands.append({
            "step_id": raw.get("step_id"),
            "template_id": evidence.get("template_id"),
            "exit_code": evidence.get("exit_code"),
            "timed_out": evidence.get("timed_out"),
            "archive_sha256": _command_archive(raw, evidence),
        })
    commands = _command_effects(sorted(commands, key=lambda item: item["step_id"]))

    checkpoint_id_value = checkpoint_id or ("ckpt_%s" % payload["report_id"])
    record = {
        "schema_version": CHECKPOINT_SCHEMA,
        "checkpoint_id": _text(checkpoint_id_value, "checkpoint id", 64),
        "report_id": _text(payload["report_id"], "report id", 128),
        "spec_sha256": spec_sha256,
        "plan_sha256": plan_sha256,
        "completed_step_ids": completed,
        "pending_step_ids": pending,
        "boundary_index": len(completed),
        "task_ids": sorted(task_ids),
        "file_effects": files,
        "command_effects": commands,
        "report_sha256": actual,
    }
    if context_bindings is not None:
        record["context_bindings"] = context_bindings
    if "task_budget" in payload:
        budget = _object(payload["task_budget"], "task budget")
        record["task_budget_sha256"] = _digest(
            _canonical(budget).encode("utf-8"))
    return parse_checkpoint(record)


REPORTS_DIR = Path(".pocketchat") / "task-reports"


def write_checkpoint(path: Path, record: Any, *, credential=None) -> str:
    """Persist one parsed checkpoint exclusively and verify its bytes."""
    parsed = parse_checkpoint(record)
    data = (_canonical(parsed) + "\n").encode("utf-8")
    if credential and credential.encode("utf-8") in data:
        raise CheckpointError("checkpoint contains the configured credential")
    target = Path(path)
    if target.is_symlink():
        raise CheckpointError("checkpoint output must not be a symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        loaded = load_checkpoint(target)
    except CheckpointError:
        raise
    if loaded != parsed or target.read_bytes() != data:
        raise CheckpointError("checkpoint changed while it was persisted")
    return _digest(data)


def resume_from_checkpoint(root: Path, checkpoint_path: Path, *,
                           expected_spec_sha256=None,
                           expected_plan_sha256=None) -> Dict[str, Any]:
    """Resolve one checkpoint into the exact report and digest to resume from.

    A checkpoint names the report it was derived from and that report's digest.
    Resolving it here means a caller never has to guess which report is current:
    the checkpoint itself is the pointer. The returned report path is
    workspace-relative and must live directly under the reports directory, so a
    checkpoint cannot name an arbitrary file as its own evidence.
    """
    record = load_checkpoint(checkpoint_path)
    record = parse_checkpoint(
        record,
        expected_spec_sha256=expected_spec_sha256,
        expected_plan_sha256=expected_plan_sha256)
    root = Path(root).resolve()
    relative = "%s/%s.json" % (REPORTS_DIR.as_posix(), record["report_id"])
    candidate = root / relative
    if not candidate.is_file() or candidate.is_symlink():
        raise CheckpointError(
            "checkpoint report %s is not a regular file in the workspace"
            % relative)
    try:
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError as exc:
        raise CheckpointError("cannot read the checkpoint report: %s" % exc)
    if actual != record["report_sha256"]:
        raise CheckpointError(
            "checkpoint report digest changed; refuse to resume from it")
    try:
        report_record = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise CheckpointError("checkpoint report is invalid JSON: %s" % exc) from exc
    if "cancellation" in report_record:
        from .tasking import TaskError, validate_cancellation_report
        try:
            validate_cancellation_report(report_record)
        except TaskError as exc:
            raise CheckpointError(str(exc)) from exc
    report_budget = report_record.get("task_budget")
    if record.get("task_budget_sha256"):
        if not isinstance(report_budget, dict):
            raise CheckpointError("checkpoint budget ledger is missing from the report")
        if _digest(_canonical(report_budget).encode("utf-8")) != record[
                "task_budget_sha256"]:
            raise CheckpointError("checkpoint budget ledger digest changed")
    elif report_budget is not None:
        raise CheckpointError("checkpoint does not bind the report budget ledger")
    try:
        from .task_recovery import validate_resume_context
        context = validate_resume_context(
            root, record.get("context_bindings", []))
    except ValueError as exc:
        raise CheckpointError(str(exc)) from exc
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "checkpoint_id": record["checkpoint_id"],
        "report_id": record["report_id"],
        "report_path": relative,
        "expected_report_sha256": record["report_sha256"],
        "spec_sha256": record["spec_sha256"],
        "plan_sha256": record["plan_sha256"],
        "completed_step_ids": record["completed_step_ids"],
        "pending_step_ids": record["pending_step_ids"],
        "boundary_index": record["boundary_index"],
        "context_bindings": context["context_bindings"],
        "replayed_side_effects": context["replayed_side_effects"],
        "task_budget_sha256": record.get("task_budget_sha256", ""),
    }
