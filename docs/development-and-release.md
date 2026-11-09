# Development and release process

> Scope: how PocketChat is developed, verified, published and maintained.
> Product direction and scope live in the roadmap; release status lives in the
> publication ledger. This page is the process source for this package and
> is intended to travel with the public repository.
>
> Version: 2026-10-29. Applies to the CLI-only product.

## 1. Operating model

PocketChat is a single-user, local-first CLI. The durable asset is evidence:
the trace, request snapshot, session, comparison and release artifact must
allow a later reader to answer what was sent, what came back and what remains
unverified.

Every change follows this loop:

```text
choose a slice -> write the completion check -> red test
-> smallest implementation -> verification at the matching layer
-> checkpoint (commit + evidence) -> integrate
-> freeze a release candidate -> run gates -> authorize -> publish
-> re-read the remote -> maintain or roll back
```

A stage is a location, not an approval gate. Small documentation fixes do not
need a release candidate. A release does not inherit evidence from an older
commit, an older wheel or another machine.

## 2. Before work starts

### 2.1 Entry and scope

Read only the sources needed for the change:

- the current slice: a product issue in this repository, or an entry in
  `docs/slice-queue.md` when the work is not tracked as an issue;
- this file;
- `compatibility.md`, `security.md`, `cli.md`, `formats.md` and the relevant
  tests.

A slice must state:

```text
Goal:
Not doing:
Done when:
Evidence location:
Budget / stop:
Rollback / next:
```

A readable slice contract is required before implementation. Within the approved
roadmap the maintainer delegates this decision to the agent; it does not require a separate planning PR
or prior `main` reachability. Use a repository issue, `docs/slice-queue.md`, or the
task review record and deliver the contract with the implementation. A machine confirmation that the file or issue changed
is a precondition, not approval. New product direction stays a maintainer decision.

A development branch does not edit the shared queue merely to claim or complete
work. Planner/integrator queue updates are small serialized commits on `main`:
the planner may add approved future rows while development continues, and the
integrator marks a row delivered after the product branch lands. Multiple task
branches may proceed when they have different branch names, settled dependencies
and disjoint `write_scope`; the same path or an ancestor/descendant path remains
serial. `CHANGELOG.md` is retained delivery history and does not substitute for
the current slice. Documentation, process-tool and test-only maintenance does
not create a product slice.

The goal is testable. "Improve the agent" is not a goal; "record a stable
`error_type` for five failure classes and keep the old JSON fields" is.

### 2.2 Product boundary

The current product is CLI-only. The following are not implicit features of a
change and require a roadmap plus slice-queue decision before implementation:

- general-purpose tool calling, file access beyond the explicit local tools,
  shell execution, sandbox or approval gates;
- multi-agent orchestration, delegation, automatic planning or auto-selection;
- memory, skills, MCP, cron or background jobs;
- Web UI, local API, desktop application, account system or remote service.

A future capability does not become current because a test, a branch or a
mock makes it possible. Update the roadmap first; then implement one slice.

### 2.3 Evidence

Record the following separately; do not compress them into one status label:

| Dimension | Values |
|---|---|
| Source type | official docs (D), current code/tests/schema (C), reverse-engineering finding (R), local run (V), open-source reference (O), project interpretation |
| Verification | verified / pending / unverified / not run |
| Scope | version, commit, OS, Python, provider, workspace or fixture actually covered |

A local trace verifies that run only. A stub verifies the adapter shape, not a
real provider. A schema declaration does not prove the event was emitted.

## 3. Development

### 3.1 Red first

Behavior changes and bug fixes start with a failing test. The failure must be
observed and must fail for the expected reason, not because the test imports
the wrong module. Write the test and run it **before** writing the implementation;
a test and implementation committed together do not prove the test can fail.

For a CLI behavior, test the process boundary when the observable contract is
stdout, stderr or an exit code. For internal state, test the owner module. For
trace and file formats, add a positive and a negative fixture. When a CLI code
change alters a documented contract, update both `docs/cli.md` and
`docs/cli.zh-CN.md` and run the package's documentation guard.

Pure documentation, reversible text and generated metadata may use a direct
diff check instead of a synthetic product test. Do not add a fake test merely
to satisfy a process.

