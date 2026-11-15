"""Bounded browser-owned records; no paths supplied by a browser are opened."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path
from uuid import uuid4

MAX_SOURCE_BYTES = 12 * 1024
MAX_INPUT_BYTES = 48 * 1024
MAX_REPLY_BYTES = 128 * 1024
ID = re.compile(r'^[a-zA-Z0-9_-]{3,80}$')


class OfficeError(ValueError):
    """Public, content-free office refusal."""

    def __init__(self, message, status=400, request_not_accepted=False):
        super().__init__(message)
        self.status = status
        self.request_not_accepted = request_not_accepted


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise OfficeError('Invalid record ID.')
    return value


def text_value(value, maximum, label):
    if not isinstance(value, str) or not value.strip():
        raise OfficeError('%s must not be empty.' % label)
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError as exc:
        raise OfficeError('%s must be valid UTF-8.' % label) from exc
    if size > maximum or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise OfficeError('%s exceeds its text limit or contains control characters.' % label)
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def safe_root(root):
    root = Path(os.path.abspath(str(root)))
    for p in [root, *root.parents]:
        if p.is_symlink():
            raise OfficeError('Workspace paths must not contain symbolic links.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not root.is_dir():
        raise OfficeError('Workspace must be a private directory.')
    if os.name != 'nt' and stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise OfficeError('Workspace must be private (mode 0700).')
    for name in ('office.sqlite', 'office.sqlite-wal', 'office.sqlite-shm', 'office.lock'):
        p = root / name
        if p.exists() or p.is_symlink():
            s = p.lstat()
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                raise OfficeError('Workspace file is not a private regular file.')
    return root


class OfficeStore:
    def __init__(self, root):
        self.root = safe_root(root)
        self.lock = threading.RLock()
        self.db = None
        self._lock_fd = None
        fd = os.open(str(self.root / 'office.lock'), os.O_RDWR | os.O_CREAT |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b'0')
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise OfficeError('This workspace already has a running server.', 409) from exc
        self._lock_fd = fd
        try:
            self.db = sqlite3.connect(str(self.root / 'office.sqlite'), check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            os.chmod(self.root / 'office.sqlite', 0o600)
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 2, 3, 4):
                raise OfficeError('Unsupported office workspace version.')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT, created REAL, archived INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, cid TEXT, name TEXT, text TEXT, sha256 TEXT);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, cid TEXT, request_id TEXT UNIQUE,
                    payload TEXT, status TEXT, reply TEXT, error TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, cid TEXT, role TEXT,
                    content TEXT, context TEXT, run_id TEXT);
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, cid TEXT, run_id TEXT UNIQUE,
                    content TEXT, sources TEXT, predecessor TEXT, created REAL);
            ''')
            if version == 0:
                self.db.execute('PRAGMA user_version=4')
            with self.db:
                self.db.execute("UPDATE runs SET status='unknown', error='Interrupted before completion; inspect before retrying.' WHERE status IN ('running','stopping')")
        except Exception:
            self.close()
            raise

    def close(self):
        with self.lock:
            if self.db is not None:
                self.db.close()
                self.db = None
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None

    def cli_state(self, replacement=None):
        """Private CLI-only drafts, with compare-and-swap under the workspace writer."""
        from .json_input import decode_json_bytes, JSONInputError
        from .office_backup import parent_directory, read_file
        path = self.root / 'office-cli-state.json'
        binding = hashlib.sha256(encoded([str(self.root), self.root.stat().st_dev,
                                          self.root.stat().st_ino]).encode()).hexdigest()
        limit = 3 * 1024 * 1024
        with self.lock:
            state = dict(revision=0, current=None, drafts={}, pending=None)
            if path.exists() or path.is_symlink():
                try:
                    document = decode_json_bytes(read_file(path, limit), limit, 12, 20000)
                except JSONInputError as exc:
                    raise OfficeError('Saved CLI state is invalid; preserve it before recovery.') from exc
                if (not isinstance(document, dict) or set(document) != {'schema', 'workspace', 'state'} or
                        document['schema'] != 'pocketchat-office-cli-state.v1' or document['workspace'] != binding):
                    raise OfficeError('Saved CLI state belongs to another workspace or version.')
                state = document['state']
            self._validate_cli_state(state)
            if replacement is None:
                return json.loads(encoded(state))
            self._validate_cli_state(replacement)
            if replacement['revision'] != state['revision']:
                raise OfficeError('CLI state changed in another client. Reopen before editing.', 409)
            saved = dict(replacement, revision=state['revision'] + 1)
            raw = encoded(dict(schema='pocketchat-office-cli-state.v1', workspace=binding, state=saved)).encode()
            if len(raw) > limit:
                raise OfficeError('CLI draft storage limit reached.')
            with parent_directory(path) as (parent, name):
                temporary = 'office-cli-state-' + uuid4().hex + '.tmp'
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                finally:
                    try: os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError: pass
            return json.loads(encoded(saved))

    def _validate_cli_state(self, state):
        if (not isinstance(state, dict) or set(state) != {'revision','current','drafts','pending'} or
                type(state['revision']) is not int or not 0 <= state['revision'] < 2**53 - 1 or
                not isinstance(state['drafts'], dict) or len(state['drafts']) > 201):
            raise OfficeError('Invalid CLI state.')
        if state['current'] is not None:
            self._row('conversations', state['current'])
        def draft(cid, value, pending=False):
            fields = {'prompt','intent','source_ids','revision_id'} | ({'request_id','consent'} if pending else set())
            if not isinstance(value, dict) or set(value) not in (fields, fields | {'branch_from'}):
                raise OfficeError('Invalid CLI draft.')
            prompt = value['prompt']
            if not isinstance(prompt, str): raise OfficeError('Invalid CLI draft text.')
            try: prompt_size=len(prompt.encode('utf-8'))
            except UnicodeError as exc: raise OfficeError('CLI draft must be valid UTF-8.') from exc
            if prompt_size > 12000 or any(ord(c)<32 and c not in '\n\r\t' for c in prompt):
                raise OfficeError('CLI draft exceeds its text limit.')
            if (value['intent'] not in ('chat','report') or not isinstance(value['source_ids'], list) or
                    len(value['source_ids']) > 8 or any(not isinstance(s, str) for s in value['source_ids']) or
                    len(set(value['source_ids'])) != len(value['source_ids'])):
                raise OfficeError('Invalid CLI draft selection.')
            if 'branch_from' in value:
                if cid == 'new' or value['intent'] != 'chat' or value['revision_id'] is not None:
                    raise OfficeError('Invalid chat branch draft.')
                identifier(value['branch_from'])
                self.context_runs(cid, value['branch_from'])
            if cid == 'new':
                if value['source_ids'] or value['revision_id'] is not None:
                    raise OfficeError('New-conversation drafts cannot reference saved material.')
            else:
                self._row('conversations', cid)
                for sid in value['source_ids']: self.source(cid, sid)
                if value['revision_id'] is not None: self.artifact(cid, value['revision_id'])
            if value['revision_id'] is not None and value['intent'] != 'report':
                raise OfficeError('Only report drafts can reference a predecessor.')
            if pending:
                identifier(value['request_id'])
                if value['consent'] is not True: raise OfficeError('Invalid pending confirmation.')
                text_value(prompt, 12000, 'Message')
        for cid, value in state['drafts'].items(): draft(cid, value)
        pending = state['pending']
        if pending is not None:
            if not isinstance(pending, dict) or set(pending) != {'cid','body','confirmation','run_id'}:
                raise OfficeError('Invalid saved request receipt.')
            if pending['cid'] == 'new': raise OfficeError('A pending request needs a saved conversation.')
            draft(pending['cid'], pending['body'], True)
            proof = pending['confirmation']
            if not isinstance(proof,str) or len(proof)!=64 or any(c not in '0123456789abcdef' for c in proof):
                raise OfficeError('Invalid saved confirmation.')
            if pending['run_id'] is not None:
                if self.run(pending['run_id'])['cid'] != pending['cid']:
                    raise OfficeError('Saved receipt belongs to another conversation.')

    def _row(self, table, key):
        row = self.db.execute('SELECT * FROM ' + table + ' WHERE id=?', (identifier(key),)).fetchone()
        if row is None:
            raise OfficeError('Record not found.', 404)
        return dict(row)

    def create(self, title):
        title = text_value(title, 240, 'Title')
        with self.lock, self.db:
            if self.db.execute('SELECT count(*) FROM conversations').fetchone()[0] >= 200:
                raise OfficeError('Conversation limit reached.', 409)
            cid = uuid4().hex
            self.db.execute('INSERT INTO conversations(id,title,created) VALUES (?,?,?)', (cid, title, time.time()))
            return self.get(cid)

    def rename(self, cid, title):
        title = text_value(title, 240, 'Title')
        with self.lock, self.db:
            self.writable(cid)
            self.db.execute('UPDATE conversations SET title=? WHERE id=?', (title, cid))
            return self.get(cid)

    def writable(self, cid):
        with self.lock:
            if self._row('conversations', cid).get('archived', 0):
                raise OfficeError('Restore the archived conversation before changing it.', 409)

    def list(self, archived=False):
        if type(archived) is not bool:
            raise OfficeError('Archive filter must be a boolean.')
        with self.lock:
            rows = [dict(r) for r in self.db.execute('SELECT * FROM conversations ORDER BY created DESC')]
            return [dict(r, archived=bool(r.get('archived', 0))) for r in rows
                    if bool(r.get('archived', 0)) == archived]

    def archive(self, cid, archived):
        if type(archived) is not bool:
            raise OfficeError('Archive state must be a boolean.')
        with self.lock, self.db:
            self._row('conversations', cid)
            if self.db.execute('PRAGMA user_version').fetchone()[0] < 3:
                raise OfficeError('Upgrade the workspace with a backup before archiving.', 409)
            if self.db.execute("SELECT 1 FROM runs WHERE cid=? AND status IN ('running','stopping')", (cid,)).fetchone():
                raise OfficeError('Wait for the active request before archiving.', 409)
            self.db.execute('UPDATE conversations SET archived=? WHERE id=?', (int(archived), cid))
            return self.get(cid)

    def search(self, query, archived=False):
        query = text_value(query, 240, 'Search query').casefold()
        with self.lock:
            visible = {c['id']: c for c in self.list(archived)}
            hits = []
            for m in self.db.execute('SELECT id,cid,role,content FROM messages ORDER BY id'):
                if m['cid'] not in visible:
                    continue
                offset = m['content'].casefold().find(query)
                if offset < 0:
                    continue
                if len(hits) == 100:
                    return dict(items=hits, truncated=True)
                # casefold may expand a character (ß -> ss). Translate the
                # folded offset back before slicing the original saved text.
                consumed = 0
                original_offset = 0
                for original_offset, character in enumerate(m['content']):
                    consumed += len(character.casefold())
                    if consumed > offset:
                        break
                start = max(0, original_offset - 60)
                hits.append(dict(conversation_id=m['cid'], title=visible[m['cid']]['title'],
                                 message_id=str(m['id']), role=m['role'],
                                 excerpt=m['content'][start:start + 240]))
            return dict(items=hits, truncated=False)

    def get(self, cid):
        with self.lock:
            c = self._row('conversations', cid)
            c['archived'] = bool(c.get('archived', 0))
            c['sources'] = [dict(r) for r in self.db.execute('SELECT id,name,sha256,length(CAST(text AS BLOB)) AS bytes FROM sources WHERE cid=? ORDER BY rowid', (cid,))]
            c['messages'] = [dict(r, id=str(r['id'])) for r in self.db.execute('SELECT id,role,content,run_id FROM messages WHERE cid=? ORDER BY id', (cid,))]
            c['artifacts'] = [dict(r) for r in self.db.execute('SELECT id,run_id,predecessor,created FROM artifacts WHERE cid=? ORDER BY rowid DESC', (cid,))]
            c['runs'] = [dict(r) for r in self.db.execute('SELECT id,status,error,created FROM runs WHERE cid=? ORDER BY rowid DESC LIMIT 20', (cid,))]
            c['active_run_ids'] = self.context_runs(cid)
            c['chat_run_ids'] = [r['id'] for r in self.db.execute(
                "SELECT id,payload FROM runs WHERE cid=? AND status='completed' ORDER BY rowid", (cid,))
                if json.loads(r['payload'])['intent'] == 'chat']
            return c

    def context_runs(self, cid, branch_from=None):
        """Resolve an append-only path; failed attempts never select a new branch."""
        with self.lock:
            self._row('conversations', cid)
            active = []
            for row in self.db.execute(
                    "SELECT id,payload FROM runs WHERE cid=? AND status='completed' ORDER BY rowid", (cid,)):
                payload = json.loads(row['payload'])
                prefix = payload.get('history_run_ids', active)
                if row['id'] == branch_from:
                    if payload['intent'] != 'chat':
                        raise OfficeError('Use report revision for a saved report.', 409)
                    return list(prefix)
                active = list(prefix) + [row['id']]
            if branch_from is not None:
                raise OfficeError('Completed chat turn not found in this conversation.', 404)
            return active

    def history(self, cid, run_ids=None):
        with self.lock:
            self._row('conversations', cid)
            ids = self.context_runs(cid) if run_ids is None else run_ids
            rows = {}
            for row in self.db.execute('SELECT role,context,run_id FROM messages WHERE cid=? ORDER BY id', (cid,)):
                rows.setdefault(row['run_id'], []).append(dict(role=row['role'], content=row['context']))
            return [message for rid in ids for message in rows[rid]]

    def add_source(self, cid, name, text):
        text_value(name, 200, 'File name')
        if '/' in name or '\\' in name or Path(name).suffix.lower() not in ('.txt', '.md', '.csv'):
            raise OfficeError('Choose a plain TXT, Markdown or CSV file name.')
        text_value(text, MAX_SOURCE_BYTES, 'Source')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with self.lock, self.db:
            self.writable(cid)
            if self.db.execute('SELECT count(*) FROM sources WHERE cid=?', (cid,)).fetchone()[0] >= 64:
                raise OfficeError('Source limit reached.', 409)
            sid = uuid4().hex
            self.db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (sid, cid, name, text, digest))
            return dict(id=sid, name=name, sha256=digest, bytes=len(text.encode('utf-8')))

    def source(self, cid, sid):
        with self.lock:
            row = self._row('sources', sid)
            if row['cid'] != cid:
                raise OfficeError('Source not found in this conversation.', 404)
            return row

    def disclosed_sources(self, cid):
        with self.lock:
            ids = []
            active = set(self.context_runs(cid))
            for row in self.db.execute("SELECT id,payload FROM runs WHERE cid=? AND status='completed' ORDER BY rowid", (cid,)):
                if row['id'] not in active:
                    continue
                for sid in json.loads(row['payload'])['source_ids']:
                    if sid not in ids:
                        ids.append(sid)
            return ids

    def citation(self, cid, sid):
        with self.lock:
            self.source(cid, sid)
            ids = [r[0] for r in self.db.execute('SELECT id FROM sources WHERE cid=? ORDER BY rowid', (cid,))]
            return 'S%d' % (ids.index(sid) + 1)

    def artifact(self, cid, aid):
        with self.lock:
            row = self._row('artifacts', aid)
            if row['cid'] != cid:
                raise OfficeError('Report not found in this conversation.', 404)
            row['sources'] = json.loads(row['sources'])
            return row

    def receipt(self, cid, request_id, wire):
        with self.lock:
            row = self.db.execute('SELECT * FROM runs WHERE request_id=?', (identifier(request_id),)).fetchone()
            if row is None:
                return None
            payload = json.loads(row['payload'])
            if row['cid'] != cid or payload.get('wire') != wire:
                raise OfficeError('Request ID is already bound to different content.', 409)
            return dict(row)

    def _next_message_ids(self):
        last = self.db.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
        if last > 2**63 - 3:
            raise OfficeError('Message IDs exhausted; this workspace cannot accept new messages.', 409)
        return last + 1, last + 2

    def begin(self, cid, request_id, payload):
        identifier(request_id)
        body = encoded(payload)
        with self.lock, self.db:
            self._row('conversations', cid)
            old = self.db.execute('SELECT * FROM runs WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['cid'] != cid or old['payload'] != body:
                    raise OfficeError('Request ID is already bound to different content.', 409)
                return dict(old)
            self.writable(cid)
            if self.db.execute("SELECT 1 FROM runs WHERE status IN ('running','stopping')").fetchone():
                raise OfficeError('A request is still running. Wait for its final status.', 409)
            if self.db.execute('SELECT count(*) FROM runs WHERE cid=?', (cid,)).fetchone()[0] >= 250:
                raise OfficeError('Conversation request limit reached.', 409)
            self._next_message_ids()
            rid = uuid4().hex
            self.db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)', (rid, cid, request_id, body, 'running', '', None, time.time()))
            return self.run(rid)

    def run(self, rid):
        with self.lock:
            return self._row('runs', rid)

    def stopping(self, rid):
        with self.lock, self.db:
            self._row('runs', rid)
            self.db.execute("UPDATE runs SET status='stopping' WHERE id=? AND status='running'", (rid,))

    def finish(self, rid, status, reply, error, context=None):
        if status not in ('completed', 'failed', 'cancelled'):
            raise OfficeError('Invalid terminal status.')
        if status == 'completed':
            text_value(reply, MAX_REPLY_BYTES, 'Reply')
        with self.lock, self.db:
            r = self.run(rid)
            if r['status'] not in ('running', 'stopping'):
                raise OfficeError('Run already has a final status.', 409)
            if r['status'] == 'stopping':
                status, reply, error = 'cancelled', '', 'Stopped by the user.'
            p = json.loads(r['payload'])
            if status == 'completed':
                user_id, assistant_id = self._next_message_ids()
                self.db.executemany('INSERT INTO messages(id,cid,role,content,context,run_id) VALUES (?,?,?,?,?,?)', [(user_id, r['cid'], 'user', p['prompt'], context or p['prompt'], rid), (assistant_id, r['cid'], 'assistant', reply, reply, rid)])
                if p['intent'] == 'report':
                    sources = [self.source(r['cid'], s) for s in p['source_ids']]
                    manifest = [dict(id=s['id'], name=s['name'], sha256=s['sha256'], citation=self.citation(r['cid'], s['id'])) for s in sources]
                    self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)', (uuid4().hex, r['cid'], rid, reply, encoded(manifest), p.get('revision_id'), time.time()))
            self.db.execute('UPDATE runs SET status=?,reply=?,error=? WHERE id=?', (status, reply[:MAX_REPLY_BYTES], error, rid))
