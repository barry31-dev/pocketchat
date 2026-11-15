"""Explicit office commands; content-bound sending, never the historical task agent."""
from __future__ import annotations

import copy
import json
import secrets
import shlex
import sys
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
    commands.add_parser('interactive', help='private draft REPL; sending requires the exact preview hash')
    models = commands.add_parser('models', help='list configured model profiles without calling a provider')
    models.add_argument('--select', help='select an existing configured profile')
    memory = commands.add_parser('memory', help='manage explicit project guidance; never verify external facts')
    actions = memory.add_subparsers(dest='action', required=True)
    actions.add_parser('list')
    recall = actions.add_parser('recall')
    recall.add_argument('--query', required=True)
    for name in ('propose', 'accept', 'revise', 'retire', 'forget'):
        action = actions.add_parser(name)
        action.add_argument('--project-id', required=True)
        action.add_argument('--store-sha256', required=True)
        if name != 'propose':
            action.add_argument('--memory-id', required=True)
            action.add_argument('--revision', type=int, required=True)
        if name in ('propose', 'revise'):
            action.add_argument('--text', required=True)
        if name == 'propose':
            action.add_argument('--category', choices=('user_preference', 'project_decision'), required=True)
        if name in ('propose', 'accept', 'revise'):
            action.add_argument('--personal-guidance', action='store_true', required=True,
                                help='declare personal guidance, not independently verified external facts')
        if name == 'forget':
            action.add_argument('--acknowledge-history', action='store_true', required=True,
                                help='old traces, exports and backups are not erased')
    project = commands.add_parser('project', help='inspect or explicitly save project preferences')
    actions = project.add_subparsers(dest='action', required=True)
    actions.add_parser('show')
    for name in ('instructions', 'memory'):
        action = actions.add_parser(name)
        action.add_argument('--project-id', required=True)
        action.add_argument('--revision', type=int, required=True)
        if name == 'instructions':
            action.add_argument('--text', required=True)
        else:
            action.add_argument('--enabled', choices=('true', 'false'), required=True)
    commands.add_parser('upgrade', help='back up and explicitly upgrade an existing office workspace')
    search = commands.add_parser('search', help='find literal text in message bodies')
    search.add_argument('--query', required=True)
    search.add_argument('--archived', action='store_true')
    backup = commands.add_parser('backup', help='save a validated data-only workspace snapshot')
    backup.add_argument('--out', type=Path, required=True, help='new backup JSON file; never overwritten')
    recover = commands.add_parser('restore', help='validate and restore a backup to a new workspace')
    recover.add_argument('--from', dest='source', type=Path, required=True, help='backup JSON file')
    conversations = commands.add_parser('conversations', help='list, create, read or rename conversations')
    actions = conversations.add_subparsers(dest='action', required=True)
    listing = actions.add_parser('list')
    listing.add_argument('--archived', action='store_true')
    new = actions.add_parser('create')
    new.add_argument('--title', required=True)
    for name in ('show', 'rename', 'archive', 'restore'):
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
            if name == 'chat':
                target.add_argument('--branch-from', help='completed chat run ID to edit or regenerate; keeps saved history')
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
    if command == 'memory':
        if args.action == 'list':
            return client.request('/api/memory'), 0
        if args.action == 'recall':
            return client.request('/api/memory/recall', dict(query=args.query)), 0
        body = dict(project_id=args.project_id, store_sha256=args.store_sha256)
        if args.action != 'propose':
            body.update(memory_id=args.memory_id, revision=args.revision)
        if args.action in ('propose', 'revise'):
            body['content'] = args.text
        if args.action == 'propose':
            body['category'] = args.category
        if args.action in ('propose', 'accept', 'revise'):
            body['personal_guidance'] = args.personal_guidance
        if args.action == 'forget':
            body['acknowledge_history'] = args.acknowledge_history
        return client.request('/api/memory/' + args.action, body), 0
    if command == 'project':
        if args.action == 'show':
            return client.request('/api/project'), 0
        if args.action == 'instructions':
            return client.request('/api/project/instructions', dict(project_id=args.project_id,
                instruction_revision=args.revision, instructions=args.text)), 0
        return client.request('/api/project/memory', dict(project_id=args.project_id,
            memory_revision=args.revision, memory_enabled=args.enabled == 'true')), 0
    if command == 'models':
        return client.request('/api/models', {'profile': args.select}) if args.select else client.request('/api/models'), 0
    if command == 'upgrade':
        return client.request('/api/upgrade', {}), 0
    if command == 'search':
        return client.request('/api/search', dict(query=args.query, archived=args.archived)), 0
    if command == 'backup':
        return write_new(args.out, client.request('/api/backup', {})), 0
    if command == 'conversations':
        if args.action in ('list', 'show'):
            if args.action == 'list' and args.archived:
                prefix += '/archived'
            return client.request(prefix), 0
        if args.action in ('archive', 'restore'):
            return client.request(prefix + '/archive', {'archived': args.action == 'archive'}), 0
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
    if getattr(args, 'branch_from', None):
        body['branch_from'] = identifier(args.branch_from)
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


