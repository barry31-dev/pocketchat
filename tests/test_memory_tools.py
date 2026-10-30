"""Automatic read-only memory search tool tests."""

from __future__ import annotations

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

    def complete(self, messages, config, api_key, on_delta=None):
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

    def test_memory_search_is_disabled_by_default(self):
        config = self.config(FakeMemoryAdapter([]))
        config["tool_memory_search_enabled"] = False
        payload = build_payload([{"role": "user", "content": "search"}], config)
        self.assertNotIn("tools", payload)


if __name__ == "__main__":
    unittest.main()
