# Artifact secret audit

`pocketchat.artifact_audit` is a pure offline check over bytes that a caller
already authorized for inspection. It compares exact secret byte values with
those bytes and returns a bounded report. It never discovers files, opens
paths, follows symlinks, reads the environment, calls a provider or writes
artifacts.

## API

```python
audit_artifacts(
    artifacts,
    secret_values,
    *,
    max_artifacts=256,
    max_artifact_bytes=8 * 1024 * 1024,
    max_total_bytes=32 * 1024 * 1024,
    max_matches=256,
    max_report_bytes=64 * 1024,
)
```

`artifacts` is a list of closed records with exactly these fields:

| field | contract |
|---|---|
| `artifact_id` | stable, non-empty identifier from a conservative identifier alphabet |
| `kind` | `request`, `trace`, `session`, `comparison`, `execution_report` or `checkpoint` |
| `path` | normalized workspace-relative path; it is checked but never returned |
| `entry_type` | `regular_file` |
| `content` | bytes supplied by the caller |

`secret_values` contains exact non-empty bytes or UTF-8 strings. Values are
compared as bytes, so CJK, emoji and values spanning an internal chunk boundary
are handled consistently. An artifact ID containing a supplied secret, a
non-string kind, a parent segment or a Windows drive path is refused without
echo. `max_report_bytes` must be at least 512 bytes so the closed refusal
envelope can fit; lower values raise `ArtifactAuditError`. Other invalid input
or an exceeded inclusive limit returns a `refused` report, never a truncated
`clean` report.

## Report

The report has schema `pocketchat-secret-audit.v1` and schema version `1`:

```json
{
  "schema": "pocketchat-secret-audit.v1",
  "schema_version": 1,
  "status": "clean | found | refused",
  "artifact_count": 1,
  "secret_count": 1,
  "match_count": 1,
  "matches": [
    {
      "artifact_id": "artifact-1",
      "location_category": "session",
      "artifact_sha256": "..."
    }
  ],
  "omissions": [],
  "limits": {},
  "report_sha256": "..."
}
```

`matches` are sorted and deduplicated by artifact, location category and
whole-artifact SHA-256. The report never includes secret values, matching
fragments, byte offsets, line numbers, paths, request/message bodies or
credentials. `report_sha256` is the canonical JSON SHA-256 of the report body
without that field.

A `found` report means an exact supplied value occurs in supplied bytes. A
`clean` report means no supplied value was found within the supplied bytes. It
does not prove that the artifact is authentic, complete, free of other secrets,
safe on disk or unchanged after the check.

## Scope

This slice is only the pure core. It does not add a CLI, reader, redaction,
quarantine, repair, evidence-pack integration, provider call, publication or
release action. Any later reader must separately prove bounded/no-follow file
access and preserve the same no-echo report contract.

## Verification

The focused suite covers clean and matching bytes, ASCII and UTF-8 secrets,
deduplication, closed artifact kinds and paths, input limits, report limits,
stable ordering, input immutability and zero-I/O behavior. It also checks that
secret-valued IDs, unhashable kinds and unsafe parent/drive paths refuse without
echo or traceback, and that overflow returns a compact report within the
declared budget. The implementation branch records the exact commands and their
unverified scope in its review record.
