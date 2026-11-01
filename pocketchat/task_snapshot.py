"""Bounded UTF-8 content snapshots for an explicit context selection.

The snapshot stage consumes a frozen tree manifest and a previously rendered
selection. It rederives the selection before opening any selected path, reads
only those paths without following symlinks, and returns hashes plus bounded
text. It never scans for new files, writes runtime state or calls a provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List

from .task_selection import (CONTEXT_SELECTION_SCHEMA,
                             MAX_TREE_MANIFEST_BYTES, TaskSelectionError,
                             select_files)
from .tools import BINARY_SUFFIXES, SENSITIVE_NAMES

CONTEXT_SNAPSHOT_SCHEMA = "pocketchat-task-context-snapshot.v1"
DEFAULT_MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024


class TaskSnapshotError(ValueError):
    """A selection cannot be trusted or a selected file cannot be snapshotted."""


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TaskSnapshotError("duplicate JSON member: %s" % key)
        value[key] = item
    return value


def _canonical_sha256(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskSnapshotError("%s must be an object" % what)
    return value


def _load_json_file(path: Path, what: str) -> Any:
    target = Path(path)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if target.is_symlink():
            raise TaskSnapshotError("%s must not be a symlink" % what)
        descriptor = os.open(str(target), flags)
    except TaskSnapshotError:
        raise
    except OSError as exc:
        raise TaskSnapshotError("cannot open %s: %s" % (what, exc))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskSnapshotError("%s must be a regular file" % what)
        if info.st_size > MAX_TREE_MANIFEST_BYTES:
            raise TaskSnapshotError("%s exceeds %d bytes"
                                    % (what, MAX_TREE_MANIFEST_BYTES))
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
            raise TaskSnapshotError("%s exceeds %d bytes"
                                    % (what, MAX_TREE_MANIFEST_BYTES))
    except OSError as exc:
        raise TaskSnapshotError("cannot read %s: %s" % (what, exc))
    finally:
        os.close(descriptor)
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_json_object)
    except TaskSnapshotError:
        raise
    except (UnicodeError, ValueError) as exc:
        raise TaskSnapshotError("%s is not valid UTF-8 JSON: %s" % (what, exc))


def parse_selection(payload: Any, tree_payload: Any) -> Dict[str, Any]:
    """Validate a selection by rederiving it from the frozen tree."""
    selected = _object(payload, "context selection")
    if selected.get("schema_version") != CONTEXT_SELECTION_SCHEMA:
        raise TaskSnapshotError("context selection has the wrong schema")
    try:
        expected = select_files(
            tree_payload, selected.get("query_terms"),
            max_files=selected.get("max_files"))
    except TaskSelectionError as exc:
        raise TaskSnapshotError("context selection is invalid: %s" % exc)
    if selected != expected:
        raise TaskSnapshotError(
            "context selection does not match its frozen tree and query")
    return expected


def load_selection(path: Path, tree_payload: Any) -> Dict[str, Any]:
    payload = _load_json_file(path, "context selection")
    if isinstance(payload, dict) and "ok" in payload:
        if payload["ok"] is not True:
            raise TaskSnapshotError("context selection CLI envelope must have ok=true")
        payload = dict(payload)
        payload.pop("ok")
    return parse_selection(payload, tree_payload)


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskSnapshotError("%s must be an integer" % what)
    if not low <= value <= high:
        raise TaskSnapshotError("%s must be between %d and %d"
                                % (what, low, high))
    return value


def _guard_path(root: Path, relative: str) -> Path:
    current = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise TaskSnapshotError("cannot inspect selected path %s: %s"
                                    % (relative, exc))
        if stat.S_ISLNK(info.st_mode):
            raise TaskSnapshotError(
                "selected path crosses a symlink: %s" % relative)
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise TaskSnapshotError(
                "selected path crosses a non-directory: %s" % relative)
    relative_parts = tuple(Path(relative).parts)
    if any(part in SENSITIVE_NAMES for part in relative_parts):
        raise TaskSnapshotError("selected path is refused by the safety policy")
    if current.suffix.lower() in BINARY_SUFFIXES:
        raise TaskSnapshotError("selected path is refused by the safety policy")
    return current


def _read_selected(path: Path, relative: str, remaining: int) -> bytes:
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0))
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise TaskSnapshotError("cannot open selected file %s: %s"
                                % (relative, exc))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TaskSnapshotError(
                "selected path must be a regular file: %s" % relative)
        if info.st_size > MAX_SNAPSHOT_FILE_BYTES:
            raise TaskSnapshotError(
                "selected file exceeds %d bytes: %s"
                % (MAX_SNAPSHOT_FILE_BYTES, relative))
        if info.st_size > remaining:
            raise TaskSnapshotError(
                "snapshot byte budget exceeded at %s" % relative)
        chunks = []
        read_limit = min(remaining, MAX_SNAPSHOT_FILE_BYTES) + 1
        while read_limit:
            chunk = os.read(descriptor, min(65536, read_limit))
            if not chunk:
                break
            chunks.append(chunk)
            read_limit -= len(chunk)
        data = b"".join(chunks)
        if len(data) > min(remaining, MAX_SNAPSHOT_FILE_BYTES):
            raise TaskSnapshotError(
                "selected file changed while being read: %s" % relative)
    except OSError as exc:
        raise TaskSnapshotError("cannot read selected file %s: %s"
                                % (relative, exc))
    finally:
        os.close(descriptor)
    return data


def _snapshot_digest(result: Dict[str, Any]) -> str:
    body = {key: value for key, value in result.items()
            if key != "snapshot_sha256"}
    return _canonical_sha256(body)


def snapshot_files(root: Path, tree_payload: Any,
                   selection_payload: Any, *,
                   max_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES) -> Dict[str, Any]:
    """Read only the selected regular UTF-8 files and return one bounded snapshot."""
    try:
        tree = _object(tree_payload, "context tree")
        selection = parse_selection(selection_payload, tree_payload)
    except TaskSnapshotError:
        raise
    except TaskSelectionError as exc:
        raise TaskSnapshotError("context tree or selection is invalid: %s" % exc)
    max_bytes = _integer(
        max_bytes, "max_bytes", 1, MAX_SNAPSHOT_BYTES)
    workspace = Path(root).resolve()
    if not workspace.is_dir():
        raise TaskSnapshotError("snapshot workspace must be a directory")
    files: List[Dict[str, Any]] = []
    total_bytes = 0
    changed_files = 0
    for match in selection["matches"]:
        relative = match["path"]
        target = _guard_path(workspace, relative)
        remaining = max_bytes - total_bytes
        data = _read_selected(target, relative, remaining)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TaskSnapshotError(
                "selected file is not UTF-8: %s (%s)" % (relative, exc))
        if "\x00" in text:
            raise TaskSnapshotError(
                "selected file contains NUL bytes: %s" % relative)
        total_bytes += len(data)
        size_changed = len(data) != match["bytes"]
        changed_files += size_changed
        files.append({
            "path": relative,
            "selection_bytes": match["bytes"],
            "bytes": len(data),
            "selection_size_changed": size_changed,
            "sha256": hashlib.sha256(data).hexdigest(),
            "text": text,
        })
    result = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA,
        "tree_schema": tree["schema_version"],
        "tree_sha256": selection["tree_sha256"],
        "selection_sha256": _canonical_sha256(selection),
        "query_terms": selection["query_terms"],
        "selection_truncated": selection["truncated"],
        "total_matches": selection["total_matches"],
        "selected_files": len(files),
        "max_bytes": max_bytes,
        "total_bytes": total_bytes,
        "selection_size_changed_files": changed_files,
        "files": files,
    }
    result["snapshot_sha256"] = _snapshot_digest(result)
    return result
