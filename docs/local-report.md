# Local material, report, revision and recovery

> **Reviewed source candidate:** Session results are cross-bound to the pinned
> read trace completion, and task-record workspace roots are cross-bound to the
> authorized read policy. The two red regressions turned green; the focused
> 63-test group and offline fixture pass on macOS CPython 3.9.6. Exact-head CI
> and product-main readback remain pending, and this is not a formal release.
> See [review-local-report-v1.json](review-local-report-v1.json).

## Slice contract: PC-LOCAL-REPORT-1

Goal: through the existing authorized `task exec` CLI, read explicit local
UTF-8 sources, supply their actual observations to a dependent report step,
create a cited text report, revise it into a separate artifact, and resume a
completed prefix without replaying its reads or writes. This is the delegated
routine-development decision of 2026-11-09, within the existing single-agent
local-material/report direction; it is not a new research runtime.

Not doing: new tools, automatic planning, overwrite authority, network research,
complex-format parsing, provider-quality certification or formal publication.
Scope: the source projector, its task-execution call site, focused tests, this
example and documentation, README counterparts, changelog and review record.
The exact ten paths are in `review-local-report-v1.json`.

Done when: real-file tests and a reusable CLI fixture verify source content and
path/digest handoff, report and versioned revision, history preservation, exact
resume and refusal before dependent writes on unusable source evidence. The
candidate also needs scoped technical review, required exact-head CI and main
readback. Budget: one bounded slice; preserve an exact checkpoint on interruption.
Rollback: revert this change; existing create-only report artifacts are not
silently replaced. Publication status stays in [release-ledger.md](release-ledger.md).

## Run the offline CLI fixture

From the product root:

```bash
python3 examples/local-report-demo.py --output /tmp/pocketchat-local-report-evidence
```

The output directory must be new or empty. The fixture runs the real `task exec`
CLI, TaskSpec/TaskPlan authorization, Session, local file tools, execution reports
and resume validation. Only config loading and the provider adapter are injected
in-process; profile selection still uses the real validator. No server, network,
account or credential is used. It produces source files, plans/specs, sessions,
traces, reports, a separate revised report, and `evidence.json` with six checks.

The provider is deterministic and builds fixture reports from the forwarded
source content rather than canned source facts. Its green result verifies local
orchestration and artifact provenance, **not** real-model reasoning, citation
quality or factual correctness. The example does not install dependencies.

## Use the existing task interface

1. Put the question in the report step's `objective` of an explicit TaskPlan.
2. Declare each source in the TaskSpec read allowlist; use one `read_file` step
   per input. Declare the output separately in the create-only write allowlist.
3. The `write_file` step must explicitly depend on each read it will use.
4. Review the spec/plan and authorize their exact plan SHA using `task exec
   --spec … --plan … --plan-sha256 … --json`. A changed question, grant or plan
   needs its new digest; no source text grants additional tools.
5. Check actual report bytes and citations against the supplied observations.
   Existing artifact acceptance can check file/text/JSON conditions separately
   from execution completion; see [task-acceptance.md](task-acceptance.md).
6. For a requested revision, read the previous report as an explicit input and
   write a new output name with distinct step IDs. The predecessor remains
   unchanged; there is no new overwrite or undo command.
7. At a boundary, save the execution-report path and exact byte digest, then use
   `--resume-report` together with `--report-sha256`. Already completed reads and
   writes are not replayed. See [task-recovery.md](task-recovery.md).

## What the next step actually sees

Only explicitly depended-on, completed `read_file` observations enter the
report prompt. Each source has the observed UTF-8 content, workspace-relative
path, byte count and content SHA-256, plus pinned session/trace references and
hashes. Existing task-record/policy/trace/session checks must agree, including
the actual completed read path; another allowlisted path cannot impersonate it.
Assistant prose and non-dependency results are not source evidence.

These are **historical read snapshots**, not a fresh verification of the live
file. Resume consumes the already pinned tool result even if the input later
changes. Changed or missing bound artifacts, symlinked evidence, conflicting
source identity, truncated content and excessive combined context refuse before
the report provider/write. Source JSON is bounded to 12,000 characters and the
whole added prompt must fit the existing step context budget; it is not clipped
silently. A missing input stops the original read rather than inventing data.

The separate `pocketchat-task-sources.v1` prompt envelope is additive; the pure
`pocketchat-task-state.v1` metadata and external execution-report schema are not
changed. Read-only steps and writes without read dependencies keep their old
prompt path. Custom runners or old artifacts without complete pinned read
observations cannot support this new source-grounded report handoff.

## Evidence boundary

Focused real-file/CLI fixture checks cover local report/revision/resume mechanics
and refusal paths. Full release build, real provider runs, manual terminal UX,
other local platforms, remote research and complex documents were **not run**
for this slice. CI proves only its exact candidate and workflows. No tag,
Release or package upload is implied; the previously frozen release artifact is
not re-certified by this source change.
