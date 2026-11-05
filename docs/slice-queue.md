# Slice queue

> Scope: the **current** approval state of product slices. A row here says a
> slice is approved to be worked on; it does not say the work is done, and it
> is not a publication authorization.
>
> This file is the product-side queue for work that is not tracked as a
> repository issue. When a slice is tracked as an issue, the issue is the
> record and this file only links to it — two live copies of one state is how
> a queue starts disagreeing with itself.
>
> Delivery history lives in [`CHANGELOG.md`](../CHANGELOG.md). It does not
> substitute for this queue.

## 1. Row contract

| Field | Meaning |
|---|---|
| `id` | stable slice identifier |
| `state` | `queued` / `ready` / `in_progress` / `delivered` / `cancelled` |
| `branch` | unique `task/<area>-<slug>` branch reserved for the slice |
| `write_scope` | normalized repository-relative paths this slice may write exclusively |
| `depends_on` | slice ids that must be settled first |
| `done_when` | the observable completion condition |
| `evidence` | where the proof lives (commit, trace, test, artifact) |
| `approved_by` | who approved it. **A machine check that this file changed is a precondition, never an approval.** |

`queued` is a pool state, not readiness. `ready` additionally requires that
dependencies are settled and the budget is available.

For a new `queued`, `ready` or `in_progress` row, `branch`, `write_scope` and
`approved_by` are mandatory before a development branch is created. Historical
`delivered` rows below predate this contract and are retained as evidence.

The approved row lands on `main` first. A development branch points to that row
but does not edit this file merely to claim or complete work. Planner commits
may add approved future rows while development branches are active; after a
product branch lands, an integrator records `delivered` in a separate serialized
queue-maintenance commit. This keeps planning and development concurrent without
making every task branch contend on one Markdown file.

This approval queue does not mirror live claim or lease state. A row may remain
`ready` while an external coordinator holds its operational lease; duplicating
that fast-changing state here would create two authorities.

Two active slices may proceed together only when their branches differ, their
dependencies are settled, and their `write_scope` values do not overlap. The
same path or an ancestor/descendant path is a conflict and remains serial.
Documentation, process-tool and test-only maintenance does not create a product
slice.

## 2. Current queue

The runtime rows below were delivered after the hardening review.
`PC-TBUDGET-2A` is the follow-on persisted budget-ledger slice. Evidence is
source/offline and does not claim provider validation or publication.

### Runtime hardening completion check

Goal: close the pre-merge usage, settlement, resume-reset and memory-envelope
findings, and reject orphan/out-of-order tool results in the local projector.
Not doing: binding a persisted budget to resume, live layered-context wiring,
memory v2, tags, Releases, package uploads or real provider calls.
Done when: focused offline regressions pass, the bounded new-diff review has no
open blocker, and the new candidate receives its own required human sign-off.
Evidence: the matching tests and `review-runtime-admission-v1.json`.
Budget / stop: one bounded hardening slice; do not add another full review after
three rounds or expand this into the remaining runtime plan.
Rollback / next: leave `main` unchanged if landing is refused; after sign-off,
land the exact hardening candidate, then complete `PC-CTXPROJ-1` live projection
and sidecar/preview binding as a separate slice.


