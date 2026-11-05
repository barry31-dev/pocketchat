"""Deterministic comparison evidence for two supplied context snapshots.

This module does not read files, inspect Git, or refresh either snapshot. Its
report can show only what the two supplied ``pocketchat-task-context-snapshot.v1``
records contain. It never claims that unselected paths were unchanged.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import posixpath
from typing import Any, Dict, Tuple

from .task_context import CONTEXT_TREE_SCHEMA
from .task_snapshot import CONTEXT_SNAPSHOT_SCHEMA

REPORT_SCHEMA = "pocketchat-task-diff.v1"
MAX_FILES = 4096
MAX_REPORT_BYTES = 64 * 1024
DEFAULT_MAX_FILES = 512
DEFAULT_MAX_BYTES = 64 * 1024
RELATIVE_PATH_MAX = 1024

_SNAPSHOT_KEYS = frozenset({
    "schema_version", "tree_schema", "tree_sha256", "selection_sha256",
    "query_terms", "selection_truncated", "total_matches", "selected_files",
    "max_bytes", "total_bytes", "selection_size_changed_files", "files",
    "snapshot_sha256",
})
_FILE_KEYS = frozenset({
    "path", "selection_bytes", "bytes", "selection_size_changed", "sha256",
    "text",
})


class TaskDiffError(ValueError):
    """A task-diff request or supplied snapshot cannot be trusted."""

    def __init__(self, error_type: str, category: str, detail: str,
                 *, path: str = "") -> None:
        super().__init__(detail)
        self.error_type = error_type
        self.category = category
        self.detail = detail
        self.path = path


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _is_digest(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def _integer(value: Any, name: str, *, low: int = 0,
             high: int = 2 ** 63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise TaskDiffError("invalid_limits", "limit",
                            "%s must be an integer between %d and %d"
                            % (name, low, high))
    return value


def _safe_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    if value.startswith(("/", "~")) or len(value) > RELATIVE_PATH_MAX:
        return False
    return posixpath.normpath(value) == value and value != "." and not value.startswith("../")


def _omission(reason: str, detail: str, *, path: str = "") -> Dict[str, str]:
    return {"reason": reason, "detail": detail, "path": path}


def _validate_snapshot(payload: Any, label: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s must be an object" % label)
    unknown = sorted(set(payload) - _SNAPSHOT_KEYS)
    missing = sorted(_SNAPSHOT_KEYS - set(payload))
    if unknown or missing:
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s does not match the closed snapshot schema" % label)
    if payload.get("schema_version") != CONTEXT_SNAPSHOT_SCHEMA:
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s has an unknown snapshot schema" % label)
    if payload.get("tree_schema") != CONTEXT_TREE_SCHEMA:
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s has an unknown tree schema" % label)
    for field in ("tree_sha256", "selection_sha256", "snapshot_sha256"):
        if not _is_digest(payload.get(field)):
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.%s must be lowercase SHA-256" % (label, field))
    if not isinstance(payload.get("selection_truncated"), bool):
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s.selection_truncated must be a boolean" % label)
    for field in ("selection_size_changed_files", "total_matches",
                  "selected_files", "max_bytes", "total_bytes"):
        try:
            _integer(payload.get(field), "%s.%s" % (label, field))
        except TaskDiffError:
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.%s must be a non-negative integer"
                                % (label, field))
    terms = payload.get("query_terms")
    if (not isinstance(terms, list) or not terms
            or any(not isinstance(term, str) or not term for term in terms)
            or terms != sorted(set(terms))):
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s.query_terms must be a sorted unique non-empty list"
                            % label)
    files = payload.get("files")
    if not isinstance(files, list):
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s.files must be a list" % label)
    if payload["selected_files"] != len(files):
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.selected_files does not match files length" % label)
    if payload["total_matches"] < len(files):
        raise TaskDiffError("invalid_snapshot", "structure",
                            "%s.total_matches is smaller than selected files" % label)
    if payload["selection_truncated"] != (payload["total_matches"] > len(files)):
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.selection_truncated contradicts total_matches"
                            % label)
    if payload["selection_size_changed_files"] > len(files):
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.selection_size_changed_files exceeds files"
                            % label)
    if payload["total_bytes"] != sum(row.get("bytes", -1) if isinstance(row, dict) else -1
                                     for row in files):
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.total_bytes does not match file rows" % label)
    if payload["total_bytes"] > payload["max_bytes"]:
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.total_bytes exceeds max_bytes" % label)
    changed_rows = sum(1 for row in files
                       if isinstance(row, dict)
                       and row.get("selection_size_changed") is True)
    if payload["selection_size_changed_files"] != changed_rows:
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.selection_size_changed_files does not match file rows"
                            % label)
    body = {key: value for key, value in payload.items()
            if key != "snapshot_sha256"}
    if _digest(body) != payload["snapshot_sha256"]:
        raise TaskDiffError("snapshot_contradiction", "structure",
                            "%s.snapshot_sha256 does not match its body" % label)
    seen = set()
    normalized = []
    for index, raw in enumerate(files):
        if not isinstance(raw, dict):
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.files[%d] must be an object" % (label, index))
        if set(raw) != _FILE_KEYS:
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.files[%d] has a non-closed shape"
                                % (label, index))
        path = raw["path"]
        if not _safe_path(path):
            raise TaskDiffError("unsafe_path", "structure",
                                "%s.files[%d].path is not workspace-relative"
                                % (label, index), path=path if isinstance(path, str) else "")
        if path in seen:
            raise TaskDiffError("duplicate_path", "structure",
                                "%s contains duplicate path %s" % (label, path),
                                path=path)
        seen.add(path)
        for field in ("selection_bytes", "bytes"):
            try:
                _integer(raw[field], "%s.files[%d].%s" % (label, index, field))
            except TaskDiffError:
                raise TaskDiffError("invalid_snapshot", "structure",
                                    "%s.files[%d].%s must be a non-negative integer"
                                    % (label, index, field), path=path)
        if not isinstance(raw["selection_size_changed"], bool):
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.files[%d].selection_size_changed must be boolean"
                                % (label, index), path=path)
        if raw["selection_size_changed"] != (raw["selection_bytes"] != raw["bytes"]):
            raise TaskDiffError("snapshot_contradiction", "structure",
                                "%s path %s size-change flag contradicts bytes"
                                % (label, path), path=path)
        if not _is_digest(raw["sha256"]):
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.files[%d].sha256 must be lowercase SHA-256"
                                % (label, index), path=path)
        text = raw["text"]
        if not isinstance(text, str):
            raise TaskDiffError("invalid_snapshot", "structure",
                                "%s.files[%d].text must be a string"
                                % (label, index), path=path)
        encoded = text.encode("utf-8")
        if hashlib.sha256(encoded).hexdigest() != raw["sha256"]:
            raise TaskDiffError("snapshot_contradiction", "structure",
                                "%s path %s text does not match sha256"
                                % (label, path), path=path)
        if len(encoded) != raw["bytes"]:
            raise TaskDiffError("snapshot_contradiction", "structure",
                                "%s path %s text length does not match bytes"
                                % (label, path), path=path)
        normalized.append({
            "path": path,
            "selection_bytes": raw["selection_bytes"],
            "bytes": raw["bytes"],
            "selection_size_changed": raw["selection_size_changed"],
            "sha256": raw["sha256"],
            "text": text,
        })
    return {
        "tree_sha256": payload["tree_sha256"],
        "selection_sha256": payload["selection_sha256"],
        "query_terms": list(terms),
        "selection_truncated": payload["selection_truncated"],
        "total_matches": payload["total_matches"],
        "selected_files": payload["selected_files"],
        "max_bytes": payload["max_bytes"],
        "total_bytes": payload["total_bytes"],
        "selection_size_changed_files": payload["selection_size_changed_files"],
        "files": sorted(normalized, key=lambda row: row["path"]),
    }


def _line_counts(before: str, after: str) -> Tuple[int, int]:
    added = removed = 0
    matcher = difflib.SequenceMatcher(a=before.splitlines(),
                                      b=after.splitlines(), autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def _entry(before: Dict[str, Any] | None,
           after: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if before is None:
        assert after is not None
        return {
            "path": after["path"],
            "change": "added",
            "before_sha256": None,
            "after_sha256": after["sha256"],
            "before_bytes": 0,
            "after_bytes": after["bytes"],
            "text_compared": False,
            "lines_added": None,
            "lines_removed": None,
        }
    if after is None:
        return {
            "path": before["path"],
            "change": "deleted",
            "before_sha256": before["sha256"],
            "after_sha256": None,
            "before_bytes": before["bytes"],
            "after_bytes": 0,
            "text_compared": False,
            "lines_added": None,
            "lines_removed": None,
        }
    if before["sha256"] == after["sha256"] and before["bytes"] == after["bytes"]:
        return None
    added, removed = _line_counts(before["text"], after["text"])
    return {
        "path": before["path"],
        "change": "modified",
        "before_sha256": before["sha256"],
        "after_sha256": after["sha256"],
        "before_bytes": before["bytes"],
        "after_bytes": after["bytes"],
        "text_compared": True,
        "lines_added": added,
        "lines_removed": removed,
    }


def _build(baseline: Dict[str, Any], final: Dict[str, Any],
           max_files: int, max_bytes: int) -> Dict[str, Any]:
    omissions = []
    if baseline["selection_truncated"] or final["selection_truncated"]:
        omissions.append(_omission("selection_truncated",
                                   "snapshot selection is explicitly truncated"))
    baseline_paths = {row["path"] for row in baseline["files"]}
    final_paths = {row["path"] for row in final["files"]}
    if (baseline["selection_sha256"] != final["selection_sha256"]
            and (baseline["query_terms"] != final["query_terms"]
                 or baseline_paths != final_paths)):
        omissions.append(_omission("selection_mismatch",
                                   "the two snapshots selected different inputs"))
    if baseline["tree_sha256"] != final["tree_sha256"]:
        omissions.append(_omission("tree_mismatch",
                                   "the two snapshots used different frozen trees"))
    if baseline["selection_size_changed_files"] or final["selection_size_changed_files"]:
        omissions.append(_omission("selection_size_changed",
                                   "at least one selected file changed size after its tree scan"))
    if not baseline["files"] or not final["files"]:
        omissions.append(_omission("empty_snapshot",
                                   "an empty snapshot cannot prove a clean comparison"))

    before = {row["path"]: row for row in baseline["files"]}
    after = {row["path"]: row for row in final["files"]}
    entries = []
    for path in sorted(set(before) | set(after)):
        entry = _entry(before.get(path), after.get(path))
        if entry is not None:
            entries.append(entry)

    omitted_files = max(0, len(entries) - max_files)
    selected_entries = entries[:max_files]
    omitted_paths = [entry["path"] for entry in entries[max_files:]]
    if omitted_files:
        omissions.append(_omission("max_files",
                                   "file limit omitted %d changed paths"
                                   % omitted_files))
    sorted_omissions = sorted(omissions,
                               key=lambda item: (item["reason"], item["path"],
                                                 item["detail"]))
    body = {
        "schema_version": REPORT_SCHEMA,
        "status": "complete",
        "whole_workspace_compared": False,
        "max_files": max_files,
        "max_bytes": max_bytes,
        "compared_files": len(entries),
        "changed_files": len(entries),
        "omitted_files": omitted_files,
        "omitted_paths": omitted_paths,
        "entries": selected_entries,
        "omissions": sorted_omissions,
    }
    def canonical_size(payload: dict) -> int:
        return len(_canonical_json(payload))

    def content_bytes() -> int:
        # Stable content accounting excludes the two self-describing fields.
        content = {key: value for key, value in body.items()
                   if key not in ("report_bytes", "report_sha256")}
        return canonical_size(content)

    def final_size() -> int:
        trial = dict(body)
        trial["report_bytes"] = content_bytes()
        trial["report_sha256"] = _digest(
            {key: value for key, value in trial.items()
             if key != "report_sha256"})
        return canonical_size(trial)

    def add_byte_omission() -> None:
        if not any(item["reason"] == "max_bytes"
                   for item in body["omissions"]):
            body["omissions"].append(_omission(
                "max_bytes", "byte budget omitted entries"))
            body["omissions"].sort(key=lambda item: (item["reason"],
                                                       item["path"],
                                                       item["detail"]))

    while final_size() > max_bytes and body["entries"]:
        dropped = body["entries"].pop()
        body["omitted_files"] += 1
        body["omitted_paths"].append(dropped["path"])
        add_byte_omission()
    if final_size() > max_bytes:
        body["omissions"] = [{"reason": "max_bytes", "detail": "", "path": ""}]
        body["compared_files"] = 0
        body["changed_files"] = 0
        while final_size() > max_bytes and body["omissions"]:
            body["omissions"] = []
        if final_size() > max_bytes and body["omitted_paths"]:
            body["omitted_paths"] = []
            add_byte_omission()
        if final_size() > max_bytes:
            raise TaskDiffError(
                "byte_limit_exceeded", "limit",
                "max_bytes is too small for the minimal report envelope")
    if body["omitted_files"]:
        body["status"] = "partial"
    if body["omissions"]:
        body["status"] = "partial"
    report = dict(body)
    report["report_bytes"] = content_bytes()
    report["report_sha256"] = _digest(report)
    if canonical_size(report) > max_bytes:
        raise TaskDiffError("byte_limit_exceeded", "limit",
                            "max_bytes is too small for the final report")
    return report


def build_task_diff(baseline: Any, final: Any, *, max_files: int = DEFAULT_MAX_FILES,
                    max_bytes: int = DEFAULT_MAX_BYTES) -> Dict[str, Any]:
    """Return bounded evidence comparing two supplied context snapshots."""
    max_files = _integer(max_files, "max_files", low=1, high=MAX_FILES)
    max_bytes = _integer(max_bytes, "max_bytes", low=1, high=MAX_REPORT_BYTES)
    before = _validate_snapshot(copy.deepcopy(baseline), "baseline")
    after = _validate_snapshot(copy.deepcopy(final), "final")
    return _build(before, after, max_files, max_bytes)
