"""Read-only tool execution and workspace permission tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pocketchat.tools import (ToolError, ToolPermissionError,
                              execute_read_file, execute_write_file)
from pocketchat.trace import TraceRecorder
from tests._helpers import run_cli


class ReadFileToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "notes.txt").write_text("hello\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=value\n", encoding="utf-8")
        (self.root / "binary.bin").write_bytes(b"\x00\x01\x02")

    def execute(self, path, *, allowed=("notes.txt",), max_bytes=1024):
        trace = TraceRecorder(self.root / "trace.jsonl", "run_tools")
        result = execute_read_file(
            self.root, path, allowed_paths=allowed, max_bytes=max_bytes,
            recorder=trace, tool_call_id="tool_1")
        events = [json.loads(line) for line in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        return result, events

    def test_reads_an_explicitly_allowed_workspace_file_and_records_events(self):
        result, events = self.execute("notes.txt")
        self.assertEqual("hello\n", result["content"])
        self.assertFalse(result["truncated"])
        self.assertEqual(["permission.requested", "permission.resolved",
                          "tool.call.started", "tool.call.completed"],
                         [event["type"] for event in events])
        self.assertEqual("none", events[-1]["payload"]["effect_disposition"])

    def test_unlisted_path_is_refused_before_reading(self):
        with self.assertRaises(ToolPermissionError):
            self.execute("notes.txt", allowed=("other.txt",))
        events = [json.loads(line) for line in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual(["permission.requested", "permission.resolved",
                          "tool.call.failed"],
                         [event["type"] for event in events])
        self.assertEqual("denied", events[1]["payload"]["decision"])

    def test_path_escape_is_refused(self):
        with self.assertRaises(ToolPermissionError):
            self.execute("../outside.txt", allowed=("../outside.txt",))
        events = [json.loads(line) for line in
                  (self.root / "trace.jsonl").read_text().splitlines()]
        self.assertEqual("path_escape", events[-1]["payload"]["guard"])

    def test_sensitive_dotfiles_and_binary_files_are_refused(self):
        for path in (".env", "binary.bin"):
            with self.subTest(path=path):
                with self.assertRaises(ToolError):
                    self.execute(path, allowed=(path,))

    def test_refusal_error_types_distinguish_the_reason(self):
        for path, allowed, expected in (
                ("notes.txt", ("other.txt",), "not_allowlisted"),
                (".env", (".env",), "denylisted_path"),
                ("binary.bin", ("binary.bin",), "binary_file"),
                ("missing.txt", ("missing.txt",), "file_not_found")):
            with self.subTest(path=path):
                with self.assertRaises(ToolPermissionError):
                    self.execute(path, allowed=allowed)
                events = [json.loads(line) for line in
                          (self.root / "trace.jsonl").read_text().splitlines()]
                self.assertEqual(expected,
                                 events[-1]["payload"]["error"]["type"])

    def test_truncation_does_not_split_a_utf8_character(self):
        (self.root / "utf8.txt").write_text("中文", encoding="utf-8")
        result, _ = self.execute(
            "utf8.txt", allowed=("utf8.txt",), max_bytes=4)
        self.assertEqual("中", result["content"])
        self.assertTrue(result["truncated"])

    def test_cli_requires_an_explicit_allow_and_can_emit_json(self):
        allowed = run_cli(
            ("tool", "read", "notes.txt", "--allow", "notes.txt", "--json"),
            self.root)
        self.assertEqual(0, allowed.returncode, allowed.stdout + allowed.stderr)
        self.assertEqual("hello\n", json.loads(allowed.stdout)["content"])
        denied = run_cli(("tool", "read", "notes.txt", "--json"), self.root)
        self.assertEqual(2, denied.returncode)
        self.assertFalse(json.loads(denied.stdout)["ok"])

    def test_cli_write_requires_allow_and_overwrite(self):
        written = run_cli(
            ("tool", "write", "cli.txt", "from cli", "--allow", "cli.txt", "--json"),
            self.root)
        self.assertEqual(0, written.returncode, written.stdout + written.stderr)
        self.assertEqual("from cli", json.loads(written.stdout)["content"])
        denied = run_cli(("tool", "write", "cli.txt", "again", "--allow", "cli.txt", "--json"), self.root)
        self.assertEqual(2, denied.returncode)
        replaced = run_cli(
            ("tool", "write", "cli.txt", "again", "--allow", "cli.txt", "--overwrite", "--json"),
            self.root)
        self.assertEqual(0, replaced.returncode, replaced.stdout + replaced.stderr)

    def test_backslash_paths_are_normalized_to_workspace_relative_form(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "note.txt").write_text("backslash\n", encoding="utf-8")
        result = execute_read_file(
            self.root, "nested\\note.txt",
            allowed_paths=["nested\\note.txt"], max_bytes=100,
            recorder=None)
        self.assertEqual("nested/note.txt", result["path"])
        self.assertEqual("backslash\n", result["content"])

    def test_write_creates_only_when_allowlisted_and_fails_closed_on_existing(self):
        trace = self.root / "write.jsonl"
        recorder = TraceRecorder(trace, "run_tools")
        result = execute_write_file(
            self.root, "new.txt", "hello", allowed_paths=["new.txt"],
            recorder=recorder, tool_call_id="tool_w")
        self.assertEqual("new.txt", result["path"])
        self.assertEqual("hello", (self.root / "new.txt").read_text(encoding="utf-8"))
        with self.assertRaises(ToolPermissionError):
            execute_write_file(
                self.root, "new.txt", "changed", allowed_paths=["new.txt"],
                recorder=recorder, tool_call_id="tool_w2")
        self.assertEqual("hello", (self.root / "new.txt").read_text(encoding="utf-8"))

    def test_overwrite_requires_explicit_flag_and_stays_inside_workspace(self):
        (self.root / "target.txt").write_text("old", encoding="utf-8")
        trace = self.root / "write.jsonl"
        recorder = TraceRecorder(trace, "run_tools")
        with self.assertRaises(ToolPermissionError):
            execute_write_file(
                self.root, "target.txt", "new", allowed_paths=["target.txt"],
                recorder=recorder)
        result = execute_write_file(
            self.root, "target.txt", "new", allowed_paths=["target.txt"],
            overwrite=True, recorder=recorder)
        self.assertEqual("new", result["content"])
        self.assertEqual("new", (self.root / "target.txt").read_text(encoding="utf-8"))
        with self.assertRaises(ToolPermissionError):
            execute_write_file(
                self.root, "../outside.txt", "x",
                allowed_paths=["../outside.txt"], recorder=recorder)

    def test_write_rejects_sensitive_binary_and_oversized_content(self):
        trace = self.root / "write.jsonl"
        recorder = TraceRecorder(trace, "run_tools")
        for path, content in ((".env", "x"), ("image.bin", "x"),
                              ("new.txt", "x" * (16 * 1024 + 1))):
            with self.subTest(path=path):
                with self.assertRaises(ToolError):
                    execute_write_file(
                        self.root, path, content, allowed_paths=[path],
                        recorder=recorder)

    def test_write_records_permission_and_tool_events(self):
        trace = self.root / "write.jsonl"
        recorder = TraceRecorder(trace, "run_tools")
        execute_write_file(self.root, "event.txt", "ok",
                           allowed_paths=["event.txt"], recorder=recorder)
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(["permission.requested", "permission.resolved",
                          "tool.call.started", "tool.call.completed"],
                         [event["type"] for event in events])

    def test_large_file_is_explicitly_truncated(self):
        (self.root / "large.txt").write_text("abcdef", encoding="utf-8")
        result, events = self.execute(
            "large.txt", allowed=("large.txt",), max_bytes=3)
        self.assertEqual("abc", result["content"])
        self.assertTrue(result["truncated"])
        self.assertEqual(6, events[-1]["payload"]["result_bytes"])
        self.assertTrue(events[-1]["payload"]["result_truncated"])


if __name__ == "__main__":
    unittest.main()
