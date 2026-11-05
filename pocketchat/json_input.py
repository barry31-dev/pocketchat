"""Pure, caller-bounded decoding of one strict UTF-8 JSON value.

The decoder accepts bytes that a caller has already acquired. It performs no
file, environment, network, command, provider or clock I/O, and it does not
canonicalize or otherwise rewrite a valid value.
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional, Tuple

PathSegment = Tuple[str, int]
StructuralPath = Tuple[PathSegment, ...]
MAX_JSON_DEPTH = 64


class JSONInputError(ValueError):
    """Caller-supplied bytes cannot be decoded within the declared bounds."""

    def __init__(self, error_type: str, category: str, *,
                 offset: Optional[int] = None,
                 path: Optional[Iterable[PathSegment]] = None) -> None:
        super().__init__("JSON input refused: %s" % error_type)
        self.error_type = error_type
        self.category = category
        self.offset = offset
        self.path = tuple(path) if path is not None else None


def _refuse(error_type: str, category: str, *,
            offset: Optional[int] = None,
            path: Optional[StructuralPath] = None) -> JSONInputError:
    return JSONInputError(
        error_type, category, offset=offset, path=path)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _refuse("duplicate_key", "structure")
        result[key] = value
    return result


def _constant(_value):
    raise _refuse("nonfinite_number", "number")


def _byte_offset(text: str, character_offset: int) -> int:
    return len(text[:character_offset].encode("utf-8"))


def _skip_whitespace(text: str, offset: int) -> int:
    while offset < len(text) and text[offset] in " \t\r\n":
        offset += 1
    return offset


def _check_structure(value: Any, max_depth: int, max_nodes: int) -> None:
    stack = [(value, 0, ())]
    nodes = 0
    while stack:
        current, parent_depth, path = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise _refuse(
                "node_limit_exceeded", "limit", path=path)

        if isinstance(current, float) and not math.isfinite(current):
            raise _refuse("nonfinite_number", "number", path=path)

        if isinstance(current, list):
            depth = parent_depth + 1
            if depth > max_depth:
                raise _refuse(
                    "depth_limit_exceeded", "limit", path=path)
            for index in range(len(current) - 1, -1, -1):
                child_path = path + (("array", index),)
                stack.append((current[index], depth, child_path))
        elif isinstance(current, dict):
            depth = parent_depth + 1
            if depth > max_depth:
                raise _refuse(
                    "depth_limit_exceeded", "limit", path=path)
            values = list(current.values())
            for ordinal in range(len(values) - 1, -1, -1):
                child_path = path + (("object", ordinal),)
                stack.append((values[ordinal], depth, child_path))


def decode_json_bytes(data: bytes, max_bytes: int,
                      max_depth: int, max_nodes: int) -> Any:
    """Decode one strict JSON value within explicit caller-supplied limits."""
    limits = (max_bytes, max_depth, max_nodes)
    if (type(data) is not bytes
            or any(isinstance(value, bool) or not isinstance(value, int)
                   or value < 1 for value in limits)
            or max_depth > MAX_JSON_DEPTH):
        raise _refuse("invalid_limits", "limit")
    if len(data) > max_bytes:
        raise _refuse("byte_limit_exceeded", "limit")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _refuse(
            "invalid_utf8", "encoding", offset=exc.start) from None

    decoder = json.JSONDecoder(
        object_pairs_hook=_object,
        parse_constant=_constant,
    )
    start = _skip_whitespace(text, 0)
    try:
        value, end = decoder.raw_decode(text, start)
    except JSONInputError:
        raise
    except json.JSONDecodeError as exc:
        raise _refuse(
            "invalid_json", "syntax",
            offset=_byte_offset(text, exc.pos)) from None
    except RecursionError:
        raise _refuse("depth_limit_exceeded", "limit") from None
    except (TypeError, ValueError):
        raise _refuse("invalid_json", "syntax") from None

    trailing = _skip_whitespace(text, end)
    if trailing != len(text):
        raise _refuse(
            "trailing_value", "syntax",
            offset=_byte_offset(text, trailing))

    _check_structure(value, max_depth, max_nodes)
    return value
