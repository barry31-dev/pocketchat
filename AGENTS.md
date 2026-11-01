# AGENTS.md — pocketchat

> **Read this before you start.** This repository is the product. Everything a
> contributor needs is inside it; nothing here depends on another repository.
>
> Scope: what this file covers, and what it does not. It does not replace
> [development-and-release.md](docs/development-and-release.md), which is the
> process source for this package, nor [compatibility.md](docs/compatibility.md),
> which owns the stable command, JSON and file boundaries.

## 1. What this product is

`pocketchat` is a small single-user, local-first CLI chat agent. The durable
asset is evidence: the trace, request snapshot, session, comparison and release
artifact must let a later reader answer what was sent, what came back and what
remains unverified.

It is **not** a general agent. There is no code execution, no shell, and no
unbounded tool surface. Optional local tools are explicit, allowlisted and
workspace-confined.

### 1.1 Boundaries that are not implied by a change

A test, a branch or a mock does not make a capability current. These require a
roadmap plus slice decision before implementation:

- general-purpose tool calling, file access beyond the explicit local tools,
  shell execution, sandbox or approval gates;
- multi-agent orchestration, delegation, automatic planning or auto-selection;
- memory, skills, MCP, cron or background jobs;
- Web UI, local API, desktop application, account system or remote service.

## 2. Rules that may not be violated

### 2.1 Evidence discipline

Record evidence **type** and verification **status** as two separate
dimensions; never collapse them into one label.

| Dimension | Values |
|---|---|
| Source type | official docs (D), current code/tests/schema (C), reverse-engineering finding (R), local run (V), open-source reference (O), project interpretation |
| Verification | verified / pending / unverified / not run |
| Scope | version, commit, OS, Python, provider, workspace or fixture actually covered |

- A local trace verifies that run only. A stub verifies the adapter shape, not
  a real provider. A schema declaration does not prove the event was emitted.
- **"Not run" is a valid result.** Never write "should work", "expected to
  pass", or "probably fine" in place of a run that did not happen.
- Static reasoning is not a test pass. Write "**not run**" for anything not
  executed.

### 2.2 Status discipline

- Publication status is recorded in [`docs/release-ledger.md`](docs/release-ledger.md).
  Other documents link to it; they do not keep a second copy.
- **Never fabricate a release link, tag, date or upload.** If a publication did
  not happen, write that it did not happen.
- A green local suite is not a release. A published CI run is evidence only for
  the exact commit and workflow it reports.

### 2.3 Language

- Code, user-visible strings and `README.md` are **English only**.
- `README.zh-CN.md` is the Chinese counterpart and may coexist.
- `docs/` pages and `CHANGELOG.md` follow the existing bilingual convention:
  new `CHANGELOG.md` entries are English.

### 2.4 Commits and identity

- Prefix the subject with `feat:` / `fix:` / `docs:` / `chore:` plus one
  English sentence. State **how it was verified** in one line.
- Group by file; do not split one file across two commits.
- Before committing, check `git diff --cached --name-only` for media, archives
  and `.DS_Store`.
- Every published commit and annotated tag must use the repository's published
  identity. Never commit a personal name, personal email or credentials.
- Commit messages never contain private paths, automation names, worktree paths
  or local credentials.

## 3. Working loop

```text
choose a slice -> write the completion check -> red test
-> smallest implementation -> verification at the matching layer
-> checkpoint (commit + evidence) -> integrate
```

- **One slice at a time.** A slice must state its goal, what it is not doing,
  the done condition, the evidence location, the budget, and the rollback.
- Write the failing test first. A green test written after the implementation
  proves less than a red one that turned green.
- Verification must be **proportional to the change** (§2.5): a docs-only
  change does not trigger a full product run; a schema change does not get to
  skip its tests.
- A docs-only change needs no release candidate.

### 3.1 Where the current slice lives

A product-code change must update the current slice record before it enters
`main`: the referenced issue in this repository, or
[`docs/slice-queue.md`](docs/slice-queue.md) when the work is not tracked as an
issue. The queue entry must already be approved; a machine check that the file
or issue changed is a precondition, **never** an approval.

`CHANGELOG.md` is delivery history and does not substitute for the current
slice. Documentation, process-tool and test-only maintenance does not create a
product slice.

## 4. Local checks

Run everything **from the package root**. Running `unittest discover` from a
directory above this package puts it outside `sys.path` and produces import
errors that are environment mistakes, not product failures.

```bash
python -m unittest discover -s tests -v
ruff check pocketchat tests
python -m build
```

Clean-environment install smoke, from outside the source tree:

```bash
python -m venv /tmp/pocketchat-venv
/tmp/pocketchat-venv/bin/python -m pip install dist/pocketchat-*.whl
/tmp/pocketchat-venv/bin/pocketchat --version
/tmp/pocketchat-venv/bin/pocketchat --help
```

Record the interpreter version, command, actual output and exit code. Use the
platform-specific `Scripts/` or `bin/` path on Windows.

CI is evidence only for the exact commit and workflow run it reports. A push
to a different repository, or a workflow that did not run, does not inherit a
previous green result. `workflow` files existing is not a green run.

## 5. Verification scale

- **Same check, one round.** A check that already passed is not re-run without
  a new change, a failure, or an unresolved concern.
- **Change type sets the surface.** Docs and process tools do not trigger
  builds, tests or packaging. Code, schema or generator changes run the
  matching checks.
- **Reuse existing entry points** instead of hand-rolling a second command for
  the same thing.
- If the budget runs out, write **not done**. Do not paper over it.

## 6. Publication

Publication is a user authorization, not an agent decision. Before any push of
a tag or Release:

1. the candidate passes the local checks above;
2. the commit author/committer and tag tagger all use the published identity;
3. a clean-room scan of the release tree is clean;
4. the user has authorized that specific action, target and candidate.

A push to a public branch is a public action; no Release is required for
content to be public. Once pushed, a leak is not undone by deleting the commit.

## 7. Maintenance and rollback

| Situation | Action |
|---|---|
| Candidate fails before publication | Fix and rebuild; never move a published tag backwards |
| Published wheel has a defect | Publish a new patch version; keep the old tag and evidence |
| Credential/path/evidence defect | Stop publication, record the affected versions, remove secrets from the live surface; publish a corrected version |
| Compatibility break | New major version with a migration note; deprecation follows the policy |
| CI missing or stale | Record it as unverified; do not copy a green result from another SHA |

Incidents and reverts are inputs to the review process. Convert each recurring
failure into the smallest useful regression test or static guard, and update
this process only when the failure reveals a missing rule.

## 8. Documentation map

| Question | Owner |
|---|---|
| Current slice and approval state | [`docs/slice-queue.md`](docs/slice-queue.md) or the product issues |
| Development, verification and release process | [`docs/development-and-release.md`](docs/development-and-release.md) |
| Publication status | [`docs/release-ledger.md`](docs/release-ledger.md) |
| Compatibility and deprecation | [`docs/compatibility.md`](docs/compatibility.md) |
| Security boundaries | [`docs/security.md`](docs/security.md) |
| CLI, JSON and exit codes | [`docs/cli.md`](docs/cli.md) |
| Trace and file formats | [`docs/formats.md`](docs/formats.md) |
