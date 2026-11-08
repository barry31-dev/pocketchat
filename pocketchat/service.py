"""The application seam between CLI argument handling and file rendering.

Every read-only command does two jobs -- read the workspace into a record,
then print it. The printing belongs to the CLI; the reading belongs here,
once, in functions that take what the operator named and return data.
Nothing here opens a socket, reads the environment or touches a credential.

Extraction is by record, not by copy. `workspace`, `history`, `search` and
`diff` already return records from their own modules, so this facade
re-exports them rather than restating them; `show` and `export` were
read-and-print interleaved in the CLI, and their orchestration moves here
whole -- same refusals, same messages, same files written. The one behaviour
that improved in the move: `show` on a session file this reader cannot
understand (an unknown schema version) now answers with the refusal the
session reader raises, where the CLI used to die on the uncaught exception
after printing half the conversation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .queries import (QueryError, collect_history, diff_sessions,  # noqa: F401
                      render_diff_lines, render_history_lines,
                      render_search_lines, search_messages)
from .context import ContextBudgetExceeded, context_chars, preview_budget
from .context_projection import ContextProjectionError, project_context
from .memory_migration import MemoryMigrationError, read_v2_store
from .memory_retrieval import (MemoryRetrievalError, recall_context_message,
                               recall_evidence_items, retrieve_memories)
from .provider import estimate_tokens
from .sessions import (SessionFileError, looks_like_session,
                       looks_like_trace, read_context_messages,
                       read_export_messages, read_header, read_turns,
                       resolve_named_path, session_for_trace)
from .workspace import (WorkspaceError, as_dict, collect_workspace,  # noqa: F401
                        render_lines)

EVIDENCE_INPUT_BYTES = 1024 * 1024
_EVIDENCE_SCOPE = {"scope": "supplied_observations", "files_rechecked": False}
_ARTIFACT_KEYS = frozenset({
    "id", "kind", "path", "whole_file_sha256", "observed_whole_file_sha256",
    "semantic_sha256", "semantic_scope", "identity", "read_coverage",
    "required", "present",
})
_RELATION_KEYS = frozenset({"id", "source", "target", "kind", "fields", "required"})


def _evidence_refusal(reason: str, digest=None) -> Dict[str, Any]:
    return {"ok": False, "schema_version": "1", "status": "refused",
            "error": "evidence", "reason": reason,
            "message": "Evidence observations were refused.",
            "source_sha256": digest, **_EVIDENCE_SCOPE}


def _evidence_metadata(payload: Dict[str, Any]) -> None:
    """Bound metadata before the pure core, without copying diagnostic values."""
    from .acceptance import AcceptanceError, _IDENTIFIER, _path
    from .evidence_links import IDENTITY_FIELDS, DIGEST_FIELDS

    def identifier(value):
        return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None

    for item in payload["artifacts"]:
        required = _ARTIFACT_KEYS - {"required", "present"}
        if not isinstance(item, dict) or set(item) - _ARTIFACT_KEYS or required - set(item):
            raise AcceptanceError("invalid metadata")
        if not identifier(item["id"]):
            raise AcceptanceError("invalid metadata")
        _path(item["path"])
        for name in (*DIGEST_FIELDS, "kind", "semantic_scope", "read_coverage"):
            if not isinstance(item[name], str) or len(item[name]) > 64:
                raise AcceptanceError("invalid metadata")
        for name in ("required", "present"):
            if name in item and not isinstance(item[name], bool):
                raise AcceptanceError("invalid metadata")
        identity = item["identity"]
        if not isinstance(identity, dict) or set(identity) - IDENTITY_FIELDS:
            raise AcceptanceError("invalid metadata")
        for name, value in identity.items():
            if name == "turn":
                valid = type(value) is int and 1 <= value <= 2 ** 31 - 1
            elif name == "run_ids":
                valid = (isinstance(value, list) and len(value) <= 256
                         and all(identifier(entry) for entry in value)
                         and len(set(value)) == len(value))
            elif name in ("spec_sha256", "plan_sha256"):
                valid = (isinstance(value, str) and len(value) == 64
                         and all(char in "0123456789abcdef" for char in value))
            else:
                valid = identifier(value)
            if not valid:
                raise AcceptanceError("invalid metadata")
    fields = IDENTITY_FIELDS | frozenset((*DIGEST_FIELDS, "semantic_scope"))
    for item in payload["relations"]:
        if (not isinstance(item, dict) or set(item) - _RELATION_KEYS
                or (_RELATION_KEYS - {"required"}) - set(item)):
            raise AcceptanceError("invalid metadata")
        if any(not identifier(item[name]) for name in ("id", "source", "target")):
            raise AcceptanceError("invalid metadata")
        if not isinstance(item["kind"], str) or len(item["kind"]) > 64:
            raise AcceptanceError("invalid metadata")
        if "required" in item and not isinstance(item["required"], bool):
            raise AcceptanceError("invalid metadata")
        names = item["fields"]
        if (not isinstance(names, list) or not names or len(names) > len(fields)
                or any(not isinstance(name, str) or name not in fields for name in names)):
            raise AcceptanceError("invalid metadata")


def _evidence_projection(report: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """The core owns reason codes; discard all caller text and diagnostic detail."""
    from .evidence_links import ARTIFACT_KINDS

    locations = {}
    for name in ("artifacts", "relations"):
        for index, item in enumerate(payload[name]):
            locations.setdefault(item["id"], []).append((name, index))

    def location(identifier):
        matches = locations.get(identifier, [])
        if len(matches) == 1:
            return {"target": matches[0][0], "index": matches[0][1]}
        return {"target": "input", "index": None}

    def rows(name, source):
        indexes = {}
        for index, item in enumerate(payload[source]):
            indexes.setdefault(item["id"], []).append(index)
        result = []
        for item in report[name]:
            available = indexes.get(item["id"], [])
            index = available.pop(0) if available else None
            row = {"index": index, "status": item["status"], "reason": item.get("reason")}
            if name == "artifacts":
                row["kind"] = item["kind"] if item["kind"] in ARTIFACT_KINDS else "invalid"
            result.append(row)
        return sorted(result, key=lambda row: (-1 if row["index"] is None else row["index"]))

    def findings(name):
        global_reasons = {"artifact_limit", "relation_limit", "report_limit", "invalid_input"}
        return [{**({"target": "input", "index": None}
                    if item["reason"] in global_reasons else location(item["id"])),
                 "reason": item["reason"]} for item in report[name]]

    return {"schema": report["schema"], "schema_version": report["schema_version"],
            "status": report["status"], "artifacts": rows("artifacts", "artifacts"),
            "edges": rows("edges", "relations"), "unknowns": findings("unknowns"),
            "omissions": findings("omissions"), "limits": dict(report["limits"])}


def verify_evidence_observations(target: str, workspace=None) -> Dict[str, Any]:
    """Read exactly one explicit observation document, not its referenced artifacts."""
    from .acceptance import AcceptanceError, _ReadIssue, _canonical, _decode, _read_file
    from .evidence_links import MAX_ARTIFACTS, MAX_RELATIONS, MAX_REPORT_BYTES, verify_evidence_links

    digest = None
    if any(not hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        return _evidence_refusal("unsupported_platform")
    try:
        data = _read_file(Path(workspace) if workspace is not None else Path.cwd(),
                          target, EVIDENCE_INPUT_BYTES)
    except _ReadIssue as exc:
        return _evidence_refusal(exc.reason)
    except (AcceptanceError, OSError, ValueError):
        return _evidence_refusal("unsafe_input_path")
    digest = hashlib.sha256(data).hexdigest()
    try:
        payload = _decode(data)
        # The shared decoder checks finite JSON; canonical encoding additionally
        # rejects escaped unpaired surrogates before the core serializes them.
        _canonical(payload)
        if (not isinstance(payload, dict)
                or set(payload) != {"schema_version", "artifacts", "relations"}
                or type(payload["schema_version"]) is not int or payload["schema_version"] != 1
                or not isinstance(payload["artifacts"], list)
                or not isinstance(payload["relations"], list)):
            return _evidence_refusal("invalid_input", digest)
    except AcceptanceError:
        return _evidence_refusal("invalid_json", digest)
    if not payload["artifacts"]:
        return _evidence_refusal("empty_artifacts", digest)
    if len(payload["artifacts"]) > MAX_ARTIFACTS:
        return _evidence_refusal("artifact_limit", digest)
    if len(payload["relations"]) > MAX_RELATIONS:
        return _evidence_refusal("relation_limit", digest)
    try:
        _evidence_metadata(payload)
    except AcceptanceError:
        return _evidence_refusal("invalid_metadata", digest)
    report = verify_evidence_links(payload["artifacts"], payload["relations"])
    safe = _evidence_projection(report, payload)
    if not payload["relations"] and safe["status"] == "complete":
        safe["status"] = "partial"
        safe["omissions"].append({"target": "relations", "index": None,
                                 "reason": "no_relations"})
    result = {"ok": safe["status"] == "complete", "schema_version": "1",
              "status": safe["status"], "source_sha256": digest,
              **_EVIDENCE_SCOPE, "verification": safe}
    if len(json.dumps(result, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")).encode("utf-8")) + 1 > MAX_REPORT_BYTES:
        return _evidence_refusal("report_limit", digest)
    return result


def _relative(path: Path, workspace: Path) -> str:
    """Return a stable workspace-relative path, or the honest absolute path."""
    path = Path(path).resolve()
    workspace = Path(workspace).resolve()
    try:
        return Path(os.path.relpath(str(path), str(workspace))).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "QueryError", "WorkspaceError",
    "collect_history", "collect_workspace", "diff_sessions",
    "as_dict", "render_lines", "render_history_lines", "render_search_lines",
    "render_diff_lines", "search_messages",
    "session_overview", "export_session",
    "show_record", "export_record", "context_preview",
]


def _resolve_pair(target: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """Map the named file to its (session, trace) pair.

    Either side is accepted as the argument. A file that is neither -- a JSONL
    file someone else's tool wrote, a log, an empty file -- is refused rather
    than treated as a trace with no events, because "this file is not a
    session" and "this session recorded no events" need different fixes.
    """
    if looks_like_session(target):
        recorded = read_header(target).get("trace")
        trace = None
        if isinstance(recorded, str) and recorded.strip():
            trace = (target.parent / recorded).resolve()
        return target, trace
    if looks_like_trace(target):
        return session_for_trace(target), target
    raise SessionFileError("%s is not a pocketchat session or trace file" % target)


def session_overview(target, sessions_dir: str = "sessions",
                     workspace=None) -> Dict[str, Any]:
    """Everything ``show`` prints, read from the files and printed by nobody.

    The session half and the trace half are read independently, because the
    command's own order says so: the conversation prints first, and a trace
    that is absent is a problem *recorded in the answer* rather than an
    exception -- the operator sees the conversation and then the reason no
    trace follows. A file that cannot be read as a session at all is still an
    exception, before anything is answered.

    ``workspace`` anchors the bare-name lookup and existence checks for a
    caller working against another directory than its own; the CLI leaves it
    unset and keeps its current-directory semantics.
    """
    target_str = str(target)
    resolved = resolve_named_path(Path(target), sessions_dir, workspace)
    if resolved is None:
        raise SessionFileError("no such session: %s" % target_str)
    session, trace = _resolve_pair(resolved)

    record: Dict[str, Any] = {"session": None, "trace": None,
                              "trace_problem": None}
    if session is not None:
        record["session"] = {
            "path": str(session),
            "header": read_header(session),
            "turns": read_turns(session),
        }
    if trace is None:
        record["trace_problem"] = {
            "message": "%s has no trace to show" % target_str}
    elif not trace.is_file():
        record["trace_problem"] = {
            "message": "trace not found: %s (recorded by %s)"
                       % (trace, session or target_str)}
    else:
        lines: List[Dict[str, Any]] = []
        for number, line in enumerate(
                trace.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                lines.append({"number": number, "error": str(exc)})
                continue
            entry = {"number": number,
                     "event_id": event.get("event_id", "-"),
                     "type": event.get("type", "-")}
            # Compaction is the one event whose counts a human reader needs
            # in `show`: every other event's body stays in the trace file.
            if event.get("type") == "context.compacted":
                entry["payload"] = event.get("payload", {})
            lines.append(entry)
        record["trace"] = {"path": str(trace), "lines": lines}
    return record


def _trace_config_summary(trace: Optional[Path]) -> Dict[str, Any]:
    if trace is None or not trace.is_file():
        return {}
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        payload = event.get("payload") if isinstance(event, dict) else None
        config = payload.get("config_summary") if isinstance(payload, dict) else None
        if isinstance(config, dict):
            return config
    return {}


def _memory_recall_preview(config: Dict[str, Any], root: Path,
                           prompt: Optional[str]):
    if (config.get("context_projection_enabled") is not True
            or config.get("memory_recall_enabled") is not True
            or prompt is None):
        return [], [], None
    store = config.get("memory_recall_store")
    workspace_id = config.get("memory_recall_workspace_id")
    if not isinstance(store, str) or not store.strip() \
            or not isinstance(workspace_id, str) or not workspace_id.strip():
        raise SessionFileError(
            "memory recall requires an explicit store and workspace id")
    try:
        result = retrieve_memories(
            read_v2_store(root, Path(store)), prompt,
            workspace_id=workspace_id,
            now=datetime.datetime.now(datetime.timezone.utc),
            max_results=int(config.get("memory_recall_max_results", 20)),
            max_excerpt_bytes=int(config.get(
                "memory_recall_max_excerpt_bytes", 4096)),
            max_recall_tokens=int(config.get(
                "memory_recall_max_recall_tokens", 1024)))
    except (MemoryMigrationError, MemoryRetrievalError, OSError, ValueError) as exc:
        raise SessionFileError("memory recall failed: %s" % exc) from exc
    evidence = recall_evidence_items(result)
    summary = {
        "schema_version": result["schema_version"],
        "query_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "workspace_id": workspace_id,
        "matched_count": result["matched_count"],
        "selected_count": result["selected_count"],
        "excluded_count": len(result["excluded"]),
        "evidence_count": len(evidence),
        "status": result["status"],
    }
    messages = [recall_context_message(result)] if evidence else []
    return messages, evidence, summary


def context_preview(target, sessions_dir: str = "sessions",
                    workspace=None, budget_chars: Optional[int] = None,
                    keep_recent: int = 2,
                    pending_prompt: Optional[str] = None) -> Dict[str, Any]:
    """Read one session and describe its context selection without writing."""
    resolved = resolve_named_path(Path(target), sessions_dir, workspace)
    if resolved is None:
        raise SessionFileError("no such session: %s" % target)
    session, trace = _resolve_pair(resolved)
    messages = read_context_messages(session)
    pending = []
    if pending_prompt is not None:
        if not isinstance(pending_prompt, str) or not pending_prompt:
            raise SessionFileError("pending prompt must be non-empty")
        pending = [{"role": "user", "content": pending_prompt}]
    config = _trace_config_summary(trace)
    root = Path(workspace) if workspace is not None else Path.cwd()
    if budget_chars is None:
        value = config.get("max_context_chars")
        budget_chars = (value if isinstance(value, int)
                        and not isinstance(value, bool) and value > 0
                        else 24000)
    projection = None
    if config.get("context_projection_enabled") is True:
        recall_messages, recall_evidence, recall_summary = \
            _memory_recall_preview(config, root, pending_prompt)
        selected = [message for message in messages
                    if message.get("role") == "system"] + recall_messages
        turns = [message for message in messages
                 if message.get("role") != "system"]
        try:
            projected = project_context(
                {"instructions": selected, "messages": turns,
                 "evidence": recall_evidence}, max_chars=budget_chars,
                keep_recent_turns=keep_recent,
                pending_messages=pending)
        except ContextProjectionError as exc:
            raise ContextBudgetExceeded(str(exc)) from exc
        selected_messages = projected["messages"]
        projection = projected["manifest"]
        if recall_summary is not None:
            projection["memory_recall"] = recall_summary
        preview = {
            "total_chars": context_chars(messages),
            "budget_chars": int(budget_chars),
            "estimated_tokens_before": estimate_tokens(messages),
            "estimated_tokens_after": estimate_tokens(
                selected_messages[:len(selected_messages) - len(pending)]),
            "compacted": (len(selected_messages) - len(pending))
                         != len(messages),
            "kept_indices": [],
            "dropped_indices": [],
            "report": {"strategy": "layered_v1"},
        }
        # The actual path sends this exact projected list; preview_messages can
        # include the pending prompt for a one-to-one comparison with it.
        preview["kept_indices"] = list(
            range(len(selected_messages) - len(pending)))
        preview["dropped_indices"] = []
    else:
        try:
            preview = preview_budget(messages, budget_chars, keep_recent)
        except ContextBudgetExceeded:
            raise
        kept = set(preview["kept_indices"])
        selected_messages = [
            message for index, message in enumerate(messages)
            if index in kept]
    result = {
        "ok": True,
        "schema_version": "1",
        "session": _relative(session, root),
        "message_count": len(messages),
        "messages": [{
            "index": index,
            "role": message["role"],
            "chars": context_chars([message]),
        } for index, message in enumerate(selected_messages)],
        "preview_messages": selected_messages,
        "pending_messages": pending,
        **preview,
    }
    if projection is not None:
        result["context_projection"] = projection
    if config.get("memory_recall_enabled") is True and pending_prompt is not None:
        result["memory_recall"] = projection.get("memory_recall") \
            if projection is not None else None
    return result

def show_record(target, sessions_dir: str = "sessions",
                workspace=None) -> Dict[str, Any]:
    """The `show` record as a stable JSON document with relative artifact paths."""
    root = Path(workspace) if workspace is not None else Path.cwd()
    overview = session_overview(target, sessions_dir, workspace)
    session = overview["session"]
    trace = overview["trace"]
    problem = overview["trace_problem"]
    safe_session = None
    if session is not None:
        safe_session = {
            "path": _relative(Path(session["path"]), root),
            "header": session["header"],
            "turns": session["turns"],
        }
    safe_trace = None
    if trace is not None:
        safe_trace = {
            "path": _relative(Path(trace["path"]), root),
            "events": [{
                "line": entry["number"],
                "event_id": entry.get("event_id"),
                "type": entry.get("type"),
                **({"error": "invalid JSON"} if "error" in entry else {}),
                **({"payload": entry["payload"]}
                   if "payload" in entry else {}),
            } for entry in trace["lines"]],
        }
    safe_problem = None
    if problem is not None:
        if problem["message"].startswith("trace not found:"):
            safe_problem = {
                "code": "trace_not_found",
                "message": "trace not found",
            }
        else:
            safe_problem = {
                "code": "trace_missing",
                "message": "session has no trace",
            }
    return {
        "ok": True,
        "schema_version": "1",
        "session": safe_session,
        "trace": safe_trace,
        "trace_problem": safe_problem,
    }


def export_session(target, fmt: str, out: Optional[Path] = None,
                   sessions_dir: str = "sessions",
                   workspace=None) -> Dict[str, Any]:
    """Write one self-contained copy of a session, deterministic by construction.

    Exporting twice must produce two identical files. That is not a nicety: a
    diff between two exports is the only way to tell "this session changed"
    from "the exporter is not deterministic", so nothing time-dependent, path
    dependent or id-dependent may enter the output unless it is read back from
    the session's own files.
    """
    target_str = str(target)
    resolved = resolve_named_path(Path(target), sessions_dir, workspace)
    if resolved is None:
        raise SessionFileError("no such session: %s" % target_str)
    if fmt == "trace":
        return _export_trace(resolved, out)

    try:
        session, trace = _resolve_pair(resolved)
        if session is not None:
            header = read_header(session)
            messages = read_export_messages(session)
            trace_text = trace.read_text(encoding="utf-8") if (
                trace is not None and trace.is_file()) else ""
    except OSError as exc:
        raise SessionFileError(str(exc)) from exc

    if session is None:
        # A trace alone carries the events, not the conversation. Exporting it
        # would produce a file that reads as a session but is missing half of
        # what one is, so the session is required rather than optional.
        raise SessionFileError("%s has no session file to export" % target_str)

    if fmt == "md":
        body = _render_markdown(header, messages)
    else:
        body = _render_jsonl(header, messages, trace_text)

    if out is None:
        out = Path(str(session.with_suffix("")) + ".export." + fmt)
    if out.exists():
        raise SessionFileError("%s already exists (use --out)" % out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return {"format": fmt, "session": str(session), "out": str(out),
            "messages": len(messages),
            "trace_events": len(_trace_events(trace_text))}


def export_record(target, fmt: str, out: Optional[Path] = None,
                  sessions_dir: str = "sessions",
                  workspace=None) -> Dict[str, Any]:
    """The `export` result as a stable JSON document with relative paths."""
    root = Path(workspace) if workspace is not None else Path.cwd()
    result = export_session(target, fmt, out, sessions_dir, workspace)
    source = result.get("source") or result.get("session")
    if not isinstance(source, str) or not source:
        raise SessionFileError("export result names no source artifact")
    return {
        "ok": True,
        "schema_version": "1",
        "format": result["format"],
        "source": _relative(Path(source), root),
        "session": (_relative(Path(result["session"]), root)
                    if result.get("session") else None),
        "out": _relative(Path(result["out"]), root),
        "messages": result.get("messages"),
        "trace_events": result["trace_events"],
    }


def _export_trace(target: Path, out: Optional[Path]) -> Dict[str, Any]:
    """Copy existing events only; full schema validation stays with Trace Doctor."""
    try:
        source = target
        body, records = _read_export_jsonl(target)
        first = records[0][1]
        if "session" in first and "kind" not in first:
            recorded = first.get("trace")
            if not isinstance(recorded, str) or not recorded.strip():
                raise SessionFileError("%s has no trace to export" % target)
            source = (target.parent / recorded).resolve()
            body, records = _read_export_jsonl(source)

        required = {"schema_version", "event_id", "timestamp", "run_id", "type"}
        for number, event in records:
            if "kind" in event or any(
                    not isinstance(event.get(field), str) or not event[field]
                    for field in required):
                raise SessionFileError(
                    "%s line %d: expected a trace event, not a session archive"
                    % (source, number)
                )

        if out is None:
            out = Path(str(target.with_suffix("")) + ".export.trace.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects the input when --out names it.
        with out.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        raise SessionFileError("%s already exists (use --out)" % out) from None
    return {"format": "trace", "source": str(source), "out": str(out),
            "trace_events": len(records)}


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _read_export_jsonl(path: Path) -> Tuple[bytes, List[Tuple[int, Dict]]]:
    """Read every record before writing, retaining the original bytes and lines."""
    body = path.read_bytes()
    records = []
    for number, line in enumerate(body.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"),
                                parse_constant=_reject_json_constant)
        except ValueError as exc:
            raise SessionFileError(
                "%s line %d: invalid JSON or UTF-8" % (path, number)
            ) from exc
        if not isinstance(record, dict):
            raise SessionFileError(
                "%s line %d: expected a JSON object" % (path, number)
            )
        records.append((number, record))
    if not records:
        raise SessionFileError("%s has no records to export" % path)
    return body, records


def _trace_events(trace_text: str) -> List[Dict]:
    """The events in a trace text, under the archive's own rules.

    The mixed archive skips a blank line and a malformed one rather than
    failing the export, so the count of what it copied has to be taken the
    same way. Counting lines instead reports evidence the file does not
    contain.
    """
    events = []
    for line in trace_text.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _render_jsonl(header: Dict, messages: List[Dict[str, Any]],
                  trace_text: str) -> str:
    """A single JSONL document: a header, every message, every event.

    One file, one line per record, the session's own ordering. Nothing is
    generated here except the ``kind`` field that tells a reader which of the
    three a line is.
    """
    lines = [json.dumps({"kind": "session", **header},
                        sort_keys=True, ensure_ascii=False)]
    for message in messages:
        lines.append(json.dumps({"kind": "message", **message},
                                sort_keys=True, ensure_ascii=False))
    for event in _trace_events(trace_text):
        lines.append(json.dumps({"kind": "event", **event},
                                sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _render_markdown(header: Dict, messages: List[Dict[str, Any]]) -> str:
    """The same conversation as prose, for reading rather than for parsing."""
    out = ["# pocketchat session %s" % header.get("session"), ""]
    created = header.get("created")
    if created:
        out.append("started %s" % created)
        out.append("")
    for message in messages:
        if message["role"] == "user":
            label = "You"
            body = message["content"]
        elif message["role"] == "tool":
            label = "Tool result"
            body = message["content"]
        elif message.get("tool_calls"):
            label = "Tool request"
            body = json.dumps(message["tool_calls"], sort_keys=True,
                              ensure_ascii=False)
        else:
            label = "Model"
            body = message["content"]
        out.append("**%s.**" % label)
        out.append("")
        out.append(body)
        out.append("")
    return "\n".join(out)
