# Chat and file work: first closed-loop design

> The original-scope proposal/confirmation/apply core now exists as library
> primitives; it is not an implemented command surface, undo path or release claim.
> The user requested the first usable chat-and-file-work loop on 2026-11-05.
> Approval reachability and scheduling live in [the slice queue](slice-queue.md).
> Publication status stays in [the release ledger](release-ledger.md).
> [中文](file-work.zh-CN.md)

## 1. Goal and scope

One user starts `pocketchat`, chats, grants exact file access, asks for a text
change, reviews its diff, confirms it, sees a verified receipt, and can undo it.
They do not author policy/spec JSON or switch to `task exec` to modify a file.
The suggested release line is **v1.2.0**, subject to compatibility review and
separate candidate-specific publication authorization. No version changes here.

Reuse Session, profile/provider selection, request/trace evidence, local tools,
limits and deterministic acceptance. Retain existing `run`, `chat`, `task`,
profile grants and create-only `write_file` behavior outside the new mode.
Do not introduce a GUI, server, general approval framework, shell, network tool,
directory/glob grants, large/binary files, recursive delegation or arbitrary delete.

The first path supports normal local folders and dirty Git files. Existing task
overwrite still requires its own clean Git baseline: do not remove that guard
to make a different workflow work. A user's bytes at proposal time, not Git HEAD,
are the undo baseline.

## 2. Planned user contract

These commands are **not available yet**:

```text
pocketchat
/files
/files read a.txt
/files write a.txt report.md
Summarize a.txt, update its conclusion, then create report.md.
# One complete bounded diff is presented; nothing has been applied.
/apply <change-id>
/changes
/undo
/undo --session
/files revoke a.txt
```

- The existing full-screen and line interfaces share one file-work service.
  Startup names the current workspace and profile. Selecting a file does not
  select its directory. Mentioning a filename in a prompt does not grant access.
- `/files` only shows state. Explicit read/write commands create session-scoped,
  workspace-bound grants. Replacing an existing file needs both read and write
  grants; create-only does not silently grant reading other files.
- Before grants become active, disclose that selected text used for an answer is
  sent to the selected provider. Local evidence is not a promise of offline inference.
- Existing profile grants are displayed as importable, not silently approved.
  Revocation invalidates matching pending proposals. Workspace/profile changes
  invalidate pending approvals; session switching never imports another session's
  permissions. On restart, show saved grants and require explicit reactivation
  of write access. Do not modify global profile configuration.
- In this mode, advertise only authorized `read_file` and the new
  `propose_file_change(path, content)`. Hide immediate `write_file`. The model
  cannot apply, undo, approve itself or expand grants. Old tools retain their schemas.
- At most one pending proposal per model turn. Further files require another
  proposal/confirmation. Two files in a request are not one atomic transaction.
- `/apply` confirms the exact presented proposal. `/undo` and session-wide undo
  preview their compensating effects and require user confirmation. A general
  stdin stream is not a trusted approval channel; without interactive approval,
  file work remains pending. Existing piped commands keep their previous behavior.

## 3. Proposal, persistence and result contract

Candidate schema: `pocketchat-file-change.v1`. Its parser is closed: unknown
versions/fields, duplicate identities, incomplete bindings or invalid state
transitions fail rather than being coerced. The planning row freezes the exact
shape below; implementation tests must cover each rejection family.

| Field | Meaning |
|---|---|
| `schema_version` | exactly `pocketchat-file-change.v1` |
| `change_id` | unique local change identity, not a content-derived guess |
| `owner` | workspace identity and existing session/conversation/branch references |
| `origin` | run id, tool-call id and selected profile identity |
| `policy_sha256` | canonical current session file-grant policy digest |
| `path` / `operation` | normalized workspace-relative file; `create` or `replace` |
| `before` | existence, original byte length/hash, private object reference and observed file identity; absent for create |
| `after` | exact candidate byte length/hash and private object reference |
| `proposal_sha256` | canonical binding of the preceding immutable proposal fields, excluding itself, approval and events |
| `approval` | null until trusted confirmation; then proposal/policy digest and local interactive approval identity |
| `events` | ordered durable transitions with observed hashes/identities and bounded failure reason; confirmed undo events also bind `restores_change_id` and `restored_file_identity` |

