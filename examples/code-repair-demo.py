#!/usr/bin/env python3
"""Offline task CLI code repair with a real, dependency-free Swift Package."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pocketchat import cli  # noqa: E402
from pocketchat.provider import build_payload  # noqa: E402
from pocketchat.task_plan import parse_plan, plan_sha256, spec_sha256  # noqa: E402
from pocketchat.task_spec import parse_spec  # noqa: E402

SOURCE = "Sources/BoundedSum/BoundedSum.swift"
BROKEN = """public enum BoundedSum {
    public static func total(through limit: Int) -> Int {
        guard limit > 0 else { return 0 }
        return (0..<limit).reduce(0, +)
    }
}
"""
MANIFEST = """// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "BoundedSum", targets: [
    .target(name: "BoundedSum"),
    .testTarget(name: "BoundedSumTests", dependencies: ["BoundedSum"])
])
"""
TESTS = """import XCTest
@testable import BoundedSum
final class BoundedSumTests: XCTestCase {
    func testInclusiveUpperBound() {
        XCTAssertEqual(BoundedSum.total(through: 3), 6)
        XCTAssertEqual(BoundedSum.total(through: 1), 1)
        XCTAssertEqual(BoundedSum.total(through: 0), 0)
    }
}
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class OfflineRepair:
    """Fix the fixture's one known range bug from the actual pinned source."""
    name = "offline-code-repair-fixture"

    def __init__(self):
        self.prompts = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, **_):
        request = json.loads(messages[0]["content"].split("\n", 1)[1])
        sources = request["editable_sources"]
        if len(sources) != 1 or sources[0]["path"] != SOURCE:
            raise ValueError("fixture requires its one explicitly authorized source")
        source = sources[0]
        if digest(source["content"].encode()) != source["sha256"]:
            raise ValueError("fixture source digest does not match its content")
        if source["content"].count("0..<limit") != 1:
            raise ValueError("fixture has no unique known range defect")
        self.prompts.append(request)
        return {"content": json.dumps({
            "action": "overwrite_file", "path": SOURCE,
            "content": source["content"].replace("0..<limit", "0...limit"),
            "reason": "The inclusive total must include the upper bound."}),
            "usage_reported": True, "usage": {"input_tokens": 4, "output_tokens": 3}}


def git_environment():
    # This repository is an isolated output fixture, never a source checkout.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="PocketChat Fixture", GIT_COMMITTER_NAME="PocketChat Fixture",
               GIT_AUTHOR_EMAIL="fixture@example.invalid", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    return env


def git(root, *args):
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull,
                             "-c", "commit.gpgSign=false", "-C", str(root), *args],
                            env=git_environment(), capture_output=True, text=True,
                            timeout=15, check=True)
    return result.stdout


def invoke(root, args, adapter):
    config = {"profiles": {"offline": {
        "base_url": "http://offline.invalid/v1", "model": "fixture-model",
        "api_key_env": "", "requires_api_key": False, "timeout_seconds": 5,
        "max_context_chars": 16000, "summary_prefix_chars": 80}},
        "default_profile": "offline"}
    select = cli.profile_config
    def selected(configuration, name=None):
        result = select(configuration, name)
        result["provider_adapter"] = adapter
        return result
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = Path.cwd()
    try:
        os.chdir(root)
        with patch.object(cli, "load_config", return_value=config), \
                patch.object(cli, "profile_config", side_effect=selected), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(args)
    finally:
        os.chdir(previous)
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


def observed_files(root):
    files = [root / SOURCE, root / "repair.diff"]
    for directory in (root / "traces", root / ".pocketchat"):
        files.extend(path for path in directory.rglob("*") if path.is_file())
    return {path.relative_to(root).as_posix():
            (digest(path.read_bytes()), path.stat().st_mtime_ns) for path in files}


