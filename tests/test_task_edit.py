"""TaskSpec-authorized overwrite: snapshot, conflict refusal and evidence."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_edit import (TaskEditError, apply_overwrite,
                                  snapshot_overwrite)
from pocketchat.task_spec import TaskSpecError, parse_spec
from tests.test_task_spec import minimal


def make_spec(**overrides):
    payload = minimal(overwrite_paths=["notes.txt"], inputs=["notes.txt"])
    payload["tools"] = {"write_file": {"allow_paths": ["notes.txt"]}}
    payload.update(overrides)
    return parse_spec(payload)


class TaskEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._git("init")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.invalid")
        (self.root / "notes.txt").write_text("old\nline\n", encoding="utf-8")
        self._git("add", "notes.txt")
        self._git("commit", "-m", "baseline")
        self.spec = make_spec()

    def _git(self, *args):
        import subprocess
        proc = subprocess.run(["git", "-C", str(self.root), *args],
                              capture_output=True, text=True, check=False)
        if proc.returncode:
            raise AssertionError(proc.stderr)
        return proc.stdout

    def test_overwrite_returns_hashes_and_line_summary(self):
        snapshot = snapshot_overwrite(self.root, "notes.txt", self.spec)
        result = apply_overwrite(
            self.root, "notes.txt", "new\nline\n", self.spec, snapshot)
        self.assertEqual("new\nline\n",
                         (self.root / "notes.txt").read_text(encoding="utf-8"))
        self.assertNotEqual(result.original_sha256, result.new_sha256)
        self.assertEqual((1, 1), (result.added_lines, result.removed_lines))
        self.assertEqual(len("new\nline\n".encode("utf-8")), result.bytes_after)

    def test_changed_between_snapshot_and_apply_is_refused(self):
        snapshot = snapshot_overwrite(self.root, "notes.txt", self.spec)
        (self.root / "notes.txt").write_text("other\n", encoding="utf-8")
        with self.assertRaisesRegex(TaskEditError, "changed since snapshot"):
            apply_overwrite(self.root, "notes.txt", "new\n", self.spec, snapshot)
        self.assertEqual("other\n", (self.root / "notes.txt").read_text())

    def test_unauthorized_spec_paths_are_refused(self):
        bad = minimal(overwrite_paths=["other.txt"], inputs=["other.txt"])
        bad["tools"] = {"write_file": {"allow_paths": ["other.txt"]}}
        # other.txt is allowed, but the editor still requires the exact
        # overwrite_paths entry it has; this asserts the spec path is enforced.
        spec = parse_spec(bad)
        with self.assertRaises(TaskEditError):
            snapshot_overwrite(self.root, "notes.txt", spec)
        with self.assertRaises(TaskSpecError):
            parse_spec(minimal(overwrite_paths=["notes.txt"]))

    def test_dirty_target_and_symlink_are_refused(self):
        (self.root / "notes.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(TaskEditError, "already dirty"):
            snapshot_overwrite(self.root, "notes.txt", self.spec)
        self._git("checkout", "--", "notes.txt")
        os.symlink(self.root / "notes.txt", self.root / "link.txt")
        spec = make_spec(overwrite_paths=["link.txt"], inputs=["link.txt"],
                         tools={"write_file": {"allow_paths": ["link.txt"]}})
        with self.assertRaisesRegex(TaskEditError, "symbolic links"):
            snapshot_overwrite(self.root, "link.txt", spec)

    def test_binary_and_unlisted_paths_are_refused(self):
        (self.root / "binary.bin").write_bytes(b"\x00\x01")
        spec = make_spec(overwrite_paths=["binary.bin"], inputs=["binary.bin"],
                         tools={"write_file": {"allow_paths": ["binary.bin"]}})
        with self.assertRaises(TaskEditError):
            snapshot_overwrite(self.root, "binary.bin", spec)
        with self.assertRaises(TaskEditError):
            snapshot_overwrite(self.root, "notes.txt", make_spec(
                overwrite_paths=[], inputs=["notes.txt"],
                tools={"write_file": {"allow_paths": ["notes.txt"]}}))

    def test_schema_declares_optional_overwrite_paths(self):
        path = (Path(__file__).resolve().parents[1] / "pocketchat" / "schemas"
                / "task-spec.schema.json")
        schema = json.loads(path.read_text(encoding="utf-8"))
        overwrite = schema["properties"]["overwrite_paths"]
        self.assertEqual("array", overwrite["type"])
        self.assertNotIn("overwrite_paths", schema["required"])
        self.assertIn("tools.write_file.allow_paths", overwrite["description"])

    def test_spec_requires_overwrite_path_to_be_declared_and_granted(self):
        for overrides in (
            {"overwrite_paths": ["notes.txt"], "inputs": []},
            {"overwrite_paths": ["notes.txt"], "inputs": ["notes.txt"],
             "tools": {"write_file": {"allow_paths": ["other.txt"]}}},
        ):
            with self.subTest(overrides=overrides):
                payload = minimal()
                payload.update(overrides)
                with self.assertRaises(TaskSpecError):
                    parse_spec(payload)

    def test_overwrite_uses_strict_content_bound(self):
        snapshot = snapshot_overwrite(self.root, "notes.txt", self.spec)
        with self.assertRaises(TaskEditError):
            apply_overwrite(self.root, "notes.txt", "x" * (16 * 1024 + 1),
                            self.spec, snapshot)
        self.assertEqual("old\nline\n", (self.root / "notes.txt").read_text())


if __name__ == "__main__":
    unittest.main()
