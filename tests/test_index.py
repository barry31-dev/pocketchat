"""Index tests: a derived surface that never becomes a second truth.

The properties under test are the five the index's contract names. The
index is pure derivation (its rows come from the workspace's files and
nothing else), it is disposable (deleting it changes no answer any command
gives), it rebuilds whole (no incremental update to drift), it reports its
own staleness against the digests of its sources, and a rebuild is accepted
on the one measure that matters: the same sources give the same rows.

One property is pinned empirically rather than promised: on this machine's
SQLite, two builds over the same bytes produce byte-identical files (the
index carries no clock; verified 2026-10-24). If a future SQLite breaks
that, the failure documents a real boundary move -- what stays promised is
logical equivalence, never bytes across versions.
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

from pocketchat.indexing import (INDEX_FILENAME, IndexingError, build_index,
                                 dump_rows, index_status)

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


class IndexTestBase(unittest.TestCase):
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

    def _cli(self, *args):
        environ = {"POCKETCHAT_LOCAL_KEY": "test-key-not-real"}
        return hp.run_cli(list(args), self.base, env=environ)

    def _cli_subprocess(self, *args):
        """The golden path: a real interpreter, a real pipe, a real exit."""
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "test-key-not-real"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base), env=environ, capture_output=True, text=True,
            timeout=120)

    def _config(self):
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "2", "name": "workbench",
            "default_profile": "local",
            "profiles": {"local": {
                "base_url": "http://127.0.0.1:%d/v1" % self.port,
                "model": "example-model",
                "api_key_env": "POCKETCHAT_LOCAL_KEY",
            }},
        }), encoding="utf-8")
        return path

    def _conversation(self):
        config = self._config()
        self._cli("run", "q1", "--session", "notes", "--config", str(config))
        self._cli("run", "q2", "--session", "notes", "--config", str(config))
        self._cli("branch", "notes", "--from", "turn_000002", "--name", "fork")
        self._cli("run", "q3", "--session", "fork", "--config", str(config))
        return config

    def _comparison(self, config):
        result = self._cli("compare", "which one?", "--profiles", "local",
                           "--config", str(config))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _evidence_digests(self):
        """Digests of the workspace, the index itself excluded."""
        digests = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file() and path.name != INDEX_FILENAME:
                digests[str(path.relative_to(self.base))] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return digests


class IndexBuildTests(IndexTestBase):
    def test_the_index_derives_every_row_from_the_files(self):
        config = self._conversation()
        self._comparison(config)
        result = self._cli("index", "--rebuild")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        rows = dump_rows(self.base / INDEX_FILENAME)
        self.assertEqual(len(rows["conversation_branches"]), 2)
        self.assertEqual(len(rows["comparisons"]), 1)
        self.assertEqual(len(rows["comparison_runs"]), 1)
        # Plain `run` commands write request snapshots no manifest claims --
        # the honest report lists them; what must be empty is everything else.
        self.assertEqual([p for p in rows["problems"]
                          if p[0] != "unlinked_request"], [])
        self.assertEqual(len(rows["problems"]), 3)

        status = self._cli("index", "--json")
        record = json.loads(status.stdout)
        self.assertEqual(record["status"], "fresh")
        self.assertEqual(record["counts"]["conversation_branches"], 2)

    def test_the_index_carries_the_selection(self):
        config = self._conversation()
        self._comparison(config)
        self._cli("select", _first_comparison_id(self.base), "local")
        result = self._cli("index", "--rebuild")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        rows = dump_rows(self.base / INDEX_FILENAME)
        run_id = json.loads(next(
            p for p in (self.base / "comparisons").glob("*.json")
            if not p.name.endswith(".selection.json")
        ).read_text(encoding="utf-8"))["runs"][0]["run_id"]
        selection = rows["comparisons"][0]
        self.assertEqual(selection[3], run_id)      # selection_run_id
        self.assertEqual(selection[4], "local")     # selection_profile

    def test_a_rebuild_yields_the_same_rows(self):
        # The acceptance measure for a rebuild is logical equivalence: the
        # same sources give the same rows in the same order.
        config = self._conversation()
        self._comparison(config)
        self._cli("index", "--rebuild")
        before = dump_rows(self.base / INDEX_FILENAME)
        self._cli("index", "--rebuild")
        self.assertEqual(dump_rows(self.base / INDEX_FILENAME), before)

    def test_bytes_are_reproducible_on_this_machine(self):
        # Pinned empirically (2026-10-24, this SQLite): the index carries no
        # clock, so two builds over the same bytes agree byte for byte. A
        # future SQLite breaking this documents a boundary move; what stays
        # promised everywhere is the row equivalence above.
        config = self._conversation()
        self._comparison(config)
        self._cli("index", "--rebuild")
        first = hashlib.sha256(
            (self.base / INDEX_FILENAME).read_bytes()).hexdigest()
        self._cli("index", "--rebuild")
        second = hashlib.sha256(
            (self.base / INDEX_FILENAME).read_bytes()).hexdigest()
        self.assertEqual(first, second)

    def test_building_over_an_existing_index_is_refused(self):
        # The CLI's plain `index` is a status report; the refusal lives at
        # the build API, where a silent replacement would be a rebuild the
        # operator never asked for.
        config = self._conversation()
        self._cli("index", "--rebuild")
        with self.assertRaises(IndexingError) as caught:
            build_index(self.base, rebuild=False)
        self.assertIn("--rebuild", str(caught.exception))

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the index family: the subprocess path
        # stays in the suite; everything else here runs in process for speed.
        self._conversation()
        result = self._cli_subprocess("index", "--rebuild")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        again = self._cli_subprocess("index")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("fresh", again.stdout)


class IndexFreshnessTests(IndexTestBase):
    def test_a_changed_source_marks_the_index_stale_by_name(self):
        config = self._conversation()
        self._cli("index", "--rebuild")
        victim = self.base / "sessions" / "notes.jsonl"
        victim.write_text(victim.read_text(encoding="utf-8") + "\n",
                          encoding="utf-8")

        result = self._cli("index")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stale", result.stdout)
        self.assertIn("sessions/notes.jsonl", result.stdout)

    def test_a_new_source_marks_the_index_stale(self):
        config = self._conversation()
        self._cli("index", "--rebuild")
        self._cli("run", "q4", "--session", "notes", "--config", str(config))
        status = json.loads(self._cli("index", "--json").stdout)
        self.assertEqual(status["status"], "stale")
        self.assertIn("sessions/notes.jsonl", status["stale_files"])

    def test_a_rebuild_restores_freshness(self):
        config = self._conversation()
        self._cli("index", "--rebuild")
        self._cli("run", "q4", "--session", "notes", "--config", str(config))
        self.assertEqual(self._cli("index").returncode, 1)
        self.assertEqual(self._cli("index", "--rebuild").returncode, 0)
        self.assertEqual(json.loads(self._cli("index", "--json").stdout)
                         ["status"], "fresh")

    def test_a_missing_index_is_a_report_not_an_error(self):
        self._conversation()
        result = self._cli("index", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "missing")

    def test_the_index_refuses_a_workspace_that_is_not_there(self):
        result = self._cli("index", "no-such-workspace")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class IndexDisposableTests(IndexTestBase):
    def test_deleting_the_index_changes_no_answer(self):
        config = self._conversation()
        self._comparison(config)
        before = self._evidence_digests()
        outputs = [self._cli(command).stdout for command in
                   ("workspace", "history", "search q1", "diff notes fork")]

        self._cli("index", "--rebuild")
        self.assertEqual(self._evidence_digests(), before,
                         "building the index wrote nothing but the index")
        (self.base / INDEX_FILENAME).unlink()

        for command, output in zip(("workspace", "history", "search q1",
                                    "diff notes fork"), outputs):
            self.assertEqual(self._cli(command).stdout, output)


def _first_comparison_id(base: Path) -> str:
    manifests = sorted((base / "comparisons").glob("*.json"),
                       key=lambda p: p.name.endswith(".selection.json"))
    return json.loads(manifests[0].read_text(encoding="utf-8"))["comparison_id"]


if __name__ == "__main__":
    unittest.main()