| ID | State | Depends on | Done when | Evidence |
|---|---|---|---|---|
| `SLICE-PREP-1` | `delivered` | — | Package can be maintained from this repository alone: process docs name a product-reachable slice location, package-level rules exist, and no doc or module depends on a private path | this file; `tests/test_package_independence.py`; [package rules](../AGENTS.md) |
| `SLICE-PREP-2` | `delivered` | `SLICE-PREP-1` | Independent build, install and CI evidence recorded for the current candidate, with the required fresh-context review completed | `tests/test_release_smoke.py`; `scripts/release_smoke.py`; `pocketchat/memory.py`; `pocketchat/task_state.py`; `tests/test_memory.py`; `tests/test_task_state.py`; `tests/test_task_cli_resume.py`; `tests/test_task_exec.py`; `docs/review-release-smoke-evidence-v1.json`; `docs/release-ledger.md` §3 candidate `1baea8a` / run `41588006974` |
| `TA-CLI-EXEC-1` | `delivered` | `SLICE-PREP-1` | A product CLI path accepts an operator TaskSpec and an explicitly authorized TaskPlan, executes the plan through the existing task runtime, and emits task/report evidence without a Python wrapper | `416e4a4`; `tests/test_task_cli_exec.py`; `docs/task-exec.md`; `CHANGELOG.md` |
| `TA-MODEL-REPAIR-1` | `delivered` | `TA-CLI-EXEC-1` | A failed authorized command step can ask a model for one repair action bound to the TaskSpec, TaskPlan and original template, apply only a `overwrite_paths` replacement through the guarded editor, and retry the identical template once without claiming acceptance | `tests/test_task_model_repair.py`; `tests/test_task_cli_exec.py`; `docs/task-repair.md`; `CHANGELOG.md` |
| `TA-PLAN-GENERATE-1` | `delivered` | `TA-CLI-EXEC-1` | A product CLI path turns one TaskSpec into a bounded, validated TaskPlan through the configured provider, persists request and plan snapshots, and emits the exact plan path and digest accepted by `task exec` without executing it | `tests/test_task_plan_generate.py`; `docs/task-plan.md`; `CHANGELOG.md` |
| `TA-CLI-RESUME-1` | `delivered` | `TA-CLI-EXEC-1` | `task exec` can stop at an explicit step boundary and resume from a digest-bound execution report or checkpoint through the same authorized plan without replaying completed side effects | `tests/test_task_cli_resume.py`; `docs/task-exec.md`; `docs/task-checkpoint.md`; `CHANGELOG.md` |
| `TA-CHECKPOINT-CLI-1` | `delivered` | `TA-CLI-RESUME-1` | `task checkpoint create` derives one checkpoint from an exact digest-bound execution report, writes it exclusively, and emits a record accepted by `task exec --checkpoint` without executing or replaying a step | `tests/test_task_checkpoint_create_cli.py`; `docs/task-checkpoint.md`; `CHANGELOG.md` |
| `TA-EXEC-ACCEPTANCE-1` | `delivered` | `TA-CLI-EXEC-1` | `task exec --check-acceptance` evaluates an operator acceptance plan against the exact execution report produced by the authorized plan, reports execution and acceptance separately, and returns success only when both complete | `tests/test_task_exec_acceptance_cli.py`; `docs/task-exec.md`; `docs/task-acceptance.md`; `CHANGELOG.md` |
| `PC-ADMIT-1` | `delivered` | `TA-EXEC-ACCEPTANCE-1` | A pure local admission function validates one complete payload against payload-byte, message-character and optional estimated-token limits; it reports payload digest, message and tool-schema counts, and never calls a provider or an adapter. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-admit-1` | `tests/test_request_admission.py`; `pocketchat/request_admission.py`; `CHANGELOG.md` |
| `PC-ADMIT-2` | `delivered` | `PC-ADMIT-1` | Every Session model request is admitted before its snapshot and send, including requests after tool results, retries and fallback attempts; the exact admitted payload is passed to the provider rather than rebuilt. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-admit-2` | `tests/test_tool_calls.py`; `tests/test_request.py`; `tests/test_kernel.py`; `pocketchat/session.py`; `pocketchat/provider.py`; `CHANGELOG.md` |
| `PC-ADMIT-3` | `delivered` | `PC-ADMIT-1` | Each of the two TaskPlan generation attempts is admitted before its request snapshot and provider call, so an oversized corrective request cannot be sent; plan generation still produces no executable plan by itself. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-admit-3` | `tests/test_task_plan_generate.py`; `pocketchat/task_plan.py`; `pocketchat/request_admission.py`; `CHANGELOG.md` |
| `PC-ADMIT-4` | `delivered` | `PC-ADMIT-1` | A model-repair request is admitted against the complete payload before its snapshot or provider call; an oversized request performs no edit and no command retry, and the existing TaskSpec/plan/template authority is unchanged. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-admit-4` | `tests/test_task_model_repair.py`; `pocketchat/task_model_repair.py`; `CHANGELOG.md` |
| `PC-RESULT-1` | `delivered` | `PC-ADMIT-1` | A pure result projector bounds the complete JSON envelope, preserves UTF-8 boundaries, records omitted bytes, can omit a write-content echo, and refuses metadata too large to fit. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-result-1` | `tests/test_tool_results.py`; `pocketchat/tool_results.py`; `CHANGELOG.md` |
| `PC-RESULT-2` | `delivered` | `PC-RESULT-1`, `PC-ADMIT-2` | Live read, write and memory tool results use bounded projections: reads acquire only the bounded prefix, writes omit the full content echo, memory lists are cut by whole records with omitted counts, and oversized envelopes remain explicit rather than terminating the run. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-result-2` | `tests/test_tools.py`; `tests/test_tool_calls.py`; `tests/test_memory_tools.py`; `pocketchat/tools.py`; `pocketchat/session.py`; `CHANGELOG.md` |
| `PC-MSTORE-1` | `delivered` | `PC-ADMIT-1` | The v1 memory store is opened as a regular non-symlink file, bounded by total bytes and per-line bytes, and read failures remain explicit rather than becoming an empty store. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-mstore-1` | `tests/test_memory.py`; `pocketchat/memory.py`; `CHANGELOG.md` |
| `PC-MSTORE-2` | `delivered` | `PC-MSTORE-1` | Memory mutations hold one store lock across read-modify-write, use a unique temporary file, reject symlinked stores, and either commit each acknowledged write or return an explicit busy/limit error without losing prior content. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-mstore-2` | `tests/test_memory.py`; `pocketchat/memory.py`; `CHANGELOG.md` |
| `PC-MSTORE-PARENT-1` | `delivered` | `PC-MSTORE-2`, `PC-META-2` | V2 memory reads open the store through a no-follow parent descriptor and relative file open, so replacing a parent before file open cannot redirect reads or writes outside the workspace; the regression test exercises both legacy path-open and descriptor-relative call shapes and leaves the outside file unchanged. `approved_by`: user directive of 2026-11-03 to complete the remaining PocketChat work and unblock PC-TCONTRACT-1 | `tests/test_memory.py`; `tests/test_memory_migration.py`; `pocketchat/memory_migration.py`; `CHANGELOG.md` |
| `PC-PWINDOW-1` | `delivered` | `PC-ADMIT-1` | A profile can declare a model context window, output reserve, safety margin and output-capability support; known windows reduce the effective input limit, unknown values stay unknown, and max_tokens is sent only when the profile explicitly supports it. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-pwindow-1` | `tests/test_profiles.py`; `tests/test_profile_admin.py`; `tests/test_request_admission.py`; `tests/test_request.py`; `pocketchat/config.py`; `pocketchat/provider.py`; `pocketchat/request_admission.py`; `CHANGELOG.md` |
| `PC-TBUDGET-1` | `delivered` | `PC-ADMIT-1` | A pure versioned task-budget ledger reserves before a request, settles once with reported usage, keeps unknown usage reserved and marked unknown, and rejects duplicate or over-limit requests. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-1` | `tests/test_task_budget.py`; `pocketchat/task_budget.py`; `CHANGELOG.md` |
| `PC-TBUDGET-2` | `delivered` | `PC-TBUDGET-1`, `PC-ADMIT-2` | `task exec` creates one task-budget ledger from optional `max_task_tokens` and passes it through every authorized step; Session reserves before each model request and settles reported usage, while missing usage fails closed. Existing per-turn and per-Session limits are unchanged. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-2` | `tests/test_task_spec.py`; `tests/test_task_run.py`; `tests/test_task_exec.py`; `tests/test_task_cli_exec.py`; `docs/task-spec.md`; `docs/task-spec.zh-CN.md`; `CHANGELOG.md` |
| `PC-TBUDGET-2A` | `delivered` | `PC-TBUDGET-2` | Execution reports persist the optional whole-task budget ledger; report/checkpoint resume restores the same limit and request history, and refuses a missing, malformed or mismatched ledger before new work. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_task_cli_resume.py`; `tests/test_task_exec.py`; `pocketchat/task_exec.py`; `docs/task-exec.md`; `docs/task-spec.md`; `CHANGELOG.md` |
| `PC-TBUDGET-3` | `delivered` | `PC-TBUDGET-2`, `PC-ADMIT-3` | TaskPlan generation reserves and settles its attempts against an optional generation budget, refuses an over-limit attempt before a provider call, and does not persist that generation limit into the generated plan or its execution authorization. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-3` | `tests/test_task_plan_generate.py`; `pocketchat/task_plan.py`; `pocketchat/cli.py`; `CHANGELOG.md` |
| `PC-TBUDGET-4` | `delivered` | `PC-TBUDGET-3`, `PC-ADMIT-4` | Model-repair provider usage is reserved and settled against the same whole-task ledger; an exhausted budget leaves the target file and retry untouched, while the existing one-repair/one-retry authority does not reset the total. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-4` | `tests/test_task_model_repair.py`; `pocketchat/task_model_repair.py`; `pocketchat/task_exec.py`; `CHANGELOG.md` |
| `PC-CTXSTATE-1` | `delivered` | `PC-ADMIT-2`, `PC-RESULT-2` | A pure task-state projector admits only bound, completed dependency evidence and declared file evidence; it refuses unresolved evidence, unfinished dependencies, malformed paths and oversized projections, and excludes unrelated steps. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-ctxstate-1` | `tests/test_task_state.py`; `pocketchat/task_state.py`; `CHANGELOG.md` |
| `PC-CTXPROJ-1` | `delivered` | `PC-CTXSTATE-1` | Live layered context projection preserves fixed instructions and task state, keeps complete recent exchanges and selected evidence, writes a bounded sidecar manifest, and makes `context preview` select the same messages as the actual request. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_context_projection.py`; `tests/test_context.py`; `pocketchat/context_projection.py`; `pocketchat/context.py`; `pocketchat/session.py`; `CHANGELOG.md` |
| `PC-META-2` | `delivered` | `PC-META-1` | The CLI exposes explicit v2 migration, acceptance, retirement and physical forget operations against an operator-selected v2 store while the default v1 memory commands retain their existing behavior. Legacy records remain unverified candidates, acceptance requires an explicit approver, retirement preserves lifecycle metadata, and forget leaves no hidden copy of the content. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan and the 2026-11-03 `继续下一步` directive selecting the next independent memory slice | `tests/test_memory.py`; `tests/test_memory_migration.py`; `pocketchat/memory_migration.py`; `pocketchat/cli.py`; `docs/cli.md`; `docs/cli.zh-CN.md`; `CHANGELOG.md` |
| `PC-META-1` | `delivered` | `PC-MSTORE-2` | A closed v2 memory record parser validates identity, scope, content digest, digest-bound source references and lifecycle metadata; v1 records migrate only through an explicit pure conversion that leaves the source unchanged, marks legacy records unverified candidates, refuses unknown fields and unsafe targets, and writes a new bounded file exclusively. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_memory_migration.py`; `pocketchat/memory_migration.py`; `docs/formats.md`; `CHANGELOG.md` |
| `PC-MRETRIEVE-1` | `delivered` | `PC-META-1`, `PC-RESULT-1` | A pure memory-v2 retrieval core applies workspace, lifecycle and sensitive admission before deterministic lexical ranking and Top-K; fixed English/CJK tokenization and dual byte/token excerpt budgets produce bounded excerpts, while stable exclusion, omission and cursor semantics leave legacy search and the CLI unchanged. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-mretrieve-1` | `tests/test_memory_retrieval.py`; `pocketchat/memory_retrieval.py`; `docs/memory-retrieval.md`; `docs/memory-retrieval.zh-CN.md`; `docs/review-memory-retrieval-v1.json`; `CHANGELOG.md` |
| `PC-RECALL-1` | `delivered` | `PC-META-2`, `PC-MRETRIEVE-1`, `PC-CTXPROJ-1` | Explicit `memory recall --dry-run` and opt-in Session/context-preview recall projection use the deterministic v2 retrieval core with workspace admission, bounded excerpts and stable evidence; legacy search and disabled mode remain unchanged. `approved_by`: user directive of 2026-11-03, “批准，全力推进项目，把剩下的任务都完成” | `tests/test_memory_retrieval.py`; `tests/test_memory.py`; `tests/test_memory_tools.py`; `tests/test_profiles.py`; `tests/test_profile_admin.py`; `pocketchat/memory_retrieval.py`; `pocketchat/session.py`; `pocketchat/service.py`; `pocketchat/config.py`; `pocketchat/cli.py`; `docs/memory-retrieval.md`; `docs/memory-retrieval.zh-CN.md`; `docs/cli.md`; `docs/cli.zh-CN.md`; `docs/compatibility.md`; `docs/compatibility.zh-CN.md`; `docs/formats.md`; `CHANGELOG.md`; `docs/review-memory-recall-v1.json` |
| `PC-CTXRESUME-1` | `delivered` | `PC-CTXPROJ-1`, `PC-RECALL-1`, `PC-TBUDGET-4` | Checkpoint resume binds optional projection/recall artifacts and the persisted task budget ledger; replay validates adjacent projection sidecars without re-searching memory, missing/changed artifacts refuse, and completed side effects remain non-replayed. `approved_by`: user directive of 2026-11-03, “批准，全力推进项目，把剩下的任务都完成” | `tests/test_task_checkpoint_resume.py`; `tests/test_task_recovery.py`; `tests/test_replay.py`; `tests/test_task_cli_resume.py`; `tests/test_task_exec.py`; `tests/test_task_budget.py`; `pocketchat/task_checkpoint.py`; `pocketchat/task_recovery.py`; `pocketchat/replay.py`; `docs/task-checkpoint.md`; `docs/task-checkpoint.zh-CN.md`; `docs/task-recovery.md`; `docs/task-recovery.zh-CN.md`; `docs/cli.md`; `docs/cli.zh-CN.md`; `docs/compatibility.md`; `docs/compatibility.zh-CN.md`; `docs/formats.md`; `CHANGELOG.md`; `docs/review-context-resume-v1.json` |
| `PC-RUNTIME-HARDEN-1` | `delivered` | Original runtime candidate | Whole-task charging uses reported usage; missing usage stops before side effects; budgeted resume restores its persisted ledger; memory envelopes stay bounded; orphan and out-of-order tool results are refused. `approved_by`: user directive of 2026-11-03 to continue the approved runtime plan | `tests/test_task_budget.py`; `tests/test_memory_tools.py`; `tests/test_task_cli_resume.py`; `tests/test_task_model_repair.py`; `tests/test_task_plan_generate.py`; `tests/test_context_projection.py`; `docs/review-runtime-admission-v1.json` |
| `PC-TCONTRACT-1` | `delivered` | `PC-RESULT-2` | `read_file`, `write_file` and `memory_search` share one closed contract for provider schema, host argument validation, trace hashing and task-policy naming; disabled tools stay default-off, unknown arguments fail before I/O, and command templates never become a free shell. `approved_by`: user directive of 2026-11-03, “批准，全力推进项目，把剩下的任务都完成” | `tests/test_tool_contracts.py`; `tests/test_tool_calls.py`; `pocketchat/tool_contracts.py`; `pocketchat/provider.py`; `pocketchat/session.py`; `pocketchat/task_policy.py`; `docs/review-tool-contracts-v1.json` |


