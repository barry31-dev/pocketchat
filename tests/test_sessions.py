"""Session persistence tests: one conversation, one run id, resumable.

The property under test is that a session is a run, not a directory of runs.
A resumed conversation keeps the run id it started with, keeps the trace it
was writing to, and continues the event numbering in that file -- so the file
stays one run and still passes the validator. A session that minted a new id
per invocation would split one conversation into N runs that each claim to be
the whole thing.

The stub is a real local socket. Nothing here reaches a provider.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
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
                    "message": {"role": "assistant", "content": "stub reply"},
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


class SessionTests(unittest.TestCase):
    def setUp(self):
        STATE.mode = "ok"
        STATE.delay = 0.0
        STATE.requests.clear()
        # One server per test, not one shared by the module: a request from a
        # previous test can still be arriving when the next one starts
        # counting, which makes shared logs order dependent.
        self.server = HTTPServer(("127.0.0.1", 0), StubHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
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

    def _run(self, *args, env=None):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_API_KEY": "test-key-not-real"}
        if env:
            environ.update(env)
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base), env=environ, capture_output=True, text=True,
            timeout=120,
        )

    def _session_file(self):
        return self.base / "sessions" / "notes.jsonl"

    def _trace_file(self):
        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1, "expected exactly one trace file")
        return traces[0]

    def _events(self, path):
        return [json.loads(line) for line in
                Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

    # --- a session is one run, not a run per invocation ----------------

    def test_a_named_session_creates_both_files(self):
        config = self._config()
        result = self._run("run", "hello", "--session", "notes", "--config", str(config))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        session = self._session_file()
        self.assertTrue(session.is_file())
        self.assertTrue((self.base / "traces").is_dir())

    def test_a_new_session_uses_one_run_id_across_artifacts(self):
        config = self._config()
        result = self._run("run", "hello", "--session", "notes",
                           "--quiet", "--config", str(config))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        trace = self._trace_file()
        header = self._events(self._session_file())[0]
        events = self._events(trace)
        status = json.loads(result.stdout)

        self.assertEqual(status["run_id"], trace.stem)
        self.assertEqual(header["session"], trace.stem)
        self.assertEqual(events[0]["run_id"], trace.stem)

    def test_resuming_keeps_the_run_id_and_the_trace(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        first = self._trace_file()
        run_id = self._events(first)[0]["run_id"]
        second = self._run("run", "again", "--session", "notes", "--config", str(config))
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        # The same file, the same run id: not a second run beside the first.
        self.assertEqual(list((self.base / "traces").glob("*.jsonl")), [first])
        events = self._events(first)
        self.assertEqual({e["run_id"] for e in events}, {run_id})

    def test_resuming_refuses_a_different_explicit_trace_path(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        recorded_trace = self._trace_file()
        before = recorded_trace.read_bytes()
        alternate = self.base / "alternate.jsonl"

        result = self._run("run", "again", "--session", "notes",
                           "--trace", str(alternate), "--config", str(config))

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--trace", result.stderr)
        self.assertIn("records", result.stderr)
        self.assertFalse(alternate.exists())
        self.assertEqual(recorded_trace.read_bytes(), before)

    def test_resuming_continues_the_event_numbering(self):
        # The validator rejects a repeated event id, so a resumed run that
        # restarted at evt_000001 would leave a file that reads as corrupt.
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        self._run("run", "again", "--session", "notes", "--config", str(config))
        events = self._events(self._trace_file())
        ids = [event["event_id"] for event in events]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))

    def test_resuming_restores_the_conversation(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        before = len(STATE.requests[0]["messages"])
        self._run("run", "again", "--session", "notes", "--config", str(config))
        # The first exchange is still in the context, so the model sees it.
        roles = [m["role"] for m in STATE.requests[-1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertGreater(len(STATE.requests[-1]["messages"]), before)

    def test_a_new_session_starts_at_one(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        self.assertEqual(self._events(self._trace_file())[0]["event_id"], "evt_000001")

    # --- the conversation file -----------------------------------------

    def test_the_session_file_records_every_exchange(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        self._run("run", "again", "--session", "notes", "--config", str(config))
        records = [json.loads(line) for line in
                   self._session_file().read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["role"] for r in records[1:]],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual([r["content"] for r in records[1:]][0], "hello")

    def test_the_session_file_holds_no_credential(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        text = self._session_file().read_text(encoding="utf-8")
        self.assertNotIn("test-key-not-real", text)
        # The environment variable's name is the credential's only footprint.
        self.assertIn("POCKETCHAT_API_KEY", self._trace_file().read_text(
            encoding="utf-8"))

    def test_a_bare_name_and_an_explicit_path_address_the_same_file(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        explicit = self._session_file()
        result = self._run("show", str(explicit))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hello", result.stdout)

    def test_a_trace_path_also_finds_its_session(self):
        # The reader is pointed at the trace, which is the file `run` prints.
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        trace = self._trace_file()
        result = self._run("show", str(trace))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hello", result.stdout)

    def test_show_reports_a_session_that_does_not_exist(self):
        config = self._config()
        result = self._run("show", "no-such-session")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no-such-session", result.stderr)

    # --- export --------------------------------------------------------

    def test_export_twice_is_byte_identical(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        first = self.base / "first.export.jsonl"
        second = self.base / "second.export.jsonl"
        self.assertEqual(self._run("export", "notes", "--out", str(first)).returncode, 0)
        self.assertEqual(self._run("export", "notes", "--out", str(second)).returncode, 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_export_carries_the_session_and_the_events(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        out = self.base / "notes.export.jsonl"
        self.assertEqual(self._run("export", "notes", "--out", str(out)).returncode, 0)
        records = [json.loads(line) for line in
                   out.read_text(encoding="utf-8").splitlines()]
        kinds = [r["kind"] for r in records]
        self.assertEqual(kinds[0], "session")
        self.assertIn("message", kinds)
        self.assertIn("event", kinds)
        # The exported event is the same event, not a re-serialised one: an
        # export that regenerated ids could not be diffed against a trace.
        trace_ids = {e["event_id"] for e in self._events(self._trace_file())}
        self.assertEqual({r["event_id"] for r in records if "event_id" in r},
                         trace_ids)

    def test_export_does_not_overwrite_without_being_asked(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        for format_name in ("jsonl", "md", "trace"):
            with self.subTest(format=format_name):
                out = self.base / ("notes.export." + format_name)
                args = ("export", "notes", "--format", format_name, "--out", str(out))
                first = self._run(*args)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                before = out.read_bytes()
                result = self._run(*args)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("already exists", result.stderr)
                self.assertEqual(out.read_bytes(), before)

    def test_export_markdown_carries_the_conversation(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        out = self.base / "notes.export.md"
        self.assertEqual(self._run("export", "notes", "--format", "md", "--out", str(out)).returncode, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("hello", text)
        self.assertIn("stub reply", text)

    def test_reported_event_count_counts_events_not_lines(self):
        """``trace_events`` must equal the events written, not the newlines read.

        The mixed archive skips a malformed trace line and a blank one, so a
        count taken from the raw text reports a number the file does not
        contain. The count is the only statement the command makes about how
        much evidence it copied, so an inflated one claims more evidence than
        the export holds.
        """
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        trace = self._trace_file()
        expected = self._events(trace)
        # One blank line and one malformed line, both of which the archive
        # skips. Both are lines in the file, neither is an event.
        trace.write_text(trace.read_text(encoding="utf-8") + "\nnot json\n",
                          encoding="utf-8")

        for format_name in ("jsonl", "md"):
            with self.subTest(format=format_name):
                out = self.base / ("count." + format_name)
                result = self._run("export", "notes", "--format", format_name,
                                   "--out", str(out))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                reported = json.loads(result.stdout)["trace_events"]
                self.assertEqual(reported, len(expected))
                if format_name == "jsonl":
                    # The archive's own count of what it copied is the only
                    # check the file itself can give for this claim.
                    written = [json.loads(line) for line in
                               out.read_text(encoding="utf-8").splitlines()
                               if line.strip()]
                    self.assertEqual(
                        len([r for r in written if r.get("kind") == "event"]),
                        len(expected))

    def test_trace_export_preserves_the_original_events_and_bytes(self):
        config = self._config()
        run = self._run("run", "hello", "again", "--session", "notes",
                        "--config", str(config))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        trace = self._trace_file()
        expected = self._events(trace)
        # Valid whitespace and a missing final newline must not change any
        # bytes or cause the last event to disappear from the export count.
        source = b"\r\n".join(b"  " + line for line in trace.read_bytes().splitlines())
        trace.write_bytes(source)
        first = self.base / "sessions" / "notes.export.trace.jsonl"
        second = self.base / "second.trace.jsonl"
        for extra, out in (((), first), (("--out", str(second)), second)):
            result = self._run("export", "notes", "--format", "trace", *extra)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(out.read_bytes(), source)
            self.assertEqual(self._events(out), expected)
            self.assertEqual(json.loads(result.stdout)["trace_events"], len(expected))
            self.assertNotIn("test-key-not-real", out.read_text(encoding="utf-8"))
        self.assertEqual(expected[-1]["type"], "run.finished")
        self.assertTrue(all("kind" not in event for event in expected))

    def test_trace_export_does_not_require_a_conversation_file(self):
        config = self._config()
        run = self._run("run", "hello", "--config", str(config))
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        trace = self._trace_file()
        result = self._run("export", str(trace), "--format", "trace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = trace.with_name(trace.stem + ".export.trace.jsonl")
        self.assertEqual(out.read_bytes(), trace.read_bytes())
        self.assertFalse((self.base / "sessions").exists())
        for format_name in ("jsonl", "md"):
            refused = self._run("export", str(trace), "--format", format_name)
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertIn("has no session file", refused.stderr)

    def test_trace_export_refuses_bad_lines_without_writing_an_output(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        trace = self._trace_file()
        original = trace.read_bytes().splitlines()
        mixed = json.loads(original[1])
        mixed["kind"] = "event"
        bad_lines = (
            b'{"broken":', b'null', b'[]', b'"text"', b'{"session": "other"}',
            json.dumps(mixed).encode("utf-8"), b'{"invalid": NaN}', b'\xff',
        )
        for bad_line in bad_lines:
            trace.write_bytes(b"\n".join([original[0], bad_line, original[-1]]) + b"\n")
            for target in ("notes", str(trace)):
                with self.subTest(line=bad_line, target=target):
                    out = self.base / "new-directory" / "refused.jsonl"
                    result = self._run("export", target, "--format", "trace",
                                       "--out", str(out))
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertIn("line 2", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(out.parent.exists())

    def test_trace_export_refuses_an_empty_or_missing_trace(self):
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        trace = self._trace_file()
        out = self.base / "refused.jsonl"
        for body in (b"", b"\n  \n", None):
            with self.subTest(body=body):
                if body is None:
                    trace.unlink()
                else:
                    trace.write_bytes(body)
                result = self._run("export", "notes", "--format", "trace",
                                   "--out", str(out))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(str(trace), result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(out.exists())

    def test_trace_export_preserves_a_failed_runs_terminal_event(self):
        STATE.mode = "http_error"
        config = self._config()
        run = self._run("run", "hello", "--session", "notes", "--config", str(config))
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        trace = self._trace_file()
        out = self.base / "failed.trace.jsonl"
        exported = self._run("export", "notes", "--format", "trace", "--out", str(out))
        self.assertEqual(exported.returncode, 0, exported.stdout + exported.stderr)
        self.assertEqual(out.read_bytes(), trace.read_bytes())
        self.assertEqual(self._events(out)[-1]["type"], "run.failed")
        self.assertEqual(self._events(out)[-1]["payload"]["status"], "failed")

    # --- failure paths still leave a session ---------------------------

    def test_a_failed_turn_records_no_exchange(self):
        # A failed turn is the thing a post-mortem needs, so it stays in the
        # trace. It is not part of the conversation, so it stays out of the
        # session file: a failed exchange is not something the model can act on.
        STATE.mode = "http_error"
        config = self._config()
        self._run("run", "hello", "--session", "notes", "--config", str(config))
        records = [json.loads(line) for line in
                   self._session_file().read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[1:], [])
        events = self._events(self._trace_file())
        self.assertIn("model.response.failed", [e["type"] for e in events])

    def test_a_session_offered_that_is_not_one_is_refused(self):
        config = self._config()
        (self.base / "sessions").mkdir(parents=True, exist_ok=True)
        (self.base / "sessions" / "bogus.jsonl").write_text("not a session\n")
        result = self._run("show", "bogus")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("not a pocketchat session", result.stderr)

    # --- a session path that cannot be read or written says so, once -----

    def test_a_session_path_that_is_a_directory_is_refused(self):
        config = self._config()
        (self.base / "adir").mkdir()
        result = self._run("run", "hello", "--session", str(self.base / "adir"),
                           "--config", str(config))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("is a directory, not a session file", result.stderr)

    def test_an_unreadable_session_file_is_refused(self):
        config = self._config()
        blocked = self.base / "blocked.jsonl"
        blocked.write_text(json.dumps(
            {"session": "abc", "trace": "traces/abc.jsonl",
             "created": "2026-10-18T00:00:00Z",
             "pocketchat_version": "0.1.0"}) + "\n", encoding="utf-8")
        blocked.chmod(0)
        try:
            result = self._run("run", "hello", "--session", str(blocked),
                               "--config", str(config))
        finally:
            blocked.chmod(0o644)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot read", result.stderr)

    def test_an_unwritable_session_directory_is_refused(self):
        config = self._config()
        locked = self.base / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        target = locked / "notes.jsonl"
        try:
            result = self._run("run", "hello", "--session", str(target),
                               "--config", str(config))
        finally:
            locked.chmod(0o755)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot write", result.stderr)

    def test_a_refused_session_leaves_no_trace_behind(self):
        # The config is prepared before any trace file is created, so a session
        # path that cannot be used must not leave a half-written run on disk.
        config = self._config()
        (self.base / "adir").mkdir()
        self._run("run", "hello", "--session", str(self.base / "adir"),
                  "--config", str(config))
        traces = self.base / "traces"
        self.assertFalse(traces.is_dir() and any(traces.glob("*.jsonl")),
                         "a refused run wrote a trace file")


    # --- a session is opt-in, and the help text says so --------------------

    def test_a_run_without_a_session_writes_no_conversation_file(self):
        # `--session` is opt in. A run that never asked for a conversation file
        # must not grow one, because the flag's own help text says it is only
        # "name or resume a session" -- it promised no default, and a silent
        # default would write the full text of every exchange somewhere the
        # operator never asked for.
        config = self._config()
        self._run("run", "hello", "--config", str(config))
        self.assertFalse((self.base / "sessions").exists(),
                         "a run without --session created a sessions directory")

    def test_the_help_text_promises_no_default_session(self):
        # The `--session` help used to claim a default of
        # ./sessions/<id>.jsonl that the code did not implement either way: a
        # bare `run` wrote no session file at all. A flag's help text is the
        # one place an operator can check what will be written to disk, so it
        # names what the flag does and nothing more.
        for command in ("run", "chat"):
            result = self._run(command, "--help")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            session_lines = [line.strip() for line in result.stdout.splitlines()
                             if line.strip().startswith("--session ")]
            self.assertEqual(len(session_lines), 1, result.stdout)
            self.assertNotIn("default", session_lines[0], session_lines[0])

    def test_trace_help_explains_the_resume_ownership_rule(self):
        for command in ("run", "chat"):
            result = self._run(command, "--help")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(
                "on resume, must match the session trace",
                " ".join(result.stdout.split()),
            )

    def test_the_required_positional_help_promises_no_default(self):
        # `show` and `export` both describe their positional as "a session
        # file or its trace (default: ./sessions/<id>.jsonl)", but the
        # argument is required: omitting it is a usage error, exit 2, and no
        # file is read or written. A default that does not exist is a promise
        # about a value the parser never produces, and it points at a `<id>`
        # the operator has no way to know.
        for command in ("show", "export"):
            result = self._run(command, "--help")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("default: ./sessions", result.stdout, result.stdout)

    def test_the_required_positional_is_refused_rather_than_defaulted(self):
        # The other half of the same claim: the help text says a default
        # exists, so the behaviour has to match it. Both commands refuse the
        # missing argument before any file is touched, which is what a
        # required positional does -- and the reason the "default" in its
        # help is a lie.
        for command in ("show", "export"):
            result = self._run(command)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("the following arguments are required", result.stderr)
            self.assertFalse(list(self.base.glob("sessions/*")),
                             "%s read or wrote a session file" % command)
            self.assertFalse(list(self.base.glob("traces/*")),
                             "%s read or wrote a trace file" % command)

if __name__ == "__main__":
    unittest.main()
