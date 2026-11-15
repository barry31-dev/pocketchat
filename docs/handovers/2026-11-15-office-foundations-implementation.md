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
