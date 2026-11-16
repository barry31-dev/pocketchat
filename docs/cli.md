# Command manual

The reference for the commands' options and behaviour: comparisons and their
manifests, request snapshots, replay, sessions and branches, the workspace
graph, the queries, the derived index, show and export, and the trace. File
formats and their compatibility rules are in [formats.md](formats.md); the
rules about credentials and the written evidence are in
[security.md](security.md). The README carries the overview and the
quickstart.

## Automation JSON

`init`, `run`, `chat`, `branch`, `checkpoint`, `rewind`, `show`, `context` and `export`
accept `--json`. Success
is one JSON object on stdout: execution commands report `ok`, `schema_version`,
`status`, `run_id`, `turns`, `reply` and a workspace-relative `trace`; `branch`
reports relationship ids and relative `source` / `session` / `trace` plus
`messages_written`, never message bodies. Preparation, execution and branch
refusals print one `{"ok":false,...}` object on stdout with stderr empty.
Preparation/refusal exits 2; a provider run that started and failed keeps its
existing exit 1. Without `--json`, existing human output is unchanged.

## Agent

`pocketchat agent` is the interactive chat shell with session-local switches.
A plain line is one run; the configured `--session` carries context into the
next run. Each run ends with exactly one terminal trace event.

```bash
pocketchat agent --session notes
```

Slash commands:

| Command | Effect |
|---|---|
| `/model NAME` | select a configured profile for the next run |
| `/session NAME` | select the session file for the next run |
| `/reasoning LEVEL` | set `minimal`, `low`, `medium` or `high` |
| `/context N` | set the character budget for the next run |
| `/status` | show the current profile, model, session, reasoning and budget |
| `/files` | show current exact grants and the pending change id |
| `/files read|write PATH...` | show provider disclosure and exact paths; activate only after the displayed `/confirm` |
| `/files revoke PATH...` | remove exact grants and invalidate a displayed confirmation |
| `/diff` / `/apply [CHANGE_ID]` | show the complete pending diff without applying it and issue a digest-bound confirmation |
| `/changes` | list persisted change metadata owned by this workspace, conversation and branch |
| `/undo CHANGE_ID` | require a fresh exact write grant, then show the persisted compensating diff and confirmation |
| `/confirm DIGEST` | execute only the exact currently displayed grant, apply or undo action |
| `/exit` / `/quit` / `/bye` | leave the agent |

File-control commands require both stdin and stdout to be TTYs. Piped input sees
the line shell banner but every file command is refused before a Session, trace or
file-change store is created. The model is offered only granted `read_file` and
`propose_file_change`; it cannot grant, confirm, apply, undo or use immediate
`write_file`. Apply success consumes the target's write grant, so undo needs a fresh
disclosed write grant. Profile/session/grant changes, a stale SessionFile view, an
in-flight reply or a foreign writer refuse confirmation. `/files cancel` revokes
grants and unconfirmed review without undoing applied files. Cancelled requests
retain a still-live worker/writer until exit. The [offline golden fixture](file-work-validation.md)
can run against an installed development wheel; real-provider/manual acceptance
and formal release remain separately evidenced.
See [file-work.md](file-work.md) for the transaction and evidence contract.

The reasoning level is recorded in `run.started` for every run. It is placed
in the request body only when the selected profile has
`reasoning_effort_supported: true`; otherwise the setting is local evidence and
is not assumed to be accepted by the endpoint.

## Doctor

```bash
pocketchat doctor
pocketchat doctor --profile hosted --json
```

`doctor` is a read-only startup check. It loads and validates the same
configuration a run uses, resolves the selected profile and reports whether
that profile's credential environment variable is set. It never prints the
credential value, opens a socket, creates a trace or session, or writes the
configuration. The run trace records the provider adapter name in
`run.started.payload.config_summary.provider_adapter`; the default is
`openai-compatible`.

A ready report exits 0. A missing or invalid configuration, an unknown profile
or a missing credential exits 2 and names the next action. `--json` returns one
object with `ok`, `schema_version`, the selected `profile`, `model`,
`api_key_env`, a stable `checks` list, a derived `check_status` mapping from
check name to status, and `next_actions` — the ordered `fix` strings of failed
checks. A ready report has an empty `next_actions` array. A ready report does
not claim that the provider endpoint is reachable.

`doctor --runtime --json` adds one bounded `runtime` field with schema version
`1`: interpreter and OS identity, explicit booleans for
`dir_fd_open`, `nofollow_open_flag` and `directory_open_flag`, and the fixed
`not_checked` names `provider_reachability`, `workspace_permissions` and
`concurrency_atomicity`. Missing no-follow support is a capability limit, not
a claim that memory or workspace operations are safe. The default report,
three existing checks, `ok` value and exit code are unchanged.

## Profiles

```bash
pocketchat profile list --json
pocketchat profile set local \
  --base-url http://127.0.0.1:8123/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_API_KEY
pocketchat profile use local
pocketchat profile remove staging
```

`profile set NAME` creates or replaces one complete profile. `--base-url`,
`--model` and `--api-key-env` are required; the timeout, context and summary
limits and the two optional prices may be supplied with their named options.
An omitted optional field resolves to its configuration default when the
file is loaded, so changing one field of an existing profile means passing
the complete profile again. The command accepts the name of the
credential's environment variable, never the credential value.

`--max-turn-tokens` and `--max-session-tokens` set token budgets for one
turn and one run. They are token counts, not currency limits. Requests are
checked by local estimate before sending and by provider-reported usage after
completion; over-budget turns do not enter the session, and missing usage
under a session budget fails closed.

`--tool-read` offers one automatic `read_file` tool to the model and
`--no-tool-read` withholds it. Every path
must be listed with repeated `--tool-read-allow`; `--tool-call-max-count`
limits tool calls per turn (default 2) and `--tool-result-max-bytes` bounds
each result (default 16384). Tool calls use the same workspace and sensitive
path guards as `tool read`. Disabled/unknown tools, invalid arguments and
guard refusals return bounded failure results so the model can correct within
the call budget. I/O failures, oversized results and exhausted budgets end the
run; no shell, execute or network tool is offered.

