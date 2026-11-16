"""Trusted diagnostic bounds do not constitute arbitrary parser permission."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.office_document_guard_probe import observe, probe, worker_command

ROOT = Path(__file__).resolve().parents[1]


class DocumentGuardProbeTests(unittest.TestCase):
    def test_no_caller_code_or_path_and_no_false_readiness(self):
        with self.assertRaises(ValueError):
            worker_command('user-supplied-code')
        with patch('scripts.office_document_guard_probe.observe', return_value={
                'status': 'completed', 'exit': 0, 'value': {'status': 'installed'}}):
            report = probe()
        self.assertFalse(report['parser_ready'])
        self.assertIn('memory_enforcement_not_proven', report['blockers'])

    def test_owned_timeout_is_observed_not_success(self):
        result = observe('wait', timeout=0.1)
        self.assertEqual(result['status'], 'timeout')
        self.assertTrue(result['child_reaped'])
        self.assertNotIn('value', result)

    def test_output_limit_is_bounded_and_owned_child_reaped(self):
        result = observe('output', maximum=1024)
        self.assertEqual(result['status'], 'output_limit')
        self.assertTrue(result['child_reaped'])
        self.assertLessEqual(result['retained_bytes'], 1024)
        self.assertNotIn('value', result)

    def test_actual_cli_one_json_no_files_or_claimed_readiness(self):
        from tests._helpers import subprocess_environment
        proc = subprocess.run([sys.executable, '-I', str(ROOT/'scripts/office_document_guard_probe.py')],
                              capture_output=True, text=True, timeout=12,
                              env=subprocess_environment())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        report = json.loads(proc.stdout)
        self.assertEqual(report['schema_version'], 'pocketchat-document-guard-probe.v1')
        self.assertFalse(report['parser_ready'])
        self.assertIn('memory_enforcement_not_proven', report['blockers'])
        self.assertEqual(report['wall']['status'], 'timeout')
        self.assertEqual(report['output']['status'], 'output_limit')
        self.assertNotIn(str(ROOT), proc.stdout)

    def test_reader_start_failure_kills_and_reaps_owned_child(self):
        import threading
        original_start = threading.Thread.start
        original_popen = subprocess.Popen
        for failed_index in (0, 1):
            children=[]; count=[0]
            def tracked(*args,**kwargs):
                child=original_popen(*args,**kwargs);children.append(child);return child
            def start(thread):
                i=count[0];count[0]+=1
                if i==failed_index:raise RuntimeError('thread resources unavailable')
                return original_start(thread)
            try:
                with patch('scripts.office_document_guard_probe.subprocess.Popen',side_effect=tracked), \
                     patch.object(threading.Thread,'start',start):
                    result=observe('wait',timeout=0.1)
                self.assertEqual(result['status'],'capture_failed')
                self.assertTrue(result['child_reaped'])
                self.assertIsNotNone(children[0].poll())
                self.assertTrue(children[0].stdout.closed)
                self.assertTrue(children[0].stderr.closed)
            finally:
                for child in children:
                    if child.poll() is None:child.kill()
                    child.wait()
                    child.stdout.close();child.stderr.close()
