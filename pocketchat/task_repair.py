"""Bounded repair for one failed command step.

The repair loop is deliberately smaller than a retry framework: a command may
run once, a caller may request at most one repair, and that repair may run the
same declared template once more. Identical failure fingerprints stop the loop;
nothing here can turn a command success into task acceptance.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

MAX_REPAIR_ATTEMPTS = 1
REPAIR_SCHEMA = "pocketchat-command-repair.v1"
_SUMMARY_KEYS = (
    "template_id", "cwd", "exit_code", "timed_out", "duration_ms",
    "stdout_bytes", "stderr_bytes", "stdout_sha256", "stderr_sha256",
    "stdout_truncated", "stderr_truncated", "trace", "task_acceptance",
)


class RepairError(ValueError):
    """A repair request or its bounded result is not trustworthy."""


@dataclass(frozen=True)
class RepairDecision:
    """An operator-visible answer to whether one bounded repair may run."""

    apply: bool
    reason: str


def _summary(command: Any) -> Dict[str, Any]:
    payload = command.to_dict() if callable(getattr(command, "to_dict", None)) else {}
    if not isinstance(payload, dict):
        raise RepairError("command evidence must be an object")
    return {key: payload.get(key) for key in _SUMMARY_KEYS if key in payload}


def failure_fingerprint(command: Any) -> str:
    """Fingerprint stable failure facts, never output preview text."""
    evidence = _summary(command)
    stable = {
        "exit_code": evidence.get("exit_code"),
        "timed_out": evidence.get("timed_out"),
        "stdout_sha256": evidence.get("stdout_sha256"),
        "stderr_sha256": evidence.get("stderr_sha256"),
        "stdout_truncated": evidence.get("stdout_truncated"),
        "stderr_truncated": evidence.get("stderr_truncated"),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_repair_evidence(root: Path, step_id: str,
                           details: Dict[str, Any]) -> str:
    """Persist only the bounded repair decision and hashes."""
    directory = Path(root) / ".pocketchat" / "task-repair"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%s.json" % step_id)
    data = (json.dumps(details, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")
    if path.exists():
        raise RepairError("repair evidence for step already exists")
    descriptor = os.open(
        str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _retry_path(path: Any, attempt: int) -> Path:
    target = Path(path)
    return target.with_name("%s.retry-%d%s" % (target.stem, attempt,
                                                target.suffix))


def run_bounded_repair(command_runner: Callable[..., Any], template_id: str,
                       trace_path: Any, run_id: str, *,
                       repair: Callable[[Dict[str, Any]], RepairDecision],
                       root: Any = None,
                       ) -> Tuple[Any, Dict[str, Any]]:
    """Run one command and, if allowed, exactly one repair retry.

    ``repair`` receives only the bounded command summary. It cannot inject a
    shell string, a new template or a new cwd: the retry uses the same template
    id and a sibling trace path. A successful retry remains command evidence,
    not task acceptance.
    """
    if not callable(command_runner):
        raise RepairError("a command runner is required")
    if not callable(repair):
        raise RepairError("a repair callback is required")

    def finish(result, details):
        if root is not None:
            _write_repair_evidence(Path(root), run_id, details)
        return result, details

    first = command_runner(template_id, trace_path, run_id)
    if getattr(first, "ok", False):
        return finish(first, {
            "schema_version": REPAIR_SCHEMA,
            "step_id": run_id,
            "attempted": False,
            "attempts": 1,
            "outcome": "succeeded",
            "task_acceptance_pass": False,
        })
    first_evidence = _summary(first)
    first_fingerprint = failure_fingerprint(first)
    decision = repair(first_evidence)
    if not isinstance(decision, RepairDecision):
        raise RepairError("repair must return RepairDecision")
    if not decision.apply:
        return finish(first, {
            "schema_version": REPAIR_SCHEMA,
            "step_id": run_id,
            "attempted": False,
            "attempts": 1,
            "outcome": "not_repairable",
            "reason": decision.reason,
            "failure_fingerprint": first_fingerprint,
            "task_acceptance_pass": False,
        })
    retry_trace = _retry_path(trace_path, 1)
    retry_run_id = "%s.retry-1" % run_id
    second = command_runner(template_id, retry_trace, retry_run_id)
    second_fingerprint = failure_fingerprint(second)
    if getattr(second, "ok", False):
        return finish(second, {
            "schema_version": REPAIR_SCHEMA,
            "step_id": run_id,
            "attempted": True,
            "attempts": 2,
            "outcome": "repaired",
            "reason": decision.reason,
            "failure_fingerprint": first_fingerprint,
            "task_acceptance_pass": False,
        })
    outcome = ("same_failure" if first_fingerprint == second_fingerprint
               else "retry_failed")
    return finish(second, {
        "schema_version": REPAIR_SCHEMA,
        "step_id": run_id,
        "attempted": True,
        "attempts": 2,
        "outcome": outcome,
        "reason": decision.reason,
        "failure_fingerprint": first_fingerprint,
        "retry_failure_fingerprint": second_fingerprint,
        "task_acceptance_pass": False,
    })
