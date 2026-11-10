#!/usr/bin/env python3
"""Reusable offline golden path; import the selected installed/source product.

No provider traffic, credentials, real user files or source-tree path injection.
The trusted controller exercises the same service as both terminal renderers;
this is not a manual curses usability or real-provider acceptance claim.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tempfile
from unittest import mock

from pocketchat.config import profile_config

from pocketchat import __version__
from pocketchat.agent import AgentState
from pocketchat.provider import build_payload
from pocketchat.tui import AgentTUI


class FixtureFailure(ValueError):
    pass


class OfflineProvider:
    name = 'file-work-golden-stub'

    def __init__(self):
        self.responses = []
        self.requests = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None, prepared_payload=None):
        self.requests.append(copy.deepcopy(messages))
        if not self.responses:
            raise FixtureFailure('fixture has no scheduled provider response')
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def response(content='fixture answer', *, tool=None, arguments=None, call_id='call'):
    result = {'content': content, 'finish_reason': 'stop', 'model': 'fixture-model',
              'usage': {'input_tokens': 3, 'output_tokens': 2, 'cache_read_tokens': 0,
                        'cache_write_tokens': 0, 'reasoning_tokens': 0},
              'usage_reported': True}
    if tool:
        result.update(content='', finish_reason='tool_calls', tool_calls=[
            {'id': call_id, 'name': tool, 'arguments': arguments}])
    return result


def sha(data):
    return hashlib.sha256(data).hexdigest()


def golden_path(workspace: Path, *, baseline='non-git', wrong_output=False, source_sha=None):
    workspace = workspace.resolve()
    if workspace.exists():
        raise FixtureFailure('fixture workspace must be absent')
    workspace.mkdir(parents=True)
    facts = workspace / 'facts.txt'
    initial, updated = b'local facts\n', b'revised local facts\n'
    facts.write_bytes(initial)
    if baseline == 'dirty-git':
        # Only this newly created fixture directory is initialized, never the product.
        def git(*args):
            subprocess.run(['git', '-C', str(workspace), *args], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git('init', '-q')
        git('add', 'facts.txt')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'fixture baseline')
        initial = b'pre-existing local edit\n'
        facts.write_bytes(initial)
    adapter = OfflineProvider()
    config = {'schema_version': '2', 'default_profile': 'fixture', 'profiles': {
        'fixture': {'model': 'fixture-model', 'base_url': 'http://example.invalid/v1',
                    'api_key_env': '', 'requires_api_key': False,
                    'max_context_chars': 40000,
                    'tool_call_max_count': 4, 'tool_result_max_bytes': 4096}}}
    evidence = {'schema_version': 'pocketchat-file-work-validation.v1',
                'source_type': 'local run (V)', 'verification': 'verified',
                'acceptance': 'not_evaluated', 'provider_authority': 'offline-stub',
                'version': __version__, 'source_sha': source_sha,
                'os': platform.system(), 'python': platform.python_version(),
                'baseline': baseline, 'assertions': {}, 'changes': [],
                'session': 'sessions/golden.jsonl', 'run_ids': [],
                'terminal_statuses': [],
                'not_run': ['real provider', 'manual curses usability', 'formal release']}
    controllers = []
    # Inject only the offline provider into validated per-run configuration.
    # No file transaction, persistence, grant or undo function is mocked.
    provider_patch = mock.patch(
        'pocketchat.tui.profile_config',
        side_effect=lambda root, name: dict(profile_config(root, name), provider_adapter=adapter))
    provider_patch.start()

    def check(name, condition):
        evidence['assertions'][name] = bool(condition)
        if not condition:
            raise FixtureFailure(name)

    def controller():
        c = AgentTUI(AgentState(profile='fixture', model='fixture-model', session='golden'),
                     config['profiles'], {'fixture': 'fixture-model'}, config=config,
                     workspace=workspace, trusted_input=True)
        controllers.append(c)
        return c

    def command(c, line):
        result = c.run_command(line)
        if result.startswith('agent:'):
            raise FixtureFailure(result)
        return result

    def confirm(c, preview):
        digest = preview.rsplit('/confirm ', 1)[1].strip()
        return command(c, '/confirm ' + digest)

    def grant(c, access, path):
        preview = command(c, '/files ' + access + ' ' + path)
        check('provider_disclosed', 'sent to provider' in preview)
        confirm(c, preview)

    def turn(c, *, tool=None, path=None, content=None, cancel=False):
        args = {'path': path} if tool else None
        if content is not None:
            args['content'] = content
        adapter.responses.append(response(tool=tool, arguments=args,
                                          call_id='call_%d' % len(adapter.requests)))
        if tool:
            adapter.responses.append(KeyboardInterrupt() if cancel else response())
        reply = c.send('fixture task')
        if not cancel and (c.last_error or reply.startswith('agent:')):
            raise FixtureFailure(c.last_error or reply)
        return c._session.file_work if c._session else None

    try:
        c = controller()
        turn(c)
        turn(c)
        check('ordinary_chat', len(adapter.requests) == 2)
        grant(c, 'read', 'facts.txt')
        grant(c, 'write', 'facts.txt')
        turn(c, tool='read_file', path='facts.txt')
        read_result = [m for m in adapter.requests[-1] if m['role'] == 'tool'][-1]
        check('authorized_read_paired', initial.decode() in json.loads(read_result['content'])['content'])
        work = turn(c, tool='propose_file_change', path='facts.txt', content='rejected\n')
        rejected_id = work.state()['pending']['change']['change_id']
        preview = command(c, '/apply')
        command(c, '/files cancel')
        check('rejected_without_effect', facts.read_bytes() == initial and
              c.run_command('/confirm ' + preview.rsplit('/confirm ', 1)[1].strip()).startswith('agent:'))
        grant(c, 'read', 'facts.txt')
        grant(c, 'write', 'facts.txt')
        work = turn(c, tool='propose_file_change', path='facts.txt', content=updated.decode())
        replace_id = work.state()['pending']['change']['change_id']
        preview = command(c, '/apply')
        check('complete_diff_before_apply', '-'+initial.decode().strip() in preview and
              '+revised local facts' in preview and facts.read_bytes() == initial)
        confirm(c, preview)
        if wrong_output:
            facts.write_bytes(b'deliberately wrong fixture output\n')
        check('replacement_readback', facts.read_bytes() == updated)
        grant(c, 'write', 'created.txt')
        work = turn(c, tool='propose_file_change', path='created.txt', content='created report\n')
        create_id = work.state()['pending']['change']['change_id']
        confirm(c, command(c, '/apply'))
        check('creation_readback', (workspace / 'created.txt').read_bytes() == b'created report\n')
        grant(c, 'write', 'cancelled.txt')
        work = turn(c, tool='propose_file_change', path='cancelled.txt', content='never applied\n', cancel=True)
        cancelled_id = next(identifier for identifier in work.store.list_change_ids()
                            if work.store.load_record(identifier)['path'] == 'cancelled.txt')
        check('cancellation_preserves_applied', facts.read_bytes() == updated and
              not (workspace / 'cancelled.txt').exists() and work.state()['pending'] is None and
              work.state()['write_paths'] == [])
        evidence['run_ids'].append(c._session.run_id)
        events = [json.loads(line) for line in c._session.trace_path.read_text().splitlines()]
        terminal_names = {'run.finished': 'completed', 'run.cancelled': 'cancelled',
                          'run.failed': 'failed'}
        evidence['terminal_statuses'].extend(terminal_names[e['type']] for e in events
                                           if e['type'] in terminal_names)
        c.close()
        restarted = controller()
        command(restarted, '/files')
        check('restart_requires_grants', restarted._session.file_work.state()['write_paths'] == [] and
              restarted.run_command('/undo ' + replace_id).startswith('agent:'))
        grant(restarted, 'write', 'created.txt')
        confirm(restarted, command(restarted, '/undo ' + create_id))
        grant(restarted, 'write', 'facts.txt')
        confirm(restarted, command(restarted, '/undo ' + replace_id))
        check('baseline_restored', facts.read_bytes() == initial and
              not (workspace / 'created.txt').exists())
        grant(restarted, 'read', 'facts.txt')
        work = turn(restarted, tool='propose_file_change', path='facts.txt', content='stale proposal\n')
        conflict_id = work.state()['pending']['change']['change_id']
        preview = command(restarted, '/apply')
        facts.write_bytes(b'concurrent external edit\n')
        result = restarted.run_command('/confirm ' + preview.rsplit('/confirm ', 1)[1].strip())
        check('conflict_refused', result.startswith('agent:') and
              facts.read_bytes() == b'concurrent external edit\n')
        # The external edit is intentionally retained; undo may not overwrite it.
        evidence['run_ids'].append(restarted._session.run_id)
        for identifier in work.store.list_change_ids():
            record = work.store.load_record(identifier)
            evidence['changes'].append({'change_id': identifier, 'path': record['path'],
                                        'state': record['events'][-1]['type'],
                                        'after_sha256': record['after']['sha256'],
                                        'proposal_sha256': record['proposal_sha256']})
        check('durable_change_identity', {rejected_id, replace_id, create_id, conflict_id, cancelled_id}
              <= {r['change_id'] for r in evidence['changes']})
        evidence['file_hashes'] = {'facts.txt': sha(facts.read_bytes()),
                                  'restored_baseline_sha256': sha(initial),
                                  'confirmed_replacement_sha256': sha(updated)}
        evidence['provider_requests'] = len(adapter.requests)
        evidence['acceptance'] = 'passed'
    except FixtureFailure as exc:
        evidence['acceptance'] = 'failed'
        evidence['failure'] = str(exc)
    finally:
        for c in controllers:
            c.close()
        provider_patch.stop()
    (workspace / 'file-work-evidence.json').write_text(
        json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--source-sha')
    parser.add_argument('--baseline', choices=['non-git', 'dirty-git'], default='non-git')
    parser.add_argument('--wrong-output', action='store_true')
    args = parser.parse_args(argv)
    if args.source_sha is not None and (len(args.source_sha) != 40 or
            any(c not in '0123456789abcdef' for c in args.source_sha)):
        parser.error('--source-sha requires 40 lowercase hexadecimal characters')
    with tempfile.TemporaryDirectory(prefix='file-work-demo-') as parent:
        try:
            result = golden_path(args.workspace or Path(parent) / 'workspace',
                                 baseline=args.baseline, wrong_output=args.wrong_output,
                                 source_sha=args.source_sha)
        except (FixtureFailure, OSError, ValueError) as exc:
            print(json.dumps({'acceptance': 'refused', 'reason': str(exc)}))
            return 2
        print(json.dumps(result, sort_keys=True))
        return 0 if result['acceptance'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
