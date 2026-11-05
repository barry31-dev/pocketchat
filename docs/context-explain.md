# Context explain

`pocketchat.context_explain` is a pure, bounded explanation surface for one
existing layered context projection. It observes the exact source payload and
`project_context` result supplied by the caller; it does not read a file,
recompute selection, rewrite the projection, or call a model/provider.

## API

```python
explain_context_projection(
    projection,
    *,
    max_findings=64,
    max_omissions=64,
    max_bytes=65536,
)
```

`projection` is a closed `pocketchat-context-explain-input.v1` object:

| key | meaning |
|---|---|
| `source` | The exact `project_context` source payload with `instructions`, `task_state`, `messages`, `evidence` and `pending_messages`. |
| `selection` | The exact `max_chars` and `keep_recent_turns` values used for the projection. |
| `result` | The existing `{messages, manifest}` result whose manifest is `pocketchat-context-projection.v1`. |
| `sources` | One closed descriptor per source entry: `{source_id, layer, locator}`. |

Each locator is `{kind, index}`. `kind` is `instruction`, `task_state`,
`message`, `pending_message` or `evidence`; `layer` is `fixed`, `recent`,
`task`, `memory` or `tool`, with evidence restricted to `memory` or `tool`.
Source ids must be unique and every source entry must have exactly one
descriptor.

Before explaining anything, the core verifies `source_sha256` against the
canonical source payload and `projection_sha256` against the exact projected
messages and manifest. A mismatch is a refusal, not a best-effort explanation.

## Report

The result is closed `pocketchat-context-explain.v1` data:

| key | meaning |
|---|---|
| `status` | `complete`, `partial` or `refused`. |
| `layers` | Ordered summaries for `fixed`, `recent`, `task`, `memory` and `tool`. |
| `observations` | Ordered selected/omitted source observations. |
| `findings` | Typed contradictions, sensitive-source markers or refusal reasons. |
| `omissions` | Explicit missing metadata or omitted-source records. |
| `budget` | Configured limits and observed finding/omission/payload bytes. |

An observation contains only `source_id`, `layer`, `record_kind`,
`record_index`, `selected`, `reason_code` and `selected_bytes`. It never
contains request, message, session, credential, memory or omitted content
bodies. Sensitive source entries produce a `sensitive_source` finding and
remain `partial`; their content is not copied into the report.

Stable reason codes include `selected`, `outside_recent_window`,
`unrecorded_omission`, `duplicate_source`, `over_budget`,
`sidecar_metadata_unavailable`, `source_digest_mismatch`,
`projection_digest_mismatch`, `invalid_input_shape`, `unknown_input_schema`,
`unknown_projection_schema`, `missing_source_descriptor`,
`unexpected_source_descriptor`, `layer_mismatch`, `finding_limit_exceeded`,
`omission_limit_exceeded` and `report_limit_exceeded`.

## Status and limits

- `complete` means every source entry was observed without a finding or
  omission.
- `partial` means the projection is usable but a named source was sensitive,
  omitted, or the sidecar did not retain enough metadata to explain it.
- `refused` means the input, digest binding, schema or limit contract is not
  trustworthy. Refusals use a bounded envelope and never silently truncate.
- `max_findings`, `max_omissions` and `max_bytes` reject overflow. The report
  does not drop the oldest finding or omission to fit.

The function preserves its input and performs no filesystem, command, socket,
provider or model call. This slice does not add a CLI, reader integration,
repair, migration, projection rewrite, tag, Release or package upload.

## Verification

`tests/test_context_explain.py` covers all five layers, digest binding,
descriptor closure, sensitive redaction, limit refusal, missing sidecar
metadata, empty projections, stable output, input immutability and zero I/O.
