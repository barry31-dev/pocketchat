# Task recovery evidence and step-boundary resume

This unreleased functionality has two distinct entry points: a historical
inventory and a strict execution recovery boundary. An inventory is not an
execution authorization. The published v1.1.0 artifact does not include this
plan-recovery interface.

## Historical inventory

`completed_side_effects(root, task_id, planned=[...])` reads execution reports
naming the task and lists completed step ids without running anything.
`remaining_steps` preserves the caller's order. Missing or malformed evidence
is reported as unknown/refused. This compatibility helper does **not** bind a
Spec, Plan, task record or current file contents; do not use its ids to skip
steps in a new execution.

## Explicit execution boundary

Use the already authorized `ExecutionPlan` with the existing executor:

```python
import hashlib

from pocketchat.task_exec import execute_plan

# stop_after counts newly attempted plan steps, not provider turns.
results = execute_plan(
    execution, run_task=run_task, workspace=workspace, config=config,
    trace_dir=workspace / "traces", session_dir=workspace / "sessions",
    command_runner=command_runner, stop_after=2,
)

# Select the exact report created by that run; never guess the latest file.
report_sha256 = hashlib.sha256(report.read_bytes()).hexdigest()
results = execute_plan(
    execution, run_task=run_task, workspace=workspace, config=config,
    trace_dir=workspace / "traces", session_dir=workspace / "sessions",
    command_runner=command_runner,
    resume_report=report, expected_report_sha256=report_sha256,
)
```

The selected report must live directly under `.pocketchat/task-reports/`.
Its filename/id, exact SHA-256, Spec digest, Plan digest and complete ordered
step list must match. A recoverable report contains a completed prefix and a
suffix marked `not_executed` with reason `step_boundary`. `stop_after` must be
a positive integer no larger than the plan; on resume it counts only new
steps. It can create another boundary for a later resume.

Before any new provider request, command, session or report write, recovery
checks every completed step:

- exact trace and task-record/session or command-archive bytes were hashed
  at the original execution boundary and still match; trace JSONL must have
  one completed terminal and matching run/task identity, and the session must
  contain complete message pairs and point to that same trace;
- a task record belongs to this step's narrowed policy and records exactly
  one successful authorized tool action; a model's final message alone is
  insufficient;
- completed writes have one recorded content hash/byte count and the current
  file still matches; external edits, missing files and symlinks refuse;
- completed commands match the plan's template id and run id, their archive
  and report summary agree, exit code is zero and timeout is false;
- task ids agree with the report, and no unresolved evidence issue or runner
  exception is present.

Evidence reads reuse the bounded, no-follow reader (1 MiB per artifact) and
reject nonregular files, symlink traversal and changed-during-read files.
A pending step's trace/session/command-archive and create-only file paths must
not already exist;
its output ancestors must not be symlinks. Existing artifacts are not quietly
appended, overwritten or treated as a fresh attempt. Command archives live
beside their traces, including in a caller-selected nondefault directory.

## Reports and non-repetition

Completed results keep their original task ids, trace/session paths and
artifact hashes. Only the never-started suffix is executed, with the same
one-action policies and declared command runner. A new cumulative report
contains all results and file changes, plus a `recovery` link holding the
predecessor report's relative path and exact digest. Old reports and traces
are left unchanged. Resuming a fully completed report is a verified no-op: it
returns the original results without creating another cumulative report.

`task report` follows valid cumulative links rather than counting the same
command twice. The predecessor is superseded only if its bytes, Spec/Plan
identity and completed results/changes are preserved. Missing/conflicting
links or multiple successors produce evidence issues. Command acceptance uses the same cumulative view, so an old unstarted
boundary does not override a later confirmed command. Reading the report
never reruns commands or acceptance.

## Refusals and limits

- Failed/cancelled steps and unknown in-flight effects are **not** retried by
  this interface, even when a command is believed to be idempotent. Use a
  separately authorized repair or investigate the evidence first.
- Older reports without complete artifact hashes remain inspectable, but
  cannot authorize execution recovery. No hashes are backfilled on resume.
- This is a local, single-writer, explicit step-boundary mechanism, not an
  exactly-once transaction across arbitrary crashes, processes or machines.
  There is no OS filesystem/network sandbox or protection against a concurrent
  writer after the checks. A digest is a local integrity commitment, not a
  signature against an actor who can replace all evidence and authorization.
- Acceptance remains separate; completion does not mean acceptance passed.
  No new CLI command, provider selection, automatic repair or release is
  introduced. Real providers and Windows/Linux operation remain unverified.