`--tool-write` adds a create-only automatic `write_file` tool. Each path must
be listed with repeated `--tool-write-allow`; the model supplies `path` and
`content` but no overwrite flag. Existing files, path escapes, sensitive or
binary paths and oversized content are refused.

`--tool-memory-search` offers a read-only automatic `memory_search` tool.
`--memory-recall` enables opt-in v2 recall projection and requires
`--memory-recall-store` plus `--memory-recall-workspace-id`; it is separate
from the legacy tool.
`--tool-memory-search-max-results` bounds returned records (default 20), and
the existing tool-result byte limit applies. The model cannot add, edit or
delete memory; `--no-tool-memory-search` withholds the tool.

`profile use NAME` changes `default_profile`. `profile remove NAME` removes a
non-default profile and refuses to remove the current default. All three
commands write only schema `"2"` configurations; a schema `"1"` file remains
read-only and is not migrated as a side effect. A candidate is validated as a
whole before an atomic replacement that preserves the file mode. A rejected
change leaves the file byte-for-byte unchanged.

The commands exit 0 after a write and exit 2 for an unusable configuration,
schema, name or candidate. After arguments parse, `--json` reports `ok`,
`action`, `profile` and `default_profile` on success; configuration and write
failures add stable `error`, `message` and `fix` fields.

## Compare

```bash
pocketchat compare "what is 2+2?" --profiles hosted,local
```

The output below came from the local stub used by the tests. It is evidence
for the command and artifact shape, not for any provider:

```
hosted       completed     0.0s     11      5  0.000021  stub reply for hosted-model
local        completed     0.0s     11      5  unknown  stub reply for local-model
manifest /private/tmp/pcrun/comparisons/cmp_20261018t232254_53de0c2d.json
```

On 2026-10-20 the same command completed one real comparison against a local
free aggregate route: both configured profiles in that call completed
(`cmp_20261020t021019_4ce71581`). Both traces passed
`trace-doctor validate`, a request snapshot hash recomputed identically, and
the credential had zero hits in the artifacts and stdout. This verifies only
those two route inputs on that call, not other models or providers.

```bash
export POCKETCHAT_HOSTED_KEY=...
export POCKETCHAT_API_KEY=...
pocketchat compare "what is 2+2?" --profiles hosted,local
```

One prompt, one run per profile, run in the order the profiles were named. Each
run is an ordinary run: its own run id, trace and request snapshot, and one
terminal event. What they share is the starting context, so a difference in the
replies is about the profiles.

The cost column is computed from the prices the profile configured, in the
same per-million units the config file uses. A profile that configured no
price shows `unknown`, because a price nobody set is not a price of zero -- a
table that printed `0.000000` there would report a cost that was never
measured. The duration is the wall time the run's model requests took, summed
over its turns and read back from the events the run itself recorded, so the
number in the table is one the trace can support.

The token columns contain only counters the provider reported; the program
does not estimate them. A provider that omits `usage` leaves those counters at
zero. That zero means "no usage numbers were reported", not proof that no tokens
were spent. The additive `usage_reported` field in the trace makes that
distinction explicit without changing the zero-valued counters. Cost is calculated only from the counters present, so `unknown`
means a price is missing, while a numeric cost is arithmetic over the counters
shown and is not a provider invoice.

`comparisons/<comparison-id>.json` is the index over those runs. It names the
trace and the request snapshot of each one, with its status, duration, usage,
summary and error, and every path in it is relative to the workspace, so it
resolves wherever the manifest is read. It never copies trace content into
itself: one request, one home. The file is created once and never rewritten,
like the snapshot beside it.

Its `base_context` records what the comparison started from: the session file
it was seeded with, when `--session` named one, and the message and character
counts of that context. The session path is relative to the workspace, like
every other path in the file, and the file is only read -- a comparison never
writes into the conversation it measured. Without `--session` the field is
`null`: no base session is a different fact from an unnamed one.

`--json` prints the comparison id, the manifest path and one entry per run
instead of the table. `--quiet` drops the table and keeps the manifest path,
because a comparison nobody is watching still needs its evidence to be
findable. `--session FILE` seeds the context from an existing conversation; it
is read, never written, and a comparison opens no session file of its own.

The exit code describes the command, not every profile. One profile failing is
a result the manifest carries, and the command still exits 0. What exits 2 is
a comparison that could not be made: an unknown profile name, a repeated one, a
profile whose credential is not set, or a `--session` that does not exist. All
of those are checked before the first request goes out, so a refused comparison
leaves no traces behind.

`compare` shares an option block with `run` and `chat`, so it still parses
`--profile` and `--trace` -- but neither applies here, and its help no longer
lists them. The profiles come from `--profiles`, and each run writes its own
trace beside it. Passing either one is refused with exit 2 rather than ignored:
a flag the parser accepted and nothing read would leave an operator believing
a choice landed when the comparison ran the profiles `--profiles` named. They
stay parsed rather than unrecognized so that the refusal names the conflict,
instead of a generic usage error that never says which command refused it.

`select` records which answer was kept. It writes its own record beside the
manifest -- the manifest itself is never rewritten -- and refuses a second
selection of the same comparison, because the first decision is evidence too:

```bash
pocketchat select <comparison-id> <run-id> --note "the fast one"
pocketchat select <comparison-id> local --into kept
```

The run may be named by id or, when that is unambiguous, by profile. `--into`
appends the selected prompt and reply to a session, starting one if absent,
so the conversation continues the ordinary way. The session is a run of its
own -- its own run id and trace -- and the selection file is what ties it
back to the comparison. The reply is written only when the run's trace
records it in full: a comparison that ran without `--verbose` carries only a
summary, and a summary written into a session would be read as the whole
answer, so that is refused before anything is written. `workspace` reports
the selection when one exists and still says `not recorded` when there is
none.

## Request snapshots

Every request is written to `traces/requests/<run-id>.turn-N.json` before the
socket opens, beside the trace it belongs to. The trace says a request happened;
the snapshot says what it contained:

```bash
cat traces/requests/<run-id>.turn-1.json
```

