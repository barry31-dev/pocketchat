"""Profile selection tests: one config, several profiles, one run at a time.

The property under test is that choosing a profile is a selection and not a
second configuration format. Selecting the default profile must produce the
same run as naming nothing, an unknown name must fail before anything is
written, and the credential's value must never reach stdout -- only the name
of the variable that holds it and whether that variable is set.

The stub is a real local socket. Nothing here reaches a provider and no
credential is real.
"""

import json
import errno
import os
import pty
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from pocketchat.config import ConfigError, load_config, profile_config

from tests import _helpers as hp

ROOT = Path(__file__).resolve().parents[1]


def _cli(args, cwd, env=None):
    return hp.run_cli(args, cwd, env=env)


def _cli_subprocess(args, cwd, env=None):
    """The golden path: a real interpreter, a real pipe, a real exit."""
    environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    if env:
        environ.update(env)
    return subprocess.run(
        [sys.executable, "-m", "pocketchat"] + args,
        cwd=str(cwd), env=environ, capture_output=True, text=True, timeout=120,
    )


def _v2_config(**overrides):
    config = {
        "schema_version": "2",
        "name": "workbench",
        "default_profile": "local",
        "system_prompt": "Be brief.",
        "profiles": {
            "local": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 30,
            },
            "hosted": {
                "base_url": "https://example.invalid/v1",
                "model": "hosted-model",
                "api_key_env": "POCKETCHAT_HOSTED_KEY",
                "timeout_seconds": 90,
                "input_price_per_million": 0.5,
                "output_price_per_million": 1.5,
            },
        },
    }
    config.update(overrides)
    return config


