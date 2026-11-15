# Office project instructions, context and controlled memory

2026-11-15 · **Approved design and continuation contract, not an implemented feature.**
This is the product-owned foundation extension to [office-web.md](office-web.md),
under the maintainer's approved chat/office direction and routine slice delegation.
It must precede P2 document-reading integration; it does not reopen P1 A01–A06.
Business acceptance remains in the [office roadmap](chat-office-roadmap.zh-CN.md).

## 1. Result, alternatives and current boundary

The user can reopen an office project in either Web or CLI, inspect/edit its own
instructions, preview the next request's selected context, and explicitly manage
project memory. Neither source material nor remembered text grants permissions.
Switching projects cannot silently carry over instructions, sources or memory.

Three approaches were considered:

- Flip existing office profile flags: rejected. Legacy recall store paths and
  workspace identities are caller-configurable, and office confirmation currently
  does not bind a memory selection or a project-instruction revision.
- Build a second office memory/projector: rejected. It duplicates validated v2
  lifecycle, retrieval and layered selection without solving admission ownership.
- **Reuse existing kernels behind office-owned admission and revision binding**:
  selected. Keep provider, local-tool, single-writer and disclosure boundaries.

At main `a25e77b4de021a9fe61879894e828363f0624466`, `office_config` disables
memory recall/search and context projection. `OfficeWorkspace.preview` binds the
prepared message, history, current branch, provider/config and model revision;
`submit_confirmed` checks that binding before accepting a new request. Existing
receipt recovery happens before preparing a new request. These are source
observations, not a new runtime test or evidence that foundations are available.

Reuse [project continuity](project-continuity.md), [memory v2 lifecycle](formats.md),
[retrieval](memory-retrieval.md), [projection](formats.md) and
[context explanation](context-explain.md). Legacy CLI behavior remains unchanged.

## 2. Project ownership and persistent data

The minimum project boundary is **one private office data directory**; this
extension does not add nested projects, accounts, directory browsing or a Web
endpoint that accepts an arbitrary host path. Open another project by explicitly
connecting to its independently owned workspace. Do not import legacy stores
merely because the current working directory or profile points at one.

Office schema 5 will add a server-generated stable logical `project_id`, project
instruction text/revision, and an explicit `memory_enabled` preference (default
false). This logical identity is not the existing path-derived native transport
`workspace_id`; keep the latter's bearer/proof checks unchanged. Schema 4 has no
such logical identity: generate it only during explicit verified-backup upgrade,
not as a side effect of GET, startup or sending. A genuinely new empty workspace
creates schema 5 and its identity once at explicit workspace creation; opening an
existing lower-version directory never silently upgrades it.

Memory remains one private office-owned v2 JSONL store, validated/mutated through
`memory_migration.py`, not copied into a second SQL memory table. Its workspace
identity must match the logical project. The adapter chooses its fixed internal
location; API requests and configured profiles cannot supply a store path.
Instruction/preference mutations use expected revisions. v2 revisions are per
record; the office adapter additionally binds the canonical whole-store digest
(no new v2 top-level revision field) so even a newly proposed/removed record
invalidates a pending preview. Stale revisions fail
without partial updates. The existing office owner and guard serialize mutations
and request admission; memory reads/writes also retain the v2 lock/file protections.
An unsafe, corrupt or externally changed store is a named refusal, not empty memory.

Restoring a backup into a new directory retains the logical project identity,
so the restored project's own accepted records remain usable, but creates fresh
native transport credentials. Copies with the same identity are separate restore
branches, not a synchronization mechanism; do not merge their records automatically.

## 3. Instructions and authority

Expose the effective layers with their origin, revision/digest and selected text:

1. Fixed office safety/disclosure rules, highest priority, not user-editable.
2. Current configuration instructions, observable, edited only through the existing
   operator configuration workflow; lower layers cannot replace them.
