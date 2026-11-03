# Session read errors

PocketChat treats a session file as evidence that must either parse completely
or fail as a typed `session_file` error. Malformed UTF-8 is part of that
contract, not an unhandled decoder exception.

```bash
pocketchat show notes --json
# exit 2, one JSON error object on stdout, empty stderr
```

The error object uses the existing schema:

```json
{
  "error": "session_file",
  "message": "cannot read sessions/notes.jsonl: ...",
  "ok": false,
  "schema_version": "1"
}
```

The reader does not rewrite, truncate or repair the input. Invalid JSON is
still classified as "not a pocketchat session" for an arbitrary JSONL file;
malformed UTF-8 preserves the concrete read failure. Both paths are refusals,
never tracebacks.
