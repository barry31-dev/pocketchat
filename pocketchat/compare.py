"""The comparison kernel: several profiles, one prompt, one run each.

A comparison is a measurement, not a mode. Each profile gets an ordinary
:class:`~pocketchat.session.Session` with its own run id, trace and request
snapshot, and runs to completion the way a single ``run`` does. That is the
whole design: there is no second place a turn can be executed and no second
shape a trace can take, so a comparison cannot drift from what a single run
already proves.

What the runs share is the base context and the prompt. Two profiles that
started from different contexts would produce a difference that says nothing
about the profiles, so the base messages are handed in once and copied per run.
What they do not share is the run id, the trace and the request snapshots. No
session file is opened at all: the second profile must not inherit the first
profile's conversation, and the base file must not be rewritten by something
that was only reading it.

Isolation is the point. One profile failing must not stop the others, so the
error is recorded on that run and the next profile still goes out. The one
exception is ``KeyboardInterrupt``: the operator asked to stop, and continuing
would spend their time against their wish, so the comparison ends and the run
in flight is closed with its own terminal event. Either way every run ends --
a trace that starts and never ends is the one shape the tooling this package
feeds rejects first.
"""

from __future__ import annotations

import copy
import os
import json
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .session import Session, new_run_id
from .trace import utc_now
from .cost import estimate_cost

TRACES_DIRNAME = "traces"
COMPARISONS_DIRNAME = "comparisons"
COMPARISON_SCHEMA_VERSION = "1"


class ComparisonManifestError(Exception):
    """The comparison ran, but the index that describes it could not be written.

    The runs are already on disk and their traces are complete; what failed is
    the one file that lets a reader find them all. Keeping that a separate
    exception from the run failures it reports is what lets a caller say "the
    measurement happened, the record of it did not".
    """


def new_comparison_id() -> str:
    """A comparison id that is unique, readable and safe in a filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%S")
    return "cmp_%s_%s" % (stamp, os.urandom(4).hex())


def comparison_manifest_path(workspace: Path, comparison_id: str) -> Path:
    """Where the manifest for one comparison lives.

    Beside ``traces`` rather than inside it: the manifest is the index a reader
    starts from, and filing it among the files it indexes would make the layout
    harder to read without buying anything.
    """
    return Path(workspace) / COMPARISONS_DIRNAME / ("%s.json" % comparison_id)


def write_comparison_manifest(path: Path, result: "ComparisonResult",
                              created: Optional[str] = None) -> Path:
    """Write the manifest for a finished comparison and return its path.

    The manifest is an index, not a second copy of the evidence: it names the
    trace and the request snapshot of every run and leaves both where the run
    put them. A manifest that copied trace content into itself would be a
    second version of the same run to keep in step with the first, and the two
    would eventually disagree.

    Every path in it is relative to the workspace the comparison ran in, so the
    manifest resolves from wherever it is read rather than from the directory
    the process happened to be started in.

    The file is created exclusively and never rewritten, for the reason the
    request snapshot is: a manifest already on disk describes a comparison
    that really happened, and replacing it would destroy the only record.
    """
    record = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "comparison_id": result.comparison_id,
        "created": created or utc_now(),
        "prompt": result.prompt,
        "base_context": result.base_context,
        "runs": [asdict(run) for run in result.runs],
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ComparisonManifestError(
            "%s already exists; it records a comparison that really happened"
            % target
        ) from exc
    except OSError as exc:
        raise ComparisonManifestError("cannot write %s: %s" % (target, exc)) from exc
    with os.fdopen(handle, "wb") as stream:
        stream.write((json.dumps(record, sort_keys=True, ensure_ascii=False,
                                 indent=2) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return target


@dataclass(frozen=True)
class ProfileRun:
    """What one profile did, and where the evidence for it lives.

    ``trace`` and ``request`` are the paths a reader follows to check the run,
    so they are relative to the comparison's workspace rather than absolute
    paths from wherever the process happened to be started.
    """

    profile: str
    run_id: str
    trace: str
    request: str
    status: str
    duration_ms: int
    usage: Dict[str, int]
    cost_usd: Optional[float]
    output_summary: str
    error: Optional[Dict[str, str]]


@dataclass(frozen=True)
class ComparisonResult:
    """The comparison as a whole: what was asked, and what each profile did."""

    comparison_id: str
    prompt: str
    base_context: Dict[str, Any]
    runs: List[ProfileRun] = field(default_factory=list)


def _relative(path: Path, workspace: Path) -> str:
    try:
        return os.path.relpath(str(Path(path)), str(workspace))
    except ValueError:
        # Different drives on Windows. The bare name is still the honest answer.
        return Path(path).name


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """An event timestamp as a datetime, or ``None`` if it will not parse."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _request_duration_ms(events: List[Dict[str, Any]]) -> int:
    """The wall time the model requests of one run took, in milliseconds.

    Measured from the events the run recorded rather than from a clock wrapped
    around the session. The events are the only timestamps the evidence
    carries, so they are what a reader can check; a duration measured from
    outside would include the time spent constructing objects no request
    waited for, and two profiles compared on it would be compared partly on
    their own setup.

    Each request is counted from its ``model.request.started`` to the response
    event that follows it, and the requests are summed, because a multi-turn
    run is compared on what its whole conversation cost. A request with no
    response event is not counted: nothing records when an abandoned request
    ended, so any number for it would be invented.
    """
    if not isinstance(events, list):
        # No events at all means no request was measured. A run that never got
        # as far as writing one is not a run that took no time, but there is
        # nothing here to measure, and inventing a number is worse than zero.
        return 0
    response_types = ("model.response.completed", "model.response.failed")
    total = 0
    started_at = None
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        stamp = _parse_timestamp(event.get("timestamp"))
        if kind == "model.request.started":
            # A second started event without an intervening response means
            # the earlier request never finished; its start is replaced rather
            # than accumulated, so the total cannot exceed the trace's span.
            started_at = stamp
        elif kind in response_types and started_at is not None and stamp is not None:
            total += max(0, (stamp - started_at).total_seconds() * 1000)
            started_at = None
    return max(0, int(total))


