"""Bounded, metadata-only repository tree discovery for task context.

The scanner is deliberately read-only: it records relative regular-file paths
and byte sizes, never file contents.  It does not follow symlinks and refuses
when a caller-selected bound would make the result partial.  Later task-context
stages may select or snapshot files from this inventory; this module does not
claim either capability.
"""
from __future__ import annotations

import os
import stat
from collections.abc import Iterable as IterableABC
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CONTEXT_TREE_SCHEMA = "pocketchat-task-context-tree.v1"
DEFAULT_MAX_FILES = 512
DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_PATH_CHARS = 65536
MAX_FILES = 4096
MAX_DEPTH = 32
MAX_PATH_CHARS = 1024 * 1024

EXCLUDED_DIRECTORY_NAMES = frozenset({
    ".git", ".hg", ".svn", ".pocketchat", ".venv", "venv",
    "__pycache__", "node_modules", "dist", "build",
})


class TaskContextError(ValueError):
    """A tree scan is malformed, unsafe, or cannot be complete."""


def _bound(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskContextError("%s must be an integer" % name)
    if not low <= value <= high:
        raise TaskContextError("%s must be between %d and %d" % (name, low, high))
    return value


def _include_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TaskContextError("include paths must be non-empty strings")
    if "\x00" in value:
        raise TaskContextError("include paths must not contain NUL")
    if value == ".":
        return value
    if value.startswith(("/", "-")) or "\\" in value:
        raise TaskContextError("include paths must be workspace-relative")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise TaskContextError(
            "include paths must not contain empty, '.' or '..' segments")
    if len(value) > 1024:
        raise TaskContextError("include path exceeds 1024 characters")
    return value


def _reject_symlink_components(root: Path, include: str, charge_path=None) -> None:
    if include == ".":
        return
    current = root
    for part in include.split("/"):
        current = current / part
        relative = current.relative_to(root).as_posix()
        if charge_path is not None:
            charge_path(relative)
        try:
            info = current.lstat()
        except (OSError, ValueError) as exc:
            raise TaskContextError(
                "cannot inspect include path %s: %s" % (include, exc))
        if stat.S_ISLNK(info.st_mode):
            raise TaskContextError(
                "include path %s crosses a symlink at %s"
                % (include, relative))


def _root(path: Path) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str) or "\x00" in raw:
        raise TaskContextError("workspace root must be a NUL-free path")
    if raw == ".":
        parts = []
    else:
        if raw.startswith(("/", "-")) or "\\" in raw:
            raise TaskContextError("workspace root must be relative to the current directory")
        parts = raw.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise TaskContextError(
                "workspace root must not contain empty, '.' or '..' segments")
    current = Path.cwd()
    for part in parts:
        current = current / part
        try:
            info = current.lstat()
        except (OSError, ValueError) as exc:
            raise TaskContextError("cannot inspect workspace root: %s" % exc)
        if stat.S_ISLNK(info.st_mode):
            raise TaskContextError(
                "workspace root crosses a symlink at %s" % current)
    try:
        root = (Path.cwd() if raw == "." else Path.cwd() / raw).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise TaskContextError("cannot resolve workspace root: %s" % exc)
    if not root.is_dir():
        raise TaskContextError("workspace root must be a directory")
    return root


