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
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from pocketchat.provider import ProviderError, complete
from pocketchat.request import verify_request_snapshot

from tests import _helpers as hp

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
        cls._stub = hp.StubHTTPServer(StubHandler)
        cls.port = cls._stub.port

    @classmethod
    def tearDownClass(cls):
        cls._stub.close()

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
        environ = {"POCKETCHAT_API_KEY": "test-key-not-real"}
        if env:
            environ.update(env)
        cmd = list(args)
        if config is not None:
            cmd += ["--config", str(config)]
        return hp.run_cli(cmd, self.base, env=environ)

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

    def test_attempt_index_and_scope_pair_request_with_successful_response(self):
        config = self._config()
        result = self._run(["run", "first", "second", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self._trace()
        started = [e for e in events if e["type"] == "model.request.started"]
        completed = [e for e in events if e["type"] == "model.response.completed"]
        self.assertEqual([e["payload"]["attempt_index"] for e in started], [1, 1])
        self.assertEqual([e["payload"]["attempt_index"] for e in completed], [1, 1])
        self.assertTrue(all(e["payload"]["attempt_scope"] == "turn" for e in started + completed))

    def test_attempt_index_pairs_a_failed_request_with_its_response(self):
        STATE.mode = "http_error"
        STATE.status = 429
        config = self._config()
        self._run(["run", "hi"], config)
        events = self._trace()
        started = next(e for e in events if e["type"] == "model.request.started")
        failed = next(e for e in events if e["type"] == "model.response.failed")
        self.assertEqual(started["payload"]["attempt_index"], 1)
        self.assertEqual(started["payload"]["attempt_scope"], "turn")
        self.assertEqual(failed["payload"]["attempt_index"], 1)
        self.assertEqual(failed["payload"]["attempt_scope"], "turn")

    def test_attempt_index_fields_are_declared_by_the_schema(self):
        import json as _json

        schema = _json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        for event_type in ("model.request.started", "model.response.completed",
                           "model.response.failed"):
            block = next(
                item for item in schema["allOf"]
                if item.get("if", {}).get("properties", {})
                .get("type", {}).get("const") == event_type)
            declared = block["then"]["properties"]["payload"]["properties"]
            self.assertEqual(declared["attempt_index"]["type"], "integer")
            self.assertEqual(declared["attempt_index"]["minimum"], 1)
            self.assertEqual(declared["attempt_scope"]["const"], "turn")

    def test_failed_response_records_whether_the_error_is_retryable(self):
        # A reader of the trace must be able to tell a transient failure from
        # a permanent one without re-deriving it from the message text.
        STATE.mode = "http_error"
        STATE.status = 429
        config = self._config()
        self._run(["run", "hi"], config)
        failed = [e for e in self._trace()
                  if e["type"] == "model.response.failed"][0]
        self.assertIs(failed["payload"]["retryable"], True)

        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        STATE.mode = "garbage"
        config = self._config()
        self._run(["run", "hi"], config)
        failed = [e for e in self._trace()
                  if e["type"] == "model.response.failed"][0]
        self.assertIs(failed["payload"]["retryable"], False)

    def test_failed_response_retryable_field_is_declared_by_the_schema(self):
        import json as _json

        schema = _json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        block = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {})
            .get("type", {}).get("const") == "model.response.failed")
        declared = block["then"]["properties"]["payload"]["properties"]
        self.assertIn("retryable", declared)
        self.assertEqual(declared["retryable"]["type"], "boolean")

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

    def test_usage_reported_is_declared_by_the_schema(self):
        import json as _json

        schema = _json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        block = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {})
            .get("type", {}).get("const") == "model.response.completed")
        declared = block["then"]["properties"]["payload"]["properties"]
        self.assertEqual(declared["usage_reported"]["type"], "boolean")

    def test_usage_reported_flag_distinguishes_missing_from_real_zero(self):
        STATE.mode = "ok"
        config = self._config()
        self._run(["run", "hi"], config)
        completed = [e for e in self._trace()
                     if e["type"] == "model.response.completed"][0]
        self.assertIs(completed["payload"]["usage_reported"], True)

        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        STATE.mode = "no_usage"
        config = self._config()
        self._run(["run", "hi"], config)
        completed = [e for e in self._trace()
                     if e["type"] == "model.response.completed"][0]
        self.assertIs(completed["payload"]["usage_reported"], False)
        self.assertEqual(completed["payload"]["usage"]["input_tokens"], 0)

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

    def test_every_context_compacted_field_is_declared_by_the_schema(self):
        # The implementation writes the event; the schema is what a reader
        # trusts. A field the recorder writes but the schema does not declare
        # is a silent contract drift, so every key must be named in the
        # schema's context.compacted block.
        import json as _json

        config = self._config(provider={"max_context_chars": 27})
        result = self._run(["run", "one", "two", "three", "--quiet"], config)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        compacted = [e for e in self._trace()
                     if e["type"] == "context.compacted"]
        self.assertGreaterEqual(len(compacted), 1)
        payload = compacted[0]["payload"]

        schema = _json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        block = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {})
            .get("type", {}).get("const") == "context.compacted")
        declared = set(block["then"]["properties"]["payload"]["properties"])
        missing = sorted(set(payload) - declared)
        self.assertEqual(missing, [],
                         "payload fields not declared in the schema: %s" % missing)

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

    @unittest.skipUnless(os.name == "posix", "requires POSIX signal delivery")
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
        env = {"PYTHONPATH": str(ROOT)}
        proc = subprocess.Popen([sys.executable, "-c", script], cwd=str(self.base),
                                env=hp.subprocess_environment(env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        threading.Timer(1.0, lambda: proc.send_signal(signal.SIGINT)).start()
        proc.communicate(timeout=60)
        self.assertTrue(trace_path.exists())
        events = self._events(trace_path)
        self.assertEqual(events[0]["type"], "run.started")
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertIn("model.response.failed", self._types(events))
        started = next(e for e in events if e["type"] == "model.request.started")
        failed = next(e for e in events if e["type"] == "model.response.failed")
        self.assertEqual(started["payload"]["attempt_index"], 1)
        self.assertEqual(failed["payload"]["attempt_index"], 1)
        self.assertEqual(started["payload"]["attempt_scope"], "turn")
        self.assertEqual(failed["payload"]["attempt_scope"], "turn")


class ProviderBoundaryTests(unittest.TestCase):
    def test_completions_url_accepts_both_base_shapes(self):
        from pocketchat.provider import _completions_url
        self.assertEqual(
            _completions_url("http://example.invalid/v1"),
            "http://example.invalid/v1/chat/completions")
        self.assertEqual(
            _completions_url("http://example.invalid/v1/chat/completions"),
            "http://example.invalid/v1/chat/completions")

    def test_error_body_clipping_marks_the_truncation(self):
        from pocketchat.provider import _clip
        self.assertTrue(_clip("x" * 2001).endswith("... [clipped]"))
        self.assertEqual(_clip("short"), "short")

    def test_empty_context_is_refused_before_a_socket_opens(self):
        from pocketchat.provider import ProviderError, complete
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("socket")):
            with self.assertRaises(ProviderError) as caught:
                complete([], {"model": "m", "base_url": "http://example.invalid/v1"}, "key")
        self.assertEqual(caught.exception.error_type, "empty_context")


class ProviderStreamTests(unittest.TestCase):
    def test_stream_records_time_to_first_token(self):
        from pocketchat.provider import _stream_response
        import time as _time

        class Response:
            headers = {"Content-Type": "text/event-stream"}

            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"content":"a"},"finish_reason":""}]}\n'
                yield b'data: {"choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}\n'
                yield b'data: [DONE]\n'

        started = _time.monotonic()
        result = _stream_response(Response(), "m", 200, on_delta=lambda *_: None)
        elapsed = (_time.monotonic() - started) * 1000
        self.assertIsInstance(result["time_to_first_token_ms"], int)
        self.assertGreaterEqual(result["time_to_first_token_ms"], 0)
        self.assertLessEqual(result["time_to_first_token_ms"], max(1, int(elapsed)))

    def test_sse_chunks_are_joined_and_reasoning_is_reported_incrementally(self):
        from pocketchat.provider import _stream_response

        class Response:
            headers = {"Content-Type": "text/event-stream; charset=utf-8"}

            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"reasoning_content":"think "},"finish_reason":""}]}\n'
                yield b'\n'
                yield b'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":""}]}\n'
                yield b'\n'
                yield b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}]}\n'
                yield b'\n'
                yield b'data: [DONE]\n'

        seen = []
        result = _stream_response(
            Response(), "example-model", 200,
            on_delta=lambda kind, text: seen.append((kind, text)),
        )
        self.assertEqual(seen, [
            ("reasoning", "think "),
            ("content", "hello "),
            ("content", "world"),
        ])
        self.assertEqual(result["content"], "hello world")
        self.assertEqual(result["reasoning"], "think ")
        self.assertEqual(result["finish_reason"], "stop")

    def test_stream_separates_first_reasoning_and_content_deltas(self):
        from pocketchat.provider import _stream_response

        class Response:
            headers = {"Content-Type": "text/event-stream"}

            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"reasoning_content":"think"},"finish_reason":""}]}\n'
                yield b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}\n'
                yield b'data: [DONE]\n'

        result = _stream_response(Response(), "m", 200, on_delta=lambda *_: None)
        self.assertEqual("reasoning", result["first_delta_kind"])
        self.assertGreaterEqual(result["time_to_first_reasoning_ms"], 0)
        self.assertGreaterEqual(
            result["time_to_first_content_ms"],
            result["time_to_first_reasoning_ms"])
        self.assertEqual(
            result["time_to_first_token_ms"],
            result["time_to_first_reasoning_ms"])

