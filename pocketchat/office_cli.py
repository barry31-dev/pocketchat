"""Explicit office commands; content-bound sending, never the historical task agent."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import ConfigError, load_config, profile_config
from .office_backup import read_file, restore, write_new
from .office_service import OfficeConnection
from .office_store import MAX_SOURCE_BYTES, OfficeError, identifier


def add_parser(subparsers):
    office = subparsers.add_parser('office', help='work with a shared private office workspace')
    office.add_argument('--data-dir', type=Path, required=True, help='private office workspace')
    office.add_argument('--json', dest='as_json', action='store_true', help='one versioned JSON result')
    office.add_argument('--config', type=Path, help='provider configuration for an offline writer')
    office.add_argument('--profile', help='existing provider profile for an offline writer')
    commands = office.add_subparsers(dest='office_command', required=True)
    backup = commands.add_parser('backup', help='save a validated data-only workspace snapshot')
    backup.add_argument('--out', type=Path, required=True, help='new backup JSON file; never overwritten')
    recover = commands.add_parser('restore', help='validate and restore a backup to a new workspace')
    recover.add_argument('--from', dest='source', type=Path, required=True, help='backup JSON file')
    conversations = commands.add_parser('conversations', help='list, create, read or rename conversations')
    actions = conversations.add_subparsers(dest='action', required=True)
    actions.add_parser('list')
    new = actions.add_parser('create')
    new.add_argument('--title', required=True)
    for name in ('show', 'rename'):
        action = actions.add_parser(name)
        action.add_argument('--conversation', required=True)
        if name == 'rename':
            action.add_argument('--title', required=True)
    materials = commands.add_parser('materials', help='copy selected plain text into the office workspace')
    actions = materials.add_subparsers(dest='action', required=True)
    add = actions.add_parser('add')
    add.add_argument('--conversation', required=True)
    add.add_argument('--file', type=Path, required=True)
    for name in ('chat', 'report'):
        command = commands.add_parser(name, help='preview a request, then send with its exact confirmation hash')
        if name == 'report':
            actions = command.add_subparsers(dest='action', required=True)
            targets = [actions.add_parser('create'), actions.add_parser('revise')]
            targets[1].add_argument('--version', required=True, help='immutable predecessor version')
        else:
            targets = [command]
        for target in targets:
            target.add_argument('--conversation', required=True)
            target.add_argument('--prompt', required=True)
            target.add_argument('--source', action='append', default=[], help='material ID; repeat for each selection')
            target.add_argument('--request-id', required=True, help='stable ID; reuse only to recover this same request')
            target.add_argument('--confirm', help='SHA-256 from the preview; no generic --yes')
    runs = commands.add_parser('runs', help='inspect a receipt or stop its running request')
    actions = runs.add_subparsers(dest='action', required=True)
    for name in ('show', 'stop'):
        action = actions.add_parser(name)
        action.add_argument('--run', required=True)
    export = commands.add_parser('export', help='write an exact report version to a new file')
    export.add_argument('--conversation', required=True)
    export.add_argument('--version', required=True)
    export.add_argument('--format', choices=('md', 'docx'), required=True)
    export.add_argument('--out', type=Path, required=True)


def _operate(args, client):
    command = args.office_command
    prefix = '/api/conversations'
    if getattr(args, 'conversation', None):
        prefix += '/' + identifier(args.conversation)
    if command == 'backup':
        return write_new(args.out, client.request('/api/backup', {})), 0
    if command == 'conversations':
        if args.action in ('list', 'show'):
            return client.request(prefix), 0
        if args.action == 'create':
            return client.request(prefix, {'title': args.title}), 0
        return client.request(prefix + '/rename', {'title': args.title}), 0
    if command == 'materials':
        try:
            text = read_file(args.file, MAX_SOURCE_BYTES).decode('utf-8')
        except UnicodeError as exc:
            raise OfficeError('Material must be valid UTF-8 plain text.') from exc
        return client.request(prefix + '/sources', dict(name=args.file.name, text=text)), 0
    if command == 'export':
        if args.out.suffix.lower() != '.' + args.format:
            raise OfficeError('Output extension must match the selected format.')
        version = identifier(args.version)
        data = client.request(prefix + '/artifacts/' + version + '/' + args.format)
        return dict(write_new(args.out, data), version=version, format=args.format), 0
    if command == 'runs':
        path = '/api/runs/' + identifier(args.run)
        return client.request(path + '/stop', {}) if args.action == 'stop' else client.request(path), 0
    body = dict(prompt=args.prompt, intent='report' if command == 'report' else 'chat',
                source_ids=args.source, revision_id=getattr(args, 'version', None),
                request_id=identifier(args.request_id), consent=True)
    if args.confirm is None:
        return dict(client.request(prefix + '/preview', body), sent=False), 0
    run = client.request(prefix + '/confirmed', dict(message=body, confirmation=args.confirm))
    try:
        while run['status'] in ('running', 'stopping'):
            time.sleep(0.15)
            run = client.request('/api/runs/' + run['id'])
    except KeyboardInterrupt:
        run = client.request('/api/runs/' + run['id'] + '/stop', {})
        return run, 1
    return run, 0 if run['status'] == 'completed' else 1


def run_office(args):
    try:
        if args.office_command == 'restore':
            result, code = restore(read_file(args.source), args.data_dir), 0
        else:
            if not args.data_dir.is_dir() and not (args.office_command == 'conversations' and args.action == 'create'):
                raise OfficeError('The office workspace does not exist. Create a conversation first.')
            config = profile_config(load_config(args.config), args.profile) if args.config else None
            if args.profile and config is None:
                raise OfficeError('--profile requires --config for an offline workspace.')
            with OfficeConnection(args.data_dir, config) as client:
                if client.remote and (args.config or args.profile):
                    raise OfficeError('The running server owns model configuration; omit CLI config/profile overrides.')
                result, code = _operate(args, client)
        response = dict(schema_version='pocketchat-office-cli.v1', ok=code == 0, result=result)
    except OfficeError as exc:
        response = dict(schema_version='pocketchat-office-cli.v1', ok=False, error=str(exc))
        code = 2
    except (OSError, ValueError, ConfigError):
        response = dict(schema_version='pocketchat-office-cli.v1', ok=False,
                        error='Office operation failed. No success is implied.')
        code = 2
    if args.as_json:
        print(json.dumps(response, ensure_ascii=False))
    elif 'result' in response:
        result = response['result']
        if isinstance(result, dict) and result.get('reply'):
            print(result['reply'])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        import sys
        print(response['error'], file=sys.stderr)
    return code
