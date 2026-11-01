"""Task report aggregation: read existing evidence, never execute it."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.cli import _task_report_payload
from pocketchat.tasking import load_record
from pocketchat.task_command import parse_command_templates, run_command_template
from pocketchat.task_exec import (EXECUTION_REPORT_SCHEMA, load_task_execution,
                                  execute_plan)
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec


def task_record(root: Path, *, state="completed") -> dict:
    record = {
        "schema_version": "pocketchat-task.v1",
        "task_id": "task_report_001",
        "created": "2026-10-31T00:00:00.000Z",
        "objective": "report existing evidence",
        "policy_sha256": "a" * 64,
        "state": state,
        "max_steps": 8,
        "max_tool_calls": 4,
        "workspace_root": ".",
        "terminal_reason": "task run completed",
        "steps": [
            {"index": 1, "kind": "model", "state": "completed",
             "recorded": "2026-10-31T00:00:01.000Z", "run_id": "run_model"},
            {"index": 2, "kind": "tool", "state": "completed",
             "tool": "write_file", "detail": "result.txt",
             "recorded": "2026-10-31T00:00:02.000Z", "run_id": "run_model"},
        ],
    }
    (root / ".pocketchat" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / ".pocketchat" / "tasks" / "task_report_001.json").write_text(
        json.dumps(record), encoding="utf-8")
    return record


class ReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_command_archive_is_aggregated_without_executing_it(self):
        template = parse_command_templates([{
            "id": "check", "argv": ["python3", "-c", "print('recorded')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "command.jsonl"
        result = run_command_template(self.root, template, trace_path=trace,
                                      run_id="run_command_001")
        task_record(self.root)
        report = self.root / ".pocketchat" / "task-reports" / "execution.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": "pocketchat-task-execution-report.v1",
            "report_id": "execution_1",
            "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64,
            "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "run_check", "action": "run_command",
                "run_id": "run_command_001", "status": "completed",
                "trace": "traces/command.jsonl",
                "evidence": result.to_dict(),
            }],
            "changes": [],
            "evidence_issues": [],
        }), encoding="utf-8")
        (self.root / "result.txt").write_text("new\n", encoding="utf-8")
        payload = _task_report_payload(self.root, load_record(self.root, "task_report_001"))
        self.assertEqual("completed", payload["state"])
        self.assertEqual("check", payload["commands"][0]["template_id"])
        self.assertEqual(0, payload["commands"][0]["exit_code"])
        self.assertEqual(64, len(payload["commands"][0]["stdout_sha256"]))
        self.assertFalse([item for item in payload["evidence_issues"]
                          if item.get("step_id")])

    def test_failed_command_evidence_is_listed_without_acceptance(self):
        spec = parse_spec({
            "schema_version": "pocketchat-task-spec.v1",
            "objective": "record a nonzero check",
            "workspace_root": ".",
            "inputs": ["check.txt"],
            "budgets": {
                "max_steps": 8, "max_tool_calls": 4,
                "max_context_chars": 8000, "max_turn_tokens": 2000,
                "max_session_tokens": 6000,
            },
            "tools": {},
            "commands": [{
                "id": "check", "argv": ["python3", "-c", "raise SystemExit(3)"],
                "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
            }],
            "acceptance": {"path": "acceptance.json"},
        })
        from pocketchat.task_exec import authorize
        plan = parse_plan({
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(spec), "revision": 0,
            "steps": [{
                "id": "run_check", "objective": "run the failing check",
                "action": "run_command", "inputs": ["check"],
                "expected_evidence": "exit status", "depends_on": [],
                "retry_limit": 0,
            }],
        }, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        template = parse_command_templates([{
            "id": "check", "argv": ["python3", "-c", "raise SystemExit(3)"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "run_check.jsonl"
        command = run_command_template(self.root, template, trace_path=trace,
                                       run_id="run_check")
        task_record(self.root)
        report = self.root / ".pocketchat" / "task-reports" / "execution.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "execution_failure",
            "spec_sha256": execution.spec_sha256,
            "plan_sha256": execution.plan_sha256,
            "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "run_check", "action": "run_command",
                "run_id": "run_check", "status": "failed",
                "evidence": command.to_dict(),
            }],
            "changes": [], "evidence_issues": [],
        }), encoding="utf-8")
        payload = _task_report_payload(
            self.root, load_record(self.root, "task_report_001"))
        self.assertEqual(3, payload["commands"][0]["exit_code"])
        self.assertEqual("not_evaluated",
                         payload["commands"][0]["task_acceptance"])
        self.assertEqual("not_recorded", payload["acceptance"]["state"])

    def test_missing_or_tampered_archive_is_an_evidence_issue(self):
        task_record(self.root)
        command_trace = self.root / "traces" / "command.jsonl"
        command_trace.parent.mkdir(parents=True)
        command_trace.write_text("", encoding="utf-8")
        archive = self.root / "traces" / "command-evidence" / "command.json"
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({"schema_version": "pocketchat-command-evidence.v1"}),
                           encoding="utf-8")
        payload = _task_report_payload(self.root, load_record(self.root, "task_report_001"))
        self.assertTrue(payload["evidence_issues"])
        self.assertTrue(all(item["state"] in ("refused", "unknown")
                            for item in payload["evidence_issues"]))

    def test_foreign_archive_is_not_attached_to_the_task(self):
        task_record(self.root)
        command_trace = self.root / "traces" / "command.jsonl"
        command_trace.parent.mkdir(parents=True)
        command_trace.write_text("", encoding="utf-8")
        archive = self.root / "traces" / "command-evidence" / "command.json"
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({
            "schema_version": "pocketchat-command-evidence.v1",
            "run_id": "someone_else", "template_id": "check",
        }), encoding="utf-8")
        payload = _task_report_payload(self.root, load_record(self.root, "task_report_001"))
        self.assertTrue(payload["evidence_issues"])
        self.assertNotIn("commands", [item for item in payload.get("commands", [])])

    def test_cli_report_reads_existing_evidence_and_preserves_bytes(self):
        from tests._helpers import run_cli
        template = parse_command_templates([{
            "id": "check", "argv": ["python3", "-c", "print('cli')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "cli_check.jsonl"
        command = run_command_template(self.root, template, trace_path=trace,
                                       run_id="cli_check")
        task_record(self.root)
        report = self.root / ".pocketchat" / "task-reports" / "cli.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "cli", "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64, "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "cli_check", "action": "run_command",
                "run_id": "cli_check", "status": "completed",
                "evidence": command.to_dict(),
            }],
            "changes": [], "evidence_issues": [],
        }), encoding="utf-8")
        archive = trace.parent / "command-evidence" / "cli_check.json"
        task_file = self.root / ".pocketchat" / "tasks" / "task_report_001.json"
        before = (archive.read_bytes(), task_file.read_bytes())
        result = run_cli(("task", "report", "task_report_001", "--json"),
                         self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("check", payload["commands"][0]["template_id"])
        self.assertEqual(0, payload["commands"][0]["exit_code"])
        self.assertEqual([], payload["changes"])
        self.assertEqual(before, (archive.read_bytes(), task_file.read_bytes()))

    def test_cli_report_surfaces_a_missing_archive_without_failing_read(self):
        from tests._helpers import run_cli
        task_record(self.root)
        trace = self.root / "traces" / "missing.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text("", encoding="utf-8")
        report = self.root / ".pocketchat" / "task-reports" / "missing.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "missing", "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64, "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "missing", "action": "run_command",
                "run_id": "missing", "status": "completed",
                "evidence": {"template_id": "check", "trace": "traces/missing.jsonl"},
            }],
            "changes": [], "evidence_issues": [],
        }), encoding="utf-8")
        result = run_cli(("task", "report", "task_report_001", "--json"),
                         self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([], payload["commands"])
        self.assertTrue(any(item["state"] == "unknown"
                            for item in payload["evidence_issues"]))

    def test_report_is_read_only(self):
        task_record(self.root)
        before = sorted((path.relative_to(self.root), path.read_bytes())
                        for path in self.root.rglob("*") if path.is_file())
        payload = _task_report_payload(self.root, load_record(self.root, "task_report_001"))
        after = sorted((path.relative_to(self.root), path.read_bytes())
                       for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(before, after)
        self.assertEqual("completed", payload["state"])


class ExecutionReportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.txt").write_text("before\n", encoding="utf-8")

    def _spec(self, commands=None):
        return parse_spec({
            "schema_version": "pocketchat-task-spec.v1",
            "objective": "write a reportable result",
            "workspace_root": ".",
            "inputs": ["input.txt"],
            "budgets": {
                "max_steps": 8, "max_tool_calls": 4,
                "max_context_chars": 8000, "max_turn_tokens": 2000,
                "max_session_tokens": 6000,
            },
            "tools": {"write_file": {"allow_paths": ["input.txt"]}},
            "commands": commands or [],
            "acceptance": {"path": "acceptance.json"},
        })

    def _execution(self, spec):
        from pocketchat.task_exec import authorize
        payload = {
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(spec),
            "revision": 0,
            "steps": [{
                "id": "write_result", "objective": "write the result",
                "action": "write_file", "inputs": ["input.txt"],
                "expected_evidence": "result.txt", "depends_on": [],
                "retry_limit": 0,
            }],
        }
        plan = parse_plan(payload, spec=spec)
        return authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))

    def test_execute_plan_writes_a_report_and_does_not_rerun_on_read(self):
        spec = self._spec()
        execution = self._execution(spec)
        calls = []

        def fake_run_task(**kwargs):
            calls.append(kwargs)
            # Simulate a completed write through the real tool path.
            from pocketchat.tools import execute_write_file
            execute_write_file(
                self.root, "input.txt", "after\n",
                allowed_paths=["input.txt"], overwrite=True)
            return type("Result", (), {
                "task_id": "task_report_001",
                "status": "completed",
                "record": type("Record", (), {"steps": [{
                    "kind": "tool", "tool": "write_file",
                    "state": "completed", "detail": "input.txt",
                }]})(),
            })()

        execute_plan(execution, run_task=fake_run_task, workspace=self.root,
                     config={}, trace_dir=self.root / "traces",
                     session_dir=self.root / "sessions")
        report = next((self.root / ".pocketchat" / "task-reports").glob("*.json"))
        self.assertEqual(EXECUTION_REPORT_SCHEMA,
                         json.loads(report.read_text())["schema_version"])
        self.assertEqual(1, len(calls))
        before = report.read_bytes()
        # The fake runner leaves no task record; the report may be inspected
        # directly, and a second read must not execute the fake again.
        evidence = load_task_execution(self.root, "task_report_001")
        self.assertEqual([], evidence["commands"])
        self.assertEqual(1, len(calls))
        self.assertEqual(before, report.read_bytes())

    def test_runner_exception_still_leaves_a_failed_report(self):
        spec = self._spec(commands=[{
            "id": "check", "argv": ["python3", "-c", "print('ok')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])
        from pocketchat.task_exec import authorize
        plan = parse_plan({
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(spec), "revision": 0,
            "steps": [{
                "id": "run_check", "objective": "run the check",
                "action": "run_command", "inputs": ["check"],
                "expected_evidence": "command evidence", "depends_on": [],
                "retry_limit": 0,
            }],
        }, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        def broken_runner(template_id, trace_path, run_id):
            raise RuntimeError("runner exploded")
        with self.assertRaisesRegex(RuntimeError, "runner exploded"):
            execute_plan(execution, run_task=lambda **_: None,
                         command_runner=broken_runner, workspace=self.root,
                         config={}, trace_dir=self.root / "traces",
                         session_dir=self.root / "sessions")
        report = next((self.root / ".pocketchat" / "task-reports").glob("*.json"))
        data = json.loads(report.read_text())
        self.assertEqual("failed", data["steps"][0]["status"])
        self.assertIn("command_runner_error", data["steps"][0]["reason"])
        self.assertEqual("RuntimeError", data["error"]["type"])
        report_data = next((self.root / ".pocketchat" / "task-reports").glob("*.json"))
        stored = json.loads(report_data.read_text())
        self.assertEqual("failed", stored["steps"][0]["status"])
        self.assertIn("command_runner_error", stored["steps"][0]["reason"])

    def test_changed_command_archive_is_detected_by_stored_digest(self):
        template = parse_command_templates([{
            "id": "check", "argv": ["python3", "-c", "print('first')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "check.jsonl"
        command = run_command_template(self.root, template, trace_path=trace,
                                       run_id="check")
        archive = trace.parent / "command-evidence" / "check.json"
        digest = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
        report = self.root / ".pocketchat" / "task-reports" / "e.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "e", "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64, "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "check", "action": "run_command", "run_id": "check",
                "status": "completed", "evidence": command.to_dict(),
                "archive_sha256": digest,
            }],
            "changes": [], "evidence_issues": [],
        }), encoding="utf-8")
        archive.write_text(archive.read_text(encoding="utf-8") + " ", encoding="utf-8")
        evidence = load_task_execution(self.root, "task_report_001")
        self.assertEqual([], evidence["commands"])
        self.assertTrue(any("bytes changed" in item["reason"]
                            for item in evidence["evidence_issues"]))

    def test_foreign_or_open_execution_report_is_refused(self):
        report = self.root / ".pocketchat" / "task-reports" / "foreign.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "foreign",
            "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64,
            "task_ids": ["task_report_001"],
            "steps": [],
            "changes": [],
            "evidence_issues": [],
            "extra": True,
        }), encoding="utf-8")
        evidence = load_task_execution(self.root, "task_report_001")
        self.assertEqual([], evidence["execution_steps"])
        self.assertTrue(evidence["evidence_issues"])
        self.assertEqual("refused", evidence["evidence_issues"][0]["state"])

    def test_tampered_command_archive_becomes_an_issue(self):
        from pocketchat.task_exec import authorize
        spec = self._spec(commands=[{
            "id": "check", "argv": ["python3", "-c", "print('ok')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])
        payload = {
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(spec), "revision": 0,
            "steps": [{
                "id": "run_check", "objective": "run the check",
                "action": "run_command", "inputs": ["check"],
                "expected_evidence": "command evidence", "depends_on": [],
                "retry_limit": 0,
            }],
        }
        plan = parse_plan(payload, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        trace = self.root / "traces" / "run_check.jsonl"
        trace.parent.mkdir(parents=True)
        archive = trace.parent / "command-evidence" / "run_check.json"
        archive.parent.mkdir(parents=True)
        archive.write_text(json.dumps({
            "schema_version": "pocketchat-command-evidence.v1",
            "run_id": "run_check", "template_id": "check", "cwd": ".",
            "exit_code": 0, "timed_out": False, "stdout_bytes": 2,
            "stderr_bytes": 0, "stdout_sha256": "a" * 64,
            "stderr_sha256": "b" * 64, "stdout_truncated": False,
            "stderr_truncated": False, "trace": str(trace),
            "task_acceptance": "passed",
        }), encoding="utf-8")
        report = self.root / ".pocketchat" / "task-reports" / "execution.json"
        report.parent.mkdir(parents=True)
        report.write_text(json.dumps({
            "schema_version": EXECUTION_REPORT_SCHEMA,
            "report_id": "execution_tampered",
            "spec_sha256": execution.spec_sha256,
            "plan_sha256": execution.plan_sha256,
            "task_ids": ["task_report_001"],
            "steps": [{
                "step_id": "run_check", "action": "run_command",
                "run_id": "run_check", "status": "completed",
                "trace": "traces/run_check.jsonl",
                "evidence": {"template_id": "check", "trace": "traces/run_check.jsonl"},
            }],
            "changes": [],
            "evidence_issues": [],
        }), encoding="utf-8")
        evidence = load_task_execution(self.root, "task_report_001")
        self.assertEqual([], evidence["commands"])
        self.assertTrue(any(item["state"] == "unknown"
                            for item in evidence["evidence_issues"]))

    def test_command_evidence_previews_are_not_written_to_the_report(self):
        from pocketchat.task_exec import authorize
        spec = self._spec(commands=[{
            "id": "check", "argv": ["python3", "-c", "print('ok')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])
        payload = {
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(spec),
            "revision": 0,
            "steps": [{
                "id": "run_check", "objective": "run the check",
                "action": "run_command", "inputs": ["check"],
                "expected_evidence": "command evidence", "depends_on": [],
                "retry_limit": 0,
            }],
        }
        plan = parse_plan(payload, spec=spec)
        execution = authorize(plan, spec, expected_plan_sha256=plan_sha256(plan))
        called = []
        def runner(template_id, trace_path, run_id):
            called.append(template_id)
            return type("Command", (), {
                "ok": True, "exit_code": 0, "trace_path": str(trace_path),
                "to_dict": lambda self: {
                    "template_id": template_id, "cwd": ".",
                    "exit_code": 0, "timed_out": False,
                    "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64,
                    "stdout_truncated": False, "stderr_truncated": False,
                    "trace": str(trace_path),
                },
            })()
        execute_plan(execution, run_task=lambda **_: None,
                     command_runner=runner, workspace=self.root, config={},
                     trace_dir=self.root / "traces",
                     session_dir=self.root / "sessions")
        self.assertEqual(["check"], called)
        report = next((self.root / ".pocketchat" / "task-reports").glob("*.json"))
        data = json.loads(report.read_text())
        self.assertEqual("run_command", data["steps"][0]["action"])
        self.assertEqual(64, len(data["steps"][0]["evidence"]["stdout_sha256"]))
        self.assertNotIn("stdout", data["steps"][0]["evidence"])
        self.assertNotIn("stderr", data["steps"][0]["evidence"])


if __name__ == "__main__":
    unittest.main()
