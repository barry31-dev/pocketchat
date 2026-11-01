"""Deterministic path-only selection from a frozen task context tree."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Dict, List

from .task_context import (CONTEXT_TREE_SCHEMA, EXCLUDED_DIRECTORY_NAMES,
                           MAX_DEPTH, MAX_FILES, MAX_PATH_CHARS)

CONTEXT_SELECTION_SCHEMA = "pocketchat-task-context-selection.v1"
DEFAULT_MAX_SELECTED_FILES = 8
MAX_SELECTED_FILES = 64
MAX_QUERY_TERMS = 32
MAX_TREE_MANIFEST_BYTES = 8 * 1024 * 1024
_ALLOWED_TREE_KEYS = {
    "schema_version", "workspace", "includes", "limits", "usage",
    "excluded_directory_names", "files", "omitted",
}
_ALLOWED_OMISSION_REASONS = {
    "symlink", "excluded_directory", "unsupported_type",
}
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class TaskSelectionError(ValueError):
    """A tree manifest or deterministic selection request is invalid."""


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskSelectionError("%s must be an object" % what)
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
        raise TaskSelectionError("%s has %s" % (what, "; ".join(detail)))


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskSelectionError("%s must be an integer" % what)
    if not low <= value <= high:
        raise TaskSelectionError("%s must be between %d and %d" % (what, low, high))
    return value


def _relative_path(value: Any, what: str, *, allow_root=False) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise TaskSelectionError("%s must be a non-empty NUL-free string" % what)
    if allow_root and value == ".":
        return value
    if value.startswith("/") or "\\" in value:
        raise TaskSelectionError("%s must be workspace-relative" % what)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TaskSelectionError(
            "%s must not contain empty, '.' or '..' segments" % what)
    if len(value) > 1024:
        raise TaskSelectionError("%s exceeds 1024 characters" % what)
    return value


def _string_list(value: Any, what: str, *, exact=None, allow_root=False) -> List[str]:
    if not isinstance(value, list):
        raise TaskSelectionError("%s must be a list" % what)
    rows = [_relative_path(item, "%s entry" % what, allow_root=allow_root)
            for item in value]
    if len(rows) != len(set(rows)):
        raise TaskSelectionError("%s must not contain duplicates" % what)
    if exact is not None and rows != list(exact):
        raise TaskSelectionError("%s does not match the tree scanner policy" % what)
    return rows


def _rows(value: Any, kind: str) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise TaskSelectionError("tree %s must be a list" % kind)
    rows = []
    paths = []
    for index, raw in enumerate(value):
        row = _object(raw, "tree %s[%d]" % (kind, index))
        allowed = {"path", "bytes"} if kind == "files" else {"path", "reason"}
        _closed(row, allowed, "tree %s[%d]" % (kind, index))
        path = _relative_path(row["path"], "tree %s path" % kind)
        if kind == "files":
            size = _integer(row["bytes"], "tree file bytes", 0, 2 ** 63 - 1)
            parsed = {"path": path, "bytes": size}
        else:
            reason = row["reason"]
            if (not isinstance(reason, str)
                    or reason not in _ALLOWED_OMISSION_REASONS):
                raise TaskSelectionError("tree omission has an unknown reason")
            parsed = {"path": path, "reason": reason}
        rows.append(parsed)
        paths.append(path)
    if paths != sorted(paths):
        raise TaskSelectionError("tree %s must be sorted by path" % kind)
    if len(paths) != len(set(paths)):
        raise TaskSelectionError("tree %s contains duplicate paths" % kind)
    return rows


def parse_tree(payload: Any) -> Dict[str, Any]:
    """Validate one complete scanner manifest without touching listed files."""
    tree = _object(payload, "context tree")
    _closed(tree, _ALLOWED_TREE_KEYS, "context tree")
    if tree["schema_version"] != CONTEXT_TREE_SCHEMA:
        raise TaskSelectionError("context tree has the wrong schema")
    if tree["workspace"] != ".":
        raise TaskSelectionError("context tree workspace must be '.'")
    includes = _string_list(tree["includes"], "tree includes", allow_root=True)
    if not includes:
        raise TaskSelectionError("tree includes must not be empty")
    excluded = _string_list(
        tree["excluded_directory_names"], "excluded_directory_names",
        exact=sorted(EXCLUDED_DIRECTORY_NAMES))

    limits = _object(tree["limits"], "tree limits")
    _closed(limits, {"max_files", "max_depth", "max_path_chars"}, "tree limits")
    parsed_limits = {
        "max_files": _integer(limits["max_files"], "max_files", 1, MAX_FILES),
        "max_depth": _integer(limits["max_depth"], "max_depth", 1, MAX_DEPTH),
        "max_path_chars": _integer(
            limits["max_path_chars"], "max_path_chars", 1, MAX_PATH_CHARS),
    }
    usage = _object(tree["usage"], "tree usage")
    _closed(usage, {"files", "omitted", "path_chars"}, "tree usage")
    files = _rows(tree["files"], "files")
    omitted = _rows(tree["omitted"], "omitted")
    parsed_usage = {
        "files": _integer(usage["files"], "usage.files", 0, MAX_FILES),
        "omitted": _integer(usage["omitted"], "usage.omitted", 0, MAX_FILES * 16),
        "path_chars": _integer(
            usage["path_chars"], "usage.path_chars", 0, MAX_PATH_CHARS),
    }
    if parsed_usage["files"] != len(files) or parsed_usage["omitted"] != len(omitted):
        raise TaskSelectionError("tree usage counts do not match its rows")
    if len(files) > parsed_limits["max_files"]:
        raise TaskSelectionError("tree files exceed the declared max_files")
    all_paths = [row["path"] for row in files + omitted]
    def covered(path):
        return any(include == "." or path == include
                   or path.startswith(include + "/") for include in includes)
    if any(not covered(path) for path in all_paths):
        raise TaskSelectionError("tree row falls outside its declared includes")
    if set(row["path"] for row in files) & set(row["path"] for row in omitted):
        raise TaskSelectionError("tree file and omission paths overlap")
    if any(len(path.split("/")) > parsed_limits["max_depth"] for path in all_paths):
        raise TaskSelectionError("tree path exceeds the declared max_depth")
    if parsed_usage["path_chars"] < sum(len(path) for path in all_paths):
        raise TaskSelectionError("tree path_chars is smaller than its emitted paths")
    if parsed_usage["path_chars"] > parsed_limits["max_path_chars"]:
        raise TaskSelectionError("tree path_chars exceeds the declared limit")
    return {
        "schema_version": CONTEXT_TREE_SCHEMA,
        "workspace": ".",
        "includes": includes,
        "limits": parsed_limits,
        "usage": parsed_usage,
        "excluded_directory_names": excluded,
        "files": files,
        "omitted": omitted,
    }


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TaskSelectionError("duplicate JSON member: %s" % key)
        value[key] = item
    return value


def load_tree(path: Path) -> Dict[str, Any]:
    target = Path(path)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if target.is_symlink():
            raise TaskSelectionError("context tree must not be a symlink")
        descriptor = os.open(str(target), flags)
    except TaskSelectionError:
        raise
    except OSError as exc:
        raise TaskSelectionError("cannot open context tree: %s" % exc)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskSelectionError("context tree must be a regular file")
        if info.st_size > MAX_TREE_MANIFEST_BYTES:
            raise TaskSelectionError("context tree exceeds %d bytes"
                                     % MAX_TREE_MANIFEST_BYTES)
        chunks = []
        remaining = MAX_TREE_MANIFEST_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_TREE_MANIFEST_BYTES:
            raise TaskSelectionError("context tree exceeds %d bytes"
                                     % MAX_TREE_MANIFEST_BYTES)
    except OSError as exc:
        raise TaskSelectionError("cannot read context tree: %s" % exc)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except TaskSelectionError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise TaskSelectionError("context tree is not valid UTF-8 JSON: %s" % exc)
    if isinstance(payload, dict) and "ok" in payload:
        if payload["ok"] is not True:
            raise TaskSelectionError("context tree CLI envelope must have ok=true")
        payload = dict(payload)
        payload.pop("ok")
    return parse_tree(payload)


def _canonical_sha256(tree: Dict[str, Any]) -> str:
    data = json.dumps(tree, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def tree_sha256(payload: Any) -> str:
    return _canonical_sha256(parse_tree(payload))


def _tokens(text: str) -> List[str]:
    return [token.casefold() for token in _TOKEN.findall(text)]


def _query_terms(value: Any) -> List[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, IterableABC):
        raise TaskSelectionError("query terms must be a collection")
    terms = []
    raw_count = 0
    for raw in value:
        raw_count += 1
        if raw_count > MAX_QUERY_TERMS:
            raise TaskSelectionError("query terms exceed %d entries"
                                     % MAX_QUERY_TERMS)
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise TaskSelectionError("query terms must be non-empty NUL-free strings")
        if len(raw) > 128:
            raise TaskSelectionError("query terms must be at most 128 characters")
        tokens = _tokens(raw)
        if not tokens:
            raise TaskSelectionError("each query term must contain a word token")
        terms.extend(tokens)
        if len(set(terms)) > MAX_QUERY_TERMS:
            raise TaskSelectionError("query terms exceed %d tokens"
                                     % MAX_QUERY_TERMS)
    normalized = sorted(set(terms))
    if not normalized:
        raise TaskSelectionError("at least one query term is required")
    return normalized


def select_files(tree_payload: Any, query_terms: Any, *,
                 max_files: int = DEFAULT_MAX_SELECTED_FILES) -> Dict[str, Any]:
    """Rank frozen file paths by exact query-token matches only."""
    tree = parse_tree(tree_payload)
    terms = _query_terms(query_terms)
    max_files = _integer(
        max_files, "max_files", 1, MAX_SELECTED_FILES)
    matches = []
    matched_any = set()
    for file_row in tree["files"]:
        path = file_row["path"]
        path_tokens = set(_tokens(path))
        basename_tokens = set(_tokens(path.rsplit("/", 1)[-1]))
        score = 0
        matched = []
        for term in terms:
            if term in basename_tokens:
                score += 4
                matched.append(term)
            elif term in path_tokens:
                score += 2
                matched.append(term)
        if score:
            matched_any.update(matched)
            matches.append({
                "path": path,
                "bytes": file_row["bytes"],
                "score": score,
                "matched_terms": matched,
            })
    matches.sort(key=lambda item: (-item["score"], item["path"]))
    total = len(matches)
    return {
        "schema_version": CONTEXT_SELECTION_SCHEMA,
        "tree_schema": CONTEXT_TREE_SCHEMA,
        "tree_sha256": _canonical_sha256(tree),
        "query_terms": terms,
        "max_files": max_files,
        "candidates_considered": len(tree["files"]),
        "total_matches": total,
        "truncated": total > max_files,
        "unmatched_terms": [term for term in terms if term not in matched_any],
        "matches": matches[:max_files],
    }
