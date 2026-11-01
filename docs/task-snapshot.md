# Explicit selected-file content snapshots

`pocketchat task context snapshot` consumes a frozen
`pocketchat-task-context-tree.v1` manifest and a previously rendered
`pocketchat-task-context-selection.v1` result. It is the content stage after
path selection, not a semantic relevance check.

```bash
pocketchat task context scan . --include src --include tests --json > tree.json
pocketchat task context select tree.json --query task --query context --json > selection.json
pocketchat task context snapshot tree.json selection.json --json
```

## Provenance

The command validates the tree through the selector, then rederives the
selection from its recorded query terms and `max_files`. The selection must
equal that fresh result field-for-field as parsed JSON; an edited path,
score, byte count, digest, count, truncation flag or extra field is refused.
The output binds the canonical tree and selection SHA-256 values.

Both JSON inputs must be regular non-symlink files no larger than 8 MiB.
Duplicate JSON members and false CLI envelopes are refused.

## Selected files only

The snapshot opens only paths in the validated selection's final `matches`.
It does not rescan the workspace or read an omitted, unmatched or truncated
candidate. Each selected path must remain workspace-relative, use regular
directories, and end at a regular non-symlink file. Sensitive path components,
binary suffixes, invalid UTF-8 and NUL bytes are refused.

The output records each file's `selection_bytes`, current `bytes`, SHA-256 and
bounded `text`. `selection_size_changed` and its count compare current size with
the frozen selection metadata; a same-size content edit still changes the
per-file hash and overall snapshot digest. A selection with zero matches is a
successful, explicit empty snapshot.

## Bounds and digest

Defaults are 1 MiB total and 1 MiB per selected file; callers may choose up to
16 MiB total. The total is inclusive, so a zero-byte selected file can follow a
snapshot that has used the full budget. Oversize, over-budget, malformed or
untrusted input exits 2 without creating task, session, trace, request,
`.pocketchat` or provider evidence.

`snapshot_sha256` is the canonical JSON digest of the result without that field.
Formatting and object-key order do not affect it. It proves only which bounded
bytes were read by this invocation. It is not semantic relevance, ignore-file
support, an atomic cross-process filesystem snapshot, a TaskSpec permission
expansion, task acceptance, checkpoint/resume, an operating-system sandbox or
publication authorization.
