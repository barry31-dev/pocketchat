"""Operator-declared, deterministic task acceptance with independent receipts.

Execution completion is not acceptance. This module checks only the declared
local conditions, never calls a model, and never repairs an artifact. A receipt
is a bounded historical observation, not a signature or a semantic guarantee.
"""
from __future__ import annotations

import copy
import errno
import hashlib
import json
import math
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from .tasking import parse_record
from .tools import SENSITIVE_NAMES
from .trace import utc_now

PLAN_SCHEMA = "pocketchat-acceptance-plan.v1"
RECORD_SCHEMA = "pocketchat-acceptance.v1"
DEFAULT_FILE_BYTES = 65536
MAX_FILE_BYTES = 1048576
MAX_PLAN_BYTES = 65536
MAX_RECORD_BYTES = 262144
MAX_CHECKS = 32
MAX_HISTORY = 256
ACCEPTANCE_DIR = Path(".pocketchat") / "acceptance"
STATES = ("passed", "failed", "unknown", "refused")
CHECK_KINDS = ("file_exists", "text_contains", "json_equals",
               "command_succeeded")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_ACCEPTANCE_ID = re.compile(r"accept_[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REASONS = {
    "passed": {"matched"},
    "failed": {"missing_file", "text_mismatch", "invalid_utf8", "invalid_json",
               "pointer_missing", "json_mismatch"},
    "refused": {"protected_path", "symlink", "not_regular_file", "file_too_large",
                "unsupported_platform"},
    "unknown": {"unreadable_file", "file_changed", "command_evidence_missing",
                "command_evidence_unreadable"},
}
_REASONS["failed"].update({"command_failed", "command_timed_out"})
_REASONS["refused"].update({"command_evidence_refused"})
_WITH_FILE = {"matched", "text_mismatch", "invalid_utf8", "invalid_json",
              "pointer_missing", "json_mismatch"}


class AcceptanceError(ValueError):
    """The plan, task or receipt cannot be safely interpreted or persisted."""


class _ReadIssue(Exception):
    def __init__(self, state: str, reason: str):
        self.state, self.reason = state, reason


