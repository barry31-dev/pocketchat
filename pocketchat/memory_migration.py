"""V2 memory parsing and a pure, explicit v1-to-v2 migration.

The v1 store remains the authority until an operator runs a migration. This
module never rewrites it: it reads bounded v1 JSONL, converts each record into
a closed v2 shape, and writes a new file exclusively. Legacy records retain
their content but cannot claim facts about their origin that v1 never stored.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from .memory import (MAX_LINE_BYTES, MAX_STORE_BYTES,
                     SCHEMA_VERSION as V1_SCHEMA_VERSION,
                     _lock_nonblocking, _unlock, memory_path)

V2_SCHEMA_VERSION = "pocketchat-memory.v2"
DEFAULT_V2_PATH = Path(".pocketchat") / "memory.v2.jsonl"
CATEGORIES = frozenset({
    "user_preference", "project_decision", "workflow_hint", "source_note",
})
SOURCE_KINDS = frozenset({
    "user_explicit", "legacy_explicit", "official_docs", "file", "event",
    "url", "tool_output", "model_summary",
})
VERIFICATION_STATUSES = frozenset({
    "not_required", "pending", "verified", "unverified", "not_run",
})
ADMISSION_STATUSES = frozenset({
    "candidate", "accepted", "retired", "conflicted",
})
_REQUIRED = {
    "schema_version", "memory_id", "revision", "workspace_id", "category",
    "content", "content_sha256", "created", "updated", "source_kind",
    "source_refs", "verification_status", "admission_status", "accepted_by",
    "supersedes",
}
_OPTIONAL = {
    "expires_at", "updated_by", "superseded_by", "conflict_with",
    "retired_at", "tags", "sensitive",
}
_SOURCE_REF_KEYS = {"kind", "ref", "sha256", "scope"}
_V1_KEYS = {"schema_version", "memory_id", "created", "content", "tags"}
_HEX = frozenset("0123456789abcdef")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class MemoryMigrationError(ValueError):
    """A v2 record or a v1 store cannot be trusted or migrated."""


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise MemoryMigrationError(f"{name} must be a non-empty string")
    return value


def _sha(value: Any, name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HEX for char in value)):
        raise MemoryMigrationError(f"{name} must be a lowercase SHA-256")
    return value


def _string_list(value: Any, name: str) -> List[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value):
        raise MemoryMigrationError(f"{name} must be a string list")
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized):
        raise MemoryMigrationError(f"{name} must contain non-empty strings")
    if len(normalized) != len(set(normalized)):
        raise MemoryMigrationError(f"{name} must not contain duplicates")
    return normalized


def _bounded_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryMigrationError("revision must be a positive integer")
    return value


def _source_refs(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise MemoryMigrationError("source_refs must be a list")
    result = []
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _SOURCE_REF_KEYS:
            raise MemoryMigrationError(
                f"source_refs[{index}] must be a closed source reference")
        kind = _text(item.get("kind"), f"source_refs[{index}].kind")
        if kind not in {"file", "event", "url"}:
            raise MemoryMigrationError("source reference kind is unsupported")
        ref = _text(item.get("ref"), f"source_refs[{index}].ref")
        scope = _text(item.get("scope"), f"source_refs[{index}].scope")
        digest = _sha(item.get("sha256"), f"source_refs[{index}].sha256")
        identity = (kind, ref, digest, scope)
        if identity in seen:
            raise MemoryMigrationError("source_refs must not contain duplicates")
        seen.add(identity)
        result.append({"kind": kind, "ref": ref, "sha256": digest,
                       "scope": scope})
    return result


def _checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any, name: str
                     ) -> Tuple[datetime.datetime, str]:
    text = _text(value, name)
    if not _TIMESTAMP_RE.fullmatch(text):
        raise MemoryMigrationError(f"{name} must be RFC 3339 UTC")
    date_part, time_part = text[:-1].split("T", 1)
    fraction = ""
    if "." in time_part:
        second, fraction = time_part.split(".", 1)
        time_part = second + "." + fraction[:6].ljust(6, "0")
    try:
        parsed = datetime.datetime.fromisoformat(
            f"{date_part}T{time_part}+00:00")
    except ValueError as exc:
        raise MemoryMigrationError(f"{name} must be RFC 3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise MemoryMigrationError(f"{name} must be RFC 3339 UTC")
    return parsed, fraction


def _timestamp_precedes(left: Any, right: Any) -> bool:
    left_time, left_fraction = left
    right_time, right_fraction = right
    if left_time != right_time:
        return left_time < right_time
    width = max(len(left_fraction), len(right_fraction))
    return (left_fraction.ljust(width, "0")
            < right_fraction.ljust(width, "0"))


def _utc_timestamp(value: Any, name: str) -> str:
    _parse_timestamp(value, name)
    return value


def parse_v2_record(payload: Any) -> Dict[str, Any]:
    """Validate one closed v2 record and return a defensive copy."""
    if not isinstance(payload, dict):
        raise MemoryMigrationError("memory v2 record must be an object")
    keys = set(payload)
    if keys - (_REQUIRED | _OPTIONAL) or not _REQUIRED <= keys:
        raise MemoryMigrationError("memory v2 record has unknown or missing fields")
    if payload.get("schema_version") != V2_SCHEMA_VERSION:
        raise MemoryMigrationError("memory v2 record has the wrong schema")
    result = copy.deepcopy(payload)
    result["memory_id"] = _text(payload.get("memory_id"), "memory_id")
    if not result["memory_id"].startswith("mem_"):
        raise MemoryMigrationError("memory_id must start with mem_")
    result["revision"] = _bounded_revision(payload.get("revision"))
    result["workspace_id"] = _text(payload.get("workspace_id"), "workspace_id")
    result["category"] = _text(payload.get("category"), "category")
    if result["category"] not in CATEGORIES:
        raise MemoryMigrationError("category is unsupported")
    content = _text(payload.get("content"), "content")
    if _sha(payload.get("content_sha256"), "content_sha256") != _checksum(content):
        raise MemoryMigrationError("content_sha256 does not match content")
    result["content"] = content
    for field in ("created", "updated"):
        result[field] = _utc_timestamp(payload.get(field), field)
    if "expires_at" in result:
        result["expires_at"] = _utc_timestamp(
            result["expires_at"], "expires_at")
    result["source_kind"] = _text(payload.get("source_kind"), "source_kind")
    if result["source_kind"] not in SOURCE_KINDS:
        raise MemoryMigrationError("source_kind is unsupported")
    result["source_refs"] = _source_refs(payload.get("source_refs"))
    result["verification_status"] = _text(
        payload.get("verification_status"), "verification_status")
    if result["verification_status"] not in VERIFICATION_STATUSES:
        raise MemoryMigrationError("verification_status is unsupported")
    if (result["source_kind"] == "model_summary"
            and result["verification_status"] == "verified"):
        raise MemoryMigrationError("model summaries cannot verify themselves")
    if result["verification_status"] == "verified" and not result["source_refs"]:
        raise MemoryMigrationError(
            "verified memory requires at least one digest-bound source reference")
    result["admission_status"] = _text(
        payload.get("admission_status"), "admission_status")
    if result["admission_status"] not in ADMISSION_STATUSES:
        raise MemoryMigrationError("admission_status is unsupported")
    accepted_by = payload.get("accepted_by")
    if not isinstance(accepted_by, str):
        raise MemoryMigrationError("accepted_by must be a string")
    result["accepted_by"] = accepted_by.strip()
    if "updated_by" in result:
        updated_by = result["updated_by"]
        if not isinstance(updated_by, str):
            raise MemoryMigrationError("updated_by must be a string")
        result["updated_by"] = updated_by.strip()
    result["supersedes"] = _string_list(payload.get("supersedes"), "supersedes")
    if result["memory_id"] in result["supersedes"]:
        raise MemoryMigrationError("memory cannot supersede itself")
    result["sensitive"] = payload.get("sensitive", False)
    if not isinstance(result["sensitive"], bool):
        raise MemoryMigrationError("sensitive must be a boolean")
    if "superseded_by" in result:
        result["superseded_by"] = _text(result["superseded_by"], "superseded_by")
        if result["superseded_by"] == result["memory_id"]:
            raise MemoryMigrationError("memory cannot be superseded by itself")
        result["admission_status"] = _require_status(
            result["admission_status"], {"retired"})
    if "retired_at" in result:
        result["retired_at"] = _text(result["retired_at"], "retired_at")
        result["admission_status"] = _require_status(
            result["admission_status"], {"retired"})
    if "tags" in result:
        result["tags"] = _string_list(result["tags"], "tags")
    if "conflict_with" in payload:
        result["conflict_with"] = _string_list(
            payload["conflict_with"], "conflict_with")
        if not result["conflict_with"]:
            raise MemoryMigrationError("conflict_with must not be empty")
        if result["memory_id"] in result["conflict_with"]:
            raise MemoryMigrationError("memory cannot conflict with itself")
        result["admission_status"] = _require_status(
            result["admission_status"], {"conflicted"})
    created_at = _parse_timestamp(result["created"], "created")
    updated_at = _parse_timestamp(result["updated"], "updated")
    if _timestamp_precedes(updated_at, created_at):
        raise MemoryMigrationError("updated must not precede created")
    if "expires_at" in result:
        expires_at = _parse_timestamp(result["expires_at"], "expires_at")
        if _timestamp_precedes(expires_at, created_at):
            raise MemoryMigrationError("expires_at must not precede created")
        if (not _timestamp_precedes(updated_at, expires_at)
                and result["admission_status"] != "retired"):
            raise MemoryMigrationError("expired memory must be retired")
    if "retired_at" in result:
        if _timestamp_precedes(
                _parse_timestamp(result["retired_at"], "retired_at"),
                created_at):
            raise MemoryMigrationError("retired_at must not precede created")
    if (result["source_kind"] == "model_summary"
            and result["admission_status"] != "candidate"):
        raise MemoryMigrationError("model summaries must remain candidates")
    lifecycle = {"superseded_by", "conflict_with", "retired_at"} & set(result)
    if result["admission_status"] == "candidate":
        if result["accepted_by"] or lifecycle:
            raise MemoryMigrationError(
                "candidate memory cannot carry approval or lifecycle metadata")
    elif result["admission_status"] == "accepted":
        if not result["accepted_by"] or lifecycle:
            raise MemoryMigrationError(
                "accepted memory requires an approver and no lifecycle metadata")
        if result["verification_status"] not in {"verified", "not_required"}:
            raise MemoryMigrationError(
                "accepted memory cannot carry stale verification")
    elif result["admission_status"] == "retired":
        if not {"superseded_by", "retired_at"} & set(result):
            raise MemoryMigrationError(
                "retired memory requires retirement metadata")
        if "conflict_with" in result:
            raise MemoryMigrationError(
                "retired memory cannot carry conflict metadata")
    elif result["admission_status"] == "conflicted":
        if "conflict_with" not in result:
            raise MemoryMigrationError(
                "conflicted memory requires conflict metadata")
        if "superseded_by" in result or "retired_at" in result:
            raise MemoryMigrationError(
                "conflicted memory cannot carry retirement metadata")
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True)
               + "\n").encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        raise MemoryMigrationError("memory v2 record exceeds the line budget")
    return result


def _require_status(value: str, allowed: set[str]) -> str:
    if value not in allowed:
        raise MemoryMigrationError("related status field disagrees with state")
    return value


def _read_v1(root: Path) -> List[Dict[str, Any]]:
    path = memory_path(root)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if not nofollow:
        raise MemoryMigrationError("v1 memory store requires no-follow open support")
    flags |= nofollow
    try:
        descriptor = os.open(str(path), flags)
    except FileNotFoundError:
        raise MemoryMigrationError("no v1 memory store to migrate")
    except OSError as exc:
        raise MemoryMigrationError(f"cannot open v1 memory store: {exc}")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MemoryMigrationError("v1 memory store must be a regular file")
        if info.st_size > MAX_STORE_BYTES:
            raise MemoryMigrationError("v1 memory store exceeds the read budget")
        chunks = []
        remaining = MAX_STORE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_STORE_BYTES:
            raise MemoryMigrationError("v1 memory store exceeds the read budget")
    except MemoryMigrationError:
        raise
    except OSError as exc:
        raise MemoryMigrationError(f"cannot read v1 memory store: {exc}")
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise MemoryMigrationError(f"cannot read v1 memory store: {exc}")
    records = []
    # JSONL uses LF as the record delimiter. str.splitlines() also splits on
    # U+2028/U+2029 and other Unicode separators that a valid JSON string may
    # contain, turning one v1 record into invalid fragments.
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise MemoryMigrationError("v1 memory line exceeds the line budget")
        try:
            records.append(json.loads(line))
        except ValueError as exc:
            raise MemoryMigrationError(f"v1 memory line {number} is invalid JSON: {exc}")
    return _validated_v1_records(records)


def _validated_v1_records(values: Iterable[Any]) -> List[Dict[str, Any]]:
    records = []
    seen = set()
    for item in values:
        if (not isinstance(item, dict) or set(item) != _V1_KEYS
                or item.get("schema_version") != V1_SCHEMA_VERSION):
            raise MemoryMigrationError("v1 memory line has an unsupported shape")
        memory_id = item.get("memory_id")
        if not isinstance(memory_id, str) or not memory_id.startswith("mem_"):
            raise MemoryMigrationError("v1 memory id is malformed")
        if memory_id in seen:
            raise MemoryMigrationError("v1 memory ids must be unique")
        seen.add(memory_id)
        records.append(item)
    return records


def _legacy_to_v2(record: Dict[str, Any], workspace_id: str) -> Dict[str, Any]:
    content = _text(record.get("content"), "content")
    created = _utc_timestamp(record.get("created"), "created")
    return parse_v2_record({
        "schema_version": V2_SCHEMA_VERSION,
        "memory_id": record["memory_id"],
        "revision": 1,
        "workspace_id": workspace_id,
        "category": "source_note",
        "content": content,
        "content_sha256": _checksum(content),
        "created": created,
        "updated": created,
        "source_kind": "legacy_explicit",
        "source_refs": [],
        "verification_status": "unverified",
        "admission_status": "candidate",
        "accepted_by": "",
        "supersedes": [],
        "sensitive": False,
        "tags": _string_list(record.get("tags") or [], "tags"),
    })


def _resolve_store(root: Path, store: Path) -> Path:
    workspace = Path(root).resolve()
    target = Path(store)
    if not target.is_absolute():
        target = workspace / target
    # Check the operator-selected entry before resolving it. resolve() follows
    # symlinks, so a link that escapes the workspace would otherwise be
    # reported as an escape instead of the unsafe entry it actually is.
    try:
        lexical = target.absolute()
        lexical.relative_to(workspace)
    except ValueError:
        raise MemoryMigrationError("v2 store must stay inside the workspace")
    if target.is_symlink():
        raise MemoryMigrationError("v2 memory store must not be a symlink")
    if target.parent.is_symlink():
        raise MemoryMigrationError(
            "v2 memory store parent must not be a symlink")
    resolved = target.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError:
        raise MemoryMigrationError("v2 store must stay inside the workspace")
    if resolved == memory_path(workspace).resolve():
        raise MemoryMigrationError("v2 store must not replace the v1 store")
    return resolved


def _store_payload(records: Iterable[Dict[str, Any]]) -> bytes:
    validated = [parse_v2_record(record) for record in records]
    payload = b"".join(
        (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        .encode("utf-8") for record in validated)
    if len(payload) > MAX_STORE_BYTES:
        raise MemoryMigrationError(
            f"v2 memory store would exceed {MAX_STORE_BYTES} bytes")
    if any(len(line) > MAX_LINE_BYTES
           for line in payload.splitlines(keepends=True)):
        raise MemoryMigrationError(
            "v2 memory record would exceed the line budget")
    return payload


def _parse_store_text(text: str) -> List[Dict[str, Any]]:
    records = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line:
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise MemoryMigrationError(
                f"v2 memory store line {number} exceeds {MAX_LINE_BYTES} bytes")
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise MemoryMigrationError(
                f"v2 memory store line {number} is invalid JSON: {exc}")
        records.append(parse_v2_record(record))
    return records


def _require_parent_current(parent: Path, parent_descriptor: int) -> None:
    try:
        current = parent.lstat()
    except OSError:
        current = None
    opened = os.fstat(parent_descriptor)
    if (current is None or not stat.S_ISDIR(current.st_mode)
            or current.st_dev != opened.st_dev
            or current.st_ino != opened.st_ino):
        raise MemoryMigrationError(
            "v2 memory store parent must remain stable without following "
            "symlinks")


def _read_store_from_parent(name: str, parent_descriptor: int
                            ) -> List[Dict[str, Any]]:
    if not hasattr(os, "O_NOFOLLOW"):
        raise MemoryMigrationError("v2 store requires no-follow file support")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | os.O_NOFOLLOW
    descriptor = None
    try:
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise MemoryMigrationError(
                    "v2 memory store must be a regular file")
            if info.st_size > MAX_STORE_BYTES:
                raise MemoryMigrationError(
                    f"v2 memory store exceeds {MAX_STORE_BYTES} bytes")
            chunks = []
            remaining = MAX_STORE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_STORE_BYTES:
                raise MemoryMigrationError(
                    f"v2 memory store exceeds {MAX_STORE_BYTES} bytes")
            text = raw.decode("utf-8")
        except MemoryMigrationError:
            raise
        except (OSError, UnicodeError) as exc:
            raise MemoryMigrationError(f"cannot read v2 memory store: {exc}")
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _parse_store_text(text)


def read_v2_store(root: Path, store: Path) -> List[Dict[str, Any]]:
    """Read one bounded v2 JSONL store without following its parent or file."""
    target = _resolve_store(root, store)
    try:
        target.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise MemoryMigrationError(f"cannot inspect v2 memory store: {exc}")
    parent_descriptor = _open_target_parent(
        Path(root).resolve(), target.parent, create=False)
    try:
        records = _read_store_from_parent(target.name, parent_descriptor)
        _require_parent_current(target.parent, parent_descriptor)
        return records
    finally:
        os.close(parent_descriptor)


def _write_store_from_parent(name: str, parent_descriptor: int,
                             payload: bytes,
                             parent_path: Optional[Path] = None) -> None:
    if parent_path is not None:
        _require_parent_current(parent_path, parent_descriptor)
    temporary_name = None
    try:
        temporary_name = f".{name}.{uuid4().hex}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600, dir_fd=parent_descriptor)
        descriptor_needs_close = True
        try:
            handle = os.fdopen(descriptor, "wb")
            descriptor_needs_close = False
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if descriptor_needs_close:
                os.close(descriptor)
        if parent_path is not None:
            _require_parent_current(parent_path, parent_descriptor)
        os.replace(temporary_name, name,
                   src_dir_fd=parent_descriptor,
                   dst_dir_fd=parent_descriptor)
        temporary_name = None
    except OSError as exc:
        raise MemoryMigrationError(f"cannot write v2 memory store: {exc}")
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except OSError:
                pass


def _write_store(root: Path, store: Path,
                 records: Iterable[Dict[str, Any]]) -> None:
    """Atomically replace one v2 store after validating every record."""
    target = _resolve_store(root, store)
    payload = _store_payload(records)
    parent_descriptor = _open_target_parent(
        Path(root).resolve(), target.parent, create=True)
    try:
        _write_store_from_parent(
            target.name, parent_descriptor, payload, parent_path=target.parent)
    finally:
        os.close(parent_descriptor)


def _mutate_v2_store(root: Path, store: Path, change) -> Any:
    """Run one read-modify-write through one no-follow parent descriptor."""
    target = _resolve_store(root, store)
    parent_descriptor = _open_target_parent(
        Path(root).resolve(), target.parent, create=True)
    lock_name = target.name + ".lock"
    lock_file = None
    locked = False
    try:
        try:
            lock_info = os.stat(
                lock_name, dir_fd=parent_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(lock_info.st_mode):
                raise MemoryMigrationError(
                    "v2 memory store lock must not be a symlink")
        except FileNotFoundError:
            pass
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        if not hasattr(os, "O_NOFOLLOW"):
            raise MemoryMigrationError(
                "v2 store requires no-follow lock support")
        flags |= os.O_NOFOLLOW
        descriptor = os.open(
            lock_name, flags, 0o600, dir_fd=parent_descriptor)
        descriptor_needs_close = True
        try:
            lock_file = os.fdopen(descriptor, "a+")
            descriptor_needs_close = False
        finally:
            if descriptor_needs_close:
                os.close(descriptor)
        _lock_nonblocking(lock_file)
        locked = True
        records = _read_store_from_parent(target.name, parent_descriptor)

        def write(validated_records):
            _write_store_from_parent(
                target.name, parent_descriptor,
                _store_payload(validated_records), parent_path=target.parent)

        return change(records, write)
    except MemoryMigrationError:
        raise
    except OSError as exc:
        raise MemoryMigrationError(f"cannot open v2 memory store lock: {exc}")
    finally:
        try:
            if locked:
                _unlock(lock_file)
        finally:
            if lock_file is not None:
                lock_file.close()
            os.close(parent_descriptor)


def _new_timestamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def accept_v2_record(root: Path, store: Path, memory_id: str,
                     accepted_by: str, *,
                     verification_status: str | None = None) -> Dict[str, Any]:
    if not isinstance(accepted_by, str) or not accepted_by.strip():
        raise MemoryMigrationError("accepted_by must be a non-empty string")

    def change(records, write):
        for index, record in enumerate(records):
            if record["memory_id"] != memory_id:
                continue
            if record["admission_status"] != "candidate":
                raise MemoryMigrationError("only candidate memory can be accepted")
            verification = record["verification_status"]
            if verification_status is not None:
                if verification_status != "not_required":
                    raise MemoryMigrationError(
                        "accept may only explicitly mark not_required verification")
                if record["source_kind"] not in {"legacy_explicit", "user_explicit"}:
                    raise MemoryMigrationError(
                        "only explicit user records may be marked not_required")
                verification = verification_status
            if verification not in {"verified", "not_required"}:
                raise MemoryMigrationError("candidate memory has stale verification")
            updated = dict(record)
            updated["verification_status"] = verification
            updated["admission_status"] = "accepted"
            updated["accepted_by"] = accepted_by.strip()
            updated["updated_by"] = accepted_by.strip()
            updated["updated"] = _new_timestamp()
            records[index] = parse_v2_record(updated)
            write(records)
            return records[index]
        raise MemoryMigrationError(f"no such memory: {memory_id}")

    return _mutate_v2_store(root, store, change)


def retire_v2_record(root: Path, store: Path, memory_id: str,
                     *, superseded_by: str | None = None) -> Dict[str, Any]:
    def change(records, write):
        for index, record in enumerate(records):
            if record["memory_id"] != memory_id:
                continue
            if record["admission_status"] == "retired":
                raise MemoryMigrationError("memory is already retired")
            updated = dict(record)
            updated["admission_status"] = "retired"
            updated["retired_at"] = _new_timestamp()
            updated["updated"] = updated["retired_at"]
            if superseded_by is not None:
                replacement = _text(superseded_by, "superseded_by")
                if replacement == memory_id:
                    raise MemoryMigrationError("memory cannot supersede itself")
                if not any(item["memory_id"] == replacement for item in records):
                    raise MemoryMigrationError(
                        f"replacement memory does not exist: {replacement}")
                updated["superseded_by"] = replacement
            records[index] = parse_v2_record(updated)
            write(records)
            return records[index]
        raise MemoryMigrationError(f"no such memory: {memory_id}")

    return _mutate_v2_store(root, store, change)


def forget_v2_record(root: Path, store: Path, memory_id: str) -> None:
    def change(records, write):
        kept = [record for record in records if record["memory_id"] != memory_id]
        if len(kept) == len(records):
            raise MemoryMigrationError(f"no such memory: {memory_id}")
        write(kept)

    _mutate_v2_store(root, store, change)


def _open_target_parent(workspace: Path, parent: Path, *,
                        create: bool = True) -> int:
    """Open the target's parent without following a replaced symlink."""
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise MemoryMigrationError("v2 target requires no-follow directory support")
    if not hasattr(os, "O_DIRECTORY"):
        raise MemoryMigrationError("v2 target requires directory descriptors")
    try:
        relative = parent.relative_to(workspace)
    except ValueError:
        raise MemoryMigrationError("v2 target must stay inside the workspace")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(str(workspace), flags)
        for part in relative.parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise MemoryMigrationError(
            "cannot open v2 target parent without following symlinks") from exc
    return descriptor


