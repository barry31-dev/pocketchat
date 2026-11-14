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

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


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
            if version not in (0, 2):
                raise OfficeError('Unsupported office workspace version.')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, cid TEXT, name TEXT, text TEXT, sha256 TEXT);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, cid TEXT, request_id TEXT UNIQUE,
                    payload TEXT, status TEXT, reply TEXT, error TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, cid TEXT, role TEXT,
                    content TEXT, context TEXT, run_id TEXT);
                CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, cid TEXT, run_id TEXT UNIQUE,
                    content TEXT, sources TEXT, predecessor TEXT, created REAL);
                PRAGMA user_version=2;
            ''')
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
            self.db.execute('INSERT INTO conversations VALUES (?,?,?)', (cid, title, time.time()))
            return self.get(cid)

    def rename(self, cid, title):
        title = text_value(title, 240, 'Title')
        with self.lock, self.db:
            self._row('conversations', cid)
            self.db.execute('UPDATE conversations SET title=? WHERE id=?', (title, cid))
            return self.get(cid)

    def list(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM conversations ORDER BY created DESC')]

    def get(self, cid):
        with self.lock:
            c = self._row('conversations', cid)
            c['sources'] = [dict(r) for r in self.db.execute('SELECT id,name,sha256,length(CAST(text AS BLOB)) AS bytes FROM sources WHERE cid=? ORDER BY rowid', (cid,))]
            c['messages'] = [dict(r) for r in self.db.execute('SELECT role,content,run_id FROM messages WHERE cid=? ORDER BY id', (cid,))]
            c['artifacts'] = [dict(r) for r in self.db.execute('SELECT id,run_id,predecessor,created FROM artifacts WHERE cid=? ORDER BY rowid DESC', (cid,))]
            c['runs'] = [dict(r) for r in self.db.execute('SELECT id,status,error,created FROM runs WHERE cid=? ORDER BY rowid DESC LIMIT 20', (cid,))]
            return c

    def history(self, cid):
        with self.lock:
            self._row('conversations', cid)
            return [dict(role=r['role'], content=r['context']) for r in self.db.execute('SELECT role,context FROM messages WHERE cid=? ORDER BY id', (cid,))]

    def add_source(self, cid, name, text):
        text_value(name, 200, 'File name')
        if '/' in name or '\\' in name or Path(name).suffix.lower() not in ('.txt', '.md', '.csv'):
            raise OfficeError('Choose a plain TXT, Markdown or CSV file name.')
        text_value(text, MAX_SOURCE_BYTES, 'Source')
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
        with self.lock, self.db:
            self._row('conversations', cid)
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
            for row in self.db.execute("SELECT payload FROM runs WHERE cid=? AND status='completed' ORDER BY rowid", (cid,)):
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
            if self.db.execute("SELECT 1 FROM runs WHERE status IN ('running','stopping')").fetchone():
                raise OfficeError('A request is still running. Wait for its final status.', 409)
            if self.db.execute('SELECT count(*) FROM runs WHERE cid=?', (cid,)).fetchone()[0] >= 250:
                raise OfficeError('Conversation request limit reached.', 409)
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
                self.db.executemany('INSERT INTO messages(cid,role,content,context,run_id) VALUES (?,?,?,?,?)', [(r['cid'], 'user', p['prompt'], context or p['prompt'], rid), (r['cid'], 'assistant', reply, reply, rid)])
                if p['intent'] == 'report':
                    sources = [self.source(r['cid'], s) for s in p['source_ids']]
                    manifest = [dict(id=s['id'], name=s['name'], sha256=s['sha256'], citation=self.citation(r['cid'], s['id'])) for s in sources]
                    self.db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)', (uuid4().hex, r['cid'], rid, reply, encoded(manifest), p.get('revision_id'), time.time()))
            self.db.execute('UPDATE runs SET status=?,reply=?,error=? WHERE id=?', (status, reply[:MAX_REPLY_BYTES], error, rid))
