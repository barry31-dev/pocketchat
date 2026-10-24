# Changelog

All notable changes to `pocketchat` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The versions below describe the public source tree. They do not describe a
package-index publication.

## [0.3.0] - 2026-10-24

The v0.3 workspace model: an identity layer on the session file, branching,
read-only reconstruction and queries, selection records, a derived index, and
format documentation. Trace events are untouched, so no previously released
artifact changes shape.

On 2026-10-24 the whole loop ran once against a real provider -- the local
cc-switch free aggregate route (`auto` and `qd-Qwen3.8-Flash`): a real
two-turn conversation, a verbose two-profile comparison seeded from it, a
selection written into a session, a real continuation of that session, a
branch derived from it and continued, exports, all five traces validated, a
request snapshot replayed dry-run, and a zero-hit credential scan over every
artifact. That evidence covers only those route inputs on that day; the
stub-backed suite remains the regression net.

### Added

- Session files record a `conversation_id` and a `branch_id` in the header, and
  a `turn_id` on every message line. The ids are minted when the file is
  created and read back when it is resumed, so one conversation keeps one
  identity across the runs that append to it.
- Turn ids are derived from a message's position in the file, so two readers of
  the same bytes name the same exchange without consulting a counter.
- `pocketchat show` prints the conversation and branch ids, and the turn id of
  each message.
- `pocketchat branch SESSION --from turn_NNNNNN [--name NAME]` derives a branch
  from one completed turn and writes its session file. The child keeps the
  parent's conversation id, carries the turns it inherited with their turn ids,
  and records `parent_branch_id` and `parent_turn_id` so a later reader can find
  its way back. The parent file is never written to.

### Reconstruction

- `pocketchat workspace [PATH] [--json]` reads a workspace back as a graph:
  which session files are branches of one conversation, which turn each branch
  forked from, which run and trace each file records, and which runs each
  comparison manifest measured. It is read-only -- no socket, no configuration,
  no credential, no byte written -- so the same files give the same graph in
  two processes, and every path in it is relative to the workspace.
- The graph reports what the files do not say instead of filling it in: a
  comparison manifest records no selection, so `selection` is `not recorded`;
  traces and request snapshots that no file claims are listed as unlinked;
  links that point at an absent branch, turn, trace, snapshot or base session
  are listed as unresolved; two files claiming one run id or one trace are
  listed as a conflict; a file that is not a session or a manifest is listed as
  unreadable. Nothing is skipped silently.
- v0.1 and v0.2 session files read as they are: one conversation per file with
  `conversation_id` reported as `not recorded`, rather than several unrelated
  files merged into a conversation nothing links.

### Format versions

- New session files carry a `schema_version` in the header (`"1"`), naming
  the file's format — distinct from `pocketchat_version`, which names the
  program. A file without the key is a legacy v0.1/v0.2 file and reads
  unchanged, byte for byte; a file with an unknown schema version was
  written by a newer pocketchat and is refused with the version named,
  everywhere sessions are read.
- `docs/security.md` (and `docs/security.zh-CN.md`) consolidates the rules
  the package enforces about credentials and the evidence it writes, each
  traced to the module that owns it.
- `docs/formats.md` (and `docs/formats.zh-CN.md`) describes every file the
  package writes — session, trace, request snapshot, comparison manifest,
  selection record, derived index — with its version key, fields and the
  migration rules: old files read unchanged and are never backfilled,
  migration means a new file beside the old one, and an unknown version is
  refused, never guessed at.

### Queries

- `pocketchat history [TARGET] [--json]` lists the turns the sessions in a
  workspace record, grouped by conversation and branch. A target narrows the
  answer to one session, conversation or branch. A v0.1/v0.2 file is listed
  with the turn ids its positions name and no invented identity.
- `pocketchat search TEXT [--session NAME] [--json]` finds literal,
  case-sensitive text in the sessions' message lines. A hit names the file,
  conversation, branch and turn it was found in. Zero hits is a result
  (exit 0); an empty text is refused.
- `pocketchat diff SESSION_A SESSION_B [--json]` compares two sessions turn
  by turn: the common prefix, then the turns each side adds after the first
  disagreement. When one side records the other as its parent, the recorded
  fork point is checked against the prefix the words support, and a
  disagreement is reported rather than resolved.
- All three are read-only: no configuration, no credential, no socket and no
  byte written; every path is relative to the workspace, and the same files
  give the same answer in any process.

### Selection