class ProviderStreamHttpTests(unittest.TestCase):
    def test_streamed_trace_has_ttft_and_nonstreamed_trace_does_not(self):
        from pocketchat.provider import OpenAICompatibleAdapter
        from pocketchat.session import Session

        class StreamAdapter(OpenAICompatibleAdapter):
            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                on_delta("content", "x")
                return {
                    "content": "x", "finish_reason": "stop",
                    "model": config["model"], "status_code": 200,
                    "time_to_first_token_ms": 7,
                    "time_to_first_reasoning_ms": 7,
                    "time_to_first_content_ms": 9,
                    "first_delta_kind": "reasoning",
                    "usage": {"input_tokens": 1, "output_tokens": 1,
                              "cache_read_tokens": 0, "cache_write_tokens": 0,
                              "reasoning_tokens": 0},
                }

        base = {"base_url": "http://example.invalid/v1",
                "model": "m", "api_key_env": "K", "timeout_seconds": 5,
                "max_context_chars": 1000, "summary_prefix_chars": 40}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = Session({**base, "provider_adapter": StreamAdapter()},
                             root / "traces" / "stream.jsonl", "run_stream",
                             api_key="k")
            stream.on_delta = lambda *_: None
            self.assertEqual(stream.run(["hi"]), "completed")
            events = [json.loads(line) for line in
                      (root / "traces" / "stream.jsonl").read_text(
                          encoding="utf-8").splitlines()]
            completed = [e for e in events
                         if e["type"] == "model.response.completed"][0]
            self.assertEqual(completed["payload"]["time_to_first_token_ms"], 7)
            self.assertEqual(
                completed["payload"]["time_to_first_reasoning_ms"], 7)
            self.assertEqual(completed["payload"]["time_to_first_content_ms"], 9)
            self.assertEqual(completed["payload"]["first_delta_kind"], "reasoning")

            # The adapter is not called here: this asserts the recorder only
            # writes TTFT when the response supplies it.
            from pocketchat.provider import _parse_response
            plain_response = _parse_response(
                b'{"choices":[{"message":{"content":"x"},"finish_reason":"stop"}],"usage":{}}',
                200, base)
            self.assertNotIn("time_to_first_token_ms", plain_response)

    def test_ttft_delta_semantics_are_declared_by_the_schema(self):
        schema = json.loads(
            (ROOT / "pocketchat" / "schemas" / "trace.schema.json")
            .read_text(encoding="utf-8"))
        block = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {})
            .get("type", {}).get("const") == "model.response.completed")
        declared = block["then"]["properties"]["payload"]["properties"]
        self.assertEqual("integer",
                         declared["time_to_first_reasoning_ms"]["type"])
        self.assertEqual("integer",
                         declared["time_to_first_content_ms"]["type"])
        self.assertEqual(
            ["content", "reasoning"],
            declared["first_delta_kind"]["enum"])

    def test_complete_requests_and_reads_an_sse_stream(self):
        from pocketchat.provider import complete

        class StreamHandler(BaseHTTPRequestHandler):
            request_body = None

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                StreamHandler.request_body = json.loads(
                    self.rfile.read(length).decode("utf-8"))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in (
                    b'data: {"choices":[{"delta":{"reasoning_content":"think "},"finish_reason":""}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"hello "},"finish_reason":""}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"world"},"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":3}}\n\n',
                    b'data: [DONE]\n\n',
                ):
                    self.wfile.write(chunk)
                    self.wfile.flush()

            def log_message(self, *args):
                pass

        stub = hp.StubHTTPServer(StreamHandler)
        self.addCleanup(stub.close)
        seen = []
        result = complete(
            [{"role": "user", "content": "hi"}],
            {"model": "stream-model",
             "base_url": "http://127.0.0.1:%d/v1" % stub.port,
             "timeout_seconds": 5},
            "",
            on_delta=lambda kind, text: seen.append((kind, text)),
        )
        self.assertTrue(StreamHandler.request_body["stream"])
        self.assertEqual(seen, [
            ("reasoning", "think "),
            ("content", "hello "),
            ("content", "world"),
        ])
        self.assertEqual(result["content"], "hello world")
        self.assertEqual(result["reasoning"], "think ")
        self.assertEqual(result["usage"]["input_tokens"], 2)
        self.assertEqual(result["usage"]["output_tokens"], 3)

