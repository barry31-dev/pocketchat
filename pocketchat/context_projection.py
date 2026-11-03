"""Deterministic layered context projection before request admission.

The projector is local policy, not a model summary. It keeps fixed instructions
and task state, then complete recent exchanges, then declared evidence. It
never emits an assistant tool call without its matching tool result.
"""

from __future__ import annotations

import json
from typing import Any


class ContextProjectionError(ValueError):
    def __init__(self, message: str,
                 error_type: str = "invalid_context_projection") -> None:
        super().__init__(message)
        self.error_type = error_type


def _chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")))


def _turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "user" and current:
            turns.append(current)
            current = [message]
        else:
            current.append(message)
    if current:
        turns.append(current)
    return turns


def _complete_turn(turn: list[dict[str, Any]]) -> bool:
    seen = set()
    pending = set()
    for message in turn:
        if message.get("role") == "assistant":
            if pending:
                return False
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                return False
            for call in calls:
                call_id = call.get("id") if isinstance(call, dict) else None
                if not isinstance(call_id, str) or not call_id or call_id in seen:
                    return False
                seen.add(call_id)
                pending.add(call_id)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                return False
            pending.remove(call_id)
    return not pending



def project_context(payload: Any, *, max_chars: int,
                    keep_recent_turns: int = 1) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContextProjectionError("context payload must be an object")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ContextProjectionError("max_chars must be a positive integer",
                                     error_type="invalid_context_projection")
    if isinstance(keep_recent_turns, bool) or not isinstance(keep_recent_turns, int) \
            or keep_recent_turns < 0:
        raise ContextProjectionError("keep_recent_turns must be non-negative",
                                     error_type="invalid_context_projection")
    instructions = payload.get("instructions") or []
    task_state = payload.get("task_state") or {}
    messages = payload.get("messages") or []
    evidence = payload.get("evidence") or []
    if not isinstance(instructions, list) or not isinstance(messages, list) \
            or not isinstance(evidence, list):
        raise ContextProjectionError("context layers have invalid shapes")
    fixed = list(instructions)
    if task_state:
        fixed.append({"role": "system", "content": json.dumps(
            task_state, ensure_ascii=False, sort_keys=True)})
    fixed_chars = _chars(fixed)
    if fixed_chars > max_chars:
        raise ContextProjectionError(
            "fixed context exceeds the configured limit",
            error_type="fixed_context_too_large")

    seen_sources = set()
    kept_evidence = []
    dropped = []
    used = fixed_chars
    for item in evidence:
        if not isinstance(item, dict) or not isinstance(item.get("source"), str):
            raise ContextProjectionError("evidence entries need a source")
        if item["source"] in seen_sources:
            dropped.append({"source": item["source"],
                            "reason": "duplicate_source"})
            continue
        seen_sources.add(item["source"])
        size = _chars(item)
        if used + size > max_chars:
            dropped.append({"source": item["source"], "reason": "over_budget"})
            continue
        kept_evidence.append(item)
        used += size

    turns = _turns(messages)
    selected_turns = turns[-keep_recent_turns:] if keep_recent_turns else turns
    if any(not _complete_turn(turn) for turn in selected_turns):
        raise ContextProjectionError("recent tool exchange is incomplete",
                                     error_type="incomplete_tool_exchange")
    kept_turns = []
    for turn in selected_turns:
        size = _chars(turn)
        if used + size > max_chars:
            continue
        kept_turns.append(turn)
        used += size
    projected_messages = fixed + [message for turn in kept_turns
                                  for message in turn]
    # Any tool result left without its assistant call is a projection bug, not
    # model input. Refuse instead of silently sending an orphan.
    projected_turns = _turns([message for message in projected_messages
                              if message.get("role") != "system"])
    if any(not _complete_turn(turn) for turn in projected_turns):
        raise ContextProjectionError("projected tool exchange is incomplete",
                                     error_type="incomplete_tool_exchange")
    return {
        "messages": projected_messages,
        "manifest": {
            "schema_version": "pocketchat-context-projection.v1",
            "max_chars": max_chars,
            "kept_chars": used,
            "kept_turns": len(kept_turns),
            "evidence": kept_evidence,
            "dropped": dropped,
        },
    }
