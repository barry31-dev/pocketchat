# Guarded overwrite v1

This unreleased slice lets a TaskSpec authorize replacement of an existing
UTF-8 workspace file. It is not a general patch or delete surface. The editor
binds every replacement to the exact bytes read in an earlier snapshot, then
delegates the actual atomic write to the existing `execute_write_file` tool.
The published v1.1.0 artifact does not contain this module.

## Authorization

A TaskSpec may add an optional `overwrite_paths` array. Every entry must also
appear in `inputs` and in `tools.write_file.allow_paths`; a path cannot become
overwritable merely by being write-allowlisted. The editor refuses paths outside
that exact list.

## Snapshot and apply

`snapshot_overwrite()` records the normalized path, SHA-256, byte count and
UTF-8 text of one existing regular file. It refuses symbolic links, sensitive
or binary-suffix paths, NUL bytes, invalid UTF-8, oversized files and targets
that are already dirty or untracked in Git.

`apply_overwrite()` rereads the file immediately before writing and refuses if
its digest differs from the snapshot. The actual replacement uses
`execute_write_file(..., overwrite=True)` with the TaskSpec's exact write
allowlist, preserving its atomic temporary-file replacement and trace events.
The result carries original/new hashes, byte counts and bounded added/removed
line counts. It never deletes a file or applies a free-form patch.

Dirty/untracked refusal prevents a task from claiming a pre-existing edit as
its own. This check covers one Git worktree target, not concurrent filesystem
writers outside Git; the hash recheck remains the final stale-write guard.
