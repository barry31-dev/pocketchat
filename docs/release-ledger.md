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
release candidate until the checks in
[development-and-release.md](development-and-release.md) §4.1 have been run
against it and recorded.
