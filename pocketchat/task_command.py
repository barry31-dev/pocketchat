"""Bounded argv command templates and their evidence.

This module executes only an operator-declared template. It never accepts a
shell string, never enables ``shell=True`` and never inherits the caller's
environment wholesale. The trace is a separate JSONL record; the return value
contains only bounded previews and hashes, so a large or sensitive stream does
not become the report by accident.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import TraceRecorder

_TEMPLATE_KEYS = ("id", "argv", "cwd", "timeout_seconds", "max_output_bytes")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "cmd",
                     "cmd.exe", "powershell", "powershell.exe", "pwsh",
                     "pwsh.exe"})
_MAX_ARGV_ITEMS = 128
_MAX_ARG_CHARS = 2048
_MAX_TIMEOUT_SECONDS = 300
_MAX_OUTPUT_BYTES = 1024 * 1024
_TRUNCATION_MARKER = b" [truncated]"
_ARCHIVE_SCHEMA = "pocketchat-command-evidence.v1"


class CommandError(ValueError):
    """A command template is malformed or outside its workspace boundary."""


@dataclass(frozen=True)
class CommandResult:
    template_id: str
    cwd: str
    exit_code: Optional[int]
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    trace_path: str
    task_acceptance: str = "not_evaluated"

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "trace": self.trace_path,
            "task_acceptance": self.task_acceptance,
        }


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise CommandError("%s must be an object" % what)
    return value


def _closed(value: Dict[str, Any], what: str) -> None:
    unknown = sorted(key for key in value if key not in _TEMPLATE_KEYS)
    if unknown:
        raise CommandError("%s has unknown field(s): %s"
                           % (what, ", ".join(unknown)))


def _text(value: Any, what: str, maximum: int, *, preserve=False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommandError("%s must be a non-empty string" % what)
    text = value if preserve else value.strip()
    if len(text) > maximum:
        raise CommandError("%s exceeds %d characters" % (what, maximum))
    if "\x00" in text:
        raise CommandError("%s must not contain NUL" % what)
    return text


def _cwd(value: Any, what: str) -> str:
    text = _text(value, what, 512)
    if text == ".":
        return text
    if text.startswith(("/", "-")) or "\\" in text:
        raise CommandError("%s must be a workspace-relative directory" % what)
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise CommandError("%s must not contain empty, '.' or '..' segments"
                           % what)
    return text


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("%s must be an integer" % what)
    if not low <= value <= high:
        raise CommandError("%s must be between %d and %d"
                           % (what, low, high))
    return value


def _argv(value: Any) -> List[str]:
    if not isinstance(value, list) or not value:
        raise CommandError("argv must be a non-empty list")
    if len(value) > _MAX_ARGV_ITEMS:
        raise CommandError("argv has too many items")
    result = []
    for index, raw in enumerate(value):
        text = _text(raw, "argv[%d]" % index, _MAX_ARG_CHARS, preserve=True)
        result.append(text)
    executable = os.path.basename(result[0].replace("\\", "/")).lower()
    if executable in _SHELLS:
        raise CommandError("shell executables are not allowed in argv")
    return result


def parse_command_templates(value: Any) -> List[Dict[str, Any]]:
    """Parse the closed command-template list from a TaskSpec."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise CommandError("commands must be a list")
    result: List[Dict[str, Any]] = []
    seen = set()
    for raw in value:
        item = _object(raw, "command template")
        _closed(item, "command template")
        for key in _TEMPLATE_KEYS:
            if key not in item:
                raise CommandError("command template is missing %s" % key)
        template_id = _text(item["id"], "command id", 64)
        if not _ID.fullmatch(template_id):
            raise CommandError("command id has an unsupported shape: %r"
                               % template_id)
        if template_id in seen:
            raise CommandError("duplicate command id %r" % template_id)
        seen.add(template_id)
        result.append({
            "id": template_id,
            "argv": _argv(item["argv"]),
            "cwd": _cwd(item["cwd"], "command cwd"),
            "timeout_seconds": _integer(
                item["timeout_seconds"], "command timeout_seconds", 1,
                _MAX_TIMEOUT_SECONDS),
            "max_output_bytes": _integer(
                item["max_output_bytes"], "command max_output_bytes",
                len(_TRUNCATION_MARKER), _MAX_OUTPUT_BYTES),
        })
    return result


