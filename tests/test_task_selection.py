"""Deterministic relevant-file selection from a frozen context tree."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_context import (CONTEXT_TREE_SCHEMA,
                                     EXCLUDED_DIRECTORY_NAMES)
from pocketchat.task_selection import (CONTEXT_SELECTION_SCHEMA,
                                       MAX_TREE_MANIFEST_BYTES,
                                       TaskSelectionError, load_tree, parse_tree,
                                       select_files, tree_sha256)
from tests._helpers import run_cli


def tree(files=None, omitted=None):
    files = files or [
        {"path": "README.md", "bytes": 10},
        {"path": "docs/context.md", "bytes": 20},
        {"path": "src/task_context.py", "bytes": 30},
        {"path": "tests/test_task_context.py", "bytes": 40},
    ]
    omitted = omitted or []
    paths = [item["path"] for item in files + omitted]
    return {
        "schema_version": CONTEXT_TREE_SCHEMA,
        "workspace": ".",
        "includes": ["."],
        "limits": {"max_files": 512, "max_depth": 12,
                   "max_path_chars": 65536},
        "usage": {"files": len(files), "omitted": len(omitted),
                  "path_chars": sum(len(path) for path in paths) + 1},
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "files": files,
        "omitted": omitted,
    }


class ManifestTests(unittest.TestCase):
    def test_valid_tree_is_closed_and_canonical(self):
        payload = tree()
        parsed = parse_tree(payload)
        self.assertEqual(payload, parsed)
        reordered = {key: payload[key] for key in reversed(list(payload))}
        self.assertEqual(tree_sha256(payload), tree_sha256(reordered))

    def test_manifest_file_is_bounded_regular_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "tree.json"
            target.write_text(json.dumps(tree()), encoding="utf-8")
            try:
                (root / "tree-link.json").symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(TaskSelectionError):
                    load_tree(root / "tree-link.json")
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (MAX_TREE_MANIFEST_BYTES + 1))
            with self.assertRaises(TaskSelectionError):
                load_tree(oversized)

    def test_manifest_fifo_is_refused_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "tree.fifo"
            os.mkfifo(str(fifo))
            code = (
                "from pathlib import Path; "
                "from pocketchat.task_selection import load_tree, TaskSelectionError; "
                "p=Path(%r); "
                "exec(\"try:\\n load_tree(p)\\nexcept TaskSelectionError:\\n pass\\nelse:\\n raise SystemExit(3)\")"
                % str(fifo)
            )
            proc = subprocess.run(
                [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=2)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_scan_cli_envelope_is_unwrapped_but_false_ok_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tree.json"
            path.write_text(json.dumps({"ok": True, **tree()}), encoding="utf-8")
            self.assertEqual(tree(), load_tree(path))
            path.write_text(json.dumps({"ok": False, **tree()}), encoding="utf-8")
            with self.assertRaises(TaskSelectionError):
                load_tree(path)

    def test_manifest_file_rejects_duplicate_json_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tree.json"
            path.write_text('{"schema_version":"a","schema_version":"b"}',
                            encoding="utf-8")
            with self.assertRaisesRegex(TaskSelectionError, "duplicate JSON member"):
                load_tree(path)

    def test_malformed_or_inconsistent_tree_is_refused(self):
        cases = []
        extra = tree()
        extra["extra"] = True
        cases.append(extra)
        wrong_schema = tree()
        wrong_schema["schema_version"] = "other"
        cases.append(wrong_schema)
        unsorted = tree()
        unsorted["files"] = list(reversed(unsorted["files"]))
        cases.append(unsorted)
        duplicate = tree()
        duplicate["files"].append(dict(duplicate["files"][0]))
        duplicate["usage"]["files"] += 1
        cases.append(duplicate)
        bad_path = tree()
        bad_path["files"][0]["path"] = "../README.md"
        cases.append(bad_path)
        bad_bytes = tree()
        bad_bytes["files"][0]["bytes"] = -1
        cases.append(bad_bytes)
        bad_usage = tree()
        bad_usage["usage"]["files"] = 99
        cases.append(bad_usage)
        bad_excludes = tree()
        bad_excludes["excluded_directory_names"] = []
        cases.append(bad_excludes)
        outside_include = tree()
        outside_include["includes"] = ["src"]
        cases.append(outside_include)
        cases.append(tree(omitted=[
            {"path": "README.md", "reason": "symlink"},
        ]))
        cases.append(tree(omitted=[
            {"path": "hidden", "reason": []},
        ]))
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(TaskSelectionError):
                    parse_tree(payload)


class SelectionTests(unittest.TestCase):
    def test_exact_basename_tokens_rank_then_tie_break_by_path(self):
        result = select_files(tree(), ["context", "task"], max_files=2)
        self.assertEqual(CONTEXT_SELECTION_SCHEMA, result["schema_version"])
        self.assertEqual(["context", "task"], result["query_terms"])
        self.assertEqual(4, result["candidates_considered"])
        self.assertEqual(3, result["total_matches"])
        self.assertTrue(result["truncated"])
        self.assertEqual([], result["unmatched_terms"])
        self.assertEqual(
            ["src/task_context.py", "tests/test_task_context.py"],
            [item["path"] for item in result["matches"]],
        )
        self.assertEqual([8, 8], [item["score"] for item in result["matches"]])
        self.assertEqual(["context", "task"], result["matches"][0]["matched_terms"])

    def test_directory_token_scores_below_basename_and_omitted_never_matches(self):
        payload = tree(
            files=[{"path": "docs/guide.md", "bytes": 1},
                   {"path": "src/docs.py", "bytes": 2}],
            omitted=[{"path": "docs/private.md", "reason": "symlink"}],
        )
        result = select_files(payload, ["docs"], max_files=8)
        self.assertEqual(["src/docs.py", "docs/guide.md"],
                         [item["path"] for item in result["matches"]])
        self.assertEqual([4, 2], [item["score"] for item in result["matches"]])
        self.assertNotIn("docs/private.md", [item["path"] for item in result["matches"]])

    def test_query_normalization_digest_and_empty_matches_are_explicit(self):
        first = select_files(tree(), ["Task context", "task"], max_files=8)
        second = select_files(tree(), ["context", "TASK"], max_files=8)
        self.assertEqual(["context", "task"], first["query_terms"])
        self.assertEqual(first["tree_sha256"], second["tree_sha256"])
        self.assertEqual(first["matches"], second["matches"])
        empty = select_files(tree(), ["unmatched"], max_files=8)
        self.assertEqual([], empty["matches"])
        self.assertEqual(["unmatched"], empty["unmatched_terms"])
        self.assertFalse(empty["truncated"])

    def test_query_iterable_stops_at_the_declared_token_bound(self):
        def too_many():
            for index in range(33):
                yield "term%d" % index
            raise AssertionError("selector read beyond its query bound")

        with self.assertRaises(TaskSelectionError):
            select_files(tree(), too_many(), max_files=8)

    def test_query_length_refusal_names_the_128_character_limit(self):
        with self.assertRaisesRegex(TaskSelectionError, "at most 128"):
            select_files(tree(), ["a" * 129], max_files=8)

    def test_query_and_output_bounds_fail_closed(self):
        for terms in ([], "task", [""], ["bad\0term"], [3]):
            with self.subTest(terms=terms):
                with self.assertRaises(TaskSelectionError):
                    select_files(tree(), terms, max_files=8)
        for limit in (0, 65, True, "8"):
            with self.subTest(limit=limit):
                with self.assertRaises(TaskSelectionError):
                    select_files(tree(), ["task"], max_files=limit)


class SelectionCliTests(unittest.TestCase):
    def test_json_and_human_selection_are_read_only_and_share_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tree.json").write_text(json.dumps(tree()), encoding="utf-8")
            machine = run_cli(("task", "context", "select", "tree.json",
                               "--query", "task", "--query", "context",
                               "--max-files", "2", "--json"), root)
            human = run_cli(("task", "context", "select", "tree.json",
                             "--query", "task", "--query", "context",
                             "--max-files", "2"), root)
            self.assertEqual(0, machine.returncode, machine.stdout + machine.stderr)
            payload = json.loads(machine.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(2, len(payload["matches"]))
            self.assertEqual(0, human.returncode, human.stdout + human.stderr)
            self.assertIn("context selection 2/3", human.stdout)
            self.assertIn("score=8", human.stdout)
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_invalid_manifest_is_exit_two_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = tree()
            broken["files"] = list(reversed(broken["files"]))
            (root / "tree.json").write_text(json.dumps(broken), encoding="utf-8")
            result = run_cli(("task", "context", "select", "tree.json",
                              "--query", "task", "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("task", json.loads(result.stdout)["error"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())


if __name__ == "__main__":
    unittest.main()
