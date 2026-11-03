# Compatibility and deprecation policy (v1.0)

PocketChat is a CLI-only product. This page is the compatibility promise for
the `v1.0.x` line; it does not promise behavior that is outside the CLI.

## Supported runtime matrix

| Axis | Supported in `v1.0.x` | Verification |
|---|---|---|
| Python | CPython 3.9 and 3.13 | Local 3.13 full suite passed 2026-10-29; local 3.14 also passed; 3.9 remains unverified until a runner reports it |
| Operating system | Linux, macOS, Windows (runtime target) | Current local verification is macOS 3.13/3.14; CI workflow defines the other matrix cells but no remote result is claimed |
| Shell | POSIX shell and PowerShell/`cmd.exe` for ordinary commands | CLI avoids shell-specific parsing; only locally exercised shell paths are verified |
| Provider protocol | OpenAI-compatible `/chat/completions` over HTTP(S) | Stub-backed local HTTP tests; real-route evidence is recorded separately and is not generalized |

Python versions and operating systems not listed above are not claimed for
`v1.0.x`. A new runtime must pass the same full test suite before it is added
to this table.

## Stable CLI surface

The following are stable within `v1.0.x`:

- Command names: `init`, `doctor`, `run`, `chat`, `agent`, `show`, `context`, `tool`, `memory`, `delegate`, `export`,
  `branch`, `checkpoint`, `rewind`, `profile`, `compare`, `replay`, `workspace`,
  `history`, `search`, `diff`, `select`, `index`.
- Exit code classes: `0` success, `1` a run started and failed or was
  cancelled, `2` usage/configuration/refusal before a run.
- JSON envelopes for commands with `--json`: one object on stdout with
  `ok` and `schema_version`; failures put no diagnostic text on stderr for
  the documented JSON paths.
- Artifact references in JSON are workspace-relative unless the command's
  documented exception says otherwise.

Adding a command is an additive minor change. Removing or renaming a command,
changing an exit-code class, or changing the meaning of a documented JSON field
is a breaking change and requires a new major version.

## File formats

The following formats are versioned and readable across `v1.0.x`:

| Artifact | Version key | Rule |
|---|---|---|
| Session | `schema_version: "1"` | Old files remain readable; completed assistant lines may carry additive `tool_context`; unknown newer versions are refused |
| Trace | Agent Trace Doctor schema `"1"` | Event shape is not silently rewritten |
| Request snapshot | `schema_version: "1"` | Exact body, no headers, hash checked by replay |
| Comparison manifest | `schema_version: "1"` | Exclusive-create index of evidence |
| Selection record | `schema_version: "1"` | One selection per comparison |
| Derived index | `schema_version: "1"` | Disposable; rebuild required when stale |
| Local memory | `schema_version: "pocketchat-memory.v1"` | Explicit records; corrupt/unknown stores refused; never auto-injected |
| Delegation manifest | `schema_version: "pocketchat-delegation.v1"` | Serial bounded children; generated exclusively; no recursive delegation |
| Context projection sidecar | `schema_version: "pocketchat-context-projection.v1"` | Enabled per profile; metadata-only, created exclusively beside request snapshots, bounded to 64 KiB |
| Tool writes | `write_file` event | Explicit allowlist, UTF-8, atomic, overwrite opt-in; no delete/shell/network |
| Automatic tool calls | `read_file` / `write_file` / `memory_search` | Disabled by default; explicit allowlists; bounded calls/results; guard/argument failures return bounded tool results, while I/O, size and budget failures end the run |
| Automatic write tool | `write_file` create-only | Disabled by default; explicit path allowlist; no overwrite/delete |
| Automatic memory tool | `memory_search` read-only | Disabled by default; bounded results; no memory mutation |
| Checkpoint manifest | `schema_version: "pocketchat-checkpoint.v1"` | Boundary digest must match before rewind; unknown versions refused |
| Rewind transaction | `schema_version: "pocketchat-rewind.v1"` | Prepared/completed state records crash recovery; source evidence never rewritten |

Migration means writing a new file beside the old one. Existing evidence is
never rewritten in place. A reader that sees an unknown format version refuses
it and names the version; it never guesses.

## Added in v1.1 (Task Run)

These artifacts are new in v1.1 and are not part of the `v1.0.x` promise above.
They are additive: a v1.0 workspace has none of them, and v1.1 reads such a
workspace unchanged.

| Artifact | Version key | Rule |
|---|---|---|
| Task policy | `schema_version: "pocketchat-task-policy.v1"` | Operator-supplied; closed fields at every level; unknown field or version refused |
| Task record | `schema_version: "pocketchat-task.v1"` | Exclusive-create, append-only through an atomic replace; closed state machine; terminal states cannot restart |
| Task checkpoint binding | `pocketchat-checkpoint.v1` + `task_id` / `step_id` / `policy_sha256` | Three additive fields, all present or all absent; task resume refuses legacy, partial or conflicting bindings |

A task record indexes evidence rather than duplicating it, so its `run_id` and
`checkpoint_id` references stay subject to the trace and checkpoint rules above.

## Deprecation

A deprecation is announced in CHANGELOG and the command manual before removal.
During `v1.0.x`, a deprecated command or field remains functional for at least
one minor release. Removing it requires a major version unless it is required
to close a credential leak, path escape, or evidence-corruption defect; that
exception is documented in the security page and CHANGELOG.

## Out of scope

Web UI, local API, desktop packaging, remote access, account systems,
telemetry, general-purpose tool calling, file access outside the explicit local
tools, code execution, recursive multi-agent orchestration and provider SDK
bundling are not part of the `v1.0.x` compatibility promise.

## Unreleased: additive task acceptance

`task verify`, opt-in `task run --acceptance`, and `task report.acceptance` are
additive. Existing runs without the flag keep their fields and exit meanings;
opt-in runs require execution completion **and** acceptance pass for `ok`/exit 0.
Task execution states and all task policy/record/checkpoint/trace v1 shapes stay
unchanged. Acceptance plans and receipts have separate version keys and unknown
versions fail closed. An old workspace without receipts reports `not_recorded`,
not `passed`. The new verifier needs POSIX descriptor-relative no-follow APIs;
Windows currently refuses with `unsupported_platform`, rather than inheriting
the ordinary CLI's runtime target. These are unreleased source additions, not
retroactive v1.1.0 artifact guarantees. See [task-acceptance.md](task-acceptance.md).
