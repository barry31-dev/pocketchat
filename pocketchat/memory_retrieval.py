"""Deterministic retrieval over validated memory-v2 records.

This module is a pure candidate core. It does not read a store, call a model,
or change the legacy ``memory search`` command. Admission gates run before
query scoring and Top-K selection; excerpts are bounded by both UTF-8 bytes
and an explicit deterministic token estimate.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .memory_migration import MemoryMigrationError, parse_v2_record

SCHEMA_VERSION = "pocketchat-memory-retrieval.v1"
CURSOR_SCHEMA_VERSION = "pocketchat-memory-retrieval-cursor.v1"
TOKENIZER_VERSION = "lexical-zh-en.v1"
TOKEN_ESTIMATOR_ID = "chars4.v1"
_TOKEN_PATTERN = re.compile(
    r"[a-z0-9]+|[\u3400-\u9fff\U00020000-\U0003134f]+")
_CURSOR_KEYS = {
    "schema_version", "tokenizer_version", "query_sha256", "order_sha256",
    "after_memory_id", "before_cursor",
}


class MemoryRetrievalError(ValueError):
    """The retrieval request or continuation cursor cannot be trusted."""

    def __init__(self, message: str, error_type: str = "invalid_retrieval"):
        super().__init__(message)
        self.error_type = error_type


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _terms(value: str) -> list[str]:
    """Return ordered English words and Chinese two-character fragments."""
    result: list[str] = []
    seen = set()
    for match in _TOKEN_PATTERN.finditer(_normalized(value)):
        token = match.group(0)
        pieces = []
        if _is_cjk(token[0]):
            if len(token) == 1:
                pieces.append(token)
            else:
                pieces.extend(token[index:index + 2]
                              for index in range(len(token) - 1))
        else:
            pieces.append(token)
        for piece in pieces:
            if piece not in seen:
                seen.add(piece)
                result.append(piece)
    return result


def _is_cjk(value: str) -> bool:
    code = ord(value)
    return (0x3400 <= code <= 0x9FFF
            or 0x20000 <= code <= 0x3134F)


def _timestamp(value: str, name: str) -> datetime.datetime:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryRetrievalError(
            f"{name} must be RFC 3339 UTC",
            error_type="invalid_memory_record") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != datetime.timedelta(0):
        raise MemoryRetrievalError(
            f"{name} must be RFC 3339 UTC",
            error_type="invalid_memory_record")
    return parsed


def _positive_limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryRetrievalError(
            f"{name} must be a positive integer",
            error_type="invalid_retrieval_limit")
    return value


def _estimated_tokens(value: str) -> int:
    return math.ceil(len(value.encode("utf-8")) / 4)


def _prefix_for_bytes(value: str, limit: int) -> str:
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


def _prefix_for_tokens(value: str, limit: int) -> str:
    if limit <= 0:
        return ""
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if _estimated_tokens(value[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return value[:low]


def _encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        _canonical_bytes(payload)).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, query: str, order_sha256: str) -> tuple[int, str]:
    if not isinstance(value, str) or not value:
        raise MemoryRetrievalError(
            "cursor must be non-empty text",
            error_type="invalid_memory_retrieval_cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(
            padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryRetrievalError(
            "cursor is malformed",
            error_type="invalid_memory_retrieval_cursor") from exc
    if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
        raise MemoryRetrievalError(
            "cursor has an unsupported shape",
            error_type="invalid_memory_retrieval_cursor")
    if (payload.get("schema_version") != CURSOR_SCHEMA_VERSION
            or payload.get("tokenizer_version") != TOKENIZER_VERSION
            or payload.get("query_sha256") != _digest(query)
            or payload.get("order_sha256") != order_sha256):
        raise MemoryRetrievalError(
            "cursor no longer matches this retrieval",
            error_type="memory_retrieval_cursor_stale")
    after = payload.get("after_memory_id")
    before = payload.get("before_cursor")
    if (not isinstance(after, str) or not after
            or isinstance(before, bool) or not isinstance(before, int)
            or before < 0):
        raise MemoryRetrievalError(
            "cursor position is malformed",
            error_type="invalid_memory_retrieval_cursor")
    return before, after


def _rank(record: dict[str, Any], query_terms: list[str],
          query_normalized: str) -> dict[str, Any] | None:
    content_normalized = _normalized(record["content"])
    content_terms = set(_terms(record["content"]))
    matched_terms = [term for term in query_terms if term in content_terms]
    tags = record.get("tags", [])
    matched_tags = [
        tag for tag in tags
        if set(_terms(tag)) & set(query_terms)
        or _normalized(tag) == query_normalized
    ]
    if not matched_terms and not matched_tags:
        return None
    return {
        "record": record,
        "exact_phrase": query_normalized in content_normalized,
        "matched_terms": matched_terms,
        "matched_tags": matched_tags,
    }


def _sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    record = candidate["record"]
    return (
        not candidate["exact_phrase"],
        -len(candidate["matched_terms"]),
        -len(candidate["matched_tags"]),
        -_timestamp(record["updated"], "updated").timestamp(),
        record["memory_id"],
    )


def _public_item(candidate: dict[str, Any], excerpt: str, *,
                 truncated: bool) -> dict[str, Any]:
    record = candidate["record"]
    item = {
        "memory_id": record["memory_id"],
        "revision": record["revision"],
        "workspace_id": record["workspace_id"],
        "category": record["category"],
        "content_sha256": record["content_sha256"],
        "created": record["created"],
        "updated": record["updated"],
        "source_kind": record["source_kind"],
        "source_refs": record["source_refs"],
        "verification_status": record["verification_status"],
        "admission_status": record["admission_status"],
        "accepted_by": record["accepted_by"],
        "tags": record.get("tags", []),
        "sensitive": record["sensitive"],
        "exact_phrase": candidate["exact_phrase"],
        "matched_terms": candidate["matched_terms"],
        "matched_tags": candidate["matched_tags"],
        "excerpt": excerpt,
        "excerpt_bytes": len(excerpt.encode("utf-8")),
        "excerpt_truncated": truncated,
        "estimated_tokens": _estimated_tokens(excerpt),
        "token_estimator_id": TOKEN_ESTIMATOR_ID,
    }
    if "expires_at" in record:
        item["expires_at"] = record["expires_at"]
    return item


def retrieve_memories(records: Iterable[dict[str, Any]], query: str, *,
                      workspace_id: str,
                      now: datetime.datetime,
                      max_results: int,
                      max_excerpt_bytes: int,
                      max_recall_tokens: int,
                      cursor: str | None = None) -> dict[str, Any]:
    """Retrieve admitted records with deterministic ranking and bounded excerpts."""
    if not isinstance(query, str) or not query.strip():
        raise MemoryRetrievalError(
            "query must be non-empty text",
            error_type="invalid_retrieval")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise MemoryRetrievalError(
            "workspace_id must be non-empty text",
            error_type="invalid_retrieval")
    if not isinstance(now, datetime.datetime) or now.tzinfo is None \
            or now.utcoffset() != datetime.timedelta(0):
        raise MemoryRetrievalError(
            "now must be a UTC timestamp",
            error_type="invalid_retrieval")
    max_results = _positive_limit(max_results, "max_results")
    max_excerpt_bytes = _positive_limit(max_excerpt_bytes, "max_excerpt_bytes")
    max_recall_tokens = _positive_limit(max_recall_tokens, "max_recall_tokens")

    query_normalized = _normalized(query.strip())
    query_terms = _terms(query)
    if not query_terms:
        raise MemoryRetrievalError(
            "query must contain a supported term",
            error_type="invalid_retrieval")

    excluded: list[dict[str, Any]] = []
    scored: list[dict[str, Any]] = []
    seen_ids = set()
    for raw in records:
        try:
            record = parse_v2_record(raw)
        except MemoryMigrationError as exc:
            raise MemoryRetrievalError(
                str(exc), error_type="invalid_memory_record") from exc
        memory_id = record["memory_id"]
        if memory_id in seen_ids:
            raise MemoryRetrievalError(
                "memory ids must be unique",
                error_type="duplicate_memory_id")
        seen_ids.add(memory_id)
        reasons = []
        if record["workspace_id"] != workspace_id.strip():
            reasons.append("workspace_mismatch")
        if record["admission_status"] != "accepted":
            reasons.append("not_accepted")
        if record["sensitive"]:
            reasons.append("sensitive")
        if "expires_at" in record and _timestamp(
                record["expires_at"], "expires_at") <= now:
            reasons.append("expired")
        if reasons:
            excluded.append({"memory_id": memory_id, "reasons": reasons})
            continue
        candidate = _rank(record, query_terms, query_normalized)
        if candidate is None:
            excluded.append({"memory_id": memory_id, "reasons": ["no_query_match"]})
            continue
        scored.append(candidate)

    scored.sort(key=_sort_key)
    order_sha256 = _digest([
        [item["record"]["memory_id"], item["record"]["revision"],
         item["record"]["content_sha256"], item["record"]["updated"],
         item["record"].get("tags", [])]
        for item in scored
    ])

    before_cursor = 0
    start = 0
    if cursor is not None:
        before_cursor, after = _decode_cursor(
            cursor, query=query, order_sha256=order_sha256)
        ids = [item["record"]["memory_id"] for item in scored]
        if after not in ids or ids.index(after) + 1 != before_cursor:
            raise MemoryRetrievalError(
                "cursor position no longer matches this retrieval",
                error_type="memory_retrieval_cursor_stale")
        start = before_cursor

    items: list[dict[str, Any]] = []
    omissions = {
        "before_cursor": before_cursor,
        "max_results": 0,
        "byte_budget": 0,
        "token_budget": 0,
    }
    remaining_bytes = max_excerpt_bytes
    remaining_tokens = max_recall_tokens
    stop_index: int | None = None
    budget_reason = ""
    truncated_count = 0

    for index, candidate in enumerate(scored[start:], start):
        if len(items) >= max_results:
            break
        content = candidate["record"]["content"]
        excerpt = _prefix_for_bytes(content, remaining_bytes)
        if not excerpt:
            stop_index = index
            budget_reason = "byte_budget"
            break
        token_excerpt = _prefix_for_tokens(excerpt, remaining_tokens)
        if not token_excerpt:
            stop_index = index
            budget_reason = "token_budget"
            break
        excerpt = token_excerpt
        truncated = excerpt != content
        item = _public_item(candidate, excerpt, truncated=truncated)
        items.append(item)
        if truncated:
            truncated_count += 1
        remaining_bytes -= item["excerpt_bytes"]
        remaining_tokens -= item["estimated_tokens"]

    if len(items) >= max_results:
        max_results_omitted = len(scored) - start - len(items)
        omissions["max_results"] = max(0, max_results_omitted)
    elif stop_index is not None:
        omissions[budget_reason] = len(scored) - stop_index

    current_omissions = (omissions["max_results"]
                         + omissions["byte_budget"]
                         + omissions["token_budget"])
    matched_count = len(scored)
    selected_through = start + len(items)
    can_continue = selected_through < len(scored) and bool(items)
    if can_continue:
        next_cursor = _encode_cursor({
            "schema_version": CURSOR_SCHEMA_VERSION,
            "tokenizer_version": TOKENIZER_VERSION,
            "query_sha256": _digest(query),
            "order_sha256": order_sha256,
            "after_memory_id": items[-1]["memory_id"],
            "before_cursor": selected_through,
        })
    else:
        next_cursor = None

    if matched_count == 0:
        status = "complete"
        empty_reason = "no_matches"
        error_type = ""
    elif not items:
        status = "budget_exhausted"
        empty_reason = "budget_exhausted"
        error_type = "memory_retrieval_budget_exhausted"
    elif current_omissions or truncated_count:
        status = "partial"
        empty_reason = ""
        error_type = ""
    else:
        status = "complete"
        empty_reason = "cursor_exhausted" if before_cursor and not items else ""
        error_type = ""

    result = {
        "schema_version": SCHEMA_VERSION,
        "tokenizer_version": TOKENIZER_VERSION,
        "token_estimator_id": TOKEN_ESTIMATOR_ID,
        "query": query,
        "query_terms": query_terms,
        "workspace_id": workspace_id,
        "status": status,
        "items": items,
        "excluded": excluded,
        "matched_count": matched_count,
        "selected_count": len(items),
        "truncated_count": truncated_count,
        "excerpt_bytes": sum(item["excerpt_bytes"] for item in items),
        "estimated_tokens": sum(item["estimated_tokens"] for item in items),
        "omissions": omissions,
        "next_cursor": next_cursor,
    }
    if empty_reason:
        result["empty_reason"] = empty_reason
    if error_type:
        result["error_type"] = error_type
    return result
