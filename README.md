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

`compare` runs one prompt against several profiles on the same context, and
records a manifest of replies, latency, tokens, cost and failures:

```bash
pocketchat compare "summarize this" --profiles local,hosted
```

The full manual — compare options and the manifest, request snapshots,
replay, sessions and branches, the workspace graph, the queries, the derived
index, show and export, and the trace — lives in
[docs/cli.md](docs/cli.md).


The rules this package enforces about credentials and the evidence it
writes are consolidated in [`docs/security.md`](docs/security.md); the file
formats and their compatibility rules are in
[`docs/formats.md`](docs/formats.md).

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

The `v0.3.0` source release is public. It is not published to a package index;
install it from the repository source as shown above.

A Chinese README, command for command the same as this file, lives at
[`README.zh-CN.md`](README.zh-CN.md).
