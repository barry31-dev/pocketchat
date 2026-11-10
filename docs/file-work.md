# Chat and file work: first closed-loop design

> Proposal/confirmation/apply and persistent undo exist as library primitives.
> The opt-in Session read/propose checkpoint and trusted full-screen/line terminal
> confirmation checkpoint now exist. Cancellation and the reusable offline golden
> fixture are covered by the closeout tests; exact development-wheel evidence is
> recorded for each candidate. Real-provider/manual and release evidence are separate.
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

## 2. Current trusted terminal checkpoint

The current checkpoint exposes the following commands only through a terminal
whose stdin and stdout are both TTYs. The full-screen UI and the line fallback
use the same `FileWork` instance owned by the active `Session`:

```text
/files
/files read a.txt
/confirm <grant-digest>
/files write a.txt report.md
/confirm <grant-digest>
Summarize a.txt and propose the change.
/diff                         # or /apply [change-id]
/confirm <apply-digest>
/changes
/undo <change-id>
/confirm <undo-digest>
/files revoke a.txt
```

- `/files read|write` first displays the selected provider disclosure and exact
  paths. A second `/confirm` bound to that disclosure, path set, current profile
  and grant generation activates the grant. A filename in chat never grants it.
- Granted turns advertise only `read_file` and `propose_file_change`. The model
  cannot grant, confirm, apply, undo or call immediate `write_file`. A completed
  model run can coexist with a pending proposal and never means bytes were applied.
- `/diff` and `/apply [change-id]` both display the complete pending unified diff
  without applying it. C0/C1 and Unicode format controls are escaped, and the
  full-screen renderer preserves diff-line whitespace. Only the separately displayed,
  digest-bound `/confirm` can call the transaction core. Successful apply returns
  the core readback receipt; a refused apply consumes the already confirmed
  in-memory proposal and leaves the durable record authoritative.
- `/changes` lists persisted changes owned by the same workspace, conversation
  and branch. `/undo <change-id>` requires a fresh exact write grant, reads the
  persisted before/after objects, displays the complete compensating diff and
  binds confirmation to the applied receipt before invoking the core undo.
- Every file-control command reacquires the existing session writer. A concurrent
  owner, an in-flight reply, grant/profile/session change, stale proposal or wrong
  digest refuses the action. Revocation invalidates the displayed confirmation.
- Piped/non-TTY input cannot run any file-control command, including `/files`; it
  is rejected before creating a Session, trace or change store. Restarted services
  restore no grants or approvals. A trusted terminal may list the conversation's
  persisted change metadata without a path grant; undo still requires a fresh exact
  write grant for the target.

A trusted `/files cancel` revokes file grants and invalidates an unconfirmed
review without undoing applied changes. An interrupted model run also invalidates
its grants/pending proposal and records `run.cancelled`. If a provider remains
blocked after the bounded interrupt, the controller keeps its worker/busy slot
and Session writer until that request exits; a late answer cannot enable a second
writer. This does not guarantee prompt cancellation of arbitrary blocking I/O.

The reusable [file-work validation fixture](file-work-validation.md) exercises
real files, rejection, apply/readback, restart/undo, conflicts and cancellation
through this shared controller. Only the provider is substituted. Directory/glob
grants, session-wide undo, real-provider/manual acceptance and formal release
remain outside this offline validation.

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

The file-change service lives in `pocketchat.file_changes`,
`pocketchat.file_change_store` and the unregistered
`tools.execute_propose_file_change` helper. It persists the closed proposal,
before/candidate objects, trusted confirmation, prepared/apply receipts and
identity-bound undo receipts. Reopening the workspace store is sufficient to
undo a successful change or compute a dependency-safe session compensation
order; no in-memory cache is authoritative. The helper itself is not a default
model or user mutation command.

### Session read/propose checkpoint

A trusted host may explicitly construct `FileWork(workspace)` and supply it as
`Session(..., file_work=service)`. Normal CLI/default sessions do not activate
this mode. The service starts with no grants, ignores existing profile grants,
and binds its workspace identity, Session run identity and provider/profile/model.
The host must show `service.disclosure()` before calling `service.grant(...)`
with the exact read/write paths and matching disclosure digest. Disclosure is a
host responsibility; model text or a filename mention cannot invoke grants.
Only exact text-file paths are supported (at most 64, each at most 1024 UTF-8
bytes); directories, globs, internal storage and sensitive paths are refused.

This mode offers only granted `read_file` and `propose_file_change`; immediate
`write_file`, grant, apply, approve and undo are never model-callable. The new
proposal declaration does not extend the legacy task/profile tool inventory.
Reads and proposals reuse the core descriptor-relative no-follow boundary;
unavailable secure primitives refuse this mode's file I/O, without downgrading
it or changing ordinary chat/legacy tool behavior.

A proposal preserves the before bytes/candidate and durable record before
returning one paired `pending` result. The bounded model result contains a
change/proposal/path/operation reference, not an applied claim. The stable trace effect enum is unchanged: a proposal completed event records
`file_change_status=proposed` and `change_id`, omitting the generic effect field
rather than claiming an applied target or a read-only operation. The complete diff
is available through a defensive copy of `service.state()["pending"]`; this
checkpoint does not approve it. While that proposal is pending, another proposal
is refused and its tool is omitted from the next provider offer. Grant changes
invalidate pending presentation and request-policy bindings. Revocation remains
possible if a target becomes a directory. Workspace replacement, a different
Session or a provider/profile/model change refuse reusing the old grants; the
new destination does not receive another request from this file-work binding.
Restarted services have no implicitly active grants or pending approvals. The
stored proposal remains evidence, not restart authorization.

The trusted-terminal checkpoint attaches this service to both existing terminal
controllers and exposes apply/readback plus persisted single-change undo only after
separate digest-bound confirmation. The complete `PC-FILE-CHAT-1` package remains
covered by the reusable offline fixture; real-provider/manual acceptance and formal release remain separate
must still be integrated. A completed model run still does not imply changed files.

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
primitives returns `file_unsupported_runtime`. After an observed successful rename/link,
readback refusal or a receipt-store error is `file_recovery_required` with
`effect_disposition=applied`, not a pre-write conflict or a no-op. The available
before-image/record evidence is retained and normal claim release is skipped;
that decision survives a secondary claim-verification/preservation failure.
An unavailable store does not prove a durable receipt or current claim ownership,
and does not authorize replay or automatic stale-claim takeover. Successful undo
requires an explicit bounded approval identity and exact persisted lineage.

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
  the verified undo receipt links the restored predecessor change and its new
  observed identity. The next undo accepts only that proven compensation
  lineage, never an arbitrary same-hash replacement or an obsolete inode.
  Repeated undo returns the same durable receipt without replaying a side effect.
  The library derives a deterministic reverse order from persisted session
  records, stops at the first conflict and reports completed and remaining items.
  It is not an atomic multi-file rollback.
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
