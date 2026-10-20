# pocketchat

`pocketchat` is a small single-user CLI chat agent. It is **not** a general
agent: it has no tools, no file access, no code execution and no multi-agent
anything. It does one thing -- it holds a multi-turn conversation with a model
you configure, and it writes a trace of that conversation that another tool can
check afterwards.

The trace is the point. Everything else here exists so that when someone asks
"what did the model actually see, and what did it actually answer", there is a
file to open instead of a memory to trust.

## What it is not

- No tool calling or function calling. The first version is conversation only.
- No GUI, no web UI, no cloud service, no accounts, no sync.
- No retrieval, no memory store, no long-term memory beyond the current session.
- No automatic retries. One failed request stops that turn.
- No bundled provider SDK. It speaks the OpenAI-compatible HTTP API over
  `urllib`, against a `base_url` you configure.

## Version boundaries

`v0.1.0` is the single-profile foundation: installable CLI, one configured
provider, `run`, `chat`, `show`, `export`, traces with terminal events, and
session resume. On 2026-10-20 one real `run` completed through the local
free aggregate route using one configured model (`run_20261020t015755_8e636ff8`,
trace SHA-256 `9102fab4a549ff398265d0094c38bf74cd81a33241db7bf77fbcd64621453e45`).
That verifies only that request, trace and credential hygiene for that call; it
does not extend to other providers.

`v0.2.0` adds multi-profile configuration and selection, serial comparison,
request snapshots, comparison manifests, cost and latency, and replay dry-run.
It does not add tool calling, automatic retries, branch trees, streaming, a
web UI or a second provider SDK.

## Requirements

Python 3.9 or newer. No third-party packages at runtime.

## Install

```bash
python3 -m pip install .
pocketchat --help
```

## Configure

`pocketchat init` writes a template:

```bash
pocketchat init
```

```json
{
  "schema_version": "2",
  "name": "local-provider",
  "default_profile": "local",
  "system_prompt": "You are a concise assistant.",
  "profiles": {
    "local": {
      "base_url": "http://127.0.0.1:8123/v1",
      "model": "example-model",
      "api_key_env": "POCKETCHAT_API_KEY",
      "timeout_seconds": 60,
      "max_context_chars": 24000,
      "summary_prefix_chars": 160,
      "input_price_per_million": null,
      "output_price_per_million": null
    },
    "hosted": {
      "base_url": "https://example.invalid/v1",
      "model": "example-model",
      "api_key_env": "POCKETCHAT_HOSTED_KEY",
      "timeout_seconds": 60,
      "max_context_chars": 24000,
      "summary_prefix_chars": 160,
      "input_price_per_million": null,
      "output_price_per_million": null
    }
  }
}
```

The config names the environment variable that holds the credential. It never
holds the credential. **Credentials are read from the environment and are never
written to the trace, to a session file or to stdout.**

`base_url` must point at an OpenAI-compatible endpoint. Without a credential in
the environment, `run` refuses to start rather than reaching for a default.

A `schema_version` of `"1"` still loads, unchanged in behaviour: it names a
single `provider`, which becomes one profile called after the config's `name`
(or the file's stem when it has none). Nothing about a v1 run is rewritten --
the migration happens in memory, on load. `run` and `chat` use
`default_profile`, which is the same as naming it with `--profile`.

`pocketchat profile list` shows what a config offers: one line per profile,
with the model, the base URL, the name of the environment variable that holds
its credential, and whether that variable is set in this shell. The
credential's value is never printed. `--json` prints the same facts as one
object.

```bash
pocketchat profile list
pocketchat profile list --json
```

`run` and `chat` take `--profile NAME`. An unknown name is refused before any
trace file is created, and the error names the profiles that do exist. A
profile whose credential is not set fails the same way -- there is no falling
back to a profile that has one. The run's own trace records which profile it
used, because two profiles can name the same model.

## Run

```bash
export POCKETCHAT_API_KEY=...
pocketchat run "what did we decide last time?"
```

Every run writes one JSONL trace. `pocketchat show` prints it:

```bash
pocketchat show traces/<run-id>.jsonl
```

## Chat

```bash
pocketchat chat
```

Reads a line, sends it, prints the reply, repeats until EOF, Ctrl-C or one of
`/exit`, `/quit`, `/bye`. Blank lines are not prompts. `--session FILE` names or
resumes the session, the same flag `run` takes.

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

## Status

Early development. The conversation kernel, the REPL and session persistence
are implemented. PocketChat is now planned as a multi-version product: a
local-first, trace-native multi-model conversation workbench for AI builders
and technical teams. The CLI is the first product surface; a desktop client is
a later milestone.

The CLI holds several profiles in one configuration and runs the one you pick
with `--profile`, or the config's `default_profile` when you do not. Each
request is saved as a snapshot beside its trace, so what was sent can be read
back and checked, and `compare` runs one prompt against several of them at once
and writes a manifest over the resulting runs. Replay dry-run -- re-sending a
saved request without a provider -- verifies the snapshot without opening a
socket or reading a credential.

Every test listed here uses a local stub HTTP server, so the tests prove the
shape of the request, the trace, and every failure path -- not any provider's
behaviour. A separate local free-route real call and comparison was made on
2026-10-20; its evidence boundary is described in the version-boundaries and
comparison sections and is not automatically reproduced by the test suite.

## License

Apache-2.0. See `LICENSE`.

## Repository

Source is maintained at `barry31-dev/pocketchat`.

## Release status

The `v0.2.0` source release is public. It is not published to a package index;
install it from the repository source as shown above.

A Chinese README, command for command the same as this file, lives at
[`README.zh-CN.md`](README.zh-CN.md).
