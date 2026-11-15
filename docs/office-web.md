# Chat-first local office workspace

## Approved slice: PC-OFFICE-WEB-1

The maintainer approved this direction on 2026-11-14: retain and improve ordinary
chat and the CLI; add a local browser entry for single-agent office work. This
slice delivers selected text materials → sourced report → versioned revision →
Word download → reopen and continue. It does not turn PocketChat into a coding
agent or a multi-agent runtime. Approval is not a release or quality verdict.

Exclusive paths are recorded in `review-office-web-v1.json`. The implementation
reuses `Session`, configuration, provider adapters, admission budgets and request
traces. A small SQLite workspace owns browser conversations, immutable uploaded
text, queued run receipts and report versions. It does not own existing CLI
sessions and cannot modify arbitrary workspace files.

## Implementation plan and completion checks

1. Add a bounded private store: conversations, selected immutable UTF-8 sources,
   run/request IDs and report versions; reject paths, duplicate request payloads,
   stale revision, unknown/foreign sources and concurrent runs. Red tests cover
   restart: interrupted work becomes unknown, never automatically replayed.
2. Reuse Session for one model turn at a time. Ordinary chat has no report or
   planning ceremony. The Report shortcut uses the same composer; revision pins
   the chosen previous report and source versions. Disable all model-callable
   file/memory tools and fallback profiles for this entry. Failed/stopped output
   is not a completed report. Text streaming is a preview, not durable completion.
3. Add optional `python-docx` generation using its existing package API (MIT,
   1.2.0, Python >=3.9); no custom Office parser, shell, macros or external links.
   Test actual ZIP/XML content and deterministic source appendix. Missing optional
   dependency gives a clear refusal, without breaking CLI or Markdown download.
4. Serve packaged HTML/CSS/JS on literal 127.0.0.1 only. Start `pocketchat web
   --config FILE --data-dir PRIVATE_DIR`; show a random bootstrap token in a URL
   fragment, never an HTTP query/log. Login exchanges the token for an HttpOnly
   SameSite=Strict cookie. JSON writes require exact Origin and a CSRF header.
   Reject hostile Host, oversized/ambiguous framing, traversal and unknown fields;
   expose only fixed assets and opaque IDs, never a general file server.
5. Build a quiet, chat-first interface: conversation search/new/rename; streaming
   replies; copy/stop/retry; source picker with disclosure; report/revision cards,
   Markdown/Word downloads and reopen. English chrome, user content unchanged.
   Safe text/limited Markdown DOM construction, no raw HTML interpretation, no
   CDN/assets/telemetry. Responsive keyboard operation and reduced motion.
6. Run focused tests and a real loopback provider + HTTP/browser journey. Open
   exported DOCX with a document reader and inspect its rendered layout when the
   renderer is available. Deep fresh-context review, exact-head CI and main
   readback follow. Real provider reasoning quality is a separate unrun boundary
   unless explicitly authorized and exercised.

## Limits and authority

At most 8 selected text sources, 48 KiB UTF-8 combined, 12 KiB each; accepted
extensions `.txt`, `.md`, `.csv`. User-selected files are copied, not granted as
arbitrary host paths. CSV is source text here, not a spreadsheet engine. A
submission explicitly discloses that the prompt, selected material and relevant
conversation history go to the displayed configured provider. Local-first does
not mean an external provider stays offline. Sources are untrusted data, not
instructions or authority. Source IDs/digests prove provenance, not the factual
correctness or completeness of model citations.

Only one request may execute at once per workspace. Each request ID is durable
and content-bound; retrying its transport reads its receipt, not a second model
call. Explicit retry after failure uses a new ID. A cancelled request retains
its slot until its worker actually exits; no abrupt process kill or automatic
resume. Restart classifies unfinished work as unknown. Confirmed old messages
and report versions remain available; a report revision never overwrites them.
The root is a private operator-owned directory; this is not an OS sandbox against
another process with the same account privileges.

No DOCX/PDF input, OCR, XLSX/PPTX output, arbitrary code/shell, multi-agent,
background jobs, cloud accounts, external send or formal release in this slice.
The existing CLI/config/JSON/session formats are unchanged. A new optional extra,
command and independent browser-workspace format are additive.

## Rollback and evidence

Stop the local server and keep its private directory. Revert this slice to
remove the new entry; existing CLI workspaces remain untouched. Never downgrade
or delete the browser workspace to make a test pass. Runtime state belongs
outside Git. Actual commands/results and unverified boundaries are maintained
in the review record and same-batch handover, not claimed by this plan.

