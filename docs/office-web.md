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
or citation relabeling is performed. Current schema-2 reopen retains all versions.
