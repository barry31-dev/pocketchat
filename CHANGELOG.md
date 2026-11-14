# Changelog

## Unreleased — local office workspace

- Add authenticated loopback browser chat with selected text material, explicit
  provider disclosure, versioned report revisions, optional DOCX export, durable
  conversations and bounded cancellation. Existing CLI commands remain intact.
- The offline browser fixture is not real-provider quality or release evidence.


All notable changes to `pocketchat` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The versions below describe the public source tree. They do not describe a
package-index publication.

## [Unreleased]

- Add explicit project-memory candidate creation through `memory propose`, with separate acceptance and source/revision-bound opt-in Session recall. Initial writes refuse concurrent publication, non-regular targets, duplicate identities and malformed UTF-8 without replacing the store. Add an offline continuity fixture covering unaccepted, accepted, foreign-workspace, expired and retired records; this does not add automatic capture, model approval or secure deletion.
- Bind code-repair requests to authorized source snapshots, preserve successful retry and original failure evidence for exact recovery, and add an offline Swift Package repair fixture.
- Add a completed-read snapshot handoff into dependent task report writes, binding each Session result to the pinned read-trace completion and the task-record workspace to its authorized policy (see `docs/review-local-report-v1.json`). Add an offline real-file CLI report/revision/resume fixture; versioned outputs preserve report history, and unusable source evidence refuses before dependent provider work. This does not add tools, overwrite authority or certify real-provider analysis.
- Prepare the version 1.2.0 chat-and-file candidate using the existing package/runtime version sources. It includes explicit Session file grants and provider disclosure, proposal-only model tools, trusted complete-diff apply/readback, persistent single-change undo, cancellation ownership and an offline real-file golden fixture. Legacy immediate `write_file` contracts are not widened. Release facts remain in `docs/release-ledger.md`; development-wheel evidence does not certify real-provider/manual acceptance or formal publication.
- Enable terminal mouse-wheel events in the full-screen agent and map wheel up/down to one-line transcript scrolling. Page keys and non-wheel mouse clicks retain their existing behavior. See `pocketchat/curses_ui.py`.
- Add a pure, deterministic evidence-link verifier for supplied artifact observations and explicit relations. It returns bounded `complete`, `partial` or `refused` reports, keeps digest scopes distinct, rejects identity/path/duplicate/orphan/budget defects, and deliberately performs no file, network or provider I/O. See `pocketchat/evidence_links.py` and `docs/evidence-links.md`.
- Treat malformed UTF-8 in a session file as the existing typed `session_file` refusal. `show --json` now returns one stable error object with exit 2 and no traceback while leaving the input bytes untouched; malformed JSON files retain their previous not-a-session classification.
- Prevent v2 memory reads and writes from following a replaced parent directory. Reads now use a no-follow parent descriptor with relative file open, and a corrected regression test exercises the parent replacement race without removing a non-empty directory.
- Declare `read_file`, `write_file` and `memory_search` through one closed tool-contract module shared by the provider schema, trace summary, task policy and host argument validator. Unknown or malformed arguments now fail before file or memory I/O while disabled tools remain absent from the default payload. See `pocketchat/tool_contracts.py`.
- Bind task checkpoints to optional context/recall artifacts and persisted budget ledgers, and validate projection sidecars during dry-run replay. Historical replay uses the saved request snapshot and never re-searches current memory; missing or changed bound artifacts refuse before resume.
- Add explicit v2 memory recall dry-run and opt-in context projection wiring. The command is read-only and workspace-bound; Session injects one bounded recall message only when both layered projection and `memory_recall_enabled` are explicitly enabled, while legacy `memory search` remains unchanged. See `docs/memory-retrieval.md`.
- Add explicit v2 memory CLI operations: `memory migrate` creates a bounded
  new store without rewriting v1, `accept` requires an approver and never
  invents verification, `retire` records lifecycle metadata, and `forget`
  physically removes a record from the active v2 store. The default v1
  `add/list/search/remove` behavior is unchanged. See `docs/cli.md`.
