"""Explicit local memory: append-only facts the operator chooses to keep."""

from __future__ import annotations

import json
import os
import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List
from uuid import uuid4

SCHEMA_VERSION = "pocketchat-memory.v1"
STORE_PATH = Path(".pocketchat") / "memory.jsonl"
MAX_CONTENT_BYTES = 16 * 1024
MAX_TAGS = 16


class MemoryError(ValueError):
    """The memory request cannot be completed safely."""


def memory_path(root: Path) -> Path:
    return Path(root) / STORE_PATH


def _read(root: Path) -> List[Dict[str, Any]]:
    path = memory_path(root)
    if not path.exists():
        return []
    records = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise MemoryError("cannot read memory store: %s" % exc)
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
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
