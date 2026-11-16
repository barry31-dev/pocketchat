#!/usr/bin/env python3
"""Trusted-only guard diagnostics. Never parses documents or grants permission."""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

SOURCES = {
    'network': '''import json,socket
results=[]
for family,kind in ((socket.AF_INET,socket.SOCK_STREAM),(socket.AF_INET,socket.SOCK_DGRAM),(socket.AF_INET6,socket.SOCK_STREAM)):
 s=socket.socket(family,kind)
 try:
  s.bind(('::1',0) if family==socket.AF_INET6 else ('127.0.0.1',0))
  results.append('allowed')
 except PermissionError:results.append('denied')
 except OSError:results.append('unavailable')
 finally:s.close()
print(json.dumps({'bindings':results}))
''',
    'memory': '''import json
try:
 import resource
 resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456))
 result={'status':'installed','readback':list(resource.getrlimit(resource.RLIMIT_AS))}
except ImportError:result={'status':'unsupported','reason':'resource_module_unavailable'}
except (ValueError,OSError):result={'status':'unsupported','reason':'address_space_limit_install_failed'}
print(json.dumps(result))
''',
    'wait': 'import time; time.sleep(30)',
    'output': 'import sys; sys.stdout.buffer.write(b"x"*65536); sys.stdout.buffer.flush()',
}


def worker_command(kind):
    if kind not in SOURCES:
        raise ValueError('Only fixed trusted diagnostic cases are allowed.')
    return [sys.executable, '-I', '-c', SOURCES[kind]]


def observe(kind, *, timeout=3.0, maximum=4096, sandbox=False):
    if not 0 < timeout <= 5 or not 0 < maximum <= 16384:
        raise ValueError('Diagnostic budget is outside its fixed bounds.')
    command = worker_command(kind)
    if sandbox:
        if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
            return dict(status='unsupported', reason='os_network_guard_unavailable')
        command = ['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)'] + command
    env = {key: os.environ[key] for key in ('SystemRoot', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP') if key in os.environ}
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    except OSError:
        return dict(status='unsupported', reason='trusted_worker_launch_failed')
    output, errors = bytearray(), bytearray()
    lock, overflow = threading.Lock(), threading.Event()
    read_failed = threading.Event()

    def drain(stream, target):
        try:
            while True:
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    break
                with lock:
                    available = maximum - len(output) - len(errors)
                    target.extend(chunk[:available])
                    if len(chunk) > available:
                        overflow.set()
        except OSError:
            read_failed.set()

    readers, started_readers = [], []
    status = 'completed'
    try:
        readers = [threading.Thread(target=drain, args=(child.stdout, output)),
                   threading.Thread(target=drain, args=(child.stderr, errors))]
        for thread in readers:
            thread.start()
            started_readers.append(thread)
        deadline = time.monotonic() + timeout
        while True:
            if overflow.is_set():
                status = 'output_limit'
                break
            if child.poll() is not None and not any(t.is_alive() for t in readers):
                break
            if time.monotonic() >= deadline:
                status = 'timeout'
                break
            time.sleep(0.01)
    except (RuntimeError, MemoryError):
        status = 'capture_failed'
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)
        for thread in started_readers:
            thread.join(timeout=2)
        child.stdout.close()
        child.stderr.close()
    if overflow.is_set():
        status = 'output_limit'
    if status == 'capture_failed' or read_failed.is_set() or any(t.is_alive() for t in started_readers):
        return dict(status='capture_failed', child_reaped=True)
    result = dict(status=status, exit=child.returncode, child_reaped=True,
                  retained_bytes=len(output) + len(errors))
    if status == 'completed':
        if errors or child.returncode != 0:
            result['status'] = 'worker_failed'
        else:
            try:
                result['value'] = json.loads(output)
            except (ValueError, UnicodeError):
                result['status'] = 'invalid_output'
    return result


def probe():
    network_baseline = observe('network')
    network_guard = observe('network', sandbox=True)
    baseline = network_baseline.get('value', {}).get('bindings')
    guarded = network_guard.get('value', {}).get('bindings')
    network_proven = baseline == ['allowed'] * 3 and guarded == ['denied'] * 3
    memory = observe('memory')
    wall = observe('wait', timeout=0.1)
    output = observe('output', maximum=1024)
    blockers = ['memory_enforcement_not_proven', 'arbitrary_parser_isolation_not_proven']
    if not network_proven:
        blockers.append('os_network_denial_not_proven')
    if wall.get('status') != 'timeout' or not wall.get('child_reaped'):
        blockers.append('owned_timeout_cleanup_not_proven')
    if output.get('status') != 'output_limit' or not output.get('child_reaped'):
        blockers.append('bounded_output_cleanup_not_proven')
    return dict(schema_version='pocketchat-document-guard-probe.v1',
                python=platform.python_version(), platform=sys.platform,
                scope='fixed trusted diagnostic children only; no document parser or user input',
                parser_ready=False, blockers=blockers,
                network=dict(baseline=network_baseline, guarded=network_guard,
                             three_loopback_bindings_denied=network_proven),
                memory=memory, wall=wall, output=output)


def main():
    if len(sys.argv) != 1:
        print('This diagnostic accepts no input, code, path or profile arguments.', file=sys.stderr)
        return 2
    print(json.dumps(probe(), sort_keys=True))
    return 0  # A completed diagnostic is not a parser-readiness success.


if __name__ == '__main__':
    raise SystemExit(main())
