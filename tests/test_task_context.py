"""Bounded, deterministic repository-tree context discovery."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_context import (CONTEXT_TREE_SCHEMA, TaskContextError,
                                     scan_tree)
from tests._helpers import run_cli


class ScanTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative, content="x"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def scan(self, workspace=None, **options):
        previous = Path.cwd()
        try:
            os.chdir(str(self.root.parent))
            target = Path(self.root.name) if workspace is None else Path(workspace)
            return scan_tree(target, **options)
        finally:
            os.chdir(str(previous))

    def test_scan_is_sorted_deduplicated_and_does_not_read_contents(self):
        self.write("z.txt", "do-not-copy-this-secret")
        self.write("src/b.py", "bb")
        self.write("src/a.py", "a")
        self.write(".git/config", "private metadata")
        self.write(".pocketchat/tasks/task.json", "runtime evidence")

        result = self.scan(includes=[".", "src"])

        self.assertEqual(CONTEXT_TREE_SCHEMA, result["schema_version"])
        self.assertEqual(".", result["workspace"])
        self.assertEqual([".", "src"], result["includes"])
        self.assertEqual(
            [
                {"path": "src/a.py", "bytes": 1},
                {"path": "src/b.py", "bytes": 2},
                {"path": "z.txt", "bytes": len("do-not-copy-this-secret")},
            ],
            result["files"],
        )
        self.assertEqual(
            [
                {"path": ".git", "reason": "excluded_directory"},
                {"path": ".pocketchat", "reason": "excluded_directory"},
            ],
            result["omitted"],
        )
        self.assertNotIn("do-not-copy-this-secret", json.dumps(result))
        self.assertEqual(3, result["usage"]["files"])

    def test_symlinks_are_named_but_never_followed(self):
        outside = Path(self.temp.name + "-outside")
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir() if outside.exists() else None)
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        self.addCleanup(lambda: (outside / "secret.txt").unlink(missing_ok=True))
        try:
            os.symlink(str(outside), str(self.root / "linked"))
            os.symlink(str(outside / "secret.txt"), str(self.root / "file-link"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")

        result = self.scan()

        self.assertEqual([], result["files"])
        self.assertEqual(
            [
                {"path": "file-link", "reason": "symlink"},
                {"path": "linked", "reason": "symlink"},
            ],
            result["omitted"],
        )

    def test_include_collection_shape_fails_closed(self):
        for includes in ([], "src", 3):
            with self.subTest(includes=includes):
                with self.assertRaises(TaskContextError):
                    self.scan(includes=includes)

    def test_root_and_include_paths_fail_closed(self):
        self.write("src/a.py")
        with self.assertRaises(TaskContextError):
            scan_tree(self.root)
        for include in ("/tmp", "../src", "src/../a", "src\\a", "src//a", "src/./a"):
            with self.subTest(include=include):
                with self.assertRaises(TaskContextError):
                    self.scan(includes=[include])
        with self.assertRaises(TaskContextError):
            self.scan(includes=["missing"])
        with self.assertRaises(TaskContextError):
            self.scan(Path(self.root.name) / "src/a.py")
        try:
            os.symlink(str(self.root), str(self.root.parent / (self.root.name + "-link")))
        except (OSError, NotImplementedError):
            return
        linked = self.root.parent / (self.root.name + "-link")
        self.addCleanup(lambda: linked.unlink(missing_ok=True))
        with self.assertRaises(TaskContextError):
            self.scan(linked.name)

    def test_an_explicit_include_cannot_cross_a_symlink_component(self):
        real = self.root / "real"
        real.mkdir()
        (real / "a.py").write_text("a", encoding="utf-8")
        try:
            os.symlink(str(real), str(self.root / "alias"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(TaskContextError):
            self.scan(includes=["alias/a.py"])

    def test_workspace_root_cannot_cross_a_parent_symlink(self):
        with tempfile.TemporaryDirectory() as lexical, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "repo"
            target.mkdir()
            try:
                os.symlink(outside, str(Path(lexical) / "alias"))
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            previous = Path.cwd()
            try:
                os.chdir(str(Path(lexical).parent))
                relative = Path(lexical).name + "/alias/repo"
                with self.assertRaises(TaskContextError):
                    scan_tree(Path(relative))
            finally:
                os.chdir(str(previous))

    def test_explicit_include_prefixes_consume_path_budget(self):
        self.write("a/b/c/file")
        with self.assertRaises(TaskContextError):
            self.scan(includes=["a/b/c/file"], max_path_chars=10)

    def test_nul_include_is_a_controlled_refusal(self):
        with self.assertRaises(TaskContextError):
            self.scan(includes=["bad\0name"])

    def test_path_budget_counts_empty_directories_not_only_output_rows(self):
        for name in ("alpha", "beta", "gamma"):
            (self.root / name).mkdir()
        with self.assertRaises(TaskContextError):
            self.scan(max_path_chars=8)

    def test_budgets_refuse_instead_of_returning_a_partial_tree(self):
        self.write("a.txt")
        self.write("b.txt")
        self.write("src/deep/c.txt")
        cases = (
            {"max_files": 1},
            {"max_depth": 2},
            {"max_path_chars": 5},
        )
        for limits in cases:
            with self.subTest(limits=limits):
                with self.assertRaises(TaskContextError):
                    self.scan(**limits)


class ContextCliTests(unittest.TestCase):
    def test_json_and_human_output_share_the_read_only_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/a.py").write_text("a", encoding="utf-8")
            (root / "src/b.py").write_text("bb", encoding="utf-8")

            machine = run_cli(
                ("task", "context", "scan", ".", "--include", "src", "--json"), root)
            human = run_cli(
                ("task", "context", "scan", ".", "--include", "src"), root)

            self.assertEqual(0, machine.returncode, machine.stdout + machine.stderr)
            self.assertEqual("", machine.stderr)
            payload = json.loads(machine.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(["src/a.py", "src/b.py"],
                             [item["path"] for item in payload["files"]])
            self.assertEqual(0, human.returncode, human.stdout + human.stderr)
            self.assertEqual("", human.stderr)
            self.assertIn("context tree .  files=2 omitted=0", human.stdout)
            self.assertLess(human.stdout.index("src/a.py"), human.stdout.index("src/b.py"))
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_cli_refusal_is_exit_two_without_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_cli(
                ("task", "context", "scan", ".", "--include", "../outside", "--json"),
                root,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertEqual("task", json.loads(result.stdout)["error"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())


if __name__ == "__main__":
    unittest.main()