- `pocketchat select COMPARISON RUN_OR_PROFILE [--note TEXT] [--json]` records
  which answer of a comparison was kept, in
  `comparisons/<comparison-id>.selection.json` (exclusive-create). The
  manifest itself is never rewritten, and a second selection of one
  comparison is refused: the first decision is evidence too.
- `--into SESSION` appends the selected prompt and reply to a session,
  starting one if absent, with its own run id and trace. It writes only what
  the run's trace records as complete: a comparison that ran without
  `--verbose` carries a summary, and a summary written into a session would
  be read as the whole answer, so it is refused. Everything is checked before
  the first byte is written; a refused selection leaves the workspace
  byte-identical.
- `pocketchat workspace` reports the selection when one exists and still says
  `not recorded` when there is none.

### Index

- `pocketchat index [PATH] [--rebuild] [--json]` builds or inspects
  `index.sqlite`, a derived index over the workspace's evidence files: one
  row per conversation branch, comparison, comparison run and problem the
  reconstruction reports, with the sha256 of every source file beside them.
  The index holds no fact of its own and no command reads it; delete it and
  every answer is unchanged.
- A build is always whole -- there is no incremental update -- and replacing
  an existing index requires `--rebuild`. A build that fails halfway removes
  the half it wrote rather than leaving a file that looks like an answer.
- `index` without `--rebuild` checks the index against the workspace and
  reports `fresh`, or `stale` at exit 1, naming every file the two disagree
  about. Two builds over the same bytes produce byte-identical files on the
  SQLite this was verified with (the index carries no clock); the promise
  that holds everywhere is logical equivalence -- the same sources give the
  same rows.

### Changed

- The README is the overview and the quickstart again; the command reference
  (comparisons, request snapshots, replay, sessions, branches, the workspace
  graph, the queries, the index, show, export, the trace) moved verbatim to
  `docs/cli.md` and `docs/cli.zh-CN.md`. The security rules are consolidated
  in `docs/security.md` / `docs/security.zh-CN.md`, and the file formats in
  `docs/formats.md` / `docs/formats.zh-CN.md`.

### Compatibility

- v0.1 and v0.2 session files read unchanged. A file with no `conversation_id`
  or `branch_id` reports `not recorded` rather than an invented value, and no
  existing file is rewritten: reading never adds the keys it did not have.
- The `jsonl` and `md` exports keep their existing contracts and now carry the
  turn ids the session file itself carries. The event-only trace export is
  byte-identical to its source.

## [0.2.0] - 2026-10-20

The multi-profile comparison and replay layer. The local implementation is
complete enough to exercise with a stub. A separately recorded local
free-route run and two-profile comparison completed on 2026-10-20; that evidence
covers only the recorded route inputs and does not replace the stub-backed
test suite.

### Added

- Configuration schema v2 with a `profiles` map, `default_profile`, optional
  per-profile input/output prices, and in-memory migration of v1 configurations.
- `pocketchat profile list` and `--json`, showing profile names, models, base
  URLs, credential environment-variable names and whether each variable is set.
  Credential values are never printed.
- `--profile NAME` on `run` and `chat`, resolved before any trace is created.
  Unknown profiles and profiles with missing credentials fail closed.
- `pocketchat compare PROMPT --profiles a,b`, running profiles serially in the
  order named. Every profile gets its own run id, trace and request snapshots;
  one profile failing does not cancel the others.
- Request snapshots under `traces/requests/<run-id>.turn-N.json`, written before
  the socket opens from the same payload builder the HTTP request uses. The
  snapshot contains no Authorization header or credential.
- Canonical request serialization and SHA-256 verification. The trace records
  both the snapshot path and digest on `model.request.started`.
- `comparisons/<comparison-id>.json` manifests that index each run's trace,
  request snapshot, status, duration, usage, summary and error without copying
  trace content. Manifests and snapshots are created exclusively.
- Cost from configured per-million prices and duration read back from the run's
  own request/response events. Missing prices report `unknown`; no token usage
  is estimated.
- `pocketchat replay <run-id-or-snapshot> --dry-run`, verifying one explicit
  snapshot or every turn in a run without reading config or credentials and
  without opening a socket.
- Human-readable and `--json` output for profile lists, comparisons and replay.

### Changed

- `pocketchat run` prints the assistant reply to stdout on success while the
  trace remains the separate evidence artifact.
- A resumed run continues its trace's event numbering and request numbering
  instead of colliding with files and ids from the earlier invocation.
- The terminal trace event is written after the conversation file, so an
  interrupted write leaves a recoverable "unknown ending" rather than a session
  exchange the trace never saw.