def _env() -> Dict[str, str]:
    """Keep only deterministic process plumbing, never arbitrary credentials."""
    result = {}
    for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT"):
        value = os.environ.get(key)
        if isinstance(value, str) and value:
            result[key] = value
    result.setdefault("PATH", os.defpath)
    return result


def _read_stream(stream, limit: int):
    stream.seek(0)
    digest = hashlib.sha256()
    total = 0
    preview = bytearray()
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
        remaining = limit - len(preview)
        if remaining > 0:
            preview.extend(chunk[:remaining])
    truncated = total > len(preview)
    if truncated:
        keep = max(0, limit - len(_TRUNCATION_MARKER))
        preview = preview[:keep] + _TRUNCATION_MARKER
    return (
        bytes(preview).decode("utf-8", "replace"),
        total,
        digest.hexdigest(),
        truncated,
    )


def _write_archive(path: Path, result: Dict[str, Any], credential=None) -> None:
    """Persist the bounded result beside the trace without exposing a secret."""
    data = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")
    if credential and credential.encode("utf-8") in data:
        raise CommandError("command evidence contains the configured credential")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _trace_error(recorder, *, template_id: str, error_type: str,
                 message: str, duration_ms: int, result_bytes: int,
                 truncated: bool, exit_code: Optional[int],
                 stdout_sha256: str = "", stderr_sha256: str = "",
                 stdout_truncated: bool = False,
                 stderr_truncated: bool = False) -> None:
    recorder.append("tool.call.failed", {
        "tool": "run_command",
        "status": "failed",
        "template_id": template_id,
        "exit_code": exit_code,
        "error": {"type": error_type, "message": message},
        "duration_ms": duration_ms,
        "result_bytes": result_bytes,
        "result_truncated": truncated,
        "stdout_sha256": stdout_sha256,
        "stderr_sha256": stderr_sha256,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "guardrail_refusal": False,
    })


def _evidence_archive(root: Path, trace_path) -> Path:
    root = Path(root).resolve()
    trace = Path(trace_path)
    if not trace.is_absolute():
        trace = root / trace
    try:
        trace.resolve().relative_to(root)
    except ValueError:
        raise CommandError("command evidence trace escapes the workspace")
    return trace.parent / "command-evidence" / (trace.stem + ".json")


def command_evidence_sha256(root, trace_path) -> str:
    """Digest the exact bounded archive bytes bound to one trace."""
    archive_path = _evidence_archive(Path(root), trace_path)
    try:
        data = archive_path.read_bytes()
    except OSError as exc:
        raise CommandError("cannot read command evidence: %s" % exc)
    return hashlib.sha256(data).hexdigest()


