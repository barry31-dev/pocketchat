# TaskSpec v1

TaskSpec v1 is an operator-authored, read-only authorization document. It is
parsed strictly and converted to the existing `pocketchat-task-policy.v1`; it
does not introduce a second executor or permission interpreter. The published
v1.1.0 artifact does not contain this unreleased source capability.

## Shape

```json
{
  "schema_version": "pocketchat-task-spec.v1",
  "objective": "write one bounded report",
  "workspace_root": ".",
  "inputs": ["input.md"],
  "budgets": {
    "max_steps": 8,
    "max_tool_calls": 4,
    "max_context_chars": 8000,
    "max_turn_tokens": 2000,
    "max_session_tokens": 6000
  },
  "tools": {"read_file": {"allow_paths": ["input.md"]}},
  "commands": [],
  "acceptance": {"path": "acceptance.json"}
}
```

All root fields are required and the shape is closed. `inputs` contains only
workspace-relative files the task may read. `workspace_root` and acceptance
paths are workspace-relative too; absolute paths, backslashes, empty segments,
`.` segments and `..` are refused rather than normalized.

Budgets use the same ceilings and type rules as the existing policy. The
supported tools are `read_file`, create-only `write_file` and bounded
`memory_search`. A read allowlist must stay inside the declared inputs.
`write_file.create_only` may only be `true`; delete, free-form shell strings
and unknown tools are refused. `commands` is a closed list of argv templates;
each template is validated and referenced by id from a TaskPlan (see
[task-command.md](task-command.md)). The optional `overwrite_paths` field names existing files
that the separate guarded editor may replace; every entry must also be in
`inputs` and `tools.write_file.allow_paths` (see
[task-edit.md](task-edit.md)).

## Preview

```bash
pocketchat task spec preview spec.json --json
```

The preview validates the document, calls the existing policy parser through
`to_policy()`, and prints the equivalent policy. It does not create a task
record, trace, session or request snapshot and never calls a provider. Invalid
input exits 2 with a stable JSON error object; it is not a successful zero.

TaskPlan and guarded overwrite are separate opt-in modules. Command templates
are also a separate opt-in execution slice; the preview still writes nothing and
calls no provider. Those later slices require their own evidence.
