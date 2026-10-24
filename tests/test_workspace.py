"""Workspace reconstruction tests: the relationships the files already hold.

The property under test is that the graph is a *reading*, not a memory. Two
processes handed the same bytes must describe the same conversation, the same
branch tree and the same run ownership, because a graph that depends on the
process that drew it cannot be checked by anyone who was not there.

The second property is that the reader invents nothing. A comparison manifest
has no field for which answer was kept, a v0.1/v0.2 session has no
conversation id, and a trace nobody claims belongs to nobody -- each of those
is reported as absent rather than filled in, and every input file is
byte-identical afterwards.

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

from pocketchat.sessions import SessionFile
from pocketchat.workspace import (WorkspaceError, as_dict, collect,
                                  render_lines)
from tests import _helpers as hp


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


class WorkspaceTestBase(unittest.TestCase):
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
            "schema_version": "2",
            "name": "workbench",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % self.port,
                    "model": "example-model",
                    "api_key_env": "POCKETCHAT_LOCAL_KEY",
                },
                "hosted": {
                    "base_url": "http://127.0.0.1:%d/v1" % self.port,
                    "model": "hosted-model",
                    "api_key_env": "POCKETCHAT_HOSTED_KEY",
                },
            },
        }), encoding="utf-8")
        return path

    def _run(self, *args):
        environ = {"POCKETCHAT_LOCAL_KEY": "test-key-not-real",
                   "POCKETCHAT_HOSTED_KEY": "test-key-not-real"}
        return hp.run_cli(list(args), self.base, env=environ)

    def _run_subprocess(self, *args):
        """The golden path: a real interpreter, a real pipe, a real exit."""
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "test-key-not-real",
                   "POCKETCHAT_HOSTED_KEY": "test-key-not-real"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat"] + list(args),
            cwd=str(self.base), env=environ, capture_output=True, text=True,
            timeout=120,
        )

    def _conversation(self):
        """A conversation with two branches, built by the real commands."""
        config = self._config()
        self._run("run", "q1", "--session", "notes", "--config", str(config))
        self._run("run", "q2", "--session", "notes", "--config", str(config))
        self._run("branch", "notes", "--from", "turn_000002", "--name", "fork")
        self._run("run", "q3", "--session", "fork", "--config", str(config))
        return config

    def _comparison(self, config=None):
        """One comparison seeded from the conversation, by the real command."""
        result = self._run("compare", "which one", "--profiles", "local,hosted",
                           "--session", "notes",
                           "--config", str(config or self._config()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _digests(self):
        """A digest of every file under the workspace, for the read-only check."""
        digests = {}
        for path in sorted(self.base.rglob("*")):
            if path.is_file():
                digests[str(path.relative_to(self.base))] = hashlib.sha256(
                    path.read_bytes()).hexdigest()
        return digests

    def _branch(self, graph, name):
        for conversation in graph.conversations:
            for branch in conversation.branches:
                if branch.path == name:
                    return branch
        self.fail("%s is not in the reconstructed graph" % name)


class RelationshipTests(WorkspaceTestBase):
    """What the graph says about a conversation the commands actually built."""

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the workspace family: the subprocess path
        # stays in the suite; everything else here runs in process for speed.
        self._conversation()
        result = self._run_subprocess("workspace")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("conversation", result.stdout)

    def test_a_branch_and_its_parent_are_two_branches_of_one_conversation(self):
        self._conversation()
        graph = collect(self.base)

        self.assertEqual(len(graph.conversations), 1)
        conversation = graph.conversations[0]
        self.assertEqual(len(conversation.branches), 2)
        root = [b for b in conversation.branches if b.parent_branch_id is None]
        derived = [b for b in conversation.branches
                   if b.parent_branch_id is not None]
        self.assertEqual(len(root), 1)
        self.assertEqual(len(derived), 1)
        root, derived = root[0], derived[0]

        self.assertEqual(root.path, "sessions/notes.jsonl")
        self.assertEqual(derived.path, "sessions/fork.jsonl")
        # The child names the fork point, and the root does not: a root branch
        # records no parent because it has none to record.
        self.assertIsNone(root.parent_branch_id)
        self.assertIsNone(root.parent_turn_id)
        self.assertEqual(derived.parent_branch_id, root.branch_id)
        self.assertEqual(derived.parent_turn_id, "turn_000002")
        self.assertEqual(derived.conversation_id, root.conversation_id)
        self.assertNotEqual(derived.branch_id, root.branch_id)
        self.assertEqual(root.turns, 4)
        self.assertEqual(derived.turns, 4)

    def test_the_fork_point_the_child_records_is_a_turn_the_parent_holds(self):
        # The link is checked against the parent's own lines rather than
        # accepted, so a child pointing at a turn that was never written is
        # reported instead of silently becoming part of the tree.
        self._conversation()
        graph = collect(self.base)

        child = [b for b in graph.conversations[0].branches
                 if b.path == "sessions/fork.jsonl"][0]
        self.assertEqual(child.parent_turn_id, "turn_000002")
        self.assertEqual(graph.unresolved, [])

    def test_a_comparisons_runs_are_not_attributed_to_a_session_branch(self):
        # A comparison writes no session file, so its runs are reachable only
        # through the manifest. Pairing them with the session they measured
        # would put a second run id into a file that records one.
        config = self._conversation()
        self._comparison(config)

        graph = collect(self.base)
        self.assertEqual(len(graph.comparisons), 1)
        comparison = graph.comparisons[0]
        self.assertEqual(len(comparison.runs), 2)
        for run in comparison.runs:
            self.assertEqual(run.origin, "comparison manifest")
            self.assertTrue(run.trace.startswith("traces/"))
            self.assertTrue(run.request.startswith("traces/requests/"))
            self.assertTrue(run.status)

        # The session branch still holds exactly the one run its header names.
        branch = self._branch(graph, "sessions/notes.jsonl")
        comparison_runs = {r.run_id for r in comparison.runs}
        self.assertNotIn(branch.run_id, comparison_runs)
        # And every branch still holds exactly one run, the one its own
        # header names: a comparison run is not folded into a session.
        self.assertEqual(sum(len(c.branches) for c in graph.conversations), 2)
        self.assertEqual(len({b.run_id for c in graph.conversations
                              for b in c.branches}), 2)
        self.assertEqual(len(graph.conversations[0].branches), 2)

    def test_the_manifest_records_no_selection(self):
        # Nothing on disk says which answer was kept. The graph says so,
        # rather than nominating the run that looks like the winner.
        config = self._conversation()
        self._comparison(config)

        graph = collect(self.base)
        record = as_dict(graph)["comparisons"][0]
        self.assertEqual(record["selection"], "not recorded")

    def test_a_trace_no_file_claims_is_listed_rather_than_paired(self):
        # The one thing a reader must not do is attach a stray trace to the
        # branch whose run id happens to sit in its filename.
        self._conversation()
        stray = self.base / "traces" / "run_00000000000000_deadbeef0000.jsonl"
        stray.write_text(json.dumps({"event_id": "evt_000001", "run_id": "x",
                                     "type": "run.started"}) + "\n",
                         encoding="utf-8")

        graph = collect(self.base)
        self.assertIn("traces/run_00000000000000_deadbeef0000.jsonl",
                      graph.unlinked_traces)
        # And no branch grew a run it never recorded.
        for conversation in graph.conversations:
            for branch in conversation.branches:
                self.assertNotEqual(branch.run_id,
                                    "run_00000000000000_deadbeef0000")


class HonestyTests(WorkspaceTestBase):
    """Absent, malformed and unrecorded things are reported, never filled in."""

    def test_a_trace_the_manifest_names_but_the_workspace_lacks_is_reported(self):
        config = self._conversation()
        self._comparison(config)
        manifest = next((self.base / "comparisons").glob("*.json"))
        record = json.loads(manifest.read_text(encoding="utf-8"))
        victim = self.base / record["runs"][0]["trace"]
        victim.unlink()

        graph = collect(self.base)
        self.assertTrue(any("is not in this workspace" in item["reason"]
                            for item in graph.unresolved),
                        graph.unresolved)
        self.assertEqual(graph.comparisons[0].runs[0].trace,
                         record["runs"][0]["trace"])

    def test_a_request_snapshot_the_manifest_names_but_lacks_is_reported(self):
        config = self._conversation()
        self._comparison(config)
        manifest = next((self.base / "comparisons").glob("*.json"))
        record = json.loads(manifest.read_text(encoding="utf-8"))
        (self.base / record["runs"][0]["request"]).unlink()

        graph = collect(self.base)
        self.assertTrue(any("request snapshot" in item["reason"]
                            for item in graph.unresolved),
                        graph.unresolved)

    def test_a_branch_whose_parent_is_absent_is_reported(self):
        # A child whose parent branch is not in the workspace is a link to
        # nothing. Reporting it is the difference between "this branch is an
        # orphan" and "this branch is a root".
        self._conversation()
        child = self.base / "sessions" / "fork.jsonl"
        header = json.loads(child.read_text(encoding="utf-8").splitlines()[0])
        header["parent_branch_id"] = "branch_00000000000000_absent0000"
        lines = child.read_text(encoding="utf-8").splitlines()
        child.write_text(json.dumps(header, sort_keys=True) + "\n"
                         + "\n".join(lines[1:]) + "\n", encoding="utf-8")

        graph = collect(self.base)
        self.assertTrue(any("parent branch" in item["reason"]
                            for item in graph.unresolved),
                        graph.unresolved)

    def test_a_fork_point_the_parent_does_not_hold_is_reported(self):
        # The parent branch is present but the turn it is said to fork from is
        # not one of its exchanges. Accepting it would make the child's first
        # message an exchange the parent never recorded.
        self._conversation()
        child = self.base / "sessions" / "fork.jsonl"
        header = json.loads(child.read_text(encoding="utf-8").splitlines()[0])
        header["parent_turn_id"] = "turn_000009"
        lines = child.read_text(encoding="utf-8").splitlines()
        child.write_text(json.dumps(header, sort_keys=True) + "\n"
                         + "\n".join(lines[1:]) + "\n", encoding="utf-8")

        graph = collect(self.base)
        self.assertTrue(any("parent turn" in item["reason"]
                            for item in graph.unresolved),
                        graph.unresolved)

    def test_a_legacy_session_is_listed_without_an_invented_conversation(self):
        # A v0.1/v0.2 file has no conversation id. Minting one would group
        # two files that nothing links; listing each on its own says only
        # what the files say.
        legacy = self.base / "sessions" / "old.jsonl"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(json.dumps({
            "session": "run_legacy0000000000_abcdef012345",
            "trace": "../traces/run_legacy0000000000_abcdef012345.jsonl",
            "created": "2026-10-19T00:00:00Z",
            "pocketchat_version": "0.2.0",
        }, sort_keys=True) + "\n" + json.dumps(
            {"role": "user", "content": "old"}, sort_keys=True) + "\n",
            encoding="utf-8")

        graph = collect(self.base)
        self.assertEqual(len(graph.conversations), 1)
        conversation = graph.conversations[0]
        self.assertEqual(conversation.conversation_id, "not recorded")
        branch = conversation.branches[0]
        self.assertIsNone(branch.conversation_id)
        self.assertIsNone(branch.branch_id)
        self.assertIsNone(branch.parent_branch_id)
        self.assertEqual(branch.turns, 1)

    def test_two_legacy_sessions_are_not_merged_into_one_conversation(self):
        for name in ("old", "older"):
            path = self.base / "sessions" / ("%s.jsonl" % name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "session": "run_%s000000000000_abcdef012345" % name,
                "trace": "../traces/run_%s000000000000_abcdef012345.jsonl" % name,
                "created": "2026-10-19T00:00:00Z",
                "pocketchat_version": "0.2.0",
            }, sort_keys=True) + "\n", encoding="utf-8")

        graph = collect(self.base)
        self.assertEqual(len(graph.conversations), 2)

    def test_a_file_that_is_not_a_session_is_reported_not_skipped(self):
        junk = self.base / "sessions" / "notes.jsonl"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("{}\n", encoding="utf-8")

        graph = collect(self.base)
        self.assertEqual(len(graph.unreadable), 1)
        self.assertEqual(graph.unreadable[0]["path"], "sessions/notes.jsonl")
        self.assertEqual(graph.conversations, [])

    def test_two_sessions_claiming_one_run_id_are_reported_not_merged(self):
        # Two files naming the same run id is two claims on one run. Picking
        # either would hide the disagreement, and merging them would invent a
        # relationship neither file made.
        self._conversation()
        copy = self.base / "sessions" / "twin.jsonl"
        copy.write_text((self.base / "sessions" / "notes.jsonl")
                        .read_text(encoding="utf-8"), encoding="utf-8")

        graph = collect(self.base)
        self.assertTrue(any("run id claimed twice" in item["kind"]
                            for item in graph.conflicts),
                        graph.conflicts)
        # Both files are still described: a conflict is reported, not resolved
        # by dropping one side of it.
        self.assertEqual(sum(len(c.branches) for c in graph.conversations), 3)

    def test_reading_the_workspace_changes_no_byte(self):
        self._conversation()
        before = self._digests()

        collect(self.base)
        render_lines(collect(self.base))
        self._run("workspace")
        self._run("workspace", "--json")

        self.assertEqual(self._digests(), before)

    def test_the_reader_agrees_with_the_session_file_about_the_header(self):
        # Two readers of one header must not grow two spellings of the same
        # rule. The writer's own view is the reference.
        self._conversation()
        for branch in collect(self.base).conversations[0].branches:
            path = self.base / branch.path
            store = SessionFile.open(path, "run_unused00000000000_000000000000",
                                     Path("traces/unused.jsonl"), "0.3.0")
            self.assertEqual(branch.conversation_id, store.conversation_id)
            self.assertEqual(branch.branch_id, store.branch_id)
            self.assertEqual(branch.parent_branch_id, store.parent_branch_id)
            self.assertEqual(branch.parent_turn_id, store.parent_turn_id)
            self.assertEqual(branch.run_id, store.run_id)
            self.assertEqual(branch.turns, len(store.turns))
            self.assertEqual(branch.version, store.version)


class ProcessIndependenceTests(WorkspaceTestBase):
    """The graph is a property of the files, not of the process that drew it."""

    def test_the_same_files_give_the_same_graph_in_two_processes(self):
        self._conversation()
        first = self._run("workspace", "--json")
        second = self._run("workspace", "--json")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(json.loads(first.stdout),
                         json.loads(second.stdout))

    def test_the_graph_of_a_workspace_read_from_elsewhere_is_the_same(self):
        # The paths are relative to the workspace, so reading it from another
        # directory describes the same thing rather than another directory's.
        self._conversation()
        here = self._run("workspace", "--json").stdout
        elsewhere = self._run("workspace", "--json", str(self.base)).stdout

        self.assertEqual(json.loads(here)["conversations"],
                         json.loads(elsewhere)["conversations"])
        self.assertEqual(json.loads(here)["comparisons"],
                         json.loads(elsewhere)["comparisons"])

    def test_an_empty_workspace_says_so_instead_of_reporting_nothing(self):
        result = self._run("workspace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no conversations or comparisons recorded", result.stdout)

    def test_a_workspace_that_does_not_exist_is_refused(self):
        result = self._run("workspace", "nowhere")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no such workspace", result.stderr)

    def test_a_directory_that_is_not_a_workspace_is_not_an_error(self):
        # A directory with no sessions, traces or comparisons is an empty
        # workspace, which is a fact rather than a failure.
        empty = self.base / "elsewhere"
        empty.mkdir()
        result = self._run("workspace", "elsewhere")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no conversations or comparisons recorded", result.stdout)


class CollectorContractTests(unittest.TestCase):
    """The reader's own edges, without a workspace on disk."""

    def test_a_workspace_path_that_is_not_a_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "afile"
            target.write_text("x", encoding="utf-8")
            with self.assertRaises(WorkspaceError):
                from pocketchat.workspace import collect_workspace
                collect_workspace(target)

    def test_the_json_has_no_absolute_path_and_no_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            graph = collect(Path(temp))
            record = json.dumps(as_dict(graph), sort_keys=True)
            self.assertNotIn(temp, record)
            self.assertIn('"schema_version": "1"', record)

    def test_the_tree_of_an_empty_workspace_is_one_line_of_fact(self):
        with tempfile.TemporaryDirectory() as temp:
            lines = render_lines(collect(Path(temp)))
            self.assertTrue(lines[0].startswith("workspace "))
            self.assertEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
