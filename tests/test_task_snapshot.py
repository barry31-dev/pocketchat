"""Content snapshots over an explicit, frozen task-context selection."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_context import CONTEXT_TREE_SCHEMA, EXCLUDED_DIRECTORY_NAMES
from pocketchat.task_selection import MAX_TREE_MANIFEST_BYTES, select_files
from pocketchat.task_snapshot import (CONTEXT_SNAPSHOT_SCHEMA,
                                      MAX_SNAPSHOT_BYTES,
                                      MAX_SNAPSHOT_FILE_BYTES, TaskSnapshotError,
                                      load_selection, parse_selection,
                                      snapshot_files)
from tests._helpers import run_cli


def tree(files=None):
    files = files or [
        {"path": "README.md", "bytes": 5},
        {"path": "docs/context.md", "bytes": 9},
        {"path": "src/task_context.py", "bytes": 13},
        {"path": "tests/test_task_context.py", "bytes": 13},
    ]
    paths = [item["path"] for item in files]
    return {
        "schema_version": CONTEXT_TREE_SCHEMA,
        "workspace": ".",
        "includes": ["."],
        "limits": {"max_files": 512, "max_depth": 12,
                   "max_path_chars": 65536},
        "usage": {"files": len(files), "omitted": 0,
                  "path_chars": sum(len(path) for path in paths) + 1},
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "files": files,
        "omitted": [],
    }


def selection(tree_payload=None, queries=None, max_files=8):
    return select_files(tree_payload or tree(), queries or ["task", "context"],
                        max_files=max_files)


def write_workspace(root, *, files=None):
    files = files or {
        "README.md": b"hello",
        "docs/context.md": b"context!!",
        "src/task_context.py": b"task context\n",
        "tests/test_task_context.py": b"context test\n",
    }
    for relative, data in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


class SelectionContractTests(unittest.TestCase):
    def test_valid_selection_is_closed_and_rederived(self):
        frozen = tree()
        selected = selection(frozen, max_files=2)
        parsed = parse_selection(selected, frozen)
        self.assertEqual(selected, parsed)
        self.assertEqual(2, len(parsed["matches"]))
        self.assertTrue(parsed["truncated"])

    def test_selection_file_is_bounded_regular_and_not_a_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "selection.json"
            target.write_text(json.dumps(selection()), encoding="utf-8")
            try:
                (root / "link.json").symlink_to(target)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(TaskSnapshotError):
                    load_selection(root / "link.json", tree())
            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (MAX_TREE_MANIFEST_BYTES + 1))
            with self.assertRaises(TaskSnapshotError):
                load_selection(oversized, tree())

    def test_selection_cli_envelopes_and_duplicate_members_are_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "selection.json"
            path.write_text(json.dumps({"ok": True, **selection()}), encoding="utf-8")
            self.assertEqual(selection(), load_selection(path, tree()))
            path.write_text(json.dumps({"ok": False, **selection()}), encoding="utf-8")
            with self.assertRaisesRegex(TaskSnapshotError, "ok=true"):
                load_selection(path, tree())
            path.write_text('{"schema_version":"a","schema_version":"b"}',
                            encoding="utf-8")
            with self.assertRaisesRegex(TaskSnapshotError, "duplicate JSON member"):
                load_selection(path, tree())

    def test_tampered_or_inconsistent_selection_is_refused(self):
        frozen = tree()
        good = selection(frozen, max_files=2)
        cases = []
        for mutate in (
            lambda row: row.update(extra=True),
            lambda row: row.update(tree_sha256="0" * 64),
            lambda row: row["matches"][0].update(path="src/other.py"),
            lambda row: row["matches"][0].update(bytes=999),
            lambda row: row["matches"][0].update(score=1),
            lambda row: row.update(total_matches=99),
            lambda row: row.update(truncated=False),
            lambda row: row.update(schema_version="wrong"),
        ):
            broken = json.loads(json.dumps(good))
            mutate(broken)
            cases.append(broken)
        for broken in cases:
            with self.subTest(broken=broken):
                with self.assertRaises(TaskSnapshotError):
                    parse_selection(broken, frozen)


class SnapshotTests(unittest.TestCase):
    def test_explicit_matches_only_and_stable_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root)
            selected = selection(frozen, max_files=2)
            first = snapshot_files(root, frozen, selected)
            second = snapshot_files(root, frozen, selected)
            self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
            self.assertEqual(CONTEXT_SNAPSHOT_SCHEMA, first["schema_version"])
            self.assertEqual(selected["tree_sha256"], first["tree_sha256"])
            self.assertEqual(2, len(first["files"]))
            self.assertEqual([row["path"] for row in selected["matches"]],
                             [row["path"] for row in first["files"]])
            for row in first["files"]:
                data = (root / row["path"]).read_bytes()
                self.assertEqual(len(data), row["bytes"])
                self.assertEqual(row["selection_bytes"], len(data))
                self.assertFalse(row["selection_size_changed"])
                self.assertEqual(row["sha256"], __import__("hashlib").sha256(data).hexdigest())

    def test_content_change_changes_snapshot_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root)
            selected = selection(frozen, max_files=2)
            before = snapshot_files(root, frozen, selected)
            target = root / before["files"][0]["path"]
            target.write_text("changed\n", encoding="utf-8")
            after = snapshot_files(root, frozen, selected)
            self.assertNotEqual(before["snapshot_sha256"], after["snapshot_sha256"])
            self.assertNotEqual(before["files"][0]["sha256"], after["files"][0]["sha256"])

    def test_empty_selection_is_explicit_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            selected = selection(frozen, queries=["unmatched"])
            result = snapshot_files(root, frozen, selected)
            self.assertEqual([], result["files"])
            self.assertFalse(result["selection_truncated"])

    def test_actual_byte_change_is_recorded_and_total_budget_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root, files={"README.md": b"x", "docs/context.md": b"y",
                                         "src/task_context.py": b"z",
                                         "tests/test_task_context.py": b"w"})
            selected = selection(frozen, max_files=1)
            result = snapshot_files(root, frozen, selected)
            self.assertEqual(1, result["selection_size_changed_files"])
            self.assertTrue(result["files"][0]["selection_size_changed"])
            for limit in (0, MAX_SNAPSHOT_BYTES + 1, True, "8"):
                with self.subTest(limit=limit):
                    with self.assertRaises(TaskSnapshotError):
                        snapshot_files(root, frozen, selected, max_bytes=limit)

    def test_symlink_sensitive_binary_and_non_utf8_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_workspace(root)
            (root / "src" / "linked.py").symlink_to(root / "src" / "task_context.py")
            (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
            (root / "src" / "image.png").write_bytes(b"not a png")
            (root / "src" / "nul.py").write_bytes(b"bad\x00data")
            frozen = tree(files=[
                {"path": ".env", "bytes": 9},
                {"path": "src/image.png", "bytes": 9},
                {"path": "src/linked.py", "bytes": 13},
                {"path": "src/nul.py", "bytes": 8},
            ])
            selected = selection(frozen, queries=["linked", "env", "image", "nul"], max_files=4)
            with self.assertRaises(TaskSnapshotError):
                snapshot_files(root, frozen, selected)

    def test_sensitive_component_does_not_reject_a_harmless_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_workspace(root, files={"docs/env-notes.md": b"notes\n"})
            frozen = tree(files=[{"path": "docs/env-notes.md", "bytes": 6}])
            selected = selection(frozen, queries=["env"], max_files=1)
            result = snapshot_files(root, frozen, selected)
            self.assertEqual("docs/env-notes.md", result["files"][0]["path"])

    def test_same_size_content_change_is_not_mislabeled_as_size_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_workspace(root, files={"README.md": b"hello"})
            frozen = tree(files=[{"path": "README.md", "bytes": 5}])
            selected = selection(frozen, queries=["readme"], max_files=1)
            before = snapshot_files(root, frozen, selected)
            (root / "README.md").write_bytes(b"world")
            after = snapshot_files(root, frozen, selected)
            self.assertNotEqual(before["snapshot_sha256"], after["snapshot_sha256"])
            self.assertNotEqual(before["files"][0]["sha256"], after["files"][0]["sha256"])
            self.assertFalse(after["files"][0]["selection_size_changed"])
            self.assertEqual(0, after["selection_size_changed_files"])

    def test_zero_byte_file_can_follow_an_exact_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_workspace(root, files={"README.md": b"abc", "docs/context.md": b""})
            frozen = tree(files=[
                {"path": "README.md", "bytes": 3},
                {"path": "docs/context.md", "bytes": 0},
            ])
            selected = selection(frozen, queries=["readme", "context"], max_files=2)
            result = snapshot_files(root, frozen, selected, max_bytes=3)
            self.assertEqual(3, result["total_bytes"])
            self.assertEqual(2, result["selected_files"])

    def test_workspace_parent_name_does_not_trigger_sensitive_path_policy(self):
        with tempfile.TemporaryDirectory(prefix=".env-") as directory:
            root = Path(directory)
            write_workspace(root, files={"README.md": b"hello"})
            frozen = tree(files=[{"path": "README.md", "bytes": 5}])
            selected = selection(frozen, queries=["readme"], max_files=1)
            result = snapshot_files(root, frozen, selected)
            self.assertEqual(1, result["selected_files"])

    def test_selected_file_over_its_own_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"x" * (MAX_SNAPSHOT_FILE_BYTES + 1)
            write_workspace(root, files={"README.md": payload})
            frozen = tree(files=[{"path": "README.md", "bytes": len(payload)}])
            selected = selection(frozen, queries=["readme"], max_files=1)
            with self.assertRaisesRegex(TaskSnapshotError, "selected file exceeds"):
                snapshot_files(root, frozen, selected)

    def test_unselected_file_is_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root)
            selected = selection(frozen, queries=["task_context"], max_files=1)
            target = root / selected["matches"][0]["path"]
            target.write_bytes(b"different size\n")
            # An unselected file with malformed contents is irrelevant because
            # it is never opened.
            (root / "docs" / "context.md").write_bytes(b"\x00")
            result = snapshot_files(root, frozen, selected)
            self.assertEqual(1, result["selection_size_changed_files"])


class SnapshotCliTests(unittest.TestCase):
    def test_json_and_human_snapshot_are_read_only_and_share_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root)
            selected = selection(frozen, max_files=2)
            (root / "tree.json").write_text(json.dumps(frozen), encoding="utf-8")
            (root / "selection.json").write_text(json.dumps(selected), encoding="utf-8")
            machine = run_cli(("task", "context", "snapshot", "tree.json",
                               "selection.json", "--json"), root)
            human = run_cli(("task", "context", "snapshot", "tree.json",
                             "selection.json"), root)
            self.assertEqual(0, machine.returncode, machine.stdout + machine.stderr)
            payload = json.loads(machine.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(2, len(payload["files"]))
            self.assertEqual(0, human.returncode, human.stdout + human.stderr)
            self.assertIn("context snapshot", human.stdout)
            self.assertIn(payload["snapshot_sha256"][:12], human.stdout)
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_real_scan_select_snapshot_cli_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_workspace(root, files={"src/task_context.py": b"task context\n",
                                         "tests/test_task_context.py": b"context test\n"})
            scan = run_cli(("task", "context", "scan", ".", "--include", "src",
                            "--include", "tests", "--json"), root)
            self.assertEqual(0, scan.returncode, scan.stdout + scan.stderr)
            (root / "tree.json").write_text(scan.stdout, encoding="utf-8")
            select = run_cli(("task", "context", "select", "tree.json",
                              "--query", "task", "--query", "context", "--json"), root)
            self.assertEqual(0, select.returncode, select.stdout + select.stderr)
            (root / "selection.json").write_text(select.stdout, encoding="utf-8")
            snap = run_cli(("task", "context", "snapshot", "tree.json",
                            "selection.json", "--json"), root)
            self.assertEqual(0, snap.returncode, snap.stdout + snap.stderr)
            payload = json.loads(snap.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(2, payload["selected_files"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())

    def test_invalid_selection_is_exit_two_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = tree()
            write_workspace(root)
            selected = selection(frozen, max_files=2)
            selected["matches"][0]["path"] = "src/not-selected.py"
            (root / "tree.json").write_text(json.dumps(frozen), encoding="utf-8")
            (root / "selection.json").write_text(json.dumps(selected), encoding="utf-8")
            result = run_cli(("task", "context", "snapshot", "tree.json",
                              "selection.json", "--json"), root)
            self.assertEqual(2, result.returncode)
            self.assertEqual("task", json.loads(result.stdout)["error"])
            self.assertFalse((root / ".pocketchat").exists())
            self.assertFalse((root / "traces").exists())


if __name__ == "__main__":
    unittest.main()
