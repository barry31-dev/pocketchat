"""Rebuilding the workspace's relationships from the files on disk.

A workspace records more than any single file can say. The session header
names the conversation and the branch a file belongs to and, for a branch,
the turn it left; the comparison manifest names the run, trace and request
snapshot of every profile it measured. What no one file holds is the shape
those records add up to: which files are two paths through one conversation,
which run belongs to which branch, and which traces nothing claims. That is
what this module reads back.

Reading is the whole of it. Nothing here opens a socket, loads a
configuration, mints an id or writes a byte, so a workspace described here is
byte-for-byte the workspace it was before -- which is the only property that
lets a reader trust the graph more than the process that drew it.

And nothing here guesses. A relation the files do not record is reported as
absent rather than reconstructed: a comparison manifest has no field for
which answer was kept, so the reconstruction says ``not recorded`` instead of
picking the run that looks like the winner. A graph the files cannot support
is a story about the workspace, not a description of it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .selection import SELECTION_SUFFIX, read_selection
from .sessions import (BRANCH_KEY, CONVERSATION_KEY, HEADER_KEY, NOT_RECORDED,
                      PARENT_BRANCH_KEY, PARENT_TURN_KEY, SessionFileError,
                      read_header, read_turns)

SESSIONS_DIRNAME = "sessions"
TRACES_DIRNAME = "traces"
COMPARISONS_DIRNAME = "comparisons"
GRAPH_SCHEMA_VERSION = "1"

VERSION_KEY = "pocketchat_version"
TRACE_KEY = "trace"


class WorkspaceError(Exception):
    """A file offered as part of the workspace cannot be read as one."""


@dataclass(frozen=True)
class BranchRelation:
    """One session file as a branch of a conversation.

    ``trace`` is ``None`` when the header records none: a file whose trace
    was never written down has no trace to point at, and inventing a path
    from the run id would pair the file with whatever happened to share its
    name.
    """

    path: str
    run_id: str
    conversation_id: Optional[str]
    branch_id: Optional[str]
    parent_branch_id: Optional[str]
    parent_turn_id: Optional[str]
    trace: Optional[str]
    turns: int
    version: str


@dataclass(frozen=True)
class RunRelation:
    """One run as some file recorded it.

    ``origin`` says which kind of file the relation was read from, because the
    two do not record the same things: a manifest carries a status and an
    error, a session header carries neither. A status this reader did not read
    is ``not recorded``, never a value inferred from the run's shape.
    """

    run_id: str
    origin: str
    trace: Optional[str]
    request: Optional[str]
    profile: Optional[str]
    status: Optional[str]
    error: Optional[Dict[str, str]]


@dataclass(frozen=True)
class ComparisonRelation:
    """One comparison manifest: the runs it measured and the file it seeded from.

    ``selection`` is the decision record beside the manifest, when there is
    one: which run was kept, and where the record says so. A manifest without
    one is not a gap -- most comparisons were never decided -- so it reads as
    ``not recorded`` rather than being filled from the runs' shape.
    """

    comparison_id: str
    path: str
    base_session: Optional[str]
    runs: List[RunRelation] = field(default_factory=list)
    selection: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class ConversationRelation:
    """The branches that share one conversation id.

    ``conversation_id`` is :data:`NOT_RECORDED` for a file written before the
    id existed. Such a file is listed on its own, because no recorded link
    says whether two of them were one dialogue -- grouping them would assert a
    relationship the files never made.
    """

    conversation_id: str
    branches: List[BranchRelation] = field(default_factory=list)


@dataclass(frozen=True)
class WorkspaceGraph:
    """Every relationship the workspace's files record, and every one they do not.

    The four lists at the end are the honest half. ``unlinked`` names evidence
    nothing claims, ``unresolved`` names a relation that points at something
    absent, ``conflicts`` names two files claiming the same thing, and
    ``unreadable`` names a file that could not be read at all. All four are
    reported rather than dropped, because a reader who is told nothing about
    them would take the graph for the whole workspace.
    """

    workspace: Path
    conversations: List[ConversationRelation] = field(default_factory=list)
    comparisons: List[ComparisonRelation] = field(default_factory=list)
    unlinked_traces: List[str] = field(default_factory=list)
    unlinked_requests: List[str] = field(default_factory=list)
    unresolved: List[Dict[str, str]] = field(default_factory=list)
    conflicts: List[Dict[str, str]] = field(default_factory=list)
    unreadable: List[Dict[str, str]] = field(default_factory=list)


def _problems(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Problem records in a stable order, whatever keys each one carries.

    ``unresolved`` and ``unreadable`` are keyed by ``path``, but ``conflicts``
    is keyed by the thing two files both claimed. Sorting all three on one
    assumed key would raise the moment a conflict is actually found, which is
    the worst time for a reader to discover it: the report would fail instead
    of naming the disagreement it exists to name.
    """
    return sorted(entries, key=lambda item: json.dumps(item, sort_keys=True))


