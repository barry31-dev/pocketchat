# File-work development acceptance

`examples/file-work-demo.py` is a reusable offline fixture for the original
chat-and-file package. It imports whichever PocketChat is installed on the
selected interpreter; it never adds a source checkout to `sys.path`. Source
tests set their source import path explicitly. Run only in a new temporary
workspace, not on user files.

```bash
PYTHONPATH=. python examples/file-work-demo.py --workspace /tmp/new-file-work-fixture
PYTHONPATH=. python examples/file-work-demo.py --workspace /tmp/new-dirty-fixture --baseline dirty-git
PYTHONPATH=. python examples/file-work-demo.py --workspace /tmp/new-wrong-fixture --wrong-output
```

The first two return 0 on acceptance. The deliberate wrong output returns 1,
not a green run. An existing workspace/unsupported input is refused; do not
rerun into the same directory. The fixture uses an in-process provider adapter,
without network, credentials or paid calls. Only provider injection is mocked:
Session, grants, file transactions, persistence and undo are real.

The fixture verifies two chat turns, explicit provider disclosure and exact file
grants, a paired authorized read, proposal rejection, complete diff, replacement
and creation with readback, cancellation with a preserved prior applied effect,
a restarted conversation with fresh undo grants, persistent undo to the original
(non-Git or pre-existing dirty Git) bytes, and stale external-edit refusal.
Its JSON and `file-work-evidence.json` include source SHA (when supplied), version,
OS/Python, session/run/change identities, hashes, assertion outcomes and terminal
statuses. A temporary external edit is intentionally preserved in the conflict
case. No model output is used as evidence that a target was applied.

## Installed development wheel

Use the existing build/install entry, then copy only this fixture outside the
checkout and run it with the fresh environment's interpreter, with `PYTHONPATH`
unset and the exact source SHA supplied:

```bash
python scripts/release_smoke.py --work-dir /tmp/new-wheel-smoke --json
# Locate the exact wheel and fresh environment from that result.
# Copy examples/file-work-demo.py to an unrelated temporary directory.
/path/to/clean-venv/bin/python /tmp/copied-file-work-demo.py \
  --workspace /tmp/new-installed-fixture --source-sha <40-character-source-sha>
```

Keep the exact wheel digest, source SHA, interpreter, commands, exit codes and
fixture JSON together outside the candidate tree. Changing the candidate or
wheel requires rebinding; this development wheel does not certify a different
release version. Repository CI separately runs its full platform matrix and
package smoke on its exact candidate.

## Coverage boundaries

Focused cancellation tests exercise invalidation of pending proposals, no effect
before confirmation, and retention of a slow worker's ownership. Existing tests
cover permission changes, invalid digests, pipe refusal, writer collisions,
quotas, symlinks, encoded-text limits, transaction failure/recovery and controls
rendering. Secure descriptor-dependent tests skip explicitly on unsupported
runtimes; a green job is not a claim that those skipped cases ran.

This fixture drives the shared trusted controller, not a human-operated curses
session. Offline stub, installed-wheel, real-provider and manual usability
results are separate authorities. No real-provider/manual acceptance, arbitrary
crash-safety, performance, release, tag or upload is established by this fixture.