### Approved foundational slices

#### `PC-SFILE-READ-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-session-read-errors`
- **Write scope**: `pocketchat/sessions.py`, `tests/test_session_read_errors.py`,
  `docs/session-read-errors.md`, `docs/session-read-errors.zh-CN.md`,
  `docs/cli.md`, `docs/cli.zh-CN.md`, `CHANGELOG.md`,
  `docs/review-session-read-errors-v1.json`
- **Depends on**: none
- **Done when**: A malformed UTF-8 session file is rejected as the existing typed
  session-file error, and `show --json` returns the existing `session_file`
  refusal with exit 2 and no traceback while the input bytes remain unchanged.
- **Evidence**: `tests/test_session_read_errors.py`, `tests/test_sessions.py`,
  both session-read error docs, and `docs/review-session-read-errors-v1.json`;
  merged to `main` as `c97fe8e`.
- **Approved by**: User directive of 2026-11-03 to continue PocketChat
  foundational work; the approved design was recorded before this slice.

#### `PC-RCAP-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-runtime-capabilities`
- **Write scope**: `pocketchat/runtime_capabilities.py`,
  `tests/test_runtime_capabilities.py`, `docs/review-runtime-capabilities-v1.json`
- **Depends on**: none
- **Done when**: A pure `collect_runtime_capabilities()` reports interpreter/OS
  facts and three explicit Python primitive booleans as schema 1 JSON without
  I/O, and fixed `not_checked` names keep provider, workspace and concurrency
  claims out of the result.
