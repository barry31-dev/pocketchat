"""Serial, bounded delegation to independent child runs.

Delegation here is deliberately not an agent-to-agent protocol: the caller
names a finite list of prompts, each child gets its own Session/trace, and the
parent records a manifest. Children cannot delegate recursively or share the
parent's session. The cap is enforced before the first request.
"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

from .session import Session, new_run_id
from .trace import utc_now

DELEGATE_DIR = Path(".pocketchat") / "delegations"
DELEGATE_SCHEMA = "pocketchat-delegation.v1"


class DelegateError(ValueError):
    """The delegation request cannot be safely executed."""


def _write_manifest(root: Path, record: Dict[str, Any]) -> Path:
    directory = Path(root) / DELEGATE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%s.json" % record["delegation_id"])
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        os.replace(str(temporary), str(path))
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise DelegateError("cannot write delegation manifest: %s" % exc)
    return path


def run_delegation(root: Path, prompts: List[str], config: Dict[str, Any],
                   *, max_children: int, timeout_seconds: int,
                   api_key: str = "") -> Dict[str, Any]:
    if not isinstance(prompts, list) or not prompts:
        raise DelegateError("delegation needs at least one prompt")
    if isinstance(max_children, bool) or not isinstance(max_children, int) or max_children < 1:
        raise DelegateError("max_children must be a positive integer")
    if len(prompts) > max_children:
        raise DelegateError("delegation has %d prompts; maximum is %d"
                            % (len(prompts), max_children))
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise DelegateError("timeout_seconds must be a positive integer")
    if any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts):
        raise DelegateError("every delegated prompt must be non-empty")
    root = Path(root).resolve()
    started_at = time.monotonic()
    children = []
    for index, prompt in enumerate(prompts, 1):
        if time.monotonic() - started_at > timeout_seconds:
            children.append({
                "index": index, "status": "timeout", "run_id": "",
                "trace": "", "reply": None,
                "error": "delegation deadline exceeded before this child",
            })
            continue
        run_id = new_run_id()
        trace_path = root / "traces" / ("%s.jsonl" % run_id)
        try:
            session = Session(
                config=copy.deepcopy(config), trace_path=trace_path,
                run_id=run_id, session_path=None, api_key=api_key)
            status = session.run([prompt])
            trace_rel = str(trace_path.relative_to(root)) if trace_path.exists() else ""
            children.append({
                "index": index, "status": status, "run_id": session.run_id,
                "trace": trace_rel,
                "reply": session.last_reply() if status == "completed" else None,
                "error": None if status == "completed" else "run_%s" % status,
            })
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            children.append({
                "index": index, "status": "failed", "run_id": run_id,
                "trace": str(trace_path.relative_to(root)) if trace_path.exists() else "",
                "reply": None, "error": "%s: %s" % (type(exc).__name__, exc),
            })
    ok = all(child["status"] == "completed" for child in children)
    delegation_id = "del_%s_%s" % (
        int(started_at * 1000), os.urandom(4).hex())
    record = {
        "schema_version": DELEGATE_SCHEMA,
        "delegation_id": delegation_id,
        "created": utc_now(),
        "prompt_count": len(prompts),
        "max_children": max_children,
        "timeout_seconds": timeout_seconds,
        "children": children,
    }
    manifest = _write_manifest(root, record)
    return {
        "ok": ok,
        "delegation_id": delegation_id,
        "manifest": str(manifest.relative_to(root)),
        "children": children,
    }