@dataclass(frozen=True)
class AcceptancePlan:
    """A validated snapshot of the operator's finite read-only conditions."""

    source: Dict[str, Any]
    max_file_bytes: int

    @property
    def checks(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self.source["checks"])


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AcceptanceError("acceptance data must be finite UTF-8 JSON")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 64:
        raise AcceptanceError("acceptance JSON nesting exceeds 64 levels")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for child in value:
            _json_value(child, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for child in value.values():
            _json_value(child, depth + 1)
        return
    raise AcceptanceError("acceptance data must contain only finite JSON values")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AcceptanceError("duplicate JSON object key")
        result[key] = value
    return result


def _constant(_value):
    raise AcceptanceError("non-finite JSON number")


def _decode(data: bytes) -> Any:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
        _json_value(value)
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise AcceptanceError("invalid, duplicate-key or non-finite JSON")


def _closed(value: Any, required, optional=(), what="acceptance data") -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceError("%s must be an object" % what)
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise AcceptanceError("%s has missing or unknown fields" % what)
    return value


def _path(value: Any, *, allow_root=False) -> str:
    if allow_root and value == ".":
        return "."
    if (not isinstance(value, str) or not value or len(value) > 1024
            or value != value.strip() or any(ord(c) < 32 for c in value)
            or "\\" in value or ":" in value
            or any(part in ("", ".", "..") or part.endswith((".", " "))
                   for part in value.split("/"))):
        raise AcceptanceError("acceptance path must be a safe workspace-relative path")
    return value


def _pointer(value: Any) -> str:
    if (not isinstance(value, str) or len(value) > 4096
            or (value and not value.startswith("/"))
            or re.search(r"~(?:[^01]|$)", value)):
        raise AcceptanceError("json_equals requires a valid JSON Pointer")
    return value


def parse_plan(payload: Any) -> AcceptancePlan:
    """Read one closed plan, refusing unbounded or ambiguous conditions."""
    data = _closed(payload, ("schema_version", "checks"), ("max_file_bytes",), "acceptance plan")
    _json_value(data)
    if data["schema_version"] != PLAN_SCHEMA:
        raise AcceptanceError("unsupported acceptance plan schema version")
    limit = data.get("max_file_bytes", DEFAULT_FILE_BYTES)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_FILE_BYTES:
        raise AcceptanceError("max_file_bytes must be an integer between 1 and 1048576")
    checks = data["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS:
        raise AcceptanceError("acceptance plan requires 1 to 32 checks")
    seen = set()
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in CHECK_KINDS:
            raise AcceptanceError("unknown acceptance check kind")
        if check["kind"] == "command_succeeded":
            _closed(check, ("id", "kind", "step_id"), what="acceptance check")
            step_id = check["step_id"]
            if (not isinstance(step_id, str)
                    or not _IDENTIFIER.fullmatch(step_id)):
                raise AcceptanceError(
                    "command check step_id must be a short ASCII identifier")
            seen.add(check["id"])
            continue
        extra = {"file_exists": (), "text_contains": ("text",),
                 "json_equals": ("pointer", "expected")}[check["kind"]]
        _closed(check, ("id", "kind", "path") + extra, what="acceptance check")
        cid = check["id"]
        if not isinstance(cid, str) or not _IDENTIFIER.fullmatch(cid) or cid in seen:
            raise AcceptanceError("check id must be a unique short ASCII identifier")
        seen.add(cid)
        _path(check["path"])
        if check["kind"] == "text_contains":
            text = check["text"]
            if not isinstance(text, str) or not 1 <= len(text) <= 4096:
                raise AcceptanceError("text_contains text must contain 1 to 4096 characters")
        if check["kind"] == "json_equals":
            _pointer(check["pointer"])
    if len(_canonical(data)) > MAX_PLAN_BYTES:
        raise AcceptanceError("acceptance plan exceeds 65536 bytes")
    return AcceptancePlan(copy.deepcopy(data), limit)


def load_plan(path: Path) -> AcceptancePlan:
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_PLAN_BYTES + 1)
    except OSError:
        raise AcceptanceError("cannot read acceptance plan")
    if len(data) > MAX_PLAN_BYTES:
        raise AcceptanceError("acceptance plan exceeds 65536 bytes")
    return parse_plan(_decode(data))


def _read_error(exc: OSError) -> _ReadIssue:
    if exc.errno == errno.ENOENT:
        return _ReadIssue("failed", "missing_file")
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return _ReadIssue("refused", "symlink" if exc.errno == errno.ELOOP else "not_regular_file")
    return _ReadIssue("unknown", "unreadable_file")


@contextmanager
def _directory(root: Path, parts=(), *, create=False):
    """Descriptor-relative traversal refuses symlinks; no unsafe fallback."""
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise _ReadIssue("refused", "unsupported_platform")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors = []
    try:
        fd = os.dup(root) if isinstance(root, int) else os.open(str(root), flags)
        descriptors.append(fd)
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            fd = os.open(part, flags, dir_fd=fd)
            descriptors.append(fd)
        yield fd
    except OSError as exc:
        raise _read_error(exc)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _protected(path: str) -> bool:
    sensitive = {name.lower() for name in SENSITIVE_NAMES} | {
        ".pocketchat", "traces", "requests", "sessions", "pocketchat.json",
        "credentials.json", "credentials.toml", "credentials.yaml",
    }
    return any(part.lower() in sensitive or part.lower().startswith(".env")
               for part in path.split("/"))


def _read_file(root: Path, path: str, limit: int, *, protect=True) -> bytes:
    _path(path)
    if protect and _protected(path):
        raise _ReadIssue("refused", "protected_path")
    parts = path.split("/")
    with _directory(root, parts[:-1]) as parent:
        fd = None
        try:
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise _ReadIssue("refused", "symlink")
            if not stat.S_ISREG(before.st_mode):
                raise _ReadIssue("refused", "not_regular_file")
            if before.st_size > limit:
                raise _ReadIssue("refused", "file_too_large")
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise _ReadIssue("refused", "not_regular_file")
            if opened.st_size > limit:
                raise _ReadIssue("refused", "file_too_large")
            chunks, size = [], 0
            while size <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            if size > limit:
                raise _ReadIssue("refused", "file_too_large")
            after = os.fstat(fd)
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            def fingerprint(s):
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (fingerprint(before) != fingerprint(after)
                    or fingerprint(opened) != fingerprint(after)
                    or fingerprint(named) != fingerprint(after) or size != after.st_size):
                raise _ReadIssue("unknown", "file_changed")
            return b"".join(chunks)
        except OSError as exc:
            raise _read_error(exc)
        finally:
            if fd is not None:
                os.close(fd)


def _at_pointer(value: Any, pointer: str) -> Any:
    if not pointer:
        return value
    for part in pointer[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            # Bound conversion; a 4096-digit string is not a useful array index.
            if len(key) > 10 or int(key) >= len(value):
                raise KeyError(key)
            value = value[int(key)]
        else:
            raise KeyError(key)
    return value


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _aggregate(checks) -> str:
    return max((check["state"] for check in checks), key=STATES.index)


def _command_evidence(root, step_id: str) -> Dict[str, str]:
    """Read the cumulative execution view; never execute a command."""
    from .task_exec import TaskExecutionError, _parse_execution_report, cumulative_reports
    report_dir = Path(".pocketchat") / "task-reports"
    with _directory(root, report_dir.parts) as fd:
        names = sorted(os.listdir(fd))
    if len(names) > MAX_HISTORY:
        raise _ReadIssue("unknown", "command_evidence_unreadable")
    reports, snapshots = {}, {}
    for name in names:
        if not name.endswith(".json"):
            continue
        relative = (report_dir / name).as_posix()
        try:
            data = _read_file(root, relative, MAX_RECORD_BYTES, protect=False)
            reports[relative] = _parse_execution_report(_decode(data), name)
            snapshots[relative] = data
        except (_ReadIssue, AcceptanceError, TaskExecutionError):
            raise _ReadIssue("refused", "command_evidence_refused")
    reports, issues = cumulative_reports(reports, snapshots.__getitem__)
    if issues:
        raise _ReadIssue("refused", "command_evidence_refused")
    matches = [step for report in reports.values() for step in report["steps"]
               if step.get("step_id") == step_id]
    if not matches:
        raise _ReadIssue("unknown", "command_evidence_missing")
    if len(matches) != 1 or matches[0].get("action") != "run_command":
        raise _ReadIssue("refused", "command_evidence_refused")
    step = matches[0]
    status, evidence = step.get("status"), step.get("evidence")
    if status == "failed":
        if isinstance(evidence, dict) and evidence.get("timed_out"):
            raise _ReadIssue("failed", "command_timed_out")
        raise _ReadIssue("failed", "command_failed")
    if status != "completed" or not isinstance(evidence, dict):
        raise _ReadIssue("refused", "command_evidence_refused")
    if evidence.get("exit_code") != 0 or evidence.get("timed_out") is not False:
        raise _ReadIssue("refused", "command_evidence_refused")
    # New reports bind the archive digest; older reports remain readable with
    # their existing summary contract. Never silently substitute current bytes.
    if "archive_sha256" in step:
        trace = evidence.get("trace")
        if not isinstance(trace, str):
            raise _ReadIssue("refused", "command_evidence_refused")
        archive = Path(trace).parent / "command-evidence" / (Path(trace).stem + ".json")
        try:
            data = _read_file(root, archive.as_posix(), MAX_RECORD_BYTES, protect=False)
            command = _decode(data)
        except (_ReadIssue, AcceptanceError):
            raise _ReadIssue("unknown", "command_evidence_unreadable")
        if (hashlib.sha256(data).hexdigest() != step["archive_sha256"]
                or not isinstance(command, dict)
                or command.get("run_id") != step.get("run_id")
                or command.get("template_id") != evidence.get("template_id")
                or command.get("exit_code") != 0 or command.get("timed_out") is not False):
            raise _ReadIssue("refused", "command_evidence_refused")
    return {"state": "passed", "reason": "matched"}


def evaluate_plan(root: Path, plan: AcceptancePlan) -> Dict[str, Any]:
    """Observe each named path once; no artifact body or expected value escapes."""
    plan = parse_plan(plan.source)
    cache, results = {}, []
    for check in plan.checks:
        if check["kind"] == "command_succeeded":
            result = {"id": check["id"], "kind": check["kind"],
                      "step_id": check["step_id"]}
            try:
                linked = _command_evidence(root, check["step_id"])
            except _ReadIssue as exc:
                result.update(state=exc.state, reason=exc.reason)
            else:
                result.update(**linked)
            results.append(result)
            continue
        result = {key: check[key] for key in ("id", "kind", "path")}
        path = check["path"]
        if path not in cache:
            try:
                cache[path] = _read_file(root, path, plan.max_file_bytes)
            except _ReadIssue as exc:
                cache[path] = exc
        data = cache[path]
        if isinstance(data, _ReadIssue):
            result.update(state=data.state, reason=data.reason)
        else:
            result["file"] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            state, reason = "passed", "matched"
            if check["kind"] == "text_contains":
                try:
                    if check["text"] not in data.decode("utf-8"):
                        state, reason = "failed", "text_mismatch"
                except UnicodeError:
                    state, reason = "failed", "invalid_utf8"
            elif check["kind"] == "json_equals":
                try:
                    actual = _at_pointer(_decode(data), check["pointer"])
                    if not _equal(actual, check["expected"]):
                        state, reason = "failed", "json_mismatch"
                except AcceptanceError:
                    state, reason = "failed", "invalid_json"
                except KeyError:
                    state, reason = "failed", "pointer_missing"
            result.update(state=state, reason=reason)
        results.append(result)
    return {"state": _aggregate(results), "checks": results}


def _task_context(workspace: Path, task_id: str):
    if not isinstance(task_id, str) or not re.fullmatch(r"task_[A-Za-z0-9_-]{1,59}", task_id):
        raise AcceptanceError("invalid acceptance task id")
    path = ".pocketchat/tasks/%s.json" % task_id
    try:
        data = _read_file(workspace, path, MAX_RECORD_BYTES, protect=False)
        record = parse_record(_decode(data), task_id=task_id)
        if data != _read_file(workspace, path, MAX_RECORD_BYTES, protect=False):
            raise AcceptanceError("task record changed during acceptance")
    except _ReadIssue as exc:
        raise AcceptanceError("task record cannot be read: %s" % exc.reason)
    _path(record.workspace_root, allow_root=True)
    return record, _digest(_decode(data))


def _public_receipt(receipt: Dict[str, Any], path: str) -> Dict[str, Any]:
    return {key: receipt[key] for key in (
        "acceptance_id", "task_id", "recorded", "plan_sha256", "task_record_sha256",
        "execution_state", "state", "checks")} | {"record": path}


def verify_task(workspace: Path, task_id: str, plan: AcceptancePlan) -> Dict[str, Any]:
    """Append one independent acceptance; never modify the completed task."""
    workspace = Path(workspace).resolve()
    plan = parse_plan(plan.source)
    record, task_sha = _task_context(workspace, task_id)
    if record.state != "completed":
        raise AcceptanceError("acceptance requires a completed task")
    root = record.workspace_root
    # The evaluation opens the root without following links. A nested root is
    # traversed relative to the workspace descriptor before any artifact read.
    try:
        parts = () if root == "." else tuple(root.split("/"))
        if root != "." and _protected(root):
            raise _ReadIssue("refused", "protected_path")
        with _directory(workspace, parts) as fd:
            # Reads duplicate this descriptor; replacing the named root cannot
            # redirect the observed artifact path to another directory.
            result = evaluate_plan(fd, plan)
    except _ReadIssue as exc:
        checks = []
        for c in plan.checks:
            keys = ("id", "kind", "path") if c["kind"] != "command_succeeded" else ("id", "kind", "step_id")
            checks.append(dict((key, c[key]) for key in keys))
        for check in checks:
            check.update(state=exc.state, reason=exc.reason)
        result = {"state": _aggregate(checks), "checks": checks}
    _, after_sha = _task_context(workspace, task_id)
    if task_sha != after_sha:
        raise AcceptanceError("task record changed during acceptance")
    receipt = {
        "schema_version": RECORD_SCHEMA, "acceptance_id": "accept_%s" % uuid4().hex,
        "task_id": task_id, "task_record_sha256": task_sha,
        "policy_sha256": record.policy_sha256, "workspace_root": root,
        "execution_state": record.state, "recorded": utc_now(),
        "plan": plan.source, "plan_sha256": _digest(plan.source), **result,
    }
    relative = ACCEPTANCE_DIR / task_id / (receipt["acceptance_id"] + ".json")
    try:
        with _directory(workspace, relative.parts[:-1], create=True) as parent:
            fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(_canonical(receipt) + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                os.unlink(relative.name, dir_fd=parent)
                raise
    except (_ReadIssue, OSError):
        raise AcceptanceError("cannot safely persist acceptance receipt")
    return _public_receipt(receipt, relative.as_posix())


def _recorded_time(value: Any) -> datetime:
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError()
        return date.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise AcceptanceError("acceptance receipt has no valid recorded time")


def _validate_receipt(payload: Any, record, task_sha: str, name: str):
    required = ("schema_version", "acceptance_id", "task_id", "task_record_sha256",
                "policy_sha256", "workspace_root", "execution_state", "recorded",
                "plan", "plan_sha256", "state", "checks")
    value = _closed(payload, required, what="acceptance receipt")
    if (value["schema_version"] != RECORD_SCHEMA
            or not isinstance(value["acceptance_id"], str)
            or not _ACCEPTANCE_ID.fullmatch(value["acceptance_id"])
            or name != value["acceptance_id"] + ".json"
            or value["task_id"] != record.task_id
            or value["task_record_sha256"] != task_sha
            or value["policy_sha256"] != record.policy_sha256
            or value["workspace_root"] != record.workspace_root
            or value["execution_state"] != "completed" or record.state != "completed"):
        raise AcceptanceError("acceptance receipt identity conflicts with task")
    _recorded_time(value["recorded"])
    plan = parse_plan(value["plan"])
    if value["plan_sha256"] != _digest(plan.source):
        raise AcceptanceError("acceptance receipt plan digest does not match")
    checks = value["checks"]
    if not isinstance(checks, list) or len(checks) != len(plan.checks):
        raise AcceptanceError("acceptance receipt has incomplete check evidence")
    for check, declared in zip(checks, plan.checks):
        identity = (("id", "kind", "step_id") if declared["kind"] == "command_succeeded"
                    else ("id", "kind", "path"))
        _closed(check, identity + ("state", "reason"), ("file",), "acceptance result")
        if any(check[key] != declared[key] for key in identity):
            raise AcceptanceError("acceptance result does not match its declared check")
        state, reason = check["state"], check["reason"]
        if (not isinstance(state, str) or state not in STATES
                or not isinstance(reason, str) or reason not in _REASONS[state]):
            raise AcceptanceError("acceptance result has an invalid state or reason")
        if ("file" in check) != (reason in _WITH_FILE):
            raise AcceptanceError("acceptance result has inconsistent file evidence")
        if "file" in check:
            file = _closed(check["file"], ("bytes", "sha256"), what="acceptance file evidence")
            size, digest = file["bytes"], file["sha256"]
            if (isinstance(size, bool) or not isinstance(size, int)
                    or not 0 <= size <= plan.max_file_bytes
                    or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
                raise AcceptanceError("acceptance result has invalid byte evidence")
    if value["state"] != _aggregate(checks):
        raise AcceptanceError("acceptance aggregate conflicts with its checks")
    return value


def acceptance_history(workspace: Path, task_id: str) -> Dict[str, Any]:
    """Read historical receipts, never re-evaluate or quietly omit bad evidence."""
    workspace = Path(workspace).resolve()
    record, task_sha = _task_context(workspace, task_id)
    base = ACCEPTANCE_DIR / task_id
    result = {"state": "not_recorded", "records": [], "evidence_issues": []}
    try:
        with _directory(workspace, base.parts) as fd:
            names = os.listdir(fd)
    except _ReadIssue as exc:
        if exc.reason != "missing_file":
            result["state"] = exc.state
            result["evidence_issues"].append({"record": base.as_posix(), "state": exc.state,
                                              "reason": exc.reason})
        return result
    if len(names) > MAX_HISTORY:
        return {"state": "unknown", "records": [], "evidence_issues": [
            {"record": base.as_posix(), "state": "unknown", "reason": "history_limit_exceeded"}]}
    for name in sorted(names):
        path = base / name
        try:
            data = _read_file(workspace, path.as_posix(), MAX_RECORD_BYTES, protect=False)
            receipt = _validate_receipt(_decode(data), record, task_sha, name)
            result["records"].append(_public_receipt(receipt, path.as_posix()))
        except (_ReadIssue, AcceptanceError) as exc:
            state = exc.state if isinstance(exc, _ReadIssue) else "refused"
            reason = exc.reason if isinstance(exc, _ReadIssue) else "invalid_receipt"
            result["evidence_issues"].append({"record": path.as_posix(), "state": state, "reason": reason})
    result["records"].sort(key=lambda r: (_recorded_time(r["recorded"]), r["acceptance_id"]))
    if result["evidence_issues"]:
        result["state"] = "refused" if any(i["state"] == "refused" for i in result["evidence_issues"]) else "unknown"
    elif result["records"]:
        latest_time = _recorded_time(result["records"][-1]["recorded"])
        latest = [r for r in result["records"] if _recorded_time(r["recorded"]) == latest_time]
        if len({r["state"] for r in latest}) > 1:
            result["state"] = "unknown"
            result["evidence_issues"].append({"record": base.as_posix(), "state": "unknown",
                                              "reason": "ambiguous_record_order"})
        else:
            result["state"] = latest[-1]["state"]
    return result
