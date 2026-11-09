# Provider adapters (v1.0)

The provider boundary is intentionally small. A session asks one adapter for
the request payload and for the normalized completion; it never reaches around
that adapter to a provider-specific function.

## The contract

`pocketchat.provider.ProviderAdapter` defines:

- `build_payload(messages, config)` returns the exact JSON body to send.
- `complete(messages, config, api_key)` returns the normalized response and
  raises `ProviderError` on failure.

The default implementation is `OpenAICompatibleAdapter`, which sends a
non-streaming `POST` to an OpenAI-compatible `/chat/completions` endpoint using
the standard library `urllib`.

The normalized response must contain:

```json
{
  "content": "assistant text",
  "finish_reason": "stop",
  "model": "provider-model",
  "status_code": 200,
  "usage": {
    "input_tokens": 1,
    "output_tokens": 1,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "reasoning_tokens": 0
  }
}
```

Missing provider usage is represented by zeros and must never be estimated.
The additive `usage_reported` boolean says whether those zeros came from a
provider usage object (`true`) or are placeholders for missing usage (`false`).
The existing `usage` keys and their zero semantics are unchanged.
`content` may be an empty string. `status_code` is the HTTP status when one
exists.

Streamed responses use the same normalized shape. Text and reasoning deltas are
joined in arrival order. Tool-call deltas are accumulated by their `index`;
their `id`, `name` and argument string may arrive in separate events. A
tool call is returned only after its complete argument string decodes to a
JSON object. A malformed, re-identified or incomplete fragment fails the
request as `malformed_response`; it is never executed as a partial call.
A tool-only response is valid with empty `content` and `finish_reason` set to
`tool_calls`. A usage event may have `choices: []` and is merged into the same
result at any point after response content or a tool call has been observed;
a stream containing only usage and no response content or tool call is refused
rather than turned into an empty assistant reply.

## Session integration

A session reads `config["provider_adapter"]` when present; otherwise it uses
the default adapter. The adapter name is recorded in
`run.started.payload.config_summary.provider_adapter`, so a trace says which
protocol implementation handled the request.

The agent TUI treats each user turn as one session run. The session writer claim
is released at the end of that run; the next turn lazily acquires a fresh claim
while retaining the same in-memory conversation and cumulative token counters.
A restart still resumes from the session file. A persistence failure is shown
with the reply as a trace-only warning; it is not reported as a successful
session write.

The same adapter instance builds the request snapshot and performs the call.
This is the invariant that keeps a snapshot from describing a request that was
never sent.

## Error contract

Adapters raise `ProviderError` with a stable `error_type`, a human-readable
message, optional `status_code`, and `retryable`. The CLI records
`model.response.failed` and ends the run; PocketChat does not retry silently or
fallback to another profile. Failure records carry `attempt_index` and
`attempt_scope: "turn"` so the physical request is paired with its result;
those fields do not claim a retry policy.

## Compatibility

`v1.0.x` keeps the `ProviderAdapter` method names and normalized response keys
stable. Adding an optional response key is additive. Removing or renaming a
method or required key requires a new major version. No third-party adapter is
bundled in `v1.0.x`.
