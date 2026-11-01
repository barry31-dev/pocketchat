"""Resuming an execution from a checkpoint instead of a bare report digest."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_checkpoint import (CheckpointError,
                                        create_checkpoint)
from pocketchat.task_checkpoint import resume_from_checkpoint
from pocketchat.task_exec import EXECUTION_REPORT_SCHEMA


def report(steps=None, changes=None):
    steps = steps if steps is not None else [
        {"step_id": "s1", "action": "write_file", "status": "completed",
         "task_id": "task-1", "trace": "traces/s1.jsonl",
         "session": "sessions/s1.jsonl"},
        {"step_id": "s2", "action": "run_command", "status": "completed",
         "run_id": "s2", "trace": "traces/s2.jsonl",
         "evidence": {"template_id": "unit-tests", "exit_code": 0,
                      "timed_out": False, "trace": "traces/s2.cmd.jsonl",
                      "archive_sha256": "c" * 64}},
        {"step_id": "s3", "action": "read_file", "status": "not_executed",
         "reason": "step_boundary"},
    ]
    changes = changes if changes is not None else [
        {"path": "out/result.md", "original_sha256": "", "new_sha256": "d" * 64,
         "bytes": 12, "baseline": "absent", "added_lines": 1,
         "removed_lines": 0, "diff_state": "text"},
    ]
    return {
        "schema_version": EXECUTION_REPORT_SCHEMA,
        "report_id": "execution_1",
        "spec_sha256": "a" * 64,
        "plan_sha256": "b" * 64,
        "task_ids": ["task-1"],
        "steps": steps,
        "changes": changes,
        "evidence_issues": [],
    }


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reports = self.root / ".pocketchat" / "task-reports"
        self.reports.mkdir(parents=True)
        self.report_path = self.reports / "execution_1.json"
        self.report_path.write_text(json.dumps(report()), encoding="utf-8")
        self.checkpoint = create_checkpoint(
            json.loads(self.report_path.read_text()),
            report_path=self.report_path,
            expected_report_sha256=hashlib.sha256(
                self.report_path.read_bytes()).hexdigest())
        self.ckpt_path = self.root / "ckpt.json"
        self.ckpt_path.write_text(json.dumps(self.checkpoint), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_checkpoint_resolves_to_its_report_and_digest(self):
        resolved = resume_from_checkpoint(self.root, self.ckpt_path)
        self.assertEqual(".pocketchat/task-reports/execution_1.json",
                         resolved["report_path"])
        self.assertEqual(self.report_path.resolve(),
                         (self.root / resolved["report_path"]).resolve())
        self.assertEqual(self.checkpoint["report_sha256"],
                         resolved["expected_report_sha256"])
        self.assertEqual(["s1", "s2"], resolved["completed_step_ids"])
        self.assertEqual(["s3"], resolved["pending_step_ids"])

    def test_digest_binding_rejects_a_changed_report(self):
        self.report_path.write_text(json.dumps(report(changes=[])),
                                    encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "report"):
            resume_from_checkpoint(self.root, self.ckpt_path)

    def test_plan_binding_rejects_a_different_execution(self):
        for kwargs in ({"expected_spec_sha256": "f" * 64},
                       {"expected_plan_sha256": "f" * 64}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(CheckpointError, "does not match"):
                    resume_from_checkpoint(self.root, self.ckpt_path, **kwargs)

    def test_report_must_live_in_the_reports_directory(self):
        stray = self.root / "stray.json"
        stray.write_text(json.dumps(report()), encoding="utf-8")
        tampered = dict(self.checkpoint)
        tampered["report_id"] = "stray"
        self.ckpt_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(CheckpointError):
            resume_from_checkpoint(self.root, self.ckpt_path)

    def test_boundary_must_match_the_completed_prefix(self):
        tampered = dict(self.checkpoint)
        tampered["boundary_index"] = 1
        self.ckpt_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(CheckpointError):
            resume_from_checkpoint(self.root, self.ckpt_path)


class CliTests(unittest.TestCase):
    def test_resume_preview_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / ".pocketchat" / "task-reports"
            reports.mkdir(parents=True)
            report_path = reports / "execution_1.json"
            report_path.write_text(json.dumps(report()), encoding="utf-8")
            checkpoint = create_checkpoint(
                json.loads(report_path.read_text()), report_path=report_path,
                expected_report_sha256=hashlib.sha256(
                    report_path.read_bytes()).hexdigest())
            ckpt = root / "ckpt.json"
            ckpt.write_text(json.dumps(checkpoint), encoding="utf-8")
            from tests._helpers import run_cli
            result = run_cli(("task", "checkpoint", "resume", "ckpt.json",
                              "--spec-sha256", "a" * 64,
                              "--plan-sha256", "b" * 64, "--json"), root)
            self.assertEqual(0, result.returncode,
                             result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(["s3"], payload["pending_step_ids"])
            self.assertIn("execution_1", payload["report_path"])
            self.assertFalse((root / "traces").exists())


if __name__ == "__main__":
    unittest.main()
