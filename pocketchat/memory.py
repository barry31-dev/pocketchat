"""Explicit local memory: append-only facts the operator chooses to keep."""

from __future__ import annotations

import datetime
import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, Iterable, List
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


def _read(root: Path) -> List[Dict[str, Any]]:
    path = memory_path(root)
    try:
        path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise MemoryError("cannot inspect memory store: %s" % exc)
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
        raise MemoryError("cannot open memory store: %s" % exc)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise MemoryError("memory store must be a regular file")
        if info.st_size > MAX_STORE_BYTES:
            raise MemoryError(
                "memory store exceeds %d bytes" % MAX_STORE_BYTES)
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
                "memory store exceeds %d bytes" % MAX_STORE_BYTES)
        text = raw.decode("utf-8")
    except MemoryError:
        raise
    except (OSError, UnicodeError) as exc:
        raise MemoryError("cannot read memory store: %s" % exc)
    finally:
        os.close(descriptor)

    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise MemoryError(
                "memory store line %d exceeds %d bytes"
                % (number, MAX_LINE_BYTES))
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise MemoryError("memory store line %d is invalid JSON: %s"
                              % (number, exc))
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise MemoryError("memory store line %d has an unsupported shape" % number)
        records.append(record)
    return records


def _atomic_write(root: Path, records: Iterable[Dict[str, Any]]) -> None:
    path = memory_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise MemoryError("cannot write memory store: %s" % exc)


def _tags(tags: Iterable[str]) -> List[str]:
    result = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.strip():
            raise MemoryError("memory tags must be non-empty strings")
        value = tag.strip()
        if value not in result:
            result.append(value)
    if len(result) > MAX_TAGS:
        raise MemoryError("at most %d tags are allowed" % MAX_TAGS)
    return result


def add_memory(root: Path, content: str, tags: Iterable[str] = ()) -> Dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise MemoryError("memory content must be non-empty")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise MemoryError("memory content exceeds %d bytes" % MAX_CONTENT_BYTES)
    records = _read(root)
    record = {
        "schema_version": SCHEMA_VERSION,
        "memory_id": "mem_%s" % uuid4().hex,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z"),
        "content": content,
        "tags": _tags(tags),
    }
    _atomic_write(root, records + [record])
    return record


def list_memory(root: Path) -> List[Dict[str, Any]]:
    return _read(root)


def search_memory(root: Path, needle: str) -> List[Dict[str, Any]]:
    if not isinstance(needle, str) or not needle:
        raise MemoryError("memory search text must be non-empty")
    return [record for record in _read(root) if needle in record.get("content", "")]


def remove_memory(root: Path, memory_id: str) -> None:
    if not isinstance(memory_id, str) or not memory_id.startswith("mem_"):
        raise MemoryError("memory id must start with mem_")
    records = _read(root)
    kept = [record for record in records if record.get("memory_id") != memory_id]
    if len(kept) == len(records):
        raise MemoryError("no such memory: %s" % memory_id)
    if kept:
        _atomic_write(root, kept)
    else:
        path = memory_path(root)
        try:
            path.unlink()
        except OSError as exc:
            raise MemoryError("cannot remove memory store: %s" % exc)