- Add a pure deterministic memory-v2 retrieval core. It gates workspace, lifecycle and sensitive records before fixed English/CJK lexical ranking, bounds excerpts by byte and estimated-token quotas, and reports stable selection, omission and stale-cursor behavior without changing legacy search or wiring a CLI. See `pocketchat/memory_retrieval.py`.
- Make explicit memory locking portable across Windows and POSIX. Windows
  uses `msvcrt` byte-range locks while POSIX retains `fcntl`, both are loaded
  only on their supported platform, and a failed lock attempt is not treated
  as held during cleanup.
- Keep task-state artifact references portable across Windows and POSIX, and
  gate recovery-resume regressions on the documented secure-reader support
  instead of treating `unsupported_platform` as a product failure.
- Add a closed memory v2 record parser and a pure v1-to-v2 migration. The
  migration leaves the v1 store unchanged, writes a bounded v2 file
  exclusively, refuses unknown v1 fields and unsafe targets, and marks legacy
  records as unverified candidates rather than inventing sources. The v2 file
  is not yet a live CLI store. See `pocketchat/memory_migration.py` and
  `docs/formats.md`.
- Harden the runtime admission candidate after an offline pre-merge review:
  known usage is charged against whole-task headroom rather than just the input
  estimate, missing usage blocks tools/repair/retries, and output reservations
  include the configured allowance. Memory results select whole records against
  the final JSON envelope instead of forcing one oversized match. The local
  context projector rejects orphan and out-of-order tool results.
- Persist the optional whole-task budget ledger in execution reports and
  restore it for report/checkpoint resume. A missing, malformed or mismatched
  ledger is refused before a new request; no fresh allowance is invented for
  an already-started budgeted task. Unbudgeted resume remains supported.
- Add an opt-in deterministic layered context projector. It keeps fixed
  instructions and task state ahead of recent complete exchanges and declared
  evidence, deduplicates evidence sources, refuses orphan tool exchanges and
  fails closed when the fixed layers cannot fit. Session writes a metadata-only
  sidecar beside the exact request snapshot, and `context` previews the same
  history selection. The legacy drop-oldest path remains the default. See
  `pocketchat/context_projection.py`, `pocketchat/session.py` and
  `docs/formats.md`.
- Add a pure task-state projector for cross-step context. It includes only
  completed, explicitly depended-on steps and declared file evidence, excludes
  unrelated steps, and refuses missing digests, unresolved evidence, malformed
  paths or oversized projections. Execution is not wired to it yet. See
  `pocketchat/task_state.py`.
- Account model-repair usage against the same whole-task budget as execution.
  The repair request reserves before its provider call and settles reported
  usage; an exhausted budget leaves the target file and retry untouched, and
  the existing one-repair/one-retry authority is not reset. See
  `pocketchat/task_model_repair.py`.
- Bound TaskPlan generation by the optional task budget. Plan attempts reserve
  before their provider request and settle reported usage; an over-limit
  attempt is refused before any request snapshot or provider call. Budgets
  remain on the TaskSpec and are not copied into the generated TaskPlan. See
  `pocketchat/task_plan.py`.
- Wire the optional whole-task token budget into `task exec`. One ledger is
  created per authorized plan and shared by its steps; model requests reserve
  before sending and settle from reported usage. Missing usage remains unknown
  and fails closed when this limit is active. Existing per-turn and per-step
  Session limits are unchanged. See `docs/task-spec.md`.
- Add a pure task-level token-budget ledger. It reserves before a request,
  settles a request at most once, retains unknown usage as an explicit unknown
  reservation, and serializes a versioned state record for later wiring. The
  Session and task runner do not consume this ledger yet. See
  `pocketchat/task_budget.py`.
- Add explicit model-capacity fields to profiles: context window, output
  reserve, safety margin and whether the endpoint accepts a max-token cap.
  Known windows reduce the admission limit; unknown values remain unknown.
  `max_tokens` is sent only when the profile explicitly declares support. See
  `pocketchat/request_admission.py` and `pocketchat/config.py`.
- Make explicit memory mutations transactional within one process and fail
  closed across writers: add/remove hold one lock over read-modify-write, use a
  unique temporary file, reject a symlinked store, and report a held lock as
  `memory_store_busy` instead of silently losing an update. See
  `pocketchat/memory.py`.
- Harden the explicit local memory store before reading it. Reads now refuse
  symlinks and non-regular files, cap the complete store and each JSONL line,
  and keep oversized or corrupt stores explicit instead of treating them as
  empty. See `pocketchat/memory.py`.
