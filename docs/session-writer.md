# Session writer ownership

An explicit `--session` path has one live writer. The claim is a separate
`<session>.writer` file created exclusively before the session header or trace
and before any provider request. Its owner token is independent of the run id,
so two stale views that share a session cannot both append the same turn.

The session uses the same canonical path for the claim and for every append.
The session-file identity is bound when the claim is acquired; replacing the
session or claim path fails closed. Ownership is never inferred from a PID or a
TTL, and an unreadable or foreign claim is never stolen automatically.

```text
acquire claim -> open/adopt session -> bind file identity
-> run -> terminal trace event -> release only our claim
```

Initialization failure releases only the claim this process created. A normal,
failed or cancelled run releases it after the terminal trace event. Different
session paths have independent claims and may proceed concurrently.

## Failure behavior

If the claim is already present, `Session` raises the existing configuration
refusal before a provider request is sent. The claim file remains for an
operator to inspect; the process does not guess whether its owner is alive.
The next normal writer needs the stale claim removed by an explicit operator
action, not by silent repair.

This contract covers one host and one explicit session path. It is not a
distributed lock, an OS sandbox, or permission to rewrite history. `git`-free
workspaces and readers are unaffected.
