# TaskPlan v1

TaskPlan v1 is a model-generated, non-executable plan bound to one
operator-authored TaskSpec. It is unreleased source; the published v1.1.0
artifact does not contain it. Parsing a plan never runs a step, calls a tool or
expands the TaskSpec's permissions.

## Shape

```json
{
  "schema_version": "pocketchat-task-plan.v1",
  "spec_sha256": "<sha256 of the normalized TaskSpec>",
  "steps": [
    {
      "id": "read_input",
      "objective": "read the declared input",
      "action": "read_file",
      "inputs": ["input.md"],
      "expected_evidence": "the bounded file content",
      "depends_on": [],
      "retry_limit": 0
    }
  ],
  "revision": 0
}
```

The root and every step are closed objects. Step ids are unique, and every step names at least one input. Dependencies
may only name earlier steps, so a cycle cannot be represented. File actions
must be granted by the TaskSpec and their inputs must remain inside that tool's
allowlist. A `memory_search` step carries exactly one literal query; it does not
pretend the query is a file path. `retry_limit` is bounded by the existing
provider attempt ceiling and never retries a step by itself; plan generation now enforces zero retries per step until recovery has its own evidence contract.

`spec_sha256` is computed over canonical normalized TaskSpec bytes. Changing
the spec makes every old plan inexecutable, even if its step text is unchanged.
The plan itself has a separate digest over canonical plan bytes.

## Preview

```bash
pocketchat task plan preview plan.json --spec spec.json --json
```

The preview revalidates the plan against the named TaskSpec and writes no task
record, trace or session. Invalid JSON, changed spec hash, unknown fields,
unknown actions, overreach and dependency errors exit 2.

## Bounded generation

`generate_plan()` accepts a provider adapter, a config and an explicit request
directory. It records every request body in an exclusive request snapshot
before calling `complete()`. A valid plan is stored once in a plan snapshot
that carries its own digest and the basenames of the request snapshots used
for that plan; neither snapshot overwrites an existing file.

Only an invalid plan gets one revision. The revision is reparsed against the
same immutable TaskSpec, so it cannot widen paths, tools or retry limits, and
it must declare `revision: 1`. Provider failures are not revised or retried.
No valid final plan means no executable plan and no plan snapshot.

The tests use a local stub adapter only. This slice does not call a real
provider and is not evidence that a plan will be useful on an arbitrary task.
## Command steps

`run_command` is a plan action only when the TaskSpec declares command
templates. Its `inputs` must contain exactly one declared template id; the plan
cannot add argv, a shell string or an environment map. Execution requires an
explicit command runner and its evidence stays separate from task acceptance.