- Bound live tool results before they return to the model. File reads acquire
  only a limited prefix, write confirmations omit the full content echo, and
  memory search drops whole records with an explicit omitted count instead of
  failing the run. See `pocketchat/session.py` and `pocketchat/tools.py`.
- Add a pure bounded projector for tool results sent to a model. It limits
  the complete JSON envelope in UTF-8 bytes, records omitted bytes, supports
  omitting a file-write content echo, and refuses metadata that cannot fit.
  This slice does not yet change the live tool-call path. See
  `pocketchat/tool_results.py`.
- Admit model-authored repair requests before their snapshots and provider
  calls. An oversized repair request leaves the target file unchanged, creates
  no edit and performs no command retry; the existing repair authority and
  single-retry bound are unchanged. See `pocketchat/task_model_repair.py`.
- Admit both TaskPlan generation requests before their snapshots and provider
  calls. An oversized corrective request now fails before a second model call,
  while successful generation still executes no task step. See
  `pocketchat/task_plan.py`.
- Enforce request admission at every Session model-request boundary. Session
  now counts the complete payload before writing its snapshot, covers requests
  after tool results and retry/fallback attempts, and hands the admitted body to
  the provider instead of letting it rebuild a second payload. Profiles gain an
  additive `max_payload_bytes` limit (default 1 MiB); the existing
  `max_context_chars` unit is unchanged. See `pocketchat/session.py` and
  `pocketchat/request_admission.py`.
- Add a pure request-admission check for complete provider payloads. It
  counts the canonical payload bytes, serialized messages and tool schemas,
  exposes an explicitly identified token estimate, and refuses oversized input
  before any caller can send it. This slice does not yet wire admission into
  the Session or provider path. See `pocketchat/request_admission.py`.
- Mark package independence delivered. Public docs stay inside the package,
  the product queue and contributor rules are reachable, no source module
  reads the control repository, and the CI workflow declares Bash for its
  output-file steps. Evidence: `tests/test_package_independence.py`.
- Add read-only `task exec --check-acceptance`. It validates the operator plan
  before provider configuration and evaluates it against the exact execution
  report from that invocation, while keeping execution status separate from
  acceptance and creating no historical receipt. See `docs/task-exec.md`.
- Add a repository-owned release smoke gate and CI job. It builds one real
  wheel, validates required package/schema/surface members and removed
  web/server paths, installs into a clean venv, checks the full installed task
  command surface, and uploads JSON plus the wheel as an Actions artifact. See
  `scripts/release_smoke.py` and `docs/development-and-release.md`.
- Add `task checkpoint create` so a CLI caller can derive a closed checkpoint
  from one exact digest-bound execution report without Python. Creation is
  read/derive/write only: it executes no step or command, calls no provider,
  refuses reports outside `.pocketchat/task-reports/`, writes exclusively,
  and reads the checkpoint back for equality. See `docs/task-checkpoint.md`.
- Add CLI step-boundary continuation for `task exec`. `--stop-after` records an
  intentional boundary and exits 0; `--resume-report` plus `--report-sha256`,
  or `--checkpoint`, re-verifies the completed prefix and executes only the
  unfinished suffix. Report/plan/checkpoint mismatches are refused before a
  new provider or command call. See `docs/task-exec.md`.
- Add `task plan generate` as the product CLI path from one TaskSpec to a
  validated TaskPlan. It uses the configured provider with at most one
  revision, writes exclusive request/plan snapshots, persists a plain plan,
  reads it back against the same TaskSpec, and prints the exact `plan_sha256`
  accepted by `task exec`. It never executes a step. See `docs/task-plan.md`.
- Add opt-in model-authored command repair. `task exec --repair-model`
  binds one model action to the authorized TaskSpec, TaskPlan and original
  template; the action may replace only an `overwrite_paths` file through the
  guarded editor, followed by exactly one identical template retry. The model
  cannot add argv, shell, cwd, tools or provider authority. Repair receipts
  keep `task_acceptance_pass: false`; the opt-in request snapshot is bound by
  digest and never stores the replacement body. See `docs/task-repair.md`.
- Add `task exec` as the product CLI entry for an explicitly authorized
  TaskSpec and TaskPlan. It validates both documents and the plan digest before
  reading provider configuration, executes steps through the existing bounded
  task runtime, preserves an execution report on failure, and never turns a
  command exit 0 into task acceptance. See `docs/task-exec.md`.

