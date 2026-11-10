# Source-bound code repair

One opt-in code-repair path uses the existing `task exec --repair-model`
command templates, guarded editor, execution reports and exact-boundary resume.
There is no new code-agent runtime, shell surface or automatic permission grant.
The slice contract and review evidence are in [review-code-repair-v1.json](review-code-repair-v1.json).

## Run the offline Swift fixture

From the product root, with an existing Swift toolchain and Git:

```bash
python3 examples/code-repair-demo.py --output /tmp/pocketchat-code-repair-evidence
```

The output must be new or empty and outside an existing Git checkout. The
fixture creates its own isolated local Git baseline and a small Swift Package
with **no external dependencies**. It runs `swift test --jobs 1` through one
explicit TaskSpec command template; each command has a 180-second timeout.
Nothing is installed, no simulator starts, and no server, network provider or
credential is used. The command runner is **not an OS-level sandbox**; the
operator still owns the consequences of a declared executable.

The deterministic offline repair adapter changes a single known half-open
range to a closed range using the actual source snapshot in its request. The
first real Swift test fails, the guarded replacement is applied, and the same
template runs once more. This verifies the fixture's compilation/test result
and local orchestration, **not real-model diagnosis quality or arbitrary code
correctness**. Portable Python tests of the fixture use an explicitly supplied
small local validator instead; they do not claim Swift compilation.

The output includes:

- `spec.json`, `plan.json`, and `acceptance.json`: exact declared authority;
- the Swift source and tests: actual candidate bytes;
- `repair.diff`: the actual Git diff against the fixture baseline;
- `traces/`: separate first-failure and successful-retry traces and command archives;
- `.pocketchat/task-repair/`: the repair request snapshot and immutable receipt;
- `.pocketchat/task-reports/`: the execution report;
- `execution.json`, `resume.json`, and `evidence.json`: observed CLI results,
  seven fixture checks, source/report digests and residual limits.

`task_acceptance_pass` on a repair receipt remains false. Independent file and
command acceptance must pass separately. A command exit 0 is not a product
release, a provider-quality claim or permission to publish a package.

## Apply the path to an existing local project

1. Describe the defect in an explicit TaskSpec/TaskPlan. Name a small set of
   existing source files in `inputs` and `overwrite_paths`, and separately grant
   those write paths. Review the exact local command template: argv, cwd,
   timeout and output limit. A model cannot replace it or supply a shell string.
2. Authorize the exact plan digest and enable `--repair-model` only when wanted.
   The failed command must reach an exit status before model repair is eligible.
3. Before provider work, the runtime takes clean, regular, UTF-8 snapshots of
   explicitly overwritable files. Dirty, missing or unsafe unused grants are
   not forwarded; selecting one is refused. The bounded request carries actual
   source content, path, byte count and SHA-256 together with the real failure
   fingerprint. Source and command text are untrusted data, not instructions.
4. The complete request must fit the task context budget and normal provider
   admission. Configured credential content is refused before payload creation.
   The returned closed action may change one granted, observed path only. Apply
   uses the **original request-time snapshot**: a concurrent edit is refused,
   even if somebody commits that edit while the provider is responding.
5. Inspect the real diff and candidate, first/retry command results and separate
   acceptance. There is one repair/retry only; repeated failure does not open a
   repair loop or authorize another command.
6. Resume with the exact execution-report path and byte digest using
   `--resume-report` and `--report-sha256`. For a model-repaired command the
   successful retry is the completed step, while the first failure, repair
   request, receipt and repaired source remain bound. Changed or missing
   evidence/source is refused before any next step. A fully completed resume
   is a checked no-op: no command, provider, edit or successor report is replayed.

Older model-repair reports without these complete bindings are not upgraded
silently and cannot authorize this recovery path. Other command/read/write
boundaries retain their existing contract. Real-provider quality, arbitrary
projects, other-platform manual Swift execution and formal publication remain
separate, unverified boundaries. See [task-command.md](task-command.md),
[task-edit.md](task-edit.md), [task-repair.md](task-repair.md), and
[task-recovery.md](task-recovery.md) for the underlying contracts.

### Current local verification boundary

The first local run used the selected Command Line Tools toolchain and failed
to resolve `XCTest` before test execution; the one permitted retry also failed.
That run is **not** a successful Swift acceptance. Rebind an existing full
testing toolchain and SDK explicitly in the operator command template before
claiming the real Swift path. No global developer selection is changed by this
fixture, and no missing component is installed automatically.
