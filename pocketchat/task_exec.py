"""Bound TaskPlan-to-TaskRun conversion, without widening authority.

A plan is useful only after two independent links are checked: its declared
spec digest must equal the normalized TaskSpec, and its own digest must equal
the authorization supplied to the executor. Every step then becomes a
one-step policy narrowed to that action's granted paths. The existing
``run_task`` owns provider, tool, session and trace behavior; this module does
not execute tools itself.

The executor also writes one bounded execution report after the steps it was
allowed to attempt. That report is evidence for a later ``task report``; it is
not a second task state machine and it never contains command output bodies.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from .task_command import (CommandError, command_evidence_sha256,
                           load_command_evidence)
from .task_plan import (TaskPlan, TaskPlanError, parse_plan, plan_sha256,
                        spec_sha256)
from .task_policy import TaskPolicyError, parse_policy
from .task_spec import TaskSpec

EXECUTION_REPORT_SCHEMA = "pocketchat-task-execution-report.v1"
EXECUTION_REPORTS_DIR = Path(".pocketchat") / "task-reports"
_MAX_CHANGE_BYTES = 1024 * 1024
_HEX = frozenset("0123456789abcdef")


class TaskExecutionError(ValueError):
    """A plan cannot be converted into a bounded run.

    ``report`` is additive evidence for the CLI: it is set only after a run
    has started and an execution report was written. The exception class and
    message remain the existing contract for other callers.
    """

    def __init__(self, message, *, report=None):
        super().__init__(message)
        self.report = report


class TaskCancelledError(TaskExecutionError):
    """A confirmed boundary was persisted; no pending step was started."""

    def __init__(self, *, report, results):
        super().__init__("task cancelled at a confirmed step boundary", report=report)
        self.results = [dict(result) for result in results]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in _HEX for char in value))


def _relative(root: Path, value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve().relative_to(root.resolve()).as_posix()


@dataclass(frozen=True)
class ExecutionStep:
    id: str
    action: str
    policy: Any
    prompt: str
    depends_on: List[str]
    command_template_id: str = ""


@dataclass(frozen=True)
class ExecutionPlan:
    spec_sha256: str
    plan_sha256: str
    acceptance_path: str
    steps: Tuple[ExecutionStep, ...]


def authorize(plan: TaskPlan, spec: TaskSpec, *,
              expected_plan_sha256: str) -> ExecutionPlan:
    """Bind a plan to its spec and an explicit plan digest.

    ``expected_plan_sha256`` is the operator-visible authorization value. If
    either document changes, or a caller passes the digest of a different
    plan, conversion fails before any provider request.
    """
    if not isinstance(plan, TaskPlan) or not isinstance(spec, TaskSpec):
        raise TaskExecutionError(
            "authorization needs a parsed TaskPlan and TaskSpec")
    if not _sha(expected_plan_sha256):
        raise TaskExecutionError("an expected lowercase SHA-256 is required")
    # Reparse the source under the same immutable spec. This rejects a plan
    # object whose fields were mutated after generation as well as a spec swap.
    try:
        reparsed = parse_plan(plan.source, spec=spec)
    except TaskPlanError as exc:
        raise TaskExecutionError(str(exc))
    if plan.spec_sha256 != reparsed.spec_sha256:
        raise TaskExecutionError("TaskPlan spec hash changed")
    digest = plan_sha256(reparsed)
    if digest != expected_plan_sha256:
        raise TaskExecutionError(
            "TaskPlan hash does not match the authorization")
    steps = tuple(_convert_step(step, spec) for step in reparsed.steps)
    return ExecutionPlan(reparsed.spec_sha256, digest,
                         spec.acceptance_path, steps)


def _convert_step(step: Dict[str, Any], spec: TaskSpec) -> ExecutionStep:
    action = step["action"]
    if action == "run_command":
        template_id = step["inputs"][0]
        if template_id not in {item["id"] for item in spec.commands}:
            raise TaskExecutionError(
            "step %r names an undeclared command template" % step["id"])
        return ExecutionStep(step["id"], action, None,
                             "Run command template %s; evidence is separate "
                             "from task acceptance." % template_id,
                             list(step["depends_on"]), template_id)
    if action not in spec.source["tools"]:
        raise TaskExecutionError(
            "step %r uses an action not granted by the spec" % step["id"])
    allowed = list(spec.source["tools"][action].get("allow_paths", []))
    if action in ("read_file", "write_file"):
        if any(item not in allowed for item in step["inputs"]):
            raise TaskExecutionError(
            "step %r exceeds its allowlisted inputs" % step["id"])
        tools = {action: {"allow_paths": list(step["inputs"])}}
        if action == "write_file":
            tools[action]["create_only"] = True
    else:
        tools = {"memory_search": {
            "max_results": spec.source["tools"][action].get("max_results", 20)}}
    try:
        policy = parse_policy({
            "schema_version": "pocketchat-task-policy.v1",
            "objective": step["objective"],
            "workspace_root": spec.source["workspace_root"],
            "max_steps": 3,
            "max_tool_calls": 1,
            "max_context_chars": int(spec.source["budgets"]["max_context_chars"]),
            "max_turn_tokens": int(spec.source["budgets"]["max_turn_tokens"]),
            "tools": tools,
        })
    except TaskPolicyError as exc:
        raise TaskExecutionError(str(exc))
    prompt = (
        "Execute exactly this TaskPlan step and no other action. "
        "Use the %s tool only with inputs %s. Expected evidence: %s.\n"
        "Step objective: %s"
        % (action, ", ".join(step["inputs"]) if step["inputs"] else "(none)",
           step["expected_evidence"], step["objective"])
    )
    return ExecutionStep(step["id"], action, policy, prompt,
                         list(step["depends_on"]))


def _git_original(root: Path, relative: str):
    """Read the HEAD form as evidence, without changing the worktree."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", "HEAD:%s" % relative],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, "unknown"
    if proc.returncode == 0:
        return proc.stdout, "head"
    # A create-only file absent from HEAD has an empty baseline. A broken
    # repository remains unknown rather than being reported as empty.
    probe = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        timeout=10)
    if probe.returncode == 0:
        return b"", "absent"
    return None, "unknown"


