# pocketchat

For the version 1.2 source-candidate preparation and exact-artifact checklist,
see [release-1.2.md](docs/release-1.2.md). Publication facts are owned by the
[release ledger](docs/release-ledger.md); source and offline fixture results do
not certify a real provider or manual file-terminal acceptance.

`pocketchat` is a small single-user CLI chat agent. It is **not** a general
agent: it has no code execution, no shell and no unbounded tool surface. Its
optional local tools are explicit, allowlisted and workspace-confined. It holds
a multi-turn conversation with a model you configure and writes a trace that
another tool can check afterwards.

The trace is the point. Everything else here exists so that when someone asks
"what did the model actually see, and what did it actually answer", there is a
file to open instead of a memory to trust.

## What it is not

- No general-purpose tool calling. Optional local tools are limited to
  allowlisted reads, create-only writes and read-only memory search.
- No web UI, local server product, or packaged desktop app. There is no cloud
  service, account system or sync.
- No vector retrieval or automatic long-term memory. Local memory is explicit
  and is never silently injected into prompts.
- No unbounded retries. Optional retry/fallback is bounded and recorded.
- No bundled provider SDK. It speaks the OpenAI-compatible HTTP API over
  `urllib`, against a `base_url` you configure.

## Version boundaries

`v1.1.0` is the current CLI-only source release. It keeps the command,
exit-code, JSON-envelope, file-format and provider-adapter boundaries documented
in [compatibility.md](docs/compatibility.md) and
[provider-adapters.md](docs/provider-adapters.md). The development and release
process is [development-and-release.md](docs/development-and-release.md).
Contributor rules are in [AGENTS.md](AGENTS.md); the current approved slice is
in [slice-queue.md](docs/slice-queue.md) and publication status is in
[release-ledger.md](docs/release-ledger.md). The version-specific v1.1 record
is [release-1.1.md](docs/release-1.1.md); the v1.0.0
candidate record remains [release-1.0.md](docs/release-1.0.md).
The evidence-gated roadmap for future Agent foundations is in
[agent-development-master-plan.zh-CN.md](docs/agent-development-master-plan.zh-CN.md).
Candidate next slices are listed in
[pc-next-steps.zh-CN.md](docs/pc-next-steps.zh-CN.md); they do not start until they
are approved in the slice queue. Implemented evidence fields and remaining gaps
are recorded in [pocketchat-evidence-matrix.md](docs/pocketchat-evidence-matrix.md).
The runtime target is CPython 3.9 and 3.13 on Linux, macOS and Windows;
current verification is local-only on macOS. The package carries a
cross-platform CI workflow; whether it ran green for a particular candidate SHA
is recorded per release, not inferred from the runtime target. No Web UI, local
API, desktop client, remote service or general-purpose tool calling is in scope.

`v0.1.0` is the single-profile foundation: installable CLI, one configured
provider, `run`, `chat`, `show`, `export`, traces with terminal events, and
session resume. On 2026-10-20 one real `run` completed through the local
free aggregate route using one configured model (`run_20261020t015755_8e636ff8`,
trace SHA-256 `9102fab4a549ff398265d0094c38bf74cd81a33241db7bf77fbcd64621453e45`).
That verifies only that request, trace and credential hygiene for that call; it
does not extend to other providers.

`v0.2.0` adds multi-profile configuration and selection, serial comparison,
request snapshots, comparison manifests, cost and latency, and replay dry-run.
It does not add tool calling, automatic retries, branch trees, a web UI, a
local server product or a second provider SDK. The interactive TTY surface
streams assistant text and reasoning deltas; non-interactive commands retain
their complete-response output.

## Unreleased: verified task delivery

The working source adds opt-in deterministic artifact acceptance after Task Run.
Execution `completed` and acceptance `passed` are separate: a model answer alone
cannot satisfy declared file/text/JSON conditions. Use `task run --acceptance`,
`task verify` and the acceptance section of `task report`. This is not in the
published v1.1.0 artifact. See [task-acceptance.md](docs/task-acceptance.md) for
0/1/2 exit codes, historical receipts and the unsupported-platform boundary.

