# CI matrix check identities

PocketChat's test workflow runs the same product suite on three operating
systems and two Python versions. Each matrix cell has a distinct check name so
a reviewer can identify the exact platform/runtime result without opening the
job first.

## Stable names

The `test` job keeps this name template:

```text
pocketchat-ci (${{ matrix.os }}, python ${{ matrix.python }})
```

With the current matrix, the six expanded names are:

- `pocketchat-ci (ubuntu-24.04, python 3.9)`
- `pocketchat-ci (ubuntu-24.04, python 3.13)`
- `pocketchat-ci (macos-latest, python 3.9)`
- `pocketchat-ci (macos-latest, python 3.13)`
- `pocketchat-ci (windows-latest, python 3.9)`
- `pocketchat-ci (windows-latest, python 3.13)`

The separate package gate remains `pocketchat-release-smoke`.

## Boundary

This naming contract does not add or remove matrix cells, change runners,
Python versions, steps, action pins, permissions, triggers, concurrency,
timeouts or release behavior. A successful job is evidence only for its exact
commit and matrix cell.

The repository's branch-protection API was not readable by the planning
credential (HTTP 403), and no repository ruleset was returned. Therefore this
change does not claim that a server-side required-check policy exists or was
updated. If a required-check consumer is configured outside the readable
repository data, a maintainer must compare it with the final check names before
landing.

## Guard

`tests/test_ci_matrix_names.py` reads the committed workflow, freezes the
current three-by-two matrix, expands the job-name template and requires six
unique names plus the unchanged release-smoke name. It is a static workflow
contract; exact GitHub Actions execution remains separate evidence.

See [Development and release process](development-and-release.md) for review,
landing and publication boundaries.
