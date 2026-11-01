"""Offline dogfood must use real tools and verifiable runtime receipts."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_command import load_command_evidence

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/task-dogfood/demo.py"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DogfoodDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name) / "evidence"
        cls.proc = subprocess.run([sys.executable, str(DEMO), "--output", str(cls.output)],
                                  cwd=ROOT, capture_output=True, text=True, timeout=45)
        if cls.proc.returncode:
            raise AssertionError(cls.proc.stdout + cls.proc.stderr)
        cls.rows = {row["case"]: row for row in map(json.loads, cls.proc.stdout.splitlines())}

    def read(self, case, key):
        row = self.rows[case]
        path = self.output / case / row["evidence"][key]
        return json.loads(path.read_text())

    def test_command_archives_use_relocatable_workspace_relative_traces(self):
        relocated = Path(self.temp.name) / "relocated"
        shutil.copytree(self.output, relocated)
        for case, row in self.rows.items():
            for command in row.get("commands", []):
                archive = json.loads((self.output / case / command["archive"]).read_text())
                self.assertFalse(Path(archive["trace"]).is_absolute())
                self.assertEqual(command["trace"], archive["trace"])
                loaded = load_command_evidence(relocated / case, command["trace"])
                self.assertEqual(command["trace"], loaded["trace"])

    def test_demo_runs_all_four_paths_offline(self):
        self.assertEqual("", self.proc.stderr)
        self.assertEqual(["success", "repair", "recovery", "refusal"], list(self.rows))
        self.assertEqual(["completed", "repaired", "recovered", "refused"],
                         [row["outcome"] for row in self.rows.values()])
        for case, row in self.rows.items():
            self.assertEqual("stub", row["provider"])
            self.assertFalse(row["real_provider"])
            report = self.output / case / row["evidence"]["report"]
            self.assertEqual(row["evidence"]["report_sha256"], sha(report))

    def test_success_has_successful_tools_and_independent_command_acceptance(self):
        row = self.rows["success"]
        report = self.read("success", "report")
        workspace = self.output / "success"
        tools = []
        for task_id in report["task_ids"]:
            record = json.loads((workspace / ".pocketchat/tasks" / (task_id + ".json")).read_text())
            tools.extend(item for item in record["steps"] if item["kind"] == "tool")
        self.assertTrue(any(tool.get("tool") == "write_file" and tool["state"] == "completed"
                            for tool in tools), tools)
        self.assertTrue(all(tool["state"] == "completed" for tool in tools), tools)
        commands = [step for step in report["steps"] if step["action"] == "run_command"]
        self.assertEqual(1, len(commands))
        self.assertEqual(0, commands[0]["exit_code"])
        receipt = self.read("success", "acceptance")
        self.assertEqual("passed", receipt["state"])
        self.assertIn("command_succeeded", [item["kind"] for item in receipt["checks"]])
        self.assertEqual("1", (workspace / "command-count.txt").read_text())
        self.assertEqual("dogfood report\n", (workspace / "report.md").read_text())
        self.assertEqual(row["spec_sha256"], report["spec_sha256"])
        self.assertEqual(row["plan_sha256"], report["plan_sha256"])

    def test_repair_changes_the_file_not_the_declared_command(self):
        row = self.rows["repair"]
        workspace = self.output / "repair"
        self.assertEqual("operator_callback", row["repair_mode"])
        commands = row["commands"]
        self.assertEqual([1, 0], [item["exit_code"] for item in commands])
        self.assertEqual(commands[0]["template_sha256"], commands[1]["template_sha256"])
        self.assertEqual(commands[0]["argv"], commands[1]["argv"])
        self.assertEqual("2", (workspace / "command-count.txt").read_text())
        edit = self.read("repair", "edit")
        self.assertNotEqual(edit["original_sha256"], edit["new_sha256"])
        self.assertEqual(edit["new_sha256"], sha(workspace / "report.md"))
        before = subprocess.run(["git", "-C", str(workspace), "show", "HEAD:report.md"],
                                capture_output=True, check=True).stdout
        self.assertEqual(edit["original_sha256"], hashlib.sha256(before).hexdigest())
        details = self.read("repair", "repair")
        self.assertEqual("repaired", details["outcome"])
        self.assertEqual(2, details["attempts"])
        self.assertEqual("passed", self.read("repair", "acceptance")["state"])
        self.assertEqual("file_only", row["acceptance_scope"])

    def test_recovery_executes_the_suffix_and_preserves_confirmed_effects(self):
        row = self.rows["recovery"]
        workspace = self.output / "recovery"
        before = self.read("recovery", "checkpoint")
        after = self.read("recovery", "report")
        self.assertEqual("not_executed", before["steps"][-1]["status"])
        self.assertEqual("step_boundary", before["steps"][-1]["reason"])
        self.assertEqual("completed", after["steps"][-1]["status"])
        self.assertEqual(row["evidence"]["checkpoint"], after["recovery"]["report"])
        self.assertEqual(sha(workspace / row["evidence"]["checkpoint"]), after["recovery"]["sha256"])
        self.assertEqual(before["steps"][:-1], after["steps"][:-1])
        self.assertEqual("1", (workspace / "command-count.txt").read_text())
        self.assertEqual("finished\n", (workspace / "done.md").read_text())
        self.assertTrue(row["preserved_artifacts"])
        for item in row["preserved_artifacts"]:
            self.assertEqual(item["before_sha256"], item["after_sha256"])
            self.assertEqual(item["before_mtime_ns"], item["after_mtime_ns"])
            self.assertEqual(item["after_sha256"], sha(workspace / item["path"]))
        self.assertEqual("passed", self.read("recovery", "acceptance")["state"])

    def test_refusal_has_no_synthetic_trace_session_or_task(self):
        row = self.rows["refusal"]
        workspace = self.output / "refusal"
        self.assertIsNone(row["evidence"]["trace"])
        self.assertIsNone(row["task_id"])
        self.assertIsNone(row["spec_sha256"])
        self.assertIsNone(row["plan_sha256"])
        self.assertFalse((workspace / "traces").exists())
        self.assertFalse((workspace / "sessions").exists())
        self.assertFalse((workspace / ".pocketchat/tasks").exists())
        report = self.read("refusal", "report")
        self.assertEqual("pocketchat-dogfood-refusal.v1", report["schema_version"])
        self.assertEqual(0, report["provider_calls"])
        self.assertEqual(0, report["command_runs"])

    def test_existing_output_is_refused_without_modifying_artifacts(self):
        before = {p: sha(p) for p in self.output.rglob("*") if p.is_file()}
        proc = subprocess.run([sys.executable, str(DEMO), "--output", str(self.output)],
                              cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(2, proc.returncode)
        self.assertEqual(before, {p: sha(p) for p in self.output.rglob("*") if p.is_file()})


if __name__ == "__main__":
    unittest.main()