def run_comparison(prompt: str, profiles: Dict[str, Dict[str, Any]],
                   workspace: Path, base_messages: Optional[List[Dict[str, str]]] = None,
                   verbose: bool = False, api_key: Optional[str] = None,
                   comparison_id: Optional[str] = None,
                   base_session: Optional[Path] = None) -> ComparisonResult:
    """Run one prompt against several profiles, serially, and report each.

    ``profiles`` maps a profile name to the flat per-run config
    :func:`~pocketchat.config.profile_config` returns. The runs happen in that
    order -- the order the operator named them in, not one this function
    reordered by a rule of its own.

    ``base_messages`` is the context every profile starts from. It is copied
    per run, so a run that trims or extends its own context cannot change what
    the next profile sees. The prompt is appended by the session itself, the
    same way it is for a single run.

    ``base_session`` is the file those messages were read from, when a
    comparison was seeded from one. The manifest records it, because the
    message count and character count are only checkable against the file they
    came from: counts without a source are numbers a reader cannot verify.

    Returns the comparison. ``duration_ms`` is the wall time the run's model
    requests took, summed over its turns and read back from the events the
    run itself recorded, so the number a reader checks is the number the
    trace can support. ``cost_usd`` is computed from the prices the profile
    configured, and stays ``None`` when either price is absent -- a price
    nobody set is not a price of zero.
    """
    workspace = Path(workspace)
    base = copy.deepcopy(list(base_messages or []))
    base_ref = _relative(base_session, workspace) if base_session is not None else None
    comparison = comparison_id or new_comparison_id()
    runs: List[ProfileRun] = []

    for name, config in profiles.items():
        run_id = new_run_id()
        trace_path = workspace / TRACES_DIRNAME / ("%s.jsonl" % run_id)
        session = Session(
            config=dict(config),
            trace_path=trace_path,
            run_id=run_id,
            session_path=None,
            verbose=verbose,
            api_key=api_key,
            initial_messages=copy.deepcopy(base),
        )
        status = session.run([prompt])
        events = session.recorder.read_events()
        error = _terminal_error(events)
        runs.append(ProfileRun(
            profile=name,
            run_id=run_id,
            trace=_relative(trace_path, workspace),
            request=_relative(session.request_snapshots[0], workspace)
            if session.request_snapshots else "",
            status=status,
            duration_ms=_request_duration_ms(events),
            usage={
                "input_tokens": session.total_input_tokens,
                "output_tokens": session.total_output_tokens,
            },
            cost_usd=estimate_cost(config, {
                "input_tokens": session.total_input_tokens,
                "output_tokens": session.total_output_tokens,
            }),
            output_summary=session.last_output_summary,
            error=error,
        ))
        if status == "cancelled":
            # The operator interrupted this run. Stopping here is what the
            # interruption meant; the remaining profiles would otherwise be run
            # against an operator who has already asked to stop.
            break

    return ComparisonResult(
        comparison_id=comparison,
        prompt=prompt,
        base_context={
            "session": base_ref,
            "messages": len(base),
            "context_chars": sum(len(m.get("content") or "") for m in base),
        },
        runs=runs,
    )


def _terminal_error(events: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """The error the run's own terminal event recorded, if it recorded one.

    The trace is the authority on how a run ended, so this reads it back rather
    than trusting a variable the session happened to be holding when it
    returned. A comparison that reported an error the trace does not carry
    would be inventing evidence.
    """
    if not events:
        return None
    payload = events[-1].get("payload")
    if not isinstance(payload, dict):
        return None
    if events[-1].get("type") not in ("run.failed", "run.cancelled"):
        return None
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("type"), str):
        return {"type": error["type"], "message": str(error.get("message") or "")}
    return None