## Review hardening

Report citation labels are stable within a conversation. A revision includes
its predecessor's source map, and reports explicitly pin previously disclosed
source dependencies rather than relabeling old citations as new files. This
can reach the eight-source limit; start a fresh conversation instead of dropping
provenance. The source manifest identifies supplied material, not a fact check.

Unknown POST receipt recovery reuses the same request ID, including reload in
the same browser tab. An explicit subsequent report revision targets the latest
completed version. Truncated or filtered output stays a failed receipt rather
than a completed downloadable report. Partial text remains in that receipt.

The office-only transport guard checks before send, after headers and after each
SSE line/read block against cancellation and a total deadline. Silent blocking
I/O remains bounded by the configured socket timeout; a server that sends
heartbeat lines cannot keep the workspace alive indefinitely. The default CLI
transport has no new guard unless the host explicitly supplies one.

Browser workspace schema 2 rejects the unreleased schema-1 development workspace.
Keep the old directory intact and use a fresh directory; no automatic migration
or citation relabeling is performed. Existing schema-2/3 workspaces remain readable; explicit upgrade enables current features.

## P1 foundation: connect from the CLI

An additive [office CLI](cli.md#office--shared-browsercli-workspace-p1-foundation)
can operate on this server's conversations, material and exact report versions.
Native clients use a separate private workspace-bound bearer discovery file;
browser cookie, same-origin and CSRF checks are retained. Stale/untrusted
ownership refuses, never silently starts another writer. `office backup` connects
to the live owner; restore always targets a new directory. See the explicit
[data-only backup exclusions](formats.md#office-data-backup-v1).

This is **not completion of P1**. Full package acceptance remains separate.
Existing report creation/revision and browser receipt recovery are reused.

## Daily conversations

Search finds conversation titles and literal message-body matches. Choose a message
match to open its conversation and focus the exact saved message. Results are plain
text, limited to 100; **Archived conversations** changes both list and search scope.
**Archive** hides without deleting; **Restore** makes it writable again. Read and
version export remain available while archived. Older workspaces show **Back up &
upgrade**; its verified backup is kept inside the private workspace before upgrading schema 2/3 to schema 4.

Unsent drafts (message, report intent, selected material and predecessor) are saved
in this browser tab, separately for each workspace and conversation, including a
new-conversation draft. Switching or refreshing restores the matching draft but
never consent. Closing the tab may discard drafts. Browser drafts are not CLI
drafts, are not in workspace backups, and never count as sent history. Pending
request recovery has a separate workspace-bound record; refresh does not resend.

Draft edits revoke consent. Upload, archive, rename and new-conversation creation
hold a short UI action lock; delayed responses cannot replace another conversation's
composer. Sending snapshots content before any asynchronous creation and refuses
if that draft changes before dispatch. Legacy unbound tab receipts show **Check
saved receipt**: this only reads and verifies the saved request in the selected
workspace, never replays it. A missing/mismatched receipt remains blocked; discarding
removes only the tab record after an explicit warning, not the server request.


## Existing configured model choices

The header selector lists only the configuration profiles loaded by the current
owner. Listing/selecting performs no model connection test. It changes new
requests only and refuses until the active worker exits. New requests pin their
model/endpoint. Changing a profile revokes browser consent; if another client
changes the displayed model/endpoint before dispatch, the browser refuses and
asks for fresh confirmation. Content-bound server confirmation also rejects a
change between preview and send. Existing request receipt recovery remains
content-bound and never substitutes the new model for the old request.


## Non-destructive chat branches

Choose **Edit question** on a saved ordinary-chat question, or **Regenerate** on
its reply. Both restore that turn as an unsent draft; send unchanged to regenerate
or change the text first. **Review branch** displays the exact original history
and message/material to send, without calling the model. Only then can you confirm
and send. Any changed draft, model or context requires another review. No model call
happens when choosing the action. **Continue current branch instead** drops the
branch selection while retaining draft text.

A successful new reply becomes the active branch, starting from the selected
turn's original context prefix. Its old reply and subsequent messages stay in
**Saved earlier branches**, excluded from new active context but still searchable
and copyable. Search opens the saved section at the exact message. A failed,
cancelled or interrupted attempt does not replace the active path. Reopening
retains the path; branch drafts remain tab-local and never restore consent.

Reports keep explicit **Revise** and exact-version export, not chat regeneration.
Sources keep their conversation-scoped labels; old reports remain accessible
regardless of the active chat branch. [CLI branch controls](cli.md#office-chat-branches)
use the same service, receipts and confirmation. Branches require schema 4;
older workspaces must explicitly back up and upgrade. Never downgrade the version
number: restore a verified pre-upgrade backup to a new directory instead.

### Keyboard and long conversations

Tab / Shift+Tab move through native controls; Space toggles consent, and Enter
activates the focused Send, Stop or retry button. Enter inside Message inserts a
newline, not a request. The Add material label shows a focus ring while its native
file input is focused. Search matches can be opened with Enter and move focus to
the saved message. Unsent text remains tab-local when switching or reloading.

A CLI request is visible when the browser opens/reopens its conversation. Either
client can stop that same run; `stopping` is not completion. Wait for the final
receipt before retrying, and explicitly review and confirm a retry. Stopped output
is not appended as a completed message or report.

The owned local fixture supports a separate remaining-P1 check, without rerunning
the report/branch journey: set `OFFICE_REMAINING_ONLY=1`, `OFFICE_OUTPUT` to a new
private evidence directory, and `OFFICE_PYTHON` to a compatible Python; run
`node tests/office_p1_browser.cjs` with Playwright and Chrome available. The check
uses 200 seeded turns (400 saved messages), two gated local-provider requests,
keyboard/search/draft actions and both stop directions. This is not a benchmark,
a live-provider test or a claim of unlimited history/accessibility coverage.

Use `OFFICE_REVERSE_EXPORT_ONLY=1` instead for the narrow A01 reverse path:
two browser-imported materials → browser report → CLI revision → browser downloads
of each old/new Markdown and Word version, with exact content/source checks and
python-docx readback. These modes use fresh fixtures and never a real account.


## Foundation continuation contract (2026-11-15)

The bounded extension is owned by [office-foundations.md](office-foundations.md):
project instructions, one context preview/send plan and explicit v2 project memory,
with OF01–OF10 acceptance and staged write scope. Schema-5 projects expose explicit
instructions, default-off accepted memory, continuity summaries and one frozen
preview/send plan through the shared browser/CLI service. Older workspaces require
a verified-backup-first upgrade; opening a page never silently migrates them.
The original slice's memory/context exclusions describe its initial scope, not a
permanent ban on this reviewed extension. P1 remains the existing integrated
result; foundations must close before P2 document-reading integration. Existing
CLI memory/context features are not evidence of office-entry availability.

### Using project context in the browser

Open **Project & context** in Materials & drafts. Save instructions or an explicit
memory preference with the current project revision. **Propose guidance** requires
a personal/project-guidance declaration; **Accept** is separate. Revising creates
a new candidate and accepting it retires the predecessor. **Forget** requires
acknowledgement that historical traces, exports and backups are not erased.

**Load summary & originals** exposes the active branch's unchanged messages.
Select original evidence, write your own summary, and explicitly accept it. The
service binds message/run IDs, Unicode ranges and UTF-8 digests; remembered text
and summaries are not verified facts or tool grants.

**Preview request** displays the exact provider JSON plus selected/omitted history,
project revisions, memory digest and summary provenance, without a provider call.
Close it, then explicitly confirm **Send**. Changes in another client refuse the
old confirmation; the browser does not silently recompute what you reviewed.
Lost-response recovery retains the original request ID/body/confirmation and
recovers its receipt; it does not regenerate the request after project changes.
A successful save followed by failed readback reports both outcomes distinctly.

The synthetic browser/CLI acceptance entry is `tests/office_foundations_browser.cjs`
with a fresh private `OFFICE_OUTPUT` directory, compatible `OFFICE_PYTHON`, Playwright
and Chrome. It records actual request equality, no-call refusals, project isolation,
explicit replacement, summaries, historical backup/restore and explicit schema-4
upgrade. Its local fixtures do not prove model obedience, Windows browser behavior,
complete accessibility, Word pagination or formal release readiness.

## P2 document reading preparation

The [bounded document-reading contract](office-document-reading.md) records the
next approved P2 preparation, synthetic measurements and exact acceptance scope.
It does not enable PDF/DOCX import; current TXT/Markdown/CSV admission remains
unchanged until the versioned shared-service implementation is accepted.
