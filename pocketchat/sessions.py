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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import utc_now

HEADER_KEY = "session"
CONVERSATION_KEY = "conversation_id"
BRANCH_KEY = "branch_id"
TURN_KEY = "turn_id"
PARENT_BRANCH_KEY = "parent_branch_id"
PARENT_TURN_KEY = "parent_turn_id"
SCHEMA_VERSION_KEY = "schema_version"
SESSION_SCHEMA_VERSION = "1"
MESSAGE_ROLES = ("user", "assistant")


NOT_RECORDED = "not recorded"


def new_workspace_id(prefix: str) -> str:
    """Build a workspace id that is unique, readable and safe in a filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
    return "%s_%s_%s" % (prefix, stamp, os.urandom(4).hex())


def turn_id_for(number: int) -> str:
    """The turn id for a message line's position in its conversation.

    The id is derived from the line's ordinal, not from a counter a process
    happens to be holding. A message's identity therefore survives the
    process that wrote it, which is the whole point: two readers comparing
    \"turn three\" must be naming the same exchange, and only the file can
    say which one that was.
    """
    return "turn_%06d" % max(1, number)


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


def resolve_named_path(value: Path, sessions_dir: str = "sessions",
                       base: Optional[Path] = None) -> Optional[Path]:
    """Locate a file an operator named, without requiring any particular kind.

    A path that exists is taken as written -- which is how a trace reaches a
    read-only command -- and a bare name is looked up under ``sessions_dir``
    with ``.jsonl`` appended when missing. ``base`` anchors both checks for a
    caller that works against another workspace than its own current
    directory; the default is the process's own. ``None`` means nothing
    readable was named, which is the caller's cue to refuse.
    """
    root = Path(base) if base is not None else Path()
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    as_written = root / candidate
    if as_written.is_file():
        return as_written
    if candidate.parent == Path("."):
        bare = resolve_session_path(candidate, sessions_dir)
        located = bare if bare.is_absolute() else root / bare
        if located.is_file():
            return located
    return None


def resolve_session_argument(value: Path, sessions_dir: str = "sessions",
                             base: Optional[Path] = None) -> Path:
    """The session file an operator named, or the session a trace names.

    Either half of the (session, trace) pair is acceptable: the link lives in
    the session's own header, so the operator types whichever file is at
    hand. The two refusals stay distinct because they need different fixes --
    a path to nothing is a typo, a path to something that is neither a
    session nor a trace is the wrong file.
    """
    named = resolve_named_path(value, sessions_dir, base)
    if named is None:
        raise SessionFileError("no such session: %s" % value)
    if looks_like_session(named):
        return named
    sibling = sibling_trace(named)
    if sibling is not None:
        return sibling
    raise SessionFileError("%s is not a pocketchat session file" % value)


def _validate_header_schema(header: Dict[str, Any], path: Path) -> None:
    """Refuse a file whose format version this reader does not speak.

    The key names the file's format, not the program's version -- that is
    what ``pocketchat_version`` is for. A file without the key is a legacy
    file, and legacy files read unchanged; a file with an unknown key was
    written by something newer, and guessing at fields this reader has never
    seen would be worse than saying so.
    """
    recorded = header.get(SCHEMA_VERSION_KEY)
    if recorded is None:
        return
    if recorded != SESSION_SCHEMA_VERSION:
        raise SessionFileError(
            "%s has unsupported session schema version %r (this pocketchat "
            "reads %r); the file was written by a newer version"
            % (path, recorded, SESSION_SCHEMA_VERSION))


def _relative_to(path: Path, start: Path) -> str:
    try:
        return os.path.relpath(str(path), str(start))
    except ValueError:
        # Different drives on Windows. The bare name is still the honest answer.
        return path.name


def create_branch(path: Path, run_id: str, trace_path: Path, version: str,
                  conversation_id: str, branch_id: str,
                  parent_branch_id: Optional[str], parent_turn_id: str,
                  messages: List[Dict[str, str]]) -> "SessionFile":
    """Write a new session file that continues another branch.

    The parent is never opened for writing: the child is created whole, with
    its own run id and its own trace, so appending to the child cannot put a
    second run id into the parent's trace. The inherited messages are copied
    in, keeping the turn ids they already had, because a turn id names an
    exchange and not the file that happens to hold it -- a child that
    renumbered them would make "turn three" mean two different exchanges.
    """
    store = SessionFile(path, run_id, Path(trace_path), utc_now(), version, [],
                        False, conversation_id, branch_id, parent_branch_id,
                        parent_turn_id)
    store._write_header()
    # `append` numbers from the messages already held, so replaying the
    # inherited turns in order gives each one the turn id it had in the
    # parent: a turn id names an exchange, not the file holding it, and a
    # branch that renumbered them would make "turn three" mean two different
    # exchanges.
    for message in messages:
        store.append(message["role"], message["content"])
    return store


class SessionFile:
    """One run's conversation file: a header line, then one line per message."""

    def __init__(self, path: Path, run_id: str, trace_path: Path, created: str,
                 version: str, messages: List[Dict[str, str]],
                 resumed: bool, conversation_id: Optional[str] = None,
                 branch_id: Optional[str] = None,
                 parent_branch_id: Optional[str] = None,
                 parent_turn_id: Optional[str] = None) -> None:
        self.path = path
        self.run_id = run_id
        self.trace_path = trace_path
        self.created = created
        self.version = version
        self.messages = messages
        self.resumed = resumed
        self._conversation_id = conversation_id
        self._branch_id = branch_id
        self._parent_branch_id = parent_branch_id
        self._parent_turn_id = parent_turn_id

    @property
    def conversation_id(self) -> Optional[str]:
        """The conversation this file belongs to, if it records one.

        ``None`` is not a failed read: it is the honest answer for a file
        written before the id existed, which is a v0.1/v0.2 file rather than a
        broken one. The alternative -- minting an id for a file that never had
        one -- would invent a history the file cannot support.
        """
        return self._conversation_id

    @property
    def branch_id(self) -> Optional[str]:
        """The branch within the conversation, likewise absent on old files."""
        return self._branch_id

    @property
    def parent_branch_id(self) -> Optional[str]:
        """The branch this one was derived from, if it was derived at all.

        ``None`` means this file is a root branch: it started a conversation
        rather than continuing somebody else's. That is a fact about the file,
        not a gap in it, so it is never written as ``null``.
        """
        return self._parent_branch_id

    @property
    def parent_turn_id(self) -> Optional[str]:
        """The turn of the parent branch this one continues from."""
        return self._parent_turn_id

    @property
    def turns(self) -> List[Dict[str, str]]:
        """Every message with the turn id that names it in this conversation.

        The id comes from the message's ordinal in the file, so it is the same
        for every reader of the same bytes and never has to be looked up
        anywhere else.
        """
        return [
            dict(message, **{TURN_KEY: turn_id_for(number)})
            for number, message in enumerate(self.messages, 1)
        ]

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
            # A new file is a new conversation: the conversation id names the
            # dialogue, and the branch id names the path through it. They are
            # written before the first message so a later reader can tell that
            # two files with different run ids were the same conversation.
            conversation_id = new_workspace_id("conv")
            branch_id = new_workspace_id("branch")
            store = cls(path, run_id, Path(trace_path), utc_now(), version, [],
                        False, conversation_id, branch_id)
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

        _validate_header_schema(header, path)
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
        conversation_id = header.get(CONVERSATION_KEY)
        branch_id = header.get(BRANCH_KEY)
        parent_branch_id = header.get(PARENT_BRANCH_KEY)
        parent_turn_id = header.get(PARENT_TURN_KEY)
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            conversation_id = None
        if not isinstance(branch_id, str) or not branch_id.strip():
            branch_id = None
        if not isinstance(parent_branch_id, str) or not parent_branch_id.strip():
            parent_branch_id = None
        if not isinstance(parent_turn_id, str) or not parent_turn_id.strip():
            parent_turn_id = None
        return cls(path, stored_id, trace,
                   created if isinstance(created, str) else utc_now(),
                   str(header.get("pocketchat_version") or version),
                   messages, True, conversation_id, branch_id,
                   parent_branch_id, parent_turn_id)

    def _write_header(self) -> None:
        record = {
            HEADER_KEY: self.run_id,
            "trace": self.trace_display,
            "created": self.created,
            "pocketchat_version": self.version,
            SCHEMA_VERSION_KEY: SESSION_SCHEMA_VERSION,
            CONVERSATION_KEY: self._conversation_id,
            BRANCH_KEY: self._branch_id,
        }
        # The parent keys are written only when there is a parent. A root
        # branch's header keeps exactly the shape the identity layer defined,
        # so a file that never branched cannot be misread as a branch that
        # lost its parent.
        if self._parent_branch_id:
            record[PARENT_BRANCH_KEY] = self._parent_branch_id
        if self._parent_turn_id:
            record[PARENT_TURN_KEY] = self._parent_turn_id
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
        record = {"role": role, "content": content, TURN_KEY: turn_id_for(
            len(self.messages) + 1)}
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
    header = records[0]
    _validate_header_schema(header, path)
    return header


def read_messages(path: Path) -> List[Dict[str, str]]:
    """The recorded messages, in order."""
    return [
        {"role": record["role"], "content": record.get("content") or ""}
        for record in _read_records(path)[1:]
        if record.get("role") in MESSAGE_ROLES
    ]


def read_turns(path: Path) -> List[Dict[str, str]]:
    """The recorded messages, each with the turn id that names it.

    A v0.3 file carries the id on the line itself. A v0.1/v0.2 one does not,
    and deriving it from the message's position is what lets a reader of
    either file point at the same exchange -- the alternative is two spellings
    of "turn three", which is the ambiguity this layer exists to remove.
    """
    turns: List[Dict[str, str]] = []
    for number, record in enumerate(
            (r for r in _read_records(path)[1:]
             if r.get("role") in MESSAGE_ROLES), 1):
        recorded = record.get(TURN_KEY)
        turns.append({
            "role": record["role"],
            "content": record.get("content") or "",
            TURN_KEY: recorded if isinstance(recorded, str) and recorded.strip()
            else turn_id_for(number),
        })
    return turns


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
