"""Explicit local read/write tools with workspace permissions.

This module is deliberately small: allowlisted UTF-8 reads and create/overwrite
writes only. There is no shell, network or delete operation. Every attempt
emits the existing permission/tool trace events, and a denied call never reads
or writes the target file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Set

SENSITIVE_NAMES = {
    ".env", ".git", ".ssh", "id_rsa", "id_ed25519", "credentials",
}
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz",
    ".tar", ".exe", ".bin", ".dylib", ".so",
}
MAX_CONTENT_BYTES = 16 * 1024


class ToolError(ValueError):
    """The tool cannot safely complete the requested operation."""

    def __init__(self, message: str, error_type: str = "tool_failed") -> None:
        super().__init__(message)
        self.error_type = error_type


class ToolPermissionError(ToolError):
    """The tool call was denied before touching the target."""

    def __init__(self, message: str,
                 error_type: str = "permission_denied") -> None:
        super().__init__(message, error_type)


def _safe_relative(root: Path, value: str) -> Path:
    root = Path(root).resolve()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ToolPermissionError(
            "path escapes the permitted workspace", error_type="permission_denied")
    return candidate


def _normalize(value: str) -> str:
    text = str(value).replace("\\", "/")
    return text[2:] if text.startswith("./") else text


def _guard_reason(relative: Path) -> str:
    if relative.name in SENSITIVE_NAMES or any(
            part in SENSITIVE_NAMES for part in relative.parts):
        return "denylist"
    if relative.suffix.lower() in BINARY_SUFFIXES:
        return "binary_ext"
    return "path_escape"


def _deny(recorder, path: str, error_type: str, message: str,
          tool_call_id: str = "") -> None:
    if recorder is None:
        return
    recorder.append("permission.resolved", {
        "decision": "denied", "decided_by": "workspace_policy",
    })
    recorder.append("tool.call.failed", {
        "tool": "write_file", "tool_call_id": tool_call_id, "status": "failed",
        "error": {"type": error_type, "message": message},
        "duration_ms": 0, "guardrail_refusal": True,
    })


def execute_read_file(root: Path, path: str, *,
                      allowed_paths: Iterable[str],
                      max_bytes: int,
                      recorder=None,
                      tool_call_id: str = "") -> Dict[str, Any]:
    """Read one explicitly allowed UTF-8 workspace file."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ToolError("max_bytes must be a non-negative integer")
    allowed: Set[str] = {_normalize(item)
                         for item in allowed_paths}
    requested = _normalize(path)
    root = Path(root).resolve()
    if recorder is not None:
        recorder.append("permission.requested", {
            "action": "read", "resource": requested, "tool": "read_file",
        })

    try:
        candidate = _safe_relative(root, requested)
        relative = candidate.relative_to(root)
    except ToolPermissionError as exc:
        if recorder is not None:
            recorder.append("permission.resolved", {
                "decision": "denied", "decided_by": "workspace_policy",
            })
            recorder.append("tool.call.failed", {
                "tool": "read_file", "tool_call_id": tool_call_id,
                "status": "failed",
                "error": {"type": "permission_denied", "message": str(exc)},
                "duration_ms": 0, "guard": "path_escape",
                "guardrail_refusal": True,
            })
        raise

    guard = _guard_reason(relative)
    denied = False
    error_type = "permission_denied"
    if requested not in allowed:
        denied = True
        message = "path is not in the explicit read allowlist"
        error_type = "not_allowlisted"
    elif guard in ("denylist", "binary_ext"):
        denied = True
        message = "path is refused by the tool safety policy"
        error_type = ("denylisted_path" if guard == "denylist"
                      else "binary_file")
    elif not candidate.is_file():
        denied = True
        message = "path does not name a readable file"
        error_type = "file_not_found"
    if denied:
        if recorder is not None:
            recorder.append("permission.resolved", {
                "decision": "denied", "decided_by": "workspace_policy",
            })
            recorder.append("tool.call.failed", {
                "tool": "read_file", "tool_call_id": tool_call_id,
                "status": "failed",
                "error": {"type": error_type, "message": message},
                "duration_ms": 0, "guard": guard,
                "guardrail_refusal": True,
            })
        raise ToolPermissionError(message, error_type=error_type)

    if recorder is not None:
        recorder.append("permission.resolved", {
            "decision": "approved", "decided_by": "workspace_policy",
        })
        recorder.append("tool.call.started", {
            "tool": "read_file", "tool_call_id": tool_call_id,
            "args_summary": "read %s" % requested,
        })
    try:
        data = candidate.read_bytes()
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if recorder is not None:
            recorder.append("tool.call.failed", {
                "tool": "read_file", "tool_call_id": tool_call_id,
                "status": "failed",
                "error": {"type": "invalid_encoding", "message": str(exc)},
                "duration_ms": 0, "result_bytes": len(data),
            })
        raise ToolError("file is not UTF-8 text", error_type="invalid_encoding")
    truncated = len(data) > max_bytes
    clipped = data[:max_bytes]
    if truncated:
        while clipped:
            try:
                body = clipped.decode("utf-8")
                break
            except UnicodeDecodeError:
                clipped = clipped[:-1]
        else:
            body = ""
    else:
        body = clipped.decode("utf-8")
    if recorder is not None:
        recorder.append("tool.call.completed", {
            "tool": "read_file", "tool_call_id": tool_call_id,
            "status": "completed", "duration_ms": 0,
            "result_bytes": len(data), "result_truncated": truncated,
            "effect_disposition": "none",
        })
    return {
        "path": requested,
        "content": body,
        "bytes": len(data),
        "truncated": truncated,
    }


