"""Pure integrity checks for already-read PocketChat session records.

This module deliberately owns no session-file reader. Callers provide the JSON
objects they have already read, and this layer answers whether those objects
form a coherent session record. It never repairs or rewrites them, and its
findings contain fixed codes and positional metadata rather than conversation,
request, tool or credential text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPORT_SCHEMA = "pocketchat-session-integrity.v1"
SCHEMA_VERSION = 1
MESSAGE_ROLES = ("user", "assistant")
TURN_ID_PATTERN = re.compile(r"^turn_([0-9]{6})$")
MAX_FINDINGS_LIMIT = 4096
MAX_OMISSIONS_LIMIT = 4096
MIN_REPORT_BYTES = 512
MAX_REPORT_BYTES = 1024 * 1024


def _canonical_bytes(report: Mapping[str, Any]) -> bytes:
    return json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _totals(findings_total: int, omissions_total: int,
            tool_calls: int, tool_calls_resolved: int) -> Dict[str, int]:
    return {
        "findings": findings_total,
        "omissions": omissions_total,
        "tool_calls": tool_calls,
        "tool_calls_resolved": tool_calls_resolved,
        "tool_calls_unresolved": tool_calls - tool_calls_resolved,
    }


def _report(*, status: str, record_count: int, message_count: int,
            findings: List[Dict[str, Any]],
            omissions: List[Dict[str, Any]],
            totals: Dict[str, int],
            refusal: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "record_count": record_count,
        "message_count": message_count,
        "findings": findings,
        "omissions": omissions,
        "totals": totals,
    }
    if refusal is not None:
        result["refusal"] = refusal
    return result


def _refusal(code: str, detail: str, *, record_count: int = 0,
             message_count: int = 0,
             totals: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    return _report(
        status="refused",
        record_count=record_count,
        message_count=message_count,
        findings=[],
        omissions=[],
        totals=totals or _totals(0, 0, 0, 0),
        refusal={"code": code, "detail": detail},
    )


def _finding(code: str, *, record_index: Optional[int] = None,
             message_index: Optional[int] = None,
             field: Optional[str] = None,
             context_index: Optional[int] = None) -> Dict[str, Any]:
    return {
        "code": code,
        "record_index": record_index,
        "message_index": message_index,
        "field": field,
        "context_index": context_index,
    }


def _valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_header(header: Mapping[str, Any], findings: List[Dict[str, Any]]) -> bool:
    if not _valid_text(header.get("session")):
        findings.append(_finding(
            "invalid_session_id", record_index=0, field="session"))
    recorded = header.get("schema_version")
    if recorded is not None and recorded != "1":
        return False
    modern = recorded == "1"
    for field in ("conversation_id", "branch_id"):
        if modern and not _valid_text(header.get(field)):
            findings.append(_finding(
                "missing_" + field, record_index=0, field=field))
        elif header.get(field) is not None and not _valid_text(header.get(field)):
            findings.append(_finding(
                "invalid_" + field, record_index=0, field=field))
    for field in ("trace", "created"):
        if header.get(field) is not None and not _valid_text(header.get(field)):
            findings.append(_finding(
                "invalid_" + field, record_index=0, field=field))
    has_parent_branch = "parent_branch_id" in header
    has_parent_turn = "parent_turn_id" in header
    if has_parent_branch != has_parent_turn:
        findings.append(_finding(
            "incomplete_parent_binding", record_index=0, field="parent"))
    if has_parent_branch and not _valid_text(header.get("parent_branch_id")):
        findings.append(_finding(
            "invalid_parent_branch_id", record_index=0,
            field="parent_branch_id"))
    if has_parent_turn:
        value = header.get("parent_turn_id")
        if (not _valid_text(value)
                or TURN_ID_PATTERN.fullmatch(value) is None):
            findings.append(_finding(
                "invalid_parent_turn_id", record_index=0,
                field="parent_turn_id"))
    if header.get("workspace_id") is not None and not _valid_text(
            header.get("workspace_id")):
        findings.append(_finding(
            "invalid_workspace_id", record_index=0, field="workspace_id"))
    return True


def _validate_tool_call(call: Any) -> bool:
    if not isinstance(call, Mapping):
        return False
    function = call.get("function")
    return (
        _valid_text(call.get("id"))
        and call.get("type") == "function"
        and isinstance(function, Mapping)
        and _valid_text(function.get("name"))
        and isinstance(function.get("arguments"), str)
    )


def _validate_tool_result(item: Mapping[str, Any]) -> bool:
    return (
        _valid_text(item.get("tool_call_id"))
        and _valid_text(item.get("name"))
        and isinstance(item.get("content"), str)
    )


def _validate_tool_context(
        context: Any, *, record_index: int, message_index: int,
        findings: List[Dict[str, Any]],
        pending: Dict[str, Tuple[int, int, int]],
        resolved: set) -> Tuple[int, int]:
    if not isinstance(context, Sequence) or isinstance(context, (str, bytes)):
        findings.append(_finding(
            "invalid_tool_context", record_index=record_index,
            message_index=message_index, field="tool_context"))
        return 0, 0
    calls = 0
    newly_resolved = 0
    for context_index, item in enumerate(context):
        if not isinstance(item, Mapping):
            findings.append(_finding(
                "invalid_tool_context_entry", record_index=record_index,
                message_index=message_index, context_index=context_index))
            continue
        role = item.get("role")
        if role == "assistant":
            tool_calls = item.get("tool_calls")
            if (not isinstance(item.get("content"), str)
                    or not isinstance(tool_calls, Sequence)
                    or isinstance(tool_calls, (str, bytes))
                    or not tool_calls):
                findings.append(_finding(
                    "malformed_tool_request", record_index=record_index,
                    message_index=message_index, context_index=context_index))
                continue
            for call_index, call in enumerate(tool_calls):
                if not _validate_tool_call(call):
                    findings.append(_finding(
                        "malformed_tool_call", record_index=record_index,
                        message_index=message_index,
                        context_index=context_index))
                    continue
                call_id = call["id"]
                if call_id in pending or call_id in resolved:
                    findings.append(_finding(
                        "duplicate_tool_call_id", record_index=record_index,
                        message_index=message_index,
                        context_index=context_index))
                    continue
                pending[call_id] = (
                    record_index, message_index, context_index)
                calls += 1
        elif role == "tool":
            if not _validate_tool_result(item):
                findings.append(_finding(
                    "malformed_tool_result", record_index=record_index,
                    message_index=message_index, context_index=context_index))
                continue
            call_id = item["tool_call_id"]
            if call_id in resolved:
                findings.append(_finding(
                    "duplicate_tool_result", record_index=record_index,
                    message_index=message_index, context_index=context_index))
            elif call_id not in pending:
                findings.append(_finding(
                    "orphan_tool_result", record_index=record_index,
                    message_index=message_index, context_index=context_index))
            else:
                pending.pop(call_id)
                resolved.add(call_id)
                newly_resolved += 1
        else:
            findings.append(_finding(
                "invalid_tool_context_role", record_index=record_index,
                message_index=message_index, context_index=context_index))
    return calls, newly_resolved


def audit_session_records(
        records: Any, *, max_findings: int = 64, max_omissions: int = 64,
        max_bytes: int = 65536) -> Dict[str, Any]:
    """Audit caller-supplied session records without I/O or mutation."""
    for value, minimum, maximum in (
        (max_findings, 1, MAX_FINDINGS_LIMIT),
        (max_omissions, 1, MAX_OMISSIONS_LIMIT),
        (max_bytes, MIN_REPORT_BYTES, MAX_REPORT_BYTES),
    ):
        if (isinstance(value, bool) or not isinstance(value, int)
                or value < minimum or value > maximum):
            return _refusal(
                "invalid_limits", "report limits must be bounded integers")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        return _refusal("invalid_records", "records must be a sequence")
    if not records:
        return _refusal("empty_input", "records contain no session header")
    if not isinstance(records[0], Mapping) or "session" not in records[0]:
        return _refusal("missing_header", "first record is not a session header")

    header = records[0]
    recorded_schema = header.get("schema_version")
    if recorded_schema is not None and recorded_schema != "1":
        return _refusal(
            "unsupported_schema", "session schema version is not supported")

    findings: List[Dict[str, Any]] = []
    _validate_header(header, findings)
    pending: Dict[str, Tuple[int, int, int]] = {}
    resolved: set = set()
    message_count = 0
    tool_calls = 0
    tool_calls_resolved = 0
    previous_turn_number: Optional[int] = None

    for record_index, record in enumerate(records[1:], 1):
        if not isinstance(record, Mapping):
            findings.append(_finding(
                "invalid_record_shape", record_index=record_index))
            continue
        if "session" in record:
            findings.append(_finding(
                "duplicate_header", record_index=record_index))
            continue
        message_count += 1
        role = record.get("role")
        if role not in MESSAGE_ROLES:
            findings.append(_finding(
                "invalid_role", record_index=record_index,
                message_index=message_count, field="role"))
        if not isinstance(record.get("content"), str):
            findings.append(_finding(
                "invalid_message_content", record_index=record_index,
                message_index=message_count, field="content"))

        if "run_id" in record and record.get("run_id") != header.get("session"):
            findings.append(_finding(
                "run_id_mismatch", record_index=record_index,
                message_index=message_count, field="run_id"))
        if "workspace_id" in record:
            expected_workspace = header.get("workspace_id")
            if (expected_workspace is None
                    or record.get("workspace_id") != expected_workspace):
                findings.append(_finding(
                    "workspace_id_mismatch", record_index=record_index,
                    message_index=message_count, field="workspace_id"))

        if "turn_id" in record:
            value = record.get("turn_id")
            match = (TURN_ID_PATTERN.fullmatch(value)
                     if isinstance(value, str) else None)
            if match is None:
                findings.append(_finding(
                    "invalid_turn_id", record_index=record_index,
                    message_index=message_count, field="turn_id"))
            else:
                turn_number = int(match.group(1))
                if turn_number <= 0:
                    findings.append(_finding(
                        "invalid_turn_id", record_index=record_index,
                        message_index=message_count, field="turn_id"))
                elif (previous_turn_number is not None
                        and turn_number == previous_turn_number):
                    findings.append(_finding(
                        "duplicate_turn_id", record_index=record_index,
                        message_index=message_count, field="turn_id"))
                elif (previous_turn_number is not None
                        and turn_number < previous_turn_number):
                    findings.append(_finding(
                        "non_monotonic_turn_id", record_index=record_index,
                        message_index=message_count, field="turn_id"))
                previous_turn_number = turn_number

        context = record.get("tool_context")
        if context is not None and context != []:
            if role != "assistant":
                findings.append(_finding(
                    "tool_context_on_non_assistant", record_index=record_index,
                    message_index=message_count, field="tool_context"))
            # Match the existing reader: validate the context entries before
            # applying the owner rule so an orphan or malformed result is not
            # hidden by the first ownership finding.
            calls, newly_resolved = _validate_tool_context(
                context, record_index=record_index,
                message_index=message_count, findings=findings,
                pending=pending, resolved=resolved)
            tool_calls += calls
            tool_calls_resolved += newly_resolved

    for call_id, (record_index, message_index, _context_index) in (
            pending.items()):
        findings.append(_finding(
            "unresolved_tool_call", record_index=record_index,
            message_index=message_index, field="tool_call_id"))

    # Findings are already discovered in record/message/context order. Keeping
    # that order is deterministic and preserves the first observed condition
    # when several checks fail on one object.
    findings_total = len(findings)
    omissions: List[Dict[str, Any]] = []
    if findings_total > max_findings:
        omissions.append({
            "kind": "findings",
            "count": findings_total - max_findings,
        })
    omitted_omissions = max(0, len(omissions) - max_omissions)
    if omitted_omissions:
        omissions = omissions[:max_omissions]
    visible_findings = findings[:max_findings]
    omissions_total = len(omissions) + omitted_omissions
    status = "partial" if (
        findings_total > max_findings or omitted_omissions
    ) else "complete"
    report = _report(
        status=status,
        record_count=len(records),
        message_count=message_count,
        findings=visible_findings,
        omissions=omissions,
        totals=_totals(
            findings_total, omissions_total,
            tool_calls, tool_calls_resolved),
    )
    if len(_canonical_bytes(report)) > max_bytes:
        return _refusal(
            "report_too_large",
            "canonical report exceeds max_bytes",
            record_count=len(records),
            message_count=message_count,
            totals=_totals(
                findings_total, omissions_total,
                tool_calls, tool_calls_resolved))
    return report
