# DOCX package admission slice

## Plan and completion check

Within the approved P2 reading contract, implement a read-only package check,
not an extraction or accepted source. Cloud extraction is excluded. Loading a
full DOM or `Document` before validation exposes unnecessary work; select bounded
stdlib ZIP reads and streaming SAX with DTD/entity rejection instead.

1. Write ordinary/CJK OOXML and adversarial ZIP/XML tests, run red.
2. Inventory ZIP declarations before reading; independently bound bytes,
   expansion and names. Read members without extracting to disk.
3. Stream XML with a closed content-type and relationship vocabulary; reject
   external relationships, DTD/entities, active/unknown parts and excess body
   paragraph/table-cell counts. Do not resolve URLs or collect text.
4. Add `office documents check --file` as metadata-only local CLI, bypassing
   configuration/workspace opening. Existing CLI JSON envelope stays v1.
5. Run focused tests and scoped review; controlled branch/PR, exact-head
   necessary CI, remote main readback and original-card completion.

Exclusive write scope: admission module, office CLI, their two test files,
reading contract, English/Chinese CLI docs, review record, this handover and
CHANGELOG. No dependency, store, backup schema, Web or parser change.

Input -> output: explicitly chosen small ordinary DOCX -> content-free raw digest,
byte/member/count metadata with `package_checked` and `text_extracted=false`;
unsafe/over-limit input -> named refusal with no workspace, source, run, provider
call or output-file creation. No raw/text content or private path in diagnostics.

Rollback: revert the additive module/CLI slice; no stored-data migration.

## Verification

macOS 27 arm64 / CPython 3.9.6: initial missing-module red (exit 1),
original CLI negative before integration (exit 1), 19 ZIP/XML cases and 29 office
CLI regression tests passed (exit 0). Ruff E9/F passed. Five public contract/bilingual documentation guards and one
CLI documentation guard passed (exit 0). The four new CLI cases were rerun only
after adding the explicit unsupported-capability assertion. Tiny generated CJK OOXML
metadata only; no extraction-fidelity claim. Original measured 17-part body/table
DOCX and 256-paragraph DOCX passed package checks; external relationship and
ratio-risk DOCX refused. Source files unchanged. Corrupt deflate first raised uncaught `zlib.error`;
its red test now returns a named, content-free ZIP refusal. Parser isolation/resource guards, PDF input, DOCX text fidelity,
Web/source acceptance/backup upgrades, model quality and complete P2 remain
outside this slice. Package validation is not proof of arbitrary-file safety.


Independent review found four real incorrect-success paths: consistently forged
expanded length/CRC and unfinished DEFLATE, tolerated ZIP64/EOCD fields,
unrefused hyperlink elements, and namespace declarations excluded from attrs.
Three red tests reproduced the classes before fixes. Raw-stream expansion now
independently bounds/counts output, validates CRC/eof/no trailing data; ZIP end
and directory fields are checked; hyperlink elements refused; namespace counts
included. All 18 admission cases passed after these corrections. Scoped second
review requested; this record is not an integration approval yet.


Scoped second review closed the original four with independent probes, but found
local/central name comparison lost when replacing ZipFile.open. The new
traversal-name mismatch test failed before the fix; exact encoded local/central
name comparison now precedes raw member reading. All 19 admission tests passed.
Third review is limited to this new diff, not a repeat of the original four.

Third narrow review explicitly closed the name mismatch with 1 test, 4 matching
and 12 mismatch probes. All five findings closed; package-check-only code review
ready for integration gates. Exact candidate CI and remote main are still pending.
