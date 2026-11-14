# Project guidance across sessions

This is a local CLI workflow over the existing explicit memory v2 store,
Session and request snapshots. It does not add automatic memory acceptance,
semantic retrieval, an agent scheduler or a new execution surface.

## Save, inspect and accept guidance

Run commands from the project workspace. Create a candidate with its exact
workspace identity; acceptance is a separate, deliberate operation:

```sh
pocketchat memory propose "patches: keep public interfaces stable" \
  --workspace-id project-a --by operator \
  --store .pocketchat/project-memory.jsonl --json
pocketchat memory accept MEMORY_ID --by operator --verification not_required \
  --store .pocketchat/project-memory.jsonl --json
```

Replace `MEMORY_ID` with the returned `item.memory_id`. `not_required` is
appropriate for your own preference or working method, not a declaration that
an external factual claim has been independently verified. Sensitive,
unaccepted, retired, expired or other-workspace records are not recalled.

Enable recall explicitly in the current configuration. The system prompt is
at the configuration root, not inside a profile:

```json
{
  "schema_version": "2",
  "system_prompt": "Keep public interfaces stable.",
  "default_profile": "project",
  "profiles": {
    "project": {
      "base_url": "http://127.0.0.1:8123/v1",
      "model": "example-model",
      "api_key_env": "POCKETCHAT_API_KEY",
      "max_context_chars": 24000,
      "context_projection_enabled": true,
      "context_projection_max_bytes": 65536,
      "memory_recall_enabled": true,
      "memory_recall_store": ".pocketchat/project-memory.jsonl",
      "memory_recall_workspace_id": "project-a"
    }
  }
}
```

The endpoint above is an example, not a bundled service. Use only an endpoint
and credential you have independently authorized. The offline demonstration
below needs neither an external endpoint nor a real key.

## Reopen and preview the actual next-turn context

After a session exists, inspect the next question using the **current** config:

```sh
pocketchat context sessions/project.jsonl --prompt "patches: what should change?" \
  --config pocketchat.json --profile project --json
pocketchat run "patches: what should change?" --session sessions/project.jsonl \
  --config pocketchat.json --profile project --json
```

`context --prompt` requires non-empty text and an explicit `--config`. It does
not read the API key, call a provider, create snapshots or edit the session,
trace, config or memory store. The JSON includes `preview_messages`,
`pending_messages`, `preview_mode` and, when enabled, current recall metadata.
The current profile replaces old trace settings rather than inheriting an old
recall permission. Current system instructions are restored as on Session reopen.
Relative recall stores use the same workspace as Session (an explicit task root,
otherwise the recorded `traces` parent workspace, otherwise the caller workspace);
viewing a session from another directory does not silently select another store.

The default history window matches the next-turn runtime: one complete recent
turn in layered mode, two pinned messages in the legacy character-budget mode.
The current layered JSON-byte limit is enforced as well as the character limit.
Explicit `--budget` or `--keep-recent` overrides are **what-if previews**; they
only match execution if execution uses the corresponding selection settings.
`--keep-recent` counts turns in layered mode and messages in legacy mode.

`context SESSION --json` without `--prompt` retains its historical,
trace-config-based behavior. It is not a statement about the current config or
what a future prompt will retrieve. Preview and sending are separate operations:
a config, memory, clock/expiry or session change between them can change the
request. The actual request snapshot is the evidence for what was sent.

Fixed system instructions survive repeated layered compaction. Recalled
messages are temporary: each turn rereads/admit-checks the selected memory
store; they are not promoted into persistent system instructions.

## Retire without rewriting history

```sh
pocketchat memory retire MEMORY_ID --store .pocketchat/project-memory.jsonl --json
pocketchat context sessions/project.jsonl --prompt "patches: continue" \
  --config pocketchat.json --profile project --json
```

The retired record is excluded from **new recall**. Earlier requests, snapshots,
responses and conversation messages remain unchanged; they may still contain
or quote the old guidance. Retirement is not secure erasure or proof the model
cannot infer something from retained history. Lexical recall also does not
promise every relevant instruction will match every query or fit the budget.

## Reproducible offline demonstration

From the source checkout:

```sh
python3 examples/project-context-demo.py --output /tmp/pocketchat-project-context-demo
```

The output directory must be new or empty. The fixture runs real CLI processes
against its own loopback HTTP server, saves candidate/acceptance/retirement
records, reopens the session, compares preview messages with the actual HTTP
payload and verifies the request snapshots. It checks cross-workspace,
sensitive and unaccepted exclusions, and uses an explicit future dry-run clock
for expiry without changing the system clock. `steps.json`, `requests.json` and
`evidence.json` preserve the exact finite run, including its limitations.

This demonstrates local selection, process boundaries and artifact continuity;
it does not establish real-model instruction-following quality, all-platform
secure-file support, installed-package acceptance or publication readiness.
