# Deterministic task acceptance

Execution `completed` means the bounded model/tool run ended successfully. It
does not mean the requested artifact meets its goal. Acceptance is an opt-in,
operator-declared set of local conditions, evaluated without a model or shell.
This is unreleased source functionality; the published v1.1.0 artifact does not
contain these commands or formats.

## Run and verify

```bash
pocketchat task run "Write report.md and summary.json" \
  --policy policy.json --session audit \
  --acceptance examples/task-acceptance/acceptance.json --json
pocketchat task verify TASK_ID --acceptance acceptance.json --json
pocketchat task report TASK_ID --json
```

`--acceptance` is optional on `task run`. Without it, existing execution
fields and exit codes do not change. With it, input is parsed before the first
model call and a successfully completed task is checked once. `task verify`
checks an already `completed` task, appends another receipt and never calls a
provider, changes a task, or modifies an artifact. Active/failed/cancelled tasks
cannot be verified. An execution failure returns `acceptance.state: not_run`
without creating an acceptance receipt.

| Command outcome | Exit | `ok` |
|---|---:|---|
| Execution completed and all declared checks `passed` | 0 | true |
| Execution failure or acceptance `failed`, `refused` or `unknown` | 1 | false |
| Invalid plan/task, unsafe receipt storage or other input refusal | 2 | false |

`task run` still reports execution in `status` and acceptance separately in
`acceptance.state`. A persistence failure after execution gives `unknown`, no
receipt path and exit 1. JSON is one object on stdout; handled errors leave
stderr empty. Parser errors such as a missing required flag use argparse's
ordinary stderr/exit 2. Human output prints both states.

## Operator plan

The format is `pocketchat-acceptance-plan.v1`:

```json
{
  "schema_version": "pocketchat-acceptance-plan.v1",
  "max_file_bytes": 65536,
  "checks": [
    {"id": "report", "kind": "file_exists", "path": "report.md"},
    {"id": "heading", "kind": "text_contains", "path": "report.md", "text": "## Findings"},
    {"id": "ready", "kind": "json_equals", "path": "summary.json", "pointer": "/status", "expected": "ready"},
    {"id": "tests", "kind": "command_succeeded", "step_id": "run_tests"}
  ]
}
```

- 1–32 checks; unique ASCII identifiers of at most 64 characters. Unknown
  fields, kinds or versions are refused. Plan JSON is limited to 65536 bytes;
  duplicate object keys, non-finite values and nesting beyond 64 are refused.
- `max_file_bytes` defaults to 65536; explicit values are integers from 1 to
  1048576. Each path is read at most once in a verification. Even `file_exists`
  reads a bounded ordinary file to record its complete byte count and SHA-256.
- `text_contains` requires 1–4096 characters, strict UTF-8 and an exact,
  case-sensitive substring. It is not a regular expression or quality score.
- `command_succeeded` reads an existing `.pocketchat/task-reports/` step and
  accepts only a completed, non-timeout command. It rechecks the archive and
  never reruns the command. Missing, unreadable or foreign evidence is
  `unknown`/`refused`; a nonzero exit is `failed`.
- `json_equals` parses finite, duplicate-free JSON, resolves an RFC 6901 JSON
  Pointer and compares JSON values recursively. `true` is not `1`; `1.0` may
  equal `1`. The empty pointer selects the document. `~0`/`~1` decode `~`/`/`;
  array indexes are canonical non-negative integers, not `01` or `-`.

Paths are relative to the task record's `workspace_root`. The plan grants
read-only access to exactly its declared artifacts; it does not grant the model
new tools or add to its read/write allowlist. File paths, limits, checks and
expected values are supplied by the operator, not by a model response.

## Safety and result states

Absolute/traversing/Windows-drive paths, backslashes, ambiguous path segments
and control characters are refused as input. Sensitive names, `.env*`,
`pocketchat.json`, `.pocketchat`, `traces`, `requests` and `sessions` are protected
at evaluation time. Symbolic links (including parents), directories and special
files are refused; over-limit files are not hashed or clipped into a pass.
Descriptor-relative no-follow reads pin the selected directory on supported
POSIX systems. Platforms without those APIs return `unsupported_platform` and
`refused`; they do not silently fall back to unsafe reads. Windows support for
this new verifier is **not verified and currently refused**.

Missing files, invalid UTF-8/JSON, missing pointers or mismatched values are
`failed`. Permission/size/platform refusals are `refused`; inaccessible files or
observed concurrent changes are `unknown`. Aggregation is
`refused > unknown > failed > passed`: one successful condition cannot hide a
refusal. No artifact body, text needle or expected value is copied to the CLI
result. The on-disk receipt contains the operator's plan, so that input should
not contain credentials or private comparison values intended to remain secret.

## Historical receipt and report

Receipts use `pocketchat-acceptance.v1` and are exclusively appended at:

```text
.pocketchat/acceptance/<task-id>/<acceptance-id>.json
```

Each records task/policy identity, canonical task-record and plan SHA-256,
execution state, workspace root, recorded time, plan snapshot, each result and
complete file bytes/SHA-256 when a file was read. Verification never rewrites
the task state, session, checkpoint, request snapshot, old receipt or trace.
New verification creates a different `acceptance_id` even under the same plan.

`task report` does not recheck artifacts. It reads existing receipts, validates
closed shapes, task binding, plan hash and aggregate consistency, and names bad
or unavailable evidence in `acceptance.evidence_issues`. No receipts means
`not_recorded`, never `passed`. The latest recorded instant (normalized to UTC, not string order) determines
the reported state; conflicting latest states at the same instant are `unknown`
(`ambiguous_record_order`), not a guessed ordering. At most 256 receipts are
read; exceeding that bound reports `history_limit_exceeded`, not a partial pass.
A changed task-record digest invalidates its old receipts. Changed artifacts do
not retroactively change historical observations: run `task verify` again to
observe current files. If the safe acceptance reader is unavailable, the
execution report remains available with `unknown` / `acceptance_unavailable`
in the optional acceptance section. `task report` exit 0 still means the report was read,
not that acceptance passed.

A receipt is **not a signature**. An editable record and recomputable hash do
not provide adversarial tamper proof, artifact authorship, semantic task success
or a filesystem sandbox. Pre-existing files may satisfy checks; passing proves
only the declared conditions at the recorded bounded local observation.

## Offline demo

```bash
python3 examples/task-acceptance/demo.py
python3 examples/task-acceptance/demo.py --output /tmp/pocketchat-acceptance-demo
```

The four deterministic cases are success, missing artifact, JSON mismatch and
protected-path refusal. Their model adapters are in-process stubs, not real
provider evidence. With `--output`, keep task/session/trace/request/acceptance
artifacts for inspection; use a new or empty output directory.

There is no shell, code execution, network tool, automatic scoring, artifact
repair, automatic retry, UI or public release in this feature.
