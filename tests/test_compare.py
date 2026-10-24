"""Comparison kernel tests: several profiles, one prompt, independent runs.

A comparison must be a series of ordinary runs and not a special one. Every
profile starts from the same base context and the same prompt, gets its own
run id, trace and request snapshot, and ends with exactly one terminal event,
because a trace with a ``run.started`` and no terminal event is the one shape
the tooling this package feeds rejects.

Isolation is the other half. One profile failing must not stop the others: the
point of comparing is to see them side by side, and a run that dies taking its
siblings with it answers nothing. Ctrl-C ends the comparison instead, because
the operator asked it to stop. And no profile may write the base session file,
because a comparison is a measurement and a measurement must not change what
it measured.

The stub is a real local socket. Nothing here reaches a provider and no
credential is real.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pocketchat.config import profile_config
from pocketchat.compare import (
    ComparisonManifestError, comparison_manifest_path,
    run_comparison, write_comparison_manifest)

try:
    from tests import _helpers as hp
except ImportError:  # unittest discover puts tests/ itself on sys.path
    import _helpers as hp

ROOT = Path(__file__).resolve().parents[1]


def _v2_config():
    return {
        "schema_version": "2",
        "name": "workbench",
        "default_profile": "local",
        "system_prompt": "Be brief.",
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "local-model",
                "api_key_env": "POCKETCHAT_LOCAL_KEY",
                "timeout_seconds": 30,
            },
            "hosted": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "hosted-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
                "timeout_seconds": 90,
            },
        },
    }


def _ok(count):
    return 200, {
        "choices": [{"message": {"content": "stub reply"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 5},
    }


def _ok_without_usage(count):
    # A provider that answers but reports no usage. The counters come back as
    # zeros, and those zeros must not be mistaken for a measurement.
    return 200, {
        "choices": [{"message": {"content": "stub reply"}, "finish_reason": "stop"}],
    }


def _bad_request(count):
    return 400, {"error": "bad request from the stub"}


class _StubServer:
    """A local socket that answers like a provider, or fails on request."""

    def __init__(self, reply):
        state = self
        state.requests = []

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                state.requests.append({
                    "model": json.loads(raw.decode("utf-8"))["model"],
                    "authorization": self.headers.get("Authorization"),
                    "raw": raw.decode("utf-8"),
                })
                status, body = reply(len(state.requests))
                self._send(status, json.dumps(body).encode("utf-8"))

            def _send(self, status, body):
                self.send_response(status)
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

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


class ComparisonKernelTests(unittest.TestCase):
    """Two profiles, one prompt: same base context, separate runs."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.config = _v2_config()
        # A run reads its credential from the environment, so the profiles
        # need one to be set. The value is fake and never reaches a provider.
        self._environ = os.environ.copy()
        os.environ["POCKETCHAT_LOCAL_KEY"] = "local-fake"
        os.environ["POCKETCHAT_HOSTED_KEY"] = "hosted-fake"
        self.addCleanup(self._restore_environ)
        self._restart_stub(_ok)

    def _restore_environ(self):
        os.environ.clear()
        os.environ.update(self._environ)

    def _restart_stub(self, reply):
        if getattr(self, "stub", None) is not None:
            self.stub.stop()
        self.stub = _StubServer(reply)
        self.addCleanup(self.stub.stop)
        for record in self.config["profiles"].values():
            record["base_url"] = "http://127.0.0.1:%d/v1" % self.stub.port
        self.profiles = {
            name: profile_config(self.config, name)
            for name in ("local", "hosted")
        }

    def _compare(self, **kwargs):
        options = {
            "prompt": "hello",
            "base_messages": [{"role": "user", "content": "earlier"}],
            "profiles": self.profiles,
            "workspace": self.workspace,
            "verbose": False,
        }
        options.update(kwargs)
        return run_comparison(**options)

    def _fail_first_request_only(self, count):
        # The first profile's request fails; the second one succeeds. Both
        # profiles talk to the same stub, so the count is what tells them
        # apart: the comparison is serial, so the first request really is the
        # first profile's.
        return _bad_request(count) if count == 1 else _ok(count)

    def _events(self, trace):
        return [json.loads(line) for line in
                (self.workspace / trace).read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_two_profiles_share_the_base_context_and_get_separate_runs(self):
        # The plan's core contract. Both runs must start from the same context
        # and the same prompt, or a difference in the replies says nothing
        # about the profiles. Two distinct run ids is what keeps the two
        # traces from describing each other.
        result = self._compare()
        self.assertEqual([run.profile for run in result.runs], ["local", "hosted"])
        self.assertEqual(len({run.run_id for run in result.runs}), 2)
        self.assertTrue(all(run.status == "completed" for run in result.runs))
        self.assertEqual(len(self.stub.requests), 2)
        for request in self.stub.requests:
            messages = json.loads(request["raw"])["messages"]
            self.assertIn({"role": "user", "content": "earlier"}, messages)
            self.assertEqual(messages[-1], {"role": "user", "content": "hello"})
        # Only the model differed, because only the profile differed.
        self.assertEqual(self.stub.requests[0]["model"], "local-model")
        self.assertEqual(self.stub.requests[1]["model"], "hosted-model")

    def test_each_profile_gets_its_own_trace_and_request_snapshot(self):
        result = self._compare()
        self.assertEqual(len({run.trace for run in result.runs}), 2)
        self.assertEqual(len({run.request for run in result.runs}), 2)
        for run in result.runs:
            trace = self.workspace / run.trace
            request = self.workspace / run.request
            self.assertTrue(trace.is_file(), "missing trace %s" % run.trace)
            self.assertTrue(request.is_file(), "missing request %s" % run.request)
            payload = json.loads(request.read_text(encoding="utf-8"))
            self.assertEqual(payload["profile"], run.profile)
            self.assertEqual(payload["run_id"], run.run_id)

    def test_both_traces_carry_exactly_one_terminal_event(self):
        result = self._compare()
        terminal = {"run.finished", "run.failed", "run.cancelled"}
        for run in result.runs:
            types = [event["type"] for event in self._events(run.trace)]
            self.assertEqual(types[0], "run.started")
            self.assertEqual([t for t in types if t in terminal],
                             ["run.finished"])
            self.assertEqual(types[-1], "run.finished")

    def test_a_failed_profile_does_not_stop_the_others(self):
        # The failure-isolation rule. The first profile fails on the wire; the
        # second must still complete, and the failed one must be reported as
        # failed with its error rather than dropped from the result.
        self._restart_stub(self._fail_first_request_only)

        result = self._compare()
        by_profile = {run.profile: run for run in result.runs}
        self.assertEqual(by_profile["local"].status, "failed")
        self.assertEqual(by_profile["hosted"].status, "completed")
        # Both runs are still reported: dropping the failed one would make the
        # comparison read as a single-profile run that happened to succeed.
        self.assertEqual([run.profile for run in result.runs], ["local", "hosted"])
        self.assertEqual(by_profile["local"].error["type"], "bad_request")
        self.assertIsNone(by_profile["hosted"].error)
        self.assertEqual(by_profile["hosted"].usage,
                         {"input_tokens": 11, "output_tokens": 5})

    def test_a_failed_run_still_ends_its_own_trace(self):
        self._restart_stub(_bad_request)
        result = self._compare()
        self.assertEqual([run.status for run in result.runs], ["failed", "failed"])
        for run in result.runs:
            events = self._events(run.trace)
            self.assertEqual(events[0]["type"], "run.started")
            self.assertEqual(events[-1]["type"], "run.failed")

    def test_the_reported_error_is_the_one_the_trace_recorded(self):
        # A comparison that reported an error the trace does not carry would
        # be inventing evidence, so the reported error is read back out of the
        # run's own terminal event rather than carried in a variable.
        self._restart_stub(_bad_request)
        result = self._compare()
        for run in result.runs:
            terminal = self._events(run.trace)[-1]
            self.assertEqual(terminal["type"], "run.failed")
            recorded = terminal["payload"]["error"]
            self.assertEqual(run.error, {
                "type": recorded["type"],
                "message": recorded["message"],
            })

    def test_a_completed_run_reports_no_error(self):
        result = self._compare()
        for run in result.runs:
            self.assertIsNone(run.error)
            self.assertTrue(run.output_summary)
            self.assertEqual(run.usage, {"input_tokens": 11, "output_tokens": 5})
            self.assertIsNone(run.cost_usd)

    def test_a_provider_that_reported_no_usage_costs_nothing_it_can_prove(self):
        # A provider that answers without usage numbers leaves the counters at
        # zero. The comparison reports those zeros and costs the run unknown,
        # because a price applied to numbers nobody reported would be a cost
        # that was never measured either.
        self._restart_stub(_ok_without_usage)
        result = self._compare()
        for run in result.runs:
            self.assertEqual(run.usage, {"input_tokens": 0, "output_tokens": 0})
            self.assertIsNone(run.cost_usd)

    def test_a_configured_price_costs_the_run_it_belongs_to(self):
        # Costing is per profile: the same prompt against two profiles with
        # different prices is two different prices. One profile priced and one
        # not is the case that matters most, because the unpriced one has to
        # stay unknown while the priced one reports a number.
        self.profiles["hosted"]["input_price_per_million"] = 1.0
        self.profiles["hosted"]["output_price_per_million"] = 2.0
        result = self._compare()
        by_profile = {run.profile: run for run in result.runs}
        # 11 input tokens at $1.00/M and 5 output tokens at $2.00/M.
        self.assertEqual(by_profile["hosted"].cost_usd, 2.1e-05)
        self.assertIsNone(by_profile["local"].cost_usd)

    def test_the_reported_duration_comes_from_the_trace_events(self):
        # The duration a comparison reports must be one its own trace can
        # support: the sum of each request's started-to-response span. A clock
        # wrapped around the whole run would include the session setup, which
        # is not time any request waited for.
        result = self._compare()
        for run in result.runs:
            events = self._events(run.trace)
            started = [e for e in events if e["type"] == "model.request.started"]
            self.assertEqual(len(started), 1)
            completed = [e for e in events
                         if e["type"] == "model.response.completed"]
            self.assertEqual(len(completed), 1)
            self.assertGreaterEqual(run.duration_ms, 0)
            self.assertLess(run.duration_ms, 60_000)

    def test_a_run_that_never_sent_a_request_reports_zero_duration(self):
        # A missing credential fails before any request exists, so there is no
        # request span to measure. Zero says "nothing was measured", which is
        # the honest answer, rather than the time spent failing.
        self.profiles["local"]["api_key_env"] = "POCKETCHAT_UNSET_KEY"
        result = self._compare()
        by_profile = {run.profile: run for run in result.runs}
        self.assertEqual(by_profile["local"].status, "failed")
        self.assertEqual(by_profile["local"].duration_ms, 0)
        self.assertIsNone(by_profile["local"].cost_usd)
        # The other profile still ran and still has a cost to report.
        self.assertEqual(by_profile["hosted"].status, "completed")

    def test_a_failed_run_measures_the_time_it_spent_failing(self):
        # A request that the provider refused still took wall time. Excluding
        # it would make the failed profile look faster than it was, which is
        # exactly the comparison being made.
        self._restart_stub(self._fail_first_request_only)
        result = self._compare()
        by_profile = {run.profile: run for run in result.runs}
        self.assertEqual(by_profile["local"].status, "failed")
        self.assertEqual(by_profile["hosted"].status, "completed")
        self.assertGreaterEqual(by_profile["local"].duration_ms, 0)
        self.assertLess(by_profile["local"].duration_ms, 60_000)

    def test_a_missing_credential_fails_only_that_profile(self):
        # A profile whose key is not set is a failure of that profile, not of
        # the comparison. The other profile must still run to completion.
        self.profiles["hosted"]["api_key_env"] = "POCKETCHAT_UNSET_KEY"
        result = self._compare()
        by_profile = {run.profile: run for run in result.runs}
        self.assertEqual(by_profile["hosted"].status, "failed")
        self.assertEqual(by_profile["hosted"].error["type"], "missing_credential")
        self.assertEqual(by_profile["local"].status, "completed")

    def test_interrupting_a_comparison_cancels_the_current_run(self):
        # Ctrl-C ends the comparison rather than skipping to the next profile:
        # the operator asked to stop, and running the remainder anyway would
        # spend their time against their wish. The current run is closed with
        # a terminal event rather than left open.
        import pocketchat.session as session_module

        def interrupt(context, config, api_key):
            raise KeyboardInterrupt

        original = session_module.complete
        session_module.complete = interrupt
        self.addCleanup(setattr, session_module, "complete", original)

        result = self._compare()
        self.assertEqual(len(result.runs), 1)
        self.assertEqual(result.runs[0].status, "cancelled")
        events = self._events(result.runs[0].trace)
        self.assertEqual(events[-1]["type"], "run.cancelled")

    def test_a_comparison_never_writes_the_base_session_file(self):
        # A comparison is a measurement. A base session file sitting in the
        # workspace is a reader, not a participant: the runs must not write
        # into it, and must not leave a second conversation file behind that a
        # later reader would take for the conversation they were keeping.
        base = self.workspace / "sessions" / "notes.jsonl"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text("\n".join([
            json.dumps({"type": "header", "run_id": "run_base0000000000",
                        "trace": "../traces/run_base0000000000.jsonl",
                        "schema_version": "1", "pocketchat_version": "0.1.0",
                        "created": "2026-10-19T00:00:00.000Z",
                        "resumed": False}),
            json.dumps({"role": "user", "content": "earlier"}),
        ]) + "\n", encoding="utf-8")
        before = base.read_text(encoding="utf-8")

        self._compare()

        self.assertEqual(base.read_text(encoding="utf-8"), before)
        # And no sibling conversation files appeared either: a comparison that
        # started keeping its own would be a second place for the truth.
        sessions = sorted(p.relative_to(self.workspace).as_posix()
                          for p in (self.workspace / "sessions").glob("*.jsonl"))
        self.assertEqual(sessions, ["sessions/notes.jsonl"])

    def test_no_run_claims_a_session_file(self):
        # Nothing points a reader at a conversation as though a profile had
        # continued one: a comparison opens no session file at all.
        result = self._compare()
        for run in result.runs:
            payload = [e.get("payload", {}) for e in self._events(run.trace)
                       if e["type"] == "run.finished"][0]
            self.assertNotIn("session", payload)
            self.assertNotIn("resumed", payload)

    def test_no_credential_reaches_the_trace_or_the_snapshot(self):
        secret = "super-secret-key-value"
        result = self._compare(api_key=secret)
        for run in result.runs:
            for name in (run.trace, run.request):
                path = self.workspace / name
                self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_a_prompt_quoting_the_credential_writes_no_snapshot(self):
        # A comparison is a run per profile, so the snapshot refusal applies to
        # each of them. The prompt carries the credential the session holds,
        # so no run may write a body containing it; what survives is the trace
        # and the manifest's empty `request` field, which names nothing rather
        # than pointing at a file that was never written.
        credential = "local-fake"
        result = self._compare(prompt="please use %s" % credential,
                               api_key=credential)
        self.assertEqual([run.status for run in result.runs],
                         ["completed", "completed"])
        for run in result.runs:
            self.assertEqual(run.request, "")
            trace = self.workspace / run.trace
            self.assertNotIn(credential, trace.read_text(encoding="utf-8"))
        self.assertFalse((self.workspace / "traces" / "requests").exists())


class ComparisonManifestTests(unittest.TestCase):
    """The manifest: an index over the runs, not a second copy of them."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.config = _v2_config()
        self._environ = os.environ.copy()
        os.environ["POCKETCHAT_LOCAL_KEY"] = "local-fake"
        os.environ["POCKETCHAT_HOSTED_KEY"] = "hosted-fake"
        self.addCleanup(self._restore_environ)
        self.stub = _StubServer(_ok)
        self.addCleanup(self.stub.stop)
        for record in self.config["profiles"].values():
            record["base_url"] = "http://127.0.0.1:%d/v1" % self.stub.port
        self.profiles = {
            name: profile_config(self.config, name)
            for name in ("local", "hosted")
        }

    def _restore_environ(self):
        os.environ.clear()
        os.environ.update(self._environ)

    def _compare(self, **kwargs):
        options = {
            "prompt": "hello",
            "profiles": self.profiles,
            "workspace": self.workspace,
            "verbose": False,
        }
        options.update(kwargs)
        return run_comparison(**options)

    def _manifest(self, result):
        path = write_comparison_manifest(
            comparison_manifest_path(self.workspace, result.comparison_id), result)
        return path, json.loads(path.read_text(encoding="utf-8"))

    def test_the_manifest_names_the_comparison_and_every_run(self):
        result = self._compare()
        path, manifest = self._manifest(result)
        self.assertEqual(path, self.workspace / "comparisons" / ("%s.json"
                                                                % result.comparison_id))
        self.assertEqual(manifest["schema_version"], "1")
        self.assertEqual(manifest["comparison_id"], result.comparison_id)
        self.assertEqual(manifest["prompt"], "hello")
        self.assertEqual([run["profile"] for run in manifest["runs"]],
                         ["local", "hosted"])
        self.assertTrue(manifest["created"].endswith("Z"))

    def test_every_path_in_the_manifest_resolves_from_the_workspace(self):
        # A path that resolves from where the process was started is not a
        # reference: it breaks the moment the manifest is read elsewhere.
        result = self._compare()
        _, manifest = self._manifest(result)
        for run in manifest["runs"]:
            self.assertFalse(os.path.isabs(run["trace"]))
            self.assertFalse(os.path.isabs(run["request"]))
            self.assertTrue((self.workspace / run["trace"]).is_file())
            self.assertTrue((self.workspace / run["request"]).is_file())

    def test_the_manifest_carries_the_evidence_not_a_copy_of_it(self):
        # The manifest says where the request snapshot is and what it proved;
        # it does not reproduce the body. Two copies of one request are two
        # versions of the truth.
        result = self._compare()
        _, manifest = self._manifest(result)
        for run in manifest["runs"]:
            snapshot = json.loads((self.workspace / run["request"])
                                  .read_text(encoding="utf-8"))
            # The digest lives in the snapshot, where the request itself is.
            # Repeating it here would give one fact two homes.
            self.assertIn("request_sha256", snapshot)
            self.assertNotIn("messages", json.dumps(run))

    def test_the_manifest_records_what_each_run_reported(self):
        result = self._compare()
        _, manifest = self._manifest(result)
        for run in manifest["runs"]:
            self.assertEqual(run["status"], "completed")
            self.assertEqual(run["usage"], {"input_tokens": 11, "output_tokens": 5})
            self.assertIsNone(run["cost_usd"])
            self.assertIsNone(run["error"])
            self.assertTrue(run["output_summary"])

    def test_a_failed_run_is_in_the_manifest_with_its_error(self):
        # A comparison where one profile failed is still a comparison. The
        # manifest must carry the failed run, or a reader counting runs would
        # take the successful ones for the whole measurement.
        self.stub.stop()
        self.stub = _StubServer(self._fail_first)
        self.addCleanup(self.stub.stop)
        for record in self.config["profiles"].values():
            record["base_url"] = "http://127.0.0.1:%d/v1" % self.stub.port
        self.profiles = {
            name: profile_config(self.config, name)
            for name in ("local", "hosted")
        }
        result = self._compare()
        _, manifest = self._manifest(result)
        by_profile = {run["profile"]: run for run in manifest["runs"]}
        self.assertEqual([run["profile"] for run in manifest["runs"]],
                         ["local", "hosted"])
        self.assertEqual(by_profile["local"]["status"], "failed")
        self.assertEqual(by_profile["local"]["error"]["type"], "bad_request")
        self.assertEqual(by_profile["hosted"]["status"], "completed")
        self.assertIsNone(by_profile["hosted"]["error"])

    def _fail_first(self, count):
        return _bad_request(count) if count == 1 else _ok(count)

    def test_the_manifest_records_the_base_context_it_measured(self):
        result = self._compare(base_messages=[{"role": "user",
                                              "content": "earlier"}])
        _, manifest = self._manifest(result)
        self.assertEqual(manifest["base_context"]["messages"], 1)
        self.assertEqual(manifest["base_context"]["context_chars"], 7)
        self.assertIsNone(manifest["base_context"]["session"])

    def _write_base_session(self, name="notes"):
        """A session file a comparison would seed its context from."""
        path = self.workspace / "sessions" / ("%s.jsonl" % name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([
            json.dumps({"session": "run_base0000000000",
                        "trace": "../traces/run_base0000000000.jsonl",
                        "created": "2026-10-19T00:00:00.000Z",
                        "pocketchat_version": "0.1.0", "resumed": False}),
            json.dumps({"role": "user", "content": "earlier"}),
        ]) + "\n", encoding="utf-8")
        return path

    def test_the_manifest_names_the_base_session_it_measured(self):
        # The manifest's base_context is the only place a reader learns which
        # conversation the comparison started from. A manifest that recorded
        # messages and characters but not the file they came from cannot be
        # checked against anything: the reader has counts with no source.
        base = self._write_base_session()
        result = self._compare(base_messages=[{"role": "user",
                                               "content": "earlier"}],
                               base_session=base)
        _, manifest = self._manifest(result)
        self.assertEqual(manifest["base_context"]["session"], "sessions/notes.jsonl")
        self.assertFalse(os.path.isabs(manifest["base_context"]["session"]))
        self.assertTrue((self.workspace / manifest["base_context"]["session"]).is_file())
        self.assertEqual(manifest["base_context"]["messages"], 1)
        self.assertEqual(manifest["base_context"]["context_chars"], 7)

    def test_the_manifest_leaves_the_base_session_empty_when_none_was_used(self):
        # No base session is a different fact from an unnamed one. Recording a
        # placeholder path there would send a reader looking for a file that
        # never took part in the measurement.
        result = self._compare(base_messages=[{"role": "user",
                                               "content": "earlier"}])
        _, manifest = self._manifest(result)
        self.assertIsNone(manifest["base_context"]["session"])

    def test_writing_the_manifest_twice_refuses_rather_than_replacing(self):
        # The exclusive create is what keeps a manifest from being quietly
        # rewritten: a file already there records a comparison that happened.
        result = self._compare()
        path = comparison_manifest_path(self.workspace, result.comparison_id)
        write_comparison_manifest(path, result)
        with self.assertRaises(ComparisonManifestError):
            write_comparison_manifest(path, result)

    def test_no_credential_reaches_the_manifest(self):
        secret = "super-secret-key-value"
        result = self._compare(api_key=secret)
        path, _ = self._manifest(result)
        self.assertNotIn(secret, path.read_text(encoding="utf-8"))


class ComparisonCliTests(unittest.TestCase):
    """The `compare` command: one comparison, one manifest, one exit code."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.config = _v2_config()
        self.config_path = self.workspace / "pocketchat.json"
        self._environ = os.environ.copy()
        os.environ["POCKETCHAT_LOCAL_KEY"] = "local-fake"
        os.environ["POCKETCHAT_HOSTED_KEY"] = "hosted-fake"
        self.addCleanup(self._restore_environ)
        self.stub = _StubServer(_ok)
        self.addCleanup(self.stub.stop)
        self._write_config()

    def _restore_environ(self):
        os.environ.clear()
        os.environ.update(self._environ)

    def _write_config(self):
        for record in self.config["profiles"].values():
            record["base_url"] = "http://127.0.0.1:%d/v1" % self.stub.port
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def _cli(self, *extra):
        environ = {"POCKETCHAT_LOCAL_KEY": "local-fake",
                   "POCKETCHAT_HOSTED_KEY": "hosted-fake"}
        return hp.run_cli(["compare", "hello", "--config", str(self.config_path)]
                          + list(extra), self.workspace, env=environ)

    def _cli_subprocess(self, *extra):
        """The golden path: a real interpreter, a real pipe, a real exit."""
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "local-fake",
                   "POCKETCHAT_HOSTED_KEY": "hosted-fake"}
        return subprocess.run(
            [sys.executable, "-m", "pocketchat", "compare", "hello",
             "--config", str(self.config_path)] + list(extra),
            cwd=str(self.workspace), env=environ, capture_output=True, text=True,
            timeout=120,
        )

    def _manifests(self):
        return sorted((self.workspace / "comparisons").glob("*.json"))

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the compare family: the subprocess path
        # stays in the suite; everything else here runs in process for speed.
        result = self._cli_subprocess("--profiles", "local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self._manifests()), 1)

    def test_compare_writes_a_manifest_and_prints_the_path(self):
        result = self._cli("--profiles", "local,hosted")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifests = self._manifests()
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual([run["profile"] for run in manifest["runs"]],
                         ["local", "hosted"])
        self.assertIn(str(manifests[0]), result.stdout)

    def test_json_output_names_the_manifest_and_the_runs(self):
        result = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["comparison_id"], manifest["comparison_id"])
        self.assertEqual([run["profile"] for run in payload["runs"]],
                         ["local", "hosted"])
        self.assertEqual([run["status"] for run in payload["runs"]],
                         ["completed", "completed"])
        # The JSON and the manifest must name the same runs, or a reader who
        # followed the JSON would be reading about a different comparison.
        self.assertEqual([run["run_id"] for run in payload["runs"]],
                         [run["run_id"] for run in manifest["runs"]])

    def test_a_priced_profile_shows_its_cost_and_an_unpriced_one_says_unknown(self):
        # The table is where an operator reads the comparison, so it has to
        # say both things: a real number for the profile that has prices, and
        # "unknown" for the one that does not. A table that printed 0.0000
        # for the unpriced profile would report a price nobody configured.
        self.config["profiles"]["hosted"]["input_price_per_million"] = 1.0
        self.config["profiles"]["hosted"]["output_price_per_million"] = 2.0
        self._write_config()
        result = self._cli("--profiles", "local,hosted")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unknown", result.stdout)
        # The priced profile's cost is far below a cent, so the table has to
        # carry enough places to show it rather than rounding it to zero.
        self.assertIn("0.000021", result.stdout)

        json_run = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(json_run.returncode, 0, json_run.stdout + json_run.stderr)
        payload = json.loads(json_run.stdout)
        by_profile = {run["profile"]: run for run in payload["runs"]}
        self.assertEqual(by_profile["hosted"]["cost_usd"], 2.1e-05)
        self.assertIsNone(by_profile["local"]["cost_usd"])
        # The manifest and the JSON must agree on the cost, or following one
        # of them would give a different answer than following the other.
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual([run["cost_usd"] for run in manifest["runs"]],
                         [run["cost_usd"] for run in payload["runs"]])

    def test_the_manifest_runs_name_request_snapshots_that_exist(self):
        result = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        for run in manifest["runs"]:
            snapshot = json.loads((self.workspace / run["request"])
                                  .read_text(encoding="utf-8"))
            self.assertIn("request_sha256", snapshot)
            self.assertTrue((self.workspace / run["trace"]).is_file())

    def test_a_failed_profile_does_not_fail_the_command(self):
        # One profile failing is a result, not an error of the command: the
        # comparison happened and the manifest says what each profile did.
        self.stub.stop()
        self.stub = _StubServer(self._fail_first)
        self.addCleanup(self.stub.stop)
        self._write_config()
        result = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        by_profile = {run["profile"]: run for run in payload["runs"]}
        self.assertEqual(by_profile["local"]["status"], "failed")
        self.assertEqual(by_profile["hosted"]["status"], "completed")

    def _fail_first(self, count):
        return _bad_request(count) if count == 1 else _ok(count)

    def test_an_unknown_profile_is_refused_before_any_trace_is_written(self):
        result = self._cli("--profiles", "local,nope")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("nope", result.stderr)
        self.assertFalse((self.workspace / "traces").exists())
        self.assertEqual(self._manifests(), [])

    def test_a_profile_without_a_credential_is_refused_before_any_run(self):
        # The credential is read from the child's environment, so the probe
        # runs without the second profile's key rather than clearing this
        # process's. Nothing may be written: a comparison that started and
        # then discovered the missing key would leave half a measurement.
        result = hp.run_cli(
            ["compare", "hello", "--config", str(self.config_path),
             "--profiles", "local,hosted"],
            self.workspace,
            env={"POCKETCHAT_LOCAL_KEY": "local-fake",
                 "POCKETCHAT_HOSTED_KEY": None})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POCKETCHAT_HOSTED_KEY", result.stderr)
        self.assertFalse((self.workspace / "traces").exists())
        self.assertEqual(self._manifests(), [])

    def test_human_output_is_one_line_per_profile(self):
        result = self._cli("--profiles", "local,hosted")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        # One row per profile, then the manifest path: the rows are for
        # scanning, and the last line is where the evidence is.
        self.assertEqual(len(lines), 3)
        self.assertIn("local", lines[0])
        self.assertIn("hosted", lines[1])
        self.assertIn("completed", lines[0])
        self.assertIn("completed", lines[1])
        self.assertIn("manifest", lines[2])
        self.assertNotIn("local-fake", result.stdout)

    def test_a_session_named_but_absent_is_refused(self):
        # The base-context rule is a CLI fact too: comparing must not start
        # keeping a conversation a reader would take for the one they kept.
        # A session named but absent is refused rather than created: an empty
        # one would measure nothing while the manifest's base_context claimed
        # the operator's earlier conversation was in scope.
        result = self._cli("--profiles", "local,hosted", "--session", "notes")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("no such session", result.stderr)
        self.assertFalse((self.workspace / "traces").exists())
        self.assertEqual(self._manifests(), [])

    def test_a_base_session_seeds_the_context_without_being_written(self):
        sessions = self.workspace / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        base = sessions / "notes.jsonl"
        base.write_text("\n".join([
            json.dumps({"session": "run_base0000000000",
                        "trace": "../traces/run_base0000000000.jsonl",
                        "created": "2026-10-19T00:00:00.000Z",
                        "pocketchat_version": "0.1.0", "resumed": False}),
            json.dumps({"role": "user", "content": "earlier"}),
        ]) + "\n", encoding="utf-8")
        before = base.read_text(encoding="utf-8")
        result = self._cli("--profiles", "local,hosted", "--session", "notes",
                           "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["base_context"]["messages"], 1)
        self.assertEqual(base.read_text(encoding="utf-8"), before)
        # And the seeded context really reached both requests.
        for request in self.stub.requests:
            messages = json.loads(request["raw"])["messages"]
            self.assertIn({"role": "user", "content": "earlier"}, messages)

    def test_every_run_ends_its_trace_with_a_terminal_event(self):
        # The single-exit contract is a property of the comparison, not of a
        # single run: each profile's trace must end, or the validator that
        # this package's output feeds rejects the file.
        result = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        terminal = {"run.finished", "run.failed", "run.cancelled"}
        for run in manifest["runs"]:
            types = [json.loads(line)["type"] for line in
                     (self.workspace / run["trace"]).read_text(
                         encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(types[0], "run.started")
            self.assertIn(types[-1], terminal)

    def test_no_credential_reaches_stdout_or_the_manifest(self):
        result = self._cli("--profiles", "local,hosted", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("local-fake", result.stdout)
        self.assertNotIn("hosted-fake", result.stdout)
        for path in self._manifests():
            self.assertNotIn("local-fake", path.read_text(encoding="utf-8"))
            self.assertNotIn("hosted-fake", path.read_text(encoding="utf-8"))

    def test_compare_help_documents_the_command(self):
        result = self._cli("--help")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("compare", result.stdout)
        self.assertIn("--profiles", result.stdout)

    def test_the_same_prompt_twice_writes_two_manifests(self):
        # Each comparison gets its own id and its own file. A second run must
        # not overwrite the first: the two measurements are both real.
        first = self._cli("--profiles", "local")
        second = self._cli("--profiles", "local")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        manifests = self._manifests()
        self.assertEqual(len(manifests), 2)
        self.assertEqual(len({json.loads(p.read_text(encoding="utf-8"))
                              ["comparison_id"] for p in manifests}), 2)

    def test_a_single_profile_comparison_is_still_a_comparison(self):
        result = self._cli("--profiles", "local", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([run["profile"] for run in payload["runs"]], ["local"])
        manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["runs"]), 1)

    def test_repeated_profile_names_are_refused(self):
        # `--profiles a,a` would run the same measurement twice and report it
        # as two, which is a difference that says nothing about anything.
        result = self._cli("--profiles", "local,local")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.workspace / "traces").exists())

    def test_an_empty_profile_list_is_refused(self):
        result = self._cli("--profiles", "")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.workspace / "traces").exists())

    def test_compare_runs_the_profiles_in_the_order_they_were_named(self):
        result = self._cli("--profiles", "hosted,local", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([run["profile"] for run in payload["runs"]],
                         ["hosted", "local"])
        self.assertEqual([r["model"] for r in self.stub.requests],
                         ["hosted-model", "local-model"])

    def test_a_quiet_comparison_still_writes_the_manifest(self):
        # --quiet is about stdout, not about the evidence. A comparison that
        # dropped its manifest because nobody was watching would lose the only
        # index over its runs.
        result = self._cli("--profiles", "local", "--quiet", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self._manifests()), 1)

    def test_a_single_run_flag_is_refused_rather_than_silently_dropped(self):
        # `compare` shares an option block with `run` and `chat`, so it
        # accepts `--profile` and `--trace` in its help. Neither means
        # anything to a comparison: the profiles come from --profiles and the
        # traces are one per profile. Accepting them and reading neither is
        # the worse of the two failures -- an operator who passed --profile
        # would believe the comparison went to that profile, when it ran the
        # ones --profiles named. Refusing names the conflict instead.
        result = self._cli("--profiles", "local,hosted", "--profile", "local")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--profiles", result.stderr)
        self.assertFalse((self.workspace / "traces").exists())
        self.assertEqual(self._manifests(), [])

        result = self._cli("--profiles", "local,hosted",
                           "--trace", "somewhere/else.jsonl")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("--trace", result.stderr)
        self.assertFalse((self.workspace / "traces").exists())
        self.assertEqual(self._manifests(), [])

    def _help_options(self, *command):
        """The option names `pocketchat <command> --help` lists."""
        result = hp.run_cli(list(command) + ["--help"], self.workspace)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listed = set()
        for line in result.stdout.splitlines():
            token = line.strip().split(" ")[0].rstrip(",")
            if token.startswith("-"):
                listed.add(token)
        return listed

    def test_help_does_not_advertise_the_single_run_options(self):
        # `compare` shares an option block with `run` and `chat`, so its help
        # listed `--profile` and `--trace`. A comparison runs several profiles
        # and writes one trace per profile, so neither option means anything
        # here and both are refused when passed. Advertising them made that
        # refusal read as a broken flag rather than a command that never took
        # it. They stay refused; they are simply no longer offered.
        listed = self._help_options("compare")
        self.assertNotIn("--profile", listed)
        self.assertNotIn("--trace", listed)
        self.assertIn("--profiles", listed)
        self.assertIn("--config", listed)
        self.assertIn("--verbose", listed)

    def test_run_and_chat_still_advertise_the_single_run_options(self):
        # The suppression belongs to `compare` alone. `run` and `chat` each
        # take one profile and write one trace, so their help must keep
        # listing both.
        for command in ("run", "chat"):
            listed = self._help_options(command)
            self.assertIn("--profile", listed)
            self.assertIn("--trace", listed)


if __name__ == "__main__":
    unittest.main()