- **Evidence**: `tests/test_runtime_capabilities.py`,
  `pocketchat/runtime_capabilities.py`, and
  `docs/review-runtime-capabilities-v1.json`; merged to `main` as `5ce3586`.
- **Approved by**: User directive of 2026-11-03 to continue PocketChat
  foundational work; the approved design was recorded before this slice.

#### `PC-RDOCTOR-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-doctor-runtime`
- **Write scope**: `pocketchat/doctor.py`, `pocketchat/cli.py`,
  `tests/test_doctor.py`, `docs/cli.md`, `docs/cli.zh-CN.md`,
  `docs/review-doctor-runtime-v1.json`
- **Depends on**: `PC-RCAP-1` (delivered at `5ce3586`)
- **Done when**: An explicit `doctor --runtime` adds one bounded runtime
  capability field while the default doctor JSON, three existing checks,
  `ok` value and exit code remain unchanged; missing no-follow support is
  reported as a capability limit without claiming tool or workspace safety.
- **Evidence**: `tests/test_doctor.py`, `pocketchat/doctor.py`,
  `pocketchat/cli.py`, both CLI docs, and
  `docs/review-doctor-runtime-v1.json`; merged to `main` as `b3ca703`.
- **Approved by**: User directive of 2026-11-03 to continue PocketChat
  foundational work; the approved design was recorded before this slice, with the
  delivered dependency `PC-RCAP-1`.

