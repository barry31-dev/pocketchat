"""Querying the conversations a workspace holds: history, search and diff.

The reconstruction names the relationships; these commands read the words.
`history` lists the turns a conversation's branches record, `search` finds
literal text across the message lines, and `diff` compares two sessions turn
by turn, prefix first. All three are readings in the sense `workspace` is:
nothing here opens a socket, loads a configuration, mints an id or writes a
byte, so the same files give the same answer in any process, and a query
cannot change the workspace it is asking about.

Nothing here invents, either. A turn the line does not name is called by the
id its position gives it -- the same rule every reader of the file applies --
a hit names the file it was found in rather than the conversation it was
probably part of, and a diff that disagrees with a fork point a file records
reports the disagreement instead of choosing the recorded answer. Problems
the graph reader finds (files that cannot be read, links that point at
nothing) stay with `pocketchat workspace`; these commands answer from the
files that do read, and leave the reporting of the rest to the command whose
job it is.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .sessions import (NOT_RECORDED, SessionFileError, read_header,
                       read_turns, resolve_session_argument,
                       resolve_session_path)
from .workspace import (SESSIONS_DIRNAME, ConversationRelation,
                        WorkspaceError, collect_workspace)

QUERY_SCHEMA_VERSION = "1"
PREVIEW_WIDTH = 72


class QueryError(Exception):
    """A query could not be answered; the operator's request is refused."""


def _relative(path: Path, workspace: Path) -> str:
    try:
        return os.path.relpath(str(path), str(workspace))
    except ValueError:
        # Different drives on Windows. The bare name is still the honest answer.
        return Path(path).name


def _text(value: Any) -> Optional[str]:
    """A recorded string, or ``None`` when the file does not carry one."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _session_path(value: str, workspace: Path) -> Path:
    """The session file a query names, by the rule every command uses.

    The resolution itself lives in ``sessions``: a bare name reads from
    ``./sessions``, an existing path stands as written, and the trace of a
    pair is mapped back to the session that recorded it -- one spelling of
    one location, shared with ``show`` and ``branch`` rather than restated
    here. This layer only turns the shared refusal into its own error type.
    """
    try:
        return resolve_session_argument(Path(value), SESSIONS_DIRNAME, workspace)
    except SessionFileError as exc:
        raise QueryError(str(exc))


def _plural(count: int, noun: str) -> str:
    return "%d %s%s" % (count, noun, "" if count == 1 else "s")


def _preview(content: str) -> str:
    """One printable line standing in for a message of any shape.

    Whitespace collapses, because a turn broken across lines is still one
    turn, and the line ends in ``...`` when text was dropped -- a preview
    that silently ended mid-sentence would read as the whole message.
    """
    flat = " ".join(content.split())
    if len(flat) > PREVIEW_WIDTH:
        return flat[:PREVIEW_WIDTH] + "..."
    return flat or "(no text)"


def _read_session(path: Path):
    """A session's header and named turns, or a refusal that names the file."""
    try:
        header = read_header(path)
        turns = read_turns(path)
    except (SessionFileError, OSError) as exc:
        raise QueryError(str(exc))
    return header, turns


def _branch_record(workspace: Path, branch) -> Dict[str, Any]:
    """One branch of the reconstructed graph, with the words it holds.

    The identity comes from the graph and the turns from the file, so both
    halves are read by the readers that own them rather than parsed a second
    time here.
    """
    _, turns = _read_session(workspace / branch.path)
    return {
        "path": branch.path,
        "run_id": branch.run_id,
        "conversation_id": branch.conversation_id,
        "branch_id": branch.branch_id,
        "parent_branch_id": branch.parent_branch_id,
        "parent_turn_id": branch.parent_turn_id,
        "trace": branch.trace,
        "turns": [dict(turn) for turn in turns],
    }


