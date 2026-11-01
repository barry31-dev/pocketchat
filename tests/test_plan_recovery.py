"""Real step-boundary recovery, not just a historical inventory."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from pocketchat.agent import run_task
from pocketchat.task_command import run_command_template
from pocketchat.task_exec import authorize, execute_plan, load_task_execution
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256
from pocketchat.task_spec import parse_spec
from tests.test_task_run import FakeTaskAdapter, final_response, model_tool
from tests.test_task_spec import minimal


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PlanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.template = {
            "id": "count", "argv": [sys.executable, "-c",
                "from pathlib import Path; p=Path('count.txt'); "
                "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"],
            "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
        }
        self.spec = parse_spec(minimal(
            inputs=["report.md", "last.md"],
            tools={"write_file": {"allow_paths": ["report.md", "last.md"]}},
            commands=[self.template]))
        steps = []
        for step_id, action, inputs in (
                ("write_report", "write_file", ["report.md"]),
                ("count_once", "run_command", ["count"]),
                ("finish", "write_file", ["last.md"])):
            steps.append({"id": step_id, "action": action, "inputs": inputs,
                          "objective": step_id, "expected_evidence": step_id,
                          "depends_on": [steps[-1]["id"]] if steps else [],
                          "retry_limit": 0})
        self.plan = parse_plan({"schema_version": "pocketchat-task-plan.v1",
                               "spec_sha256": spec_sha256(self.spec),
                               "revision": 0, "steps": steps}, spec=self.spec)
        self.execution = authorize(self.plan, self.spec,
                                   expected_plan_sha256=plan_sha256(self.plan))
        self.adapter = FakeTaskAdapter([
            model_tool("write", "write_file", {"path": "report.md", "content": "first\n"}),
            final_response(),
            model_tool("last", "write_file", {"path": "last.md", "content": "last\n"}),
            final_response(),
        ])
        self.config = {
            "profile": "stub", "model": "task-model",
            "base_url": "http://stub.invalid/v1", "api_key_env": "",
            "requires_api_key": False, "timeout_seconds": 5,
            "summary_prefix_chars": 80, "provider_adapter": self.adapter,
        }
        self.runner = Mock(side_effect=lambda template_id, trace, run_id:
                           run_command_template(self.root, self.template,
                                                trace_path=trace, run_id=run_id))

    def execute(self, **kwargs):
        defaults = dict(run_task=run_task, workspace=self.root, config=self.config,
                        trace_dir=self.root / "traces", session_dir=self.root / "sessions",
                        command_runner=self.runner)
        defaults.update(kwargs)
        return execute_plan(self.execution, **defaults)

    def boundary(self, count=2):
        results = self.execute(stop_after=count)
        self.assertEqual(["completed"] * count + ["not_executed"] * (3-count),
                         [r["status"] for r in results])
        report = next((self.root / ".pocketchat" / "task-reports").glob("*.json"))
        return report, digest(report), results

    def resume(self, report, sha, **kwargs):
        return self.execute(resume_report=report, expected_report_sha256=sha, **kwargs)

    def refuse(self, report, sha=None, **kwargs):
        from pocketchat.task_recovery import TaskRecoveryError
        calls, commands = len(self.adapter.calls), self.runner.call_count
        files = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}
        with self.assertRaises(TaskRecoveryError):
            self.resume(report, sha or digest(report), **kwargs)
        self.assertEqual(calls, len(self.adapter.calls))
        self.assertEqual(commands, self.runner.call_count)
        self.assertEqual(files, {p.relative_to(self.root).as_posix(): p.read_bytes()
                                for p in self.root.rglob("*")
                                if p.is_file() and not p.is_symlink()})

    def rewrite(self, report, edit):
        payload = json.loads(report.read_text())
        edit(payload)
        report.write_text(json.dumps(payload), encoding="utf-8")

    def test_resume_completes_remaining_steps_without_repeating_side_effects(self):
        report, sha, initial = self.boundary()
        kept = [report, self.root / "report.md", self.root / "count.txt",
                self.root / "traces/write_report.jsonl",
                self.root / "sessions/write_report.jsonl",
                self.root / "traces/count_once.jsonl",
                self.root / "traces/command-evidence/count_once.json"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in kept}
        results = self.resume(report, sha)
        self.assertEqual(["completed"] * 3, [r["status"] for r in results])
        self.assertEqual(initial[0]["task_id"], results[0]["task_id"])
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(4, len(self.adapter.calls))
        self.assertEqual("last\n", (self.root / "last.md").read_text())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in kept})
        later = next(p for p in report.parent.glob("*.json") if p != report)
        payload = json.loads(later.read_text())
        self.assertEqual({"report": report.relative_to(self.root).as_posix(), "sha256": sha},
                         payload["recovery"])
        self.assertEqual(2, len(payload["changes"]))
        summary = load_task_execution(self.root, results[0]["task_id"])
        self.assertEqual([], summary["evidence_issues"])
        self.assertEqual(1, len(summary["commands"]))
        self.assertEqual(3, len(summary["execution_steps"]))
        self.resume(later, digest(later))
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(4, len(self.adapter.calls))

    def test_multiple_boundaries_preserve_original_evidence(self):
        report, sha, _ = self.boundary(1)
        self.assertEqual(0, self.runner.call_count)
        results = self.resume(report, sha, stop_after=1)
        self.assertEqual(["completed", "completed", "not_executed"],
                         [item["status"] for item in results])
        later = next(p for p in report.parent.glob("*.json") if p != report)
        self.resume(later, digest(later))
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(4, len(self.adapter.calls))

    def test_report_hash_must_match_exact_bytes(self):
        report, sha, _ = self.boundary()
        report.write_text(report.read_text() + "\n")
        self.refuse(report, sha)

    def test_spec_and_plan_identity_are_checked(self):
        report, sha, _ = self.boundary()
        original = self.execution
        for field in ("spec_sha256", "plan_sha256"):
            with self.subTest(field=field):
                self.execution = replace(original, **{field: "0" * 64})
                self.refuse(report, sha)
        self.execution = original

    def test_changed_missing_or_symlink_file_is_refused(self):
        report, sha, _ = self.boundary()
        target = self.root / "report.md"
        target.write_text("external edit\n")
        self.refuse(report, sha)
        target.unlink()
        self.refuse(report, sha)
        other = self.root / "elsewhere.md"
        other.write_text("first\n")
        target.symlink_to(other)
        self.refuse(report, sha)

    def test_changed_task_record_or_trace_or_session_or_archive_is_refused(self):
        report, sha, results = self.boundary()
        paths = [self.root / ".pocketchat/tasks" / (results[0]["task_id"] + ".json"),
                 self.root / "traces/write_report.jsonl",
                 self.root / "sessions/write_report.jsonl",
                 self.root / "traces/count_once.jsonl",
                 self.root / "traces/command-evidence/count_once.json"]
        for path in paths:
            with self.subTest(path=path.name):
                before = path.read_bytes()
                path.write_bytes(before + b"\n")
                self.refuse(report, sha)
                path.write_bytes(before)

    def test_report_or_archive_symlink_is_refused(self):
        report, sha, _ = self.boundary()
        for path in (self.root / "traces/command-evidence/count_once.json", report):
            original = path.read_bytes()
            moved = path.with_suffix(".backup")
            path.rename(moved)
            path.symlink_to(moved.name)
            self.refuse(report, sha)
            path.unlink()
            path.write_bytes(original)

    def test_invalid_report_status_order_identity_and_issues_are_refused(self):
        report, _, _ = self.boundary()
        original = report.read_bytes()
        edits = [
            lambda p: p["steps"][0].update(status="failed"),
            lambda p: p["steps"][1].update(status="not_executed", reason="previous_step_did_not_complete"),
            lambda p: p["steps"][0].update(action="read_file"),
            lambda p: p["steps"].reverse(),
            lambda p: p["steps"].append(p["steps"][0]),
            lambda p: p.update(task_ids=["task_foreign"]),
            lambda p: p.update(error={"type": "unknown", "message": "interrupted"}),
            lambda p: p["evidence_issues"].append({"state": "unknown", "reason": "gap"}),
            lambda p: p["steps"][0].pop("evidence_hashes", None),
            lambda p: p["steps"][1].update(archive_sha256="unknown"),
            lambda p: p["steps"][2].update(reason="previous_step_did_not_complete"),
        ]
        for index, edit in enumerate(edits):
            with self.subTest(case=index):
                report.write_bytes(original)
                self.rewrite(report, edit)
                self.refuse(report)

    def test_resume_never_reuses_pending_trace_or_session_or_command_archive(self):
        report, sha, _ = self.boundary(1)
        for relative in ("traces/count_once.jsonl", "sessions/finish.jsonl",
                         "traces/command-evidence/count_once.json"):
            with self.subTest(path=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("previous attempt\n")
                self.refuse(report, sha)
                path.unlink()

    def test_pending_output_parent_symlink_is_refused(self):
        report, sha, _ = self.boundary()
        parent = self.root / "linked"
        parent.symlink_to(self.root / "traces", target_is_directory=True)
        self.refuse(report, sha, trace_dir=parent)

    def test_missing_digest_and_invalid_boundary_are_refused_without_running(self):
        from pocketchat.task_exec import TaskExecutionError
        for value in (True, 0, -1, 4, "1"):
            with self.subTest(value=value), self.assertRaises(TaskExecutionError):
                self.execute(stop_after=value)
        self.assertEqual(0, len(self.adapter.calls))
        self.assertEqual(0, self.runner.call_count)
        report, _, _ = self.boundary()
        self.refuse(report, "not-a-digest")

    def test_pending_file_and_custom_archive_conflicts_refuse_before_calls(self):
        report, sha, _ = self.boundary(1)
        for relative, options in (
                ("last.md", {}),
                ("custom-traces/command-evidence/count_once.json",
                 {"trace_dir": self.root / "custom-traces"})):
            with self.subTest(path=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("old attempt")
                self.refuse(report, sha, **options)
                path.unlink()
        (self.root / "last.md").symlink_to(self.root / "missing.md")
        self.refuse(report, sha)

    def test_invalid_or_foreign_trace_and_session_cannot_be_authorized_by_hash(self):
        report, _, _ = self.boundary()
        original = report.read_bytes()
        for kind, relative in (("trace", "traces/write_report.jsonl"),
                               ("session", "sessions/write_report.jsonl")):
            path = self.root / relative
            before = path.read_bytes()
            for content in (b"not json\n", before.replace(b"run_", b"alien_")):
                with self.subTest(kind=kind, malformed=content[:8]):
                    path.write_bytes(content)
                    report.write_bytes(original)
                    self.rewrite(report, lambda p: p["steps"][0]["evidence_hashes"].update(
                        {kind: digest(path)}))
                    self.refuse(report)
            path.write_bytes(before)
            report.write_bytes(original)

    def test_repeated_noop_resume_does_not_create_reports_or_duplicate_commands(self):
        report, sha, results = self.boundary()
        self.resume(report, sha)
        complete = next(p for p in report.parent.glob("*.json") if p != report)
        files = {p: p.read_bytes() for p in report.parent.glob("*.json")}
        for _ in range(2):
            self.resume(complete, digest(complete))
        self.assertEqual(files, {p: p.read_bytes() for p in report.parent.glob("*.json")})
        summary = load_task_execution(self.root, results[0]["task_id"])
        self.assertEqual(1, len(summary["commands"]))
        self.assertEqual(3, len(summary["execution_steps"]))

    def test_command_acceptance_uses_the_recovered_result_not_the_old_boundary(self):
        from pocketchat.acceptance import evaluate_plan, parse_plan as acceptance_plan
        report, _, _ = self.boundary(1)
        payload = json.loads(report.read_text())
        report.unlink()
        report = report.with_name("execution_0000.json")
        payload["report_id"] = report.stem
        report.write_text(json.dumps(payload))
        self.resume(report, digest(report))
        result = evaluate_plan(self.root, acceptance_plan({
            "schema_version": "pocketchat-acceptance-plan.v1",
            "checks": [{"id": "count", "kind": "command_succeeded", "step_id": "count_once"}],
        }))
        self.assertEqual("passed", result["state"], result)
        self.assertEqual(1, self.runner.call_count)

    def test_custom_trace_directory_recovers_and_keeps_command_archive(self):
        custom = self.root / "custom-traces"
        self.execute(stop_after=2, trace_dir=custom)
        report = next((self.root / ".pocketchat/task-reports").glob("*.json"))
        archive = custom / "command-evidence/count_once.json"
        before = archive.read_bytes()
        self.resume(report, digest(report), trace_dir=custom)
        self.assertEqual(1, self.runner.call_count)
        self.assertEqual(before, archive.read_bytes())

    def test_pending_output_alias_cannot_append_to_completed_trace(self):
        report, sha, _ = self.boundary()
        self.refuse(report, sha, trace_dir=self.root / "sessions/../traces")

    def test_empty_oversized_or_nonregular_reports_refuse(self):
        import os
        report, _, _ = self.boundary()
        original = report.read_bytes()
        for data in (b"", b" " * (1024 * 1024 + 1)):
            report.write_bytes(data)
            self.refuse(report)
        report.write_bytes(original)
        sha = digest(report)
        report.unlink()
        os.mkfifo(report)
        self.refuse(report, sha)

    def test_model_completion_without_confirmed_tool_is_not_recoverable(self):
        self.adapter.responses = [final_response()]
        report, sha, _ = self.boundary(1)
        self.refuse(report, sha)


if __name__ == "__main__":
    unittest.main()