class ProfileConfigTests(unittest.TestCase):
    def write(self, payload):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "pocketchat.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_v1_config_becomes_one_default_profile(self):
        path = self.write({
            "schema_version": "1",
            "name": "local",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY"
            },
            "system_prompt": "Be concise."
        })
        config = load_config(path)
        self.assertEqual(config["schema_version"], "2")
        self.assertEqual(config["default_profile"], "local")
        self.assertEqual(config["profiles"]["local"]["model"], "stub")

    def test_v2_config_rejects_unknown_profile_field(self):
        path = self.write({
            "schema_version": "2",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:8123/v1",
                    "model": "stub",
                    "api_key_env": "POCKETCHAT_API_KEY",
                    "typo": True
                }
            }
        })
        with self.assertRaisesRegex(ConfigError, "unknown profile field"):
            load_config(path)

    def test_v1_optional_fields_survive_the_migration(self):
        # The flat per-run keys are what Session, provider and the CLI read.
        # A migration that dropped an optional field would leave a v1 config
        # running with the default instead of what the operator wrote, which
        # is the silent-wrong-value failure this package refuses elsewhere.
        path = self.write({
            "schema_version": "1",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1/",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
                "timeout_seconds": 5,
                "max_context_chars": 100,
                "summary_prefix_chars": 7,
            },
        })
        config = load_config(path)
        profile = config["profiles"]["pocketchat"]
        self.assertEqual(config["timeout_seconds"], 5)
        self.assertEqual(config["max_context_chars"], 100)
        self.assertEqual(config["summary_prefix_chars"], 7)
        self.assertEqual(config["model"], "stub")
        self.assertEqual(config["base_url"], "http://127.0.0.1:8123/v1")
        self.assertEqual(profile["input_price_per_million"], None)
        self.assertEqual(profile["output_price_per_million"], None)

    def test_provider_and_profiles_are_not_both_legal(self):
        # A config that declares `profiles` while also carrying a stale
        # `provider` would otherwise load one and ignore the other. Which one
        # it loaded would depend on the version, so the file the operator
        # edited is not guaranteed to be the one that runs.
        v2_with_provider = self.write({
            "schema_version": "2",
            "default_profile": "local",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "ignored",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:8123/v1",
                    "model": "stub",
                    "api_key_env": "POCKETCHAT_API_KEY",
                },
            },
        })
        with self.assertRaisesRegex(ConfigError, "unknown field"):
            load_config(v2_with_provider)

        v1_with_profiles = self.write({
            "schema_version": "1",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
            "profiles": {"other": {"model": "ignored"}},
        })
        with self.assertRaisesRegex(ConfigError, "unknown field"):
            load_config(v1_with_profiles)

    # --- selecting one profile out of a loaded config -------------------

    def test_profile_config_selects_the_named_profile(self):
        config = load_config(self.write(_v2_config()))
        selected = profile_config(config, "hosted")
        self.assertEqual(selected["profile"], "hosted")
        self.assertEqual(selected["model"], "hosted-model")
        self.assertEqual(selected["base_url"], "https://example.invalid/v1")
        self.assertEqual(selected["api_key_env"], "POCKETCHAT_HOSTED_KEY")
        self.assertEqual(selected["timeout_seconds"], 90)
        self.assertEqual(selected["input_price_per_million"], 0.5)
        self.assertEqual(selected["output_price_per_million"], 1.5)
        # The system prompt is the config's, not the profile's: two profiles
        # of one conversation share what the operator wrote at the top.
        self.assertEqual(selected["system_prompt"], "Be brief.")

    def test_profile_config_defaults_to_the_default_profile(self):
        config = load_config(self.write(_v2_config()))
        self.assertEqual(profile_config(config), profile_config(config, "local"))
        self.assertEqual(profile_config(config)["model"], "stub")

    def test_profile_config_rejects_unknown_name(self):
        # The plan's case, in the shape the CLI will hand it: a loaded config
        # plus a name that is not in it.
        config = load_config(self.write(_v2_config()))
        with self.assertRaisesRegex(ConfigError, "unknown profile"):
            profile_config(config, "missing")

    def test_selecting_the_default_changes_nothing_else(self):
        # An explicit `--profile local` and no flag at all have to be the same
        # run. If selecting the default dropped or rewrote anything, the two
        # would be two subtly different runs and the flag would be a trap.
        # The comparison is over the keys a run reads, not over the whole
        # loaded config: that also carries `profiles`, `default_profile` and
        # the file's own `name`, which are bookkeeping rather than a run.
        run_keys = ("base_url", "model", "api_key_env", "timeout_seconds",
                    "max_context_chars", "summary_prefix_chars")
        config = load_config(self.write(_v2_config()))
        selected = profile_config(config)
        self.assertEqual({key: selected[key] for key in run_keys},
                         {key: config[key] for key in run_keys})
        self.assertEqual(selected["system_prompt"], config["system_prompt"])
        self.assertEqual(selected["profile"], "local")

    def test_profile_config_works_on_a_config_that_was_never_loaded(self):
        # `profile list` and the CLI hand around plain dicts, so the function
        # cannot depend on keys only `load_config` fills in.
        selected = profile_config(_v2_config(), "hosted")
        self.assertEqual(selected["profile"], "hosted")
        self.assertEqual(selected["model"], "hosted-model")
        self.assertEqual(selected["system_prompt"], "Be brief.")

    def test_the_selected_profile_carries_every_field_the_provider_reads(self):
        # `complete()` reads model, base_url, api_key_env and timeout_seconds
        # straight off the config it is handed. A profile record that lost one
        # of them would send the request against the wrong endpoint, or with a
        # default the operator never chose.
        config = load_config(self.write(_v2_config()))
        for name in ("local", "hosted"):
            selected = profile_config(config, name)
            record = config["profiles"][name]
            for field in ("base_url", "model", "api_key_env", "timeout_seconds",
                          "max_context_chars", "summary_prefix_chars"):
                self.assertEqual(selected[field], record[field],
                                 "%s lost %s" % (name, field))
            self.assertNotIn("profiles", selected)
            self.assertNotIn("default_profile", selected)
            # Prices are carried, not dropped: nothing computes a cost yet,
            # but a selection that lost them would silently make a priced
            # profile look like an unpriced one.
            self.assertEqual(selected["input_price_per_million"],
                             record["input_price_per_million"])
            self.assertEqual(selected["output_price_per_million"],
                             record["output_price_per_million"])
            # And nothing from the config's own bookkeeping rides along.
            self.assertNotIn("name", selected)
            self.assertNotIn("schema_version", selected)
            self.assertEqual(set(selected), {
                "base_url", "model", "api_key_env", "timeout_seconds",
                "max_context_chars", "summary_prefix_chars",
                "input_price_per_million", "output_price_per_million",
                "profile", "system_prompt",
            })