#### `PC-SFILE-FENCE-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-session-writer-fence`
- **Write scope**: `pocketchat/session_writer.py`, `pocketchat/sessions.py`,
  `pocketchat/session.py`, `tests/test_session_writer.py`,
  `docs/session-writer.md`, `docs/session-writer.zh-CN.md`,
  `docs/compatibility.md`, `docs/compatibility.zh-CN.md`,
  `docs/review-session-writer-v1.json`
- **Depends on**: `PC-SFILE-READ-1` (delivered at `c97fe8e`); no other
  `session.py` writer may be active in the same branch.
- **Done when**: Two stale in-memory views can no longer append duplicate
  identities to one explicit session; an independent-process barrier shows
  only one writer entering I/O for one path while different paths remain
  independent; initialization, cancellation and exception release only the
  owner's claim; foreign-owner and path-replacement cases fail closed.
- **Evidence**: `tests/test_session_writer.py`,
  `docs/review-session-writer-v1.json`, a bounded two-process fixture, a stub
  request counter proving the refused writer sends zero requests, and an
  unchanged historical file hash; merged to `main` as `4877e5e`.
- **Approved by**: User directive of 2026-11-03 to continue PocketChat
  foundational work; the approved design was recorded before this slice, with the
  delivered dependency `PC-SFILE-READ-1`.

