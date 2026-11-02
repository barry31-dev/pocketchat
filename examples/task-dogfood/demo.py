#!/usr/bin/env python3
"""Offline task evidence: real tools, bounded repair, resume and acceptance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from pocketchat.acceptance import parse_plan as acceptance_plan, verify_task  # noqa: E402
from pocketchat.agent import run_task  # noqa: E402
from pocketchat.provider import build_payload  # noqa: E402
from pocketchat.task_command import run_command_template  # noqa: E402
from pocketchat.task_edit import apply_overwrite, snapshot_overwrite  # noqa: E402
from pocketchat.task_exec import authorize, execute_plan  # noqa: E402
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256  # noqa: E402
from pocketchat.task_repair import RepairDecision, run_bounded_repair  # noqa: E402
from pocketchat.task_spec import TaskSpecError, parse_spec  # noqa: E402

REPORT = "dogfood report\n"


class StubAdapter:
    name = "task-dogfood-stub"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls += 1
        return next(self.responses)


def final(content="done"):
    return {"content": content, "finish_reason": "stop", "model": "dogfood-model",
            "usage": {"input_tokens": 2, "output_tokens": 1, "cache_read_tokens": 0,
                      "cache_write_tokens": 0, "reasoning_tokens": 0},
            "usage_reported": True}


def tool(name, **arguments):
    response = final("")
    response.update(finish_reason="tool_calls", tool_calls=[{
        "id": "call", "name": name, "arguments": arguments}])
    return response


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return path


def command_template():
    return {
        "id": "check", "argv": [sys.executable, "-c",
            "from pathlib import Path; p=Path('command-count.txt'); "
            "p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); "
            "raise SystemExit(0 if Path('report.md').read_text() == 'dogfood report\\n' else 1)"],
        "cwd": ".", "timeout_seconds": 5, "max_output_bytes": 64,
    }


def spec(template, *, repair=False):
    return parse_spec({
        "schema_version": "pocketchat-task-spec.v1",
        "objective": "produce and verify a bounded dogfood report",
        "workspace_root": ".", "inputs": ["input.txt", "report.md", "done.md"],
        "budgets": {"max_steps": 8, "max_tool_calls": 4,
                    "max_context_chars": 8000, "max_turn_tokens": 2000,
                    "max_session_tokens": 6000},
        "tools": {"read_file": {"allow_paths": ["input.txt", "report.md"]},
                  "write_file": {"allow_paths": ["report.md", "done.md"]}},
        "commands": [template], "overwrite_paths": ["report.md"] if repair else [],
        "acceptance": {"path": "acceptance.json"},
    })


def plan_for(spec_value, steps):
    rows = []
    for step_id, action, inputs in steps:
        rows.append({"id": step_id, "objective": step_id.replace("_", " "),
                     "action": action, "inputs": inputs, "expected_evidence": step_id,
                     "depends_on": [rows[-1]["id"]] if rows else [], "retry_limit": 0})
    return parse_plan({"schema_version": "pocketchat-task-plan.v1",
                       "spec_sha256": spec_sha256(spec_value), "revision": 0,
                       "steps": rows}, spec=spec_value)


def config(adapter):
    return {"profile": "stub", "model": "dogfood-model",
            "base_url": "http://stub.invalid/v1", "api_key_env": "",
            "requires_api_key": False, "timeout_seconds": 5,
            "summary_prefix_chars": 80, "provider_adapter": adapter}


def new_report(workspace, callback):
    directory = workspace / ".pocketchat/task-reports"
    before = set(directory.glob("*.json"))
    results = callback()
    added = set(directory.glob("*.json")) - before
    if len(added) != 1:
        raise RuntimeError("expected exactly one new execution report")
    return results, added.pop()


def observe(workspace, paths):
    return {path.relative_to(workspace).as_posix():
            (sha(path), path.stat().st_mtime_ns) for path in paths}


def git_baseline(workspace):
    # This is an isolated fixture repository, never the caller's checkout.
    # Clear inherited Git overrides and disable hooks/signing for these local
    # fixture commits. No remote is configured and nothing is pushed.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="PocketChat Fixture", GIT_COMMITTER_NAME="PocketChat Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    for args in (("init",), ("add", "report.md"), ("commit", "-m", "fixture baseline")):
        proc = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                               "-c", "commit.gpgSign=false", "-C", str(workspace), *args],
                              env=env, capture_output=True, text=True, timeout=10)
        if proc.returncode:
            raise RuntimeError("fixture git %s failed: %s" % (args[0], proc.stderr.strip()))


def run_case(root: Path, case: str):
    workspace = root / case
    workspace.mkdir()
    row = {"case": case, "provider": "stub", "real_provider": False,
           "task_id": None, "spec_sha256": None, "plan_sha256": None}
    if case == "refusal":
        try:
            parse_spec({})
        except TaskSpecError as exc:
            report = save_json(workspace / ".pocketchat/dogfood/refusal.json", {
                "schema_version": "pocketchat-dogfood-refusal.v1", "outcome": "refused",
                "error_type": type(exc).__name__, "message": str(exc),
                "provider_calls": 0, "command_runs": 0, "real_provider": False})
        else:
            raise AssertionError("invalid spec was accepted")
        row.update(outcome="refused", evidence={"report": report.relative_to(workspace).as_posix(),
                   "report_sha256": sha(report), "trace": None})
        return row

    (workspace / "input.txt").write_text("bounded input\n", encoding="utf-8")
    template = command_template()
    spec_value = spec(template, repair=case == "repair")
    commands = []

    def runner(template_id, trace, run_id):
        if template_id != template["id"]:
            raise AssertionError("undeclared command")
        relative_trace = trace.relative_to(workspace)
        previous_cwd = Path.cwd()
        try:
            # The command API writes relative trace paths from the process cwd.
            # This single-writer demo enters its isolated workspace so both the
            # trace file and the persisted archive remain relocatable.
            os.chdir(workspace)
            result = run_command_template(
                workspace, template, trace_path=relative_trace, run_id=run_id)
        finally:
            os.chdir(previous_cwd)
        commands.append({"template_id": template_id, "template_sha256": json_sha(template),
                         "argv": list(template["argv"]), "exit_code": result.exit_code,
                         "trace": relative_trace.as_posix(),
                         "archive": (relative_trace.parent / "command-evidence"
                                     / (relative_trace.stem + ".json")).as_posix()})
        return result

    if case == "repair":
        (workspace / "report.md").write_text("incorrect report\n", encoding="utf-8")
        git_baseline(workspace)
        snapshot = snapshot_overwrite(workspace, "report.md", spec_value)
        edit_path = workspace / ".pocketchat/dogfood/edit.json"

        def repair(evidence):
            if evidence.get("exit_code") != 1:
                return RepairDecision(False, "unexpected command failure")
            edit = apply_overwrite(workspace, "report.md", REPORT, spec_value, snapshot)
            save_json(edit_path, asdict(edit))
            return RepairDecision(True, "operator callback applied an authorized hash-bound edit")

        result, details = run_bounded_repair(runner, "check", workspace / "traces/check.jsonl",
                                              "check", repair=repair, root=workspace)
        if not result.ok or details["outcome"] != "repaired":
            raise AssertionError("bounded repair did not succeed")
        # The repair API owns a separate command-repair receipt. Do not forge a
        # TaskPlan command report; the independent task acceptance here is file-only.
        steps = [("read_report", "read_file", ["report.md"])]
        responses = [tool("read_file", path="report.md"), final()]
        row.update(repair_mode="operator_callback", acceptance_scope="file_only")
    else:
        steps = [("write_report", "write_file", ["report.md"]),
                 ("check", "run_command", ["check"])]
        responses = [tool("write_file", path="report.md", content=REPORT), final()]
        if case == "recovery":
            steps.append(("finish", "write_file", ["done.md"]))
            responses += [tool("write_file", path="done.md", content="finished\n"), final()]
        row["acceptance_scope"] = "file_and_command"

    plan = plan_for(spec_value, steps)
    execution = authorize(plan, spec_value, expected_plan_sha256=plan_sha256(plan))
    checks = [{"id": "report", "kind": "text_contains", "path": "report.md", "text": REPORT}]
    if case != "repair":
        checks.append({"id": "command", "kind": "command_succeeded", "step_id": "check"})
    if case == "recovery":
        checks.append({"id": "finish", "kind": "text_contains", "path": "done.md", "text": "finished\n"})
    acceptance = acceptance_plan({"schema_version": "pocketchat-acceptance-plan.v1", "checks": checks})
    save_json(workspace / "spec.json", spec_value.source)
    save_json(workspace / "plan.json", plan.source)
    save_json(workspace / "acceptance.json", acceptance.source)
    adapter = StubAdapter(responses)

    def execute(**options):
        return execute_plan(execution, run_task=run_task, workspace=workspace,
                            config=config(adapter), trace_dir=workspace / "traces",
                            session_dir=workspace / "sessions", command_runner=runner, **options)

    results, report = new_report(workspace, lambda: execute(**({"stop_after": 2} if case == "recovery" else {})))
    evidence = {"spec": "spec.json", "plan": "plan.json", "acceptance_plan": "acceptance.json"}
    if case == "recovery":
        checkpoint = report
        preserved = [checkpoint, workspace / "report.md", workspace / "command-count.txt"]
        preserved += list((workspace / "traces").rglob("*.json*"))
        preserved += list((workspace / "sessions").glob("*.jsonl"))
        preserved += list((workspace / ".pocketchat/tasks").glob("*.json"))
        before = observe(workspace, preserved)
        results, report = new_report(workspace, lambda: execute(
            resume_report=checkpoint, expected_report_sha256=sha(checkpoint)))
        after = observe(workspace, preserved)
        if before != after:
            raise AssertionError("recovery changed confirmed artifacts")
        row["preserved_artifacts"] = [
            {"path": path, "before_sha256": prior[0], "after_sha256": after[path][0],
             "before_mtime_ns": prior[1], "after_mtime_ns": after[path][1]}
            for path, prior in sorted(before.items())]
        evidence["checkpoint"] = checkpoint.relative_to(workspace).as_posix()
    if not all(step["status"] == "completed" for step in results):
        raise AssertionError("plan execution did not complete")
    task_id = next(step["task_id"] for step in results if "task_id" in step)
    receipt = verify_task(workspace, task_id, acceptance)
    if receipt["state"] != "passed":
        raise AssertionError("independent acceptance failed: %s" % receipt["state"])
    evidence.update(report=report.relative_to(workspace).as_posix(), report_sha256=sha(report),
                    trace=results[0]["trace"], acceptance=receipt["record"])
    evidence["trace"] = Path(evidence["trace"]).relative_to(workspace).as_posix() if Path(evidence["trace"]).is_absolute() else evidence["trace"]
    if case == "repair":
        evidence.update(repair=".pocketchat/task-repair/check.json", edit=".pocketchat/dogfood/edit.json")
    row.update(outcome={"success": "completed", "repair": "repaired", "recovery": "recovered"}[case],
               task_id=task_id, spec_sha256=spec_sha256(spec_value), plan_sha256=plan_sha256(plan),
               evidence=evidence, commands=commands, provider_calls=adapter.calls)
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    temp = None
    if args.output is None:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name).resolve()
    else:
        root = args.output.resolve()
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            parser.error("--output must be a new or empty directory")
        root.mkdir(parents=True, exist_ok=True)
    try:
        for case in ("success", "repair", "recovery", "refusal"):
            print(json.dumps(run_case(root, case), ensure_ascii=False, sort_keys=True))
    finally:
        if temp is not None:
            temp.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
