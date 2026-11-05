"""Pure, bounded integrity audit for caller-supplied v2 memory records.

The caller supplies records that were already read and explicit source
observations. This module never reads or mutates a memory store, and it keeps
schema/content consistency separate from whether a source was actually seen.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, List, Tuple

from .memory_migration import MemoryMigrationError, parse_v2_record

SCHEMA = "pocketchat-memory-doctor.v1"
SCHEMA_VERSION = 1
MAX_RECORDS = 4096
MAX_SOURCES = 4096
MAX_FINDINGS = 256
MAX_REPORT_BYTES = 65536
_COVERAGE = frozenset({"complete", "partial", "missing"})
_OBSERVATION_KEYS = frozenset({
    "source_kind", "ref", "scope", "sha256", "coverage",
})
_HEX = frozenset("0123456789abcdef")


class MemoryDoctorError(ValueError):
    """An audit limit or envelope cannot be represented safely."""

    def __init__(self, message: str, *, error_type: str = "invalid_audit") -> None:
        super().__init__(message)
        self.error_type = error_type


def _limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MemoryDoctorError(
            f"{name} must be a positive integer",
            error_type="invalid_limits")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source text must be non-empty")
    return value.strip()


def _sha(value: Any) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in _HEX for char in value)):
        raise ValueError("sha256 must be lowercase hexadecimal")
    return value


def _observation(payload: Any) -> Dict[str, str]:
    if not isinstance(payload, dict) or set(payload) != _OBSERVATION_KEYS:
        raise ValueError("source observation must use the closed schema")
    source_kind = _text(payload.get("source_kind"))
    if source_kind not in {"file", "event", "url"}:
        raise ValueError("source kind is unsupported")
    coverage = payload.get("coverage")
    if coverage not in _COVERAGE:
        raise ValueError("source coverage is unsupported")
    return {
        "source_kind": source_kind,
        "ref": _text(payload.get("ref")),
        "scope": _text(payload.get("scope")),
        "sha256": _sha(payload.get("sha256")),
        "coverage": coverage,
    }


def _identity(item: Dict[str, str]) -> Tuple[str, str, str]:
    return (item["source_kind"], item["ref"], item["scope"])


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def _stable_sort_key(value: Any) -> str:
    try:
        return "json:" + _canonical_bytes(value).decode("utf-8")
    except (TypeError, ValueError):
        return "py:" + type(value).__module__ + "." + type(value).__qualname__ + ":" + repr(value)


def _bounded_iterable(values: Any, limit: int, *, error_type: str,
                      label: str) -> List[Any]:
    result = []
    try:
        iterator = iter(values)
    except TypeError:
        raise MemoryDoctorError(
            f"{label} must be iterable", error_type=error_type)
    for value in iterator:
        if len(result) == limit:
            raise MemoryDoctorError(
                f"{label} limit exceeded", error_type=error_type)
        result.append(value)
    return result


def _bounded_report(report: Dict[str, Any], max_report_bytes: int) -> Dict[str, Any]:
    if len(_canonical_bytes(report)) > max_report_bytes:
        raise MemoryDoctorError(
            "report limit exceeded", error_type="report_limit_exceeded")
    return report


def audit_memory_records(
        records: Iterable[Dict[str, Any]], *, workspace_id: str,
        source_observations: Iterable[Dict[str, Any]],
        max_records: int = MAX_RECORDS,
        max_sources: int = MAX_SOURCES,
        max_findings: int = MAX_FINDINGS,
        max_report_bytes: int = MAX_REPORT_BYTES) -> Dict[str, Any]:
    """Audit already-read v2 records without any external I/O."""
    max_records = _limit(max_records, "max_records")
    max_sources = _limit(max_sources, "max_sources")
    max_findings = _limit(max_findings, "max_findings")
    max_report_bytes = _limit(max_report_bytes, "max_report_bytes")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise MemoryDoctorError(
            "workspace_id must be non-empty text",
            error_type="invalid_workspace")
    workspace_id = workspace_id.strip()

    findings: List[Dict[str, Any]] = []
    raw_records = _bounded_iterable(
        records, max_records, label="records",
        error_type="record_limit_exceeded")
    raw_observations = _bounded_iterable(
        source_observations, max_sources, label="source observations",
        error_type="source_limit_exceeded")

    raw_records = sorted(raw_records, key=_stable_sort_key)
    raw_observations = sorted(raw_observations, key=_stable_sort_key)
    parsed_records = []
    parsed_observations = []
    for index, payload in enumerate(raw_records):
        try:
            parsed = parse_v2_record(copy.deepcopy(payload))
        except (MemoryMigrationError, TypeError, ValueError):
            findings.append({"code": "invalid_record", "record_index": index})
            continue
        parsed_records.append((index, parsed))

    for index, payload in enumerate(raw_observations):
        try:
            parsed_observations.append(_observation(payload))
        except (TypeError, ValueError):
            findings.append({
                "code": "invalid_source_observation",
                "observation_index": index,
            })

    observations: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    for index, item in enumerate(parsed_observations):
        identity = _identity(item)
        previous = observations.get(identity)
        if previous is None:
            observations[identity] = item
        elif previous["sha256"] != item["sha256"]:
            findings.append({
                "code": "source_observation_conflict",
                "observation_index": index,
            })
        elif previous["coverage"] != item["coverage"]:
            findings.append({
                "code": "source_observation_coverage_conflict",
                "observation_index": index,
            })
        else:
            findings.append({
                "code": "duplicate_source_observation",
                "observation_index": index,
            })

    accepted = []
    seen_ids = set()
    seen_revisions = set()
    for index, item in parsed_records:
        memory_id = item["memory_id"]
        revision = (memory_id, item["revision"])
        if memory_id in seen_ids:
            findings.append({"code": "duplicate_memory_id",
                             "record_index": index})
            continue
        if revision in seen_revisions:
            findings.append({"code": "duplicate_revision",
                             "record_index": index})
            continue
        seen_ids.add(memory_id)
        seen_revisions.add(revision)
        if item["workspace_id"] == workspace_id:
            accepted.append((index, item))

    counts = {
        "records": len(accepted),
        "admission_accepted": 0,
        "admission_candidate": 0,
        "admission_retired": 0,
        "admission_conflicted": 0,
        "sensitive": 0,
        "foreign_workspace_omitted": len(parsed_records) - len(accepted),
        "sources_required": 0,
        "sources_verified": 0,
        "sources_unknown": 0,
        "findings": 0,
        "omissions": 0,
    }
    for index, item in accepted:
        counts["admission_" + item["admission_status"]] += 1
        if item["sensitive"]:
            counts["sensitive"] += 1
        for ref in item["source_refs"]:
            counts["sources_required"] += 1
            observed = observations.get(
                (ref["kind"], ref["ref"], ref["scope"]))
            if observed is None:
                counts["sources_unknown"] += 1
                findings.append({
                    "code": "source_missing",
                    "record_index": index,
                })
                continue
            if observed["coverage"] != "complete":
                counts["sources_unknown"] += 1
                findings.append({
                    "code": "source_missing"
                    if observed["coverage"] == "missing"
                    else "source_coverage_partial",
                    "record_index": index,
                })
                continue
            if observed["sha256"] != ref["sha256"]:
                findings.append({
                    "code": "source_digest_mismatch",
                    "record_index": index,
                })
                continue
            counts["sources_verified"] += 1

    omissions = [
        {"code": "foreign_workspace", "record_index": index}
        for index, item in parsed_records
        if item["workspace_id"] != workspace_id
    ]
    counts["findings"] = len(findings)
    counts["omissions"] = len(omissions)
    findings = sorted(findings, key=lambda item: (
        item["code"], item.get("record_index", -1),
        item.get("observation_index", -1)))
    if len(findings) > max_findings:
        raise MemoryDoctorError(
            "finding limit exceeded", error_type="finding_limit_exceeded")
    status = "refused" if any(
        finding["code"] in {
            "invalid_record", "invalid_records", "duplicate_memory_id",
            "duplicate_revision", "invalid_source_observation",
            "invalid_source_observations", "duplicate_source_observation",
            "source_observation_conflict",
            "source_observation_coverage_conflict", "source_digest_mismatch",
        } for finding in findings) else (
            "partial" if counts["sources_unknown"] or omissions
            else "complete")
    report = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "workspace_id": workspace_id,
        "counts": counts,
        "findings": findings,
        "omissions": omissions,
    }
    return _bounded_report(report, max_report_bytes)
