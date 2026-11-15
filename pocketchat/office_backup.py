"""Bounded, data-only office backups. No imported SQL or filesystem extraction."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from .json_input import JSONInputError, decode_json_bytes
from .office_store import (MAX_REPLY_BYTES, MAX_SOURCE_BYTES, OfficeError,
                           OfficeStore, encoded, identifier, text_value)

MAX_BACKUP_BYTES = 32 * 1024 * 1024
SCHEMA = 'pocketchat-office-backup.v1'
TABLES = {
    'conversations': ('id', 'title', 'created'),
    'sources': ('id', 'cid', 'name', 'text', 'sha256'),
    'runs': ('id', 'cid', 'request_id', 'payload', 'status', 'reply', 'error', 'created'),
    'messages': ('id', 'cid', 'role', 'content', 'context', 'run_id'),
    'artifacts': ('id', 'cid', 'run_id', 'content', 'sources', 'predecessor', 'created'),
}


def table_columns(version):
    if version >= 3:
        return dict(TABLES, conversations=TABLES['conversations'] + ('archived',))
    return TABLES


def _json(raw):
    try:
        return decode_json_bytes(raw, MAX_BACKUP_BYTES, 24, 1000000)
    except JSONInputError as exc:
        raise OfficeError('Invalid or oversized office backup JSON.') from exc


def _require(ok):
    if not ok:
        raise OfficeError('Invalid office backup data or references.')


def _payload(value):
    _require(isinstance(value, str))
    return _json(value.encode('utf-8'))


def validate(raw):
    """Check closed record shapes, hashes and same-conversation references."""
    doc = _json(raw)
    _require(isinstance(doc, dict) and set(doc) == {'schema_version', 'sha256', 'data'})
    if doc['schema_version'] != SCHEMA:
        raise OfficeError('Unsupported office backup version.')
    data = doc['data']
    _require(isinstance(data, dict) and set(data) == {'database_version', 'tables'})
    if type(data['database_version']) is not int or data['database_version'] not in (2, 3, 4):
        raise OfficeError('Unsupported office database version.')
    _require(hashlib.sha256(encoded(data).encode('utf-8')).hexdigest() == doc['sha256'])
    tables = data['tables']
    _require(isinstance(tables, dict) and set(tables) == set(TABLES))
    maps = {}
    for table, columns in table_columns(data['database_version']).items():
        rows = tables[table]
        _require(isinstance(rows, list) and len(rows) <= 100000)
        maps[table] = {}
        for row in rows:
            _require(isinstance(row, dict) and set(row) == set(columns))
            if table == 'messages':
                _require(type(row['id']) is int and 0 < row['id'] <= 2**63 - 1)
            else:
                identifier(row['id'])
            _require(row['id'] not in maps[table])
            maps[table][row['id']] = row
            for key, value in row.items():
                if key == 'archived':
                    _require(type(value) is int and value in (0, 1))
                elif key == 'created':
                    _require(type(value) in (int, float) and 0 <= value < 1e12)
                elif key not in ('id', 'predecessor', 'error'):
                    _require(isinstance(value, str) and len(value.encode('utf-8')) <= MAX_BACKUP_BYTES)
            if 'cid' in row:
                _require(row['cid'] in maps['conversations'])
    _require(len(maps['conversations']) <= 200)
    for table, limit in (('sources', 64), ('runs', 250)):
        _require(all(count <= limit for count in Counter(row['cid'] for row in tables[table]).values()))
    for c in tables['conversations']:
        text_value(c['title'], 240, 'Title')
    for s in tables['sources']:
        text_value(s['name'], 200, 'File name')
        _require('/' not in s['name'] and '\\' not in s['name'] and
                 Path(s['name']).suffix.lower() in ('.txt', '.md', '.csv'))
        text_value(s['text'], MAX_SOURCE_BYTES, 'Source')
        _require(s['sha256'] == hashlib.sha256(s['text'].encode('utf-8')).hexdigest())

    def ref(table, key, cid):
        _require(isinstance(key, str) and key in maps[table] and maps[table][key]['cid'] == cid)
        return maps[table][key]

    request_ids = set()
    payloads = {}
    paths = {}
    active = {}
    for r in tables['runs']:
        identifier(r['request_id'])
        _require(r['request_id'] not in request_ids)
        request_ids.add(r['request_id'])
        _require(r['status'] in ('completed', 'failed', 'cancelled', 'unknown'))
        _require(r['error'] is None or isinstance(r['error'], str))
        if r['status'] == 'completed':
            text_value(r['reply'], MAX_REPLY_BYTES, 'Reply')
        p = _payload(r['payload'])
        required = {'prompt', 'intent', 'source_ids', 'revision_id'}
        _require(isinstance(p, dict) and required <= set(p) <= required | {'wire', 'confirmation', 'model_snapshot', 'history_run_ids', 'branch_from'})
        prefix = active.get(r['cid'], [])
        if 'branch_from' in p:
            target = ref('runs', p['branch_from'], r['cid'])
            _require(data['database_version'] >= 4 and target['id'] in paths and
                     target['status'] == 'completed' and payloads[target['id']]['intent'] == 'chat' and
                     p['intent'] == 'chat' and p['revision_id'] is None and
                     'history_run_ids' in p and 'wire' in p)
            prefix = paths[target['id']][:-1]
        if 'history_run_ids' in p:
            _require(data['database_version'] >= 4 and p['history_run_ids'] == prefix)
        if r['status'] == 'completed':
            paths[r['id']] = prefix + [r['id']]
            active[r['cid']] = paths[r['id']]
        if 'confirmation' in p:
            _require({'wire', 'model_snapshot'} <= set(p))
            proof = p['confirmation']
            _require(isinstance(proof, str) and len(proof) == 64 and all(c in '0123456789abcdef' for c in proof))
        if 'model_snapshot' in p:
            _require('wire' in p)
            model = p['model_snapshot']
            _require(isinstance(model, dict) and set(model) == {'model', 'provider'})
            text_value(model['model'], 512, 'Model')
            text_value(model['provider'], 4096, 'Provider')
        text_value(p['prompt'], 12000, 'Message')
        _require(p['intent'] in ('chat', 'report') and isinstance(p['source_ids'], list))
        _require(len(p['source_ids']) <= 8 and all(isinstance(s, str) for s in p['source_ids']))
        _require(len(set(p['source_ids'])) == len(p['source_ids']))
        for sid in p['source_ids']:
            ref('sources', sid, r['cid'])
        if p['revision_id'] is not None:
            ref('artifacts', p['revision_id'], r['cid'])
        if 'wire' in p:
            w = p['wire']
            _require(isinstance(w, dict) and set(w) == {'prompt', 'intent', 'source_ids', 'revision_id'} | ({'branch_from'} if 'branch_from' in p else set()))
            _require(w.get('branch_from') == p.get('branch_from'))
            _require(isinstance(w['source_ids'], list) and len(w['source_ids']) <= 8)
            _require(w['prompt'] == p['prompt'] and w['intent'] == p['intent'] and w['revision_id'] == p['revision_id'])
            for sid in w['source_ids']:
                ref('sources', sid, r['cid'])
        payloads[r['id']] = p
    messages = {}
    last_id = 0
    for m in tables['messages']:
        _require(m['id'] > last_id)
        last_id = m['id']
        r = ref('runs', m['run_id'], m['cid'])
        _require(r['status'] == 'completed' and m['role'] in ('user', 'assistant'))
        _require(m['content'] == (r['reply'] if m['role'] == 'assistant' else payloads[r['id']]['prompt']))
        if m['role'] == 'assistant':
            _require(m['context'] == r['reply'])
        messages.setdefault(r['id'], []).append(m['role'])
    # Completed-run chronology is independently committed by the monotonically
    # allocated message IDs, not by the untrusted runs array order alone.
    completed = [r['id'] for r in tables['runs'] if r['status'] == 'completed']
    message_order = [(m['run_id'], m['role']) for m in tables['messages']]
    _require(message_order == [(rid, role) for rid in completed for role in ('user', 'assistant')])
    artifact_runs = set()
    for a in tables['artifacts']:
        r = ref('runs', a['run_id'], a['cid'])
        p = payloads[r['id']]
        _require(r['status'] == 'completed' and p['intent'] == 'report' and a['content'] == r['reply'])
        _require(a['run_id'] not in artifact_runs and a['predecessor'] == p['revision_id'])
        artifact_runs.add(a['run_id'])
        if a['predecessor'] is not None:
            prior = ref('artifacts', a['predecessor'], a['cid'])
            _require(prior['id'] != a['id'])
        sources = _payload(a['sources'])
        expected = []
        ordered = [s['id'] for s in tables['sources'] if s['cid'] == a['cid']]
        for sid in p['source_ids']:
            s = ref('sources', sid, a['cid'])
            expected.append(dict(id=sid, name=s['name'], sha256=s['sha256'], citation='S%d' % (ordered.index(sid) + 1)))
        _require(sources == expected)
    for r in tables['runs']:
        if r['status'] == 'completed':
            _require(messages.get(r['id']) == ['user', 'assistant'])
            _require((r['id'] in artifact_runs) == (payloads[r['id']]['intent'] == 'report'))
    checked = set()
    for aid in maps['artifacts']:
        chain = set()
        current = aid
        while current is not None and current not in checked:
            _require(current not in chain)
            chain.add(current)
            current = maps['artifacts'][current]['predecessor']
        checked.update(chain)
    return data


def snapshot(store):
    """Take a consistent SQLite online snapshot under the store mutation lock."""
    with store.lock:
        if store.db.execute("SELECT 1 FROM runs WHERE status IN ('running','stopping')").fetchone():
            raise OfficeError('Wait for the active request before making a backup.', 409)
        size = store.db.execute('PRAGMA page_count').fetchone()[0] * store.db.execute('PRAGMA page_size').fetchone()[0]
        if size > MAX_BACKUP_BYTES:
            raise OfficeError('Workspace exceeds the 32 MiB backup limit.')
        with sqlite3.connect(':memory:') as copy:
            store.db.backup(copy)
            copy.row_factory = sqlite3.Row
            data = dict(database_version=copy.execute('PRAGMA user_version').fetchone()[0],
                        tables={name: [dict(r) for r in copy.execute('SELECT ' + ','.join(columns) + ' FROM ' + name + ' ORDER BY rowid')]
                                for name, columns in table_columns(copy.execute('PRAGMA user_version').fetchone()[0]).items()})
    raw = encoded(dict(schema_version=SCHEMA, data=data,
                       sha256=hashlib.sha256(encoded(data).encode('utf-8')).hexdigest())).encode('utf-8')
    validate(raw)
    return raw


@contextmanager
def parent_directory(path):
    """Pin each parent without following links; never downgrade secure output I/O."""
    required = (os.open, os.mkdir, os.unlink, os.rmdir)
    if (not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY') or
            any(fn not in os.supports_dir_fd for fn in required)):
        raise OfficeError('Office filesystem commands require no-follow directory-descriptor support.')
    path = Path(os.path.abspath(str(path)))
    if not path.name or path.name.startswith('.'):
        raise OfficeError('Choose a visible new file or directory name.')
    fd = None
    try:
        fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in path.parent.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd, path.name
    except OSError as exc:
        raise OfficeError('Path is unavailable, linked or already exists.') from exc
    finally:
        if fd is not None:
            os.close(fd)


def _write_at(parent, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        os.unlink(name, dir_fd=parent)
        raise


def write_new(path, raw):
    with parent_directory(path) as (parent, name):
        _write_at(parent, name, raw)
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def read_file(path, maximum=MAX_BACKUP_BYTES):
    with parent_directory(path) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
                raise OfficeError('Choose a bounded regular file, not a link.')
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise OfficeError('File exceeds its size limit.')
            return data


def restore(raw, destination):
    """Validate entirely before creating a destination; never overwrite a workspace."""
    data = validate(raw)
    with tempfile.TemporaryDirectory(prefix='pocketchat-restore-') as tmp:
        store = OfficeStore(Path(tmp).resolve() / 'workspace')
        try:
            with store.db:
                if data['database_version'] == 2:
                    store.db.execute('DROP TABLE conversations')
                    store.db.execute('CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,created REAL)')
                    store.db.execute('PRAGMA user_version=2')
                store.db.execute('PRAGMA user_version=%d' % data['database_version'])
                for name, columns in table_columns(data['database_version']).items():
                    query = 'INSERT INTO ' + name + '(' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')'
                    store.db.executemany(query, [[row[k] for k in columns] for row in data['tables'][name]])
        finally:
            store.close()
        database = (store.root / 'office.sqlite').read_bytes()
    with parent_directory(destination) as (parent, name):
        os.mkdir(name, 0o700, dir_fd=parent)
        directory = lock_fd = None
        lock_owned = False
        try:
            directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            # Claim before publishing even the first SQLite byte. A competing
            # writer winning before this exclusive create makes restore refuse.
            lock_fd = os.open('office.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock_owned = True
            _write_at(directory, 'office.sqlite', database)
        except BaseException:
            # Do not remove a lock acquired by a competing writer or its data.
            if lock_owned:
                os.unlink('office.lock', dir_fd=directory)
            try:
                os.rmdir(name, dir_fd=parent)
            except OSError:
                pass
            raise
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
            if directory is not None:
                os.close(directory)
    return dict(conversations=len(data['tables']['conversations']),
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))


def upgrade(store):
    """Explicit schema upgrade; preserve a validated durable backup first."""
    with store.lock:
        version = store.db.execute('PRAGMA user_version').fetchone()[0]
        if version == 4:
            return dict(database_version=4, upgraded=False)
        if version not in (2, 3):
            raise OfficeError('Unsupported office workspace version.')
        raw = snapshot(store)
        digest = hashlib.sha256(raw).hexdigest()
        name = 'office-v%d-before-upgrade-' % version + digest + '.json'
        target = store.root / name
        if target.exists() or target.is_symlink():
            if read_file(target) != raw:
                raise OfficeError('Existing upgrade backup does not match; workspace unchanged.')
        else:
            write_new(target, raw)
        if read_file(target) != raw:
            raise OfficeError('Upgrade backup readback failed; workspace unchanged.')
        # Readback is not a directory-entry durability barrier. Sync both new
        # and reused backup names before changing the database; failure is retryable.
        with parent_directory(target) as (parent, _):
            os.fsync(parent)
        # Explicit BEGIN: sqlite DDL must share the version update transaction.
        store.db.execute('BEGIN IMMEDIATE')
        try:
            if version == 2:
                store.db.execute('ALTER TABLE conversations ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            store.db.execute('PRAGMA user_version=4')
            store.db.commit()
        except BaseException:
            store.db.rollback()
            raise
        return dict(database_version=4, upgraded=True, backup=name, sha256=digest)
