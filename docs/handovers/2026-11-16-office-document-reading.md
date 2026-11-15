# Office document reading preparation

2026-11-16: P1/project foundations integrated at `12403b8` via PR #76.
This preparation implements no production PDF/DOCX input; the existing office
sources still accept TXT/Markdown/CSV. The new product contract fixes a bounded
local/shared-service design, original/located selection identity, safe admission,
upgrade/backup and A07–A09 user-path exit, without reopening agent work.

Ran `python scripts/office_document_probe.py --out ABSENT_DIRECTORY` on macOS 27.0
arm64 Python 3.12.14 with pypdf 6.21.4/python-docx 1.2.0/ReportLab 4.4.9:12 synthetic
fixtures passed their discriminating assertions; peak parser child RSS about
40.4 MB. Libraries also parsed over-proposed-page/paragraph samples and an external
relationship, proving host admission cannot be inferred from library success.
Ruff E9/F passed for the probe. Generated artifacts and detailed digests remain
private; this repository carries the repeatable generator, not personal documents.

Next: register one exact P2 runtime scope from latest main, add failing dense/CJK,
malicious/over-limit and resource-worker cases, then shared explicit inspection,
selection/acceptance and source versions with backup-first schema migration.
Do not repeat the completed foundation UI/backend/CI work. The full Web/CLI PDF
and DOCX journey and production memory/network/resource safety are unverified.


Scoped preparation review found two issues: optimized Python removed acceptance
assertions yet printed success, and the initial A07 exit omitted generated
summary/QA citation binding. `-O` now refuses before output creation (observed
RC1/no destination after original RC0 false-confidence success); ordinary corrected
12-fixture run passed. The contract now requires deterministic summary and QA
citations resolving the frozen document/version/selection, invalid locator refusal
and retained historical snapshots, without claiming model factual accuracy.
