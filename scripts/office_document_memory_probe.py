#!/usr/bin/env python3
"""Compile and observe fixed trusted probes, never arbitrary document/code input."""
from __future__ import annotations

import errno
import json
from pathlib import Path
import subprocess
import sys
import tempfile

SCHEMA = 'pocketchat-native-memory-probe.v1'
FIELDS = {'case', 'installed', 'install_errno', 'mapped', 'allocation_errno',
          'ceiling_bytes', 'allocation_bytes', 'rss_aliases_as'}


def valid(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        return False
    if value['case'] not in ('baseline', 'as', 'data'):
        return False
    if any(type(value[key]) is not bool for key in ('installed', 'mapped', 'rss_aliases_as')):
        return False
    if any(type(value[key]) is not int or not 0 <= value[key] <= 4095
           for key in ('install_errno', 'allocation_errno')):
        return False
    if type(value['ceiling_bytes']) is not int or value['ceiling_bytes'] != 33554432:
        return False
    if type(value['allocation_bytes']) is not int or value['allocation_bytes'] != 50331648:
        return False
    if value['mapped'] != (value['allocation_errno'] == 0):
        return False
    if value['case'] == 'baseline':
        return not value['installed'] and value['install_errno'] == 0
    return value['installed'] == (value['install_errno'] == 0)


def classify(baseline, guarded):
    """Mapping refusal is not a proof of production resident-memory enforcement."""
    if (not valid(baseline) or not valid(guarded) or baseline['case'] != 'baseline'
            or guarded['case'] == 'baseline' or not baseline['mapped']):
        return 'inconclusive'
    if not guarded['installed']:
        return 'unsupported'
    if guarded['mapped']:
        return 'not_enforced'
    if guarded['allocation_errno'] == errno.ENOMEM:
        return 'mapping_refused'
    return 'inconclusive'


def compiler_command():
    # No PATH search, environment-selected compiler, or caller-supplied program.
    if sys.platform == 'darwin' and Path('/usr/bin/xcrun').is_file():
        return ['/usr/bin/xcrun', 'clang']
    if sys.platform.startswith('linux') and Path('/usr/bin/cc').is_file():
        return ['/usr/bin/cc']
    return None


def run_fixed(executable, case):
    if case not in ('baseline', 'as', 'data'):
        raise ValueError('Only fixed native diagnostic cases are allowed.')
    try:
        proc = subprocess.run([str(executable), case], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env={}, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for its own child on timeout.
        return {'status': 'timeout'}
    except OSError:
        return {'status': 'launch_failed'}
    if proc.returncode != 0:
        return {'status': 'worker_failed'}
    # This is the fixed program's envelope, not a hostile-worker output guard.
    if len(proc.stdout) > 1024:
        return {'status': 'invalid_output'}
    try:
        value = json.loads(proc.stdout)
    except (ValueError, UnicodeError):
        return {'status': 'invalid_output'}
    if not valid(value) or value['case'] != case:
        return {'status': 'invalid_output'}
    return {'status': 'completed', 'value': value}


def probe():
    report = dict(schema_version=SCHEMA, parser_ready=False,
                  blockers=['production_peak_memory_not_proven', 'parser_not_enabled'])
    compiler = compiler_command()
    if compiler is None:
        return dict(report, status='unsupported', reason='native_compiler_unavailable')
    source = Path(__file__).with_suffix('.c')
    try:
        with tempfile.TemporaryDirectory(prefix='pocketchat-memory-probe-') as folder:
            executable = Path(folder)/'fixed-probe'
            built = subprocess.run(compiler+['-std=c11', '-D_DEFAULT_SOURCE', '-Wall',
                                   '-Wextra', '-Werror', str(source), '-o', str(executable)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={'PATH': '/usr/bin:/bin'}, timeout=15, check=False)
            if built.returncode != 0:
                return dict(report, status='unsupported', reason='native_build_failed')
            observations = {case: run_fixed(executable, case) for case in ('baseline', 'as', 'data')}
    except subprocess.TimeoutExpired:
        return dict(report, status='inconclusive', reason='native_build_timeout')
    except OSError:
        return dict(report, status='unsupported', reason='native_build_failed')
    baseline = observations['baseline'].get('value')
    if baseline is None:
        return dict(report, status='inconclusive', reason='baseline_failed', observations=observations)
    guards = {case: dict(observation=observations[case],
                        classification=classify(baseline, observations[case].get('value')))
              for case in ('as', 'data')}
    return dict(report, status='completed', baseline=baseline, guards=guards,
                scope='Fixed 32 MiB limit / 48 MiB mmap; one byte touched; no resident peak claim.')


if __name__ == '__main__':
    if len(sys.argv) != 1:
        print('This trusted diagnostic accepts no arguments.', file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(probe(), sort_keys=True))
