# Session integrity audit

`pocketchat.session_integrity` is a pure audit over session records a caller
has already read. It does not open the session file, repair records, execute
commands, inspect the environment or call a provider. A `complete` report
means only that the supplied objects passed the checks below; it does not
prove that the original file still exists or that its run occurred.

## Interface

```python
from pocketchat.session_integrity import audit_session_records

report = audit_session_records(
    records,
    max_findings=64,
    max_omissions=64,
    max_bytes=65536,
)
```

`records` is the ordered list of JSON objects from a session file, including
the header. Inputs and nested tool context remain unchanged. The report uses
`schema_version=1` and the schema name
`pocketchat-session-integrity.v1`.

| Status | Meaning |
|---|---|
| `complete` | The available records were fully checked and every finding is present in the report. |
| `partial` | The audit ran, but bounded findings or omission entries were left out and counted explicitly. |
| `refused` | The container/header is not auditable, a limit is invalid, or the canonical report cannot fit `max_bytes`. |

A report over 64 KiB by default is refused rather than truncated and presented
as complete. `max_findings` and `max_omissions` are bounded to 4096;
`max_bytes` is bounded from 512 bytes to 1 MiB.

## Checks

The first record must be the session header. A legacy header without
`schema_version` remains accepted; schema `1` requires non-empty
`conversation_id` and `branch_id`. Session/run and optional workspace
association are compared without echoing identifiers. Parent branch and parent
turn must appear as a pair.

Message roles are `user` and `assistant`. Modern `turn_id` values use
`turn_NNNNNN`, are unique and increase monotonically. Legacy messages without
turn IDs remain accepted. Tool context follows the existing provider shape:
assistant call objects name one function, tool results name a pending call,
and every accepted call receives exactly one result. A result is the explicit
terminal evidence for its call; missing, duplicate, orphaned and unresolved
pairs are named findings.

Findings contain only fixed codes and positional metadata: record index,
message index, field and tool-context index. They never contain message,
request, tool argument/result, session or credential bodies. Input order
determines stable finding order. Empty input is a named refusal and is not
treated as the same condition as an invalid container or corrupt record.

## Non-goals

This slice adds no CLI, filesystem reader, repair/copy operation, writer
change, migration, provider call or publication. It does not infer missing
history from a clean report. A real file read, reader integration and exit-code
contract belong to separate slices.
