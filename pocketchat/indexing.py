"""The derived index: a disposable query surface over the evidence files.

``index.sqlite`` holds no fact of its own. Every row is a reading of the
session files, traces and comparison manifests that the reconstruction
already reads, and every source file's digest is recorded beside them --
so the index can say, about itself, whether it still describes the
workspace it was built from. A stale index refuses to pass silently: the
status report names the files that changed and exits non-zero, because an
index that disagrees with its sources is worse than no index at all.

Nothing in the index reads a clock. Freshness comes from the digests, not
from a build timestamp, so two builds over the same bytes produce
byte-identical files on this machine's SQLite (verified 2026-10-24);
what is not promised is byte equality across SQLite versions, whose page
layout is their own business. The index is disposable by contract: delete
it and every answer the commands give is unchanged, because no command
reads it -- it exists for the tools and UI that will.

A build that fails halfway removes the half it wrote. A corrupt index that
claims freshness would be the one failure mode this module cannot report
on its own.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .workspace import (COMPARISONS_DIRNAME, GRAPH_SCHEMA_VERSION,
                        SESSIONS_DIRNAME, TRACES_DIRNAME, WorkspaceError,
                        collect_workspace)

INDEX_SCHEMA_VERSION = "1"
INDEX_FILENAME = "index.sqlite"


class IndexingError(Exception):
    """The index could not be built or read; the request is refused."""


def index_path(workspace: Path) -> Path:
    return Path(workspace) / INDEX_FILENAME


def _source_digests(workspace: Path) -> Dict[str, str]:
    """The sha256 of every evidence file, relative to the workspace.

    The three evidence directories are the whole input: anything that
    changes inside them changes the graph, and the digest list is how the
    index notices. The index itself is excluded -- it lives in the
    workspace but is not one of its sources, or a build would mark itself
    stale by existing.
    """
    digests: Dict[str, str] = {}
    for dirname in (SESSIONS_DIRNAME, TRACES_DIRNAME, COMPARISONS_DIRNAME):
        root = Path(workspace) / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == INDEX_FILENAME:
                continue
            key = path.relative_to(workspace).as_posix()
            digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def _graph_rows(graph) -> Dict[str, List[tuple]]:
    """The graph as flat rows, one table per kind of thing it records."""
    branches: List[tuple] = []
    for conversation in graph.conversations:
        for branch in conversation.branches:
            branches.append((
                conversation.conversation_id, branch.branch_id, branch.path,
                branch.run_id, branch.parent_branch_id, branch.parent_turn_id,
                branch.trace, branch.turns, branch.version,
            ))
    comparisons: List[tuple] = []
    runs: List[tuple] = []
    for comparison in graph.comparisons:
        selection = comparison.selection or {}
        comparisons.append((
            comparison.comparison_id, comparison.path,
            comparison.base_session,
            selection.get("run_id"), selection.get("profile"),
            selection.get("path"),
        ))
        for run in comparison.runs:
            error_type = (run.error or {}).get("type")
            runs.append((
                comparison.comparison_id, run.run_id, run.profile, run.status,
                run.trace, run.request, run.origin, error_type,
            ))
    problems: List[tuple] = []
    for trace in graph.unlinked_traces:
        problems.append(("unlinked_trace", trace, "no file claims this trace"))
    for request in graph.unlinked_requests:
        problems.append(("unlinked_request", request,
                         "no file claims this request snapshot"))
    for kind, records in (("unresolved", graph.unresolved),
                          ("conflict", graph.conflicts),
                          ("unreadable", graph.unreadable)):
        problems.extend((kind, record.get("path", ""),
                         record.get("reason", "")) for record in records)
    return {
        "conversation_branches": branches,
        "comparisons": comparisons,
        "comparison_runs": runs,
        "problems": problems,
    }


_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE conversation_branches (
    conversation_id TEXT NOT NULL,
    branch_id TEXT,
    session_path TEXT NOT NULL,
    run_id TEXT NOT NULL,
    parent_branch_id TEXT,
    parent_turn_id TEXT,
    trace TEXT,
    turns INTEGER NOT NULL,
    version TEXT
);
CREATE TABLE comparisons (
    comparison_id TEXT NOT NULL,
    path TEXT NOT NULL,
    base_session TEXT,
    selection_run_id TEXT,
    selection_profile TEXT,
    selection_path TEXT
);
CREATE TABLE comparison_runs (
    comparison_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    profile TEXT,
    status TEXT,
    trace TEXT,
    request TEXT,
    origin TEXT NOT NULL,
    error_type TEXT
);
CREATE TABLE problems (
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""

_TABLES = ("conversation_branches", "comparisons", "comparison_runs",
           "problems")


def build_index(workspace: Path, rebuild: bool = False) -> Dict[str, Any]:
    """Build the index from the workspace's files, all of them, every time.

    There is no incremental update: the workspace is small enough to read
    whole, and an incremental index would need its own truth to stay in step
    with files it did not see change. ``--rebuild`` replaces an existing one;
    without it, an existing index is refused rather than silently replaced,
    so a rebuild is always something the operator asked for.
    """
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise IndexingError("no such workspace: %s" % workspace)
    path = index_path(workspace)
    if path.exists():
        if not rebuild:
            raise IndexingError(
                "%s already exists; pass --rebuild to replace it" % path)
        path.unlink()

    try:
        graph = collect_workspace(workspace)
    except WorkspaceError as exc:
        raise IndexingError(str(exc)) from exc
    rows = _graph_rows(graph)
    digests = _source_digests(workspace)

    con = sqlite3.connect(str(path))
    try:
        with con:
            con.executescript(_SCHEMA)
            con.executemany(
                "INSERT INTO conversation_branches VALUES (?,?,?,?,?,?,?,?,?)",
                rows["conversation_branches"])
            con.executemany(
                "INSERT INTO comparisons VALUES (?,?,?,?,?,?)",
                rows["comparisons"])
            con.executemany(
                "INSERT INTO comparison_runs VALUES (?,?,?,?,?,?,?,?)",
                rows["comparison_runs"])
            con.executemany(
                "INSERT INTO problems VALUES (?,?,?)", rows["problems"])
            con.executemany(
                "INSERT INTO meta VALUES (?,?)", [
                    ("schema_version", INDEX_SCHEMA_VERSION),
                    ("workspace_graph_schema", GRAPH_SCHEMA_VERSION),
                    ("source_digests", json.dumps(digests, sort_keys=True)),
                ])
    except Exception:
        con.close()
        # A half-built index must not sit there looking like an answer.
        path.unlink(missing_ok=True)
        raise
    con.close()

    return {
        "status": "fresh",
        "path": path,
        "sources": len(digests),
        "counts": {name: len(rows[name]) for name in _TABLES},
    }


def index_status(workspace: Path) -> Dict[str, Any]:
    """What the index says about itself, checked against the workspace.

    ``stale`` is a verdict, not a note: the index and the workspace disagree
    about at least one file, and a consumer that used it anyway would be
    reading a description of a workspace that no longer exists. The report
    names every file the two disagree about -- added, changed or gone.
    """
    workspace = Path(workspace)
    if not workspace.is_dir():
        raise IndexingError("no such workspace: %s" % workspace)
    path = index_path(workspace)
    if not path.is_file():
        return {"status": "missing", "path": path, "counts": {},
                "sources": 0, "mismatched": []}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        try:
            meta = dict(con.execute(
                "SELECT key, value FROM meta").fetchall())
            counts = {name: con.execute(
                "SELECT COUNT(*) FROM %s" % name).fetchone()[0]
                for name in _TABLES}
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise IndexingError("cannot read %s: %s" % (path, exc)) from exc
    if meta.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise IndexingError(
            "%s has unsupported index schema version %r; rebuild it"
            % (path, meta.get("schema_version")))

    recorded = json.loads(meta.get("source_digests", "{}"))
    current = _source_digests(workspace)
    mismatched = sorted(
        key for key in set(recorded) | set(current)
        if recorded.get(key) != current.get(key))
    return {
        "status": "fresh" if not mismatched else "stale",
        "path": path,
        "counts": counts,
        "sources": len(current),
        "mismatched": mismatched,
    }


def dump_rows(path: Path) -> Dict[str, List[tuple]]:
    """Every row of every table, ordered, for the equivalence check.

    The comparison a rebuild must pass is that this dump is unchanged: the
    same sources give the same rows in the same order, whatever the file's
    page layout did in between.
    """
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return {name: sorted(con.execute(
            "SELECT * FROM %s" % name).fetchall()) for name in _TABLES}
    finally:
        con.close()