class ProfileListCommandTests(unittest.TestCase):
    """`pocketchat profile list`: what a config offers, and nothing secret."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "pocketchat.json"
        self.config.write_text(json.dumps(_v2_config()), encoding="utf-8")

    def test_the_cli_runs_end_to_end_as_a_subprocess(self):
        # One real interpreter for the profile family: the subprocess path
        # stays in the suite; everything else here runs in process for speed.
        result = _cli_subprocess(["profile", "list", "--config",
                                  str(self.config)], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("local", result.stdout)

    def test_profile_list_never_prints_the_credential_value(self):
        secret = "sk-not-a-real-key-0123456789"
        result = _cli(["profile", "list", "--config", str(self.config)],
                      self.base, {"POCKETCHAT_API_KEY": secret})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The variable's name and whether it is set are the whole question.
        self.assertIn("POCKETCHAT_API_KEY", result.stdout)
        self.assertIn("set", result.stdout)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("POCKETCHAT_HOSTED_KEY", result.stdout)
        self.assertIn("not set", result.stdout)

    def test_profile_list_marks_the_default(self):
        result = _cli(["profile", "list", "--config", str(self.config)], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # One line per profile, in name order, and the default is marked on
        # its own line rather than implied by position.
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        by_name = {line.split()[0]: line for line in lines}
        self.assertEqual(sorted(by_name), ["hosted", "local"])
        self.assertIn("(default)", by_name["local"])
        self.assertNotIn("(default)", by_name["hosted"])
        self.assertIn("hosted-model", by_name["hosted"])
        self.assertIn("stub", by_name["local"])
        self.assertIn("POCKETCHAT_HOSTED_KEY", by_name["hosted"])
        self.assertIn("POCKETCHAT_API_KEY", by_name["local"])

    def test_profile_list_shows_each_endpoint(self):
        result = _cli(["profile", "list", "--config", str(self.config)], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The endpoint is part of choosing a profile: two profiles can share a
        # model name, and only the base URL says where each request goes.
        by_name = {line.split()[0]: line for line in result.stdout.splitlines()
                   if line.strip()}
        self.assertIn("http://127.0.0.1:8123/v1", by_name["local"])
        self.assertIn("https://example.invalid/v1", by_name["hosted"])

    def test_profile_list_json_reports_each_key_state(self):
        result = _cli(["profile", "list", "--json", "--config", str(self.config)],
                      self.base, {"POCKETCHAT_API_KEY": "x"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["default_profile"], "local")
        by_name = {entry["name"]: entry for entry in payload["profiles"]}
        self.assertEqual(sorted(by_name), ["hosted", "local"])
        self.assertTrue(by_name["local"]["default"])
        self.assertFalse(by_name["hosted"]["default"])
        self.assertTrue(by_name["local"]["key_set"])
        self.assertFalse(by_name["hosted"]["key_set"])
        self.assertEqual(by_name["hosted"]["api_key_env"], "POCKETCHAT_HOSTED_KEY")

    def test_profile_list_rejects_an_unusable_config(self):
        self.config.write_text("{ not json", encoding="utf-8")
        result = _cli(["profile", "list", "--config", str(self.config)], self.base)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("pocketchat:", result.stderr)

    def test_profile_list_help_runs(self):
        result = _cli(["profile", "list", "--help"], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("--json", result.stdout)

    def test_profile_list_reads_the_default_config(self):
        result = _cli(["profile", "list"], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("local", result.stdout)

    def test_run_rejects_an_unknown_profile(self):
        env = {"POCKETCHAT_API_KEY": "test-key-not-real"}
        result = _cli(["run", "hi", "--profile", "missing",
                       "--config", str(self.config)], self.base, env)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("missing", result.stderr)
        # The config is unusable for this run, so nothing is written: a run
        # that never started must not leave a trace behind it.
        self.assertFalse((self.base / "traces").exists())

    def test_v1_config_offers_the_one_profile_it_migrated_to(self):
        self.config.write_text(json.dumps({
            "schema_version": "1",
            "name": "legacy",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")
        result = _cli(["profile", "list", "--config", str(self.config)], self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("legacy", result.stdout)
        self.assertIn("default", result.stdout)

    def test_v1_config_rejects_a_profile_it_does_not_have(self):
        self.config.write_text(json.dumps({
            "schema_version": "1",
            "name": "legacy",
            "provider": {
                "base_url": "http://127.0.0.1:8123/v1",
                "model": "stub",
                "api_key_env": "POCKETCHAT_API_KEY",
            },
        }), encoding="utf-8")
        result = _cli(["run", "hi", "--profile", "hosted",
                       "--config", str(self.config)], self.base,
                      {"POCKETCHAT_API_KEY": "test-key-not-real"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("unknown profile", result.stderr)
        self.assertIn("legacy", result.stderr)


class ProfileRunTests(unittest.TestCase):
    """`--profile` against a real socket: the run goes where the flag says."""

    def setUp(self) -> None:
        self.requests = []
        state = self

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                state.requests.append({
                    "model": json.loads(raw.decode("utf-8"))["model"],
                    "authorization": self.headers.get("Authorization"),
                })
                self._send(200, json.dumps({
                    "choices": [{"message": {"content": "stub reply"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 5},
                }).encode("utf-8"))

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
        self.addCleanup(self._stop_server)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / "pocketchat.json"
        self.config.write_text(json.dumps({
            "schema_version": "2",
            "name": "workbench",
            "default_profile": "local",
            "profiles": {
                "local": {
                    "base_url": "http://127.0.0.1:%d/v1" % self.port,
                    "model": "local-model",
                    "api_key_env": "POCKETCHAT_LOCAL_KEY",
                    "timeout_seconds": 30,
                },
                "hosted": {
                    "base_url": "http://127.0.0.1:%d/v1" % self.port,
                    "model": "hosted-model",
                    "api_key_env": "POCKETCHAT_HOSTED_KEY",
                    "timeout_seconds": 90,
                },
            },
        }), encoding="utf-8")

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def _run(self, *extra, env=None):
        environ = {"POCKETCHAT_LOCAL_KEY": "local-fake",
                   "POCKETCHAT_HOSTED_KEY": "hosted-fake"}
        if env:
            environ.update(env)
        return hp.run_cli(
            ["run", "hi", "--quiet", "--config", str(self.config)] + list(extra),
            self.base, env=environ)

    def _trace(self):
        traces = list((self.base / "traces").glob("*.jsonl"))
        self.assertEqual(len(traces), 1, "expected exactly one trace file")
        return [json.loads(line) for line in
                traces[0].read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_the_profile_flag_chooses_the_model_and_the_credential(self):
        result = self._run("--profile", "hosted")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0]["model"], "hosted-model")
        self.assertEqual(self.requests[0]["authorization"], "Bearer hosted-fake")

    def test_omitting_the_flag_runs_the_default_profile(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.requests[0]["model"], "local-model")
        self.assertEqual(self.requests[0]["authorization"], "Bearer local-fake")

    def test_naming_the_default_profile_is_the_same_run(self):
        # The flag must not be a second way to run. Explicitly naming the
        # default has to produce the same request as naming nothing.
        self._run()
        explicit = self._run("--profile", "local")
        self.assertEqual(explicit.returncode, 0, explicit.stdout + explicit.stderr)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0], self.requests[1])

    def test_the_trace_records_which_profile_ran(self):
        # Two profiles can name the same model. Without the profile name the
        # run's own evidence would not say which of them it used.
        result = self._run("--profile", "hosted")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        started = self._trace()[0]
        self.assertEqual(started["type"], "run.started")
        summary = started["payload"]["config_summary"]
        self.assertEqual(summary["profile"], "hosted")
        self.assertEqual(summary["model"], "hosted-model")
        self.assertEqual(summary["api_key_env"], "POCKETCHAT_HOSTED_KEY")
        self.assertNotIn("local-fake", json.dumps(summary))
        self.assertNotIn("hosted-fake", json.dumps(summary))

    def test_a_run_on_a_profile_without_a_credential_fails_before_it_starts(self):
        # The default has a key and the other profile does not: the run must
        # refuse rather than fall back to the profile that has one.
        result = self._run("--profile", "hosted", env={"POCKETCHAT_HOSTED_KEY": ""})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("POCKETCHAT_HOSTED_KEY", result.stderr)
        self.assertFalse(self.requests)
        self.assertFalse((self.base / "traces").exists())

    def test_chat_also_takes_the_profile_flag(self):
        # `chat` and `run` share `_prepare`, so both take the flag. This pins
        # that the flag reaches the interactive path too.
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "POCKETCHAT_LOCAL_KEY": "local-fake",
                   "POCKETCHAT_HOSTED_KEY": "hosted-fake"}
        proc = subprocess.Popen(
            [sys.executable, "-m", "pocketchat", "chat", "--quiet",
             "--config", str(self.config), "--profile", "hosted"],
            cwd=str(self.base), env=environ, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate("hello\n/exit\n", timeout=120)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertEqual(self.requests[0]["model"], "hosted-model")
        events = self._trace()
        self.assertEqual(events[0]["payload"]["config_summary"]["profile"], "hosted")

    def test_an_interrupted_chat_on_a_profile_ends_the_run(self):
        # The single-exit contract is a property of the run, not of the
        # profile: a run cancelled mid-turn still gets its terminal event.
        #
        # This needs a pty, for the reason `test_repl.py` records: writing to
        # a pipe closes stdin as soon as the input is consumed, so the child
        # reads EOF and finishes "completed" long before the signal lands.
        environ = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin",
                   "TERM": "dumb", "POCKETCHAT_HOSTED_KEY": "hosted-fake"}
        primary, secondary = pty.openpty()
        proc = subprocess.Popen(
            [sys.executable, "-m", "pocketchat", "chat", "--quiet",
             "--config", str(self.config), "--profile", "hosted"],
            cwd=str(self.base), env=environ, stdin=secondary, stdout=secondary,
            stderr=secondary, close_fds=True)
        os.close(secondary)
        os.set_blocking(primary, False)
        deadline = time.time() + 60

        def fire():
            time.sleep(1.5)
            proc.send_signal(signal.SIGINT)

        threading.Thread(target=fire, daemon=True).start()
        try:
            while True:
                if time.time() > deadline:
                    self.fail("chat did not exit within 60s")
                try:
                    data = os.read(primary, 4096)
                except (BlockingIOError, InterruptedError):
                    # Nothing to read yet: the child is parked in readline(),
                    # which is exactly the state this test wants to interrupt.
                    data = None
                except OSError as error:
                    # A Linux pty reports EOF with EIO instead of an empty
                    # read. Treat that as "no more output"; anything else is a
                    # real test failure.
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if not data and proc.poll() is not None:
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=60)
            try:
                os.close(primary)
            except OSError:
                pass
        events = self._trace()
        self.assertEqual(events[-1]["type"], "run.cancelled")
        self.assertEqual(events[-1]["payload"]["reason"], "interrupted by the user")
        self.assertEqual(events[0]["payload"]["config_summary"]["profile"], "hosted")


if __name__ == "__main__":
    unittest.main()
