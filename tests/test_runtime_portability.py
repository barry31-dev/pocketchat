"""Portable evidence references and the isolated Windows runner contract."""
from __future__ import annotations

import hashlib
import ntpath
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch

from pocketchat import (acceptance, cli, compare, queries, recovery, service,
                        sessions, task_recovery, workspace)
from tests import _helpers as hp


class _WindowsPath(PureWindowsPath):
    def resolve(self):
        return self


class RuntimePortabilityTests(unittest.TestCase):
    def test_recorded_relative_paths_use_the_json_separator_on_windows(self):
        source = _WindowsPath('C:/work/sessions/notes.jsonl')
        root = _WindowsPath('C:/work')
        for module, helper in ((compare, compare._relative),
                               (queries, queries._relative),
                               (recovery, recovery._relative),
                               (service, service._relative),
                               (sessions, sessions._relative_to),
                               (workspace, workspace._relative)):
            with self.subTest(module=module.__name__), \
                    patch.object(module, 'Path', _WindowsPath), \
                    patch.object(module.os.path, 'relpath', ntpath.relpath):
                self.assertEqual(helper(source, root), 'sessions/notes.jsonl')

    def test_cross_drive_json_path_keeps_the_actual_absolute_reference(self):
        with patch.object(cli, 'Path', PureWindowsPath), \
                patch.object(PureWindowsPath, 'cwd', return_value=PureWindowsPath('D:/work'), create=True), \
                patch.object(cli.os.path, 'relpath', ntpath.relpath):
            self.assertEqual(cli._json_path(PureWindowsPath('C:/evidence/trace.jsonl')),
                             'C:/evidence/trace.jsonl')

    def test_unsupported_safe_reader_is_an_explicit_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "report.md").write_bytes(b"report")
            plan = acceptance.parse_plan({
                "schema_version": "pocketchat-acceptance-plan.v1",
                "checks": [{"id": "report", "kind": "file_exists",
                            "path": "report.md"}],
            })
            with patch.object(acceptance.os, "supports_dir_fd", set()):
                result = acceptance.evaluate_plan(root, plan)
        self.assertEqual(result["state"], "refused")
        self.assertEqual(result["checks"][0]["reason"],
                         "unsupported_platform")

    def test_recovery_refuses_unsupported_reader_before_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            report = root / ".pocketchat" / "task-reports" / "execution.json"
            report.parent.mkdir(parents=True)
            data = b"{}"
            report.write_bytes(data)
            trace_dir = root / "new-traces"
            session_dir = root / "new-sessions"
            with patch.object(acceptance.os, "supports_dir_fd", set()), \
                    self.assertRaisesRegex(
                        task_recovery.TaskRecoveryError,
                        "unsupported_platform"):
                task_recovery.prepare_recovery(
                    object(), root, report, hashlib.sha256(data).hexdigest(),
                    trace_dir=trace_dir, session_dir=session_dir)
            self.assertEqual(report.read_bytes(), data)
            self.assertFalse(trace_dir.exists())
            self.assertFalse(session_dir.exists())

    def test_isolated_subprocess_keeps_windows_bootstrap_not_credentials(self):
        with patch.object(hp.os, 'name', 'nt'), patch.dict(hp.os.environ, {
            'SystemRoot': 'C:/Windows', 'WINDIR': 'C:/Windows',
            'POCKETCHAT_API_KEY': 'must-not-be-inherited', 'SECRET': 'private',
        }, clear=True):
            env = hp.subprocess_environment({'PYTHONPATH': '/fixture'})
        self.assertEqual(env['SystemRoot'], 'C:/Windows')
        self.assertEqual(env['PYTHONPATH'], '/fixture')
        self.assertNotIn('POCKETCHAT_API_KEY', env)
        self.assertNotIn('SECRET', env)


if __name__ == '__main__':
    unittest.main()