- Add checkpoint-driven resume. `resume_from_checkpoint()` resolves one
  checkpoint into the workspace-relative report and digest to resume from,
  refusing a changed report digest, a report outside the reports directory or
  a plan-digest mismatch; `task checkpoint resume` previews it read-only. The
  resolved report and digest feed the existing `execute_plan` resume path.
  See `docs/task-checkpoint.md`.

- Add parent-plan-authorized delegation. A closed delegation request is checked
  against the authorized ExecutionPlan and TaskSpec: the parent plan digest and
  step must match, a child may only use a granted action and inputs inside that
  action's allowlist, and `task delegate preview` authorizes read-only without
  running a child. See `docs/task-delegate.md`.

- Add explicit plan/command checkpoints derived from a bounded execution
  report. A record binds spec/plan digests, the completed/pending step
  partition, bounded file and command effect hashes and the source report
  digest; `task checkpoint preview` validates one read-only and can bind it to
  the execution being resumed. See `docs/task-checkpoint.md`.

- Add bounded UTF-8 content snapshots over an explicit frozen context selection.
  The command rederives and verifies selection provenance before opening only
  its selected regular files, records per-file and canonical snapshot hashes,
  and refuses path, encoding, sensitivity, size and budget violations without
  writing runtime state or calling a model. See `docs/task-snapshot.md`.

- Add deterministic path-token selection over a frozen bounded context-tree
  manifest. The selector strictly validates and digests the manifest, explains
  exact basename/path scores and truncation, and never opens candidate files or
  calls a model.

- Add a read-only `task context scan` command for deterministic, bounded
  workspace-relative file metadata. It prunes runtime/generated directories,
  never follows symlinks, reads no file contents and refuses partial results
  when a file/depth/path budget is exceeded.

- Replace scripted dogfood shortcuts with actual tool writes, fixed-template
  bounded repair, report-bound continuation and independent acceptance. The
  offline stub, operator-authored repair and file-only repair acceptance are
  explicit; refusal no longer invents a trace or an unrelated plan identity.

- Add explicit plan-step boundaries and hash-bound continuation. Completed file
  and command steps retain their artifacts and are not replayed; incomplete,
  conflicting or unconfirmed evidence refuses before new calls. Cumulative
  execution reports retain their predecessor link without double-counting.

### Added

- Offline task-agent dogfood demo covers TaskSpec/TaskPlan execution, one
  bounded command repair, recovery inventory and invalid-spec refusal in a
  fresh temporary workspace. It records spec/plan/report hashes and never uses
  a real provider. See `examples/task-dogfood/README.md`.
- Read-only recovery side-effect inventory: completed command and file steps
  are derived from existing execution reports, failed/not-executed steps remain
  pending, and foreign/malformed evidence is refused or unknown. It does not
  rerun commands or resume a provider turn. See `docs/task-recovery.md`.
- Acceptance plans can cite an existing `command_succeeded` step from a
  bounded execution report. The reader validates the completed/non-timeout
  evidence and never reruns the command; missing, foreign or unreadable
  evidence is `unknown`/`refused`, and a nonzero exit is `failed`.
- Bounded command repair: one declared template may be rerun at most once after
  an explicit repair decision, with a sibling trace and a stable failure
  fingerprint that stops identical retries. Results are persisted once and
  never imply task acceptance. See `docs/task-repair.md`.
- `execute_plan()` now writes one closed
  `pocketchat-task-execution-report.v1` report binding spec/plan digests, task
  ids, step outcomes, verified command archive/stream hashes and counts, and
  write-step file hashes/line counts. A stored command archive digest detects
  byte changes before evidence is attached. `task report` reads it without
  rerunning commands; missing,
  malformed, foreign or tampered command evidence becomes `evidence_issues`
  rather than a pass. See `docs/task-exec.md`.
- Closed TaskSpec command templates and argv execution with `shell=False`, a
  workspace-contained cwd, explicit timeouts and per-stream output bounds.
  TaskPlan `run_command` steps may reference only a declared template id;
  evidence records exit status, hashes and truncated previews, and command
  success remains separate from task acceptance. See `docs/task-command.md`.
  The published v1.1.0 artifact does not contain this capability.
