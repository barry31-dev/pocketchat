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
(`turn_NNNNNN`, derived from the line's position).

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

## Traces — `traces/<run-id>.jsonl`

Owner: `pocketchat.trace`. Schema: Agent Trace Doctor `trace.schema.json`
v1 (shipped in `pocketchat/schemas/`). Event-only export (`export --format
trace`) copies the bytes unchanged; schema and cross-event validation stay
with `trace-doctor validate`.

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

## Comparison manifests — `comparisons/<comparison-id>.json`

Owner: `pocketchat.compare`. Schema version: **1**.

Keys: `schema_version`, `comparison_id`, `created`, `prompt`,
`base_context` (`session` reference, `messages` count, `context_chars`),
`runs` — one entry per profile with `profile`, `run_id`, `trace`,
`request`, `status`, `duration_ms`, `usage`, `cost_usd`, `output_summary`,
`error`. The manifest is an index: it names where the evidence lives and
copies none of it. Created exclusively, never rewritten, so the measurement
it records cannot be revised.

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

## Migration rules

- Old files read unchanged and are never backfilled.
- Migration to a new format means writing a new file beside the old one,
  which stays untouched.
- A reader that meets a version it does not understand refuses with the
  version named, rather than guessing at fields it has never seen.
