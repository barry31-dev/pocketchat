"""CLI step-boundary stop and resume for authorized TaskPlans."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat import cli
from pocketchat.task_checkpoint import create_checkpoint
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests import _helpers as hp
from tests._helpers import run_cli
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TaskExecResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "input.md").write_text("bounded input\n", encoding="utf-8")
        self.spec_payload = minimal(
            inputs=["input.md"],
            tools={"read_file": {"allow_paths": ["input.md"]}},
        )
        self.spec = parse_spec(self.spec_payload)
        steps = []
        for step_id in ("first", "second"):
            steps.append({
                "id": step_id,
                "objective": "read " + step_id,
                "action": "read_file",
                "inputs": ["input.md"],
                "expected_evidence": "file content",
                "depends_on": [steps[-1]["id"]] if steps else [],
                "retry_limit": 0,
            })
        payload = {
            "schema_version": "pocketchat-task-plan.v1",
            "spec_sha256": spec_sha256(self.spec),
            "steps": steps,
            "revision": 0,
        }
        self.plan = parse_plan(payload, spec=self.spec)
        self.spec_path = self.root / "spec.json"
        self.plan_path = self.root / "plan.json"
        self.spec_path.write_text(json.dumps(self.spec_payload), encoding="utf-8")
        self.plan_path.write_text(json.dumps(payload), encoding="utf-8")
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
            model_tool("read-first", "read_file", {"path": "input.md"}),
            final_response(),
            model_tool("read-second", "read_file", {"path": "input.md"}),
            final_response(),
        ])
        original = cli.profile_config
        def selected(config, profile):
            return dict(original(config, profile), provider_adapter=self.adapter)
        cli.profile_config = selected
        self.addCleanup(setattr, cli, "profile_config", original)

    def args(self, *extra):
        return (
            "task", "exec",
            "--spec", str(self.spec_path),
            "--plan", str(self.plan_path),
            "--plan-sha256", plan_sha256(self.plan),
            "--config", str(self.config_path),
            *extra,
        )

    def stop(self):
        result = run_cli(self.args("--stop-after", "1", "--json"), self.root,
                         env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("boundary", payload["status"])
        self.assertTrue(payload["ok"])
        self.assertEqual(["completed", "not_executed"],
                         [step["status"] for step in payload["steps"]])
        self.assertEqual("step_boundary", payload["steps"][1]["reason"])
        report = self.root / payload["reports"][0]
        return payload, report

    def _enable_task_budget(self, limit=100000):
        self.spec_payload["budgets"]["max_task_tokens"] = limit
        self.spec = parse_spec(self.spec_payload)
        self.plan.source["spec_sha256"] = spec_sha256(self.spec)
        self.plan = parse_plan(self.plan.source, spec=self.spec)
        self.spec_path.write_text(json.dumps(self.spec_payload), encoding="utf-8")
        self.plan_path.write_text(json.dumps(self.plan.source), encoding="utf-8")

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "budgeted resume requires secure descriptor-relative reads")
    def test_budgeted_report_resume_restores_the_persisted_ledger(self):
        self._enable_task_budget()
        _, report = self.stop()
        persisted = json.loads(report.read_text())["task_budget"]
        self.assertEqual(100000, persisted["limit_tokens"])
        self.assertEqual(2, len(persisted["requests"]))
        self.assertEqual(2, sum(
            row["state"] == "settled" for row in persisted["requests"]))
        before_calls = len(self.adapter.calls)
        result = run_cli(self.args(
            "--resume-report", str(report), "--report-sha256", digest(report),
            "--json"), self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertEqual(before_calls + 2, len(self.adapter.calls))
        successor = json.loads((self.root / payload["reports"][0]).read_text())
        self.assertEqual(4, len(successor["task_budget"]["requests"]))
        self.assertTrue(
            {row["request_id"] for row in persisted["requests"]}
            <= {row["request_id"] for row in successor["task_budget"]["requests"]})

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "budgeted resume requires secure descriptor-relative reads")
    def test_budgeted_checkpoint_resume_restores_the_persisted_ledger(self):
        self._enable_task_budget()
        _, report = self.stop()
        report_sha = digest(report)
        checkpoint = create_checkpoint(
            json.loads(report.read_text()), report_path=report,
            expected_report_sha256=report_sha)
        checkpoint_path = self.root / "budget-checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        before_calls = len(self.adapter.calls)
        result = run_cli(self.args(
            "--checkpoint", str(checkpoint_path), "--json"), self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertEqual(before_calls + 2, len(self.adapter.calls))
        successor = json.loads((self.root / payload["reports"][0]).read_text())
        self.assertEqual(4, len(successor["task_budget"]["requests"]))

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "budgeted resume requires secure descriptor-relative reads")
    def test_budgeted_resume_rejects_missing_tampered_or_mismatched_ledger(self):
        self._enable_task_budget()
        _, report = self.stop()
        original = json.loads(report.read_text())
        variants = []
        missing = json.loads(json.dumps(original))
        missing.pop("task_budget")
        variants.append(missing)
        forged = json.loads(json.dumps(original))
        forged["task_budget"]["requests"][0]["settled_tokens"] = 0
        variants.append(forged)
        mismatched = json.loads(json.dumps(original))
        mismatched["task_budget"]["limit_tokens"] = 99999
        variants.append(mismatched)
        before_calls = len(self.adapter.calls)
        before_reports = sorted((self.root / ".pocketchat/task-reports").glob("*.json"))
        for index, data in enumerate(variants):
            candidate = (self.root / ".pocketchat/task-reports"
                         / report.name)
            candidate.write_text(json.dumps(data), encoding="utf-8")
            result = run_cli(self.args(
                "--resume-report", str(candidate),
                "--report-sha256", digest(candidate), "--json"),
                self.root, env={})
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("task budget", result.stdout.lower())
        self.assertEqual(before_calls, len(self.adapter.calls))
        self.assertEqual(before_reports,
                         sorted((self.root / ".pocketchat/task-reports").glob("*.json")))

    def test_stop_after_writes_a_boundary_and_exits_zero(self):
        payload, report = self.stop()
        self.assertEqual(1, len(payload["task_ids"]))
        self.assertTrue(report.is_file())
        self.assertFalse((self.root / "sessions" / "second.jsonl").exists())
        self.assertEqual(2, len(self.adapter.calls))

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "resume requires secure descriptor-relative reads")
    def test_direct_report_resume_runs_only_the_unfinished_step(self):
        _, report = self.stop()
        report_sha = digest(report)
        result = run_cli(self.args(
            "--resume-report", str(report),
            "--report-sha256", report_sha,
            "--json"), self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertTrue(payload["ok"])
        self.assertEqual(["completed", "completed"],
                         [step["status"] for step in payload["steps"]])
        self.assertEqual(4, len(self.adapter.calls))
        resumed_report = self.root / payload["reports"][0]
        self.assertNotEqual(report, resumed_report)
        data = json.loads(resumed_report.read_text())
        self.assertEqual(report.relative_to(self.root).as_posix(),
                         data["recovery"]["report"])

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "resume requires secure descriptor-relative reads")
    def test_checkpoint_resume_uses_the_bound_report_and_digest(self):
        _, report = self.stop()
        checkpoint = create_checkpoint(
            json.loads(report.read_text()), report_path=report,
            expected_report_sha256=digest(report))
        checkpoint_path = self.root / "checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        result = run_cli(self.args("--checkpoint", str(checkpoint_path), "--json"),
                         self.root, env={})
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertEqual(["completed", "completed"],
                         [step["status"] for step in payload["steps"]])
        self.assertEqual(4, len(self.adapter.calls))

    def test_checkpoint_binding_rejects_a_foreign_execution_before_provider(self):
        _, report = self.stop()
        checkpoint = create_checkpoint(
            json.loads(report.read_text()), report_path=report,
            expected_report_sha256=digest(report))
        checkpoint["plan_sha256"] = "0" * 64
        checkpoint_path = self.root / "foreign-checkpoint.json"
        checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
        calls_before = len(self.adapter.calls)
        with mock.patch.object(cli, "load_config",
                               side_effect=AssertionError("config must not load")):
            result = run_cli(self.args("--checkpoint", str(checkpoint_path), "--json"),
                             self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual(calls_before, len(self.adapter.calls))

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "resume requires secure descriptor-relative reads")
    def test_fully_completed_source_is_a_verified_noop_without_a_new_report(self):
        _, report = self.stop()
        report_sha = digest(report)
        first = run_cli(self.args(
            "--resume-report", str(report),
            "--report-sha256", report_sha, "--json"), self.root, env={})
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        completed = json.loads(first.stdout)
        completed_report = self.root / completed["reports"][0]
        calls_before = len(self.adapter.calls)
        reports_before = sorted((self.root / ".pocketchat/task-reports").glob("*.json"))
        second = run_cli(self.args(
            "--resume-report", str(completed_report),
            "--report-sha256", digest(completed_report), "--json"),
            self.root, env={})
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        payload = json.loads(second.stdout)
        self.assertEqual("completed", payload["status"])
        self.assertEqual(calls_before, len(self.adapter.calls))
        self.assertEqual(reports_before, sorted(
            (self.root / ".pocketchat/task-reports").glob("*.json")))
        self.assertEqual([completed_report.relative_to(self.root).as_posix()],
                         payload["reports"])

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "resume requires secure descriptor-relative reads")
    def test_resume_digest_mismatch_refuses_before_provider_or_command(self):
        _, report = self.stop()
        calls_before = len(self.adapter.calls)
        before = {path.relative_to(self.root).as_posix(): path.read_bytes()
                  for path in self.root.rglob("*") if path.is_file()}
        result = run_cli(self.args(
            "--resume-report", str(report),
            "--report-sha256", "0" * 64,
            "--json"), self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(calls_before, len(self.adapter.calls))
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_resume_inputs_are_exclusive_and_need_a_digest(self):
        result = run_cli(self.args("--resume-report", "report.json", "--json"),
                         self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        _, report = self.stop()
        result = run_cli(self.args(
            "--resume-report", str(report),
            "--report-sha256", digest(report),
            "--checkpoint", "checkpoint.json", "--json"), self.root, env={})
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)

    def test_help_exposes_boundary_and_resume_options(self):
        result = run_cli(("task", "exec", "--help"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for option in ("--stop-after", "--resume-report", "--report-sha256",
                       "--checkpoint"):
            self.assertIn(option, result.stdout)


if __name__ == "__main__":
    unittest.main()
