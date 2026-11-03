"""Tool contracts are one closed declaration for provider and host."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.provider import build_payload
from pocketchat.session import Session
from pocketchat.task_policy import TaskPolicyError, parse_policy
from pocketchat.tool_contracts import (
    CONTRACTS,
    ToolContractError,
    contract_summary,
    parse_arguments,
    provider_tool,
)


class ToolContractTests(unittest.TestCase):
    def test_the_contract_declares_the_three_bounded_tools_once(self):
        self.assertEqual(
            {"read_file", "write_file", "memory_search"}, set(CONTRACTS))
        for name, contract in CONTRACTS.items():
            self.assertEqual(name, contract.name)
            self.assertEqual("object", contract.parameters["type"])
            self.assertFalse(contract.parameters["additionalProperties"])

    def test_provider_offer_uses_the_same_schema_as_the_host(self):
        offered = provider_tool("read_file")
        self.assertEqual("read_file", offered["function"]["name"])
        self.assertEqual(CONTRACTS["read_file"].parameters,
                         offered["function"]["parameters"])
        self.assertEqual(["path", "content"],
                         CONTRACTS["write_file"].parameters["required"])

    def test_parse_arguments_refuses_unknown_or_missing_fields(self):
        self.assertEqual(
            {"path": "facts.txt"},
            parse_arguments("read_file", {"path": "facts.txt"}))
        for arguments in (
                {"path": "facts.txt", "extra": True},
                {"content": "text"},
                {},
                [],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolContractError):
                    parse_arguments("read_file", arguments)

    def test_write_content_must_be_a_string_and_large_arguments_are_bounded(self):
        for arguments in (
                {"path": "new.txt", "content": 1},
                {"path": "new.txt", "content": "x" * (64 * 1024 + 1)},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolContractError):
                    parse_arguments("write_file", arguments)

    def test_summary_hashes_each_contract_not_the_enabled_list(self):
        summary = contract_summary({"read_file": True})
        self.assertEqual({"read_file": True, "write_file": False,
                          "memory_search": False},
                         {item["name"]: item["available"] for item in summary})
        read_hash = summary[0]["schema_hash"]
        other = contract_summary({"read_file": True})
        self.assertEqual(read_hash, other[0]["schema_hash"])
        self.assertFalse(summary[1]["available"])
        self.assertEqual(summary[1]["schema_hash"], other[1]["schema_hash"])

    def test_provider_tool_copy_cannot_mutate_the_shared_contract(self):
        offered = provider_tool("write_file")
        offered["function"]["parameters"]["properties"]["path"]["description"] = "changed"
        self.assertEqual(
            "Workspace-relative new path",
            CONTRACTS["write_file"].parameters["properties"]["path"]["description"])

    def test_task_policy_tools_are_imported_from_the_contract(self):
        payload = {
            "schema_version": "pocketchat-task-policy.v1",
            "objective": "read one file",
            "max_steps": 1,
            "max_tool_calls": 1,
            "max_context_chars": 100,
            "max_turn_tokens": 100,
            "tools": {"read_file": {"allow_paths": ["facts.txt"]}},
        }
        self.assertEqual(["read_file"],
                         list(parse_policy(payload).tools))
        payload["tools"]["run_command"] = {"argv": ["sh", "-c", "true"]}
        with self.assertRaises(TaskPolicyError):
            parse_policy(payload)


class HostValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "facts.txt").write_text("secret-file-content\n", encoding="utf-8")

    def _response(self, name, arguments):
        return {
            "content": "",
            "finish_reason": "tool_calls",
            "model": "m",
            "usage": {"input_tokens": 2, "output_tokens": 1,
                      "cache_read_tokens": 0, "cache_write_tokens": 0,
                      "reasoning_tokens": 0},
            "usage_reported": True,
            "tool_calls": [{"id": "call_1", "name": name,
                            "arguments": arguments}],
        }

    def _config(self, adapter):
        return {
            "profile": "contracts", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 4000,
            "summary_prefix_chars": 40, "requires_api_key": False,
            "provider_adapter": adapter,
            "tool_read_enabled": False,
            "tool_write_enabled": False,
            "tool_memory_search_enabled": False,
            "tool_call_max_count": 2,
            "tool_result_max_bytes": 4096,
        }

    def test_disabled_contracts_are_not_offered_by_default(self):
        config = self._config(object())
        self.assertNotIn("tools", build_payload(
            [{"role": "user", "content": "hello"}], config))

    def test_unknown_arguments_fail_before_file_or_memory_access(self):
        class Adapter:
            name = "contract-test"

            def __init__(self):
                self.responses = [
                    self.response("read_file",
                                  {"path": "facts.txt", "recursive": True}),
                    self.response("write_file",
                                  {"path": "new.txt", "content": "text",
                                   "overwrite": True}),
                    self.response("memory_search",
                                  {"query": "anything", "limit": 2}),
                    {"content": "done", "finish_reason": "stop", "model": "m",
                     "usage_reported": True,
                     "usage": {"input_tokens": 1, "output_tokens": 1,
                               "cache_read_tokens": 0, "cache_write_tokens": 0,
                               "reasoning_tokens": 0}},
                ]
                self.calls = []

            def response(self, name, arguments):
                return {
                    "content": "", "finish_reason": "tool_calls", "model": "m",
                    "usage_reported": True,
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                              "cache_read_tokens": 0, "cache_write_tokens": 0,
                              "reasoning_tokens": 0},
                    "tool_calls": [{"id": "call_1", "name": name,
                                    "arguments": arguments}],
                }

            def build_payload(self, messages, config):
                return build_payload(messages, config)

            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                self.calls.append(list(messages))
                return self.responses.pop(0)

        adapter = Adapter()
        config = self._config(adapter)
        config["tool_read_enabled"] = True
        config["tool_read_allow_paths"] = ["facts.txt"]
        config["tool_write_enabled"] = True
        config["tool_write_allow_paths"] = ["new.txt"]
        config["tool_memory_search_enabled"] = True
        config["tool_call_max_count"] = 4
        session = Session(config, self.root / "traces" / "contracts.jsonl",
                          "run_contracts", api_key="")
        self.assertEqual("completed", session.run(["try"]))
        self.assertEqual(4, len(adapter.calls))
        for index in (1, 2, 3):
            result = json.loads(adapter.calls[index][-1]["content"])
            self.assertEqual("invalid_arguments", result["error"]["type"])
        events = [json.loads(line) for line in
                  (self.root / "traces" / "contracts.jsonl").read_text().splitlines()]
        invalid = [event for event in events
                   if event["type"] == "tool.call.failed"]
        self.assertEqual(3, len(invalid))
        for event in invalid:
            self.assertEqual("invalid_arguments",
                             event["payload"]["error"]["type"])
        self.assertNotIn("permission.requested", [event["type"] for event in events])
        self.assertFalse((self.root / "new.txt").exists())
        self.assertNotIn("secret-file-content",
                         (self.root / "traces" / "contracts.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
