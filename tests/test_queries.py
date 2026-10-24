"""Query tests: history, search and diff answer from the files alone.

The property under test is the one the reconstruction carries: a query's
answer is a reading of the workspace's bytes, so the same files give the
same answer in any process, and nothing a query does changes a byte.

The second property is that the answers invent nothing. A turn id is derived
from a line's position when the line carries none, a hit names the file it
was found in rather than the conversation it was probably part of, and a
diff that disagrees with a recorded fork point says so instead of choosing
the recorded one.

Most fixtures are written by hand, because the session file is the contract:
a query that answers from the bytes a v0.3 file actually holds is correct
even if no command wrote those bytes. One test builds the workspace with the
real commands, against a stub, to prove the same reading over the files the
product itself writes.
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

from pocketchat.queries import (QueryError, collect_history, diff_sessions,
                                render_diff_lines, render_history_lines,
                                render_search_lines, search_messages)

ROOT = Path(__file__).resolve().parents[1]

CONVERSATION = "conv_20261024t000000_aa000000"
ROOT_BRANCH = "branch_20261024t000000_bb000000"
FORK_BRANCH = "branch_20261024t000000_cc000000"
OTHER_CONVERSATION = "conv_20261024t000000_dd000000"
OTHER_BRANCH = "branch_20261024t000000_ee000000"


def write_session(base: Path, name: str, messages, conversation_id=None,
                  branch_id=None, parent_branch_id=None, parent_turn_id=None,
                  version="0.3.0", turn_ids=True, trace=None) -> Path:
    """Write one session file, in the shape the version says it has.

    The identity keys are written only when there is an identity to write,
    which is how the writer itself behaves: a v0.2 file has neither the
    header keys nor the turn ids, and reading it must not depend on their
    being there.
    """
    path = base / "sessions" / (name + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "session": "run_20261024t000000_%s0000" % name[:8],
        "trace": trace if trace is not None else "../traces/run_%s.jsonl" % name,
        "created": "2026-10-24T00:00:00Z",
        "pocketchat_version": version,
    }
    if conversation_id is not None:
        header["conversation_id"] = conversation_id
        header["branch_id"] = branch_id
    if parent_branch_id is not None:
        header["parent_branch_id"] = parent_branch_id
        header["parent_turn_id"] = parent_turn_id
    lines = [json.dumps(header, sort_keys=True)]
    for number, (role, content) in enumerate(messages, 1):
        record = {"role": role, "content": content}
        if turn_ids:
            record["turn_id"] = "turn_%06d" % number
        lines.append(json.dumps(record, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def workspace_with_fork(base: Path) -> None:
    """One conversation with a fork at turn two, and one unrelated session."""
    root = [
        ("user", "q1 about the retry policy"),
        ("assistant", "a1 first answer"),
        ("user", "q2 about the timeout"),
        ("assistant", "a2 second answer"),
    ]
    fork = root[:2] + [
        ("user", "q3 a different third question"),
        ("assistant", "a3 the fork's own answer"),
    ]
    write_session(base, "notes", root, CONVERSATION, ROOT_BRANCH)
    write_session(base, "fork", fork, CONVERSATION, FORK_BRANCH,
                  parent_branch_id=ROOT_BRANCH, parent_turn_id="turn_000002")
    write_session(base, "other", [("user", "an unrelated question"),
                                  ("assistant", "an unrelated answer")],
                  OTHER_CONVERSATION, OTHER_BRANCH)


class QueryTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base),
            env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=60,
        )

    def _digests(self):
        digests = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file():
                digests[str(path.relative_to(self.base))] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return digests


class HistoryTests(QueryTestBase):
    def test_history_lists_the_turns_of_every_branch(self):
        workspace_with_fork(self.base)
        record = collect_history(self.base)

        self.assertEqual(len(record["conversations"]), 2)
        conversation = record["conversations"][0]
        self.assertEqual(conversation["conversation_id"], CONVERSATION)
        self.assertEqual([b["path"] for b in conversation["branches"]],
                         ["sessions/fork.jsonl", "sessions/notes.jsonl"])
        notes = conversation["branches"][1]
        fork = conversation["branches"][0]
        self.assertEqual(notes["branch_id"], ROOT_BRANCH)
        self.assertEqual(
            [t["turn_id"] for t in notes["turns"]],
            ["turn_%06d" % n for n in range(1, 5)])
        self.assertEqual(notes["turns"][0]["content"], "q1 about the retry policy")
        # The fork inherited the first two exchanges verbatim, turn ids and
        # all, so the same id names the same exchange in both files.
        self.assertEqual(fork["turns"][:2], notes["turns"][:2])
        self.assertEqual(fork["parent_branch_id"], ROOT_BRANCH)
        self.assertEqual(fork["parent_turn_id"], "turn_000002")

    def test_history_reports_old_files_without_inventing_ids(self):
        write_session(self.base, "old", [("user", "old question"),
                                         ("assistant", "old answer")],
                      version="0.2.0", turn_ids=False)
        record = collect_history(self.base)

        self.assertEqual(len(record["conversations"]), 1)
        conversation = record["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "not recorded")
        branch = conversation["branches"][0]
        self.assertIsNone(branch["conversation_id"])
        self.assertIsNone(branch["branch_id"])
        # The ids come from the lines' positions, so a file that never wrote
        # one still has its exchanges named.
        self.assertEqual([t["turn_id"] for t in branch["turns"]],
                         ["turn_000001", "turn_000002"])

    def test_history_targets_one_branch_by_name(self):
        workspace_with_fork(self.base)
        record = collect_history(self.base, "notes")

        self.assertEqual(len(record["conversations"]), 1)
        conversation = record["conversations"][0]
        self.assertEqual(conversation["conversation_id"], CONVERSATION)
        self.assertEqual([b["path"] for b in conversation["branches"]],
                         ["sessions/notes.jsonl"])

    def test_history_targets_a_conversation_or_branch_by_id(self):
        workspace_with_fork(self.base)
        by_conversation = collect_history(self.base, CONVERSATION)
        self.assertEqual(
            [b["path"] for c in by_conversation["conversations"]
             for b in c["branches"]],
            ["sessions/fork.jsonl", "sessions/notes.jsonl"])

        by_branch = collect_history(self.base, FORK_BRANCH)
        self.assertEqual(
            [b["path"] for c in by_branch["conversations"]
             for b in c["branches"]],
            ["sessions/fork.jsonl"])

    def test_history_refuses_a_target_nothing_records(self):
        workspace_with_fork(self.base)
        with self.assertRaises(QueryError):
            collect_history(self.base, "no-such-session")
        result = self._cli("history", "no-such-session")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no-such-session", result.stderr)

    def test_history_json_carries_no_absolute_path(self):
        workspace_with_fork(self.base)
        record = collect_history(self.base)
        self.assertNotIn(str(self.base), json.dumps(record))

    def test_history_text_names_the_fork_point(self):
        workspace_with_fork(self.base)
        record = collect_history(self.base)
        text = "\n".join(render_history_lines(record))
        self.assertIn("conversation %s" % CONVERSATION, text)
        self.assertIn("from %s  at turn_000002" % ROOT_BRANCH, text)
        self.assertIn("turn_000003", text)


class SearchTests(QueryTestBase):
    def test_search_finds_every_occurrence_where_it_is_recorded(self):
        workspace_with_fork(self.base)
        record = search_messages("retry policy", self.base)

        # The inherited exchange is in both files, so both report it: a hit
        # is a fact about a file, not a claim about the one conversation.
        self.assertEqual(record["sessions_searched"], 3)
        self.assertEqual([m["path"] for m in record["matches"]],
                         ["sessions/fork.jsonl", "sessions/notes.jsonl"])
        for match in record["matches"]:
            self.assertEqual(match["turn_id"], "turn_000001")
            self.assertEqual(match["role"], "user")
            self.assertEqual(match["conversation_id"], CONVERSATION)

    def test_search_is_literal_and_case_sensitive(self):
        workspace_with_fork(self.base)
        self.assertEqual(search_messages("Q1", self.base)["matches"], [])
        self.assertEqual(len(search_messages("q1", self.base)["matches"]), 2)

    def test_search_one_named_session(self):
        workspace_with_fork(self.base)
        record = search_messages("unrelated", self.base, "other")
        self.assertEqual(record["sessions_searched"], 1)
        # Both of the session's messages carry the word; each is its own hit.
        self.assertEqual([m["path"] for m in record["matches"]],
                         ["sessions/other.jsonl", "sessions/other.jsonl"])
        self.assertEqual([m["turn_id"] for m in record["matches"]],
                         ["turn_000001", "turn_000002"])

        with self.assertRaises(QueryError):
            search_messages("x", self.base, "no-such-session")

    def test_search_accepts_the_trace_of_a_pair(self):
        # One rule for every read-only command: either half of the
        # (session, trace) pair addresses the session. The query layer used
        # to refuse a trace where show and branch accepted it; the resolver
        # unification extends the pair rule here too.
        pair = write_session(self.base, "pair",
                             [("user", "the paired question"),
                              ("assistant", "the paired answer")],
                             trace="../traces/pair.jsonl")
        header = json.loads(pair.read_text(encoding="utf-8").splitlines()[0])
        trace = (pair.parent / header["trace"]).resolve()
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(json.dumps({
            "event_id": "evt_000001", "run_id": "x", "type": "run.started",
        }) + "\n", encoding="utf-8")

        record = search_messages("paired question", self.base, str(trace))
        self.assertEqual([m["path"] for m in record["matches"]],
                         ["sessions/pair.jsonl"])

    def test_search_reports_zero_matches_as_a_result(self):
        workspace_with_fork(self.base)
        record = search_messages("needle that is not there", self.base)
        self.assertEqual(record["matches"], [])

        result = self._cli("search", "needle that is not there", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["matches"], [])
        text = "\n".join(render_search_lines(record))
        self.assertIn("no matches", text)

    def test_search_refuses_empty_text(self):
        result = self._cli("search", "")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class DiffTests(QueryTestBase):
    def test_diff_of_a_fork_shares_the_inherited_prefix(self):
        workspace_with_fork(self.base)
        record = diff_sessions("notes", "fork", self.base)

        self.assertEqual(record["common_prefix"]["turns"], 2)
        self.assertEqual(record["common_prefix"]["through"], "turn_000002")
        self.assertEqual([t["turn_id"] for t in record["only_left"]],
                         ["turn_000003", "turn_000004"])
        self.assertEqual([t["turn_id"] for t in record["only_right"]],
                         ["turn_000003", "turn_000004"])
        self.assertNotEqual(record["only_left"][0]["content"],
                            record["only_right"][0]["content"])
        self.assertTrue(record["same_conversation"])
        relation = record["recorded_relation"]
        self.assertEqual(relation["child"], "right")
        self.assertEqual(relation["parent_turn_id"], "turn_000002")
        self.assertTrue(relation["matches_common_prefix"])
        self.assertFalse(record["identical"])

    def test_diff_of_identical_sessions(self):
        workspace_with_fork(self.base)
        record = diff_sessions("notes", "notes", self.base)
        self.assertTrue(record["identical"])
        self.assertEqual(record["only_left"], [])
        self.assertEqual(record["only_right"], [])
        self.assertEqual(record["common_prefix"]["turns"], 4)

    def test_diff_of_unrelated_sessions(self):
        workspace_with_fork(self.base)
        record = diff_sessions("notes", "other", self.base)
        self.assertEqual(record["common_prefix"]["turns"], 0)
        self.assertIsNone(record["common_prefix"]["through"])
        self.assertIsNone(record["recorded_relation"])
        self.assertFalse(record["same_conversation"])
        self.assertEqual(len(record["only_left"]), 4)
        self.assertEqual(len(record["only_right"]), 2)

    def test_diff_reports_a_fork_point_the_files_disagree_with(self):
        # The child claims it left the parent at turn four, but its third
        # exchange already differs from the parent's. Both facts are true of
        # the files; the diff reports the disagreement instead of picking.
        root = [
            ("user", "q1"), ("assistant", "a1"),
            ("user", "q2"), ("assistant", "a2"),
        ]
        child = [
            ("user", "q1"), ("assistant", "a1"),
            ("user", "different q2"), ("assistant", "different a2"),
        ]
        write_session(self.base, "parent", root, CONVERSATION, ROOT_BRANCH)
        write_session(self.base, "child", child, CONVERSATION, FORK_BRANCH,
                      parent_branch_id=ROOT_BRANCH, parent_turn_id="turn_000004")
        record = diff_sessions("parent", "child", self.base)

        relation = record["recorded_relation"]
        self.assertEqual(relation["parent_turn_id"], "turn_000004")
        self.assertFalse(relation["matches_common_prefix"])
        text = "\n".join(render_diff_lines(record))
        self.assertIn("turn_000004", text)
        self.assertIn("turn_000002", text)

    def test_diff_requires_two_sessions_that_exist(self):
        workspace_with_fork(self.base)
        for pair in (("no-such-session", "fork"), ("notes", "no-such-session")):
            result = self._cli("diff", *pair)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        (self.base / "not-a-session.jsonl").write_text("hello\n", encoding="utf-8")
        result = self._cli("diff", "notes", "not-a-session.jsonl")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class RealWorkspaceTests(QueryTestBase):
    """The same queries over a workspace the real commands built."""

    def _run(self, *args, port=None, config=None):
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "test-key-not-real"}
        command = [sys.executable, "-m", "pocketchat"] + list(args)
        if config is not None:
            command += ["--config", str(config)]
        return subprocess.run(command, cwd=str(self.base), env=environ,
                              capture_output=True, text=True, timeout=120)

    def _build(self):
        server = HTTPServer(("127.0.0.1", 0), StubHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=lambda s=server: s.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        config = self.base / "pocketchat.json"
        config.write_text(json.dumps({
            "schema_version": "2", "name": "workbench",
            "default_profile": "local",
            "profiles": {"local": {
                "base_url": "http://127.0.0.1:%d/v1" % port,
                "model": "example-model",
                "api_key_env": "POCKETCHAT_LOCAL_KEY",
            }},
        }), encoding="utf-8")
        self._run("run", "q1", "--session", "notes", port=port, config=config)
        self._run("run", "q2", "--session", "notes", port=port, config=config)
        self._run("branch", "notes", "--from", "turn_000002", "--name", "fork")
        self._run("run", "q3", "--session", "fork", port=port, config=config)
        return config

    def test_queries_answer_over_the_commands_own_files(self):
        self._build()

        history = self._run("history", "--json")
        self.assertEqual(history.returncode, 0, history.stdout + history.stderr)
        record = json.loads(history.stdout)
        self.assertEqual(len(record["conversations"]), 1)
        branches = record["conversations"][0]["branches"]
        self.assertEqual([b["path"] for b in branches],
                         ["sessions/fork.jsonl", "sessions/notes.jsonl"])

        diff = self._run("diff", "notes", "fork", "--json")
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)
        diff_record = json.loads(diff.stdout)
        self.assertEqual(diff_record["common_prefix"]["through"], "turn_000002")
        self.assertTrue(diff_record["recorded_relation"]["matches_common_prefix"])

        search = self._run("search", "q2", "--json")
        self.assertEqual(search.returncode, 0, search.stdout + search.stderr)
        self.assertEqual(
            [m["path"] for m in json.loads(search.stdout)["matches"]],
            ["sessions/notes.jsonl"])

    def test_queries_change_no_byte(self):
        self._build()
        before = self._digests()
        for result in (self._run("history"),
                       self._run("history", "notes"),
                       self._run("search", "q1"),
                       self._run("diff", "notes", "fork")):
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self._digests(), before)


class StubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps({
            "id": "cmpl-stub", "model": "example-model",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "stub reply"},
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


if __name__ == "__main__":
    unittest.main()
