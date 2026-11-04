"""Pure verification of supplied evidence observations.

This module does not read artifacts, inspect paths, or contact a provider. A
caller supplies descriptors and explicit relations; the result can prove only
that those observations are internally consistent. It cannot prove that the
underlying files still exist or were read by this process.
"""

from __future__ import annotations

import json
import posixpath
from typing import Any, Dict, Iterable, List, Tuple

SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketchat-evidence-links.v1"
MAX_ARTIFACTS = 256
MAX_RELATIONS = 1024
MAX_REPORT_BYTES = 64 * 1024

ARTIFACT_KINDS = frozenset({
    "session",
    "trace",
    "request",
    "comparison",
    "execution_report",
    "checkpoint",
})
RELATION_KINDS = frozenset({
    "same-run",
    "same-run-turn",
    "contains-run",
    "binds-spec",
    "binds-plan",
    "binds-checkpoint",
})
SEMANTIC_SCOPES = frozenset({
    "canonical-json",
    "request-body-canonical",
    "trace-events",
})
COVERAGE_VALUES = frozenset({"complete", "partial"})
IDENTITY_FIELDS = frozenset({
    "run_id", "run_ids", "turn", "conversation_id", "task_id",
    "spec_sha256", "plan_sha256", "comparison_id",
})
DIGEST_FIELDS = ("whole_file_sha256", "observed_whole_file_sha256",
                 "semantic_sha256")


def _is_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _safe_workspace_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/") or value.startswith("~"):
        return False
    normalized = posixpath.normpath(value)
    return normalized == value and normalized != "." and not normalized.startswith("../")


def _canonical_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8"))


def _finding(identifier: str, reason: str, detail: str) -> Dict[str, str]:
    return {"id": identifier, "reason": reason, "detail": detail}


