"""Office-owned project admission; never use native identity as project identity.

This module does not enable foundations, upgrade stores, select context or call a
provider. Schema creation and verified backup upgrade must precede its use.
"""
from __future__ import annotations

import re
import sqlite3
from uuid import uuid4

from .office_store import OfficeError, text_value

MAX_INSTRUCTION_BYTES = 12 * 1024
PROJECT_FIELDS = frozenset({'project_id', 'instructions', 'instruction_revision',
                            'memory_enabled', 'memory_revision'})
PROJECT_COLUMNS = ('project_id', 'instructions', 'instruction_revision',
                   'memory_enabled', 'memory_revision')
_PROJECT_ID = re.compile(r'project-[0-9a-f]{32}\Z')


def validate_project_metadata(value, *, expected_project_id=None):
    """Validate a closed logical project value and return a detached copy."""
    if not isinstance(value, dict) or set(value) != PROJECT_FIELDS:
        raise OfficeError('Invalid office project metadata.')
    project_id = value['project_id']
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        raise OfficeError('Invalid office project identity.')
    if expected_project_id is not None and project_id != expected_project_id:
        raise OfficeError('Office project identity does not match.')
    instructions = value['instructions']
    if instructions != '':
        text_value(instructions, MAX_INSTRUCTION_BYTES, 'Project instructions')
    elif not isinstance(instructions, str):
        raise OfficeError('Invalid project instructions.')
    for name in ('instruction_revision', 'memory_revision'):
        if type(value[name]) is not int or not 1 <= value[name] < 2**53:
            raise OfficeError('Invalid office project revision.')
    if type(value['memory_enabled']) is not bool:
        raise OfficeError('Invalid office memory preference.')
    return dict(value)


def read_project_metadata(store):
    """Read admitted schema-5 metadata only; no implicit upgrade or empty fallback."""
    with store.lock:
        version = store.db.execute('PRAGMA user_version').fetchone()[0]
        if version in (2, 3, 4):
            raise OfficeError('Explicit verified-backup upgrade is required for office foundations.', 409)
        if version != 5:
            raise OfficeError('Unsupported office foundation workspace version.')
        try:
            rows = store.db.execute('SELECT * FROM office_project LIMIT 2').fetchall()
        except sqlite3.Error as exc:
            raise OfficeError('Office project metadata is missing or unreadable.') from exc
        if len(rows) != 1 or set(rows[0].keys()) != PROJECT_FIELDS:
            raise OfficeError('Office project metadata must contain exactly one project.')
        value = dict(rows[0])
        if type(value['memory_enabled']) is not int or value['memory_enabled'] not in (0, 1):
            raise OfficeError('Invalid stored office memory preference.')
        value['memory_enabled'] = bool(value['memory_enabled'])
        return validate_project_metadata(value)


def create_project_schema(db, project=None):
    """Create the owned logical identity inside the caller's explicit transaction."""
    project = validate_project_metadata(project or dict(
        project_id='project-' + uuid4().hex, instructions='',
        instruction_revision=1, memory_enabled=False, memory_revision=1))
    db.execute('CREATE TABLE office_project(project_id TEXT PRIMARY KEY, instructions TEXT, '
               'instruction_revision INTEGER, memory_enabled INTEGER, memory_revision INTEGER)')
    db.execute('INSERT INTO office_project VALUES(?,?,?,?,?)',
               tuple(int(project[k]) if k == 'memory_enabled' else project[k] for k in PROJECT_COLUMNS))
    db.execute('CREATE TABLE office_summaries(cid TEXT PRIMARY KEY, revision INTEGER, content TEXT, refs TEXT)')
    return project


MEMORY_FILE = 'office-memory.v2.jsonl'


def validate_project_memory(records, project_id):
    """Validate original v2 records and normalized present supersession edges."""
    from .memory_migration import MemoryMigrationError, parse_v2_record, _store_payload
    if not isinstance(records, list):
        raise OfficeError('Invalid office project memory.')
    try:
        parsed = [parse_v2_record(r) for r in records]
        _store_payload(parsed)
    except (MemoryMigrationError, UnicodeError) as exc:
        raise OfficeError('Office project memory is invalid or exceeds its limit.') from exc
    by_id = {r['memory_id']: r for r in parsed}
    if len(by_id) != len(parsed) or any(r['workspace_id'] != project_id for r in parsed):
        raise OfficeError('Office memory identity is duplicate or belongs to another project.')
    edges = {key: set() for key in by_id}
    for record in parsed:
        mid = record['memory_id']
        for old in record['supersedes']:
            if old in by_id:
                target = by_id[old].get('superseded_by')
                if target is not None and target != mid:
                    raise OfficeError('Office memory replacement edges contradict.')
                edges[old].add(mid)
        new = record.get('superseded_by')
        if new in by_id:
            if mid not in by_id[new]['supersedes']:
                raise OfficeError('Office memory replacement edges contradict.')
            edges[mid].add(new)
    # Kahn's algorithm: no recursive walk of untrusted replacement chains.
    incoming = {key: 0 for key in edges}
    for targets in edges.values():
        for target in targets:
            incoming[target] += 1
    ready = [key for key, count in incoming.items() if not count]
    count = 0
    while ready:
        key = ready.pop()
        count += 1
        for target in edges[key]:
            incoming[target] -= 1
            if not incoming[target]:
                ready.append(target)
    if count != len(edges):
        raise OfficeError('Office memory replacement cycle is invalid.')
    return parsed