def _conversations_holding(graph, paths: List[str]) -> List[ConversationRelation]:
    """The conversations again, keeping only the branches a query selected.

    The conversation wrapper survives the filter because the conversation id
    is context the branch cannot carry alone: a one-branch answer that named
    no conversation would read as a conversation of one where the file simply
    has no siblings in this workspace.
    """
    wanted = set(paths)
    selected: List[ConversationRelation] = []
    for conversation in graph.conversations:
        branches = [b for b in conversation.branches if b.path in wanted]
        if branches:
            selected.append(ConversationRelation(
                conversation_id=conversation.conversation_id,
                branches=branches))
    return selected


def _select_target(graph, target: str,
                   workspace: Path) -> Tuple[str, List[ConversationRelation]]:
    """Resolve a history target into its kind and the conversations it names.

    A file is tried first, by the rule every read command uses, then the
    conversation ids, then the branch ids. The three are tried in that order
    because they live at different scopes: a name that happens to equal an
    id is a coincidence the operator can see in the answer's ``target``
    record, while an order that changed per input would make the same
    argument mean different things on different days.
    """
    candidate = resolve_session_path(Path(target), SESSIONS_DIRNAME)
    path = candidate if candidate.is_absolute() else workspace / candidate
    path = path.resolve()
    paths = [branch.path
             for conversation in graph.conversations
             for branch in conversation.branches
             if (workspace / branch.path).resolve() == path]
    if paths:
        return "session", _conversations_holding(graph, paths)
    for conversation in graph.conversations:
        if conversation.conversation_id == target:
            return "conversation", [conversation]
    for conversation in graph.conversations:
        for branch in conversation.branches:
            if branch.branch_id == target:
                return "branch", _conversations_holding(graph, [branch.path])
    raise QueryError("no session, conversation or branch named %r in this "
                     "workspace" % target)


def collect_history(workspace: Path = Path("."),
                    target: Optional[str] = None) -> Dict[str, Any]:
    """The turns the workspace's sessions record, as JSON-ready data.

    Every path is relative to ``workspace`` and every list is sorted, so two
    processes handed the same bytes return the same record. A v0.1/v0.2 file
    is listed with the turns its positions name; its identity fields read as
    ``None`` and its conversation as ``not recorded``, which is what the file
    itself says.
    """
    workspace = Path(workspace).resolve()
    try:
        graph = collect_workspace(workspace)
    except WorkspaceError as exc:
        raise QueryError(str(exc))

    selected = graph.conversations
    target_record = None
    if target is not None:
        kind, selected = _select_target(graph, target, workspace)
        target_record = {"kind": kind, "name": target}

    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "workspace": ".",
        "target": target_record,
        "conversations": [{
            "conversation_id": conversation.conversation_id,
            "branches": [_branch_record(workspace, branch)
                         for branch in conversation.branches],
        } for conversation in selected],
    }


def search_messages(needle: str, workspace: Path = Path("."),
                    session: Optional[str] = None) -> Dict[str, Any]:
    """Find literal text in the message lines the sessions record.

    The match is a plain substring, case sensitive, because that is the one
    rule whose hits a reader can verify by eye: a quiet normalization (case
    folding, whitespace squeezing) would find more and let the operator
    believe a file holds a wording it does not. An empty needle is refused
    rather than answered with everything, and zero hits is a result: the
    answer says so and the exit code stays 0.
    """
    if not needle:
        raise QueryError("the search text is empty; name the text to look for")
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise QueryError("no such workspace: %s" % workspace)

    if session is not None:
        files = [_session_path(session, workspace)]
    else:
        directory = workspace / SESSIONS_DIRNAME
        files = (sorted(p for p in directory.glob("*.jsonl") if p.is_file())
                 if directory.is_dir() else [])

    matches: List[Dict[str, Any]] = []
    for path in files:
        header, turns = _read_session(path)
        # A turn the fork inherited is a hit in both files that hold it. That
        # is not a duplicate: each hit names a file, and a match folded into
        # "the conversation" would hide which files actually carry the words.
        for turn in turns:
            if needle in turn["content"]:
                matches.append({
                    "path": _relative(path, workspace),
                    "conversation_id": _text(header.get("conversation_id")),
                    "branch_id": _text(header.get("branch_id")),
                    "turn_id": turn["turn_id"],
                    "role": turn["role"],
                    "content": turn["content"],
                })
    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "workspace": ".",
        "query": needle,
        "sessions_searched": len(files),
        "matches": matches,
    }


