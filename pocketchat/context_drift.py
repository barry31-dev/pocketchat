"""Pure drift checks for a selected context snapshot.

The caller supplies both the frozen snapshot and current path observations.
This module never scans the workspace, refreshes the snapshot, or carries a
mismatched authorization forward. It compares only the files that were
selected; an unselected path is outside this check.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

from .task_context import CONTEXT_TREE_SCHEMA, MAX_FILES
from .task_selection import MAX_QUERY_TERMS, MAX_SELECTED_FILES
from .task_snapshot import (CONTEXT_SNAPSHOT_SCHEMA, MAX_SNAPSHOT_BYTES,
                            MAX_SNAPSHOT_FILE_BYTES, _snapshot_digest)

SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketchat-context-drift-report.v1"
DEFAULT_MAX_OBSERVATIONS = 256
DEFAULT_MAX_REPORT_BYTES = 64 * 1024
ENTRY_TYPES = frozenset({
    "regular_file", "missing", "symlink", "directory", "other",
})
SNAPSHOT_FIELDS = frozenset({
    "schema_version", "tree_schema", "tree_sha256", "selection_sha256",
    "query_terms", "selection_truncated", "total_matches", "selected_files",
    "max_bytes", "total_bytes", "selection_size_changed_files", "files",
    "snapshot_sha256",
})
FILE_FIELDS = frozenset({
    "path", "selection_bytes", "bytes", "selection_size_changed",
    "sha256", "text",
})
OBSERVATION_FIELDS = frozenset({"coverage", "files"})
OBSERVED_FILE_FIELDS = frozenset({"path", "entry_type", "bytes", "sha256"})


class ContextDriftError(ValueError):
    """A drift request cannot be evaluated under its declared contract."""


def _refuse(message: str) -> ContextDriftError:
    return ContextDriftError("context drift refused: %s" % message)


def _closed(value: Any, allowed: frozenset, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _refuse("%s must be an object" % what)
    unexpected = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if unexpected or missing:
        parts = []
        if unexpected:
            parts.append("unexpected field(s): %s" % ", ".join(unexpected))
        if missing:
            parts.append("missing field(s): %s" % ", ".join(missing))
        raise _refuse("%s has %s" % (what, "; ".join(parts)))
    return value


def _integer(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _refuse("%s must be a non-negative integer" % what)
    return value


def _positive_integer(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _refuse("%s must be a positive integer" % what)
    return value


def _path(value: Any, what: str) -> str:
    if (not isinstance(value, str) or not value or "\x00" in value
            or value.startswith("/") or "\\" in value):
        raise _refuse("%s must be a non-empty workspace-relative path" % what)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _refuse("%s must be a normalized workspace-relative path" % what)
    return value


def _digest(value: Any, what: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise _refuse("%s must be a lowercase SHA-256 digest" % what)
    return value


def _sort_key(row: Dict[str, str]) -> Tuple[str, str]:
    return (row["path"], row["reason"])


def _canonical_size(value: Any) -> int:
    body = {key: item for key, item in value.items()
            if key not in ("report_sha256", "report_bytes")}
    return len(json.dumps(body, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def _report_digest(report: Dict[str, Any]) -> str:
    body = {key: value for key, value in report.items()
            if key != "report_sha256"}
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_snapshot(value: Any) -> Tuple[List[Dict[str, Any]], str]:
    snapshot = _closed(value, SNAPSHOT_FIELDS, "snapshot")
    if snapshot["schema_version"] != CONTEXT_SNAPSHOT_SCHEMA:
        raise _refuse("snapshot has the wrong schema")
    if snapshot["tree_schema"] != CONTEXT_TREE_SCHEMA:
        raise _refuse("snapshot refers to the wrong context tree schema")
    query_terms = snapshot["query_terms"]
    if (not isinstance(query_terms, list)
            or len(query_terms) > MAX_QUERY_TERMS
            or any(not isinstance(term, str) or not term or "\x00" in term
                   or len(term) > 128 for term in query_terms)
            or query_terms != sorted(set(query_terms))):
        raise _refuse("snapshot query_terms must be bounded sorted unique strings")
    files = snapshot["files"]
    if not isinstance(files, list):
        raise _refuse("snapshot files must be a list")
    parsed: List[Dict[str, Any]] = []
    total_bytes = 0
    changed_files = 0
    paths = []
    for index, raw in enumerate(files):
        row = _closed(raw, FILE_FIELDS, "snapshot file[%d]" % index)
        path = _path(row["path"], "snapshot file path")
        selection_bytes = _integer(
            row["selection_bytes"], "snapshot selection_bytes")
        byte_count = _integer(row["bytes"], "snapshot bytes")
        if not isinstance(row["selection_size_changed"], bool):
            raise _refuse("snapshot selection_size_changed must be a boolean")
        digest = _digest(row["sha256"], "snapshot file sha256")
        text = row["text"]
        if not isinstance(text, str) or "\x00" in text:
            raise _refuse("snapshot file text must be a NUL-free string")
        encoded = text.encode("utf-8")
        if len(encoded) != byte_count:
            raise _refuse("snapshot file bytes do not match its text")
        if byte_count > MAX_SNAPSHOT_FILE_BYTES:
            raise _refuse("snapshot file exceeds the producer file limit")
        if hashlib.sha256(encoded).hexdigest() != digest:
            raise _refuse("snapshot file sha256 does not match its text")
        if row["selection_size_changed"] != (
                selection_bytes != byte_count):
            raise _refuse("snapshot size-change flag contradicts its counts")
        parsed.append(row)
        total_bytes += byte_count
        changed_files += bool(row["selection_size_changed"])
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise _refuse("snapshot files must not contain duplicate paths")
    for name in (
        "total_matches", "selected_files", "max_bytes", "total_bytes",
        "selection_size_changed_files",
    ):
        _integer(snapshot[name], "snapshot %s" % name)
    if snapshot["selected_files"] != len(parsed):
        raise _refuse("snapshot selected_files contradict its file rows")
    if len(parsed) > MAX_SELECTED_FILES:
        raise _refuse("snapshot selected_files exceeds the producer limit")
    if snapshot["total_matches"] < len(parsed):
        raise _refuse("snapshot total_matches is smaller than selected files")
    if snapshot["total_matches"] > MAX_FILES:
        raise _refuse("snapshot total_matches exceeds the producer limit")
    if not snapshot["selection_truncated"] and snapshot["total_matches"] != len(parsed):
        raise _refuse("snapshot total_matches contradicts an untruncated selection")
    if snapshot["selection_truncated"] and snapshot["total_matches"] <= len(parsed):
        raise _refuse("snapshot total_matches contradicts a truncated selection")
    if snapshot["total_bytes"] != total_bytes:
        raise _refuse("snapshot total_bytes contradicts its file rows")
    if snapshot["selection_size_changed_files"] != changed_files:
        raise _refuse("snapshot size-change count contradicts its file rows")
    if not 1 <= snapshot["max_bytes"] <= MAX_SNAPSHOT_BYTES:
        raise _refuse("snapshot max_bytes is outside the producer limit")
    if snapshot["max_bytes"] < total_bytes:
        raise _refuse("snapshot total_bytes exceeds max_bytes")
    if not isinstance(snapshot["selection_truncated"], bool):
        raise _refuse("snapshot selection_truncated must be a boolean")
    for name in ("tree_sha256", "selection_sha256", "snapshot_sha256"):
        _digest(snapshot[name], "snapshot %s" % name)
    if _snapshot_digest(snapshot) != snapshot["snapshot_sha256"]:
        raise _refuse("snapshot digest does not bind its body")
    return parsed, snapshot["snapshot_sha256"]


def _validate_observation(value: Any) -> Tuple[str, List[Dict[str, Any]]]:
    observed = _closed(value, OBSERVATION_FIELDS, "observation")
    coverage = observed["coverage"]
    if coverage not in ("complete", "partial"):
        raise _refuse("observation coverage must be complete or partial")
    files = observed["files"]
    if not isinstance(files, list):
        raise _refuse("observation files must be a list")
    parsed = []
    seen = set()
    for index, raw in enumerate(files):
        row = _closed(raw, OBSERVED_FILE_FIELDS, "observation file[%d]" % index)
        path = _path(row["path"], "observation file path")
        if path in seen:
            raise _refuse("observation contains duplicate paths")
        seen.add(path)
        entry_type = row["entry_type"]
        if entry_type not in ENTRY_TYPES:
            raise _refuse("observation entry_type is unknown")
        if entry_type == "regular_file":
            byte_count = _integer(row["bytes"], "observation bytes")
            digest = _digest(row["sha256"], "observation sha256")
        else:
            if row["bytes"] is not None or row["sha256"] is not None:
                raise _refuse(
                    "non-regular observation rows must carry null bytes and sha256")
            byte_count = None
            digest = None
        parsed.append({
            "path": path,
            "entry_type": entry_type,
            "bytes": byte_count,
            "sha256": digest,
        })
    return coverage, parsed


def _make_report(*, status: str, snapshot_digest: str, coverage: str,
                 checked_files: int, unchanged_files: int,
                 changes: List[Dict[str, str]], unknowns: List[Dict[str, str]],
                 ignored_observations: int, max_observations: int,
                 max_report_bytes: int) -> Dict[str, Any]:
    changes = sorted(changes, key=_sort_key)
    unknowns = sorted(unknowns, key=_sort_key)
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "snapshot_sha256": snapshot_digest,
        "coverage": coverage,
        "checked_files": checked_files,
        "unchanged_files": unchanged_files,
        "changes": changes,
        "unknowns": unknowns,
        "ignored_observations": ignored_observations,
        "next_action": {
            "allow": "continue", "refused": "replan", "unknown": "reobserve",
        }[status],
        "limits": {
            "observations": max_observations,
            "report_bytes": max_report_bytes,
        },
    }
    report["report_sha256"] = _report_digest(report)
    return report


def check_context_drift(snapshot: Any, observation: Any, *,
                        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
                        max_report_bytes: int = DEFAULT_MAX_REPORT_BYTES
                        ) -> Dict[str, Any]:
    """Compare a frozen selection with supplied current observations."""
    max_observations = _positive_integer(max_observations, "max_observations")
    max_report_bytes = _positive_integer(max_report_bytes, "max_report_bytes")
    selected, snapshot_digest = _validate_snapshot(snapshot)
    coverage, observed_rows = _validate_observation(observation)
    observed = {row["path"]: row for row in observed_rows}
    selected_paths = {row["path"] for row in selected}
    changes: List[Dict[str, str]] = []
    unknowns: List[Dict[str, str]] = []
    checked = 0
    unchanged = 0
    for frozen in selected:
        path = frozen["path"]
        current = observed.get(path)
        if current is None:
            reason = ("unobserved" if coverage == "partial"
                      else "observation_incomplete")
            unknowns.append({"path": path, "reason": reason})
            continue
        checked += 1
        if current["entry_type"] == "missing":
            changes.append({"path": path, "reason": "path_missing"})
        elif current["entry_type"] != "regular_file":
            changes.append({"path": path, "reason": "entry_type_changed"})
        elif (current["bytes"] != frozen["bytes"]
              or current["sha256"] != frozen["sha256"]):
            changes.append({"path": path, "reason": "content_changed"})
        else:
            unchanged += 1
    ignored = sum(1 for path in observed if path not in selected_paths)
    if len(observed_rows) > max_observations:
        for row in observed_rows[max_observations:]:
            unknowns.append({
                "path": row["path"], "reason": "observation_limit",
            })
    if coverage == "partial" and not unknowns:
        unknowns.append({"path": "*", "reason": "coverage_partial"})
    status = "refused" if changes else (
        "refused" if any(row["reason"] in ("observation_incomplete",
                                          "observation_limit")
                         for row in unknowns)
        else "unknown" if unknowns else "allow")
    report = _make_report(
        status=status, snapshot_digest=snapshot_digest, coverage=coverage,
        checked_files=checked, unchanged_files=unchanged,
        changes=changes, unknowns=unknowns,
        ignored_observations=ignored,
        max_observations=max_observations,
        max_report_bytes=max_report_bytes,
    )
    if _canonical_size(report) > max_report_bytes:
        return _make_report(
            status="refused", snapshot_digest=snapshot_digest,
            coverage=coverage, checked_files=checked,
            unchanged_files=unchanged, changes=[],
            unknowns=[{"path": "*", "reason": "report_limit"}],
            ignored_observations=ignored,
            max_observations=max_observations,
            max_report_bytes=max_report_bytes)
    return report