def _relative(path: Path, workspace: Path) -> str:
    try:
        return os.path.relpath(str(Path(path)), str(workspace))
    except ValueError:
        # Different drives on Windows. The bare name is still the honest answer.
        return Path(path).name


def _text(value: Any) -> Optional[str]:
    """A recorded string, or ``None`` when the file does not carry one."""
    if isinstance(value, str) and value.strip():
        return value
    return None


def _jsonl_files(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.jsonl") if p.is_file())


def _snapshot_files(directory: Path, pattern: str) -> List[Path]:
    """The files of one artifact directory, in a stable order.

    The two artifact kinds do not share a suffix -- a trace is ``.jsonl``, a
    request snapshot is ``.json`` -- and asking one directory for both would
    either miss half of it or pick up files it does not own.
    """
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(pattern) if p.is_file())


def _read_branch(path: Path,
                 workspace: Path) -> Tuple[BranchRelation, Optional[Path], List[str]]:
    """Read one session file as a branch, without writing to it.

    The turn ids come back alongside because the fork point a branch records
    has to be checked against the turns the parent actually holds, and that
    check needs the parent's ids rather than a count.
    """
    try:
        header = read_header(path)
        turns = read_turns(path)
    except SessionFileError as exc:
        raise WorkspaceError(str(exc))
    except OSError as exc:
        raise WorkspaceError("cannot read %s: %s" % (path, exc))

    run_id = _text(header.get(HEADER_KEY))
    if run_id is None:
        raise WorkspaceError("%s: the header names no run id"
                             % _relative(path, workspace))
    recorded_trace = _text(header.get(TRACE_KEY))
    trace_target = None
    if recorded_trace is not None:
        trace_target = (path.parent / recorded_trace).resolve()
    relation = BranchRelation(
        path=_relative(path, workspace),
        run_id=run_id,
        conversation_id=_text(header.get(CONVERSATION_KEY)),
        branch_id=_text(header.get(BRANCH_KEY)),
        parent_branch_id=_text(header.get(PARENT_BRANCH_KEY)),
        parent_turn_id=_text(header.get(PARENT_TURN_KEY)),
        trace=_relative(trace_target, workspace) if trace_target else None,
        turns=len(turns),
        version=str(header.get(VERSION_KEY) or NOT_RECORDED),
    )
    return relation, trace_target, [turn["turn_id"] for turn in turns]


