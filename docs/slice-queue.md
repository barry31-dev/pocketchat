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
| `depends_on` | slice ids that must be settled first |
| `done_when` | the observable completion condition |
| `evidence` | where the proof lives (commit, trace, test, artifact) |
| `approved_by` | who approved it. **A machine check that this file changed is a precondition, never an approval.** |

`queued` is a pool state, not readiness. `ready` additionally requires that
dependencies are settled and the budget is available.

A product-code change must update the current row before it can enter `main`.
Documentation, process-tool and test-only maintenance does not create a
product slice.

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
| `PC-PWINDOW-1` | `delivered` | `PC-ADMIT-1` | A profile can declare a model context window, output reserve, safety margin and output-capability support; known windows reduce the effective input limit, unknown values stay unknown, and max_tokens is sent only when the profile explicitly supports it. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-pwindow-1` | `tests/test_profiles.py`; `tests/test_profile_admin.py`; `tests/test_request_admission.py`; `tests/test_request.py`; `pocketchat/config.py`; `pocketchat/provider.py`; `pocketchat/request_admission.py`; `CHANGELOG.md` |
| `PC-TBUDGET-1` | `delivered` | `PC-ADMIT-1` | A pure versioned task-budget ledger reserves before a request, settles once with reported usage, keeps unknown usage reserved and marked unknown, and rejects duplicate or over-limit requests. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-1` | `tests/test_task_budget.py`; `pocketchat/task_budget.py`; `CHANGELOG.md` |
| `PC-TBUDGET-2` | `delivered` | `PC-TBUDGET-1`, `PC-ADMIT-2` | `task exec` creates one task-budget ledger from optional `max_task_tokens` and passes it through every authorized step; Session reserves before each model request and settles reported usage, while missing usage fails closed. Existing per-turn and per-Session limits are unchanged. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-2` | `tests/test_task_spec.py`; `tests/test_task_run.py`; `tests/test_task_exec.py`; `tests/test_task_cli_exec.py`; `docs/task-spec.md`; `docs/task-spec.zh-CN.md`; `CHANGELOG.md` |
| `PC-TBUDGET-2A` | `delivered` | `PC-TBUDGET-2` | Execution reports persist the optional whole-task budget ledger; report/checkpoint resume restores the same limit and request history, and refuses a missing, malformed or mismatched ledger before new work. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_task_cli_resume.py`; `tests/test_task_exec.py`; `pocketchat/task_exec.py`; `docs/task-exec.md`; `docs/task-spec.md`; `CHANGELOG.md` |
| `PC-TBUDGET-3` | `delivered` | `PC-TBUDGET-2`, `PC-ADMIT-3` | TaskPlan generation reserves and settles its attempts against an optional generation budget, refuses an over-limit attempt before a provider call, and does not persist that generation limit into the generated plan or its execution authorization. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-3` | `tests/test_task_plan_generate.py`; `pocketchat/task_plan.py`; `pocketchat/cli.py`; `CHANGELOG.md` |
| `PC-TBUDGET-4` | `delivered` | `PC-TBUDGET-3`, `PC-ADMIT-4` | Model-repair provider usage is reserved and settled against the same whole-task ledger; an exhausted budget leaves the target file and retry untouched, while the existing one-repair/one-retry authority does not reset the total. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-tbudget-4` | `tests/test_task_model_repair.py`; `pocketchat/task_model_repair.py`; `pocketchat/task_exec.py`; `CHANGELOG.md` |
| `PC-CTXSTATE-1` | `delivered` | `PC-ADMIT-2`, `PC-RESULT-2` | A pure task-state projector admits only bound, completed dependency evidence and declared file evidence; it refuses unresolved evidence, unfinished dependencies, malformed paths and oversized projections, and excludes unrelated steps. `approved_by`: user directive of 2026-11-03 to implement the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-ctxstate-1` | `tests/test_task_state.py`; `pocketchat/task_state.py`; `CHANGELOG.md` |
| `PC-CTXPROJ-1` | `delivered` | `PC-CTXSTATE-1` | Live layered context projection preserves fixed instructions and task state, keeps complete recent exchanges and selected evidence, writes a bounded sidecar manifest, and makes `context preview` select the same messages as the actual request. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_context_projection.py`; `tests/test_context.py`; `pocketchat/context_projection.py`; `pocketchat/context.py`; `pocketchat/session.py`; `CHANGELOG.md` |
| `PC-META-1` | `delivered` | `PC-MSTORE-2` | A closed v2 memory record parser validates identity, scope, content digest, digest-bound source references and lifecycle metadata; v1 records migrate only through an explicit pure conversion that leaves the source unchanged, marks legacy records unverified candidates, refuses unknown fields and unsafe targets, and writes a new bounded file exclusively. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan | `tests/test_memory_migration.py`; `pocketchat/memory_migration.py`; `docs/formats.md`; `CHANGELOG.md` |
| `PC-MRETRIEVE-1` | `delivered` | `PC-META-1`, `PC-RESULT-1` | A pure memory-v2 retrieval core applies workspace, lifecycle and sensitive admission before deterministic lexical ranking and Top-K; fixed English/CJK tokenization and dual byte/token excerpt budgets produce bounded excerpts, while stable exclusion, omission and cursor semantics leave legacy search and the CLI unchanged. `approved_by`: user directive of 2026-11-03 to continue the PocketChat runtime plan, reviewed against `sys:2026-11-03:pc-mretrieve-1` | `tests/test_memory_retrieval.py`; `pocketchat/memory_retrieval.py`; `docs/memory-retrieval.md`; `docs/memory-retrieval.zh-CN.md`; `docs/review-memory-retrieval-v1.json`; `CHANGELOG.md` |
| `PC-RUNTIME-HARDEN-1` | `delivered` | Original runtime candidate | Whole-task charging uses reported usage; missing usage stops before side effects; budgeted resume restores its persisted ledger; memory envelopes stay bounded; orphan and out-of-order tool results are refused. `approved_by`: user directive of 2026-11-03 to continue the approved runtime plan | `tests/test_task_budget.py`; `tests/test_memory_tools.py`; `tests/test_task_cli_resume.py`; `tests/test_task_model_repair.py`; `tests/test_task_plan_generate.py`; `tests/test_context_projection.py`; `docs/review-runtime-admission-v1.json` |


Cancelled and delivered rows are moved to
[`CHANGELOG.md`](../CHANGELOG.md) rather than deleted, so a later reader can
see why a slice stopped.

## 3. Not a queue entry

- A branch name, a worktree, or a passing test is not an approved slice.
- "Low priority" is not a reason to skip the difference, identity or history
  audit for a change.
- The absence of a row is not an approval to start.
