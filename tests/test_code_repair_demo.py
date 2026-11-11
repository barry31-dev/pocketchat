"""Portable code-repair orchestration checks; these do not compile Swift."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from tests import _helpers as hp


class CodeRepairDemoTests(unittest.TestCase):
    def example(self):
        path = Path(__file__).resolve().parents[1] / "examples/code-repair-demo.py"
        self.assertTrue(path.is_file(), "the code-repair CLI fixture is missing")
        spec = importlib.util.spec_from_file_location("code_repair_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS,
                         "requires secure descriptor-relative reads")
    def test_real_command_repair_diff_acceptance_and_no_replay_resume(self):
        example = self.example()
        template = {
            "id": "swift_tests", "cwd": ".", "timeout_seconds": 10,
            "max_output_bytes": 1024,
            "argv": [sys.executable, "-c", (
                "from pathlib import Path; "
                "good = '0...limit' in Path('Sources/BoundedSum/BoundedSum.swift').read_text(); "
                "print('portable fixture validator: ' + ('pass' if good else 'fail')); "
                "raise SystemExit(0 if good else 1)"
            )],
        }
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "evidence"
            result = example.demonstrate(root, template=template)
            self.assertTrue(all(result["checks"].values()), result["checks"])
            self.assertEqual("operator-supplied fixture validator", result["validation_scope"])
            self.assertEqual([1, 0], [row["exit_code"] for row in result["commands"]])
            self.assertEqual(1, result["repair_provider_calls"])
            self.assertIn("0...limit", (root / result["source"]["path"]).read_text())
            self.assertIn("-        return (0..<limit)", (root / "repair.diff").read_text())
            self.assertIn("+        return (0...limit)", (root / "repair.diff").read_text())
            self.assertTrue((root / "evidence.json").is_file())

    def test_existing_output_is_refused_without_touching_its_contents(self):
        example = self.example()
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            sentinel = root / "keep.txt"
            sentinel.write_text("user content\n")
            with self.assertRaisesRegex(ValueError, "new or empty"):
                example.demonstrate(root)
            self.assertEqual("user content\n", sentinel.read_text())


if __name__ == "__main__":
    unittest.main()
