# Deterministic task context selection

`pocketchat task context select` ranks paths from a frozen
`pocketchat-task-context-tree.v1` manifest. It does not open any listed file and
does not call a model.

```bash
pocketchat task context scan . --include src --include tests --json > tree.json
pocketchat task context select tree.json --query task --query context --max-files 8 --json
```

## Frozen input

The selector accepts the closed tree schema or the scanner CLI JSON envelope with `ok: true`. It
checks duplicate JSON members, the schema, workspace marker, include coverage, scanner exclusion policy,
limits, usage counts, sorted unique file/omission rows, relative paths, byte
sizes and non-overlap. The manifest must be a regular non-symlink file no larger
than 8 MiB. Its canonical JSON SHA-256 is copied into every selection result.
Formatting and object-key order do not change this digest.

Omission rows are never candidates. A listed path outside the manifest's
includes is refused rather than silently considered.

## Scoring

Queries are case-folded into Unicode word tokens; underscore is a separator.
Tokens are deduplicated and sorted. Empty/NUL values, non-string values, entries over 128 characters and more
than 32 input entries or normalized tokens are refused.

For each file:

- an exact token in the basename scores 4;
- otherwise the same exact token elsewhere in the relative path scores 2;
- substrings, prefixes, file contents and omission rows score 0.

Scores are summed across query tokens. Positive matches sort by descending
score, then ascending path. `max_files` is 1–64. The result also records total
matches, whether the bounded output was truncated, every selected row's matched
terms and query terms that matched no candidate.

## Boundary

JSON and human output render the same result. A malformed manifest or request
exits 2 without creating task, session, trace or provider evidence. A valid
query with zero matches is a successful, explicit empty selection.

Path-token ranking is not semantic relevance and does not prove a file's
contents were read, safe or useful. This command does not parse ignore files,
expand TaskSpec permissions, snapshot contents, handle concurrent manifest
replacement across processes, provide an operating-system sandbox, or authorize
publication.
