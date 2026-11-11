"""Read-only release-acceptance preflight over supplied observations.

The evaluator never invokes a provider, opens a network connection, reads a
release artifact, creates a tag or publishes anything. It answers one bounded
question: which declared release gates are supported by the observation
record the caller supplied, and which remain pending, unknown or blocked?
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

SCHEMA = "pocketchat-release-acceptance-preflight.v1"
OBSERVATION_SCHEMA = "pocketchat-release-acceptance-observations.v1"
SOURCE_VERSION = "1.2.0"
GATES = (
    "offline_fixture",
    "exact_ci",
    "real_provider",
    "manual_tty",
    "publication_authority",
    "publication",
    "public_install_readback",
)
BLOCKER_STATES = ("unknown", "not_run", "failed")
BLOCKER_ACTIONS = {
    "offline_fixture": "collect_exact_wheel_offline_fixture_evidence",
    "exact_ci": "record_exact_candidate_ci",
    "real_provider": "run_authorized_real_provider_acceptance",
    "manual_tty": "run_manual_trusted_terminal_acceptance",
    "publication_authority": "obtain_exact_tag_release_authorization",
    "publication": "publish_the_exact_authorized_artifact",
    "public_install_readback": "install_and_read_back_the_public_artifact",
}
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReleasePreflightError(ValueError):
    """The supplied observation record cannot support a closed report."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ReleasePreflightError("invalid_observation") from exc


def _object(value: Any, required) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(required):
        raise ReleasePreflightError("invalid_observation")
    _canonical(value)
    return value


def _text(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 128
            or value != value.strip() or not value.isprintable()):
        raise ReleasePreflightError("invalid_observation")
    return value


def _digest(value: Any, pattern) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ReleasePreflightError("invalid_observation")
    return value


def _release(value: Any) -> Dict[str, Any]:
    row = _object(value, ("version", "source_sha", "wheel_sha256"))
    version = _text(row["version"])
    if version != SOURCE_VERSION:
        raise ReleasePreflightError("identity_mismatch")
    return {
        "version": version,
        "source_sha": _digest(row["source_sha"], SHA40),
        "wheel_sha256": _digest(row["wheel_sha256"], SHA256),
    }


def _gate(gate: str, value: Any, release: Dict[str, Any]) -> Dict[str, Any]:
    required = {
        "offline_fixture": (
            "version", "source_sha", "wheel_sha256", "evidence_sha256"),
        "exact_ci": (
            "source_sha", "run_id", "check_count", "evidence_sha256"),
        "real_provider": (
            "source_sha", "wheel_sha256", "provider_scope", "model_scope",
            "evidence_sha256"),
        "manual_tty": (
            "source_sha", "wheel_sha256", "operator_scope", "evidence_sha256"),
        "publication_authority": (
            "source_sha", "wheel_sha256", "action", "authority_sha256"),
        "publication": (
            "source_sha", "wheel_sha256", "tag", "evidence_sha256"),
        "public_install_readback": (
            "version", "wheel_sha256", "evidence_sha256"),
    }[gate]
    row = _object(value, required)
    result: Dict[str, Any] = {}
    for key in required:
        value = row[key]
        if key in ("evidence_sha256", "authority_sha256"):
            result[key] = _digest(value, SHA256)
        elif key == "source_sha":
            result[key] = _digest(value, SHA40)
        elif key == "wheel_sha256":
            result[key] = _digest(value, SHA256)
        elif key == "version":
            result[key] = _text(value)
        elif key in ("provider_scope", "model_scope", "operator_scope", "tag"):
            _text(value)
        elif key in ("run_id", "check_count"):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ReleasePreflightError("invalid_observation")
            result[key] = value
        elif key == "action":
            _text(value)
        else:
            raise ReleasePreflightError("invalid_observation")

    if (gate in ("offline_fixture", "public_install_readback")
            and result["version"] != release["version"]):
        raise ReleasePreflightError("identity_mismatch")
    if "source_sha" in result and result["source_sha"] != release["source_sha"]:
        raise ReleasePreflightError("identity_mismatch")
    if "wheel_sha256" in result and result["wheel_sha256"] != release["wheel_sha256"]:
        raise ReleasePreflightError("identity_mismatch")
    if gate == "exact_ci" and result["check_count"] != 7:
        raise ReleasePreflightError("identity_mismatch")
    if gate == "publication_authority" and row["action"] != "tag_release":
        raise ReleasePreflightError("identity_mismatch")
    if gate == "publication" and row["tag"] != "v" + release["version"]:
        raise ReleasePreflightError("identity_mismatch")
    return result


