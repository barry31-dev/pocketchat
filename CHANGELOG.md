# Changelog

All notable changes to `pocketchat` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The versions below describe the public source tree. They do not describe a
package-index publication.

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
