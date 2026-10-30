"""The shipped smoke demo must keep its three evidence paths deterministic."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TaskDemoTests(unittest.TestCase):
    def test_demo_prints_success_resume_and_permission_refusal(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "examples" / "task-demo" / "demo.py"
        proc = subprocess.run(
            [sys.executable, str(script)], cwd=str(root),
            capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        rows = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(
            ["success", "checkpoint_resume", "permission_refusal"],
            [row["path"] for row in rows])
        self.assertEqual("completed", rows[0]["status"])
        self.assertEqual("running", rows[1]["status"])
        self.assertEqual(4, rows[1]["recovery_step"])
        self.assertEqual(["refused"], rows[2]["tool_states"])


    def test_demo_can_persist_request_snapshots_for_replay(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "examples" / "task-demo" / "demo.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            proc = subprocess.run(
                [sys.executable, str(script), "--output", str(output)],
                cwd=str(root), capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((output / "traces" / "success.jsonl").is_file())
            self.assertTrue((output / "traces" / "requests").is_dir())
            self.assertTrue(list((output / "traces" / "requests").glob("*.json")))


if __name__ == "__main__":
    unittest.main()
