# Office foundation implementation checkpoint — 2026-11-15

Schema 5 now creates a stable logical project identity only in a genuinely new
workspace or an explicit verified-backup upgrade. Revision-bound instructions and
memory preferences share authenticated service and CLI routes. V2 data-only backups
retain project metadata, validated v2 memory and digest-bound original summaries;
v1 restores stay legacy until explicit upgrade. Failure and unowned-file guards
preserve original data. This is not the complete OF01–OF10 result.

Explicit memory propose/accept/revise/retire/forget and recall now share authenticated
service and CLI, with legacy-guidance rejection and atomic predecessor replacement.
Independent review found and closed two lifecycle-graph defects with red regressions.

A tested single-turn prepared Session hook now avoids legacy reselection and refuses
unsafe config/snapshot or payload mismatch before calling the provider. It is not
yet wired into OfficeWorkspace and does not change current office send content.

Next: build one office-owned context plan and wire preview/admission/execute to it,
add summary operator controls; finally browser controls and full user-path acceptance. No paid provider
or formal release. Contract: [foundations](../office-foundations.md).
Review: [record](../review-office-foundations-implementation-v1.json).
