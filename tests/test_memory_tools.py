"""Automatic read-only memory search tool tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.memory import add_memory
from pocketchat.provider import build_payload
from pocketchat.session import Session


class FakeMemoryAdapter:
    name = "memory-tool-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append(list(messages))
        return self.responses.pop(0)


def memory_response(call_id, query):
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
            "name": "memory_search",
            "arguments": {"query": query},
        }],
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


class MemoryToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        add_memory(self.root, "the answer is 42", tags=["fact"])

    def config(self, adapter):
        return {
            "profile": "memory", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
            "provider_adapter": adapter,
            "tool_memory_search_enabled": True,
            "tool_memory_search_max_results": 2,
        }

    def test_payload_offers_only_memory_search_when_enabled(self):
        config = self.config(FakeMemoryAdapter([]))
        config["tool_read_enabled"] = False
        payload = build_payload([{"role": "user", "content": "search"}], config)
        self.assertEqual(["memory_search"],
                         [tool["function"]["name"] for tool in payload["tools"]])

    def test_model_searches_memory_and_sends_results(self):
        adapter = FakeMemoryAdapter([
            memory_response("call_m", "answer"), text_response(),
        ])
        session = Session(self.config(adapter), self.root / "traces" / "run.jsonl",
                          "run_memory", api_key="secret-tool")
        self.assertEqual("completed", session.run(["search memory"]))
        self.assertEqual(2, len(adapter.calls))
        self.assertIn("the answer is 42", adapter.calls[1][-1]["content"])

    def test_memory_results_are_bounded_without_becoming_an_empty_search(self):
        for index in range(8):
            add_memory(self.root, ("answer-%d-" % index) + "x" * 240,
                       tags=["fact"])
        adapter = FakeMemoryAdapter([
            memory_response("call_m", "answer"), text_response(),
        ])
        config = self.config(adapter)
        config["tool_memory_search_max_results"] = 10
        config["tool_result_max_bytes"] = 900
        session = Session(config, self.root / "traces" / "bounded.jsonl",
                          "run_memory_bounded", api_key="secret-tool")
        self.assertEqual("completed", session.run(["search memory"]))
        result = json.loads(adapter.calls[1][-1]["content"])
        self.assertEqual("memory_search", result["kind"])
        self.assertTrue(result["truncated"])
        self.assertGreater(result["bytes_omitted"], 0)
        self.assertGreater(len(result["items"]), 0)

    def test_oversized_memory_record_is_omitted_inside_the_full_envelope(self):
        add_memory(self.root, "oversized-" + "x" * 600, tags=["fact"])
        adapter = FakeMemoryAdapter([
            memory_response("call_m", "oversized"), text_response(),
        ])
        config = self.config(adapter)
        config["tool_result_max_bytes"] = 512
        config["max_context_chars"] = 4096
        session = Session(config, self.root / "traces" / "large.jsonl",
                          "run_large_memory", api_key="secret-tool")
        self.assertEqual("completed", session.run(["search memory"]))
        encoded = adapter.calls[1][-1]["content"]
        result = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode("utf-8")), 512)
        self.assertEqual([], result["items"])
        self.assertEqual(1, result["total_items"])
        self.assertEqual(1, result["omitted_items"])
        self.assertTrue(result["truncated"])

    def test_task_budget_missing_usage_stops_before_a_tool_or_second_request(self):
        from pocketchat.task_budget import TaskBudget
        response = memory_response("call_m", "answer")
        response["usage_reported"] = False
        adapter = FakeMemoryAdapter([response, text_response()])
        trace = self.root / "traces" / "unknown.jsonl"
        session = Session(self.config(adapter), trace,
                          "run_unknown_usage", api_key="secret-tool")
        session.task_budget = TaskBudget(limit_tokens=10000)
        self.assertEqual("failed", session.run(["search memory"]))
        self.assertEqual(1, len(adapter.calls))
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertNotIn("tool.call.started", [event["type"] for event in events])
        self.assertIn("task_token_usage_unknown", trace.read_text())

    def test_malformed_usage_blocks_the_shared_task_ledger(self):
        from pocketchat.task_budget import TaskBudget, TaskBudgetError
        response = text_response()
        response["usage"] = {"input_tokens": "bad", "output_tokens": 1}
        adapter = FakeMemoryAdapter([response])
        trace = self.root / "traces" / "malformed-usage.jsonl"
        session = Session(self.config(adapter), trace,
                          "run_malformed_usage", api_key="secret-tool")
        session.task_budget = TaskBudget(limit_tokens=10000)
        self.assertEqual("failed", session.run(["short prompt"]))
        self.assertEqual(1, len(adapter.calls))
        with self.assertRaises(TaskBudgetError) as caught:
            session.task_budget.reserve(1, request_id="after_malformed_usage")
        self.assertEqual("task_token_usage_unknown", caught.exception.error_type)
        self.assertIn("task_token_usage_unknown", trace.read_text())

    def test_task_reservation_includes_the_configured_output_reserve(self):
        from pocketchat.task_budget import TaskBudget
        adapter = FakeMemoryAdapter([text_response()])
        config = self.config(adapter)
        config["output_reserve_tokens"] = 1000
        session = Session(config, self.root / "traces" / "reserve.jsonl",
                          "run_output_reserve", api_key="secret-tool")
        session.task_budget = TaskBudget(limit_tokens=1000)
        self.assertEqual("failed", session.run(["short prompt"]))
        self.assertEqual([], adapter.calls)

    def test_unknown_transport_usage_stops_budgeted_retry(self):
        from pocketchat.provider import ProviderError
        from pocketchat.task_budget import TaskBudget, TaskBudgetError

        class FailingAdapter(FakeMemoryAdapter):
            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                self.calls.append(list(messages))
                raise ProviderError("timeout", "fixture timeout", retryable=True)

        adapter = FailingAdapter([])
        config = self.config(adapter)
        config["retry_max_attempts"] = 2
        session = Session(config, self.root / "traces" / "transport.jsonl",
                          "run_transport_unknown", api_key="secret-tool")
        session.task_budget = TaskBudget(limit_tokens=10000)
        self.assertEqual("failed", session.run(["short prompt"]))
        self.assertEqual(1, len(adapter.calls))
        with self.assertRaises(TaskBudgetError) as caught:
            session.task_budget.reserve(1, request_id="after_failure")
        self.assertEqual("task_token_usage_unknown", caught.exception.error_type)

    def test_memory_search_is_disabled_by_default(self):
        config = self.config(FakeMemoryAdapter([]))
        config["tool_memory_search_enabled"] = False
        payload = build_payload([{"role": "user", "content": "search"}], config)
        self.assertNotIn("tools", payload)


if __name__ == "__main__":
    unittest.main()
