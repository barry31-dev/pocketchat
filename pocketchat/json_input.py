"""Pure, caller-bounded decoding of one strict UTF-8 JSON value.

The decoder accepts bytes that a caller has already acquired. It performs no
file, environment, network, command, provider or clock I/O, and it does not
canonicalize or otherwise rewrite a valid value.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Iterable, Optional, Tuple

PathSegment = Tuple[str, int]
StructuralPath = Tuple[PathSegment, ...]
MAX_JSON_DEPTH = 64
_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


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
        # An active caller handler may attach its own exception as context.
        # Suppress its display for every refusal, including pre-decode limits.
        self.__suppress_context__ = True


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


def _integer(text: str) -> int:
    """Convert JSON integers without changing the process-wide digit guard."""
    negative = text.startswith("-")
    value = 0
    # Each conversion stays below CPython's minimum configurable digit limit.
    # The already-admitted byte limit, not an ambient process setting, bounds
    # the complete number. The JSON decoder owns validation of its grammar.
    for offset in range(1 if negative else 0, len(text), 512):
        chunk = text[offset:offset + 512]
        value = value * 10 ** len(chunk) + int(chunk)
    return -value if negative else value


def _byte_offset(text: str, character_offset: int) -> int:
    return len(text[:character_offset].encode("utf-8"))


def _skip_whitespace(text: str, offset: int) -> int:
    while offset < len(text) and text[offset] in " \t\r\n":
        offset += 1
    return offset


def _admit_structure(text: str, start: int,
                     max_depth: int, max_nodes: int) -> None:
    """Count the first value without building strings, numbers or containers.

    The standard library still owns value conversion. This lexical pass keeps
    its whole-value decoder behind the caller's depth and node budgets. Only
    a depth-bounded stack of offsets and key-free paths is retained here.
    """
    nodes = 0

    def invalid(offset: int) -> None:
        raise _refuse("invalid_json", "syntax",
                      offset=_byte_offset(text, offset))

    def string_end(offset: int) -> int:
        offset += 1  # Opening quote.
        while offset < len(text):
            character = text[offset]
            if character == '"':
                return offset + 1
            if ord(character) < 0x20:
                invalid(offset)
            if character == "\\":
                offset += 1
                if offset >= len(text):
                    invalid(offset)
                escape = text[offset]
                if escape == "u":
                    digits = text[offset + 1:offset + 5]
                    if (len(digits) != 4
                            or any(c not in "0123456789abcdefABCDEF"
                                   for c in digits)):
                        invalid(offset)
                    offset += 4
                elif escape not in '\\"/bfnrt':
                    invalid(offset)
            offset += 1
        invalid(offset)

    def value_end(offset: int, parent_depth: int,
                  path: StructuralPath) -> int:
        nonlocal nodes
        offset = _skip_whitespace(text, offset)
        if offset >= len(text):
            invalid(offset)
        character = text[offset]
        if character not in '[{"-0123456789tfnNI':
            invalid(offset)
        nodes += 1
        if nodes > max_nodes:
            raise _refuse("node_limit_exceeded", "limit", path=path)

        if character in "[{":
            depth = parent_depth + 1
            if depth > max_depth:
                raise _refuse("depth_limit_exceeded", "limit", path=path)
            kind, closing = ("array", "]") if character == "[" else ("object", "}")
            offset = _skip_whitespace(text, offset + 1)
            if offset < len(text) and text[offset] == closing:
                return offset + 1
            ordinal = 0
            while True:
                if kind == "object":
                    if offset >= len(text) or text[offset] != '"':
                        invalid(offset)
                    offset = _skip_whitespace(text, string_end(offset))
                    if offset >= len(text) or text[offset] != ":":
                        invalid(offset)
                    offset += 1
                offset = value_end(offset, depth, path + ((kind, ordinal),))
                offset = _skip_whitespace(text, offset)
                if offset < len(text) and text[offset] == closing:
                    return offset + 1
                if offset >= len(text) or text[offset] != ",":
                    invalid(offset)
                offset = _skip_whitespace(text, offset + 1)
                ordinal += 1
        if character == '"':
            return string_end(offset)
        for literal in ("true", "false", "null", "NaN", "Infinity", "-Infinity"):
            if text.startswith(literal, offset):
                return offset + len(literal)
        number = _NUMBER.match(text, offset)
        if number is not None:
            return number.end()
        invalid(offset)

    # Ignore the tail here: raw_decode owns trailing-value and numeric errors.
    value_end(start, 0, ())


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

    failure = None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        failure = _refuse("invalid_utf8", "encoding", offset=exc.start)
    # Raising outside the handler avoids retaining the input-bearing decoder
    # exception in __context__; `from None` alone only hides its display.
    if failure is not None:
        raise failure from None

    decoder = json.JSONDecoder(
        object_pairs_hook=_object,
        parse_constant=_constant,
        parse_int=_integer,
    )
    start = _skip_whitespace(text, 0)
    try:
        _admit_structure(text, start, max_depth, max_nodes)
        value, end = decoder.raw_decode(text, start)
    except JSONInputError as exc:
        failure = exc
    except json.JSONDecodeError as exc:
        failure = _refuse(
            "invalid_json", "syntax",
            offset=_byte_offset(text, exc.pos))
    except RecursionError:
        failure = _refuse("depth_limit_exceeded", "limit")
    except (TypeError, ValueError):
        failure = _refuse("invalid_json", "syntax")
    if failure is not None:
        raise failure from None

    trailing = _skip_whitespace(text, end)
    if trailing != len(text):
        raise _refuse(
            "trailing_value", "syntax",
            offset=_byte_offset(text, trailing))

    _check_structure(value, max_depth, max_nodes)
    return value
