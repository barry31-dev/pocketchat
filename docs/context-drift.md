# Selected-context drift check

`check_context_drift()` compares the selected-file evidence in one
`pocketchat-task-context-snapshot.v1` record with caller-supplied current
observations. It is a pure core: it never scans the workspace, refreshes the
snapshot, reads selected content, calls a provider or runs a command.

```python
from pocketchat.context_drift import check_context_drift

report = check_context_drift(
    frozen_snapshot,
    {
        "coverage": "complete",
        "files": [
            {
                "path": "README.md",
                "entry_type": "regular_file",
                "bytes": 17,
                "sha256": "<64 lowercase hex characters>",
            }
        ],
    },
)
```

The observation list may omit unselected paths. Rows for paths outside the
frozen selection are ignored and counted as `ignored_observations`; they do
not turn an unchanged selection into a refusal.

## Status

| Status | Meaning |
|---|---|
| `allow` | Complete observation, every selected path still a regular file with the same byte count and SHA-256 |
| `refused` | A known change, a complete observation missing a selected path, malformed input, or an explicit resource limit |
| `unknown` | No known contradiction, but coverage is partial or a selected path was not observed |

A known change wins over partial coverage and is always `refused`. A partial
observation is never promoted to `allow`: if all selected rows happen to be
present, the report still records `coverage_partial` as an unknown. The
corresponding `next_action` is `continue`, `replan` or `reobserve`.

Change reasons are closed:

- `content_changed` — byte count or SHA-256 differs;
- `path_missing` — the selected path is reported missing;
- `entry_type_changed` — the path is a symlink, directory or another
  non-regular entry.

## Frozen snapshot validation

The supplied snapshot is validated before comparison. Its schema, tree schema,
bounded query terms, selected-file rows, UTF-8 text/byte agreement, SHA-256,
counters and canonical `snapshot_sha256` must all agree. A forged or
inconsistent snapshot raises `ContextDriftError`; it is never treated as an
empty or unchanged observation.

The check does not require the snapshot's selected files to be in a particular
list order, but duplicate selected paths are refused.

## Bounds and digest

The default observation limit is 256 rows and the report body limit is 64 KiB.
Exceeding either produces a `refused` report, never a silently truncated
`allow`. The report body is canonical UTF-8 JSON without `report_sha256`;
`report_sha256` binds the body including the effective limits.

## Boundary

This report proves only that the supplied observations match or differ from the
supplied snapshot. It does not prove the observations were collected without a
race, that an unselected path is unchanged, that a caller cannot fabricate an
observation, or that a later plan is safe. Wiring live collection, refreshing
the snapshot, automatic replanning and provider/command integration belongs to
separate approved slices.