def migrate_v1_to_v2(root: Path, *, workspace_id: str, out: Path,
                     records: Iterable[Dict[str, Any]] | None = None
                     ) -> Dict[str, Any]:
    """Convert bounded v1 records to an exclusive new v2 file.

    The source path is read-only. The target must be outside the source file
    itself, inside the supplied workspace, and must not already exist.
    """
    workspace = Path(root).resolve()
    source = memory_path(workspace)
    target = Path(out)
    if not target.is_absolute():
        target = workspace / target
    target = target.resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        raise MemoryMigrationError("v2 target must stay inside the workspace")
    if target == source.resolve():
        raise MemoryMigrationError("v2 target must not replace the v1 store")
    if target.exists():
        raise MemoryMigrationError("v2 target already exists")
    source_records = (_read_v1(workspace) if records is None
                      else _validated_v1_records(records))
    if not source_records:
        raise MemoryMigrationError("v1 memory store has no records to migrate")
    converted = [_legacy_to_v2(item, workspace_id) for item in source_records]
    lines = [
        (json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for item in converted]
    if any(len(line) > MAX_LINE_BYTES for line in lines):
        raise MemoryMigrationError("v2 memory line would exceed the line budget")
    payload = b"".join(lines)
    if len(payload) > MAX_STORE_BYTES:
        raise MemoryMigrationError("v2 memory store would exceed the size budget")
    parent_descriptor = _open_target_parent(workspace, target.parent)
    try:
        descriptor = os.open(
            target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644, dir_fd=parent_descriptor)
    except OSError as exc:
        os.close(parent_descriptor)
        raise MemoryMigrationError(f"cannot create v2 memory store: {exc}")

    descriptor_needs_close = True
    try:
        handle = os.fdopen(descriptor, "wb")
        descriptor_needs_close = False
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            os.unlink(target.name, dir_fd=parent_descriptor)
        except OSError:
            pass
        if descriptor_needs_close:
            os.close(descriptor)
        os.close(parent_descriptor)
        raise MemoryMigrationError(f"cannot write v2 memory store: {exc}")
    else:
        os.close(parent_descriptor)
    return {
        "schema_version": V2_SCHEMA_VERSION,
        "source": source.as_posix(),
        "target": target.as_posix(),
        "migrated": len(converted),
        "source_bytes": source.stat().st_size if source.exists() else 0,
    }
