"""The task policy: what an operator allowed a bounded task to do.

A task run is only as safe as the document that authorized it, so this module
is deliberately boring. It reads one closed shape, refuses anything it does not
name, and returns a value the executor can trust without re-checking. An
unknown field is not ignored: a permission document that silently accepted
``execute_shell`` would read as a narrower grant than it actually was.

Budgets are bounds on work, not on quality. Every one of them has a ceiling
declared here and mirrored in ``schemas/task.schema.json``; the two are kept
in agreement by a test, because a schema that promises a limit the parser does
not enforce is a promise nobody keeps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

POLICY_SCHEMA = "pocketchat-task-policy.v1"

MIN_STEPS = 1
MAX_STEPS = 64
MIN_TOOL_CALLS = 1
MAX_TOOL_CALLS = 64
MIN_CONTEXT_CHARS = 1
MAX_CONTEXT_CHARS = 400000
MIN_TOKENS = 1
MAX_TOKENS = 4000000
MIN_MEMORY_RESULTS = 1
MAX_MEMORY_RESULTS = 100
DEFAULT_MEMORY_RESULTS = 20
MIN_CHILDREN = 1
MAX_CHILDREN = 8

TOOLS = ("read_file", "write_file", "memory_search")
_TOOL_KEYS = {
    "read_file": ("allow_paths",),
    "write_file": ("allow_paths", "create_only"),
    "memory_search": ("max_results",),
}
_DELEGATE_KEYS = ("enabled", "max_children")


class TaskPolicyError(ValueError):
    """The policy could not be read, or it asked for something unnamed."""


@dataclass
class TaskPolicy:
    """One parsed policy. Every field is a bound the executor may rely on."""

    schema_version: str
    objective: str
    workspace_root: str
    max_steps: int
    max_tool_calls: int
    max_context_chars: int
    max_turn_tokens: int
    max_session_tokens: Optional[int]
    tools: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    max_task_tokens: Optional[int] = None
    delegate_enabled: bool = False
    delegate_max_children: int = 0
    #: The document that was signed, kept so its digest is over the policy
    #: itself and not over this parsed shape. A resume compares digests.
    source: Dict[str, Any] = field(default_factory=dict)

    def tool_enabled(self, name: str) -> bool:
        return name in self.tools

    @property
    def read_allow_paths(self) -> List[str]:
        return list(self.tools.get("read_file", {}).get("allow_paths", []))

    @property
    def write_allow_paths(self) -> List[str]:
        return list(self.tools.get("write_file", {}).get("allow_paths", []))

    @property
    def write_create_only(self) -> bool:
        return bool(self.tools.get("write_file", {}).get("create_only", True))

    @property
    def memory_search_max_results(self) -> int:
        return int(self.tools.get("memory_search", {})
                   .get("max_results", DEFAULT_MEMORY_RESULTS))


def _require_object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskPolicyError("%s must be an object" % what)
    return value


def _closed(payload: Mapping[str, Any], allowed, what: str) -> None:
    """Refuse every key the contract does not name, by name."""
    unknown = [key for key in payload if key not in allowed]
    if unknown:
        raise TaskPolicyError("%s has unknown field(s): %s"
                              % (what, ", ".join(sorted(map(str, unknown)))))


def _objective(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskPolicyError("objective must be a non-empty string")
    return value.strip()


def _relative_path(value: Any, what: str) -> str:
    """One workspace-relative path, or a refusal naming the reason.

    An absolute path, an empty segment, a ``.`` segment and any ``..`` are all
    refused rather than normalized away: the allowlist is compared against what
    the tool layer resolves, and a grant that only becomes safe after rewriting
    is a grant nobody read.
    """
    if not isinstance(value, str):
        raise TaskPolicyError("%s must be a string" % what)
    text = value.strip()
    if not text:
        raise TaskPolicyError("%s must not be empty" % what)
    if "\\" in text:
        raise TaskPolicyError("%s must not contain a backslash: %r" % (what, value))
    if text.startswith("/"):
        raise TaskPolicyError("%s must be workspace-relative, not absolute: %r"
                              % (what, value))
    for segment in text.split("/"):
        if segment in ("", ".", ".."):
            raise TaskPolicyError("%s must not contain an empty, '.' or '..' "
                                  "segment: %r" % (what, value))
    return text


def _workspace_root(value: Any) -> str:
    """The task's root inside the workspace. ``.`` means the workspace itself."""
    if value is None:
        return "."
    if isinstance(value, str) and value.strip() == ".":
        return "."
    return _relative_path(value, "workspace_root")