#### `PC-EVIDENCE-LINK-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-evidence-link-core`
- **Write scope**: `pocketchat/evidence_links.py`,
  `tests/test_evidence_links.py`, `docs/evidence-links.md`,
  `docs/evidence-links.zh-CN.md`, `docs/review-evidence-link-v1.json`
- **Depends on**: none
- **Done when**: A pure `verify_evidence_links(artifacts, relations)` returns
  `schema_version=1` and `complete`, `partial` or `refused` without I/O. Every
  artifact descriptor carries a unique ID, one of the six allowed kinds,
  a workspace-relative path, declared and observed whole-file SHA-256, the
  semantic digest and its scope, run/task/spec/plan identity, and complete or
  partial read coverage. Every relation names two existing artifact IDs, a
  relation kind and the fields compared; there is no link conclusion without
  an explicit edge. Whole-file hashes, request-body canonical digests and
  semantic digests are never interchangeable. A same-hash/different-run-ID
  observation, wrong identity or digest, duplicate ID, orphan relation or
  unsafe path is `refused`; a missing optional artifact or incomplete but
  explicit observation is `partial`; only bounded, fully covered required
  relations with no contradiction are `complete`. The report names unknowns
  and omissions, excludes request/message/credential text, and refuses above
  256 artifacts, 1024 relations or a 64 KiB report rather than truncating.
  Focused tests cover the three fixed observations
  session→trace→request, comparison→run and
  report→spec/plan/checkpoint, input immutability, stable order and zero
  open/socket/provider calls. This slice does not read files, add a CLI,
  repair artifacts, call a provider, publish, or claim that a `complete`
  report re-read the original files.
- **Evidence**: `pocketchat/evidence_links.py`,
  `tests/test_evidence_links.py`, `docs/evidence-links.md`,
  `docs/evidence-links.zh-CN.md`, `CHANGELOG.md` and
  `docs/review-evidence-link-v1.json`; exact reviewed candidate
  `f0cd4629eb0b96817fbb953ea6ecc1d8f1debced` merged by PR #14 as
  `797a7e6c007e176e0ae331d8c64e31e4aabed117`, with exact-SHA CI run
  `41831133989` reporting seven successful jobs.
- **Approved by**: User directive of 2026-11-04 to add missing tasks and
  finish required functionality first; this row and the planning review
  record freeze the evidence-link core contract. Exact candidate landing
  remains gated by the implementation review record
  and its required human sign-off. This approval is limited to the pure core
  above; it does not authorize a CLI, second evidence schema, provider call,
  tag, Release or package upload.
- **Approval evidence**: This row and
  `docs/review-evidence-link-approval-v1.json` are the approval record; the
  row is ready only after this planning branch is integrated and reachable
  from `main`. The exact reviewed SHA and merge readback belong to the
  integration record. The execution branch remains uncreated by this
  planning change; `CHANGELOG.md` and the exact implementation SHA belong to
  the later implementation and delivery records.

#### `PC-TASK-PREFLIGHT-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-task-preflight`
- **Write scope**: `pocketchat/task_preflight.py`, `tests/test_task_preflight.py`,
  `docs/task-preflight.md`, `docs/task-preflight.zh-CN.md`,
  `docs/review-task-preflight-v1.json`
