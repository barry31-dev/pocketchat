"""Request snapshot tests: what was sent, provably, without the credential.

A trace records that a request happened; a snapshot records what it said.
The two answer different questions, and this file is about the second one:
replay and diff need the exact payload, and "exact" has to mean something
checkable. So the snapshot is written by the same builder the provider uses,
hashed with a serializer that is stable across runs, and carries nothing a
credential could hide in.

The properties under test: the snapshot's request is byte-for-byte the
payload the provider sent, its hash is the hash of exactly those bytes, the
file is created once and never silently overwritten, and a second snapshot of
the same run lands in its own file rather than clobbering the first.

The stub is a real local socket. Nothing here reaches a provider and no
credential is real.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from pocketchat.provider import build_payload
from pocketchat.request import (
    REQUEST_SCHEMA_VERSION,
    CredentialInRequestError,
    canonical_bytes,
    contains_credential,
    next_request_turn,
    request_snapshot_path,
    verify_request_snapshot,
    write_request_snapshot,
)

from tests import _helpers as hp

ROOT = Path(__file__).resolve().parents[1]


def _config(model="stub", **extra):
    config = {
        "base_url": "http://127.0.0.1:8123/v1",
        "model": model,
        "api_key_env": "POCKETCHAT_API_KEY",
        "timeout_seconds": 30,
        "max_context_chars": 24000,
        "summary_prefix_chars": 160,
        "profile": "local",
    }
    config.update(extra)
    return config


class BuildPayloadTests(unittest.TestCase):
    """One payload builder, so a snapshot cannot describe a different request."""

    def test_payload_is_the_chat_completions_shape_the_provider_sends(self):
        messages = [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello"},
        ]
        self.assertEqual(build_payload(messages, _config(model="example-model")), {
            "model": "example-model",
            "messages": messages,
            "stream": False,
        })

    def test_complete_sends_exactly_what_build_payload_returns(self):
        # The plan's real requirement: `complete()` must not carry a second,
        # slightly different body. A stub that records the raw body is the only
        # way to prove the two agree, because comparing the two functions in
        # one process would pass even if the provider ignored the builder.
        state = self
        state.sent = []

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                state.sent.append(self.rfile.read(length))
                body = json.dumps({
                    "choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Stub)
        # Cleanups run last-registered-first: shutdown must run before join,
        # or join waits out its full timeout against a still-serving thread.
        # The thread does not exist yet, so join is deferred through a holder.
        running = []
        self.addCleanup(lambda: running[0].join(10))
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        running.append(thread)

        from pocketchat.provider import complete
        messages = [{"role": "user", "content": "hello"}]
        config = _config(
            model="stub",
            base_url="http://127.0.0.1:%d/v1" % server.server_address[1])
        complete(messages, config, "fake-key")

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(json.loads(self.sent[0].decode("utf-8")),
                         build_payload(messages, config))


class CanonicalBytesTests(unittest.TestCase):
    """The hash is only meaningful if the bytes it hashes are stable."""

    def test_key_order_does_not_change_the_digest(self):
        first = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        second = {"messages": first["messages"], "model": "m"}
        self.assertEqual(canonical_bytes(first), canonical_bytes(second))

    def test_whitespace_and_escaping_are_fixed_by_the_serializer(self):
        # Two payloads that differ only in JSON punctuation are the same
        # request; a serializer that kept the caller's formatting would hash
        # them differently and call a replay a mismatch. The exact shape is
        # pinned here because the digest is over these bytes: changing the
        # serializer changes every historical hash with it.
        self.assertEqual(canonical_bytes({"model": "m", "messages": []}),
                         b'{\n  "messages": [],\n  "model": "m"\n}\n')
        # The same request written compactly by a caller still canonicalizes
        # to the same bytes, which is the property that makes a replay able to
        # verify a snapshot it did not write.
        self.assertEqual(canonical_bytes(json.loads(
            '{"model":"m","messages":[]}')), canonical_bytes(
                {"model": "m", "messages": []}))

    def test_non_ascii_is_not_escaped(self):
        # `ensure_ascii=False` is a deliberate choice: an escaped form is a
        # different byte string, so a snapshot written by one build and read
        # by another would not verify.
        self.assertIn("caf\u00e9".encode("utf-8"),
                      canonical_bytes({"model": "caf\u00e9"}))


class SnapshotWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _write(self, name="run_x", payload=None, credential=None):
        return write_request_snapshot(
            self.base / "requests" / ("%s.json" % name),
            run_id=name, profile="local", created="2026-10-18T00:00:00.000Z",
            payload=payload if payload is not None else build_payload(
                [{"role": "user", "content": "hello"}], _config()),
            credential=credential)

    def test_snapshot_carries_the_payload_and_its_digest(self):
        payload = build_payload([{"role": "user", "content": "hello"}], _config())
        digest = self._write(payload=payload)
        record = json.loads((self.base / "requests" / "run_x.json").read_text("utf-8"))
        self.assertEqual(record["schema_version"], REQUEST_SCHEMA_VERSION)
        self.assertEqual(record["run_id"], "run_x")
        self.assertEqual(record["profile"], "local")
        self.assertEqual(record["request"], payload)
        self.assertEqual(record["request_sha256"], digest)
        self.assertEqual(digest, hashlib.sha256(canonical_bytes(payload)).hexdigest())

    def test_the_credential_is_nowhere_in_the_file(self):
        # The payload has no header and no key by construction; this pins it,
        # because the day someone adds a field to the payload is the day a key
        # could ride along unexamined.
        payload = build_payload([{"role": "user", "content": "hello"}], _config())
        self._write(payload=payload)
        text = (self.base / "requests" / "run_x.json").read_text("utf-8")
        self.assertNotIn("Authorization", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("POCKETCHAT_API_KEY", text)

    def test_a_past_prompt_cannot_write_the_credential_into_a_snapshot(self):
        # The payload has no header by construction, but the operator's own
        # words do get in: an environment that quotes its key, a paste, a
        # support ticket. The snapshot would then be the one artifact on disk
        # holding the credential, and replay would carry it onwards. The
        # writer refuses rather than producing that file.
        credential = "sk-pasted-into-a-prompt-4242"
        payload = build_payload(
            [{"role": "user", "content": "please use %s" % credential}], _config())
        with self.assertRaises(CredentialInRequestError):
            self._write(payload=payload, credential=credential)
        self.assertFalse((self.base / "requests" / "run_x.json").exists())

    def test_the_credential_is_part_of_the_request(self):
        credential = "sk-pasted-into-a-prompt-4242"
        payload = build_payload(
            [{"role": "user", "content": "please use %s" % credential}], _config())
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertTrue(contains_credential(body, credential))
        self.assertFalse(contains_credential(body, "sk-some-other-key-0000"))
        self.assertFalse(contains_credential(body, ""))
        self.assertFalse(contains_credential(body, None))

    def test_the_directory_is_created_on_demand(self):
        # A run in a fresh working directory must not need the operator to
        # mkdir anything first.
        self.assertFalse((self.base / "requests").exists())
        self._write()
        self.assertTrue((self.base / "requests" / "run_x.json").is_file())

    def test_an_existing_snapshot_is_never_overwritten(self):
        # Exclusive create is the whole reason this is not a plain write: a
        # resumed or re-run session that silently replaced its own snapshot
        # would destroy the evidence for the run that already happened.
        self._write()
        original = (self.base / "requests" / "run_x.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self._write()
        self.assertEqual((self.base / "requests" / "run_x.json").read_bytes(), original)

    def test_each_turn_gets_its_own_snapshot(self):
        # A run is several requests with different contexts. Naming them by
        # turn means the second turn's snapshot cannot describe the first
        # turn's request, and neither is lost.
        first = build_payload([{"role": "user", "content": "one"}], _config())
        second = build_payload([{"role": "user", "content": "one"},
                                {"role": "assistant", "content": "reply"},
                                {"role": "user", "content": "two"}], _config())
        digest_one = write_request_snapshot(
            self.base / "requests" / "run_x.turn-1.json", run_id="run_x",
            profile="local", created="2026-10-18T00:00:00.000Z", payload=first)
        digest_two = write_request_snapshot(
            self.base / "requests" / "run_x.turn-2.json", run_id="run_x",
            profile="local", created="2026-10-18T00:00:01.000Z", payload=second)
        self.assertNotEqual(digest_one, digest_two)
        self.assertEqual(len(list((self.base / "requests").glob("*.json"))), 2)


class SnapshotVerifyTests(unittest.TestCase):
    """Reading a snapshot back has to be able to fail."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _write(self):
        payload = build_payload([{"role": "user", "content": "hello"}], _config())
        digest = write_request_snapshot(
            self.base / "requests" / "run_x.json", run_id="run_x", profile="local",
            created="2026-10-18T00:00:00.000Z", payload=payload)
        return self.base / "requests" / "run_x.json", digest, payload

    def test_a_snapshot_verifies_against_itself(self):
        path, digest, payload = self._write()
        self.assertTrue(verify_request_snapshot(path))
        self.assertEqual(verify_request_snapshot(path), digest)

    def test_a_tampered_request_does_not_verify(self):
        # This is what makes the hash worth writing: a snapshot whose body was
        # edited after the fact must not pass as the request that was sent.
        path, _, payload = self._write()
        record = json.loads(path.read_text("utf-8"))
        record["request"]["model"] = "something-else"
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", "utf-8")
        self.assertIsNone(verify_request_snapshot(path))

    def test_a_mismatched_digest_does_not_verify(self):
        path, _, payload = self._write()
        record = json.loads(path.read_text("utf-8"))
        record["request_sha256"] = "0" * 64
        path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", "utf-8")
        self.assertIsNone(verify_request_snapshot(path))


