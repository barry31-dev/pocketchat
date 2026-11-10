"""Real read observations reach a later report, never model prose alone."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.agent import run_task
from pocketchat.task_exec import TaskExecutionError, authorize, execute_plan
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal


def report_execution(*, workspace=".", source="input.md", output="report.md", extra_source=None, max_context=16000):
    spec = parse_spec(minimal(
        objective="Read local facts and save a cited report",
        workspace_root=workspace, inputs=[source] + ([extra_source] if extra_source else []),
        tools={"read_file": {"allow_paths": [source] + ([extra_source] if extra_source else [])},
               "write_file": {"allow_paths": [output]}},
        budgets={**minimal()["budgets"], "max_context_chars": max_context,
                   "max_turn_tokens": 16000, "max_session_tokens": 48000},
    ))
    plan = parse_plan({
        "schema_version": "pocketchat-task-plan.v1",
        "spec_sha256": spec_sha256(spec), "revision": 0,
        "steps": [
            {"id": "read", "objective": "Read the allowed material",
             "action": "read_file", "inputs": [source] + ([extra_source] if extra_source else []),
             "expected_evidence": "Observed UTF-8 content",
             "depends_on": [], "retry_limit": 0},
            {"id": "report", "objective": "Write a report with source citations",
             "action": "write_file", "inputs": [output],
             "expected_evidence": "Report bytes",
             "depends_on": ["read"], "retry_limit": 0},
        ],
    }, spec=spec)
    return authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))


def responses(source="input.md", output="report.md"):
    return [model_tool("read-call", "read_file", {"path": source}),
            final_response(),
            model_tool("write-call", "write_file",
                       {"path": output, "content": "fixture report\n"}),
            final_response()]


@unittest.skipUnless(os.open in os.supports_dir_fd,
                     "descriptor-relative evidence reads required")
class LocalReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.content = "Observed value: 17. No revenue figure was supplied.\n"
        (self.root / "input.md").write_text(self.content, encoding="utf-8")
        self.execution = report_execution()

    def execute(self, adapter, **extra):
        return execute_plan(
            self.execution, run_task=run_task, workspace=self.root,
            config={"model": "task-model", "base_url": "http://stub.invalid/v1",
                    "api_key_env": "", "requires_api_key": False,
                    "summary_prefix_chars": 80, "timeout_seconds": 5,
                    "provider_adapter": adapter},
            trace_dir=self.root / "traces", session_dir=self.root / "sessions",
            **extra)

    def source_state(self, adapter, call_index):
        prompt = next(m["content"] for m in adapter.calls[call_index]
                      if m["role"] == "user")
        self.assertIn("Dependency sources:\n", prompt,
                      "dependent report sees metadata, not its real read observation")
        return json.loads(prompt.split("Dependency sources:\n", 1)[1])

    def test_dependent_report_receives_exact_observed_content_and_digest(self):
        adapter = FakeTaskAdapter(responses())
        results = self.execute(adapter)
        self.assertTrue(all(row["status"] == "completed" for row in results))
        state = self.source_state(adapter, 2)
        self.assertEqual("pocketchat-task-sources.v1", state["schema_version"])
        self.assertEqual(1, len(state["sources"]))
        source = state["sources"][0]
        self.assertEqual("read", source["step_id"])
        self.assertEqual("input.md", source["path"])
        self.assertEqual(self.content, source["content"])
        self.assertEqual(hashlib.sha256(self.content.encode()).hexdigest(),
                         source["sha256"])
        self.assertEqual(len(self.content.encode()), source["bytes"])

    def test_resume_uses_pinned_historical_read_without_rereading_live_input(self):
        first = FakeTaskAdapter(responses()[:2])
        result = self.execute(first, stop_after=1)
        report = self.root / result[0]["execution_report"]
        digest = hashlib.sha256(report.read_bytes()).hexdigest()
        (self.root / "input.md").write_text("Changed live value: 99\n")
        second = FakeTaskAdapter(responses()[2:])
        resumed = self.execute(second, resume_report=report,
                               expected_report_sha256=digest)
        self.assertEqual(2, len(second.calls))
        self.assertTrue(all(row["status"] == "completed" for row in resumed))
        source = self.source_state(second, 0)["sources"][0]
        self.assertEqual(self.content, source["content"])
        self.assertNotIn("99", source["content"])
        self.assertEqual(2, len(first.calls))

    def test_nested_workspace_source_path_names_the_real_read_location(self):
        directory = self.root / "analysis"
        directory.mkdir()
        (directory / "input.md").write_text("Nested observed value: 23\n")
        self.execution = report_execution(workspace="analysis")
        adapter = FakeTaskAdapter(responses())
        self.execute(adapter)
        source = self.source_state(adapter, 2)["sources"][0]
        self.assertEqual("analysis/input.md", source["path"])
        self.assertIn("23", source["content"])
        self.assertTrue((directory / "report.md").is_file())

    def test_truncated_observation_refuses_before_the_report_provider_call(self):
        self.execution = report_execution(max_context=64000)
        (self.root / "input.md").write_text("x" * 20000)
        adapter = FakeTaskAdapter(responses())
        with self.assertRaisesRegex(TaskExecutionError, "incomplete source"):
            self.execute(adapter)
        self.assertEqual(2, len(adapter.calls))
        self.assertFalse((self.root / "report.md").exists())

    def test_aggregate_source_budget_refuses_before_the_report_provider_call(self):
        self.execution = report_execution(max_context=64000)
        (self.root / "input.md").write_text("x" * 13000)
        adapter = FakeTaskAdapter(responses())
        with self.assertRaisesRegex(TaskExecutionError, "context budget"):
            self.execute(adapter)
        self.assertEqual(2, len(adapter.calls))
        self.assertFalse((self.root / "report.md").exists())

    def first_read(self):
        adapter = FakeTaskAdapter(responses()[:2])
        results = self.execute(adapter, stop_after=1)
        return results[0]

    def append_sources(self, row):
        from pocketchat.task_sources import append_source_prompt
        return append_source_prompt(
            "Report step", root=self.root, step=self.execution.steps[1],
            execution=self.execution, results=[row])

    def rewrite_source_session(self, row, path):
        session = Path(row["session"])
        records = [json.loads(line) for line in session.read_text().splitlines()]
        for message in records[1:]:
            for entry in message.get("tool_context", []):
                if entry["role"] == "assistant":
                    entry["tool_calls"][0]["function"]["arguments"] = json.dumps({"path": path})
                else:
                    body = json.loads(entry["content"])
                    body["source"] = path
                    entry["content"] = json.dumps(body)
        session.write_text("".join(json.dumps(record) + "\n" for record in records))
        row["evidence_hashes"]["session"] = hashlib.sha256(session.read_bytes()).hexdigest()

    def test_other_allowlisted_path_cannot_impersonate_the_completed_read(self):
        self.execution = report_execution(extra_source="other.md")
        (self.root / "other.md").write_text("Different unobserved facts\n")
        row = self.first_read()
        self.rewrite_source_session(row, "other.md")
        from pocketchat.task_recovery import TaskRecoveryError
        with self.assertRaisesRegex(TaskRecoveryError, "completed read"):
            self.append_sources(row)

    def test_ungranted_source_result_is_not_admitted_even_if_rebound(self):
        row = self.first_read()
        self.rewrite_source_session(row, "ungranted.md")
        from pocketchat.task_recovery import TaskRecoveryError
        with self.assertRaisesRegex(TaskRecoveryError, "read grant|completed read"):
            self.append_sources(row)

    def test_changed_bound_session_refuses_without_provider_or_live_read(self):
        row = self.first_read()
        Path(row["session"]).write_text("changed\n")
        (self.root / "input.md").unlink()
        from pocketchat.task_recovery import TaskRecoveryError
        with self.assertRaisesRegex(TaskRecoveryError, "evidence bytes changed"):
            self.append_sources(row)

    def test_session_symlink_does_not_escape_the_evidence_root(self):
        row = self.first_read()
        session = Path(row["session"])
        target = self.root / "other-session.jsonl"
        target.write_bytes(session.read_bytes())
        session.unlink()
        session.symlink_to(target)
        from pocketchat.task_recovery import TaskRecoveryError
        with self.assertRaises(TaskRecoveryError):
            self.append_sources(row)

    def test_reusable_offline_cli_fixture_checks_report_revision_and_resume(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "local-report-demo.py"
        spec = importlib.util.spec_from_file_location("local_report_fixture", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        outcome = module.demonstrate(self.root / "golden")
        self.assertTrue(outcome["passed"], outcome)
        for name in ("source_grounded_report", "versioned_revision",
                     "history_preserved", "resume_without_read_replay",
                     "tampered_source_refused", "missing_source_refused"):
            self.assertTrue(outcome["assertions"][name], name)

    def test_rebound_session_cannot_hide_trace_recorded_truncation(self):
        self.execution = report_execution(max_context=64000)
        (self.root / "input.md").write_text("x" * 20000)
        row = self.first_read()
        session = Path(row["session"])
        records = [json.loads(line) for line in session.read_text().splitlines()]
        for message in records[1:]:
            for entry in message.get("tool_context", []):
                if entry["role"] == "tool":
                    body = json.loads(entry["content"])
                    body.update(content="x" * 100, bytes=100,
                                truncated=False, bytes_omitted=0)
                    entry["content"] = json.dumps(body)
        session.write_text("".join(json.dumps(record) + "\n" for record in records))
        row["evidence_hashes"]["session"] = hashlib.sha256(session.read_bytes()).hexdigest()
        from pocketchat.task_recovery import TaskRecoveryError
        with self.assertRaisesRegex(TaskRecoveryError, "trace"):
            self.append_sources(row)

    def test_rebound_record_cannot_claim_a_different_read_workspace(self):
        (self.root / "analysis").mkdir()
        (self.root / "analysis" / "input.md").write_text("Nested actual facts\n")
        self.execution = report_execution(workspace="analysis")
        row = self.first_read()
        from pocketchat.task_recovery import TaskRecoveryError, _evidence_paths
        record = self.root / _evidence_paths(self.root, row)["record"]
        data = json.loads(record.read_text())
        data["workspace_root"] = "."
        record.write_text(json.dumps(data))
        row["evidence_hashes"]["record"] = hashlib.sha256(record.read_bytes()).hexdigest()
        with self.assertRaisesRegex(TaskRecoveryError, "workspace"):
            self.append_sources(row)