def _fork_relation(child_side: str, child_header: Dict[str, Any],
                   parent_turns: List[Dict[str, str]],
                   common: int) -> Dict[str, Any]:
    """Check a recorded fork point against the turns the files hold.

    The child says which turn it left the parent at. The files say where
    their words actually stop agreeing. A reader who reported only one of
    the two would either trust a claim the bytes contradict or silently
    correct it, and both are worse than saying the two disagree.
    """
    recorded = _text(child_header.get("parent_turn_id"))
    matches: Optional[bool] = None
    if recorded is not None:
        parent_ids = [turn["turn_id"] for turn in parent_turns]
        if recorded in parent_ids:
            # The recorded turn's position in the parent is the prefix the
            # child claims to have inherited; the computed prefix is the one
            # the words support.
            matches = (parent_ids.index(recorded) + 1) == common
        else:
            matches = False
    return {
        "child": child_side,
        "parent_turn_id": recorded,
        "matches_common_prefix": matches,
    }


def diff_sessions(left: str, right: str,
                  workspace: Path = Path(".")) -> Dict[str, Any]:
    """Compare two sessions turn by turn, common prefix first.

    The comparison is on the words (a turn's role and content), position by
    position from the first. Everything after the first disagreement is
    reported per side, which is the shape a branch actually has: a child is
    the turns it inherited plus its own, so the diff of a real fork is a
    shared prefix and two remainders. A common *suffix* is deliberately not
    computed -- two conversations that happen to end alike are not thereby
    joined, and reporting matched tails would suggest they were.
    """
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise QueryError("no such workspace: %s" % workspace)
    left_path = _session_path(left, workspace)
    right_path = _session_path(right, workspace)
    left_header, left_turns = _read_session(left_path)
    right_header, right_turns = _read_session(right_path)

    common = 0
    for left_turn, right_turn in zip(left_turns, right_turns):
        if ((left_turn["role"], left_turn["content"])
                != (right_turn["role"], right_turn["content"])):
            break
        common += 1

    left_conversation = _text(left_header.get("conversation_id"))
    right_conversation = _text(right_header.get("conversation_id"))
    if left_conversation is None or right_conversation is None:
        same_conversation: Optional[bool] = None
    else:
        same_conversation = left_conversation == right_conversation

    # The relation is read from the headers, not assumed from the shape: two
    # sessions of one conversation are not necessarily a parent and the child
    # it derived, and calling them one would put a fork where none is
    # recorded.
    left_branch = _text(left_header.get("branch_id"))
    right_branch = _text(right_header.get("branch_id"))
    relation = None
    if (left_branch and right_branch
            and _text(right_header.get("parent_branch_id")) == left_branch):
        relation = _fork_relation("right", right_header, left_turns, common)
    elif (left_branch and right_branch
            and _text(left_header.get("parent_branch_id")) == right_branch):
        relation = _fork_relation("left", left_header, right_turns, common)

    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "workspace": ".",
        "left": {
            "path": _relative(left_path, workspace),
            "conversation_id": left_conversation,
            "branch_id": left_branch,
            "turns": len(left_turns),
        },
        "right": {
            "path": _relative(right_path, workspace),
            "conversation_id": right_conversation,
            "branch_id": right_branch,
            "turns": len(right_turns),
        },
        "same_conversation": same_conversation,
        "recorded_relation": relation,
        "common_prefix": {
            "turns": common,
            # The id is read from the left session's own line: the prefix is
            # shared, and naming it by one side's spelling is the same choice
            # every other reader makes when it points at an exchange.
            "through": left_turns[common - 1]["turn_id"] if common else None,
        },
        "only_left": [dict(turn) for turn in left_turns[common:]],
        "only_right": [dict(turn) for turn in right_turns[common:]],
        "identical": (common == len(left_turns) == len(right_turns)),
    }


