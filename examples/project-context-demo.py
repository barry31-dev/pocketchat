#!/usr/bin/env python3
"""Source-checkout CLI project guidance journey against a local HTTP fixture.

No remote model or real credential. A new evidence directory is required.
Retirement prevents future recall; it deliberately does not erase history.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parents[1]
KEY = "synthetic-project-context-fixture-key"
STORE = ".pocketchat/project-memory.jsonl"
SESSION = "sessions/project.jsonl"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def footprint(root):
    return {str(p.relative_to(root)): (digest(p.read_bytes()), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def demonstrate(root):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("the evidence directory must be new or empty")
    requests, steps, checks = [], [], {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            count = int(self.headers.get("Content-Length") or 0)
            requests.append(json.loads(self.rfile.read(count)))
            body = json.dumps({"model": "fixture-model", "choices": [{"message": {
                "role": "assistant", "content": "Fixture reply %d" % len(requests)},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 4, "completion_tokens": 2}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = dict(base_url="http://127.0.0.1:%d/v1" % server.server_port,
                   model="fixture-model", api_key_env="PROJECT_CONTEXT_FIXTURE_KEY",
                   timeout_seconds=3, max_context_chars=24000, summary_prefix_chars=40,
                   context_projection_enabled=True, context_projection_max_bytes=65536,
                   memory_recall_enabled=True, memory_recall_store=STORE,
                   memory_recall_workspace_id="project-a")
    config = dict(schema_version="2", default_profile="project", system_prompt="Keep public APIs stable.",
                  profiles={"project": profile})
    config_path = root / "pocketchat.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    def command(*args, preview=False):
        env = dict(os.environ, PYTHONPATH=str(SOURCE), PYTHONDONTWRITEBYTECODE="1",
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        if preview:
            env.pop("PROJECT_CONTEXT_FIXTURE_KEY", None)
        else:
            env["PROJECT_CONTEXT_FIXTURE_KEY"] = KEY
        before = footprint(root) if preview else None
        count = len(requests)
        result = subprocess.run([sys.executable, "-B", "-m", "pocketchat", *args], cwd=root,
                                env=env, capture_output=True, text=True, timeout=20)
        steps.append(dict(argv=list(args), exit_code=result.returncode,
                          stdout=result.stdout, stderr=result.stderr))
        if result.returncode:
            raise RuntimeError("CLI refused fixture step: %s\n%s\n%s" %
                               (args[0], result.stdout, result.stderr))
        data = json.loads(result.stdout)
        if preview and (before != footprint(root) or count != len(requests)):
            raise RuntimeError("read-only preview wrote files or called the provider")
        return data

    def propose(content, workspace="project-a", accept=False, **extra):
        args = ["memory", "propose", content, "--workspace-id", workspace,
                "--by", "fixture-operator", "--store", STORE, "--json"]
        if extra.get("sensitive"):
            args.append("--sensitive")
        if extra.get("expires_at"):
            args += ["--expires-at", extra["expires_at"]]
        result = command(*args)
        memory_id = result["item"]["memory_id"]
        if accept:
            command("memory", "accept", memory_id, "--by", "fixture-operator",
                    "--verification", "not_required", "--store", STORE, "--json")
        return memory_id

    def run(*prompts):
        return command("run", *prompts, "--config", "pocketchat.json", "--profile", "project",
                       "--session", SESSION, "--json")

    def preview(prompt):
        return command("context", SESSION, "--prompt", prompt, "--config", "pocketchat.json",
                       "--profile", "project", "--json", preview=True)

    try:
        admitted = propose("patches: use small isolated changes and record exact checks")
        run("patches first")
        checks["unaccepted_guidance_not_injected"] = admitted not in json.dumps(requests[-1])
        command("memory", "accept", admitted, "--by", "fixture-operator",
                "--verification", "not_required", "--store", STORE, "--json")
        other = propose("patches CROSS_PROJECT_MARKER", workspace="project-b", accept=True)
        sensitive = propose("patches SENSITIVE_MARKER", sensitive=True, accept=True)
        candidate = propose("patches CANDIDATE_MARKER")
        run("patches second")
        checks["accepted_guidance_has_bound_identity"] = admitted in json.dumps(requests[-1])
        checks["other_workspace_sensitive_candidate_excluded"] = all(
            text not in json.dumps(requests[-1]) for text in (other, sensitive, candidate,
                "CROSS_PROJECT_MARKER", "SENSITIVE_MARKER", "CANDIDATE_MARKER"))
        config["system_prompt"] = "Current project instruction: preserve public APIs."
        config_path.write_text(json.dumps(config), encoding="utf-8")
        expected = preview("patches third")
        run("patches third")
        checks["reopened_preview_equals_actual_messages"] = expected["preview_messages"] == requests[-1]["messages"]
        checks["current_config_not_old_trace"] = requests[-1]["messages"][0]["content"] == config["system_prompt"]
        checks["preview_binds_selected_guidance"] = expected["memory_recall"]["selected_count"] == 1
        run("patches fourth", "patches fifth", "patches sixth")
        checks["fixed_instructions_survive_compaction"] = all(
            body["messages"][0]["content"] == config["system_prompt"] for body in requests[-3:])
        snapshots = {str(p.relative_to(root)): digest(p.read_bytes()) for p in
                     (root / "traces" / "requests").glob("*.json")}
        command("memory", "retire", admitted, "--store", STORE, "--json")
        after = preview("patches after retirement")
        run("patches after retirement")
        checks["retired_guidance_not_recalled"] = (after["memory_recall"]["selected_count"] == 0 and
            admitted not in json.dumps(after["preview_messages"]) and
            after["preview_messages"] == requests[-1]["messages"])
        checks["historical_snapshots_unchanged"] = all(digest((root / p).read_bytes()) == sha
                                                      for p, sha in snapshots.items())
        checks["historical_evidence_retained"] = any(admitted in (root / p).read_text()
                                                    for p in snapshots)
        # Explicit future clock tests expiry separately; no clock changes or sleeps.
        expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
        expired = propose("patches EXPIRED_MARKER", workspace="expiry-project", accept=True,
                          expires_at=expiry.isoformat().replace("+00:00", "Z"))
        expired_result = command("memory", "recall", "patches", "--workspace-id", "expiry-project",
            "--store", STORE, "--now", (expiry + datetime.timedelta(days=1)).isoformat().replace("+00:00", "Z"),
            "--dry-run", "--json", preview=True)
        checks["expiry_fixed_clock_refused"] = (expired_result["items"] == [] and any(
            item["memory_id"] == expired and "expired" in item["reasons"]
            for item in expired_result["excluded"]))
        material = [json.loads(p.read_text()) for p in (root / "traces" / "requests").glob("*.json")]
        recorded = [v["request"] for v in material if "request" in v]
        checks["every_http_request_has_exact_snapshot"] = (len(recorded) == len(requests) and
            sorted(json.dumps(v, sort_keys=True) for v in recorded) ==
            sorted(json.dumps(v, sort_keys=True) for v in requests))
        checks["credential_not_in_workspace"] = all(KEY.encode() not in p.read_bytes()
            for p in root.rglob("*") if p.is_file())
        checks["preview_read_only_without_key"] = True  # Enforced for every preview call above.
        report = dict(schema_version="pocketchat-project-context-demo.v1", passed=all(checks.values()),
                      checks=checks, requests=len(requests), steps=len(steps), python=sys.version.split()[0],
                      limitations=["Loopback fixture, not real-model instruction compliance",
                                   "Retirement prevents new recall; does not erase old messages or traces",
                                   "Expiry used an explicit dry-run clock, not a changed system clock",
                                   "Source CLI demonstration, not installed-package release acceptance"])
        (root / "requests.json").write_text(json.dumps(requests, indent=2), encoding="utf-8")
        (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        (root / "steps.json").write_text(json.dumps(steps, indent=2), encoding="utf-8")
        server.shutdown()
        server.server_close()
        thread.join(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new or empty directory for fixed evidence")
    args = parser.parse_args()
    if args.output:
        result = demonstrate(args.output)
    else:
        with tempfile.TemporaryDirectory() as temporary:
            result = demonstrate(Path(temporary) / "fixture")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
