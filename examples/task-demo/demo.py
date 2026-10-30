#!/usr/bin/env python3
"""Deterministic Task Run smoke demo with an in-process stub provider."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from pocketchat.agent import run_task  # noqa: E402
from pocketchat.provider import build_payload  # noqa: E402
from pocketchat.recovery import create_task_checkpoint, resume_task  # noqa: E402
from pocketchat.task_policy import load_policy  # noqa: E402
from pocketchat.tasking import (create_record, load_record, record_step,  # noqa: E402
                                save_record, transition)


class StubAdapter:
    name = "task-demo-stub"

    def __init__(self, responses):
        self.responses = list(responses)

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None):
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def tool_response(name, arguments, call_id="call_demo"):
    return {
        "content": "",
        "finish_reason": "tool_calls",
        "model": "demo-model",
        "usage": {"input_tokens": 2, "output_tokens": 1,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
        "tool_calls": [{"id": call_id, "name": name,
                        "arguments": arguments}],
    }


def final_response(content="demo complete"):
    return {
        "content": content,
        "finish_reason": "stop",
        "model": "demo-model",
        "usage": {"input_tokens": 3, "output_tokens": 2,
                  "cache_read_tokens": 0, "cache_write_tokens": 0,
                  "reasoning_tokens": 0},
        "usage_reported": True,
    }


def config(adapter):
    return {
        "profile": "demo",
        "model": "demo-model",
        "base_url": "http://stub.invalid/v1",
        "api_key_env": "DEMO_KEY",
        "api_key": "not-a-real-key",
        "requires_api_key": True,
        "timeout_seconds": 5,
        "max_context_chars": 8000,
        "summary_prefix_chars": 80,
        "provider_adapter": adapter,
    }


def completed_session(root: Path, run_id: str) -> Path:
    session = root / "sessions" / (run_id + ".jsonl")
    trace = root / "traces" / (run_id + ".jsonl")
    session.parent.mkdir(parents=True, exist_ok=True)
    trace.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("\n".join([
        json.dumps({"session": run_id, "schema_version": "1",
                    "created": "2026-10-30T00:00:00.000Z",
                    "pocketchat_version": "demo",
                    "trace": "../traces/%s.jsonl" % run_id,
                    "conversation_id": "conv_demo",
                    "branch_id": "branch_demo"}),
        json.dumps({"role": "user", "content": "read",
                    "turn_id": "turn_000001"}),
        json.dumps({"role": "assistant", "content": "done",
                    "turn_id": "turn_000002"}),
    ]) + "\n", encoding="utf-8")
    trace.write_text("\n".join([
        json.dumps({
            "schema_version": "1", "event_id": "evt_000001",
            "timestamp": "2026-10-30T00:00:00.000Z",
            "run_id": run_id, "type": "run.started",
            "payload": {"input_summary": "1 prompt"},
        }, sort_keys=True),
        json.dumps({
            "schema_version": "1", "event_id": "evt_000002",
            "timestamp": "2026-10-30T00:00:01.000Z",
            "run_id": run_id, "type": "run.finished",
            "payload": {"status": "completed", "turns": 1},
        }, sort_keys=True),
    ]) + "\n", encoding="utf-8")
    return session


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None,
                        help="write the demo workspace to this empty directory")
    args = parser.parse_args(argv)
    policy = load_policy(Path(__file__).with_name("task-policy.json"))
    temporary = None
    if args.output is None:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
    else:
        root = args.output.resolve()
        if root.exists() and any(root.iterdir()):
            parser.error("--output must be a new or empty directory")
        root.mkdir(parents=True, exist_ok=True)
    try:
        _run_paths(root, policy)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return 0


def _run_paths(root: Path, policy) -> None:
        (root / "inputs").mkdir()
        (root / "inputs" / "a.md").write_text("same fact\n", encoding="utf-8")
        (root / "inputs" / "b.md").write_text("same fact\n", encoding="utf-8")

        success = run_task(
            policy=policy, workspace=root,
            config=config(StubAdapter([
                tool_response("read_file", {"path": "inputs/a.md"}),
                final_response(),
            ])),
            trace_path=root / "traces" / "success.jsonl",
            session_path=root / "sessions" / "success.jsonl",
            prompts=["compare"])
        print(json.dumps({
            "path": "success", "status": success.status,
            "steps": success.report["steps"],
            "tools": success.report["tool_calls"],
        }, sort_keys=True))

        record = create_record(policy)
        transition(record, "start", "demo")
        record_step(record, "model", state="completed", run_id="run_resume")
        record_step(record, "tool", state="completed", tool="read_file",
                    detail="inputs/a.md", run_id="run_resume")
        save_record(root, record)
        session = completed_session(root, "run_resume")
        checkpoint = create_task_checkpoint(
            root, session, task_id=record.task_id, step_id=2,
            policy_sha256=record.policy_sha256,
            turn_id="turn_000002", checkpoint_id="checkpoint_demo")
        resumed = resume_task(root, checkpoint["checkpoint_id"], policy,
                              name="resumed")
        print(json.dumps({
            "path": "checkpoint_resume", "status": resumed["report"]["state"],
            "recovery_step": resumed["report"]["steps"],
            "context_messages": len(resumed["context"]),
        }, sort_keys=True))

        refused = run_task(
            policy=policy, workspace=root,
            config=config(StubAdapter([
                tool_response("read_file", {"path": "secret.txt"}),
                final_response("permission refusal recorded"),
            ])),
            trace_path=root / "traces" / "refusal.jsonl",
            session_path=root / "sessions" / "refusal.jsonl",
            prompts=["read outside policy"])
        states = [step["state"] for step in refused.record.steps
                  if step["kind"] == "tool"]
        print(json.dumps({
            "path": "permission_refusal", "status": refused.status,
            "tool_states": states,
        }, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