def read_project_memory(store, project_id):
    """Read only the fixed private office store; never silently swallow corruption."""
    import os
    import stat
    from .memory_migration import MemoryMigrationError, read_v2_store
    path = store.root / MEMORY_FILE
    try:
        info = path.lstat()
    except FileNotFoundError:
        return []
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            (os.name != 'nt' and stat.S_IMODE(info.st_mode) & 0o077)):
        raise OfficeError('Office memory must be a private unlinked regular file.')
    try:
        records = read_v2_store(store.root, path)
    except MemoryMigrationError as exc:
        raise OfficeError('Office memory could not be read safely.') from exc
    return validate_project_memory(records, project_id)


def validate_summary(value, *, messages, runs, active_ids):
    """Bind an accepted summary to original text ranges on one active branch."""
    import hashlib
    from .json_input import decode_json_bytes, JSONInputError
    from .office_store import encoded
    if (not isinstance(value, dict) or set(value) != {'cid', 'revision', 'content', 'refs'} or
            type(value['revision']) is not int or not 1 <= value['revision'] < 2**53):
        raise OfficeError('Invalid office continuity summary.')
    text_value(value['content'], MAX_INSTRUCTION_BYTES, 'Continuity summary')
    try:
        refs = decode_json_bytes(value['refs'].encode('utf-8'), 48 * 1024, 8, 4096)
    except (JSONInputError, UnicodeError, AttributeError) as exc:
        raise OfficeError('Invalid office summary source references.') from exc
    if not isinstance(refs, list) or not 1 <= len(refs) <= 100:
        raise OfficeError('Summary requires bounded original source references.')
    seen = set()
    for ref in refs:
        if (not isinstance(ref, dict) or set(ref) != {'message_id', 'run_id', 'start', 'end', 'sha256'} or
                any(type(ref[k]) is not int for k in ('message_id', 'start', 'end'))):
            raise OfficeError('Invalid summary source reference fields.')
        message = messages.get(ref['message_id'])
        run = runs.get(ref['run_id']) if isinstance(ref['run_id'], str) else None
        if (message is None or run is None or message['cid'] != value['cid'] or
                run['cid'] != value['cid'] or message['run_id'] != ref['run_id'] or
                run['status'] != 'completed' or ref['run_id'] not in active_ids or
                not 0 <= ref['start'] < ref['end'] <= len(message['content']) or
                hashlib.sha256(message['content'][ref['start']:ref['end']].encode('utf-8')).hexdigest() != ref['sha256']):
            raise OfficeError('Summary original text, branch, range or digest does not match.')
        key = encoded(ref)
        if key in seen:
            raise OfficeError('Duplicate summary source reference.')
        seen.add(key)
    return dict(value)


def update_project_metadata(store, project_id, **changes):
    """Compare-and-swap one explicitly saved project preference under its owner."""
    keys = set(changes)
    if keys not in ({'instructions', 'instruction_revision'}, {'memory_enabled', 'memory_revision'}):
        raise OfficeError('Save exactly one project preference with its expected revision.')
    with store.lock:
        project = read_project_metadata(store)
        if project['project_id'] != project_id:
            raise OfficeError('Office project identity does not match.', 409)
        revision = 'instruction_revision' if 'instructions' in keys else 'memory_revision'
        if type(changes[revision]) is not int or changes[revision] != project[revision]:
            raise OfficeError('Project preference changed. Preview and save its current revision.', 409)
        updated = dict(project, **changes)
        updated[revision] += 1
        validate_project_metadata(updated, expected_project_id=project_id)
        field = 'instructions' if revision == 'instruction_revision' else 'memory_enabled'
        with store.db:
            store.db.execute('UPDATE office_project SET ' + field + '=?, ' + revision + '=? WHERE project_id=?',
                             (updated[field], updated[revision], project_id))
        return updated
