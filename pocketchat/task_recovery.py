"""Read-only recovery evidence from bounded task execution reports.

A resumed task must not repeat a write or command that the previous execution
already completed. This module derives the completed side-effect set from
existing reports; it does not run a provider, a command or a file write.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPORT_DIR = Path(".pocketchat") / "task-reports"
REPORT_SCHEMA = "pocketchat-task-execution-report.v1"


class TaskRecoveryError(ValueError):
    """Existing evidence cannot support a safe recovery decision."""


def _closed(value: Dict[str, Any], allowed, what: str) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise TaskRecoveryError("%s has unknown field(s): %s"
                                % (what, ", ".join(unknown)))


def _load(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskRecoveryError("cannot read execution report: %s" % exc)
    if not isinstance(data, dict):
        raise TaskRecoveryError("execution report is not an object")
    _closed(data, {"schema_version", "report_id", "spec_sha256", "plan_sha256",
                   "task_ids", "steps", "changes", "evidence_issues", "error", "recovery",
                   "cancellation", "task_budget"},
            "execution report")
    if "cancellation" in data:
        from .tasking import TaskError, validate_cancellation_report
        try:
            validate_cancellation_report(data)
        except TaskError as exc:
            raise TaskRecoveryError(str(exc)) from exc
    if "task_budget" in data:
        from .task_budget import TaskBudget, TaskBudgetError
        try:
            TaskBudget.from_dict(data["task_budget"])
        except TaskBudgetError as exc:
            raise TaskRecoveryError("execution report has an invalid task budget") from exc
    if data.get("schema_version") != REPORT_SCHEMA:
        raise TaskRecoveryError("execution report has the wrong schema")
    if (not isinstance(data.get("task_ids"), list)
            or any(not isinstance(item, str) or not item
                   for item in data["task_ids"])):
        raise TaskRecoveryError("execution report has invalid task ids")
    if not isinstance(data.get("steps"), list):
        raise TaskRecoveryError("execution report has invalid steps")
    return data


def _state(issues: List[Dict[str, str]]) -> str:
    if any(item.get("state") == "refused" for item in issues):
        return "refused"
    if issues:
        return "unknown"
    return "completed"


def completed_side_effects(root: Path, task_id: str, *, planned: Iterable[str] = ()
                           ) -> Dict[str, Any]:
    """Return completed write/command steps and the remaining plan steps.

    Only a report explicitly naming this task contributes. A malformed, foreign
    or unreadable report contributes an evidence issue rather than a completed
    side effect. ``planned`` is used solely to preserve the caller's step order.
    """
    root = Path(root).resolve()
    directory = root / REPORT_DIR
    issues: List[Dict[str, str]] = []
    completed = []
    seen = set()
    failed = set()
    if not directory.is_dir():
        issues.append({"path": REPORT_DIR.as_posix(), "state": "unknown",
                       "reason": "no execution report"})
        return {"state": _state(issues), "steps": [], "remaining_steps": list(planned),
                "evidence_issues": issues}
    for path in sorted(directory.glob("*.json")):
        relative = path.relative_to(root).as_posix()
        try:
            report = _load(path)
        except TaskRecoveryError as exc:
            issues.append({"path": relative, "state": "refused", "reason": str(exc)})
            continue
        if task_id not in report["task_ids"]:
            continue
        for step in report["steps"]:
            if not isinstance(step, dict):
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step is not an object"})
                continue
            step_id = step.get("step_id")
            action = step.get("action")
            status = step.get("status")
            if not isinstance(step_id, str) or not step_id:
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step has no id"})
                continue
            if status not in ("completed", "failed", "not_executed"):
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step has an invalid status",
                               "step_id": step_id})
                continue
            if action == "run_command":
                if status != "completed":
                    failed.add(step_id)
                elif not isinstance(step.get("evidence"), dict):
                    issues.append({"path": relative, "state": "unknown",
                                   "reason": "completed command has no evidence",
                                   "step_id": step_id})
                elif step_id not in seen:
                    completed.append({"step_id": step_id, "action": action,
                                      "status": status})
                    seen.add(step_id)
            elif action in ("write_file", "read_file", "memory_search"):
                if status != "completed":
                    failed.add(step_id)
                elif step_id not in seen:
                    completed.append({"step_id": step_id, "action": action,
                                      "status": status, "task_id": step.get("task_id")})
                    seen.add(step_id)
            else:
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step has an unknown action",
                               "step_id": step_id})
    if not completed and not issues:
        issues.append({"path": REPORT_DIR.as_posix(), "state": "unknown",
                       "reason": "no execution report for task"})
    order = list(planned)
    remaining = [step for step in order if step not in seen]
    return {"state": _state(issues), "steps": completed,
            "remaining_steps": remaining, "evidence_issues": issues}


def recovery_remaining_steps(root: Path, task_id: str,
                             planned: Iterable[str]) -> List[str]:
    """Return planned step ids with no completed side effect."""
    return completed_side_effects(root, task_id, planned=planned)["remaining_steps"]


# Execution recovery is stricter than the legacy read-only inventory above.
# Reuse the existing no-follow, bounded evidence reader and JSON decoder.
MAX_RECOVERY_BYTES = 1024 * 1024


def _read(root, path, *, protect=False):
    from .acceptance import AcceptanceError, _ReadIssue, _read_file
    try:
        return _read_file(root, path, MAX_RECOVERY_BYTES, protect=protect)
    except (_ReadIssue, AcceptanceError, OSError) as exc:
        raise TaskRecoveryError("cannot read recovery evidence %s: %s"
                                % (path, getattr(exc, "reason", str(exc)))) from exc


def _decode(data):
    from .acceptance import AcceptanceError, _decode as decode
    try:
        return decode(data)
    except AcceptanceError as exc:
        raise TaskRecoveryError("invalid recovery JSON") from exc


def _sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _relative(root, value):
    if not isinstance(value, (str, Path)):
        raise TaskRecoveryError("recovery evidence needs a path")
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise TaskRecoveryError("recovery path escapes workspace") from exc
    # Do not resolve here: the descriptor-relative reader must see symlinks.
    if not path.parts or any(part == ".." for part in path.parts):
        raise TaskRecoveryError("invalid recovery evidence path")
    return path.as_posix()


def _evidence_paths(root, result):
    paths = {"trace": _relative(root, result.get("trace"))}
    if result.get("action") == "run_command":
        trace = Path(paths["trace"])
        paths["archive"] = (trace.parent / "command-evidence" / (trace.stem + ".json")).as_posix()
    else:
        from .tasking import _record_path
        paths["record"] = _record_path(Path("."), result.get("task_id")).as_posix()
        paths["session"] = _relative(root, result.get("session"))
    return paths


def bind_step_evidence(root, result):
    """Pin exact artifacts at a completed boundary, not at the later resume."""
    from .tasking import TaskError
    if result.get("status") != "completed":
        return
    hashes = {}
    try:
        for kind, path in _evidence_paths(root, result).items():
            hashes[kind] = _sha(_read(root, path))
    except (TaskRecoveryError, TaskError):
        # Ordinary execution remains usable with custom runners and old data.
        # Partial hashes explicitly cannot authorize recovery.
        pass
    result["evidence_hashes"] = hashes
    if "archive" in hashes:
        result["archive_sha256"] = hashes["archive"]


def _json_lines(data):
    rows = [_decode(line) for line in data.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise TaskRecoveryError("recovery JSONL is empty or malformed")
    return rows


def _trace_events(data):
    events = _json_lines(data)
    ids = set()
    for event in events:
        if (event.get("schema_version") != "1"
                or not isinstance(event.get("event_id"), str)
                or not isinstance(event.get("run_id"), str)
                or not isinstance(event.get("type"), str)
                or not isinstance(event.get("payload"), dict)
                or event["event_id"] in ids):
            raise TaskRecoveryError("invalid recovery trace event")
        ids.add(event["event_id"])
    terminals = [event for event in events if event["type"] in (
        "run.finished", "run.failed", "run.cancelled")]
    if (events[0]["type"] != "run.started" or len(terminals) != 1
            or terminals[0] != events[-1] or events[-1]["type"] != "run.finished"):
        raise TaskRecoveryError("recovery trace has no unique completed boundary")
    return events


def _session_records(data, run_ids, paths, root):
    import os
    from .sessions import SessionFileError, _validated_tool_context
    records = _json_lines(data)
    header = records[0]
    if (header.get("schema_version") != "1" or header.get("session") not in run_ids
            or not isinstance(header.get("trace"), str)):
        raise TaskRecoveryError("session does not match the completed task run")
    recorded = Path(header["trace"])
    if not recorded.is_absolute():
        recorded = root / Path(paths["session"]).parent / recorded
    if Path(os.path.normpath(recorded)) != root / paths["trace"]:
        raise TaskRecoveryError("session points to another trace")
    messages = records[1:]
    if (not messages or len(messages) % 2
            or any(message.get("role") != ("user" if i % 2 == 0 else "assistant")
                   or not isinstance(message.get("content"), str)
                   or message.get("turn_id") != "turn_%06d" % (i + 1)
                   for i, message in enumerate(messages))):
        raise TaskRecoveryError("session has no complete message pairs")
    try:
        for message in messages:
            _validated_tool_context(message.get("tool_context"), Path(paths["session"]))
    except SessionFileError as exc:
        raise TaskRecoveryError("invalid session tool context") from exc


def _check_completed(root, expected, step, changes):
    from .task_command import CommandError, load_command_evidence
    from .tasking import TaskError, parse_record, policy_digest
    try:
        paths = _evidence_paths(root, step)
        hashes = step.get("evidence_hashes")
        if not isinstance(hashes, dict) or set(hashes) != set(paths):
            raise TaskRecoveryError("completed step lacks bound evidence hashes")
        captured = {kind: _read(root, path) for kind, path in paths.items()}
        if any(_sha(data) != hashes[kind] for kind, data in captured.items()):
            raise TaskRecoveryError("completed step evidence bytes changed")
        events = _trace_events(captured["trace"])
        if expected.action == "run_command":
            if any(event["run_id"] != expected.id for event in events):
                raise TaskRecoveryError("command trace belongs to another run")
            archive = load_command_evidence(root, paths["trace"])
            if captured["archive"] != _read(root, paths["archive"]):
                raise TaskRecoveryError("command evidence changed while reading")
            if (step.get("archive_sha256") != hashes["archive"]
                    or step.get("run_id") != expected.id
                    or archive.get("run_id") != expected.id
                    or archive.get("template_id") != expected.command_template_id
                    or archive.get("exit_code") != 0 or archive.get("timed_out") is not False
                    or step.get("exit_code") != 0):
                raise TaskRecoveryError("completed command identity or outcome conflicts")
            evidence = step.get("evidence")
            if not isinstance(evidence, dict):
                raise TaskRecoveryError("completed command has no summary")
            for key, value in evidence.items():
                actual = archive.get(key)
                if key == "trace":
                    actual = _relative(root, actual)
                if value != actual:
                    raise TaskRecoveryError("command summary conflicts with archive")
            return
        record = parse_record(_decode(captured["record"]), task_id=step.get("task_id"))
        if (record.state != "completed"
                or record.policy_sha256 != policy_digest(expected.policy.source)):
            raise TaskRecoveryError("task record does not match the authorized step")
        run_ids = {item.get("run_id") for item in record.steps}
        if (len(run_ids) != 1 or None in run_ids
                or any(event["run_id"] not in run_ids for event in events)
                or events[-1]["payload"].get("task_id") != record.task_id
                or events[-1]["payload"].get("task_state") != "completed"):
            raise TaskRecoveryError("task trace belongs to another task/run")
        _session_records(captured["session"], run_ids, paths, root)
        tools = [item for item in record.steps if item.get("kind") == "tool"]
        if (len(tools) != 1 or tools[0].get("tool") != expected.action
                or tools[0].get("state") != "completed"):
            raise TaskRecoveryError("task completion does not confirm its tool action")
        if expected.action in ("read_file", "write_file"):
            field = "read_allow_paths" if expected.action == "read_file" else "write_allow_paths"
            if tools[0].get("detail") not in getattr(expected.policy, field):
                raise TaskRecoveryError("completed tool path is outside the step inputs")
        if expected.action == "write_file":
            relative = (Path(record.workspace_root) / tools[0]["detail"]).as_posix()
            matches = [item for item in changes if item.get("path") == relative]
            if len(matches) != 1:
                raise TaskRecoveryError("completed write needs one file hash record")
            data = _read(root, relative, protect=True)
            if (_sha(data) != matches[0].get("new_sha256")
                    or len(data) != matches[0].get("bytes")):
                raise TaskRecoveryError("completed file changed after the boundary")
    except (TaskError, CommandError, OSError, ValueError) as exc:
        if isinstance(exc, TaskRecoveryError):
            raise
        raise TaskRecoveryError("invalid completed-step evidence: %s" % exc) from exc


def _unused_output(root, value):
    """Refuse a previous attempt, a symlink, or an unsafe output parent."""
    import os
    import stat
    relative = _relative(root, value)
    cursor = root
    for part in Path(relative).parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TaskRecoveryError("cannot inspect recovery output") from exc
        if stat.S_ISLNK(info.st_mode):
            raise TaskRecoveryError("recovery output contains a symlink")
        if cursor != root / relative and not stat.S_ISDIR(info.st_mode):
            raise TaskRecoveryError("recovery output parent is not a directory")
    if os.path.lexists(cursor):
        raise TaskRecoveryError("pending step already has an output: %s" % relative)


def prepare_recovery(execution, root, report_path, expected_sha256, *,
                     trace_dir, session_dir, cancellation=None):
    """Validate an exact report before any new task, command or report write.

    Only an explicit completed-prefix / never-started-suffix boundary can be
    resumed. Failed or interrupted side effects require a separate decision.
    """
    from .task_exec import TaskExecutionError, _parse_execution_report
    from .task_exec import _sha as valid_sha
    root = Path(root).resolve()
    if not valid_sha(expected_sha256):
        raise TaskRecoveryError("recovery needs the exact report SHA-256")
    relative = _relative(root, report_path)
    path = Path(relative)
    if path.parent != REPORT_DIR or path.suffix != ".json":
        raise TaskRecoveryError("recovery must select one execution report")
    data = _read(root, relative)
    if _sha(data) != expected_sha256:
        raise TaskRecoveryError("execution report bytes changed")
    try:
        report = _parse_execution_report(_decode(data), path.name)
    except TaskExecutionError as exc:
        raise TaskRecoveryError(str(exc)) from exc
    if (report.get("report_id") != path.stem
            or report["spec_sha256"] != execution.spec_sha256
            or report["plan_sha256"] != execution.plan_sha256):
        raise TaskRecoveryError("execution report identity does not match this plan")
    cancelled = report.get("cancellation")
    if cancelled is not None:
        from .tasking import TaskCancellation, TaskError
        if not isinstance(cancellation, TaskCancellation):
            raise TaskRecoveryError("cancelled recovery requires a fresh owner")
        try:
            cancellation.validate_for(execution.spec_sha256, execution.plan_sha256)
        except TaskError as exc:
            raise TaskRecoveryError(str(exc)) from exc
        if cancellation.requested or cancellation.owner_sha256 == cancelled["owner_sha256"]:
            raise TaskRecoveryError("cancelled recovery requires a fresh uncancelled owner")
    if report["evidence_issues"] or "error" in report:
        raise TaskRecoveryError("execution report has unresolved evidence or an error")
    if len(report["steps"]) != len(execution.steps):
        raise TaskRecoveryError("execution report does not cover this plan boundary")
    completed, pending = [], []
    for expected, step in zip(execution.steps, report["steps"]):
        if step.get("step_id") != expected.id or step.get("action") != expected.action:
            raise TaskRecoveryError("execution step identity or order conflicts")
        if step.get("status") == "completed" and not pending:
            _check_completed(root, expected, step, report["changes"])
            completed.append(step)
        elif (step.get("status") == "not_executed"
              and step.get("reason") == ("task_cancelled" if cancelled else "step_boundary")
              and set(step) == {"step_id", "action", "status", "reason"}):
            pending.append(expected)
        else:
            raise TaskRecoveryError("step is not a confirmed completed or unstarted boundary")
    task_ids = sorted({item["task_id"] for item in completed if "task_id" in item})
    if report["task_ids"] != task_ids:
        raise TaskRecoveryError("execution report task ids do not match its steps")
    for step in pending:
        trace = Path(trace_dir) / (step.id + ".jsonl")
        _unused_output(root, trace)
        if step.action == "run_command":
            _unused_output(root, trace.parent / "command-evidence" / (step.id + ".json"))
        else:
            _unused_output(root, Path(session_dir) / (step.id + ".jsonl"))
            if step.action == "write_file":
                for path in step.policy.write_allow_paths:
                    _unused_output(root, root / step.policy.workspace_root / path)
    return report, {"report": relative, "sha256": expected_sha256}


_CONTEXT_BINDING_KEYS = {
    "request_snapshot", "request_sha256", "projection_sidecar",
    "projection_sha256", "recall_sha256",
}


def _canonical_sha(value):
    import hashlib
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def validate_resume_context(root: Path, bindings) -> Dict[str, Any]:
    """Validate bound request/projection/recall artifacts without replaying."""
    root = Path(root).resolve()
    if bindings is None:
        bindings = []
    if not isinstance(bindings, list) or len(bindings) > 64:
        raise TaskRecoveryError("context bindings must be a bounded list")
    validated = []
    for index, raw in enumerate(bindings):
        if not isinstance(raw, dict) or set(raw) != _CONTEXT_BINDING_KEYS:
            raise TaskRecoveryError(
                "context artifact binding %d has an unsupported shape" % index)
        request_rel = _relative(root, raw["request_snapshot"])
        request_path = root / request_rel
        if request_path.is_symlink() or not request_path.is_file():
            raise TaskRecoveryError(
                "context artifact request snapshot is missing: %s" % request_rel)
        request_bytes = _read(root, request_rel)
        try:
            request_record = _decode(request_bytes)
            from .request import request_digest
            if (not isinstance(request_record, dict)
                    or request_digest(request_record.get("request"))
                    != raw["request_sha256"]):
                raise TaskRecoveryError(
                    "context artifact request body digest changed")
        except TaskRecoveryError:
            raise
        except (OSError, ValueError) as exc:
            raise TaskRecoveryError(
                "context artifact request is invalid: %s" % exc) from exc

        projection_rel = raw["projection_sidecar"]
        recall_sha = raw["recall_sha256"]
        projection_sha = raw["projection_sha256"]
        if not projection_rel:
            if projection_sha or recall_sha:
                raise TaskRecoveryError(
                    "context artifact projection digest has no sidecar")
            validated.append({
                "request_snapshot": request_rel,
                "request_sha256": raw["request_sha256"],
                "projection_sidecar": "",
                "projection_sha256": "",
                "recall_sha256": "",
            })
            continue
        projection_rel = _relative(root, projection_rel)
        projection_path = root / projection_rel
        if projection_path.is_symlink() or not projection_path.is_file():
            raise TaskRecoveryError(
                "context artifact projection sidecar is missing: %s"
                % projection_rel)
        projection_bytes = _read(root, projection_rel)
        if _sha(projection_bytes) != projection_sha:
            raise TaskRecoveryError("context artifact projection digest changed")
        try:
            projection = _decode(projection_bytes)
        except ValueError as exc:
            raise TaskRecoveryError(
                "context artifact projection is invalid: %s" % exc) from exc
        manifest = projection.get("manifest") if isinstance(projection, dict) else None
        if (not isinstance(projection, dict)
                or projection.get("schema_version")
                != "pocketchat-context-projection.v1"
                or projection.get("payload_sha256") != raw["request_sha256"]
                or not isinstance(manifest, dict)):
            raise TaskRecoveryError(
                "context artifact projection does not bind this request")
        recall = manifest.get("memory_recall")
        if recall_sha:
            if not isinstance(recall, dict) or _canonical_sha(recall) != recall_sha:
                raise TaskRecoveryError(
                    "context artifact recall set digest changed")
            evidence = manifest.get("evidence")
            recall_count = (len([item for item in evidence
                                 if isinstance(item, dict)
                                 and item.get("kind") == "memory_recall"])
                            if isinstance(evidence, list) else 0)
            if recall.get("evidence_count") != recall_count:
                raise TaskRecoveryError(
                    "context artifact recall evidence count changed")
        elif recall is not None:
            raise TaskRecoveryError(
                "context artifact recall set has no binding digest")
        validated.append({
            "request_snapshot": request_rel,
            "request_sha256": raw["request_sha256"],
            "projection_sidecar": projection_rel,
            "projection_sha256": projection_sha,
            "recall_sha256": recall_sha,
        })
    return {"context_bindings": validated, "replayed_side_effects": []}