def _bound(value: Any, name: str, low: int, high: int) -> int:
    """An integer inside [low, high]. Booleans and floats are not integers here."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskPolicyError("%s must be an integer" % name)
    if value < low or value > high:
        raise TaskPolicyError("%s must be between %d and %d, got %d"
                              % (name, low, high, value))
    return value


def _allow_paths(grant: Mapping[str, Any], tool: str) -> List[str]:
    raw = grant.get("allow_paths")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TaskPolicyError("%s.allow_paths must be a list" % tool)
    paths: List[str] = []
    for entry in raw:
        path = _relative_path(entry, "%s.allow_paths entry" % tool)
        if path not in paths:
            paths.append(path)
    return paths


def _tool_grants(value: Any) -> Dict[str, Dict[str, Any]]:
    """Parse the tool block. An unlisted tool is a refusal, not a no-op."""
    if value is None:
        return {}
    payload = _require_object(value, "tools")
    unknown = sorted(key for key in payload if key not in TOOLS)
    if unknown:
        raise TaskPolicyError("tools has unknown tool(s): %s"
                              % ", ".join(unknown))
    grants: Dict[str, Dict[str, Any]] = {}
    for name, raw in payload.items():
        grant = _require_object(raw, "tools.%s" % name)
        _closed(grant, _TOOL_KEYS[name], "tools.%s" % name)
        parsed: Dict[str, Any] = {"allow_paths": _allow_paths(grant, name)}
        if name == "write_file":
            create_only = grant.get("create_only", True)
            if create_only is not True:
                raise TaskPolicyError(
                    "tools.write_file is create-only and cannot be widened")
            parsed["create_only"] = True
        if name == "memory_search":
            results = grant.get("max_results", DEFAULT_MEMORY_RESULTS)
            parsed["max_results"] = _bound(
                results, "tools.memory_search.max_results",
                MIN_MEMORY_RESULTS, MAX_MEMORY_RESULTS)
        grants[name] = parsed
    return grants


def _delegate(value: Any) -> Dict[str, Any]:
    """Delegation stays opt-in and finite; the default is no children."""
    if value is None:
        return {"enabled": False, "max_children": 0}
    payload = _require_object(value, "delegate")
    _closed(payload, _DELEGATE_KEYS, "delegate")
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise TaskPolicyError("delegate.enabled must be a boolean")
    if not enabled:
        return {"enabled": False, "max_children": 0}
    children = _bound(payload.get("max_children", 0),
                      "delegate.max_children", MIN_CHILDREN, MAX_CHILDREN)
    return {"enabled": True, "max_children": children}


def parse_policy(payload: Any) -> TaskPolicy:
    """One policy mapping, parsed strictly. Anything unnamed is refused."""
    data = _require_object(payload, "the task policy")
    _closed(data, ("schema_version", "objective", "workspace_root",
                   "max_steps", "max_tool_calls", "max_context_chars",
                   "max_turn_tokens", "max_session_tokens", "max_task_tokens",
                   "tools", "delegate"), "the task policy")
    version = data.get("schema_version")
    if version != POLICY_SCHEMA:
        raise TaskPolicyError(
            "unsupported task policy schema version %r; this pocketchat reads "
            "%r" % (version, POLICY_SCHEMA))
    turn_tokens = _bound(data["max_turn_tokens"], "max_turn_tokens",
                         MIN_TOKENS, MAX_TOKENS) if "max_turn_tokens" in data \
        else None
    if turn_tokens is None:
        raise TaskPolicyError("max_turn_tokens is required")
    session = data.get("max_session_tokens")
    session_tokens = (_bound(session, "max_session_tokens", MIN_TOKENS,
                             MAX_TOKENS) if session is not None else None)
    if session_tokens is not None and session_tokens < turn_tokens:
        raise TaskPolicyError(
            "max_session_tokens (%d) cannot be below one turn's budget "
            "max_turn_tokens (%d)" % (session_tokens, turn_tokens))
    task_tokens = data.get("max_task_tokens")
    task_tokens = (_bound(task_tokens, "max_task_tokens", MIN_TOKENS,
                          MAX_TOKENS) if task_tokens is not None else None)
    delegate = _delegate(data.get("delegate"))
    return TaskPolicy(
        schema_version=POLICY_SCHEMA,
        objective=_objective(data.get("objective")),
        workspace_root=_workspace_root(data.get("workspace_root")),
        max_steps=_bound(data["max_steps"], "max_steps", MIN_STEPS, MAX_STEPS),
        max_tool_calls=_bound(data["max_tool_calls"], "max_tool_calls",
                              MIN_TOOL_CALLS, MAX_TOOL_CALLS),
        max_context_chars=_bound(data["max_context_chars"],
                                 "max_context_chars", MIN_CONTEXT_CHARS,
                                 MAX_CONTEXT_CHARS),
        max_turn_tokens=turn_tokens,
        max_session_tokens=session_tokens,
        max_task_tokens=task_tokens,
        tools=_tool_grants(data.get("tools")),
        delegate_enabled=delegate["enabled"],
        delegate_max_children=delegate["max_children"],
        source=dict(data),
    )


def load_policy(path: Path) -> TaskPolicy:
    """Read one policy file. An unreadable file is a refusal, never a default."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaskPolicyError("cannot read task policy %s: %s" % (target, exc))
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise TaskPolicyError("task policy %s is not valid JSON: %s"
                              % (target, exc))
    return parse_policy(payload)