### Fixed

- Resumed sessions now write request snapshots with continued turn numbering.
- New run ids use one lowercase timestamp separator across stdout, the session
  header, the trace filename and trace events.
- A conflicting explicit `--trace` on resume is rejected before writing.
- `run` and `chat` help text no longer promises a default session file they do
  not create.
- Session path read/write failures are reported as usage errors instead of bare
  tracebacks.
- A timeout wrapped by `urllib` as `URLError` is recorded as `timeout` instead
  of being misreported as `connection_error`.
- A comparison seeded with `--session` now records that session file in the
  manifest's `base_context`. The message and character counts alone could not
  be checked against anything, because nothing said which conversation they
  came from.
- A request snapshot whose body contains the configured credential is refused
  instead of written. The body is the operator's own words, so a quoted key
  would otherwise reach the one artifact that is kept and replayed. The
  request still goes out and the run completes, but `model.request.started`
  carries no snapshot reference and an `error.recorded` event with
  `credential_in_request` records why.
- A provider error body is now checked against the credential this run sent,
  rather than against a list of shapes. A 401 that echoes the rejected key is
  recorded with the value replaced and the provider's own wording intact,
  whatever form the echo takes: after a colon, nested under an unrelated
  field, with a suffix appended, or repeated. Two shapes used to be recognized
  and the credential reached the trace -- and through it the comparison
  manifest -- verbatim in every other one.
- The `trace_events` count reported by `export` for the mixed JSONL and
  Markdown formats now counts the events actually written instead of the
  newlines in the trace text. A blank or malformed trace line was counted as
  evidence the export never copied.
- The README no longer describes the bundled `trace.schema.json` as
  byte-identical to the published one. It is the published schema plus two
  documented `model.request.started` payload fields, so what it shares with
  the published file is the event set, the required fields and the event
  types.
- `compare --help` no longer advertises `--profile` and `--trace`. They come
  from the option block shared with `run` and `chat`, and neither means
  anything to a comparison: the profiles come from `--profiles` and each run
  writes its own trace. Passing either one still exits 2 before the first
  request rather than being dropped -- an operator who passed `--profile`
  would believe the comparison went to that profile when it ran the ones
  `--profiles` named. They are hidden rather than made unrecognized so the
  refusal still names the conflict instead of a generic usage error, and
  `run` / `chat` keep listing both.
- `show` and `export` help text no longer claims a default for their required
  positional argument. The argument is required: leaving it out exits 2 and
  reads and writes nothing, so "default: ./sessions/<id>.jsonl" described a
  value the parser never produces and an `<id>` the operator could not know.
  The text now names what the argument accepts -- a session file, the trace it
  recorded, or a bare name read from `./sessions`.

### Not verified

- Automated tests use a local stub HTTP server; the recorded real-route run and
  comparison are not part of the automated suite.
- No package-index publication has been made.

## [0.1.0] - 2026-10-20

The single-profile foundation. The local implementation and stub-backed tests
exist. One separately recorded local free-route run completed on 2026-10-20;
that evidence covers only the recorded route input.

### Added

- A `pocketchat` package requiring Python 3.9+ and no runtime dependencies
  beyond the standard library.
- `pocketchat init`, fail-closed configuration loading, `run`, `chat`, `show`
  and `export`.
- A provider-neutral OpenAI-compatible HTTP kernel over `urllib`, with context
  accumulation, explicit truncation and one request per turn. There are no
  retries, fallback models, tools or bundled provider SDK.
- Trace recording in the Agent Trace Doctor `trace.schema.json` v1 shape, with
  the schema shipped inside the package and one terminal event per run.
- Session persistence and resume through `--session`; session files hold the
  conversation while traces hold the events.
- Mixed JSONL and Markdown conversation exports, plus an event-only trace export
  that preserves event ids, timestamps, order and payloads.
- Credential hygiene across traces, sessions, snapshots and provider error
  bodies. Credentials are read only from environment variables.
- English and Chinese READMEs covering the same commands and boundaries.

### Fixed

- Blank REPL lines are skipped instead of becoming empty prompts.
- Failed turns stay out of the conversation context and session file.
- Ctrl-C produces `run.cancelled` whether it lands during a request or while
  waiting for input.
- Context overflow fails with `context_too_large` instead of sending a request
  beyond the configured budget.
- `show` and `export` distinguish an invalid file from a session with no events.

### Not verified

- Tests still use a local stub HTTP server and fake credentials; the separately
  recorded real-route run is not part of the automated suite.
- The project has not been published to a package index.