def _changed_files(workspace: Path, record):
    """Hash completed write paths and name changes that cannot be evidenced."""
    root = Path(workspace).resolve()
    paths = []
    for step in getattr(record, "steps", []) if record is not None else []:
        if (step.get("kind") == "tool" and step.get("tool") == "write_file"
                and step.get("state") == "completed" and step.get("detail")):
            # Tool paths are relative to the policy root; report paths are
            # relative to the caller workspace, as recovery expects.
            path = (Path(getattr(record, "workspace_root", "."))
                    / step["detail"]).as_posix()
            if path not in paths:
                paths.append(path)
    changes = []
    issues = []
    for path in paths:
        try:
            relative = _relative(root, path)
            candidate = (root / relative).resolve()
            candidate.relative_to(root)
            data = candidate.read_bytes()
        except (OSError, ValueError) as exc:
            issues.append({"path": str(path), "state": "unknown",
                           "reason": "cannot evidence changed file: %s" % exc})
            continue
        if len(data) > _MAX_CHANGE_BYTES:
            issues.append({"path": relative, "state": "unknown",
                           "reason": "changed file exceeds the evidence limit"})
            continue
        original, baseline = _git_original(root, relative)
        if original is None:
            original_hash = ""
            added = removed = None
            diff_state = "unknown"
        else:
            original_hash = _digest(original)
            try:
                before = original.decode("utf-8").splitlines()
                after = data.decode("utf-8").splitlines()
            except UnicodeDecodeError:
                added = removed = None
                diff_state = "binary"
            else:
                matcher = difflib.SequenceMatcher(a=before, b=after)
                added = removed = 0
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag in ("replace", "delete"):
                        removed += i2 - i1
                    if tag in ("replace", "insert"):
                        added += j2 - j1
                diff_state = "text"
        changes.append({
            "path": relative,
            "original_sha256": original_hash,
            "new_sha256": _digest(data),
            "bytes": len(data),
            "baseline": baseline,
            "added_lines": added,
            "removed_lines": removed,
            "diff_state": diff_state,
        })
    return changes, issues


