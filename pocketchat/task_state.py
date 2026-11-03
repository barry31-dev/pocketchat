"""Pure reconstruction of the evidence visible to a later plan step.

Only completed and explicitly depended-on steps enter the projection. A model's
own words are never completion evidence; the report digests and step state are.
"""

from __future__ import annotations

import json
from pathlib import PureWindowsPath
from typing import Any

MAX_TASK_STATE_CHARS = 12000


class TaskStateError(ValueError):
    def __init__(self, message: str,
                 error_type: str = "invalid_task_state") -> None:
        super().__init__(message)
        self.error_type = error_type


def _sha(value: Any, name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise TaskStateError(f"{name} must be a lowercase digest")
    return value


def _path(value: Any) -> str:
    if (not isinstance(value, str) or not value
            or value.startswith("/") or "\\" in value
            or ".." in value.split("/")):
        raise TaskStateError("file evidence path must be workspace-relative")
    return value


def _workspace_reference(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    windows = PureWindowsPath(value)
    return not windows.is_absolute() and not windows.drive


def project_task_state(report: Any, *, step_id: str,
                       depends_on: list[str],
                       allowed_paths: list[str] | None = None,
                       max_chars: int = MAX_TASK_STATE_CHARS) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise TaskStateError("execution report must be an object")
    if not isinstance(step_id, str) or not step_id:
        raise TaskStateError("step_id must be non-empty")
    if (not isinstance(depends_on, list)
            or any(not isinstance(item, str) or not item for item in depends_on)):
        raise TaskStateError("depends_on must contain step ids")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise TaskStateError("max_chars must be a positive integer")
    spec_sha = _sha(report.get("spec_sha256"), "spec_sha256")
    plan_sha = _sha(report.get("plan_sha256"), "plan_sha256")
    if report.get("evidence_issues"):
        raise TaskStateError("execution report has unresolved evidence issues",
                             error_type="evidence_issue")
    rows = report.get("steps")
    if not isinstance(rows, list):
        raise TaskStateError("execution report steps must be a list")
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("step_id"), str):
            raise TaskStateError("execution report has a malformed step")
        by_id[row["step_id"]] = row

    evidence = []
    for dependency in depends_on:
        row = by_id.get(dependency)
        if row is None or row.get("status") != "completed":
            raise TaskStateError(
                f"dependency {dependency} is not bound to completed evidence",
                error_type="dependency_not_completed")
        item = {
            "step_id": dependency,
            "status": "completed",
        }
        for key in ("task_id", "trace", "session"):
            value = row.get(key)
            if _workspace_reference(value):
                item[key] = value
        inputs = row.get("inputs")
        if isinstance(inputs, list):
            item["inputs"] = [value for value in inputs
                              if isinstance(value, str)]
        evidence.append(item)

    declared = set(allowed_paths or [])
    files = []
    for change in report.get("changes", []):
        if not isinstance(change, dict):
            raise TaskStateError("execution report has malformed file evidence")
        path = _path(change.get("path"))
        if declared and path not in declared:
            raise TaskStateError("file evidence is outside the step allowlist")
        item = {"path": path}
        if isinstance(change.get("sha256"), str):
            _sha(change["sha256"], "change sha256")
            item["sha256"] = change["sha256"]
        files.append(item)

    state = {
        "schema_version": "pocketchat-task-state.v1",
        "spec_sha256": spec_sha,
        "plan_sha256": plan_sha,
        "step_id": step_id,
        "evidence": evidence,
        "files": files,
        "unverified": [],
    }
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True)
    if len(encoded) > max_chars:
        raise TaskStateError("task state exceeds the configured character limit",
                             error_type="task_state_too_large")
    return state