class _StateSaveFailed(OfficeError):
    """A stale/unknown CAS must end the REPL, never save again on exit."""


class _InteractiveOffice:
    def __init__(self, client):
        self.client = client
        self.state = client.request('/api/cli-state')
        self.models = client.request('/api/models')
        self.conversations = client.request('/api/conversations')
        self.unsaved = None

    @staticmethod
    def empty_draft():
        return dict(prompt='', intent='chat', source_ids=[], revision_id=None)

    def key(self):
        return self.state['current'] or 'new'

    def draft(self):
        return copy.deepcopy(self.state['drafts'].get(self.key(), self.empty_draft()))

    def save(self, candidate):
        # Keep the attempted state in memory; do not overwrite another writer or
        # run a finally-save after any refused/unknown CAS.
        self.unsaved = candidate
        try:
            saved = self.client.request('/api/cli-state', candidate)
        except (OfficeError, OSError, ValueError) as exc:
            # User-visible recovery content is not an error/log message. The
            # private file stays untouched; let the user copy uncommitted text.
            draft = candidate['drafts'].get(candidate['current'] or 'new')
            if draft:
                print('Uncommitted draft (not saved; copy before reopening):', flush=True)
                print(draft['prompt'], flush=True)
            raise _StateSaveFailed('State save refused or outcome unknown. Interaction stopped; no automatic replay. '
                                   'Uncommitted input was not saved. Reopen before editing.') from exc
        self.state = saved
        self.unsaved = None

    def change(self, **fields):
        candidate = copy.deepcopy(self.state)
        candidate.update(fields)
        self.save(candidate)

    def update_draft(self, draft):
        drafts = copy.deepcopy(self.state['drafts'])
        drafts[self.key()] = draft
        self.change(drafts=drafts)

    def show_draft(self):
        draft = self.draft()
        print('Draft: %s (%s)' % (self.key(), draft['intent']), flush=True)
        print('Sources: ' + ', '.join(draft['source_ids']), flush=True)
        if draft['revision_id']:
            print('Revision: ' + draft['revision_id'], flush=True)
        if draft.get('branch_from'):
            print('Branch from: ' + draft['branch_from'] + ' (earlier history stays saved)', flush=True)
        print(draft['prompt'] or '(empty)', flush=True)

    def show_models(self, models):
        print('Selected model profile: ' + str(models['selected'] or '(none)'), flush=True)
        for item in models['items']:
            print('%s: %s | %s | chat=%s report=%s tools=%s' % (
                item['profile'], item['model'], item['provider'],
                item['capabilities']['chat'], item['capabilities']['report'],
                item['capabilities']['tools']), flush=True)

    def listing(self, result):
        for item in result['items']:
            print('%s: %s' % (item['id'], item['title']), flush=True)
        if not result['items']:
            print('(no conversations)', flush=True)

    def new(self, title):
        # Admission CAS precedes the conversation creation side effect.
        self.change(current=None)
        created = self.client.request('/api/conversations', {'title': title or 'New conversation'})
        drafts = copy.deepcopy(self.state['drafts'])
        drafts[created['id']] = drafts.pop('new', self.empty_draft())
        self.change(current=created['id'], drafts=drafts)
        print('Opened: ' + created['id'], flush=True)
        self.show_draft()

    def receipt_matches(self, run):
        pending = self.state['pending']
        wire = {k: v for k, v in pending['body'].items() if k not in ('request_id', 'consent')}
        return (run.get('request_id') == pending['body']['request_id'] and
                run.get('conversation_id') == pending['cid'] and
                run.get('request', {}).get('wire') == wire and
                (pending['run_id'] is None or run.get('id') == pending['run_id']))

    def receipt(self, run):
        if not self.receipt_matches(run):
            print('Request outcome unknown: receipt does not match. Pending request retained.', flush=True)
            return
        pending = copy.deepcopy(self.state['pending'])
        pending['run_id'] = identifier(run['id'])
        if run['status'] in ('completed', 'failed', 'cancelled'):
            drafts = copy.deepcopy(self.state['drafts'])
            original = {k: v for k, v in pending['body'].items() if k not in ('request_id', 'consent')}
            # Preserve a draft edited while a run was active. Failed/cancelled
            # requests also retain their original draft for an explicit retry.
            if run['status'] == 'completed' and drafts.get(pending['cid']) == original:
                drafts[pending['cid']] = self.empty_draft()
            self.change(pending=None, drafts=drafts)
        else:
            self.change(pending=pending)
        print('Receipt: %s %s' % (run['id'], run['status']), flush=True)
        if run.get('reply'):
            print(run['reply'], flush=True)
        if run.get('error'):
            print('Request did not complete successfully.', flush=True)

    def recover(self):
        pending = self.state['pending']
        if pending is None:
            print('No pending request.', flush=True)
            return
        try:
            if pending['run_id'] is not None:
                run = self.client.request('/api/runs/' + identifier(pending['run_id']))
            else:
                run = self.client.request('/api/conversations/' + identifier(pending['cid']) +
                                          '/requests/' + identifier(pending['body']['request_id']))
        except OfficeError:
            print('Request outcome unknown. Pending request retained; no replay is allowed.', flush=True)
            return
        self.receipt(run)

    def send(self):
        if self.state['pending'] is not None:
            print('A pending request has an unresolved outcome. Use /recover; no replay is allowed.', flush=True)
            return True
        draft = self.draft()
        if not draft['prompt'].strip():
            print('Draft is empty; not sent.', flush=True)
            return True
        if self.state['current'] is None:
            self.new('New conversation')
        cid = self.state['current']
        body = dict(draft, request_id=secrets.token_hex(16), consent=True)
        preview = self.client.request('/api/conversations/' + cid + '/preview', body)
        print('Provider: %s | Model: %s' % (preview['provider'], preview['model']), flush=True)
        print('History:', flush=True)
        for message in preview['history']:
            print('%s: %s' % (message['role'], message['content']), flush=True)
        print('Material/message:', flush=True)
        print(preview['message'], flush=True)
        print('Selected sources: ' + ', '.join(preview['source_ids']), flush=True)
        print('Type the complete confirmation hash to send, or any other line to return.', flush=True)
        print('Confirmation: ' + preview['confirmation'], flush=True)
        try:
            confirmation = input()
        except (EOFError, KeyboardInterrupt):
            print('Not sent. Draft retained.', flush=True)
            return False
        if confirmation != preview['confirmation']:
            print('Confirmation did not match; not sent. Draft retained.', flush=True)
            return True
        pending = dict(cid=cid, body=body, confirmation=confirmation, run_id=None)
        self.change(pending=pending)
        try:
            run = self.client.request('/api/conversations/' + cid + '/confirmed',
                                      dict(message=body, confirmation=confirmation))
        except OfficeError as exc:
            if exc.request_not_accepted:
                self.change(pending=None)
                print('Request not accepted. Draft retained; preview and confirm again.',flush=True)
                return True
            print('Request outcome unknown. Pending request retained. Use /recover; do not resend.', flush=True)
            return True
        except KeyboardInterrupt:
            print('Request outcome unknown. Pending request retained. Use /recover; do not resend.',flush=True)
            return True
        self.receipt(run)
        return True

    def command(self, line):
        name, _, tail = line.partition(' ')
        tail = tail.strip()
        if name == '/quit':
            return False
        if name == '/draft':
            self.show_draft()
        elif name == '/list':
            self.listing(self.client.request('/api/conversations'))
        elif name == '/new':
            self.new(tail)
        elif name == '/open':
            cid = identifier(tail)
            self.client.request('/api/conversations/' + cid)
            self.change(current=cid)
            self.show_draft()
        elif name == '/branch':
            cid = identifier(self.state['current'])
            run = self.client.request('/api/runs/' + identifier(tail))
            if (run['conversation_id'] != cid or run['status'] != 'completed' or
                    run['request']['intent'] != 'chat'):
                raise OfficeError('Choose a completed chat turn from this conversation.')
            wire = run['request'].get('wire', run['request'])
            draft = {k: copy.deepcopy(wire[k]) for k in ('prompt', 'intent', 'source_ids', 'revision_id')}
            draft['branch_from'] = run['id']
            self.update_draft(draft)
            self.show_draft()
        elif name == '/history':
            cid = identifier(self.state['current'])
            c = self.client.request('/api/conversations/' + cid)
            for message in c['messages']:
                state = 'active' if message['run_id'] in c['active_run_ids'] else 'saved earlier branch'
                print('%s | %s | %s: %s' % (message['run_id'], state, message['role'], message['content']), flush=True)
        elif name == '/models':
            self.show_models(self.client.request('/api/models'))
        elif name == '/model':
            # CAS guards this interaction before the configuration side effect.
            self.change()
            self.show_models(self.client.request('/api/models', {'profile': tail}))
        elif name == '/materials':
            cid = identifier(self.state['current'])
            materials = self.client.request('/api/conversations/' + cid)['sources']
            for material in materials:
                print('%s: %s | %s' % (material['id'], material['name'], material['sha256']), flush=True)
            if not materials:
                print('(no materials)', flush=True)
        elif name == '/import':
            paths = shlex.split(tail)
            if len(paths) != 1:
                raise OfficeError('Use /import PATH.')
            cid = identifier(self.state['current'])
            path = Path(paths[0])
            text = read_file(path, MAX_SOURCE_BYTES).decode('utf-8')
            self.change()
            material = self.client.request('/api/conversations/' + cid + '/sources',
                                           dict(name=path.name, text=text))
            print('Imported material: ' + material['id'] + '; select with /sources.', flush=True)
        elif name in ('/sources', '/report', '/chat', '/revise'):
            draft = self.draft()
            if name == '/sources':
                ids = [identifier(value) for value in tail.replace(',', ' ').split()]
                if len(ids) > 8 or len(set(ids)) != len(ids):
                    raise OfficeError('Select at most eight distinct sources.')
                draft['source_ids'] = ids
            elif name == '/revise':
                version = identifier(tail)
                cid = identifier(self.state['current'])
                self.client.request('/api/conversations/' + cid + '/artifacts/' + version + '/view')
                draft.update(intent='report', revision_id=version)
            else:
                draft.update(intent=name[1:], revision_id=None)
            if name in ('/chat', '/report', '/revise'):
                draft.pop('branch_from', None)
            self.update_draft(draft)
            self.show_draft()
        elif name == '/send':
            return self.send()
        elif name == '/recover':
            self.recover()
        elif name == '/stop':
            pending = self.state['pending']
            if pending is None or pending['run_id'] is None:
                print('No known active run. Use /recover first.', flush=True)
            else:
                self.change()
                run = self.client.request('/api/runs/' + identifier(pending['run_id']) + '/stop', {})
                self.receipt(run)
        elif name == '/export':
            parts = shlex.split(tail)
            if len(parts) != 3 or parts[1] not in ('md', 'docx'):
                raise OfficeError('Use /export VERSION md|docx PATH.')
            version, format_name, path = parts
            target = Path(path)
            if target.suffix.lower() != '.' + format_name:
                raise OfficeError('Output extension must match the selected format.')
            cid = identifier(self.state['current'])
            data = self.client.request('/api/conversations/' + cid + '/artifacts/' + identifier(version) + '/' + format_name)
            write_new(target, data)
            print('Exported version: ' + version, flush=True)
        else:
            print('Unknown command. Use /new /open /list /draft /history /branch /sources /chat /report /revise '
                  '/send /recover /stop /export /materials /import /models /model /quit.', flush=True)
        return True

    def run(self):
        print('Office interactive: text saves drafts; /send requires a fresh exact confirmation.', flush=True)
        self.show_models(self.models)
        self.listing(self.conversations)
        self.show_draft()
        if self.state['pending'] is not None:
            print('Pending request retained. Use /recover; saved confirmation is not new consent.', flush=True)
        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                print('Draft retained. Nothing sent on exit.', flush=True)
                return 0
            try:
                if line.startswith('/'):
                    if not self.command(line):
                        return 0
                else:
                    draft = self.draft()
                    draft['prompt'] = draft['prompt'] + ('\n' if self.key() in self.state['drafts'] else '') + line
                    self.update_draft(draft)
                    print('Draft saved; not sent.', flush=True)
            except _StateSaveFailed:
                raise
            except (OfficeError, OSError, ValueError, TypeError, KeyError):
                print('Office command refused. No success is implied; draft and pending request retained.', flush=True)


