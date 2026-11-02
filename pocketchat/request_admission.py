"""Local request admission and its counted request facts.

This module is intentionally provider-free. It answers whether one already-built
payload fits local hard limits and records how the estimate was made. A later
caller can refuse to send an oversized request; this module never sends one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .provider import estimate_tokens
from .request import canonical_bytes

ESTIMATOR_ID = "chars4.v1"


class AdmissionError(ValueError):
    """A request cannot be admitted under the configured local limits."""

    def __init__(self, message: str,
                 error_type: str = "request_budget_exceeded") -> None:
        super().__init__(message)
        self.error_type = error_type


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AdmissionError(
            f"{name} must be a positive integer",
            error_type="invalid_admission_limit")
    return value


def _message_chars(messages: Any) -> int:
    encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    return len(encoded)


def prepare_admission(payload: Any, *, max_payload_bytes: int,
                      max_context_chars: int,
                      max_input_tokens: int | None = None,
                      estimator_id: str = ESTIMATOR_ID) -> dict:
    """Count one complete payload and refuse limits it cannot meet.

    ``max_context_chars`` is measured on the serialized messages, including
    tool calls and arguments. ``max_payload_bytes`` is measured on the complete
    canonical request, so tools and envelope fields are counted too. The token
    value is always labelled as an estimate and is never provider usage.
    """
    if not isinstance(payload, dict):
        raise AdmissionError(
            "request payload must be an object",
            error_type="invalid_request_payload")
    max_payload_bytes = _positive_int(max_payload_bytes, "max_payload_bytes")
    max_context_chars = _positive_int(max_context_chars, "max_context_chars")
    if max_input_tokens is not None:
        max_input_tokens = _positive_int(max_input_tokens, "max_input_tokens")
    if estimator_id != ESTIMATOR_ID:
        raise AdmissionError(
            f"unsupported token estimator: {estimator_id}",
            error_type="unsupported_estimator")

    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise AdmissionError(
            "request messages must be a list",
            error_type="invalid_request_payload")
    try:
        encoded = canonical_bytes(payload)
        message_chars = _message_chars(messages)
        tools = payload.get("tools")
        tool_schema_bytes = (
            len(canonical_bytes(tools)) if tools is not None else 0)
        estimated = estimate_tokens(messages)
        if tools is not None:
            estimated += estimate_tokens([{
                "role": "system",
                "content": json.dumps(
                    tools, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":")),
            }])
    except (TypeError, ValueError) as exc:
        raise AdmissionError(
            f"request payload cannot be serialized: {exc}",
            error_type="invalid_request_payload")

    counts = {
        "payload_bytes": len(encoded),
        "message_chars": message_chars,
        "tool_schema_bytes": tool_schema_bytes,
        "estimated_input_tokens": estimated,
    }
    reasons = []
    if counts["payload_bytes"] > max_payload_bytes:
        reasons.append(
            f"payload_bytes {counts['payload_bytes']} > {max_payload_bytes}")
    if counts["message_chars"] > max_context_chars:
        reasons.append(
            f"message_chars {counts['message_chars']} > {max_context_chars}")
    if max_input_tokens is not None and estimated > max_input_tokens:
        reasons.append(f"estimated_input_tokens {estimated} > {max_input_tokens}")
    if reasons:
        raise AdmissionError(
            "request exceeds local budget: " + "; ".join(reasons))

    return {
        "admitted": True,
        "payload_sha256": hashlib.sha256(encoded).hexdigest(),
        "payload_bytes": counts["payload_bytes"],
        "message_chars": counts["message_chars"],
        "tool_schema_bytes": counts["tool_schema_bytes"],
        "estimated_input_tokens": counts["estimated_input_tokens"],
        "estimator_id": estimator_id,
    }
