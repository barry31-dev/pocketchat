"""Read-only context preview and dry-run compaction contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.context import ContextBudgetExceeded, preview_budget
from pocketchat.session import Session
from pocketchat.sessions import read_context_messages
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


class ContextProjectionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.joinpath("one.md").write_text("one", encoding="utf-8")
        self.root.joinpath("two.md").write_text("two", encoding="utf-8")
        self.config = {
            "profile": "projection", "model": "m",
            "base_url": "http://example.invalid/v1", "api_key_env": "K",
            "timeout_seconds": 5, "max_context_chars": 1000,
            "summary_prefix_chars": 40, "requires_api_key": True,
            "context_projection_enabled": True,
            "context_projection_max_bytes": 1000,
        }

    def response(self, content="reply"):
        return {
            "content": content, "finish_reason": "stop", "model": "m",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "usage_reported": True,
        }

    def test_preview_selects_the_same_messages_the_session_sends(self):
        from pocketchat.provider import build_payload
        class Adapter:
            name = "projection-test"
            def __init__(self):
                self.calls = []
            def build_payload(self, messages, config):
                return build_payload(messages, config)
            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                self.calls.append(list(messages))
                return {"content": "reply", "finish_reason": "stop",
                        "model": "m", "usage": {"input_tokens": 1,
                                                 "output_tokens": 1},
                        "usage_reported": True}
        adapter = Adapter()
        config = dict(self.config, provider_adapter=adapter)
        session_path = self.root / "sessions" / "same.jsonl"
        session = Session(config, self.root / "traces" / "same.jsonl",
                          "run_same", session_path=session_path, api_key="k")
        self.assertEqual("completed", session.run(["first"]))
        resumed = Session(config, self.root / "traces" / "same.jsonl",
                          session_path=session_path, api_key="k")
        sent = resumed._context_with_budget("second")
        from pocketchat.service import context_preview
        preview = context_preview(
            session_path, workspace=self.root, budget_chars=1000,
            keep_recent=1, pending_prompt="second")
        self.assertEqual("first", preview["preview_messages"][0]["content"])
        self.assertEqual("assistant",
                         preview["preview_messages"][-2]["role"])
        self.assertEqual("second", preview["preview_messages"][-1]["content"])
        self.assertEqual("layered_v1", preview["report"]["strategy"])
        from pocketchat.context_projection import project_context
        direct = project_context(
            {"instructions": [],
             "messages": read_context_messages(session_path),
             "evidence": []}, max_chars=1000, keep_recent_turns=1,
            pending_messages=[{"role": "user", "content": "second"}])
        self.assertEqual(direct["messages"], sent)
        sidecar = (self.root / "traces" / "requests"
                   / "same.turn-1.projection.json")
        self.assertTrue(sidecar.is_file())
        sidecar_body = json.loads(sidecar.read_text(encoding="utf-8"))
        snapshot = self.root / "traces" / "requests" / "same.turn-1.json"
        snapshot_body = json.loads(snapshot.read_text(encoding="utf-8"))
        self.assertEqual(snapshot_body["request_sha256"],
                         sidecar_body["payload_sha256"])
        self.assertEqual("pocketchat-context-projection.v1",
                         sidecar_body["schema_version"])

    def test_projection_failure_does_not_send_or_create_a_snapshot(self):
        from pocketchat.provider import build_payload
        class Adapter:
            name = "projection-test"
            def __init__(self):
                self.calls = []
            def build_payload(self, messages, config):
                return build_payload(messages, config)
            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                self.calls.append(list(messages))
                return {"content": "reply", "finish_reason": "stop",
                        "model": "m", "usage": {"input_tokens": 1,
                                                 "output_tokens": 1},
                        "usage_reported": True}
        adapter = Adapter()
        config = dict(self.config, provider_adapter=adapter,
                      context_projection_max_bytes=1)
        session = Session(config, self.root / "traces" / "too-big.jsonl",
                          "run_too_big", api_key="k")
        self.assertEqual("failed", session.run(["hello"]))
        self.assertEqual([], adapter.calls)
        self.assertFalse((self.root / "traces" / "requests").exists())

    def test_trace_and_sidecar_expose_projection_binding(self):
        from pocketchat.provider import build_payload
        class Adapter:
            name = "projection-test"
            def build_payload(self, messages, config):
                return build_payload(messages, config)
            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                return {"content": "reply", "finish_reason": "stop",
                        "model": "m", "usage": {"input_tokens": 1,
                                                 "output_tokens": 1},
                        "usage_reported": True}
        config = dict(self.config, provider_adapter=Adapter())
        trace = self.root / "traces" / "binding.jsonl"
        session = Session(config, trace, "run_binding", api_key="k")
        self.assertEqual("completed", session.run(["hello"]))
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        started = [item for item in events
                   if item["type"] == "model.request.started"][0]
        self.assertEqual("layered_v1",
                         started["payload"]["context_projection"]["strategy"])
        self.assertTrue(started["payload"]["context_projection_sidecar"])
        sidecar = self.root / "traces" / started["payload"][
            "context_projection_sidecar"]
        sidecar_body = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual("run_binding", sidecar_body["run_id"])
        self.assertEqual(started["payload"]["request_sha256"],
                         sidecar_body["payload_sha256"])

    def test_projection_opt_in_is_default_off(self):
        from pocketchat.config import load_config, profile_config
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "p", "default_profile": "p",
            "profiles": {"p": {
                "base_url": "http://example.invalid/v1", "model": "m",
                "api_key_env": "K",
            }},
        }), encoding="utf-8")
        selected = profile_config(load_config(path))
        self.assertFalse(selected["context_projection_enabled"])
        self.assertEqual(65536, selected["context_projection_max_bytes"])

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
