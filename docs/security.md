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
- Keys come from the environment and nothing else. A desktop keychain is
  the planned second source (M5); there is no third.

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

## What is written where

The evidence the package writes lives inside the workspace the commands run
in: `sessions/`, `traces/` (with `traces/requests/`), `comparisons/`, and —
only when asked — `index.sqlite`. The one deliberate exception is
`export --out`, which writes wherever the operator points it. Paths inside
the records are relative to the workspace; the JSON records
(`workspace`, `history`, `search`, `diff`) carry no absolute path. There is
no telemetry, no default upload, no silent default endpoint: a provider is
used because a profile named it.

## Not yet (and therefore not promised)

The local UI (M4) does not exist yet. When it does, it is specified to
listen on loopback only and to require a short-lived token; until that
milestone lands, there is no network surface to secure beyond the provider
requests the operator configures. Windows support and the desktop
keychain follow in M5.
