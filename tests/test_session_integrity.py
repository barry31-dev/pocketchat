"""Focused tests for the pure session-integrity audit."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from pocketchat.session_integrity import audit_session_records


def header(**overrides):
    record = {
        "session": "run_123",
        "trace": "traces/run_123.jsonl",
        "created": "2026-11-05T06:00:00Z",
        "pocketchat_version": "0.1.0",
        "schema_version": "1",
        "conversation_id": "conv_20261105t060000_abcdef01",
        "branch_id": "branch_20261105t060000_abcdef01",
    }
    record.update(overrides)
    return record


def message(role="user", content="hello", turn_id="turn_000001", **overrides):
    record = {"role": role, "content": content}
    if turn_id is not None:
        record["turn_id"] = turn_id
    record.update(overrides)
    return record


def assistant_calls(call_id="call_1"):
    return assistant_with_calls(call_id)


def assistant_with_calls(*call_ids):
    return [{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        } for call_id in call_ids],
    }]


def tool_result(call_id="call_1"):
    return [{
        "role": "tool",
        "tool_call_id": call_id,
        "name": "lookup",
        "content": "done",
    }]


class SessionIntegrityAuditTests(unittest.TestCase):
    def test_valid_modern_records_are_complete(self):
        records = [
            header(),
            message(),
            message(
                role="assistant",
                content="calling a tool",
                turn_id="turn_000002",
                tool_context=assistant_calls() + tool_result(),
            ),
        ]

        report = audit_session_records(records)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["record_count"], 3)
        self.assertEqual(report["message_count"], 2)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["totals"], {
            "findings": 0,
            "omissions": 0,
            "tool_calls": 1,
            "tool_calls_resolved": 1,
            "tool_calls_unresolved": 0,
        })

    def test_legacy_missing_schema_and_turn_ids_are_accepted(self):
        records = [
            {
                "session": "legacy",
                "trace": "legacy.jsonl",
                "created": "2026-10-01T00:00:00Z",
                "pocketchat_version": "0.1.0",
            },
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]

        report = audit_session_records(records)

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["message_count"], 2)

    def test_empty_input_is_distinct_from_invalid_container(self):
        empty = audit_session_records([])
        invalid = audit_session_records({"session": "not-a-list"})

        self.assertEqual(empty["status"], "refused")
        self.assertEqual(empty["refusal"]["code"], "empty_input")
        self.assertEqual(invalid["status"], "refused")
        self.assertEqual(invalid["refusal"]["code"], "invalid_records")

    def test_missing_header_and_unknown_schema_are_refused(self):
        missing = audit_session_records([message()])
        unknown = audit_session_records([
            header(schema_version="2"),
            message(),
        ])

        self.assertEqual(missing["refusal"]["code"], "missing_header")
        self.assertEqual(unknown["refusal"]["code"], "unsupported_schema")

    def test_modern_header_requires_bound_conversation_and_branch(self):
        report = audit_session_records([
            header(conversation_id=None, branch_id=""),
            message(),
        ])

        self.assertEqual(report["status"], "complete")
        self.assertEqual(
            [item["code"] for item in report["findings"]],
            ["missing_conversation_id", "missing_branch_id"],
        )

    def test_parent_identity_must_be_a_complete_pair(self):
        report = audit_session_records([
            header(parent_branch_id="branch_parent"),
            message(),
        ])

        self.assertIn(
            "incomplete_parent_binding",
            [item["code"] for item in report["findings"]],
        )

    def test_turn_ids_must_be_unique_and_monotonic(self):
        report = audit_session_records([
            header(),
            message(turn_id="turn_000002"),
            message(turn_id="turn_000002", content="duplicate"),
            message(turn_id="turn_000001", content="regression"),
            message(turn_id="bad", content="malformed"),
        ])

        codes = [item["code"] for item in report["findings"]]
        self.assertEqual(codes, [
            "duplicate_turn_id",
            "non_monotonic_turn_id",
            "invalid_turn_id",
        ])

    def test_message_shapes_are_reported_without_echoing_bodies(self):
        secret = "SECRET-CANARY-91f7"
        report = audit_session_records([
            header(),
            {"role": "system", "content": secret},
            {"role": "user", "content": 123, "turn_id": 7},
        ])

        codes = [item["code"] for item in report["findings"]]
        self.assertEqual(codes, ["invalid_role", "invalid_message_content",
                                 "invalid_turn_id"])
        self.assertNotIn(secret, str(report))

    def test_tool_calls_pair_with_results_across_records(self):
        report = audit_session_records([
            header(),
            message(
                role="assistant",
                content="first",
                turn_id="turn_000001",
                tool_context=assistant_with_calls("call_1", "call_2"),
            ),
            message(
                role="assistant",
                content="second",
                turn_id="turn_000002",
                tool_context=tool_result("call_1"),
            ),
            message(
                role="assistant",
                content="third",
                turn_id="turn_000003",
                tool_context=tool_result("call_2"),
            ),
        ])

        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["totals"]["tool_calls"], 2)
        self.assertEqual(report["totals"]["tool_calls_resolved"], 2)

    def test_tool_context_reports_orphan_duplicate_and_unresolved_calls(self):
        report = audit_session_records([
            header(),
            message(
                role="user",
                content="wrong owner",
                turn_id="turn_000001",
                tool_context=tool_result("missing"),
            ),
            message(
                role="assistant",
                content="calls",
                turn_id="turn_000002",
                tool_context=(
                    assistant_calls("call_1")
                    + assistant_calls("call_1")
                    + tool_result("call_1")
                    + tool_result("call_1")
                ),
            ),
            message(
                role="assistant",
                content="unresolved",
                turn_id="turn_000003",
                tool_context=assistant_calls("call_2"),
            ),
        ])

        codes = [item["code"] for item in report["findings"]]
        self.assertEqual(codes, [
            "tool_context_on_non_assistant",
            "orphan_tool_result",
            "duplicate_tool_call_id",
            "duplicate_tool_result",
            "unresolved_tool_call",
        ])
        self.assertEqual(report["totals"]["tool_calls_unresolved"], 1)

    def test_optional_run_and_workspace_bindings_are_checked(self):
        report = audit_session_records([
            header(workspace_id="workspace_a"),
            message(turn_id="turn_000001", run_id="run_123",
                    workspace_id="workspace_a"),
            message(turn_id="turn_000002", run_id="other",
                    workspace_id="workspace_b"),
        ])

        codes = [item["code"] for item in report["findings"]]
        self.assertEqual(codes, ["run_id_mismatch", "workspace_id_mismatch"])

    def test_input_records_and_nested_context_are_unchanged(self):
        records = [
            header(),
            message(
                role="assistant",
                content="tool",
                turn_id="turn_000001",
                tool_context=assistant_calls() + tool_result(),
            ),
        ]
        before = copy.deepcopy(records)

        audit_session_records(records)

        self.assertEqual(records, before)

    def test_findings_caps_produce_partial_with_explicit_omissions(self):
        records = [header()]
        records.extend(
            {"role": "system", "content": "x"}
            for _ in range(4)
        )

        report = audit_session_records(
            records, max_findings=2, max_omissions=1, max_bytes=65536)

        self.assertEqual(report["status"], "partial")
        self.assertEqual(len(report["findings"]), 2)
        self.assertEqual(report["totals"]["findings"], 4)
        self.assertEqual(report["omissions"], [
            {"kind": "findings", "count": 2},
        ])

    def test_report_overflow_is_refused_without_echoing_bodies(self):
        secret = "SECRET-CANARY-OVERFLOW"
        records = [header()]
        records.extend(
            {"role": "system", "content": secret, "turn_id": "turn_000001"}
            for _ in range(20)
        )

        report = audit_session_records(records, max_bytes=512)

        self.assertEqual(report["status"], "refused")
        self.assertEqual(report["refusal"]["code"], "report_too_large")
        self.assertNotIn(secret, str(report))

    def test_invalid_limits_are_refused(self):
        for kwargs in (
            {"max_findings": 0},
            {"max_findings": True},
            {"max_omissions": -1},
            {"max_bytes": 255},
            {"max_bytes": False},
        ):
            with self.subTest(kwargs=kwargs):
                report = audit_session_records([header()], **kwargs)
                self.assertEqual(report["status"], "refused")
                self.assertEqual(report["refusal"]["code"], "invalid_limits")

    def test_output_is_deterministic_and_never_contains_raw_message_bodies(self):
        secret = "SECRET-CANARY-DETERMINISTIC"
        records = [
            header(),
            message(content=secret),
            {"role": "system", "content": secret, "turn_id": "turn_000002"},
        ]

        first = audit_session_records(records)
        second = audit_session_records(records)

        self.assertEqual(first, second)
        self.assertNotIn(secret, str(first))
        self.assertEqual(first["findings"][0]["record_index"], 2)

    def test_audit_performs_no_file_environment_command_socket_or_provider_io(self):
        records = [header(), message()]

        with patch("builtins.open", side_effect=AssertionError("open")), \
             patch("socket.create_connection",
                   side_effect=AssertionError("socket")), \
             patch("subprocess.Popen", side_effect=AssertionError("subprocess")), \
             patch("os.environ.get", side_effect=AssertionError("environment")):
            report = audit_session_records(records)

        self.assertEqual(report["status"], "complete")


if __name__ == "__main__":
    unittest.main()
