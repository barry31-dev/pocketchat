"""Deriving a new branch from a turn of an existing conversation.

A branch is a second path through one conversation. The conversation id says
which dialogue the two files belong to; the parent keys say where the new path
left the old one. Without those two links, "compare two answers and keep one"
has nowhere to write down which answer was kept, and the next reader sees two
files that are halves of one conversation rather than a fork.

The parent file is never opened for writing. The child is created whole, with
its own run id and its own trace, and it carries copies of the turns it
inherits -- so appending to the child can never put a second run id into the
parent's trace. Reading a child
therefore never needs the parent, and rewriting a child never touches the
parent -- which is what keeps the append-only promise of both files intact.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .sessions import (TURN_KEY, SessionFile, create_branch,
                       new_workspace_id, resolve_session_path)
from .session import new_run_id


class BranchError(Exception):
    """A branch could not be derived; the operator's request is refused."""


def branch_path(name: str, sessions_dir: str) -> Path:
    """Where the child file goes when the operator named it.

    Same rule the other commands use for a session name -- a bare name is
    read from ``./sessions``, a value that already carries a directory is
    taken as written -- stated once in ``sessions.resolve_session_path``
    rather than restated here.
    """
    return resolve_session_path(Path(name), sessions_dir)


def default_branch_path(source: Path, sessions_dir: str) -> Path:
    """The child's path when the operator did not name one.

    The parent's own name keeps the file findable: somebody looking at one of
    the two later can find the other by name-inspection alone, which is the
    only recovery route that does not depend on reading the headers.
    """
    stem = source.stem + "-branch"
    return Path(sessions_dir) / (stem + ".jsonl")


def derive_branch(source: Path, turn_id: str, name: Optional[str] = None,
                  sessions_dir: str = "sessions") -> Dict[str, Any]:
    """Describe the branch that continues ``source`` from ``turn_id``.

    Returns a description the caller can act on: the conversation both files
    share, the branch id minted here, the turn the parent stopped at, and the
    messages the child should start from. Nothing is written, so a caller that
    goes no further -- a turn id that names nothing, a source that is not a
    session -- leaves the workspace exactly as it found it.

    ``turn_id`` must name a turn recorded as complete: an exchange whose
    assistant line is already in the file. Branching from a user line that was
    never answered would fork the conversation at a point no model has spoken
    from, and the child would begin with a request the parent never made.
    """
    source = Path(source)
    if not source.is_file():
        # Checked before anything else because the reader below creates a file
        # it is handed that does not exist yet -- which is right for `run
        # --session notes`, and would be an invented conversation here.
        raise BranchError("no such session: %s" % source)
    if not name:
        name = default_branch_path(source, sessions_dir).name
    child = branch_path(name, sessions_dir)
    if child.exists():
        # Adopting an existing file would append a second branch to a file
        # whose header already names a different one, and a file that two
        # conversations both claim is neither.
        raise BranchError("%s already exists (choose another --name)" % child)
    try:
        store = SessionFile.open(source, "run_branch00000000000_000000000000",
                                 Path("traces/branch.jsonl"), "0.3.0")
    except Exception as exc:
        raise BranchError(str(exc))

    if store.conversation_id is None:
        # A v0.1/v0.2 file has no conversation id, and minting one here would
        # put a child of the new format beside a parent of the old with no
        # link that can be read back out of either file. Refusing keeps the
        # claim honest: this command branches the v0.3 identity layer, so it
        # branches a file that has one.
        raise BranchError(
            "%s records no conversation id; only a v0.3 session file can be "
            "branched" % source
        )

    turns: List[Dict[str, str]] = store.turns
    for number, turn in enumerate(turns, 1):
        if turn.get(TURN_KEY) == turn_id:
            if number % 2 != 0:
                raise BranchError(
                    "turn %r is not a completed exchange: a branch continues "
                    "from an answered user turn" % turn_id
                )
            return {
                "conversation_id": store.conversation_id,
                "branch_id": new_workspace_id("branch"),
                "parent_branch_id": store.branch_id,
                "parent_turn_id": turn_id,
                "turns": number,
                "messages": [{"role": t["role"], "content": t["content"]}
                             for t in turns[:number]],
                "session": str(child),
                "source": str(source),
            }
    raise BranchError("%s has no turn %r" % (source, turn_id))


def create_branch_from(source: Path, turn_id: str, name: Optional[str] = None,
                       sessions_dir: str = "sessions",
                       version: str = "0.3.0") -> Dict[str, Any]:
    """Derive a branch and write its session file.

    The child file is created whole: its own run id, its own trace path, the
    conversation id it shares with the parent, the branch id minted for it,
    the two parent keys, and copies of the turns it inherits. It is then a
    real session, so `run --session <name>` continues it exactly the way it
    continues a session that was started rather than branched.
    """
    described = derive_branch(source, turn_id, name, sessions_dir)
    child = Path(described["session"])
    run_id = new_run_id()
    trace_path = child.parent.parent / "traces" / ("%s.jsonl" % run_id)
    store = create_branch(
        child, run_id, trace_path, version,
        described["conversation_id"], described["branch_id"],
        described["parent_branch_id"], described["parent_turn_id"],
        described["messages"],
    )
    return {
        **described,
        "run_id": run_id,
        "trace": str(trace_path),
        "messages_written": len(store.messages),
    }
