# Command manual

The reference for the commands' options and behaviour: comparisons and their
manifests, request snapshots, replay, sessions and branches, the workspace
graph, the queries, the derived index, show and export, and the trace. File
formats and their compatibility rules are in [formats.md](formats.md); the
rules about credentials and the written evidence are in
[security.md](security.md). The README carries the overview and the
quickstart.

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
were spent. Cost is calculated only from the counters present, so `unknown`
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

A mismatch exits 2 and prints both the expected and actual digest. A missing or
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
understand refuses with the version named. Every file this package writes —
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

`export` writes a session archive or an event-only trace. Exporting unchanged
source files twice produces identical bytes:

```bash
pocketchat export notes                     # sessions/notes.export.jsonl
pocketchat export notes --format md        # sessions/notes.export.md
pocketchat export notes --format trace     # sessions/notes.export.trace.jsonl
trace-doctor validate sessions/notes.export.trace.jsonl
```

The `jsonl` export is a mixed archive: the session header, messages, then trace
events, each with a `kind` field. The `md` export is the conversation as prose.
Neither format is an event-only trace for Trace Doctor; removing `kind` from
the mixed archive does not turn its header and messages into events.

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
