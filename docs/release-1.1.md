# PocketChat v1.1.0 release candidate

> **Version-specific candidate record, not a publication authorization.** The
> general process is [development-and-release.md](development-and-release.md).
> This file records the local evidence for one v1.1.0 candidate. The user
> authorized tag, GitHub Release and package-index publication for this version
> on 2026-10-30. This pre-publication record does not itself execute those
> actions; post-publication facts are read back and recorded outside it.

## Candidate record

```text
version: 1.1.0
candidate code SHA: 0f4a137427ad1a601a10c03c16b3345752b3b3f2
full tests: CPython 3.9.6, 691 tests OK, 55.554s, exit 0
full tests: CPython 3.13.9, 691 tests OK, 36.776s, exit 0
wheel: pocketchat-1.1.0-py3-none-any.whl
wheel bytes: 150895
wheel members: 38
wheel sha256: a9f873f037112bd87474e21cc98d5e42c31d3567eb0563b2adb7cfa25e19834d
build: CPython 3.13.9 / setuptools 80.9.0, --no-deps --no-build-isolation
install: clean CPython 3.9.6 venv, --no-index
installed version: 1.1.0
installed artifacts: task schema present; web/server members 0
publication authorization: received 2026-10-30 for v1.1.0
```

The 3.9 toolchain's setuptools 58.0.4 does not implement PEP 621 metadata and
produced `UNKNOWN-0.0.0` under `--no-build-isolation`; that output was rejected.
The valid wheel was built with the installed setuptools 80.9.0 and then tested
through a clean 3.9 venv. An isolated `uv build` was attempted and failed before
project build while resolving `setuptools>=61` from PyPI after three network
retries; this is an environment/network gap, not a successful isolated build.

## Required checks

- [x] Full PocketChat suite passes on CPython 3.9.
- [x] Full PocketChat suite passes on CPython 3.13.
- [x] A `pocketchat-1.1.0-py3-none-any.whl` wheel builds.
- [x] The wheel installs into a clean venv and reports `1.1.0`.
- [x] The installed wheel contains `pocketchat/schemas/task.schema.json`.
- [x] The installed wheel contains no `web/` package or `server.py`.
- [x] `task run/status/resume/report` are present in the installed CLI.
- [x] The deterministic demo writes success, checkpoint-resume and
      permission-refusal evidence.
- [x] All three demo traces pass `trace-doctor validate`.
- [x] `replay --dry-run` recomputes the selected request snapshot hash.
- [x] Compatibility, formats, CLI, English/Chinese README and CHANGELOG agree.
- [x] Full repository `ruff check pocketchat tests` passes.
- [ ] Isolated PEP 517 build passes in a clean build environment.
- [ ] Linux/Windows runner results are recorded.
- [ ] A real provider task run is recorded for this candidate.
- [x] Tag, GitHub Release and package-index publication are explicitly
      authorized for v1.1.0.
- [ ] Post-publication tag, Release, identity and artifact readback is recorded
      in the publication ledger (outside this pre-publication record).

## Demo evidence

Generated with:

```bash
/usr/bin/python3 开源/pocketchat/examples/task-demo/demo.py \
  --output /tmp/pocketchat-v11-demo-final-20261030-084411
```

| Path | Result | Trace SHA-256 |
|---|---|---|
| `traces/success.jsonl` | completed, 3 steps, 1 tool | `98ae0888e75e47f6211567b8d7b679db0448568aa53cf22645c87157b58b665b` |
| `traces/run_resume.jsonl` | recovery step 4, 2 context messages | `565a8892a765947c957163e8ac3f72eb4ecd5bfd931ae48b12fc4e8655b62adf` |
| `traces/refusal.jsonl` | completed, tool state `refused` | `b52d729df2c88f33fdfc8fddcd5e3e4eb75bd2c96495732991d558571b105ab2` |

`trace-doctor validate` returned exit 0 for 10, 2 and 9 events respectively.

Replay was run against the explicit first request snapshot:

```bash
pocketchat replay traces/requests/success.turn-1.json --dry-run --json
```

Result: `match: true`, `sent: false`, stored and recomputed canonical request
SHA-256 both `3a5bacdb4014ee30a251de775bdc38555ad31a2e43acc58b515a3280c6d2880f`.
Selecting the run id for a custom trace basename did not discover
`success.turn-N.json`; explicit snapshot selection is verified, run-id discovery
for custom trace names is not.

## Scope not established

This candidate does not establish real-provider behavior, billing, Linux or
Windows behavior, isolated build reproducibility, external Trace Doctor
consumers, or post-publication state. The demo uses an in-process stub and fake
credential. Cancellation remains terminal; recovery evidence uses a real
`checkpointed -> running` boundary. No tag, Release or package-index upload was
created.
