"""Machine-readable output contracts for `show` and `export`.

The human renderings are already covered by the command suites. This module
pins the automation half: one JSON document on stdout, relative artifact
paths, stable error objects and no diagnostic text mixed into stdout.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests._helpers import run_cli


class CliJsonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _build(self):
        sessions = self.base / "sessions"
        traces = self.base / "traces"
        sessions.mkdir()
        traces.mkdir()
        session = sessions / "notes.jsonl"
        session.write_text("\n".join([
            json.dumps({
                "session": "run_json",
                "schema_version": "1",
                "created": "2026-10-27T00:00:00.000Z",
                "pocketchat_version": "test",
                "trace": "../traces/run_json.jsonl",
                "conversation_id": "conv_json",
                "branch_id": "branch_json",
            }, sort_keys=True),
            json.dumps({"role": "user", "content": "hello",
                        "turn_id": "turn_000001"}, sort_keys=True),
            json.dumps({"role": "assistant", "content": "hi",
                        "turn_id": "turn_000002"}, sort_keys=True),
        ]) + "\n", encoding="utf-8")
        trace = traces / "run_json.jsonl"
        trace.write_text("\n".join([
            json.dumps({
                "schema_version": "1", "event_id": "evt_000001",
                "timestamp": "2026-10-27T00:00:00.000Z",
                "run_id": "run_json", "type": "run.started",
            }, sort_keys=True),
            json.dumps({
                "schema_version": "1", "event_id": "evt_000002",
                "timestamp": "2026-10-27T00:00:00.100Z",
                "run_id": "run_json", "type": "run.finished",
            }, sort_keys=True),
        ]) + "\n", encoding="utf-8")
        return session, trace

    def test_show_json_emits_one_safe_document(self):
        session, trace = self._build()
        result = run_cli(("show", "notes", "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(sorted(payload),
                         ["ok", "schema_version", "session", "trace",
                          "trace_problem"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], "1")
        self.assertEqual(payload["session"]["path"], "sessions/notes.jsonl")
        self.assertEqual(payload["session"]["turns"][0]["turn_id"],
                         "turn_000001")
        self.assertEqual(payload["trace"]["path"], "traces/run_json.jsonl")
        self.assertEqual([event["type"] for event in payload["trace"]["events"]],
                         ["run.started", "run.finished"])
        self.assertIsNone(payload["trace_problem"])
        self.assertNotIn(str(session), result.stdout)
        self.assertNotIn(str(trace), result.stdout)

    def test_show_json_carries_the_context_compaction_payload(self):
        session, trace = self._build()
        lines = trace.read_text(encoding="utf-8").splitlines()
        lines.insert(1, json.dumps({
            "schema_version": "1", "event_id": "evt_000009",
            "timestamp": "2026-10-27T00:00:00.050Z",
            "run_id": "run_json", "type": "context.compacted",
            "payload": {"strategy": "drop_oldest_turns",
                        "dropped_messages": 2, "kept_messages": 1,
                        "kept_chars": 10, "budget_chars": 20,
                        "estimated_tokens_before": 8,
                        "estimated_tokens_after": 3},
        }, sort_keys=True))
        trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = run_cli(("show", "notes", "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        event = [item for item in payload["trace"]["events"]
                 if item["type"] == "context.compacted"][0]
        self.assertEqual(event["payload"]["dropped_messages"], 2)
        self.assertEqual(event["payload"]["budget_chars"], 20)

    def test_show_json_failure_is_one_stable_error_object(self):
        result = run_cli(("show", "missing", "--json"), self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": False,
            "schema_version": "1",
            "error": "session_file",
            "message": "no such session: missing",
        })

    def test_show_json_is_documented_but_human_output_stays_default(self):
        self._build()
        help_result = run_cli(("show", "--help"), self.base)
        self.assertIn("--json", help_result.stdout)
        human = run_cli(("show", "notes"), self.base)
        self.assertEqual(human.returncode, 0, human.stdout + human.stderr)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(human.stdout)

    def test_export_json_emits_success_with_relative_artifact_paths(self):
        self._build()
        out = self.base / "exports" / "notes.jsonl"
        result = run_cli(
            ("export", "notes", "--out", str(out), "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertEqual(payload, {
            "ok": True,
            "schema_version": "1",
            "format": "jsonl",
            "source": "sessions/notes.jsonl",
            "session": "sessions/notes.jsonl",
            "out": "exports/notes.jsonl",
            "messages": 2,
            "trace_events": 2,
        })
        self.assertTrue(out.is_file())
        self.assertNotIn(str(self.base), result.stdout)

    def test_export_json_for_trace_only_reports_its_actual_source(self):
        _, trace = self._build()
        out = self.base / "exports" / "run.trace.jsonl"
        result = run_cli((
            "export", str(trace), "--format", "trace", "--out", str(out),
            "--json"), self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source"], "traces/run_json.jsonl")
        self.assertIsNone(payload["session"])
        self.assertIsNone(payload["messages"])
        self.assertEqual(payload["trace_events"], 2)
        self.assertEqual(payload["out"], "exports/run.trace.jsonl")

    def test_export_json_failure_is_stable_and_does_not_overwrite(self):
        self._build()
        out = self.base / "existing.jsonl"
        out.write_bytes(b"keep me\n")
        result = run_cli(
            ("export", "notes", "--out", str(out), "--json"), self.base)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout), {
            "ok": False,
            "schema_version": "1",
            "error": "export",
            "message": "%s already exists (use --out)" % out,
        })
        self.assertEqual(out.read_bytes(), b"keep me\n")


if __name__ == "__main__":
    unittest.main()
