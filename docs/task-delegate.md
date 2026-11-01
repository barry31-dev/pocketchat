# Parent-plan-authorized delegation

`task delegate preview` validates one `pocketchat-task-delegation.v1` request
against the TaskPlan it claims to descend from. It authorizes children; it
never runs them.

```bash
pocketchat task delegate preview deleg.json --plan plan.json --spec spec.json --json
```

## What authorization checks

The request is a closed object naming a `delegation_id`, the parent
`parent_plan_sha256`, the `parent_step_id` it hangs from, and 1–32 children.
Each child carries a unique bounded id, an objective, one action and 1–32
declared inputs.

Before anything is authorized:

- the parent plan digest must equal the authorized `ExecutionPlan` digest;
- the named parent step must exist in that plan;
- the child action must be one the TaskSpec grants (`read_file`, `write_file`,
  `memory_search`);
- every child input must be workspace-relative and inside that action's
  allowlist; a `write_file` target must additionally be an authorized
  overwrite path;
- a `memory_search` child carries exactly one literal query.

The result binds the child rows to their parent step and records the parent
spec digest. It does not widen anything: a child that asks for an undeclared
action, a path outside the parent grant, an escaping segment or an absolute
path is refused before any provider call.

## Boundaries

The request file must be a regular non-symlink file within 1 MiB, and
duplicate JSON members are refused. The preview is read-only: it creates no
task, session, trace, request, `.pocketchat` state or provider call, and it
runs no child.

Authorization is not execution. It does not start a child run, does not bound
child budgets, does not prove a child succeeded, does not protect against
concurrent writers, and does not authorize publication. A child result must
be recorded by the caller with its own evidence; a refused or failed child
must never be written back as a completed parent step.