class SnapshotPathTests(unittest.TestCase):
    """The snapshot path is derived, so a caller cannot invent its own."""

    def test_the_path_lives_beside_the_trace(self):
        # Beside the trace, not in a directory the layout happens to assume:
        # the recorded reference has to resolve against the trace a reader
        # actually has, wherever `--trace` put it.
        trace = Path("/tmp/traces/run_x.jsonl")
        self.assertEqual(request_snapshot_path(trace),
                         Path("/tmp/traces/requests/run_x.turn-1.json"))

    def test_each_turn_is_a_distinct_file(self):
        trace = Path("/tmp/traces/run_x.jsonl")
        self.assertEqual(request_snapshot_path(trace, 2),
                         Path("/tmp/traces/requests/run_x.turn-2.json"))

    def test_a_trace_outside_the_default_layout_keeps_its_snapshots(self):
        # `--trace custom/here/mytrace.jsonl` puts the trace somewhere the
        # default layout never predicted. Deriving the snapshot directory from
        # the trace keeps the pair together; assuming a `traces`/`requests`
        # sibling pair would write the file somewhere the reference does not
        # resolve, and a reference that resolves to nothing is worse than none.
        trace = Path("/tmp/custom/here/mytrace.jsonl")
        snapshot = request_snapshot_path(trace)
        self.assertEqual(snapshot, Path("/tmp/custom/here/requests/mytrace.turn-1.json"))
        self.assertEqual(snapshot.parent, trace.parent / "requests")

    def test_a_bare_trace_path_still_resolves(self):
        # A trace given as a bare filename has no directory of its own; the
        # snapshot belongs in `./requests`, not in the parent of nothing.
        self.assertEqual(request_snapshot_path(Path("mytrace.jsonl")),
                         Path("requests/mytrace.turn-1.json"))