3. Explicit user project instructions, editable in the office project (12 KiB
   UTF-8 maximum), versioned rather than accepted through a source upload.
4. The current user's request, preserving the user's content/language.
5. History, accepted summaries, selected sources, previous drafts and recalled
   records: **context data**, never system policy or an authorization record.

Do not concatenate editable text after safety rules and assume order is a security
mechanism. Label/separate layers in the request; deterministic host checks continue
blocking tools, shell, outside-workspace access, fallback and extra model attempts,
regardless of provider interpretation. Even an accepted remembered instruction is
context data: the user must explicitly copy it to project instructions to make it
project guidance. It still cannot override fixed rules.

Show the project identifier and current instruction revision before editing or
sending. A mutation displays a preview and requires an explicit save/accept action;
merely opening, searching, reading or proposing does not make text effective.
Project instruction changes affect future requests only, never rewrite sent
requests, old messages, artifacts or source manifests.

## 4. One context plan, preview and send

Use the same office admission function for both entry points and actual sending.
It produces the exact provider messages plus a bounded provenance/exclusion
manifest; do not separately reimplement selection for the preview UI.

- Preserve fixed instructions and the pending request. Add the current project
  instructions, then the explicitly selected complete history window/accepted
  continuity summary and source envelopes, and admitted recall excerpts.
- Keep the existing eight-source, 12 KiB-per-source and 48 KiB-combined limits
  until P2 has its own approved bounded extraction contract. Retain report source
  inheritance and stable citation labels; truncation cannot silently drop a pinned
  predecessor or relabel old references.
- Let users preview selected history/source IDs, source digests/ranges, summary
  source references, recalled IDs/revisions/excerpts and exclusion reasons before
  confirming. Show an empty selection as empty, disabled recall as disabled and
  a failed read as refused. Expired/sensitive/foreign records expose reason/count,
  not their bodies in diagnostics.
- Enforce the configured character ceiling and JSON-byte ceiling against the
  **full provider payload**, not just the pending source envelope. Defaults inherit
  current bounded config. Fixed/pending/pinned content that cannot fit refuses
  before any provider call; the user can reduce selected history/material.
- Preserve complete exchanges and original branch selection. Never mix later
  responses from the old branch into an edited or regenerated branch. Ordinary
  window omission is visible, not a claim that omitted history was read.
- Token figures from `chars4.v1` are marked estimates. Actual usage is known only
  when a provider reports it. No preview call reads a provider credential or calls
  a model, writes a session/trace, or creates a request receipt.

A new confirmation binds logical project ID, project-instruction revision/digest,
memory preference revision/whole-store digest, admitted memory IDs/revisions/content digests,
summary revision/digest/source ranges, current history IDs/branch, source snapshots,
prompt/intent/predecessor, model/config revision and exact provider payload digest.
Preview does not itself accept a request. Under the owner guard, sending revalidates
all these inputs, including expiry at admission time, before accepting/saving the
prepared payload. Any selection or revision change returns 409 and asks for a new
preview, with **zero provider calls**; there is no silent best-effort re-preview.

After admission, use the frozen prepared payload, not a second Session-side
projection/recall pass. The current `initial_messages` interface is insufficient:
`Session._turn` still calls `_context_with_budget` even in legacy mode. Add a
narrow single-turn prepared-plan execution hook: validate/fail closed against
the payload/manifest digest and ceilings, reuse Session trace/snapshot, transport,
usage and cancellation, but never append/reselect/trim messages. It accepts only
the office-host prepared plan, not a public user-supplied bypass of admission.
The hook must freeze final wire options, including streaming, and prove equality
with the recorded HTTP body. Legacy `Session.run` stays unchanged. Later settings/retirement changes apply to future requests,
not a payload already accepted or sent. The request snapshot and manifest record
what was actually submitted; a preview is not execution evidence. Memory is
re-admitted each new turn, never cached as persistent system instructions.

