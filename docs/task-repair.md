# Bounded command repair v1

This unreleased slice adds one opt-in repair after a failed command step. It
does not retry arbitrary tasks, regenerate a plan or change task acceptance.

## Policy

`run_bounded_repair()` runs the declared template once. If it fails, a caller
receives only the bounded command summary and returns an explicit
`RepairDecision`. A decision to repair may rerun the same template id exactly
once with a sibling trace (`<step>.retry-1.jsonl`). It cannot inject a shell
string, another template or a different cwd.

A stable failure fingerprint is SHA-256 over exit status, timeout and the two
complete output hashes. It does not include output previews. If the retry has
the same fingerprint, the outcome is `same_failure` and the loop stops; no
third attempt exists. Other outcomes are `repaired`, `retry_failed`,
`not_repairable` and `succeeded` (no repair needed).

The bounded result is persisted once at `.pocketchat/task-repair/<step>.json`
when a root is supplied. A second write to the same step is refused. Every
result carries `task_acceptance_pass: false`: command success is never task
acceptance.

## Limits

This slice does not modify files, rerun acceptance, contact a provider, use a
real provider, add a general retry framework, install dependencies or provide
an OS-level sandbox. Only local macOS with CPython 3.13.9 is verified; Python
3.9, Windows, Linux, wheel and clean-install paths remain unverified.
