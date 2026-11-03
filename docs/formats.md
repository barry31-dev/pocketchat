# File formats

This document describes every file `pocketchat` writes, the version key each
one carries, and the compatibility rules between versions. It matches the
code of this release; each section names the module that owns the format.

The rule that governs all of them: **nothing already written is ever
rewritten.** Session files, traces, request snapshots and comparison
manifests are created exclusively and only ever appended to; a migration
reads an old file and writes a new one, leaving the original in place.

## Session files — `sessions/<name>.jsonl`

Owner: `pocketchat.sessions`. Schema version: **1** (header key
`schema_version`, written since the v0.3 identity layer; files written
before it have no such key and are the legacy format).

A session file is JSONL: one header line, then one line per message.

Header (schema version 1):

| key | meaning |
|---|---|
| `session` | the run id that owns the conversation |
| `trace` | the trace path, relative to this file |
| `created` | when the session started |
| `pocketchat_version` | the version of the program that wrote it |
| `schema_version` | the file format's version (`"1"`) |
| `conversation_id` | the dialogue this file belongs to |
| `branch_id` | the path through that dialogue |
| `parent_branch_id` | present only on files that were branched |
| `parent_turn_id` | the parent turn this file continues from |

Message line: `role` (`user` or `assistant`), `content`, and `turn_id`
(`turn_NNNNNN`, derived from the line's position). A completed assistant line
may carry additive `tool_context`: the ordered provider messages from that
turn's tool loop, including assistant `tool_calls` and matching `role: tool`
results, before the final assistant `content`. New readers validate and expand
it when rebuilding model context; readers that consume only `role` and
`content` continue to see the same user/final-assistant exchange.

Compatibility:

- A file with no `schema_version` key is a legacy v0.1/v0.2 file. It reads
  unchanged; its conversation and branch ids read as `not recorded`, its
  turn ids come from the lines' positions, and it is never rewritten to
  gain keys it did not have.
- A file with an unknown `schema_version` was written by a newer pocketchat
  and is refused (`no such session` never covers this; the error names the
  version).
- A legacy file cannot be upgraded in place (append-only). To continue an
  old conversation under the identity model, copy it into a new session and
  keep the original; `branch` refuses files without a conversation id for
  the same reason.
- `tool_context` is optional and additive within schema 1. Files without it are
  never rewritten to gain it. A malformed or unpaired tool context is refused
  rather than partially sent to a provider.

## Traces — `traces/<run-id>.jsonl`

Owner: `pocketchat.trace`. Schema: Agent Trace Doctor `trace.schema.json`
v1 (shipped in `pocketchat/schemas/`). Event-only export (`export --format
trace`) copies the bytes unchanged; schema and cross-event validation stay
with `trace-doctor validate`.

For streamed responses, `time_to_first_token_ms` remains the first non-empty
reasoning or content delta. Additive `first_delta_kind`,
`time_to_first_reasoning_ms` and `time_to_first_content_ms` distinguish those
two delta kinds. They describe PocketChat's recorder, not a provider's
official TTFT definition, and are absent when the corresponding delta did not
arrive.

`retry.attempt` and `fallback.activated` are additive trace events. The first
records a planned physical request retry and its backoff; the second records
one-hop fallback activation after the primary budget is exhausted. Neither
event means recovery succeeded: the terminal `run.*` event remains the only
outcome.

## Request snapshots — `traces/requests/<run-id>.turn-N.json`

Owner: `pocketchat.request`. Schema version: **1**.

| key | meaning |
|---|---|
| `schema_version` | `"1"` |
| `run_id` | the run that sent the request |
| `profile` | the profile it went out under (may be empty) |
| `created` | when the request was sent |
| `request` | the body exactly as sent, no headers |
| `request_sha256` | SHA-256 of the canonical body bytes |

The digest is over the body alone (`canonical_bytes`: sorted keys, fixed
separators, trailing newline). A snapshot whose body contains the configured
credential is never written — the writer fails closed. Snapshots are created
exclusively and never rewritten; `replay --dry-run` recomputes the digest
and reports a mismatch rather than trusting the stored value.

## Context projection sidecars — `traces/requests/<run-id>.turn-N.projection.json`

Owner: `pocketchat.session` + `pocketchat.context_projection`. Schema version:
**`pocketchat-context-projection.v1`**. A sidecar exists only when layered
projection is enabled. It carries metadata, not a second copy of the request
body: the request snapshot remains the exact body sent.

The sidecar binds the request snapshot digest, selected message count, source
and projection SHA-256 values, evidence references, omission reasons and the
64 KiB sidecar limit. It is created exclusively beside request snapshots and
is never rewritten. If request snapshot or sidecar creation fails on the
projection path, no request is sent. A sidecar is evidence for the selection
that produced a request; it is not task acceptance or publication proof.

## Comparison manifests — `comparisons/<comparison-id>.json`

Owner: `pocketchat.compare`. Schema version: **1**.

Keys: `schema_version`, `comparison_id`, `created`, `prompt`,
`base_context` (`session` reference, `messages` count, `context_chars`),
`runs` — one entry per profile with `profile`, `run_id`, `trace`,
`request`, `status`, `duration_ms`, `usage`, `cost_usd`, `output_summary`,
`error`. The manifest is an index: it names where the evidence lives and
copies none of it. Created exclusively, never rewritten, so the measurement
it records cannot be revised.

## Recovery records — `.pocketchat/recovery/`

Owner: `pocketchat.recovery`. Checkpoint manifests use schema version
`pocketchat-checkpoint.v1`; rewind transactions use
`pocketchat-rewind.v1`. A checkpoint records the source session/trace, the
completed `turn_id`, message count and SHA-256 digest of that exact message
prefix, including additive `tool_context` when present. A rewind transaction records its checkpoint, generated target,
branch identity and `prepared`/`completed` state.

Checkpoint manifests are created once and never rewritten. A rewind
transaction is intentionally updated from `prepared` to `completed` after the
generated target verifies; this is the crash-recovery boundary. Neither file
rewrites the source session or trace. Unknown schema versions are refused.

A Task Run checkpoint adds `task_id`, `step_id` and `policy_sha256` to the same
manifest. The three fields are all present or all absent: legacy checkpoints
keep their old checkpoint/rewind semantics, while `task resume` refuses legacy
or partial bindings. A task checkpoint is only written after the latest task
step ended `completed` or `refused`, and the checkpoint step itself counts
against `max_steps`. Resume validates policy digest, task record state/order,
checkpoint identity and tool context before the first new byte; a prepared
rewind transaction is recovered rather than duplicated.

## Task policy — operator-supplied JSON

Owner: `pocketchat.task_policy`. Schema version: **`pocketchat-task-policy.v1`**
(`schemas/task.schema.json`, `$defs.policy`).

A task policy is the document that authorizes a bounded task run, so it is read
strictly: an unknown field at any level is refused rather than ignored. A policy
that silently accepted a field nobody declared would read as a narrower grant
than it actually was.

| key | meaning |
|---|---|
| `schema_version` | `pocketchat-task-policy.v1` |
| `objective` | the one sentence the task tries to make true |
| `workspace_root` | directory inside the workspace the task works in; `.` is the workspace |
| `max_steps` | hard ceiling on recorded steps (1-64); reaching it stops the task |
| `max_tool_calls` | tool calls across the task (1-64), including ones that fail |
| `max_context_chars` | character budget handed to the existing compaction |
| `max_turn_tokens` | per-turn token budget enforced by the existing guardrail |
| `max_session_tokens` | optional whole-task budget; must be at least `max_turn_tokens` |
| `tools` | grants for `read_file`, `write_file` (create-only) and `memory_search` |
| `delegate` | optional finite serial delegation, `enabled` false by default |

Allowlisted paths are workspace-relative. An absolute path, an empty segment, a
`.` segment and any `..` are refused rather than normalized away: the grant is
compared against what the tool layer resolves, and a path that only becomes safe
after rewriting is a path nobody read. `write_file` cannot be widened to
overwrite. Only these three tools exist; a tool the policy names but the package
does not implement is a refusal, not a no-op.

## Task records — `.pocketchat/tasks/<task-id>.json`

Owner: `pocketchat.tasking`. Schema version: **`pocketchat-task.v1`**
(`schemas/task.schema.json`, `$defs.task_record`).

One record per task, holding what no other artifact owns: how many steps the
task took, which state it is in, and why it stopped. It is created exclusively
and appended to through an atomic replace, so a crash leaves either the old
record or the new one and never half of either. Readers validate the closed
record and step shapes before reporting: the file's `task_id` must match its
name, step indexes must be contiguous and within the declared budgets, and a
terminal state must carry a non-empty `terminal_reason` while an active state
must not carry one.

| key | meaning |
|---|---|
| `schema_version` | `pocketchat-task.v1` |
| `task_id` | `task_<hex>`; also the record's filename stem |
| `created` | RFC 3339 UTC |
| `objective` | copied from the policy |
| `policy_sha256` | digest of the canonical policy document |
| `state` | one of the states below |
| `max_steps` / `max_tool_calls` | the bounds the policy declared |
| `workspace_root` | the root the task worked in |
| `terminal_reason` | present only when the record says one; never inferred |
| `steps` | appended list of `index`, `kind`, `state`, optional `tool`, `detail`, `run_id`, `checkpoint_id`, `recorded` |

States and the closed transition table:

```text
created      --start-->      running
running      --checkpoint--> checkpointed --resume--> running
running      --interrupt-->  interrupted  --resume--> running
running      --complete-->   completed
running      --fail------>   failed
running|checkpointed|interrupted --cancel--> cancelled
```

`created`, `running`, `checkpointed` and `interrupted` are active; `completed`,
`failed` and `cancelled` are terminal and cannot restart. An event the table
does not name is refused rather than ignored, and a terminal transition requires
a recorded reason.

The record is an index, not a copy: a step names the `run_id` whose trace holds
the model and tool evidence, and names the `checkpoint_id` whose manifest holds
the recovery boundary. It duplicates no trace fact, so it cannot disagree with
one. `policy_sha256` is what makes a resume honest — a resume under a policy
that digests differently is a different task and is refused.
The checkpoint's `task_id` is likewise checked against the task named by the
resume command before any recovery target is written.

## Selection records — `comparisons/<comparison-id>.selection.json`

Owner: `pocketchat.selection`. Schema version: **1**.

Keys: `schema_version`, `comparison_id`, `run_id`, `profile`,
`selected_at`, and optional `note`. One decision per comparison,
exclusive-create: the manifest is never rewritten and a second `select` is
refused. `--into` writes its exchange into a session file (see above); the
selection record is what ties that session back to the comparison.

## Index — `index.sqlite`

Owner: `pocketchat.indexing`. Schema version: **1** (in the `meta` table).

A derived index over the evidence files: one row per conversation branch,
comparison, comparison run and reported problem, plus the sha256 of every
source file. It holds no fact of its own and no command reads it — delete
it and every answer is unchanged. Rebuilding is always whole (no
incremental update), and an index whose recorded digests disagree with the
workspace reports `stale` and exits non-zero. Two builds over the same
bytes are byte-identical on the SQLite this release was verified with (the
index carries no clock); across SQLite versions only logical equivalence —
the same rows — is promised.

## CLI output records

`workspace`, `history`, `search`, `diff` and `index --json` print JSON
records whose `schema_version` is `1` where they carry one (the workspace
graph and the query records do; the index report does not). Paths inside
them are relative to the workspace and no record carries a clock except
where a file itself records one.

## Compatibility policy

The stable v1.0 compatibility promise, including runtime matrix and deprecation,
is in [compatibility.md](compatibility.md). A format version listed here is
stable within v1.0.x; adding fields is additive, while changing an existing
field's meaning requires a new major version.

## Migration rules

- Old files read unchanged and are never backfilled.
- Migration to a new format means writing a new file beside the old one,
  which stays untouched.
- A reader that meets a version it does not understand refuses with the
  version named, rather than guessing at fields it has never seen.

## Unreleased: acceptance plan and historical receipt

`pocketchat-acceptance-plan.v1` is a separate closed operator input, not an
extension of `pocketchat-task-policy.v1`. `pocketchat-acceptance.v1` receipts are
exclusively appended in `.pocketchat/acceptance/<task-id>/<acceptance-id>.json`.
They bind canonical task-record/policy/plan identity, recorded time, execution
state and per-check file bytes/SHA-256. Task/step/checkpoint/trace v1 formats
remain unchanged. `schemas/acceptance.schema.json` declares the shapes; runtime
also checks unique ids, hashes, state/reason compatibility and aggregate state.
The receipt embeds the operator plan but public JSON omits it and artifact
bodies. Full field semantics, bounds, historical reporting and unsupported
platform behavior are in [task-acceptance.md](task-acceptance.md).