```bash
python3 examples/task-acceptance/demo.py
```

## Unreleased: local reports and revisions

This reviewed source candidate lets the existing authorized task CLI forward completed read snapshots to a dependent report step, with Session results bound to the pinned read trace and task workspace bound to the authorized policy, then create a separate revised artifact and resume without replaying completed reads or writes. Run `python3 examples/local-report-demo.py` for the offline real-file CLI fixture. See [local-report.md](docs/local-report.md) for grants, snapshot provenance, refusal paths and the real-provider verification boundary. Exact-head CI and product-main readback remain pending; this source addition is not a formal release.

## Unreleased: explicit project continuity

The source candidate adds `memory propose`, which creates one bounded,
unverified v2 candidate without touching the legacy store or granting recall.
An operator accepts the exact record separately; an explicitly configured
profile may then project its source-, revision- and digest-bound excerpt into a
later Session. Unaccepted, retired, expired and foreign-workspace records stay
out of new requests. Run `python3 examples/project-continuity-demo.py` for the
offline request/trace fixture and see
[project-continuity.md](docs/project-continuity.md) for the authority, evidence
and deletion boundaries. This does not add automatic capture, model approval,
provider verification, sync or formal publication.

## Requirements

Python 3.9 or newer. No third-party packages at runtime.

## Install

```bash
python3 -m pip install .
pocketchat --help
```

### Use a local newnewrouter catalog

If newnewrouter is running on `127.0.0.1:8137`, generate one PocketChat profile
per live model:

```bash
python3 scripts/sync_newnewrouter_models.py
python3 -m pocketchat doctor --config ~/.pocketchat/newnewrouter.json
python3 -m pocketchat agent --config ~/.pocketchat/newnewrouter.json
```

The generated profiles set `requires_api_key: false`: newnewrouter owns the
upstream credentials, while PocketChat never stores or prints them. The
generator prefers `pgt` as the default when the catalog
contains it, then falls back to the first catalog entry. Re-run the
sync script whenever the router's `/v1/models` catalog changes.

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

```bash
pocketchat profile set staging \
  --base-url https://example.invalid/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_STAGING_KEY
pocketchat profile use staging
pocketchat profile remove staging
```

`profile set` creates or replaces a complete schema `"2"` profile and never
takes a credential value. `profile use` changes the default, and `profile
remove` refuses the current default. Every rejected write leaves the file
unchanged; successful writes validate the whole candidate, preserve the file
mode and replace it atomically. After arguments parse, `--json` provides
stable success and configuration/write-error objects. See the [command manual](docs/cli.md) for the full options.

Schema `"2"` profiles can also declare `retry_max_attempts`,
`retry_backoff_ms`, `fallback_max_attempts` and a one-hop `fallback_profile`.
The defaults preserve today's behavior: no automatic retry and no fallback.
Optional `max_turn_tokens` and `max_session_tokens` add fail-closed token
budgets; they are not currency limits.

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

## Agent chat

`pocketchat agent` is the full-screen interactive surface. It keeps a
conversation in a session and lets the next message use a different profile,
reasoning setting or context budget. Slash commands are control input and never
become user turns or trace events.

```bash
pocketchat
```

Bare `pocketchat` enters the interactive agent. On a terminal with the
standard-library curses module it opens a full-screen interface with a compact
intro, compact message gutters and separators, a persistent status rule, scrolling conversation, input
history and slash-command completion. `Enter` sends,
`Ctrl+J` inserts a newline, `Up`/`Down` recall history, the mouse wheel and
`PgUp`/`PgDn` move through transcript history, `Ctrl+T` expands or collapses
reasoning, `Ctrl+C` interrupts an in-flight reply and `Ctrl+D` exits. Type `/` to select a command with Up/Down and Tab; Enter first completes a
partial command, then executes the completed command. On platforms whose
standard library does not provide curses (including standard Windows Python),
the same command falls back to the line interface instead of importing a
third-party renderer. Type `/model` with no
profile to choose from the profile picker. While a turn runs, the interface shows a
live `thinking...` line, elapsed seconds, reasoning deltas and assistant text
as they arrive. After a turn, the status rule reads the trace and shows reported
tokens (`tok`), configured-price cost estimate (`cost~`), latest TTFT, physical
request count (`req`) and compaction facts (`cmp`). Missing usage, prices or
TTFT show `?`; `req` is not a retry count. Piped input keeps the line-oriented
REPL for scripts and tests.