### 3.2 Smallest implementation

Implement only what the current slice needs:

- keep the existing command, exit-code, JSON and file-format contracts unless
  the compatibility policy explicitly allows a change;
- do not add retry, fallback, model selection, scoring or a second provider
  path as an unplanned side effect;
- do not silently rewrite an existing session, trace or request snapshot;
- keep credentials out of config, JSON, traces, manifests and test output.

### 3.3 Checkpoint

At each completed milestone write a checkpoint in the task message, commit or
handoff:

```text
Slice / commit:
Changed:
Working directory:
Commands and exit codes:
Verified:
Not verified:
Next action:
```

The checkpoint is deliberately shorter than the full log. Keep the raw trace
or command output at a path that can be re-opened; do not rely on a chat
summary or model memory.

### 3.4 Stop conditions

Stop and report instead of looping when:

- the same failure repeats twice without new evidence;
- a test passes without executing any case (`--filter` or a collection error);
- a concurrent writer changes the same file or workspace;
- a credential, external account or provider is unavailable and the proposed
  fix would switch accounts or bypass a gate;
- the intended action is an irreversible publish and the authorization is not
  bound to this version and SHA;
- the evidence required for the claim has been lost and cannot be re-read.

Retry only when the action is idempotent, the previous attempt is known to be
finished and the retry has an explicit limit.

## 4. Verification matrix

Choose the smallest layer that can prove the changed behavior. One layer never
substitutes for another.

| Change | Minimum evidence | What it does not prove |
|---|---|---|
| Docs, roadmap, release checklist | diff, links, terms, scope consistency | product behavior |
| Python logic | focused unittest + negative case | CLI envelope or real provider |
| CLI / JSON / exit code | process-level test with stdout, stderr and exit code | every shell or OS |
| trace / schema | schema check, terminal event, malformed-event case | remote provider quality |
| provider adapter | local stub HTTP; real route recorded separately | all providers or billing |
| package | wheel contents, clean venv install, installed CLI smoke | cross-platform matrix |
| release tree | clean-room scan, required files, identity gate | public release authorization |

### 4.1 Required local checks for a release candidate

Run these **from the package root**. Running `unittest discover` from a
directory above this package puts it outside `sys.path` and produces import
errors that are environment mistakes, not product failures.

```bash
python -m unittest discover -s tests -v
ruff check pocketchat tests
```

Then build and install in a clean environment. The repository-owned smoke
entry performs the real wheel build, archive-member checks, clean venv install,
installed version check and task-command surface check, and emits machine-readable
evidence:

```bash
python scripts/release_smoke.py --work-dir /tmp/pocketchat-release --json
```

It requires exactly one wheel, all five shipped schemas, the task/recovery
modules, no `pocketchat/web/` or `pocketchat/server.py`, and the full installed
`task ... --help` family. The JSON result contains workspace-independent artifact
name, byte count, SHA-256, member count, schemas and command families. CI runs the
same script and uploads the JSON plus wheel as `pocketchat-release-smoke`.
The default build uses PEP 517 isolation, so a clean runner does not depend on
its base interpreter having setuptools preinstalled. `--no-build-isolation` is
an explicit offline/debug fallback and must only be used when those build
dependencies are already available.

Record the interpreter version, command, actual output and exit code. The CI
artifact is evidence only for its exact SHA; it does not authorize a tag,
Release or package-index upload.

The CI matrix is evidence only for the exact commit and workflow run it
reports. A push to a different repository, or a workflow that did not run,
does not inherit a previous green result.

### 4.2 Verification-plan vocabulary

An **uncovered path** is a changed source file with no matching test file. It
is not a missing validation layer, and it is not a test failure. Report the
path and the absent test explicitly; never replace it with the layer name.

### 4.3 Compatibility and security

Before a release, check:

- command names, exit-code classes and documented JSON fields against
  `compatibility.md`;
- unknown format versions are refused rather than guessed;
- no key appears in config, JSON, request snapshots, traces, manifests or
  test artifacts;
- paths in evidence remain workspace-relative where documented;
- the package contains no removed Web/server surface.

These are release gates, not a substitute for a product test.

## 5. Review ladder and harness practices

### 5.1 Review Ladder