class SessionStreamTests(unittest.TestCase):
    def test_session_forwards_deltas_and_records_the_final_content(self):
        from pocketchat.provider import ProviderAdapter
        from pocketchat.session import Session

        class StreamingAdapter(ProviderAdapter):
            name = "streaming"

            def build_payload(self, messages, config):
                return {"model": config["model"], "stream": True}

            def complete(self, messages, config, api_key, on_delta=None,
                         prepared_payload=None):
                if on_delta is not None:
                    on_delta("reasoning", "think ")
                    on_delta("content", "hello ")
                    on_delta("content", "world")
                return {
                    "content": "hello world",
                    "reasoning": "think ",
                    "finish_reason": "stop",
                    "model": config["model"],
                    "status_code": 200,
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "reasoning_tokens": 0,
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            seen = []
            config = {
                "base_url": "http://example.invalid/v1",
                "model": "stream-model",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 5,
                "max_context_chars": 24000,
                "summary_prefix_chars": 160,
                "profile": "local",
                "provider_adapter": StreamingAdapter(),
            }
            session = Session(
                config=config,
                trace_path=base / "traces" / "run_stream.jsonl",
                run_id="run_stream",
                api_key="key",
            )
            session.on_delta = lambda kind, text: seen.append((kind, text))
            self.assertEqual(session.run(["hello"]), "completed")
            self.assertEqual(seen, [
                ("reasoning", "think "),
                ("content", "hello "),
                ("content", "world"),
            ])
            events = [json.loads(line) for line in
                      (base / "traces" / "run_stream.jsonl").read_text(
                          encoding="utf-8").splitlines() if line.strip()]
            completed = [e for e in events
                         if e["type"] == "model.response.completed"][0]
            self.assertEqual(completed["payload"]["output_summary"],
                             "hello world")

class ProviderAdapterContractTests(unittest.TestCase):
    """A provider adapter is the boundary future native providers implement."""

    def test_session_uses_the_injected_adapter_for_snapshot_and_request(self):
        from pocketchat.provider import ProviderAdapter
        from pocketchat.session import Session

        class StubAdapter(ProviderAdapter):
            name = "stub-adapter"

            def __init__(self):
                self.payloads = []
                self.calls = []

            def build_payload(self, messages, config):
                payload = {"model": config["model"], "input": list(messages)}
                self.payloads.append(payload)
                return payload

            def complete(self, messages, config, api_key,
                         on_delta=None, prepared_payload=None):
                self.calls.append((list(messages), config["model"], api_key))
                return {
                    "content": "adapter reply",
                    "finish_reason": "stop",
                    "model": config["model"],
                    "status_code": 200,
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 2,
                        "cache_read_tokens": 0,
                        "cache_write_tokens": 0,
                        "reasoning_tokens": 0,
                    },
                }

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            adapter = StubAdapter()
            config = {
                "base_url": "http://example.invalid/v1",
                "model": "adapter-model",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 5,
                "max_context_chars": 24000,
                "summary_prefix_chars": 160,
                "profile": "local",
                "provider_adapter": adapter,
            }
            session = Session(
                config=config,
                trace_path=base / "traces" / "run_adapter.jsonl",
                run_id="run_adapter",
                api_key="adapter-key",
            )
            self.assertEqual(session.run(["hello"]), "completed")
            self.assertEqual(adapter.calls[0][1:], ("adapter-model", "adapter-key"))
            snapshot = next((base / "traces" / "requests").glob("*.json"))
            record = json.loads(snapshot.read_text(encoding="utf-8"))
            self.assertEqual(record["request"], adapter.payloads[0])
            events = [
                json.loads(line) for line in
                (base / "traces" / "run_adapter.jsonl").read_text(
                    encoding="utf-8").splitlines() if line.strip()
            ]
            summary = events[0]["payload"]["config_summary"]
            self.assertEqual(summary["provider_adapter"], "stub-adapter")

    def test_adapter_can_forward_reasoning_effort_only_when_supported(self):
        from pocketchat.provider import OpenAICompatibleAdapter
        adapter = OpenAICompatibleAdapter()
        messages = [{"role": "user", "content": "hello"}]
        base = {"model": "m", "base_url": "http://example.invalid/v1"}
        self.assertNotIn(
            "reasoning_effort",
            adapter.build_payload(messages, {**base, "reasoning_effort": "high"}))
        self.assertEqual(
            adapter.build_payload(
                messages,
                {**base, "reasoning_effort": "high",
                 "reasoning_effort_supported": True},
            )["reasoning_effort"],
            "high",
        )

    def test_default_adapter_preserves_the_existing_payload(self):
        from pocketchat.provider import OpenAICompatibleAdapter, build_payload
        adapter = OpenAICompatibleAdapter()
        messages = [{"role": "user", "content": "hello"}]
        config = {"model": "m", "base_url": "http://example.invalid/v1"}
        self.assertEqual(adapter.build_payload(messages, config),
                         build_payload(messages, config))


if __name__ == "__main__":
    unittest.main()
