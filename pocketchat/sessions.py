"""The session file: the conversation in full, beside the trace.

The trace records what happened, and by default only a summary of what was
said. That leaves the full text of a conversation nowhere on disk, which is
the one thing "resume this session" needs. So the conversation lives here, one
JSONL line per message, and the trace stays the evidence.

The format is flat and human-readable: a header line that names the session,
then message lines in order. No credentials, no configuration, nothing binary.
Appending is the only write, so a session interrupted mid-conversation keeps
every line already written instead of losing the file to a half-finished
rewrite.

Two rules this file owes to the reverse-engineered references:

- A resumed run adopts the run id and the trace path recorded here. Taking the
  command line's instead would put two run ids in one file, which the validator
  rejects as ``single_run_id``.
- The trace path is stored relative to this file, so a session copied to
  another machine still names its trace without carrying a home directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import utc_now

HEADER_KEY = "session"
MESSAGE_ROLES = ("user", "assistant")


class SessionFileError(Exception):
    """A file offered as a session that cannot be read as one."""


def open_appending(path: Path):
    """Open ``path`` for appending, creating its directory if it does not exist.

    The directory creation used to happen separately, so a failure there
    surfaced as a bare ``OSError`` from ``mkdir`` instead of a
    :class:`SessionFileError` the caller already knows how to report. Doing
    both steps under one exception type keeps "cannot write the session file"
    a single answer, whether the cause is a missing directory or a full disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def _read_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if path.is_dir():
        raise SessionFileError("%s is a directory, not a session file" % path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise SessionFileError(
                        "%s line %d: invalid JSON: %s" % (path, number, exc)
                    )
                if not isinstance(record, dict):
                    raise SessionFileError(
                        "%s line %d: expected a JSON object" % (path, number)
                    )
                records.append(record)
    except OSError as exc:
        raise SessionFileError("cannot read %s: %s" % (path, exc))
    return records


def looks_like_session(path: Path) -> bool:
    """True when the file's first record is a session header."""
    if not path.is_file():
        return False
    try:
        records = _read_records(path)
    except (SessionFileError, OSError):
        return False
    return bool(records) and HEADER_KEY in records[0]


def resolve_session_path(value: Path, sessions_dir: str = "sessions") -> Path:
    """Turn a ``--session`` value into a session file path.

    A bare name means ``./sessions/<name>.jsonl``, so ``--session notes`` and
    ``--session notes.jsonl`` address the same file. A value that already
    carries a directory is taken as given.
    """
    candidate = Path(value)
    if candidate.parent != Path("."):
        return candidate
    name = candidate.name
    if not name.endswith(".jsonl"):
        name += ".jsonl"
    return Path(sessions_dir) / name


def _relative_to(path: Path, start: Path) -> str:
    try:
        return os.path.relpath(str(path), str(start))
    except ValueError:
        # Different drives on Windows. The bare name is still the honest answer.
        return path.name


class SessionFile:
    """One run's conversation file: a header line, then one line per message."""

    def __init__(self, path: Path, run_id: str, trace_path: Path, created: str,
                 version: str, messages: List[Dict[str, str]],
                 resumed: bool) -> None:
        self.path = path
        self.run_id = run_id
        self.trace_path = trace_path
        self.created = created
        self.version = version
        self.messages = messages
        self.resumed = resumed

    @property
    def trace_display(self) -> str:
        return _relative_to(self.trace_path, self.path.parent)

    @classmethod
    def open(cls, path: Path, run_id: str, trace_path: Path,
             version: str) -> "SessionFile":
        """Adopt an existing session file, or start a new one.

        An existing file wins over the arguments: it is the record of what this
        session was, so a resumed run continues its run id and its trace rather
        than starting a second run beside them.
        """
        if not path.exists():
            store = cls(path, run_id, Path(trace_path), utc_now(), version, [],
                        False)
            store._write_header()
            return store

        records = _read_records(path)
        header = records[0] if records else {}
        if HEADER_KEY not in header:
            raise SessionFileError(
                "%s is not a pocketchat session file: no %r header" % (path, HEADER_KEY)
            )
        stored_id = header.get(HEADER_KEY)
        if not isinstance(stored_id, str) or not stored_id.strip():
            raise SessionFileError("%s: the header names no run id" % path)

        stored_trace = header.get("trace")
        if isinstance(stored_trace, str) and stored_trace.strip():
            trace = (path.parent / stored_trace).resolve()
        else:
            trace = Path(trace_path)

        created = header.get("created")
        messages = [
            {"role": record["role"], "content": record.get("content") or ""}
            for record in records[1:]
            if record.get("role") in MESSAGE_ROLES
        ]
        return cls(path, stored_id, trace,
                   created if isinstance(created, str) else utc_now(),
                   str(header.get("pocketchat_version") or version),
                   messages, True)

    def _write_header(self) -> None:
        record = {
            HEADER_KEY: self.run_id,
            "trace": self.trace_display,
            "created": self.created,
            "pocketchat_version": self.version,
        }
        try:
            with open_appending(self.path) as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionFileError("cannot write %s: %s" % (self.path, exc))

    def append(self, role: str, content: str) -> None:
        """Append one message. This is the only write that happens per turn."""
        if role not in MESSAGE_ROLES:
            raise SessionFileError(
                "a session file records %s messages, got %r"
                % ("/".join(MESSAGE_ROLES), role)
            )
        record = {"role": role, "content": content}
        try:
            with open_appending(self.path) as handle:
                handle.write(
                    json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise SessionFileError("cannot append to %s: %s" % (self.path, exc))
        self.messages.append(dict(record))


def read_header(path: Path) -> Dict[str, Any]:
    """The session header, for the read-only commands."""
    records = _read_records(path)
    if not records or HEADER_KEY not in records[0]:
        raise SessionFileError("%s is not a pocketchat session file" % path)
    return records[0]


def read_messages(path: Path) -> List[Dict[str, str]]:
    """The recorded messages, in order."""
    return [
        {"role": record["role"], "content": record.get("content") or ""}
        for record in _read_records(path)[1:]
        if record.get("role") in MESSAGE_ROLES
    ]


def sibling_trace(session_or_trace: Path) -> Optional[Path]:
    """The trace that sits beside a session, if the session was given as one.

    ``--session traces/notes.jsonl`` addresses the same run as the session file
    ``sessions/notes.jsonl`` next to it, so the read-only commands follow that
    link instead of refusing a path the operator can reasonably type.
    """
    candidate = Path(session_or_trace)
    if looks_like_session(candidate):
        return (candidate.parent / read_header(candidate).get("trace", "")).resolve()
    if looks_like_trace(candidate):
        sibling = candidate.parent.parent / "sessions" / candidate.name
        if sibling.is_file() and looks_like_session(sibling):
            return sibling
    return None


def looks_like_trace(path: Path) -> bool:
    """True when the file's first record is a trace event."""
    if not path.is_file():
        return False
    try:
        records = _read_records(path)
    except (SessionFileError, OSError):
        return False
    return bool(records) and {"event_id", "run_id", "type"} <= set(records[0])


def session_for_trace(trace_path: Path, sessions_dir: str = "sessions") -> Optional[Path]:
    """The session whose recorded trace is ``trace_path``, if there is one.

    The two files do not share a name: the session is named by whoever typed
    ``--session notes``, and the trace by the run id the session recorded. So
    the link lives in the header, and finding it means reading the headers in
    the directory and matching the trace each one claims. Guessing from file
    names would silently pair a trace with a different conversation whenever
    two sessions exist.
    """
    trace_path = Path(trace_path).resolve()
    directory = trace_path.parent.parent / sessions_dir
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob("*.jsonl")):
        try:
            header = read_header(candidate)
        except (SessionFileError, OSError):
            continue
        recorded = header.get("trace")
        if not isinstance(recorded, str) or not recorded.strip():
            continue
        if (candidate.parent / recorded).resolve() == trace_path:
            return candidate
    return None