class NextRequestTurnTests(unittest.TestCase):
    """A resumed run must not restart the snapshot numbering."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _trace(self, request_count):
        traces = self.base / "traces"
        traces.mkdir(parents=True, exist_ok=True)
        path = traces / "run_x.jsonl"
        lines = [{"type": "run.started", "event_id": "evt_000001"}]
        for index in range(request_count):
            lines.append({"type": "model.request.started",
                          "event_id": "evt_%06d" % (2 * index + 2)})
            lines.append({"type": "model.response.completed",
                          "event_id": "evt_%06d" % (2 * index + 3)})
        path.write_text("".join(json.dumps(e) + "\n" for e in lines), "utf-8")
        return path

    def test_the_first_request_of_a_new_trace_is_turn_one(self):
        path = self.base / "traces" / "run_x.jsonl"
        self.assertEqual(next_request_turn(path), 1)

    def test_a_resumed_trace_continues_the_numbering(self):
        # The failure this guards against: a second process adopting this run
        # id restarted at turn 1, hit the exclusive create on a snapshot that
        # was already there, and lost the resumed request's evidence.
        path = self._trace(3)
        self.assertEqual(next_request_turn(path), 4)

    def test_the_count_comes_from_the_trace_not_a_counter(self):
        # The trace is what both processes see, so it is the only safe source.
        path = self._trace(1)
        self.assertEqual(next_request_turn(path), 2)
        self.assertEqual(next_request_turn(path), 2)


class SnapshotRunTests(unittest.TestCase):
    """A real run writes a snapshot the trace can point at."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "pocketchat.json"
        port = self._serve()
        self.config.write_text(json.dumps({
            "schema_version": "2",
            "name": "workbench",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % port,
                    "model": "local-model",
                    "api_key_env": "POCKETCHAT_LOCAL_KEY",
                    "timeout_seconds": 30,
                },
            },
        }), encoding="utf-8")

    def _serve(self):
        state = self
        state.bodies = []

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                state.bodies.append(self.rfile.read(length))
                body = json.dumps({
                    "choices": [{"message": {"content": "stub reply"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 5},
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Stub)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda s=self.server: s.serve_forever(poll_interval=0.05), daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        return self.port

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _run(self, *args):
        return hp.run_cli(
            ["run", "hi", "--quiet", "--config", str(self.config)] + list(args),
            self.base, env={"POCKETCHAT_LOCAL_KEY": "local-fake"})

    def _run_subprocess(self, *args):
        """The golden path: a real interpreter, a real pipe, a real exit."""
        return subprocess.run(
            [sys.executable, "-m", "pocketchat", "run", "hi", "--quiet",
             "--config", str(self.config)] + list(args),
            cwd=str(self.base),
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                 "POCKETCHAT_LOCAL_KEY": "local-fake"},
            capture_output=True, text=True, timeout=120)

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the snapshot and replay families: run once
        # through a real pipe, then verify the snapshot it wrote with the
        # replay command, also through a real pipe.
        result = self._run_subprocess()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshots = list((self.base / "traces" / "requests").glob("*.json"))
        self.assertEqual(len(snapshots), 1)
        replayed = subprocess.run(
            [sys.executable, "-m", "pocketchat", "replay", str(snapshots[0]),
             "--dry-run"],
            cwd=str(self.base),
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                 "POCKETCHAT_LOCAL_KEY": "local-fake"},
            capture_output=True, text=True, timeout=120)
        self.assertEqual(replayed.returncode, 0,
                         replayed.stdout + replayed.stderr)

    def test_a_run_writes_a_snapshot_the_trace_points_at(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1)
        events = [json.loads(line) for line in
                  traces[0].read_text("utf-8").splitlines() if line.strip()]
        started = [e for e in events if e["type"] == "model.request.started"]
        self.assertEqual(len(started), 1)
        payload = started[0]["payload"]
        self.assertIn("request_snapshot", payload)
        self.assertIn("request_sha256", payload)

        snapshot = traces[0].parent / payload["request_snapshot"]
        self.assertTrue(snapshot.is_file(), snapshot)
        record = json.loads(snapshot.read_text("utf-8"))
        self.assertEqual(record["request"], json.loads(self.bodies[0].decode("utf-8")))
        self.assertEqual(record["request_sha256"], payload["request_sha256"])
        self.assertTrue(verify_request_snapshot(snapshot))
        self.assertNotIn("local-fake", snapshot.read_text("utf-8"))

    def _chat(self, *args):
        return hp.run_cli(
            ["chat", "--quiet", "--config", str(self.config)] + list(args),
            self.base, env={"POCKETCHAT_LOCAL_KEY": "local-fake"},
            stdin="hi\n/exit\n")

    def test_a_resumed_session_keeps_writing_snapshots(self):
        # A resumed session adopts the run id and the trace of the run it
        # continues. Its requests are part of that same run, so they need
        # their own snapshots -- and numbering them from one again would
        # collide with the file already on disk and lose the evidence.
        first = self._chat("--session", "notes")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = self._chat("--session", "notes")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1, "a resumed run keeps its trace")
        snapshots = sorted((traces[0].parent / "requests").glob("*.json"))
        self.assertEqual(len(snapshots), 2, [p.name for p in snapshots])
        self.assertEqual(len(self.bodies), 2)

        # Every request the stub actually saw is described by a file, and no
        # request was recorded as a snapshot that failed to land.
        for snapshot in snapshots:
            self.assertTrue(verify_request_snapshot(snapshot), snapshot)
        text = traces[0].read_text("utf-8")
        self.assertNotIn("snapshot_unwritable", text)
        started = [json.loads(line) for line in text.splitlines() if line.strip()]
        requests = [e for e in started if e["type"] == "model.request.started"]
        self.assertEqual(len(requests), 2)
        self.assertEqual([e["payload"]["request_sha256"] for e in requests],
                         [verify_request_snapshot(p) for p in snapshots])

    def test_a_trace_outside_the_default_layout_points_at_its_snapshot(self):
        # `--trace` lets an operator put the trace wherever they like. The
        # snapshot must land somewhere the recorded reference resolves to,
        # because a reference that resolves to nothing is a dead end for the
        # one reader the snapshot exists for.
        result = self._run("--trace", "custom/here/mytrace.jsonl")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        trace = self.base / "custom" / "here" / "mytrace.jsonl"
        self.assertTrue(trace.is_file())
        events = [json.loads(line) for line in
                  trace.read_text("utf-8").splitlines() if line.strip()]
        started = [e for e in events if e["type"] == "model.request.started"]
        self.assertEqual(len(started), 1)
        reference = started[0]["payload"]["request_snapshot"]

        snapshot = trace.parent / reference
        self.assertTrue(snapshot.is_file(), "%s does not resolve" % reference)
        self.assertEqual(verify_request_snapshot(snapshot),
                         started[0]["payload"]["request_sha256"])
        self.assertFalse((self.base / "requests").exists(),
                         "the snapshot belongs beside its trace, not in a "
                         "directory the default layout would have used")

    def test_a_run_that_never_reaches_the_provider_writes_no_snapshot(self):
        # The snapshot is written before the socket opens, so a run refused
        # earlier -- an unknown profile -- leaves no file claiming a request
        # was made.
        self._run()
        missing = hp.run_cli(
            ["run", "hi", "--quiet", "--config", str(self.config),
             "--profile", "nope"],
            self.base, env={"POCKETCHAT_LOCAL_KEY": "local-fake"})
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        self.assertEqual(
            len(list((self.base / "traces" / "requests").glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
