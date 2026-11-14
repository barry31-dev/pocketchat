# Slice queue

## Approved chat-first office slice (2026-11-14)

`PC-OFFICE-WEB-1`: the maintainer approved retention of chat/CLI plus a local
browser entry and selected-material → report → revision → DOCX → reopen.
[office-web.md](office-web.md) owns the exact implementation/completion contract;
[review record](review-office-web-v1.json) owns exclusive paths and evidence.
Complex coding and multi-agent work are not the current product mainline.
Existing delivery/history rows stay intact; this approval is not a release.


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

The readable contract exists before implementation, but inside the approved
roadmap it may land with implementation instead of requiring a separate planning
PR. A development branch does not edit this file merely to claim or complete
work. Planner/integrator updates remain serialized; a task-specific issue or
review record avoids contention on this shared file. Historical rows saying
"main first" describe their old approval procedure, not a new daily stop gate.

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


#### `PC-CHAT-LIFECYCLE-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-chat-lifecycle`
- **Write scope**: `pocketchat/tui.py`, `pocketchat/provider.py`,
  `tests/test_agent.py`, `tests/test_kernel.py`, `tests/test_tool_calls.py`,
  `docs/provider-adapters.md`, `docs/provider-adapters.zh-CN.md`,
  `docs/review-chat-lifecycle-v1.json`
- **Depends on**: `PC-SFILE-FENCE-1` (delivered at `4877e5e`) and
  `PC-RUNTIME-HARDEN-1` (delivered); no CLI, file-work, schema, workflow or
  release dependency. Session storage and provider normalization remain the
  owners of their existing formats and contracts.
- **Done when**: A same-profile, same-session `AgentTUI` performs three
  successful sends, and the resulting session file contains its header plus
  all three user/assistant exchanges. The second and third provider requests
  carry the preceding conversation context; a persistence failure is visible
  rather than silently dropping later appends. The TUI either reacquires
  writer ownership for each run or uses one explicit open/close lifecycle; it
  never reuses a released token. Streamed provider responses normalize text,
  tool calls and usage to the same closed response contract as non-streaming
  responses: text-only, tool-only, text plus fragmented tool arguments, and
  usage-only events with `choices=[]` are covered. Tool calls are returned only
  after their complete arguments decode, malformed or incomplete fragments
  fail closed, and reported usage is retained rather than replaced by zero.
  Focused tests must be observed red for the fixed-SHA defects before the
  smallest implementation. Existing CLI/file/TUI behavior outside these paths,
  prompt bytes, request snapshots, stable schemas and exit-code classes remain
  compatible. This slice does not add shell/network tools, provider fallback,
  a second session format, a second executor, UI features or public release.
- **Evidence**: `tests/test_agent.py`, `tests/test_kernel.py`,
  `tests/test_tool_calls.py`, `pocketchat/tui.py`, `pocketchat/provider.py`,
  both provider-adapter docs and `docs/review-chat-lifecycle-v1.json`; reviewed
  candidate `9ded1d4eb9b6d01a494fd9a3bec08c5e12657ee7` merged by PR #41 as
  `72bf2bc8f61b00c073da00e0ea03494fd378d835`. Exact-head workflow run
  `42652625582` reports seven successful jobs. The implementation review retains
  all eleven findings as fixed and keeps its original unverified scope; this
  queue reconciliation does not rerun or widen that evidence.
- **Approved by**: agent under the user 2026-11-07 planning/timer optimization
  authorization and the existing 2026-11-04 PocketChat routine delegation.
  This approval covers one bounded P0 chat lifecycle slice only; it does not
  authorize a paid provider call, a provider switch, tag, Release, package
  upload or publication.
- **Approval evidence**: This row and
  `docs/review-chat-lifecycle-approval-v1.json` are the approval record. The
  execution branch is not created by this planning change; it may start only
  after this row is integrated and read back from product `main`, and only if
  its exact write scope remains free of an active writer.


#### `PC-SFILE-AUDIT-1`