Retain original-wire idempotency: same request ID/body/confirmation reads the
original receipt before consulting changed project state; mismatched body or
confirmation refuses. A stopped/unknown request does not become a new model call.

## 5. Long history and controlled memory

### 5.1 Explicit continuity summary

A bounded history window alone does not satisfy long-conversation continuity.
Support a user-authored continuity summary with digest-bound original run/message
references and ranges. Offer original text readback, editable key decisions and
unfinished items, and explicit acceptance (12 KiB UTF-8 maximum). The summary is
a separate versioned context item; it cannot replace/delete old messages or inherit
write/provider permissions. Reject missing references, cross-conversation/branch
references, changed digests and claims of verified facts without evidence. Changes
require a new accepted revision and invalidate pending confirmation.

This first extension does not automatically summarize with a paid model. Automatic
model-assisted proposals remain a later explicit request with disclosure/budget;
model output alone never accepts itself. The minimum summary path needs no model.

### 5.2 Memory lifecycle and retrieval

Reuse validated `pocketchat-memory.v2` source/verification/admission fields and
`memory_retrieval.py` admission-before-ranking. Expose Web and office CLI actions
for propose, inspect/list, accept, revise, retire, forget, recall preview and enable/
disable. These are operator actions, **not model-callable tools**.

- Proposals require user text, declared source and current project identity. First-version office memory is
  explicitly declared personal guidance, not an external-fact store. Never
  absorb all chat or generated reports automatically. Acceptance is distinct from
  factual verification; an own preference may be `not_required`, not “verified”.
- No in-place rewrite of accepted guidance. Revision creates a new unaccepted v2
  proposal referencing its predecessor; explicit acceptance retires the old one
  atomically under the existing store lock. Add this narrow transactional replacement primitive
  in `memory_migration.py`; existing separate propose/accept/retire calls cannot
  by themselves guarantee atomic replacement. Stale predecessor/revision refuses.
- The office adapter's closed acceptance/recall matrix permits only
  `category=user_preference|project_decision`, `source_kind=user_explicit`, and
  `verification_status=not_required` after an explicit “personal guidance, not
  independently verified external facts” declaration and acceptance. The source
  event binds the submitted text/digest, project and operator action. Proposals
  start unverified; accepting them sets `not_required` only through this gate.
  `source_note`, `workflow_hint`, other source kinds, externally factual records
  and all other verification values are refused for office acceptance/recall,
  even if a legacy store marks them accepted. `not_required` is not an exemption
  for source notes. The adapter does not infer truth from text or a category;
  misleading content authored as guidance is not machine-verified fact.
- Factual-memory verification/import is outside this first extension; no UI may
  self-assign `verified`. P2 sources remain digest-bound context data, not memory
  fact approvals. Proposed, unaccepted, retired, expired, sensitive and foreign
  records remain excluded before ranking. Model summaries remain unaccepted v2
  candidates. A verification label never makes data an instruction.
- Default recall is off. With explicit enablement, use the pending user's request
  as query (not arbitrary uploaded bodies), max 5 selected records, 8 KiB UTF-8
  combined excerpts, still within the total context ceiling. Return lexical reason,
  source refs and current revision so the user can see “why this was remembered”.
  Overflow is disclosed selection/omission, not silent full-content truncation.
- Disable/retire/forget invalidates pending preview and removes future selection;
  forget deletes the active-store record and evicts derived in-process selection.
  Lifecycle IDs in `supersedes`/`superseded_by`/`conflict_with` are historical
  provenance, not dereferenceable active recall pointers. Forget removes the
  addressed record; dangling historical IDs remain visible as “record forgotten
  or unavailable”, never reconstruct its text or resurrect it. If a later
  replacement proposal needs that predecessor, accepting it refuses because
  there is no current predecessor to replace. Forgetting a successor does not
  reactivate a retired predecessor. No separate content-bearing tombstone.
  It does **not** promise removal from old traces, sent payloads, sessions, exports,
  other restore branches or backups. Show this boundary before confirmation.

