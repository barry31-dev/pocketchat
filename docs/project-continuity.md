# Project continuity with explicit memory

PocketChat can carry one reviewed project decision into a later Session without
turning memory into an automatic authority. The workflow is deliberately split:

1. `memory propose` writes an **unverified candidate** to an explicit v2 store.
2. `memory accept` is a separate operator action. For an explicit user record,
   `--verification not_required` means that the preference or decision does not
   claim an independently verified fact.
3. A profile opts into both layered context projection and v2 recall for one
   exact workspace identity and store.
4. Each new Session records the selected memory ID, revision, content digest and
   source reference in its request snapshot and projection sidecar.
5. Unaccepted, retired, expired, sensitive and foreign-workspace records are not
   projected.

This is bounded project continuity, not automatic capture, model approval,
vector retrieval, cross-device sync or secure deletion.

## Create and review a candidate

Run from the project workspace and name the v2 store explicitly:

```bash
pocketchat memory propose \
  "Project Orion uses Python 3.13 and keeps public command names English." \
  --workspace-id project-orion \
  --by operator \
  --category project_decision \
  --tag project-continuity \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

The result has `admission_status: "candidate"`,
`verification_status: "unverified"`, an empty `accepted_by`, revision `1` and a
digest-bound `memory:propose:<memory-id>` source reference. The command does not
read or rewrite the legacy v1 store. It refuses malformed metadata, unsafe
paths, symlink or non-regular targets, corrupt/duplicate v2 records, oversized
stores and a competing first writer without replacing the existing bytes.

Review the candidate, then accept that exact ID separately:

```bash
pocketchat memory accept <memory-id> \
  --by operator \
  --verification not_required \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

Acceptance does not invent a source or independently verify content. Omit
`--verification not_required` when the candidate still requires another
verification state; an unverified candidate cannot be accepted.

## Opt a profile into recall

A profile must explicitly enable layered context projection and memory recall,
and must bind the same store and workspace identity. `profile set` replaces one
complete profile, so include its required provider fields and any other options
you need:

```bash
pocketchat profile set local \
  --base-url http://127.0.0.1:8123/v1 \
  --model example-model \
  --api-key-env POCKETCHAT_API_KEY \
  --context-projection \
  --memory-recall \
  --memory-recall-store .pocketchat/project-memory.v2.jsonl \
  --memory-recall-workspace-id project-orion
```

A later `run`, `chat` or reopened Session using that profile performs bounded,
read-only retrieval for the pending prompt. If a record is selected, the exact
request snapshot carries one `pocketchat-memory-recall.v1` system message with
its `memory_id`, `revision`, `workspace_id`, `content_sha256`, source references
and bounded excerpt. The trace records the recall summary; the projection
sidecar binds that summary to the request digest. No matching admitted record
means no recall message.

Use the read-only CLI preview when you want to inspect selection without a model
request:

```bash
pocketchat memory recall "Python naming" \
  --workspace-id project-orion \
  --store .pocketchat/project-memory.v2.jsonl \
  --dry-run --json
```

## Revoke future recall

Retirement is explicit and affects future retrieval:

```bash
pocketchat memory retire <memory-id> \
  --store .pocketchat/project-memory.v2.jsonl \
  --json
```

A retired record remains in the v2 store as lifecycle evidence but is no longer
projected into a new request. An expired record and a record bound to another
workspace identity are also excluded. These changes do not remove content from
historical traces, request snapshots, sessions, backups or requests that were
already sent. `memory forget` removes the active-store record but is not a
secure-erasure promise.

## Offline evidence fixture

Run the dependency-free fixture without a provider or network:

```bash
python3 examples/project-continuity-demo.py --output /tmp/pocketchat-project-continuity
```

The fixture uses the public CLI to propose and separately accept a record, then
opens distinct Sessions for the unaccepted, accepted, foreign-workspace,
expired and retired cases. It writes each trace and exact request snapshot plus
`evidence.json`, and checks that only the accepted same-workspace case carries
the source- and revision-bound recall message.

The fixture verifies local orchestration and evidence shape only. It does not
verify real-provider relevance or answer quality, arbitrary projects,
cross-platform manual operation, secure deletion, a tag, a Release or package
publication.
