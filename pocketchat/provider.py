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

import copy
from http.client import HTTPException, IncompleteRead
import json
import re
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, cast

from .request import canonical_bytes
from .tool_contracts import (contract_names, contract_summary, provider_tool,
                             file_work_contract_summary)

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


class ProviderAdapter:
    """One provider boundary: request shape in, normalized response out.

    The adapter owns both halves that must agree: the payload recorded in a
    request snapshot and the payload actually sent. A session receives one
    adapter and never reaches around it to a provider-specific function.
    Implementations must return the normalized response documented by
    :func:`complete` and raise :class:`ProviderError` on failure.
    """

    name = "provider"

    def build_payload(self, messages: List[Dict[str, str]],
                      config: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def complete(self, messages: List[Dict[str, str]], config: Dict[str, Any],
                 api_key: str, on_delta=None, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError


class OpenAICompatibleAdapter(ProviderAdapter):
    """The stable first-party adapter: OpenAI-compatible chat completions."""

    name = "openai-compatible"

    def build_payload(self, messages: List[Dict[str, str]],
                      config: Dict[str, Any]) -> Dict[str, Any]:
        return build_payload(messages, config)

    def complete(self, messages: List[Dict[str, str]], config: Dict[str, Any],
                 api_key: str, on_delta=None, **kwargs: Any) -> Dict[str, Any]:
        prepared_payload = kwargs.get("prepared_payload")
        if on_delta is not None and prepared_payload is not None:
            prepared_payload = dict(prepared_payload)
            prepared_payload["stream"] = True
        return complete(messages, config, api_key, on_delta=on_delta,
                        prepared_payload=prepared_payload)


DEFAULT_PROVIDER_ADAPTER = OpenAICompatibleAdapter()


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


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """A local token estimate for the context budget.

    The provider's real usage is recorded when it is reported; this number is
    only for deciding whether to truncate before sending, so it must be cheap
    and must never be written into a trace as though it were usage.
    """
    total = 0
    for message in messages:
        content = message.get("content") or ""
        metadata = 0
        for key in ("tool_calls", "name", "tool_call_id"):
            value = message.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                metadata += len(value)
            else:
                metadata += len(json.dumps(value, sort_keys=True,
                                           ensure_ascii=False,
                                           separators=(",", ":")))
        total += (len(content) + metadata) // CHARS_PER_TOKEN_ESTIMATE + 4
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
    payload = {
        "model": config["model"],
        "messages": messages,
        "stream": False,
    }
    # OpenAI-compatible endpoints do not agree on this extension. Send it only
    # when the operator's profile explicitly opts in; the agent records the
    # setting regardless, so an unsupported endpoint still leaves evidence.
    if (config.get("reasoning_effort_supported") is True
            and config.get("reasoning_effort")):
        payload["reasoning_effort"] = config["reasoning_effort"]
    tools = tool_offer(config)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    # Only send a generation cap when the profile says the endpoint accepts
    # this extension. A configured reserve without provider support is local
    # policy, not a claim that the response was capped.
    if (config.get("max_output_tokens_supported") is True
            and config.get("output_reserve_tokens")):
        payload["max_tokens"] = int(config["output_reserve_tokens"])
    return payload


def tool_offer(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "file_work_tools" in config:
        return [provider_tool(name) for name in config["file_work_tools"]
                if name in ("read_file", "propose_file_change")]
    enabled = {
        "read_file": config.get("tool_read_enabled") is True,
        "write_file": config.get("tool_write_enabled") is True,
        "memory_search": config.get("tool_memory_search_enabled") is True,
    }
    return [provider_tool(name) for name in contract_names()
            if enabled[name]]


def tool_offer_summary(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if "file_work_tools" in config:
        return file_work_contract_summary([
            name for name in config["file_work_tools"]
            if name in ("read_file", "propose_file_change")])
    return contract_summary({
        "read_file": config.get("tool_read_enabled") is True,
        "write_file": config.get("tool_write_enabled") is True,
        "memory_search": config.get("tool_memory_search_enabled") is True,
    })


def _parse_tool_calls(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProviderError(
            "malformed_response", "tool_calls was not an array")
    calls = []
    for item in value:
        if not isinstance(item, dict):
            raise ProviderError(
                "malformed_response", "tool call was not an object")
        call_id = item.get("id")
        function = item.get("function")
        if (not isinstance(call_id, str) or not call_id.strip()
                or not isinstance(function, dict)):
            raise ProviderError(
                "malformed_response", "tool call lacked id or function")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(name, str) or not name.strip():
            raise ProviderError(
                "malformed_response", "tool call lacked a function name")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                raise ProviderError(
                    "malformed_response",
                    "tool call arguments were not valid JSON: %s" % exc)
        if not isinstance(arguments, dict):
            raise ProviderError(
                "malformed_response", "tool call arguments were not an object")
        calls.append({"id": call_id, "name": name, "arguments": arguments})
    return calls


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
             api_key: str, timeout_seconds: Optional[int] = None,
             on_delta=None, prepared_payload: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
    """POST one chat completion and return a normalized response.

    The returned dict always carries ``content`` (a string, empty when the
    provider returned none) and ``usage`` with the integer counters. A provider
    that reports no usage yields zeros, and those zeros are recorded as zeros
    rather than omitted: "not reported" and "nothing spent" are different facts.

    Raises :class:`ProviderError` on anything that is not a 2xx carrying a
    readable completion. The caller turns that into a trace event and stops the
    turn; nothing here retries.
    """
    if not messages and prepared_payload is None:
        raise ProviderError("empty_context", "no messages to send")

    timeout = timeout_seconds or int(config.get("timeout_seconds") or 60)
    # A caller that already admitted a payload sends that exact body. The
    # legacy path still uses the one builder, so both paths snapshot what they
    # send rather than reconstructing a second body inside complete().
    payload = (copy.deepcopy(prepared_payload)
               if prepared_payload is not None
               else build_payload(messages, config))
    if on_delta is not None:
        payload["stream"] = True
    body = canonical_bytes(payload)

    request = urllib.request.Request(
        _completions_url(config["base_url"]),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
            "Accept": "text/event-stream" if on_delta is not None else "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 0) or 0)
            if on_delta is not None and "text/event-stream" in (
                    response.headers.get("Content-Type") or ""):
                return _stream_response(response, config["model"], status_code,
                                        on_delta=on_delta)
            try:
                raw = response.read()
            except HTTPException as exc:
                # Do not parse partial bytes or reflect peer-controlled framing.
                raise ProviderError(
                    "incomplete_response",
                    "HTTP %d response body was interrupted" % status_code,
                    status_code=status_code,
                    retryable=True,
                ) from exc
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
    except HTTPException as exc:
        # Includes RemoteDisconnected (also an OSError) before headers arrive.
        raise ProviderError(
            "connection_error", "provider HTTP exchange failed", retryable=True
        ) from exc
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
    retryable = status >= 500 or status in (408, 425, 429)
    return ProviderError(error_type, message, status_code=status, retryable=retryable)


def _usage_count(usage: Dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _stream_response(response, model: str, status_code: int,
                    on_delta=None) -> Dict[str, Any]:
    """Read one OpenAI-compatible SSE response into the normal response shape.

    A streamed response is still one request and one normalized result. The
    callback is only a display hook; it does not alter what the trace records.
    Tool calls arrive in fragments and are returned only after every fragment
    forms complete JSON arguments. Malformed events fail closed rather than
    being treated as empty content.
    """
    content: List[str] = []
    reasoning: List[str] = []
    finish_reason = ""
    usage_payload = {}
    usage_seen = False
    saw_event = False
    started = time.monotonic()
    first_token_ms = None
    first_delta_kind = None
    first_reasoning_ms = None
    first_content_ms = None
    tool_fragments: Dict[int, Dict[str, Any]] = {}
    saw_terminal_marker = False
    iterator = iter(response)
    while True:
        try:
            raw_line = next(iterator)
        except StopIteration:
            break
        except (IncompleteRead, OSError) as exc:
            if saw_terminal_marker or finish_reason:
                break
            raise ProviderError(
                "incomplete_stream",
                "HTTP %d stream read failed before a terminal marker"
                % status_code,
                status_code=status_code,
                retryable=True,
            ) from exc
        line = raw_line.decode("utf-8", "replace").strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            saw_terminal_marker = True
            break
        try:
            payload = json.loads(data)
        except ValueError:
            raise ProviderError(
                "malformed_response",
                "HTTP %d stream contained a non-JSON event" % status_code,
            )
        if not isinstance(payload, dict):
            raise ProviderError(
                "malformed_response",
                "HTTP %d stream event was not a JSON object" % status_code,
            )
        if payload.get("error") is not None:
            detail = payload["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail, sort_keys=True)
            raise ProviderError("provider_error", str(detail))
        if isinstance(payload.get("usage"), dict):
            usage_payload = payload["usage"]
            usage_seen = True
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        if isinstance(delta, dict):
            thought = delta.get("reasoning_content")
            if isinstance(thought, str) and thought:
                reasoning.append(thought)
                saw_event = True
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                    first_delta_kind = "reasoning"
                if first_reasoning_ms is None:
                    first_reasoning_ms = int((time.monotonic() - started) * 1000)
                if on_delta is not None:
                    on_delta("reasoning", thought)
            text = delta.get("content")
            if isinstance(text, str) and text:
                content.append(text)
                saw_event = True
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                    first_delta_kind = "content"
                if first_content_ms is None:
                    first_content_ms = int((time.monotonic() - started) * 1000)
                if on_delta is not None:
                    on_delta("content", text)
            _merge_tool_call_delta(tool_fragments, delta.get("tool_calls"),
                                   status_code)
            if isinstance(delta.get("tool_calls"), list) and delta["tool_calls"]:
                saw_event = True
        if isinstance(choice.get("finish_reason"), str) and choice["finish_reason"]:
            finish_reason = choice["finish_reason"]
    if not saw_terminal_marker and not finish_reason:
        raise ProviderError(
            "incomplete_stream",
            "HTTP %d stream ended without [DONE] or a terminal finish_reason"
            % status_code,
            status_code=status_code,
            retryable=True,
        )
    if not saw_event:
        raise ProviderError(
            "malformed_response",
            "HTTP %d stream contained no text, tool-call or usage event"
            % status_code,
        )
    result = {
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "finish_reason": finish_reason or (
            "tool_calls" if tool_fragments else "stop"),
        "model": model,
        "status_code": status_code,
        "time_to_first_token_ms": first_token_ms,
        "usage_reported": usage_seen,
        "usage": {
            "input_tokens": _usage_count(usage_payload, "prompt_tokens"),
            "output_tokens": _usage_count(usage_payload, "completion_tokens"),
            "cache_read_tokens": _usage_count(
                usage_payload, "prompt_cache_hit_tokens"),
            "cache_write_tokens": _usage_count(
                usage_payload, "prompt_cache_miss_tokens"),
            "reasoning_tokens": _usage_count(usage_payload, "reasoning_tokens"),
        },
    }
    tool_calls = _finalize_stream_tool_calls(tool_fragments)
    if tool_calls:
        result["tool_calls"] = tool_calls
    if first_delta_kind is not None:
        result["first_delta_kind"] = first_delta_kind
    if first_reasoning_ms is not None:
        result["time_to_first_reasoning_ms"] = first_reasoning_ms
    if first_content_ms is not None:
        result["time_to_first_content_ms"] = first_content_ms
    return result


def _merge_tool_call_delta(fragments: Dict[int, Dict[str, Any]],
                           value: Any, status_code: int) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise ProviderError(
            "malformed_response",
            "HTTP %d stream tool_calls was not an array" % status_code,
        )
    for item in value:
        if not isinstance(item, dict):
            raise ProviderError(
                "malformed_response",
                "HTTP %d stream tool call was not an object" % status_code,
            )
        index = item.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ProviderError(
                "malformed_response",
                "HTTP %d stream tool call lacked a valid index" % status_code,
            )
        fragment = fragments.setdefault(index, {
            "id": "", "name": "", "arguments": [],
        })
        call_id = item.get("id")
        if call_id is not None:
            if not isinstance(call_id, str) or not call_id.strip():
                raise ProviderError(
                    "malformed_response",
                    "HTTP %d stream tool call had an invalid id" % status_code,
                )
            if fragment["id"] and fragment["id"] != call_id:
                raise ProviderError(
                    "malformed_response",
                    "HTTP %d stream tool call changed id" % status_code,
                )
            fragment["id"] = call_id
        function = item.get("function")
        if function is None:
            continue
        if not isinstance(function, dict):
            raise ProviderError(
                "malformed_response",
                "HTTP %d stream tool function was not an object" % status_code,
            )
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip():
                raise ProviderError(
                    "malformed_response",
                    "HTTP %d stream tool call had an invalid name" % status_code,
                )
            if fragment["name"] and fragment["name"] != name:
                raise ProviderError(
                    "malformed_response",
                    "HTTP %d stream tool call changed name" % status_code,
                )
            fragment["name"] = name
        arguments = function.get("arguments")
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ProviderError(
                    "malformed_response",
                    "HTTP %d stream tool arguments were not a string"
                    % status_code,
                )
            fragment["arguments"].append(arguments)


def _finalize_stream_tool_calls(
        fragments: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not fragments:
        return []
    calls = []
    seen_ids = set()
    for index in sorted(fragments):
        fragment = fragments[index]
        if not fragment["id"] or not fragment["name"] or not fragment["arguments"]:
            raise ProviderError(
                "malformed_response",
                "HTTP stream tool call %d was incomplete" % index,
            )
        raw_arguments = "".join(fragment["arguments"])
        try:
            arguments = json.loads(raw_arguments)
        except ValueError as exc:
            raise ProviderError(
                "malformed_response",
                "stream tool call arguments were not valid JSON: %s" % exc,
            )
        if not isinstance(arguments, dict):
            raise ProviderError(
                "malformed_response",
                "stream tool call arguments were not an object",
            )
        if fragment["id"] in seen_ids:
            raise ProviderError(
                "malformed_response",
                "HTTP stream reused tool call id %s" % fragment["id"],
            )
        seen_ids.add(fragment["id"])
        calls.append({
            "id": fragment["id"],
            "name": fragment["name"],
            "arguments": arguments,
        })
    return calls


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

    raw_usage = payload.get("usage")
    usage_reported = isinstance(raw_usage, dict)
    usage: Dict[str, Any] = cast(Dict[str, Any], raw_usage) if usage_reported else {}

    def _count(key: str) -> int:
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return 0
        return value

    result = {
        "content": content,
        "finish_reason": first.get("finish_reason"),
        "model": payload.get("model") or config["model"],
        "status_code": status_code,
        "usage_reported": usage_reported,
        "usage": {
            "input_tokens": _count("prompt_tokens"),
            "output_tokens": _count("completion_tokens"),
            "cache_read_tokens": _count("prompt_cache_hit_tokens"),
            "cache_write_tokens": _count("prompt_cache_miss_tokens"),
            "reasoning_tokens": _count("reasoning_tokens"),
        },
    }
    tool_calls = _parse_tool_calls(message.get("tool_calls"))
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result