def execute_write_file(root: Path, path: str, content: str, *,
                       allowed_paths: Iterable[str],
                       overwrite: bool = False,
                       recorder=None,
                       tool_call_id: str = "") -> Dict[str, Any]:
    """Create or explicitly overwrite one UTF-8 workspace file atomically."""
    if not isinstance(content, str):
        raise ToolError("write content must be UTF-8 text")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ToolError("write content exceeds %d bytes" % MAX_CONTENT_BYTES)
    normalized = _normalize(path)
    root = Path(root).resolve()
    if recorder is not None:
        recorder.append("permission.requested", {
            "action": "write", "resource": normalized, "tool": "write_file",
        })
    try:
        candidate = _safe_relative(root, normalized)
    except ToolPermissionError as exc:
        if recorder is not None:
            recorder.append("permission.resolved", {
                "decision": "denied", "decided_by": "workspace_policy",
            })
            recorder.append("tool.call.failed", {
                "tool": "write_file", "tool_call_id": tool_call_id,
                "status": "failed",
                "error": {"type": exc.error_type, "message": str(exc)},
                "duration_ms": 0, "guard": "path_escape",
                "guardrail_refusal": True,
            })
        raise
    relative = candidate.relative_to(root)
    if normalized not in {
            _normalize(item) for item in allowed_paths
    }:
        _deny(recorder, normalized, "not_allowlisted",
              "path is not in the explicit write allowlist", tool_call_id)
        raise ToolPermissionError(
            "path is not in the explicit write allowlist",
            error_type="not_allowlisted")
    guard = _guard_reason(relative)
    if guard in ("denylist", "binary_ext"):
        error_type = "denylisted_path" if guard == "denylist" else "binary_file"
        _deny(recorder, normalized, error_type,
              "path is refused by the tool safety policy", tool_call_id)
        raise ToolPermissionError(
            "path is refused by the tool safety policy", error_type=error_type)
    if candidate.exists() and not overwrite:
        _deny(recorder, normalized, "exists",
              "file already exists; pass overwrite", tool_call_id)
        raise ToolPermissionError(
            "file already exists; pass overwrite", error_type="exists")
    if recorder is not None:
        recorder.append("permission.resolved", {
            "decision": "approved", "decided_by": "workspace_policy",
        })
        recorder.append("tool.call.started", {
            "tool": "write_file", "tool_call_id": tool_call_id,
            "args_summary": "write %s" % normalized,
        })
    temporary = candidate.with_name(candidate.name + ".tmp")
    try:
        if overwrite:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(candidate))
        else:
            descriptor = os.open(str(candidate), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
    except (OSError, FileExistsError) as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        if recorder is not None:
            recorder.append("tool.call.failed", {
                "tool": "write_file", "tool_call_id": tool_call_id,
                "status": "failed",
                "error": {"type": "write_failed", "message": str(exc)},
                "duration_ms": 0,
            })
        raise ToolError("cannot write %s: %s" % (normalized, exc))
    if recorder is not None:
        recorder.append("tool.call.completed", {
            "tool": "write_file", "tool_call_id": tool_call_id,
            "status": "completed", "duration_ms": 0,
            "result_bytes": len(content.encode("utf-8")),
            "result_truncated": False,
            "effect_disposition": "applied",
        })
    return {"path": normalized, "bytes": len(content.encode("utf-8")),
            "content": content, "overwritten": bool(candidate.exists() and overwrite)}
