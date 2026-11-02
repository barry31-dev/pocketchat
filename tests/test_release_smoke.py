"""Executable release-candidate smoke checks for the public package."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import release_smoke


class WorkflowLintTests(unittest.TestCase):
    def test_ci_runs_repository_lint_before_tests(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        lint = workflow.find("- name: Run repository lint")
        tests = workflow.find("- name: Run the suite")
        self.assertGreaterEqual(lint, 0)
        self.assertGreater(tests, lint)
        self.assertIn('python -m pip install "ruff==0.17.3"', workflow)
        self.assertIn("python -m ruff check --select E9,F pocketchat tests", workflow)


class WorkflowTests(unittest.TestCase):
    def test_workflow_runs_and_uploads_the_release_smoke(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" /
                    "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("release-smoke:", workflow)
        self.assertIn("scripts/release_smoke.py", workflow)
        self.assertIn("actions/upload-artifact@", workflow)
        self.assertIn("pocketchat-release-smoke", workflow)
        self.assertNotIn("rm -rf", workflow)


class ReleaseSmokeTests(unittest.TestCase):
    def test_success_reports_wheel_digest_and_installed_task_surface(self):
        calls = []
        artifact = type("Artifact", (), {
            "path": Path("/tmp/pocketchat-1.1.0-py3-none-any.whl"),
            "size": 1234,
            "sha256": "a" * 64,
            "members": release_smoke.required_members(),
            "version": "1.1.0",
        })()

        def build(*args, **kwargs):
            calls.append(("build", args, kwargs))
            self.assertTrue(kwargs["isolated"])
            return artifact

        def install(*args, **kwargs):
            calls.append(("install", args, kwargs))
            return {"version": "1.1.0", "commands": list(release_smoke.REQUIRED_TASK_COMMANDS)}

        result = release_smoke.run_release_smoke(
            Path("/repo"), Path("/tmp/out"), build=build, install=install,
            python="python3")
        self.assertTrue(result["ok"])
        self.assertEqual("1.1.0", result["version"])
        self.assertEqual("a" * 64, result["wheel_sha256"])
        self.assertEqual(1234, result["wheel_bytes"])
        self.assertEqual(sorted(release_smoke.REQUIRED_TASK_COMMANDS),
                         result["commands"])
        self.assertEqual(2, len(calls))

    def test_missing_schema_or_removed_surface_is_refused(self):
        bad_members = (
            "pocketchat/__init__.py",
            "pocketchat/task_exec.py",
            "pocketchat/web/server.py",
        )
        artifact = release_smoke.WheelArtifact(
            Path("/tmp/bad.whl"), 1, "b" * 64, bad_members, "1.1.0")
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "schema|web"):
            release_smoke.validate_artifact(artifact)

    def test_build_command_uses_isolation_by_default(self):
        with mock.patch.object(release_smoke, "_run") as run, \
                mock.patch.object(release_smoke, "read_wheel_members",
                                  return_value=release_smoke.required_members()), \
                mock.patch.object(release_smoke, "validate_artifact"):
            root = Path(tempfile.mkdtemp())
            (root / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            out = root / "dist"
            out.mkdir(parents=True)
            wheel = out / "pocketchat-1.1.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel")
            release_smoke.build_wheel(root, out)
        command = run.call_args.args[0]
        self.assertNotIn("--no-build-isolation", command)

    def test_installed_cli_must_expose_every_required_task_command(self):
        with self.assertRaisesRegex(release_smoke.ReleaseSmokeError,
                                    "installed CLI"):
            release_smoke.validate_installed(
                {"version": "1.1.0",
                 "commands": ["task exec", "task verify"]})

    def test_wheel_member_reader_rejects_duplicate_or_unsafe_members(self):
        class Archive:
            def __init__(self, names):
                self.names = names
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def namelist(self):
                return self.names
        with mock.patch.object(release_smoke.zipfile, "ZipFile",
                               return_value=Archive(["a.py", "a.py"])):
            with self.assertRaises(release_smoke.ReleaseSmokeError):
                release_smoke.read_wheel_members(Path("x.whl"))
        with mock.patch.object(release_smoke.zipfile, "ZipFile",
                               return_value=Archive(["../escape.py"])):
            with self.assertRaises(release_smoke.ReleaseSmokeError):
                release_smoke.read_wheel_members(Path("x.whl"))

    def test_json_output_is_stable_and_contains_no_absolute_path(self):
        artifact = release_smoke.WheelArtifact(
            Path("/tmp/pocketchat.whl"), 1, "c" * 64,
            release_smoke.required_members(), "1.1.0")
        result = release_smoke.ReleaseSmokeResult(artifact, {"version": "1.1.0",
                                                              "commands": list(release_smoke.REQUIRED_TASK_COMMANDS)})
        payload = result.as_dict()
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn("/tmp/", encoded)
        self.assertEqual("pocketchat.whl", payload["wheel"])


if __name__ == "__main__":
    unittest.main()