Only `approval` and appended events evolve. The digest commits the bytes through
verified object hashes, not just mutable object filenames. Owner/profile/path
changes require a new proposal. Persist the before image (or proven absence),
candidate object and prepared record **before touching the target**.

Internal storage is `.pocketchat/changes/` under the declared workspace. It is
never model-readable, never a grant candidate, never sent as context, and never
included in a release artifact. Reject symlinked/replaced store directories and
use private directory/file permissions where supported. A hidden directory is
not a Git exclusion: documentation must tell users not to publish these records.
There is no automatic cleanup of undoable records.

The current original-scope checkpoint lives in `pocketchat.file_changes`,
`pocketchat.file_change_store` and the unregistered
`tools.execute_propose_file_change` helper. It persists the closed proposal,
before/candidate objects, trusted confirmation, prepared boundary and verified
apply receipt. It is not registered in `tool_contracts`, the CLI, Session or UI;
undo, restart reconciliation and default mutation remain later package work.

Bounds: UTF-8 text only, at most the existing 16 KiB per input/candidate file;
64 KiB per rendered diff, 64 KiB per metadata record, 16 MiB per workspace change
store, one pending proposal per turn. Count encoded bytes, including events and
object files. Refuse a new operation before crossing a limit; do not evict an old
before image. A truncated or incomplete diff cannot be approved.

```text
proposed -> prepared -> applied -> undone
    |          |          |
 rejected   recovery_required / failed / conflict
```

The file record owns these facts; session/trace only reference `change_id`.
A model run can be completed while a file proposal is pending. A proposed tool
result never says the file was written. Apply/undo make no provider request;
subsequent turns can receive bounded confirmed receipt references as trusted
state without rewriting old turns or inventing an unpaired tool message.

## 4. Safe apply and undo

Reuse path rules, sensitive/binary refusal, size limits, hashes and line counts.
Do not wire the current overwrite helper directly to a model approval: a path
check plus a fixed `.tmp` file is not the new transaction contract.

The writer needs no-follow, descriptor-relative directory/file access, exclusive
same-directory temporary files, cooperative writer ownership, current target
identity/hash checks, atomic replacement and independent post-write readback.
Missing runtime primitives or unverifiable ownership must refuse file mutation;
chat/read behavior has its own capability contract. A declared primitive is not
an execution result. Keep the existing OS/Python CI matrix; prioritize actual
macOS CPython 3.13 fixture evidence for the new loop.

The implemented core uses descriptor-relative `O_NOFOLLOW` traversal, private
0700/0600 store entries, an exclusive identity-bound writer claim, exclusive
same-directory temporary files, directory/file `fsync`, atomic replace (or
exclusive link for create), and reopened readback. A runtime missing those
primitives returns `file_unsupported_runtime`; if the target was changed but the
durable receipt cannot be saved, the prepared record and writer claim remain for
recovery inspection instead of reporting a no-op.

Preflight hashing plus atomic replace is **not general cross-process CAS**.
Document the cooperative single-writer/local-filesystem boundary. Detectable
external edits or path replacement must refuse without changing outside files.
Do not claim arbitrary hostile-process races, network-filesystem guarantees or
crash-proof exactly-once behavior.

An applied result needs target readback equal to the candidate and a durable
receipt. Report operation/path, before/after hash, line counts, change identity,
acceptance outcome and undo action. Write success, content acceptance and user
satisfaction are distinct. Acceptance failure after a write still reports the
applied effect and preserves undo; it is not a no-op.

Undo is a narrow compensating transaction:

- Restore the saved before bytes only if current identity/hash still match that
  applied change and current grants permit it; otherwise preserve the user's
  newer bytes and report conflict. Never restore Git HEAD implicitly.
- A newly created file can be removed only when this change created it
  exclusively and its identity/hash remain unchanged. This does not expose a
  delete tool or allow removing arbitrary files/directories.
- Reverse A -> B -> C as C -> B -> A. Atomic restore can create a new inode:
  the verified undo receipt must link the restored predecessor change and its
  new observed identity. The next undo accepts only that proven compensation
  lineage, never an arbitrary same-hash replacement or an obsolete inode.
  Repeated undo does not replay a side effect.
  Session-wide undo previews the reverse list, stops at the first conflict and
  reports completed and remaining items. It is not an atomic multi-file rollback.
- Session checkpoint/rewind does not touch files; file undo does not erase chat
  or trace. After a restart, query the persisted records, not an in-memory cache.