def _read_comparison(path: Path, workspace: Path) -> Tuple[ComparisonRelation,
                                                          List[Tuple[str, Path]],
                                                          List[Dict[str, str]]]:
    """Read one comparison manifest as the set of runs it measured.

    The manifest is an index, so reading it is the only way to learn which
    runs a comparison produced: the runs themselves write no session file, and
    their traces are named by run ids that nothing else records.
    """
    problems: List[Dict[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError("cannot read %s: %s" % (path, exc))
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkspaceError("%s: invalid JSON: %s"
                             % (_relative(path, workspace), exc))
    if not isinstance(record, dict):
        raise WorkspaceError("%s: expected a JSON object"
                             % _relative(path, workspace))

    comparison_id = _text(record.get("comparison_id"))
    if comparison_id is None:
        raise WorkspaceError("%s: the manifest names no comparison id"
                             % _relative(path, workspace))

    base = record.get("base_context")
    base_session = _text(base.get("session")) if isinstance(base, dict) else None

    runs: List[RunRelation] = []
    targets: List[Tuple[str, Path]] = []
    entries = record.get("runs")
    if not isinstance(entries, list):
        problems.append({
            "path": _relative(path, workspace),
            "reason": "the manifest records no list of runs",
        })
        entries = []
    for position, entry in enumerate(entries, 1):
        where = "%s run %d" % (_relative(path, workspace), position)
        if not isinstance(entry, dict):
            problems.append({"path": where, "reason": "expected a JSON object"})
            continue
        run_id = _text(entry.get("run_id"))
        if run_id is None:
            problems.append({"path": where, "reason": "the run names no run id"})
            continue
        error = entry.get("error")
        runs.append(RunRelation(
            run_id=run_id,
            origin="comparison manifest",
            trace=_text(entry.get("trace")),
            request=_text(entry.get("request")),
            profile=_text(entry.get("profile")),
            status=_text(entry.get("status")),
            error=error if isinstance(error, dict) else None,
        ))
        for key in ("trace", "request"):
            recorded = _text(entry.get(key))
            if recorded is None:
                continue
            targets.append((key, (workspace / recorded).resolve()))

    relation = ComparisonRelation(
        comparison_id=comparison_id,
        path=_relative(path, workspace),
        base_session=base_session,
        runs=runs,
    )
    return relation, targets, problems


def _read_selection(path: Path,
                    workspace: Path) -> Tuple[Optional[Dict[str, str]],
                                              Optional[Dict[str, str]]]:
    """A comparison's selection record, or the reason it cannot be trusted.

    ``None, None`` means there is no record -- the ordinary case, not a
    failure. A file that exists but cannot be read as a selection is a
    problem to report, because a reader told nothing about it would take the
    graph for the whole workspace.
    """
    if not path.is_file():
        return None, None
    try:
        record = read_selection(path)
    except ValueError as exc:
        return None, {"path": _relative(path, workspace), "reason": str(exc)}
    selection = {
        "path": _relative(path, workspace),
        "run_id": record["run_id"],
        "profile": str(record.get("profile") or ""),
        "selected_at": str(record.get("selected_at") or NOT_RECORDED),
    }
    if record.get("note"):
        selection["note"] = str(record["note"])
    return selection, None


def collect(workspace: Path = Path(".")) -> WorkspaceGraph:
    """Read a workspace's files and return the relationships they record.

    Every path in the result is relative to ``workspace`` and every list is
    sorted, so two processes handed the same bytes describe the same graph.
    """
    # Resolved, not taken as typed: on macOS ``/var`` is a symlink to
    # ``/private/var``, so a path built by following the directory tree and a
    # path typed at the command line name the same file with different
    # prefixes. Resolving both to the real path is what keeps ``os.path``
    # computing "traces/x" instead of a detour through the symlink -- and it
    # is what makes reading the same workspace from two spellings of it give
    # the same answer.
    workspace = Path(workspace).resolve()
    branches: List[BranchRelation] = []
    turn_ids: Dict[str, List[str]] = {}
    comparisons: List[ComparisonRelation] = []
    unreadable: List[Dict[str, str]] = []
    unresolved: List[Dict[str, str]] = []
    conflicts: List[Dict[str, str]] = []
    trace_owners: Dict[str, str] = {}
    request_owners: Dict[str, str] = {}
    run_owners: Dict[str, str] = {}
    trace_targets: List[Tuple[str, str, Path]] = []
    request_targets: List[Tuple[str, str, Path]] = []

    def claim_run(run_id: str, source: str) -> None:
        previous = run_owners.get(run_id)
        if previous is not None and previous != source:
            conflicts.append({
                "kind": "run id claimed twice",
                "id": run_id,
                "paths": sorted([previous, source]),
            })
            return
        run_owners[run_id] = source

    def claim_trace(target: Path, source: str) -> None:
        key = str(target)
        previous = trace_owners.get(key)
        if previous is not None and previous != source:
            conflicts.append({
                "kind": "trace claimed twice",
                "id": _relative(target, workspace),
                "paths": sorted([previous, source]),
            })
            return
        trace_owners[key] = source

    for path in _jsonl_files(workspace / SESSIONS_DIRNAME):
        try:
            relation, trace_target, ids = _read_branch(path, workspace)
        except WorkspaceError as exc:
            unreadable.append({"path": _relative(path, workspace),
                               "reason": str(exc)})
            continue
        branches.append(relation)
        turn_ids[relation.path] = ids
        claim_run(relation.run_id, relation.path)
        if trace_target is not None:
            claim_trace(trace_target, relation.path)
            trace_targets.append((relation.path, relation.trace, trace_target))

    comparisons_dir = workspace / COMPARISONS_DIRNAME
    for path in _snapshot_files(comparisons_dir, "*.json"):
        # A selection record is not a manifest: it is the decision beside one,
        # and reading it as a manifest would report every selection as a
        # broken index.
        if path.name.endswith(SELECTION_SUFFIX):
            continue
        try:
            relation, targets, problems = _read_comparison(path, workspace)
        except WorkspaceError as exc:
            unreadable.append({"path": _relative(path, workspace),
                               "reason": str(exc)})
            continue
        selection, selection_problem = _read_selection(
            comparisons_dir / (relation.comparison_id + SELECTION_SUFFIX),
            workspace)
        if selection_problem is not None:
            unreadable.append(selection_problem)
        if selection is not None or selection_problem is not None:
            relation = replace(relation, selection=selection)
        comparisons.append(relation)
        unreadable.extend(problems)
        for run in relation.runs:
            claim_run(run.run_id, relation.path)
        for key, target in targets:
            if key == "trace":
                claim_trace(target, relation.path)
                trace_targets.append((relation.path, _relative(target, workspace),
                                      target))
            else:
                request_owners[str(target)] = relation.path
                request_targets.append((relation.path, _relative(target, workspace),
                                        target))

    for source, display, target in trace_targets:
        if not target.is_file():
            unresolved.append({
                "path": source,
                "reason": "records trace %s, which is not in this workspace"
                          % display,
            })
    for source, display, target in request_targets:
        if not target.is_file():
            unresolved.append({
                "path": source,
                "reason": "records request snapshot %s, which is not in this "
                          "workspace" % display,
            })

    for relation in comparisons:
        if relation.base_session is None:
            continue
        if not (workspace / relation.base_session).is_file():
            unresolved.append({
                "path": relation.path,
                "reason": "records base session %s, which is not in this "
                          "workspace" % relation.base_session,
            })

    by_branch: Dict[str, BranchRelation] = {}
    for relation in branches:
        if relation.branch_id is not None:
            by_branch.setdefault(relation.branch_id, relation)
    for relation in branches:
        if relation.parent_branch_id is None:
            continue
        parent = by_branch.get(relation.parent_branch_id)
        if parent is None:
            unresolved.append({
                "path": relation.path,
                "reason": "records parent branch %s, which is not in this "
                          "workspace" % relation.parent_branch_id,
            })
            continue
        if (parent.conversation_id is not None
                and parent.conversation_id != relation.conversation_id):
            unresolved.append({
                "path": relation.path,
                "reason": "records parent branch %s, which belongs to "
                          "conversation %s" % (relation.parent_branch_id,
                                               parent.conversation_id),
            })
            continue
        if relation.parent_turn_id is None:
            continue
        if relation.parent_turn_id not in turn_ids.get(parent.path, []):
            unresolved.append({
                "path": relation.path,
                "reason": "records parent turn %s, which %s does not hold"
                          % (relation.parent_turn_id, parent.path),
            })

    groups: Dict[str, List[BranchRelation]] = {}
    for relation in branches:
        # A file with no conversation id is grouped on its own path rather
        # than with every other such file: two v0.1/v0.2 files are two
        # conversations unless something says otherwise, and nothing here does.
        key = relation.conversation_id or ("file:%s" % relation.path)
        groups.setdefault(key, []).append(relation)
    conversations = [ConversationRelation(
        conversation_id=sorted(members, key=lambda r: r.path)[0].conversation_id
        or NOT_RECORDED,
        branches=sorted(members, key=lambda r: r.path),
    ) for members in groups.values()]
    conversations.sort(key=lambda c: (c.conversation_id, c.branches[0].path))
    comparisons.sort(key=lambda c: c.path)

    traces_dir = workspace / TRACES_DIRNAME
    requests_dir = traces_dir / "requests"
    return WorkspaceGraph(
        workspace=workspace,
        conversations=conversations,
        comparisons=comparisons,
        unlinked_traces=[_relative(p, workspace)
                         for p in _jsonl_files(traces_dir)
                         if str(p.resolve()) not in trace_owners],
        unlinked_requests=[_relative(p, workspace)
                           for p in _snapshot_files(requests_dir, "*.json")
                           if str(p.resolve()) not in request_owners],
        unresolved=_problems(unresolved),
        conflicts=_problems(conflicts),
        unreadable=_problems(unreadable),
    )


def collect_workspace(workspace: Path = Path(".")) -> WorkspaceGraph:
    """Collect a workspace, refusing one that cannot be read at all.

    The directory check comes first because every other failure is reported
    per file: a workspace that does not exist has no files to report, and
    answering with an empty graph would read as "this workspace is empty".
    """
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise WorkspaceError("no such workspace: %s" % workspace)
    return collect(workspace)


def as_dict(graph: WorkspaceGraph) -> Dict[str, Any]:
    """The graph as JSON-ready data: every path relative, no absolute, no clock.

    The workspace itself is emitted as ``"."`` rather than as a path, because
    every other path in the record is already relative to it. Writing the
    real location in would tie a portable record to one machine's directory
    tree, which is the one thing a file meant to be read back anywhere should
    not carry.
    """
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "workspace": ".",
        "conversations": [{
            "conversation_id": conversation.conversation_id,
            "branches": [{
                "path": branch.path,
                "run_id": branch.run_id,
                "conversation_id": branch.conversation_id,
                "branch_id": branch.branch_id,
                "parent_branch_id": branch.parent_branch_id,
                "parent_turn_id": branch.parent_turn_id,
                "trace": branch.trace,
                "turns": branch.turns,
                "version": branch.version,
            } for branch in conversation.branches],
        } for conversation in graph.conversations],
        "comparisons": [{
            "comparison_id": comparison.comparison_id,
            "path": comparison.path,
            "base_session": comparison.base_session,
            "selection": (dict(comparison.selection)
                          if comparison.selection is not None else NOT_RECORDED),
            "runs": [{
                "profile": run.profile,
                "run_id": run.run_id,
                "trace": run.trace,
                "request": run.request,
                "status": run.status if run.status is not None else NOT_RECORDED,
                "error": run.error,
                "origin": run.origin,
            } for run in comparison.runs],
        } for comparison in graph.comparisons],
        "unlinked_traces": list(graph.unlinked_traces),
        "unlinked_requests": list(graph.unlinked_requests),
        "unresolved": [dict(item) for item in graph.unresolved],
        "conflicts": [dict(item) for item in graph.conflicts],
        "unreadable": [dict(item) for item in graph.unreadable],
    }


def render_lines(graph: WorkspaceGraph) -> List[str]:
    """The graph as text an operator can read top to bottom."""
    lines = ["workspace %s" % graph.workspace]
    if not graph.conversations and not graph.comparisons:
        lines.append("  (no conversations or comparisons recorded)")
    for conversation in graph.conversations:
        count = len(conversation.branches)
        if conversation.conversation_id == NOT_RECORDED:
            lines.append("conversation %s  (no conversation id recorded, so "
                         "this file is listed on its own)" % NOT_RECORDED)
        else:
            lines.append("conversation %s  (%d branch%s)"
                         % (conversation.conversation_id, count,
                            "" if count == 1 else "es"))
        for branch in conversation.branches:
            head = "  branch %s  %s" % (branch.branch_id or NOT_RECORDED,
                                        branch.path)
            if branch.parent_branch_id:
                head += "  from %s at %s" % (branch.parent_branch_id,
                                             branch.parent_turn_id or NOT_RECORDED)
            lines.append(head)
            lines.append("    run %s  %s  (from the session header)"
                         % (branch.run_id, branch.trace or NOT_RECORDED))
    for comparison in graph.comparisons:
        lines.append("comparison %s  %s  base %s"
                     % (comparison.comparison_id, comparison.path,
                        comparison.base_session or NOT_RECORDED))
        for run in comparison.runs:
            detail = "    %-8s %s  %s  %s" % (
                run.profile or "-", run.run_id, run.trace or NOT_RECORDED,
                run.status or NOT_RECORDED)
            if run.error and run.error.get("type"):
                detail += "  %s" % run.error["type"]
            lines.append(detail)
        if comparison.selection is not None:
            selection = comparison.selection
            line = "    selection %s  (%s)  %s" % (
                selection["run_id"], selection["profile"] or "-",
                selection["path"])
            if selection.get("note"):
                line += "  %s" % selection["note"]
            lines.append(line)
        else:
            lines.append("    selection %s" % NOT_RECORDED)
    for title, items in (("unlinked traces", graph.unlinked_traces),
                         ("unlinked request snapshots", graph.unlinked_requests)):
        if items:
            lines.append("%s:" % title)
            lines.extend("  %s" % item for item in items)
    for title, items in (("unresolved", graph.unresolved),
                         ("conflicts", graph.conflicts),
                         ("unreadable", graph.unreadable)):
        if items:
            lines.append("%s:" % title)
            lines.extend("  %s: %s" % (item["path"], item["reason"])
                         for item in items)
    return lines
