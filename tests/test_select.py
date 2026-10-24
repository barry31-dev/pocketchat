"""Selection tests: a comparison's answer is recorded, never guessed.

The property under test is that `select` writes the one fact the workspace
was missing -- which answer was kept -- as its own exclusive-create record
beside the manifest, never into the manifest itself and never into a trace.
The second property is that writing is fail-closed: everything `select`
needs is checked before the first byte lands, so a refused selection leaves
the workspace byte-identical.

`--into` is the half that closes the loop the M3 criteria name: the selected
exchange becomes the first two lines of a session a later `run` continues.
It writes only what the evidence records as complete -- a comparison that
ran without ``--verbose`` carries a summary, and a summary written into a
session would be read as the whole answer -- so it is refused instead.

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

from tests import _helpers as hp

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


class SelectTestBase(unittest.TestCase):
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

    def _cli(self, *args, env=None):
        environ = {"POCKETCHAT_LOCAL_KEY": "test-key-not-real",
                   "POCKETCHAT_HOSTED_KEY": "test-key-not-real"}
        if env:
            environ.update(env)
        return hp.run_cli(list(args), self.base, env=environ)

    def _cli_subprocess(self, *args):
        """The golden path: a real interpreter, a real pipe, a real exit."""
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "test-key-not-real",
                   "POCKETCHAT_HOSTED_KEY": "test-key-not-real"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base), env=environ, capture_output=True, text=True,
            timeout=120)

    def _config(self, verbose=False):
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "workbench",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % self.port,
                    "model": "local-model",
                    "api_key_env": "POCKETCHAT_LOCAL_KEY",
                },
                "hosted": {
                    # A port nothing listens on: its run fails, which is how
                    # the suite gets a manifest with one live and one dead run
                    # without a second stub.
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "hosted-model",
                    "api_key_env": "POCKETCHAT_HOSTED_KEY",
                },
            },
        }), encoding="utf-8")
        return path

    def _compare(self, verbose=False):
        config = self._config(verbose=verbose)
        result = self._cli("compare", "which one is better?",
                           "--profiles", "local,hosted",
                           "--config", str(config),
                           *(("--verbose",) if verbose else ()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = next((self.base / "comparisons").glob("*.json"))
        return config, manifest, json.loads(manifest.read_text(encoding="utf-8"))

    def _digests(self):
        digests = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file():
                digests[str(path.relative_to(self.base))] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return digests

    def _workspace(self):
        result = self._cli("workspace", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)


class SelectionRecordTests(SelectTestBase):
    def test_select_records_the_chosen_run_beside_the_manifest(self):
        config, manifest, record = self._compare()
        run = record["runs"][0]
        result = self._cli("select", record["comparison_id"], run["run_id"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        path = self.base / "comparisons" / (
            record["comparison_id"] + ".selection.json")
        self.assertTrue(path.is_file())
        selection = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(selection["schema_version"], "1")
        self.assertEqual(selection["comparison_id"], record["comparison_id"])
        self.assertEqual(selection["run_id"], run["run_id"])
        self.assertEqual(selection["profile"], run["profile"])
        self.assertTrue(selection["selected_at"])

    def test_select_accepts_an_unambiguous_profile_name(self):
        config, manifest, record = self._compare()
        result = self._cli("select", record["comparison_id"], "local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        selection = json.loads(
            (self.base / "comparisons" / (
                record["comparison_id"] + ".selection.json")
             ).read_text(encoding="utf-8"))
        self.assertEqual(selection["run_id"], record["runs"][0]["run_id"])

    def test_select_refuses_a_run_the_manifest_does_not_record(self):
        config, manifest, record = self._compare()
        before = self._digests()
        result = self._cli("select", record["comparison_id"], "run_nope")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(
            list((self.base / "comparisons").glob("*.selection.json")),
            "a refused selection wrote a file")
        self.assertEqual(self._digests(), before)

    def test_select_refuses_a_second_selection_of_one_comparison(self):
        config, manifest, record = self._compare()
        first = self._cli("select", record["comparison_id"], "local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        after = self._digests()

        second = self._cli("select", record["comparison_id"], "hosted")
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertIn("already", second.stderr)
        self.assertEqual(self._digests(), after)

    def test_select_refuses_a_comparison_that_does_not_exist(self):
        result = self._cli("select", "cmp_nope", "local")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_selecting_a_failed_run_is_a_record_not_an_error(self):
        # The manifest carries the dead run too; recording that its answer
        # was kept is odd but honest -- the selection is a record of a
        # choice, and only --into needs an answer that exists.
        config, manifest, record = self._compare()
        dead = [r for r in record["runs"] if r["status"] != "completed"][0]
        result = self._cli("select", record["comparison_id"], dead["run_id"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_workspace_reports_the_selection(self):
        # The graph's one invented field -- selection was always "not
        # recorded" -- now says what a selection file records, and still says
        # "not recorded" when there is none.
        config, manifest, record = self._compare()
        before = self._workspace()
        self.assertEqual(before["comparisons"][0]["selection"],
                         "not recorded")

        self._cli("select", record["comparison_id"], "local", "--note", "the fast one")
        after = self._workspace()
        selection = after["comparisons"][0]["selection"]
        self.assertEqual(selection["run_id"], record["runs"][0]["run_id"])
        self.assertEqual(selection["profile"], "local")
        self.assertEqual(selection["note"], "the fast one")
        self.assertTrue(selection["path"].endswith(".selection.json"))
        self.assertEqual(after["unreadable"], [],
                         "the selection file is not a broken manifest")

        text = self._cli("workspace")
        self.assertIn("selection %s" % record["runs"][0]["run_id"],
                      text.stdout)

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the select family: the subprocess path
        # stays in the suite; everything else here runs in process for speed.
        config, manifest, record = self._compare()
        result = self._cli_subprocess(
            "select", record["comparison_id"], "local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("selection", result.stdout)


class SelectionIntoTests(SelectTestBase):
    def test_select_into_starts_a_session_the_next_run_continues(self):
        # The closed loop the M3 criteria name: compare, keep one, keep
        # talking, export. The session is a new run with its own trace; the
        # selection file is what ties it back to the comparison.
        config, manifest, record = self._compare(verbose=True)
        result = self._cli("select", record["comparison_id"], "local",
                           "--into", "kept")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        session = self.base / "sessions" / "kept.jsonl"
        self.assertTrue(session.is_file())
        lines = [json.loads(line)
                 for line in session.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        self.assertNotEqual(lines[0]["session"],
                            record["runs"][0]["run_id"],
                            "the new session names its own run, not the "
                            "compare run's")
        self.assertEqual(lines[1]["role"], "user")
        self.assertEqual(lines[1]["content"], "which one is better?")
        self.assertEqual(lines[2]["role"], "assistant")
        self.assertEqual(lines[2]["content"], "the stub reply")

        # ...and the conversation continues the ordinary way.
        follow = self._cli("run", "and then?", "--session", "kept")
        self.assertEqual(follow.returncode, 0, follow.stdout + follow.stderr)
        lines = [json.loads(line)
                 for line in session.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        self.assertEqual(len(lines), 5, "header + two selected + two new")

        exported = self._cli("export", "kept", "--format", "md")
        self.assertEqual(exported.returncode, 0,
                         exported.stdout + exported.stderr)

    def test_select_into_resumes_an_existing_session(self):
        config, manifest, record = self._compare(verbose=True)
        self._cli("run", "earlier question", "--session", "notes",
                  "--config", str(config))
        before = (self.base / "sessions" / "notes.jsonl").read_text(
            encoding="utf-8")

        result = self._cli("select", record["comparison_id"], "local",
                           "--into", "notes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        after = (self.base / "sessions" / "notes.jsonl").read_text(
            encoding="utf-8")
        self.assertTrue(after.startswith(before),
                        "an existing session is appended to, never rewritten")
        self.assertIn("which one is better?", after)

    def test_select_into_refuses_a_summary_only_comparison(self):
        # A comparison that ran without --verbose carries a prefix, not the
        # answer. Writing it into a session would put a truncated reply where
        # a reader expects the whole one, so the refusal comes before any
        # byte is written -- the selection file included.
        config, manifest, record = self._compare(verbose=False)
        before = self._digests()
        result = self._cli("select", record["comparison_id"], "local",
                           "--into", "kept")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("verbose", result.stderr)
        self.assertEqual(self._digests(), before)

    def test_select_into_refuses_a_run_without_a_completed_answer(self):
        config, manifest, record = self._compare(verbose=True)
        dead = [r for r in record["runs"] if r["status"] != "completed"][0]
        before = self._digests()
        result = self._cli("select", record["comparison_id"], dead["run_id"],
                           "--into", "kept")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self._digests(), before)

    def test_select_into_refuses_a_trace_that_is_not_in_the_workspace(self):
        config, manifest, record = self._compare(verbose=True)
        trace = self.base / record["runs"][0]["trace"]
        trace.unlink()
        before = self._digests()
        result = self._cli("select", record["comparison_id"], "local",
                           "--into", "kept")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(self._digests(), before)


if __name__ == "__main__":
    unittest.main()