## 6. Upgrade, backup and failure

No schema change is made by this documentation commit. Implement schema 5 via the
existing explicit office upgrade workflow, keeping the schema-4 backup intact.
A new `pocketchat-office-backup.v2` envelope must include project metadata,
instructions/revisions, accepted continuity summaries/source bindings and the
validated office-owned memory store; old v1 backups remain readable using their
original exclusions and cannot silently acquire missing memory/instruction data.

Validate size/schema/digests and project identities before writing a restore.
Every accepted summary's original office run/message/range/digest binding must
resolve within the restored conversation/branch. Validate each v2 source-ref
structure and digest declaration without claiming external files/URLs were
re-fetched; historical lifecycle IDs may be unavailable after forget as above.
If a lifecycle target is present, its project and edge semantics must agree.
Normalize `B.supersedes=[A]` and `A.superseded_by=B` into the same predecessor→
successor edge A→B before checking the supersession DAG. Matching reciprocal
bookkeeping is not a cycle; `conflict_with` is not a supersession edge. Self-links,
true normalized replacement cycles and contradictory present edges refuse. A missing historical
ID alone is not a bad backup. Active recall selects only actual present/admitted
records; historical IDs confer no authority and never create a record. Write only into a fresh private directory, never overwrite
an existing project or reuse bearer/cookie/confirmation credentials. Restore does
not resume requests or preserve preview authority. Importing an old v1 backup needs
an explicit schema-5 upgrade before foundations are enabled. Older programs must
refuse schema 5 instead of editing its version number. Runtime rollback is restore
of the pre-upgrade backup into a new directory, not downgrade or shared-history rewrite.

## 7. Discriminating acceptance (both Web and office CLI)

These cases are **required future checks, not results from this commit**. Use local
synthetic data and a recording loopback provider; pin candidate/Python/browser,
request bytes, manifests and zero-call refusals. Do not rerun unchanged P1 cases.

| ID | User path and required assertion |
|---|---|
| OF01 | Edit project instructions in Web, reopen CLI and preview/send; reverse the direction. Matching ID/revision/digest appears in the snapshot. Project B contains none of A's instructions/material/memory. |
| OF02 | Source/template/remembered text requests shell or higher-priority override. Selection remains data and tools stay disabled even under a legacy tool-enabled profile; fixed safety rules remain present. Model obedience is separately unverified. |
| OF03 | Preview, then change instructions, model, history branch, summary, memory preference/record or source selection; each stale new submission refuses before provider call. Unchanged preview/send final streaming HTTP bytes and manifest agree. Expiry between preview/send also refuses. |
| OF04 | Lost response receipt, then change project settings; same ID/body/confirmation recovers the original run without a second call. Changed body/confirmation refuses; unknown/stopping does not auto-resend. |
| OF05 | Long conversation: select complete recent turns, accept a summary with key decision/unfinished item and original links, reopen and send. Manifest shows omitted original ranges and summary provenance; full originals remain readable; branch-incompatible/tampered links refuse. |
| OF06 | Fixed/project/pending/pinned source payload exceeds chars or bytes. Preview/send refuse with no receipt/provider call; user narrows scope. Estimated tokens are labelled estimates and missing provider usage remains unknown. |
| OF07 | Propose in one entry point, inspect/accept in the other; revise requires new acceptance. Wrongly labelling a `source_note` as `not_required` refuses; unaccepted/unverified fact/sensitive/expired/foreign/retired records never enter HTTP payload. Personal guidance is not a truth claim. Disabled recall reads no content into a request. |
| OF08 | Retire/forget/disable after preview; stale confirmation refuses. Next new request excludes it after process reopen; old trace/backup remains explicitly historical, not claimed erased. Corrupt/linked/mismatched store refuses rather than silently recalling zero. |
| OF09 | Verified-backup schema-4→5 upgrade and new-directory restore preserve conversation/source/report lineage, instructions, summaries and accepted project memory. Bad schema/digest/identity/reference refuses with original and destination unchanged. Revision replacement → forget predecessor and separately successor → backup/restore succeeds with missing historical IDs marked unavailable, never reactivated. Native credential/confirmation from the old directory cannot authorize the new one. |
| OF10 | Existing representative CLI JSON/search/context/memory contracts remain unchanged. Office read/preview operations never call a provider or mutate history; double-writer and stale-revision mutations refuse. |

