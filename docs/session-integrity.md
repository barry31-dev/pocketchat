# Session integrity audit

`pocketchat.session_integrity.audit_session_records(records, *, max_findings=64,
max_omissions=64, max_bytes=65536)` audits records that the caller has already
read. It does not open the session file, repair or rewrite a line, execute a
command, contact a provider, or return message bodies.

The report is closed and uses schema `pocketchat-session-integrity.v1` with
`schema_version=1`. Its status is:

| Status | Meaning |
|---|---|
| `complete` | The supplied records are internally consistent and not truncated. |
| `partial` | The supplied records are consistent, but an explicit read coverage is partial or the bounded audit omitted records/evidence. |
| `refused` | The header, schema, message shape, turn sequence or tool context is malformed, unsafe, or exceeds a hard contract. |

A `complete` result proves only that this in-memory record stream satisfies the
audit contract. It does not prove that a file was read in full, that a provider
executed the conversation, or that a different session is valid.

## Input

`records` is an ordered list. The first record must be a header with a non-empty
`session` identity and, when present, valid `trace`, `created` and
`pocketchat_version` strings. A schema-less header is accepted as legacy. When
`schema_version` is present it must be `"1"`; an explicit null or another value
is refused. Optional conversation/branch/parent identities, when present, must
be non-empty strings. Unknown header keys are not copied into the report.

Each following record must be an object with role `user` or `assistant` and a
string `content`. Modern records may carry `turn_id`; legacy records without it
receive the existing reader's ordinal `turn_000001` naming. Supplied turn ids
must match `turn_` followed by six digits, be unique, increase strictly, and
follow the ordinal sequence. Tool contexts may only appear on an assistant
message and use the provider shape accepted by the existing session reader:
one or more assistant `tool_calls` with unique function-call ids, followed by
exactly one matching `tool` result per call. A later `tool_calls: []` is not a
terminal state; every call must be paired with its result. Message and tool
bodies are never copied into findings.

An input record may carry `_audit_read_coverage="partial"` to say that its
caller inspected only part of that record's source. That is a `partial`
outcome, not a malformed record. Any other value is refused. The field is an
input observation only and is not written back.

## Bounds and report

The defaults are 64 findings, 64 omissions and a 64 KiB canonical UTF-8 report.
Limits must be positive integers and booleans are rejected. At most 128 records
including the header are inspected; further records produce an explicit
integer `record_limit` omission and a `partial` status. Unexpected failures are
reported with fixed codes and record/item indexes, including a bounded
`evidence_omitted` count when the finding budget is exceeded. If even the
minimal report cannot fit `max_bytes`, the function raises `ValueError` instead
of returning a misleading `complete` report. It never mutates its input.

## Non-goals

This slice adds no CLI, real session-file reader, repair/copy, writer change,
runtime integration, provider call, tag, Release or publication. A real reader
and CLI must each remain a separate bounded slice.
