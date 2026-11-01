"""Parent-plan-authorized child delegation for task agents.

Delegation narrows an already authorized parent plan into a finite set of
child requests. A child may only use an action and inputs the parent plan
already granted; anything wider is refused before a provider call. The module
is read-only with respect to the workspace: it authorizes and records a
request, and never runs a step itself.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, List

DELEGATION_REQUEST_SCHEMA = "pocketchat-task-delegation.v1"
MAX_CHILDREN = 32
_MAX_OBJECTIVE_CHARS = 512
_STEP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HEX = frozenset("0123456789abcdef")


class TaskDelegateError(ValueError):
    """A delegation request exceeds its parent plan or is malformed."""


def _digest(payload: Any) -> str:
    import hashlib
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _sha(value: Any, what: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HEX for char in value)):
        raise TaskDelegateError("%s must be a lowercase SHA-256" % what)
    return value


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskDelegateError("%s must be an object" % what)
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
        raise TaskDelegateError("%s has %s" % (what, "; ".join(detail)))


def _text(value: Any, what: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise TaskDelegateError("%s must be a non-empty string <= %d characters"
                                % (what, maximum))
    return value


def _relative(value: Any, what: str) -> str:
    text = _text(value, what, 1024)
    if text.startswith("/") or "\\" in text or "\x00" in text:
        raise TaskDelegateError("%s must be workspace-relative" % what)
    parts = text.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TaskDelegateError(
            "%s must not contain empty, '.' or '..' segments" % what)
    return text


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TaskDelegateError("duplicate JSON member: %s" % key)
        value[key] = item
    return value


_CHILD_KEYS = {"child_id", "objective", "action", "inputs"}
_ALLOWED_KEYS = {"schema_version", "delegation_id", "parent_plan_sha256",
                 "parent_step_id", "children"}


def _child(raw: Any, index: int) -> Dict[str, Any]:
    row = _object(raw, "children[%d]" % index)
    _closed(row, _CHILD_KEYS, "children[%d]" % index)
    child_id = _text(row["child_id"], "child id", 64)
    if not _STEP_ID.fullmatch(child_id):
        raise TaskDelegateError("child id must be a bounded identifier")
    action = row["action"]
    if action not in ("read_file", "write_file", "memory_search"):
        raise TaskDelegateError(
            "child %s uses an action a parent plan cannot grant" % child_id)
    inputs = row["inputs"]
    if not isinstance(inputs, list) or not inputs or len(inputs) > 32:
        raise TaskDelegateError("child %s needs 1-32 declared inputs" % child_id)
    paths = [_relative(item, "child %s input" % child_id) for item in inputs]
    return {
        "child_id": child_id,
        "objective": _text(row["objective"], "child objective",
                           _MAX_OBJECTIVE_CHARS),
        "action": action,
        "inputs": paths,
    }


def parse_delegation_request(payload: Any) -> Dict[str, Any]:
    """Validate one closed delegation request without executing anything."""
    record = _object(payload, "delegation request")
    _closed(record, _ALLOWED_KEYS, "delegation request")
    if record["schema_version"] != DELEGATION_REQUEST_SCHEMA:
        raise TaskDelegateError("delegation request has the wrong schema")
    children_raw = record["children"]
    if not isinstance(children_raw, list) or not children_raw:
        raise TaskDelegateError("a delegation needs at least one child")
    if len(children_raw) > MAX_CHILDREN:
        raise TaskDelegateError("a delegation may not exceed %d children"
                                % MAX_CHILDREN)
    children = [_child(raw, index) for index, raw in enumerate(children_raw)]
    ids = [row["child_id"] for row in children]
    if len(ids) != len(set(ids)):
        raise TaskDelegateError("child ids must be unique")
    return {
        "schema_version": DELEGATION_REQUEST_SCHEMA,
        "delegation_id": _text(record["delegation_id"], "delegation id", 64),
        "parent_plan_sha256": _sha(record["parent_plan_sha256"],
                                   "parent_plan_sha256"),
        "parent_step_id": _text(record["parent_step_id"], "parent step id", 64),
        "children": children,
    }


def _parent_grant(execution, step_id: str):
    for step in execution.steps:
        if step.id == step_id:
            return step
    return None


def _allowed_paths(parsed, action: str) -> List[str]:
    tools = parsed.source.get("tools", {})
    entry = tools.get(action)
    if not isinstance(entry, dict):
        return []
    return list(entry.get("allow_paths", []))


def _covered(path: str, allowed: List[str]) -> bool:
    for root in allowed:
        if path == root or path.startswith(root.rstrip("/") + "/"):
            return True
    return False


def authorize_delegation(payload: Any, execution, parsed) -> Dict[str, Any]:
    """Bind a delegation request to the parent plan it may not exceed.

    ``execution`` is the authorized ``ExecutionPlan`` and ``parsed`` the
    ``TaskSpec`` it came from. Every child must use an action the spec grants
    and inputs inside that action's allowlist; the parent step must exist.
    Nothing here runs a child.
    """
    request = parse_delegation_request(payload)
    if execution is None or not hasattr(execution, "steps"):
        raise TaskDelegateError("authorization needs an authorized ExecutionPlan")
    if request["parent_plan_sha256"] != getattr(execution, "plan_sha256", ""):
        raise TaskDelegateError(
            "delegation names a different parent plan digest")
    step = _parent_grant(execution, request["parent_step_id"])
    if step is None:
        raise TaskDelegateError(
            "parent step %r is not part of this plan" % request["parent_step_id"])
    children = []
    for raw in request["children"]:
        action = raw["action"]
        if action not in parsed.source.get("tools", {}):
            raise TaskDelegateError(
                "child %s uses %s, which the TaskSpec does not grant"
                % (raw["child_id"], action))
        if action == "memory_search" and len(raw["inputs"]) != 1:
            raise TaskDelegateError(
                "child %s memory_search needs exactly one query"
                % raw["child_id"])
        allowed = _allowed_paths(parsed, action)
        if action != "memory_search":
            for path in raw["inputs"]:
                if not _covered(path, allowed):
                    raise TaskDelegateError(
                        "child %s input %s is outside the parent allowlist"
                        % (raw["child_id"], path))
        if action == "write_file":
            overwrite = list(parsed.source.get("overwrite_paths", []))
            for path in raw["inputs"]:
                if path not in overwrite and not _covered(path, allowed):
                    raise TaskDelegateError(
                        "child %s write target %s is not authorized"
                        % (raw["child_id"], path))
        children.append({
            "child_id": raw["child_id"],
            "parent_step_id": request["parent_step_id"],
            "objective": raw["objective"],
            "action": action,
            "inputs": raw["inputs"],
        })
    return {
        "schema_version": DELEGATION_REQUEST_SCHEMA,
        "delegation_id": request["delegation_id"],
        "parent_plan_sha256": request["parent_plan_sha256"],
        "parent_spec_sha256": _digest(parsed.source),
        "parent_step_id": request["parent_step_id"],
        "children": children,
    }


def load_delegation_request(path: Path) -> Dict[str, Any]:
    target = Path(path)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if target.is_symlink():
            raise TaskDelegateError("delegation request must not be a symlink")
        descriptor = os.open(str(target), flags)
    except TaskDelegateError:
        raise
    except OSError as exc:
        raise TaskDelegateError("cannot open delegation request: %s" % exc)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskDelegateError("delegation request must be a regular file")
        if info.st_size > 1024 * 1024:
            raise TaskDelegateError("delegation request exceeds 1 MiB")
        chunks = []
        remaining = 1024 * 1024 + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:
        raise TaskDelegateError("cannot read delegation request: %s" % exc)
    finally:
        os.close(descriptor)
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except TaskDelegateError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise TaskDelegateError(
            "delegation request is not valid UTF-8 JSON: %s" % exc)
