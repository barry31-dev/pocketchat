"""Guarded overwrite of one existing file authorized by a TaskSpec.

The editor deliberately does not become a general patch surface. It takes a
snapshot before writing, requires that exact snapshot immediately before the
atomic replacement, and returns only hashes plus bounded line counts. The
underlying UTF-8/allowlist/atomic-write behavior remains in ``tools``.
"""

from __future__ import annotations

import difflib
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from .task_spec import TaskSpec
from .tools import (MAX_CONTENT_BYTES, ToolError, _guard_reason, _normalize,
                    _safe_relative, execute_write_file)


class TaskEditError(ValueError):
    """The overwrite cannot be proven safe or remains inside the TaskSpec."""


@dataclass(frozen=True)
class OverwriteSnapshot:
    path: str
    sha256: str
    bytes: int
    text: str


@dataclass(frozen=True)
class OverwriteResult:
    path: str
    original_sha256: str
    new_sha256: str
    bytes_before: int
    bytes_after: int
    added_lines: int
    removed_lines: int


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _authorized_paths(spec: TaskSpec) -> list:
    return list(spec.overwrite_paths)


def _check_no_symlink(root: Path, normalized: str) -> None:
    current = root
    for part in Path(normalized).parts:
        current = current / part
        if current.is_symlink():
            raise TaskEditError("symbolic links are not allowed for overwrite")


def _clean_baseline(root: Path, normalized: str) -> None:
    """Refuse a target already dirty or untracked in its Git workspace."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain",
             "--untracked-files=all", "--", normalized],
            capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaskEditError("cannot prove a clean baseline: %s" % exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise TaskEditError("workspace is not a clean Git checkout: %s" % detail)
    if proc.stdout.strip():
        raise TaskEditError("target is already dirty; refusing to absorb it")


def snapshot_overwrite(root: Path, path: str, spec: TaskSpec) -> OverwriteSnapshot:
    """Read one authorized existing file and freeze its baseline hash."""
    if not isinstance(spec, TaskSpec):
        raise TaskEditError("overwrite needs a parsed TaskSpec")
    normalized = _normalize(path)
    if normalized not in _authorized_paths(spec):
        raise TaskEditError("path is not declared in overwrite_paths")
    root = Path(root).resolve()
    try:
        candidate = _safe_relative(root, normalized)
    except ToolError as exc:
        raise TaskEditError(str(exc))
    relative = candidate.relative_to(root)
    guard = _guard_reason(relative)
    if guard in ("denylist", "binary_ext"):
        raise TaskEditError("path is refused by the tool safety policy")
    _check_no_symlink(root, normalized)
    if not candidate.is_file():
        raise TaskEditError("overwrite target must be an existing regular file")
    _clean_baseline(root, normalized)
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise TaskEditError("cannot read overwrite target: %s" % exc)
    if len(data) > MAX_CONTENT_BYTES:
        raise TaskEditError("overwrite target exceeds %d bytes"
                            % MAX_CONTENT_BYTES)
    if b"\x00" in data:
        raise TaskEditError("binary content is not allowed for overwrite")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TaskEditError("overwrite target is not UTF-8: %s" % exc)
    return OverwriteSnapshot(normalized, _digest(data), len(data), text)


def _line_counts(before: str, after: str):
    matcher = difflib.SequenceMatcher(a=before.splitlines(),
                                      b=after.splitlines())
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def apply_overwrite(root: Path, path: str, content: str, spec: TaskSpec,
                    snapshot: OverwriteSnapshot, *, recorder=None,
                    tool_call_id: str = "") -> OverwriteResult:
    """Atomically replace one snapshot-bound file through the existing tool."""
    if not isinstance(content, str):
        raise TaskEditError("replacement content must be UTF-8 text")
    if not isinstance(snapshot, OverwriteSnapshot):
        raise TaskEditError("an OverwriteSnapshot is required")
    normalized = _normalize(path)
    if normalized != snapshot.path:
        raise TaskEditError("snapshot belongs to another path")
    if normalized not in _authorized_paths(spec):
        raise TaskEditError("path is not declared in overwrite_paths")
    root = Path(root).resolve()
    try:
        candidate = _safe_relative(root, normalized)
    except ToolError as exc:
        raise TaskEditError(str(exc))
    _check_no_symlink(root, normalized)
    if not candidate.is_file():
        raise TaskEditError("overwrite target no longer exists")
    try:
        before = candidate.read_bytes()
    except OSError as exc:
        raise TaskEditError("cannot reread overwrite target: %s" % exc)
    if _digest(before) != snapshot.sha256:
        raise TaskEditError("target changed since snapshot; refusing overwrite")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise TaskEditError("replacement exceeds %d bytes" % MAX_CONTENT_BYTES)
    added, removed = _line_counts(snapshot.text, content)
    try:
        execute_write_file(
            root, normalized, content,
            allowed_paths=_authorized_paths(spec), overwrite=True,
            recorder=recorder, tool_call_id=tool_call_id)
    except ToolError as exc:
        raise TaskEditError(str(exc))
    after = candidate.read_bytes()
    return OverwriteResult(
        path=normalized,
        original_sha256=snapshot.sha256,
        new_sha256=_digest(after),
        bytes_before=snapshot.bytes,
        bytes_after=len(after),
        added_lines=added,
        removed_lines=removed,
    )
