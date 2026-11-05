# Memory Doctor Core Contract

> Scope: the pure `pocketchat-memory-doctor.v1` integrity report for one
> caller-supplied list of already parsed v2 memory records and explicit source
> observations. This is not a store reader, CLI, repair operation, migration or
> source-freshness claim.

## API

```python
audit_memory_records(
    records,
    *,
    workspace_id,
    source_observations,
    max_records=4096,
    max_sources=4096,
    max_findings=256,
    max_report_bytes=65536,
)
```

`records` must contain records already read by the caller. `source_observations`
must contain only caller-supplied observations. The function does not open
files, inspect the environment, resolve paths, take locks, make network calls,
run commands, read a clock or call a provider. It does not re-read a source to
prove that an observation is honest or race-free.

The limits are positive integers. Booleans are not integers. The exact record,
source, finding and canonical-report byte limits are accepted; one over is
refused with `MemoryDoctorError` carrying `error_type`. Input iterables are
consumed only through limit plus one and are never materialized first.

## Record checks

Every record is validated through the existing `parse_v2_record` contract. A
malformed record is an `invalid_record` finding and makes the report `refused`;
it is never normalized or repaired. Duplicate `memory_id` values and duplicate
`(memory_id, revision)` identities are refused.

Records belonging to another workspace are omitted with an explicit
`foreign_workspace` omission and count. They are not treated as corruption of
the requested workspace. A valid record with no source refs needs no source
observation.

The report contains admission counts (`accepted`, `candidate`, `retired`,
`conflicted`), a `sensitive` count, source counts, finding and omission counts.
It never contains memory bodies, memory IDs, source refs, source digests,
credentials or free-form source text.

## Source observations

Each observation has exactly these fields:

```json
{
  "source_kind": "file",
  "ref": "notes.md",
  "scope": "whole-file",
  "sha256": "<lowercase sha256>",
  "coverage": "complete"
}
```

`source_kind` is `file`, `event` or `url`. `coverage` is `complete`, `partial`
or `missing`. The identity is the tuple `(source_kind, ref, scope)`.

- A record source ref with an exact digest and `complete` coverage increments
  `sources_verified`.
- A missing observation, `missing` coverage or `partial` coverage increments
  `sources_unknown` and produces a typed finding. It is not called verified.
- A `complete` observation whose digest differs from the record's ref is a
  `source_digest_mismatch` contradiction and makes the report `refused`.
- Duplicate observations are refused. Repeating the same digest and coverage
  is `duplicate_source_observation`; a different digest is
  `source_observation_conflict`; a different coverage is
  `source_observation_coverage_conflict`.

Multiple records may reference the same source identity. The source observation
is shared, while each source ref still contributes to the source counts.

## Status and bounds

- `refused`: malformed input, duplicate identities, source-observation conflicts
  or a known digest mismatch.
- `partial`: valid workspace records with missing/partial source observations or
  foreign-workspace omissions.
- `complete`: all supplied records are valid, all source refs have complete exact
  observations, and there are no omissions.

The report is canonical JSON-compatible data sorted independently of input
order. Exact limits are inclusive. Reports over the byte bound, or findings over
their bound, are refused rather than truncated. Input mappings and lists are not
mutated.

## Evidence boundary

This core proves consistency of the supplied records and observations. It does
not prove that the file bytes, store tail, source history or observation were
collected correctly. A later read-only CLI integration must use the existing
bounded secure v2 store reader; it is outside this slice. No accept, retire,
forget, merge, repair or publication action is authorized.
