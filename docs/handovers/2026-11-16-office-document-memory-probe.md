# Native memory diagnostic handover

Goal: reproducible native errno and bounded allocation evidence for the approved
P2 memory-boundary dependency; not another document parser or product phase.

Six-path slice; production imports, document admission, store, Web and CLI
contracts stay unchanged. `scripts/office_document_memory_probe.py` accepts no
arguments, compiles its fixed C companion, and always reports parser readiness
false. See [the P2 diagnostic scope](../office-document-reading.md#native-memory-diagnostic-2026-11-16)
and [scoped review](../review-office-document-memory-probe-v1.json).

Actual run: macOS 27/CPython 3.9.6, six initial focused tests RC0 after missing-module
red RC1. Scoped review found compiler timeout misclassification and host-resource
CI assumptions; explicit timeout red RC1 then eight corrected tests RC0. Native baseline mmap succeeds; AS/DATA setup returns EINVAL22 and
mappings remain allowed. RSS aliases AS in the current SDK. This is not a hard
production ceiling or proof of peak RAM. No full local suite, arbitrary parsing,
provider call, source acceptance, Web upload, tag or Release was run.

Next: a supported, independently tested production memory/network boundary
before extraction; do not relax the target or treat polling as a hard ceiling.
