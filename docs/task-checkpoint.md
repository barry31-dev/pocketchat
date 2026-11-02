# Plan/command checkpoints

`task checkpoint preview` validates one `pocketchat-task-checkpoint.v1`
record without running anything. A checkpoint is derived from a closed
`pocketchat-task-execution-report.v1` report and names exactly which plan
steps are known complete and which remain. It is evidence for a later resume,
not a new execution authorization.

```bash
pocketchat task checkpoint preview ckpt.json --json
pocketchat task checkpoint preview ckpt.json \
  --spec-sha256 <TaskSpec digest> --plan-sha256 <TaskPlan digest>
```

## What a checkpoint binds

A record carries the spec and plan digests, the completed/pending step
partition, `boundary_index` equal to the completed count, the task ids the
steps ran under, a bounded summary of file effects (path, new SHA-256, byte
count, baseline) and command effects (step id, template id, exit code, timeout
flag, archive SHA-256), plus the SHA-256 of the report it was derived from.

`create_checkpoint()` reads the report file and refuses when the object handed
to it differs from those bytes, when the declared report digest does not match,
when the report carries unresolved evidence issues, or when a completed step
follows an unexecuted one. A failed step is never part of the completed prefix,
and a command effect without an archive digest is refused.

`parse_checkpoint()` optionally binds the record to the execution being
resumed: `expected_spec_sha256` / `expected_plan_sha256` make a structurally
valid record that names a different authorized execution refuse. Without them
only shape is checked.

## Resuming from a checkpoint

`resume_from_checkpoint(root, path)` turns a checkpoint into the exact report
and digest to resume from, so a caller never guesses which report is current.
The returned `report_path` is workspace-relative and must live directly under
`.pocketchat/task-reports/`; a checkpoint cannot name an arbitrary file as its
own evidence. The report's bytes are hashed and must equal the recorded
`report_sha256`, and the optional expected spec/plan digests must match.

```bash
pocketchat task checkpoint resume ckpt.json \
  --spec-sha256 <TaskSpec digest> --plan-sha256 <TaskPlan digest> --json
```

The preview is read-only. `task exec --checkpoint ckpt.json` resolves the same
bound report and digest for the existing `execute_plan(..., resume_report=...,
expected_report_sha256=...)`; completed steps are re-verified rather than
replayed. A direct `task exec --resume-report ... --report-sha256 ...` is the
equivalent path when the operator already holds the exact report digest.

## Boundaries

Both the checkpoint file and the report it names must be regular non-symlink
files within 1 MiB / the report reader's own bound. Duplicate JSON members and
a false CLI envelope are refused. `task checkpoint preview` is read-only: it
creates no task, session, trace, request, `.pocketchat` state or provider call,
and it never executes a step, re-runs a command or re-reads file contents.

Resuming from a checkpoint does not prove the workspace still matches the
recorded file hashes beyond what the existing recovery checks re-verify, does
not resume from an arbitrary crash, does not protect against concurrent
writers, and does not authorize publication.

A checkpoint proves which steps a report claimed complete and with which
hashes. It does not prove the workspace still matches those hashes, does not
replay a step, does not protect against concurrent writers or cross-process
replacement, does not resume a task by itself, and does not authorize
publication.
