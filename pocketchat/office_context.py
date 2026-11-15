"""Office-owned project admission; never use native identity as project identity.

This module does not enable foundations, upgrade stores, select context or call a
provider. Schema creation and verified backup upgrade must precede its use.
"""
from __future__ import annotations

import re
import sqlite3

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