def render_history_lines(record: Dict[str, Any]) -> List[str]:
    """The history record as text an operator can read top to bottom."""
    conversations = record["conversations"]
    if not conversations:
        return ["no conversations recorded in this workspace"]
    lines: List[str] = []
    for conversation in conversations:
        if conversation["conversation_id"] == NOT_RECORDED:
            lines.append("conversation %s  (no conversation id recorded, so "
                         "this file is listed on its own)" % NOT_RECORDED)
        else:
            lines.append("conversation %s  (%s)"
                         % (conversation["conversation_id"],
                            _plural(len(conversation["branches"]), "branch")))
        for branch in conversation["branches"]:
            head = "  branch %s  %s" % (branch["branch_id"] or NOT_RECORDED,
                                        branch["path"])
            if branch["parent_branch_id"]:
                head += "  from %s  at %s" % (
                    branch["parent_branch_id"],
                    branch["parent_turn_id"] or NOT_RECORDED)
            lines.append(head)
            for turn in branch["turns"]:
                lines.append("    %s  %-5s  %s" % (
                    turn["turn_id"],
                    "you" if turn["role"] == "user" else "model",
                    _preview(turn["content"])))
    return lines


def render_search_lines(record: Dict[str, Any]) -> List[str]:
    """The search record as one summary line and one line per hit."""
    count = len(record["matches"])
    found = _plural(count, "match") if count else "no matches"
    lines = ['search "%s"  %s in %s' % (
        record["query"], found,
        _plural(record["sessions_searched"], "session"))]
    for match in record["matches"]:
        lines.append("  %s  %s  %-5s  %s" % (
            match["path"], match["turn_id"],
            "you" if match["role"] == "user" else "model",
            _preview(match["content"])))
    return lines


def render_diff_lines(record: Dict[str, Any]) -> List[str]:
    """The diff record as text: the prefix, then each side's own turns."""
    left, right = record["left"], record["right"]
    prefix = record["common_prefix"]
    lines = ["diff %s %s" % (left["path"], right["path"])]
    if record["identical"]:
        lines.append("  identical: %s" % _plural(prefix["turns"], "turn"))
        return lines

    same = record["same_conversation"]
    if same is True:
        lines.append("  both are branches of conversation %s"
                     % left["conversation_id"])
    elif same is False:
        lines.append("  %s belongs to conversation %s; %s to %s" % (
            left["path"], left["conversation_id"] or NOT_RECORDED,
            right["path"], right["conversation_id"] or NOT_RECORDED))
    else:
        lines.append("  a conversation id is not recorded on both sides")

    if prefix["turns"]:
        lines.append("  common prefix %s, through %s"
                     % (_plural(prefix["turns"], "turn"), prefix["through"]))
    else:
        lines.append("  no common turn")
    for side, path, turns in (("left", left["path"], record["only_left"]),
                              ("right", right["path"], record["only_right"])):
        lines.append("  %s (%s) then adds %s:" % (path, side,
                                                  _plural(len(turns), "turn")))
        for turn in turns:
            lines.append("    %s  %-5s  %s" % (
                turn["turn_id"],
                "you" if turn["role"] == "user" else "model",
                _preview(turn["content"])))

    relation = record["recorded_relation"]
    if relation is not None and relation["parent_turn_id"] is not None:
        if relation["matches_common_prefix"]:
            lines.append("  the recorded fork at %s matches the common prefix"
                         % relation["parent_turn_id"])
        else:
            lines.append("  the recorded fork at %s disagrees: the common "
                         "prefix ends at %s"
                         % (relation["parent_turn_id"],
                            prefix["through"] or "the first turn"))
    return lines