def demonstrate(root, *, template=None):
    root = Path(root)
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        raise ValueError("fixture output must be new or empty")
    root = root.resolve()
    parent = root if root.exists() else root.parent
    while not parent.exists():
        parent = parent.parent
    probe = subprocess.run(["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
                           env=git_environment(), capture_output=True, timeout=10)
    if probe.returncode == 0:
        raise ValueError("fixture output must be outside an existing Git checkout")
    validation_scope = "operator-supplied fixture validator"
    if template is None:
        swift = shutil.which("swift")
        if swift is None:
            raise ValueError("Swift is unavailable; no dependency will be installed")
        template = {"id": "swift_tests", "argv": [swift, "test", "--jobs", "1"],
                    "cwd": ".", "timeout_seconds": 180, "max_output_bytes": 4096}
        validation_scope = "real local Swift Package tests; offline repair provider"
    root.mkdir(parents=True, exist_ok=True)
    for name, content in {SOURCE: BROKEN, "Package.swift": MANIFEST,
                          "Tests/BoundedSumTests/BoundedSumTests.swift": TESTS,
                          ".gitignore": ".build/\n"}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(root, "init")
    git(root, "add", SOURCE, "Package.swift", "Tests", ".gitignore")
    git(root, "commit", "-m", "fixture baseline")
    spec_data = {"schema_version": "pocketchat-task-spec.v1",
        "objective": "Fix the inclusive upper-bound sum and run the declared local tests",
        "workspace_root": ".", "inputs": [SOURCE], "overwrite_paths": [SOURCE],
        "budgets": {"max_steps": 2, "max_tool_calls": 2, "max_context_chars": 16000,
                    "max_turn_tokens": 4000, "max_session_tokens": 12000},
        "tools": {"read_file": {"allow_paths": [SOURCE]},
                  "write_file": {"allow_paths": [SOURCE]}},
        "commands": [template], "acceptance": {"path": "acceptance.json"}}
    spec = parse_spec(spec_data)
    plan_data = {"schema_version": "pocketchat-task-plan.v1", "revision": 0,
        "spec_sha256": spec_sha256(spec), "steps": [{"id": "code_check",
        "objective": "Confirm the inclusive total through the declared test template",
        "action": "run_command", "inputs": [template["id"]], "depends_on": [],
        "retry_limit": 0, "expected_evidence": "Test exit status and exact repair hashes"}]}
    plan = parse_plan(plan_data, spec=spec)
    save(root / "spec.json", spec_data)
    save(root / "plan.json", plan_data)
    save(root / "acceptance.json", {"schema_version": "pocketchat-acceptance-plan.v1",
        "checks": [{"id": "source", "kind": "text_contains", "path": SOURCE,
                    "text": "return (0...limit).reduce(0, +)"},
                   {"id": "tests", "kind": "command_succeeded", "step_id": "code_check"}]})
    adapter = OfflineRepair()
    args = ["task", "exec", "--spec", "spec.json", "--plan", "plan.json",
            "--plan-sha256", plan_sha256(plan), "--repair-model",
            "--check-acceptance", "acceptance.json", "--json"]
    code, result, error = invoke(root, args, adapter)
    save(root / "execution.json", {"exit_code": code, "result": result, "stderr": error})
    if code != 0 or result.get("status") != "completed":
        raise ValueError("fixture did not complete: " + json.dumps(result))
    receipt = json.loads((root / ".pocketchat/task-repair/code_check.json").read_text())
    actual = (root / SOURCE).read_bytes()
    diff = git(root, "diff", "--", SOURCE)
    (root / "repair.diff").write_text(diff, encoding="utf-8")
    report = root / result["reports"][-1]
    before = observed_files(root)
    code, resumed, error = invoke(root, args + ["--resume-report", str(report),
                                  "--report-sha256", digest(report.read_bytes())], adapter)
    save(root / "resume.json", {"exit_code": code, "result": resumed, "stderr": error})
    checks = {
        "source_bound_request": adapter.prompts[0]["editable_sources"][0]["sha256"] == digest(BROKEN.encode()),
        "failed_then_repaired": receipt["outcome"] == "repaired" and receipt["commands"][0]["exit_code"] != 0 and receipt["commands"][1]["exit_code"] == 0,
        "same_template_retry": receipt["commands"][0]["argv"] == receipt["commands"][1]["argv"] == template["argv"],
        "exact_edit_binding": receipt["action"]["original_sha256"] == digest(BROKEN.encode()) and receipt["action"]["new_sha256"] == digest(actual),
        "actual_diff": "-        return (0..<limit)" in diff and "+        return (0...limit)" in diff,
        "independent_acceptance": result.get("acceptance", {}).get("state") == "passed",
        "resume_no_replay": code == 0 and resumed.get("status") == "completed" and before == observed_files(root) and len(adapter.prompts) == 1,
    }
    evidence = {"schema_version": "pocketchat-code-repair-fixture.v1", "checks": checks,
        "validation_scope": validation_scope, "commands": receipt["commands"],
        "source": {"path": SOURCE, "sha256": digest(actual), "bytes": len(actual)},
        "report": {"path": report.relative_to(root).as_posix(), "sha256": digest(report.read_bytes())},
        "repair_provider_calls": len(adapter.prompts),
        "unverified": "Real-provider diagnosis quality, arbitrary projects, OS-level sandbox and other-platform manual Swift execution are not verified. No release or upload."}
    save(root / "evidence.json", evidence)
    if not all(checks.values()):
        raise ValueError("fixture checks failed: " + json.dumps(checks))
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(demonstrate(args.output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
