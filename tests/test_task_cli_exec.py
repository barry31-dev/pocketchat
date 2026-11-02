"""CLI boundary for authorized TaskPlan execution."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat import cli
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests._helpers import run_cli
from tests.test_task_plan import plan_payload
from tests.test_task_spec import minimal


class TaskExecCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.md").write_text("bounded input\n", encoding="utf-8")
        self.spec_payload = minimal()
        self.spec = parse_spec(self.spec_payload)
        self.plan_payload = plan_payload(spec_sha256(self.spec))
        self.plan = parse_plan(self.plan_payload, spec=self.spec)
        self.spec_path = self.root / "spec.json"
        self.plan_path = self.root / "plan.json"
        self.spec_path.write_text(json.dumps(self.spec_payload), encoding="utf-8")
        self.plan_path.write_text(json.dumps(self.plan_payload), encoding="utf-8")
        self.config_path = self.root / "pocketchat.json"
        self.config_path.write_text(json.dumps({
            "schema_version": "1",
            "provider": {
                "base_url": "http://example.invalid/v1",
                "model": "task-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")

    def _args(self, *extra, plan_digest=None):
        return (
            "task", "exec",
            "--spec", str(self.spec_path),
            "--plan", str(self.plan_path),
            "--plan-sha256", plan_digest or plan_sha256(self.plan),
            "--config", str(self.config_path),
            *extra,
        )

    def test_cli_refuses_a_plan_digest_before_reading_provider_config(self):
        with mock.patch.object(cli, "load_config",
                               side_effect=AssertionError("config must not load")):
            result = run_cli(self._args("--json", plan_digest="0" * 64),
                             self.root, env={"POCKETCHAT_API_KEY": "not-a-real-key"})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("TaskPlan hash", payload["message"])

    def test_cli_executes_the_authorized_plan_and_returns_report_path(self):
        calls = []
        report = ".pocketchat/task-reports/execution_test.json"

        def fake_execute(execution, **kwargs):
            calls.append((execution, kwargs))
            return [{"step_id": "read_input", "action": "read_file",
                     "status": "completed", "task_id": "task_test",
                     "execution_report": report}]

        with mock.patch.object(cli, "execute_plan", fake_execute):
            result = run_cli(self._args("--json"), self.root,
                             env={"POCKETCHAT_API_KEY": "not-a-real-key"})

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("task_test", payload["task_ids"][0])
        self.assertEqual("read_input", payload["steps"][0]["step_id"])
        self.assertEqual("completed", payload["steps"][0]["status"])
        self.assertEqual([report], payload["reports"])
        self.assertEqual(1, len(calls))
        execution, kwargs = calls[0]
        self.assertEqual(plan_sha256(self.plan), execution.plan_sha256)
        self.assertEqual(self.root.resolve(), Path(kwargs["workspace"]).resolve())

    def test_help_exposes_the_exec_entry(self):
        result = run_cli(("task", "exec", "--help"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("--plan-sha256", result.stdout)
        self.assertIn("--spec", result.stdout)

    def test_bad_config_is_reported_as_json_after_authorization(self):
        self.config_path.write_text("{not-json}\n", encoding="utf-8")
        result = run_cli(self._args("--json"), self.root, env={})
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertNotIn("api", payload["message"].lower())

    def test_failed_step_returns_nonzero_and_keeps_execution_report(self):
        report = ".pocketchat/task-reports/execution_failed.json"

        def fake_execute(execution, **kwargs):
            return [{"step_id": "read_input", "action": "read_file",
                     "status": "failed", "reason": "provider_failed",
                     "execution_report": report}]

        with mock.patch.object(cli, "execute_plan", fake_execute):
            result = run_cli(self._args("--json"), self.root, env={})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("failed", payload["steps"][0]["status"])
        self.assertEqual([report], payload["reports"])

    def test_output_never_contains_the_api_key(self):
        secret = "sk-do-not-print-this"
        with mock.patch.object(cli, "execute_plan", return_value=[]):
            result = run_cli(self._args("--json"), self.root,
                             env={"POCKETCHAT_API_KEY": secret})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_real_stub_execution_writes_task_trace_and_report(self):
        from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
        class Adapter(FakeTaskAdapter):
            name = "task-cli-exec-stub"

        adapter = Adapter([
            model_tool("read", "read_file", {"path": "input.md"}),
            final_response(),
        ])
        self.config_path.write_text(json.dumps({
            "schema_version": "1",
            "provider": {
                "base_url": "http://example.invalid/v1",
                "model": "task-model",
                "api_key_env": "",
                "requires_api_key": False,
            },
        }), encoding="utf-8")
        original = cli.profile_config
        cli.profile_config = lambda config, profile: dict(
            original(config, profile), provider_adapter=adapter)
        try:
            result = run_cli(self._args("--json"), self.root, env={})
        finally:
            cli.profile_config = original
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual("completed", payload["steps"][0]["status"])
        self.assertTrue((self.root / "traces" / "read_input.jsonl").is_file())
        self.assertTrue((self.root / "sessions" / "read_input.jsonl").is_file())
        self.assertEqual(1, len(payload["reports"]), payload)
        self.assertTrue((self.root / payload["reports"][0]).is_file())

    def test_command_step_uses_the_declared_template_and_records_evidence(self):
        command = {
            "id": "run_tests",
            "argv": ["python3", "-c", "print('cli command ok')"],
            "cwd": ".",
            "timeout_seconds": 5,
            "max_output_bytes": 128,
        }
        self.spec_payload = minimal(commands=[command])
        self.spec = parse_spec(self.spec_payload)
        self.plan_payload = plan_payload(spec_sha256(self.spec))
        self.plan_payload["steps"][0].update({
            "action": "run_command",
            "inputs": ["run_tests"],
            "expected_evidence": "command evidence",
        })
        self.plan = parse_plan(self.plan_payload, spec=self.spec)
        self.spec_path.write_text(json.dumps(self.spec_payload), encoding="utf-8")
        self.plan_path.write_text(json.dumps(self.plan_payload), encoding="utf-8")
        result = run_cli(self._args("--json"), self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual("run_tests", payload["steps"][0]["evidence"]["template_id"])
        self.assertTrue((self.root / "traces" / "read_input.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
