"""Offline project-continuity workflow evidence tests; no provider network."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from tests import _helpers as hp


class ProjectContinuityDemoTests(unittest.TestCase):
    def example(self):
        path = (Path(__file__).resolve().parents[1]
                / "examples/project-continuity-demo.py")
        self.assertTrue(path.is_file(), "the project-continuity fixture is missing")
        spec = importlib.util.spec_from_file_location(
            "project_continuity_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "requires secure descriptor-relative reads")
    def test_candidate_accept_recall_and_revocation_workflow(self):
        example = self.example()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "evidence"
            result = example.demonstrate(root)
            self.assertTrue(result["passed"], result["checks"])
            self.assertTrue(all(result["checks"].values()), result["checks"])
            self.assertEqual("pocketchat-project-continuity-fixture.v1",
                             result["schema_version"])
            memory = result["memory"]
            self.assertEqual(1, memory["revision"])
            self.assertTrue(memory["memory_id"].startswith("mem_"))
            self.assertIn(memory["memory_id"], memory["source_ref"])
            cases = {row["case"]: row for row in result["cases"]}
            self.assertEqual(
                {"unaccepted", "accepted", "foreign_workspace", "expired", "retired"},
                set(cases))
            self.assertFalse(cases["unaccepted"]["recalled"])
            self.assertTrue(cases["accepted"]["recalled"])
            self.assertFalse(cases["foreign_workspace"]["recalled"])
            self.assertFalse(cases["expired"]["recalled"])
            self.assertFalse(cases["retired"]["recalled"])
            accepted = cases["accepted"]
            self.assertEqual(memory["memory_id"], accepted["memory_id"])
            self.assertEqual(memory["revision"], accepted["revision"])
            self.assertEqual(memory["source_ref"], accepted["source_ref"])
            for row in cases.values():
                self.assertEqual(0, row["exit_code"])
                self.assertTrue((root / row["trace_path"]).is_file())
                self.assertTrue((root / row["request_snapshot_path"]).is_file())
            self.assertTrue((root / "evidence.json").is_file())

    def test_existing_output_is_refused_without_touching_its_contents(self):
        example = self.example()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sentinel = root / "keep.txt"
            sentinel.write_text("user content\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                example.demonstrate(root)
            self.assertEqual("user content\n", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
