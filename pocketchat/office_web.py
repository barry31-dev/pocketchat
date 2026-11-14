"""Literal-loopback browser adapter; no arbitrary filesystem or model routes."""
from __future__ import annotations

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
from .office_service import dispatch, exact, native_proof, publish_discovery, remove_discovery
from .office_store import OfficeError

MAX_BODY = 96 * 1024
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
ASSETS = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}


class OfficeServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, app, port):
        self.app = app
        self.native_token = secrets.token_urlsafe(32)
        self.discovery_bytes = None
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.cookie_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), OfficeHandler)
        self.origin = 'http://127.0.0.1:%d' % self.server_port
        self.cookie_name = 'pocketchat_%d' % self.server_port
        try:
            publish_discovery(self)
        except Exception:
            super().server_close()
            raise

    def server_close(self):
        remove_discovery(self)
        super().server_close()


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
                         'X-Office-Workspace': self.server.workspace_id,
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
        if self.path == '/api/native-proof':
            nonce = self.headers.get('X-Office-Nonce', '')
            if (self.command != 'GET' or getattr(self, 'native_proved', False) or
                    self.headers.get('Origin') is not None or self.headers.get('Sec-Fetch-Site') is not None or
                    self.headers.get('Authorization') is not None or
                    self.headers.get_all('X-Office-Workspace', []) != [self.server.workspace_id] or
                    self.headers.get_all('X-Office-Nonce', []) != [nonce] or
                    len(nonce) != 64 or any(c not in '0123456789abcdef' for c in nonce)):
                raise OfficeError('Native proof request refused.', 403)
            return self.path, None
        native = self.headers.get('Authorization') is not None
        if native:
            if (self.headers.get('Origin') is not None or self.headers.get('Sec-Fetch-Site') is not None or
                    self.headers.get_all('Authorization', []) != ['Bearer ' + self.server.native_token] or
                    self.headers.get_all('X-Office-Workspace', []) != [self.server.workspace_id]):
                raise OfficeError('Native client authentication refused.', 403)
        origins = self.headers.get_all('Origin', [])
        if origins and origins != [self.server.origin]:
            raise OfficeError('Origin refused.', 403)
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise OfficeError('Only local paths without query parameters are accepted.')
        if self.command == 'POST':
            if not native and origins != [self.server.origin]:
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
            if not native and not self._authenticated():
                raise OfficeError('Open the private launch link to connect.', 401)
            if not native and self.command == 'POST' and not hmac.compare_digest(
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
            if path == '/api/native-proof':
                self.protocol_version = 'HTTP/1.1'
                self.close_connection = False
                self.native_proved = True
                self.connection.settimeout(5)
                proof = native_proof(self.server.native_token, self.server.workspace_id, self.headers['X-Office-Nonce'])
                self._send(200, proof.encode('ascii'), 'text/plain', {'Connection': 'keep-alive'})
                return
            if getattr(self, 'native_proved', False):
                self.close_connection = True
            if self.command == 'POST' and path == '/api/login':
                exact(body, ['token'])
                if not isinstance(body['token'], str) or not hmac.compare_digest(body['token'], self.server.bootstrap_token):
                    raise OfficeError('Launch token refused.', 401)
                self._send(200, {'csrf': self.server.csrf_token}, headers={
                    'Set-Cookie': '%s=%s; HttpOnly; SameSite=Strict; Path=/' % (self.server.cookie_name, self.server.cookie_token)})
                return
            status, value, mime, headers = dispatch(app, path, self.command, body)
            if path == '/api/bootstrap':
                value['csrf'] = self.server.csrf_token
            self._send(status, value, mime, headers)
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