```json
{
  "schema_version": "1",
  "run_id": "run_20261018t000000_abcdef12",
  "profile": "local",
  "created": "2026-10-18T00:00:00.000Z",
  "request": {"model": "example-model", "messages": [...], "stream": false},
  "request_sha256": "64 lowercase hex characters"
}
```

The snapshot is the payload the provider was sent, produced by the same builder
the request itself uses, so it cannot describe a body that never went out. It
carries no header and no credential. `request_sha256` is the digest of those
exact bytes, and `model.request.started` carries both the path and the digest,
so a reader can recompute the hash from the file instead of trusting the trace.
The recorded path is relative to the trace, so it resolves wherever `--trace`
put the file.

A run makes one snapshot per turn, because each turn sends a different context.
A snapshot is created once and never rewritten: a resumed session continues the
numbering its trace already used rather than replacing a file that records a
request that really happened.

A snapshot whose body contains the configured credential is refused. The body
is the operator's own words, and a quoted key -- a paste, a `dotenv` line, a
support ticket -- would otherwise put the credential in the one artifact that
is kept and replayed. When that happens the request still goes out and the run
completes, but no snapshot is written and `model.request.started` carries no
`request_snapshot` reference; instead an `error.recorded` event with
`credential_in_request` says why. A key belonging to somebody else is not this
package's to police, and a prompt that merely mentions one is unaffected.

A provider that rejects a credential often echoes what it rejected in its error
body. Whatever form that echo takes -- after a colon, nested under an unrelated
field, with a suffix appended, or repeated -- the value is replaced before the
body reaches the trace, the comparison manifest or stdout, and the provider's
own wording around it survives so the error still says what went wrong.

## Replay

```bash
pocketchat replay <run-id> --dry-run
pocketchat replay traces/requests/<run-id>.turn-1.json --dry-run --json
```

Dry-run replay reads saved request snapshots and recomputes their SHA-256 with
the same canonical serializer used when they were written. A run id verifies
every turn in the default `traces/requests` layout; when `--trace` changed the
snapshot name, pass the explicit snapshot path instead. The command needs no
configuration or credential, and it does not open a socket.

A mismatch exits 2 and prints both the expected and actual digest. When an
adjacent projection sidecar exists, replay validates its request binding and
recall evidence counts without reading the current memory store. A missing or
malformed snapshot is also a usage error, because there is nothing trustworthy
to replay.

## Sessions

`--session notes` keeps a conversation in `sessions/notes.jsonl`, beside its
trace in `traces/`. The session file holds the full text of every exchange; the
trace holds what happened. A credential is in neither.

```bash
pocketchat run "what did we decide last time?" --session notes
pocketchat run "and about the retry policy?" --session notes
```

The second command continues the first: same run id, same trace file, the
earlier exchanges still in the context. A session is one run, not a run per
invocation, so the trace stays a single run that the validator can read.
When resuming, an explicit `--trace` must name that recorded trace; a different
path is refused before the run starts.

The session file carries its own identity. The header names the conversation
and the branch it belongs to, and every message line carries a `turn_id`
derived from its position in the file. A conversation therefore keeps one
identity across the runs that append to it, and two readers of the same bytes
name the same exchange. Files written before these fields existed read
unchanged and report `not recorded`; they are never rewritten to gain keys
they did not have. New session files also carry a `schema_version` naming
the file format itself, and a reader that meets a version it does not
understand refuses with the version named. Malformed UTF-8 in a session file
is a `session_file` refusal too: `show --json` exits 2 with one stable error
object, leaves the input bytes untouched, and never leaks a traceback. Every file this package writes —
session, trace, request snapshot, comparison manifest, selection record and
the derived index — is described, with its version key and compatibility
rules, in [`formats.md`](formats.md).

`branch` marks a second path through a conversation from one completed turn.
It writes a new session file that shares the parent's conversation id, copies
the turns up to the turn you name, and records `parent_branch_id` and
`parent_turn_id` so the child names its own origin. The parent file is not
touched:

```bash
pocketchat branch notes --from turn_000002 --name fork
```

Forking from a turn that was never answered is refused: a branch continues a
question the model already replied to, not one the parent asked and never sent.
Only v0.3 session files can be branched; a file with no conversation id is
refused rather than given one, because an invented link cannot be read back.

## Checkpoint and rewind

`checkpoint` records a completed turn as a recovery boundary. It writes a
small manifest under `.pocketchat/recovery/checkpoints/` containing the
session/trace references, turn id and SHA-256 digest of the exact message
prefix. Missing trace evidence, an unanswered turn or a changed boundary is
refused.

```bash
pocketchat checkpoint notes --at turn_000002 --id before-retry --json
pocketchat rewind before-retry --name notes-rewound --json
```

`rewind` creates a new session branch containing only the checkpointed
messages. The source session and trace are never rewritten. A prepared
transaction is recorded before the target is created and completed only after
the target verifies against the boundary digest; retrying after a process
crash finishes or replaces a partial generated target. Existing evidence is
never replayed automatically and no provider request is sent.

## Bounded task run

`task` exposes the v1.1 bounded task loop without becoming a second execution
kernel:

```bash
pocketchat task run "compare the inputs"   --policy examples/task-policy.json --session task --json
pocketchat task status <task-id> --json
pocketchat task resume <task-id> --checkpoint <checkpoint-id>   --policy examples/task-policy.json --name resumed --json
pocketchat task report <task-id> --policy examples/task-policy.json --json
```

`task run` requires an explicit policy and session. It creates
`.pocketchat/tasks/<task-id>.json`, then routes model/tool work through the
existing request snapshot, trace, session and tool events. Without `--acceptance`, a completed run
exits 0; failed or cancelled runs exit 1. Preparation/refusal errors exit 2.

`task status` distinguishes active, interrupted, failed and terminal states
without running anything. `task resume` refuses legacy, partial or conflicting
task checkpoints before writing, including a checkpoint bound to a different
task id, restores the ordered tool context into the resumed branch and reports
the appended recovery step. It does not send a provider request.