- Guarded TaskSpec overwrite for one existing UTF-8 workspace file. Optional
  `overwrite_paths` must be both declared in `inputs` and write-allowlisted;
  a dirty-target refusal, no-follow symbolic-link check, exact snapshot hash
  recheck before atomic replacement, and bounded hash/line evidence prevent
  blind overwrite. See `docs/task-edit.md`. The published v1.1.0 artifact does
  not contain this capability.
- Deterministic task acceptance: operator-declared `file_exists`,
  `text_contains` and `json_equals` checks, bounded no-follow artifact reads,
  independent task/plan/hash-bound receipts and explicit failed/refused/unknown
  observations. `task run --acceptance` separates execution `completed` from
  acceptance `passed`; `task verify` appends a new observation without a model,
  and `task report` validates historical receipts without rechecking artifacts.
  Existing task/policy/checkpoint/trace v1 shapes and unflagged run semantics
  remain unchanged. See `docs/task-acceptance.md` for platform limitations and
  `examples/task-acceptance/demo.py` for four offline evidence paths.

### Fixed

- Command traces now carry their declared `template_id`, exit code and both
  output SHA-256 values on completed and failed terminal tool events. The
  values are additive trace fields; they do not change run/tool event type
  ownership or turn `exit 0` into acceptance.
- `task report` now links a recorded run to a trace only when a parsed event's
  `run_id` matches exactly. A malformed, unreadable or missing trace is listed
  in additive `evidence_issues` with `refused` or `unknown` state instead of
  being omitted. Task records also reject non-string root evidence fields, and
  `artifacts` contains only completed `read_file` / `write_file` paths rather
  than treating a `memory_search` query as a file.
- `task resume` now checks the task id named on the command line against the
  checkpoint's `task_id` before verifying or writing any recovery evidence.
  A checkpoint belonging to another task is refused without creating the
  resumed session.

## [1.0.0] - superseded CLI-only candidate

### Added

- Stable CLI compatibility policy for commands, exit-code classes, JSON envelopes,
  artifact paths, file-format versions and deprecation. See
  `docs/compatibility.md`.
- Provider adapter boundary: `ProviderAdapter`, default
  `OpenAICompatibleAdapter`, and the adapter name in the run's config summary.
  See `docs/provider-adapters.md`.
- Local CPython 3.9/3.13 test coverage; the cross-platform CI candidate is removed
  by the 2026-10-28 local-only decision.
- Regression coverage for `init --force --json`, provider-failure exit 1 JSON,
  the CLI command inventory and versioned artifacts.
- Local release checklist in `docs/release-1.0.md`; CI matrix and public
  release remain separate gates.

## [1.1.0] - 2026-10-30

### Fixed

- Task record readers now fail closed on malformed evidence: a terminal state
  without a non-empty reason, an active state carrying a terminal reason,
  mismatched filename identity, unknown fields, non-contiguous step indexes,
  invalid step shapes, or steps/tool calls above the declared budgets are
  refused instead of being reported as task facts.

### Added

- `task run`, `task status`, `task resume` and `task report` expose bounded
  task execution, closed-state inspection, checkpoint resume and evidence-only
  reporting with stable JSON envelopes and workspace-relative paths. The
  deterministic `examples/task-demo/demo.py` smoke demo covers success,
  checkpoint resume and permission refusal without a network or credential.
- Task checkpoints bind the existing recovery manifest to `task_id`, `step_id`
  and `policy_sha256`. Task resume validates the policy digest, complete task
  record, checkpoint boundary and ordered tool context before writing, restores
  prepared rewind transactions, and records a recovery step only after the
  resumed branch verifies. Legacy checkpoints without task fields retain their
  existing checkpoint/rewind behavior and cannot be used for task resume.
- `agent.run_task` executes one policy-bounded task through the existing
  session provider and tool path. Task records persist model/tool step
  boundaries, enforce step and tool-call budgets, retain terminal reasons and
  link the session/trace evidence needed by resume. This is the v1.1 Task Run
  execution core; the `task` CLI, checkpoint resume and release packaging remain
  separate work.

### Changed

