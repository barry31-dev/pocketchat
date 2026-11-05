"""Focused contract tests for the pure session-integrity audit."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from pocketchat.session_integrity import audit_session_records


def header(**changes):
    value = {
        "session": "run-one",
        "trace": "traces/run-one.jsonl",
        "created": "2026-11-05T00:00:00Z",
        "pocketchat_version": "0.4.0",
        "schema_version": "1",
        "conversation_id": "conv-one",
        "branch_id": "branch-one",
    }
    value.update(changes)
    return value


def message(role, content, turn_id, **changes):
    value = {"role": role, "content": content, "turn_id": turn_id}
    value.update(changes)
    return value


def user(turn="turn_000001", text="hello", **changes):
    return message("user", text, turn, **changes)


def assistant(turn="turn_000002", text="hi", tool_context=None, **changes):
    value = message("assistant", text, turn, **changes)
    if tool_context is not None:
        value["tool_context"] = tool_context
    return value


def tool_context(call_id="call-one", result_id=None, call=True, result=True,
                 call_content="", result_content="ok"):
    context = []
    if call:
        context.append({
            "role": "assistant",
            "content": call_content,
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
        })
    if result:
        context.append({
            "role": "tool",
            "tool_call_id": call_id if result_id is None else result_id,
            "name": "read_file",
            "content": result_content,
        })
    return context


def valid_records():
    return [header(), user(), assistant()]


def codes(report):
    return [item["code"] for item in report["findings"]]


class SessionIntegrityTests(unittest.TestCase):
    def test_complete_modern_and_legacy_sessions_are_distinct_from_corruption(self):
        modern = audit_session_records(valid_records())
        self.assertEqual("pocketchat-session-integrity.v1", modern["schema"])
        self.assertEqual(1, modern["schema_version"])
        self.assertEqual("complete", modern["status"])
        self.assertEqual([], modern["findings"])
        self.assertEqual([], modern["omissions"])
        self.assertEqual(
            {"schema", "schema_version", "status", "counts", "findings",
             "omissions", "limits"}, set(modern))

        legacy_header = {
            "session": "legacy-run",
            "trace": "traces/legacy.jsonl",
            "created": "2026-10-01T00:00:00Z",
            "pocketchat_version": "0.2.0",
        }
        legacy = audit_session_records([
            legacy_header,
            {"role": "user", "content": "old hello"},
            {"role": "assistant", "content": "old reply"},
        ])
        self.assertEqual("complete", legacy["status"])
        self.assertEqual(3, legacy["counts"]["records"])
        self.assertEqual(2, legacy["counts"]["messages"])

        empty = audit_session_records([header()])
        self.assertEqual("complete", empty["status"])
        self.assertEqual(0, empty["counts"]["messages"])

        corrupt = audit_session_records([])
        self.assertEqual("refused", corrupt["status"])
        self.assertIn("missing_header", codes(corrupt))

    def test_header_identity_and_schema_are_validated_without_rejecting_legacy(self):
        cases = [
            ({"session": ""}, "invalid_header_identity"),
            ({"trace": 7}, "invalid_header_identity"),
            ({"created": None}, "invalid_header_identity"),
            ({"schema_version": "2"}, "unsupported_schema"),
            ({"conversation_id": 42}, "invalid_header_identity"),
            ({"schema_version": None}, "invalid_schema"),
        ]
        for changes, code in cases:
            report = audit_session_records([header(**changes)])
            self.assertEqual("refused", report["status"], changes)
            self.assertIn(code, codes(report), changes)

    def test_message_shape_roles_and_content_are_checked(self):
        records = valid_records()
        records[1]["role"] = "tool"
        records[2]["content"] = {"secret": "message body canary"}
        records.append({"role": "user", "content": 3, "turn_id": "turn_000003"})
        report = audit_session_records(records)
        self.assertEqual("refused", report["status"])
        self.assertIn("invalid_role", codes(report))
        self.assertIn("invalid_content", codes(report))
        self.assertNotIn("message body canary", repr(report))

        wrong_owner = audit_session_records([
            header(), user(tool_context=tool_context()),
        ])
        self.assertEqual("refused", wrong_owner["status"])
        self.assertIn("tool_context_on_non_assistant", codes(wrong_owner))

    def test_turn_ids_are_required_unique_and_monotonic(self):
        reports = [
            audit_session_records([header(), user(), assistant(turn="turn_000001")]),
            audit_session_records([header(), user("turn_000002"),
                                   assistant("turn_000001")]),
            audit_session_records([header(), user(turn="bad-id")]),
            audit_session_records([header(), user("turn_000009")]),
        ]
        for report in reports:
            self.assertEqual("refused", report["status"])
        self.assertIn("duplicate_turn", codes(reports[0]))
        self.assertIn("regressing_turn", codes(reports[1]))
        self.assertIn("invalid_turn", codes(reports[2]))
        self.assertIn("nonconsecutive_turn", codes(reports[3]))

    def test_provider_tool_context_pairs_calls_and_explicit_terminal_states(self):
        good = audit_session_records([
            header(), user(), assistant(tool_context=tool_context()),
        ])
        self.assertEqual("complete", good["status"])

        unpaired = audit_session_records([
            header(), user(), assistant("turn_000002", "",
                                        tool_context=tool_context(result=False)),
        ])
        self.assertEqual("refused", unpaired["status"])
        self.assertIn("unresolved_tool_call", codes(unpaired))

        mismatch = audit_session_records([
            header(), user(), assistant("turn_000002", "",
                                        tool_context=tool_context(result_id="call-two")),
        ])
        self.assertEqual("refused", mismatch["status"])
        self.assertIn("unknown_tool_result", codes(mismatch))

        duplicate = audit_session_records([
            header(), user(), assistant("turn_000002", "", tool_context=[
                tool_context()[0], tool_context()[0], tool_context()[1],
            ]),
        ])
        self.assertEqual("refused", duplicate["status"])
        self.assertIn("duplicate_tool_call", codes(duplicate))

        missing_terminal = audit_session_records([
            header(), user(), assistant("turn_000002", "", tool_context=[
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call-one", "type": "function",
                     "function": {"name": "read_file", "arguments": "{}"}},
                ]},
                tool_context()[1],
                {"role": "assistant", "content": "", "tool_calls": []},
            ]),
        ])
        self.assertEqual("refused", missing_terminal["status"])
        self.assertIn("missing_tool_terminal", codes(missing_terminal))

    def test_partial_coverage_is_explicit_and_input_is_unchanged(self):
        records = valid_records() + [assistant("turn_000003")]
        records[-1]["_audit_read_coverage"] = "partial"
        before = copy.deepcopy(records)
        report = audit_session_records(records)
        self.assertEqual("partial", report["status"])
        self.assertEqual(1, report["counts"]["partial_records"])
        self.assertEqual(before, records)

    def test_limits_reject_instead_of_truncating_and_report_is_stable(self):
        for kwargs, reason in [
            ({"max_findings": 0}, "max_findings"),
            ({"max_omissions": 0}, "max_omissions"),
            ({"max_bytes": 0}, "max_bytes"),
            ({"max_findings": True}, "max_findings"),
        ]:
            with self.assertRaises(ValueError) as caught:
                audit_session_records(valid_records(), **kwargs)
            self.assertEqual(reason, str(caught.exception))

        too_many = [header()] + [
            {"role": "bogus", "content": "bad", "turn_id": "turn_%06d" % number}
            for number in range(1, 70)
        ]
        first = audit_session_records(too_many, max_findings=8, max_omissions=8)
        second = audit_session_records(copy.deepcopy(too_many),
                                       max_findings=8, max_omissions=8)
        self.assertEqual("refused", first["status"])
        self.assertEqual(first, second)
        self.assertEqual({"max_findings": 8, "max_omissions": 8,
                          "max_bytes": 65536}, first["limits"])
        self.assertLessEqual(len(first["findings"]), 8)
        self.assertLessEqual(len(first["omissions"]), 8)
        self.assertLessEqual(len(first["findings"]) + len(first["omissions"]), 16)
        self.assertTrue(first["omissions"])

        with self.assertRaises(ValueError) as caught:
            audit_session_records(valid_records(), max_bytes=32)
        self.assertEqual("max_bytes", str(caught.exception))

    def test_large_valid_input_returns_partial_with_bounded_omissions(self):
        records = [header()] + [
            message("user", "x", "turn_%06d" % number)
            for number in range(1, 130)
        ]
        report = audit_session_records(records, max_findings=8, max_omissions=8)
        self.assertEqual("partial", report["status"])
        self.assertEqual(1, len(report["omissions"]))
        self.assertEqual(1, report["omissions"][0]["count"])
        self.assertLessEqual(len(report["findings"]) + len(report["omissions"]), 8)

    def test_findings_never_echo_messages_request_bodies_or_credentials(self):
        canary = "private-session-canary"
        report = audit_session_records([
            header(session=canary),
            user(text=canary),
            assistant("turn_000002", canary, tool_context=tool_context(
                result_content=canary)),
        ])
        self.assertNotIn(canary, repr(report))
        self.assertNotIn(canary, repr(report))

    def test_zero_filesystem_socket_and_subprocess_io(self):
        records = valid_records()
        before = copy.deepcopy(records)
        with mock.patch("builtins.open", side_effect=AssertionError("open called")), \
                mock.patch("socket.socket", side_effect=AssertionError("socket called")), \
                mock.patch("subprocess.run", side_effect=AssertionError("subprocess called")):
            report = audit_session_records(records)
        self.assertEqual("complete", report["status"])
        self.assertEqual(before, records)


if __name__ == "__main__":
    unittest.main()