def _command_summary(evidence: Dict[str, Any]) -> Dict[str, Any]:
    """Keep hashes and counts; never copy the bounded output previews."""
    allowed = (
        "template_id", "cwd", "exit_code", "timed_out", "duration_ms",
        "stdout_bytes", "stderr_bytes", "stdout_sha256", "stderr_sha256",
        "stdout_truncated", "stderr_truncated", "trace", "task_acceptance",
    )
    summary = {key: evidence.get(key) for key in allowed if key in evidence}
    repair = evidence.get("repair")
    if isinstance(repair, dict):
        bounded = {
            key: repair.get(key) for key in (
                "schema_version", "repair_mode", "attempted", "attempts",
                "outcome", "reason", "spec_sha256", "plan_sha256",
                "template_id", "template_sha256", "failure_fingerprint",
                "retry_failure_fingerprint", "action", "stop",
                "task_acceptance_pass", "request_snapshots",
                "request_snapshot_sha256")
            if key in repair
        }
        commands = repair.get("commands")
        if isinstance(commands, list):
            bounded["commands"] = [
                {key: item.get(key) for key in
                 ("template_id", "template_sha256", "argv", "exit_code",
                  "timed_out") if key in item}
                for item in commands if isinstance(item, dict)]
        summary["repair"] = bounded
    return summary


