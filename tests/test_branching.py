"""Branch derivation tests: a second path through one conversation.

The property under test is that branching records *where* the second path
leaves the first, and that the parent file is not touched to say so. A branch
whose links live only in a process's memory is a branch a later reader cannot
see; a branch created by rewriting the parent is a branch that lost the
append-only promise the session format depends on.

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

from pocketchat.branching import branch_path, derive_branch, BranchError

ROOT = Path(__file__).resolve().parents[1]


class StubState:
    def __init__(self):
        self.requests = []


STATE = StubState()


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        STATE.requests.append(json.loads(raw.decode("utf-8")) if raw else {})
        body = json.dumps({
            "id": "cmpl-stub", "model": "example-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant",
                                     "content": "stub reply"},
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


class BranchTestBase(unittest.TestCase):
    def setUp(self):
        STATE.requests.clear()
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

    def _config(self):
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "1",
            "provider": {
                "base_url": "http://127.0.0.1:%d/v1" % self.port,
                "model": "example-model",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")
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

    def _messages(self, path):
        return [json.loads(line) for line in
                Path(path).read_text(encoding="utf-8").splitlines()[1:]]


class BranchIdentityTests(BranchTestBase):
    """The links a branch writes down, and the ones it refuses to invent."""

    def _parent(self, turns=2):
        """A real v0.3 session with ``turns`` message lines."""
        from pocketchat.sessions import SessionFile
        path = self.base / "sessions" / "notes.jsonl"
        store = SessionFile.open(path, "run_parent00000000000_aaaaaaaaaaaa",
                                 Path("traces/parent.jsonl"), "0.3.0")
        for number in range(turns):
            store.append("user" if number % 2 == 0 else "assistant",
                         "line %d" % number)
        return path, store

    def test_a_branch_names_the_conversation_and_the_turn_it_forked_from(self):
        path, store = self._parent()

        result = derive_branch(path, "turn_000002")

        self.assertEqual(result["conversation_id"], store.conversation_id)
        self.assertEqual(result["parent_branch_id"], store.branch_id)
        self.assertEqual(result["parent_turn_id"], "turn_000002")
        self.assertEqual(result["turns"], 2)
        self.assertNotEqual(result["branch_id"], store.branch_id)
        self.assertEqual([m["content"] for m in result["messages"]],
                         ["line 0", "line 1"])

    def test_describing_a_branch_writes_nothing(self):
        path, _ = self._parent()
        before = path.read_bytes()

        derive_branch(path, "turn_000002", "child")

        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.base / "sessions" / "child.jsonl").exists())

    def test_a_turn_that_was_never_answered_is_refused(self):
        # Forking from the user line would make the child start from a
        # question the parent asked and never sent.
        path, _ = self._parent(turns=3)

        with self.assertRaises(BranchError) as caught:
            derive_branch(path, "turn_000003")

        self.assertIn("completed exchange", str(caught.exception))
        self.assertEqual(len(self._messages(path)), 3,
                         "a refused branch still did not write anything")

    def test_an_unknown_turn_is_refused(self):
        path, _ = self._parent()

        with self.assertRaises(BranchError) as caught:
            derive_branch(path, "turn_000009")

        self.assertIn("has no turn", str(caught.exception))

    def test_a_legacy_file_cannot_be_branched(self):
        # v0.1/v0.2 files carry no conversation id. Minting one here would
        # link a child to a parent that cannot name the same link back.
        path = self.base / "sessions" / "legacy.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "session": "run_legacy0000000000_abcdef012345",
            "trace": "../traces/run_legacy0000000000_abcdef012345.jsonl",
            "created": "2026-10-19T00:00:00Z",
            "pocketchat_version": "0.2.0",
        }, sort_keys=True) + "\n" + json.dumps(
            {"role": "user", "content": "old"}, sort_keys=True) + "\n",
            encoding="utf-8")

        with self.assertRaises(BranchError) as caught:
            derive_branch(path, "turn_000001")

        self.assertIn("no conversation id", str(caught.exception))

    def test_a_missing_session_is_refused(self):
        # The reader creates the file it is handed, so the check has to come
        # first: `branch` must not invent a conversation to branch from.
        with self.assertRaises(BranchError) as caught:
            derive_branch(self.base / "sessions" / "absent.jsonl", "turn_000002")

        self.assertIn("no such session", str(caught.exception))
        self.assertFalse((self.base / "sessions" / "absent.jsonl").exists())

    def test_a_named_child_that_already_exists_is_refused(self):
        path, _ = self._parent()
        existing = self.base / "sessions" / "child.jsonl"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("{}", encoding="utf-8")

        result = self._run("branch", "notes", "--from", "turn_000002",
                           "--name", "child")

        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("already exists", result.stderr)
        self.assertEqual(existing.read_text(encoding="utf-8"), "{}")

    def test_the_named_path_follows_the_session_name_convention(self):
        # Same rule `--session` uses: a bare name lives in ./sessions, a value
        # that carries a directory is taken as written.
        self.assertEqual(branch_path("child", "sessions"),
                         Path("sessions/child.jsonl"))
        self.assertEqual(branch_path("elsewhere/x", "sessions"),
                         Path("elsewhere/x"))


class BranchContinuationTests(BranchTestBase):
    """End to end: branch, continue it, and read both files back."""

    def test_a_branch_continues_the_conversation_from_the_turn(self):
        config = self._config()
        self._run("run", "q1", "--session", "notes", "--config", str(config))
        self._run("run", "q2", "--session", "notes", "--config", str(config))
        parent = self.base / "sessions" / "notes.jsonl"
        parent_before = parent.read_bytes()

        described = self._run("branch", "notes", "--from", "turn_000002",
                             "--name", "fork")
        self.assertEqual(described.returncode, 0,
                         described.stdout + described.stderr)

        self._run("run", "q3", "--session", "fork", "--config", str(config))

        child = self.base / "sessions" / "fork.jsonl"
        self.assertTrue(child.is_file())

        # The parent is untouched: a branch is recorded in the child.
        self.assertEqual(parent.read_bytes(), parent_before)

        # The child names the parent branch and the turn it forked from, so a
        # reader of the child alone can find its way back.
        header = json.loads(child.read_text(encoding="utf-8").splitlines()[0])
        parent_header = json.loads(
            parent.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(header["conversation_id"],
                         parent_header["conversation_id"])
        self.assertEqual(header["parent_branch_id"],
                         parent_header["branch_id"])
        self.assertEqual(header["parent_turn_id"], "turn_000002")
        self.assertNotEqual(header["branch_id"], parent_header["branch_id"])

        # The child has its own run id and its own trace, so appending to the
        # child can never put a second run id into the parent's trace.
        self.assertNotEqual(header["session"], parent_header["session"])
        self.assertTrue((child.parent / header["trace"]).resolve().is_file(),
                        header["trace"])

        # The child carries the turns it inherited from the parent.
        self.assertEqual([m["turn_id"] for m in self._messages(child)],
                         ["turn_000001", "turn_000002",
                          "turn_000003", "turn_000004"])

    def test_show_reads_the_child_and_the_parent_as_one_conversation(self):
        config = self._config()
        self._run("run", "q1", "--session", "notes", "--config", str(config))
        self._run("run", "q2", "--session", "notes", "--config", str(config))
        parent_turns = [json.loads(line) for line in
                        (self.base / "sessions" / "notes.jsonl")
                        .read_text(encoding="utf-8").splitlines()[1:]]
        self._run("branch", "notes", "--from", "turn_000002",
                  "--name", "fork")
        self._run("run", "q3", "--session", "fork", "--config", str(config))

        shown = self._run("show", "fork")
        self.assertEqual(shown.returncode, 0, shown.stdout + shown.stderr)
        # The child starts where the parent stopped, so it holds the first
        # exchange and not the second -- the fork point is what was named.
        self.assertIn("q1", shown.stdout)
        self.assertNotIn("q2", shown.stdout)
        self.assertIn("q3", shown.stdout)
        self.assertIn("turn_000004", shown.stdout)
        # And the parent still holds its second exchange: the fork did not
        # move it out.
        self.assertEqual([m["content"] for m in parent_turns],
                         ["q1", "stub reply", "q2", "stub reply"])


if __name__ == "__main__":
    unittest.main()
