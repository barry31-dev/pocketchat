"""Per-turn and per-session token budget policy tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.session import Session


class FakeAdapter:
    name = "budget-test"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def build_payload(self, messages, config):
        return {"model": config["model"], "messages": messages, "stream": False}

    def complete(self, messages, config, api_key, on_delta=None):
        self.calls.append(list(messages))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(input_tokens=2, output_tokens=2, reported=True):
    return {
        "content": "reply", "finish_reason": "stop", "model": "m",
        "usage": {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        },
        "usage_reported": reported,
    }


class SessionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = {
            "profile": "test", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
        }

    def run_session(self, adapter, **budgets):
        config = {**self.base, "provider_adapter": adapter, **budgets}
        session = Session(config, self.root / "trace.jsonl", "run_budget",
                          api_key="k")
        return session, session.run(["hello"])

    def test_turn_budget_is_checked_before_the_request(self):
        adapter = FakeAdapter([response()])
        session, status = self.run_session(adapter, max_turn_tokens=1)
        self.assertEqual("failed", status)
        self.assertEqual([], adapter.calls)
        events = [json.loads(x) for x in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual("run.failed", events[-1]["type"])
        self.assertEqual("token_budget_exceeded", events[-1]["payload"]["error"]["type"])
        self.assertNotIn("model.request.started", [e["type"] for e in events])

    def test_turn_budget_accepts_a_request_inside_the_limit_and_counts_reported_usage(self):
        adapter = FakeAdapter([response(2, 2)])
        _, status = self.run_session(adapter, max_turn_tokens=100)
        self.assertEqual("completed", status)
        self.assertEqual(1, len(adapter.calls))

    def test_reported_usage_over_turn_budget_fails_closed(self):
        adapter = FakeAdapter([response(80, 30)])
        config = {**self.base, "provider_adapter": adapter,
                  "max_turn_tokens": 100}
        session_file = self.root / "sessions" / "budget.jsonl"
        session = Session(config, self.root / "trace-budget.jsonl", "run_budget",
                          session_path=session_file, api_key="k")
        status = session.run(["hello"])
        self.assertEqual("failed", status)
        self.assertEqual([], [m for m in session.messages
                              if m.get("role") == "assistant"])
        self.assertEqual(1, len(session_file.read_text().splitlines()))
        events = [json.loads(x) for x in
                  (self.root / "trace-budget.jsonl").read_text().splitlines()]
        self.assertIn("model.response.completed", [e["type"] for e in events])
        self.assertEqual("token_budget_exceeded", events[-1]["payload"]["error"]["type"])

    def test_session_budget_uses_reported_usage_plus_next_input_estimate(self):
        adapter = FakeAdapter([response(20, 10), response(20, 10)])
        config = {**self.base, "provider_adapter": adapter,
                  "max_session_tokens": 25}
        session = Session(config, self.root / "trace.jsonl", "run_budget",
                          api_key="k")
        status = session.run(["hello", "again"])
        self.assertEqual("failed", status)
        self.assertEqual(1, len(adapter.calls))
        events = [json.loads(x) for x in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual("token_budget_exceeded", events[-1]["payload"]["error"]["type"])

    def test_unreported_usage_with_session_budget_fails_closed(self):
        adapter = FakeAdapter([response(0, 0, reported=False)])
        _, status = self.run_session(adapter, max_session_tokens=100)
        self.assertEqual("failed", status)
        events = [json.loads(x) for x in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual("token_usage_unknown", events[-1]["payload"]["error"]["type"])

    def test_fallback_uses_its_own_token_budget(self):
        from pocketchat.provider import ProviderError

        adapter = FakeAdapter([
            ProviderError("timeout", "primary failed", retryable=True),
            response(50, 5),
        ])
        fallback = {
            **self.base, "profile": "fallback", "model": "fallback-model",
            "api_key_env": "", "requires_api_key": False,
            "max_turn_tokens": 1,
        }
        config = {
            **self.base, "provider_adapter": adapter,
            "retry_max_attempts": 1, "fallback_profile": "fallback",
            "fallback_config": fallback,
        }
        session = Session(config, self.root / "fallback.jsonl", "run_fallback",
                          api_key="k")
        status = session.run(["hello"])
        self.assertEqual("failed", status)
        self.assertEqual(1, len(adapter.calls))
        events = [json.loads(x) for x in
                  (self.root / "fallback.jsonl").read_text().splitlines()]
        self.assertEqual("token_budget_exceeded", events[-1]["payload"]["error"]["type"])

    def test_retry_usage_is_counted_within_the_session_budget(self):
        from pocketchat.provider import ProviderError

        adapter = FakeAdapter([
            response(20, 0),
            ProviderError("timeout", "retry", retryable=True),
            response(20, 0),
        ])
        config = {
            **self.base, "provider_adapter": adapter,
            "retry_max_attempts": 2, "retry_backoff_ms": 0,
            "max_session_tokens": 100,
        }
        session = Session(config, self.root / "retry-budget.jsonl",
                          "run_retry_budget", api_key="k")
        status = session.run(["one", "two"])
        self.assertEqual("completed", status)
        events = [json.loads(x) for x in
                  (self.root / "retry-budget.jsonl").read_text().splitlines()]
        finished = events[-1]["payload"]
        self.assertEqual(40, finished["total_input_tokens"])
        self.assertFalse(events[-1]["payload"]["status"] == "failed")


if __name__ == "__main__":
    unittest.main()