`task report` lists recorded step objects, tool calls, checkpoint bindings,
run ids, trace files and completed `read_file` / `write_file` artifacts. It
matches a run only against a parsed event's own `run_id`, never against raw
JSON text. A malformed, unreadable or missing trace appears in the additive
`evidence_issues` list with `refused` or `unknown` state instead of disappearing
from the report. Without a matching `--policy`, context/turn/session token
budgets are `unknown`; the record's own step/tool budgets remain available.
When a `TaskPlan` was executed through `execute_plan()`, the same report also
lists `execution_steps`, verified `commands` and file `changes` read from
`.pocketchat/task-reports/`. This path only reads existing evidence: it does not
rerun a command or regenerate a provider request. A missing, malformed, foreign
or tampered command archive appears in `evidence_issues`; command success still
never implies acceptance. JSON paths are workspace relative and error output is
one JSON object on stdout with stderr empty.

The deterministic stub demo covers success, checkpoint resume and permission
refusal:

```bash
/usr/bin/python3 examples/task-demo/demo.py
```


### Unreleased: bounded TaskPlan generation

`task plan generate` turns one TaskSpec into a validated, non-executable plan:

```bash
pocketchat task plan generate --spec spec.json --run-id plan-review \
  --config pocketchat.json --profile local --json
```

It writes a plain plan to `.pocketchat/task-plans/<run-id>.json` (or a new
workspace-relative `--out` path) plus an exclusive plan snapshot under
`traces/requests/`. The JSON result carries the plain `plan` path, exact
`plan_sha256`, `spec_sha256`, request snapshots and attempt count. Generation
does not execute a step. Provider failure exits 1; invalid input or an existing
output/snapshot exits 2. See [task-plan.md](task-plan.md).

### Unreleased: authorized TaskPlan execution

