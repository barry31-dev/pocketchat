"""The one HTTP call: OpenAI-compatible, standard library only, provider neutral.

This module is the only place that knows a provider exists. It takes a
conversation and a config, POSTs once, and hands back either a completion or a
structured failure. It does not retry, does not fall back to another model, and
does not silently reshape the response: a failed request stops the turn, which
is the same fail-closed shape the sibling packages use.

Two things it deliberately refuses to do, both because they turn a trace from
evidence into guesswork:

- It never puts the credential in a returned string, an error message or a
  trace payload. The key is read once, used once, and dropped.
- It never reports a success without usage numbers it actually received. A
  provider that omits ``usage`` yields zeros, not an estimate, so a reader can
  tell "reported nothing" from "cost nothing".
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .request import canonical_bytes

DEFAULT_CHAT_PATH = "/chat/completions"
MAX_ERROR_BODY_CHARS = 2000

REDACTED = "[redacted]"

# Rough character-per-token ratio used only for the local context budget. It is
# an estimate, and it is labelled as one everywhere it surfaces: the provider's
# own usage numbers are what a trace records as fact.
CHARS_PER_TOKEN_ESTIMATE = 4

_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
_KEY_PATTERN = re.compile(
    r'(?i)("?(?:api[-_]?key|authorization)"?\s*[:=]\s*")[^"]*(")')


class ProviderError(Exception):
    """A request that could not be completed.

    ``error_type`` is the stable error class the trace records. ``retryable``
    exists so a future slice can tell a timeout from a malformed request
    without re-deriving it from the message text.
    """

    def __init__(self, error_type: str, message: str, status_code: Optional[int] = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_trace_error(self) -> Dict[str, str]:
        return {"type": self.error_type, "message": self.message}


def estimate_tokens(messages: List[Dict[str, str]]) -> int:
    """A local token estimate for the context budget.

    The provider's real usage is recorded when it is reported; this number is
    only for deciding whether to truncate before sending, so it must be cheap
    and must never be written into a trace as though it were usage.
    """
    total = 0
    for message in messages:
        content = message.get("content") or ""
        total += len(content) // CHARS_PER_TOKEN_ESTIMATE + 4
    return total


def build_payload(messages: List[Dict[str, str]],
                  config: Dict[str, Any]) -> Dict[str, Any]:
    """The request body, in one place.

    ``complete()`` sends exactly this, and the request snapshot records exactly
    this, so the snapshot cannot describe a body the provider never sent. A
    second, near-identical construction inside ``complete()`` would make the
    snapshot evidence for a request that did not happen, which is the failure
    this package is built to avoid.

    ``stream`` is pinned false: streaming is not implemented, and a snapshot
    claiming it was requested would be a lie about what the provider was asked
    for.
    """
    return {
        "model": config["model"],
        "messages": messages,
        "stream": False,
    }


def _completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith(DEFAULT_CHAT_PATH):
        return base
    return base + DEFAULT_CHAT_PATH


def _clip(text: str, limit: int = MAX_ERROR_BODY_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "... [clipped]"


def _sanitize_body(text: str, credential: Optional[str] = None) -> str:
    """Strip a credential out of a provider error body before it is recorded.

    A 401 body is the one place a provider echoes what it rejected, and it
    does so without the operator choosing to put it there. The value must not
    reach a trace, a session file or stdout, so the credential is not
    recoverable from the run's own output.

    The match is against the literal credential this request sent, not against
    a list of shapes a provider might use. A pattern list can only know the
    shapes its author imagined, and the shapes that actually arrive are wider
    than any list: a bare key after a colon, one nested under an unrelated
    field, one with a suffix appended, one repeated twice. The same literal
    test the request snapshot uses catches all of them, and it fails closed:
    an unrecognised shape is redacted because it contains the credential, not
    because it looked like something.

    The text around the credential survives. Redacting the whole body would
    leave a trace saying a request failed without saying why, and "why" is the
    part the operator needs. So only the credential itself is replaced, and
    the provider's own wording stays readable.
    """
    cleaned = text
    if credential:
        # Every occurrence, not only the first: a body that repeats the key
        # would otherwise keep the second copy intact.
        cleaned = cleaned.replace(credential, REDACTED)
    # The two patterns stay, because a body can echo a credential this run did
    # not send -- a key from another profile, or one the operator typed into a
    # support ticket. Those are worth redacting where the pattern is
    # unambiguous, and they are not the protection: the protection is the
    # literal match above.
    cleaned = _BEARER_PATTERN.sub("Bearer %s" % REDACTED, cleaned)
    cleaned = _KEY_PATTERN.sub(r"\1%s\2" % REDACTED, cleaned)
    return cleaned


def complete(messages: List[Dict[str, str]], config: Dict[str, Any],
             api_key: str, timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
    """POST one chat completion and return a normalized response.

    The returned dict always carries ``content`` (a string, empty when the
    provider returned none) and ``usage`` with the integer counters. A provider
    that reports no usage yields zeros, and those zeros are recorded as zeros
    rather than omitted: "not reported" and "nothing spent" are different facts.

    Raises :class:`ProviderError` on anything that is not a 2xx carrying a
    readable completion. The caller turns that into a trace event and stops the
    turn; nothing here retries.
    """
    if not messages:
        raise ProviderError("empty_context", "no messages to send")

    timeout = timeout_seconds or int(config.get("timeout_seconds") or 60)
    # The body comes from the one builder, so a snapshot of this payload is a
    # snapshot of this request.
    body = canonical_bytes(build_payload(messages, config))

    request = urllib.request.Request(
        _completions_url(config["base_url"]),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status_code = int(getattr(response, "status", 0) or 0)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc, api_key)
    except socket.timeout:
        raise ProviderError(
            "timeout", "request timed out after %ss" % timeout, retryable=True
        )
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        # urllib wraps some socket failures, including connect timeouts, in
        # URLError. Inspect the reason before calling every wrapped failure a
        # connection error: a timeout is a distinct outcome in the trace.
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ProviderError(
                "timeout", "request timed out after %ss" % timeout, retryable=True
            )
        raise ProviderError(
            "connection_error", "could not reach provider: %s" % reason, retryable=True
        )
    except OSError as exc:
        raise ProviderError("io_error", "request failed: %s" % exc)

    return _parse_response(raw, status_code, config, api_key)


def _http_error(exc: urllib.error.HTTPError,
                credential: Optional[str] = None) -> ProviderError:
    status = int(getattr(exc, "code", 0) or 0)
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    detail = _sanitize_body(_clip(body), credential)
    message = "provider returned HTTP %d" % status
    if detail:
        message = "%s: %s" % (message, detail)
    error_type = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        408: "timeout",
        413: "payload_too_large",
        422: "unprocessable",
        429: "rate_limited",
    }.get(status, "http_error")
    return ProviderError(error_type, message, status_code=status, retryable=status >= 500)


def _parse_response(raw: bytes, status_code: int, config: Dict[str, Any],
                    credential: Optional[str] = None) -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(
            "malformed_response",
            "HTTP %d body was not JSON (%s)"
            % (status_code, type(exc).__name__),
        )
    if not isinstance(payload, dict):
        raise ProviderError(
            "malformed_response", "HTTP %d body was not a JSON object" % status_code
        )

    if payload.get("error") is not None:
        detail = payload["error"]
        if isinstance(detail, dict):
            detail = detail.get("message") or json.dumps(detail, sort_keys=True)
        raise ProviderError(
            "provider_error",
            "provider reported an error: %s" % _sanitize_body(
                _clip(str(detail)), credential),
            status_code=status_code,
        )

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(
            "malformed_response",
            "HTTP %d response carried no choices" % status_code,
            status_code=status_code,
        )
    first = choices[0]
    if not isinstance(first, dict):
        raise ProviderError(
            "malformed_response", "HTTP %d choice was not an object" % status_code,
            status_code=status_code,
        )

    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderError(
            "malformed_response", "HTTP %d choice carried no message" % status_code,
            status_code=status_code,
        )
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ProviderError(
            "malformed_response",
            "HTTP %d message content was not a string" % status_code,
            status_code=status_code,
        )

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    def _count(key: str) -> int:
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    return {
        "content": content,
        "finish_reason": first.get("finish_reason"),
        "model": payload.get("model") or config["model"],
        "status_code": status_code,
        "usage": {
            "input_tokens": _count("prompt_tokens"),
            "output_tokens": _count("completion_tokens"),
            "cache_read_tokens": _count("prompt_cache_hit_tokens"),
            "cache_write_tokens": _count("prompt_cache_miss_tokens"),
            "reasoning_tokens": _count("reasoning_tokens"),
        },
    }