- **Depends on**: none
- **Done when**: A pure `check_task_preflight(spec, plan, snapshot, observation)`
  reuses the parsed TaskSpec/TaskPlan and snapshot digest contracts and returns
  `schema_version=1` with `allow`, `refused` or `unknown`, without Git, filesystem,
  command, network or provider I/O. The caller supplies closed observations for
  workspace identity, base and head commit, tracked/untracked/dirty paths,
  read coverage and the declared spec/plan/snapshot digests. `allow` requires all
  required observations complete and bound to the same workspace and plan, a
  stable head, and every planned read/write input contained by the TaskSpec
  allowlists; any undeclared dirty/foreign/out-of-scope write or digest/identity/
  head contradiction is `refused`; a non-Git or explicitly partial dirty/source
  observation is `unknown` rather than a clean workspace. The report has stable
  order, names unknowns/omissions and rejects rather than truncates beyond 4096
  path observations or a 64 KiB canonical report. Input mapping/list objects
  remain unchanged, the report never echoes free-form objectives, request bodies,
  messages or credentials, and focused tests cover clean/dirty, HEAD/source drift,
  untracked out-of-scope writes, ancestor/descendant scope overlap, case collisions,
  non-Git and zero open/socket/provider/command calls. This slice adds no CLI,
  real workspace reader, executor authorization change, file mutation, provider
  call, publication or claim that a pure fixture is a real Git preflight.
- **Evidence**: `pocketchat/task_preflight.py`, `tests/test_task_preflight.py`,
  `docs/task-preflight.md`, `docs/task-preflight.zh-CN.md` and
  `docs/review-task-preflight-v1.json`; delivered on `main` at
  `fdff82d167a11849d4e6fd7772595fbe3af88486` with exact-SHA run `41944847472`
  (7/7 checks successful).
- **Approved by**: agent (user 2026-11-04 16:34 PocketChat delegation); this row
  and the planning review record freeze the existing `PC-TASK-PREFLIGHT-1`
  contract of this public package. This is not an execution authorization and
  does not authorize a CLI, real Git observation,
  second task model, provider call, tag, Release or package upload.
- **Approval evidence**: This row and `docs/review-task-preflight-approval-v1.json`
  are the approval record; the row becomes claimable only after this planning
  branch is reachable from `main`. The implementation branch and exact reviewed
  candidate remain separate; this planning change does not create or claim them.


#### `PC-SFILE-AUDIT-1`

- **State**: `ready`
- **Branch**: `task/pocketchat-session-audit-core`
- **Write scope**: `pocketchat/session_integrity.py`,
  `tests/test_session_integrity.py`, `docs/session-integrity.md`,
  `docs/session-integrity.zh-CN.md`, `docs/review-session-integrity-v1.json`
- **Depends on**: `PC-SFILE-READ-1` (delivered at `c97fe8e`); no session
  writer, CLI, repair, provider or runtime dependency.
- **Done when**: A pure
  `audit_session_records(records, *, max_findings=64, max_omissions=64, max_bytes=65536)`
  consumes caller-supplied, already-read session records and returns a closed
  `pocketchat-session-integrity.v1` report with `schema_version=1` and status
  `complete`, `partial` or `refused`. The audit validates the header and
  session identity, run/workspace association, message role and content shape,
  unique and monotonically increasing turn ids, and provider-shaped tool
  contexts whose calls and results pair exactly and end in explicit terminal
  states. It distinguishes malformed input from inspected partial coverage and
  keeps an empty valid record list distinct from corrupt input. Findings use
  stable order and bounded typed fields such as record index, code and redacted
  context; report omissions are explicit. Inputs remain unchanged. The core
  never repairs, truncates or rewrites records, reads a path, executes a
  command, calls a provider, or returns full request, message, session or
  credential bodies. Limits reject rather than silently truncate when the
  minimal envelope cannot fit. Legacy tolerance is limited to the reader's
  existing accepted schema and derived turn ids; this slice invents no
  migration or repair behavior. Focused tests cover valid modern and legacy
  records, empty valid input, malformed header/message/tool records, duplicate
  and regressing turn ids, cross-record call/result pairing, explicit terminal
  states, bounded findings/omissions/report size, input immutability, stable
  output and zero filesystem/command/socket/provider calls. This slice adds no
  CLI, real session reader integration, repair/copy, writer change, tag,
  Release or publication.
