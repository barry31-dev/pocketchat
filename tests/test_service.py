"""Service-layer tests: the records the CLI prints, callable in process.

The property under test is that the seam adds nothing and changes nothing:
`session_overview` returns exactly what `show` prints, `export_session`
writes exactly what `export` writes -- byte for byte -- and the refusals
carry the messages the CLI has always answered with. The one improvement
the extraction made is pinned too: `show` over a file whose schema version
this reader does not speak is a clean refusal naming the version, not an
uncaught traceback after half the conversation printed.

The stub is a real local socket. Nothing here reaches a provider and no
credential is real.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pocketchat.service import (SessionFileError, export_session,
                                session_overview)

ROOT = Path(__file__).resolve().parents[1]


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({
            "id": "cmpl-stub", "model": "example-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant",
                                     "content": "the stub reply"},
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


class ServiceTestBase(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=lambda s=self.server: s.serve_forever(poll_interval=0.05),
            daemon=True)
        self.thread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self._stop_server)
        self.base = Path(self.temp.name)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _run(self, *args):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "test-key-not-real"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base), env=environ, capture_output=True, text=True,
            timeout=120)

    def _build(self):
        """A conversation and a comparison, built by the real commands."""
        config = self.base / "pocketchat.json"
        config.write_text(json.dumps({
            "schema_version": "2", "name": "workbench",
            "default_profile": "local",
            "profiles": {"local": {
                "base_url": "http://127.0.0.1:%d/v1" % self.port,
                "model": "example-model",
                "api_key_env": "POCKETCHAT_LOCAL_KEY",
            }},
        }), encoding="utf-8")
        self._run("run", "q1", "--session", "notes", "--config", str(config))
        self._run("run", "q2", "--session", "notes", "--config", str(config))
        self._run("compare", "which one?", "--profiles", "local",
                  "--verbose", "--config", str(config))
        manifest = next((self.base / "comparisons").glob("*.json"))
        return config, manifest


class SessionOverviewTests(ServiceTestBase):
    def test_the_record_holds_what_the_command_prints(self):
        self._build()
        record = session_overview("notes", workspace=self.base)

        self.assertIsNotNone(record["session"])
        self.assertIsNone(record["trace_problem"])
        part = record["session"]
        self.assertEqual(part["path"], str(self.base / "sessions" / "notes.jsonl"))
        self.assertEqual(len(part["turns"]), 4)
        self.assertEqual(part["turns"][0]["turn_id"], "turn_000001")
        # The trace is the recorded one, and the events line up with the file:
        # two runs, each its own started / request / response / finished set.
        trace_path = self.base / record["trace"]["path"]
        self.assertTrue(trace_path.is_file())
        self.assertEqual(len(record["trace"]["lines"]),
                         len([l for l in trace_path.read_text(
                             encoding="utf-8").splitlines() if l.strip()]))
        self.assertEqual(len(record["trace"]["lines"]), 8)

        shown = self._run("show", "notes").stdout
        self.assertIn("session %s" % part["header"]["session"], shown)
        self.assertIn("turn_000001", shown)

    def test_a_trace_only_argument_answers_without_a_session(self):
        config, manifest = self._build()
        compare_trace = json.loads(
            manifest.read_text(encoding="utf-8"))["runs"][0]["trace"]
        record = session_overview(str(self.base / compare_trace),
                                  workspace=self.base)

        self.assertIsNone(record["session"])
        self.assertIsNone(record["trace_problem"])
        self.assertEqual(len(record["trace"]["lines"]), 4,
                         "one compare turn: started, request, response, done")

    def test_a_session_without_a_trace_records_the_problem(self):
        self._build()
        trace = next((self.base / "sessions").glob("notes.jsonl")
                     ).parent.parent / "traces" / "gone.jsonl"
        # Point the header at a trace that is not there, by name.
        # The service reads the header, so the problem carries the message
        # the command prints and exits 2 after the conversation.
        session_file = self.base / "sessions" / "notes.jsonl"
        lines = session_file.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        victim = (session_file.parent / header["trace"]).resolve()
        victim.unlink()

        record = session_overview("notes", workspace=self.base)
        self.assertIsNotNone(record["session"])
        self.assertEqual(record["trace_problem"]["message"],
                         "trace not found: %s (recorded by %s)"
                         % (victim, session_file))
        del trace

    def test_a_missing_session_is_refused_with_the_command_message(self):
        with self.assertRaises(SessionFileError) as caught:
            session_overview("no-such-session", workspace=self.base)
        self.assertEqual(str(caught.exception),
                         "no such session: no-such-session")

    def test_an_unknown_schema_version_is_a_clean_refusal(self):
        # Pinned deliberately: the pre-extraction CLI died on the uncaught
        # SessionFileError after printing the conversation. The refusal with
        # the version named is what the session reader promised.
        self._build()
        session_file = self.base / "sessions" / "notes.jsonl"
        lines = session_file.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        header["schema_version"] = "9"
        session_file.write_text(
            json.dumps(header, sort_keys=True) + "\n" + "\n".join(lines[1:])
            + "\n", encoding="utf-8")

        with self.assertRaises(SessionFileError) as caught:
            session_overview("notes", workspace=self.base)
        self.assertIn("schema version", str(caught.exception))


class ExportSessionTests(ServiceTestBase):
    def test_the_service_writes_what_the_command_writes(self):
        self._build()
        result = export_session("notes", "jsonl", workspace=self.base)
        service_file = Path(result["out"])
        self.assertEqual(sorted(result), ["format", "messages", "out",
                                          "session", "trace_events"])

        # The same export through the command, in a twin workspace, must
        # agree byte for byte -- the seam did not change the bytes.
        (self.base / "twin").mkdir()
        (self.base / "twin" / "pocketchat.json").write_text(
            (self.base / "pocketchat.json").read_text(encoding="utf-8"),
            encoding="utf-8")
        for name in ("sessions/notes.jsonl",):
            source = self.base / name
            target = self.base / "twin" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"),
                              encoding="utf-8")
        for trace in (self.base / "traces").glob("*.jsonl"):
            twin_trace = self.base / "twin" / "traces" / trace.name
            twin_trace.parent.mkdir(parents=True, exist_ok=True)
            twin_trace.write_text(trace.read_text(encoding="utf-8"),
                                  encoding="utf-8")
        self._run("export", "notes", "--out", "twin-out.jsonl")
        command_file = self.base / "twin-out.jsonl"

        self.assertEqual(service_file.read_bytes(), command_file.read_bytes())

    def test_trace_format_copies_the_source_bytes(self):
        self._build()
        # The source is the trace the session header records -- not whichever
        # file an unordered glob yields first (the comparison run writes one
        # too, and taking the wrong one would make this test a coin flip).
        header = json.loads((self.base / "sessions" / "notes.jsonl")
                            .read_text(encoding="utf-8").splitlines()[0])
        trace = (self.base / "sessions" / header["trace"]).resolve()
        result = export_session("notes", "trace", workspace=self.base)
        exported = Path(result["out"])
        self.assertEqual(exported.read_bytes(), trace.read_bytes())
        self.assertEqual(result["trace_events"],
                         len([l for l in trace.read_text(
                             encoding="utf-8").splitlines() if l.strip()]))

    def test_an_existing_output_is_refused_without_writing(self):
        self._build()
        first = export_session("notes", "md", workspace=self.base)
        with self.assertRaises(SessionFileError) as caught:
            export_session("notes", "md", workspace=self.base)
        self.assertIn("already exists (use --out)", str(caught.exception))
        self.assertTrue(Path(first["out"]).is_file())

    def test_a_trace_only_argument_cannot_export_a_session(self):
        config, manifest = self._build()
        compare_trace = json.loads(
            manifest.read_text(encoding="utf-8"))["runs"][0]["trace"]
        with self.assertRaises(SessionFileError) as caught:
            export_session(str(self.base / compare_trace), "jsonl",
                           workspace=self.base)
        self.assertIn("has no session file to export", str(caught.exception))

    def test_a_missing_session_is_refused_with_the_command_message(self):
        with self.assertRaises(SessionFileError) as caught:
            export_session("no-such-session", "jsonl", workspace=self.base)
        self.assertEqual(str(caught.exception),
                         "no such session: no-such-session")


if __name__ == "__main__":
    unittest.main()