def _blockers(value: Any) -> Dict[str, str]:
    if not isinstance(value, list):
        raise ReleasePreflightError("invalid_observation")
    result = {}
    for raw in value:
        row = _object(raw, ("gate", "state"))
        gate = row["gate"]
        state = row["state"]
        if gate not in GATES or state not in BLOCKER_STATES or gate in result:
            raise ReleasePreflightError("invalid_observation")
        result[gate] = state
    return result


def evaluate_release_acceptance(payload: Any) -> Dict[str, Any]:
    """Return one closed report without side effects or inferred passes."""
    data = _object(
        payload, ("schema_version", "release", "evidence", "blockers"))
    if data["schema_version"] != OBSERVATION_SCHEMA:
        raise ReleasePreflightError("invalid_observation")
    release = _release(data["release"])
    evidence = data["evidence"]
    if not isinstance(evidence, dict) or set(evidence) - set(GATES):
        raise ReleasePreflightError("invalid_observation")
    blockers = _blockers(data["blockers"])
    if set(evidence) & set(blockers):
        raise ReleasePreflightError("invalid_observation")
    parsed = {gate: _gate(gate, evidence[gate], release)
              for gate in GATES if gate in evidence}

    rows: List[Dict[str, Any]] = []
    reached_missing = False
    for gate in GATES:
        if gate in parsed:
            state = "satisfied"
            if reached_missing:
                raise ReleasePreflightError("evidence_order")
        elif gate in blockers:
            state = blockers[gate]
            reached_missing = True
        else:
            state = "pending"
            reached_missing = True
        rows.append({"gate": gate, "state": state})

    pending = [row["gate"] for row in rows
               if row["state"] in ("pending", "not_run")]
    unknown = [row["gate"] for row in rows if row["state"] == "unknown"]
    blocked = [row["gate"] for row in rows if row["state"] == "failed"]
    not_run = [row["gate"] for row in rows if row["state"] == "not_run"]
    if blocked:
        status = "blocked"
    elif unknown:
        status = "unknown"
    elif pending:
        status = "pending"
    else:
        status = "complete"
    next_actions = [BLOCKER_ACTIONS[row["gate"]] for row in rows
                    if row["state"] != "satisfied" and row["gate"] in BLOCKER_ACTIONS]
    if status == "complete":
        next_actions = []
    result = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": status,
        "ok": status == "complete",
        "scope": "supplied_observations",
        "files_rechecked": False,
        "release": release,
        "gates": rows,
        "evidence": parsed,
        "pending": pending,
        "unknown": unknown,
        "blocked": blocked,
        "not_run": not_run,
        "next_actions": next_actions,
    }
    result["report_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    if len(_canonical(result)) > 64 * 1024:
        raise ReleasePreflightError("report_limit")
    return result


def _platform_refusal() -> Dict[str, Any]:
    return {"ok": False, "status": "refused",
            "scope": "supplied_observations", "files_rechecked": False,
            "reason": "unsupported_platform", "source_sha256": None}


def _attach_source_digest(result: Dict[str, Any], digest: str) -> Dict[str, Any]:
    """Bind the exact input bytes and make the report digest self-checking."""
    result = dict(result)
    result["source_sha256"] = digest
    result.pop("report_sha256", None)
    result["report_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    if len(_canonical(result)) > 64 * 1024:
        raise ReleasePreflightError("report_limit")
    return result


def verify_release_acceptance(target: str, workspace=None) -> Dict[str, Any]:
    """Read one explicit regular observation file and evaluate it read-only."""
    from .acceptance import AcceptanceError, _ReadIssue, _decode, _read_file
    if (os.open not in os.supports_dir_fd
            or os.stat not in os.supports_dir_fd
            or any(not hasattr(os, flag) for flag in
                   ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))):
        return _platform_refusal()
    try:
        data = _read_file(Path(workspace) if workspace is not None else Path.cwd(),
                          target, 64 * 1024)
    except _ReadIssue as exc:
        return {"ok": False, "status": "refused", "scope": "supplied_observations",
                "files_rechecked": False, "reason": exc.reason,
                "source_sha256": None}
    except (AcceptanceError, OSError, ValueError):
        return {"ok": False, "status": "refused", "scope": "supplied_observations",
                "files_rechecked": False, "reason": "unsafe_input_path",
                "source_sha256": None}
    digest = hashlib.sha256(data).hexdigest()
    try:
        value = _decode(data)
        result = evaluate_release_acceptance(value)
    except (AcceptanceError, ReleasePreflightError) as exc:
        reason = getattr(exc, "reason", "invalid_json")
        return {"ok": False, "status": "refused", "scope": "supplied_observations",
                "files_rechecked": False, "reason": reason,
                "source_sha256": digest}
    return _attach_source_digest(result, digest)
