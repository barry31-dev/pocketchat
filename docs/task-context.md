# Bounded task context tree

`pocketchat task context scan` is a read-only, metadata-only repository tree
inventory. It is the first context-stage building block: later stages may
select or snapshot files from this result, but this command does neither.

```bash
pocketchat task context scan . --include src --include tests --json
pocketchat task context scan . --max-files 256 --max-depth 8
```

The workspace is reported as `.` and every file path is POSIX-style and
workspace-relative. Includes are explicit files or directories; `.` means the
workspace root. Duplicate and overlapping includes produce one file row.
Regular files carry only `path` and `bytes`: file contents, timestamps and
content hashes are not read.

## Boundary and budgets

The workspace argument must be `.` or a strict current-directory-relative path.
Absolute paths, backslashes, empty segments, `.` segments, `..`, unavailable
paths and symlink components are refused. Includes apply the same segment rules,
and an explicitly included path may not cross a symlink component.
Symlinks discovered during traversal are listed as `omitted: symlink` and are
never followed. Non-regular filesystem entries are also omitted.

These exact directory names are pruned and reported as
`excluded_directory`: `.git`, `.hg`, `.svn`, `.pocketchat`, `.venv`, `venv`,
`__pycache__`, `node_modules`, `dist`, and `build`.

The defaults are 512 files, depth 12 and 65,536 visited path characters. A
caller may lower or raise them within the implementation ceiling. Exceeding any
limit refuses the whole request instead of returning a partial tree. Files and
omissions are sorted by relative path, so identical filesystem metadata and
arguments produce identical row order.

## Output and limits

JSON uses `pocketchat-task-context-tree.v1` and includes the normalized
includes, limits, usage counts, excluded names, file rows and omission rows.
Human output is rendered from the same result. Refusal exits 2. Success and
refusal create no task, session, trace, request or provider call.

This is not a content snapshot, ignore-file parser, relevant-file selector,
TaskSpec permission expansion, operating-system sandbox, or guarantee against
concurrent filesystem changes. File names themselves may be sensitive; choose
include roots accordingly and keep private inventories private.
