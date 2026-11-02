"""Retry/fallback state-machine and trace contract tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.provider import ProviderError
from pocketchat.config import ConfigError, load_config, profile_config
from pocketchat.session import Session


ROOT = Path(__file__).resolve().parents[1]


def response(model):
    return {
        "content": "ok", "finish_reason": "stop", "model": model,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
    }


class SequenceAdapter:
    name = "sequence"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def build_payload(self, messages, config):
        return {"model": config["model"], "messages": messages, "stream": False}

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append((config["profile"], config["model"], api_key))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return response(config["model"])


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_session(self, outcomes, *, retry_attempts=1, fallback=False):
        adapter = SequenceAdapter(outcomes)
        fallback_config = {
            "profile": "fallback", "model": "fallback-model",
            "base_url": "http://fallback.invalid/v1",
            "api_key_env": "FALLBACK_KEY", "timeout_seconds": 5,
            "max_context_chars": 1000, "summary_prefix_chars": 40,
            "requires_api_key": True, "retry_max_attempts": 2,
            "retry_backoff_ms": 0,
        } if fallback else None
        config = {
            "profile": "primary", "model": "primary-model",
            "base_url": "http://primary.invalid/v1",
            "api_key_env": "PRIMARY_KEY", "timeout_seconds": 5,
            "max_context_chars": 1000, "summary_prefix_chars": 40,
            "requires_api_key": True, "retry_max_attempts": retry_attempts,
            "retry_backoff_ms": 0, "fallback_max_attempts": 2,
            "provider_adapter": adapter, "fallback_config": fallback_config,
        }
        previous = dict(os.environ)
        os.environ["PRIMARY_KEY"] = "primary"
        os.environ["FALLBACK_KEY"] = "fallback"
        try:
            session = Session(config, self.root / "traces" / "run.jsonl",
                              "run_recovery", api_key="primary")
            status = session.run(["hello"])
        finally:
            os.environ.clear()
            os.environ.update(previous)
        events = [json.loads(line) for line in
                  (self.root / "traces" / "run.jsonl").read_text(
                      encoding="utf-8").splitlines()]
        return status, events, adapter.calls

    def test_retryable_failure_retries_with_separate_request_ordinal(self):
        status, events, calls = self.run_session([
            ProviderError("rate_limited", "later", retryable=True),
            None,
        ], retry_attempts=2)
        self.assertEqual("completed", status)
        self.assertEqual(2, len(calls))
        types = [event["type"] for event in events]
        self.assertIn("retry.attempt", types)
        retry = next(event for event in events if event["type"] == "retry.attempt")
        self.assertEqual(1, retry["payload"]["retry_index"])
        self.assertEqual(1, retry["payload"]["from_attempt_index"])
        self.assertEqual(2, retry["payload"]["to_attempt_index"])
        self.assertEqual(0, retry["payload"]["backoff_ms"])
        completed = next(event for event in events
                         if event["type"] == "model.response.completed")
        self.assertEqual(2, completed["payload"]["attempt_index"])

    def test_nonretryable_failure_does_not_retry(self):
        status, events, calls = self.run_session([
            ProviderError("unauthorized", "bad", retryable=False),
        ], retry_attempts=3)
        self.assertEqual("failed", status)
        self.assertEqual(1, len(calls))
        self.assertNotIn("retry.attempt", [event["type"] for event in events])

    def test_retry_budget_exhaustion_ends_failed(self):
        status, events, calls = self.run_session([
            ProviderError("timeout", "one", retryable=True),
            ProviderError("timeout", "two", retryable=True),
        ], retry_attempts=2)
        self.assertEqual("failed", status)
        self.assertEqual(2, len(calls))
        self.assertEqual(1, sum(event["type"] == "retry.attempt"
                                for event in events))
        self.assertEqual("run.failed", events[-1]["type"])

    def test_fallback_is_activated_after_primary_budget(self):
        status, events, calls = self.run_session([
            ProviderError("timeout", "one", retryable=True),
            ProviderError("timeout", "two", retryable=True),
            None,
        ], retry_attempts=2, fallback=True)
        self.assertEqual("completed", status)
        self.assertEqual(
            [("primary", "primary-model", "primary"),
             ("primary", "primary-model", "primary"),
             ("fallback", "fallback-model", "fallback")],
            calls)
        activated = next(event for event in events
                         if event["type"] == "fallback.activated")
        self.assertEqual("primary", activated["payload"]["from_profile"])
        self.assertEqual("fallback", activated["payload"]["to_profile"])
        self.assertEqual(2, activated["payload"]["from_attempt_index"])
        self.assertEqual(3, activated["payload"]["to_attempt_index"])

    def test_fallback_failure_has_a_terminal_event(self):
        status, events, calls = self.run_session([
            ProviderError("timeout", "one", retryable=True),
            ProviderError("timeout", "two", retryable=True),
            ProviderError("timeout", "three", retryable=True),
            ProviderError("timeout", "four", retryable=True),
        ], retry_attempts=2, fallback=True)
        self.assertEqual("failed", status)
        self.assertEqual(4, len(calls))
        self.assertEqual("run.failed", events[-1]["type"])
        self.assertEqual(1, sum(event["type"] == "fallback.activated"
                                for event in events))

    def test_interrupt_during_backoff_cancels_without_next_request(self):
        adapter = SequenceAdapter([
            ProviderError("timeout", "one", retryable=True), None])
        config = {
            "profile": "primary", "model": "primary-model",
            "base_url": "http://primary.invalid/v1", "api_key_env": "PRIMARY_KEY",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
            "retry_max_attempts": 2, "retry_backoff_ms": 10,
            "provider_adapter": adapter,
        }
        with mock.patch("pocketchat.session.time.sleep",
                        side_effect=KeyboardInterrupt):
            session = Session(config, self.root / "traces" / "cancel.jsonl",
                              "run_cancel", api_key="primary")
            status = session.run(["hello"])
        self.assertEqual("cancelled", status)
        self.assertEqual(1, len(adapter.calls))
        events = [json.loads(line) for line in
                  (self.root / "traces" / "cancel.jsonl").read_text(
                      encoding="utf-8").splitlines()]
        self.assertEqual("run.cancelled", events[-1]["type"])

    def test_recovery_events_are_declared_by_the_schema(self):
        schema = json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        self.assertIn("retry.attempt", schema["properties"]["type"]["enum"])
        self.assertIn("fallback.activated", schema["properties"]["type"]["enum"])
        contracts = {
            item["if"]["properties"]["type"]["const"]:
                set(item["then"]["properties"]["payload"]["required"])
            for item in schema["allOf"]
            if item.get("if", {}).get("properties", {})
            .get("type", {}).get("const") in ("retry.attempt", "fallback.activated")
        }
        self.assertIn("backoff_ms", contracts["retry.attempt"])
        self.assertIn("to_profile", contracts["fallback.activated"])

    def test_config_accepts_recovery_budgets_and_one_fallback(self):
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "test", "default_profile": "primary",
            "profiles": {
                "primary": {
                    "base_url": "http://primary.invalid/v1", "model": "one",
                    "api_key_env": "PRIMARY_KEY",
                    "retry_max_attempts": 2, "retry_backoff_ms": 5,
                    "fallback_max_attempts": 2, "fallback_profile": "backup",
                },
                "backup": {
                    "base_url": "http://backup.invalid/v1", "model": "two",
                    "api_key_env": "BACKUP_KEY",
                },
            },
        }), encoding="utf-8")
        config = load_config(path)
        selected = profile_config(config, "primary")
        self.assertEqual(2, selected["retry_max_attempts"])
        self.assertEqual(5, selected["retry_backoff_ms"])
        self.assertEqual(2, selected["fallback_max_attempts"])
        self.assertEqual("backup", selected["fallback_config"]["profile"])

    def test_config_rejects_unknown_or_chained_fallbacks(self):
        for name, backup in (("missing", "absent"), ("chain", "second")):
            profiles = {
                "primary": {
                    "base_url": "http://one.invalid/v1", "model": "one",
                    "api_key_env": "ONE", "fallback_profile": backup,
                },
                "second": {
                    "base_url": "http://two.invalid/v1", "model": "two",
                    "api_key_env": "TWO", "fallback_profile": "primary",
                },
            }
            path = self.root / ("%s.json" % name)
            path.write_text(json.dumps({
                "schema_version": "2", "name": name,
                "default_profile": "primary", "profiles": profiles,
            }), encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
