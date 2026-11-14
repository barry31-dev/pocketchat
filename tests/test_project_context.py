"""Project guidance continuation and explicit next-turn context, offline only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pocketchat.context import ContextBudgetExceeded
from pocketchat.provider import build_payload
from pocketchat.service import context_preview
from pocketchat.session import Session, SessionError
from pocketchat.sessions import SessionFileError
from tests import _helpers as hp


class RecordingAdapter:
    name = "project-context-test"

    def __init__(self):
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, **kwargs):
        self.calls.append(copy.deepcopy(messages))
        return {"content": "Saved reply", "finish_reason": "stop", "model": "m",
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "usage_reported": True}


class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = RecordingAdapter()
        self.config = dict(profile="project", model="m", base_url="http://127.0.0.1:1/v1",
                           api_key_env="PROJECT_CONTEXT_TEST_KEY", timeout_seconds=2,
                           max_context_chars=24000, summary_prefix_chars=40,
                           system_prompt="Fixed project instruction.", context_projection_enabled=True,
                           context_projection_max_bytes=65536, provider_adapter=self.adapter)
        self.path = self.root / "sessions" / "project.jsonl"
        self.trace = self.root / "traces" / "project.jsonl"

    def start(self, config=None):
        session = Session(config or self.config, self.trace, "run_project",
                          session_path=self.path, api_key="synthetic-key")
        self.assertEqual(session.run(["first", "second", "third", "fourth"]), "completed")
        return session

    def footprint(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def write_config(self, **changes):
        config = {k: v for k, v in self.config.items() if k not in ("provider_adapter", "profile")}
        config.update(changes)
        path = self.root / "pocketchat.json"
        system_prompt = config.pop("system_prompt", "")
        path.write_text(json.dumps({"schema_version": "2", "default_profile": "project",
                                    "system_prompt": system_prompt,
                                    "profiles": {"project": config}}))
        return path

    def test_fixed_instructions_survive_repeated_layered_compaction(self):
        session = self.start()
        self.assertGreater(session.compacted_count, 0)
        for index, request in enumerate(self.adapter.calls):
            with self.subTest(turn=index):
                self.assertEqual([m["content"] for m in request if m["role"] == "system"],
                                 [self.config["system_prompt"]])
        self.assertEqual(session.messages[0]["role"], "system")

    def test_current_config_preview_matches_reopened_runtime_without_writes(self):
        self.start()
        config = dict(self.config, system_prompt="New explicit project instruction.")
        before = self.footprint()
        with mock.patch("socket.socket", side_effect=AssertionError("preview opened socket")), \
                mock.patch("pocketchat.session.load_api_key", side_effect=AssertionError("read key")):
            preview = context_preview(self.path, workspace=self.root, pending_prompt="continue",
                                      config=config)
        self.assertEqual(self.footprint(), before)
        reopened = Session(config, self.trace, session_path=self.path, api_key="synthetic-key")
        self.addCleanup(reopened._release_session_writer)
        sent = reopened._context_with_budget("continue")
        self.assertEqual(preview["preview_messages"], sent)
        self.assertEqual(preview["preview_messages"][0]["content"], config["system_prompt"])
        self.assertEqual(preview["preview_messages"][-1], {"role": "user", "content": "continue"})
        self.assertEqual(preview["preview_mode"]["config_source"], "explicit")
        self.assertEqual(preview["preview_mode"]["keep_recent"], 1)

    def test_cli_exposes_pending_prompt_and_current_profile_without_key(self):
        self.start()
        path = self.write_config(system_prompt="Current CLI instruction.")
        before = self.footprint()
        result = hp.run_cli(("context", str(self.path), "--prompt", "continue",
                             "--config", str(path), "--profile", "project", "--json"), self.root,
                            env={"PROJECT_CONTEXT_TEST_KEY": None})
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["preview_messages"][0]["content"], "Current CLI instruction.")
        self.assertEqual(data["pending_messages"], [{"role": "user", "content": "continue"}])
        self.assertEqual(self.footprint(), before)

    def test_pending_preview_requires_explicit_config_and_nonempty_prompt(self):
        self.start()
        config = self.write_config()
        for args in (("--prompt", "continue"), ("--prompt", "", "--config", str(config)),
                     ("--config", str(config)), ("--profile", "project")):
            with self.subTest(args=args):
                result = hp.run_cli(("context", str(self.path), *args, "--json"), self.root)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(json.loads(result.stdout)["ok"])

    def test_current_preview_respects_projected_byte_budget(self):
        self.start()
        config = dict(self.config, context_projection_max_bytes=1)
        with self.assertRaises(ContextBudgetExceeded):
            context_preview(self.path, workspace=self.root, pending_prompt="continue", config=config)
        reopened = Session(config, self.trace, session_path=self.path, api_key="synthetic-project-key-123")
        self.addCleanup(reopened._release_session_writer)
        with self.assertRaisesRegex(SessionError, "projection budget"):
            reopened._context_with_budget("continue")

    def test_nonlayered_pending_preview_matches_runtime_and_current_system(self):
        self.start()
        config = dict(self.config, context_projection_enabled=False, max_context_chars=75)
        preview = context_preview(self.path, workspace=self.root, pending_prompt="continue", config=config)
        reopened = Session(config, self.trace, session_path=self.path, api_key="synthetic-project-key-123")
        self.addCleanup(reopened._release_session_writer)
        self.assertEqual(preview["preview_messages"], reopened._context_with_budget("continue"))
        self.assertEqual(preview["preview_mode"]["keep_recent"], 2)
        self.assertIsNone(preview.get("context_projection"))

    def test_historical_preview_still_works_without_config_or_key(self):
        self.start()
        before = self.footprint()
        result = hp.run_cli(("context", str(self.path), "--json"), self.root,
                            env={"PROJECT_CONTEXT_TEST_KEY": None})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["pending_messages"], [])
        self.assertEqual(self.footprint(), before)

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure memory descriptor reads")
    def test_recall_is_ephemeral_and_current_settings_override_old_trace(self):
        from pocketchat.memory_migration import propose_v2_record, accept_v2_record, retire_v2_record
        store = Path(".pocketchat/memory.v2.jsonl")
        candidate = propose_v2_record(self.root, store, "patches must keep public interfaces stable",
                                     workspace_id="project-a", proposed_by="operator")
        memory = accept_v2_record(self.root, store, candidate["memory_id"], "operator",
                                  verification_status="not_required")
        config = dict(self.config, memory_recall_enabled=True, memory_recall_store=str(store),
                      memory_recall_workspace_id="project-a")
        session = Session(config, self.trace, session_path=self.path, api_key="synthetic-project-key-123")
        self.assertEqual(session.run(["patches one", "patches two", "patches three", "patches four"]), "completed")
        self.assertNotIn("memory_recall", json.dumps(session.messages))
        self.assertEqual([m["content"] for m in session.messages if m["role"] == "system"],
                         [config["system_prompt"]])
        with mock.patch("pocketchat.service.read_v2_store", side_effect=AssertionError("old recall used")):
            preview = context_preview(self.path, workspace=self.root, pending_prompt="patches",
                                      config=dict(config, memory_recall_enabled=False))
        self.assertNotIn(memory["memory_id"], json.dumps(preview))
        other = context_preview(self.path, workspace=self.root, pending_prompt="patches",
                                config=dict(config, memory_recall_workspace_id="project-b"))
        self.assertEqual(other["memory_recall"]["selected_count"], 0)
        retire_v2_record(self.root, store, memory["memory_id"])
        preview = context_preview(self.path, workspace=self.root, pending_prompt="patches", config=config)
        self.assertEqual(preview["memory_recall"]["selected_count"], 0)
        self.assertNotIn(memory["memory_id"], json.dumps(preview["preview_messages"]))
        # Historical snapshots are deliberately not erased by retiring guidance.
        self.assertIn(memory["memory_id"], session.request_snapshots[0].read_text())

    @unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure memory descriptor reads")
    def test_corrupt_recall_store_refuses_preview_without_mutating_artifacts(self):
        self.start()
        store = Path("broken.jsonl")
        (self.root / store).write_text("not valid memory")
        config = dict(self.config, memory_recall_enabled=True, memory_recall_store=str(store),
                      memory_recall_workspace_id="project-a")
        before = self.footprint()
        with self.assertRaisesRegex(SessionFileError, "memory recall failed"):
            context_preview(self.path, workspace=self.root, pending_prompt="patches", config=config)
        self.assertEqual(self.footprint(), before)


@unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure memory descriptor reads")
class ProjectContextDemoTests(unittest.TestCase):
    def test_real_cli_guidance_accept_reopen_retire_and_evidence(self):
        import subprocess
        import sys
        script = Path(__file__).resolve().parents[1] / "examples" / "project-context-demo.py"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            result = subprocess.run([sys.executable, "-B", str(script), "--output", str(output)],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["passed"])
            self.assertEqual(report["requests"], 7)
            self.assertGreaterEqual(len(report["checks"]), 14)
            self.assertTrue(all(report["checks"].values()))
            steps = json.loads((output / "steps.json").read_text())
            self.assertTrue(all(step["exit_code"] == 0 for step in steps))
            self.assertEqual(report, json.loads((output / "evidence.json").read_text()))


@unittest.skipUnless(hp.SECURE_DESCRIPTOR_READS, "requires secure memory descriptor reads")
class ProjectRecallRootTests(unittest.TestCase):
    def test_next_preview_uses_session_trace_workspace_not_observer_cwd(self):
        from pocketchat.memory_migration import propose_v2_record, accept_v2_record
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            store = Path(".pocketchat/memory.v2.jsonl")
            item = propose_v2_record(root, store, "patches source-bound guidance", workspace_id="project-a",
                                     proposed_by="operator")
            accept_v2_record(root, store, item["memory_id"], "operator", verification_status="not_required")
            config = dict(profile="project", model="m", base_url="http://127.0.0.1:1/v1", api_key_env="K",
                          timeout_seconds=2, max_context_chars=24000, summary_prefix_chars=40,
                          context_projection_enabled=True, context_projection_max_bytes=65536,
                          memory_recall_enabled=True, memory_recall_store=str(store),
                          memory_recall_workspace_id="project-a", provider_adapter=RecordingAdapter())
            path = root / "sessions" / "project.jsonl"
            session = Session(config, root / "traces" / "project.jsonl", session_path=path,
                              api_key="synthetic-project-key-123")
            self.assertEqual(session.run(["patches"]), "completed")
            observer_root = root / "other-directory"
            observer_root.mkdir()
            preview = context_preview(path, workspace=observer_root, pending_prompt="patches", config=config)
            self.assertEqual(preview["memory_recall"]["selected_count"], 1)
            self.assertIn(item["memory_id"], json.dumps(preview["preview_messages"]))
