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

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .queries import (QueryError, collect_history, diff_sessions,  # noqa: F401
                      render_diff_lines, render_history_lines,
                      render_search_lines, search_messages)
from .context import ContextBudgetExceeded, context_chars, preview_budget
from .sessions import (SessionFileError, looks_like_session,
                       looks_like_trace, read_context_messages,
                       read_export_messages, read_header, read_turns,
                       resolve_named_path, session_for_trace)
from .workspace import (WorkspaceError, as_dict, collect_workspace,  # noqa: F401
                        render_lines)

def _relative(path: Path, workspace: Path) -> str:
    """Return a workspace-relative path without leaking the absolute root."""
    try:
        return os.path.relpath(str(Path(path).resolve()),
                               str(Path(workspace).resolve()))
    except ValueError:
        return Path(path).name


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


def context_preview(target, sessions_dir: str = "sessions",
                    workspace=None, budget_chars: Optional[int] = None,
                    keep_recent: int = 2) -> Dict[str, Any]:
    """Read one session and describe its context trim without writing."""
    resolved = resolve_named_path(Path(target), sessions_dir, workspace)
    if resolved is None:
        raise SessionFileError("no such session: %s" % target)
    session, trace = _resolve_pair(resolved)
    messages = read_context_messages(session)
    if budget_chars is None:
        budget_chars = 24000
        if trace is not None and trace.is_file():
            for line in trace.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                config = payload.get("config_summary") if isinstance(payload, dict) else None
                value = config.get("max_context_chars") if isinstance(config, dict) else None
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    budget_chars = value
                    break
    try:
        preview = preview_budget(messages, budget_chars, keep_recent)
    except ContextBudgetExceeded:
        raise
    root = Path(workspace) if workspace is not None else Path.cwd()
    return {
        "ok": True,
        "schema_version": "1",
        "session": _relative(session, root),
        "message_count": len(messages),
        "messages": [{
            "index": index,
            "role": message["role"],
            "chars": context_chars([message]),
        } for index, message in enumerate(messages)],
        **preview,
    }


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