This ladder adapts Anthropic's
[How the Claude Code team does code review](https://claude.dev/blog/solving-code-review-at-anthropic/)
to PocketChat's size and product boundary.

Code review has two jobs: catch issues before they ship, and build shared
context across maintainers. Reading every diff line by line is not the goal.
The scarce resource is human attention, so review effort follows the cost of
missing a defect, not the author's identity or the diff size.

Risk is set by the change:

| Tier | Applies to | Required review |
|---|---|---|
| low | Documentation, links, generated metadata and non-release configuration | `quick`: diff, terminology, links and applicable document guards |
| medium | Python behavior, CLI/JSON contracts, schemas, provider adapters and packaging | `standard`: quick review + focused tests + fresh-context review of the diff and contracts |
| high | Security boundaries, compatibility policy, release/identity paths, publication and rollback | `deep`: standard review + command or external readback evidence + explicit security and rollback review |

An unknown changed path is `medium`, never an implicit `low`. When one change
mixes tiers, use the highest tier and review the whole change at that depth.

Every change that will enter `main` leaves a compact review record:

```text
changed paths:
commit / candidate SHA:
risk tier / review depth:
rounds:
findings (id, severity, status, reason/fingerprint):
reviewed evidence:
```

The merge entry validates the whole record. It recomputes the risk tier and
review depth from `changed paths`, recomputes the round decision from `rounds`,
and rejects missing paths/categories, an unknown finding status, a missing
fingerprint or any disagreement between the record and those calculations.
The recorded paths must exactly match the task diff; categories and risk tier
are recomputed from that diff rather than trusted from the author. Inspecting
only the findings list is not a review gate.

A finding is `open`, `fixed`, `dismissed` or `deferred`. `dismissed` and
`deferred` require a reason. Repeated findings share a fingerprint and are
reported once as a class. An open `blocker` or `major` finding blocks landing.
Tests, the review record and release checks are separate evidence; none can
stand in for another.

Review converges instead of looping:

- review the full diff in round one; later rounds review the new diff;
- do not repeat a closed finding;
- after at most three rounds, stop and reconsider the approach;
- only a new-diff review may continue after that point;
- if reviewers cannot agree within the cap, escalate rather than start another
  full round.

Every change receives an automated review. The author proves behavior with
tests or readback and writes the evidence. A fresh-context reviewer looks at
the goal, diff, contracts and tests without adopting the author's conclusion.
Routine technical approval is delegated to agents by the maintainer's
2026-11-09 standing decision, including explicit security/rollback review when
needed. Record the candidate, reviewer and actual evidence; no per-SHA human
sign-off is required for ordinary development. Formal releases, new paid access,
third-party rights and destructive/shared-history exceptions still need the
maintainer. A bot finding or approval never replaces a required human approval.


Before landing, check what could go wrong and which layer should catch it. For
an early design disagreement, discuss a different direction in the task thread
before the implementation is finished; closing a cheap, wrong-direction change
is cheaper than reviewing it to completion. If the change still goes wrong
after landing, the landing author normally owns the fix because they hold the
most context, while incident review remains blameless and examines the system.

Use incidents, reverts and rollback drills to improve the process. Put a
recurring miss into a test, lint rule, compatibility guard or this document;
do not only ask the next reviewer to be stricter. Keep small changes because
they are easier to review, debug and undo.

### 5.2 Harness practices

PocketChat is not a general agent harness; it is a CLI with an auditable model
call path. The harness lessons apply to the work around it:

- **Context:** load the current slice, compatibility and security pages, not
  every historical plan. Keep the prompt and tool output bounded.
- **State:** a checkpoint names the commit and the evidence path. A trace is
  the durable record of what happened, not a memory to trust.
- **Observability:** record model/profile, command, exit code, trace hash,
  wheel hash and cost when reported. Unknown cost is `unknown`, not zero.
- **Evaluation:** support claims about a change with a fixed fixture and one
  variable at a time. A single pass is not a benchmark.
- **Approval:** authorization binds the repository, version, SHA, tag/Release
  and external action. It does not authorize a different tree or a later
  version.
- **Recovery:** after interruption, read the branch, commit, diff, last trace,
  tests and checkpoint. Do not replay a request with an unknown side effect.

## 6. Release candidate

A release candidate is a frozen tuple:

```text
version:
candidate SHA:
branch / repository:
wheel path + size + sha256:
test / lint / install results:
review record:
evidence paths:
known unverified:
```

The tree must be clean before freezing. If a file changes after the SHA is
recorded, repeat the gate; an older green run is not evidence for the new SHA.

### 6.1 Version-specific checklist

Keep a per-version checklist in this package's `docs/`. The v1.1 record is
[release-1.1.md](release-1.1.md); the v1.0 candidate lives in
[release-1.0.md](release-1.0.md) and is explicitly a historical candidate
record, not the general process.

## 7. Publication

Ordinary task-branch non-force push, PR, required CI and reviewed main integration
are covered by the 2026-11-09 standing routine-development authorization. It lasts
until revoked; automatically renewed short-lived execution receipts are not new
human approvals. The version-bound publication rules below apply to tags,
Releases and package uploads, not every ordinary development commit.


Publication has two separate parts: machine checks and the user's external
authorization.

### 7.1 Machine checks

- the full local suite and lint pass on the candidate SHA;
- the wheel builds, installs and exposes the expected version;
- compatibility/security/clean-room checks pass;
- the review record is bound to the candidate SHA, has the required depth and
  contains no open blocking finding;
- the required CI run is attached to the same SHA, or the missing CI is
  recorded as an unverified platform gap;
- commit and annotated-tag identity pass the repository identity gate;
- the release tree contains no internal paths, credentials or local state.

### 7.2 Authorization

Before creating a public repository, first push, tag, GitHub Release or package
index upload, obtain explicit authorization. Record the repository, version,
SHA, action and channel. A green machine gate only means "ready for a human
decision"; it never authorizes the action.

The release path must use the repository's approved publication path. Do not
hand-push around the gate or create an annotated tag with a private identity.

### 7.3 Post-publication readback

After publishing, re-read:

- tag object and the commit it points to;
- Release draft/prerelease state and body;
- commit author/committer and tagger;
- published tree and wheel asset hash;
- a fresh install from the published artifact, when applicable.

Record the actual URL, timestamp, version and unverified scope in
`docs/release-ledger.md`. A local file or build log is not a remote publication
fact.

## 8. Rollback and maintenance

| Situation | Action |
|---|---|
| Candidate fails before publication | Fix and rebuild; never move a published tag backwards |
| Published wheel has a defect | Publish a new patch version; keep the old tag and evidence |
| Credential/path/evidence defect | Stop publication, record the affected versions and remove secrets from the live surface; publish a corrected version |
| Compatibility break | New major version with a migration note; deprecation follows the policy |
| CI missing or stale | Record it as unverified; do not copy a green result from another SHA |

Incidents and reverts are inputs to the review process. Convert each recurring
failure into the smallest useful regression test or static guard, record it in
the review evidence and update this process only when the failure reveals a
missing rule. This keeps review calibrated to observed failures instead of
expanding checks after every scare.

The charter's identity and material rules apply. Paths, automation names,
worktree paths and local credentials from any private environment never enter
this repository or its release notes.

## 9. Documentation map

| Question | Owner |
|---|---|
| Product direction and milestones | `docs/slice-queue.md` and the product issues |
| This process | `docs/development-and-release.md` and its Chinese counterpart |
| Compatibility/deprecation | `docs/compatibility.md` |
| Security boundaries | `docs/security.md` |
| CLI, JSON and exit codes | `docs/cli.md` |
| File formats | `docs/formats.md` |
| Field evidence and gaps | `docs/pocketchat-evidence-matrix.md` |
| Version-specific candidate | `docs/release-1.1.md` and the v1.0 checklist |
| Current public status | `docs/release-ledger.md` |

If a rule belongs to one of these owners, link to it rather than copying it
here.

## 10. Minimal completion card

Copy this into a task, commit body or handoff; omit inapplicable rows for a
small change, but never omit the completion check or unverified scope for a
release:

```text
Goal / not doing:
Done when:
Risk tier / review depth / rounds:
Environment (cwd / Python / OS):
Commands / output / exit:
Findings / dispositions:
Evidence path:
Not verified:
Rollback / next:
```
