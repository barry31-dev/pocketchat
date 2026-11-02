"""CLI creation of execution-report checkpoints."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_checkpoint import load_checkpoint
from pocketchat.task_exec import EXECUTION_REPORT_SCHEMA
from tests._helpers import run_cli


def report() -> dict:
    return {
        "schema_version": EXECUTION_REPORT_SCHEMA,
        "report_id": "execution_checkpoint_001",
        "spec_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "task_ids": ["task-1"],
        "steps": [
            {"step_id": "first", "action": "write_file", "status": "completed",
             "task_id": "task-1", "trace": "traces/first.jsonl",
             "session": "sessions/first.jsonl"},
            {"step_id": "second", "action": "read_file", "status": "not_executed",
             "reason": "step_boundary"},
        ],
        "changes": [{
            "path": "report.md", "original_sha256": "", "new_sha256": "c" * 64,
            "bytes": 6, "baseline": "absent", "added_lines": 1,
            "removed_lines": 0, "diff_state": "text",
        }],
        "evidence_issues": [],
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CheckpointCreateCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        reports = self.root / ".pocketchat" / "task-reports"
        reports.mkdir(parents=True)
        self.report = reports / "execution_checkpoint_001.json"
        self.report.write_text(json.dumps(report()), encoding="utf-8")

    def args(self, *extra):
        return (
            "task", "checkpoint", "create",
            "--report", str(self.report),
            "--report-sha256", digest(self.report),
            "--checkpoint-id", "ckpt-cli-1",
            "--out", ".pocketchat/task-checkpoints/ckpt-cli-1.json",
            *extra,
        )

    def test_create_writes_a_closed_checkpoint_and_preserves_the_report(self):
        before = self.report.read_bytes()
        result = run_cli(self.args("--json"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual("created", payload["status"])
        self.assertEqual(".pocketchat/task-checkpoints/ckpt-cli-1.json",
                         payload["checkpoint"])
        self.assertEqual("execution_checkpoint_001", payload["report_id"])
        self.assertEqual(["first"], payload["completed_step_ids"])
        self.assertEqual(["second"], payload["pending_step_ids"])
        self.assertEqual(before, self.report.read_bytes())
        checkpoint = load_checkpoint(self.root / payload["checkpoint"])
        self.assertEqual("ckpt-cli-1", checkpoint["checkpoint_id"])
        self.assertEqual(digest(self.report), checkpoint["report_sha256"])
        self.assertFalse((self.root / "traces").exists())
        self.assertFalse((self.root / "sessions").exists())

    def test_created_checkpoint_is_accepted_by_checkpoint_resume_preview(self):
        created = run_cli(self.args("--json"), self.root)
        self.assertEqual(0, created.returncode, created.stdout + created.stderr)
        path = json.loads(created.stdout)["checkpoint"]
        preview = run_cli((
            "task", "checkpoint", "resume", path,
            "--spec-sha256", "a" * 64,
            "--plan-sha256", "b" * 64, "--json",
        ), self.root)
        self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
        payload = json.loads(preview.stdout)
        self.assertEqual("execution_checkpoint_001", payload["report_id"])
        self.assertEqual(["second"], payload["pending_step_ids"])
        self.assertEqual(digest(self.report), payload["expected_report_sha256"])

    def test_wrong_report_digest_is_refused_without_writing(self):
        result = run_cli((
            "task", "checkpoint", "create",
            "--report", str(self.report),
            "--report-sha256", "0" * 64,
            "--checkpoint-id", "bad",
            "--out", "bad.json", "--json",
        ), self.root)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertFalse((self.root / "bad.json").exists())

    def test_report_outside_the_reports_directory_is_refused(self):
        stray = self.root / "stray.json"
        stray.write_text(json.dumps(report()), encoding="utf-8")
        result = run_cli((
            "task", "checkpoint", "create",
            "--report", "stray.json", "--report-sha256", digest(stray),
            "--checkpoint-id", "stray", "--out", "stray-checkpoint.json",
            "--json",
        ), self.root)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root / "stray-checkpoint.json").exists())

    def test_output_cannot_replace_the_execution_report(self):
        result = run_cli((
            "task", "checkpoint", "create",
            "--report", str(self.report),
            "--report-sha256", digest(self.report),
            "--checkpoint-id", "same-path",
            "--out", ".pocketchat/task-reports/execution_checkpoint_001.json",
            "--json",
        ), self.root)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("execution_checkpoint_001",
                         json.loads(self.report.read_text())["report_id"])

    def test_output_outside_the_workspace_is_refused(self):
        result = run_cli(self.args("--json", "--out", "../outside.json"), self.root)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertFalse((self.root.parent / "outside.json").exists())

    def test_existing_checkpoint_is_never_overwritten(self):
        first = run_cli(self.args("--json"), self.root)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        path = self.root / ".pocketchat/task-checkpoints/ckpt-cli-1.json"
        before = path.read_bytes()
        second = run_cli(self.args("--json"), self.root)
        self.assertEqual(2, second.returncode, second.stdout + second.stderr)
        self.assertEqual(before, path.read_bytes())

    def test_help_exposes_create_inputs(self):
        result = run_cli(("task", "checkpoint", "create", "--help"), self.root)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for option in ("--report", "--report-sha256", "--checkpoint-id", "--out"):
            self.assertIn(option, result.stdout)


if __name__ == "__main__":
    unittest.main()
