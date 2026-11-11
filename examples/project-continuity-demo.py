#!/usr/bin/env python3
"""Offline project-memory continuity and revocation fixture; no network."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pocketchat import cli  # noqa: E402
from pocketchat.provider import build_payload  # noqa: E402
import pocketchat.session as session_module  # noqa: E402
from pocketchat.session import Session  # noqa: E402


STORE = Path(".pocketchat/project-memory.v2.jsonl")
WORKSPACE_ID = "project-orion"
CONTENT = "Project Orion uses Python 3.13 and keeps public command names English."
PROMPT = "What Python version and public naming rule does Project Orion use?"
EXPIRED_CONTENT = "The zephyr marker is cobalt during temporary validation."
EXPIRED_PROMPT = "What is the zephyr marker?"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OfflineAdapter:
    """Record the exact context and return one deterministic local response."""

    name = "project-continuity-offline-fixture"

    def __init__(self) -> None:
        self.calls = []

    def build_payload(self, messages, config):
        return build_payload(messages, config)

    def complete(self, messages, config, api_key, on_delta=None,
                 prepared_payload=None):
        self.calls.append(list(messages))
        return {
            "content": "offline fixture complete",
            "finish_reason": "stop",
            "model": "fixture-model",
            "usage_reported": True,
            "usage": {
                "input_tokens": 3,
                "output_tokens": 2,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
            },
        }


def invoke_memory(root: Path, arguments: list[str]):
    stdout, stderr = io.StringIO(), io.StringIO()
    previous = Path.cwd()
    try:
        os.chdir(root)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = cli.main(["memory", *arguments, "--json"])
            except SystemExit as exc:
                code = exc.code
    finally:
        os.chdir(previous)
    output = stdout.getvalue()
    return code, json.loads(output) if output else {}, stderr.getvalue()


def session_config(adapter: OfflineAdapter, root: Path, workspace_id: str):
    return {
        "profile": "project-continuity-fixture",
        "model": "fixture-model",
        "base_url": "http://example.invalid/v1",
        "api_key_env": "POCKETCHAT_UNUSED_FIXTURE_KEY",
        "timeout_seconds": 5,
        "max_context_chars": 8000,
        "summary_prefix_chars": 40,
        "requires_api_key": True,
        "provider_adapter": adapter,
        "system_prompt": "Use only the supplied offline evidence.",
        "context_projection_enabled": True,
        "context_projection_max_bytes": 65536,
        "memory_recall_enabled": True,
        "memory_recall_store": STORE.as_posix(),
        "memory_recall_workspace_id": workspace_id,
        "memory_recall_max_results": 20,
        "memory_recall_max_excerpt_bytes": 4096,
        "memory_recall_max_recall_tokens": 1024,
        "task_workspace_root": str(root),
    }


def recall_item(snapshot: dict) -> dict | None:
    messages = snapshot["request"].get("messages") or []
    for message in messages:
        if message.get("role") != "system":
            continue
        try:
            body = json.loads(message.get("content") or "")
        except (TypeError, ValueError):
            continue
        if body.get("schema_version") != "pocketchat-memory-recall.v1":
            continue
        items = body.get("items") or []
        return items[0] if items else None
    return None


def run_case(root: Path, case: str, *, workspace_id: str, prompt: str,
             now: dt.datetime | None = None) -> dict:
    adapter = OfflineAdapter()
    trace = root / "traces" / (case + ".jsonl")
    session_path = root / "sessions" / (case + ".jsonl")
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session = Session(
        session_config(adapter, root, workspace_id),
        trace,
        "run_" + case,
        session_path=session_path,
        api_key="offline-fixture-key",
    )

    manager = contextlib.nullcontext()
    if now is not None:
        class FixedDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                value = now
                return value if tz is None else value.astimezone(tz)
        manager = patch.object(session_module, "datetime", FixedDateTime)
    with manager:
        status = session.run([prompt])
    if not session.request_snapshots:
        raise ValueError("fixture session wrote no request snapshot")
    snapshot_path = session.request_snapshots[0]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    item = recall_item(snapshot)
    row = {
        "case": case,
        "exit_code": 0 if status == "completed" else 1,
        "status": status,
        "recalled": item is not None,
        "trace_path": trace.relative_to(root).as_posix(),
        "trace_sha256": digest(trace.read_bytes()),
        "request_snapshot_path": snapshot_path.relative_to(root).as_posix(),
        "request_snapshot_sha256": digest(snapshot_path.read_bytes()),
    }
    if item is not None:
        row.update({
            "memory_id": item["memory_id"],
            "revision": item["revision"],
            "source_ref": item["source_refs"][0]["ref"],
            "content_sha256": item["content_sha256"],
        })
    return row


def require_success(case: str, result) -> dict:
    code, payload, error = result
    if code != 0 or payload.get("ok") is not True:
        raise ValueError("%s failed: %s" % (case, error or payload))
    return payload


def demonstrate(root: Path):
    root = Path(root).resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError("fixture output must be new or empty")
    root.mkdir(parents=True, exist_ok=True)

    proposed = require_success("propose", invoke_memory(root, [
        "propose", CONTENT,
        "--workspace-id", WORKSPACE_ID,
        "--by", "fixture-operator",
        "--category", "project_decision",
        "--tag", "project-continuity",
        "--store", STORE.as_posix(),
    ]))["item"]
    memory = {
        "memory_id": proposed["memory_id"],
        "revision": proposed["revision"],
        "content_sha256": proposed["content_sha256"],
        "source_ref": proposed["source_refs"][0]["ref"],
    }

    cases = [run_case(
        root, "unaccepted", workspace_id=WORKSPACE_ID, prompt=PROMPT)]
    accepted = require_success("accept", invoke_memory(root, [
        "accept", proposed["memory_id"],
        "--by", "fixture-operator",
        "--verification", "not_required",
        "--store", STORE.as_posix(),
    ]))["item"]
    cases.append(run_case(
        root, "accepted", workspace_id=WORKSPACE_ID, prompt=PROMPT))
    cases.append(run_case(
        root, "foreign_workspace", workspace_id="project-foreign", prompt=PROMPT))

    expired = require_success("propose-expiring", invoke_memory(root, [
        "propose", EXPIRED_CONTENT,
        "--workspace-id", WORKSPACE_ID,
        "--by", "fixture-operator",
        "--category", "workflow_hint",
        "--expires-at", "2099-01-01T00:00:00.000Z",
        "--store", STORE.as_posix(),
    ]))["item"]
    require_success("accept-expiring", invoke_memory(root, [
        "accept", expired["memory_id"],
        "--by", "fixture-operator",
        "--verification", "not_required",
        "--store", STORE.as_posix(),
    ]))
    cases.append(run_case(
        root, "expired", workspace_id=WORKSPACE_ID, prompt=EXPIRED_PROMPT,
        now=dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)))

    retired = require_success("retire", invoke_memory(root, [
        "retire", proposed["memory_id"], "--store", STORE.as_posix(),
    ]))["item"]
    cases.append(run_case(
        root, "retired", workspace_id=WORKSPACE_ID, prompt=PROMPT))

    by_case = {row["case"]: row for row in cases}
    recalled = by_case["accepted"]
    checks = {
        "candidate_is_unverified_and_not_accepted": (
            proposed["verification_status"] == "unverified"
            and proposed["admission_status"] == "candidate"
            and proposed["accepted_by"] == ""),
        "unaccepted_candidate_not_recalled": not by_case["unaccepted"]["recalled"],
        "explicit_acceptance_recalled_in_new_session": (
            accepted["admission_status"] == "accepted"
            and accepted["verification_status"] == "not_required"
            and recalled["recalled"]),
        "recall_binds_source_and_revision": (
            recalled.get("memory_id") == memory["memory_id"]
            and recalled.get("revision") == memory["revision"]
            and recalled.get("source_ref") == memory["source_ref"]
            and recalled.get("content_sha256") == memory["content_sha256"]),
        "foreign_workspace_not_recalled": not by_case["foreign_workspace"]["recalled"],
        "expired_record_not_recalled": not by_case["expired"]["recalled"],
        "retired_record_not_recalled": (
            retired["admission_status"] == "retired"
            and not by_case["retired"]["recalled"]),
        "all_sessions_wrote_request_and_trace_evidence": all(
            row["exit_code"] == 0
            and (root / row["trace_path"]).is_file()
            and (root / row["request_snapshot_path"]).is_file()
            for row in cases),
    }
    outcome = {
        "schema_version": "pocketchat-project-continuity-fixture.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "memory": memory,
        "cases": cases,
        "unverified": [
            "real-provider relevance and answer quality",
            "arbitrary projects or cross-platform manual operation",
            "secure deletion of prior traces or already-sent context",
            "formal tag, Release or package publication",
        ],
    }
    (root / "evidence.json").write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        help="new or empty evidence directory")
    args = parser.parse_args(argv)
    if args.output is None:
        with tempfile.TemporaryDirectory() as directory:
            outcome = demonstrate(Path(directory) / "fixture")
    else:
        outcome = demonstrate(args.output)
    print(json.dumps({"passed": outcome["passed"],
                      "checks": outcome["checks"]}, sort_keys=True))
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
