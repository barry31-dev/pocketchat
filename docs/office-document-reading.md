# Bounded office document reading

2026-11-16 · Approved preparation within the existing P2 direction. This page
is the product-owned implementation contract, not a claim that PDF/DOCX import
exists. Current source admission still accepts TXT/Markdown/CSV only. The additive
`office documents check --file` command checks a bounded DOCX package but does
not extract text, accept a source or open an office workspace. P1 and
project foundations are integrated; reuse their workspace, owner guard, prepared
context, consent, request receipt and historical versions.

## 1. User result and selected design

Both Web and office CLI can explicitly inspect a small ordinary PDF or DOCX,
select located text, save an immutable source snapshot, ask about that selected
material and reopen the original evidence. A07–A09 from the
[office roadmap](chat-office-roadmap.zh-CN.md#p2普通文档阅读与来源问答)
remain the full P2 exit; preparation is not that exit.

Options considered: cloud extraction would add credentials and external upload
without approval; uploading binary text under the existing source route would
lose file identity and location. Use one bounded local extraction service with
optional dependencies instead. The ordinary chat installation remains dependency
free. Initial candidates are `pypdf>=6.21.4,<7`, `python-docx>=1.2,<2` and a safe
XML admission dependency; the actual Python 3.9/3.13 dependency matrix must be
verified before these become package requirements. Never install at runtime.
ReportLab/Pillow are synthetic-probe dependencies only, not product imports.

Excluded: OCR, scanned-document understanding, password entry, macros, embedded
objects, remote URLs/relationships, archive extraction to disk, arbitrary tools,
code execution, cloud parsers, complex layout restoration and DOCX page numbers.
Source text remains untrusted context data, not project instructions or consent.

## 2. Proposed first limits and fail-closed admission

These are implementation requirements informed by §7's small synthetic samples,
not proof of arbitrary-file resource safety. Check each boundary independently;
never turn a raw-file ceiling into a claim about decoded memory use.

| Boundary | First ceiling | Refusal or explicit next action |
|---|---:|---|
| Raw file | 4 MiB | Refuse before parsing; choose a smaller file |
| PDF pages | 32 | Refuse before page extraction; no silent first-32 import |
| DOCX ZIP members | 512 | Inventory before reading members; refuse excess |
| Expanded ZIP total/member | 8 MiB / 2 MiB | Reject declarations and streamed expansion exceeding either |
| Member expansion ratio | 100:1 | Reject risky member; not a complete bomb detector |
| DOCX body paragraphs/table cells | 256 / 1,024 | Refuse excess; not a DOCX-page approximation |
| Extracted selected/all text | 12 KiB / 64 KiB UTF-8 | Selection may fit; otherwise refuse, never silently truncate |
| Worker wall time/output | 10 s / 512 KiB | Terminate owned worker; preserve input and no accepted source |
| Worker peak-memory target | 256 MiB | Requires an independently proven platform guard before arbitrary-file parsing is enabled |
| Send limits | Existing 8 sources, 12 KiB each, 48 KiB combined, plus full prepared-payload ceilings | Preview refusal creates no request/provider call |

Inventory rejects duplicate or traversal names, encrypted ZIP members, symlinks,
DTD/entity declarations, macros and embedded/active package parts. Allowed OOXML
parts/relationships are closed: external relationships are refused before
`Document` parsing, not ignored after a library has loaded the package. Content
signature and package content types must agree with the chosen PDF/DOCX format;
extension alone is not evidence. PDF encryption refuses before password attempts;
active actions/attachments are not executed and must be explicitly refused by
validated admission. Never use the probe below as this production validator.

Use an owned parser child with bounded input/output, wall deadline and cleanup.
A parser exception, missing dependency, timeout or unknown platform resource guard
returns a named refusal; do not translate it to an empty successful source.
No network is permitted by the production parsing boundary. A process is not a
sandbox: resource and network isolation must be exercised on the actual target
platform, including failure/termination, before enabling user files. Unsupported
platforms keep text chat/source import and explicitly refuse binary parsing.
The probe measures tiny generated files only and does not provide this isolation.

## 3. Source identities, locations and unchanged historical evidence

Store a binary-document record separately from existing text-source rows. Do not
rename a PDF to TXT or change the meaning of the existing `sources.sha256`.
Records bind logical project ID, conversation ID, format, sanitized display name,
raw SHA-256/byte count, extractor name/version/options, canonical extracted
segment SHA-256/byte count, status and explicit coverage/omission reasons.
A source version is derived from raw digest plus extractor/options and extraction
digest; identical input/version can be recognized without overwriting old IDs.

PDF segments use one-based page numbers and zero-based UTF-8 byte ranges within
that page's extracted text. DOCX uses one-based body paragraph or table/row/cell
coordinates, with byte ranges within each segment. Retain body order and blank
positions; explicitly list omitted headers/footers, images or unsupported parts.
Do not claim PDF visual reading order or DOCX pagination. No-text PDF is reported
as `no_extracted_text`, not classified as a proven scan or fabricated content.

A selection binds exact version/segment IDs and ranges. Every accepted selected
text source points back to its document/version and location. The prepared context
must freeze those bindings; receipt recovery returns the old result even after a
new version is imported. A changed file gets a new raw identity; old reports,
source selections, manifests and backups retain their original snapshots.
No import or preview calls a model. Generated answers carry the selected source
labels and excerpts; literal locator validation can verify a cited selection,
not the truth or quality of the model's claim.

## 4. Shared visible Web and CLI flow

The names here define the proposed additive internal office surface; current
commands/routes are unchanged until implementation.

1. Web file picker and native `office documents inspect --file FILE` both send
   bounded bytes to the owner-held shared service, never a browser host path.
   The existing authenticated native loopback transport stays unchanged. Inspection
   returns a short-lived opaque inspection ID/digest, segment inventory, versions,
   excluded parts and a visible refusal/partial status. No SQLite source/run is
   accepted yet. Raw bytes live only in a bounded owner-private staging record.
2. Both clients display matching raw/extracted digests and text locations. User
   chooses explicit page/paragraph/cell ranges. `office documents accept` requires
   current inspection identity/digest and ranges; Web has the same explicit action.
   Acceptance saves an immutable document/selection, never starts a provider run.
   Wrong project, expired/changed staging, stale owner or invalid ranges refuse.
3. Existing preview/confirm/send consumes only accepted selections, with disclosure
   of selected versus unselected locations. Frozen context/confirmation includes
   document/selection digests. Use original send/stop/receipt mechanisms unchanged.
4. Clicking a source citation or `office documents show` returns only the owned
   snapshot/segment. Importing a changed file creates a new version, not an update.

The document inspect route alone permits a closed JSON envelope containing a
canonical base64 byte string (6 MiB encoded-request ceiling, 4 MiB decoded ceiling).
Validate encoded size/alphabet/padding before bounded decode and compare decoded
byte count/digest; no caller paths or alternate encodings. Existing JSON routes
retain their 96 KiB ceiling and native proof, browser Origin/CSRF and owner checks.
The decoded-upload budget, parser-child budget and staging aggregate are separate;
large upload parsing cannot hold the UI/owner thread indefinitely. Add an explicit
body-read deadline and malformed/duplicate fields/length tests at this route.

Bound staging: at most two raw inspections (8 MiB aggregate), explicit release or
five-minute expiry, no background autostart. Failed/expired inspections are cleaned
by the owner; successful acceptance consumes staging atomically. Process restart
invalidates inspection authority, not accepted source history. Unsupported client
uploads and capability failures have visible named errors rather than a disabled
button with no reason. Full keyboard/mobile/accessibility and real browser upload
need separate user-path evidence during implementation, not this preparation.

## 5. Backup, schema and recovery before source acceptance

Use an explicit backup-first office upgrade for any new persistent document tables.
The target schema version and backup-envelope version must be chosen from current
main at implementation time; do not repurpose schema-5 fields. A versioned backup
includes raw snapshot bytes, normalized extractor metadata, segments/selections
and every report/reference binding, under a bounded canonical envelope. Recompute
all raw/extracted digests and references before restore; never execute imported
SQL, trust embedded paths, overwrite an existing directory or preserve old bearer,
inspection/confirmation authority. Retain existing backup readers/exclusions.

Acceptance commits a full validated document and selection in one owner-guarded
transaction, or accepts nothing. Validation/worker/size failures leave existing
sources, messages, artifacts and run receipts unchanged. A failed upgrade leaves
the original project plus verified old backup intact; rollback restores into a
fresh directory, not an in-place downgrade. Add destination-limit/refusal tests
before accepting raw files; binary backups must not bypass import resource rules.

## 6. Implementation plan and discriminating exit

Do not register duplicate kernels or confuse this preparation card with runtime
completion. Start one conflict-checked P2 implementation task from current main,
with an exact scope covering only the required existing call sites and new files:

- New extraction/admission module and owned worker; meaningful failing fixtures
  for signature/type, encrypted/no-text/broken PDF, malicious OOXML, archive
  limits, resource timeout/kill and unknown capabilities. Versioned parser options
  and closed normalized source schema precede UI acceptance.
- `office_store.py`, `office_backup.py`, `office_context.py`, `office.py`,
  `office_service.py`, `office_web.py`, `office_cli.py`, existing static UI and
  optional dependencies: additive source identity, explicit upgrade/restore,
  shared inspect/select/accept/show and prepared-payload provenance. Only change
  Session if an actual missing primitive is demonstrated; no legacy runtime rewrite.
- Corresponding office/extraction/backup/CLI/browser tests, this contract and
  format/security/compatibility/README/CHANGELOG documentation. Establish actual
  file names in that task's exclusive scope before edits, not via this outline.

A07: real Web/CLI subprocess journeys with the same text PDF and body/table DOCX
produce equal version/selection IDs and located excerpts; unchanged displayed
payload equals recorded loopback HTTP, unselected material is disclosed as unread.
A deterministic response fixture exercises both summary and question-answer paths:
each displayed citation resolves to the frozen document/version/selection and
exact located excerpt in both clients. Unknown, out-of-range or mismatched-version
locators remain invalid and are never relabelled as verified citations. Valid
historical locators still open their original snapshot after a new import; a newer
source cannot rewrite them. This verifies citation binding, not model factuality.
A08: representative unsafe/empty/partial/missing-dependency/over-limit cases refuse
or disclose exactly known coverage; zero provider/receipt/source writes on refusal,
no network/macros/object execution, owned worker terminated and staging cleaned.
A09: changed raw input receives a new version; old source/report/backup still resolves
to old raw/extracted digests after restart and cross-client access. Tampered backup
or foreign project/selection refuses with original/destination unchanged.
Run only new and affected checks; reuse unchanged foundations/P1 evidence. Necessary
exact candidate CI, scoped review and actual remote-main readback remain required.

## 7. Synthetic measurements and limitations

`python scripts/office_document_probe.py --out ABSENT_DIRECTORY` generates known
synthetic fixtures only, runs parser children under a 10-second wall deadline and
writes digests, exact segments, elapsed times and process peak RSS to `probe.json`.
It refuses optimized Python execution before creating outputs, so `-O` cannot
remove its acceptance assertions while still printing a successful result.
It intentionally observes that the libraries parse a 33-page PDF and 257-paragraph
DOCX and tolerate an external relationship; the host must enforce §2 beforehand.
The ratio-risk package is inventory-only and is never handed to the DOCX parser.
An appended PDF comment demonstrates changed raw identity, not changed text.

Observed 2026-11-16 on macOS 27.0 arm64 / Python 3.12.14 with pypdf 6.21.4,
python-docx 1.2.0, ReportLab 4.4.9: 12 synthetic cases completed; parser exceptions,
encryption and empty extracted text remained distinguishable. Largest measured
parser child peak RSS was 40,419,328 bytes; PDF cases took about 0.04–0.05 seconds,
DOCX cases about 0.02–0.03 seconds (library import included). These are small fixture
observations, not hard resource limits, Windows support or arbitrary-file safety.

The three-page PDF was 2,451 bytes/212 extracted characters; 32/33-page samples
were 17,494/18,013 bytes. Body/table DOCX was 36,784 bytes with three body paragraphs
and four cells, 827,106 declared expanded bytes; its largest ratio was about 32:1.
The 256/257-paragraph samples produced 15,021/15,080 joined text characters.
A 1 MiB repeated-byte package was 1,166 bytes with a ratio above 1,014:1; inventory
only. No broad limit was selected from file size alone.

Not measured: dense/font-heavy/large-object PDF, CJK extraction fidelity, complex
DOCX ordering/headers/footers, all ZIP/XML attacks, CPU/memory/network enforcement,
actual Web/CLI ingestion, production backup migration, Python 3.9/3.13 parser
compatibility, Windows parsing, model citations/quality, formal release or full P2.
Before runtime enablement, add representative dense/CJK and adversarial cases;
a guard unsupported on the actual platform remains a named capability refusal.


## 8. Implemented DOCX package check (not P2 extraction)

`pocketchat office --data-dir PRIVATE_DIR --json documents check --file small.docx`
returns raw SHA-256/bytes, member/expanded-byte counts, body-paragraph/table-cell
counts, `status=package_checked` and `text_extracted=false`. It ignores provider
configuration and does not open/create the data directory, save sources, call a
provider or produce a file. No extracted text, path or XML/ZIP exception content
is included in the result/refusal. Missing no-follow directory-descriptor I/O
returns a capability refusal, including on unsupported Windows installations.

Before XML inspection it checks the 4 MiB raw / 512-member / 2 MiB member /
8 MiB total / 100:1 declarations, names, regular entries, CRC/size/header agreement
and supported stored/deflated compression. Duplicate/traversal/linked/encrypted
entries, data descriptors, ZIP64 and unknown parts are refused. Raw DEFLATE streams
are independently expanded with bounded output, actual length/CRC, end-of-stream
and trailing-data checks (not the ZIP library's declaration-truncated reader).
End records, disk/count fields and central-directory bounds are checked, with no
extraction to disk.

The initial closed profile contains the main document, styles/settings/font
and numbering XML, themes, package/document relationships, core/app metadata,
data-only custom XML/properties and an opaque JPEG thumbnail. Unknown or active
parts, macros, embeddings, hyperlinks/external relationships, missing targets,
incompatible content types and active/field/drawing document elements are refused.
This is deliberately narrower than all valid DOCX files; refusal is not evidence
that a file is malicious. Nested tables count every cell; only direct body
paragraphs contribute to the 256 limit (not a pagination estimate).

All XML is read using namespace-aware SAX, DTD/entity declarations rejected even
for UTF-16, external entity resolution disabled, with 64-level/100,000-node and
64-attribute limits (namespace declarations included). A cooperative 10-second check is **not** a hard wall-time or
256 MiB platform resource guard. The bounded inventory/SAX step is not an isolated
optional document parser or a sandbox. Arbitrary PDF/DOCX extraction therefore
remains disabled pending actual platform resource/network/termination evidence.
No claim is made about extraction fidelity, CJK layout, completeness or model
citations. A successful check cannot be reused as source acceptance or parser
permission; future extraction must validate the exact bytes again.
