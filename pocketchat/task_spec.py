"""Strict TaskSpec v1 parsing and conversion to the existing task policy.

A TaskSpec is an operator-authored authorization document. It deliberately
does not add a second executor or permission interpreter: after its closed
shape is validated, ``to_policy()`` delegates the final authority check to
``task_policy.parse_policy``. This first slice is read-only and supports only
features the existing bounded task runner already understands.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .task_command import CommandError, parse_command_templates
from .task_policy import (
    MAX_CONTEXT_CHARS,
    MAX_MEMORY_RESULTS,
    MAX_STEPS,
    MAX_TOOL_CALLS,
    MAX_TOKENS,
    MIN_CONTEXT_CHARS,
    MIN_MEMORY_RESULTS,
    MIN_STEPS,
    MIN_TOOL_CALLS,
    MIN_TOKENS,
    TOOLS,
    TaskPolicy,
    TaskPolicyError,
    parse_policy,
)

SPEC_SCHEMA = "pocketchat-task-spec.v1"
_REQUIRED_ROOT_KEYS = ("schema_version", "objective", "workspace_root",
                       "inputs", "budgets", "tools", "commands", "acceptance")
_OPTIONAL_ROOT_KEYS = ("overwrite_paths",)
_ROOT_KEYS = _REQUIRED_ROOT_KEYS + _OPTIONAL_ROOT_KEYS
_BUDGET_KEYS = ("max_steps", "max_tool_calls", "max_context_chars",
                "max_turn_tokens", "max_session_tokens")
_TOOL_KEYS = {
    "read_file": ("allow_paths",),
    "write_file": ("allow_paths", "create_only"),
    "memory_search": ("max_results",),
}


class TaskSpecError(ValueError):
    """The TaskSpec is malformed or asks for an unsupported capability."""


@dataclass(frozen=True)
class TaskSpec:
    source: Dict[str, Any]
    acceptance_path: str

    @property
    def objective(self) -> str:
        return self.source["objective"]

    @property
    def overwrite_paths(self) -> list:
        return list(self.source.get("overwrite_paths", []))

    @property
    def commands(self) -> list:
        return list(self.source.get("commands", []))

    def to_policy(self) -> TaskPolicy:
        """Convert to a policy and let the existing parser validate it."""
        budgets = self.source["budgets"]
        policy: Dict[str, Any] = {
            "schema_version": "pocketchat-task-policy.v1",
            "objective": self.source["objective"],
            "workspace_root": self.source["workspace_root"],
            "max_steps": budgets["max_steps"],
            "max_tool_calls": budgets["max_tool_calls"],
            "max_context_chars": budgets["max_context_chars"],
            "max_turn_tokens": budgets["max_turn_tokens"],
            "tools": copy.deepcopy(self.source["tools"]),
        }
        if "max_session_tokens" in budgets:
            policy["max_session_tokens"] = budgets["max_session_tokens"]
        try:
            return parse_policy(policy)
        except TaskPolicyError as exc:
            raise TaskSpecError(str(exc))


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskSpecError("%s must be an object" % what)
    return value


def _closed(value: Dict[str, Any], allowed, what: str) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise TaskSpecError("%s has unknown field(s): %s"
                            % (what, ", ".join(unknown)))


def _nonempty_string(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskSpecError("%s must be a non-empty string" % what)
    return value.strip()


def _relative(value: Any, what: str, allow_root: bool = False) -> str:
    text = _nonempty_string(value, what)
    if allow_root and text == ".":
        return text
    if text.startswith("/") or "\\" in text:
        raise TaskSpecError("%s must be workspace-relative" % what)
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise TaskSpecError("%s must not contain empty, '.' or '..' segments"
                            % what)
    return text


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskSpecError("%s must be an integer" % what)
    if not low <= value <= high:
        raise TaskSpecError("%s must be between %d and %d"
                            % (what, low, high))
    return value


def _paths(value: Any, what: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TaskSpecError("%s must be a list" % what)
    result = []
    for entry in value:
        path = _relative(entry, "%s entry" % what)
        if path not in result:
            result.append(path)
    return result


def _budgets(value: Any) -> Dict[str, int]:
    payload = _object(value, "budgets")
    _closed(payload, _BUDGET_KEYS, "budgets")
    required = ("max_steps", "max_tool_calls", "max_context_chars",
                "max_turn_tokens")
    for key in required:
        if key not in payload:
            raise TaskSpecError("budgets.%s is required" % key)
    result = {
        "max_steps": _integer(payload["max_steps"], "budgets.max_steps",
                              MIN_STEPS, MAX_STEPS),
        "max_tool_calls": _integer(payload["max_tool_calls"],
                                   "budgets.max_tool_calls",
                                   MIN_TOOL_CALLS, MAX_TOOL_CALLS),
        "max_context_chars": _integer(payload["max_context_chars"],
                                      "budgets.max_context_chars",
                                      MIN_CONTEXT_CHARS, MAX_CONTEXT_CHARS),
        "max_turn_tokens": _integer(payload["max_turn_tokens"],
                                    "budgets.max_turn_tokens",
                                    MIN_TOKENS, MAX_TOKENS),
    }
    if "max_session_tokens" in payload:
        result["max_session_tokens"] = _integer(
            payload["max_session_tokens"], "budgets.max_session_tokens",
            MIN_TOKENS, MAX_TOKENS)
    return result


def _tools(value: Any) -> Dict[str, Dict[str, Any]]:
    payload = _object(value, "tools")
    _closed(payload, TOOLS, "tools")
    result = {}
    for name, raw in payload.items():
        grant = _object(raw, "tools.%s" % name)
        _closed(grant, _TOOL_KEYS[name], "tools.%s" % name)
        item: Dict[str, Any] = {"allow_paths": _paths(
            grant.get("allow_paths"), "tools.%s.allow_paths" % name)}
        if name == "write_file":
            if grant.get("create_only", True) is not True:
                raise TaskSpecError(
                    "tools.write_file supports only create_only=true; overwrite "
                    "is not part of TaskSpec v1")
            item["create_only"] = True
        if name == "memory_search":
            item["max_results"] = _integer(
                grant.get("max_results", 20), "tools.memory_search.max_results",
                MIN_MEMORY_RESULTS, MAX_MEMORY_RESULTS)
        result[name] = item
    return result


def _acceptance(value: Any) -> str:
    payload = _object(value, "acceptance")
    _closed(payload, ("path",), "acceptance")
    if "path" not in payload:
        raise TaskSpecError("acceptance.path is required")
    return _relative(payload["path"], "acceptance.path")


def parse_spec(payload: Any) -> TaskSpec:
    data = _object(payload, "the TaskSpec")
    _closed(data, _ROOT_KEYS, "the TaskSpec")
    for key in _REQUIRED_ROOT_KEYS:
        if key not in data:
            raise TaskSpecError("the TaskSpec is missing %s" % key)
    if data["schema_version"] != SPEC_SCHEMA:
        raise TaskSpecError("unsupported TaskSpec schema version %r"
                            % (data["schema_version"],))
    inputs = _paths(data["inputs"], "inputs")
    overwrite_paths = _paths(data.get("overwrite_paths"), "overwrite_paths")
    tools = _tools(data["tools"])
    read_paths = tools.get("read_file", {}).get("allow_paths", [])
    if any(path not in inputs for path in read_paths):
        raise TaskSpecError("tools.read_file paths must be declared in inputs")
    write_paths = tools.get("write_file", {}).get("allow_paths", [])
    if any(path not in inputs for path in overwrite_paths):
        raise TaskSpecError("overwrite_paths must be declared in inputs")
    if any(path not in write_paths for path in overwrite_paths):
        raise TaskSpecError(
            "overwrite_paths must be included in tools.write_file.allow_paths")
    try:
        commands = parse_command_templates(data["commands"])
    except CommandError as exc:
        raise TaskSpecError(str(exc))
    normalized = {
        "schema_version": SPEC_SCHEMA,
        "objective": _nonempty_string(data["objective"], "objective"),
        "workspace_root": _relative(data["workspace_root"], "workspace_root",
                                    allow_root=True),
        "inputs": inputs,
        "overwrite_paths": overwrite_paths,
        "budgets": _budgets(data["budgets"]),
        "tools": tools,
        "commands": commands,
        "acceptance": {"path": _acceptance(data["acceptance"])},
    }
    spec = TaskSpec(copy.deepcopy(normalized), normalized["acceptance"]["path"])
    spec.to_policy()
    return spec


def load_spec(path: Path) -> TaskSpec:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaskSpecError("cannot read TaskSpec %s: %s" % (target, exc))
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise TaskSpecError("TaskSpec %s is not valid JSON: %s" % (target, exc))
    return parse_spec(payload)