It uses `./pocketchat.json` when present, otherwise
`~/.pocketchat/newnewrouter.json`; explicit `--config` wins. Named subcommands
such as `pocketchat run` and `pocketchat profile list` remain available.

```bash
pocketchat --profile hosted --reasoning high --session notes
```

```text
/model hosted
/session another-conversation
/reasoning high
/context 16000
/status
/exit
```

Each plain message is its own run with its own terminal trace event; the
session file carries the conversation between runs. `reasoning` is always
recorded in the run configuration. It is sent to an OpenAI-compatible endpoint
only when that profile declares `reasoning_effort_supported: true`, because
compatible endpoints do not agree on that extension. Other configured agent
capabilities are unchanged by this checkpoint. The current trusted-terminal
checkpoint adds opt-in, exact-path file controls: `/files` shows current grants
and the pending change id; `/files read|write PATH...` shows the provider disclosure
and proposed exact grant; `/diff` or `/apply` shows the complete pending diff;
`/confirm` performs the exact displayed action; `/changes` lists this conversation's
persisted change
metadata, and `/undo CHANGE_ID` previews one persisted compensation. The model can
only read granted paths and propose changes; it cannot grant, confirm, apply or undo.
File controls require both stdin and stdout to be TTYs. Piped input is refused before
creating a session, trace or file-change store. A successful apply consumes that
path's write grant, so undo requires a fresh disclosed write grant. `/files cancel`
invalidates grants and an unconfirmed review, without undoing applied effects.
Interrupted runs do the same; a still-live request retains its writer until exit.
The [offline golden fixture](docs/file-work-validation.md) covers the shared
controller and can run against an installed development wheel. Real-provider/manual
acceptance and formal release are separate; a development pass is not a release.

## Compare

`compare` runs one prompt against several profiles on the same context, and
records a manifest of replies, latency, tokens, cost and failures:

```bash
pocketchat compare "summarize this" --profiles local,hosted
```

The full manual — compare options and the manifest, request snapshots,
replay, sessions and branches, checkpoint/rewind, the workspace graph, the
queries, the derived index, show and export, and the trace — lives in
[docs/cli.md](docs/cli.md).

For scripts, `init`, `run`, `chat`, `branch`, `checkpoint`, `rewind`, `task`,
`show`, `context` and `export` accept
`--json`. Execution objects carry status, run id, turn count, reply and a
workspace-relative trace; branch objects omit message bodies; read/export
objects report relative artifact paths. Failures are stable error objects and
default output is unchanged.

`checkpoint` records a completed session boundary and `rewind` starts a new
branch from it. The source session and trace remain unchanged; the recovery
manifest makes the boundary and interrupted rewind transaction auditable.

`task run --policy FILE --session NAME` executes one bounded task through the
same provider, tools, session and trace path. `task status` reports the closed
task state, `task resume TASK --checkpoint ID --policy FILE` restores a task
checkpoint and reports its recovery step, and `task report TASK` aggregates
only existing steps, tools, budgets, checkpoints, exact run-to-trace links and
completed file artifacts. Malformed or missing trace evidence is reported in
`evidence_issues`, not silently omitted. All four support stable `--json`;
token budgets are `unknown` unless the report receives the same policy and its
digest matches. The deterministic local smoke demo is
`examples/task-demo/demo.py`.

`context` previews a session's message budget and dry-runs the current
drop-oldest-turns compaction without sending a request or writing any file.

`tool read PATH --allow PATH` reads one explicitly allowlisted UTF-8 file
inside the workspace. It is read-only, bounded, and refuses sensitive or
binary paths; `tool read` itself never writes.

`tool write PATH CONTENT --allow PATH` writes one allowlisted UTF-8 file
atomically. Existing files require `--overwrite`; no delete, shell, execute
or network tool is exposed.