def scan_tree(workspace: Path, *, includes: Optional[Iterable[str]] = None,
              max_files: int = DEFAULT_MAX_FILES,
              max_depth: int = DEFAULT_MAX_DEPTH,
              max_path_chars: int = DEFAULT_MAX_PATH_CHARS) -> Dict[str, Any]:
    """Return one complete, deterministic metadata inventory.

    ``includes`` are strict workspace-relative files or directories.  ``.`` is
    the only special value and means the workspace root.  Generated/runtime
    directories are omitted by exact directory name.  A budget violation raises
    :class:`TaskContextError`; no truncated inventory is returned.
    """
    root = _root(Path(workspace))
    max_files = _bound(max_files, "max_files", 1, MAX_FILES)
    max_depth = _bound(max_depth, "max_depth", 1, MAX_DEPTH)
    max_path_chars = _bound(
        max_path_chars, "max_path_chars", 1, MAX_PATH_CHARS)

    if includes is None:
        raw_includes = ["."]
    else:
        if isinstance(includes, (str, bytes)) or not isinstance(includes, IterableABC):
            raise TaskContextError("includes must be a collection of paths")
        raw_includes = list(includes)
    if not raw_includes:
        raise TaskContextError("at least one include path is required")
    parsed_includes: List[str] = []
    for value in raw_includes:
        include = _include_path(value)
        if include not in parsed_includes:
            parsed_includes.append(include)

    files: Dict[str, Dict[str, Any]] = {}
    omitted: Dict[str, Dict[str, str]] = {}
    path_chars = 0
    charged_paths = set()

    def charge(relative: str) -> None:
        nonlocal path_chars
        if relative in charged_paths:
            return
        needed = path_chars + len(relative)
        if needed > max_path_chars:
            raise TaskContextError(
                "tree path characters exceed max_path_chars=%d" % max_path_chars)
        charged_paths.add(relative)
        path_chars = needed

    def add_path(row: Dict[str, Any], target: Dict[str, Dict[str, Any]]) -> None:
        relative = row["path"]
        if relative in target:
            return
        target[relative] = row

    def omit(relative: str, reason: str) -> None:
        if relative in files:
            return
        add_path({"path": relative, "reason": reason}, omitted)

    def visit(path: Path, relative: str) -> None:
        charge(relative)
        try:
            info = path.lstat()
        except OSError as exc:
            raise TaskContextError("cannot inspect %s: %s" % (relative, exc))
        if stat.S_ISLNK(info.st_mode):
            omit(relative, "symlink")
            return
        depth = 0 if relative == "." else len(relative.split("/"))
        if depth > max_depth:
            raise TaskContextError(
                "tree depth exceeds max_depth=%d at %s" % (max_depth, relative))
        if stat.S_ISREG(info.st_mode):
            if relative not in files:
                if len(files) >= max_files:
                    raise TaskContextError(
                        "tree files exceed max_files=%d" % max_files)
                add_path({"path": relative, "bytes": info.st_size}, files)
            return
        if not stat.S_ISDIR(info.st_mode):
            omit(relative, "unsupported_type")
            return
        if relative != "." and path.name in EXCLUDED_DIRECTORY_NAMES:
            omit(relative, "excluded_directory")
            return
        try:
            with os.scandir(str(path)) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise TaskContextError("cannot list %s: %s" % (relative, exc))
        if depth >= max_depth and entries:
            raise TaskContextError(
                "tree depth exceeds max_depth=%d below %s" % (max_depth, relative))
        for entry in entries:
            child = entry.name if relative == "." else relative + "/" + entry.name
            visit(Path(entry.path), child)

    for include in parsed_includes:
        _reject_symlink_components(root, include, charge)
        candidate = root if include == "." else root / include
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise TaskContextError("include path %s is unavailable or escapes the workspace: %s"
                                   % (include, exc))
        # Resolve only proves the boundary.  Visit the lexical path so a symlink
        # is reported rather than traversed.
        visit(candidate, include)

    file_rows = [files[path] for path in sorted(files)]
    omitted_rows = [omitted[path] for path in sorted(omitted)]
    return {
        "schema_version": CONTEXT_TREE_SCHEMA,
        "workspace": ".",
        "includes": parsed_includes,
        "limits": {
            "max_files": max_files,
            "max_depth": max_depth,
            "max_path_chars": max_path_chars,
        },
        "usage": {
            "files": len(file_rows),
            "omitted": len(omitted_rows),
            "path_chars": path_chars,
        },
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "files": file_rows,
        "omitted": omitted_rows,
    }
