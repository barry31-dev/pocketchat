"""Strict TaskPlan v1 parsing and one bounded plan-generation attempt.

A TaskPlan is not an execution authorization. It describes the steps a model
proposes under a TaskSpec, and every step is re-checked against that immutable
spec before the plan can leave this module. The only retry here is one revision
of the plan text; it never retries a provider request or executes a step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .provider import ProviderError
from .request import (CredentialInRequestError, canonical_bytes,
                      contains_credential, write_request_snapshot)
from .task_spec import TaskSpec
from .trace import utc_now

PLAN_SCHEMA = "pocketchat-task-plan.v1"
PLAN_SNAPSHOT_SCHEMA = "pocketchat-task-plan-snapshot.v1"
MAX_PLAN_STEPS = 64
MAX_OBJECTIVE_CHARS = 2000
MAX_PLAN_RESPONSE_CHARS = 64000
MAX_REVISION_CONTEXT_CHARS = 16000
MAX_STEP_TEXT_CHARS = 512
MAX_INPUT_CHARS = 512
_ACTIONS = frozenset({"read_file", "write_file", "memory_search",
                      "run_command"})
_ROOT_KEYS = ("schema_version", "spec_sha256", "steps", "revision")
_STEP_KEYS = ("id", "objective", "action", "inputs", "expected_evidence",
              "depends_on", "retry_limit")
_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class TaskPlanError(ValueError):
    """A plan is malformed or asks for more than its TaskSpec allows."""


class PlanGenerationError(TaskPlanError):
    """Plan generation failed before producing an executable plan."""

    def __init__(self, message: str, *, attempts: int,
                 error_type: str = "plan_generation_failed") -> None:
        super().__init__(message)
        self.attempts = attempts
        self.error_type = error_type


@dataclass(frozen=True)
class TaskPlan:
    source: Dict[str, Any]
    spec_sha256: str

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return self.source["steps"]

    @property
    def revision(self) -> int:
        return self.source["revision"]


@dataclass(frozen=True)
class PlanGeneration:
    plan: TaskPlan
    attempts: int
    plan_snapshot: Path
    plan_sha256: str
    request_snapshots: List[Path]
    plan_file: Optional[Path] = None


def _canonical_spec_bytes(spec: TaskSpec) -> bytes:
    if not isinstance(spec, TaskSpec):
        raise TaskPlanError("a TaskPlan must be bound to a parsed TaskSpec")
    try:
        text = json.dumps(spec.source, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise TaskPlanError("TaskSpec cannot be canonicalized: %s" % exc)
    return text.encode("utf-8")


def spec_sha256(spec: TaskSpec) -> str:
    """The digest of the normalized TaskSpec, not of a caller's dict order."""
    return hashlib.sha256(_canonical_spec_bytes(spec)).hexdigest()


def plan_sha256(plan: TaskPlan) -> str:
    """The digest a later authorization must bind to this exact plan."""
    if not isinstance(plan, TaskPlan):
        raise TaskPlanError("a plan digest needs a parsed TaskPlan")
    return hashlib.sha256(canonical_bytes(plan.source)).hexdigest()