def _artifact_shape(record: Any, index: int) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Validate one descriptor and return its bounded public projection."""
    findings: List[Dict[str, str]] = []
    fallback_id = "artifact[%d]" % index
    if not isinstance(record, dict):
        return {"id": fallback_id}, [_finding(fallback_id, "invalid_artifact",
                                              "artifact descriptor is not an object")]
    artifact_id = record.get("id")
    if not isinstance(artifact_id, str) or not artifact_id:
        artifact_id = fallback_id
        findings.append(_finding(artifact_id, "invalid_id",
                                 "artifact id must be a non-empty string"))
    kind = record.get("kind")
    if kind not in ARTIFACT_KINDS:
        findings.append(_finding(artifact_id, "invalid_kind",
                                 "artifact kind is not in the closed set"))
    path = record.get("path")
    if not _safe_workspace_path(path):
        findings.append(_finding(artifact_id, "unsafe_path",
                                 "path must be normalized and workspace-relative"))
    for key in DIGEST_FIELDS:
        if not _is_digest(record.get(key)):
            findings.append(_finding(artifact_id, "invalid_digest",
                                     "%s must be lowercase SHA-256 hex" % key))
    declared = record.get("whole_file_sha256")
    observed = record.get("observed_whole_file_sha256")
    if _is_digest(declared) and _is_digest(observed) and declared != observed:
        findings.append(_finding(artifact_id, "digest_mismatch",
                                 "declared and observed whole-file digests differ"))
    semantic = record.get("semantic_sha256")
    semantic_scope = record.get("semantic_scope")
    if semantic == declared or semantic_scope == "whole-file":
        findings.append(_finding(artifact_id, "digest_scope_confusion",
                                 "semantic digest must not reuse the whole-file digest"))
    elif semantic_scope not in SEMANTIC_SCOPES:
        findings.append(_finding(artifact_id, "invalid_semantic_scope",
                                 "semantic scope is not in the closed set"))
    coverage = record.get("read_coverage")
    if coverage not in COVERAGE_VALUES:
        findings.append(_finding(artifact_id, "invalid_read_coverage",
                                 "read coverage must be complete or partial"))
    identity = record.get("identity")
    if not isinstance(identity, dict):
        findings.append(_finding(artifact_id, "invalid_identity",
                                 "identity must be an object"))
        identity = {}
    else:
        unexpected = sorted(set(identity) - IDENTITY_FIELDS)
        if unexpected:
            findings.append(_finding(artifact_id, "unexpected_identity_field",
                                     "identity contains fields outside the closed set"))
        identity = {key: value for key, value in identity.items()
                    if key in IDENTITY_FIELDS}
    required = record.get("required", True)
    if not isinstance(required, bool):
        findings.append(_finding(artifact_id, "invalid_required",
                                 "required must be a boolean"))
        required = True
    projected = {
        "id": artifact_id,
        "kind": kind if isinstance(kind, str) else "invalid",
        "path": path if isinstance(path, str) else "",
        "whole_file_sha256": declared if _is_digest(declared) else "",
        "observed_whole_file_sha256": observed if _is_digest(observed) else "",
        "semantic_sha256": semantic if _is_digest(semantic) else "",
        "semantic_scope": semantic_scope if isinstance(semantic_scope, str) else "",
        "identity": identity,
        "read_coverage": coverage if isinstance(coverage, str) else "invalid",
        "required": required,
        "present": record.get("present", True) is not False,
    }
    return projected, findings


def _relation_shape(record: Any, index: int) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    findings: List[Dict[str, str]] = []
    fallback_id = "relation[%d]" % index
    if not isinstance(record, dict):
        return {"id": fallback_id}, [_finding(fallback_id, "invalid_relation",
                                              "relation is not an object")]
    relation_id = record.get("id")
    if not isinstance(relation_id, str) or not relation_id:
        relation_id = fallback_id
        findings.append(_finding(relation_id, "invalid_id",
                                 "relation id must be a non-empty string"))
    source = record.get("source")
    target = record.get("target")
    if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
        findings.append(_finding(relation_id, "invalid_endpoints",
                                 "relation endpoints must name artifact ids"))
    if source == target:
        findings.append(_finding(relation_id, "self_relation",
                                 "relation endpoints must be different artifacts"))
    kind = record.get("kind")
    if kind not in RELATION_KINDS:
        findings.append(_finding(relation_id, "invalid_relation_kind",
                                 "relation kind is not in the closed set"))
    fields = record.get("fields")
    if not isinstance(fields, list) or not fields or \
            any(not isinstance(field, str) or not field for field in fields) or \
            len(fields) != len(set(fields)):
        findings.append(_finding(relation_id, "invalid_fields",
                                 "fields must be a non-empty unique string list"))
    required = record.get("required", True)
    if not isinstance(required, bool):
        findings.append(_finding(relation_id, "invalid_required",
                                 "required must be a boolean"))
        required = True
    return {
        "id": relation_id,
        "source": source if isinstance(source, str) else "",
        "target": target if isinstance(target, str) else "",
        "kind": kind if isinstance(kind, str) else "invalid",
        "fields": fields if isinstance(fields, list) else [],
        "required": required,
    }, findings


def _lookup(artifact: Dict[str, Any], field: str) -> Any:
    if field in artifact:
        return artifact.get(field)
    return (artifact.get("identity") or {}).get(field)


def _compare(relation: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    source = by_id[relation["source"]]
    target = by_id[relation["target"]]
    if relation["kind"] in {"same-run", "same-run-turn", "contains-run"}:
        source_ids = source["identity"].get("run_id")
        target_ids = target["identity"].get("run_id")
        if relation["kind"] == "contains-run" and source_ids is None:
            source_ids = source["identity"].get("run_ids")
        if isinstance(source_ids, list):
            matches = target_ids in source_ids
        else:
            matches = bool(source_ids) and source_ids == target_ids
        if not matches:
            findings.append(_finding(relation["id"], "identity_mismatch",
                                     "run identity does not match"))
    for field in relation["fields"]:
        if field == "run_id" and relation["kind"] in {
                "same-run", "same-run-turn", "contains-run"}:
            continue
        if field == "turn" and relation["kind"] == "same-run-turn":
            target_turn = target["identity"].get("turn")
            if isinstance(target_turn, int) and target_turn >= 1:
                continue
        source_value = _lookup(source, field)
        target_value = _lookup(target, field)
        if source_value is None or target_value is None:
            findings.append(_finding(relation["id"], "unobserved_field",
                                     "relation field is absent on an endpoint: %s" % field))
        elif source_value != target_value:
            findings.append(_finding(relation["id"], "identity_mismatch",
                                     "compared field differs: %s" % field))
    return findings


def _build_report(status: str, artifact_results: Iterable[Dict[str, Any]],
                  edge_results: Iterable[Dict[str, Any]], unknowns: Iterable[Dict[str, str]],
                  omissions: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "artifacts": sorted(list(artifact_results), key=lambda item: item["id"]),
        "edges": sorted(list(edge_results), key=lambda item: item["id"]),
        "unknowns": sorted(list(unknowns),
                           key=lambda item: (item["id"], item["reason"], item["detail"])),
        "omissions": sorted(list(omissions),
                            key=lambda item: (item["id"], item["reason"], item["detail"])),
        "limits": {"artifacts": MAX_ARTIFACTS, "relations": MAX_RELATIONS,
                   "report_bytes": MAX_REPORT_BYTES},
    }


def verify_evidence_links(artifacts: Any, relations: Any) -> Dict[str, Any]:
    """Validate explicit evidence observations without reading their artifacts."""
    if not isinstance(artifacts, list) or not isinstance(relations, list):
        return _build_report("refused", [], [], [
            _finding("input", "invalid_input", "artifacts and relations must be lists")
        ], [])
    if len(artifacts) > MAX_ARTIFACTS:
        return _build_report("refused", [], [], [
            _finding("artifacts", "artifact_limit",
                     "artifact count exceeds %d" % MAX_ARTIFACTS)
        ], [])
    if len(relations) > MAX_RELATIONS:
        return _build_report("refused", [], [], [
            _finding("relations", "relation_limit",
                     "relation count exceeds %d" % MAX_RELATIONS)
        ], [])

    projected_artifacts: List[Dict[str, Any]] = []
    unknown_findings: List[Dict[str, str]] = []
    omissions: List[Dict[str, str]] = []
    for index, record in enumerate(artifacts):
        projected, findings = _artifact_shape(record, index)
        if findings:
            projected["status"] = "refused"
            projected["reason"] = findings[0]["reason"]
        elif not projected["present"] and not projected["required"]:
            projected["status"] = "omitted"
            projected["reason"] = "optional_artifact_not_observed"
            omissions.append(_finding(projected["id"],
                                      "optional_artifact_not_observed",
                                      "optional artifact was not observed"))
        elif projected["read_coverage"] == "partial":
            projected["status"] = "partial"
            projected["reason"] = "incomplete_read_coverage"
            omissions.append(_finding(projected["id"], "incomplete_read_coverage",
                                      "artifact observation is explicitly partial"))
        else:
            projected["status"] = "complete"
            projected["reason"] = None
        projected_artifacts.append(projected)
        unknown_findings.extend(findings)

    by_id: Dict[str, Dict[str, Any]] = {}
    for projected in projected_artifacts:
        artifact_id = projected["id"]
        if artifact_id in by_id:
            unknown_findings.append(_finding(artifact_id, "duplicate_id",
                                             "artifact id occurs more than once"))
        elif projected["present"]:
            by_id[artifact_id] = projected

    edge_results: List[Dict[str, Any]] = []
    seen_relation_ids = set()
    for index, record in enumerate(relations):
        projected, findings = _relation_shape(record, index)
        unknown_findings.extend(findings)
        relation_id = projected["id"]
        if relation_id in seen_relation_ids:
            unknown_findings.append(_finding(relation_id, "duplicate_relation_id",
                                             "relation id occurs more than once"))
        seen_relation_ids.add(relation_id)
        missing = [endpoint for endpoint in (projected["source"], projected["target"])
                   if endpoint not in by_id]
        if missing:
            unknown_findings.append(_finding(relation_id, "orphan_relation",
                                             "relation names an unknown artifact"))
            edge_results.append({"id": relation_id, "status": "unknown",
                                 "reason": "orphan_relation"})
            continue
        if projected["source"] == projected["target"]:
            edge_results.append({"id": relation_id, "status": "refused",
                                 "reason": "self_relation"})
            continue
        edge_findings = _compare(projected, by_id)
        unknown_findings.extend(edge_findings)
        if edge_findings:
            edge_results.append({"id": relation_id, "status": "refused",
                                 "reason": edge_findings[0]["reason"]})
        elif projected["required"]:
            edge_results.append({"id": relation_id, "status": "complete",
                                 "reason": None})
        else:
            edge_results.append({"id": relation_id, "status": "omitted",
                                 "reason": "optional_relation_not_covered"})
            omissions.append(_finding(relation_id, "optional_relation_not_covered",
                                      "optional relation is not required for completeness"))

    for projected in projected_artifacts:
        if projected["required"] and not projected["present"]:
            unknown_findings.append(_finding(projected["id"],
                                             "missing_required_artifact",
                                             "required artifact id was not supplied"))

    omission_reasons = {"incomplete_read_coverage",
                        "optional_artifact_not_observed",
                        "optional_relation_not_covered"}
    has_refusal = any(item["reason"] not in omission_reasons
                      for item in unknown_findings)
    has_refusal = has_refusal or any(edge["status"] in {"refused", "unknown"}
                                     for edge in edge_results)
    has_omission = bool(omissions)
    status = "refused" if has_refusal else ("partial" if has_omission else "complete")

    report = _build_report(status, projected_artifacts, edge_results,
                           unknown_findings, omissions)
    if _canonical_size(report) > MAX_REPORT_BYTES:
        return _build_report("refused", [], [], [
            _finding("report", "report_limit", "report exceeds %d bytes" % MAX_REPORT_BYTES)
        ], [])
    return report
