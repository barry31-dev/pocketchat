"""Task acceptance at library and real CLI boundaries, not model self-report."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pocketchat.acceptance import AcceptanceError, parse_plan
from pocketchat.agent import run_task
from pocketchat.provider import ProviderError
from tests import _helpers as hp
from tests._helpers import run_cli
from tests.test_acceptance import plan_payload
from tests.test_cli_exec_json import StubProviderHandler
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from pocketchat.task_policy import parse_policy
from pocketchat.tasking import load_record


class TaskAcceptanceRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def execute(self, adapter, acceptance=None):
        return run_task(
            policy=parse_policy({"schema_version": "pocketchat-task-policy.v1",
                                 "objective": "produce verified report", "workspace_root": ".",
                                 "max_steps": 8, "max_tool_calls": 4,
                                 "max_context_chars": 8000, "max_turn_tokens": 8000,
                                 "tools": {"write_file": {"allow_paths": ["report.md"], "create_only": True}}}),
            workspace=self.root,
            config={"model": "task-model", "base_url": "http://example.invalid/v1",
                    "api_key_env": "", "requires_api_key": False, "timeout_seconds": 5,
                    "summary_prefix_chars": 80,
                    "provider_adapter": adapter},
            trace_path=self.root / "traces" / "task.jsonl",
            session_path=self.root / "sessions" / "task.jsonl",
            prompts=["write a report"], acceptance=acceptance)

    def test_opt_in_run_passes_on_tool_created_artifact(self):
        adapter = FakeTaskAdapter([
            model_tool("write", "write_file", {"path": "report.md", "content": "## Findings\nready"}),
            final_response()])
        plan = parse_plan(plan_payload({"id": "heading", "kind": "text_contains", "path": "report.md",
                                        "text": "## Findings"}))
        result = self.execute(adapter, plan)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.acceptance["state"], "passed")
        self.assertEqual(load_record(self.root, result.task_id).state, "completed")
        events = [json.loads(line) for line in result.trace_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[-1]["type"], "run.finished")
        self.assertEqual(events[-1]["payload"]["task_state"], "completed")
        self.assertTrue((self.root / result.acceptance["record"]).is_file())

    def test_completed_model_answer_does_not_pass_a_missing_artifact(self):
        result = self.execute(FakeTaskAdapter([final_response()]), parse_plan(plan_payload()))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.acceptance["state"], "failed")
        self.assertEqual(result.record.terminal_reason, "task run completed")

    def test_failed_provider_skips_acceptance_and_creates_no_receipt(self):
        adapter = FakeTaskAdapter([ProviderError("upstream", "offline", retryable=False)])
        result = self.execute(adapter, parse_plan(plan_payload()))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.acceptance["state"], "not_run")
        self.assertFalse((self.root / ".pocketchat" / "acceptance").exists())

    def test_legacy_run_has_no_implicit_acceptance(self):
        result = self.execute(FakeTaskAdapter([final_response()]))
        self.assertIsNone(result.acceptance)
        self.assertEqual(result.status, "completed")

    def test_receipt_write_failure_is_unknown_not_false_pass(self):
        with patch("pocketchat.acceptance.verify_task", side_effect=AcceptanceError("cannot persist")):
            result = self.execute(FakeTaskAdapter([final_response()]), parse_plan(plan_payload()))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.acceptance["state"], "unknown")
        self.assertNotIn("record", result.acceptance)


class CommandEvidenceAcceptanceTests(unittest.TestCase):
    """Acceptance may cite a command that already ran; it must never rerun it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _report(self, command, *, step_id="run_tests"):
        report = self.root / ".pocketchat" / "task-reports" / "execution.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "schema_version": "pocketchat-task-execution-report.v1",
            "report_id": "execution_accept",
            "spec_sha256": "b" * 64,
            "plan_sha256": "c" * 64,
            "task_ids": ["task_accept_001"],
            "steps": [{
                "step_id": step_id, "action": "run_command",
                "run_id": step_id,
                "status": "completed" if command.get("exit_code") == 0 else "failed",
                "evidence": command,
            }],
            "changes": [], "evidence_issues": [],
        }), encoding="utf-8")
        return report

    def _task_record(self):
        record = {
            "schema_version": "pocketchat-task.v1",
            "task_id": "task_accept_001",
            "created": "2026-10-31T00:00:00.000Z",
            "objective": "cite a completed command",
            "policy_sha256": "a" * 64,
            "state": "completed",
            "max_steps": 8,
            "max_tool_calls": 4,
            "workspace_root": ".",
            "terminal_reason": "task run completed",
            "steps": [],
        }
        path = self.root / ".pocketchat" / "tasks" / "task_accept_001.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")

    def test_existing_command_evidence_is_accepted_without_execution(self):
        from pocketchat.task_command import parse_command_templates, run_command_template
        template = parse_command_templates([{
            "id": "run_tests", "argv": ["python3", "-c", "print('ok')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "run_tests.jsonl"
        command = run_command_template(self.root, template,
                                       trace_path=trace, run_id="run_tests")
        self._report(command.to_dict())
        self._task_record()
        plan = parse_plan({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{
                "id": "tests", "kind": "command_succeeded",
                "step_id": "run_tests",
            }],
        })
        archive = trace.parent / "command-evidence" / "run_tests.json"
        before = archive.read_bytes()
        receipt = __import__("pocketchat.acceptance", fromlist=["verify_task"]).verify_task(
            self.root, "task_accept_001", plan)
        self.assertEqual("passed", receipt["state"])
        self.assertEqual(before, archive.read_bytes())

    def test_failed_command_is_acceptance_failed_not_passed(self):
        from pocketchat.task_command import parse_command_templates, run_command_template
        template = parse_command_templates([{
            "id": "failing", "argv": ["python3", "-c", "raise SystemExit(7)"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }])[0]
        trace = self.root / "traces" / "failing.jsonl"
        command = run_command_template(self.root, template, trace_path=trace,
                                       run_id="failing")
        self._report(command.to_dict(), step_id="failing")
        self._task_record()
        from pocketchat.acceptance import verify_task
        receipt = verify_task(self.root, "task_accept_001", parse_plan({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{"id": "tests", "kind": "command_succeeded",
                        "step_id": "failing"}],
        }))
        self.assertEqual("failed", receipt["state"])
        self.assertEqual("command_failed", receipt["checks"][0]["reason"])

    def test_command_schema_is_closed_and_rejects_unknown_fields(self):
        with self.assertRaises(AcceptanceError):
            parse_plan({
                "schema_version": "pocketchat-acceptance-plan.v1",
                "checks": [{"id": "tests", "kind": "command_succeeded",
                            "step_id": "run_tests", "path": "x"}],
            })
        self.assertIn("command_succeeded", __import__(
            "pocketchat.acceptance", fromlist=["CHECK_KINDS"]).CHECK_KINDS)

    def test_unattached_or_tampered_command_cannot_pass(self):
        from pocketchat.acceptance import verify_task
        self._report({"template_id": "missing", "trace": "traces/missing.jsonl"})
        self._task_record()
        plan = parse_plan({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{
                "id": "tests", "kind": "command_succeeded",
                "step_id": "missing",
            }],
        })
        receipt = verify_task(self.root, "task_accept_001", plan)
        self.assertIn(receipt["state"], ("refused", "unknown", "failed"))
        self.assertNotEqual("passed", receipt["state"])


class TaskAcceptanceCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stub = hp.StubHTTPServer(StubProviderHandler)
        self.addCleanup(self.stub.close)
        (self.root / "pocketchat.json").write_text(json.dumps({
            "schema_version": "2", "name": "acceptance-cli", "default_profile": "local",
            "profiles": {"local": {"model": "stub-model", "base_url": "http://127.0.0.1:%d/v1" % self.stub.port,
                                   "api_key_env": "", "requires_api_key": False}}}), encoding="utf-8")
        (self.root / "policy.json").write_text(json.dumps({
            "schema_version": "pocketchat-task-policy.v1", "objective": "produce a report",
            "workspace_root": ".", "max_steps": 8, "max_tool_calls": 4,
            "max_context_chars": 8000, "max_turn_tokens": 8000}), encoding="utf-8")
        self.plan_path = self.root / "acceptance.json"
        self.plan_path.write_text(json.dumps(plan_payload()), encoding="utf-8")

    def cli(self, args, *, process=False):
        if process:
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            return subprocess.run([sys.executable, "-m", "pocketchat", *args], cwd=self.root,
                                  env=env, capture_output=True, text=True, timeout=10)
        return run_cli(args, self.root)

    def run_args(self, *, acceptance=True):
        args = ["task", "run", "write report", "--policy", "policy.json", "--session", "audit",
                "--config", "pocketchat.json", "--json"]
        return args + (["--acceptance", "acceptance.json"] if acceptance else [])

    def test_real_process_run_then_verify_then_report(self):
        (self.root / "report.md").write_bytes(b"report")
        proc = self.cli(self.run_args(), process=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["acceptance"]["state"], "passed")
        task_id = payload["task_id"]
        self.assertNotIn(str(self.root), proc.stdout)
        verify = self.cli(["task", "verify", task_id, "--acceptance", "acceptance.json", "--json"], process=True)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        verified = json.loads(verify.stdout)
        self.assertTrue(verified["ok"])
        self.assertEqual(verified["acceptance"]["state"], "passed")
        self.assertNotEqual(payload["acceptance"]["acceptance_id"], verified["acceptance"]["acceptance_id"])
        report = self.cli(["task", "report", task_id, "--json"], process=True)
        self.assertEqual(report.returncode, 0, report.stdout + report.stderr)
        self.assertEqual(json.loads(report.stdout)["acceptance"]["state"], "passed")
        self.assertEqual(len(json.loads(report.stdout)["acceptance"]["records"]), 2)

    def test_acceptance_failed_run_is_exit_one_but_execution_stays_completed(self):
        proc = self.cli(self.run_args(), process=True)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["acceptance"]["state"], "failed")
        record = load_record(self.root, payload["task_id"])
        self.assertEqual(record.state, "completed")

    def test_malformed_plan_is_exit_two_before_provider_or_task_creation(self):
        self.plan_path.write_text('{"schema_version":"future","checks":[]}', encoding="utf-8")
        with patch("pocketchat.cli.run_task", side_effect=AssertionError("must not run")):
            proc = self.cli(self.run_args())
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertFalse(json.loads(proc.stdout)["ok"])
        self.assertFalse((self.root / ".pocketchat" / "tasks").exists())
        self.assertFalse((self.root / "traces").exists())

    def test_legacy_run_and_report_do_not_invent_passed_acceptance(self):
        proc = self.cli(self.run_args(acceptance=False))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertNotIn("acceptance", payload)
        report = self.cli(["task", "report", payload["task_id"], "--json"])
        self.assertEqual(json.loads(report.stdout)["acceptance"]["state"], "not_recorded")

    def test_verify_uses_no_provider_and_appends_failed_observation(self):
        (self.root / "report.md").write_bytes(b"report")
        first = json.loads(self.cli(self.run_args()).stdout)
        before = (self.root / first["record"]).read_bytes()
        (self.root / "report.md").unlink()
        with patch("pocketchat.cli.run_task", side_effect=AssertionError("no provider")):
            proc = self.cli(["task", "verify", first["task_id"], "--acceptance", "acceptance.json", "--json"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(json.loads(proc.stdout)["acceptance"]["state"], "failed")
        self.assertEqual((self.root / first["record"]).read_bytes(), before)

    def test_verify_bad_task_and_unsafe_plan_use_usage_error(self):
        proc = self.cli(["task", "verify", "task_missing", "--acceptance", "acceptance.json", "--json"], process=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_report_does_not_recheck_artifacts_and_names_bad_receipts(self):
        (self.root / "report.md").write_bytes(b"report")
        payload = json.loads(self.cli(self.run_args()).stdout)
        (self.root / "report.md").unlink()
        report = self.cli(["task", "report", payload["task_id"], "--json"])
        self.assertEqual(json.loads(report.stdout)["acceptance"]["state"], "passed")
        (self.root / payload["acceptance"]["record"]).write_bytes(b"{")
        report = self.cli(["task", "report", payload["task_id"], "--json"])
        self.assertEqual(report.returncode, 0)
        value = json.loads(report.stdout)
        self.assertEqual(value["acceptance"]["state"], "refused")
        self.assertTrue(value["acceptance"]["evidence_issues"])

    def test_human_output_distinguishes_execution_and_acceptance(self):
        args = self.run_args()
        args.remove("--json")
        proc = self.cli(args)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("completed", proc.stdout)
        self.assertIn("acceptance failed", proc.stdout)

    def test_malformed_task_state_or_step_kind_is_refused_as_json(self):
        payload = json.loads(self.cli(self.run_args(acceptance=False)).stdout)
        path = self.root / payload["record"]
        original = json.loads(path.read_text(encoding="utf-8"))
        for field, value in (("state", []), ("state", {}), ("kind", []), ("kind", {})):
            data = json.loads(json.dumps(original))
            if field == "kind":
                data["steps"][0]["kind"] = value
            else:
                data[field] = value
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(field=field, value=value):
                proc = self.cli(["task", "verify", payload["task_id"], "--acceptance", "acceptance.json", "--json"], process=True)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(proc.stderr, "")
                self.assertFalse(json.loads(proc.stdout)["ok"])

    def test_report_keeps_execution_report_when_verifier_is_unsupported(self):
        payload = json.loads(self.cli(self.run_args(acceptance=False)).stdout)
        with patch("pocketchat.acceptance.os.supports_dir_fd", set()):
            proc = self.cli(["task", "report", payload["task_id"], "--json"])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        self.assertEqual(value["state"], "completed")
        self.assertIn(value["acceptance"]["state"], ("unknown", "refused"))
        self.assertTrue(value["acceptance"]["evidence_issues"])