- **State**: `delivered`
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
  `docs/review-session-integrity-v1.json`; reviewed implementation
  `24bea6cc86a8e1de3ad60a722b1c27aacb490800` and evidence-bearing tip
  `6e8a0a2724bc68fc218541207eedab0aaa2c9f44` merged by PR #28 as
  `b5fbaa613bef79cc0ef6b537bfea9125931bd659`. Exact-SHA runs
  `42002841536` and `42003912267` each report seven successful jobs; the
  review record also binds the final closeout review and landing sign-off.
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

#### `PC-CONTEXT-EXPLAIN-1`

- **State**: `delivered`
- **Branch**: `task/pocketchat-context-explain`
- **Write scope**: `pocketchat/context_explain.py`,
  `tests/test_context_explain.py`, `docs/context-explain.md`,
  `docs/context-explain.zh-CN.md`, `docs/review-context-explain-v1.json`
- **Depends on**: `PC-CTXPROJ-1` (delivered on `main`); no context-drift,
  evidence-link, preflight, memory, provider, CLI or session-writer dependency.
- **Done when**: A pure
  `explain_context_projection(projection, *, max_findings=64, max_omissions=64,
  max_bytes=65536)` consumes one closed explanation input containing the exact
  `project_context` source payload, its selection parameters and result, plus
  bounded source descriptors whose stable source ids map entries to the fixed,
  recent, task, memory and tool layers. It returns deterministic
  `pocketchat-context-explain.v1` data with `complete`, `partial` or `refused`
  status, a closed budget account and ordered observations for selected and
  omitted entries. The function verifies the existing
  `pocketchat-context-projection.v1` source and projection digests, explains
  what the existing sidecar records and names unavailable reasons as explicit
  omissions; it never recomputes or changes selection. Fixed instructions and
  task state, complete recent turns, and memory/tool evidence are reported with
  source id, layer, bounded record index, stable reason code and byte count.
  Duplicate or missing source ids, malformed or unknown input schema, digest
  drift, sensitive evidence, budget exhaustion and missing sidecar metadata are
  distinct observations; sensitive or omitted request, message, session,
  credential and memory bodies are never returned. Reports use stable ordering
  and refuse rather than silently truncate when the minimal envelope cannot fit
  within `max_findings`, `max_omissions` or `max_bytes`. Inputs remain unchanged.
  Focused tests cover all five layers, exact digest binding, duplicate and
  missing source ids, memory/tool classification, sensitive summaries, fixed
  and per-entry budget boundaries, malformed sidecars, empty valid projection,
  explicit partial coverage, stable bytes and zero filesystem, command, socket
  or provider calls. This slice adds no projection rewrite, reader or CLI
  integration, file observation, repair, migration, model/provider call, tag,
  Release or publication.
- **Evidence**: `pocketchat/context_explain.py`,
  `tests/test_context_explain.py`, `docs/context-explain.md`,
  `docs/context-explain.zh-CN.md` and
  `docs/review-context-explain-v1.json`; reviewed implementation
  `0480cd110c6eb33af791bb4533340011bf06622c`, source candidate
  `a08ddd51161a65df5f3b9ae3e5cb12b9abb5d4f7` and merge
  `2f3c7f3d74764fca6354b6cabcf282b0ac9f5c47` are recorded in the review record
  and the fleet delivery receipt. The receipt binds the candidate to main and
  records a green seven-check exact-SHA gate.
- **Approved by**: agent under the user 2026-11-04 16:34 PocketChat routine
  delegation and the user 2026-11-05 request for a non-conflicting PocketChat
  task. This approval covers the bounded read-only explanation core only; it
  does not authorize projection mutation, a CLI, real file observation, repair,
  provider/network access, tag, Release or package upload.
- **Approval evidence**: This row and
  `docs/review-context-explain-approval-v1.json` are the product approval
  record. The execution branch is not created by this planning change; it may
  start only after this row is integrated and read back from product `main`,
  and only if its five-path write scope remains free of an active writer.

#### `PC-PACKAGE-MANIFEST-1`

- **State**: `delivered`
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
  package-manifest docs and `docs/review-package-manifest-v1.json`; reviewed
  candidate `ae6bb690d389672bf98c6ca6c91fe205ab23188e` merged by PR #43 as
  `cab0d5caa7e8026b5539f743c4527945f0f4398f`. Exact-head workflow run
  `42691516522` reports seven successful jobs. The review closes PM-1, PM-2,
  PM-5 and PM-6 while retaining deferred minor limitations PM-3 and PM-4; this
  queue reconciliation does not silently close or reverify them.
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

