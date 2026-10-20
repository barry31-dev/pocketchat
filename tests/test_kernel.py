"""Conversation-kernel tests: real HTTP round trips against a local stub.

The stub is a real socket. Nothing here reaches a provider and no credential is
real. What these tests prove is that the request the package builds is one a
provider would accept, that a response becomes the right trace events, and that
every failure path still ends the run with a terminal event.

They do not prove anything about a real provider. That needs a real key and a
real endpoint, which is a later slice and a separate authorisation.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from pocketchat.provider import ProviderError, complete
from pocketchat.request import verify_request_snapshot

ROOT = Path(__file__).resolve().parents[1]


class StubState:
    """What the stub was asked, so a test can assert on the request itself."""

    def __init__(self):
        self.requests = []
        self.mode = "ok"
        self.status = 200
        self.body = b""
        self.delay = 0.0
        self.error_body = b""


STATE = StubState()


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        STATE.requests.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(raw.decode("utf-8")) if raw else {},
        })

        if STATE.delay:
            time.sleep(STATE.delay)

        if STATE.mode == "hang":
            return

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
            self._send(STATE.status, STATE.body or b'{"error": {"message": "nope"}}')
        elif STATE.mode == "garbage":
            self._send(200, b"this is not json")
        elif STATE.mode == "no_choices":
            self._send(200, json.dumps({"choices": []}).encode("utf-8"))
        elif STATE.mode == "no_usage":
            payload = {"choices": [{"message": {"content": "no usage reported"}}]}
            self._send(200, json.dumps(payload).encode("utf-8"))
        elif STATE.mode == "key_in_error":
            # A provider echoing the rejected credential, which must not reach
            # the trace.
            self._send(401, b'{"error": {"message": "bad key Bearer sk-leak-me-12345"}}')
        elif STATE.mode == "key_in_error_shapes":
            # The same credential echoed in every shape a provider has been
            # seen to use. A sanitizer that only knows one shape leaves the
            # credential in the trace for all the others, which is the one
            # artifact the whole package exists to keep checkable.
            self._send(401, STATE.error_body)
        else:
            self._send(500, b"{}")

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
class KernelTests(unittest.TestCase):
    server = None
    thread = None
    port = None

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        STATE.mode = "ok"
        STATE.status = 200
        STATE.body = b""
        STATE.delay = 0.0
        STATE.error_body = b""
        STATE.requests.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

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

    def _run(self, args, config=None, env=None):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_API_KEY": "test-key-not-real"}
        if env:
            environ.update(env)
        cmd = [sys.executable, "-m", "pocketchat"] + args
        if config is not None:
            cmd += ["--config", str(config)]
        return subprocess.run(cmd, cwd=str(self.base), env=environ,
                              capture_output=True, text=True, timeout=120)

    def _events(self, trace_path):
        return [json.loads(line) for line in
                Path(trace_path).read_text(encoding="utf-8").splitlines() if line.strip()]

    def _types(self, events):
        return [event["type"] for event in events]

    def _trace(self):
        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1)
        return self._events(traces[0])

    # --- the happy path -------------------------------------------------

    def test_one_round_trip_against_the_stub(self):
        config = self._config()
        result = self._run(["run", "hello there", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self._trace()
        self.assertEqual(self._types(events), [
            "run.started",
            "model.request.started",
            "model.response.completed",
            "run.finished",
        ])
        request = events[1]
        self.assertEqual(request["payload"]["model"], "example-model")
        # No system prompt in this config, so the request is the one prompt.
        self.assertEqual(request["payload"]["messages"], 1)
        self.assertEqual(request["payload"]["prompt_chars"], len("hello there"))
        response = events[2]["payload"]
        self.assertEqual(response["usage"]["input_tokens"], 11)
        self.assertEqual(response["usage"]["output_tokens"], 5)
        self.assertGreaterEqual(response["duration_ms"], 0)
        self.assertEqual(events[3]["payload"]["status"], "completed")
        self.assertEqual(events[3]["payload"]["turns"], 1)

    def test_second_turn_carries_the_first_exchange(self):
        config = self._config()
        result = self._run(["run", "first", "second", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(STATE.requests), 2)
        first = STATE.requests[0]["body"]["messages"]
        second = STATE.requests[1]["body"]["messages"]
        self.assertEqual([m["role"] for m in first], ["user"])
        self.assertEqual(
            [m["role"] for m in second], ["user", "assistant", "user"]
        )
        self.assertEqual(second[1]["content"], "hello from the stub")
        events = self._trace()
        self.assertEqual(self._types(events).count("model.response.completed"), 2)
        self.assertEqual(events[-1]["payload"]["turns"], 2)
        self.assertEqual(events[-1]["payload"]["total_input_tokens"], 22)

    def test_system_prompt_is_sent_first(self):
        config = self._config(system_prompt="be brief")
        self._run(["run", "hi", "--quiet"], config)
        messages = STATE.requests[0]["body"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": "be brief"})

    def test_credential_goes_in_the_header_only(self):
        config = self._config()
        self._run(["run", "hi", "--quiet"], config)
        self.assertEqual(STATE.requests[0]["authorization"], "Bearer test-key-not-real")
        trace_text = next((self.base / "traces").glob("*.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("test-key-not-real", trace_text)
        self.assertIn("POCKETCHAT_API_KEY", trace_text)

    def test_reply_is_printed_to_stdout(self):
        config = self._config()
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hello from the stub", result.stdout)

    # --- failure paths --------------------------------------------------

    def test_http_error_ends_the_run_as_failed(self):
        STATE.mode = "http_error"
        STATE.status = 429
        STATE.body = b'{"error": {"message": "slow down"}}'
        config = self._config()
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        self.assertEqual(self._types(events), [
            "run.started",
            "model.request.started",
            "model.response.failed",
            "run.failed",
        ])
        failed = events[2]["payload"]
        self.assertEqual(failed["error"]["type"], "rate_limited")
        self.assertIn("429", failed["error"]["message"])
        self.assertEqual(events[3]["payload"]["error"]["type"], "rate_limited")

    def test_a_failed_turn_is_not_added_to_the_context(self):
        STATE.mode = "http_error"
        STATE.status = 500
        config = self._config()
        self._run(["run", "hi", "again", "--quiet"], config)
        # The run stops at the first failure, so there is one request and one
        # failure; the second prompt never reaches a provider.
        self.assertEqual(len(STATE.requests), 1)
        events = self._trace()
        self.assertEqual(events[-1]["payload"]["turns"], 0)

    def test_garbage_body_is_reported_not_swallowed(self):
        STATE.mode = "garbage"
        config = self._config()
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        self.assertEqual(events[2]["type"], "model.response.failed")
        self.assertEqual(events[2]["payload"]["error"]["type"], "malformed_response")
        self.assertEqual(events[-1]["type"], "run.failed")

    def test_missing_usage_is_recorded_as_zero_not_omitted(self):
        STATE.mode = "no_usage"
        config = self._config()
        result = self._run(["run", "hi", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self._trace()
        usage = events[2]["payload"]["usage"]
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)

    def test_provider_echoed_key_never_reaches_the_trace(self):
        STATE.mode = "key_in_error"
        config = self._config()
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        trace_text = next((self.base / "traces").glob("*.jsonl")).read_text(encoding="utf-8")
        self.assertNotIn("sk-leak-me-12345", trace_text)
        self.assertNotIn("test-key-not-real", trace_text)

    def test_a_credential_pasted_into_a_prompt_never_reaches_a_snapshot(self):
        # The trace records counts and summaries, so a prompt's own text
        # never lands there. The request snapshot is the opposite: it carries
        # the body verbatim, so an operator who quotes the configured key in a
        # prompt puts the credential into the one artifact meant to be kept
        # and replayed.
        credential = "test-key-not-real"
        config = self._config()
        result = self._run(
            ["run", "please use this key: %s" % credential, "--quiet"], config)
        # The conversation itself still completes: refusing to write a file is
        # not a reason to stop a turn the operator asked for.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        artifacts = [path for path in (self.base / "traces").rglob("*")
                     if path.is_file()]
        self.assertTrue(artifacts, "the run left no trace behind")
        for path in artifacts:
            self.assertNotIn(
                credential, path.read_text(encoding="utf-8"),
                "%s carries the credential" % path.name)
        # The request still went out intact: only the artifact is refused.
        self.assertEqual(len(STATE.requests), 1)
        self.assertEqual(STATE.requests[0]["authorization"],
                         "Bearer test-key-not-real")
        self.assertEqual(
            STATE.requests[0]["body"]["messages"][-1]["content"],
            "please use this key: %s" % credential)
        events = self._trace()
        self.assertEqual(events[-1]["type"], "run.finished")
        unwritable = [e for e in events if e["type"] == "error.recorded"]
        self.assertEqual(len(unwritable), 1)
        self.assertEqual(unwritable[0]["payload"]["error"]["type"],
                         "credential_in_request")
        self.assertFalse(unwritable[0]["payload"]["fatal"])
        started = [e for e in events if e["type"] == "model.request.started"][0]
        self.assertNotIn("request_snapshot", started["payload"])
        self.assertNotIn("request_sha256", started["payload"])

    # --- the credential echoed in a provider error body ------------------

    def test_a_provider_error_body_never_carries_the_credential(self):
        # A provider that rejects a key often echoes what it rejected, and the
        # error body is the one place that happens without the operator
        # choosing it. Whatever shape it arrives in, the value must not reach
        # the trace, because the trace is the artifact that gets kept and
        # shared. Matching the literal credential is what makes that hold for
        # shapes nobody has thought of, rather than only the ones a pattern
        # list anticipated.
        # The credential the run actually holds. `_run` puts this in the
        # environment, and it is the only value the sanitizer can match: a key
        # belonging to somebody else is not this package's to police, the same
        # boundary the request snapshot draws.
        credential = "test-key-not-real"
        shapes = [
            '{"error": {"message": "bad key Bearer %s"}}' % credential,
            '{"error": {"message": "invalid key: %s"}}' % credential,
            '{"error": {"message": "Invalid API key %s"}}' % credential,
            "unauthorized: %s" % credential,
            '{"error": {"api_key": "%s"}}' % credential,
            '{"error": {"message": "nope"}, "detail": {"key": "%s"}}' % credential,
            '{"error": {"message": "first %s and again %s"}}' % (
                credential, credential),
            '{"error": {"message": "key %s-suffix-extra"}}' % credential,
        ]
        config = self._config()
        for body in shapes:
            with self.subTest(body=body):
                STATE.mode = "key_in_error_shapes"
                STATE.error_body = body.encode("utf-8")
                STATE.requests.clear()
                result = self._run(["run", "hi", "--quiet"], config)
                self.assertEqual(result.returncode, 1,
                                 result.stdout + result.stderr)
                artifacts = [path for path in (self.base / "traces").rglob("*")
                             if path.is_file()]
                self.assertTrue(artifacts, "the run left no trace behind")
                for path in artifacts:
                    self.assertNotIn(
                        credential, path.read_text(encoding="utf-8"),
                        "%s carries the credential" % path.name)
                self.assertNotIn(credential, result.stdout)
                self.assertNotIn(credential, result.stderr)
                # Each shape is a separate run, so the directory accumulates
                # one trace per shape. The one just written is the newest.
                traces = sorted(
                    (self.base / "traces").glob("*.jsonl"),
                    key=lambda path: path.stat().st_mtime)
                events = self._events(traces[-1])
                failed = [e for e in events
                          if e["type"] == "model.response.failed"]
                self.assertEqual(len(failed), 1)
                self.assertNotIn(credential, failed[0]["payload"]["error"]["message"])

    def test_a_sanitized_body_still_says_what_went_wrong(self):
        # Redaction must not turn the error into nothing: the reason the
        # provider refused is the whole value of recording it. The status and
        # the surrounding text survive, only the secret does not.
        credential = "test-key-not-real"
        STATE.mode = "key_in_error_shapes"
        STATE.error_body = (
            '{"error": {"message": "invalid key: %s"}}' % credential
        ).encode("utf-8")
        config = self._config()
        result = self._run(["run", "hi", "--quiet"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        failed = [e for e in events if e["type"] == "model.response.failed"][0]
        error = failed["payload"]["error"]
        self.assertEqual(error["type"], "unauthorized")
        self.assertIn("HTTP 401", error["message"])
        self.assertIn("[redacted]", error["message"])
        # The original text around the secret is still there, so the operator
        # can tell a bad key from a bad request.
        self.assertIn("invalid key", error["message"])

    def test_a_body_that_mentions_no_credential_is_left_alone(self):
        # Redacting everything would make every provider error unreadable, so
        # a body with no credential in it must pass through unchanged.
        STATE.mode = "key_in_error_shapes"
        STATE.error_body = b'{"error": {"message": "rate limited, retry later"}}'
        config = self._config()
        result = self._run(["run", "hi", "--quiet"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        failed = [e for e in events if e["type"] == "model.response.failed"][0]
        self.assertIn("rate limited, retry later", failed["payload"]["error"]["message"])

    def test_a_mentioned_key_that_is_not_the_configured_one_is_kept(self):
        # The writer guards the credential this process holds, because that is
        # the one a snapshot would turn into a usable key. Somebody else's key
        # quoted in a prompt is not this package's to police, and refusing it
        # would break a conversation that merely mentions one.
        config = self._config()
        result = self._run(
            ["run", "the docs say to use sk-someone-elses-key-0000", "--quiet"],
            config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self._trace()
        started = [e for e in events if e["type"] == "model.request.started"][0]
        self.assertIn("request_snapshot", started["payload"])
        reference = started["payload"]["request_snapshot"]
        snapshot = next((self.base / "traces").glob("*.jsonl")).parent / reference
        self.assertTrue(snapshot.is_file(), reference)
        self.assertEqual(verify_request_snapshot(snapshot),
                         started["payload"]["request_sha256"])

    def test_unreachable_provider_ends_the_run(self):
        config = self._config(provider={"base_url": "http://127.0.0.1:1/v1"})
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        self.assertEqual(events[2]["payload"]["error"]["type"], "connection_error")

    def test_timeout_ends_the_run_with_terminal_event(self):
        STATE.mode = "hang"
        STATE.delay = 1.2
        config = self._config(provider={"timeout_seconds": 1})
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        events = self._trace()
        self.assertEqual(self._types(events), [
            "run.started",
            "model.request.started",
            "model.response.failed",
            "run.failed",
        ])
        self.assertEqual(events[2]["payload"]["error"]["type"], "timeout")
        self.assertEqual(events[3]["payload"]["error"]["type"], "timeout")

    def test_wrapped_timeout_is_classified_as_timeout(self):
        config = {
            "model": "example-model",
            "base_url": "http://127.0.0.1:%d/v1" % self.port,
            "timeout_seconds": 1,
        }
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(socket.timeout("timed out")),
        ):
            with self.assertRaises(ProviderError) as caught:
                complete(
                    [{"role": "user", "content": "hi"}],
                    config,
                    "test-key-not-real",
                )
        self.assertEqual(caught.exception.error_type, "timeout")
        self.assertTrue(caught.exception.retryable)

    def test_missing_credential_still_fails_closed(self):
        config = self._config()
        result = self._run(["run", "hi"], config, {"POCKETCHAT_API_KEY": ""})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.base / "traces").exists())

    # --- context truncation ---------------------------------------------

    def test_context_truncation_is_recorded(self):
        # A tight budget over several turns: the oldest turns go, and the trace
        # says so with real before/after counts.
        config = self._config(provider={"max_context_chars": 27})
        result = self._run(["run", "one", "two", "three", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self._trace()
        compacted = [e for e in events if e["type"] == "context.compacted"]
        self.assertGreaterEqual(len(compacted), 1)
        first = compacted[0]["payload"]
        self.assertEqual(first["strategy"], "drop_oldest_turns")
        self.assertGreater(first["dropped_messages"], 0)
        self.assertLess(first["kept_messages"], first["kept_messages"] + first["dropped_messages"])
        self.assertLessEqual(first["kept_chars"], 27)
        # The newest exchange always survives: the last request still carries
        # the assistant reply and the new prompt.
        last_request = STATE.requests[-1]["body"]["messages"]
        self.assertEqual([m["role"] for m in last_request], ["user", "assistant", "user"])
        self.assertEqual(last_request[1]["content"], "hello from the stub")

    def test_a_long_prompt_is_never_truncated_away(self):
        # The pending prompt is pinned, so a prompt larger than what remains of
        # the budget still goes out whole.
        long_prompt = "z" * 900
        config = self._config(provider={"max_context_chars": 905})
        result = self._run(["run", "first", long_prompt, "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sent = STATE.requests[-1]["body"]["messages"]
        self.assertEqual(sent[-1], {"role": "user", "content": long_prompt})
        # The first exchange was dropped to make room.
        compacted = [e for e in self._trace() if e["type"] == "context.compacted"]
        self.assertGreaterEqual(len(compacted), 1)

    def test_context_that_cannot_fit_stops_the_run(self):
        # A budget that cannot hold the system prompt plus one prompt is a
        # configuration error, not a reason to send an over-long context.
        config = self._config(system_prompt="s" * 500,
                              provider={"max_context_chars": 100})
        result = self._run(["run", "hi"], config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(STATE.requests), 0)
        events = self._trace()
        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertEqual(events[-1]["payload"]["error"]["type"], "context_too_large")
        self.assertIn("budget", events[-1]["payload"]["error"]["message"])

    def test_interrupted_in_flight_request_writes_a_failure_event(self):
        STATE.mode = "ok"
        STATE.delay = 3.0
        config = self._config()
        trace_path = self.base / "interrupted.jsonl"
        script = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "from pocketchat.session import Session;"
            "from pocketchat.config import load_config;"
            "from pathlib import Path;"
            "session = Session(config=load_config(Path(%r)), trace_path=Path(%r),"
            " api_key='test-key');"
            "print(session.run(['hello']))" % (str(ROOT), str(config), str(trace_path))
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT)
        proc = subprocess.Popen([sys.executable, "-c", script], cwd=str(self.base),
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        threading.Timer(1.0, lambda: proc.send_signal(signal.SIGINT)).start()
        proc.communicate(timeout=60)
        self.assertTrue(trace_path.exists())
        events = self._events(trace_path)
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertIn("model.response.failed", self._types(events))


if __name__ == "__main__":
    unittest.main()
