"""The same real-file fixture is runnable against source or an installed wheel."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pocketchat.file_change_store import FileChangeStoreError, require_secure_runtime

try:
    require_secure_runtime()
    SECURE_RUNTIME = os.link in os.supports_dir_fd
except FileChangeStoreError:
    SECURE_RUNTIME = False


@unittest.skipUnless(SECURE_RUNTIME, 'secure file runtime unavailable')
class FileWorkGoldenTests(unittest.TestCase):
    def fixture(self, *extra):
        with tempfile.TemporaryDirectory() as parent:
            root = Path(__file__).resolve().parents[1]
            env = dict(os.environ, PYTHONPATH=str(root))
            result = subprocess.run(
                [sys.executable, '-B', str(root / 'examples/file-work-demo.py'),
                 '--workspace', str(Path(parent) / 'workspace'), *extra],
                env=env, capture_output=True, text=True, timeout=30)
            self.assertTrue(result.stdout, result.stderr)
            return result, json.loads(result.stdout)

    def test_golden_path_passes_with_real_files_and_restarted_session(self):
        result, evidence = self.fixture()
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual('passed', evidence['acceptance'])
        self.assertTrue(all(evidence['assertions'].values()))
        self.assertGreaterEqual(len(evidence['changes']), 4)
        self.assertEqual('offline-stub', evidence['provider_authority'])
        self.assertIn('cancelled', evidence['terminal_statuses'])
        self.assertIn('completed', evidence['terminal_statuses'])
        self.assertIn('undone', [row['state'] for row in evidence['changes']])

    def test_wrong_output_fails_acceptance_not_a_false_pass(self):
        result, evidence = self.fixture('--wrong-output')
        self.assertEqual(1, result.returncode, result.stderr + result.stdout)
        self.assertEqual('failed', evidence['acceptance'])
        self.assertFalse(evidence['assertions']['replacement_readback'])

    def test_dirty_git_baseline_is_preserved_after_apply_and_undo(self):
        result, evidence = self.fixture('--baseline', 'dirty-git')
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertEqual('dirty-git', evidence['baseline'])
        self.assertTrue(evidence['assertions']['baseline_restored'])


if __name__ == '__main__':
    unittest.main()