def load_command_evidence(root, trace_path) -> Dict[str, Any]:
    """Read a bounded command archive for a trace inside this workspace.

    The archive is evidence, not an instruction. Its closed shape and its
    trace-derived location are checked here; ``task report`` compares the
    stored run id with the task record instead of trusting a filename alone.
    """
    root = Path(root).resolve()
    trace = Path(trace_path)
    if not trace.is_absolute():
        trace = root / trace
    try:
        relative = trace.resolve().relative_to(root)
    except ValueError:
        raise CommandError("command evidence trace escapes the workspace")
    archive_path = _evidence_archive(root, trace)
    try:
        payload = json.loads(archive_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CommandError("cannot read command evidence: %s" % exc)
    except ValueError as exc:
        raise CommandError("command evidence is not valid JSON: %s" % exc)
    if not isinstance(payload, dict):
        raise CommandError("command evidence is not an object")
    template_root = {key for key in _TEMPLATE_KEYS if key != "id"}
    allowed = template_root | {
        "schema_version", "run_id", "template_id", "exit_code", "stdout", "stderr", "stdout_bytes",
        "stderr_bytes", "stdout_sha256", "stderr_sha256",
        "stdout_truncated", "stderr_truncated", "trace",
        "task_acceptance", "timed_out", "duration_ms",
    }
    unknown = sorted(key for key in payload if key not in allowed)
    if unknown:
        raise CommandError("command evidence has unknown field(s): %s"
                           % ", ".join(unknown))
    if payload.get("schema_version") != _ARCHIVE_SCHEMA:
        raise CommandError("command evidence has the wrong schema")
    if (not isinstance(payload.get("run_id"), str)
            or not payload["run_id"].strip()):
        raise CommandError("command evidence is missing its run_id")
    required = ("template_id", "cwd", "exit_code", "timed_out",
                "stdout_bytes", "stderr_bytes", "stdout_sha256",
                "stderr_sha256", "stdout_truncated", "stderr_truncated")
    for key in required:
        if key not in payload:
            raise CommandError("command evidence is missing %s" % key)
    if (not isinstance(payload["template_id"], str)
            or not payload["template_id"].strip()):
        raise CommandError("command evidence has an invalid template_id")
    if not isinstance(payload["cwd"], str):
        raise CommandError("command evidence has an invalid cwd")
    if payload["exit_code"] is not None and (
            isinstance(payload["exit_code"], bool)
            or not isinstance(payload["exit_code"], int)):
        raise CommandError("command evidence has an invalid exit_code")
    for key in ("timed_out", "stdout_truncated", "stderr_truncated"):
        if not isinstance(payload[key], bool):
            raise CommandError(
                "command evidence field %s must be a boolean" % key)
    for key in ("stdout_bytes", "stderr_bytes"):
        if (isinstance(payload[key], bool) or not isinstance(payload[key], int)
                or payload[key] < 0):
            raise CommandError("command evidence field %s must be a "
                               "non-negative integer" % key)
    for key in ("stdout_sha256", "stderr_sha256"):
        if (not isinstance(payload[key], str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload[key])):
            raise CommandError(
                "command evidence field %s is not a SHA-256" % key)
    if payload.get("task_acceptance") != "not_evaluated":
        raise CommandError("command evidence cannot claim task acceptance")
    stored_trace = payload.get("trace")
    if not isinstance(stored_trace, str):
        raise CommandError("command evidence is missing its trace path")
    stored = Path(stored_trace)
    if stored.is_absolute():
        try:
            stored = stored.resolve().relative_to(root)
        except ValueError:
            raise CommandError("command evidence trace escapes the workspace")
    if stored.as_posix() != relative.as_posix():
        raise CommandError("command evidence trace does not match its archive")
    return payload


def run_command_template(root, template, *, trace_path, run_id,
                         cwd: Optional[str] = None,
                         credential: Optional[str] = None) -> CommandResult:
    """Run one already-parsed template with argv and ``shell=False``.

    A nonzero exit or timeout is a command result, not an exception: callers
    need the evidence. Malformed templates and cwd escapes are refused before
    starting a process and therefore raise :class:`CommandError`.
    """
    if template is None:
        raise CommandError("an explicit command template is required")
    normalized = parse_command_templates([template])[0]
    root = Path(root).resolve()
    requested_cwd = normalized["cwd"] if cwd is None else _cwd(cwd, "command cwd")
    target = (root / requested_cwd).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise CommandError("command cwd escapes the workspace")
    if not target.is_dir():
        raise CommandError("command cwd does not name a directory")

    trace = Path(trace_path)
    evidence_path = trace.parent / "command-evidence" / (trace.stem + ".json")
    recorder = TraceRecorder(trace, run_id)
    recorder.append("run.started", {
        "input_summary": "command template %s" % normalized["id"],
        "config_summary": {
            "cwd": requested_cwd,
            "timeout_seconds": normalized["timeout_seconds"],
            "max_output_bytes": normalized["max_output_bytes"],
        },
    })
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, \
            tempfile.TemporaryFile(mode="w+b") as stderr_file:
        recorder.append("tool.call.started", {
            "tool": "run_command",
            "args_summary": "template %s" % normalized["id"],
        })
        try:
            process = subprocess.Popen(
                normalized["argv"],
                cwd=str(target),
                stdout=stdout_file,
                stderr=stderr_file,
                env=_env(),
                shell=False,
            )
        except OSError as exc:
            duration = int((time.monotonic() - started) * 1000)
            _trace_error(recorder, template_id=normalized["id"],
                        error_type="command_start_failed", message=str(exc),
                        duration_ms=duration, result_bytes=0,
                        truncated=False, exit_code=None)
            recorder.append("run.failed", {
                "status": "failed",
                "error": {"type": "command_start_failed", "message": str(exc)},
            })
            failed = CommandResult(
                normalized["id"], requested_cwd, None, False, duration,
                "", "", 0, 0, "", "", False, False,
                str(trace), "not_evaluated")
            _write_archive(evidence_path, failed.to_dict(), credential)
            return failed
        timed_out = False
        try:
            process.wait(timeout=normalized["timeout_seconds"])
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        duration = int((time.monotonic() - started) * 1000)
        limit = normalized["max_output_bytes"]
        stdout, stdout_bytes, stdout_hash, stdout_truncated = _read_stream(
            stdout_file, limit)
        stderr, stderr_bytes, stderr_hash, stderr_truncated = _read_stream(
            stderr_file, limit)
        total_bytes = stdout_bytes + stderr_bytes
        truncated = stdout_truncated or stderr_truncated
        if timed_out:
            _trace_error(
                recorder, template_id=normalized["id"], error_type="timeout",
                message="command exceeded %d seconds"
                % normalized["timeout_seconds"], duration_ms=duration,
                result_bytes=total_bytes, truncated=truncated,
                exit_code=process.returncode, stdout_sha256=stdout_hash,
                stderr_sha256=stderr_hash,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated)
            recorder.append("run.failed", {
                "status": "failed",
                "error": {"type": "timeout", "message": "command timed out"},
            })
        elif process.returncode != 0:
            _trace_error(
                recorder, template_id=normalized["id"],
                error_type="command_failed",
                message="command exited with status %d" % process.returncode,
                duration_ms=duration, result_bytes=total_bytes,
                truncated=truncated, exit_code=process.returncode,
                stdout_sha256=stdout_hash, stderr_sha256=stderr_hash,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated)
            recorder.append("run.failed", {
                "status": "failed",
                "error": {"type": "command_failed",
                          "message": "command exited with status %d"
                          % process.returncode},
            })
        else:
            recorder.append("tool.call.completed", {
                "tool": "run_command",
                "status": "completed",
                "template_id": normalized["id"],
                "exit_code": process.returncode,
                "duration_ms": duration,
                "result_bytes": total_bytes,
                "result_truncated": truncated,
                "stdout_sha256": stdout_hash,
                "stderr_sha256": stderr_hash,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "effect_disposition": "unknown",
            })
            recorder.append("run.finished", {
                "status": "completed",
                "output_summary": "command template %s completed"
                % normalized["id"],
            })
    result = CommandResult(
        template_id=normalized["id"],
        cwd=requested_cwd,
        exit_code=process.returncode,
        timed_out=timed_out,
        duration_ms=duration,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdout_sha256=stdout_hash,
        stderr_sha256=stderr_hash,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        trace_path=str(trace),
        task_acceptance="not_evaluated",
    )
    archive = dict(result.to_dict())
    archive["schema_version"] = _ARCHIVE_SCHEMA
    archive["run_id"] = run_id
    _write_archive(evidence_path, archive, credential)
    return result
