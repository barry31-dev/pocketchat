# PocketChat v1.0.0 release checklist

> **Version-specific candidate record, not the general process.** The current
> development/verification/publication process is
> [development-and-release.md](development-and-release.md). This checklist
> records what was checked for this v1.0 candidate; it does not authorize a tag,
> Release, package-index upload or any other external action.

## Required before calling the candidate locally complete

- [ ] Full CLI suite passes on CPython 3.9. (local interpreter unavailable on 2026-10-29)
- [x] Full CLI suite passes on CPython 3.13 (576 tests, 2026-10-29).
- [ ] Full `ruff check pocketchat tests` passes. (repository-wide baseline still reports legacy findings; changed-file E9/F checks pass)
- [x] A v1.0.0 wheel builds with no server/web assets.
- [x] The wheel installs into a clean venv and reports version `1.0.0`.
- [x] The installed CLI contains the trace schema and no `web` package.
- [x] The installed CLI rejects `serve` with exit 2.
- [x] The compatibility and provider-adapter contracts are documented.
- [x] CLI command inventory and versioned artifacts are pinned by tests.
- [x] `agent` supports multi-turn context, `/model`, `/session`, `/reasoning`,
  `/context` and `/status` with local stub coverage.
- [x] Cross-platform CI workflow exists; local macOS CPython 3.13/3.14 validation passed. CAND-7 remains the skipped hosted-provider slice; Linux/Windows/3.9 runner results are not claimed.
- [ ] A public tag and Release have been created after explicit authorization.
- [ ] A package-index publication has been explicitly authorized.

## Local evidence for this candidate

Candidate record (2026-10-29):

```text
version: 1.0.0
candidate SHA: 84ee31ae83dd63608fe40fb4ba11c36e3816cf98 (pre-RELEASE-1 commit)
wheel: pocketchat-1.0.0-py3-none-any.whl
sha256: 593159a096cc581a0128c58401fca976d364f587e223314ef5f6f8734423acaf
build: local setuptools, --no-build-isolation
install: clean CPython 3.13 venv; version 1.0.0; schema present; serve exit 2
tested: macOS CPython 3.13 full suite; macOS 3.14 full suite
unverified: CPython 3.9; Linux/Windows runners; isolated build; full-repo ruff
publication: not authorized, not executed
```

| Check | Evidence |
|---|---|
| Python 3.9 | not run; interpreter unavailable |
| Python 3.13 | 576 tests OK (2026-10-29) |
| Python 3.14 | full suite OK (2026-10-29) |
| Lint | changed-file `ruff --select E9,F` passes; full repository lint remains unresolved |
| Wheel | `pocketchat-1.0.0-py3-none-any.whl`, 35 members, sha256 `593159a096cc581a0128c58401fca976d364f587e223314ef5f6f8734423acaf` |
| Wheel build mode | local setuptools, `--no-build-isolation`; isolated build not run |
| Installed wheel | version `1.0.0`; schema present; web absent; serve exit 2 |
| Isolation build | PyPI access timed out; the wheel was built with local setuptools |

The isolation-build line is an environment limitation, not a product pass. The
next public release run must repeat the build in an environment where
`build-system.requires` can be resolved and record that result.

## Public release sequence

1. Run the full local test suite on the exact candidate SHA.
2. Run the repository release gate and identity gate.
3. Obtain explicit user authorization for tag/Release/push.
4. Create the annotated tag and Release through the existing publication path;
   do not hand-push around the gate.
5. Re-read the remote tag, Release, commit identity and published tree.
