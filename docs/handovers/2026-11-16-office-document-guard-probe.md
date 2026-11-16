# Trusted document worker guard probe

Plan: diagnose the next approved P2 dependency without enabling arbitrary input.
Use fixed trusted Python snippets only, no supplied code/file/URL/profile and no
optional document parser. Compare ordinary loopback socket binding with macOS
OS-level deny-network binding. Try installing a 256 MiB address-space limit but
never allocate large memory; installation/readback alone never prove enforcement.
Exercise owned-child timeout/kill and output-budget refusal. Return content-free
versioned JSON and keep `parser_ready=false` until independent evidence exists.

Alternatives: silently trust rlimit readback or monkeypatch Python socket access
would not prove OS enforcement. Select an actual OS denial probe where available;
unsupported platforms retain a named capability outcome. No profile installation,
configuration, workspace or product state change. The probe creates no files.

Write scope: probe script/test, reading contract, review record and this handover.
Completion: red-first fixed-input/bounded-output tests, actual platform probe,
no false readiness, scoped independent review and exact-head CI/main readback.
Rollback: remove the additive diagnostic; no schema/data/API migration.

Not verified: arbitrary parser memory/network isolation, hard-allocation refusal,
all network families/operations, Linux/Windows positive guards, source ingestion,
optional parser compatibility, full P2 or release.


macOS 27 arm64 / CPython 3.9.6: original missing diagnostic module red exit 1;
four focused tests passed exit 0. Actual no-argument diagnostic returned JSON:
three unguarded loopback bindings allowed, guarded denied; memory installation
unsupported; timeout killed/reaped child; 1,024-byte output refusal. Ruff E9/F
passed. These are trusted-case observations only, not parser readiness.

The same four focused tests also passed on macOS CPython 3.13.9. The CLI
refused an extra argument with exit 2 and no stdout; it accepts no document input.

Independent review found reader initialization outside cleanup: first thread
start failure leaked the owned child. First/second start-failure tests observed
red exit 1; moving initialization under cleanup and joining only started readers
now yields capture_failed, killed/reaped child and closed pipes. Five tests green
on CPython 3.9.6. Scoped correction review pending.

Scoped review closed cleanup with the new test plus eight constructor/start
RuntimeError/MemoryError variants: reaped child, closed pipes, no live readers.
All five corrected tests also passed on macOS Python 3.13.9. Final candidate CI
and remote main readback remain pending; parser readiness is still false.
