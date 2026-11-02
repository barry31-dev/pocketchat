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

| ID | State | Depends on | Done when | Evidence |
|---|---|---|---|---|
| `SLICE-PREP-1` | `in_progress` | — | Package can be maintained from this repository alone: process docs name a product-reachable slice location, package-level rules exist, and no doc or module depends on a private path | this file; `tests/test_package_independence.py`; [package rules](../AGENTS.md) |
| `SLICE-PREP-2` | `queued` | `SLICE-PREP-1` | Independent build, install and CI evidence recorded for the current candidate | `docs/release-ledger.md` §3 |
| `TA-CLI-EXEC-1` | `in_progress` | `SLICE-PREP-1` | A product CLI path accepts an operator TaskSpec and an explicitly authorized TaskPlan, executes the plan through the existing task runtime, and emits task/report evidence without a Python wrapper | `tests/test_task_cli_exec.py`; `docs/task-exec.md`; `CHANGELOG.md` |

Cancelled and delivered rows are moved to
[`CHANGELOG.md`](../CHANGELOG.md) rather than deleted, so a later reader can
see why a slice stopped.

## 3. Not a queue entry

- A branch name, a worktree, or a passing test is not an approved slice.
- "Low priority" is not a reason to skip the difference, identity or history
  audit for a change.
- The absence of a row is not an approval to start.
