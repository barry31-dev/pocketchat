"""Dry-run replay for saved request snapshots.

Replay reconstructs the request from the snapshot and verifies the digest. It
does not need a config, a credential or a provider: the snapshot is already the
request body that was sent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .request import REQUEST_SCHEMA_VERSION, request_digest

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TURN_PATTERN = re.compile(r"\.turn-(\d+)\.json$")


class ReplayError(Exception):
    """A saved request cannot be read or verified as a snapshot."""


def _read_snapshot(path: Path) -> Dict[str, Any]:
    target = Path(path)
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReplayError("cannot read request snapshot %s: %s" % (target, exc)) from exc
    if not isinstance(record, dict):
        raise ReplayError("%s is not a request snapshot object" % target)
    if record.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise ReplayError(
            "%s has unsupported request schema version %r"
            % (target, record.get("schema_version"))
        )
    if not isinstance(record.get("run_id"), str) or not record["run_id"]:
        raise ReplayError("%s has no run id" % target)
    if not isinstance(record.get("request"), dict):
        raise ReplayError("%s has no request object" % target)
    stored = record.get("request_sha256")
    if not isinstance(stored, str) or not _DIGEST_PATTERN.match(stored):
        raise ReplayError("%s has no valid request digest" % target)
    return record


def replay_dry_run(path: Path) -> Dict[str, Any]:
    """Rebuild one snapshot's request and compare its digest.

    ``sent`` is always false. The result carries both values when they differ,
    so the caller can show what the file claimed and what its body now hashes
    to rather than a bare "mismatch".
    """
    target = Path(path)
    record = _read_snapshot(target)
    stored = record["request_sha256"]
    recomputed = request_digest(record["request"])
    return {
        "run_id": record["run_id"],
        "request_path": str(target),
        "request_sha256": stored,
        "recomputed_sha256": recomputed,
        "match": stored == recomputed,
        "sent": False,
    }


def _turn_number(path: Path) -> int:
    match = _TURN_PATTERN.search(path.name)
    return int(match.group(1)) if match else 0


def _snapshot_paths(workspace: Path, target: str) -> List[Path]:
    """Resolve an explicit snapshot path or a run id in the current layout.

    Current snapshots live beside their trace at
    ``traces/requests/<run-id>.turn-N.json``. The legacy ``requests`` directory
    remains readable so an older local snapshot is not silently unreplayable.
    """
    workspace = Path(workspace)
    direct = Path(target)
    if direct.is_file():
        return [direct]

    name = direct.name
    if name.endswith(".json"):
        name = name[:-5]

    current = workspace / "traces" / "requests"
    paths = sorted(current.glob("%s.turn-*.json" % name), key=_turn_number)
    if paths:
        return paths

    legacy = workspace / "requests"
    exact = legacy / ("%s.json" % name)
    if exact.is_file():
        return [exact]
    paths = sorted(legacy.glob("%s.turn-*.json" % name), key=_turn_number)
    if paths:
        return paths
    raise ReplayError(
        "no request snapshot for %s under %s"
        % (target, workspace / "traces" / "requests")
    )


def replay_snapshots(workspace: Path, target: str) -> List[Dict[str, Any]]:
    """Verify one explicit snapshot or every turn recorded for one run."""
    results = [replay_dry_run(path) for path in _snapshot_paths(workspace, target)]
    run_ids = {result["run_id"] for result in results}
    if len(run_ids) != 1:
        raise ReplayError(
            "%s resolves to snapshots from more than one run: %s"
            % (target, ", ".join(sorted(run_ids)))
        )
    return results