- Completed automatic tool interactions are retained as additive session
  `tool_context`, including assistant `tool_calls` and matching tool results.
  Same-session continuation, process resume and branch derivation restore them
  before the final assistant reply; tool metadata also counts toward context
  budgets. Existing session schema remains version 1 and old files are not
  rewritten.
- Recoverable automatic-tool refusals now return bounded `status: failed` tool
  results so the model can correct within the existing call budget. Disabled
  or unknown tools, invalid arguments, workspace guard refusals and invalid
  text encodings no longer end the whole run. Underlying I/O/store errors,
  oversized results, exhausted budgets, provider failures and interrupts still
  fail closed. Tool trace events now carry `tool_call_id`.

### Added

- Profiles can optionally offer one automatic `read_file` tool to the model.
  Calls remain workspace-confined and allowlisted, have per-turn and result
  limits, and are recorded with tool/permission events and schema hashes.
- Profiles can optionally offer create-only automatic `write_file` calls.
  Writes are allowlisted, bounded, and cannot overwrite or delete files.
- Profiles can optionally offer read-only automatic `memory_search` calls
  with bounded results. The model cannot modify or delete memory.
- `tool write` adds explicit-allowlist, workspace-confined UTF-8 writes with
  atomic replacement and opt-in `--overwrite`. It provides no delete, shell,
  execute or network operation.
- `delegate run` executes a bounded, serial batch of independent child runs
  with a delegation manifest. It is not recursive agent orchestration and
  does not share a parent session.
- `memory add/list/search/remove` manages explicit local memory records in
  `.pocketchat/memory.jsonl`; memories are never injected into prompts
  automatically, and corrupt stores fail closed.
- `tool read` provides an explicit-allowlist, workspace-confined read-only
  file tool with sensitive/binary path refusal, UTF-8-safe bounded output and
  permission/tool trace events.
- `checkpoint` records a completed session/trace boundary with a message
  digest, and `rewind` creates a new session branch from it without rewriting
  source evidence. Prepared/completed recovery transactions make interrupted
  rewinds retryable and auditable.
- The interactive TUI status rule now reads trace facts after each turn:
  reported token totals, configured-price cost estimate (`cost~`), latest TTFT,
  physical request count (`req`) and context-compaction facts (`cmp`). Missing
  usage, prices or TTFT remain `?`; the display does not invent retry counts or
  provider billing facts.
- `model.response.completed` now carries an additive `usage_reported` boolean:
  true when the provider returned a usage object, false when the zero counters
  are placeholders for missing usage. The existing `usage` keys and their zero
  semantics are unchanged.
- Streamed model responses now record `time_to_first_token_ms`: the time from
  opening the request to the first non-empty text or reasoning delta. The
  field is absent for non-streamed responses and when no delta arrived.

### Added

- `delegate run` executes a bounded, serial batch of independent child runs
  with a delegation manifest. It is not recursive agent orchestration and
  does not share a parent session.
- `memory add/list/search/remove` manages explicit local memory records in
  `.pocketchat/memory.jsonl`; memories are never injected into prompts
  automatically, and corrupt stores fail closed.
- `tool read` provides an explicit-allowlist, workspace-confined read-only
  file tool with sensitive/binary path refusal, UTF-8-safe bounded output and
  permission/tool trace events. No write, shell or automatic tool calling is
  included.
- `context` previews a session's message budget and dry-runs drop-oldest-turns
  compaction without sending a request or writing files. The report includes
  safe message metadata and estimated token counts.
- Schema-2 profiles can configure bounded retry/backoff and one-hop fallback.
  Defaults remain no retry and no fallback; `retry.attempt` and
  `fallback.activated` record actions without claiming recovery succeeded.
- Schema-2 profiles can set `max_turn_tokens` and `max_session_tokens`.
  Requests are checked by local estimate before sending and by reported usage
  after completion; over-budget turns fail without entering context, and
  missing usage under a session budget fails closed.
- `model.request.started`, `model.response.completed` and
  `model.response.failed` now carry additive `attempt_index` and
  `attempt_scope: "turn"` fields. They identify the physical request within
  one turn and pair it with its result; they do not imply automatic retry.

### Fixed

- `compare` now honors `requires_api_key: false` in its up-front credential
  check, so local router profiles without a client key can participate in a
  comparison just as they can in a single `run`.
- `show` now prints a one-line context-compaction summary and `show --json`
  carries the same `context.compacted` payload, so a reader can see how many
  messages were dropped and what the character budget was without opening
  the trace file.
