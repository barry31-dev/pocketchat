# Cooperative task cancellation

This additive, unreleased API cancels an explicitly authorized plan only at a
confirmed step boundary. It does not install signal handlers, terminate a
subprocess/provider request, change the CLI, or add an executor. An in-flight
step may finish before the request is observed.

## Owner and request

```python
from pocketchat.tasking import TaskCancellation
from pocketchat.task_exec import TaskCancelledError, execute_plan

owner = TaskCancellation(execution.spec_sha256, execution.plan_sha256)
owner.request(spec_sha256=execution.spec_sha256,
              plan_sha256=execution.plan_sha256,
              owner_token=owner.owner_token)
try:
    execute_plan(execution, run_task=run_task, workspace=workspace,
                 config=config, trace_dir=trace_dir, session_dir=session_dir,
                 command_runner=command_runner, cancellation=owner)
except TaskCancelledError as stopped:
    report_path = workspace / stopped.report
    completed_and_pending = stopped.results
```

The owner binds the exact Spec/Plan digests and an opaque, 32-character
lowercase hexadecimal token. A token is generated when omitted. Every request
checks all three values before setting its sticky flag. The first matching
request returns `True`; repeating it returns `False` and does not reset state.
A different Spec, Plan or token raises `TaskError` without changing the flag.
Neither errors nor reports include the token. `owner_sha256` identifies it by
SHA-256. This is a host-process caller capability, not authentication, a lease,
a cross-process channel, or a synchronization guarantee.

## Confirmed boundary and evidence

The executor checks a supplied owner's Spec/Plan before artifact reads or new
runner calls. With no owner its previous execution behavior remains unchanged.
Cancellation is observed before the first/new step and after the final
successful step. The existing executor still controls failed-step stopping,
dependency order, tool/command policies and budgets.

A safe cancellation preserves every completed result, task record, trace,
session, command archive and file change. It appends the never-started suffix
with `status: not_executed` and `reason: task_cancelled`, writes one existing
execution report, then raises `TaskCancelledError` (a `TaskExecutionError`
subclass) carrying that report reference and the result list. A completed
subtask remains `completed`: cancellation does not rewrite its historical fact.
Execution cancellation is not acceptance success.

The report gains one optional, closed metadata object:

```json
{
  "cancellation": {
    "schema_version": "pocketchat-task-cancellation.v1",
    "state": "cancelled",
    "spec_sha256": "<exact spec digest>",
    "plan_sha256": "<exact plan digest>",
    "owner_sha256": "<opaque owner token digest>",
    "boundary_index": 2
  }
}
```

The boundary index is the length of the confirmed completed prefix, not the
number of requests or attempted effects. Every prefix step must have complete
artifact hashes; the suffix contains only step identity/action/status/reason.
Malformed fields, duplicate step identities, mismatched digests, uncertain
artifact paths, an error/evidence issue, a failed prefix or a nonempty started
suffix refuse the cancellation boundary. A custom runner without complete
artifact evidence yields a normal execution error/report instead of claiming
a safely recoverable cancellation. A runner exception or failed step remains
failed/unknown even if cancellation was requested during that call.

The checkpoint's schema is unchanged. Its exact `report_sha256` binds the
optional cancellation metadata. Checkpoint creation accepts both the current
top-level command archive digest and the old nested declaration; conflicting
declarations are refused, not silently preferred.

## Explicit resume without repetition

Read [execution recovery](task-recovery.md) for the original evidence checks.
A cancelled report requires a **fresh, uncancelled** owner for the same
Spec/Plan. Reusing the cancelled owner/token or omitting an owner refuses
before a new model, command or report write. A newly requested fresh owner
is also refused; it cannot manufacture another successor of the same stopped
boundary.

```python
import hashlib
from pocketchat.tasking import TaskCancellation

fresh_owner = TaskCancellation(execution.spec_sha256, execution.plan_sha256)
report_digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
results = execute_plan(
    execution, run_task=run_task, workspace=workspace, config=config,
    trace_dir=trace_dir, session_dir=session_dir, command_runner=command_runner,
    cancellation=fresh_owner, resume_report=report_path,
    expected_report_sha256=report_digest,
)
```

The existing direct-report or checkpoint resolver selects the exact report.
Recovery rechecks all completed evidence and file effects, restores the
historical declared token budget, checks unused suffix outputs, and runs only
the never-started suffix. A fully completed cancelled boundary is a verified
no-op. A cumulative successor binds its predecessor digest; reading a task
report does not count the preserved command twice. Old reports without
cancellation metadata use their original `step_boundary` rules.

## Verification scope and limitations

Focused deterministic tests exercise foreign/idempotent requests, cancellation
before provider/command, after an acknowledged command, after the final step,
checkpoint/direct resume, changed historical evidence, failure/unknown effects,
closed metadata and default execution. The command fixture runs a real local
bounded Python subprocess and asserts its effect count stays one on resume;
the provider is an in-process stub, not a real service. Exact results and
interpreter scope live in `review-task-cancel-v1.json`.

This is cooperative, single-writer, local evidence checking. It does not prove
exactly-once execution across crashes, defend against an actor replacing all
artifacts and authorization, or prevent a concurrent writer after a check.
No OS signal cancellation, arbitrary subprocess termination, real provider,
new CLI command, deadline integration, tag, Release or upload is claimed.
Reverting this slice does not rewrite old reports; an older reader may refuse
the additive cancellation field and must not pretend it understood it.
