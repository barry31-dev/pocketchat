"""CLI acceptance against the exact report produced by task exec."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat import cli
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests import _helpers as hp
from tests._helpers import run_cli
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal


class TaskExecAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.md").write_text("bounded input\n", encoding="utf-8")
        self.spec_payload = minimal(
            inputs=["input.md", "report.md"],
            tools={
                "read_file": {"allow_paths": ["input.md"]},
                "write_file": {"allow_paths": ["report.md"]},
            })
        self.spec = parse_spec(self.spec_payload)
        self.plan_payload = {
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(self.spec),
            "steps": [{
                "id": "write_report", "objective": "write report",
                "action": "write_file", "inputs": ["report.md"],
                "expected_evidence": "report.md", "depends_on": [],
                "retry_limit": 0,
            }],
            "revision": 0,
        }
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
                "api_key_env": "",
                "requires_api_key": False,
            },
        }), encoding="utf-8")
        self.adapter = FakeTaskAdapter([
            model_tool("write", "write_file",
                       {"path": "report.md", "content": "ready\n"}),
            final_response(),
        ])
        original = cli.profile_config
        cli.profile_config = lambda config, profile: dict(
            original(config, profile), provider_adapter=self.adapter)
        self.addCleanup(setattr, cli, "profile_config", original)
        self.acceptance_path = self.root / "acceptance.json"
        self.acceptance_path.write_text(json.dumps({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [
                {"id": "report", "kind": "file_exists", "path": "report.md"},
                {"id": "ready", "kind": "text_contains",
                 "path": "report.md", "text": "ready"},
            ],
        }), encoding="utf-8")

    def args(self, *extra):
        return (
            "task", "exec", "--spec", str(self.spec_path),
            "--plan", str(self.plan_path),
            "--plan-sha256", plan_sha256(self.plan),
            "--config", str(self.config_path), *extra,
        )

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "file acceptance requires secure descriptor-relative reads")
    def test_passing_acceptance_is_separate_from_execution_and_bound_to_report(self):
        result = run_cli(self.args(
            "--check-acceptance", str(self.acceptance_path), "--json"),
            self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("completed", payload["status"])
        acceptance = payload["acceptance"]
        self.assertEqual("passed", acceptance["state"])
        self.assertEqual(2, len(acceptance["checks"]))
        self.assertEqual(payload["spec_sha256"], acceptance["spec_sha256"])
        self.assertEqual(payload["plan_sha256"], acceptance["plan_sha256"])
        report = self.root / payload["reports"][0]
        import hashlib
        self.assertEqual(hashlib.sha256(report.read_bytes()).hexdigest(),
                         acceptance["execution_report_sha256"])
        self.assertNotIn("record", acceptance)
        self.assertFalse((self.root / ".pocketchat/acceptance").exists())

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "file acceptance requires secure descriptor-relative reads")
    def test_missing_artifact_fails_acceptance_without_reclassifying_execution(self):
        (self.root / "acceptance.json").write_text(json.dumps({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{"id": "missing", "kind": "file_exists",
                        "path": "absent.md"}],
        }), encoding="utf-8")
        result = run_cli(self.args(
            "--check-acceptance", "acceptance.json", "--json"),
            self.root, env={})
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual("completed", payload["status"])
        self.assertEqual("failed", payload["acceptance"]["state"])
        self.assertEqual("missing_file", payload["acceptance"]["checks"][0]["reason"])

    def test_platform_refusal_keeps_execution_completed_and_returns_one(self):
        result = run_cli(self.args(
            "--check-acceptance", str(self.acceptance_path), "--json"),
            self.root, env={})
        if hp.SECURE_DESCRIPTOR_READS:
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            return
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertEqual("refused", payload["acceptance"]["state"])
        self.assertEqual("unsupported_platform",
                         payload["acceptance"]["checks"][0]["reason"])

    def test_invalid_acceptance_plan_is_refused_before_provider(self):
        self.acceptance_path.write_text('{"schema_version":"bad"}', encoding="utf-8")
        result = run_cli(self.args(
            "--check-acceptance", str(self.acceptance_path), "--json"),
            self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual([], self.adapter.calls)
        self.assertFalse((self.root / "traces").exists())

    def test_boundary_does_not_claim_acceptance_passed(self):
        self.plan_payload["steps"].append({
            "id": "second", "objective": "second", "action": "read_file",
            "inputs": ["input.md"], "expected_evidence": "content",
            "depends_on": ["write_report"], "retry_limit": 0,
        })
        self.plan = parse_plan(self.plan_payload, spec=self.spec)
        self.plan_path.write_text(json.dumps(self.plan_payload), encoding="utf-8")
        result = run_cli(self.args(
            "--plan-sha256", plan_sha256(self.plan),
            "--stop-after", "1",
            "--check-acceptance", str(self.acceptance_path), "--json"),
            self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("boundary", payload["status"])
        self.assertEqual("not_evaluated", payload["acceptance"]["state"])
        self.assertEqual("execution_incomplete", payload["acceptance"]["reason"])

    def test_help_exposes_check_acceptance(self):
        result = run_cli(("task", "exec", "--help"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("--check-acceptance", result.stdout)


if __name__ == "__main__":
    unittest.main()
