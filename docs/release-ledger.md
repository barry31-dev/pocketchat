# Release ledger

> Scope: the publication status of this product. This file is the **only**
> place publication facts are maintained. Other documents link to it; they do
> not keep a second copy of a release status, version or delivery date.
>
> A row is a fact that was read back, not a plan. If an action did not happen,
> this file says so. Nothing here is a publication authorization.

## 1. How to record a row

Each published version records, separately:

| Field | Meaning |
|---|---|
| `version` | the tag or release identifier |
| `target` | where it was published (repository, package index) |
| `tag_sha` | the commit the tag object points to |
| `published_at` | the timestamp read back from the target |
| `identity` | author / committer / tagger, plus the login the target attributes |
| `artifact` | name, bytes and digest of the published artifact |
| `ci` | the workflow run that covered that exact commit, with its result |
| `not_verified` | what this row does **not** establish |

A row is added only after the target has been re-read. A local build log is
not a remote publication fact.

## 2. Published versions

### v0.2.0

- Target: GitHub. Tag `v0.2.0`, root commit `caee4fc`, published 2026-10-20.
- Identity: commit author/committer and tag tagger all
  `barry31-dev <329888575+barry31-dev@users.noreply.github.com>`.
- Not verified: no package-index upload.

### v0.3.0

- Target: GitHub. Tag `v0.3.0` → `3bc8a9f`, published 2026-10-24.
- CI: Python 3.9 and 3.13 each 329 tests, clean venv install passed.
- Identity: commit author/committer and tag tagger all `barry31-dev`.
- Not verified: no package-index upload; real-provider evidence covers the
  local router used at the time only.

### v1.1.0

- Target: GitHub. Tag `v1.1.0` → `f3b5c76d6d1c069badde428d3721215df04282fe`,
  published 2026-10-30. Release is not a draft and not a prerelease.
- Artifact: `pocketchat-1.1.0-py3-none-any.whl`, 150895 bytes, SHA-256
  `a9f873f037112bd87474e21cc98d5e42c31d3567eb0563b2adb7cfa25e19834d`
  (matches the digest GitHub reports and a locally recomputed digest).
- Identity: commit author/committer, GitHub `author.login`/`committer.login`
  and annotated tag tagger all `barry31-dev`.
- Local candidate evidence: Python 3.9 and 3.13 each 691 tests OK, full `ruff`
  pass, 38-member wheel, clean install, three demo traces validated and replay
  hash verified.
- **Package index: not published.** The user authorized the upload, but the
  machine had no `UV_PUBLISH_TOKEN`, `.pypirc` or keyring credential and no
  OIDC token; the tool stopped at `Missing credentials` and no POST was sent.
  A later site readback also timed out, so this records "no successful upload
  in this round", not "the index does not have this version".
- Not verified: real provider, Linux/Windows runners, isolated build
  reproducibility, PyPI site readback.

### v1.0.0

- No `v1.0.0` tag and no `v1.0.0` Release exist. The CLI-only candidate was
  superseded by v1.1.0. The candidate record remains in
  [release-1.0.md](release-1.0.md) as history.

## 3. Unreleased source

The `main` branch ahead of `v1.1.0` holds unreleased source. It is not a
published release. The current machine-checked candidate record is below; it
does not authorize a tag, GitHub Release or package-index upload.

### Candidate `1baea8a22bc4d20ab13e76883938fb86c200899f`

- Candidate SHA: `1baea8a22bc4d20ab13e76883938fb86c200899f`; branch
  `task/release-smoke-evidence` in `barry31-dev/pocketchat`.
- Identity: commit author and committer are
  `barry31-dev <329888575+barry31-dev@users.noreply.github.com>`. No tag was
  created for this candidate.
- Local machine checks: CPython 3.9.6 and 3.13.9 each ran 1126 tests OK; 48
  focused memory/task-state/recovery tests, `ruff check --select E9,F` and
  `git diff --check` passed. The documented offline release smoke built and
  installed a 245269-byte wheel with SHA-256
  `709f6b2177ed92c7b5d1a5d1bfc49cbcda60e31c760b2e26385fa06cb606bc3e`.
