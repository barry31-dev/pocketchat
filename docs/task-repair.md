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
`not_repairable` and `succeeded` (no repair needed). A command start failure
has no exit code and both empty stream hashes; such a failure is not eligible
for model repair unless a bounded failed result can actually be fingerprinted.

The bounded result is persisted once at `.pocketchat/task-repair/<step>.json`
when a root is supplied. A second write to the same step is refused. Every
result carries `task_acceptance_pass: false`: command success is never task
acceptance.

## Limits

The operator-callback path does not modify files or contact a provider by
itself; the opt-in model path added below does both through the existing guarded
tools and provider adapter. Neither reruns acceptance, adds a general retry
framework, installs dependencies nor provides an OS-level sandbox. Only local
macOS with CPython 3.13.9 is verified for this unreleased slice; Python 3.9,
Windows, Linux, wheel and clean-install paths remain unverified.

## Model-authored repair (opt-in)

`task exec --repair-model` enables a separate model repair path for a failed
`run_command` step that reached an exit status (including a timeout). It is
disabled by default; without the flag no repair provider request is made. The
model receives the authorized spec/plan digests, the exact command template
digest, the stable failure fingerprint, bounded stdout/stderr previews and the
spec's `overwrite_paths`.

The model must return one closed JSON action with exactly `action`, `path`,
`content` and `reason`. `action` must be `overwrite_file`; `path` must exactly
match an entry in `overwrite_paths`. argv, shell, cwd, tools, provider and
additional paths cannot be supplied. The replacement is applied through the
existing `snapshot_overwrite()` and `apply_overwrite()` guard, then the same
template id, argv, cwd and declared timeout run once with a sibling retry trace.
A repair proposal that is malformed, unauthorized, stale, or contains the
configured credential causes a refusal before any file change or retry. A
provider build, evidence-write, or request failure also stops before the edit.

The model repair receipt is written once at
`.pocketchat/task-repair/<step>.json`; the request snapshot is stored separately
at `.pocketchat/task-repair/<step>.attempt-1.json` and bound by SHA-256 in the
receipt. The receipt includes `repair_mode: model`, the
spec/plan/template digests, both failure fingerprints, before/after file hashes
and line counts, bounded command facts and `task_acceptance_pass: false`. The
receipt never stores the replacement body or command output previews. The
request snapshot does contain the bounded failure context actually sent to the
provider, but not the provider response or replacement body. A successful
retry remains command evidence; it is not task acceptance.
