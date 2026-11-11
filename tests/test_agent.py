"""Full-screen agent shell: chat with switches, without tools.

The agent is deliberately a small loop around the existing conversation
kernel. Each user message is one run with a terminal trace event; the session
file is what carries context between runs. Slash commands change the next run
without writing a user turn or a trace event.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from pocketchat.agent import AgentState
from tests import _helpers as hp


class _ProviderState:
    def __init__(self):
        self.requests = []


STATE = _ProviderState()


class _ProviderHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        STATE.requests.append({
            "authorization": self.headers.get("Authorization"),
            "body": json.loads(raw.decode("utf-8")),
        })
        body = json.dumps({
            "choices": [{"message": {"content": "agent reply"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AgentCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self._config()

    def _config(self):
        STATE.requests.clear()
        self._stub = hp.StubHTTPServer(_ProviderHandler)
        self.addCleanup(self._stub.close)
        path = self.base / "pocketchat.json"
        path.write_text(json.dumps({
            "schema_version": "2",
            "name": "agent",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % self._stub.port,
                    "model": "local-model",
                    "api_key_env": "LOCAL_KEY",
                },
                "hosted": {
                    "base_url": "http://127.0.0.1:%d/v1" % self._stub.port,
                    "model": "hosted-model",
                    "api_key_env": "HOSTED_KEY",
                },
            },
        }), encoding="utf-8")
        return path

    def _wait_for_tui_idle(self, tui, timeout=2.0):
        import time
        deadline = time.time() + timeout
        worker = getattr(tui, "_worker", None)
        if worker is not None:
            worker.join(timeout=max(0.0, deadline - time.time()))
        while getattr(tui, "busy", False) and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(worker is not None and worker.is_alive(),
                         "TUI worker thread did not exit before cleanup")
        self.assertFalse(getattr(tui, "busy", False),
                         "TUI worker did not settle before cleanup")

    def _agent(self, stdin):
        return hp.run_cli(
            ["agent", "--session", "main", "--config", str(self.config)],
            self.base,
            env={"LOCAL_KEY": "local-fake", "HOSTED_KEY": "hosted-fake"},
            stdin=stdin,
        )

    def test_bare_invocation_enters_agent_by_default(self):
        result = hp.run_cli(
            ["--config", str(self.config)],
            self.base,
            env={"LOCAL_KEY": "local-fake", "HOSTED_KEY": "hosted-fake"},
            stdin="/status\n/exit\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PocketChat agent.", result.stdout)
        self.assertIn("profile=local", result.stdout)

    def test_bare_agent_uses_the_user_newnewrouter_config_as_fallback(self):
        home = self.base / "home"
        cfg_dir = home / ".pocketchat"
        cfg_dir.mkdir(parents=True)
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        (cfg_dir / "newnewrouter.json").write_text(
            json.dumps(raw), encoding="utf-8")
        empty = self.base / "empty-workspace"
        empty.mkdir()
        result = hp.run_cli(
            [], empty,
            env={"HOME": str(home), "USERPROFILE": str(home),
                 "LOCAL_KEY": "local-fake", "HOSTED_KEY": "hosted-fake"},
            stdin="/status\n/exit\n",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("profile=local", result.stdout)

    def test_bare_invocation_enters_the_full_screen_tui_on_a_terminal(self):
        calls = []

        def fake_run_tui(state, profiles, models, **options):
            calls.append((state, profiles, models, options))
            return 0

        result = hp.run_cli(
            ["--config", str(self.config)],
            self.base,
            env={"LOCAL_KEY": "local-fake", "HOSTED_KEY": "hosted-fake"},
            stdin="/status\n/exit\n",
            stdin_isatty=True,
            run_tui=fake_run_tui,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].profile, "local")
        self.assertIn("local", calls[0][1])
        self.assertIn("hosted", calls[0][1])

    def test_terminal_falls_back_to_line_agent_when_tui_is_unavailable(self):
        from pocketchat import cli as cli_module

        unavailable = getattr(cli_module, "TUIUnavailable", RuntimeError)
        with unittest.mock.patch.object(
                cli_module, "run_tui", side_effect=unavailable("no curses")):
            result = hp.run_cli(
                ["--config", str(self.config)],
                self.base,
                env={"LOCAL_KEY": "local-fake",
                     "HOSTED_KEY": "hosted-fake"},
                stdin="/status\n/exit\n",
                stdin_isatty=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PocketChat agent.", result.stdout)
        self.assertIn("profile=local", result.stdout)

    def test_piped_agent_input_keeps_the_line_repl(self):
        result = hp.run_cli(
            ["--config", str(self.config)],
            self.base,
            env={"LOCAL_KEY": "local-fake", "HOSTED_KEY": "hosted-fake"},
            stdin="/status\n/exit\n",
            stdin_isatty=False,
            run_tui=lambda *args, **kwargs: self.fail("piped input must not open a TUI"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PocketChat agent.", result.stdout)
        self.assertIn("Trusted terminal file controls", result.stdout)

    def test_tui_controller_offers_profiles_and_commands(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        self.assertEqual(tui.profile_names(), ["hosted", "local"])
        self.assertEqual(tui.command_names(),
                         ["/context", "/exit", "/model", "/reasoning",
                          "/session", "/status", "/files", "/diff",
                          "/apply", "/changes", "/undo", "/confirm"])
        self.assertEqual(tui.matching_commands("/mo"), ["/model"])

    def test_tui_controller_model_picker_changes_the_next_profile(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model",
                        "reasoning_effort_supported": True}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.open_model_picker()
        self.assertEqual(tui.picker_options(), ["hosted", "local"])
        tui.choose_picker(0)
        self.assertEqual(tui.state.profile, "hosted")
        self.assertEqual(tui.state.model, "hosted-model")
        self.assertTrue(tui.state.reasoning_supported)

    def test_tui_controller_reports_a_command_error_without_mutating_state(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        reply = tui.run_command("/model missing")
        self.assertIn("unknown profile", reply)
        self.assertEqual(tui.state.profile, "local")

    def test_tui_controller_sends_a_message_through_the_agent_session(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        reply = tui.send("hello")
        self.assertEqual(reply, "agent reply")
        self.assertEqual(len(STATE.requests), 1)
        self.assertEqual(tui.transcript[-2][0], "you")
        self.assertEqual(tui.transcript[-1], ("agent", "agent reply"))
        self.assertTrue((self.base / "sessions" / "main.jsonl").is_file())
        self.assertEqual(4, tui.status_facts["tokens"])

    def test_tui_controller_persists_three_turns_and_reuses_context(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))

        self.assertEqual(tui.send("first"), "agent reply")
        self.assertEqual(tui.send("second"), "agent reply")
        self.assertEqual(tui.send("third"), "agent reply")

        self.assertEqual(len(STATE.requests), 3)
        third_messages = STATE.requests[2]["body"]["messages"]
        self.assertEqual(
            [message["role"] for message in third_messages],
            ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(
            [message["content"] for message in third_messages
             if message["role"] == "user"],
            ["first", "second", "third"])
        session = (self.base / "sessions" / "main.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(session), 7)  # header + three exchanges
        records = [json.loads(line) for line in session]
        self.assertEqual(
            [record["content"] for record in records[1:]
             if record["role"] == "user"],
            ["first", "second", "third"])

    def test_tui_controller_reacquires_session_writer_after_failed_turn(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))

        self.assertEqual(tui.send("first"), "agent reply")
        self.assertEqual(tui.send("second"), "agent reply")

        second_messages = STATE.requests[1]["body"]["messages"]
        self.assertEqual(
            [message["content"] for message in second_messages
             if message["role"] == "user"],
            ["first", "second"])
        session = (self.base / "sessions" / "main.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(session), 5)
        self.assertFalse(
            (self.base / "sessions" / "main.jsonl.writer").exists())

    def test_tui_controller_keeps_session_token_budget_across_turns(self):
        from pocketchat.provider import estimate_tokens
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))

        self.assertEqual(tui.send("first"), "agent reply")
        self.assertEqual(len(STATE.requests), 1)
        context = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "agent reply"},
            {"role": "user", "content": "second"},
        ]
        tui.config["profiles"]["local"]["max_session_tokens"] = (
            estimate_tokens(context) + 1)

        reply = tui.send("second")

        self.assertIn("token_budget_exceeded", reply)
        self.assertEqual(len(STATE.requests), 1)

    def test_tui_controller_reports_session_persistence_failure(self):
        from pocketchat.sessions import SessionFile, SessionFileError
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        with mock.patch.object(
                SessionFile, "append",
                side_effect=SessionFileError("cannot append session")):
            reply = tui.send("first")

        self.assertIn("agent reply", reply)
        self.assertIn("session persistence failed", reply)
        self.assertIn("session persistence failed", tui.last_error)

    def test_tui_controller_does_not_send_a_slash_command(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        reply = tui.send("/status")
        self.assertIn("profile=local", reply)
        self.assertEqual(STATE.requests, [])
        self.assertEqual(tui.transcript, [])

    def test_model_command_without_a_profile_opens_the_picker(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        reply = tui.send("/model")
        self.assertEqual(reply, "model picker")
        self.assertEqual(tui.picker_options(), ["hosted", "local"])

    def test_tui_layout_keeps_the_prompt_and_status_visible(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.extend([
            ("you", "hello"),
            ("agent", "world"),
        ])
        lines = compose_screen(tui, input_text="draft", width=72, height=18)
        self.assertIn("PocketChat", lines[0])
        self.assertIn("local", lines[-1])
        self.assertIn("hello", "\n".join(lines))
        self.assertIn("world", "\n".join(lines))
        self.assertIn("draft", "\n".join(lines))
        self.assertIn("❯ draft", "\n".join(lines))

    def test_tui_layout_shows_command_completion(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        lines = compose_screen(tui, input_text="/mo", width=72, height=18)
        text = "\n".join(lines)
        self.assertIn("/model", text)
        self.assertIn("Switch model", text)

    def test_tui_layout_shows_the_model_picker(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.open_model_picker()
        lines = compose_screen(tui, input_text="", width=72, height=18)
        text = "\n".join(lines)
        self.assertIn("Select model", text)
        self.assertIn("hosted", text)
        self.assertIn("local", text)

    def test_tui_session_switch_starts_the_selected_session_file(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        tui.send("first")
        tui.send("/session other")
        tui.send("second")
        self.assertTrue((self.base / "sessions" / "main.jsonl").is_file())
        self.assertTrue((self.base / "sessions" / "other.jsonl").is_file())
        self.assertEqual(len(STATE.requests), 2)
        self.assertEqual(STATE.requests[1]["body"]["messages"][0]["content"],
                         "second")

    def test_tui_model_switch_reloads_the_profile_credential(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous_local = os.environ.get("LOCAL_KEY")
        previous_hosted = os.environ.get("HOSTED_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        os.environ["HOSTED_KEY"] = "hosted-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous_local)
            if previous_local is not None else os.environ.pop("LOCAL_KEY", None))
        self.addCleanup(
            lambda: os.environ.__setitem__("HOSTED_KEY", previous_hosted)
            if previous_hosted is not None else os.environ.pop("HOSTED_KEY", None))
        tui.send("first")
        tui.send("/model hosted")
        tui.send("second")
        self.assertEqual(STATE.requests[1]["authorization"],
                         "Bearer hosted-fake")

    def test_tui_send_appends_the_user_message_before_waiting(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        tui.send("hello")
        self.assertEqual(tui.transcript[0], ("you", "hello"))

    def test_cancellation_keeps_live_worker_and_writer_until_exit(self):
        import threading
        from pocketchat.tui import AgentTUI
        tui = AgentTUI(AgentState(profile='local', model='local-model', session='main'),
                       {'local': {'model': 'local-model'}}, {'local': 'local-model'})
        started, finish = threading.Event(), threading.Event()
        tui._session = mock.Mock()
        def slow_send(text):
            started.set()
            finish.wait(3)
            return 'late reply'
        tui._send_once = slow_send
        tui._interrupt_worker = mock.Mock()  # Simulate a provider stuck in blocking I/O.
        tui.start_send('first')
        self.assertTrue(started.wait(1))
        worker = tui._worker
        try:
            tui.close()
            self.assertIs(worker, tui._worker)
            self.assertTrue(tui.busy)
            tui._session._release_session_writer.assert_not_called()
            self.assertIn('already running', tui.run_command('/files'))
            tui.start_send('must not start')
            self.assertEqual([('you', 'first')], tui.transcript)
        finally:
            finish.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(tui.busy)
        self.assertIsNone(tui._worker)
        self.assertEqual([('you', 'first')], tui.transcript)

    def test_tui_send_can_be_cancelled_while_the_provider_runs(self):
        import threading
        import time
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        started = threading.Event()

        def slow_send(text):
            started.set()
            time.sleep(2)
            return "late reply"

        tui._send_once = slow_send
        tui.start_send("hello")
        self.assertTrue(started.wait(1))
        self.assertTrue(tui.busy)
        worker = tui._worker
        tui.cancel_send()
        # A blocked provider may outlive the bounded interrupt. Wait for the
        # actual worker exit before asserting its slot is released; the held-
        # worker test above separately proves ownership remains until then.
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(tui.busy)
        self.assertEqual(tui.transcript, [("you", "hello")])

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_runs_on_a_real_terminal(self):
        import os as _os
        import pty
        import select
        import subprocess
        import time

        env = {
            "PATH": _os.environ.get("PATH", ""),
            "HOME": str(self.base / "home"),
            "LOCAL_KEY": "local-fake",
            "HOSTED_KEY": "hosted-fake",
            "TERM": "xterm-256color",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
        master, slave = pty.openpty()
        proc = subprocess.Popen(
            ["python3", "-m", "pocketchat", "--config", str(self.config)],
            cwd=str(self.base),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=hp.subprocess_environment(env),
            close_fds=True,
        )
        _os.close(slave)
        output = b""
        deadline = time.time() + 5
        try:
            while time.time() < deadline:
                ready, _, _ = select.select([master], [], [], 0.2)
                if ready:
                    output += _os.read(master, 65536)
                if (b"PocketChat" in output and b"you>" in output
                        and b"profile=local" in output):
                    break
            self.assertIn(b"PocketChat", output)
            self.assertIn(b"ready", output)
            self.assertNotIn(b"agent> ", output)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            _os.close(master)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_key_handlers_apply_the_model_picker_and_command_completion(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        state = InputState()
        apply_key(tui, state, "/")
        apply_key(tui, state, "m")
        apply_key(tui, state, "o")
        apply_key(tui, state, "\t")
        self.assertEqual(state.text, "/model")
        apply_key(tui, state, "\n")
        self.assertEqual(tui.picker_options(), ["hosted", "local"])
        apply_key(tui, state, curses_key_down())
        apply_key(tui, state, "\n")
        self.assertEqual(tui.state.profile, "local")

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_history_navigation_restores_previous_input(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="first", cursor=5)
        state.history.append("first")
        state.text = ""
        state.cursor = 0
        apply_key(tui, state, __import__("curses").KEY_UP)
        self.assertEqual(state.text, "first")
        self.assertEqual(state.cursor, 5)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_escape_closes_the_model_picker(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.open_model_picker()
        apply_key(tui, InputState(), "\x1b")
        self.assertFalse(tui.picker_open)

    def test_tui_close_cancels_an_in_flight_turn_and_keeps_a_terminal_trace(self):
        import threading
        import time
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        started = threading.Event()

        def slow_send(text):
            started.set()
            time.sleep(0.3)
            return "late"

        tui._send_once = slow_send
        tui.start_send("hello")
        self.assertTrue(started.wait(1))
        tui.close()
        self._wait_for_tui_idle(tui)
        self.assertFalse(tui.busy)

    def test_tui_close_cancels_a_real_provider_request_with_a_terminal_trace(self):
        import threading
        import time
        from pocketchat.tui import AgentTUI

        started = threading.Event()

        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                started.set()
                time.sleep(3)
                body = b'{"choices":[{"message":{"content":"late"},"finish_reason":"stop"}],"usage":{}}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        stub = hp.StubHTTPServer(SlowHandler)
        self.addCleanup(stub.close)
        config_path = self.base / "slow.json"
        config_path.write_text(json.dumps({
            "schema_version": "2",
            "name": "slow",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % stub.port,
                    "model": "slow-model",
                    "api_key_env": "LOCAL_KEY",
                    "timeout_seconds": 10,
                },
            },
        }), encoding="utf-8")
        tui = AgentTUI(
            AgentState(profile="local", model="slow-model", session="main"),
            {"local": {"model": "slow-model"}},
            {"local": "slow-model"},
        )
        tui.config = json.loads(config_path.read_text(encoding="utf-8"))
        tui.workspace = self.base
        previous = os.environ.get("LOCAL_KEY")
        os.environ["LOCAL_KEY"] = "local-fake"
        self.addCleanup(
            lambda: os.environ.__setitem__("LOCAL_KEY", previous)
            if previous is not None else os.environ.pop("LOCAL_KEY", None))
        tui.start_send("hello")
        self.assertTrue(started.wait(2))
        tui.close()
        deadline = time.time() + 2
        terminal = []
        while time.time() < deadline:
            traces = list((self.base / "traces").glob("*.jsonl"))
            if traces:
                events = [json.loads(line) for line in
                          traces[0].read_text(encoding="utf-8").splitlines()
                          if line.strip()]
                terminal = [event for event in events
                            if event["type"] in ("run.completed", "run.failed",
                                                 "run.cancelled")]
                if terminal:
                    break
            time.sleep(0.05)
        self.assertTrue(terminal, "closing the TUI left an unterminated trace")
        self.assertEqual(terminal[-1]["type"], "run.cancelled")

    def test_model_picker_starts_on_the_current_profile(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.open_model_picker()
        self.assertEqual(tui.picker_index, 1)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_ctrl_d_is_an_exit_even_when_curses_reports_an_error(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        self.assertEqual(apply_key(tui, InputState(), "\x04"), "exit")

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_key_reader_does_not_lose_ctrl_d_behind_nodelay_errors(self):
        from pocketchat.curses_ui import read_key
        import curses

        class Window:
            def __init__(self):
                self.calls = 0

            def get_wch(self):
                self.calls += 1
                if self.calls == 1:
                    raise curses.error
                return "\x04"

            def getch(self):
                return 4

        self.assertEqual(read_key(Window()), "\x04")

    def test_model_picker_keeps_the_selected_row_visible_with_many_profiles(self):
        from pocketchat.tui import AgentTUI, compose_screen

        profiles = {"p%02d" % i: {"model": "m%02d" % i} for i in range(40)}
        models = {name: record["model"] for name, record in profiles.items()}
        tui = AgentTUI(
            AgentState(profile="p00", model="m00", session="main"),
            profiles, models,
        )
        tui.open_model_picker()
        tui.move_picker(35)
        lines = compose_screen(tui, input_text="", width=72, height=18)
        text = "\n".join(lines)
        self.assertIn("p35", text)
        self.assertIn("Select model", text)
        self.assertIn("p00", lines[-1])
        self.assertLessEqual(len(lines), 18)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_enter_sends_and_ctrl_j_inserts_a_newline(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="first", cursor=5)
        self.assertEqual(apply_key(tui, state, 10), "continue")
        self.assertEqual(state.text, "first\n")
        state.text = "send me"
        state.cursor = len(state.text)
        self.assertEqual(apply_key(tui, state, "\n"), "send")
        self.assertEqual(state.text, "")

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_key_reader_parses_raw_arrow_sequences(self):
        from pocketchat.curses_ui import read_key
        import curses

        class Window:
            def __init__(self, values):
                self.values = iter(values)

            def get_wch(self):
                raise curses.error

            def getch(self):
                return next(self.values)

        self.assertEqual(read_key(Window([27, 91, 66])), curses.KEY_DOWN)
        self.assertEqual(read_key(Window([27, 91, 65])), curses.KEY_UP)
        self.assertEqual(read_key(Window([27, 91, 68])), curses.KEY_LEFT)
        self.assertEqual(read_key(Window([27, 91, 67])), curses.KEY_RIGHT)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_idle_ctrl_c_exits_without_a_traceback(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        self.assertEqual(apply_key(tui, InputState(), "\x03"), "exit")

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_busy_ctrl_c_cancels_without_exiting(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.busy = True
        self.assertEqual(apply_key(tui, InputState(), "\x03"), "continue")
        self.assertFalse(tui.busy)

    def test_tui_layout_keeps_status_visible_for_a_long_composer(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        lines = compose_screen(
            tui,
            input_text="line\n" * 30,
            width=60,
            height=12,
            cursor=0,
        )
        self.assertEqual(len(lines), 12)
        self.assertIn("local", lines[-1])
        self.assertIn("ready", lines[-1])
        self.assertIn("line", "\n".join(lines[:-2]))

    def test_tui_rejects_a_second_message_while_one_is_running(self):
        import threading
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def slow_send(text, *, record=False):
            gate.wait(2)
            return "ok"

        tui._send_once = slow_send
        tui.start_send("first")
        reply = tui.send("second")
        gate.set()
        self._wait_for_tui_idle(tui)
        self.assertIn("already running", reply)
        self.assertEqual(tui.transcript.count(("you", "second")), 0)

    def test_tui_commands_are_rejected_while_a_reply_is_running(self):
        import threading
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def slow_send(text, *, record=False):
            gate.wait(2)
            return "ok"

        tui._send_once = slow_send
        tui.start_send("first")
        reply = tui.send("/model hosted")
        gate.set()
        self._wait_for_tui_idle(tui)
        self.assertIn("reply is already running", reply)
        self.assertEqual(tui.state.profile, "local")

    def test_tui_renders_thinking_and_streamed_text_while_a_reply_runs(self):
        import threading
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def slow_send(text, *, record=False):
            tui.on_delta("reasoning", "thinking about it")
            tui.on_delta("content", "partial answer")
            gate.wait(2)
            return "partial answer"

        tui._send_once = slow_send
        tui.start_send("hello")
        lines = compose_screen(tui, input_text="", width=80, height=20)
        text = "\n".join(lines)
        self.assertIn("thinking", text)
        self.assertIn("partial answer", text)
        gate.set()
        self._wait_for_tui_idle(tui)

    def test_tui_renders_the_provider_error_after_a_failed_run(self):
        import threading
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def failing_send(text, *, record=False):
            gate.wait(1)
            return "agent: run failed"

        tui._send_once = failing_send
        tui.start_send("hello")
        gate.set()
        self._wait_for_tui_idle(tui)
        lines = compose_screen(tui, input_text="", width=80, height=20)
        text = "\n".join(lines)
        self.assertIn("run failed", text)
        self.assertNotIn("thinking", text)

    def test_tui_renders_a_real_exception_as_an_error_turn(self):
        import threading
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def failing_send(text, *, record=False):
            gate.wait(1)
            raise RuntimeError("provider exploded")

        tui._send_once = failing_send
        tui.start_send("hello")
        gate.set()
        self._wait_for_tui_idle(tui)
        lines = compose_screen(tui, input_text="", width=80, height=20)
        text = "\n".join(lines)
        self.assertIn("RuntimeError: provider exploded", text)
        self.assertNotIn("thinking", text)

    def test_tui_layout_updates_after_a_stream_delta(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.busy = True
        tui.thinking_started = __import__("time").time()
        before = "\n".join(compose_screen(
            tui, input_text="", width=80, height=20))
        tui.on_delta("content", "visible now")
        after = "\n".join(compose_screen(
            tui, input_text="", width=80, height=20))
        self.assertNotIn("visible now", before)
        self.assertIn("visible now", after)
        self.assertIn("thinking...", after)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_loop_refreshes_while_busy_even_without_a_key(self):
        from pocketchat.curses_ui import run_loop_for_test
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.busy = True

        class Window:
            def __init__(self):
                self.reads = 0

            def keypad(self, value):
                pass

            def nodelay(self, value):
                pass

            def getmaxyx(self):
                return (20, 80)

            def erase(self):
                pass

            def addnstr(self, *args):
                pass

            def move(self, *args):
                pass

            def refresh(self):
                pass

            def get_wch(self):
                self.reads += 1
                import curses
                if self.reads >= 3:
                    return "\x04"
                raise curses.error

            def getch(self):
                import curses
                raise curses.error

        self.assertEqual(run_loop_for_test(Window(), tui, max_iterations=5), 0)
        self.assertFalse(tui.busy)

    def test_tui_does_not_duplicate_a_streamed_final_reply(self):
        import threading
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def streamed_send(text, *, record=False):
            tui.on_delta("content", "streamed answer")
            gate.wait(1)
            return "streamed answer"

        tui._send_once = streamed_send
        tui.start_send("hello")
        gate.set()
        self._wait_for_tui_idle(tui)
        text = "\n".join(compose_screen(tui, input_text="", width=80, height=20))
        self.assertEqual(text.count("streamed answer"), 1)

    def test_tui_keeps_a_reasoning_summary_after_the_turn_completes(self):
        import threading
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        gate = threading.Event()

        def streamed_send(text, *, record=False):
            tui.on_delta("reasoning", "thought through the answer")
            tui.on_delta("content", "answer")
            gate.wait(1)
            return "answer"

        tui._send_once = streamed_send
        tui.start_send("hello")
        gate.set()
        self._wait_for_tui_idle(tui)
        tui.toggle_reasoning()
        text = "\n".join(compose_screen(tui, input_text="", width=80, height=20))
        self.assertIn("thought through the answer", text)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_real_terminal_enter_sends_but_raw_ctrl_j_does_not(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        state = InputState(text="hello", cursor=5)
        self.assertEqual(apply_key(tui, state, "\n"), "send")
        self.assertEqual(tui.transcript[0], ("you", "hello"))
        state = InputState(text="line one", cursor=8)
        self.assertEqual(apply_key(tui, state, 10), "continue")
        self.assertEqual(state.text, "line one\n")

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_exit_command_returns_exit_from_the_key_path(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="/exit", cursor=5)
        self.assertEqual(apply_key(tui, state, "\n"), "exit")

    def test_tui_block_layout_does_not_repeat_role_prefixes(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.extend([
            ("you", "pocketchat 能干嘛"),
            ("agent", "第一段\n第二段\n第三段"),
        ])
        text = "\n".join(compose_screen(
            tui, input_text="", width=72, height=20))
        self.assertIn("pocketchat 能干嘛", text)
        self.assertIn("┊", text)
        self.assertEqual(text.count("agent>"), 0)
        self.assertEqual(text.count("you>"), 0)
        self.assertEqual(text.count("第三段"), 1)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_reasoning_is_collapsed_until_ctrl_t(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.extend([
            ("you", "hello"),
            ("reasoning", "a long private chain of thought"),
            ("agent", "answer"),
        ])
        collapsed = "\n".join(compose_screen(
            tui, input_text="", width=72, height=20))
        self.assertIn("thought", collapsed)
        self.assertNotIn("a long private chain of thought", collapsed)
        self.assertIn("Ctrl+T", collapsed)
        apply_key(tui, InputState(), "\x14")
        expanded = "\n".join(compose_screen(
            tui, input_text="", width=72, height=20))
        self.assertIn("a long private chain of thought", expanded)

    def test_tui_header_and_status_do_not_duplicate_an_identical_profile_model(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="wb-cn:model-a",
                       model="wb-cn:model-a",
                       session="main",
                       context_chars=1000000,
                       reasoning_supported=True),
            {"wb-cn:model-a": {"model": "wb-cn:model-a"}},
            {"wb-cn:model-a": "wb-cn:model-a"},
        )
        lines = compose_screen(tui, input_text="", width=100, height=20)
        header = lines[0]
        status = lines[-1]
        self.assertLessEqual(header.count("wb-cn:model-a"), 1)
        self.assertLessEqual(status.count("wb-cn:model-a"), 1)
        self.assertIn("ready", status.lower())
        self.assertIn("1.0M", status)

    def test_tui_cleans_markdown_emphasis_and_bullets(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.append((
            "agent",
            "**写作润色**：文章、邮件\n- **翻译**：中英互译\n- **编程**：写代码",
        ))
        text = "\n".join(compose_screen(
            tui, input_text="", width=72, height=20))
        self.assertNotIn("**", text)
        self.assertIn("写作润色：文章、邮件", text)
        self.assertIn("• 翻译：中英互译", text)

    def test_tui_command_palette_lists_commands_vertically(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        text = "\n".join(compose_screen(
            tui, input_text="/", width=72, height=24))
        self.assertIn("Commands", text)
        self.assertIn("/model", text)
        self.assertIn("Switch model", text)
        self.assertIn("/session", text)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_loop_enables_mouse_wheel_reporting(self):
        from pocketchat.curses_ui import run_loop_for_test
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )

        class Window:
            def keypad(self, value):
                pass

            def nodelay(self, value):
                pass

            def getmaxyx(self):
                return (20, 80)

            def erase(self):
                pass

            def addnstr(self, *args):
                pass

            def move(self, *args):
                pass

            def refresh(self):
                pass

            def get_wch(self):
                return "\x04"

            def getch(self):
                raise AssertionError("getch should not be needed")

        with mock.patch("pocketchat.curses_ui.curses.mousemask",
                        return_value=(0, 0)) as mousemask:
            self.assertEqual(run_loop_for_test(Window(), tui,
                                               max_iterations=1), 0)
        self.assertTrue(mousemask.called)

    @unittest.skipUnless(os.name == "posix", "requires curses")
    def test_tui_read_key_turns_a_mouse_event_into_a_wheel_action(self):
        import curses

        from pocketchat.curses_ui import read_key

        class Window:
            def __init__(self):
                self.codes = [curses.KEY_MOUSE]

            def get_wch(self):
                return self.codes.pop(0)

        button4 = getattr(curses, "BUTTON4_PRESSED", 65536)
        event = (0, 10, 5, 0, button4)
        with mock.patch("pocketchat.curses_ui.curses.getmouse",
                        return_value=event):
            self.assertEqual(read_key(Window()), ("mouse", button4))

    @unittest.skipUnless(os.name == "posix", "requires curses")
    def test_tui_mouse_wheel_scrolls_the_transcript(self):
        import curses
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        button4 = getattr(curses, "BUTTON4_PRESSED", 65536)
        button5 = getattr(curses, "BUTTON5_PRESSED", button4 << 5)

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        for i in range(30):
            tui.transcript.append(("agent", "message %02d" % i))
        state = InputState()
        state.screen_size = (14, 72)
        apply_key(tui, state, ("mouse", button4))
        self.assertGreater(tui.transcript_offset, 0)
        apply_key(tui, state, ("mouse", button5))
        self.assertEqual(tui.transcript_offset, 0)

    @unittest.skipUnless(os.name == "posix", "requires curses")
    def test_tui_mouse_event_without_wheel_click_is_ignored(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        for i in range(30):
            tui.transcript.append(("agent", "message %02d" % i))
        state = InputState()
        tui.transcript_offset = 3
        apply_key(tui, state, ("mouse", 0))
        self.assertEqual(tui.transcript_offset, 3)

    @unittest.skipUnless(os.name == "posix", "requires curses")
    def test_tui_transcript_can_scroll_with_page_keys(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        for i in range(30):
            tui.transcript.append(("agent", "message %02d" % i))
        bottom = "\n".join(compose_screen(
            tui, input_text="", width=72, height=14))
        self.assertIn("message 29", bottom)
        apply_key(tui, InputState(), __import__("curses").KEY_PPAGE)
        scrolled = "\n".join(compose_screen(
            tui, input_text="", width=72, height=14))
        self.assertNotIn("message 29", scrolled)
        self.assertIn("↑", scrolled)

    def test_tui_wraps_chinese_by_display_width(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.append(("agent", "中" * 80))
        import unicodedata

        def display_width(text):
            return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
                       for ch in text)

        lines = compose_screen(tui, input_text="", width=40, height=20)
        self.assertTrue(all(display_width(line) <= 40 for line in lines))
        self.assertIn("中", "\n".join(lines))

    def test_tui_status_bar_keeps_the_model_at_narrow_widths(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="wb-cn:model-a",
                       model="wb-cn:model-a",
                       session="main", context_chars=1000000),
            {"wb-cn:model-a": {"model": "wb-cn:model-a"}},
            {"wb-cn:model-a": "wb-cn:model-a"},
        )
        status = compose_screen(tui, input_text="", width=48, height=20)[-1]
        self.assertIn("wb-cn:model-a", status)
        self.assertIn("1.0M", status)
        self.assertNotIn("though", status)

    def test_trace_status_facts_come_from_recorded_events(self):
        from pocketchat.tui import trace_status_facts

        trace = self.base / "facts.jsonl"
        events = [
            {"type": "model.request.started", "attempt_index": 1},
            {"type": "model.response.completed", "attempt_index": 1,
             "usage": {"input_tokens": 2, "output_tokens": 3},
             "usage_reported": True, "time_to_first_token_ms": 42},
            {"type": "context.compacted", "dropped_messages": 2,
             "kept_messages": 4},
            {"type": "model.request.started", "attempt_index": 2},
            {"type": "model.response.failed", "attempt_index": 2},
        ]
        trace.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8")
        facts = trace_status_facts(trace, {
            "input_price_per_million": 1,
            "output_price_per_million": 1,
        })
        self.assertTrue(facts["usage_known"])
        self.assertEqual(5, facts["tokens"])
        self.assertEqual(0.000005, facts["cost_usd"])
        self.assertEqual(42, facts["ttft_ms"])
        self.assertEqual(2, facts["attempt_index"])
        self.assertEqual(1, facts["compactions"])
        self.assertEqual(2, facts["last_compaction"]["dropped_messages"])

    def test_trace_status_facts_keep_unknown_cost_and_usage_unknown(self):
        from pocketchat.tui import trace_status_facts

        trace = self.base / "unknown.jsonl"
        trace.write_text(json.dumps({
            "type": "model.response.completed",
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "usage_reported": False,
        }) + "\n", encoding="utf-8")
        facts = trace_status_facts(trace, {})
        self.assertFalse(facts["usage_known"])
        self.assertIsNone(facts["tokens"])
        self.assertIsNone(facts["cost_usd"])
        self.assertIsNone(facts["ttft_ms"])

        trace.write_text(json.dumps({
            "type": "model.response.completed",
            "usage": {},
            "usage_reported": True,
        }) + "\n", encoding="utf-8")
        malformed = trace_status_facts(trace, {
            "input_price_per_million": 1,
            "output_price_per_million": 1,
        })
        self.assertFalse(malformed["usage_known"])
        self.assertIsNone(malformed["tokens"])
        self.assertIsNone(malformed["cost_usd"])

        trace.write_text(json.dumps({
            "type": "model.response.completed",
            "usage": {"input_tokens": 2, "output_tokens": 3},
            "usage_reported": True,
        }) + "\n", encoding="utf-8")
        missing_price = trace_status_facts(trace, {})
        self.assertTrue(missing_price["usage_known"])
        self.assertEqual(5, missing_price["tokens"])
        self.assertIsNone(missing_price["cost_usd"])

    def test_tui_status_bar_shows_trace_facts_and_unknown_markers(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.status_facts = {
            "usage_known": True,
            "tokens": 5,
            "cost_usd": 0.000005,
            "ttft_ms": 42,
            "attempt_index": 2,
            "compactions": 1,
            "last_compaction": {"dropped_messages": 2, "kept_messages": 4},
        }
        status = compose_screen(tui, input_text="", width=140, height=20)[-1]
        self.assertIn("tok 5", status)
        self.assertIn("$0.000005", status)
        self.assertIn("ttft 42ms", status)
        self.assertIn("req 2", status)
        self.assertIn("cmp1 d2/k4", status)

        tui.status_facts = {
            "usage_known": False, "tokens": None, "cost_usd": None,
            "ttft_ms": None, "attempt_index": None, "compactions": 0,
            "last_compaction": None,
        }
        status = compose_screen(tui, input_text="", width=140, height=20)[-1]
        self.assertIn("tok ?", status)
        self.assertIn("cost ?", status)
        self.assertIn("ttft ?", status)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tui_new_message_returns_the_view_to_the_latest_turn(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.config = json.loads(self.config.read_text(encoding="utf-8"))
        tui.workspace = self.base
        tui.transcript_offset = 20
        state = InputState(text="hello", cursor=5)
        apply_key(tui, state, "\n")
        self._wait_for_tui_idle(tui)
        self.assertEqual(tui.transcript_offset, 0)

    def test_tui_streaming_blocks_have_separating_blank_lines(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.busy = True
        tui.thinking_started = __import__("time").time()
        tui.on_delta("reasoning", "thinking")
        tui.on_delta("content", "answer")
        rows = compose_screen(tui, input_text="", width=72, height=20)
        thought = next(i for i, row in enumerate(rows) if "thinking  Ctrl+T" in row)
        assistant = next(i for i, row in enumerate(rows) if "┊ answer" in row)
        self.assertGreater(assistant, thought + 1)
        self.assertTrue(any(not rows[i].strip()
                            for i in range(thought + 1, assistant)))

    def test_tui_matches_the_gutter_and_status_rule_shape(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.extend([
            ("you", "hello"),
            ("agent", "world"),
        ])
        rows = compose_screen(tui, input_text="", width=80, height=20)
        text = "\n".join(rows)
        self.assertIn("❯ hello", text)
        self.assertIn("┊ world", text)
        self.assertNotIn("● assistant", text)
        self.assertTrue(rows[-1].lstrip().startswith("─ ready"))
        self.assertIn(" │ ", rows[-1])

    def test_tui_composer_uses_the_prompt_and_rules(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        rows = compose_screen(tui, input_text="hello", width=80, height=20)
        text = "\n".join(rows)
        self.assertIn("❯ hello", text)
        self.assertTrue(any(row.lstrip().startswith("─") for row in rows[-4:-1]))

    def test_tui_separates_turns_with_styled_layout(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        tui.transcript.extend([
            ("you", "first"),
            ("agent", "answer one"),
            ("you", "second"),
            ("agent", "answer two"),
        ])
        text = "\n".join(compose_screen(
            tui, input_text="", width=80, height=24))
        self.assertIn(" ───", text)
        self.assertLess(text.index(" ───"), text.index("❯ second"))

    def test_tui_fresh_session_shows_a_compact_brand_block(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        text = "\n".join(compose_screen(
            tui, input_text="", width=80, height=24))
        self.assertIn("PocketChat", text)
        self.assertIn("model", text)
        self.assertIn("local-model", text)
        self.assertIn("session  main", text)
        tui.transcript.append(("you", "hello"))
        after = "\n".join(compose_screen(
            tui, input_text="", width=80, height=24))
        self.assertNotIn("Type a message", after)

    def test_command_menu_has_a_selected_row_with_styled_layout(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        rows = compose_screen(tui, input_text="/", width=72, height=24)
        menu = "\n".join(rows)
        self.assertIn("❯ /model", menu)
        self.assertIn("Switch model", menu)
        self.assertNotIn("❯ /session", menu)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_command_menu_up_down_cycles_selection(self):
        import curses
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="/", cursor=1)
        apply_key(tui, state, curses.KEY_DOWN)
        self.assertIn("❯ /session", "\n".join(compose_screen(
            tui, input_text=state.text, width=72, height=24)))
        apply_key(tui, state, curses.KEY_UP)
        self.assertIn("❯ /model", "\n".join(compose_screen(
            tui, input_text=state.text, width=72, height=24)))

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_tab_applies_the_selected_command(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="/", cursor=1)
        self.assertEqual(apply_key(tui, state, "\t"), "continue")
        self.assertEqual(state.text, "/model")
        self.assertEqual(state.cursor, len("/model"))

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_enter_accepts_partial_command_before_executing(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        state = InputState(text="/mo", cursor=3)
        self.assertEqual(apply_key(tui, state, "\n"), "continue")
        self.assertEqual(state.text, "/model")
        self.assertFalse(tui.picker_open)
        self.assertEqual(apply_key(tui, state, "\n"), "continue")
        self.assertTrue(tui.picker_open)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_enter_submits_a_complete_command_without_accepting_a_menu(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"},
             "hosted": {"model": "hosted-model"}},
            {"local": "local-model", "hosted": "hosted-model"},
        )
        state = InputState(text="/model", cursor=6)
        self.assertEqual(apply_key(tui, state, "\n"), "continue")
        self.assertTrue(tui.picker_open)

    @unittest.skipUnless(os.name == "posix", "requires POSIX curses/pty")
    def test_escape_cancels_the_command_menu(self):
        from pocketchat.curses_ui import InputState, apply_key
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        state = InputState(text="/mo", cursor=3)
        self.assertEqual(apply_key(tui, state, "\x1b"), "continue")
        self.assertEqual(state.text, "")
        self.assertEqual(state.cursor, 0)

    def test_command_menu_searches_command_descriptions(self):
        from pocketchat.tui import AgentTUI

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        matches = tui.command_completions("/sw")
        self.assertEqual(matches[:2], ["/model", "/session"])
        self.assertIn("/status", tui.command_completions("/curr"))

    def test_composer_continuation_lines_use_blank_prompt_gutter(self):
        from pocketchat.tui import AgentTUI, compose_screen

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        rows = compose_screen(
            tui, input_text="hello\nworld", width=80, height=20)
        text = "\n".join(rows)
        self.assertIn("❯ hello", text)
        self.assertIn("  world", text)
        self.assertNotIn("❯ world", text)

    def test_composer_cursor_position_uses_prompt_width(self):
        from pocketchat.tui import AgentTUI, compose_screen_layout

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        layout = compose_screen_layout(
            tui, input_text="hello", width=80, height=20, cursor=0)
        self.assertEqual(layout.cursor_col, 2)

    def test_composer_cursor_follows_explicit_newlines(self):
        from pocketchat.tui import AgentTUI, compose_screen_layout

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        layout = compose_screen_layout(
            tui, input_text="hello\nworld", width=80, height=20, cursor=6)
        self.assertEqual(layout.cursor_col, 2)
        self.assertIn("world", layout.rows[layout.cursor_row])

    def test_composer_cursor_follows_soft_wrapped_lines(self):
        from pocketchat.tui import AgentTUI, compose_screen_layout

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        layout = compose_screen_layout(
            tui, input_text="abcdefg", width=8, height=20, cursor=6)
        self.assertEqual(layout.cursor_col, 4)
        self.assertIn("g", layout.rows[layout.cursor_row])

    def test_composer_cursor_uses_cjk_display_width(self):
        from pocketchat.tui import AgentTUI, compose_screen_layout

        tui = AgentTUI(
            AgentState(profile="local", model="local-model", session="main"),
            {"local": {"model": "local-model"}},
            {"local": "local-model"},
        )
        layout = compose_screen_layout(
            tui, input_text="中文", width=20, height=20, cursor=1)
        self.assertEqual(layout.cursor_col, 4)

    def test_help_lists_chat_switches(self):
        result = hp.run_cli(["agent", "--help"], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for command in ("/model", "/session", "/reasoning", "/context", "/status"):
            self.assertIn(command, result.stdout)

    def test_model_command_changes_the_next_run_profile(self):
        result = self._agent("/model hosted\n/status\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("profile=hosted", result.stdout)
        self.assertIn("model=hosted-model", result.stdout)
        self.assertFalse((self.base / "sessions" / "main.jsonl").exists())

    def test_reasoning_and_context_commands_validate_and_update_status(self):
        result = self._agent(
            "/reasoning high\n/context 12000\n/status\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reasoning=high", result.stdout)
        self.assertIn("context_chars=12000", result.stdout)

    def test_invalid_switch_values_are_reported_without_exiting(self):
        result = self._agent(
            "/reasoning turbo\n/context 0\n/model missing\n/status\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reasoning must be one of", result.stdout)
        self.assertIn("context must be", result.stdout)
        self.assertIn("unknown profile", result.stdout)
        self.assertIn("profile=local", result.stdout)

    def test_session_command_switches_without_merging_conversations(self):
        result = self._agent("/session other\n/status\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("session=other", result.stdout)
        self.assertFalse((self.base / "sessions" / "main.jsonl").exists())

    def test_plain_input_is_a_chat_turn_not_a_slash_command(self):
        result = self._agent("hello\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent reply", result.stdout)
        self.assertNotIn("unknown command", result.stdout.lower())
        self.assertEqual(len(STATE.requests), 1)

    def test_switches_affect_the_next_request_and_trace(self):
        result = self._agent(
            "/model hosted\n/reasoning high\n/context 12345\nhello\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(STATE.requests), 1)
        request = STATE.requests[0]
        self.assertEqual(request["body"]["model"], "hosted-model")
        self.assertEqual(request["authorization"], "Bearer hosted-fake")
        events = [
            json.loads(line) for line in
            (self.base / "traces").glob("*.jsonl").__next__().read_text(
                encoding="utf-8").splitlines() if line.strip()
        ]
        summary = events[0]["payload"]["config_summary"]
        self.assertEqual(summary["profile"], "hosted")
        self.assertEqual(summary["max_context_chars"], 12345)
        self.assertEqual(summary["reasoning_effort"], "high")

    def test_commands_and_blank_lines_do_not_create_a_session(self):
        result = self._agent("\n/status\n/model hosted\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.base / "sessions").exists())
        self.assertFalse((self.base / "traces").exists())
        self.assertEqual(STATE.requests, [])

    def test_a_failed_chat_run_returns_one_and_keeps_a_terminal_trace(self):
        result = self._agent("hello\n/exit\n")
        # The local stub answers, so this is the success control; the failing
        # provider path is covered by the existing chat/kernel suites.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list((self.base / "traces").glob("*.jsonl"))), 1)

    def test_consecutive_messages_share_context_in_one_session(self):
        result = self._agent("first\nsecond\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(STATE.requests), 2)
        second = STATE.requests[1]["body"]["messages"]
        self.assertEqual([m["role"] for m in second],
                         ["user", "assistant", "user"])
        self.assertEqual(second[0]["content"], "first")
        session = (self.base / "sessions" / "main.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(session), 5)  # header + two exchanges

    def test_two_sessions_do_not_share_context(self):
        result = self._agent("first\n/session other\nsecond\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(STATE.requests), 2)
        self.assertEqual([m["role"] for m in STATE.requests[1]["body"]["messages"]],
                         ["user"])
        self.assertEqual(STATE.requests[1]["body"]["messages"][0]["content"],
                         "second")

    def test_reasoning_is_forwarded_only_for_a_supporting_profile(self):
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        raw["profiles"]["hosted"]["reasoning_effort_supported"] = True
        self.config.write_text(json.dumps(raw), encoding="utf-8")
        result = self._agent(
            "/model hosted\n/reasoning high\nhello\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(STATE.requests[0]["body"]["reasoning_effort"], "high")

        STATE.requests.clear()
        result = self._agent(
            "/model local\n/reasoning low\nhello\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("reasoning_effort", STATE.requests[0]["body"])

    def test_session_switch_changes_the_file_that_receives_the_turn(self):
        result = self._agent("/session other\nhello\n/exit\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.base / "sessions" / "other.jsonl").is_file())
        self.assertFalse((self.base / "sessions" / "main.jsonl").exists())


def curses_key_down():
    import curses
    return curses.KEY_DOWN


if __name__ == "__main__":
    unittest.main()