Service tests prove admission/payload equality; browser and real CLI subprocess
journeys prove both visible entry points. A stub does not prove model reasoning,
full accessibility, Windows support, Word pagination or formal release readiness.

## 8. Implementation sequence and exclusive paths

This is one foundation result with internal checkpoints, not three duplicate
kernels or parallel claims. No runtime path below is leased by the docs-only task.
Before coding, register one conflict-checked implementation scope and failing
acceptance cases against current main; keep a candidate-bound review record.

1. **Office project admission:** schema 5, stable logical ID, revisioned instructions,
   explicit upgrade and backup v2. Files: `pocketchat/office_store.py`,
   `pocketchat/office_backup.py`, new `pocketchat/office_context.py`;
   `tests/test_office_store.py`, `tests/test_office_backup.py`,
   new `tests/test_office_context.py`. Prove OF01/02/09 ownership and unsafe-input
   refusals before adding user controls.
2. **One prepared context and explicit memory:** reuse `memory_migration.py`,
   `memory_retrieval.py`, `context_projection.py`, `context_explain.py` without
   enabling global legacy flags. Include `pocketchat/memory_migration.py` and
   `tests/test_memory_migration.py` for the atomic replacement primitive; retain
   old CLI operations and v2 record closure. Implement the office adapter's bounded lifecycle,
   accepted summaries and frozen preview/send plan in `pocketchat/office_context.py`,
   `pocketchat/office.py`; include `pocketchat/session.py` and `tests/test_session.py`
   for the additive single-turn prepared-plan hook (current `initial_messages`
   does not bypass `_context_with_budget`). Assert final streaming wire equality,
   rejected/tampered plans and unchanged legacy Session behavior; modify other
   existing kernels only if the leased scope
   expressly includes a demonstrated missing primitive. Test OF03–08 using
   `tests/test_office_context.py`, `tests/test_office.py` and
   `tests/test_office_backup.py`. Never allocate a second request projection.
3. **Both entry points and result:** add authenticated routes through
   `pocketchat/office_service.py`/`pocketchat/office_web.py`, CLI operations in
   `pocketchat/office_cli.py`, and visible project/context/memory controls in
   `pocketchat/office_static/app.js`, `index.html`, `style.css`.
   Tests: `tests/test_office_service.py`, `tests/test_office_web.py`,
   `tests/test_office_cli.py`, new `tests/office_foundations_browser.cjs`.
   Bind the actual subprocess/browser fixture to OF01–10 and the same request
   snapshots, then complete scoped review, necessary CI and main readback.

Update `docs/office-foundations.md`, both office docs, `docs/cli.md`,
`docs/formats.md`, `docs/compatibility.md`, `docs/compatibility.zh-CN.md`,
`CHANGELOG.md` and the implementation's exact review/handover paths with that
implementation. If an unlisted write is necessary, adjust scope before writing;
this list does not grant a blanket lease on `pocketchat/` or `tests/`.

Budget: one owned checkpoint at a time; reuse green evidence until inputs change.
Stop the affected action on unsafe state, conflicting owner or missing external
permission. Paid requests, new credentials, third-party rights, arbitrary files/
shell, coding, delegation, background work, tools/MCP/skills and tag/Release/package
upload remain excluded. After OF01–10 closes on integrated main, continue P2 A07–A09
against this admission/source-provenance boundary. A document commit alone does
not open P2 or mark these foundation cases complete.
