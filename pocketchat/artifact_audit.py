"""Bounded offline secret matching over caller-supplied artifact bytes.

The audit is deliberately pure. A caller supplies closed artifact records and
exact secret byte values; this module never discovers files, follows paths or
returns input text. Its result is evidence about those supplied bytes only.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from collections.abc import Sequence
from typing import Any

SCHEMA = "pocketchat-secret-audit.v1"
SCHEMA_VERSION = 1
MAX_ARTIFACTS = 256
MAX_SECRETS = 256
DEFAULT_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_MATCHES = 256
DEFAULT_MAX_REPORT_BYTES = 64 * 1024
MIN_MAX_REPORT_BYTES = 512
ARTIFACT_KINDS = frozenset({
    "request", "trace", "session", "comparison", "execution_report",
    "checkpoint",
})
_ARTIFACT_KEYS = frozenset({
    "artifact_id", "kind", "path", "entry_type", "content",
})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class ArtifactAuditError(ValueError):
    """An invocation limit cannot support the closed report contract."""


def _canonical_sha256(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _limit(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactAuditError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ArtifactAuditError(
            f"{name} must be between {low} and {high}")
    return value


def _safe_path(value: Any) -> bool:
    if (not isinstance(value, str) or not value or "\x00" in value
            or "\\" in value or value.startswith(("/", "~"))
            or re.match(r"^[A-Za-z]:", value)):
        return False
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    normalized = posixpath.normpath(value)
    return normalized == value


def _closed(record: Any, allowed: set[str]) -> bool:
    return isinstance(record, dict) and set(record) == allowed


def _base_report(status: str, artifact_count: int, secret_count: int,
                 matches: list[dict[str, Any]],
                 omissions: list[dict[str, Any]],
                 limits: dict[str, int]) -> dict[str, Any]:
    report = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "artifact_count": artifact_count,
        "secret_count": secret_count,
        "match_count": len(matches),
        "matches": matches,
        "omissions": omissions,
        "limits": limits,
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def _refusal(reason: str, artifact_count: int, secret_count: int,
             limits: dict[str, int]) -> dict[str, Any]:
    return _base_report("refused", artifact_count, secret_count, [],
                        [{"reason": reason, "count": 1}], limits)


def _secret_bytes(values: Sequence[Any]) -> tuple[list[bytes], bool]:
    if not isinstance(values, list) or len(values) > MAX_SECRETS:
        return [], False
    result: list[bytes] = []
    for value in values:
        if isinstance(value, bytes):
            data = value
        elif isinstance(value, str):
            try:
                data = value.encode("utf-8")
            except UnicodeError:
                return [], False
        else:
            return [], False
        if not data:
            return [], False
        result.append(data)
    return result, True


def _parse_artifacts(values: Sequence[Any], secrets: Sequence[bytes],
                     max_artifact_bytes: int,
                     max_total_bytes: int) -> tuple[list[dict[str, Any]], bool, str]:
    if not isinstance(values, list) or len(values) > MAX_ARTIFACTS:
        return [], False, "artifact_limit"
    parsed: list[dict[str, Any]] = []
    ids: set[str] = set()
    paths: set[str] = set()
    total = 0
    for raw in values:
        if not _closed(raw, _ARTIFACT_KEYS):
            return [], False, "invalid_artifact"
        identifier = raw["artifact_id"]
        if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                or identifier in ids):
            return [], False, "invalid_artifact"
        encoded_id = identifier.encode("ascii")
        if any(secret in encoded_id for secret in secrets):
            return [], False, "invalid_artifact"
        kind = raw["kind"]
        if not isinstance(kind, str) or kind not in ARTIFACT_KINDS:
            return [], False, "invalid_artifact"
        path = raw["path"]
        if not _safe_path(path) or path in paths:
            return [], False, "invalid_artifact"
        if raw["entry_type"] != "regular_file":
            return [], False, "invalid_artifact"
        content = raw["content"]
        if not isinstance(content, bytes):
            return [], False, "invalid_artifact"
        if len(content) > max_artifact_bytes:
            return [], False, "artifact_byte_limit"
        total += len(content)
        if total > max_total_bytes:
            return [], False, "total_byte_limit"
        ids.add(identifier)
        paths.add(path)
        parsed.append({
            "artifact_id": identifier,
            "kind": kind,
            "content": content,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
        })
    return parsed, True, ""


def audit_artifacts(artifacts: Any, secret_values: Any, *,
                    max_artifacts: int = MAX_ARTIFACTS,
                    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
                    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                    max_matches: int = DEFAULT_MAX_MATCHES,
                    max_report_bytes: int = DEFAULT_MAX_REPORT_BYTES
                    ) -> dict[str, Any]:
    """Audit supplied bytes without I/O and return a closed bounded report."""
    max_artifacts = _limit(max_artifacts, "max_artifacts", 1, MAX_ARTIFACTS)
    max_artifact_bytes = _limit(
        max_artifact_bytes, "max_artifact_bytes", 1, 1 << 30)
    max_total_bytes = _limit(
        max_total_bytes, "max_total_bytes", 1, 1 << 30)
    max_matches = _limit(max_matches, "max_matches", 1, MAX_ARTIFACTS)
    max_report_bytes = _limit(
        max_report_bytes, "max_report_bytes", MIN_MAX_REPORT_BYTES, 1 << 30)
    limits = {
        "max_artifacts": max_artifacts,
        "max_artifact_bytes": max_artifact_bytes,
        "max_total_bytes": max_total_bytes,
        "max_matches": max_matches,
        "max_report_bytes": max_report_bytes,
    }
    artifact_count = len(artifacts) if isinstance(artifacts, list) else 0
    secret_count = len(secret_values) if isinstance(secret_values, list) else 0
    secrets, secrets_ok = _secret_bytes(secret_values)
    if not secrets_ok:
        return _refusal("invalid_secret", artifact_count, secret_count, limits)
    parsed, artifacts_ok, reason = _parse_artifacts(
        artifacts, secrets, max_artifact_bytes, max_total_bytes)
    if not artifacts_ok:
        return _refusal(reason, artifact_count, secret_count, limits)
    if len(parsed) > max_artifacts:
        return _refusal("artifact_limit", artifact_count, secret_count, limits)

    found: set[tuple[str, str, str]] = set()
    for item in parsed:
        for secret in secrets:
            if secret in item["content"]:
                found.add((item["artifact_id"], item["kind"],
                           item["artifact_sha256"]))
                break
    if len(found) > max_matches:
        return _refusal("match_limit", artifact_count, secret_count, limits)
    matches = [{
        "artifact_id": identifier,
        "location_category": category,
        "artifact_sha256": digest,
    } for identifier, category, digest in sorted(found)]
    status = "found" if matches else "clean"
    report = _base_report(status, artifact_count, secret_count, matches,
                          [], limits)
    if len(json.dumps(report, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")) > max_report_bytes:
        return _refusal("report_limit", artifact_count, secret_count, limits)
    return report
