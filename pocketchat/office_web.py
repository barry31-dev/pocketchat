"""Literal-loopback browser adapter; no arbitrary filesystem or model routes."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .config import ConfigError, load_api_key, load_config, profile_config, requires_api_key
from .json_input import JSONInputError, decode_json_bytes
from .office import OfficeWorkspace
from .office_docx import render_docx
from .office_store import OfficeError

MAX_BODY = 96 * 1024
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
ASSETS = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}


def exact(body, fields):
    if not isinstance(body, dict) or set(body) != set(fields):
        raise OfficeError('Invalid request fields.')
    return body


class OfficeServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, app, port):
        self.app = app
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.cookie_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), OfficeHandler)
        self.origin = 'http://127.0.0.1:%d' % self.server_port
        self.cookie_name = 'pocketchat_%d' % self.server_port


class OfficeHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, *args):
        pass  # URLs, tokens, local paths and user text never enter access logs.

    def _send(self, status, value, content_type='application/json', headers=None):
        data = json.dumps(value, ensure_ascii=False).encode('utf-8') if content_type == 'application/json' else value
        self.send_response(status)
        for key, val in {'Content-Type': content_type, 'Content-Length': str(len(data)),
                         'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                         'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': CSP,
                         'Cross-Origin-Resource-Policy': 'same-origin',
                         **(headers or {})}.items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(data)

    def _authenticated(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie[self.server.cookie_name].value if self.server.cookie_name in cookie else ''
        except Exception:
            token = ''
        return hmac.compare_digest(token, self.server.cookie_token)

    def _admit(self):
        if self.headers.get_all('Host', []) != [urlsplit(self.server.origin).netloc]:
            raise OfficeError('Host refused.', 403)
        origins = self.headers.get_all('Origin', [])
        if origins and origins != [self.server.origin]:
            raise OfficeError('Origin refused.', 403)
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise OfficeError('Only local paths without query parameters are accepted.')
        if self.command == 'POST':
            if origins != [self.server.origin]:
                raise OfficeError('A same-origin request is required.', 403)
            if self.headers.get('Transfer-Encoding') is not None:
                raise OfficeError('Transfer encoding is not supported.')
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise OfficeError('One bounded content length is required.')
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                raise OfficeError('Request exceeds its size limit.', 413)
            if self.headers.get('Content-Type') != 'application/json':
                raise OfficeError('JSON content type is required.', 415)
            self.connection.settimeout(5)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise OfficeError('Incomplete request body.')
            try:
                body = decode_json_bytes(raw, MAX_BODY, 12, 4000)
            except JSONInputError as exc:
                raise OfficeError('Invalid bounded JSON.') from exc
        else:
            body = None
        if parsed.path == '/api/login' and self.command == 'POST':
            return parsed.path, body
        if parsed.path not in ASSETS:
            if not self._authenticated():
                raise OfficeError('Open the private launch link to connect.', 401)
            if self.command == 'POST' and not hmac.compare_digest(
                    self.headers.get('X-CSRF-Token', ''), self.server.csrf_token):
                raise OfficeError('Request confirmation token refused.', 403)
        return parsed.path, body

    def _handle(self):
        try:
            path, body = self._admit()
            app = self.server.app
            if self.command == 'GET' and path in ASSETS:
                file = Path(__file__).parent / 'office_static' / ASSETS[path]
                content_type = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/style.css': 'text/css; charset=utf-8'}[path]
                self._send(200, file.read_bytes(), content_type)
                return
            if self.command == 'POST' and path == '/api/login':
                exact(body, ['token'])
                if not isinstance(body['token'], str) or not hmac.compare_digest(body['token'], self.server.bootstrap_token):
                    raise OfficeError('Launch token refused.', 401)
                self._send(200, {'csrf': self.server.csrf_token}, headers={
                    'Set-Cookie': '%s=%s; HttpOnly; SameSite=Strict; Path=/' % (self.server.cookie_name, self.server.cookie_token)})
                return
            if path == '/api/bootstrap' and self.command == 'GET':
                self._send(200, dict(csrf=self.server.csrf_token, model=app.config['model'],
                                    provider=app.config['base_url'], max_source_bytes=12*1024))
                return
            if path == '/api/conversations':
                if self.command == 'GET':
                    self._send(200, {'items': app.store.list()})
                else:
                    self._send(201, app.store.create(exact(body, ['title'])['title']))
                return
            parts = path.strip('/').split('/')
            if len(parts) >= 3 and parts[:2] == ['api', 'conversations']:
                cid = parts[2]
                if len(parts) == 3 and self.command == 'GET':
                    self._send(200, app.store.get(cid))
                    return
                if len(parts) == 4 and self.command == 'POST':
                    if parts[3] == 'rename':
                        self._send(200, app.store.rename(cid, exact(body, ['title'])['title']))
                        return
                    if parts[3] == 'sources':
                        exact(body, ['name', 'text'])
                        self._send(201, app.store.add_source(cid, body['name'], body['text']))
                        return
                    if parts[3] == 'runs':
                        self._send(202, app.submit(cid, body))
                        return
                if len(parts) == 6 and parts[3] == 'artifacts' and self.command == 'GET':
                    artifact = app.store.artifact(cid, parts[4])
                    if parts[5] == 'view':
                        self._send(200, artifact)
                        return
                    if parts[5] == 'docx':
                        data = render_docx(artifact['content'], artifact['sources'])
                        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                    elif parts[5] == 'md':
                        data = artifact['content'].encode('utf-8')
                        mime = 'text/markdown; charset=utf-8'
                    else:
                        raise OfficeError('Not found.', 404)
                    self._send(200, data, mime, {'Content-Disposition': 'attachment; filename="report-%s.%s"' % (parts[4][:8], parts[5]), 'X-Content-SHA256': hashlib.sha256(data).hexdigest()})
                    return
            if len(parts) in (3, 4) and parts[:2] == ['api', 'runs']:
                if len(parts) == 3 and self.command == 'GET':
                    self._send(200, app.status(parts[2]))
                    return
                if len(parts) == 4 and parts[3] == 'stop' and self.command == 'POST':
                    exact(body, [])
                    self._send(200, app.cancel(parts[2]))
                    return
            raise OfficeError('Not found.', 404)
        except OfficeError as exc:
            self._send(exc.status, {'error': str(exc)})
        except (OSError, ValueError):
            self._send(400, {'error': 'Request could not be read or completed.'})
        except Exception:
            self._send(500, {'error': 'Workspace operation failed. No success is implied.'})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()


def create_server(app, port=0):
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise OfficeError('Invalid port.')
    return OfficeServer(app, port)


def run_web(args):
    app = server = None
    try:
        config = profile_config(load_config(args.config), args.profile)
        if requires_api_key(config) and not load_api_key(config):
            raise OfficeError('Configured provider credential is not available.')
        app = OfficeWorkspace(args.data_dir, config)
        server = create_server(app, args.port)
        print('PocketChat local workspace: %s/#%s' % (server.origin, server.bootstrap_token), flush=True)
        print('Keep this launch link private. Press Ctrl+C to stop.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OfficeError, ConfigError, OSError) as exc:
        print('Cannot start local workspace: %s' % exc)
        return 2
    finally:
        if server:
            server.server_close()
        if app:
            app.close()
    return 0