Profiles may enable automatic `read_file` tool calls for the model with
`tool_read_enabled`, `tool_read_allow_paths`, `tool_call_max_count` and
`tool_result_max_bytes`. The default is disabled; only `read_file` is offered.

Profiles may separately enable create-only automatic `write_file` calls with
`tool_write_enabled` and `tool_write_allow_paths`. The model cannot overwrite
or delete files; `--no-tool-write` disables the tool.

Profiles may also enable read-only automatic `memory_search` calls with
`tool_memory_search_enabled` and `tool_memory_search_max_results`. The model
cannot add, edit or delete memory.

In the automatic tool loop, disabled or unknown tools, missing arguments and
workspace guard refusals return bounded `status: failed` tool results so the
model can correct within the existing call budget. Underlying I/O or memory
store failures, oversized results, exhausted budgets, provider errors and
interrupts still end the run. Permission and tool trace events carry the tool
call id. Completed tool calls and results are retained as additive session
`tool_context`, so the next prompt, a resumed process and a derived branch see
the same tool interaction before the final assistant reply.

`memory add/list/search/remove` manages explicit local memory records in
`.pocketchat/memory.jsonl`. Memories are never injected into prompts
automatically.

`delegate run PROMPT...` executes a bounded, serial batch of independent
child runs and writes a delegation manifest. It is not recursive agent
orchestration and does not share a parent session.

## Scope

PocketChat is CLI-only. It does not plan, support or accept new work for a Web
UI, local API, desktop client, mobile client, remote service or general-purpose
tool calling. The explicit local tool boundaries above are the complete tool
surface.


The rules this package enforces about credentials and the evidence it
writes are consolidated in [`docs/security.md`](docs/security.md); the file
formats and their compatibility rules are in
[`docs/formats.md`](docs/formats.md).

## Status

Stable CLI candidate. The conversation kernel, the REPL and session persistence
are implemented. PocketChat is a local-first, trace-native, multi-model
conversation **CLI** for AI builders and technical teams. The CLI is the only
product surface. Web UI, desktop clients and general-purpose tool calling are
out of scope; the explicit local tool surface remains available as documented.

On 2026-10-28 the CLI-only regression passed on CPython 3.9.6 and 3.13:
371 tests each. A clean 3.9 venv installed from source reported the package
from `site-packages`, contained the trace schema, contained no `web` package,
and rejected `pocketchat serve` with exit 2. The installed CLI also completed
init/doctor JSON, a failed-run JSON document at exit 1 with a terminal trace,
and a byte-identical trace export.

The CLI holds several profiles in one configuration and runs the one you pick
with `--profile`, or the config's `default_profile` when you do not. Each
request is saved as a snapshot beside its trace, so what was sent can be read
back and checked, and `compare` runs one prompt against several of them at once
and writes a manifest over the resulting runs. Replay dry-run -- re-sending a
saved request without a provider -- verifies the snapshot without opening a
socket or reading a credential.

Every test listed here uses a local stub HTTP server, so the tests prove the
shape of requests, traces and every failure path -- not any provider's
behaviour. Separately recorded real calls through the local cc-switch free
aggregate route cover their own route inputs only (a single run and a
two-profile comparison on 2026-10-20, the full loop -- conversation,
comparison, selection, continuation, branch, export, validation -- on
2026-10-24); see the manual for those cases. They do not make the tests
less stub-backed, and they do not extend to other providers.

## License

Apache-2.0. See `LICENSE`.

## Repository

Source is maintained at `barry31-dev/pocketchat`.

## Release status

The `v1.1.0` source release is public, with a GitHub tag and Release. It is not
published to a package index; install the attached wheel from that Release or
install from the repository source as shown above. The earlier `v0.3.0` source
release remains public.

A Chinese README, command for command the same as this file, lives at
[`README.zh-CN.md`](README.zh-CN.md).

### Source-bound code repair

The existing opt-in task repair now binds actual authorized source snapshots before the provider request and supports exact no-replay recovery after the successful retry. Try the [offline Swift Package fixture](docs/code-repair.md); it performs real local tests but does not certify real-provider diagnosis quality.