### First chat-and-file-work sequence (2026-11-05)

The user selected outcome-sized delivery packages on 2026-11-05: fewer handoffs,
not fewer safety checks. The remaining six execution rows become **two
implementation packages and one release package**. Keep the existing CHANGE,
CHAT and RELEASE IDs; do not create a second queue or restart the landed plan.
[The file-work contract](file-work.md) still owns permissions, compensation,
limits and acceptance; [the development roadmap](agent-development-master-plan.zh-CN.md)
owns the later capability stages, not runnable approvals.

| Retained owner | Work included | Treatment of former separate rows |
|---|---|---|
| `PC-FILE-CHANGE-1` | proposal, safe apply, receipts, restart and persistent undo | `PC-FILE-UNDO-1` is absorbed |
| `PC-FILE-CHAT-1` | Session tools, both terminal paths, golden-path fixture and development-wheel validation | `PC-FILE-TOOL-1` and `PC-FILE-E2E-1` are absorbed |
| `PC-FILE-RELEASE-1` | final artifact freeze, artifact-specific acceptance, authorization and publication | remains separate because its authority differs |

**Activation and existing work.** The maintainer's 2026-11-09 standing routine
delegation covers this bounded regrouping. Its readable contract may accompany
implementation; it does not require a separate planning PR to land first.
Before writing, atomically register the exact scope and check conflicts through
the existing ownership mechanism. This document does not rewrite an operational
card or widen a live lease. Before landing or claiming, re-read main, existing
branches/issues/PRs and operational leases. If
an absorbed row has acquired an owner or delivered work, preserve that work and
settle its ownership before switching; do not cancel a live lease, replay a
finished change or open a competing package. The cancelled rows below retire
scheduling units, not features. A queued row is not a running lease, delivery
or external-action authorization.

