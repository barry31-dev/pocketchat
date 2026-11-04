"""Pure preflight over caller-supplied task and workspace observations.

This module never runs Git, opens a workspace path, starts a command or calls a
provider. It can only say whether the observations the caller supplied are
internally consistent and complete for this check. The report is not an
execution token and cannot authorize a plan.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

from .task_plan import TaskPlan, TaskPlanError, parse_plan, plan_sha256, spec_sha256
from .task_spec import TaskSpec, TaskSpecError, parse_spec
from .task_snapshot import CONTEXT_SNAPSHOT_SCHEMA, _snapshot_digest

SCHEMA_VERSION = 1
REPORT_SCHEMA = "pocketchat-task-preflight.v1"
MAX_PATH_OBSERVATIONS = 4096
MAX_REPORT_BYTES = 64 * 1024
OBSERVATION_FIELDS = frozenset({
    "workspace_id", "workspace_roots", "base_commit", "head_commit", "is_git",
    "tracked_paths", "untracked_paths", "dirty_paths", "write_paths",
    "dirty_coverage", "source_coverage", "spec_sha256", "plan_sha256",
    "snapshot_sha256",
})


def _digest(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _commit_id(value: Any) -> bool:
    """Git object IDs are distinct from the SHA-256 document digests."""
    return (isinstance(value, str) and len(value) in (40, 64)
            and all(char in "0123456789abcdef" for char in value))


def _finding(identifier: str, reason: str, detail: str) -> Dict[str, str]:
    return {"id": identifier, "reason": reason, "detail": detail}


def _path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    normalized = value.rstrip("/")
    return bool(normalized) and all(part not in ("", ".", "..") for part in normalized.split("/"))


def _paths(value: Any) -> List[str]:
    if not isinstance(value, list) or any(not _path(item) for item in value):
        raise ValueError("path observations must be a list of workspace-relative paths")
    return sorted(set(value))


def _covered(value: Any, name: str, unknown: List[Dict[str, str]]) -> bool:
    if value not in ("complete", "partial"):
        unknown.append(_finding(name, "invalid_coverage",
                                "coverage must be complete or partial"))
        return False
    if value != "complete":
        unknown.append(_finding(name, "partial_" + name,
                                "observation coverage is explicitly partial"))
        return False
    return True


def _scope_conflicts(paths: List[str]) -> Tuple[str, str]:
    lowered: Set[str] = set()
    for path in paths:
        parts = path.split("/")
        for index in range(1, len(parts)):
            ancestor = "/".join(parts[:index])
            if ancestor in paths:
                return "scope_overlap", "%s / %s" % (ancestor, path)
        if path.lower() in lowered:
            return "case_collision", path
        lowered.add(path.lower())
    return "", ""


def _canonical_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode("utf-8"))


def _report(status: str, refusals: List[Dict[str, str]],
            unknowns: List[Dict[str, str]], omissions: List[Dict[str, str]],
            path_count: int) -> Dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "refusals": sorted(refusals, key=lambda item: (item["id"], item["reason"], item["detail"])),
        "unknowns": sorted(unknowns, key=lambda item: (item["id"], item["reason"], item["detail"])),
        "omissions": sorted(omissions, key=lambda item: (item["id"], item["reason"], item["detail"])),
        "path_observations": path_count,
        "limits": {"path_observations": MAX_PATH_OBSERVATIONS,
                   "report_bytes": MAX_REPORT_BYTES},
    }


def check_task_preflight(spec: TaskSpec, plan: TaskPlan, snapshot: Any,
                         observation: Any) -> Dict[str, Any]:
    """Validate already-parsed documents and observations without I/O."""
    refusals: List[Dict[str, str]] = []
    unknowns: List[Dict[str, str]] = []
    omissions: List[Dict[str, str]] = []
    path_count = 0
    try:
        if not isinstance(spec, TaskSpec) or not isinstance(plan, TaskPlan):
            refusals.append(_finding("documents", "invalid_documents",
                                     "spec and plan must be parsed documents"))
        if not isinstance(snapshot, dict) or not isinstance(observation, dict):
            refusals.append(_finding("inputs", "invalid_inputs",
                                     "snapshot and observation must be objects"))
        else:
            unexpected = sorted(set(observation) - OBSERVATION_FIELDS)
            if unexpected:
                refusals.append(_finding("observation", "unexpected_field",
                                         "observation contains a field outside the closed set"))
        if refusals:
            return _report("refused", refusals, [], [], path_count)
        try:
            parsed_spec = parse_spec(spec.source)
            if (parsed_spec.source != spec.source
                    or parsed_spec.acceptance_path != spec.acceptance_path):
                raise TaskSpecError("spec is not a normalized parsed document")
        except (TaskSpecError, TypeError, UnicodeError):
            return _report("refused", [_finding("spec", "invalid_spec",
                           "TaskSpec no longer satisfies its parser")], [], [], path_count)
        expected_spec = spec_sha256(parsed_spec)
        if (plan.spec_sha256 != expected_spec
                or plan.source.get("spec_sha256") != expected_spec):
            return _report("refused", [_finding("plan", "plan_spec_digest_mismatch",
                           "TaskPlan is not bound to this TaskSpec")], [], [], path_count)
        try:
            parsed_plan = parse_plan(plan.source, spec=parsed_spec)
            if parsed_plan.source != plan.source:
                raise TaskPlanError("plan is not a normalized parsed document")
        except (TaskPlanError, TypeError, UnicodeError):
            return _report("refused", [_finding("plan", "invalid_plan",
                           "TaskPlan no longer satisfies its parser and grants")], [], [], path_count)
        if "is_git" in observation and not isinstance(observation["is_git"], bool):
            refusals.append(_finding("workspace", "invalid_git_state",
                                     "is_git must be a boolean observation"))
        elif observation.get("is_git") is not True:
            unknowns.append(_finding("workspace", "not_a_git_workspace",
                                     "workspace is not observed as a Git repository"))
        if not _commit_id(observation.get("base_commit")):
            unknowns.append(_finding("base_commit", "missing_base",
                                     "base commit is absent"))
        if not _commit_id(observation.get("head_commit")):
            unknowns.append(_finding("head_commit", "missing_head",
                                     "head commit is absent"))
        if (_commit_id(observation.get("base_commit"))
                and _commit_id(observation.get("head_commit"))
                and observation.get("base_commit") != observation.get("head_commit")):
            refusals.append(_finding("head", "head_drift",
                                     "base and head commit differ"))
        if not isinstance(observation.get("workspace_id"), str) or not observation["workspace_id"]:
            unknowns.append(_finding("workspace", "missing_workspace_id",
                                     "workspace identity is absent"))
        if not _covered(observation.get("dirty_coverage"), "dirty_coverage", unknowns):
            omissions.append(_finding("dirty_coverage", "incomplete_observation",
                                      "dirty path set is not complete"))
        if not _covered(observation.get("source_coverage"), "source_coverage", unknowns):
            omissions.append(_finding("source_coverage", "incomplete_observation",
                                      "source observation is not complete"))

        planned_writes = [item for step in parsed_plan.steps
                          if step["action"] == "write_file"
                          for item in step["inputs"]]
        for field in ("tracked_paths", "untracked_paths", "dirty_paths"):
            if field not in observation:
                unknowns.append(_finding(field, "missing_path_observation",
                                         "required path observation is absent"))
                omissions.append(_finding(field, "incomplete_observation",
                                          "required path observation is absent"))
        raw_paths = {
            field: observation.get(field, [])
            for field in ("tracked_paths", "untracked_paths", "dirty_paths")
        }
        raw_paths["write_paths"] = observation.get("write_paths", planned_writes)
        path_count = sum(len(value) for value in raw_paths.values()
                         if isinstance(value, list))
        if path_count > MAX_PATH_OBSERVATIONS:
            return _report("refused", [_finding("paths", "path_limit",
                           "path observations exceed %d" % MAX_PATH_OBSERVATIONS)],
                           unknowns, omissions, path_count)
        try:
            _paths(raw_paths["tracked_paths"])
            untracked = _paths(raw_paths["untracked_paths"])
            dirty = _paths(raw_paths["dirty_paths"])
            write_paths = _paths(raw_paths["write_paths"])
        except ValueError:
            refusals.append(_finding("paths", "invalid_path",
                                     "path observations must be lists of workspace-relative paths"))
            untracked = dirty = write_paths = []
        roots = observation.get("workspace_roots", [])
        if not isinstance(roots, list) or any(not isinstance(item, str) or not item.strip()
                                               for item in roots):
            refusals.append(_finding("workspace", "invalid_workspace_roots",
                                     "workspace_roots must be a list of non-empty identities"))
        elif observation.get("workspace_id") not in roots:
            refusals.append(_finding("workspace", "workspace_identity_mismatch",
                                     "observed workspace identity is not bound by the workspace roots"))
        elif len(set(roots)) != 1:
            refusals.append(_finding("workspace", "foreign_workspace_observation",
                                     "multiple workspace identities were supplied"))
        if isinstance(spec, TaskSpec):
            observed_spec = observation.get("spec_sha256")
            if not _digest(observed_spec):
                unknowns.append(_finding("spec", "missing_spec_digest",
                                         "spec digest is absent"))
            elif observed_spec != spec_sha256(spec):
                refusals.append(_finding("spec", "spec_digest_mismatch",
                                         "observed spec digest differs"))
        if isinstance(plan, TaskPlan):
            observed_plan = observation.get("plan_sha256")
            if not _digest(observed_plan):
                unknowns.append(_finding("plan", "missing_plan_digest",
                                         "plan digest is absent"))
            elif observed_plan != plan_sha256(plan):
                refusals.append(_finding("plan", "plan_digest_mismatch",
                                         "observed plan digest differs"))
        if "schema_version" not in snapshot:
            unknowns.append(_finding("snapshot", "missing_snapshot_schema",
                                     "snapshot schema is absent"))
            omissions.append(_finding("snapshot", "incomplete_observation",
                                      "snapshot schema was not supplied"))
        elif snapshot["schema_version"] != CONTEXT_SNAPSHOT_SCHEMA:
            refusals.append(_finding("snapshot", "snapshot_schema_mismatch",
                                     "snapshot does not use the context snapshot schema"))
        snapshot_digest = snapshot.get("snapshot_sha256") if isinstance(snapshot, dict) else None
        observed_snapshot = observation.get("snapshot_sha256")
        if _digest(snapshot_digest):
            try:
                computed_snapshot = _snapshot_digest(snapshot)
            except (TypeError, ValueError, UnicodeError):
                refusals.append(_finding("snapshot", "invalid_snapshot",
                                         "snapshot body cannot be canonicalized"))
            else:
                if computed_snapshot != snapshot_digest:
                    refusals.append(_finding("snapshot", "snapshot_digest_mismatch",
                                             "snapshot digest does not bind its body"))
        if not _digest(snapshot_digest):
            unknowns.append(_finding("snapshot", "missing_snapshot_digest",
                                     "snapshot digest is absent from the snapshot"))
        elif not _digest(observed_snapshot):
            unknowns.append(_finding("snapshot", "missing_snapshot_digest",
                                     "snapshot digest is absent from the observation"))
        elif observed_snapshot != snapshot_digest:
            refusals.append(_finding("snapshot", "snapshot_digest_mismatch",
                                     "snapshot digest differs"))
        declared = set(write_paths)
        reason, detail = _scope_conflicts(sorted(declared))
        if reason:
            refusals.append(_finding("write_scope", reason, detail))
        if isinstance(plan, TaskPlan) and not reason:
            planned = set(planned_writes)
            undeclared = sorted(declared - planned)
            if undeclared:
                refusals.append(_finding("write_scope", "out_of_scope_write",
                                         "an observed write path is not planned"))
            if declared < planned:
                refusals.append(_finding("write_scope", "write_observation_mismatch",
                                         "observed writes omit a planned write"))
        for label, values in (("untracked", untracked), ("dirty", dirty)):
            outside = sorted(path for path in values if path not in declared)
            if outside:
                refusals.append(_finding(label, "dirty_outside_write_scope",
                                         "dirty path is outside declared writes: %s"
                                         % outside[0]))
        if not reason and any(path not in _paths(spec.source.get("tools", {}).get("write_file", {})
                                  .get("allow_paths", [])) for path in declared):
            refusals.append(_finding("write_scope", "out_of_scope_write",
                                     "write path is outside TaskSpec allowlist"))
    except (AttributeError, KeyError, TypeError, ValueError):
        refusals.append(_finding("preflight", "invalid_observation",
                                 "input does not satisfy the preflight contract"))

    status = "refused" if refusals else ("unknown" if unknowns else "allow")
    report = _report(status, refusals, unknowns, omissions, path_count)
    if _canonical_size(report) > MAX_REPORT_BYTES:
        return _report("refused", [_finding("report", "report_limit",
                                            "report exceeds %d bytes" % MAX_REPORT_BYTES)],
                       [], [], 0)
    return report
