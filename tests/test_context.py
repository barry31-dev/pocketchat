"""Read-only context preview and dry-run compaction contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.context import ContextBudgetExceeded, preview_budget
from tests._helpers import run_cli


class ContextPreviewTests(unittest.TestCase):
    def test_preview_reports_indices_without_mutating_messages(self):
        messages = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "two"},
            {"role": "assistant", "content": "answer two"},
        ]
        before = [dict(message) for message in messages]
        result = preview_budget(messages, 24)
        self.assertEqual(before, messages)
        self.assertTrue(result["compacted"])
        self.assertEqual([2, 3], result["kept_indices"])
        self.assertEqual([0, 1], result["dropped_indices"])
        self.assertGreater(result["estimated_tokens_before"],
                           result["estimated_tokens_after"])

    def test_preview_keeps_everything_when_the_budget_is_wide(self):
        messages = [{"role": "user", "content": "hello"}]
        result = preview_budget(messages, 100)
        self.assertFalse(result["compacted"])
        self.assertEqual([0], result["kept_indices"])
        self.assertEqual([], result["dropped_indices"])

    def test_preview_refuses_a_budget_that_cannot_fit_a_pinned_prompt(self):
        with self.assertRaises(ContextBudgetExceeded):
            preview_budget([{"role": "user", "content": "long prompt"}], 1)


class ContextCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        session = self.base / "sessions" / "notes.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text("\n".join([
            json.dumps({"session": "run_context", "schema_version": "1",
                        "created": "2026-10-29T00:00:00.000Z",
                        "pocketchat_version": "test",
                        "trace": "../traces/run_context.jsonl"}),
            json.dumps({"role": "user", "content": "one"}),
            json.dumps({"role": "assistant", "content": "answer one"}),
            json.dumps({"role": "user", "content": "two"}),
            json.dumps({"role": "assistant", "content": "answer two"}),
        ]) + "\n", encoding="utf-8")
        trace = self.base / "traces" / "run_context.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text("\n".join([
            json.dumps({"schema_version": "1", "event_id": "evt_000001",
                        "timestamp": "2026-10-29T00:00:00.000Z",
                        "run_id": "run_context", "type": "run.started",
                        "payload": {"config_summary": {
                            "max_context_chars": 24}}}),
            json.dumps({"schema_version": "1", "event_id": "evt_000002",
                        "timestamp": "2026-10-29T00:00:01.000Z",
                        "run_id": "run_context", "type": "run.finished"}),
        ]) + "\n", encoding="utf-8")

    def test_context_json_is_read_only_and_uses_the_trace_budget(self):
        before = sorted(path.read_bytes() for path in self.base.rglob("*")
                        if path.is_file())
        result = run_cli(("context", "notes", "--json"), self.base)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(24, payload["budget_chars"])
        self.assertEqual([2, 3], payload["kept_indices"])
        self.assertEqual(before, sorted(path.read_bytes() for path in self.base.rglob("*")
                                         if path.is_file()))

    def test_context_budget_override_and_usage_error_are_stable(self):
        result = run_cli(("context", "notes", "--budget", "1", "--json"), self.base)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(result.stdout)["ok"])
        missing = run_cli(("context", "missing", "--json"), self.base)
        self.assertEqual(2, missing.returncode)
        self.assertFalse(json.loads(missing.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