- CI: [run 41588006974](https://github.com/barry31-dev/pocketchat/actions/runs/41588006974)
  completed successfully for `1baea8a`. It contains six Linux/macOS/Windows
  matrix jobs for Python 3.9/3.13 plus the `pocketchat-release-smoke` job.
- Release smoke artifact: `pocketchat-release-smoke`, artifact id `13208881597`,
  241908 bytes. The downloaded archive contains
  `pocketchat-1.1.0-py3-none-any.whl` (245268 bytes) and JSON evidence. The
  wheel SHA-256 recomputed from the downloaded asset is
  `3a6913293938c9334b0f974024981704f3758040dcfe304d2a96f25abcd235b1`,
  equal to the JSON value. It contains 60 members, all five schemas and the
  complete installed task command family.
- Review: `docs/review-release-smoke-evidence-v1.json` closes F1-F3. The first
  exact-SHA attempt
  [run 41586196768](https://github.com/barry31-dev/pocketchat/actions/runs/41586196768)
  for `c34749c` is retained as failure evidence; its Windows failures produced
  the task-state and test-gating fixes before this candidate.
- Publication: **not authorized for this candidate and not performed**. No tag,
  GitHub Release or package-index upload exists for `1baea8a`.
- Unverified: a local isolated PEP 517 build could not fetch `setuptools>=61`
  through the proxy, Windows recovery/acceptance remains intentionally
  `unsupported_platform` and skipped, the unselected full Ruff command retains
  pre-existing style findings, and real provider behavior/package-index
  reachability remain outside this slice.

### Prior candidate `ca3dcfc6c0519427319790eab0eb27645e1e9986`

- Candidate SHA: `ca3dcfc6c0519427319790eab0eb27645e1e9986`; branch `main` in
  `barry31-dev/pocketchat`.
- Identity: commit author and committer are
  `barry31-dev <329888575+barry31-dev@users.noreply.github.com>`. No tag was
  created for this candidate.
- Local machine checks: 1033 tests OK on CPython 3.13.9; full
  `ruff check --select E9,F pocketchat tests`, `compileall` and
  `git diff --check` pass. These checks cover the candidate tree at the time
  the evidence was written.
- CI: [run 41466053639](https://github.com/barry31-dev/pocketchat/actions/runs/41466053639)
  completed successfully for `ca3dcfc`. It contains six Linux/macOS/Windows
  matrix jobs for Python 3.9/3.13 plus the `pocketchat-release-smoke` job.
- Documentation follow-up: [run 41472710840](https://github.com/barry31-dev/pocketchat/actions/runs/41472710840)
  completed successfully for
  `74cc2596e0d0efdd78873b1dde7cbccac6c6192e`. All six matrix jobs and
  `pocketchat-release-smoke` passed; artifact id `13156860069` was uploaded.
- Final documentation commit `be581f1fc1f7b4057856f0a3143ef7b518524296`:
  [run 41475576711](https://github.com/barry31-dev/pocketchat/actions/runs/41475576711)
  has six successful jobs, including `pocketchat-release-smoke`, and one
  Ubuntu Python 3.9 job failed while cleaning up the real-terminal TUI test's
  temporary directory after that lone test errored. The same test passed
  locally in 0.019s. The token could not rerun the failed CI job, so this is
  recorded as **not a full seven-job green run**, not as a pass. Artifact id
  `13156860069` is from the preceding fully green documentation tree.
- Release smoke artifact: `pocketchat-release-smoke`, 224986 bytes, artifact
  id `13151077049`. The downloaded archive contains
  `pocketchat-1.1.0-py3-none-any.whl` (228024 bytes) and its JSON evidence.
  The wheel SHA-256 recomputed from the downloaded asset is
  `1981c149ceba7fc42a79f6ac608cd91007c500d09ce6f1fd0677749532ca3e7b`,
  equal to the JSON value. It contains 55 members, all five schemas and the
  complete installed task command family.
- Publication: **not authorized for this candidate and not performed**. No tag,
  GitHub Release or package-index upload exists for `ca3dcfc`.
- Review gap: fresh-context review attempts were started against the full
  candidate and a bounded recent diff, but the local provider stream repeatedly
  closed before `response.completed`; the final bounded attempt exhausted five
  reconnects and exited 1 after reading the target files. The alternative
  Claude reviewer was not authenticated. None of these attempts is recorded as
  a passing review. The project review ladder still requires that review before
  a publication decision.
- Unverified: real provider behavior/quality and billing, isolated-build
  reproducibility at another machine, package-index reachability, and all
  artifacts outside the exact SHA/run/artifact IDs above.


### Version 1.2.0 preparation (not published)

- Publication: not performed. Tag, Release URL, publication time and uploaded
  artifact: not recorded. No package-index action is implied.
- Source preparation and acceptance procedure: [release-1.2.md](release-1.2.md).
- Exact source candidate: `4019525ab297c131130da3fca1563176530beb50`,
  product main after PR #60. Exact-head CI run
  [43028154207](https://github.com/barry31-dev/pocketchat/actions/runs/43028154207)
  completed seven successful jobs.
- Release-smoke artifact: `pocketchat-release-smoke`, artifact id `13860340159`,
  345465 bytes, archive SHA-256
  `b665aedeab94c92e416ec8fcebc72c44fa58d87c446590bedeac97e2c3bfe91f`.
  It contains `pocketchat-1.2.0-py3-none-any.whl`, 345168 bytes, SHA-256
  `df8dbb69034a2ab961f708375ca1497fd157adc0416a9af11451782fcab7f729`,
  80 members and the five declared schemas. A fresh install reports
  `pocketchat 1.2.0`; the normal and dirty-Git offline fixture paths passed and
  deliberate wrong output exited 1.
- A separate local build produced the same 80 member paths and member bytes but
  a different container SHA-256
  (`11be7b63fd52f92302c84ace97e11355979aa61b456f9edbde2ae16a2fe5b111`)
  because ZIP timestamps differ. Canonical manifests are identical; wheel
  container reproducibility is not claimed.
- Publication: **not performed**. No v1.2.0 tag or Release was observed and no
  package-index action is implied.
- Required outstanding authority/evidence: artifact-bound real tool-capable
  provider and manual file-terminal acceptance, then specific tag/Release
  authorization, same-artifact publication and public-install readback. The
  offline stub does not satisfy the provider gate.
