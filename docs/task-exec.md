# Task execution conversion v1

This unreleased slice turns one parsed TaskPlan into bounded calls to the
existing `run_task`. It does not add a second executor: provider calls, tools,
sessions, task records and traces remain owned by the current task runtime.
The published v1.1.0 artifact does not contain this module.

## Authorization

`authorize(plan, spec, expected_plan_sha256=...)` reparses the plan against the
same immutable TaskSpec. It refuses conversion unless the plan's
`spec_sha256` still matches and the plan's canonical digest equals the explicit
authorization supplied by the caller. A changed spec, mutated plan, or digest
from another plan fails before any provider or tool call.

## Step conversion

Each plan step becomes a one-action task policy:

- `read_file` and `write_file` keep only that step's `inputs`, which must be a
  subset of the TaskSpec allowlist;
- `write_file` remains create-only;
- `memory_search` carries the spec's result limit and one literal query;
- retries, arbitrary actions and free-form shell are not introduced.

The prompt names only the step objective, its action/inputs and expected
evidence. The plan's dependency order is checked before execution.

## CLI execution

`task exec --spec SPEC --plan PLAN --plan-sha256 DIGEST` is the product entry
for an operator who already has both documents. It re-parses the spec, binds the
plan to that spec, and checks the plan digest before reading the provider
configuration. It then reuses the same `execute_plan()` path described below;
the CLI does not generate a plan or widen authority. `--repair-model` is an
opt-in extension: after a failed command step it may ask the configured model
for one guarded file overwrite and retry the identical template once. Without
the flag no repair provider request is made. See [task repair](task-repair.md).

`--stop-after N` records an explicit `step_boundary` after N newly attempted
steps. A boundary is an intentional pause, so the command returns 0 with
`status: boundary`; it is not a completed task and does not imply acceptance.

Resume either directly from an exact report:

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --resume-report REPORT \
  --report-sha256 <report-digest> --config pocketchat.json --json
```

or from a checkpoint whose bound report and digest are verified before the
provider configuration is read:

```bash
pocketchat task exec --spec spec.json --plan plan.json \
  --plan-sha256 <digest> --checkpoint checkpoint.json \
  --config pocketchat.json --json
```

Completed steps are re-verified and skipped; only the never-started suffix is
attempted. A fully completed source is a verified no-op that creates no new
report. A failed/unknown prior side effect, changed report digest, mismatched
Spec/Plan, or missing evidence is refused before a new model/command call.
`--resume-report` and `--checkpoint` are mutually exclusive; a direct report
requires `--report-sha256`, and neither resume form combines with
`--stop-after`.

The JSON result contains the exact spec/plan digests, task ids, step outcomes,
and the execution report created by this invocation. A failed step returns 1
and the report remains inspectable. A completed step is not acceptance:
command evidence keeps `task_acceptance: not_evaluated`.

## Sequential execution

`execute_plan()` accepts the existing `run_task` callable explicitly, so tests
can use a stub. Steps run serially under their narrowed policies. A failed step
stops the sequence; later steps are returned as `not_executed` rather than
being silently skipped. The function returns task id, trace path and session
path for every attempted step; an attempted step must report `completed` to let
its dependents run.

This slice does not overwrite files itself, repair failures, call a real provider
or perform acceptance beyond the existing `run_task` contract. A completed
`write_file` step is summarized after the call; guarded overwrite remains a
separate TaskSpec feature. Command steps are the explicit opt-in exception.

## Command steps

A `run_command` step has no generated model policy. `execute_plan()` requires an
explicit `command_runner` and passes only the template id and trace path. A
missing runner is a refusal before execution; command success is still not a
task-acceptance pass.

## Execution report

After the steps it was allowed to attempt, `execute_plan()` writes one closed
`pocketchat-task-execution-report.v1` document under
`.pocketchat/task-reports/`. It binds the spec and plan digests, the task ids,
each step's status and trace, command archive/stream hashes and counts (never
stdout/stderr previews), and hashes plus line counts for completed write steps.
The exact command archive digest is rechecked before its evidence is attached. The report is
written before an exception from a runner propagates, so a refused or failed
step remains inspectable.

`task report` reads these reports without executing a command, generating a
provider request or writing a task record. A missing, malformed, foreign or
tampered command archive enters additive `evidence_issues` with `refused` or
`unknown`; it never becomes `task_acceptance: passed`. The report's `commands`
list contains template id, cwd, exit code, timeout flag, output hashes and
truncation flags. The `changes` list contains paths, before/after hashes, byte
counts and line counts.

## Step-boundary continuation

`stop_after=N` records an explicit never-started suffix after N newly attempted
steps. `resume_report` plus `expected_report_sha256` binds a continuation to
that exact report, Spec/Plan and completed artifact evidence; it skips the
verified prefix and leaves old evidence untouched. Failed/unknown side effects
are refused, not retried. See [task recovery](task-recovery.md) for the strict
preflight, cumulative report links and single-writer/crash limitations.

## Limits

This slice does not retry commands, repair failures, install dependencies,
delete files, call a real provider or publish a release. The executor has no
OS-level network or filesystem sandbox. The step-boundary recovery tests also ran on local macOS with CPython 3.9.6;
this is not a full 3.9 regression claim. Windows, Linux, wheel and clean-install
paths remain unverified.
