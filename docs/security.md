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
used because a profile named it. Pending file-change objects and records live in
`.pocketchat/changes/` with private permissions. That directory is internal,
never model-readable or grantable, and must not be published with a workspace.

## Local tool boundary

`tool read` is the only local tool surface. It requires explicit allowlisted
paths, stays inside the workspace, refuses sensitive names and binary data,
never writes or executes commands, and records permission/tool events without
storing full arguments.

`tool write` is a separate explicit-allowlist write surface. It accepts UTF-8
text only, caps writes at 16 KiB, uses atomic replacement, and requires
`--overwrite` to replace an existing file. It provides no delete, shell,
execute or network operation.

The file-change service adds an internal, unregistered proposal helper plus
separate confirmation, apply and identity-bound undo primitives. A proposal only
writes private content-addressed evidence and returns a complete bounded diff; it
cannot modify the target. Apply requires the exact proposal/policy confirmation,
a current write grant, unchanged target identity/hash and the exclusive writer
claim. Undo requires an explicit local approval, the durable applied receipt and
either its exact file identity or a verified successor compensation identity.
Created files are removed only while their owned identity/hash remain unchanged.
All mutations use no-follow descriptor-relative I/O and independent readback.
Persisted session compensation stops at the first conflict. These primitives are
not present in the model tool contract, CLI, Session or UI, and expose no general
overwrite, delete or undo tool.

Automatic model tool calls are off by default. A profile offers only the local
tools it explicitly enables (`read_file`, create-only `write_file` or read-only
`memory_search`), paths come from explicit allowlists, and calls/results are
bounded. The provider schema, trace summary, task policy and host validator all
come from one closed contract in `pocketchat.tool_contracts`; unknown arguments
are refused before file or memory I/O. Disabled or unknown tools, invalid
arguments and guard refusals return bounded failure results so the model can
correct; underlying I/O/store errors, oversized results and exhausted budgets
end the run. Tool schemas and call ids are recorded in the run trace; shell,
execute and network tools remain unavailable.

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

## Unreleased acceptance boundary

The operator's acceptance plan authorizes finite read-only artifact checks,
not additional model tools. The verifier refuses sensitive/internal paths,
symlinks, special files and over-limit content; unsupported no-follow APIs do
not fall back to ordinary path reads. Per-check result JSON carries bytes/hash
but no artifact body, text needle or expected value. Independent receipts embed
the operator plan, so do not put credentials or secret comparison values in that
input. Receipts are local editable observations, not signatures, adversarial
tamper proof, authorship evidence or a security sandbox. See
[task-acceptance.md](task-acceptance.md) for exact limits and result states.

## Shared office CLI and data-only restore

`office_service` acquires the existing office writer lock or reads a private
no-follow discovery file bound to the canonical directory's identity. It only
connects to literal `127.0.0.1` HTTP, without proxies or redirects. Before sending
any bearer or business body on each connection, it verifies a fresh nonce HMAC
proof from the server, then uses that same socket with automatic reconnect
disabled. Echoing the workspace header alone cannot authenticate a stale port.
It additionally verifies the workspace binding in every response. The native bearer is not the browser
launch/cookie token. Native requests carrying browser Origin/fetch metadata are
refused; browser Host/Origin/CSRF checks remain required. Discovery is not model
send consent. CLI previews require a content/history/config-bound confirmation;
receipt recovery does not create new execution authority. Tools and fallback
remain disabled by `OfficeWorkspace`, regardless of legacy profile flags.

Explicit file operations require no-follow directory-descriptor support and
fail closed when it is absent. Backups never copy credential/config directories;
they remain sensitive because they contain user content. Restore imports only
validated values into the fixed office schema, holds the target writer lock and
never overwrites an existing directory. There is no remote service, server
autostart, arbitrary shell, imported SQL, extraction path or overwrite API.
Same-user processes that can replace program code or alter the private workspace
are not a sandboxed adversary; these checks do not claim OS-level isolation.

Office-only provider preflight refuses URL userinfo, queries, fragments and
control characters before creating/opening the workspace, so those credential
carriers cannot enter its send preview or model snapshot. Use the existing
credential environment variable instead; the legacy profile parser is unchanged.


### Office project guidance admission

Logical project identity is not native service authentication. Project preference
CAS and whole-memory-store digests prevent stale saves; no request may choose a
memory path. Only original office proposal events are eligible as explicit
non-factual guidance. Accepted legacy records, source facts and verification labels
never grant authority. Replacement acceptance retires its predecessor in one locked
publication; backups validate normalized lifecycle edges even after rehashing.
Forgotten historical IDs confer no recall and never recreate text. Forget does
not promise to remove earlier traces, sent payloads, exports or backups. At this
checkpoint these controls are independent of the not-yet-wired prepared send path.
