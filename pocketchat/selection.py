"""Recording which answer a comparison produced was kept.

A comparison manifest is an index of a measurement: it can say what each
profile answered, what it cost and how it failed, but it has no field for
the one decision the operator makes afterwards -- which answer was kept. A
manifest is also exclusive-create evidence that is never rewritten, so the
decision cannot go there. It gets its own record instead:
``comparisons/<comparison_id>.selection.json``, created once, naming the
comparison, the run and the profile, optionally why.

``--into`` closes the loop the workspace model is built for: the selected
exchange becomes the first two lines of a session a later ``run`` continues.
That write is the strictest thing here, and it is fail-closed on purpose. A
reply is only written when the run's own trace says the full text was
recorded (``run.started`` carries ``config_summary.verbose``); a comparison
that ran without ``--verbose`` carries a prefix standing in for the answer,
and a prefix written into a session would be read as the whole reply. Every
input -- manifest, run, trace, reply -- is checked before the first byte is
written, so a refused selection leaves the workspace byte-identical.

The session ``--into`` creates or resumes is a run of its own: a fresh run
id and a fresh trace, seeded with the selected exchange, the way a branch is
a fresh run seeded with inherited turns. The selection file is what ties it
back to the comparison; the session's header does not borrow the compare
run's id, because a file that names a run it does not continue would put a
second writer on that run's trace.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .compare import COMPARISONS_DIRNAME, ComparisonManifestError
from .session import new_run_id
from .sessions import (SessionFile, SessionFileError,
                       resolve_session_path)
from .trace import utc_now

SELECTION_SCHEMA_VERSION = "1"
SELECTION_SUFFIX = ".selection.json"


class SelectionError(Exception):
    """A selection could not be recorded; the operator's request is refused."""


def selection_path(workspace: Path, comparison_id: str) -> Path:
    """Where one comparison's selection record lives, beside its manifest."""
    return Path(workspace) / COMPARISONS_DIRNAME / (comparison_id + SELECTION_SUFFIX)


def _load_manifest(workspace: Path, target: str) -> tuple:
    """The manifest an operator named, by id or by path.

    An explicit path that exists is taken as written; a bare id is looked up
    under ``./comparisons`` with ``.json`` appended. The manifest is an index
    a reader starts from, so a missing or unreadable one is refused with the
    path in the message rather than answered with an empty result.
    """
    workspace = Path(workspace)
    candidate = Path(target)
    if not candidate.is_file():
        named = candidate.name
        if not named.endswith(".json"):
            named += ".json"
        candidate = workspace / COMPARISONS_DIRNAME / named
    if not candidate.is_file():
        raise SelectionError("no comparison manifest for %s" % target)
    try:
        record = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SelectionError("cannot read comparison manifest %s: %s"
                             % (candidate, exc)) from exc
    if not isinstance(record, dict) or not isinstance(record.get("runs"), list):
        raise SelectionError("%s is not a comparison manifest" % candidate)
    comparison_id = record.get("comparison_id")
    if not isinstance(comparison_id, str) or not comparison_id.strip():
        raise SelectionError("%s names no comparison id" % candidate)
    return candidate, record


def _resolve_run(manifest: Dict[str, Any], key: str) -> Dict[str, Any]:
    """The run an operator named, by run id or by unambiguous profile.

    A profile name is accepted because it is what an operator is looking at
    in the comparison table -- but only when it names exactly one run. Two
    runs of one profile cannot happen in this kernel, and a name that
    matches none is a typo to refuse, never an empty choice to make.
    """
    runs = [run for run in manifest["runs"] if isinstance(run, dict)]
    for run in runs:
        if run.get("run_id") == key:
            return run
    by_profile = [run for run in runs if run.get("profile") == key]
    if len(by_profile) == 1:
        return by_profile[0]
    if len(by_profile) > 1:
        raise SelectionError("%r names more than one run; use a run id" % key)
    raise SelectionError("the manifest records no run or profile named %r" % key)


def _reply_as_recorded(workspace: Path, run: Dict[str, Any]) -> str:
    """The run's reply, only when the trace says it was recorded in full.

    The fullness is read from the run's own evidence, not from the reply's
    shape: a short reply fits any prefix, so guessing from the text would
    let a summary through precisely when it happens to look complete.
    """
    status = run.get("status")
    if status != "completed":
        raise SelectionError(
            "run %s is %s, not completed; there is no answer to write into a "
            "session" % (run.get("run_id"), status or "not recorded"))

    trace = run.get("trace")
    if not isinstance(trace, str) or not trace.strip():
        raise SelectionError("run %s records no trace" % run.get("run_id"))
    trace_path = workspace / trace
    if not trace_path.is_file():
        raise SelectionError("run %s records trace %s, which is not in this "
                             "workspace" % (run.get("run_id"), trace))
    try:
        events = [json.loads(line)
                  for line in trace_path.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    except (OSError, ValueError) as exc:
        raise SelectionError("cannot read %s: %s" % (trace_path, exc)) from exc

    verbose = None
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "run.started":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            config = payload.get("config_summary")
            if isinstance(config, dict):
                verbose = config.get("verbose")
    if verbose is not True:
        raise SelectionError(
            "run %s recorded its reply only as a summary (the comparison did "
            "not run with --verbose); a summary written into a session would "
            "be read as the whole answer" % run.get("run_id"))

    reply = None
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "model.response.completed":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("output_summary"), str):
            reply = payload["output_summary"]
    if reply is None:
        raise SelectionError("run %s records no completed response"
                             % run.get("run_id"))
    return reply


