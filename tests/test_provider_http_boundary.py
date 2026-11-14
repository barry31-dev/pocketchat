"""Real loopback framing failures, not real-provider behavior."""
import json
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from pocketchat.provider import ProviderError, complete
from pocketchat.session import Session
from tests import _helpers as hp


class ProviderHttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.mode = "fixed"
        self.requests = []
        case = self
        self.body = json.dumps({
            "choices": [{"message": {"content": "complete reply"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                case.requests.append(json.loads(self.rfile.read(length)))
                self.close_connection = True
                if case.mode == "disconnect":
                    return
                body = b"not json" if case.mode == "malformed" else case.body
                self.send_response(401 if case.mode == "short_error" else 200)
                self.send_header("Content-Type", "application/json")
                if case.mode in ("chunked", "broken_chunk"):
                    self.send_header("Transfer-Encoding", "chunked")
                elif case.mode != "close_delimited":
                    self.send_header("Content-Length", str(
                        len(body) + (20 if case.mode in ("short", "short_error") else 0)))
                self.end_headers()
                if case.mode in ("chunked", "broken_chunk"):
                    self.wfile.write(b"%x\r\n" % len(body) + body + b"\r\n")
                    self.wfile.write(b"not-a-size-secret\r\n" if case.mode == "broken_chunk"
                                     else b"0\r\n\r\n")
                else:
                    self.wfile.write(body)
                self.wfile.flush()

            def log_message(self, *args):
                pass

        self.stub = hp.StubHTTPServer(Handler)
        self.addCleanup(self.stub.close)
        self.config = {"base_url": "http://127.0.0.1:%d/v1" % self.stub.port,
                       "model": "m", "api_key_env": "K", "timeout_seconds": 5,
                       "max_context_chars": 1000, "summary_prefix_chars": 40}

    def call(self):
        return complete([{"role": "user", "content": "hi"}], self.config,
                        "not-a-size-secret")

    def test_short_valid_json_is_incomplete_not_success(self):
        self.mode = "short"
        with self.assertRaises(ProviderError) as failure:
            self.call()
        self.assertEqual(failure.exception.error_type, "incomplete_response")
        self.assertEqual(failure.exception.status_code, 200)
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(len(self.requests), 1)

    def test_broken_chunk_is_incomplete_without_remote_details(self):
        self.mode = "broken_chunk"
        with self.assertRaises(ProviderError) as failure:
            self.call()
        self.assertEqual(failure.exception.error_type, "incomplete_response")
        self.assertTrue(failure.exception.retryable)
        self.assertNotIn("not-a-size-secret", str(failure.exception))
        self.assertEqual(len(self.requests), 1)

    def test_disconnect_before_headers_is_retryable_connection_failure(self):
        self.mode = "disconnect"
        with self.assertRaises(ProviderError) as failure:
            self.call()
        self.assertEqual(failure.exception.error_type, "connection_error")
        self.assertIsNone(failure.exception.status_code)
        self.assertTrue(failure.exception.retryable)
        self.assertEqual(len(self.requests), 1)

    def test_session_records_each_transport_failure_and_terminates(self):
        for mode, error_type in (("short", "incomplete_response"),
                                 ("broken_chunk", "incomplete_response"),
                                 ("disconnect", "connection_error")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                self.mode = mode
                trace = Path(directory) / "run.jsonl"
                session = Session(self.config, trace, "run_" + mode, api_key="not-a-size-secret")
                self.assertEqual(session.run(["hi"]), "failed")
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                self.assertNotIn("model.response.completed", [e["type"] for e in events])
                failure = next(e for e in events if e["type"] == "model.response.failed")
                self.assertEqual(failure["payload"]["error"]["type"], error_type)
                self.assertIs(failure["payload"]["retryable"], True)
                self.assertEqual(events[-1]["type"], "run.failed")
                self.assertNotIn("not-a-size-secret", trace.read_text())
                self.assertEqual(len(session.request_snapshots), 1)

    def test_complete_fixed_chunked_and_close_delimited_json_stay_valid(self):
        for mode in ("fixed", "chunked", "close_delimited"):
            with self.subTest(mode=mode):
                self.mode = mode
                response = self.call()
                self.assertEqual(response["content"], "complete reply")
                self.assertEqual(response["usage"]["input_tokens"], 3)
                self.assertEqual(response["finish_reason"], "stop")

    def test_complete_invalid_json_remains_nonretryable(self):
        self.mode = "malformed"
        with self.assertRaises(ProviderError) as failure:
            self.call()
        self.assertEqual(failure.exception.error_type, "malformed_response")
        self.assertFalse(failure.exception.retryable)

    def test_short_http_error_keeps_status_semantics(self):
        self.mode = "short_error"
        with self.assertRaises(ProviderError) as failure:
            self.call()
        self.assertEqual(failure.exception.error_type, "unauthorized")
        self.assertEqual(failure.exception.status_code, 401)
        self.assertFalse(failure.exception.retryable)


class ProviderBodyResetTests(unittest.TestCase):
    def test_body_reset_uses_configured_retry_with_separate_evidence(self):
        """Synchronize a real TCP RST after response headers, without stubbing I/O."""
        import http.client
        import socket
        import struct
        import threading
        from unittest import mock

        reading = threading.Event()
        stopped = threading.Event()
        requests = []
        server_errors = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        listener.settimeout(0.1)
        self.addCleanup(listener.close)
        body = b'{"choices":[{"message":{"content":"recovered"},"finish_reason":"stop"}]}'

        def serve():
            try:
                while len(requests) < 2 and not stopped.is_set():
                    try:
                        connection, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(3)
                        incoming = b""
                        while b"\r\n\r\n" not in incoming:
                            part = connection.recv(4096)
                            if not part:
                                raise RuntimeError("request headers ended early")
                            incoming += part
                        headers, received = incoming.split(b"\r\n\r\n", 1)
                        length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                                      if line.lower().startswith(b"content-length:"))
                        while len(received) < length:
                            part = connection.recv(4096)
                            if not part:
                                raise RuntimeError("request body ended early")
                            received += part
                        requests.append(json.loads(received))
                        first = len(requests) == 1
                        size = len(body) + (20 if first else 0)
                        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                           b"Content-Length: %d\r\nConnection: close\r\n\r\n" % size + body)
                        if first:
                            if not reading.wait(3):
                                raise RuntimeError("client never began response body read")
                            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                  struct.pack("ii", 1, 0))
            except Exception as exc:
                server_errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        def stop():
            stopped.set()
            thread.join(4)
        self.addCleanup(stop)
        original_read = http.client.HTTPResponse.read
        raw_errors = []
        def read(response, *args, **kwargs):
            reading.set()
            try:
                return original_read(response, *args, **kwargs)
            except ConnectionResetError:
                raw_errors.append("ConnectionResetError")
                raise

        config = {"base_url": "http://127.0.0.1:%d/v1" % listener.getsockname()[1],
                  "model": "m", "api_key_env": "K", "timeout_seconds": 3,
                  "max_context_chars": 1000, "summary_prefix_chars": 40,
                  "retry_max_attempts": 2, "retry_backoff_ms": 0}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
                http.client.HTTPResponse, "read", read):
            trace = Path(directory) / "run.jsonl"
            session = Session(config, trace, "run_rst", api_key="synthetic-secret")
            status = session.run(["hi"])
            events = [json.loads(line) for line in trace.read_text().splitlines()]
            failures = [e for e in events if e["type"] == "model.response.failed"]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["payload"]["error"]["type"], "incomplete_response")
            self.assertIs(failures[0]["payload"]["retryable"], True)
            self.assertEqual(raw_errors, ["ConnectionResetError"])
            self.assertEqual(status, "completed")
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(session.request_snapshots), 2)
            self.assertEqual([e["type"] for e in events].count("retry.attempt"), 1)
            self.assertEqual([e["type"] for e in events].count("model.response.completed"), 1)
            self.assertEqual(events[-1]["type"], "run.finished")
            self.assertNotIn("synthetic-secret", trace.read_text())
        stop()
        self.assertFalse(thread.is_alive())
        self.assertEqual(server_errors, [])

    def test_tls_body_eof_is_incomplete_but_timeout_keeps_its_class(self):
        import socket
        import ssl
        from unittest import mock
        config = {"base_url": "http://127.0.0.1/v1", "model": "m"}
        for error, expected in ((ssl.SSLEOFError(8, "peer-secret"), "incomplete_response"),
                                (socket.timeout("peer-secret"), "timeout")):
            with self.subTest(expected=expected):
                response = mock.MagicMock()
                response.status = 200
                response.read.side_effect = error
                with mock.patch("urllib.request.urlopen") as opener:
                    opener.return_value.__enter__.return_value = response
                    with self.assertRaises(ProviderError) as failure:
                        complete([{"role": "user", "content": "hi"}], config, "k")
                self.assertEqual(failure.exception.error_type, expected)
                self.assertTrue(failure.exception.retryable)
                self.assertNotIn("peer-secret", str(failure.exception))
