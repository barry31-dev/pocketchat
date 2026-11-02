"""Recovery boundary derived from a bounded execution report."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_recovery import (completed_side_effects,
                                      recovery_remaining_steps)


def report(root: Path, *, steps, issues=None):
    path = root / ".pocketchat" / "task-reports" / "r.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "pocketchat-task-execution-report.v1",
        "report_id": "r", "spec_sha256": "a" * 64,
        "plan_sha256": "b" * 64, "task_ids": ["task_recovery_001"],
        "steps": steps, "changes": [], "evidence_issues": issues or [],
    }), encoding="utf-8")
    return path


def command_step(step_id="run_tests"):
    return {"step_id": step_id, "action": "run_command", "run_id": step_id,
            "status": "completed", "evidence": {"template_id": "tests",
                                                  "trace": "traces/%s.jsonl" % step_id}}


def write_step(step_id="write_report"):
    return {"step_id": step_id, "action": "write_file",
            "status": "completed", "task_id": "task_recovery_001",
            "trace": "traces/%s.jsonl" % step_id}


class CompletedSideEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_completed_steps_are_reported_once(self):
        report(self.root, steps=[command_step(), write_step()])
        effects = completed_side_effects(self.root, "task_recovery_001")
        self.assertEqual(["run_tests", "write_report"],
                         [item["step_id"] for item in effects["steps"]])
        self.assertEqual("completed", effects["state"])

    def test_failed_or_not_executed_steps_are_remaining(self):
        report(self.root, steps=[{
            "step_id": "run_tests", "action": "run_command",
            "run_id": "run_tests", "status": "failed",
        }, {
            "step_id": "write_report", "action": "write_file",
            "status": "not_executed", "reason": "previous_step_did_not_complete",
        }])
        effects = completed_side_effects(
            self.root, "task_recovery_001", planned=["run_tests", "write_report"])
        self.assertEqual([], effects["steps"])
        self.assertEqual(["run_tests", "write_report"], effects["remaining_steps"])

    def test_foreign_report_and_bad_evidence_are_refused(self):
        path = report(self.root, steps=[command_step()])
        data = json.loads(path.read_text())
        data["task_ids"] = ["another_task"]
        path.write_text(json.dumps(data), encoding="utf-8")
        effects = completed_side_effects(self.root, "task_recovery_001")
        self.assertEqual([], effects["steps"])
        self.assertTrue(effects["evidence_issues"])
        self.assertIn(effects["state"], ("refused", "unknown"))

    def test_no_reports_means_unknown_not_completed(self):
        effects = completed_side_effects(self.root, "task_recovery_001")
        self.assertEqual("unknown", effects["state"])
        self.assertTrue(effects["evidence_issues"])

    def test_remaining_steps_preserve_plan_order(self):
        report(self.root, steps=[write_step("write_report"), command_step("run_tests")])
        remaining = recovery_remaining_steps(
            self.root, "task_recovery_001", ["run_tests", "write_report", "verify"])
        self.assertEqual(["verify"], remaining)

    def test_duplicate_completed_step_ids_do_not_duplicate_side_effects(self):
        report(self.root, steps=[command_step("run_tests"), command_step("run_tests")])
        effects = completed_side_effects(self.root, "task_recovery_001")
        self.assertEqual(["run_tests"], [item["step_id"] for item in effects["steps"]])

    def test_tampered_step_shape_is_refused(self):
        report(self.root, steps=[{"step_id": "run_tests", "action": "run_command"}])
        effects = completed_side_effects(self.root, "task_recovery_001")
        self.assertEqual([], effects["steps"])
        self.assertTrue(effects["evidence_issues"])


if __name__ == "__main__":
    unittest.main()
