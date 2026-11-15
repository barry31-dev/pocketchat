"""One office operation surface, with an authenticated loopback client adapter."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from .json_input import JSONInputError, decode_json_bytes
from .office import OfficeWorkspace
from .office_backup import MAX_BACKUP_BYTES, read_file, snapshot, upgrade, write_new
from .office_docx import render_docx
from .office_store import OfficeError, encoded

DISCOVERY = 'office-service.json'
DISCOVERY_SCHEMA = 'pocketchat-office-service.v1'


def exact(body, fields):
    if not isinstance(body, dict) or set(body) != set(fields):
        raise OfficeError('Invalid request fields.')
    return body


def workspace_id(root):
    root = Path(os.path.abspath(str(root)))
    for path in (root, *root.parents):
        if path.is_symlink():
            raise OfficeError('Workspace paths must not contain symbolic links.')
    info = root.stat()
    return hashlib.sha256(encoded([str(root), info.st_dev, info.st_ino]).encode()).hexdigest()


def native_proof(token, workspace, nonce):
    return hmac.new(token.encode('ascii'), ('office-native-v1:' + workspace + ':' + nonce).encode('ascii'), hashlib.sha256).hexdigest()


def discovery_available():
    return hasattr(os, 'O_NOFOLLOW') and all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.unlink, os.rmdir))


def publish_discovery(server):
    server.workspace_id = workspace_id(server.app.root)
    server.discovery_bytes = None
    if not discovery_available():
        return  # The existing browser is still usable on unsupported native I/O runtimes.
    path = server.app.root / DISCOVERY
    if path.exists() or path.is_symlink():
        prior = decode_json_bytes(read_file(path, 4096), 4096, 4, 32)
        if not isinstance(prior, dict) or prior.get('schema_version') != DISCOVERY_SCHEMA:
            raise OfficeError('Unrecognized service discovery file; inspect it before starting.')
        path.unlink()  # The app already owns the workspace's exclusive lock.
    raw = encoded(dict(schema_version=DISCOVERY_SCHEMA, workspace_id=server.workspace_id,
                       origin=server.origin, token=server.native_token)).encode()
    write_new(path, raw)
    server.discovery_bytes = raw


def remove_discovery(server):
    if server.discovery_bytes is None:
        return
    path = server.app.root / DISCOVERY
    try:
        if read_file(path, 4096) == server.discovery_bytes:
            path.unlink()
    except (OfficeError, OSError):
        pass  # Never unlink a replaced/unknown file.
    server.discovery_bytes = None


def dispatch(app, path, method, body=None):
    """Business routing shared by direct CLI and HTTP; never accept host paths."""
    headers = {}
    mime = 'application/json'
    def result(value, status=200, content_type=mime, extra=None):
        return status, value, content_type, extra or headers
    if path == '/api/bootstrap' and method == 'GET':
        return result(dict(model=app.config.get('model'), provider=app.config.get('base_url'),
                           max_source_bytes=12 * 1024, workspace_id=workspace_id(app.root),
                           database_version=app.store.db.execute('PRAGMA user_version').fetchone()[0]))
    if path == '/api/project' and method == 'GET':
        from .office_context import read_project_metadata
        return result(read_project_metadata(app.store))
    if path in ('/api/project/instructions', '/api/project/memory') and method == 'POST':
        from .office_context import update_project_metadata
        fields = ['project_id', 'instruction_revision', 'instructions'] if path.endswith('/instructions') else ['project_id', 'memory_revision', 'memory_enabled']
        exact(body, fields)
        with app.guard:
            changes = {key: value for key, value in body.items() if key != 'project_id'}
            return result(update_project_metadata(app.store, body['project_id'], **changes))
    if path == '/api/memory' and method == 'GET':
        from .office_context import office_memory_view
        return result(office_memory_view(app.store))
    if path == '/api/memory/recall' and method == 'POST':
        from .office_context import office_memory_view
        exact(body, ['query'])
        return result(office_memory_view(app.store, body['query']))
    if path.startswith('/api/memory/') and method == 'POST':
        from .office_context import office_memory_action
        if not isinstance(body, dict) or not {'project_id', 'store_sha256'} <= set(body):
            raise OfficeError('Invalid memory mutation fields.')
        with app.guard:
            return result(office_memory_action(app.store, path[len('/api/memory/'):],
                body['project_id'], body['store_sha256'], **{k: v for k, v in body.items() if k not in ('project_id', 'store_sha256')}))
    if path == '/api/models':
        if method == 'GET':
            return result(app.models())
        return result(app.select_model(exact(body, ['profile'])['profile']))
    if path == '/api/cli-state':
        with app.guard:
            if method == 'POST': exact(body, ['revision','current','drafts','pending'])
            return result(app.store.cli_state(body if method == 'POST' else None))
    if path == '/api/upgrade' and method == 'POST':
        exact(body, [])
        with app.guard:
            return result(upgrade(app.store))
    if path == '/api/search' and method == 'POST':
        exact(body, ['query', 'archived'])
        return result(app.store.search(body['query'], body['archived']))
    if path == '/api/conversations/archived' and method == 'GET':
        return result({'items': app.store.list(archived=True)})
    if path == '/api/backup' and method == 'POST':
        exact(body, [])
        with app.guard:
            return result(snapshot(app.store), content_type='application/octet-stream')
    if path == '/api/conversations':
        if method == 'GET':
            return result({'items': app.store.list()})
        return result(app.store.create(exact(body, ['title'])['title']), 201)
    parts = path.strip('/').split('/')
    if len(parts) >= 3 and parts[:2] == ['api', 'conversations']:
        cid = parts[2]
        if len(parts) == 3 and method == 'GET':
            return result(app.store.get(cid))
        if len(parts) == 5 and parts[3] == 'requests' and method == 'GET':
            with app.guard, app.store.lock:
                app.store._row('conversations', cid)
                from .office_store import identifier
                row = app.store.db.execute('SELECT id FROM runs WHERE cid=? AND request_id=?', (cid, identifier(parts[4]))).fetchone()
                if row is None: raise OfficeError('Request receipt not found. Its outcome remains unknown.', 404)
                return result(app.status(row['id']))
        if len(parts) == 4 and method == 'POST':
            if parts[3] == 'archive':
                with app.guard:
                    return result(app.store.archive(cid, exact(body, ['archived'])['archived']))
            if parts[3] == 'rename':
                return result(app.store.rename(cid, exact(body, ['title'])['title']))
            if parts[3] == 'sources':
                exact(body, ['name', 'text'])
                return result(app.store.add_source(cid, body['name'], body['text']), 201)
            if parts[3] == 'preview':
                return result(app.preview(cid, body))
            if parts[3] == 'confirmed':
                exact(body, ['message', 'confirmation'])
                return result(app.submit_confirmed(cid, body['message'], body['confirmation']), 202)
            if parts[3] == 'runs':
                return result(app.submit(cid, body), 202)
        if len(parts) == 6 and parts[3] == 'artifacts' and method == 'GET':
            artifact = app.store.artifact(cid, parts[4])
            if parts[5] == 'view':
                return result(artifact)
            if parts[5] == 'docx':
                raw = render_docx(artifact['content'], artifact['sources'])
                mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            elif parts[5] == 'md':
                raw = artifact['content'].encode('utf-8')
                mime = 'text/markdown; charset=utf-8'
            else:
                raise OfficeError('Not found.', 404)
            return result(raw, content_type=mime, extra={
                'Content-Disposition': 'attachment; filename="report-%s.%s"' % (parts[4][:8], parts[5]),
                'X-Content-SHA256': hashlib.sha256(raw).hexdigest()})
    if len(parts) in (3, 4) and parts[:2] == ['api', 'runs']:
        if len(parts) == 3 and method == 'GET':
            return result(app.status(parts[2]))
        if len(parts) == 4 and parts[3] == 'stop' and method == 'POST':
            exact(body, [])
            return result(app.cancel(parts[2]))
    raise OfficeError('Not found.', 404)


class OfficeConnection:
    """Own the lock, or connect to its current owner. Never auto-start a server."""
    def __init__(self, root, config=None):
        self.app = None
        self.remote = False
        self.discovery = None
        try:
            self.app = OfficeWorkspace(root, config)
        except OfficeError as exc:
            if exc.status != 409:
                raise
            self.remote = True
            try:
                path = Path(root) / DISCOVERY
                if os.name != 'nt' and path.lstat().st_mode & 0o077:
                    raise OfficeError('Service discovery must be private.')
                d = decode_json_bytes(read_file(path, 4096), 4096, 4, 32)
                if not isinstance(d, dict) or set(d) != {'schema_version', 'workspace_id', 'origin', 'token'}:
                    raise OfficeError('Invalid service discovery.')
                if d['schema_version'] != DISCOVERY_SCHEMA or d['workspace_id'] != workspace_id(root):
                    raise OfficeError('Service does not belong to this workspace.')
                if not all(isinstance(value, str) for value in d.values()):
                    raise OfficeError('Invalid service discovery field types.')
                url = urlsplit(d['origin'])
                if (url.scheme != 'http' or url.hostname != '127.0.0.1' or
                        url.username or url.password or url.path or url.query or url.fragment or
                        not url.port or d['origin'] != 'http://127.0.0.1:%d' % url.port or
                        not isinstance(d['token'], str) or not 32 <= len(d['token']) <= 128 or not d['token'].isascii()):
                    raise OfficeError('Only the exact local office endpoint is accepted.')
                self.discovery = d
                self.request('/api/bootstrap')  # Authenticate and verify workspace binding before any operation.
            except (OSError, ValueError, http.client.HTTPException, JSONInputError) as error:
                raise OfficeError('Workspace owner is unavailable or untrusted. Reconnect; do not replay an unknown request.', 409) from error

    def request(self, path, body=None):
        method = 'GET' if body is None else 'POST'
        if self.app:
            return dispatch(self.app, path, method, body)[1]
        d = self.discovery
        conn = http.client.HTTPConnection('127.0.0.1', urlsplit(d['origin']).port, timeout=10)
        try:
            nonce = secrets.token_hex(32)
            conn.request('GET', '/api/native-proof', headers={
                'X-Office-Workspace': d['workspace_id'], 'X-Office-Nonce': nonce})
            proof_response = conn.getresponse()
            proof = proof_response.read(4097)
            expected = native_proof(d['token'], d['workspace_id'], nonce).encode('ascii')
            if (proof_response.status != 200 or proof_response.will_close or len(proof) != 64 or
                    not hmac.compare_digest(proof, expected) or conn.sock is None):
                raise OfficeError('The workspace listener could not prove its identity.', 409)
            # No bearer or user content precedes the proof. The authenticated
            # socket must carry the actual operation; never reconnect to the port.
            conn.auto_open = 0
            raw = None if body is None else encoded(body).encode('utf-8')
            headers = {'Authorization': 'Bearer ' + d['token'], 'X-Office-Workspace': d['workspace_id']}
            if raw is not None:
                headers['Content-Type'] = 'application/json'
            conn.request(method, path, raw, headers)
            response = conn.getresponse()
            if not hmac.compare_digest(response.getheader('X-Office-Workspace', ''), d['workspace_id']):
                raise OfficeError('Workspace response identity mismatch.', 409)
            payload = response.read(MAX_BACKUP_BYTES + 1)
            if len(payload) > MAX_BACKUP_BYTES:
                raise OfficeError('Workspace response exceeds its limit.')
            if response.getheader('Content-Type') == 'application/json':
                value = decode_json_bytes(payload, MAX_BACKUP_BYTES, 24, 1000000)
            else:
                value = payload
            if response.status >= 300:
                raise OfficeError(value.get('error', 'Office request refused.') if isinstance(value, dict) else 'Office request refused.', response.status, request_not_accepted=isinstance(value,dict) and value.get('request_not_accepted') is True)
            return value
        except (OSError, http.client.HTTPException, JSONInputError) as exc:
            raise OfficeError('Connection lost. Request outcome is unknown; recover its receipt before sending again.', 409) from exc
        finally:
            conn.close()

    def close(self):
        if self.app:
            self.app.close()
            self.app = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
