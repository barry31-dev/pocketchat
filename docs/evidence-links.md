# Evidence links

`pocketchat.evidence_links` is a pure verifier over observations a caller has
already collected. It does not open files, resolve paths, call a provider, or
repair an artifact. A `complete` result means the supplied descriptors and
relations are internally consistent; it does **not** mean this module re-read
the original files or proved that every described run happened.

## Interface

```python
from pocketchat.evidence_links import verify_evidence_links

report = verify_evidence_links(artifacts, relations)
```

Both arguments are lists of dictionaries. The verifier never mutates them. The
report has `schema_version=1` and one of these statuses:

| status | meaning |
|---|---|
| `complete` | Every required observation is present, fully covered, and every required relation is covered without contradiction. |
| `partial` | The supplied evidence is consistent, but an explicitly optional artifact/relation is missing or an observation is explicitly incomplete. |
| `refused` | An input is malformed or unsafe, identities or digests disagree, a required artifact is missing, or a limit is exceeded. |

Limits are 256 artifacts, 1024 relations and 64 KiB of canonical report JSON.
Crossing a limit returns `refused`; it never truncates and calls the remainder
complete.

## Artifact descriptor

```json
{
  "id": "request",
  "kind": "request",
  "path": "traces/requests/run_1.turn-1.json",
  "whole_file_sha256": "...",
  "observed_whole_file_sha256": "...",
  "semantic_sha256": "...",
  "semantic_scope": "request-body-canonical",
  "identity": {"run_id": "run_1", "turn": 1},
  "read_coverage": "complete",
  "required": true,
  "present": true
}
```

The closed `kind` set is `session`, `trace`, `request`, `comparison`,
`execution_report` and `checkpoint`. An artifact ID is unique. A path is a
normalized workspace-relative POSIX path: absolute paths, `..`, backslashes and
symlink-like escapes are refused. `identity` is also closed to the bounded
fields `run_id`, `run_ids`, `turn`, `conversation_id`, `task_id`,
`spec_sha256`, `plan_sha256` and `comparison_id`; any other key is refused
instead of copied into the report.

The declared and observed whole-file SHA-256 values must both be lowercase
64-character hex digests and must agree. `semantic_sha256` is a separate digest
whose closed `semantic_scope` is `canonical-json`, `request-body-canonical` or
`trace-events`. A semantic digest that equals the whole-file digest, or a
`whole-file` semantic scope, is refused rather than treated as a second name for
the same digest.

`read_coverage` is `complete` or `partial`. `required` defaults to true.
`present` defaults to true and may be false only when the caller is recording
an explicit absence. A missing required artifact is `refused`; a missing
optional artifact is `partial`.

## Relations

A relation names two existing artifact IDs, a closed relation kind, and the
identity fields it compared:

```json
{
  "id": "trace-request",
  "source": "trace",
  "target": "request",
  "kind": "same-run-turn",
  "fields": ["run_id", "turn"],
  "required": true
}
```

The relation kinds are `same-run`, `same-run-turn`, `contains-run`,
`binds-spec`, `binds-plan` and `binds-checkpoint`. There is no link conclusion
without an explicit relation. `same-run-turn` checks the run identity plus the
request turn; `contains-run` accepts a comparison's `run_ids` list. Other
relations compare only the named fields. An orphan endpoint, duplicate relation
ID, self-relation or unobserved compared field is refused.

The report contains bounded artifact and edge results, plus named `unknowns`
and `omissions`. It never copies request, message or credential bodies. Sorting
is stable and independent of caller order.

## Non-goals

This slice does not add a CLI, read artifact bytes, verify that a digest was
computed from the current filesystem, repair a mismatch, call a provider, or
claim publication. A caller that needs those actions must use a separate
bounded reader/CLI slice and keep this report's evidence scope explicit.

## Explicit observation CLI

```bash
pocketchat evidence verify observations.json --workspace ./checkout --json
pocketchat evidence verify observations.json --workspace ./checkout
```

`observations.json` is a normalized **workspace-relative** path. `--workspace`
selects its read-only root and defaults to the current directory. The command
reads exactly this one document; it does not open the artifact paths it names,
run a task, replay a request, repair evidence, load a profile or contact a model.
The input is a closed object containing exactly:

```json
{
  "schema_version": 1,
  "artifacts": [
    {
      "id": "session-a", "kind": "session", "path": "sessions/a.jsonl",
      "whole_file_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "observed_whole_file_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "semantic_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "semantic_scope": "canonical-json", "read_coverage": "complete",
      "identity": {"run_id": "run-a"}
    },
    {
      "id": "trace-a", "kind": "trace", "path": "traces/a.jsonl",
      "whole_file_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "observed_whole_file_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "semantic_sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "semantic_scope": "trace-events", "read_coverage": "complete",
      "identity": {"run_id": "run-a"}
    }
  ],
  "relations": [{"id": "link-a", "source": "session-a", "target": "trace-a",
                 "kind": "same-run", "fields": ["run_id"]}]
}
```

The hashes above are illustrative declarations, not measured files. The CLI
reuses `verify_evidence_links()` on supplied observations. A `complete` result
means this supplied evidence is internally consistent, **not** that the original
files were rechecked or still exist. Every result therefore carries
`scope: "supplied_observations"` and `files_rechecked: false`. `source_sha256`
binds the bytes of the input document actually read, not the referenced files.

Identifiers must match `[A-Za-z][A-Za-z0-9_-]{0,63}`; paths use the shipped safe
workspace-relative path rules. `identity` uses the core's closed names;
`run_ids` is a unique list of at most 256 identifiers, and `turn` is a positive
32-bit integer. Unknown artifact/relation keys or unsafe metadata are refused.
`required` and `present` remain optional booleans. Input is limited to 1 MiB
(inclusive), strictly decoded as finite UTF-8 JSON, with duplicate keys and
more than 64 levels of nesting refused. Symlinks, nonregular files, protected
paths, changed reads and missing no-follow primitives are refused, without a
path-following fallback. The explicit root itself must not be a symlink; the
existing reader does not claim to detect every ancestor replacement outside
that root. A descriptor read is an observation, not a filesystem sandbox.

The JSON output is one document, including its trailing newline within a
64 KiB budget. `ok` is true only for `complete`. Artifact and relation findings
refer to **zero-based input indexes**, with `null` for ambiguous/global findings;
they retain only fixed kind/status/reason values. Artifact paths, identifiers,
identity values, diagnostic detail, message bodies and credentials are never
copied to the output. Identical input bytes yield identical JSON bytes. Reports
that exceed the budget are refused, not silently truncated.

| Status | Exit | Meaning |
|---|---:|---|
| `complete` | 0 | Supplied required links are consistent and completely observed. |
| `partial` | 1 | Coverage/optional evidence is missing, or no relations were supplied. |
| `refused` | 2 | Contradictory evidence, empty artifact set, bad input, unsafe read or exceeded budget. |

Human and JSON modes share the same result and exit. Handled input errors use
`error: "evidence"` and a fixed reason; JSON mode does not emit a traceback or
stderr body. This command creates no session, trace, request, verification
receipt or output file. Live artifact acquisition and freshness verification
require a separate approved slice.