def _object(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskPlanError("%s must be an object" % what)
    return value


def _closed(value: Mapping[str, Any], allowed, what: str) -> None:
    unknown = sorted(map(str, (key for key in value if key not in allowed)))
    if unknown:
        raise TaskPlanError("%s has unknown field(s): %s"
                            % (what, ", ".join(unknown)))


def _nonempty(value: Any, what: str, maximum: int = MAX_OBJECTIVE_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskPlanError("%s must be a non-empty string" % what)
    text = value.strip()
    if len(text) > maximum:
        raise TaskPlanError("%s exceeds %d characters" % (what, maximum))
    return text


def _integer(value: Any, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TaskPlanError("%s must be an integer" % what)
    if not low <= value <= high:
        raise TaskPlanError("%s must be between %d and %d"
                            % (what, low, high))
    return value


def _path(value: Any, what: str) -> str:
    text = _nonempty(value, what, MAX_INPUT_CHARS)
    if text.startswith("/") or "\\" in text:
        raise TaskPlanError("%s must be workspace-relative" % what)
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise TaskPlanError("%s must not contain empty, '.' or '..' segments"
                            % what)
    return text


def _paths(value: Any, what: str) -> List[str]:
    if not isinstance(value, list):
        raise TaskPlanError("%s must be a list" % what)
    result: List[str] = []
    for entry in value:
        path = _path(entry, "%s entry" % what)
        if path not in result:
            result.append(path)
    return result


def _granted_paths(spec: TaskSpec, action: str) -> List[str]:
    tool = spec.source["tools"].get(action)
    if not isinstance(tool, dict):
        return []
    return list(tool.get("allow_paths", []))


def _input_value(value: Any, action: str, spec: TaskSpec,
                 step_id: str) -> List[str]:
    """Validate action inputs while reusing the TaskSpec's grants.

    File tools consume workspace-relative paths and must remain in the matching
    allowlist. Each step names at least one input so it is not a no-op; a write
    names exactly one target, and memory search carries exactly one literal
    query bounded by the result budget rather than a path allowlist.
    """
    if action == "run_command":
        if not isinstance(value, list) or len(value) != 1:
            raise TaskPlanError("run_command requires exactly one template id")
        template_id = _nonempty(value[0], "step.inputs command template",
                                MAX_INPUT_CHARS)
        if template_id not in {item["id"] for item in spec.commands}:
            raise TaskPlanError("run_command names an undeclared template %r"
                                % template_id)
        return [template_id]
    if action == "memory_search":
        if not isinstance(value, list):
            raise TaskPlanError("step.inputs must be a list")
        if len(value) != 1:
            raise TaskPlanError("memory_search requires exactly one query input")
        query = _nonempty(value[0], "step.inputs entry", MAX_INPUT_CHARS)
        if int(spec.source["budgets"]["max_tool_calls"]) < 1:
            raise TaskPlanError("memory_search exceeds the TaskSpec tool-call budget")
        if "\u0000" in query:
            raise TaskPlanError("step.inputs query must not contain NUL")
        return [query]
    inputs = _paths(value, "step.inputs")
    if not inputs:
        raise TaskPlanError("step %r requires at least one input" % step_id)
    if len(inputs) > int(spec.source["budgets"]["max_tool_calls"]):
        raise TaskPlanError(
            "step %r lists more inputs than the TaskSpec tool-call budget"
            % step_id)
    if action == "write_file" and len(inputs) != 1:
        raise TaskPlanError("write_file step must name exactly one target")
    allowed_inputs = _granted_paths(spec, action)
    if any(path not in allowed_inputs for path in inputs):
        raise TaskPlanError("step %r reads or writes outside its spec grant"
                            % step_id)
    return inputs


def _validate_steps(steps: Any, spec: TaskSpec) -> List[Dict[str, Any]]:
    if not isinstance(steps, list) or not steps:
        raise TaskPlanError("steps must be a non-empty list")
    limit = min(MAX_PLAN_STEPS, int(spec.source["budgets"]["max_steps"]))
    if len(steps) > limit:
        raise TaskPlanError("steps must not exceed the TaskSpec max_steps budget of %d"
                            % limit)
    parsed: List[Dict[str, Any]] = []
    seen = set()
    for raw in steps:
        step = _object(raw, "each plan step")
        _closed(step, _STEP_KEYS, "plan step")
        for key in _STEP_KEYS:
            if key not in step:
                raise TaskPlanError("plan step is missing %s" % key)
        step_id = _nonempty(step["id"], "step.id", 64)
        if not _ID_PATTERN.fullmatch(step_id):
            raise TaskPlanError("step.id has an unsupported shape: %r" % step_id)
        if step_id in seen:
            raise TaskPlanError("duplicate step id %r" % step_id)
        seen.add(step_id)
        action = _nonempty(step["action"], "step.action", 32)
        if action not in _ACTIONS:
            raise TaskPlanError("unknown action %r" % action)
        if action == "run_command":
            if not spec.commands:
                raise TaskPlanError("run_command is not granted by the TaskSpec")
        elif action not in spec.source["tools"]:
            raise TaskPlanError("action %r is not granted by the TaskSpec" % action)
        inputs = _input_value(step["inputs"], action, spec, step_id)
        depends = _paths(step["depends_on"], "step.depends_on")
        parsed.append({
            "id": step_id,
            "objective": _nonempty(step["objective"], "step.objective",
                                   MAX_STEP_TEXT_CHARS),
            "action": action,
            "inputs": inputs,
            "expected_evidence": _nonempty(
                step["expected_evidence"], "step.expected_evidence",
                MAX_STEP_TEXT_CHARS),
            "depends_on": depends,
            "retry_limit": _integer(step["retry_limit"], "step.retry_limit",
                                    0, 0),
        })
    # A plan is not an execution authorization, and this slice has no
    # command/recovery contract that could safely spend a retry budget. Until
    # that exists, every step must state zero; the bounded one revision belongs
    # to generation, not to step execution.
    if any(step["retry_limit"] != 0 for step in parsed):
        raise TaskPlanError("step.retry_limit must be 0 in TaskPlan v1")
    # Dependencies may only name earlier steps. That makes cycles impossible
    # and keeps a plan replayable without a separate graph normalization pass.
    prior = set()
    for step in parsed:
        unknown = [item for item in step["depends_on"] if item not in prior]
        if unknown:
            raise TaskPlanError("step %r depends on a missing or later step: %s"
                                % (step["id"], ", ".join(unknown)))
        prior.add(step["id"])
    if len(parsed) > int(spec.source["budgets"]["max_tool_calls"]):
        raise TaskPlanError("plan would exceed the TaskSpec max_tool_calls budget")
    return parsed


def parse_plan(payload: Any, *, spec: TaskSpec) -> TaskPlan:
    """Parse one closed plan and prove it stays inside its TaskSpec."""
    data = _object(payload, "the TaskPlan")
    _closed(data, _ROOT_KEYS, "the TaskPlan")
    for key in _ROOT_KEYS:
        if key not in data:
            raise TaskPlanError("the TaskPlan is missing %s" % key)
    if data["schema_version"] != PLAN_SCHEMA:
        raise TaskPlanError("unsupported TaskPlan schema version %r"
                            % (data["schema_version"],))
    expected_digest = spec_sha256(spec)
    digest = data["spec_sha256"]
    if not isinstance(digest, str) or digest != expected_digest:
        raise TaskPlanError("TaskPlan spec_sha256 does not match the TaskSpec")
    revision = _integer(data["revision"], "revision", 0, 1)
    normalized = {
        "schema_version": PLAN_SCHEMA,
        "spec_sha256": digest,
        "steps": _validate_steps(data["steps"], spec),
        "revision": revision,
    }
    return TaskPlan(normalized, digest)


def verify_plan_snapshot(path: Path, *, spec: TaskSpec) -> TaskPlan:
    """Verify one generated plan snapshot and return its parsed plan.

    The snapshot is evidence only if its stored digest is recomputed from the
    plan object and the plan is revalidated against the same TaskSpec. A
    changed snapshot is refused rather than returned with stale authority.
    """
    target = Path(path)
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskPlanError("cannot read TaskPlan snapshot %s: %s" % (target, exc))
    data = _object(record, "the TaskPlan snapshot")
    _closed(data, ("schema_version", "run_id", "created", "attempts",
                   "request_snapshots", "plan", "plan_sha256"),
            "the TaskPlan snapshot")
    if data.get("schema_version") != PLAN_SNAPSHOT_SCHEMA:
        raise TaskPlanError("unsupported TaskPlan snapshot schema version %r"
                            % (data.get("schema_version"),))
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not _ID_PATTERN.fullmatch(run_id):
        raise TaskPlanError("TaskPlan snapshot run_id has an unsupported shape")
    if not isinstance(data.get("created"), str) or not data["created"].strip():
        raise TaskPlanError("TaskPlan snapshot created must be a non-empty string")
    attempts = _integer(data.get("attempts"), "snapshot attempts", 1, 2)
    references = data.get("request_snapshots")
    expected_refs = ["%s.attempt-%d.json" % (run_id, index)
                     for index in range(1, attempts + 1)]
    if references != expected_refs:
        raise TaskPlanError("TaskPlan snapshot request references are incomplete")
    plan = parse_plan(data.get("plan"), spec=spec)
    if attempts != plan.revision + 1:
        raise TaskPlanError("TaskPlan snapshot attempts disagree with revision")
    stored = data.get("plan_sha256")
    if not isinstance(stored, str) or stored != plan_sha256(plan):
        raise TaskPlanError("TaskPlan snapshot plan_sha256 does not match its plan")
    return plan


def load_plan(path: Path, *, spec: TaskSpec) -> TaskPlan:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaskPlanError("cannot read TaskPlan %s: %s" % (target, exc))
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise TaskPlanError("TaskPlan %s is not valid JSON: %s" % (target, exc))
    return parse_plan(payload, spec=spec)


def write_plan(path: Path, plan: TaskPlan, *, spec: TaskSpec) -> str:
    """Persist one plain TaskPlan once and verify the bytes that were read back."""
    if not isinstance(plan, TaskPlan) or not isinstance(spec, TaskSpec):
        raise TaskPlanError("plan persistence needs parsed TaskPlan and TaskSpec")
    reparsed = parse_plan(plan.source, spec=spec)
    digest = plan_sha256(reparsed)
    if digest != plan_sha256(plan):
        raise TaskPlanError("TaskPlan changed before persistence")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(reparsed.source)
    handle = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    loaded = load_plan(target, spec=spec)
    if plan_sha256(loaded) != digest:
        raise TaskPlanError("TaskPlan changed while it was persisted")
    return digest


def _actionable_action(spec: TaskSpec) -> Optional[str]:
    """Return one action that can name at least one valid input.

    The returned action must also have enough remaining tool-call budget for
    the example to be valid; a grant with no actionable budget cannot produce
    a plan that this parser will accept.
    """
    if int(spec.source["budgets"]["max_tool_calls"]) < 1:
        return None
    for name in ("read_file", "write_file", "memory_search"):
        tool = spec.source["tools"].get(name)
        if not isinstance(tool, dict):
            continue
        if name == "memory_search" or tool.get("allow_paths"):
            return name
    if spec.commands:
        return "run_command"
    return None


def _example_step(spec: TaskSpec) -> Dict[str, Any]:
    """A schema-valid example drawn only from the TaskSpec's own grants."""
    action = _actionable_action(spec)
    if action is None:
        raise TaskPlanError("TaskSpec grants no action with a usable input")
    if action == "run_command":
        inputs = [spec.commands[0]["id"]]
    elif action == "memory_search":
        inputs = ["literal query"]
    else:
        paths = spec.source["tools"][action].get("allow_paths", [])
        inputs = [paths[0]] if paths else []
    return {
        "id": "stable_name", "objective": "bounded objective",
        "action": action, "inputs": inputs,
        "expected_evidence": "what proves this step",
        "depends_on": [], "retry_limit": 0,
    }


def _prompt(spec: TaskSpec, previous_error: Optional[str],
            previous_response: Optional[str] = None) -> str:
    payload = {
        "task_spec": spec.source,
        "output_schema": {
            "schema_version": PLAN_SCHEMA,
            "spec_sha256": spec_sha256(spec),
            "steps": [_example_step(spec)],
            "revision": 0,
        },
        "allowed_actions": sorted(_ACTIONS),
    }
    text = ("Create exactly one TaskPlan JSON object for this TaskSpec. "
            "Return JSON only, with no prose or markdown. Do not add fields, "
            "expand paths or use tools not granted by the TaskSpec.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if previous_error:
        if previous_response:
            clipped = previous_response[:MAX_REVISION_CONTEXT_CHARS]
            if len(previous_response) > len(clipped):
                clipped += "\n... [previous response clipped]"
            text += "\nPrevious invalid plan text:\n" + clipped
        text += ("\nThe previous plan was rejected before execution: %s\n"
                 "Revise once, keeping or narrowing the TaskSpec permissions."
                 % previous_error)
    return text


def _response_text(response: Mapping[str, Any]) -> str:
    if not isinstance(response, Mapping):
        raise TaskPlanError("provider response must be an object")
    content = response.get("content")
    if not isinstance(content, str):
        raise TaskPlanError("provider response did not contain text")
    if len(content) > MAX_PLAN_RESPONSE_CHARS:
        raise TaskPlanError("provider plan text exceeds %d characters"
                            % MAX_PLAN_RESPONSE_CHARS)
    return content


def plan_snapshot_path(requests_dir: Path, run_id: str) -> Path:
    """A deterministic, non-overwriting location for one generated plan."""
    return Path(requests_dir) / ("%s.plan.json" % run_id)


def _write_plan_snapshot(path: Path, plan: TaskPlan, attempts: int,
                         run_id: str, request_snapshots: List[Path],
                         credential: Optional[str] = None) -> str:
    digest = plan_sha256(plan)
    references = [Path(item).name for item in request_snapshots]
    expected_refs = ["%s.attempt-%d.json" % (run_id, index)
                     for index in range(1, attempts + 1)]
    if references != expected_refs:
        raise TaskPlanError("request snapshots do not match the plan attempts")
    if any(Path(item).name != item for item in references):
        raise TaskPlanError("request snapshot references must be bare filenames")
    if len(set(references)) != len(references):
        raise TaskPlanError("request snapshot references must be unique")
    record = {
        "schema_version": PLAN_SNAPSHOT_SCHEMA,
        "run_id": run_id,
        "created": utc_now(),
        "attempts": attempts,
        "request_snapshots": references,
        "plan": plan.source,
        "plan_sha256": digest,
    }
    record_bytes = canonical_bytes(record)
    if contains_credential(record_bytes, credential):
        raise CredentialInRequestError(
            "the generated plan contains the configured credential; no plan "
            "snapshot was written")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(handle, "wb") as stream:
        stream.write(record_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    return digest


def generate_plan(spec: TaskSpec, adapter, config: Dict[str, Any],
                  *, requests_dir: Path, run_id: str,
                  api_key: Optional[str] = None,
                  out_path: Optional[Path] = None) -> PlanGeneration:
    """Generate a plan with at most one revision after a parse failure.

    Provider errors are not revised or retried: there is no evidence that a
    second request would have a different outcome. Only a syntactically or
    semantically invalid plan gets one corrective request.
    """
    if not isinstance(config, dict):
        raise PlanGenerationError("provider config must be an object", attempts=0)
    if not isinstance(run_id, str) or not _ID_PATTERN.fullmatch(run_id):
        raise PlanGenerationError("run_id has an unsupported shape", attempts=0)
    if (not isinstance(requests_dir, (str, os.PathLike))
            or not str(requests_dir).strip()):
        raise PlanGenerationError("requests_dir must be a non-empty path",
                                  attempts=0)
    if plan_snapshot_path(requests_dir, run_id).exists():
        raise PlanGenerationError("plan snapshot already exists for this run_id",
                                  attempts=0,
                                  error_type="snapshot_exists")
    if out_path is not None:
        target = Path(out_path)
        if target.exists():
            raise PlanGenerationError(
                "plain plan path already exists", attempts=0,
                error_type="snapshot_exists")
        snapshot_target = plan_snapshot_path(Path(requests_dir), run_id)
        try:
            requests_root = Path(requests_dir)
            workspace = requests_root.resolve().parent.parent
            target.resolve().relative_to(workspace)
            if target.resolve() == snapshot_target.resolve():
                raise PlanGenerationError(
                    "plain plan path collides with its snapshot", attempts=0,
                    error_type="path_escape")
        except PlanGenerationError:
            raise
        except (OSError, ValueError):
            raise PlanGenerationError(
                "plain plan path escapes the workspace", attempts=0,
                error_type="path_escape")
    if _actionable_action(spec) is None:
        raise PlanGenerationError("TaskSpec grants no action with a usable input",
                                  attempts=0)
    max_steps = int(spec.source["budgets"]["max_steps"])
    max_tool_calls = int(spec.source["budgets"]["max_tool_calls"])
    if max_steps < 1 or max_tool_calls < 1:
        raise PlanGenerationError("TaskSpec has no usable execution budget",
                                  attempts=0)
    snapshots: List[Path] = []
    previous_error: Optional[str] = None
    previous_response: Optional[str] = None
    for attempt in (1, 2):
        prompt = _prompt(spec, previous_error, previous_response)
        messages = [{"role": "user", "content": prompt}]
        try:
            payload = adapter.build_payload(messages, config)
            if not isinstance(payload, dict):
                raise TaskPlanError("provider built a non-object request payload")
            path = Path(requests_dir) / ("%s.attempt-%d.json" % (run_id, attempt))
            write_request_snapshot(path, run_id=run_id, profile=config.get("profile"),
                                   created=utc_now(), payload=payload,
                                   credential=api_key)
            snapshots.append(path)
        except CredentialInRequestError as exc:
            raise PlanGenerationError(
                "plan request evidence was refused: %s" % exc,
                attempts=attempt, error_type="request_evidence_refused")
        except (OSError, ValueError) as exc:
            raise PlanGenerationError(
                "plan request evidence could not be written: %s" % exc,
                attempts=attempt, error_type="request_evidence_write_failed")
        try:
            response = adapter.complete(messages, config, api_key or "")
            raw = _response_text(response)
        except ProviderError as exc:
            raise PlanGenerationError(
                "plan provider failed: %s" % exc.error_type, attempts=attempt,
                error_type="provider_failed")
        try:
            try:
                draft = json.loads(raw)
            except ValueError as exc:
                raise TaskPlanError("provider plan is not valid JSON: %s" % exc)
            plan = parse_plan(draft, spec=spec)
            if attempt == 2 and plan.revision != 1:
                raise TaskPlanError("the revised plan must declare revision 1")
            if attempt == 1 and plan.revision != 0:
                raise TaskPlanError("initial plan must declare revision 0")
            try:
                plan_path = plan_snapshot_path(requests_dir, run_id)
                digest = _write_plan_snapshot(plan_path, plan, attempt, run_id,
                                              snapshots, api_key)
            except (OSError, CredentialInRequestError) as exc:
                raise PlanGenerationError(
                    "plan evidence could not be written: %s" % exc,
                    attempts=attempt, error_type="plan_evidence_write_failed")
            if verify_plan_snapshot(plan_path, spec=spec) != plan:
                raise PlanGenerationError(
                    "plan snapshot changed before it could be returned",
                    attempts=attempt, error_type="snapshot_changed")
            plan_file = None
            if out_path is not None:
                try:
                    plan_file = Path(out_path)
                    write_plan(plan_file, plan, spec=spec)
                except (OSError, TaskPlanError) as exc:
                    raise PlanGenerationError(
                        "plain plan file could not be written: %s" % exc,
                        attempts=attempt, error_type="plan_write_failed")
            return PlanGeneration(plan, attempt, plan_path, digest, snapshots,
                                  plan_file)
        except PlanGenerationError:
            raise
        except (ValueError, TaskPlanError) as exc:
            previous_error = str(exc)
            previous_response = raw[:MAX_REVISION_CONTEXT_CHARS]
            if attempt == 2:
                raise PlanGenerationError(
                    "plan generation did not produce a valid plan after one "
                    "revision: %s" % exc, attempts=attempt)
    raise AssertionError("unreachable")
