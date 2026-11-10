# PocketChat version 1.2 candidate preparation

This is the approved chat-and-file release package's preparation record, not a
release authorization or another publication ledger. Publication facts live
only in [release-ledger.md](release-ledger.md). Routine preparation uses the
original `PC-FILE-RELEASE-1` task branch; the product queue records its precise
scope and remaining full-release completion condition.

## Inputs frozen together

- Runtime and package metadata target `1.2.0` in the existing version sources.
- The Session grant/proposal and trusted complete-diff apply/readback/undo service
  is reused, not rewritten. `/files cancel` and cancelled-worker ownership keep
  unconfirmed actions invalid while leaving applied receipts authoritative.
- Compatibility preserves legacy CLI/JSON and create-only tool boundaries.
- README, candidate guidance and changelog describe source capabilities, not a
  released-wheel or real-provider claim. Unsupported secure file runtimes refuse.

Freeze the final source commit **after** these inputs and the scoped review are
complete. Use `scripts/release_smoke.py` for the exact wheel, fresh-venv install,
version and manifest checks. Retain final source SHA, wheel name/bytes/SHA-256,
module manifests, environment and commands outside the frozen source tree.

## Artifact acceptance

Copy `examples/file-work-demo.py` outside the checkout, unset source import
paths and use that exact wheel's clean interpreter with `--source-sha` binding.
Run the normal and dirty-Git paths; deliberately wrong output must exit 1.
The [fixture guide](file-work-validation.md) defines the real-file assertions,
Session/run/change/hash evidence and limitations. Do not copy version 1.1
source/development-wheel results into this artifact's record.

Required CI is the repository's exact-candidate Python 3.9/3.13 platform matrix
plus package smoke. Secure-runtime-dependent cases skip explicitly where those
APIs are unsupported; a green job does not say skipped file tests ran.

## Completion boundaries

Preparation can integrate under the standing routine-development decision.
The original release card is not completed by source integration or an offline
wheel. Its next artifact checks remain:

1. Required real tool-capable provider and manual file-terminal acceptance,
   bound to this candidate/wheel. Do not use the offline fixture's provider
   substitution as either proof or new paid-provider authorization.
2. Specific tag/Release authority for this exact target and frozen candidate.
   No package-index upload is a hidden prerequisite.
3. Same-artifact publication through the approved path, then tag identity,
   Release state, wheel digest and a fresh public-artifact install readback.
4. Register those real readbacks in the ledger/evidence documentation without
   moving a published tag. A changed frozen input requires a new candidate.

Until these hold, retain the original card and exact candidate with explicit
missing evidence. Do not infer full release acceptance from a green local suite,
a branch push, the prior package review or a development wheel.
