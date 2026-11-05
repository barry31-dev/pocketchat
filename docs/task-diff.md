# Task diff evidence

`pocketchat.task_diff.build_task_diff(baseline, final)` is a pure comparison
over two already-produced `pocketchat-task-context-snapshot.v1` records. It
does not open files, inspect Git, refresh either snapshot, generate a patch or
claim that unselected paths were unchanged.

## Input contract

Both arguments must use the existing context-snapshot schema. The function
revalidates the closed record shape, tree and snapshot schemas, SHA-256 fields,
canonical `snapshot_sha256`, file rows, total bytes, size-change counters and
workspace-relative paths. It also enforces the producer's actual bounds: at
most 64 selected files, 32 bounded query terms, a 16 MiB snapshot budget, a
1 MiB current file body and NUL-free text. A malformed or impossible record is
refused with `TaskDiffError`; it is never treated as an empty snapshot.

`max_files` and `max_bytes` are explicit positive integers. Booleans, strings,
floats and non-positive values are refused with `error_type="invalid_limits"`.
Exact limits are inclusive.

## Result

The result uses `pocketchat-task-diff.v1` and contains:

- `status`: `complete` or `partial`;
- `whole_workspace_compared`: always `false`;
- deterministic `entries` sorted by path;
- explicit `omissions`, `omitted_files` and `omitted_paths`;
- `report_bytes` and `report_sha256` for the report envelope.

A path only in `final` is `added`; only in `baseline` is `deleted`. A path in
both is `modified` when its digest or byte count differs. Equal paths are
`unchanged` and omitted from `entries`. Text line counts are emitted only when
both snapshot rows contain text; source text is never copied into the report.

`text_compared` is true only for modified rows whose bounded line comparison
ran. To prevent quadratic work on repetitive input, line detail is omitted when
the two bodies exceed 256 KiB combined or 2,048 lines combined; then
`text_compared` is false and both line counts are null while digest/byte change
evidence remains available. Added and deleted rows also use `false` because
there is no two-sided text comparison. Rename detection is not attempted: the
snapshot schema has no rename marker, so a rename appears as a separate delete
and add.

`compared_files` is the number of changed paths represented by the entries
before limits, not the sum of baseline and final row counts.

## Coverage and budgets

A truncated selection, mismatched selection inputs, different frozen trees,
selection-size changes and empty snapshots produce `partial` with named
omissions. A report can be `complete` only when both supplied snapshots were
fully compared; that still does not mean the whole workspace was inspected.

`report_bytes` is the canonical UTF-8 size of the report content excluding
`report_bytes` and `report_sha256`. The complete serialized envelope must still
fit `max_bytes`. If the file limit omits changed paths, `omitted_paths` names
them while the byte budget allows. If byte pressure requires removing that
detail, the report remains `partial` and carries a `max_bytes` omission. A
budget too small for the minimal public envelope is refused with
`byte_limit_exceeded`; a report that can be returned never exceeds its byte
budget.

Errors have stable `error_type` and `category` fields. Structural failures use
`invalid_limits`, `invalid_snapshot`, `duplicate_path`, `unsafe_path` and
`snapshot_contradiction`; an impossible minimum envelope uses
`byte_limit_exceeded`. The builder never mutates either input.

## Non-goals

This is not a live diff, a snapshot producer, a Git observer, a patch engine,
a merge resolver, a semantic reviewer or a CLI. It does not establish that a
selected file is unchanged outside the two supplied records, and it does not
authorize task execution, acceptance, publication or release.
