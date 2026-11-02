"""Request admission tests: local budget checks before a provider call.

The first slice is deliberately a pure function. It inspects the payload that
would be sent and reports what was counted; it does not call a provider,
mutate messages, or decide how a session should recover.
"""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from pocketchat.request import canonical_bytes
from pocketchat.request_admission import AdmissionError, prepare_admission


class RequestAdmissionTests(unittest.TestCase):
    def test_tool_schema_can_exceed_the_payload_limit(self):
        payload = {
            "model": "fixture",
            "stream": False,
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "x" * 4096,
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
        }
        self.assertLess(len(canonical_bytes(payload["messages"])), 512)
        with self.assertRaises(AdmissionError) as caught:
            prepare_admission(payload, max_payload_bytes=512,
                              max_context_chars=512)
        self.assertEqual(caught.exception.error_type,
                         "request_budget_exceeded")


    def test_report_is_a_pure_counted_view_of_the_complete_payload(self):
        payload = {
            "model": "fixture",
            "stream": False,
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "你好"},
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one file.",
                    "parameters": {"type": "object"},
                },
            }],
        }
        before = copy.deepcopy(payload)
        report = prepare_admission(payload, max_payload_bytes=4096,
                                   max_context_chars=4096,
                                   max_input_tokens=4096)
        encoded = canonical_bytes(payload)
        self.assertEqual(payload, before)
        self.assertTrue(report["admitted"])
        self.assertEqual(report["payload_bytes"], len(encoded))
        self.assertEqual(report["payload_sha256"],
                         hashlib.sha256(encoded).hexdigest())
        self.assertEqual(report["message_chars"], 11)
        self.assertGreater(report["tool_schema_bytes"], 0)
        self.assertGreater(report["estimated_input_tokens"], 0)
        self.assertEqual(report["estimator_id"], "chars4.v1")

    def test_tool_schema_contributes_to_the_token_estimate(self):
        without_tools = {
            "model": "fixture",
            "messages": [{"role": "user", "content": "x"}],
        }
        with_tools = copy.deepcopy(without_tools)
        with_tools["tools"] = [{
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "x" * 4000,
                "parameters": {"type": "object"},
            },
        }]
        plain = prepare_admission(without_tools, max_payload_bytes=8192,
                                  max_context_chars=8192)
        offered = prepare_admission(with_tools, max_payload_bytes=8192,
                                    max_context_chars=8192)
        self.assertGreater(offered["estimated_input_tokens"],
                           plain["estimated_input_tokens"])

    def test_tool_arguments_are_counted_in_message_chars(self):
        plain = {"model": "fixture", "messages": [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call_1", "type": "function",
                             "function": {"name": "read_file",
                                          "arguments": "{}"}}]},
        ]}
        with_arguments = copy.deepcopy(plain)
        with_arguments["messages"][0]["tool_calls"][0]["function"][
            "arguments"] = json.dumps({"path": "facts.txt"})
        plain_report = prepare_admission(plain, max_payload_bytes=4096,
                                         max_context_chars=4096)
        arguments_report = prepare_admission(
            with_arguments, max_payload_bytes=4096, max_context_chars=4096)
        self.assertGreater(arguments_report["message_chars"],
                           plain_report["message_chars"])

    def test_cjk_is_counted_as_utf8_payload_bytes(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "你好世界"}]}
        report = prepare_admission(payload, max_payload_bytes=4096,
                                   max_context_chars=4096)
        self.assertEqual(report["payload_bytes"],
                         len(canonical_bytes(payload)))
        self.assertGreater(report["payload_bytes"], 50)

    def test_the_message_character_limit_is_independent_of_payload_bytes(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "x" * 80}]}
        with self.assertRaises(AdmissionError) as caught:
            prepare_admission(payload, max_payload_bytes=4096,
                              max_context_chars=64)
        self.assertEqual(caught.exception.error_type,
                         "request_budget_exceeded")

    def test_envelope_bytes_do_not_redefine_the_legacy_context_char_budget(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "x" * 80}]}
        report = prepare_admission(payload, max_payload_bytes=4096,
                                   max_context_chars=80)
        self.assertEqual(80, report["message_chars"])
        self.assertGreater(report["payload_bytes"], report["message_chars"])
        with self.assertRaises(AdmissionError):
            prepare_admission(payload, max_payload_bytes=80,
                              max_context_chars=80)

    def test_known_context_window_reduces_the_input_limit(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "x" * 80}]}
        report = prepare_admission(
            payload, max_payload_bytes=4096, max_context_chars=4096,
            model_context_window_tokens=100,
            output_reserve_tokens=40, safety_margin_tokens=20)
        self.assertEqual(40, report["effective_input_tokens"])
        with self.assertRaises(AdmissionError):
            prepare_admission(
                payload, max_payload_bytes=4096, max_context_chars=4096,
                model_context_window_tokens=20,
                output_reserve_tokens=10, safety_margin_tokens=20)

    def test_unknown_context_window_does_not_invent_a_model_limit(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "x"}]}
        report = prepare_admission(
            payload, max_payload_bytes=4096, max_context_chars=4096,
            model_context_window_tokens=None,
            output_reserve_tokens=None, safety_margin_tokens=None)
        self.assertIsNone(report["effective_input_tokens"])

    def test_negative_window_parts_are_refused(self):
        payload = {"model": "fixture", "messages": []}
        with self.assertRaises(AdmissionError) as caught:
            prepare_admission(
                payload, max_payload_bytes=4096, max_context_chars=4096,
                model_context_window_tokens=100,
                output_reserve_tokens=-1, safety_margin_tokens=0)
        self.assertEqual("invalid_admission_limit", caught.exception.error_type)

    def test_the_estimated_token_limit_is_enforced_when_supplied(self):
        payload = {"model": "fixture", "messages": [
            {"role": "user", "content": "x" * 80}]}
        with self.assertRaises(AdmissionError):
            prepare_admission(payload, max_payload_bytes=4096,
                              max_context_chars=4096, max_input_tokens=1)

    def test_an_unknown_estimator_is_refused(self):
        payload = {"model": "fixture", "messages": []}
        with self.assertRaises(AdmissionError) as caught:
            prepare_admission(payload, max_payload_bytes=4096,
                              max_context_chars=4096,
                              estimator_id="not-a-real-estimator")
        self.assertEqual(caught.exception.error_type,
                         "unsupported_estimator")

    def test_limits_are_positive_integers(self):
        payload = {"model": "fixture", "messages": []}
        invalid = (0, -1, True, 1.5, "1")
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(AdmissionError) as caught:
                    prepare_admission(payload, max_payload_bytes=value,
                                      max_context_chars=4096)
                self.assertEqual(caught.exception.error_type,
                                 "invalid_admission_limit")

    def test_non_object_payload_is_refused(self):
        with self.assertRaises(AdmissionError) as caught:
            prepare_admission([], max_payload_bytes=4096,
                              max_context_chars=4096)
        self.assertEqual(caught.exception.error_type,
                         "invalid_request_payload")


if __name__ == "__main__":
    unittest.main()