- An unresolved prepared record enters recovery inspection. Reconcile bound
  bytes/identity/owner/events when provable; otherwise keep recovery_required.
  Never infer success from similar text, silently retry a write or blindly roll back.

## 5. Cancellation and errors

Reuse Session cancellation and the task-boundary contract tracked by issue #31;
no parallel task state machine. Cancelling before confirmation changes no user
file. During the local commit segment, settle at an observable boundary and
report the actual applied effect. Cancelling the next step does not hide or
replay an earlier effect. Preserve evidence and explicit undo.

Stable file-work error kinds to freeze in the new envelope:
`file_permission_denied`, `file_stale_proposal`, `file_conflict`,
`file_confirmation_required`, `file_unsupported_runtime`, `file_too_large`,
`file_store_full`, `file_invalid_record`, `file_recovery_required`.
Each has a bounded, sanitized explanation and a concrete next action. Map legacy
tool errors at the adapter; do not rename their existing public error types or
CLI exit codes. Success/pending/refusal must agree between UI and service results.

## 6. Acceptance and release boundary

The minimum fixture covers two chat turns, authorized reading, preview/reject,
confirmed replacement, confirmed creation, deterministic file/text assertions,
undo, conflict refusal and restart/readback. Include non-Git and dirty Git
baselines, repeat edits, permission/profile/session changes, binary/oversize/
escape/symlink refusal, quota limits, writer collisions and interrupted prepare/
replace/receipt boundaries. Use real filesystem effects in offline CLI tests;
do not stub out apply or undo. A deliberately wrong output must fail acceptance.

Test both full-screen and line paths against the same service. A test that uses
`unittest.mock` must explicitly import it and run independently, not only after
another test has imported that package. Test-only repairs do not create a new
product slice. Do not run the entire product suite for this planning change.

For the release candidate, use the **built wheel outside the source checkout**
and one authorized, real tool-capable provider for the full path. Record exact
version/SHA, wheel hash, OS/Python, profile/model scope, session/run/change refs,
file hashes, assertions and exits. A router run proves that route, not a direct
hosted provider or another model. Stub, real-provider and manual terminal
usability evidence are separate. No new paid calls or credentials are implied.

The chat delivery package includes the reusable golden-path fixture and development
validation; E2E is an internal acceptance step, not a separate queue card. Its wheel
is not automatically the release wheel. In the release package, finish version, packaging and shipped-document changes **before freezing
the final SHA and wheel**. Then use that same fixture for the final wheel's
real-provider and manual acceptance, required checks and exact-candidate CI.
Development-wheel results cannot certify a different version or artifact.

Keep the final run evidence outside the frozen tree, bound to its SHA/wheel.
After publication, register that evidence and readbacks in a separate
documentation commit without moving the tag. A change to frozen product code,
packaging inputs or shipped behavior instructions invalidates the freeze and
requires proportionate revalidation. This is not an instruction to rerun
unrelated checks merely to add a post-publication ledger record. Public branch push,
merge, tag, Release and package-index upload remain separately authorized
external actions through the approved path. GitHub Release distribution does
not depend on package-index credentials. Read back tag identity, Release state,
wheel digest and a fresh public-artifact install before recording publication.

After this loop, prioritize bounded directory grant expansion, candidate-file
selection, multi-file review usability, richer diagnosis and observed platform
or provider gaps. Do not make all memory/context/evidence backlog work a hidden
prerequisite, and do not count this design as implementation or validation.


## 7. Delivery packaging

The 2026-11-05 user request reduces the remaining work to two implementation
packages plus one release package: **safe changes with undo**, **chat integration
with development acceptance**, then **final artifact and publication**. Exact IDs,
write scopes, dependencies and absorbed-row migration live only in
[slice queue](slice-queue.md#first-chat-and-file-work-sequence-2026-11-05).
The landed design is not reimplemented. Tests, schema/CLI/docs and necessary
review stay inside their owning package. Checkpoint commits and session resumes
do not create extra task IDs or relax leases, review depth, persistent undo,
trusted confirmation, final-artifact evidence or publication authorization.

Later stages are outcome-driven in the [development roadmap](agent-development-master-plan.zh-CN.md):
continuous tasks, governed memory/skills, bounded collaboration, and remote work
only after a separate product decision. They are not pre-created execution rows
and do not make a broad framework migration a prerequisite for this loop.