Existing cancellation (issue #31), task diff (#18), matrix identity (#29) and
JSON input work retain their original owners. An open issue alone does not mean
its code is absent: inspect the current main and exact candidate. Pull in only a
real safety or acceptance dependency. Task snapshot diff is not file approval
or undo; never fabricate an executed snapshot to render a proposal.

**Execution granularity.** One package owns one user-visible outcome and its
cohesive write scope. Schema, parser, CLI wiring, relevant tests, documents and
review are internal steps, not separate cards. Keep observable checkpoints and
multiple reviewed commits on the same package when needed; a session time limit
ends that attempt, not the task identity. An actual lease expiry still stops
writes and requires the existing ownership/reacquisition procedure. Do not
split merely because 50 minutes elapsed. Split only for genuinely independent
outcomes, disjoint parallel ownership or a separate authorization boundary.

Tests follow meaningful behavior changes; a green check is not repeated without
changed inputs, a failure or an unresolved concern. Each package retains the
required fresh-context review (deep for file safety/rollback) and the existing
review-round cap. No default mutation path is exposed before persistent undo is
proved. Integrators alone maintain queue delivery; workers do not edit this file
to claim or complete work. CHANGELOG consolidation belongs to integration/release.
No full product suite is required for this documentation-only regrouping.

#### `PC-FILE-PLAN-1`

- **State**: `delivered`.
- **Branch**: `task/pocketchat-file-work-plan`.
- **Depends on**: the selected user goal and a lawful product approval-record path.
- **Write scope**:
  - `docs/file-work.md`
  - `docs/file-work.zh-CN.md`
  - `docs/review-file-plan-v1.json`
  - `docs/slice-queue.md`
- **Done when**: Freeze the independent file-work contract and these bounded approval records. No product implementation, version change, provider call or publication. Completion requires the exact reviewed planning candidate to be integrated and read back from main.
- **Evidence**: reviewed four-path planning candidate merged by PR #34 as `a79c037f4d13e79795221cf2706c3973d4912c49`; `docs/review-file-plan-v1.json` records the two fixed design findings and the bounded independent second review.
- **Budget / stop / rollback**: one reviewable slice; stop on ownership, permission or evidence conflicts. Leave the last integrated product intact, retain the candidate and do not replay side effects. Runtime compensation follows file-work.md, not a Git history rewrite.
- **Approved by**: agent under the user 2026-11-04 routine technical delegation and the user 2026-11-05 first chat/file-loop request. This covers the bounded design, not public push/merge, paid provider access, tag, Release or package upload. The narrower existing permissions still apply.

#### `PC-FILE-CHANGE-1` — safe changes and persistent undo

- **State**: `delivered` (safe file-change and persistent-undo source integrated; release artifact acceptance is separate).
- **Integration evidence**: [PR #45](https://github.com/barry31-dev/pocketchat/pull/45), merge `677fcd0514afa1e936104721d5c458591e69ec75`, is an ancestor of the completed file-chat development integration. Historical transaction review evidence is retained; this row does not claim a release or replace it with the later fixture.
- **Branch**: `task/pocketchat-file-change`.
- **Depends on**: `PC-FILE-PLAN-1`.
- **Write scope**:
  - `pocketchat/file_changes.py`
  - `pocketchat/file_change_store.py`
  - `pocketchat/tools.py`
  - `tests/test_file_changes.py`
  - `tests/test_file_change_store.py`
  - `tests/test_tools.py`
  - `docs/file-work.md`
  - `docs/file-work.zh-CN.md`
  - `docs/security.md`
  - `docs/security.zh-CN.md`
  - `tests/test_file_undo.py`
  - `docs/review-file-change-v1.json`
- **Done when**: One deterministic file-change service previews, confirms, safely creates/replaces and durably undoes text in non-Git and dirty-Git fixtures. Trusted confirmation binds the full proposal; before bytes and prepared evidence are durable before mutation; independent readback verifies actual effects. Stale proposals, revoked grants, path/identity replacement, unavailable secure I/O, quota and prepare/receipt failures produce truthful zero-effect, conflict or recovery-required outcomes. A-to-B-to-C undo works across restart through confirmed restores_change_id/restored_file_identity lineage; arbitrary same-hash replacement remains a conflict. Created-file undo only removes its owned unchanged file, duplicate undo has no repeated effect, and session compensation stops at the first conflict. Existing tool contracts and task-edit Git constraints remain unchanged. No model/UI wiring or general deletion; deep safety/rollback review is required.
- **Evidence**: exact candidate, scoped review record and relevant real-filesystem tests/artifacts; development results do not certify the release wheel.
- **Budget / stop / rollback**: retain the same package through bounded sessions and commit checkpoints; obey current ownership and runtime budgets. Stop the affected action on conflict, missing authority or unresolved side effects. Keep the last integrated product intact; runtime compensation follows file-work.md, not a Git history rewrite.
- **Approved by**: the user request of 2026-11-05 to reduce task fragmentation and the maintainer's 2026-11-09 standing routine-development delegation. This regroups the selected file-work scope; ordinary technical review, non-force branch push, PR and reviewed main integration follow [the development process](development-and-release.md#7-publication) without per-candidate human approval. Exact scope and exclusive writers still apply. New product directions, paid provider access/new credentials, third-party rights, tags, Releases, package uploads and destructive/shared-history actions remain outside this routine authority.
- **Internal checkpoints** (not separately claimable tasks):
  1. Reuse the existing proposal/persistence/apply candidate rather than rebuilding it. Map the original requirements to exact reviewed changes and missing safety evidence before selecting the next implementation change; preserve prior findings and review-round history.
  2. Complete undo/restart/compensation lineage and conflict cases before offering any default mutation entry.
  3. Review the complete service and rollback boundary together; retain one package evidence record.

#### `PC-FILE-CHAT-1` — usable chat loop and development acceptance

- **State**: `delivered` (offline development package integrated; real-provider/manual release acceptance is separate).
- **Integration evidence**: [PR #49](https://github.com/barry31-dev/pocketchat/pull/49), merged as `8ad2a9f7e22847e94dd68e81837edf3967389712`. Cancellation, real-file golden fixture and installed-development-wheel evidence are recorded in the PR and [validation guide](file-work-validation.md); secure-runtime skips are not execution claims.
- **Branch**: `task/pocketchat-file-chat`.
- **Depends on**: `PC-FILE-CHANGE-1`.
- **Write scope**:
  - `pocketchat/file_work.py`
  - `pocketchat/session.py`
  - `pocketchat/provider.py`
  - `pocketchat/tool_contracts.py`
  - `tests/test_file_work.py`
  - `tests/test_file_work_tools.py`
  - `tests/test_tool_contracts.py`
  - `tests/test_tool_calls.py`
  - `docs/file-work.md`
  - `docs/file-work.zh-CN.md`
  - `docs/formats.md`
  - `docs/formats.zh-CN.md`
  - `pocketchat/agent.py`
  - `pocketchat/cli.py`
  - `pocketchat/tui.py`
  - `pocketchat/curses_ui.py`
  - `pocketchat/repl.py`
  - `tests/test_agent.py`
  - `tests/test_file_work_cli.py`
  - `README.md`
  - `README.zh-CN.md`
  - `docs/cli.md`
  - `docs/cli.zh-CN.md`
  - `docs/compatibility.md`
  - `docs/compatibility.zh-CN.md`
  - `tests/test_file_work_e2e.py`
  - `examples/file-work-demo.py`
  - `docs/file-work-validation.md`
  - `docs/file-work-validation.zh-CN.md`
  - `docs/pocketchat-evidence-matrix.md`
  - `docs/review-file-chat-v1.json`
- **Done when**: One existing agent session supports ordinary chat, explicit grants and provider disclosure, authorized read/propose, complete diff, trusted apply, readback/receipt and undo. The model cannot immediately write, self-approve, undo or expand grants; grant/profile/session changes invalidate approval, tool results remain paired, one pending proposal and existing budgets remain enforced. Full-screen and line paths use the same service; untrusted piped input cannot approve and completed model runs cannot imply applied files. Existing scripts/JSON contracts stay compatible; independent mock imports and permission/refusal cases are covered. The package includes the golden-path fixture and development-wheel validation outside the checkout, bound to version/SHA/wheel/platform/session/run/change evidence; real-provider and manual gaps remain explicit. The cancellation behavior actually required by this path must be integrated and exercised before package acceptance. No external publication; final artifact evidence belongs to RELEASE.
- **Evidence**: exact candidate, scoped review record and relevant real-filesystem tests/artifacts; development results do not certify the release wheel.
- **Budget / stop / rollback**: retain the same package through bounded sessions and commit checkpoints; obey current ownership and runtime budgets. Stop the affected action on conflict, missing authority or unresolved side effects. Keep the last integrated product intact; runtime compensation follows file-work.md, not a Git history rewrite.
- **Approved by**: the user request of 2026-11-05 to reduce task fragmentation and the maintainer's 2026-11-09 standing routine-development delegation. This regroups the selected file-work scope; ordinary technical review, non-force branch push, PR and reviewed main integration follow [the development process](development-and-release.md#7-publication) without per-candidate human approval. Exact scope and exclusive writers still apply. New product directions, paid provider access/new credentials, third-party rights, tags, Releases, package uploads and destructive/shared-history actions remain outside this routine authority.
- **Internal checkpoints** (not separately claimable tasks):
  1. Connect read/propose and bounded receipts to Session without changing legacy create-only tools or exposing model approval.
  2. Connect both terminal paths to the same file-work service and verify grant, confirmation, cancellation and error behavior.
  3. Run the reusable golden-path fixture against real files and a development wheel; record manual/provider authority gaps without fabricating a pass.

#### `PC-FILE-RELEASE-1` — frozen artifact and publication

The routine preparation contract is [review-file-release-v1.json](review-file-release-v1.json). The task branch prepares version/package/evidence; this does not remove the original real-provider/manual acceptance or exact external-action requirements below.

- **State**: `queued` (approved bounded package; not readiness, implementation or a lease).
- **Branch**: `task/pocketchat-file-release` (serialized routine preparation; any formal frozen tag/Release remains separately authorized).
- **Depends on**: `PC-FILE-CHAT-1`.
- **Write scope**:
  - `pocketchat/__init__.py`
  - `pyproject.toml`
  - `CHANGELOG.md`
  - `README.md`
  - `README.zh-CN.md`
  - `docs/compatibility.md`
  - `docs/compatibility.zh-CN.md`
  - `docs/release-ledger.md`
  - `docs/file-work-validation.md`
  - `docs/file-work-validation.zh-CN.md`
  - `docs/pocketchat-evidence-matrix.md`
  - `docs/release-1.2.md`
  - `docs/release-1.2.zh-CN.md`
  - `docs/review-file-release-v1.json`
- **Done when**: Finish version/packaging/shipped-doc changes, freeze the final SHA/wheel, then execute the existing golden-path fixture for that artifact's real-provider/manual acceptance and required checks/CI/install. Retain evidence outside the frozen tree, obtain exact external-action authorization, publish that same artifact, and read back tag identity, Release state, wheel hash and a fresh public-wheel installation loop. Register final validation/evidence/ledger readbacks in a separate post-publication documentation commit without moving the tag; any frozen product input change requires a new freeze and proportionate verification. Use the existing build version source rather than duplicating it. A local wheel, tag-only action or main source is not publication. Package-index upload is separate, not a hidden prerequisite.
- **Evidence**: exact candidate, scoped review record and relevant real-filesystem tests/artifacts; development results do not certify the release wheel.
- **Budget / stop / rollback**: retain the same package through bounded sessions and commit checkpoints; obey current ownership and runtime budgets. Stop the affected action on conflict, missing authority or unresolved side effects. Keep the last integrated product intact; runtime compensation follows file-work.md, not a Git history rewrite.
- **Approved by**: the user request of 2026-11-05 to reduce task fragmentation and the maintainer's 2026-11-09 standing routine-development delegation. This regroups the selected file-work scope; ordinary technical review, non-force branch push, PR and reviewed main integration follow [the development process](development-and-release.md#7-publication) without per-candidate human approval. Exact scope and exclusive writers still apply. New product directions, paid provider access/new credentials, third-party rights, tags, Releases, package uploads and destructive/shared-history actions remain outside this routine authority.
- **Internal checkpoints** (not separately claimable tasks):
  1. Finish version, packaging and shipped-document changes, then freeze the final SHA and wheel.
  2. Reuse the existing fixture for this artifact's required real-provider/manual acceptance and exact-candidate checks; obtain the separate external-action authorization.
  3. Publish that artifact through the approved path, read it back and register evidence without moving the tag.

#### `PC-FILE-UNDO-1` — absorbed scheduling row

- **State**: `cancelled` (superseded scheduling unit, not cancelled functionality; effective only after the activation check above).
- **Branch**: `task/pocketchat-file-undo` (retired reservation; do not open it for duplicate work).
- **Superseded by**: `PC-FILE-CHANGE-1`, including the former tests, acceptance and failure boundaries.
- **Reason / evidence**: user requested fewer tasks on 2026-11-05; this queue revision and its scoped review record bind the regrouping. Historical commits/reviews remain valid only for their original scope. No operational lease is changed by this row.

#### `PC-FILE-TOOL-1` — absorbed scheduling row

- **State**: `cancelled` (superseded scheduling unit, not cancelled functionality; effective only after the activation check above).
- **Branch**: `task/pocketchat-file-tool` (retired reservation; do not open it for duplicate work).
- **Superseded by**: `PC-FILE-CHAT-1`, including the former tests, acceptance and failure boundaries.
- **Reason / evidence**: user requested fewer tasks on 2026-11-05; this queue revision and its scoped review record bind the regrouping. Historical commits/reviews remain valid only for their original scope. No operational lease is changed by this row.

#### `PC-FILE-E2E-1` — absorbed scheduling row

- **State**: `cancelled` (superseded scheduling unit, not cancelled functionality; effective only after the activation check above).
- **Branch**: `task/pocketchat-file-e2e` (retired reservation; do not open it for duplicate work).
- **Superseded by**: `PC-FILE-CHAT-1`, including the former tests, acceptance and failure boundaries.
- **Reason / evidence**: user requested fewer tasks on 2026-11-05; this queue revision and its scoped review record bind the regrouping. Historical commits/reviews remain valid only for their original scope. No operational lease is changed by this row.

## 3. Not a queue entry

- A branch name, a worktree, or a passing test is not an approved slice.
- "Low priority" is not a reason to skip the difference, identity or history
  audit for a change.
- The absence of a row is not an approval to start.
