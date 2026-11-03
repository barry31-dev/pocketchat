"""Provider tool-call protocol and bounded read-only execution tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.provider import build_payload
from pocketchat.session import Session


class FakeToolAdapter:
    name = "tool-call-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append(list(messages))
        return self.responses.pop(0)


def tool_response(call_id, path):
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "m",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{
            "id": call_id,
            "name": "read_file",
            "arguments": {"path": path},
        }],
    }


def write_response(call_id, path, content):
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "m",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{
            "id": call_id,
            "name": "write_file",
            "arguments": {"path": path, "content": content},
        }],
    }


def multi_tool_response(*calls):
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "m",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{
            "id": call_id,
            "name": name,
            "arguments": arguments,
        } for call_id, name, arguments in calls],
    }


def text_response():
    return {
        "content": "final answer",
        "finish_reason": "stop",
        "model": "m",
        "usage": {"input_tokens": 3, "output_tokens": 2,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
    }


class ToolCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "facts.txt").write_bytes(b"the answer\n")

    def config(self, adapter):
        return {
            "profile": "tool", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
            "provider_adapter": adapter,
            "tool_read_enabled": True,
            "tool_read_allow_paths": ["facts.txt"],
            "tool_call_max_count": 2,
            "tool_result_max_bytes": 4096,
            "tool_write_enabled": True,
            "tool_write_allow_paths": ["new.txt"],
        }

    def test_payload_offers_only_the_read_file_tool(self):
        config = self.config(FakeToolAdapter([]))
        config["tool_write_enabled"] = False
        payload = build_payload([{"role": "user", "content": "read"}], config)
        self.assertEqual(["read_file"],
                         [tool["function"]["name"] for tool in payload["tools"]])
        self.assertEqual("auto", payload["tool_choice"])

    def test_payload_offers_read_and_create_only_write_tools(self):
        payload = build_payload([{"role": "user", "content": "write"}],
                                self.config(FakeToolAdapter([])))
        self.assertEqual(["read_file", "write_file"],
                         [tool["function"]["name"] for tool in payload["tools"]])

    def test_profile_fields_keep_tool_configuration(self):
        from pocketchat.config import load_config, profile_config
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "tool", "default_profile": "tool",
            "profiles": {"tool": {
                "base_url": "http://example.invalid/v1", "model": "m",
                "api_key_env": "K",
                "tool_read_enabled": True,
                "tool_read_allow_paths": ["facts.txt"],
                "tool_call_max_count": 2,
                "tool_result_max_bytes": 4096,
            }},
        }), encoding="utf-8")
        selected = profile_config(load_config(path), "tool")
        self.assertTrue(selected["tool_read_enabled"])
        self.assertEqual(["facts.txt"], selected["tool_read_allow_paths"])

    def test_profile_cli_can_disable_tool_read(self):
        from tests._helpers import run_cli
        path = self.root / "profile-config.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "tool",
            "default_profile": "other",
            "profiles": {"other": {
                "base_url": "http://other.invalid/v1", "model": "other",
                "api_key_env": "OTHER_KEY",
            }},
        }), encoding="utf-8")
        result = run_cli((
            "profile", "set", "tool", "--base-url", "http://example.invalid/v1",
            "--model", "m", "--api-key-env", "K", "--no-tool-read", "--json",
            "--config", str(path),
        ), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        record = json.loads(path.read_text())["profiles"]["tool"]
        self.assertFalse(record["tool_read_enabled"])

    def test_model_tool_call_executes_read_and_sends_tool_result(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", "facts.txt"), text_response(),
        ])
        session = Session(self.config(adapter), self.root / "traces" / "run.jsonl",
                          "run_tool", api_key="secret-tool")
        self.assertEqual("completed", session.run(["read the fact"]))
        self.assertEqual(2, len(adapter.calls))
        self.assertIn("tool_calls", adapter.calls[1][-2])
        events = [json.loads(line) for line in
                  (self.root / "traces" / "run.jsonl").read_text().splitlines()]
        types = [event["type"] for event in events]
        self.assertIn("tool.call.started", types)
        self.assertIn("tool.call.completed", types)
        offered = events[0]["payload"]["config_summary"]["tools"]
        self.assertTrue(offered[0]["available"])
        self.assertEqual(64, len(offered[0]["schema_hash"]))
        self.assertTrue(offered[1]["available"])
        self.assertEqual(64, len(offered[1]["schema_hash"]))
        self.assertFalse(offered[2]["available"])
        self.assertEqual(64, len(offered[2]["schema_hash"]))

    def test_adapter_rebuild_drift_is_not_sent(self):
        adapter = FakeToolAdapter([text_response()])
        original_build = adapter.build_payload

        builds = []

        def drifting_build(messages, config):
            builds.append(messages)
            payload = original_build(messages, config)
            if len(builds) == 2:
                payload["marker"] = "drift"
            return payload

        adapter.build_payload = drifting_build
        session = Session(self.config(adapter),
                          self.root / "traces" / "drift.jsonl",
                          "run_drift", api_key="secret-tool")
        self.assertEqual("failed", session.run(["hello"]))
        self.assertEqual(0, len(adapter.calls))
        events = [json.loads(line) for line in
                  (self.root / "traces" / "drift.jsonl").read_text().splitlines()]
        self.assertEqual("request_payload_mismatch",
                         events[-1]["payload"]["error"]["type"])

    def test_tool_result_is_admitted_before_the_second_provider_call(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", "facts.txt"),
            text_response(),
        ])
        config = self.config(adapter)
        config["max_payload_bytes"] = 4096
        first_payload_bytes = len(__import__(
            "pocketchat.request", fromlist=["canonical_bytes"]).canonical_bytes(
                adapter.build_payload(
                    [{"role": "user", "content": "read the fact"}], config)))
        config["max_payload_bytes"] = first_payload_bytes + 1
        session = Session(config, self.root / "traces" / "admission.jsonl",
                          "run_admission", api_key="secret-tool")
        self.assertEqual("failed", session.run(["read the fact"]))
        self.assertEqual(1, len(adapter.calls))
        events = [json.loads(line) for line in
                  (self.root / "traces" / "admission.jsonl").read_text().splitlines()]
        failed = events[-1]
        self.assertEqual("run.failed", failed["type"])
        self.assertEqual("request_budget_exceeded",
                         failed["payload"]["error"]["type"])
        self.assertEqual(1, sum(event["type"] == "model.request.started"
                                for event in events))

    def test_tool_call_budget_is_enforced(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", "facts.txt"),
            tool_response("call_2", "facts.txt"),
            tool_response("call_3", "facts.txt"),
        ])
        config = self.config(adapter)
        config["tool_call_max_count"] = 2
        session = Session(config, self.root / "traces" / "budget.jsonl",
                          "run_budget", api_key="secret-tool")
        self.assertEqual("failed", session.run(["read"]))
        self.assertEqual(3, len(adapter.calls))

    def test_disabled_tool_is_not_executable_even_if_model_requests_it(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", "facts.txt"), text_response(),
        ])
        config = self.config(adapter)
        config["tool_read_enabled"] = False
        session = Session(config, self.root / "traces" / "disabled.jsonl",
                          "run_disabled", api_key="secret-tool")
        self.assertEqual("completed", session.run(["read"]))
        self.assertEqual(2, len(adapter.calls))
        failure = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("failed", failure["status"])
        self.assertEqual("unsupported_tool", failure["error"]["type"])

    def test_invalid_tool_arguments_are_returned_for_model_correction(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", ""), text_response(),
        ])
        session = Session(self.config(adapter),
                          self.root / "traces" / "invalid.jsonl",
                          "run_invalid", api_key="secret-tool")
        self.assertEqual("completed", session.run(["read"]))
        self.assertEqual(2, len(adapter.calls))
        failure = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("invalid_arguments", failure["error"]["type"])

    def test_guard_refusal_does_not_drop_other_tool_results_in_the_batch(self):
        adapter = FakeToolAdapter([
            multi_tool_response(
                ("call_denied", "read_file", {"path": ".env"}),
                ("call_ok", "read_file", {"path": "facts.txt"}),
            ),
            text_response(),
        ])
        config = self.config(adapter)
        config["tool_read_allow_paths"] = ["facts.txt", ".env"]
        session = Session(config,
                          self.root / "traces" / "mixed.jsonl",
                          "run_mixed", api_key="secret-tool")
        self.assertEqual("completed", session.run(["read both"]))
        self.assertEqual(2, len(adapter.calls))
        denied = json.loads(adapter.calls[1][-2]["content"])
        allowed = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("failed", denied["status"])
        self.assertEqual("denylisted_path", denied["error"]["type"])
        self.assertEqual("the answer\n", allowed["content"])
        events = [json.loads(line) for line in
                  (self.root / "traces" / "mixed.jsonl").read_text().splitlines()]
        failed = [event for event in events if event["type"] == "tool.call.failed"]
        completed = [event for event in events
                     if event["type"] == "tool.call.completed"]
        self.assertEqual("call_denied", failed[0]["payload"]["tool_call_id"])
        self.assertEqual("call_ok", completed[0]["payload"]["tool_call_id"])

    def test_invalid_text_encoding_is_returned_for_model_correction(self):
        (self.root / "bad.txt").write_bytes(b"\xff\xfe")
        adapter = FakeToolAdapter([
            tool_response("call_bad", "bad.txt"), text_response(),
        ])
        config = self.config(adapter)
        config["tool_read_allow_paths"] = ["facts.txt", "bad.txt"]
        session = Session(config, self.root / "traces" / "encoding.jsonl",
                          "run_encoding", api_key="secret-tool")
        self.assertEqual("completed", session.run(["read bad"]))
        failure = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("invalid_encoding", failure["error"]["type"])

    def test_recoverable_failures_count_against_the_tool_call_budget(self):
        adapter = FakeToolAdapter([
            tool_response("call_1", ""),
            tool_response("call_2", "facts.txt"),
            tool_response("call_3", "facts.txt"),
        ])
        config = self.config(adapter)
        config["tool_call_max_count"] = 2
        session = Session(config, self.root / "traces" / "failure_budget.jsonl",
                          "run_failure_budget", api_key="secret-tool")
        self.assertEqual("failed", session.run(["read"]))
        self.assertEqual(3, len(adapter.calls))

    def test_model_write_result_does_not_echo_the_written_content(self):
        adapter = FakeToolAdapter([
            write_response("call_w", "new.txt", "created"), text_response(),
        ])
        session = Session(self.config(adapter), self.root / "traces" / "write_echo.jsonl",
                          "run_echo", api_key="secret-tool")
        self.assertEqual("completed", session.run(["create a file"]))
        result = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("write_file", result["kind"])
        self.assertEqual("new.txt", result["source"])
        self.assertEqual("applied", result["effect_disposition"])
        self.assertNotIn("content", result)
        self.assertEqual("created", (self.root / "new.txt").read_text())

    def test_model_write_creates_only_new_allowlisted_file(self):
        adapter = FakeToolAdapter([
            write_response("call_w", "new.txt", "created"), text_response(),
        ])
        session = Session(self.config(adapter), self.root / "traces" / "write.jsonl",
                          "run_write", api_key="secret-tool")
        self.assertEqual("completed", session.run(["create a file"]))
        self.assertEqual("created", (self.root / "new.txt").read_text())

    def test_model_write_cannot_overwrite_or_execute_other_paths(self):
        for path, content, name in (
                ("facts.txt", "changed", "existing"),
                ("../outside.txt", "x", "escape")):
            with self.subTest(path=path):
                adapter = FakeToolAdapter([
                    write_response("call_w", path, content), text_response(),
                ])
                session = Session(
                    self.config(adapter),
                    self.root / "traces" / ("%s.jsonl" % name),
                    "run_" + name, api_key="secret-tool")
                self.assertEqual("completed", session.run(["write"]))
                self.assertEqual(2, len(adapter.calls))
                failure = json.loads(adapter.calls[1][-1]["content"])
                self.assertEqual("failed", failure["status"])
                self.assertIn(failure["error"]["type"],
                              ("not_allowlisted", "exists", "permission_denied"))


if __name__ == "__main__":
    unittest.main()
