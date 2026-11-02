"""Explicit local memory: append-only facts the operator chooses to keep."""

from __future__ import annotations

import datetime
import errno
import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = "pocketchat-memory.v1"
STORE_PATH = Path(".pocketchat") / "memory.jsonl"
MAX_CONTENT_BYTES = 16 * 1024
MAX_STORE_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 32 * 1024
MAX_TAGS = 16


class MemoryError(ValueError):
    """The memory request cannot be completed safely."""


def memory_path(root: Path) -> Path:
    return Path(root) / STORE_PATH


def _read(root: Path) -> list[dict[str, Any]]:
    path = memory_path(root)
    try:
        path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise MemoryError(f"cannot inspect memory store: {exc}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        if path.is_symlink():
            raise MemoryError("memory store must not be a symlink")
        descriptor = os.open(str(path), flags)
    except MemoryError:
        raise
    except OSError as exc:
        raise MemoryError(f"cannot open memory store: {exc}")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MemoryError("memory store must be a regular file")
        if info.st_size > MAX_STORE_BYTES:
            raise MemoryError(
                f"memory store exceeds {MAX_STORE_BYTES} bytes")
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
            raise MemoryError(
                f"memory store exceeds {MAX_STORE_BYTES} bytes")
        text = raw.decode("utf-8")
    except MemoryError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MemoryError(f"cannot read memory store: {exc}")
    finally:
        os.close(descriptor)

    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise MemoryError(
                f"memory store line {number} exceeds {MAX_LINE_BYTES} bytes")
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise MemoryError(
                f"memory store line {number} is invalid JSON: {exc}")
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise MemoryError(
                f"memory store line {number} has an unsupported shape")
        records.append(record)
    return records


def _lock_path(root: Path) -> Path:
    return memory_path(root).with_name(memory_path(root).name + ".lock")


def _mutate(root: Path, change):
    """Apply one read-modify-write while holding the store lock."""
    path = memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise MemoryError("memory store must not be a symlink")
    lock = _lock_path(root)
    try:
        lock_file = lock.open("a+")
    except OSError as exc:
        raise MemoryError(f"cannot open memory store lock: {exc}")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise MemoryError("memory store is busy")
            raise MemoryError(f"cannot lock memory store: {exc}")
        records = _read(root)
        changed = change(records)
        if changed:
            encoded = b"".join(
                (json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                .encode("utf-8") for record in changed)
            if len(encoded) > MAX_STORE_BYTES:
                raise MemoryError(
                    f"memory store would exceed {MAX_STORE_BYTES} bytes")
            _atomic_write_unlocked(root, changed)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise MemoryError(f"cannot remove memory store: {exc}")
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def _atomic_write_unlocked(root: Path, records: Iterable[dict[str, Any]]) -> None:
    path = memory_path(root)
    temporary_name = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary_name), str(path))
        temporary_name = None
    except OSError as exc:
        raise MemoryError(f"cannot write memory store: {exc}")
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _tags(tags: Iterable[str]) -> list[str]:
    result = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise MemoryError("memory tags must be non-empty strings")
        value = tag.strip()
        if value not in result:
            result.append(value)
    if len(result) > MAX_TAGS:
        raise MemoryError(f"at most {MAX_TAGS} tags are allowed")
    return result


def add_memory(root: Path, content: str, tags: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise MemoryError("memory content must be non-empty")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise MemoryError(f"memory content exceeds {MAX_CONTENT_BYTES} bytes")
    record = {
        "schema_version": SCHEMA_VERSION,
        "memory_id": f"mem_{uuid4().hex}",
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"),
        "content": content,
        "tags": _tags(tags),
    }
    _mutate(root, lambda records: records + [record])
    return record


def list_memory(root: Path) -> list[dict[str, Any]]:
    return _read(root)


def search_memory(root: Path, needle: str) -> list[dict[str, Any]]:
    if not isinstance(needle, str) or not needle:
        raise MemoryError("memory search text must be non-empty")
    return [record for record in _read(root) if needle in record.get("content", "")]


def remove_memory(root: Path, memory_id: str) -> None:
    if not isinstance(memory_id, str) or not memory_id.startswith("mem_"):
        raise MemoryError("memory id must start with mem_")
    def remove(records):
        kept = [record for record in records
                if record.get("memory_id") != memory_id]
        if len(kept) == len(records):
            raise MemoryError(f"no such memory: {memory_id}")
        return kept

    _mutate(root, remove)
