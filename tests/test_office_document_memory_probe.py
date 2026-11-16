"""Fixed-native evidence never enables arbitrary document parsing."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.office_document_memory_probe import classify, probe, run_fixed

ROOT = Path(__file__).resolve().parents[1]


def result(case, *, installed=False, install_errno=0, mapped=True, allocation_errno=0):
    return dict(case=case, installed=installed, install_errno=install_errno,
                mapped=mapped, allocation_errno=allocation_errno,
                ceiling_bytes=33554432, allocation_bytes=50331648,
                rss_aliases_as=True)


class NativeMemoryProbeTests(unittest.TestCase):
    def test_installation_is_not_enforcement(self):
        baseline = result('baseline')
        self.assertEqual(classify(baseline, result('as', installed=True)), 'not_enforced')
        self.assertEqual(classify(baseline, result('as', install_errno=22)), 'unsupported')
        self.assertEqual(classify(baseline, result('as', installed=True, mapped=False,
                                                 allocation_errno=12)), 'mapping_refused')
        self.assertEqual(classify(result('baseline', mapped=False, allocation_errno=12),
                                  result('as', installed=True, mapped=False,
                                         allocation_errno=12)), 'inconclusive')

    def test_malformed_or_unrelated_failures_do_not_prove_a_guard(self):
        baseline = result('baseline')
        for bad in ({}, result('as', installed=True, mapped=False, allocation_errno=1),
                    dict(result('as', installed=True, mapped=False, allocation_errno=12),
                         allocation_bytes=True),
                    dict(result('as'), unexpected='value'), result('baseline')):
            self.assertEqual(classify(baseline, bad), 'inconclusive')

    def test_only_fixed_native_cases_can_be_requested(self):
        with self.assertRaises(ValueError):
            run_fixed('/bin/false', 'user-code')

    def test_compiler_absence_is_named_and_not_ready(self):
        with patch('scripts.office_document_memory_probe.compiler_command', return_value=None):
            report = probe()
        self.assertFalse(report['parser_ready'])
        self.assertEqual(report['status'], 'unsupported')
        self.assertEqual(report['reason'], 'native_compiler_unavailable')

    def test_subprocess_failure_remains_content_free(self):
        with patch('scripts.office_document_memory_probe.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('trusted', 5)):
            self.assertEqual(run_fixed('/bin/false', 'baseline'),
                             {'status': 'timeout'})
        with patch('scripts.office_document_memory_probe.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, b'not-json', b'')):
            self.assertEqual(run_fixed('/bin/false', 'baseline'),
                             {'status': 'invalid_output'})

    def test_build_timeout_is_named_inconclusive(self):
        with patch('scripts.office_document_memory_probe.compiler_command', return_value=['/usr/bin/cc']), \
             patch('scripts.office_document_memory_probe.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('trusted-compiler', 15)):
            report = probe()
        self.assertEqual(report['status'], 'inconclusive')
        self.assertEqual(report['reason'], 'native_build_timeout')
        self.assertFalse(report['parser_ready'])

    def assert_report(self, report):
        self.assertEqual(report['schema_version'], 'pocketchat-native-memory-probe.v1')
        self.assertFalse(report['parser_ready'])
        if report['status'] == 'completed':
            self.assertEqual(report['baseline']['case'], 'baseline')
            self.assertEqual(set(report['guards']), {'as', 'data'})
            if not report['baseline']['mapped']:
                self.assertTrue(all(guard['classification'] == 'inconclusive'
                                    for guard in report['guards'].values()))
        elif report['status'] == 'inconclusive':
            self.assertIn(report['reason'], ('baseline_failed', 'native_build_timeout'))
        else:
            self.assertEqual(report['status'], 'unsupported')
            self.assertIn(report['reason'], ('native_compiler_unavailable', 'native_build_failed'))

    def test_unavailable_baseline_is_a_valid_diagnostic(self):
        for baseline in ({'status': 'timeout'},
                         {'status': 'completed', 'value': result('baseline', mapped=False,
                                                                allocation_errno=12)}):
            with patch('scripts.office_document_memory_probe.compiler_command',
                       return_value=['/usr/bin/cc']), \
                 patch('scripts.office_document_memory_probe.subprocess.run',
                       return_value=subprocess.CompletedProcess([], 0, b'', b'')), \
                 patch('scripts.office_document_memory_probe.run_fixed',
                       side_effect=[baseline, {'status': 'launch_failed'}, {'status': 'timeout'}]):
                report = probe()
            self.assert_report(report)

    def test_actual_process_native_report_and_closed_arguments(self):
        from tests._helpers import subprocess_environment
        command = [sys.executable, '-I', str(ROOT/'scripts/office_document_memory_probe.py')]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30,
                              env=subprocess_environment())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, '')
        report = json.loads(proc.stdout)
        self.assert_report(report)
        self.assertLess(len(proc.stdout.encode()), 8192)
        self.assertNotIn(str(ROOT), proc.stdout)
        refused = subprocess.run(command+['--file', 'private.docx'], capture_output=True,
                                 text=True, timeout=5, env=subprocess_environment())
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, '')