- `model.response.failed` now records `retryable` so a trace reader can tell a
  transient provider failure (408/425/429/5xx) from a permanent one without
  parsing the error message. The CLI still does not retry.
- `context.compacted` trace schema now declares the fields the recorder
  actually writes (`dropped_messages`, `dropped_roles`, `kept_messages`,
  `kept_chars`, `budget_chars`, `estimated_tokens_before`,
  `estimated_tokens_after`). The schema previously named
  `before_messages`/`after_messages`/`before_tokens`/`after_tokens`, which
  were never written. The compaction strategy and event shape are unchanged.

### Added

- `scripts/sync_newnewrouter_models.py`: generate a schema-2 PocketChat config
  from the live newnewrouter `/v1/models` catalog. Generated profiles declare
  `requires_api_key: false`, keeping upstream credentials inside the router.
  The generated default is `pgt` when present, then the first catalog entry.
- Bare `pocketchat` now enters the interactive agent, using
  `./pocketchat.json` or `~/.pocketchat/newnewrouter.json` as its config
  fallback. On a TTY it opens a standard-library full-screen interface with a
  compact intro, compact message gutters and turn separators, a
  persistent status rule, transcript, input history, slash-command completion with active-row selection,
  Up/Down navigation, Tab application and two-step Enter completion, plus a
  profile picker. The TTY surface streams reasoning and assistant deltas,
  shows a live thinking/elapsed status while waiting, keeps reasoning
  collapsed behind Ctrl+T, supports transcript paging, and uses a compact
  status bar; piped input keeps the line REPL. Named
  subcommands remain unchanged.
- `pocketchat agent`: a full-screen interactive chat shell with `/model`,
  `/session`, `/reasoning`, `/context` and `/status`. Every plain message is a
  separate run sharing one session; slash commands create neither messages nor
  trace events. Reasoning is recorded on every run and forwarded only for
  profiles that opt in with `reasoning_effort_supported`.

> **2026-10-28 v1.0.0 CLI-only candidate:** the former `serve`, local API and
> loopback Web UI code is removed from the product tree. The stable CLI surface,
> compatibility policy, file-format versions and provider-adapter contract are
> documented in `docs/compatibility.md` and `docs/provider-adapters.md`.
> The v1.0.0 candidate was superseded by v1.1.0; no v1.0.0 tag or Release was
> created.

### CLI additions

- `init --json`, `run --json`, `chat --json` and the stabilized
  `branch --json` complete machine-readable output across creation, execution,
  conversation and branching commands. Execution objects carry reply and
  workspace-relative trace paths; branch objects omit message bodies. Stable
  error objects go to stdout with stderr empty, while default human output and
  run exit semantics remain unchanged.
- `show --json` and `export --json` provide stable machine-readable output for
  CLI automation. Success objects use workspace-relative artifact paths;
  failures are one JSON object on stdout with exit 2 and an empty stderr.
  Trace-only export reports its actual source and leaves `session` / `messages`
  null. Default human output and all artifact formats remain unchanged.
- `pocketchat profile set NAME --base-url URL --model MODEL --api-key-env ENV`
  creates or replaces a complete profile, `pocketchat profile use NAME` changes
  the default and `pocketchat profile remove NAME` removes a non-default one.
  Writes require configuration schema 2, never accept a credential value,
  validate the complete candidate, preserve the file mode and replace the file
  atomically. After arguments parse, `--json` reports stable success and
  configuration/write-error objects, and a rejected change leaves the
  configuration byte-for-byte unchanged.
- `pocketchat doctor [--config FILE] [--profile NAME] [--json]` checks the
  configuration, selected profile and credential environment variable without
  contacting a provider, opening a socket or writing a file. A ready report
  exits 0; configuration, profile and credential problems exit 2 with a stable
  JSON report or a human-readable next action. Credential values never appear.

## [0.3.0] - 2026-10-24

The v0.3 workspace model: an identity layer on the session file, branching,
read-only reconstruction and queries, selection records, a derived index, and
format documentation. Trace events are untouched, so no previously released
artifact changes shape.

On 2026-10-24 the whole loop ran once against a real provider -- the local
cc-switch free aggregate route (two configured profiles): a real
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
