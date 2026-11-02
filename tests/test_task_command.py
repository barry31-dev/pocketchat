"""Command-template execution: argv only, bounded evidence, trace closure."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from pocketchat.task_command import (CommandError, parse_command_templates,
                                     run_command_template)


def template(**overrides):
    value = {
        "id": "run_tests",
        "argv": ["python3", "-c", "print('ok')"],
        "cwd": ".",
        "timeout_seconds": 5,
        "max_output_bytes": 32,
    }
    value.update(overrides)
    return value


class TemplateParsingTests(unittest.TestCase):
    def test_closed_template_shape_and_ids_are_validated(self):
        parsed = parse_command_templates([template(argv=["python3", " x "])])
        self.assertEqual(" x ", parsed[0]["argv"][1])
        bad_cases = (
            [template(id="bad id")],
            [template(argv=[])],
            [template(argv=[""])],
            [template(argv=["sh", "-c", "echo bad"])],
            [template(argv=["/bin/sh", "-c", "echo bad"])],
            [template(argv=["C:\\Windows\\System32\\cmd.exe", "/c", "echo bad"])],
            [template(cwd="../outside")],
            [template(cwd="/tmp")],
            [template(cwd="-x")],
            [template(timeout_seconds=0)],
            [template(max_output_bytes=0)],
            [template(extra="x")],
            [template(), template()],
        )
        for payload in bad_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(CommandError):
                    parse_command_templates(payload)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trace = self.root / "traces" / "command.jsonl"

    def test_success_records_bounded_evidence_and_closed_trace(self):
        parsed = parse_command_templates([template()])[0]
        result = run_command_template(
            self.root, parsed, trace_path=self.trace, run_id="cmd-success")
        self.assertTrue(result.ok)
        self.assertEqual(0, result.exit_code)
        self.assertIn("ok", result.stdout)
        self.assertEqual("not_evaluated", result.task_acceptance)
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual("run.started", events[0]["type"])
        self.assertEqual("tool.call.started", events[1]["type"])
        self.assertEqual("tool.call.completed", events[2]["type"])
        self.assertEqual("run_tests", events[2]["payload"]["template_id"])
        self.assertEqual(0, events[2]["payload"]["exit_code"])
        self.assertEqual(64, len(events[2]["payload"]["stdout_sha256"]))
        self.assertEqual(64, len(events[2]["payload"]["stderr_sha256"]))
        self.assertFalse(events[2]["payload"]["stdout_truncated"])
        self.assertEqual("run.finished", events[-1]["type"])
        self.assertNotIn("print('ok')", self.trace.read_text())
        archive = json.loads((self.trace.parent / "command-evidence" / "command.json").read_text())
        self.assertEqual("pocketchat-command-evidence.v1", archive["schema_version"])
        self.assertEqual("cmd-success", archive["run_id"])
        self.assertEqual(64, len(archive["stdout_sha256"]))

    def test_nonzero_and_timeout_are_results_with_failed_terminal_trace(self):
        nonzero = parse_command_templates([template(
            argv=["python3", "-c", "import sys; sys.exit(3)"])])[0]
        result = run_command_template(
            self.root, nonzero, trace_path=self.trace, run_id="cmd-nonzero")
        self.assertFalse(result.ok)
        self.assertEqual(3, result.exit_code)
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual("tool.call.failed", events[2]["type"])
        self.assertEqual(3, events[2]["payload"]["exit_code"])
        self.assertEqual(64, len(events[2]["payload"]["stderr_sha256"]))
        self.assertEqual("run.failed", events[-1]["type"])
        timed = parse_command_templates([template(
            argv=["python3", "-c", "import time; time.sleep(2)"],
            timeout_seconds=1)])[0]
        result = run_command_template(
            self.root, timed, trace_path=self.root / "traces" / "timeout.jsonl",
            run_id="cmd-timeout")
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)

    def test_output_is_clipped_with_an_explicit_marker_and_hash(self):
        parsed = parse_command_templates([template(
            argv=["python3", "-c", "print('x' * 200)"],
            max_output_bytes=16)])[0]
        result = run_command_template(
            self.root, parsed, trace_path=self.trace, run_id="cmd-clip")
        self.assertTrue(result.stdout_truncated)
        self.assertEqual(16, len(result.stdout.encode()))
        self.assertTrue(result.stdout.endswith("[truncated]"))
        self.assertEqual(64, len(result.stdout_sha256))
        self.assertGreater(result.stdout_bytes, 16)

    def test_shell_metacharacters_are_not_expanded_and_secret_env_is_not_inherited(self):
        parsed = parse_command_templates([template(
            argv=["python3", "-c", "import os; print('$HOME', os.environ.get('POCKETCHAT_SECRET', 'missing'))"],
            max_output_bytes=128)])[0]
        old = os.environ.get("POCKETCHAT_SECRET")
        os.environ["POCKETCHAT_SECRET"] = "should-not-leak"
        try:
            result = run_command_template(
                self.root, parsed, trace_path=self.trace, run_id="cmd-env")
        finally:
            if old is None:
                os.environ.pop("POCKETCHAT_SECRET", None)
            else:
                os.environ["POCKETCHAT_SECRET"] = old
        self.assertIn("$HOME", result.stdout)
        self.assertIn("missing", result.stdout)
        self.assertNotIn("should-not-leak", result.stdout)
        self.assertNotIn("should-not-leak", self.trace.read_text())

    def test_bounded_archive_refuses_the_configured_credential(self):
        parsed = parse_command_templates([template(
            argv=["python3", "-c", "print('secret-value')"],
            max_output_bytes=64)])[0]
        with self.assertRaises(CommandError):
            run_command_template(
                self.root, parsed, trace_path=self.trace,
                run_id="cmd-secret", credential="secret-value")
        self.assertFalse(
            (self.trace.parent / "command-evidence" / "command.json").exists())

    def test_cwd_escape_and_unknown_template_are_refused(self):
        parsed = parse_command_templates([template()])[0]
        with self.assertRaises(CommandError):
            run_command_template(
                self.root, parsed, cwd="../outside",
                trace_path=self.trace, run_id="cmd-cwd")
        with self.assertRaises(CommandError):
            run_command_template(
                self.root, None, trace_path=self.trace, run_id="cmd-id")


if __name__ == "__main__":
    unittest.main()
