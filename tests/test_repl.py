"""Interactive-loop tests: the REPL as a prompt source, never a trace path.

The property under test is the one the W7 brief calls out: no entry point may
produce a run that starts and never ends. The REPL is the second entry point,
so every test here ends a conversation some way other than a clean exit --
EOF, an exit word, a Ctrl-C while a line is being read, and a Ctrl-C while a
request is in flight -- and then checks that the trace still carries exactly
one terminal event.

The stub server is a real local socket. No test reaches a provider and no
credential is real, so nothing here says anything about a real provider.
"""

from __future__ import annotations

import json
import signal
import os
import pty
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StubState:
    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self.delay = 0.0


STATE = StubState()


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        STATE.requests.append(json.loads(raw.decode("utf-8")) if raw else {})
        if STATE.delay:
            time.sleep(STATE.delay)
        if STATE.mode == "ok":
            self._send(200, json.dumps({
                "id": "cmpl-stub",
                "model": "example-model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello from the stub"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5},
            }).encode("utf-8"))
        elif STATE.mode == "http_error":
            self._send(500, b'{"error": {"message": "boom"}}')
        else:
            self._send(200, b"{}")

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ReplTests(unittest.TestCase):
    def setUp(self):
        STATE.mode = "ok"
        STATE.delay = 0.0
        STATE.requests.clear()
        # One server per test, not one shared by the module. Each test spawns a
        # real subprocess against it, so a request from a previous test can
        # still be arriving when the next one starts counting. Sharing the
        # server and its request log across tests made the counts order
        # dependent: the same file passed alone and failed in a full run.
        self.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda s=self.server: s.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._stop_server)
        self.base = Path(self.temp.name)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _config(self, **overrides):
        provider = {
            "base_url": "http://127.0.0.1:%d/v1" % self.port,
            "model": "example-model",
            "api_key_env": "POCKETCHAT_API_KEY",
        }
        provider.update(overrides.pop("provider", {}))
        config = {"schema_version": "1", "provider": provider}
        config.update(overrides)
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def _chat(self, stdin_text, config, timeout=120, send_signal=None,
              signal_after=0.0):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_API_KEY": "test-key-not-real"}
        cmd = [sys.executable, "-m", "pocketchat", "chat", "--config", str(config)]
        proc = subprocess.Popen(cmd, cwd=str(self.base), env=environ,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        if send_signal is not None:
            def fire():
                time.sleep(signal_after)
                proc.send_signal(send_signal)
            threading.Thread(target=fire, daemon=True).start()
        try:
            out, err = proc.communicate(stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            self.fail("chat did not exit within %ss" % timeout)
        return proc.returncode, out, err

    def _trace(self):
        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1, "expected exactly one trace file")
        return [json.loads(line) for line in
                traces[0].read_text(encoding="utf-8").splitlines() if line.strip()]

    def _types(self, events):
        return [event["type"] for event in events]

    def _chat_in_tty(self, config, interrupt_after=1.5, timeout=60):
        """Run ``chat`` on a real terminal and interrupt it mid-readline.

        The tty is what makes this test mean anything: stdin stays open, so the
        process is actually parked inside ``readline()`` when the signal lands.
        Returns the same (returncode, stdout, stderr) triple as ``_chat``.
        """
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "TERM": "dumb", "POCKETCHAT_API_KEY": "test-key-not-real"}
        cmd = [sys.executable, "-m", "pocketchat", "chat", "--config", str(config)]
        primary, secondary = pty.openpty()
        proc = subprocess.Popen(cmd, cwd=str(self.base), env=environ,
                                stdin=secondary, stdout=secondary, stderr=secondary,
                                close_fds=True)
        os.close(secondary)
        # A terminal with no input never reaches EOF, so the child can sit in
        # readline() indefinitely. Reading it with a blocking os.read() would
        # hang this test forever if the child misbehaved, so the fd is polled
        # and aborted against a hard deadline: a broken run must fail fast.
        os.set_blocking(primary, False)
        deadline = time.time() + timeout

        def fire():
            time.sleep(interrupt_after)
            proc.send_signal(signal.SIGINT)

        threading.Thread(target=fire, daemon=True).start()
        chunks = []

        def read_available():
            try:
                return os.read(primary, 4096)
            except (BlockingIOError, InterruptedError):
                return None
            except OSError:
                return b""

        try:
            while True:
                if time.time() > deadline:
                    self.fail("chat did not exit within %ss" % timeout)
                data = read_available()
                if not data:
                    if proc.poll() is not None and data == b"":
                        break
                    if data == b"":
                        break
                    time.sleep(0.05)
                    continue
                chunks.append(data)
                if proc.poll() is not None:
                    # Drain whatever is left before the fd closes.
                    try:
                        while True:
                            data = os.read(primary, 4096)
                            if not data:
                                break
                            chunks.append(data)
                    except OSError:
                        pass
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=timeout)
            try:
                os.close(primary)
            except OSError:
                pass
        output = b"".join(chunks).decode("utf-8", "replace")
        return proc.returncode, output, ""

    # --- the REPL is a prompt source, not a trace path -----------------

    def test_piped_lines_become_turns_of_one_run(self):
        config = self._config()
        code, out, err = self._chat("first\nsecond\n/exit\n", config)
        self.assertEqual(code, 0, out + err)
        # One run, not one run per line: the whole conversation is a single
        # run.started / run.finished pair.
        events = self._trace()
        self.assertEqual(self._types(events).count("run.started"), 1)
        self.assertEqual(self._types(events)[-1], "run.finished")
        self.assertEqual(self._types(events).count("model.request.started"), 2)
        self.assertEqual(len(STATE.requests), 2)

    def test_piped_input_carries_context_across_lines(self):
        config = self._config()
        self._chat("first\nsecond\n/exit\n", config)
        self.assertEqual([m["role"] for m in STATE.requests[1]["messages"]],
                         ["user", "assistant", "user"])

    def test_eof_ends_the_run_as_completed(self):
        # Ctrl-D at the prompt is a clean end, not a failure.
        config = self._config()
        code, out, err = self._chat("only line\n", config)
        self.assertEqual(code, 0, out + err)
        events = self._trace()
        self.assertEqual(self._types(events)[-1], "run.finished")
        self.assertEqual(events[-1]["payload"]["status"], "completed")
        self.assertEqual(events[-1]["payload"]["turns"], 1)

    def test_no_input_at_all_still_ends_the_run(self):
        # The degenerate case: a conversation that never started must not
        # leave a trace with a start and no end.
        config = self._config()
        code, out, err = self._chat("", config)
        self.assertEqual(code, 0, out + err)
        events = self._trace()
        self.assertEqual(self._types(events), ["run.started", "run.finished"])
        self.assertEqual(events[-1]["payload"]["turns"], 0)

    def test_exit_word_ends_the_conversation(self):
        config = self._config()
        code, out, err = self._chat("hi\n/quit\n", config)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(STATE.requests), 1)
        self.assertEqual(self._types(self._trace())[-1], "run.finished")

    def test_empty_lines_are_not_prompts(self):
        config = self._config()
        self._chat("\n\n\nreal prompt\n/exit\n", config)
        self.assertEqual(len(STATE.requests), 1)

    def test_provider_failure_ends_the_run_as_failed(self):
        STATE.mode = "http_error"
        config = self._config()
        code, out, err = self._chat("hi\n/exit\n", config)
        self.assertEqual(code, 1, out + err)
        events = self._trace()
        self.assertEqual(self._types(events), [
            "run.started",
            "model.request.started",
            "model.response.failed",
            "run.failed",
        ])

    # --- the interruption paths ----------------------------------------

    def test_ctrl_c_while_a_line_is_being_read_ends_the_run(self):
        # Nothing is in flight: the interrupt lands while the REPL waits for
        # input. The conversation still ends as cancelled, because an
        # interrupt at the prompt means the user is done, not that this line
        # should be skipped.
        #
        # This needs a pty. Writing to a pipe closes stdin as soon as the
        # input is consumed, so by the time the signal arrives the child has
        # already read EOF and finished normally -- the run comes back
        # "completed" and the test proves nothing. A terminal stays open, so
        # the process is genuinely blocked in readline() when the signal
        # lands.
        config = self._config()
        code, out, err = self._chat_in_tty(config, interrupt_after=1.5)
        events = self._trace()
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertEqual(
            self._types(events).count("run.cancelled"), 1)

    def test_ctrl_c_during_an_in_flight_request_ends_the_run_as_cancelled(self):
        # The case the W7 brief names: an interrupt during a turn must leave
        # run.cancelled, and the request it interrupted must be recorded as
        # never having completed.
        STATE.delay = 3.0
        config = self._config()
        code, out, err = self._chat("hello\n", config,
                                    send_signal=signal.SIGINT, signal_after=1.0)
        events = self._trace()
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertIn("model.response.failed", self._types(events))
        failed = [e for e in events if e["type"] == "model.response.failed"][-1]
        self.assertEqual(failed["payload"]["error"]["type"], "interrupted")

    def test_a_trace_file_never_has_a_start_without_an_end(self):
        # The invariant, stated once for the whole file: whichever way the
        # conversation ended, the file has exactly one terminal event.
        cases = (
            ("clean", "one\n/exit\n", "ok", 0.0),
            ("eof", "one\n", "ok", 0.0),
            ("empty", "", "ok", 0.0),
            ("provider-error", "one\n/exit\n", "http_error", 0.0),
        )
        for label, stdin_text, mode, delay in cases:
            with self.subTest(case=label):
                self.setUp()
                STATE.mode = mode
                STATE.delay = delay
                STATE.requests.clear()
                code, out, err = self._chat(stdin_text, self._config())
                events = self._trace()
                self.assertEqual(events[0]["type"], "run.started")
                self.assertEqual(
                    self._types(events).count("run.finished")
                    + self._types(events).count("run.failed")
                    + self._types(events).count("run.cancelled"),
                    1,
                    "expected exactly one terminal event",
                )


if __name__ == "__main__":
    unittest.main()