def _report_steps(root: Path, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for result in results:
        item = dict(result)
        path_issue = False
        for key in ("trace", "session"):
            if isinstance(item.get(key), str):
                try:
                    item[key] = _relative(root, item[key])
                except (OSError, ValueError):
                    item.pop(key, None)
                    path_issue = True
        evidence = item.get("evidence")
        if isinstance(evidence, dict):
            item["evidence"] = _command_summary(evidence)
            trace = item["evidence"].get("trace")
            if isinstance(trace, str):
                try:
                    item["evidence"]["trace"] = _relative(root, trace)
                except (OSError, ValueError):
                    item["evidence"].pop("trace", None)
                    path_issue = True
        if path_issue:
            item["path_issue"] = "path_escapes_workspace"
        trace = item.get("evidence", {}).get("trace") if isinstance(item.get("evidence"), dict) else None
        if isinstance(trace, str) and "archive_sha256" not in item:
            try:
                item["archive_sha256"] = command_evidence_sha256(root, trace)
            except CommandError:
                item["archive_sha256"] = "unknown"
        cleaned.append(item)
    return cleaned


def _write_execution_report(execution: ExecutionPlan, root: Path,
                            results: List[Dict[str, Any]],
                            changes: List[Dict[str, Any]],
                            issues: List[Dict[str, str]],
                            error: Dict[str, str] = None,
                            recovery=None, task_budget=None, cancellation=None) -> Path:
    task_ids = sorted({item["task_id"] for item in results
                       if isinstance(item.get("task_id"), str)
                       and item["task_id"]})
    payload = {
        "schema_version": EXECUTION_REPORT_SCHEMA,
        "report_id": "execution_%s" % uuid4().hex,
        "spec_sha256": execution.spec_sha256,
        "plan_sha256": execution.plan_sha256,
        "task_ids": task_ids,
        "steps": _report_steps(root, results),
        "changes": changes,
        "evidence_issues": issues,
    }
    if cancellation is not None:
        from .tasking import validate_cancellation_report
        payload["cancellation"] = cancellation
        validate_cancellation_report(payload)
    if task_budget is not None:
        payload["task_budget"] = task_budget.to_dict()
    if error is not None:
        payload["error"] = error
    if recovery is not None:
        payload["recovery"] = recovery
    directory = root / EXECUTION_REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("%s.json" % payload["report_id"])
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def _task_state_prompt(step, results, execution, root) -> str:
    """Add bounded evidence from completed dependencies to one step prompt.

    The projector is deliberately given a report-shaped view assembled from the
    already returned step records. It never includes a model's prose as proof.
    """
    if not step.depends_on:
        return step.prompt
    from .task_state import TaskStateError, project_task_state
    rows = []
    by_id = {item.id: item for item in execution.steps}
    for result in results:
        dependency_id = result.get("step_id")
        if dependency_id not in step.depends_on:
            continue
        row = {key: result[key] for key in
               ("step_id", "action", "status", "task_id", "trace",
                "session") if key in result}
        dependency = by_id.get(dependency_id)
        if dependency is None:
            raise TaskExecutionError(
                "dependency step %r is not in the authorized plan"
                % dependency_id)
        if dependency.policy is not None:
            row["inputs"] = (list(dependency.policy.read_allow_paths)
                             + list(dependency.policy.write_allow_paths))
        else:
            row["inputs"] = ([dependency.command_template_id]
                             if dependency.command_template_id else [])
        rows.append(row)
    report = {
        "spec_sha256": execution.spec_sha256,
        "plan_sha256": execution.plan_sha256,
        "steps": rows,
        "changes": [],
        "evidence_issues": [],
    }
    try:
        state = project_task_state(
            report, step_id=step.id, depends_on=list(step.depends_on),
            allowed_paths=list(step.policy.read_allow_paths)
            + list(step.policy.write_allow_paths))
    except TaskStateError as exc:
        raise TaskExecutionError(str(exc)) from exc
    prompt = step.prompt + "\nDependency evidence:\n" + json.dumps(
        state, ensure_ascii=False, sort_keys=True)
    from .task_sources import append_source_prompt
    from .task_recovery import TaskRecoveryError
    try:
        return append_source_prompt(
            prompt, root=root, step=step, execution=execution, results=results)
    except TaskRecoveryError as exc:
        raise TaskExecutionError(str(exc)) from exc


def execute_plan(execution: ExecutionPlan, *, run_task, workspace, config,
                 trace_dir, session_dir, acceptance=None, command_runner=None,
                 stop_after=None, resume_report=None, expected_report_sha256=None,
                 model_repair=None, spec=None, plan=None, api_key=None,
                 task_budget=None, cancellation=None):
    """Execute converted steps serially and persist a bounded report.

    Dependency order is validated by TaskPlan parsing, so each step is a
    closed one-tool authorization. A failed step stops the sequence; later
    steps are returned as not_executed rather than silently skipped. The
    report is written even when a runner raises, so a failed command cannot
    disappear from later inspection.
    """
    if not isinstance(execution, ExecutionPlan):
        raise TaskExecutionError("execute_plan needs an authorized ExecutionPlan")
    if cancellation is not None:
        from .tasking import TaskCancellation, TaskError
        if not isinstance(cancellation, TaskCancellation):
            raise TaskExecutionError("cancellation needs a task cancellation owner")
        try:
            cancellation.validate_for(execution.spec_sha256, execution.plan_sha256)
        except TaskError as exc:
            raise TaskExecutionError(str(exc)) from exc
    if cancellation is not None and model_repair == "model":
        # Repair retry archives use a separate run identity. Until that evidence
        # chain supports recovery, refuse this combination before any effect.
        raise TaskExecutionError("cancellation cannot be combined with model repair")
    if model_repair not in (None, "model"):
        raise TaskExecutionError("model_repair must be 'model' when supplied")
    if model_repair == "model":
        if spec is None or plan is None:
            raise TaskExecutionError(
                "model repair needs the authorized TaskSpec and TaskPlan")
        if (spec_sha256(spec) != execution.spec_sha256
                or plan_sha256(plan) != execution.plan_sha256):
            raise TaskExecutionError(
                "model repair source does not match the authorized execution")
    from .task_recovery import (TaskRecoveryError, bind_step_evidence,
                                prepare_recovery)
    if (stop_after is not None
            and (isinstance(stop_after, bool) or not isinstance(stop_after, int)
                 or not 1 <= stop_after <= len(execution.steps))):
        raise TaskExecutionError("stop_after must be a positive bounded step count")
    if resume_report is None and expected_report_sha256 is not None:
        raise TaskRecoveryError("a report digest needs an explicit report")
    root = Path(workspace).resolve()
    previous = recovery = None
    if resume_report is not None:
        previous, recovery = prepare_recovery(
            execution, root, resume_report, expected_report_sha256,
            trace_dir=trace_dir, session_dir=session_dir,
            cancellation=cancellation)
        previous_budget = previous.get("task_budget")
        budget = task_budget
        if budget is None and previous_budget is not None:
            raise TaskRecoveryError(
                "report has a task budget but the authorized spec does not")
        if budget is not None:
            from .task_budget import TaskBudget, TaskBudgetError
            if previous_budget is None:
                raise TaskRecoveryError(
                    "budgeted resume requires a task budget in the report")
            try:
                restored = TaskBudget.from_dict(previous_budget)
            except TaskBudgetError as exc:
                raise TaskRecoveryError(
                    "invalid task budget in report: %s" % exc)
            if restored.limit_tokens != budget.limit_tokens:
                raise TaskRecoveryError(
                    "task budget limit does not match the authorized spec")
            task_budget = restored
    results: List[Dict[str, Any]] = []
    changes: List[Dict[str, Any]] = []
    issues: List[Dict[str, str]] = []
    previous_ids = set()
    if previous is not None:
        results = [dict(step) for step in previous["steps"] if step["status"] == "completed"]
        changes = [dict(change) for change in previous["changes"]]
        previous_ids = {step["step_id"] for step in results}
    completed_count = len(results)
    if previous is not None and completed_count == len(execution.steps):
        return results  # A fully checked no-op must not create another successor.
    attempted = 0
    error = None
    cancelled = False
    try:
        for step in execution.steps[completed_count:]:
            if any(dependency not in previous_ids for dependency in step.depends_on):
                raise TaskExecutionError("step %r dependency order is invalid"
                                         % step.id)
            if results and any(item["status"] != "completed" and item.get("reason") != "step_boundary"
                               for item in results):
                results.append({"step_id": step.id, "status": "not_executed",
                                "reason": "previous_step_did_not_complete"})
                continue
            if cancellation is not None and cancellation.requested:
                cancelled = True
                break
            if stop_after is not None and attempted >= stop_after:
                results.append({"step_id": step.id, "action": step.action,
                                "status": "not_executed", "reason": "step_boundary"})
                previous_ids.add(step.id)
                continue
            attempted += 1
            path = Path(trace_dir) / ("%s.jsonl" % step.id)
            if step.action == "run_command":
                if command_runner is None:
                    raise TaskExecutionError(
                        "command step %r needs an explicit command_runner"
                        % step.id)
                try:
                    command = command_runner(
                        step.command_template_id, path, step.id)
                    repair_details = None
                    if model_repair == "model" and not getattr(command, "ok", False):
                        from .task_model_repair import attempt_model_repair
                        command, repair_details = attempt_model_repair(
                            command_runner, step.command_template_id, path,
                            step.id, failed=command, spec=spec, plan=plan,
                            config=config, api_key=api_key, root=root,
                            task_budget=task_budget)
                except Exception as exc:
                    results.append({
                        "step_id": step.id, "action": "run_command",
                        "run_id": step.id, "status": "failed",
                        "trace": str(path),
                        "reason": "command_runner_error: %s" % type(exc).__name__,
                    })
                    raise
                evidence = (command.to_dict()
                            if callable(getattr(command, "to_dict", None)) else {})
                evidence = dict(evidence)
                if isinstance(evidence.get("trace"), str):
                    evidence["trace"] = str(path)
                if repair_details is not None:
                    evidence["repair"] = repair_details
                results.append({
                    "step_id": step.id,
                    "action": "run_command",
                    "run_id": step.id,
                    "status": "completed" if getattr(command, "ok", False)
                    else "failed",
                    "exit_code": getattr(command, "exit_code", None),
                    "trace": str(path),
                    "evidence": evidence,
                })
                bind_step_evidence(root, results[-1])
                previous_ids.add(step.id)
                continue
            session_path = Path(session_dir) / ("%s.jsonl" % step.id)
            result = run_task(
                policy=step.policy, workspace=root, config=config,
                trace_path=path, session_path=session_path,
                prompts=[_task_state_prompt(step, results, execution, root)],
                acceptance=None,
                task_budget=task_budget)
            results.append({"step_id": step.id, "action": step.action,
                            "status": result.status,
                            "task_id": result.task_id,
                            "trace": str(path), "session": str(session_path)})
            found, problems = _changed_files(
                root, getattr(result, "record", None))
            changes.extend(found)
            issues.extend(problems)
            bind_step_evidence(root, results[-1])
            previous_ids.add(step.id)
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        report_path = _write_execution_report(
            execution, root, results, changes, issues, error, recovery,
            task_budget)
        relative_report = report_path.relative_to(root).as_posix()
        for result in results:
            result.setdefault("execution_report", relative_report)
        if isinstance(exc, TaskExecutionError):
            exc.report = relative_report
        else:
            # Preserve the runner's exception type and text for existing
            # callers. The CLI reads the report path from this note.
            try:
                exc.add_note("execution_report=%s" % relative_report)
            except AttributeError:
                pass
        raise
    # A request received during the last successful step still has a boundary.
    cancelled = cancelled or (
        cancellation is not None and cancellation.requested
        and all(result["status"] == "completed" for result in results))
    metadata = None
    if cancelled:
        from .tasking import CANCELLATION_SCHEMA, TaskError, validate_cancellation_report
        boundary = len(results)
        results.extend({"step_id": step.id, "action": step.action,
                        "status": "not_executed", "reason": "task_cancelled"}
                       for step in execution.steps[boundary:])
        metadata = {"schema_version": CANCELLATION_SCHEMA, "state": "cancelled",
                    "spec_sha256": execution.spec_sha256,
                    "plan_sha256": execution.plan_sha256,
                    "owner_sha256": cancellation.owner_sha256,
                    "boundary_index": boundary}
        try:
            cleaned = _report_steps(root, results)
            validate_cancellation_report({
                "cancellation": metadata, "spec_sha256": execution.spec_sha256,
                "plan_sha256": execution.plan_sha256,
                "steps": cleaned, "evidence_issues": issues})
            from .task_recovery import _check_completed
            for expected, result in zip(execution.steps[:boundary], cleaned[:boundary]):
                _check_completed(root, expected, result, changes)
        except (TaskError, TaskRecoveryError) as exc:
            # A nominally completed custom runner without bound artifacts is
            # not a safe cancellation/recovery boundary.
            error = {"type": "TaskExecutionError",
                     "message": "task cancellation needs confirmed step evidence"}
            path = _write_execution_report(execution, root, results, changes, issues,
                                           error, recovery, task_budget)
            raise TaskExecutionError(error["message"],
                                     report=path.relative_to(root).as_posix()) from exc
    report_path = _write_execution_report(
        execution, root, results, changes, issues, error, recovery,
        task_budget, metadata)
    relative_report = report_path.relative_to(root).as_posix()
    for result in results:
        result.setdefault("execution_report", relative_report)
    if cancelled:
        raise TaskCancelledError(report=relative_report, results=results)
    return results


def _closed(value: Dict[str, Any], allowed, what: str) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise TaskExecutionError("%s has unknown field(s): %s"
                                 % (what, ", ".join(unknown)))


def _load_execution_report(root: Path, path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TaskExecutionError("cannot read execution report %s: %s"
                                 % (path.name, exc))
    return _parse_execution_report(data, path.name)


def _parse_execution_report(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise TaskExecutionError(
            "execution report %s is not an object" % name)
    allowed = {"schema_version", "report_id", "spec_sha256", "plan_sha256",
               "task_ids", "steps", "changes", "evidence_issues", "recovery",
               "task_budget", "cancellation"}
    if "error" in data:
        allowed.add("error")
        if not isinstance(data["error"], dict):
            raise TaskExecutionError("execution report %s has an invalid error"
                                     % name)
    _closed(data, allowed, "execution report")
    if "cancellation" in data:
        from .tasking import TaskError, validate_cancellation_report
        try:
            validate_cancellation_report(data)
        except TaskError as exc:
            raise TaskExecutionError(str(exc)) from exc
    if "recovery" in data:
        link = data["recovery"]
        if (not isinstance(link, dict) or set(link) != {"report", "sha256"}
                or not isinstance(link.get("report"), str)
                or Path(link["report"]).parent != EXECUTION_REPORTS_DIR
                or Path(link["report"]).suffix != ".json" or not _sha(link.get("sha256"))):
            raise TaskExecutionError("execution report %s has an invalid recovery link" % name)
    if data.get("schema_version") != EXECUTION_REPORT_SCHEMA:
        raise TaskExecutionError("execution report %s has the wrong schema"
                                 % name)
    if "task_budget" in data:
        from .task_budget import TaskBudget, TaskBudgetError
        try:
            TaskBudget.from_dict(data["task_budget"])
        except TaskBudgetError as exc:
            raise TaskExecutionError(
                "execution report %s has an invalid task budget: %s"
                % (name, exc))
    if not _sha(data.get("spec_sha256")) or not _sha(data.get("plan_sha256")):
        raise TaskExecutionError("execution report %s has an invalid plan link"
                                 % name)
    if not isinstance(data.get("task_ids"), list) or any(
            not isinstance(item, str) or not item for item in data["task_ids"]):
        raise TaskExecutionError("execution report %s has invalid task ids"
                                 % name)
    if (not isinstance(data.get("steps"), list)
            or not isinstance(data.get("changes"), list)
            or not isinstance(data.get("evidence_issues"), list)):
        raise TaskExecutionError("execution report %s has invalid evidence"
                                 % name)
    for item in data["evidence_issues"]:
        if (not isinstance(item, dict)
                or set(item) - {"path", "state", "reason", "step_id"}
                or item.get("state") not in ("refused", "unknown")
                or not isinstance(item.get("reason"), str)
                or not item["reason"].strip()):
            raise TaskExecutionError(
                "execution report %s has an invalid evidence issue" % name)
    step_fields = {"step_id", "action", "run_id", "status", "task_id",
                   "trace", "session", "exit_code", "reason", "evidence",
                   "path_issue", "archive_sha256", "evidence_hashes"}
    for step in data["steps"]:
        if not isinstance(step, dict) or set(step) - step_fields:
            raise TaskExecutionError(
                "execution report %s has an open or malformed step" % name)
        if "evidence_hashes" in step:
            hashes = step["evidence_hashes"]
            if (not isinstance(hashes, dict)
                    or set(hashes) - {"record", "trace", "session", "archive"}
                    or any(not _sha(value) for value in hashes.values())):
                raise TaskExecutionError("execution report %s has invalid evidence hashes" % name)
        digest = step.get("archive_sha256")
        if digest not in (None, "unknown") and not _sha(digest):
            raise TaskExecutionError(
                "execution report %s has an invalid command archive digest"
                % name)
    change_fields = {"path", "original_sha256", "new_sha256", "bytes",
                     "baseline", "added_lines", "removed_lines", "diff_state"}
    for change in data["changes"]:
        if not isinstance(change, dict) or set(change) - change_fields:
            raise TaskExecutionError(
                "execution report %s has an open or malformed change"
                % name)
        path_value = change.get("path")
        bytes_value = change.get("bytes")
        if (not isinstance(path_value, str) or not path_value
                or path_value.startswith("/")
                or "\\" in path_value
                or ".." in path_value.split("/")
                or not isinstance(bytes_value, int)
                or isinstance(bytes_value, bool)
                or bytes_value < 0):
            raise TaskExecutionError(
                "execution report %s has invalid file evidence" % name)
    return data


def cumulative_reports(reports, read_bytes):
    """Return cumulative report leaves and explicit lineage issues, without IO policy."""
    from .task_recovery import TaskRecoveryError
    issues, successors = [], {}
    for relative, report in reports.items():
        link = report.get("recovery")
        if link is None:
            continue
        parent = reports.get(link["report"])
        try:
            completed = [step for step in parent["steps"] if step.get("status") == "completed"] if parent else []
            if (parent is None or link["report"] == relative
                    or _digest(read_bytes(link["report"])) != link["sha256"]
                    or parent["spec_sha256"] != report["spec_sha256"]
                    or parent["plan_sha256"] != report["plan_sha256"]
                    or parent["evidence_issues"] or "error" in parent
                    or report["steps"][:len(completed)] != completed
                    or report["changes"][:len(parent["changes"])] != parent["changes"]
                    or not set(parent["task_ids"]).issubset(report["task_ids"])):
                raise TaskRecoveryError("recovery report does not preserve its predecessor")
            successors.setdefault(link["report"], []).append(relative)
        except TaskRecoveryError as exc:
            issues.append({"path": relative, "state": "refused", "reason": str(exc)})
    superseded = set()
    for parent, children in successors.items():
        if len(children) == 1:
            superseded.add(parent)
        else:
            issues.append({"path": parent, "state": "unknown",
                           "reason": "multiple recovery successors"})
    return {key: value for key, value in reports.items() if key not in superseded}, issues


def load_task_execution(root: Path, task_id: str):
    """Return report sections linked to one task, plus evidence issues."""
    root = Path(root).resolve()
    directory = root / EXECUTION_REPORTS_DIR
    commands = []
    steps = []
    changes = []
    issues = []
    reports = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        relative = path.relative_to(root).as_posix()
        try:
            reports[relative] = _load_execution_report(root, path)
        except TaskExecutionError as exc:
            issues.append({"path": relative, "state": "refused", "reason": str(exc)})
    # A recovery report is cumulative. Hide a predecessor only when the child
    # binds its bytes and preserves its completed prefix, not merely its id.
    from .task_recovery import _read
    reports, lineage_issues = cumulative_reports(reports, lambda path: _read(root, path))
    issues.extend(lineage_issues)
    for relative, report in reports.items():
        if task_id not in report["task_ids"]:
            continue
        issues.extend(dict(item) for item in report["evidence_issues"])
        if "error" in report:
            error = report["error"]
            issues.append({
                "path": relative, "state": "unknown",
                "reason": "execution report records %s: %s"
                          % (error.get("type", "error"),
                             error.get("message", "unknown error")),
            })
        for step in report["steps"]:
            if not isinstance(step, dict) or not isinstance(step.get("step_id"), str):
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step is malformed"})
                continue
            item = dict(step)
            if (not isinstance(item.get("action"), str)
                    or item.get("action") not in ("read_file", "write_file",
                                                  "memory_search", "run_command")
                    or (item.get("action") == "run_command"
                        and item.get("status") != "not_executed"
                        and not isinstance(item.get("run_id"), str))
                    or not isinstance(item.get("status"), str)):
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step has invalid identity",
                               "step_id": item.get("step_id")})
                continue
            if item.get("path_issue"):
                issues.append({"path": relative, "state": "refused",
                               "reason": "execution step path escapes the workspace",
                               "step_id": item.get("step_id")})
            if item.get("status") == "not_executed":
                steps.append(item)
                continue
            if item.get("action") == "run_command":
                evidence = item.get("evidence")
                if not isinstance(evidence, dict):
                    issues.append({"path": relative, "state": "refused",
                                   "reason": "command evidence object is absent",
                                   "step_id": item.get("step_id")})
                    steps.append(item)
                    continue
                trace = evidence.get("trace")
                command = {
                    "step_id": item.get("step_id"),
                    "run_id": item.get("run_id"),
                    "status": item.get("status", "unknown"),
                    "template_id": evidence.get("template_id", "unknown"),
                    "cwd": evidence.get("cwd", "unknown"),
                    "exit_code": evidence.get("exit_code"),
                    "timed_out": evidence.get("timed_out"),
                    "stdout_sha256": evidence.get("stdout_sha256"),
                    "stderr_sha256": evidence.get("stderr_sha256"),
                    "stdout_truncated": evidence.get("stdout_truncated"),
                    "stderr_truncated": evidence.get("stderr_truncated"),
                    "trace": trace,
                    "archive_sha256": item.get("archive_sha256"),
                }
                if not isinstance(trace, str):
                    issues.append({"path": relative, "state": "unknown",
                                   "reason": "command evidence trace is absent",
                                   "step_id": item.get("step_id")})
                else:
                    try:
                        archive = load_command_evidence(root, trace)
                    except (CommandError, OSError) as exc:
                        issues.append({"path": trace, "state": "unknown",
                                       "reason": str(exc),
                                       "step_id": item.get("step_id")})
                    else:
                        try:
                            archive_digest = command_evidence_sha256(root, trace)
                        except CommandError as exc:
                            issues.append({"path": trace, "state": "unknown",
                                           "reason": str(exc),
                                           "step_id": item.get("step_id")})
                            steps.append(item)
                            continue
                        if (isinstance(item.get("archive_sha256"), str)
                                and item["archive_sha256"] != archive_digest):
                            issues.append({"path": trace, "state": "refused",
                                           "reason": "command evidence bytes changed",
                                           "step_id": item.get("step_id")})
                        elif archive.get("run_id") != item.get("run_id"):
                            issues.append({"path": trace, "state": "refused",
                                           "reason": "command evidence belongs to another run",
                                           "step_id": item.get("step_id")})
                        elif archive.get("template_id") != command["template_id"]:
                            issues.append({"path": trace, "state": "refused",
                                           "reason": "command template does not match its step",
                                           "step_id": item.get("step_id")})
                        else:
                            command.update({
                                "archive_sha256": archive_digest,
                                "exit_code": archive.get("exit_code"),
                                "timed_out": archive.get("timed_out"),
                                "stdout_sha256": archive.get("stdout_sha256"),
                                "stderr_sha256": archive.get("stderr_sha256"),
                                "stdout_truncated": archive.get("stdout_truncated"),
                                "stderr_truncated": archive.get("stderr_truncated"),
                                "task_acceptance": archive.get("task_acceptance", "not_evaluated"),
                            })
                            commands.append(command)
                steps.append(item)
            else:
                steps.append(item)
            if item.get("reason", "").startswith("command_runner_error"):
                issues.append({"path": relative, "state": "unknown",
                               "reason": item["reason"],
                               "step_id": item.get("step_id")})
        for change in report["changes"]:
            if not isinstance(change, dict) or not _sha(change.get("new_sha256")):
                issues.append({"path": relative, "state": "refused",
                               "reason": "file change evidence is malformed"})
                continue
            changes.append(dict(change))
    return {
        "execution_steps": steps,
        "commands": commands,
        "changes": changes,
        "evidence_issues": issues,
    }