- **Evidence**: `pocketchat/session_integrity.py`,
  `tests/test_session_integrity.py`, `docs/session-integrity.md`,
  `docs/session-integrity.zh-CN.md` and
  `docs/review-session-integrity-v1.json`; focused unittest output, exact
  reviewed candidate SHA and `git diff --check` are recorded in the
  implementation review. The delivery commit is recorded only after the
  candidate is examined against this row and merged.
- **Approved by**: agent under the user 2026-11-04 16:34 PocketChat routine
  delegation; this row and `docs/review-session-audit-approval-v1.json`
  freeze the pure-core contract. This approval does not authorize a CLI, real
  file/session observation, repair or mutation, provider/network call, tag,
  Release or package upload.
- **Approval evidence**: This row and
  `docs/review-session-audit-approval-v1.json` are the product approval
  record. The execution branch is not created by this planning change; it may
  start only after this row is integrated and read back from product `main`,
  and only if its write scope remains free of an active writer.

#### `PC-PACKAGE-MANIFEST-1`

- **State**: `ready`
- **Branch**: `task/pocketchat-package-manifest`
- **Write scope**: `scripts/release_smoke.py`, `tests/test_release_smoke.py`,
  `docs/package-manifest.md`, `docs/package-manifest.zh-CN.md`,
  `docs/review-package-manifest-v1.json`
- **Depends on**: the delivered release-smoke surface in `SLICE-PREP-2`; no
  runtime, provider, session, memory, task-diff or shared CLI dependency.
- **Done when**: The existing release-smoke module exposes
  `build_package_manifest(source, *, source_kind, max_members=2048,
  max_member_bytes=8388608, max_total_bytes=67108864,
  max_report_bytes=262144)` for an explicitly selected wheel or installed
  `site-packages` root. It returns deterministic canonical JSON data with
  `schema=pocketchat-package-manifest.v1`, `schema_version=1`, source kind,
  package/version/entry-point identity, stable module/schema/metadata groups,
  and a sorted record for every admitted regular member containing only its
  normalized logical path, category, byte length and SHA-256. Wheel and
  installed-tree inspection share one classification and validation path.
  Exactly one `pocketchat/` package and one matching
  `pocketchat-<version>.dist-info/` tree are admitted; the five shipped schemas,
  existing release-critical modules, `METADATA`, `WHEEL`, `RECORD` and the
  `pocketchat = pocketchat.cli:main` declaration must be present and consistent.
  Archive duplicates, absolute/backslash/parent paths, case or Unicode-normalized
  collisions, encrypted entries, symlinks, non-regular installed entries,
  unknown source kinds, version/entry-point mismatch, missing required resources
  and member/count/total/report limit overflow are refused rather than skipped or
  truncated. The manifest contains no absolute path or file body and does not
  mutate inputs. `run_release_smoke` includes the wheel manifest and verifies a
  clean installed-tree manifest before its existing import/version and complete
  `task ... --help` smoke; it reuses the current build/install path and adds no
  second builder, package upload, tag, network/provider/credential access or
  arbitrary workspace scan. Focused tests create two bounded fixture wheels and
  one installed tree, cover stable bytes, wheel/install agreement, unsafe and
  missing resources, every limit boundary, input immutability and the existing
  installed import/help surface. This slice does not change package contents,
  version, CLI commands, schemas, CI workflow, tag, Release or package-index
  state.
- **Evidence**: `scripts/release_smoke.py`, `tests/test_release_smoke.py`, both
  package-manifest docs and `docs/review-package-manifest-v1.json`; the
  implementation review records the observed red, focused/full tests, Ruff,
  compile and diff checks plus the exact candidate SHA. Delivery is recorded
  only after that candidate is reviewed and integrated.
- **Approved by**: agent under the user 2026-11-04 16:34 PocketChat routine
  delegation and the user 2026-11-05 request for a non-conflicting PocketChat
  task. This approval covers the bounded package-evidence slice only; it does
  not authorize a tag, Release, package upload, version change or publication.
- **Approval evidence**: This row and
  `docs/review-package-manifest-approval-v1.json` are the product approval
  record. The execution branch is not created by this planning change; it may
  start only after this row is integrated and read back from product `main`,
  and only if its five-path write scope remains free of an active writer.


Cancelled and delivered rows are moved to
[`CHANGELOG.md`](../CHANGELOG.md) rather than deleted, so a later reader can
see why a slice stopped.

## 3. Not a queue entry

- A branch name, a worktree, or a passing test is not an approved slice.
- "Low priority" is not a reason to skip the difference, identity or history
  audit for a change.
- The absence of a row is not an approval to start.
