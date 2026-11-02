"""Bounded projections of tool results sent back to a model.

The projection is deterministic and does not mutate the tool's raw result. It
bounds the bytes of the final JSON envelope, not just one text field, so
metadata cannot push an otherwise short result over the caller's limit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ResultProjectionError(ValueError):
    """A tool result cannot be represented within the requested bound."""

    def __init__(self, message: str, error_type: str = "invalid_tool_result"):
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class ResultProjection:
    encoded: str
    bytes_omitted: int


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _fit_text(value: str, limit: int) -> str:
    """Return the longest prefix of ``value`` that encodes within ``limit``."""
    if limit <= 0:
        return ""
    if len(value.encode("utf-8")) <= limit:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if len(value[:middle].encode("utf-8")) <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _positive_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ResultProjectionError(
            "max_bytes must be a positive integer",
            error_type="invalid_result_limit")
    return value


def project_result(tool_call_id: str, kind: str, result: Any, *,
                   max_bytes: int,
                   omit_content_echo: bool = False,
                   effect_disposition: str | None = None) -> ResultProjection:
    """Render one bounded result envelope for the next model request.

    Recognized result fields are retained when they fit. A text field is
    shortened independently until the complete encoded envelope fits. The
    returned flag says the result was cut; it never claims a complete digest
    for the omitted suffix.
    """
    max_bytes = _positive_limit(max_bytes)
    if not isinstance(result, dict):
        raise ResultProjectionError("tool result must be an object")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise ResultProjectionError("tool_call_id must be non-empty")
    if not isinstance(kind, str) or not kind:
        raise ResultProjectionError("tool kind must be non-empty")

    if result.get("status") == "failed":
        error = result.get("error")
        if not isinstance(error, dict):
            raise ResultProjectionError("failed tool result needs an error object")
        if not isinstance(error.get("type"), str) or not isinstance(
                error.get("message"), str):
            raise ResultProjectionError(
                "failed tool result error needs text type and message")
    source = result.get("path")
    if source is not None and not isinstance(source, str):
        raise ResultProjectionError("tool result path must be text")
    content = result.get("content")
    if content is not None and not isinstance(content, str):
        raise ResultProjectionError("tool result content must be text")

    projection: dict[str, Any] = {
        "schema_version": "pocketchat-tool-result.v1",
        "tool_call_id": tool_call_id,
        "kind": kind,
        "status": result.get("status", "completed"),
        "effect_disposition": effect_disposition or (
            "applied" if kind == "write_file" else "none"),
        "source": source or "",
        "truncated": bool(result.get("truncated", False)),
    }
    if result.get("bytes_omitted") not in (None, 0):
        projection["bytes_omitted"] = result["bytes_omitted"]
    for key in ("bytes", "overwritten", "items", "total_items",
                "omitted_items", "bytes_omitted"):
        if key in result:
            projection[key] = result[key]
    if result.get("status") == "failed":
        projection["error"] = result["error"]
    if content is not None and not omit_content_echo:
        projection["content"] = content

    # Reserve room for truncation metadata, then shrink only the content
    # field. Re-encode after every change because JSON escaping changes size.
    base = dict(projection)
    baseline = dict(base)
    baseline.pop("content", None)
    minimum = _encoded(baseline)
    if len(minimum.encode("utf-8")) > max_bytes:
        raise ResultProjectionError(
            "tool result metadata exceeds the configured limit",
            error_type="result_projection_too_small")

    original_bytes = len(content.encode("utf-8")) if content is not None else 0
    omitted = 0
    if "content" in projection:
        low, high = 0, len(content or "")
        best = None
        while low <= high:
            middle = (low + high) // 2
            candidate = dict(base)
            candidate["content"] = (content or "")[:middle]
            candidate["truncated"] = (
                projection["truncated"]
                or candidate["content"] != (content or ""))
            candidate["bytes_omitted"] = max(
                0, original_bytes
                - len(candidate["content"].encode("utf-8")))
            encoded = _encoded(candidate)
            if len(encoded.encode("utf-8")) <= max_bytes:
                best = (candidate, candidate["bytes_omitted"])
                low = middle + 1
            else:
                high = middle - 1
        if best is None:
            raise ResultProjectionError(
                "tool result metadata exceeds the configured limit",
                error_type="result_projection_too_small")
        projection, omitted = best

    encoded = _encoded(projection)
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ResultProjectionError(
            "tool result metadata exceeds the configured limit",
            error_type="result_projection_too_small")
    return ResultProjection(encoded=encoded, bytes_omitted=omitted)
