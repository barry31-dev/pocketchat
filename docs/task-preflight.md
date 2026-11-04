# Task preflight

`pocketchat.task_preflight.check_task_preflight(spec, plan, snapshot, observation)`
is a pure check over a caller's observations. It does not run Git, open a
workspace path, start a command, contact a provider or authorize execution. A
successful report says only that the supplied observations are complete and
internally consistent; the caller remains responsible for collecting them.

## Inputs

`spec` and `plan` must already be parsed `TaskSpec` and `TaskPlan` objects. The
function revalidates their in-memory sources with the existing parsers and
reuses `spec_sha256()` and `plan_sha256()`; it does not load or execute either
document. This revalidation matters because the parsed objects contain mutable
mappings. The plan must bind to this exact spec, and each file step must satisfy
its action-specific read or write grant. `snapshot` supplies the existing
task-snapshot document and its `snapshot_sha256`. `observation` is a closed
mapping with these fields:

```json
{
  "workspace_id": "checkout-a",
  "workspace_roots": ["checkout-a"],
  "base_commit": "<40 or 64 lowercase hex>",
  "head_commit": "<40 or 64 lowercase hex>",
  "is_git": true,
  "tracked_paths": ["README.md"],
  "untracked_paths": [],
  "dirty_paths": [],
  "write_paths": ["docs/out.md"],
  "dirty_coverage": "complete",
  "source_coverage": "complete",
  "spec_sha256": "<spec digest>",
  "plan_sha256": "<plan digest>",
  "snapshot_sha256": "<snapshot digest>"
}
```

All path lists are workspace-relative. Required `tracked_paths`,
`untracked_paths` and `dirty_paths` fields must be explicit lists: a missing
field is `unknown` with an omission even if coverage is labelled complete; a
null or otherwise malformed value is refused. `is_git` must be a boolean.
Path sets are sorted for deterministic checking.
`workspace_roots` names the workspace identities covered by the observation;
`workspace_id` must be exactly one of them. A missing or foreign identity is
refused rather than merged into the current workspace. The caller must set
`dirty_coverage` and `source_coverage` explicitly. An empty
`dirty_paths` list with `dirty_coverage: "partial"` is `unknown`; an empty list
is never treated as proof that a scan completed. The module does not infer a
Git state from an empty list, and it does not turn a non-Git workspace into a
clean workspace.

`write_paths` is optional: when omitted, the planned `write_file` inputs are
derived only from validated `write_file` steps. If supplied, the set must equal
the planned writes, including when the supplied list is empty. A `read_file`
input is never a planned write. The TaskSpec's action-specific allowlists remain
the authority.

## Result

The report has `schema_version: 1`, a stable sorted list of refusals,
unknowns and omissions, a path count, and explicit limits. Its status is:

| status | meaning |
|---|---|
| `allow` | Required observations are complete and consistent, HEAD is stable, and all planned writes are inside the declared scope. |
| `refused` | A supplied identity, digest or path contradicts the plan or TaskSpec, or a limit is exceeded. |
| `unknown` | A required observation is missing, partial or not a Git observation; absence is not treated as a clean result. |

The context-snapshot schema is checked and its canonical body digest is
recomputed with the existing snapshot helper. A body/digest mismatch or a
mismatch with the observation is `refused`; a missing snapshot digest is
`unknown`. Git object IDs accept 40- or 64-character lowercase hexadecimal;
document digests remain SHA-256 only. A missing `plan_sha256`, spec digest or
HEAD is likewise `unknown`. Path scope checks reject ancestor/descendant
overlap and case collisions before plan-membership checks, and dirty or
untracked paths outside the declared write scope are refused with a named
reason.

The function never mutates its inputs, never copies free-form objectives or
request/message/credential bodies into the report, and refuses rather than
truncates above 4096 path observations or a 64 KiB canonical report. The path
count is the raw total across tracked, untracked, dirty and supplied writes
(or derived planned writes when omitted), checked before deduplication. Repeated
paths consume budget; grants are not additional observations. Parser failures
use fixed diagnostics rather than copying document content into the report.

## Non-goals

This is not a live Git preflight, an executor authorization token, a file
reader, a command runner, a provider adapter or a CLI. A real workspace
observation and any execution wiring require a separate bounded slice. The
report proves consistency of the supplied observations only.
