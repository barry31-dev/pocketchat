# Security boundaries

What this package does — and does not do — with credentials and with the
evidence it writes. Every rule here is enforced in code and pinned by a
test; each section names the module that owns it. This page consolidates
rules that otherwise live scattered across the README and the module
docstrings.

## Credentials never touch disk

- The configuration file stores the *name* of the environment variable a
  profile reads (`api_key_env`), never a value. There is no field a key
  could be typed into. (`pocketchat.config`)
- `pocketchat profile list` prints each profile's model, base URL, the
  variable's name and whether it is set. The value never appears — not in
  the text output, not in `--json`. (`cli._cmd_profile`)
- Keys come from the environment and nothing else. There is no desktop
  keychain and no other credential source.

## Requests and snapshots

- The request snapshot carries the body exactly as sent and **no headers**:
  the Authorization header is not recorded anywhere. (`pocketchat.request`)
- Before a snapshot is written, the body is checked against the credential
  this process holds, as a literal byte match — whatever quotes or escapes
  surround it. A body containing the key fails closed: no snapshot, not a
  redacted one. (`CredentialInRequestError`)
- The comparison's up-front checks resolve profiles and credentials before
  the first request goes out, so a missing key is a usage error (exit 2)
  with no trace, no snapshot and no manifest left behind.

## Traces and replies

- A trace records a *summary* of each response by default (a length and a
  short prefix, `summary_prefix_chars`). The full body is the `--verbose`
  choice, made by the operator, and the choice is itself recorded in the
  run's own evidence (`run.started` → `config_summary.verbose`) — which is
  what lets `select --into` refuse to write a summary into a session where
  it would read as the whole answer.
- Provider errors are redacted against the literal credential before they
  reach a trace or a manifest: a response body that echoes the key back is
  not stored. (`pocketchat.provider`)

## Provider adapter boundary

Every model call goes through a provider adapter. The adapter builds the exact
payload and performs the call; the adapter name is recorded in the run's config
summary. See [provider-adapters.md](provider-adapters.md). The security rules
below apply to every adapter, including future native adapters.

## What is written where

The evidence the package writes lives inside the workspace the commands run
in: `sessions/`, `traces/` (with `traces/requests/`), `comparisons/`, and —
only when asked — `index.sqlite`. The one deliberate exception is
`export --out`, which writes wherever the operator points it. Paths inside
the records are relative to the workspace; the JSON records
(`workspace`, `history`, `search`, `diff`) carry no absolute path. There is
no telemetry, no default upload, no silent default endpoint: a provider is
used because a profile named it.

## Local tool boundary

`tool read` is the only local tool surface. It requires explicit allowlisted
paths, stays inside the workspace, refuses sensitive names and binary data,
never writes or executes commands, and records permission/tool events without
storing full arguments.

`tool write` is a separate explicit-allowlist write surface. It accepts UTF-8
text only, caps writes at 16 KiB, uses atomic replacement, and requires
`--overwrite` to replace an existing file. It provides no delete, shell,
execute or network operation.

Automatic model tool calls are off by default. A profile offers only the local
tools it explicitly enables (`read_file`, create-only `write_file` or read-only
`memory_search`), paths come from explicit allowlists, and calls/results are
bounded. Disabled or unknown tools, invalid arguments and guard refusals return
bounded failure results so the model can correct; underlying I/O/store errors,
oversized results and exhausted budgets end the run. Tool schemas and call ids
are recorded in the run trace; shell, execute and network tools remain
unavailable.

When `tool_write_enabled` is enabled, `write_file` is create-only. The model
cannot request overwrite or delete, and every target must be explicitly
allowlisted. Existing files fail closed before any write.

`memory_search`, when enabled, is read-only. It searches explicit local
records and returns bounded metadata/content; the model cannot add, edit or
delete memories. Memory is never silently injected into prompts.

## Not yet (and therefore not promised)

PocketChat is CLI-only. There is no planned single-profile chat-send route,
remote-access mode, TLS, account system, analytics, telemetry, packaged desktop
client, Windows desktop support or desktop keychain.