`task exec` is the product CLI entry for an already authorized TaskSpec and
TaskPlan. It re-parses both documents, requires the caller's exact plan digest,
and refuses a mismatched or widened plan before reading provider configuration:

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --config pocketchat.json --profile local --json
```

Steps run serially through the same bounded task runtime; command steps use
only the argv templates declared by the TaskSpec. The command returns the
spec/plan digests, step outcomes, task ids and the execution report path. A
failed step exits 1 with its report preserved. `ok` means the attempted steps
completed, not that acceptance passed or that a command was a semantic task
pass; command evidence remains `task_acceptance: not_evaluated`.

### Unreleased: deterministic acceptance

```bash
pocketchat task run "Write the report" --policy policy.json --session audit --acceptance acceptance.json --json
pocketchat task verify TASK_ID --acceptance acceptance.json --json
pocketchat task report TASK_ID --json
```

`--acceptance` is opt-in. Execution `status: completed` remains separate from
`acceptance.state: passed`; with the flag, `ok`/exit 0 requires both. Missing or
mismatched artifacts, permission refusals and unknown observations return 1.
Invalid input is refused before the model call with exit 2. Without the flag,
existing `task run` output and exits are unchanged. `task verify` does no model
work and appends a receipt; `task report` only reads historical receipts and
never rechecks current files. It keeps its report-read exit 0 semantics.
See [task-acceptance.md](task-acceptance.md) for plan/check kinds, 0/1/2 JSON
contracts, evidence validation, limits and platform support.

## Context preview

`context` reads one session and dry-runs the existing context budget without
sending a request or writing any file:

```bash
pocketchat context notes
pocketchat context notes --budget 24000 --json
```

The report contains message indices, roles and character counts, estimated
tokens before/after, and the messages the current drop-oldest-turns policy
would keep or drop. `--keep-recent` pins the recent boundary (default 2).
The session's recorded `max_context_chars` is used when `--budget` is absent;
a budget too small for the pinned prompt exits 2. Message bodies are not
duplicated into the output. Persisted `tool_context` messages appear in the
same order a resumed request would use, and their character count includes
tool call/result metadata.

When `context_projection_enabled` is set, `context` uses the same layered
projection as Session. The fixed instructions, complete recent exchanges,
selected evidence and omission reasons are recorded as metadata; the
`preview_messages` list is the actual history selection, with a pending prompt
appended by the caller. `context_projection_max_bytes` separately bounds the
complete projected JSON and does not reinterpret the older text-only
`max_context_chars` unit. This mode is off by default.

### Next-turn preview with the current config

```sh
pocketchat context notes --prompt "patches: continue" --config pocketchat.json --profile project --json
```

`--prompt` requires non-empty text and an explicit `--config`; `--profile` selects
that config's current profile. No API key is read, no provider is called and no
artifacts are written. Current settings replace historical trace settings,
including fixed system instructions and opt-in memory recall. The default window
matches execution: one recent turn in layered mode, two pinned messages otherwise;
layered JSON-byte admission also applies. `preview_mode` identifies this explicit
next-turn mode. `--budget`/`--keep-recent` are what-if overrides, not changes to the
runtime configuration. A later config/store/session/expiry change can change the
actual request. Without `--prompt`, historical preview behavior is unchanged.
See [project guidance continuity](project-context.md) for save/accept/reopen/retire
and the distinction between disabling new recall and erasing old history.


## Local read tool

`tool read` is a local, explicit-allowlist read-only operation:

```bash
pocketchat tool read notes.txt --allow notes.txt --json
```

The path must be listed with `--allow`; it must resolve inside the current
workspace, contain no sensitive path component, not be a binary suffix and be
valid UTF-8. Output is capped by `--max-bytes` (default 65536); truncation is
reported. The command never writes, executes a command or opens a network
socket.

`tool write` performs an explicit-allowlist UTF-8 write:

```bash
pocketchat tool write notes.txt "new text" --allow notes.txt --overwrite --json
```

Existing files are refused unless `--overwrite` is passed. Writes use atomic
replacement and have a 16 KiB limit. Sensitive/binary/escaping paths are
refused. The tool never deletes files or runs a command.

## Local memory

`memory` manages explicit records in `.pocketchat/memory.jsonl`:

```bash
pocketchat memory add "Prefer concise answers" --tag style --json
pocketchat memory list --json
pocketchat memory search concise --json
pocketchat memory remove <memory-id> --json
```

Records have a `memory_id`, timestamp, content and tags. Add is bounded to
16 KiB and 16 tags; search is literal and read-only; corrupt/wrong-schema
stores are refused. Memory is never injected into model requests automatically.

The v2 memory operations are explicit and require `--store`; they never make
`.pocketchat/memory.v2.jsonl` the default store. `memory recall` is read-only
and `--dry-run` previews deterministic v2 retrieval, exclusions and the bounded
context message without writing or injecting anything:

```bash
pocketchat memory propose "Prefer reviewed patches" --workspace-id workspace-1 --by operator --category project_decision --store .pocketchat/memory.v2.jsonl --json
pocketchat memory migrate --workspace-id workspace-1 --store .pocketchat/memory.v2.jsonl --json
pocketchat memory accept <memory-id> --by operator --verification not_required --store .pocketchat/memory.v2.jsonl --json
pocketchat memory retire <memory-id> --store .pocketchat/memory.v2.jsonl --json
pocketchat memory forget <memory-id> --store .pocketchat/memory.v2.jsonl --json
pocketchat memory recall "query" --workspace-id workspace-1 --store .pocketchat/memory.v2.jsonl --dry-run --json
```

`propose` creates or appends one explicit `user_explicit` candidate without
reading or rewriting v1 and without accepting or verifying the content. The
candidate binds its exact text digest, workspace identity, revision and
`memory:propose:<memory-id>` event reference. Initial publication is exclusive,
and existing corrupt, duplicate, unsafe or non-regular stores are refused
without replacement. `migrate` creates a new bounded v2 file and leaves the v1
source unchanged. Legacy records remain unverified candidates. `accept`
requires an explicit approver and may only mark a `legacy_explicit` or
`user_explicit` preference as `not_required`; it does not invent a source.
`retire` records a lifecycle timestamp, and `--superseded-by` must name another
record in the same store. `forget` physically removes the selected record from
the active v2 store; it does not retain a hidden copy of the content. The
command does not erase content that may already exist in historical traces,
sessions or request snapshots, and it does not promise secure erasure from
backups or storage media. See [project-continuity.md](project-continuity.md) for
the explicit propose/accept/recall/revoke workflow and offline evidence fixture.

## Delegation

`delegate run` executes a finite, serial batch of child prompts:

```bash
pocketchat delegate run "check A" "check B" --profile local --max-children 4 --json
```

Each child is an ordinary independent run with its own trace; the batch has a
child-count cap and deadline, and writes `.pocketchat/delegations/<id>.json`.
A child failure is recorded without aborting the remaining children. This is
bounded delegation, not recursive agent orchestration: children cannot
delegate and no parent session is shared.

## Recovery policy

Schema `"2"` profiles may declare `retry_max_attempts` (1-5, default 1),
`retry_backoff_ms` (0-60000, default 0), `fallback_max_attempts` (1-5,
default 1) and a one-hop `fallback_profile`. Only `retryable` provider
failures consume retry attempts. Each retry writes `retry.attempt` with its
physical request ordinals and backoff; when the primary budget is exhausted,
`fallback.activated` names the source and target profiles. Fallback has its
own budget and is not a chain. A streamed response that ends without `[DONE]`
or a terminal `finish_reason` fails as retryable `incomplete_stream`; partial
content is not recorded as a completed turn. A non-streaming short body or
broken chunked transfer or body-read connection reset fails as retryable `incomplete_response`; an HTTP
protocol failure before response headers is retryable `connection_error`.
These failures retain the request snapshot and failed-response/run trace, not a
partial completed reply. Only the configured recovery policy retries them;
a retryable error does not prove the provider did no work. Cancellation during backoff or a
request ends the run without inventing a recovery success.

`workspace` reads the whole directory back as a graph: which files are two
paths through one conversation, which turn each branch forked from, which run
each file records, and which comparison measured which runs. Nothing is
written and no credential is read, so the graph describes the workspace after
the fact and cannot be changed by the process that drew it:

```bash
pocketchat workspace
pocketchat workspace --json
```

Every path is relative to the workspace, and the JSON carries no absolute path
and no clock, so two processes handed the same files describe the same graph.
Four lists at the end report what the files do *not* say rather than filling it
in: traces and request snapshots nothing claims, links that point at something
absent, two files claiming the same run or trace, and files that cannot be read
at all. A comparison manifest has no field for which answer was kept, so the
graph prints `not recorded` there instead of nominating a winner.

`history`, `search` and `diff` ask questions of those same files. All three
are read-only -- no configuration, no credential, no socket, nothing written --
and every path they print is relative to the workspace:

```bash
pocketchat history                    # every conversation, branch and turn
pocketchat history notes              # one session, by name or by id
pocketchat search "retry policy"      # literal, case-sensitive text
pocketchat search "retry policy" --session notes --json
pocketchat diff notes fork            # common prefix, then each side's turns
```

If the workspace graph contains an unreadable file, a conflict or an
unresolved link, these commands refuse a partial answer and name the affected
path. A valid append is visible to the next query immediately; an existing
index reports `stale` until rebuilt. `index` additionally compares its rows
with a fresh reconstruction and reports `inconsistent` if its source digests
still match but its rows do not.

`history` groups the turns by conversation and branch, so a forked dialogue
reads as two paths through one conversation, each turn named by the id its
position gives it; a target accepts a session name, a conversation id or a
branch id. A v0.1/v0.2 file is listed with its turns and no invented
identity. `search` matches the exact text as given -- case and whitespace
included -- and a hit names the file, conversation, branch and turn it was
found in; zero hits is a result (exit 0), and an empty text is refused.
`diff` compares the words turn by turn and reports the common prefix first,
then the turns each side adds; when one side records the other as its
parent, the fork point it claims is checked against the prefix the files
actually share, and a disagreement is printed rather than quietly resolved.

`index` builds `index.sqlite`, a derived index over the same evidence: one
row per conversation branch, comparison, comparison run and problem the
graph reports, with the sha256 of every source file beside them. It holds
no fact of its own and no command reads it -- delete it and every answer is
unchanged. A build is always whole (there is no incremental update), and
replacing one requires `--rebuild`:

```bash
pocketchat index --rebuild
pocketchat index          # fresh, or stale with the files that changed
```

`index` without `--rebuild` checks the index against the workspace and
reports `fresh`, or `stale` at exit 1, naming every file the two disagree
about. A build that fails halfway removes the half it wrote rather than
leaving an index that looks like an answer.

`show` prints the conversation and then the events that recorded it, and takes
either file as its argument:

```bash
pocketchat show notes
pocketchat show traces/<run-id>.jsonl
```

`show --json` prints one machine-readable object containing `ok`,
`schema_version`, the session header and turns, the trace's line summaries and
`trace_problem`. Artifact paths are relative to the current workspace. A
missing session or trace prints one stable error object on stdout and exits 2
with stderr empty; without `--json`, the existing rendering and diagnostics
remain unchanged.

`export` writes a session archive or an event-only trace. Exporting unchanged
source files twice produces identical bytes:

```bash
pocketchat export notes                     # sessions/notes.export.jsonl
pocketchat export notes --format md        # sessions/notes.export.md
pocketchat export notes --format trace     # sessions/notes.export.trace.jsonl
trace-doctor validate sessions/notes.export.trace.jsonl
```

`export --json` prints one success object with `format`, `source`, `session`,
`out`, `messages` and `trace_events`. A trace-only export reports the trace as
`source` and leaves `session` / `messages` null. Refusals print one stable
error object on stdout and exit 2; existing output is never overwritten.

The `jsonl` export is a mixed archive: the session header, messages, then trace
events, each with a `kind` field. The `md` export is the conversation as prose.
Both include restored tool request/result messages in the order a resumed
request would use. Neither format is an event-only trace for Trace Doctor;
removing `kind` from the mixed archive does not turn its header and messages
into events.

`--format trace` copies the original trace bytes, with no session header,
messages or added `kind` fields. Event ids, timestamps, order and payloads stay
unchanged. It accepts a session name or a trace path, including a trace from a
run without `--session`. Its default suffix is `.export.trace.jsonl`; `--out`
selects another file. Existing output files are refused in all three formats.

Trace export reads every line before creating output. Missing or empty traces,
invalid JSON or UTF-8, and non-event records return exit 2 without creating an
export. It does not repair events, invent a terminal event or replace Trace
Doctor's schema and cross-event validation. The older `jsonl` archive retains
its existing behaviour, including skipping malformed JSON lines in its trace.
Use `trace-doctor fix-suggest FILE` for the `incomplete_run` signal;
`trace-doctor validate` alone does not check terminal-event completeness.

Ctrl-C ends the conversation as `run.cancelled` -- whether it lands while a
request is in flight or while the prompt is waiting for input. Either way the
trace gets a terminal event, because the interrupt is never swallowed by the
read loop.

`chat` also reads from a pipe, so a scripted conversation works:

```bash
printf 'first\nsecond\n/exit\n' | pocketchat chat --quiet
```

## The trace

Events are the Agent Trace Doctor `trace.schema.json` v1 shape. The schema is
shipped inside this package, and it is the published one plus two documented
`model.request.started` payload fields -- `request_snapshot` and
`request_sha256` -- so it is not byte-identical to the published file. What is
unchanged is the event set, the required fields and the event types, which is
why the existing validator reads this output with no new tooling:

```bash
trace-doctor validate traces/<run-id>.jsonl
trace-doctor summary traces/<run-id>.jsonl
```

One run, one run id, one terminal event. `run.started` is written before the
first turn; exactly one of `run.finished`, `run.failed` or `run.cancelled` is
written when the run ends, from the single place every entry point passes
through. A trace with a start and no end is the one shape that reads as "we do
not know what happened", so it is not a shape this program produces.

Event ids are allocated once per session and are never regenerated, and each
event carries its own timestamp. A run that appends to an existing trace
continues that trace's numbering rather than restarting it, so the file stays
one run and no id is used twice. Two exports of one session are therefore
byte-comparable, which is the property that makes a diff between two runs mean
anything.

Response bodies are recorded as a summary (character count plus a short prefix)
by default. `--verbose` records the full body. The choice is yours; the trace
says which one was used.

`model.request.started`, `model.response.completed` and
`model.response.failed` carry `attempt_index` (one-based within the current
turn) and `attempt_scope: "turn"`. They pair one physical request with its
result. The number restarts at 1 on the next turn. It is not a retry ordinal:
`v1.0.x` still sends each request once, so a value greater than 1 means a
future implementation made more than one request in that turn, not that this
version retried automatically.

## `evidence verify`

```bash
pocketchat evidence verify observations.json --workspace ./checkout --json
```

Read one explicit, bounded workspace-relative observation document and use the
shipped evidence-link verifier. Omit `--json` for a human summary; omit
`--workspace` to use the current directory. This does not open the original
artifacts, load credentials, call a provider, execute/replay a task or write any
file. Both modes say that the scope is supplied observations and the original
files were not rechecked. Complete returns 0, partial returns 1, and refused or
invalid input returns 2; partial is never `ok: true`. JSON is one safe bounded
document with input indexes and reason codes, not paths, identity values or
bodies. See [the input contract and limits](evidence-links.md#explicit-observation-cli).

## `evidence release-preflight`

```bash
pocketchat evidence release-preflight release-acceptance.json --workspace . --json
```

This is a subcommand of `evidence`; the stable top-level command list is
unchanged. Read one explicit release-acceptance observation file and report the
remaining 1.2 artifact, provider, manual, authority and publication gates. It does not
invoke a provider, read the wheel or source tree, publish or write. Complete
returns 0; pending/unknown/blocked return 1; refused input returns 2. See
[the observation contract](release-preflight.md).

## Office — shared browser/CLI workspace (P1 foundation)

`office` is separate from legacy `chat`, `agent`, `run` and their JSONL files.
Place workspace options **before** the office subcommand. No command starts a
background server. If `web` owns the directory, the CLI authenticates to that
exact loopback instance; otherwise it takes the exclusive workspace lock.

```bash
pocketchat office --data-dir PRIVATE_DIR conversations create --title "Weekly work"
pocketchat office --data-dir PRIVATE_DIR conversations list
pocketchat office --data-dir PRIVATE_DIR conversations list --archived
pocketchat office --data-dir PRIVATE_DIR search --query "budget"
pocketchat office --data-dir PRIVATE_DIR search --query "budget" --archived
pocketchat office --data-dir PRIVATE_DIR conversations archive --conversation CID
pocketchat office --data-dir PRIVATE_DIR conversations restore --conversation CID
pocketchat office --data-dir PRIVATE_DIR upgrade
pocketchat office --data-dir PRIVATE_DIR conversations show --conversation CID
pocketchat office --data-dir PRIVATE_DIR conversations rename --conversation CID --title "Week 2"
pocketchat office --data-dir PRIVATE_DIR materials add --conversation CID --file notes.md
pocketchat office --data-dir PRIVATE_DIR --json report create --conversation CID --prompt "Draft a report" --source SID --request-id REQUEST_ID
# Review the returned model, provider, history, message and material selection.
# Repeat those exact arguments with --confirm CONFIRMATION_SHA to send.
pocketchat office --data-dir PRIVATE_DIR chat --conversation CID --prompt "Hello" --request-id ANOTHER_ID
pocketchat office --data-dir PRIVATE_DIR report revise --conversation CID --version VERSION_ID --prompt "Shorten it" --request-id REVISION_REQUEST_ID
pocketchat office --data-dir PRIVATE_DIR runs show --run RUN_ID
pocketchat office --data-dir PRIVATE_DIR runs stop --run RUN_ID
pocketchat office --data-dir PRIVATE_DIR export --conversation CID --version VERSION_ID --format md --out report.md
pocketchat office --data-dir PRIVATE_DIR export --conversation CID --version VERSION_ID --format docx --out report.docx
pocketchat office --data-dir PRIVATE_DIR backup --out backup.json
pocketchat office --data-dir NEW_PRIVATE_DIR restore --from backup.json
```

`chat` and `report` without `--confirm` only preview; they do not call the model.
Repeat `--source SID` to select more material. The hash binds the exact request
ID/content, normalized sources/predecessor, current history and model config.
Changes invalidate it. Keep the ID, arguments and original hash until the final
receipt is known: replaying them recovers the original run, including after
restart, rather than calling the provider twice. A different payload with the
same ID is refused. `unknown` is never automatically replayed. Poll `runs show`
after `runs stop`; `stopping` is not a terminated worker.

An offline writer needs `--config CONFIG [--profile NAME]` before `chat`/`report`.
When connected, the server owns config: CLI overrides are refused rather than
silently ignored. Read/import/export/backup/restore require no provider config.
This checkpoint supplies single-request commands, not an interactive office REPL.

`--json` emits one `pocketchat-office-cli.v1` object with `ok` plus `result` or
`error`; command failures keep stderr empty. Exit 0: success or unsent preview;
1: known failed/cancelled/unknown run or interrupted wait; 2: refusal/config/input
or unknown transport outcome. Argument-parser usage errors retain normal argparse
stderr/exit 2. Progress is not mixed into JSON. Human mode prints replies or a
readable result, and errors on stderr. Results never include discovery tokens.

Output is create-only, mode 0600; no overwrite flag exists. Materials remain
bounded TXT/Markdown/CSV, not PDF/DOCX input. DOCX output needs the existing
`office` extra. Backup details and exclusions are in [formats.md](formats.md).
Native discovery and explicit file import/export/restore require no-follow,
directory-descriptor I/O (currently macOS/Linux). They fail closed when unavailable;
there is no insecure Windows fallback. Existing browser/legacy CLI behavior is
unchanged. Protect content-bearing previews, backups and exported documents.

### Office daily conversation operations

`search` matches message bodies literally with Unicode case folding, not regular
expressions or SQL wildcards. Queries are bounded to 240 UTF-8 bytes. Results
contain `conversation_id`, stable decimal-string `message_id`, title, role and a bounded
excerpt; at most 100 matches are returned with an explicit `truncated` flag.
Default list/search shows active conversations; `--archived` shows only archives.
Archive is reversible and never deletes messages, materials, receipts or reports.
Archived conversations may be read/exported and their original receipts recovered,
but must be restored before rename, material import or a new model request. An
active request prevents archive. `conversations restore` restores visibility;
the top-level `restore --from` instead creates a new workspace from a backup.

Schema-2 users must explicitly run `upgrade` before archive. It requires no model,
uses the current writer, keeps a validated pre-upgrade backup inside the private
workspace and is idempotent on the current schema 5. See [backup format](formats.md#office-data-backup-v1).

Message search `message_id` and conversation message `id` are opaque decimal
strings, not JSON numbers; they identify the exact saved message without
JavaScript integer rounding. Backup row IDs retain their integer format.

Office run receipts additionally expose their original `request_id` for read-only
receipt matching; this does not change the original request or call the provider.


### Configured office models and the interactive entrance

```sh
pocketchat office --data-dir PRIVATE_DIR models
pocketchat office --data-dir PRIVATE_DIR models --select PROFILE
pocketchat office --data-dir PRIVATE_DIR interactive
# Offline: explicitly load existing profiles; the live server owns its own config.
pocketchat office --data-dir PRIVATE_DIR --config config.json --profile PROFILE interactive
```

`models` reads/selects only existing office profiles. It does not probe the
endpoint, spend a model call, create credentials, or enable legacy tools/fallback.
Choices display profile, model, safe provider endpoint and office chat/report
capabilities. Selection is owned by the current workspace writer, affects only
new requests and refuses while a request/worker is still active. Restart loads the
explicit config again; selection is not a new persistent global configuration.

`interactive` is an ordinary line entrance, not the historical task agent.
Each non-command line appends to the current **unsent** draft; Enter never sends.
Use `/new TITLE`, `/open CID`, `/list`, `/draft`, `/materials`, `/import PATH`,
`/sources ID [ID…]`, `/chat`, `/report`, `/revise VERSION`, `/models`, `/model PROFILE`,
`/send`, `/recover`, `/stop`, `/export VERSION md|docx PATH`, `/quit`.
The source file is an explicitly selected bounded TXT/Markdown/CSV copy, not
arbitrary provider file access. `/export` uses the same exact-version export and
exclusive-create destination rules as the noninteractive office command.

`/send` displays the actual history, selected material/message, endpoint/model
and content-bound preview hash. Only typing the **entire hash** sends. A mismatch,
EOF or Ctrl+C at confirmation leaves the draft unsent. Each confirmed request is
saved as pending before the network operation. Reopening never resends; `/recover`
reads the exact request-ID receipt, verifies conversation/wire, and never calls
the provider. Missing/unknown receipts remain pending, not permission to retry. A verified
`request_not_accepted` response from the owner (stale preview, before admission)
clears pending by CAS, keeps the draft, and requires a fresh preview/hash;
ordinary errors do not receive that interpretation.
Failed/cancelled receipts retain a draft for a new explicit request. `/stop` uses
the same live owner and worker lifetime as the browser. `--json` is refused for
interactive input; use the existing noninteractive commands for one JSON result.

CLI drafts and pending state are private workspace-local UI state, distinct from
browser-tab drafts and excluded from data backups. A competing CLI state edit
causes compare-and-swap refusal; interaction stops without overwriting it or
sending, and displays any uncommitted draft for the operator to copy. Protect
terminal output containing previews/drafts; local-first does not mean private
content is never sent after confirmation. Old request snapshots do not change
when profiles switch, and old confirmed receipts remain recoverable.


### Office chat branches

`office conversations show --conversation ID` retains all messages and reports;
`active_run_ids` identifies the currently sent history and `chat_run_ids` the
completed ordinary-chat targets. Run IDs also appear on saved messages.

```sh
pocketchat office --data-dir PRIVATE_DIR --json chat --conversation ID --branch-from RUN_ID --prompt "Edited question" --request-id NEW_REQUEST_ID
```

Inspect the preview, then repeat the exact command with `--confirm HASH`. Use the
same original prompt to regenerate; selected `--source` material is still explicit.
The selected turn's original prefix is sent, never its old reply or later tail.
The active path changes only on success. Reuse the exact request/confirmation only
to recover its receipt, not to regenerate again; a new attempt needs a new ID.

In `office interactive`, `/history` shows run IDs and active/saved-earlier labels.
`/branch RUN_ID` loads the original question and source selection into a private
unsent branch draft. Ordinary text appends to that draft; use the noninteractive
command for an exact replacement prompt. `/send` still requires the entire fresh
confirmation hash. `/chat`, `/report` or `/revise VERSION` leaves branch mode;
`/sources` can change explicit material. Reopening keeps the branch draft without
sending. Only completed ordinary-chat turns in this conversation are eligible.
Run `office upgrade` first for schema-2/3 workspaces; backup readback must succeed.


### Office project and explicit memory controls

```sh
pocketchat office --data-dir PRIVATE_DIR --json project show
pocketchat office --data-dir PRIVATE_DIR --json project instructions --project-id PROJECT_ID --revision 1 --text "Keep reports concise."
pocketchat office --data-dir PRIVATE_DIR --json project memory --project-id PROJECT_ID --revision 1 --enabled true
pocketchat office --data-dir PRIVATE_DIR --json memory list
pocketchat office --data-dir PRIVATE_DIR --json memory propose --project-id PROJECT_ID --store-sha256 HASH --text "Prefer concise replies" --category user_preference --personal-guidance
pocketchat office --data-dir PRIVATE_DIR --json memory accept --project-id PROJECT_ID --store-sha256 HASH --memory-id MEMORY_ID --revision 1 --personal-guidance
pocketchat office --data-dir PRIVATE_DIR --json memory recall --query "concise"
pocketchat office --data-dir PRIVATE_DIR --json memory revise --project-id PROJECT_ID --store-sha256 HASH --memory-id MEMORY_ID --revision REVISION --text "Prefer concise Chinese replies" --personal-guidance
pocketchat office --data-dir PRIVATE_DIR --json memory retire --project-id PROJECT_ID --store-sha256 HASH --memory-id MEMORY_ID --revision REVISION
pocketchat office --data-dir PRIVATE_DIR --json memory forget --project-id PROJECT_ID --store-sha256 HASH --memory-id MEMORY_ID --revision REVISION --acknowledge-history
```

Use the current project revisions and whole-store hash from readback, not the
example placeholders. Proposals/revisions do not become accepted automatically.
`--personal-guidance` declares non-factual personal or project guidance, not
independent fact verification. `--acknowledge-history` acknowledges that prior
traces, exports and backups are not erased. These actions never call a provider.
Recall is disabled until explicitly enabled. Schema-5 previews include accepted
project guidance and project instructions in the final content-bound request;
source text and remembered text remain data, never tool permissions.
Old schemas require explicit verified-backup upgrade for these controls.


### Explicit office continuity summary

`office summary show --conversation ID` returns the accepted summary and original
active-branch messages. `office summary save --conversation ID --project-id ID
--revision REVISION --text TEXT --refs-json JSON --accept` explicitly saves a
user-authored summary. References contain `message_id`, `run_id`, `start`, `end`,
`sha256`; ranges use Unicode code points, digests use selected UTF-8 original text.
Zero is the initial revision. Missing, foreign, stale or changed text references
refuse. These operations never call a model or overwrite original messages.
Schema-5 request previews now expose `prepared_payload` and `context_manifest`;
project revisions, whole-memory digest, accepted summary and final payload are
bound to confirmation. Old confirmation refuses after any selected input changes.
The admitted single-turn Session sends the frozen payload without reselecting.


## DOCX package check (not source import)

`pocketchat office --data-dir PRIVATE_DIR --json documents check --file small.docx`
checks the explicitly selected local DOCX package and returns raw SHA-256, bytes,
members/expanded bytes, body-paragraph/table-cell counts, `status=package_checked`
and `text_extracted=false`. It never opens/creates a workspace, reads provider
configuration, calls a provider or saves a source. JSON retains the
`pocketchat-office-cli.v1` envelope: exit 0 success / 2 refusal, empty stderr.
Bounded ZIP/XML, content-type and relationship checks refuse unsafe/unsupported
content; missing no-follow directory-descriptor support is a named refusal.
Success is not text extraction, complete DOCX support or arbitrary-file safety.
See [the closed profile and limits](office-document-reading.md#8-implemented-docx-package-check-not-p2-extraction).