def _prepare_into(workspace: Path, manifest: Dict[str, Any],
                  run: Dict[str, Any], session: str) -> Dict[str, Any]:
    """Everything ``--into`` needs, checked before anything is written.

    A selection that fails halfway through would have already recorded a
    choice whose consequence never landed. Validating the reply, the session
    path and the ids first is what makes "refused" and "zero bytes written"
    the same event.
    """
    reply = _reply_as_recorded(workspace, run)
    prompt = manifest.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SelectionError("the manifest records no prompt to seed the "
                             "session with")

    session_path = resolve_session_path(Path(session), "sessions")
    if not session_path.is_absolute():
        session_path = Path(workspace) / session_path
    if session_path.exists() and session_path.is_dir():
        raise SelectionError("%s is a directory, not a session file"
                             % session_path)

    run_id = new_run_id()
    return {
        "session_path": session_path,
        "run_id": run_id,
        "trace_path": Path(workspace) / "traces" / ("%s.jsonl" % run_id),
        "prompt": prompt,
        "reply": reply,
    }


def record_selection(workspace: Path, target: str, key: str,
                     note: Optional[str] = None,
                     into: Optional[str] = None) -> Dict[str, Any]:
    """Record which run of a comparison was kept, optionally into a session.

    Returns the record and the paths a caller reports. The write is
    exclusive-create: a comparison already has a selection or it does not,
    and a second ``select`` for one is refused rather than overwritten --
    the first decision is evidence too.
    """
    workspace = Path(workspace)
    manifest_path, manifest = _load_manifest(workspace, target)
    run = _resolve_run(manifest, key)
    comparison_id = manifest["comparison_id"]

    plan = None
    if into is not None:
        plan = _prepare_into(workspace, manifest, run, into)

    path = selection_path(workspace, comparison_id)
    record: Dict[str, Any] = {
        "schema_version": SELECTION_SCHEMA_VERSION,
        "comparison_id": comparison_id,
        "run_id": run["run_id"],
        "profile": run.get("profile") or "",
        "selected_at": utc_now(),
    }
    if note:
        record["note"] = note

    try:
        handle = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise SelectionError(
            "%s already records a selection; one comparison keeps one decision"
            % path) from exc
    except OSError as exc:
        raise SelectionError("cannot write %s: %s" % (path, exc)) from exc
    with os.fdopen(handle, "wb") as stream:
        stream.write((json.dumps(record, sort_keys=True, indent=2) + "\n")
                     .encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())

    result: Dict[str, Any] = {
        "selection_path": path,
        "record": record,
        "manifest_path": manifest_path,
    }
    if plan is not None:
        result["session_path"] = _write_into(plan)
    return result


def _write_into(plan: Dict[str, Any]) -> Path:
    """Append the selected exchange to the session the plan names.

    The session is opened, not rewritten: an existing file keeps its header
    and its history and gains two lines, a new one starts whole with its own
    run id and trace. The selection file -- not the session header -- is what
    says where the exchange came from.
    """
    try:
        store = SessionFile.open(plan["session_path"], plan["run_id"],
                                 plan["trace_path"], __version__)
        store.append("user", plan["prompt"])
        store.append("assistant", plan["reply"])
    except (SessionFileError, OSError) as exc:
        raise SelectionError(str(exc)) from exc
    return plan["session_path"]


def read_selection(path: Path) -> Optional[Dict[str, Any]]:
    """A selection record, or ``None`` when the file names none.

    Malformed content raises :class:`ValueError` rather than coming back as a
    half-read record: a selection a reader cannot trust is a problem to
    report, not a gap to fill.
    """
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("cannot read selection %s: %s" % (path, exc)) from exc
    if not isinstance(record, dict) or not isinstance(record.get("run_id"), str):
        raise ValueError("%s is not a selection record" % path)
    return record


__all__ = ["SELECTION_SCHEMA_VERSION", "SELECTION_SUFFIX", "SelectionError",
           "ComparisonManifestError", "read_selection", "record_selection",
           "selection_path"]
