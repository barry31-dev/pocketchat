"""Pure, bounded integrity checks for caller-supplied session records.

This module receives records that a caller has already read. It does not open
the session path, repair a malformed line, execute a command, contact a
provider, or return message content. A complete report therefore means only
that the supplied record stream is internally consistent.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketchat-session-integrity.v1"
MAX_FINDINGS = 64
MAX_OMISSIONS = 64
MAX_REPORT_BYTES = 65536
MAX_RECORDS_INSPECTED = 128
MESSAGE_ROLES = frozenset({"user", "assistant"})
PARTIAL_KEY = "_audit_read_coverage"
TURN_RE = re.compile(r"^turn_(\d{6})$")


def _finding(record: Any, code: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"record": record, "code": code}
    if context:
        item["context"] = context
    return item


def _canonical_size(value: Any) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False).encode("utf-8"))


def _report(status: str, findings: List[Dict[str, Any]],
            omissions: List[Dict[str, Any]], counts: Dict[str, int]) -> Dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "counts": counts,
        "findings": findings,
        "omissions": omissions,
        "limits": {
            "max_findings": MAX_FINDINGS,
            "max_omissions": MAX_OMISSIONS,
            "max_bytes": MAX_REPORT_BYTES,
        },
    }


def _positive_limit(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(name)
    return value


def _header_finding(record: Dict[str, Any], index: int) -> str | None:
    if not isinstance(record, dict):
        return "invalid_header"
    if "session" not in record:
        return "missing_header"
    for key in ("session", "trace", "created", "pocketchat_version"):
        if key in record and (not isinstance(record[key], str) or not record[key].strip()):
            return "invalid_header_identity"
    if "schema_version" not in record:
        return None
    if record["schema_version"] is None:
        return "invalid_schema"
    if record["schema_version"] != "1":
        return "unsupported_schema"
    for key in ("conversation_id", "branch_id", "parent_branch_id", "parent_turn_id"):
        if key in record and (not isinstance(record[key], str) or not record[key].strip()):
            return "invalid_header_identity"
    return None


def _message_finding(record: Any) -> str | None:
    if not isinstance(record, dict):
        return "invalid_message"
    if record.get("role") not in MESSAGE_ROLES:
        return "invalid_role"
    if not isinstance(record.get("content"), str):
        return "invalid_content"
    return None


def _tool_context_findings(record: Dict[str, Any], index: int) -> List[Dict[str, Any]]:
    value = record.get("tool_context")
    if value is None:
        return []
    if not isinstance(value, list):
        return [_finding(index, "invalid_tool_context")]
    findings: List[Dict[str, Any]] = []
    pending = set()
    seen_calls = set()
    for item_index, item in enumerate(value):
        if not isinstance(item, dict):
            findings.append(_finding(index, "invalid_tool_context",
                                     {"item": item_index}))
            continue
        role = item.get("role")
        if role == "assistant":
            content = item.get("content")
            calls = item.get("tool_calls")
            if not isinstance(content, str) or not isinstance(calls, list):
                findings.append(_finding(index, "invalid_tool_context",
                                         {"item": item_index}))
                continue
            if not calls:
                findings.append(_finding(index, "missing_tool_terminal",
                                         {"item": item_index}))
                continue
            for call in calls:
                if not isinstance(call, dict):
                    findings.append(_finding(index, "invalid_tool_context",
                                             {"item": item_index}))
                    continue
                call_id = call.get("id")
                function = call.get("function")
                if (not isinstance(call_id, str) or not call_id.strip()
                        or call.get("type") != "function"
                        or not isinstance(function, dict)
                        or not isinstance(function.get("name"), str)
                        or not function["name"].strip()
                        or not isinstance(function.get("arguments"), str)):
                    findings.append(_finding(index, "invalid_tool_context",
                                             {"item": item_index}))
                    continue
                if call_id in seen_calls:
                    findings.append(_finding(index, "duplicate_tool_call",
                                             {"item": item_index}))
                    continue
                seen_calls.add(call_id)
                pending.add(call_id)
        elif role == "tool":
            result_id = item.get("tool_call_id")
            if (not isinstance(result_id, str) or not result_id.strip()
                    or not isinstance(item.get("content"), str)
                    or not isinstance(item.get("name"), str)):
                findings.append(_finding(index, "invalid_tool_context",
                                         {"item": item_index}))
                continue
            if result_id not in pending:
                findings.append(_finding(index, "unknown_tool_result",
                                         {"item": item_index}))
                continue
            pending.remove(result_id)
        else:
            findings.append(_finding(index, "invalid_tool_context",
                                     {"item": item_index}))
    if pending:
        findings.append(_finding(index, "unresolved_tool_call",
                                 {"count": len(pending)}))
    return findings


def _coverage_finding(record: Any, index: int) -> Tuple[bool, Dict[str, Any] | None]:
    if not isinstance(record, dict) or PARTIAL_KEY not in record:
        return False, None
    value = record[PARTIAL_KEY]
    if value == "partial":
        return True, None
    return True, _finding(index, "invalid_read_coverage")


def _prepare_counts(records: List[Any]) -> Dict[str, int]:
    return {
        "records": len(records),
        "messages": max(0, len(records) - 1),
        "partial_records": 0,
    }


def audit_session_records(records: Any, *, max_findings: int = MAX_FINDINGS,
                          max_omissions: int = MAX_OMISSIONS,
                          max_bytes: int = MAX_REPORT_BYTES) -> Dict[str, Any]:
    """Audit already-read session records without touching the outside world.

    ``records`` is the ordered list a session reader would return: a header
    followed by message records. Modern records may carry a derived
    ``turn_id`` and provider-shaped ``tool_context``. Legacy records without
    ``turn_id`` are accepted with ordinal turn ids, as the existing reader
    does. A record carrying ``_audit_read_coverage='partial'`` marks an
    explicitly partial read; any other value is refused.
    """
    max_findings = _positive_limit(max_findings, "max_findings")
    max_omissions = _positive_limit(max_omissions, "max_omissions")
    max_bytes = _positive_limit(max_bytes, "max_bytes")
    if not isinstance(records, list):
        raise ValueError("records")

    findings: List[Dict[str, Any]] = []
    omissions: List[Dict[str, Any]] = []
    counts = _prepare_counts(records)
    if not records:
        findings.append(_finding(0, "missing_header"))
    else:
        header_code = _header_finding(records[0], 0)
        if header_code:
            findings.append(_finding(0, header_code))
        turn_number = 0
        previous_turn = 0
        seen_turns = set()
        inspected = records[1:MAX_RECORDS_INSPECTED]
        if len(records) > MAX_RECORDS_INSPECTED:
            omissions.append({
                "record": None,
                "code": "record_limit",
                "count": len(records) - MAX_RECORDS_INSPECTED,
            })
        for index, record in enumerate(inspected, 1):
            code = _message_finding(record)
            if code:
                findings.append(_finding(index, code))
            if not isinstance(record, dict):
                continue
            partial, coverage_code = _coverage_finding(record, index)
            if partial:
                counts["partial_records"] += 1
            if coverage_code:
                findings.append(coverage_code)
            if record.get("role") not in MESSAGE_ROLES:
                continue
            turn_id = record.get("turn_id")
            if turn_id is None:
                turn_id = "turn_%06d" % (turn_number + 1)
            if not isinstance(turn_id, str) or TURN_RE.match(turn_id) is None:
                findings.append(_finding(index, "invalid_turn"))
            else:
                turn_number = int(TURN_RE.match(turn_id).group(1))
                if turn_id in seen_turns:
                    findings.append(_finding(index, "duplicate_turn"))
                elif turn_number <= previous_turn:
                    findings.append(_finding(index, "regressing_turn"))
                elif turn_number != len(seen_turns) + 1:
                    findings.append(_finding(index, "nonconsecutive_turn"))
                seen_turns.add(turn_id)
                previous_turn = turn_number
            if "tool_context" in record:
                if record.get("role") != "assistant":
                    findings.append(_finding(index, "tool_context_on_non_assistant"))
                findings.extend(_tool_context_findings(record, index))

    if findings:
        status = "refused"
    elif counts["partial_records"] or omissions:
        status = "partial"
    else:
        status = "complete"

    total_issues = len(findings) + len(omissions)
    kept = findings[:max_findings]
    remaining = total_issues - len(kept)
    if remaining > 0:
        omissions = [{
            "record": None,
            "code": "evidence_omitted",
            "count": remaining,
        }][:max_omissions]
    else:
        omissions = omissions[:max_omissions]
    if remaining > 0 and status == "complete":
        status = "partial"
    report = _report(status, kept, omissions, counts)
    report["limits"] = {
        "max_findings": max_findings,
        "max_omissions": max_omissions,
        "max_bytes": max_bytes,
    }
    def fit() -> bool:
        return _canonical_size(report) <= max_bytes

    if not fit():
        report = _report("refused", [_finding(None, "report_limit")], [], counts)
        report["limits"] = {"max_findings": max_findings,
                            "max_omissions": max_omissions,
                            "max_bytes": max_bytes}
    if not fit():
        raise ValueError("max_bytes")
    return report