def run_office(args):
    try:
        if args.office_command == 'interactive' and args.as_json:
            raise OfficeError('--json is not available for interactive office sessions.')
        if args.office_command == 'restore':
            result, code = restore(read_file(args.source), args.data_dir), 0
        else:
            if (not args.data_dir.is_dir() and args.office_command != 'interactive' and
                    not (args.office_command == 'conversations' and args.action == 'create')):
                raise OfficeError('The office workspace does not exist. Create a conversation first.')
            config = load_config(args.config) if args.config else None
            if args.profile and config is None:
                raise OfficeError('--profile requires --config for an offline workspace.')
            if config and args.profile:
                profile_config(config, args.profile)  # Validate, but retain all configured choices.
                config = dict(config, default_profile=args.profile)
            with OfficeConnection(args.data_dir, config) as client:
                if client.remote and (args.config or args.profile):
                    raise OfficeError('The running server owns model configuration; omit CLI config/profile overrides.')
                if args.office_command == 'interactive':
                    return _InteractiveOffice(client).run()
                result, code = _operate(args, client)
        response = dict(schema_version='pocketchat-office-cli.v1', ok=code == 0, result=result)
    except OfficeError as exc:
        # Interactive errors are content-free even if a server sends unsafe text.
        error = (str(exc) if isinstance(exc, _StateSaveFailed) else
                 'Office interactive operation refused. Draft and pending request were not overwritten.') if args.office_command == 'interactive' else str(exc)
        response = dict(schema_version='pocketchat-office-cli.v1', ok=False, error=error)
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
        print(response['error'], file=sys.stderr)
    return code
