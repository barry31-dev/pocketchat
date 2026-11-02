# Task agent dogfood

An offline, single-writer example of the actual PocketChat task/tool paths.
The model adapter is an in-process **stub**. Commands execute locally; no
provider credential, network route, dependency installation or publication is
used. This example is unreleased and is not part of the v1.1.0 artifact.

```bash
python3 examples/task-dogfood/demo.py
python3 examples/task-dogfood/demo.py --output /tmp/task-dogfood-evidence
```

`--output` must be absent or empty. Existing output is refused without
modification. Without this option the temporary evidence is removed on exit;
use `--output` to keep artifacts for inspection. The repair case needs Git,
creates its own fixture repository with a generic fixture identity and no
remote, and does not change the caller's checkout.

## Four paths, four bounded claims

| Case | What actually runs | What independently checks the result |
| --- | --- | --- |
| `success` | A stub tool response goes through `run_task` → create-only `write_file`; a declared command checks the file and increments a counter once | Persisted file/text and `command_succeeded` acceptance; real task tool record, execution report and command archive |
| `repair` | One fixed argv command fails on a deliberately incorrect **committed fixture**; a deterministic **operator callback** uses `snapshot_overwrite` / `apply_overwrite`; the exact same template runs once more and passes | Before/after edit hashes and line counts, bounded repair receipt, both command archives and counter `2`; a separate read task receives **file-only** acceptance |
| `recovery` | Write and command finish, `stop_after=2` leaves `finish` unstarted, then exact report/SHA-bound continuation creates `done.md` | Original task/trace/session/report/file/command bytes and mtimes are unchanged, counter stays `1`, new cumulative report links its predecessor, independent file/command/finish acceptance passes |
| `refusal` | Invalid TaskSpec is rejected before task, session, command or provider execution | A dedicated `pocketchat-dogfood-refusal.v1` receipt; trace/task/spec/plan fields are `null`, not a fabricated empty trace or an unrelated valid plan |

The report is **not** prewritten before the success/recovery tool call. The
repair runner never switches argv based on attempt count. `repair_mode` is
`operator_callback`: this does not claim that a model diagnosed or authored
the repair. Repair command evidence remains in the existing command-repair
format; it is not fabricated as an executed TaskPlan step, and file-only
acceptance is not labeled a command-acceptance pass.

## Inspectable outputs

Each stdout line is one JSON result with case/outcome, explicit stub identity,
Spec/Plan digests when applicable, task id, and workspace-relative evidence
paths. Successful cases retain `spec.json`, `plan.json`, `acceptance.json`,
task/session/trace records and an execution report whose exact SHA-256 is
included. Acceptance receipts are separate from execution status.

The repair row lists both command attempts, their template digest and argv,
archive/trace paths, edit receipt and repair receipt. The recovery row lists
preserved artifacts with before/after hashes and mtimes. The filesystem itself
contains the command counter; tests reread it rather than trusting a claimed
call count.

To read a task report without rerunning anything, start in the case workspace
and use an installed PocketChat checkout containing this unreleased change:

```bash
pocketchat task report TASK_ID --json
```

## Limits

This demonstrates these local deterministic inputs, not general agent quality.
A stub is not real-provider evidence. This demo's repair is operator-authored,
not autonomous model repair; the separate opt-in model repair path is not
exercised here. Continuation is at a confirmed step boundary, not arbitrary-crash
or concurrent exactly-once recovery. There is no OS filesystem/network sandbox,
cross-platform guarantee or release authorization. Keep output private if you
modify the fixtures to contain your own data.
