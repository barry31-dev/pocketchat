#!/usr/bin/env python3
"""Four offline Task Run acceptance paths; no network, account or credential."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from pocketchat.acceptance import load_plan, parse_plan  # noqa: E402
from pocketchat.agent import run_task  # noqa: E402
from pocketchat.provider import build_payload  # noqa: E402
from pocketchat.task_policy import parse_policy  # noqa: E402


class StubAdapter:
    name = "acceptance-demo-stub"

    def __init__(self, responses):
        self.responses = iter(responses)

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        return next(self.responses)


def response(*, tool=None, content="execution complete"):
    value = {"content": content, "finish_reason": "stop", "model": "demo-model",
             "usage": {"input_tokens": 3, "output_tokens": 2, "cache_read_tokens": 0,
                       "cache_write_tokens": 0, "reasoning_tokens": 0}, "usage_reported": True}
    if tool is not None:
        path, body = tool
        value.update(content="", finish_reason="tool_calls", tool_calls=[{
            "id": "write_" + path.replace(".", "_"), "name": "write_file",
            "arguments": {"path": path, "content": body}}])
    return value


def demonstrate(root: Path):
    plan = load_plan(Path(__file__).with_name("acceptance.json"))
    policy = parse_policy({"schema_version": "pocketchat-task-policy.v1", "objective": "write an audit report",
                           "workspace_root": ".", "max_steps": 12, "max_tool_calls": 4,
                           "max_context_chars": 8000, "max_turn_tokens": 8000,
                           "tools": {"write_file": {"allow_paths": ["report.md", "summary.json"], "create_only": True}}})
    for case in ("success", "missing_artifact", "json_mismatch", "permission_refusal"):
        workspace = root / case
        workspace.mkdir()
        selected = plan
        if case == "permission_refusal":
            selected = parse_plan({"schema_version": "pocketchat-acceptance-plan.v1", "checks": [
                {"id": "secret", "kind": "file_exists", "path": ".env"}]})
        responses = []
        if case in ("success", "json_mismatch"):
            responses.append(response(tool=("report.md", "## Findings\nThe observed facts agree.\n")))
            state = "ready" if case == "success" else "incomplete"
            responses.append(response(tool=("summary.json", json.dumps({"status": state}))))
        responses.append(response())
        result = run_task(
            policy=policy, workspace=workspace,
            config={"model": "demo-model", "base_url": "http://stub.invalid/v1", "api_key_env": "",
                    "requires_api_key": False, "summary_prefix_chars": 80, "timeout_seconds": 5,
                    "provider_adapter": StubAdapter(responses)},
            prompts=["Write report.md and summary.json under the permitted policy."],
            trace_path=workspace / "traces" / "task.jsonl",
            session_path=workspace / "sessions" / "task.jsonl", acceptance=selected)
        print(json.dumps({"case": case, "execution": result.status, "task_id": result.task_id,
                          "acceptance": result.acceptance}, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new or empty evidence directory")
    args = parser.parse_args(argv)
    if args.output is None:
        with tempfile.TemporaryDirectory() as directory:
            demonstrate(Path(directory))
    else:
        root = args.output.resolve()
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            parser.error("--output must be a new or empty directory")
        root.mkdir(parents=True, exist_ok=True)
        demonstrate(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
