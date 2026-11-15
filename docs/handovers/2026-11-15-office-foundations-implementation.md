# Office foundation implementation checkpoint — 2026-11-15

Schema-5 project identity/preferences and explicit memory lifecycle are implemented.
Office preview and admitted send now share one canonical prepared turn with project,
recall, summary and recent-history provenance; Session never reselects the frozen
payload. Recording loopback SSE proves preview = saved plan = snapshot = HTTP body.
Summary CLI/service controls require explicit acceptance and original branch/range
SHA-256 bindings. Independent bounded reviews identified and closed lifecycle,
replay, history-window, backup-lineage and summary-route/reference defects.

Verified at this checkpoint: 168 office tests (1 skip), 12 Session tests and
Ruff E9/F passed on macOS Python 3.9.6; real loopback SSE payload equality passed.

Next: implement browser project/context/memory/summary controls, run targeted real
browser plus CLI journeys for OF01–OF10, then exact final-candidate CI and main
integration. This checkpoint does not certify full foundation acceptance, Windows,
model quality or release. No paid provider or formal release.
Contract: [foundations](../office-foundations.md).
Review: [record](../review-office-foundations-implementation-v1.json).

## Browser continuation plan — 2026-11-16

Keep the approved backend and existing visual language. First add a discriminating
real-browser/CLI regression for project instructions, explicit memory, frozen
preview and stale confirmation (red before controls). Wire the current sidebar to
the existing authenticated operations; preserve request IDs and confirmation on
previewed sends, and never automatically accept remembered text or summaries.
Add explicit summary-original selection and show payload/manifest without model
calls. Then run only the new browser journey and affected client regressions,
review the new diff independently, and continue the same original slice to its
remaining OF01–OF10 checks. No new product approval or release is implied.

Browser controls now cover explicit project instructions, guidance proposal/acceptance/forget, summary-original selection and frozen payload/manifest preview. Real browser/CLI subset passed with actual HTTP equality and stale-preview zero runs; 18 client race checks and 168 office tests (1 skip) passed. Full foundation acceptance and fresh new-diff review remain pending.


## Browser/CLI acceptance candidate — 2026-11-16

The owned Chrome/CLI fixture now exercises project instructions in both directions;
explicit proposal/acceptance/revision/retirement/forget; a changed project or recall
preference refusing a previously previewed request; manual original receipt recovery
without replay; unchanged raw HTTP request = displayed frozen payload; summary
acceptance preserving 400 original messages with explicit bounded-window omission;
independent project isolation; live-owner backup/restore preserving project ID,
accepted memory, summaries and original messages while omitting native credentials;
and schema-four GET remaining unchanged until explicit browser backup-first upgrade.
Over-limit input admits no new request. Untrusted memory text stays in the user data
envelope and the payload has no tools. A source/config/summary/history dependency
matrix independently checks zero-call stale refusal at the shared service layer.

Fresh-context review of the new browser diff found two issues: branch-preview
consent bypass and save-success obscuring failed readback. Both have reproductions
and focused corrections; correction review is recorded separately. Mobile controls
no longer overlap the title at 390 px. A rejected 60,001-character preview leaves
originals unchanged. HTTP fixture count is exactly one successful physical request
for the entire cross-client journey, including old-receipt recovery.

Evidence: `node tests/office_foundations_browser.cjs` with a fresh private output,
macOS Python 3.9.6, Node 22.23.4, headless installed Chrome. Initial red: missing
project control; subsequent branch-confirmation repro failed before its fix.
Final expanded browser journey passed, as did 18 existing VM client race cases and
the new six-dependency service test. The earlier 168-office-test run (one skip)
remains evidence for unchanged Python implementations; it predates the new test.
The prior candidate's full-suite and original scoped reviews are historical evidence,
not a newly run full-suite result or this candidate's CI.

Not verified here: model obedience/quality, paid services, complete accessibility,
Windows browser journeys, Word pagination, formal release or P2 document reading.
Exact candidate/PR CI and actual remote-main readback remain required for integration.

Expiry/sensitivity/corruption negative coverage additionally passed with a controlled UTC clock: expiry between preview/send invalidates confirmation, expired/sensitive records do not enter payload, corrupt store is a named refusal, with zero provider calls and no receipt. Backend code is unchanged.

Fresh-context substitute correction review read the exact b657dbb dispatch/readback changes and closed both original findings. This is static evidence for those fixes only; candidate-bound necessary CI and actual main readback are still pending. The interrupted original reviewer supplied no approval.

Final added-test/docs review identified an over-limit evidence gap: unchanged messages and zero calls alone did not prove zero failed receipts. Read-only actual run/request/status snapshots now compare equal before/after the oversized preview; static correction review closed that precise gap and the expanded real Chrome journey passed again. All scoped findings are closed; exact final-head CI and remote-main integration remain pending.
