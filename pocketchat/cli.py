"""Command line surface.

Exit codes, matching the convention this package's siblings use:

0  the run completed
1  the run failed (provider error, or a cancelled run)
2  the command could not be understood or the configuration is unusable

The configuration is validated before any trace file is created, so a typo in
the config does not leave a half-written run behind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__
from .acceptance import (AcceptanceError, acceptance_history, evaluate_plan,
                         load_plan, verify_task)
from .agent import (AgentCommandError, AgentState, apply_command,
                    run_task, DEFAULT_REASONING)
from .branching import BranchError, create_branch_from
from .config import (CONFIG_TEMPLATE, ConfigError, ConfigWriteError,
                     load_api_key, load_config, profile_config, requires_api_key,
                     remove_profile, set_default_profile, upsert_profile)
from .doctor import diagnose, render_lines as render_doctor_lines
from .delegate import DelegateError, run_delegation
from .compare import (ComparisonManifestError, comparison_manifest_path,
                      run_comparison, write_comparison_manifest)
from .context import ContextBudgetExceeded
from .indexing import IndexingError, build_index, index_status
from .memory import (MemoryError, add_memory, list_memory, remove_memory,
                     search_memory)
from .queries import (QueryError, collect_history, diff_sessions,
                      render_diff_lines, render_history_lines,
                      render_search_lines, search_messages)
from .recovery import (RecoveryError, create_checkpoint, rewind_to_checkpoint,
                       resume_task)
from .replay import ReplayError, replay_snapshots
from .selection import SelectionError, record_selection
from .repl import echo_reply, goodbye, read_lines
from .tui import TUIUnavailable, run_tui
from .tools import (ToolError, ToolPermissionError, execute_read_file,
                    execute_write_file)
from .service import (context_preview, export_record, export_session,
                      session_overview, show_record)
from .sessions import (SessionFileError, read_header, read_messages,
                       resolve_session_argument, resolve_session_path,
                       NOT_RECORDED)
from .session import Session, SessionError, new_run_id
from .task_context import (DEFAULT_MAX_DEPTH, DEFAULT_MAX_FILES,
                           DEFAULT_MAX_PATH_CHARS, TaskContextError, scan_tree)
from .task_selection import (DEFAULT_MAX_SELECTED_FILES, TaskSelectionError,
                             load_tree as load_context_tree, select_files)
from .task_snapshot import (DEFAULT_MAX_SNAPSHOT_BYTES, TaskSnapshotError,
                            load_selection, snapshot_files)
from .task_checkpoint import (CheckpointError, create_checkpoint as create_task_checkpoint,
                              load_checkpoint, parse_checkpoint,
                              resume_from_checkpoint, write_checkpoint)
from .task_delegate import (TaskDelegateError, authorize_delegation,
                            load_delegation_request)
from .task_plan import (PlanGenerationError, TaskPlanError,
                        generate_plan as generate_task_plan,
                        load_plan as load_task_plan, plan_sha256,
                        spec_sha256)
from .task_exec import TaskExecutionError, authorize, execute_plan
from .task_command import CommandError as TaskCommandError, run_command_template
from .task_policy import TaskPolicyError, load_policy
from .task_spec import TaskSpecError, load_spec
from .tasking import (TaskError, load_record, policy_digest, task_summary)
from .workspace import (WorkspaceError, as_dict, collect_workspace,
                        render_lines)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

DEFAULT_CONFIG = "pocketchat.json"
DEFAULT_SESSIONS_DIR = "sessions"


def _config_path(value: Optional[Path]) -> Path:
    return Path(value) if value else Path.cwd() / DEFAULT_CONFIG


def _agent_config_path(value: Optional[Path]) -> Path:
    """The agent config, with a user-level newnewrouter fallback.

    Explicit paths and a local ``pocketchat.json`` keep their existing
    meaning. Only the bare agent command may fall back to the generated user
    config, so other commands never silently read a different workspace.
    """
    if value:
        return Path(value)
    local = Path.cwd() / DEFAULT_CONFIG
    if local.is_file():
        return local
    user_config = Path.home() / ".pocketchat" / "newnewrouter.json"
    if user_config.is_file():
        return user_config
    return local


def _trace_path_for(args: argparse.Namespace, run_id: str) -> Path:
    if args.trace:
        return Path(args.trace)
    return Path.cwd() / "traces" / ("%s.jsonl" % run_id)


def _session_path_for(args: argparse.Namespace) -> Optional[Path]:
    """Where the conversation file goes, if this run keeps one.

    ``--session`` is opt in. With it, ``run`` and ``chat`` keep a
    conversation file that a later invocation resumes. Without it no
    conversation file is written and the trace alone records the run.
    ``show`` and ``export`` never reach this path: they read a file the
    operator names and write nothing.
    """
    if getattr(args, "session", None) is None:
        return None
    return resolve_session_path(args.session, DEFAULT_SESSIONS_DIR)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pocketchat",
        description="A small single-user CLI chat agent with a verifiable trace.",
    )
    parser.add_argument("--version", action="version",
                        version="pocketchat %s" % __version__)
    # Bare `pocketchat` is the full-screen interactive agent; named
    # subcommands retain their own argument parsers.
    parser.add_argument("--config", type=Path, default=None,
                        help="agent configuration file (default: ./pocketchat.json)")
    parser.add_argument("--profile", default=None,
                        help="agent initial profile")
    parser.add_argument("--session", default="main",
                        help="agent initial session (default: main)")
    parser.add_argument("--reasoning", choices=("minimal", "low", "medium", "high"),
                        default=DEFAULT_REASONING,
                        help="agent reasoning effort (default: medium)")
    parser.add_argument("--context-chars", type=int, default=None,
                        dest="context_chars",
                        help="agent context character budget")
    parser.add_argument("--verbose", action="store_true",
                        help="record full response bodies for agent runs")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress agent replies and goodbye")
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser,
                   hide: Iterable[str] = ()) -> None:
        sub.add_argument("--config", type=Path, default=None,
                         help="configuration file (default: ./%s)" % DEFAULT_CONFIG)
        add_single_run_options(sub, hide)
        sub.add_argument("--verbose", action="store_true",
                         help="record response bodies in the trace, not just summaries")

    def add_single_run_options(sub: argparse.ArgumentParser,
                               hide: Iterable[str] = ()) -> None:
        """Add the options that name one run's profile and one run's trace.

        ``hide`` takes option strings to keep parsing but drop from the help.
        ``compare`` needs that: it shares this block with ``run`` and
        ``chat``, but a comparison runs several profiles and writes one trace
        per profile, so ``--profile`` and ``--trace`` name nothing here. They
        stay refused on purpose -- an unrecognized flag would exit 2 with a
        usage error that never says which command refused it, while a parsed
        and rejected one names the conflict. Hiding them from the help stops
        the command advertising an option it does not take, without changing
        how a passed one is answered.
        """
        hidden = set(hide)
        if "--profile" not in hidden:
            sub.add_argument("--profile", default=None,
                             help="profile to run (default: the config's "
                                  "default_profile)")
        else:
            sub.add_argument("--profile", default=None, help=argparse.SUPPRESS)
        if "--trace" not in hidden:
            sub.add_argument("--trace", type=Path, default=None,
                             help="trace file to write (on resume, must match the "
                                  "session trace; default: ./traces/<id>.jsonl)")
        else:
            sub.add_argument("--trace", type=Path, default=None,
                             help=argparse.SUPPRESS)

    init = subparsers.add_parser("init", help="write an annotated configuration template")
    init.add_argument("path", type=Path, nargs="?", default=None,
                      help="where to write the template (default: ./%s)" % DEFAULT_CONFIG)
    init.add_argument("--force", action="store_true",
                      help="overwrite an existing file")
    init.add_argument("--json", action="store_true", dest="as_json",
                      help="print one machine-readable JSON object")

    doctor = subparsers.add_parser(
        "doctor", help="check configuration and credential readiness without network access")
    doctor.add_argument("--config", type=Path, default=None,
                        help="configuration file (default: ./%s)" % DEFAULT_CONFIG)
    doctor.add_argument("--profile", default=None,
                        help="profile to check (default: the config's default_profile)")
    doctor.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of human-readable lines")

    run = subparsers.add_parser("run", help="run one or more prompts non-interactively")
    add_common(run)
    run.add_argument("prompts", nargs="+", help="prompts to send, in order")
    run.add_argument("--session", type=Path, default=None,
                     help="name or resume a session file; without it there is none")
    run.add_argument("--quiet", action="store_true",
                     help="print only the final status line")
    run.add_argument("--json", action="store_true", dest="as_json",
                     help="print one machine-readable JSON object")

    agent = subparsers.add_parser(
        "agent", help="interactive chat with model, session and context switches")
    add_common(agent)
    agent.add_argument("--session", default="main",
                       help="session name to start in (default: main)")
    agent.add_argument("--reasoning", choices=("minimal", "low", "medium", "high"),
                       default=DEFAULT_REASONING, dest="reasoning",
                       help="reasoning effort recorded for each run")
    agent.add_argument("--context-chars", type=int, default=None,
                       dest="context_chars",
                       help="context character budget (default: profile setting)")
    agent.add_argument("--quiet", action="store_true",
                       help="suppress replies and the goodbye line")
    agent.epilog = ("Slash commands: /model <profile>, /session <name>, "
                    "/reasoning <minimal|low|medium|high>, "
                    "/context <characters>, /status, /exit")

    chat = subparsers.add_parser("chat", help="hold a conversation interactively")
    add_common(chat)
    chat.add_argument("--session", type=Path, default=None,
                     help="name or resume a session file; without it there is none")
    chat.add_argument("--quiet", action="store_true",
                      help="print only the final status line")
    chat.add_argument("--json", action="store_true", dest="as_json",
                      help="print one machine-readable JSON object")

    show = subparsers.add_parser("show", help="print one session, then its trace")
    show.add_argument("session", type=Path,
                      help="a session file, or the trace it recorded; a bare "
                           "name is read from ./sessions")
    show.add_argument("--json", action="store_true", dest="as_json",
                      help="print one machine-readable JSON object")

    context = subparsers.add_parser(
        "context", help="preview one session's context and dry-run compaction")
    context.add_argument("session", type=Path,
                         help="a session file, or the trace it recorded")
    context.add_argument("--budget", type=int, default=None,
                         help="character budget (default: trace config)")
    context.add_argument("--keep-recent", type=int, default=2,
                         help="recent messages pinned at the end (default: 2)")
    context.add_argument("--json", action="store_true", dest="as_json",
                         help="print one JSON object instead of human lines")

    tool = subparsers.add_parser(
        "tool", help="run one explicitly permitted local tool")
    tool_sub = tool.add_subparsers(dest="tool_command", required=True)
    tool_read = tool_sub.add_parser("read", help="read one allowed UTF-8 file")
    tool_read.add_argument("path", help="workspace-relative file path")
    tool_read.add_argument("--allow", action="append", default=[],
                           dest="allowed_paths",
                           help="explicit workspace-relative allowlist entry")
    tool_read.add_argument("--max-bytes", type=int, default=65536,
                           dest="max_bytes",
                           help="maximum bytes returned (default: 65536)")
    tool_read.add_argument("--json", action="store_true", dest="as_json",
                           help="print one JSON object instead of file text")
    tool_write = tool_sub.add_parser("write", help="write one allowed UTF-8 file")
    tool_write.add_argument("path", help="workspace-relative file path")
    tool_write.add_argument("content", help="UTF-8 content to write")
    tool_write.add_argument("--allow", action="append", default=[],
                            dest="allowed_paths",
                            help="explicit workspace-relative allowlist entry")
    tool_write.add_argument("--overwrite", action="store_true",
                            help="replace an existing file")
    tool_write.add_argument("--json", action="store_true", dest="as_json",
                            help="print one JSON object instead of a summary")

    memory = subparsers.add_parser(
        "memory", help="manage explicit local memory records")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    memory_add = memory_sub.add_parser("add", help="add one explicit memory")
    memory_add.add_argument("content", help="memory text")
    memory_add.add_argument("--tag", action="append", default=[],
                            dest="tags", help="repeatable memory tag")
    memory_add.add_argument("--json", action="store_true", dest="as_json")
    memory_list = memory_sub.add_parser("list", help="list local memories")
    memory_list.add_argument("--json", action="store_true", dest="as_json")
    memory_search = memory_sub.add_parser("search", help="search memory text")
    memory_search.add_argument("text", help="literal text to find")
    memory_search.add_argument("--json", action="store_true", dest="as_json")
    memory_remove = memory_sub.add_parser("remove", help="remove one memory")
    memory_remove.add_argument("memory_id", help="memory id")
    memory_remove.add_argument("--json", action="store_true", dest="as_json")

    delegate = subparsers.add_parser(
        "delegate", help="run a bounded, serial delegation batch")
    delegate_sub = delegate.add_subparsers(dest="delegate_command", required=True)
    delegate_run = delegate_sub.add_parser("run", help="run child prompts serially")
    delegate_run.add_argument("prompts", nargs="+", help="child prompts")
    delegate_run.add_argument("--config", type=Path, default=None,
                              help="configuration file (default: ./pocketchat.json)")
    delegate_run.add_argument("--profile", default=None,
                              help="profile to use for every child")
    delegate_run.add_argument("--max-children", type=int, default=4,
                              dest="max_children",
                              help="maximum child prompts (default: 4)")
    delegate_run.add_argument("--timeout-seconds", type=int, default=120,
                              dest="timeout_seconds",
                              help="batch deadline in seconds (default: 120)")
    delegate_run.add_argument("--json", action="store_true", dest="as_json")

    export = subparsers.add_parser(
        "export", help="write a session archive or an event-only trace")
    export.add_argument("session", type=Path,
                        help="a session file, or the trace it recorded; a bare "
                             "name is read from ./sessions")
    export.add_argument("--format", choices=("jsonl", "md", "trace"), default="jsonl",
                        dest="format",
                        help="jsonl: session archive; md: conversation; trace: events only")
    export.add_argument("--out", type=Path, default=None,
                        help="output file (default: <session>.export.<format>; "
                             "trace uses .export.trace.jsonl)")
    export.add_argument("--json", action="store_true", dest="as_json",
                        help="print one machine-readable JSON object")

    branch = subparsers.add_parser(
        "branch", help="derive a branch from one completed turn of a session")
    branch.add_argument("session", type=Path,
                        help="a session file, or the trace it recorded; a bare "
                             "name is read from ./sessions")
    branch.add_argument("--from", dest="from_turn", required=True, metavar="TURN",
                        help="the completed turn (turn_NNNNNN) the branch "
                             "continues from")
    branch.add_argument("--name", type=str, default=None,
                        help="name the new branch file (default: derived from "
                             "the parent's name)")
    branch.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of a human line")

    checkpoint = subparsers.add_parser(
        "checkpoint", help="record a completed session boundary for recovery")
    checkpoint.add_argument("session", type=Path,
                            help="session file, or the trace it recorded")
    checkpoint.add_argument("--at", dest="at_turn", default=None,
                            help="completed turn to checkpoint (default: latest)")
    checkpoint.add_argument("--id", dest="checkpoint_id", default=None,
                            help="checkpoint id (default: generated)")
    checkpoint.add_argument("--json", action="store_true", dest="as_json",
                            help="print one JSON object instead of a human line")

    rewind = subparsers.add_parser(
        "rewind", help="create a new session branch from a checkpoint")
    rewind.add_argument("checkpoint", help="checkpoint id to rewind to")
    rewind.add_argument("--name", required=True,
                        help="name/path for the new session branch")
    rewind.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of a human line")

    task = subparsers.add_parser(
        "task", help="run and inspect one bounded, evidence-backed task")
    task_subparsers = task.add_subparsers(dest="task_command", required=True)

    task_spec = task_subparsers.add_parser(
        "spec", help="inspect an operator-authored TaskSpec without running it")
    spec_subparsers = task_spec.add_subparsers(dest="spec_command", required=True)
    spec_preview = spec_subparsers.add_parser(
        "preview", help="validate a TaskSpec and print its equivalent task policy")
    spec_preview.add_argument("spec", type=Path, help="TaskSpec JSON")
    spec_preview.add_argument("--json", action="store_true", dest="as_json",
                              help="print one machine-readable JSON object")

    task_context = task_subparsers.add_parser(
        "context", help="inspect bounded repository context without a model")
    context_subparsers = task_context.add_subparsers(
        dest="context_command", required=True)
    context_scan = context_subparsers.add_parser(
        "scan", help="list bounded workspace-relative file metadata")
    context_scan.add_argument("path", type=Path, nargs="?", default=Path("."),
                              help="workspace directory (default: current directory)")
    context_scan.add_argument("--include", action="append", dest="includes",
                              help="workspace-relative file or directory; repeatable")
    context_scan.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    context_scan.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    context_scan.add_argument("--max-path-chars", type=int,
                              default=DEFAULT_MAX_PATH_CHARS)
    context_scan.add_argument("--json", action="store_true", dest="as_json",
                              help="print one machine-readable JSON object")
    context_select = context_subparsers.add_parser(
        "select", help="rank frozen tree paths by exact query tokens")
    context_select.add_argument("tree", type=Path, help="context-tree JSON")
    context_select.add_argument("--query", action="append", required=True,
                                dest="queries", help="query text; repeatable")
    context_select.add_argument("--max-files", type=int,
                                default=DEFAULT_MAX_SELECTED_FILES)
    context_select.add_argument("--json", action="store_true", dest="as_json",
                                help="print one machine-readable JSON object")
    context_snapshot = context_subparsers.add_parser(
        "snapshot", help="read only selected files into a bounded UTF-8 snapshot")
    context_snapshot.add_argument("tree", type=Path, help="context-tree JSON")
    context_snapshot.add_argument("selection", type=Path, help="context-selection JSON")
    context_snapshot.add_argument("--max-bytes", type=int,
                                  default=DEFAULT_MAX_SNAPSHOT_BYTES)
    context_snapshot.add_argument("--json", action="store_true", dest="as_json",
                                  help="print one machine-readable JSON object")

    task_plan = task_subparsers.add_parser(
        "plan", help="inspect a TaskPlan without running it")
    plan_subparsers = task_plan.add_subparsers(dest="plan_command", required=True)
    plan_preview = plan_subparsers.add_parser(
        "preview", help="validate a TaskPlan against its TaskSpec")
    plan_preview.add_argument("plan", type=Path, help="TaskPlan JSON")
    plan_preview.add_argument("--spec", type=Path, required=True,
                              help="TaskSpec the plan must remain inside")
    plan_preview.add_argument("--json", action="store_true", dest="as_json",
                              help="print one machine-readable JSON object")
    plan_generate = plan_subparsers.add_parser(
        "generate", help="generate one validated TaskPlan without executing it")
    plan_generate.add_argument("--spec", type=Path, required=True,
                               help="TaskSpec JSON the plan must stay inside")
    plan_generate.add_argument("--run-id", required=True,
                               help="operator-visible generation id")
    plan_generate.add_argument("--out", type=Path, default=None,
                               help="new plain TaskPlan path (default: .pocketchat/task-plans/<run-id>.json)")
    plan_generate.add_argument("--config", type=Path, default=None,
                               help="configuration file (default: ./pocketchat.json)")
    plan_generate.add_argument("--profile", default=None,
                               help="profile name (default: config's default profile)")
    plan_generate.add_argument("--json", action="store_true", dest="as_json",
                               help="print one machine-readable JSON object")

    task_exec = task_subparsers.add_parser(
        "exec", help="execute an explicitly authorized TaskPlan")
    task_exec.add_argument("--spec", type=Path, required=True,
                           help="operator-authored TaskSpec JSON")
    task_exec.add_argument("--plan", type=Path, required=True,
                           help="TaskPlan JSON bound to the TaskSpec")
    task_exec.add_argument("--plan-sha256", required=True,
                           help="expected lowercase SHA-256 of the authorized plan")
    task_exec.add_argument("--config", type=Path, default=None,
                           help="configuration file (default: ./pocketchat.json)")
    task_exec.add_argument("--profile", default=None,
                           help="profile name (default: config's default profile)")
    task_exec.add_argument("--repair-model", action="store_true",
                           dest="repair_model",
                           help="allow one model-authored guarded repair after a failed command step")
    task_exec.add_argument("--stop-after", type=int, default=None,
                           help="write a boundary after N newly attempted steps")
    task_exec.add_argument("--resume-report", type=Path, default=None,
                           help="existing execution report to resume from")
    task_exec.add_argument("--report-sha256", default=None,
                           help="exact SHA-256 of --resume-report")
    task_exec.add_argument("--checkpoint", type=Path, default=None,
                           help="checkpoint that resolves the report to resume")
    task_exec.add_argument("--check-acceptance", type=Path, default=None,
                           help="evaluate this acceptance plan against the produced report")
    task_exec.add_argument("--json", action="store_true", dest="as_json",
                           help="print one machine-readable JSON object")

    task_checkpoint = task_subparsers.add_parser(
        "checkpoint", help="inspect a plan/command checkpoint without running it")
    checkpoint_subparsers = task_checkpoint.add_subparsers(
        dest="checkpoint_command", required=True)
    checkpoint_preview = checkpoint_subparsers.add_parser(
        "preview", help="validate a checkpoint and print its step boundary")
    checkpoint_preview.add_argument("checkpoint", type=Path,
                                    help="checkpoint JSON")
    checkpoint_preview.add_argument("--spec-sha256", default=None,
                                    help="expected TaskSpec digest")
    checkpoint_preview.add_argument("--plan-sha256", default=None,
                                    help="expected TaskPlan digest")
    checkpoint_preview.add_argument("--json", action="store_true",
                                    dest="as_json",
                                    help="print one machine-readable JSON object")
    checkpoint_create = checkpoint_subparsers.add_parser(
        "create", help="derive a checkpoint from an exact execution report")
    checkpoint_create.add_argument("--report", type=Path, required=True,
                                   help="execution report under .pocketchat/task-reports")
    checkpoint_create.add_argument("--report-sha256", required=True,
                                   help="exact SHA-256 of the execution report")
    checkpoint_create.add_argument("--checkpoint-id", required=True,
                                   help="stable checkpoint id")
    checkpoint_create.add_argument("--out", type=Path, required=True,
                                   help="new checkpoint JSON path")
    checkpoint_create.add_argument("--json", action="store_true", dest="as_json",
                                   help="print one machine-readable JSON object")
    checkpoint_resume = checkpoint_subparsers.add_parser(
        "resume", help="resolve a checkpoint into the report to resume from")
    checkpoint_resume.add_argument("checkpoint", type=Path,
                                   help="checkpoint JSON")
    checkpoint_resume.add_argument("--spec-sha256", default=None,
                                   help="expected TaskSpec digest")
    checkpoint_resume.add_argument("--plan-sha256", default=None,
                                   help="expected TaskPlan digest")
    checkpoint_resume.add_argument("--json", action="store_true",
                                   dest="as_json",
                                   help="print one machine-readable JSON object")

    task_delegate = task_subparsers.add_parser(
        "delegate", help="authorize child delegations from a parent plan")
    delegate_subparsers = task_delegate.add_subparsers(
        dest="delegate_command", required=True)
    delegate_preview = delegate_subparsers.add_parser(
        "preview", help="validate a delegation against its parent plan")
    delegate_preview.add_argument("delegation", type=Path,
                                  help="delegation request JSON")
    delegate_preview.add_argument("--plan", type=Path, required=True,
                                  help="parent TaskPlan JSON")
    delegate_preview.add_argument("--spec", type=Path, required=True,
                                  help="TaskSpec the plan must remain inside")
    delegate_preview.add_argument("--json", action="store_true",
                                  dest="as_json",
                                  help="print one machine-readable JSON object")

    task_run = task_subparsers.add_parser(
        "run", help="create and run one task under an explicit policy")
    add_common(task_run)
    task_run.add_argument("prompts", nargs="+", help="prompts to send, in order")
    task_run.add_argument("--policy", type=Path, required=True,
                          help="task policy JSON")
    task_run.add_argument("--session", type=Path, required=True,
                          help="session file to create or continue")
    task_run.add_argument("--json", action="store_true", dest="as_json",
                          help="print one machine-readable JSON object")
    task_run.add_argument("--acceptance", type=Path,
                          help="optional operator-declared acceptance plan JSON")

    task_verify = task_subparsers.add_parser(
        "verify", help="check declared artifacts of a completed task without a model")
    task_verify.add_argument("task_id")
    task_verify.add_argument("--acceptance", type=Path, required=True,
                             help="operator-declared acceptance plan JSON")
    task_verify.add_argument("--json", action="store_true", dest="as_json",
                             help="print one machine-readable JSON object")

    task_status = task_subparsers.add_parser(
        "status", help="report one task record without running it")
    task_status.add_argument("task_id")
    task_status.add_argument("--json", action="store_true", dest="as_json",
                             help="print one machine-readable JSON object")

    task_resume = task_subparsers.add_parser(
        "resume", help="resume one task from a task checkpoint")
    task_resume.add_argument("task_id")
    task_resume.add_argument("--checkpoint", required=True,
                             help="task checkpoint id")
    task_resume.add_argument("--policy", type=Path, required=True,
                             help="the same task policy JSON")
    task_resume.add_argument("--name", required=True,
                             help="name/path for the resumed session branch")
    task_resume.add_argument("--json", action="store_true", dest="as_json",
                             help="print one machine-readable JSON object")

    task_report = task_subparsers.add_parser(
        "report", help="aggregate existing task evidence without inference")
    task_report.add_argument("task_id")
    task_report.add_argument("--policy", type=Path, default=None,
                             help="optional policy JSON used to report token budgets")
    task_report.add_argument("--json", action="store_true", dest="as_json",
                             help="print one machine-readable JSON object")

    profile = subparsers.add_parser(
        "profile", help="show or manage the profiles a configuration offers")
    profile_subparsers = profile.add_subparsers(dest="profile_command", required=True)

    def add_profile_config(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--config", type=Path, default=None,
                         help="configuration file (default: ./%s)" % DEFAULT_CONFIG)
        sub.add_argument("--json", action="store_true", dest="as_json",
                         help="print one JSON object instead of a human line")

    profile_list = profile_subparsers.add_parser(
        "list", help="list the profiles, and whether each credential is set")
    add_profile_config(profile_list)

    profile_set = profile_subparsers.add_parser(
        "set", help="create or replace one profile")
    profile_set.add_argument("name", help="profile name")
    profile_set.add_argument("--base-url", required=True, dest="base_url",
                             help="OpenAI-compatible endpoint")
    profile_set.add_argument("--model", required=True,
                             help="model name sent to the endpoint")
    profile_set.add_argument("--api-key-env", required=True,
                             dest="api_key_env",
                             help="environment variable holding the credential")
    profile_set.add_argument("--timeout-seconds", type=int,
                             dest="timeout_seconds", default=None,
                             help="request timeout (default: 60)")
    profile_set.add_argument("--max-context-chars", type=int,
                             dest="max_context_chars", default=None,
                             help="context character limit (default: 24000)")
    profile_set.add_argument("--summary-prefix-chars", type=int,
                             dest="summary_prefix_chars", default=None,
                             help="summary prefix limit (default: 160)")
    profile_set.add_argument("--max-turn-tokens", type=int,
                             dest="max_turn_tokens", default=None,
                             help="maximum locally estimated/reported tokens "
                                  "for one turn (default: unbounded)")
    profile_set.add_argument("--max-session-tokens", type=int,
                             dest="max_session_tokens", default=None,
                             help="maximum reported tokens for one run "
                                  "(default: unbounded)")
    tool_read_group = profile_set.add_mutually_exclusive_group()
    tool_read_group.add_argument("--tool-read", action="store_true",
                                 dest="tool_read_enabled", default=None,
                                 help="offer the read_file tool to the model")
    tool_read_group.add_argument("--no-tool-read", action="store_false",
                                 dest="tool_read_enabled",
                                 help="withhold the read_file tool from the model")
    tool_write_group = profile_set.add_mutually_exclusive_group()
    tool_write_group.add_argument("--tool-write", action="store_true",
                                  dest="tool_write_enabled", default=None,
                                  help="offer create-only write_file to the model")
    tool_write_group.add_argument("--no-tool-write", action="store_false",
                                  dest="tool_write_enabled",
                                  help="withhold write_file from the model")
    tool_memory_group = profile_set.add_mutually_exclusive_group()
    tool_memory_group.add_argument("--tool-memory-search", action="store_true",
                                   dest="tool_memory_search_enabled",
                                   default=None,
                                   help="offer memory_search to the model")
    tool_memory_group.add_argument("--no-tool-memory-search", action="store_false",
                                   dest="tool_memory_search_enabled",
                                   help="withhold memory_search from the model")
    profile_set.add_argument("--tool-read-allow", action="append", default=None,
                             dest="tool_read_allow_paths",
                             help="workspace-relative path allowed for read_file")
    profile_set.add_argument("--tool-write-allow", action="append", default=None,
                             dest="tool_write_allow_paths",
                             help="workspace-relative path allowed for write_file")
    profile_set.add_argument("--tool-call-max-count", type=int, default=None,
                             dest="tool_call_max_count",
                             help="maximum read_file calls per turn (default: 2)")
    profile_set.add_argument("--tool-result-max-bytes", type=int, default=None,
                             dest="tool_result_max_bytes",
                             help="maximum tool result bytes (default: 16384)")
    profile_set.add_argument("--tool-memory-search-max-results", type=int,
                             default=None,
                             dest="tool_memory_search_max_results",
                             help="maximum memory search results (default: 20)")
    profile_set.add_argument("--input-price-per-million", type=float,
                             dest="input_price_per_million", default=None,
                             help="USD per million input tokens (default: unknown)")
    profile_set.add_argument("--output-price-per-million", type=float,
                             dest="output_price_per_million", default=None,
                             help="USD per million output tokens (default: unknown)")
    profile_set.add_argument("--reasoning-effort-supported", action="store_true",
                             dest="reasoning_effort_supported",
                             help="send reasoning_effort to this OpenAI-compatible endpoint")
    add_profile_config(profile_set)

    profile_use = profile_subparsers.add_parser(
        "use", help="select the default profile")
    profile_use.add_argument("name", help="existing profile name")
    add_profile_config(profile_use)

    profile_remove = profile_subparsers.add_parser(
        "remove", help="remove a non-default profile")
    profile_remove.add_argument("name", help="existing profile name")
    add_profile_config(profile_remove)

    compare = subparsers.add_parser(
        "compare", help="run one prompt against several profiles and compare")
    add_common(compare, hide=("--profile", "--trace"))
    compare.add_argument("prompt", help="the prompt every profile is sent")
    compare.add_argument("--profiles", required=True,
                         help="comma-separated profile names, run in this order")
    compare.add_argument("--session", type=Path, default=None,
                         help="name a session file to seed the context from; "
                              "it is read, never written")
    compare.add_argument("--quiet", action="store_true",
                         help="print only the manifest path")
    compare.add_argument("--json", action="store_true", dest="as_json",
                         help="print one JSON object instead of one line per profile")

    replay = subparsers.add_parser(
        "replay", help="verify saved requests without sending them")
    replay.add_argument("run_or_request",
                        help="a run id or an explicit request snapshot path")
    replay.add_argument("--dry-run", action="store_true", required=True,
                        help="verify hashes without contacting a provider")
    replay.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of one line per request")

    workspace = subparsers.add_parser(
        "workspace", help="rebuild the workspace's relationships from its files")
    workspace.add_argument("path", type=Path, nargs="?", default=None,
                           help="workspace directory to read (default: the "
                                "current directory)")
    workspace.add_argument("--json", action="store_true", dest="as_json",
                           help="print one JSON object instead of a tree")

    history = subparsers.add_parser(
        "history", help="list the turns the sessions in this workspace record")
    history.add_argument("target", nargs="?", default=None,
                         help="a session file, a conversation id or a branch "
                              "id (default: every conversation in this "
                              "workspace)")
    history.add_argument("--json", action="store_true", dest="as_json",
                         help="print one JSON object instead of a tree")

    search = subparsers.add_parser(
        "search", help="find literal text in the messages the sessions record")
    search.add_argument("text",
                        help="the exact text to look for (case-sensitive)")
    search.add_argument("--session", type=Path, default=None,
                        help="search one session only; a bare name is read "
                             "from ./sessions")
    search.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of one line per hit")

    diff = subparsers.add_parser(
        "diff", help="compare two sessions turn by turn")
    diff.add_argument("session_a", type=Path,
                      help="one session file; a bare name is read from "
                           "./sessions")
    diff.add_argument("session_b", type=Path,
                      help="the other session file")
    diff.add_argument("--json", action="store_true", dest="as_json",
                      help="print one JSON object instead of a summary")

    select = subparsers.add_parser(
        "select", help="record which answer of a comparison was kept")
    select.add_argument("comparison",
                        help="a comparison id or manifest path")
    select.add_argument("run", metavar="RUN_OR_PROFILE",
                        help="the run id, or an unambiguous profile name, "
                             "whose answer is kept")
    select.add_argument("--note", default=None,
                        help="why this one (recorded, optional)")
    select.add_argument("--into", type=Path, default=None,
                        help="append the selected exchange to this session "
                             "(a new one is started if absent; requires the "
                             "comparison to have run with --verbose)")
    select.add_argument("--json", action="store_true", dest="as_json",
                        help="print one JSON object instead of a summary")

    index = subparsers.add_parser(
        "index", help="rebuild or inspect the derived workspace index")
    index.add_argument("path", type=Path, nargs="?", default=None,
                       help="workspace directory (default: the current "
                            "directory)")
    index.add_argument("--rebuild", action="store_true",
                       help="replace an existing index (the build is always "
                            "whole; there is no incremental update)")
    index.add_argument("--json", action="store_true", dest="as_json",
                       help="print one JSON object instead of a report")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command is None:
        args.command = "agent"
        args.as_json = False
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "chat":
        return _cmd_chat(args)
    if args.command == "agent":
        return _cmd_agent(args)
    if args.command == "show":
        return _cmd_show(args)
    if args.command == "context":
        return _cmd_context(args)
    if args.command == "tool":
        return _cmd_tool(args)
    if args.command == "memory":
        return _cmd_memory(args)
    if args.command == "delegate":
        return _cmd_delegate(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "branch":
        return _cmd_branch(args)
    if args.command == "checkpoint":
        return _cmd_checkpoint(args)
    if args.command == "rewind":
        return _cmd_rewind(args)
    if args.command == "task":
        return _cmd_task(args)
    if args.command == "profile":
        return _cmd_profile(args)
    if args.command == "compare":
        return _cmd_compare(args)
    if args.command == "replay":
        return _cmd_replay(args)
    if args.command == "workspace":
        return _cmd_workspace(args)
    if args.command == "history":
        return _cmd_history(args)
    if args.command == "search":
        return _cmd_search(args)
    if args.command == "diff":
        return _cmd_diff(args)
    if args.command == "select":
        return _cmd_select(args)
    if args.command == "index":
        return _cmd_index(args)
    print("pocketchat: unknown command %r" % args.command, file=sys.stderr)
    return EXIT_USAGE


def _json_path(value: Path, start: Path = None) -> str:
    value = Path(value)
    start = Path.cwd() if start is None else Path(start)
    try:
        return Path(os.path.relpath(str(value), str(start))).as_posix()
    except ValueError:
        return value.as_posix()


def _print_json_error(code: str, message: str) -> None:
    print(json.dumps({
        "ok": False, "schema_version": "1",
        "error": code, "message": message,
    }, sort_keys=True))


def _prepare_error(message: str, args: argparse.Namespace,
                   code: str) -> int:
    if getattr(args, "as_json", False):
        _print_json_error(code, message)
    else:
        print("pocketchat: %s" % message, file=sys.stderr)
    return EXIT_USAGE


def _execution_record(status: str, session: Session,
                      trace_path: Path) -> Dict[str, Any]:
    reply = (session.last_reply()
             if status == "completed" and session.turn_count else None)
    payload = {
        "ok": status == "completed",
        "schema_version": "1",
        "status": status,
        "run_id": session.run_id,
        "turns": session.turn_count,
        "trace": _json_path(trace_path),
        "reply": reply,
    }
    if status != "completed":
        payload["error"] = "run_%s" % status
    return payload


def _branch_record(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "ok": True,
        "schema_version": "1",
        "conversation_id": result["conversation_id"],
        "branch_id": result["branch_id"],
        "parent_branch_id": result["parent_branch_id"],
        "parent_turn_id": result["parent_turn_id"],
        "turns": result["turns"],
        "session": _json_path(Path(result["session"])),
        "source": _json_path(Path(result["source"])),
        "run_id": result["run_id"],
        "trace": _json_path(Path(result["trace"])),
        "messages_written": result["messages_written"],
    }


def _cmd_init(args: argparse.Namespace) -> int:
    target = args.path or Path.cwd() / DEFAULT_CONFIG
    if target.exists() and not args.force:
        if args.as_json:
            _print_json_error(
                "config",
                "%s already exists (use --force to overwrite)"
                % _json_path(target))
            return EXIT_USAGE
        print("pocketchat: %s already exists (use --force to overwrite)" % target,
              file=sys.stderr)
        return EXIT_USAGE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        if args.as_json:
            _print_json_error("config", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps({
            "ok": True, "schema_version": "1",
            "path": _json_path(target), "created": not args.force,
        }, sort_keys=True))
        return EXIT_OK
    print("wrote %s" % target)
    print("set %s before running a real provider" % "POCKETCHAT_API_KEY")
    return EXIT_OK


def _cmd_branch(args: argparse.Namespace) -> int:
    """Create the session file a branch continues, and name its links.

    A branch is a second path through one conversation, so the only thing it
    records beyond the parent's own is *where it left*: the branch it came
    from, and the turn it came in at. Everything else -- the conversation id,
    the inherited messages -- is copied, which makes the child a real session
    that `run --session <name>` continues rather than a note about one.

    The parent is never written to. Writing the links there would force a
    second branch's file into the one conversation that owns it, and the
    append-only promise -- a session keeps its own history -- is worth more
    than the saved bytes.

    The turn id must name an exchange that finished. Forking from an
    unanswered user line would make the child start from a question the
    parent asked and never sent.
    """
    try:
        source = resolve_session_argument(args.session, DEFAULT_SESSIONS_DIR)
    except SessionFileError as exc:
        if args.as_json:
            _print_json_error("branch", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    try:
        result = create_branch_from(source, args.from_turn, args.name,
                                    DEFAULT_SESSIONS_DIR)
    except BranchError as exc:
        if args.as_json:
            _print_json_error("branch", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps(_branch_record(result), sort_keys=True))
        return EXIT_OK

    print("branch %s  from %s  at %s  (turns 1-%d)"
          % (result["branch_id"], result["parent_branch_id"] or NOT_RECORDED,
             result["parent_turn_id"], result["turns"]))
    print("conversation %s  session %s" % (result["conversation_id"],
                                           result["session"]))
    return EXIT_OK


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    try:
        record = create_checkpoint(Path.cwd(), args.session, args.at_turn,
                                   args.checkpoint_id)
    except RecoveryError as exc:
        if args.as_json:
            _print_json_error("recovery", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps({**record, "ok": True}, sort_keys=True))
        return EXIT_OK
    print("checkpoint %s  at %s  (%d messages)"
          % (record["checkpoint_id"], record["turn_id"], record["messages"]))
    print("manifest %s" % record["manifest"])
    return EXIT_OK


def _cmd_rewind(args: argparse.Namespace) -> int:
    try:
        record = rewind_to_checkpoint(Path.cwd(), args.checkpoint, args.name)
    except (RecoveryError, OSError) as exc:
        if args.as_json:
            _print_json_error("recovery", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(record, sort_keys=True))
        return EXIT_OK
    print("rewind %s  to %s  at %s  (%d messages)"
          % (record["rewind_id"], record["checkpoint_id"],
             record["parent_turn_id"], record["messages_written"]))
    print("session %s" % record["target_session"])
    return EXIT_OK


def _task_trace_evidence(
        root: Path,
        run_ids: List[str]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Find trace files by parsing each JSONL line, not by substring.

    A recorded run is linked only when an event's own ``run_id`` matches it.
    Searching serialized JSON as a substring turns a different run into a
    link. A malformed or unreadable trace is reported with a state instead of
    disappearing into an empty result that looks like "there was no trace".
    """
    linked: List[str] = []
    issues: List[Dict[str, str]] = []
    matched_run_ids = set()
    traces = root / "traces"
    for path in sorted(traces.glob("*.jsonl")) if traces.is_dir() else []:
        relative = _json_path(path, root)
        file_run_ids = set()
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError):
                        issues.append({
                            "path": relative,
                            "state": "refused",
                            "reason": "invalid JSON line %d" % line_number,
                        })
                        break
                    if not isinstance(event, dict):
                        issues.append({
                            "path": relative,
                            "state": "refused",
                            "reason": "event line %d is not an object"
                                      % line_number,
                        })
                        break
                    if event.get("run_id") in run_ids:
                        file_run_ids.add(event["run_id"])
                        matched_run_ids.add(event["run_id"])
        except OSError as exc:
            issues.append({
                "path": relative,
                "state": "unknown",
                "reason": "cannot read trace: %s" % exc,
            })
            continue
        if file_run_ids:
            linked.append(relative)
    for run_id in run_ids:
        if run_id not in matched_run_ids:
            issues.append({
                "path": "traces",
                "state": "unknown",
                "reason": "no trace file for recorded run %s" % run_id,
            })
    return linked, issues


def _task_report_payload(root: Path, record, policy=None) -> Dict[str, Any]:
    """Aggregate only evidence already present in the workspace."""
    summary = task_summary(record)
    steps = list(record.steps)
    tools = [{
        "name": step.get("tool"),
        "state": step.get("state"),
        "detail": step.get("detail"),
        "run_id": step.get("run_id"),
    } for step in steps if step.get("kind") == "tool"]
    checkpoints = [{
        "checkpoint_id": step.get("checkpoint_id"),
        "step_id": step.get("index"),
        "state": step.get("state"),
    } for step in steps if step.get("kind") == "checkpoint"]
    budget = {
        "max_steps": record.max_steps,
        "max_tool_calls": record.max_tool_calls,
        "max_context_chars": "unknown",
        "max_turn_tokens": "unknown",
        "max_session_tokens": "unknown",
    }
    if policy is not None:
        budget.update({
            "max_context_chars": policy.max_context_chars,
            "max_turn_tokens": policy.max_turn_tokens,
            "max_session_tokens": policy.max_session_tokens,
        })
    trace_paths, evidence_issues = _task_trace_evidence(
        root, summary["runs"])
    from .task_exec import load_task_execution
    execution = load_task_execution(root, record.task_id)
    evidence_issues.extend(execution["evidence_issues"])
    artifacts = [{
        "tool": step.get("tool"),
        "path": step.get("detail"),
        "run_id": step.get("run_id"),
    } for step in tools
        if step.get("state") == "completed"
        and step.get("tool") in ("read_file", "write_file")
        and step.get("detail")]
    try:
        acceptance = acceptance_history(root, record.task_id)
    except AcceptanceError:
        # An unavailable optional verifier must not remove the legacy execution
        # report (for example on a platform without no-follow dir-fd APIs).
        acceptance = {"state": "unknown", "records": [], "evidence_issues": [{
            "record": ".pocketchat/acceptance/%s" % record.task_id,
            "state": "unknown", "reason": "acceptance_unavailable"}]}
    return {
        "ok": True,
        "schema_version": "1",
        "task_id": record.task_id,
        "state": record.state,
        "report": summary,
        "step_count": len(steps),
        "steps": steps,
        "tools": tools,
        "budget": budget,
        "checkpoints": checkpoints,
        "runs": summary["runs"],
        "trace": trace_paths,
        "evidence_issues": evidence_issues,
        "artifacts": artifacts,
        "execution_steps": execution["execution_steps"],
        "commands": execution["commands"],
        "changes": execution["changes"],
        "acceptance": acceptance,
    }


def _cmd_task(args: argparse.Namespace) -> int:
    if args.task_command == "context":
        try:
            if args.context_command == "scan":
                result = scan_tree(
                    args.path, includes=args.includes, max_files=args.max_files,
                    max_depth=args.max_depth, max_path_chars=args.max_path_chars)
            elif args.context_command == "select":
                result = select_files(
                    load_context_tree(args.tree), args.queries,
                    max_files=args.max_files)
            else:
                tree = load_context_tree(args.tree)
                result = snapshot_files(
                    Path("."), tree, load_selection(args.selection, tree),
                    max_bytes=args.max_bytes)
        except (TaskContextError, TaskSelectionError, TaskSnapshotError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        if args.as_json:
            print(json.dumps({"ok": True, **result}, sort_keys=True))
            return EXIT_OK
        if args.context_command == "scan":
            usage = result["usage"]
            print("context tree %s  files=%d omitted=%d path_chars=%d"
                  % (result["workspace"], usage["files"], usage["omitted"],
                     usage["path_chars"]))
            for item in result["files"]:
                print("%8d  %s" % (item["bytes"], item["path"]))
            for item in result["omitted"]:
                print("omitted  %s  %s" % (item["path"], item["reason"]))
        elif args.context_command == "select":
            print("context selection %d/%d  truncated=%s  tree=%s"
                  % (len(result["matches"]), result["total_matches"],
                     "yes" if result["truncated"] else "no",
                     result["tree_sha256"][:12]))
            for item in result["matches"]:
                print("score=%d  bytes=%d  %s  [%s]"
                      % (item["score"], item["bytes"], item["path"],
                         ",".join(item["matched_terms"])))
            if result["unmatched_terms"]:
                print("unmatched %s" % ",".join(result["unmatched_terms"]))
        else:
            print("context snapshot %d files  bytes=%d  size_changed=%d  sha256=%s"
                  % (result["selected_files"], result["total_bytes"],
                     result["selection_size_changed_files"],
                     result["snapshot_sha256"][:12]))
            for item in result["files"]:
                print("%8d  %s  sha256=%s"
                      % (item["bytes"], item["path"], item["sha256"][:12]))
        return EXIT_OK

    if args.task_command == "spec":
        try:
            spec = load_spec(args.spec)
            policy = spec.to_policy()
        except (TaskSpecError, TaskPolicyError, OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {
            "ok": True,
            "schema_version": "1",
            "spec_schema": spec.source["schema_version"],
            "acceptance": spec.acceptance_path,
            "policy": policy.source,
        }
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("TaskSpec %s" % spec.source["schema_version"])
            print("policy %s" % policy.schema_version)
            print("objective %s" % policy.objective)
        return EXIT_OK

    if args.task_command == "plan":
        if args.plan_command == "generate":
            try:
                spec = load_spec(args.spec)
                requests_dir = Path.cwd() / "traces" / "requests"
                default_plan = (Path.cwd() / ".pocketchat" / "task-plans" /
                                ("%s.json" % args.run_id))
                out_path = args.out or default_plan
                if not out_path.is_absolute():
                    out_path = Path.cwd() / out_path
                try:
                    out_path.resolve().relative_to(Path.cwd().resolve())
                except (OSError, ValueError) as exc:
                    raise PlanGenerationError(
                        "generated plan path escapes the workspace: %s" % exc,
                        attempts=0, error_type="path_escape")
                config = load_config(_config_path(args.config))
                selected = profile_config(config, args.profile)
                api_key = ""
                if requires_api_key(selected):
                    api_key = load_api_key(selected)
                    if api_key is None:
                        raise ConfigError(
                            "%s is not set" % selected["api_key_env"])
                adapter = selected.get("provider_adapter")
                if adapter is None:
                    from .provider import DEFAULT_PROVIDER_ADAPTER
                    adapter = DEFAULT_PROVIDER_ADAPTER
                generation = generate_task_plan(
                    spec, adapter, selected,
                    requests_dir=requests_dir, run_id=args.run_id,
                    api_key=api_key, out_path=out_path)
                digest = generation.plan_sha256
                relative_plan = Path(generation.plan_file).relative_to(Path.cwd()).as_posix()
                relative_preview = Path(generation.plan_snapshot).relative_to(Path.cwd()).as_posix()
                request_paths = [path.relative_to(Path.cwd()).as_posix()
                                 for path in generation.request_snapshots]
            except ConfigError as exc:
                if args.as_json:
                    _print_json_error("task", str(exc))
                else:
                    print("pocketchat: %s" % exc, file=sys.stderr)
                return EXIT_USAGE
            except PlanGenerationError as exc:
                payload = {"ok": False, "schema_version": "1",
                           "error": exc.error_type, "message": str(exc),
                           "attempts": exc.attempts}
                if args.as_json:
                    print(json.dumps(payload, sort_keys=True))
                else:
                    print("pocketchat: %s" % exc, file=sys.stderr)
                run_errors = {
                    "provider_failed", "plan_generation_failed",
                    "plan_evidence_write_failed", "plan_write_failed",
                    "snapshot_changed",
                }
                return (EXIT_RUN_FAILED if exc.error_type in run_errors
                        else EXIT_USAGE)
            except (TaskPlanError, TaskSpecError, TaskPolicyError,
                    OSError, ValueError) as exc:
                if args.as_json:
                    _print_json_error("task", str(exc))
                else:
                    print("pocketchat: %s" % exc, file=sys.stderr)
                return EXIT_USAGE
            payload = {
                "ok": True,
                "schema_version": "1",
                "status": "generated",
                "run_id": args.run_id,
                "attempts": generation.attempts,
                "spec_sha256": spec_sha256(spec),
                "plan_sha256": digest,
                "plan": relative_plan,
                "plan_document": generation.plan.source,
                "plan_snapshot": generation.plan_snapshot.relative_to(Path.cwd()).as_posix(),
                "preview_snapshot": relative_preview,
                "request_snapshots": request_paths,
            }
            if args.as_json:
                print(json.dumps(payload, sort_keys=True))
            else:
                print("TaskPlan generated  spec=%s  plan=%s"
                      % (payload["spec_sha256"][:12], digest[:12]))
                print("plan %s" % relative_plan)
                print("steps %d  attempts %d"
                      % (len(generation.plan.steps), generation.attempts))
            return EXIT_OK
        try:
            spec = load_spec(args.spec)
            plan = load_task_plan(args.plan, spec=spec)
        except (TaskPlanError, TaskSpecError, TaskPolicyError, OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {
            "ok": True,
            "schema_version": "1",
            "plan": plan.source,
            "spec_sha256": plan.spec_sha256,
        }
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("TaskPlan %s" % plan.source["schema_version"])
            print("spec_sha256 %s" % plan.spec_sha256)
            print("steps %d" % len(plan.steps))
        return EXIT_OK

    if args.task_command == "checkpoint" and args.checkpoint_command == "create":
        try:
            root = Path.cwd().resolve()
            report_path = args.report.resolve()
            relative_report = report_path.relative_to(root)
            if relative_report.parent != Path(".pocketchat/task-reports"):
                raise CheckpointError(
                    "report must be directly under .pocketchat/task-reports")
            if not report_path.is_file() or report_path.is_symlink():
                raise CheckpointError("report must be a regular file")
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError) as exc:
                raise CheckpointError(
                    "cannot read execution report: %s" % exc)
            record = create_task_checkpoint(
                payload, report_path=report_path,
                checkpoint_id=args.checkpoint_id,
                expected_report_sha256=args.report_sha256)
            out = args.out
            if not out.is_absolute():
                out = root / out
            try:
                relative_out = out.resolve().relative_to(root)
            except (OSError, ValueError) as exc:
                raise CheckpointError(
                    "checkpoint output escapes the workspace: %s" % exc)
            if relative_out == relative_report:
                raise CheckpointError(
                    "checkpoint output cannot replace its execution report")
            write_checkpoint(out, record)
            loaded = load_checkpoint(out)
        except (CheckpointError, OSError, ValueError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        result = {
            "ok": True,
            "schema_version": "1",
            "status": "created",
            "checkpoint": relative_out.as_posix(),
            "checkpoint_id": loaded["checkpoint_id"],
            "report_id": loaded["report_id"],
            "report": relative_report.as_posix(),
            "report_sha256": loaded["report_sha256"],
            "completed_step_ids": loaded["completed_step_ids"],
            "pending_step_ids": loaded["pending_step_ids"],
            "boundary_index": loaded["boundary_index"],
        }
        if args.as_json:
            print(json.dumps(result, sort_keys=True))
        else:
            print("checkpoint %s  report=%s"
                  % (loaded["checkpoint_id"], loaded["report_id"]))
            print("path %s" % relative_out.as_posix())
            print("boundary %d  completed=%s  pending=%s"
                  % (loaded["boundary_index"],
                     ",".join(loaded["completed_step_ids"]) or "(none)",
                     ",".join(loaded["pending_step_ids"]) or "(none)"))
        return EXIT_OK

    if args.task_command == "checkpoint" and args.checkpoint_command == "preview":
        try:
            result = load_checkpoint(args.checkpoint)
            result = parse_checkpoint(
                result,
                expected_spec_sha256=args.spec_sha256,
                expected_plan_sha256=args.plan_sha256)
        except CheckpointError as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {
            "ok": True,
            "schema_version": "1",
            "checkpoint": result,
        }
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("checkpoint %s  report=%s" % (
                result["checkpoint_id"], result["report_id"]))
            print("boundary %d/%d" % (result["boundary_index"],
                                      len(result["completed_step_ids"])
                                      + len(result["pending_step_ids"])))
            print("completed %s" % ",".join(result["completed_step_ids"]))
            print("pending %s" % ",".join(result["pending_step_ids"]))
            print("files %d  commands %d" % (len(result["file_effects"]),
                                             len(result["command_effects"])))
        return EXIT_OK

    if args.task_command == "delegate":
        try:
            spec = load_spec(args.spec)
            plan = load_task_plan(args.plan, spec=spec)
            execution = authorize(plan, spec,
                                  expected_plan_sha256=plan_sha256(plan))
            result = authorize_delegation(
                load_delegation_request(args.delegation), execution, spec)
        except (TaskSpecError, TaskPlanError, TaskPolicyError, TaskDelegateError,
                OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {"ok": True, "schema_version": "1", **result}
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("delegation %s  step=%s  children=%d"
                  % (result["delegation_id"], result["parent_step_id"],
                     len(result["children"])))
            for row in result["children"]:
                print("%s  %s  %s" % (row["child_id"], row["action"],
                                      ",".join(row["inputs"])))
        return EXIT_OK

    if args.task_command == "checkpoint" and args.checkpoint_command == "resume":
        try:
            result = resume_from_checkpoint(
                Path.cwd(), args.checkpoint,
                expected_spec_sha256=args.spec_sha256,
                expected_plan_sha256=args.plan_sha256)
        except CheckpointError as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {"ok": True, "schema_version": "1", **result}
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("resume %s  report=%s" % (result["checkpoint_id"],
                                            result["report_path"]))
            print("boundary %d  pending=%s"
                  % (result["boundary_index"],
                     ",".join(result["pending_step_ids"]) or "(none)"))
        return EXIT_OK

    if args.task_command == "exec":
        try:
            spec = load_spec(args.spec)
            plan = load_task_plan(args.plan, spec=spec)
            execution = authorize(plan, spec,
                                  expected_plan_sha256=args.plan_sha256)
            acceptance_plan = (load_plan(args.check_acceptance)
                               if args.check_acceptance else None)
            uses_report = args.resume_report is not None
            uses_checkpoint = args.checkpoint is not None
            if uses_report and uses_checkpoint:
                raise TaskExecutionError(
                    "--resume-report and --checkpoint are mutually exclusive")
            if uses_report != (args.report_sha256 is not None):
                raise TaskExecutionError(
                    "--resume-report and --report-sha256 must be used together")
            if uses_checkpoint and args.report_sha256 is not None:
                raise TaskExecutionError(
                    "--report-sha256 belongs to --resume-report, not --checkpoint")
            if args.stop_after is not None and (uses_report or uses_checkpoint):
                raise TaskExecutionError(
                    "--stop-after cannot be combined with a resume source")
            resume_report = None
            expected_report_sha256 = None
            root = Path.cwd().resolve()
            if uses_report:
                try:
                    resume_report = args.resume_report.resolve().relative_to(root)
                except (OSError, ValueError) as exc:
                    raise TaskExecutionError(
                        "resume report escapes the workspace: %s" % exc)
                expected_report_sha256 = args.report_sha256
            elif uses_checkpoint:
                resolution = resume_from_checkpoint(
                    Path.cwd(), args.checkpoint,
                    expected_spec_sha256=execution.spec_sha256,
                    expected_plan_sha256=execution.plan_sha256)
                resume_report = Path(resolution["report_path"])
                expected_report_sha256 = resolution["expected_report_sha256"]
            # Authorization is complete before configuration or credentials
            # are read.
            config = load_config(_config_path(args.config))
            selected = profile_config(config, args.profile)
            api_key = ""
            if args.repair_model and requires_api_key(selected):
                api_key = load_api_key(selected)
                if api_key is None:
                    raise ConfigError("%s is not set" % selected["api_key_env"])
            template_by_id = {item["id"]: item for item in spec.commands}

            def command_runner(template_id, trace_path, run_id):
                return run_command_template(
                    Path.cwd(), template_by_id[template_id],
                    trace_path=trace_path, run_id=run_id)

            results = execute_plan(
                execution, run_task=run_task, workspace=Path.cwd(),
                config=selected, trace_dir=Path.cwd() / "traces",
                session_dir=Path.cwd() / DEFAULT_SESSIONS_DIR,
                command_runner=command_runner,
                model_repair="model" if args.repair_model else None,
                spec=spec, plan=plan, api_key=api_key,
                stop_after=args.stop_after,
                resume_report=resume_report,
                expected_report_sha256=expected_report_sha256,
            )
        except (AcceptanceError, TaskError, ConfigError, TaskPolicyError,
                TaskSpecError, TaskPlanError, TaskExecutionError,
                TaskCommandError, SessionError, OSError, ValueError) as exc:
            report = getattr(exc, "report", None)
            if report is None:
                for note in getattr(exc, "__notes__", ()):
                    if isinstance(note, str) and note.startswith("execution_report="):
                        report = note.split("=", 1)[1]
                        break
            if args.as_json:
                payload = {
                    "ok": False, "schema_version": "1",
                    "error": "task", "message": str(exc),
                }
                if report:
                    payload["reports"] = [report]
                print(json.dumps(payload, sort_keys=True))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
                if report:
                    print("execution report %s" % report, file=sys.stderr)
            if report:
                return EXIT_RUN_FAILED
            if isinstance(exc, SessionError):
                return EXIT_RUN_FAILED
            return EXIT_USAGE
        task_ids = sorted({item.get("task_id") for item in results
                           if isinstance(item.get("task_id"), str)})
        reports = sorted({
            item["execution_report"] for item in results
            if isinstance(item.get("execution_report"), str)
        })
        # A fully completed source is intentionally a no-op: the executor
        # returns its verified results without creating a successor report.
        # Name the source as the operative report rather than emitting [].
        if not reports and (uses_report or uses_checkpoint):
            reports = [Path(resume_report).as_posix()]
        boundary = any(item.get("status") == "not_executed"
                       and item.get("reason") == "step_boundary"
                       for item in results)
        failed = any(item.get("status") == "failed" for item in results)
        status = "failed" if failed else ("boundary" if boundary else "completed")
        acceptance_result = None
        if acceptance_plan is not None:
            if failed or boundary:
                acceptance_result = {
                    "state": "not_evaluated",
                    "reason": ("execution_failed" if failed
                               else "execution_incomplete"),
                    "spec_sha256": execution.spec_sha256,
                    "plan_sha256": execution.plan_sha256,
                }
            else:
                try:
                    report_path = Path.cwd() / reports[-1]
                    execution_report_sha256 = hashlib.sha256(
                        report_path.read_bytes()).hexdigest()
                    evaluated = evaluate_plan(Path.cwd(), acceptance_plan)
                except (AcceptanceError, OSError) as exc:
                    acceptance_result = {
                        "state": "unknown", "reason": "acceptance_unavailable",
                        "message": str(exc),
                        "spec_sha256": execution.spec_sha256,
                        "plan_sha256": execution.plan_sha256,
                    }
                else:
                    acceptance_result = {
                        **evaluated,
                        "spec_sha256": execution.spec_sha256,
                        "plan_sha256": execution.plan_sha256,
                        "execution_report_sha256": execution_report_sha256,
                    }
        ok = not failed
        if acceptance_result is not None and status == "completed":
            ok = acceptance_result["state"] == "passed"
        payload = {
            "ok": ok,
            "status": status,
            "schema_version": "1",
            "spec_sha256": execution.spec_sha256,
            "plan_sha256": execution.plan_sha256,
            "task_ids": task_ids,
            "steps": results,
            "reports": reports,
        }
        if uses_report or uses_checkpoint:
            payload["resumed_from_report"] = Path(resume_report).as_posix()
            payload["resumed_from_report_sha256"] = expected_report_sha256
        if acceptance_result is not None:
            payload["acceptance"] = acceptance_result
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("task exec  spec=%s  plan=%s  status=%s"
                  % (execution.spec_sha256[:12], execution.plan_sha256[:12],
                     status))
            for item in results:
                print("%s  %s  %s" % (item.get("step_id"), item.get("action"),
                                      item.get("status")))
            print("reports %s" % (", ".join(reports) or "(none)"))
            if acceptance_result is not None:
                print("acceptance %s" % acceptance_result["state"])
                if acceptance_result.get("reason"):
                    print("acceptance reason %s" % acceptance_result["reason"])
        return EXIT_OK if payload["ok"] else EXIT_RUN_FAILED

    if args.task_command == "run":
        try:
            acceptance = load_plan(args.acceptance) if args.acceptance else None
            policy = load_policy(args.policy)
            config = load_config(_config_path(args.config))
            selected = profile_config(config, args.profile)
            result = run_task(
                policy=policy, workspace=Path.cwd(), config=selected,
                trace_path=Path(args.trace) if args.trace else Path("traces") /
                ("%s.jsonl" % args.session.stem),
                session_path=resolve_session_path(
                    args.session, DEFAULT_SESSIONS_DIR),
                prompts=args.prompts, acceptance=acceptance)
        except (AcceptanceError, TaskError, ConfigError, TaskPolicyError, RecoveryError,
                SessionError, OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE if not isinstance(exc, SessionError) else EXIT_RUN_FAILED
        accepted = result.acceptance is None or result.acceptance["state"] == "passed"
        ok = result.status == "completed" and accepted
        payload = {
            "ok": ok,
            "schema_version": "1",
            "task_id": result.task_id,
            "status": result.status,
            "run_id": result.run_id,
            "report": result.report,
            "record": _json_path(Path.cwd() / ".pocketchat" / "tasks" /
                                 (result.task_id + ".json")),
            "session": _json_path(result.session_path),
            "trace": _json_path(result.trace_path),
        }
        if result.acceptance is not None:
            payload["acceptance"] = result.acceptance
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("task %s  %s" % (result.task_id, result.status))
            print("record %s" % payload["record"])
            if result.acceptance is not None:
                print("acceptance %s" % result.acceptance["state"])
                if result.acceptance.get("record"):
                    print("acceptance record %s" % result.acceptance["record"])
        return EXIT_OK if ok else EXIT_RUN_FAILED

    if args.task_command == "verify":
        try:
            plan = load_plan(args.acceptance)
            receipt = verify_task(Path.cwd(), args.task_id, plan)
        except (AcceptanceError, TaskError, OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        ok = receipt["state"] == "passed"
        payload = {"ok": ok, "schema_version": "1", "task_id": args.task_id,
                   "acceptance": receipt}
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("task %s  acceptance %s" % (args.task_id, receipt["state"]))
            print("acceptance record %s" % receipt["record"])
        return EXIT_OK if ok else EXIT_RUN_FAILED

    if args.task_command == "status":
        try:
            record = load_record(Path.cwd(), args.task_id)
        except (TaskError, OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        summary = task_summary(record)
        payload = {"ok": True, "schema_version": "1",
                   "task_id": record.task_id, "state": record.state,
                   "report": summary}
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("task %s  %s" % (record.task_id, record.state))
        return EXIT_OK

    if args.task_command == "resume":
        try:
            policy = load_policy(args.policy)
            result = resume_task(Path.cwd(), args.checkpoint, policy,
                                 task_id=args.task_id,
                                 name=args.name)
        except (TaskError, TaskPolicyError, RecoveryError, SessionFileError,
                OSError) as exc:
            if args.as_json:
                _print_json_error("task", str(exc))
            else:
                print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        payload = {
            "ok": True, "schema_version": "1",
            "task_id": result["task_id"], "checkpoint_id": args.checkpoint,
            "step_id": result["step_id"], "session": result["session"],
            "report": result["report"],
            "recovery_step": result["report"]["steps"],
            "context_messages": len(result["context"]),
        }
        if args.as_json:
            print(json.dumps(payload, sort_keys=True))
        else:
            print("task %s resumed from %s" % (result["task_id"],
                                               args.checkpoint))
            print("session %s" % result["session"])
        return EXIT_OK

    try:
        record = load_record(Path.cwd(), args.task_id)
        policy = load_policy(args.policy) if args.policy else None
        if (policy is not None
                and policy_digest(getattr(policy, "source", None) or policy)
                != record.policy_sha256):
            raise TaskError("report policy does not match task policy_sha256")
        payload = _task_report_payload(Path.cwd(), record, policy)
    except (AcceptanceError, TaskError, TaskPolicyError) as exc:
        if args.as_json:
            _print_json_error("task", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("task %s  %s" % (record.task_id, record.state))
        print("steps %d  tools %d  checkpoints %d"
              % (len(record.steps), len(payload["tools"]),
                 len(payload["checkpoints"])))
        print("commands %d  changes %d"
              % (len(payload["commands"]), len(payload["changes"])))
        if payload["evidence_issues"]:
            print("evidence_issues %d" % len(payload["evidence_issues"]))
        print("acceptance %s" % payload["acceptance"]["state"])
    return EXIT_OK


def _cmd_profile(args: argparse.Namespace) -> int:
    """List or manage the profiles a config offers.

    Write commands never take a credential value. They name only the
    environment variable that will hold it, validate the complete candidate,
    preserve the configuration file's mode and replace it atomically.
    """
    if args.profile_command != "list":
        return _cmd_profile_write(args)
    return _cmd_profile_list(args)


def _profile_write_error(action: str, name: str, exc: ConfigWriteError,
                         as_json: bool) -> int:
    payload = {
        "ok": False,
        "action": action,
        "profile": name,
        "error": exc.code,
        "message": exc.message,
    }
    if exc.fix:
        payload["fix"] = exc.fix
    if as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("pocketchat: %s" % exc.message, file=sys.stderr)
        if exc.fix:
            print("next: %s" % exc.fix, file=sys.stderr)
    return EXIT_USAGE


def _cmd_profile_write(args: argparse.Namespace) -> int:
    path = _config_path(args.config)
    action = args.profile_command
    name = args.name
    try:
        if action == "set":
            fields = {
                "base_url": args.base_url,
                "model": args.model,
                "api_key_env": args.api_key_env,
            }
            for key in (
                "timeout_seconds", "max_context_chars",
                "summary_prefix_chars", "input_price_per_million",
                "output_price_per_million", "reasoning_effort_supported",
                "max_turn_tokens", "max_session_tokens",
                "tool_read_enabled", "tool_read_allow_paths",
                "tool_write_enabled", "tool_write_allow_paths",
                "tool_memory_search_enabled", "tool_memory_search_max_results",
                "tool_call_max_count", "tool_result_max_bytes",
            ):
                value = getattr(args, key)
                if value is not None:
                    fields[key] = value
            config = upsert_profile(path, name, fields)
        elif action == "use":
            config = set_default_profile(path, name)
        else:
            config = remove_profile(path, name)
    except ConfigWriteError as exc:
        return _profile_write_error(action, name, exc, args.as_json)

    payload = {
        "ok": True,
        "action": action,
        "profile": name,
        "default_profile": config["default_profile"],
    }
    if args.as_json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("profile %s %s" % (action, name))
    return EXIT_OK


def _cmd_profile_list(args: argparse.Namespace) -> int:
    """List the profiles a config offers, and whether each key is set.

    The credential's *value* never appears here. What an operator needs to
    know before choosing is which environment variable each profile reads and
    whether that variable is set in this shell, so that is the whole output:
    a name, a model, the endpoint it talks to, the variable's name and one
    word about its state. A profile that reads an unset variable is still
    listed -- it is a fact about the config, not a reason to hide the profile.
    """
    try:
        config = load_config(_config_path(args.config))
    except ConfigError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    default_profile = config.get("default_profile")
    entries = []
    for name in sorted(config["profiles"]):
        record = config["profiles"][name]
        env_name = record.get("api_key_env", "")
        entries.append({
            "name": name,
            "default": name == default_profile,
            "model": record.get("model", ""),
            "base_url": record.get("base_url", ""),
            "api_key_env": env_name,
            # Whether the variable is set, never what it holds. An empty
            # variable counts as unset: `load_api_key` treats it that way, so
            # reporting "set" here would promise a run that then refuses.
            "key_set": bool(os.environ.get(env_name, "").strip()),
            "reasoning_effort_supported": bool(
                record.get("reasoning_effort_supported", False)),
            "max_turn_tokens": record.get("max_turn_tokens"),
            "max_session_tokens": record.get("max_session_tokens"),
            "tool_read_enabled": bool(record.get("tool_read_enabled", False)),
            "tool_read_allow_paths": record.get("tool_read_allow_paths", []),
            "tool_write_enabled": bool(record.get("tool_write_enabled", False)),
            "tool_write_allow_paths": record.get("tool_write_allow_paths", []),
            "tool_memory_search_enabled": bool(
                record.get("tool_memory_search_enabled", False)),
            "tool_memory_search_max_results":
                record.get("tool_memory_search_max_results"),
            "tool_call_max_count": record.get("tool_call_max_count"),
            "tool_result_max_bytes": record.get("tool_result_max_bytes"),
        })

    if args.as_json:
        print(json.dumps({"default_profile": default_profile, "profiles": entries},
                         sort_keys=True))
        return EXIT_OK
    width = max(len(entry["name"]) for entry in entries)
    model_width = max(len(entry["model"]) for entry in entries)
    url_width = max(len(entry["base_url"]) for entry in entries)
    for entry in entries:
        suffix = ""
        if entry["reasoning_effort_supported"]:
            suffix += "  reasoning"
        if entry["max_turn_tokens"] is not None:
            suffix += "  turn<=%s" % entry["max_turn_tokens"]
        if entry["max_session_tokens"] is not None:
            suffix += "  session<=%s" % entry["max_session_tokens"]
        if entry["default"]:
            suffix += "  (default)"
        print("%-*s  %-*s  %-*s  %-8s %s%s" % (
            width, entry["name"],
            model_width, entry["model"],
            url_width, entry["base_url"],
            "set" if entry["key_set"] else "not set",
            entry["api_key_env"],
            suffix))
    return EXIT_OK


def _named_profiles(value: str) -> List[str]:
    """Split a ``--profiles`` value into the names it names, in order.

    The order is the operator's and is not rearranged: a comparison run in a
    different order than the one named is a comparison the operator did not
    ask for. Whitespace around a name is stripped so ``a, b`` works, but an
    empty name is refused rather than skipped -- ``a,,b`` is a typo, and a
    typo that silently produced two profiles instead of three would be a
    comparison that quietly measured less than it claimed.
    """
    names = [name.strip() for name in value.split(",")]
    if any(not name for name in names):
        raise ConfigError("--profiles names profiles separated by commas, "
                          "with no empty name: %r" % value)
    return names


def _cmd_compare(args: argparse.Namespace) -> int:
    """Run one prompt against several profiles, and write the manifest.

    This is a measurement, so everything it needs is resolved before the first
    request goes out: an unknown profile name, a repeated one, or a profile
    whose credential is not set all fail with exit 2 and leave no trace behind.
    A comparison that started and then discovered a missing key would have
    already written half a measurement.

    The exit code describes the command, not every profile. One profile
    failing is a result the manifest carries; the command itself succeeded.

    ``--profile`` and ``--trace`` name *one* run's profile and trace file. A
    comparison runs several, so neither means anything here, and both are
    refused rather than dropped: they come from the shared option block, and
    a flag the parser accepts but nothing reads would let an operator believe
    a choice landed when the runs went somewhere else entirely.
    """
    if getattr(args, "profile", None):
        print("pocketchat: compare runs the profiles --profiles names; "
              "--profile selects one and does not apply here", file=sys.stderr)
        return EXIT_USAGE
    if args.trace is not None:
        print("pocketchat: compare writes one trace per profile under %s; "
              "--trace names a single file and does not apply here"
              % (Path.cwd() / "traces"), file=sys.stderr)
        return EXIT_USAGE

    try:
        names = _named_profiles(args.profiles)
    except ConfigError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if len(set(names)) != len(names):
        print("pocketchat: --profiles names %s twice; each profile runs once"
              % [name for name in set(names) if names.count(name) > 1][0],
              file=sys.stderr)
        return EXIT_USAGE

    try:
        config = load_config(_config_path(args.config))
    except ConfigError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    profiles = {}
    for name in names:
        try:
            profiles[name] = profile_config(config, name)
        except ConfigError as exc:
            print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE

    # Every credential is checked up front, so a comparison is never half
    # started. A profile whose key is missing fails before the first request
    # rather than in the middle, where its failure would look like a wire
    # error instead of a configuration one.
    for name, record in profiles.items():
        if requires_api_key(record) and load_api_key(record) is None:
            print("pocketchat: %s is not set" % record["api_key_env"],
                  file=sys.stderr)
            return EXIT_USAGE

    base_messages: Optional[List[Dict[str, str]]] = None
    base_session: Optional[Path] = None
    if args.session is not None:
        session_path = resolve_session_path(args.session, DEFAULT_SESSIONS_DIR)
        if not session_path.is_file():
            # A named base session is a fact to read, not a file to create.
            # Creating one would turn a typo into a fresh conversation that
            # silently measured nothing.
            print("pocketchat: no such session: %s" % session_path,
                  file=sys.stderr)
            return EXIT_USAGE
        try:
            # A comparison reads a session file and never writes one, so the
            # header is taken from the reader's own API rather than through
            # SessionFile.open, which would create the file when missing.
            base_messages = read_messages(session_path)
        except (SessionFileError, OSError) as exc:
            print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        # The file is named to the manifest as well: the counts it records are
        # only checkable against the conversation they were read from.
        base_session = session_path

    workspace = Path.cwd()
    try:
        result = run_comparison(
            prompt=args.prompt,
            profiles=profiles,
            workspace=workspace,
            base_messages=base_messages,
            verbose=args.verbose,
            base_session=base_session,
        )
    except KeyboardInterrupt:
        # The comparison itself already closed the run in flight with its own
        # terminal event; what is left is to say so without a traceback.
        print("pocketchat: comparison interrupted", file=sys.stderr)
        return EXIT_RUN_FAILED

    try:
        manifest_path = write_comparison_manifest(
            comparison_manifest_path(workspace, result.comparison_id), result)
    except ComparisonManifestError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_RUN_FAILED

    if args.as_json:
        print(json.dumps({
            "comparison_id": result.comparison_id,
            "manifest": str(manifest_path),
            "prompt": result.prompt,
            "runs": [{
                "profile": run.profile,
                "status": run.status,
                "run_id": run.run_id,
                "duration_ms": run.duration_ms,
                "usage": run.usage,
                "cost_usd": run.cost_usd,
                "error": run.error,
            } for run in result.runs],
        }, sort_keys=True))
        return EXIT_OK

    # --quiet drops the table, not the manifest path. A comparison whose
    # stdout nobody reads still needs to say where its evidence went.
    if not args.quiet:
        for run in result.runs:
            # "unknown" rather than a number: a profile whose prices were
            # never configured has no price to report, and printing 0 would
            # state one nobody set. The formatting keeps six places because a
            # per-million rate over a few tokens lands far below 0.0001, and
            # rounding it to zero would make a real cost read as free.
            cost = "unknown" if run.cost_usd is None else "%.6f" % run.cost_usd
            reply = run.output_summary or (run.error or {}).get("type", "")
            print("%-12s %-10s %7s %6d %6d %8s  %s" % (
                run.profile, run.status,
                "%.1fs" % (run.duration_ms / 1000.0),
                run.usage.get("input_tokens", 0),
                run.usage.get("output_tokens", 0),
                cost, reply))
    print("manifest %s" % manifest_path)
    return EXIT_OK


def _cmd_replay(args: argparse.Namespace) -> int:
    """Verify saved request snapshots without reading a key or opening a socket."""
    try:
        results = replay_snapshots(Path.cwd(), args.run_or_request)
    except ReplayError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    mismatches = [result for result in results if not result["match"]]
    if mismatches:
        for result in mismatches:
            print(
                "pocketchat: request hash mismatch for %s: expected %s actual %s"
                % (result["request_path"], result["request_sha256"],
                   result["recomputed_sha256"]),
                file=sys.stderr,
            )
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps({
            "run_id": results[0]["run_id"],
            "sent": False,
            "requests": results,
        }, sort_keys=True))
        return EXIT_OK

    for result in results:
        print("verified %s %s %s (dry-run, no network request sent)"
              % (result["run_id"], result["request_path"],
                 result["request_sha256"]))
    return EXIT_OK


def _cmd_workspace(args: argparse.Namespace) -> int:
    """Print the relationships the workspace's files record.

    Nothing is written and no key is read: the graph comes from the session
    headers, the message lines and the comparison manifests that are already
    on disk, so it describes the workspace after the fact and cannot be
    changed by anything this process does. A relation the files do not record
    is printed as absent -- a manifest says nothing about which answer was
    kept, so the graph says that instead of choosing.
    """
    workspace = Path(args.path) if args.path is not None else Path(".")
    try:
        graph = collect_workspace(workspace)
    except WorkspaceError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps(as_dict(graph), sort_keys=True, indent=2))
        return EXIT_OK

    for line in render_lines(graph):
        print(line)
    return EXIT_OK


def _cmd_history(args: argparse.Namespace) -> int:
    """Print the turns the workspace's sessions record, read-only.

    Like `workspace`, this command reads files that already exist and writes
    nothing: no configuration, no credential, no socket. A target narrows the
    answer to one session, conversation or branch; without one, every
    conversation the workspace holds is listed, branches sorted by path so
    two processes describe the same workspace the same way.
    """
    try:
        record = collect_history(Path("."), args.target)
    except QueryError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(record, sort_keys=True, indent=2))
        return EXIT_OK
    for line in render_history_lines(record):
        print(line)
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    """Find literal text in the sessions' message lines.

    Zero hits is a result, not a failure: the command answers "the text is in
    no session" with exit 0, because a script looking for a wording it hopes
    is absent needs that answer on stdout rather than an error on stderr.
    The text is matched exactly as given -- case and whitespace included --
    so a hit is always a wording a file actually holds.
    """
    session = str(args.session) if args.session is not None else None
    try:
        record = search_messages(args.text, Path("."), session)
    except QueryError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(record, sort_keys=True, indent=2))
        return EXIT_OK
    for line in render_search_lines(record):
        print(line)
    return EXIT_OK


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare two sessions turn by turn, common prefix first.

    The diff is over the words the files hold, not over their identities:
    which turns the two share from the start, and which turns each side adds
    after the first disagreement. When one side records the other as its
    parent, the fork point it records is checked against the prefix the
    words support, and a disagreement is printed rather than resolved.
    """
    try:
        record = diff_sessions(str(args.session_a), str(args.session_b),
                               Path("."))
    except QueryError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(record, sort_keys=True, indent=2))
        return EXIT_OK
    for line in render_diff_lines(record):
        print(line)
    return EXIT_OK


def _cmd_select(args: argparse.Namespace) -> int:
    """Record which answer of a comparison was kept, optionally into a session.

    The manifest is never rewritten -- it is exclusive-create evidence of the
    measurement, and the decision gets its own record beside it, also
    exclusive-create: one comparison keeps one decision. Everything ``--into``
    needs is checked before the first byte lands, so a refused selection
    leaves the workspace byte-identical, and the session it writes is a run
    of its own -- a fresh run id and trace seeded with the selected exchange,
    the way a branch is a fresh run seeded with inherited turns.
    """
    try:
        result = record_selection(
            Path("."), args.comparison, args.run,
            note=args.note,
            into=str(args.into) if args.into is not None else None)
    except SelectionError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    record = result["record"]
    if args.as_json:
        payload = {
            "selection": str(result["selection_path"]),
            "comparison_id": record["comparison_id"],
            "run_id": record["run_id"],
            "profile": record["profile"],
        }
        if args.into is not None:
            payload["session"] = str(result["session_path"])
        print(json.dumps(payload, sort_keys=True))
        return EXIT_OK

    line = "selection %s  (%s)  %s" % (record["run_id"],
                                       record["profile"] or "-",
                                       result["selection_path"])
    if args.note:
        line += "  %s" % args.note
    print(line)
    if args.into is not None:
        print("session %s" % result["session_path"])
    return EXIT_OK


def _cmd_index(args: argparse.Namespace) -> int:
    """Rebuild or inspect the derived index.

    The index holds no fact of its own and no command reads it: it exists
    for tools and UI that want the workspace's rows without re-reading every
    file. What this command does promise is honesty about the index itself
    -- a stale one names the files it disagrees with and exits non-zero,
    because a derived index that silently describes a workspace that no
    longer exists is worse than none.
    """
    workspace = Path(args.path) if args.path is not None else Path(".")
    try:
        if args.rebuild:
            report = build_index(workspace, rebuild=True)
        else:
            report = index_status(workspace)
    except IndexingError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps({
            "status": report["status"],
            "index": str(report["path"]),
            "sources": report["sources"],
            "counts": report["counts"],
            "stale_files": report["mismatched"],
            "mismatched_rows": report.get("mismatched_rows", []),
        }, sort_keys=True))
        return EXIT_OK if report["status"] in ("fresh", "missing") else EXIT_RUN_FAILED

    index = report["path"]
    status = report["status"]
    counts = report["counts"]
    if status == "missing":
        print("index %s  missing  (build one with --rebuild)" % index)
        return EXIT_OK
    detail = ("%d conversation branches, %d comparisons, %d runs, "
              "%d problems" % (counts.get("conversation_branches", 0),
                               counts.get("comparisons", 0),
                               counts.get("comparison_runs", 0),
                               counts.get("problems", 0)))
    print("index %s  %s  (%d sources; %s)" % (index, status,
                                              report["sources"], detail))
    if status in ("stale", "inconsistent"):
        for name in report["mismatched"]:
            print("  changed: %s" % name)
        for name in report.get("mismatched_rows", []):
            print("  inconsistent rows: %s" % name)
        print("pocketchat: the index disagrees with the workspace; "
              "rebuild it with --rebuild")
        return EXIT_RUN_FAILED
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = diagnose(_config_path(args.config), args.profile)
    if args.as_json:
        payload = dict(report)
        payload["schema_version"] = "1"
        payload["check_status"] = {
            check["name"]: check["status"] for check in report["checks"]
        }
        payload["next_actions"] = [
            check["fix"] for check in report["checks"]
            if check["status"] == "failed" and check.get("fix")
        ]
        print(json.dumps(payload, sort_keys=True))
    else:
        for line in render_doctor_lines(report):
            print(line)
    return EXIT_OK if report["ok"] else EXIT_USAGE


def _cmd_run(args: argparse.Namespace) -> int:
    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    session, trace_path = prepared
    try:
        status = session.run(args.prompts)
    except SessionError as exc:
        if args.as_json:
            _print_json_error("run", str(exc))
            return EXIT_RUN_FAILED
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_RUN_FAILED
    if args.as_json:
        payload = _execution_record(status, session, trace_path)
        print(json.dumps(payload, sort_keys=True))
        return EXIT_OK if status == "completed" else EXIT_RUN_FAILED
    if not args.quiet and status == "completed":
        # The reply goes to stdout, so a script can pipe it. The trace stays a
        # separate artifact: stdout is the answer, the file is the evidence.
        print(session.last_reply())
    print(json.dumps({"status": status, "run_id": session.run_id,
                      "turns": session.turn_count,
                      "trace": str(trace_path)}, sort_keys=True))
    return EXIT_OK if status == "completed" else EXIT_RUN_FAILED


def _prepare(args: argparse.Namespace):
    """Load and check everything before a trace file is created.

    Both interactive and non-interactive commands come through here, so a
    typo in the config or a missing credential fails the same way and leaves
    no half-written run behind. Returns the session or an exit code.
    """
    try:
        config = load_config(_config_path(args.config))
    except ConfigError as exc:
        return _prepare_error(str(exc), args, "config")

    # The profile is resolved here, before anything is written, so a
    # misspelled name is a usage error rather than a run that goes out on a
    # profile the operator did not choose. Selecting the default profile
    # yields the config unchanged, which is why this is unconditional.
    try:
        config = profile_config(config, getattr(args, "profile", None))
    except ConfigError as exc:
        return _prepare_error(str(exc), args, "config")

    if requires_api_key(config) and load_api_key(config) is None:
        return _prepare_error(
            "%s is not set" % config["api_key_env"], args, "credential")

    # A resumable run does not mint an id here: the session file names the run,
    # and Session adopts that name. Minting one first would put a fresh run id
    # into a file that already belongs to another run, which the validator
    # rejects as two runs in one trace. Without a session file the id is minted
    # here, because there is no file to take it from.
    run_id = new_run_id()
    trace_path = _trace_path_for(args, run_id)
    session_path = _session_path_for(args)
    if session_path is not None and args.trace is not None and session_path.exists():
        try:
            recorded = read_header(session_path).get("trace")
        except SessionFileError as exc:
            return _prepare_error(str(exc), args, "session_file")
        if isinstance(recorded, str) and recorded.strip():
            recorded_trace = (session_path.parent / recorded).resolve()
            if Path(args.trace).resolve() != recorded_trace:
                return _prepare_error(
                    "session %s records trace %s; --trace must match it or be "
                    "omitted" % (session_path, recorded_trace), args,
                    "session_file")

    try:
        session = Session(config=config, trace_path=trace_path, run_id=run_id,
                          session_path=session_path, verbose=args.verbose)
    except ConfigError as exc:
        return _prepare_error(str(exc), args, "config")
    return session, session.trace_path


def _cmd_chat(args: argparse.Namespace) -> int:
    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    session, trace_path = prepared

    # The REPL is a source of prompts, not a second trace path. Every line it
    # reads is a prompt to the one session, and the session's single exit is
    # what writes the terminal event -- so an interrupted conversation still
    # ends with run.cancelled rather than a trace that stops mid-line.
    status = session.run(read_lines())
    if args.as_json:
        print(json.dumps(_execution_record(status, session, trace_path),
                         sort_keys=True))
        return EXIT_OK if status == "completed" else EXIT_RUN_FAILED
    if not args.quiet and status == "completed" and session.turn_count:
        # The last reply is the one the human is looking at.
        echo_reply(session.last_reply())
    if not args.quiet:
        goodbye()
    print(json.dumps({"status": status, "run_id": session.run_id,
                      "turns": session.turn_count,
                      "trace": str(trace_path)}, sort_keys=True))
    return EXIT_OK if status == "completed" else EXIT_RUN_FAILED


def _cmd_agent(args: argparse.Namespace) -> int:
    """Interactive chat with switches that affect the next message only."""
    config_path = _agent_config_path(args.config)
    try:
        root_config = load_config(config_path)
    except ConfigError as exc:
        print("agent: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    profiles = root_config.get("profiles") or {}
    models = {
        name: record.get("model", "")
        for name, record in profiles.items()
        if isinstance(record, dict)
    }
    initial_profile = args.profile or root_config.get("default_profile")
    if initial_profile not in profiles:
        print("agent: unknown profile: %s" % initial_profile, file=sys.stderr)
        return EXIT_USAGE
    state = AgentState(
        profile=initial_profile,
        model=models[initial_profile],
        session=args.session,
        reasoning=args.reasoning,
        context_chars=(args.context_chars
                       if args.context_chars is not None
                       else profiles[initial_profile].get("max_context_chars", 24000)),
        reasoning_supported=bool(
            profiles[initial_profile].get("reasoning_effort_supported", False)),
    )

    interactive = bool(getattr(sys.stdin, "isatty", lambda: False)())
    if interactive:
        try:
            return run_tui(
                state,
                profiles,
                models,
                config=root_config,
                workspace=Path.cwd(),
                verbose=args.verbose,
            )
        except TUIUnavailable:
            # Windows' standard library does not ship curses. Keep the agent
            # usable and explicit by falling back to the same line interface
            # used for piped input, rather than crashing during import.
            pass

    print("PocketChat agent. Commands: /model /session /reasoning /context /status /exit")
    print(state.status_line())
    for raw in read_lines(prompt="agent> "):
        line = raw.strip()
        if not line:
            continue
        try:
            response = apply_command(state, line, profiles, models)
        except AgentCommandError as exc:
            print("agent: %s" % exc)
            continue
        if response == "exit":
            break
        if response is not None:
            print(response)
            continue

        try:
            selected = profile_config(root_config, state.profile)
        except ConfigError as exc:
            print("agent: %s" % exc)
            continue
        selected["max_context_chars"] = state.context_chars
        selected["reasoning_effort"] = state.reasoning
        session_path = resolve_session_path(Path(state.session), DEFAULT_SESSIONS_DIR)
        run_id = new_run_id()
        trace_path = Path.cwd() / "traces" / ("%s.jsonl" % run_id)
        session = Session(
            config=selected,
            trace_path=trace_path,
            run_id=run_id,
            session_path=session_path,
            verbose=args.verbose,
        )
        status = session.run([line])
        if not args.quiet and status == "completed" and session.turn_count:
            echo_reply(session.last_reply())
        if status != "completed":
            print("agent: run %s" % status, file=sys.stderr)
            return EXIT_RUN_FAILED
    if not args.quiet:
        goodbye()
    return EXIT_OK


def _cmd_show(args: argparse.Namespace) -> int:
    """Print one session: the conversation, then the events that recorded it.

    Both are read from the files rather than from a running process, so this
    command works after the fact and cannot be changed by what an operator did
    since. The reading lives in the service layer; what is left here is the
    printing -- the conversation first, then the trace, then the reason no
    trace follows when there is none.
    """
    if args.as_json:
        try:
            record = show_record(args.session, DEFAULT_SESSIONS_DIR)
        except SessionFileError as exc:
            print(json.dumps({
                "ok": False, "schema_version": "1",
                "error": "session_file", "message": str(exc),
            }, sort_keys=True))
            return EXIT_USAGE
        if record["trace_problem"] is not None:
            problem = record["trace_problem"]
            print(json.dumps({
                "ok": False, "schema_version": "1",
                "error": problem["code"], "message": problem["message"],
            }, sort_keys=True))
            return EXIT_USAGE
        print(json.dumps(record, sort_keys=True))
        return EXIT_OK

    try:
        record = session_overview(args.session, DEFAULT_SESSIONS_DIR)
    except SessionFileError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    part = record["session"]
    if part is not None:
        header = part["header"]
        turns = part["turns"]
        print("session %s  started %s  messages %d"
              % (header.get("session"), header.get("created"), len(turns)))
        # The two ids are the file's own. A v0.1/v0.2 file has neither, and
        # the honest line says so rather than printing an empty string that
        # reads as "this session has no identity".
        print("conversation %s  branch %s"
              % (header.get("conversation_id") or NOT_RECORDED,
                 header.get("branch_id") or NOT_RECORDED))
        for message in turns:
            label = "you" if message["role"] == "user" else "model"
            print("")
            print("turn %s  %s> %s"
                  % (message["turn_id"], label, message["content"]))
        print("")

    problem = record["trace_problem"]
    if problem is not None:
        print("pocketchat: %s" % problem["message"], file=sys.stderr)
        return EXIT_USAGE

    trace = record["trace"]
    print("trace %s" % trace["path"])
    for entry in trace["lines"]:
        if "error" in entry:
            print("  line %d: invalid JSON: %s"
                  % (entry["number"], entry["error"]))
            continue
        print("%4d  %-12s %s"
              % (entry["number"], entry["event_id"], entry["type"]))
        payload = entry.get("payload")
        if entry["type"] == "context.compacted" and isinstance(payload, dict):
            print("      compacted: strategy=%s dropped=%s kept=%s "
                  "chars=%s budget=%s"
                  % (payload.get("strategy", "-"),
                     payload.get("dropped_messages", "-"),
                     payload.get("kept_messages", "-"),
                     payload.get("kept_chars", "-"),
                     payload.get("budget_chars", "-")))
    return EXIT_OK


def _cmd_context(args: argparse.Namespace) -> int:
    try:
        record = context_preview(
            args.session, budget_chars=args.budget,
            keep_recent=args.keep_recent)
    except (SessionFileError, ContextBudgetExceeded, ValueError) as exc:
        if args.as_json:
            _print_json_error("context", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(record, sort_keys=True))
        return EXIT_OK
    print("context %s  messages=%d  chars=%d/%d" % (
        record["session"], record["message_count"], record["total_chars"],
        record["budget_chars"]))
    print("tokens estimated %d -> %d  compacted=%s" % (
        record["estimated_tokens_before"], record["estimated_tokens_after"],
        "yes" if record["compacted"] else "no"))
    print("kept %s  dropped %s" % (
        ",".join(str(index) for index in record["kept_indices"]) or "-",
        ",".join(str(index) for index in record["dropped_indices"]) or "-"))
    return EXIT_OK


def _cmd_tool(args: argparse.Namespace) -> int:
    try:
        if args.tool_command == "read":
            result = execute_read_file(
                Path.cwd(), args.path, allowed_paths=args.allowed_paths,
                max_bytes=args.max_bytes)
        elif args.tool_command == "write":
            result = execute_write_file(
                Path.cwd(), args.path, args.content,
                allowed_paths=args.allowed_paths, overwrite=args.overwrite)
        else:
            print("pocketchat: unknown tool command", file=sys.stderr)
            return EXIT_USAGE
    except (ToolPermissionError, ToolError) as exc:
        if args.as_json:
            _print_json_error("tool", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps({"ok": True, "schema_version": "1", **result},
                         sort_keys=True))
    else:
        if args.tool_command == "read":
            print(result["content"], end="")
        else:
            print("wrote %s (%d bytes)" % (result["path"], result["bytes"]))
    return EXIT_OK


def _cmd_memory(args: argparse.Namespace) -> int:
    root = Path.cwd()
    try:
        if args.memory_command == "add":
            record = add_memory(root, args.content, args.tags)
            result = {"ok": True, "schema_version": "1",
                      "memory_id": record["memory_id"], "item": record}
        elif args.memory_command == "list":
            result = {"ok": True, "schema_version": "1",
                      "items": list_memory(root)}
        elif args.memory_command == "search":
            result = {"ok": True, "schema_version": "1",
                      "items": search_memory(root, args.text)}
        elif args.memory_command == "remove":
            remove_memory(root, args.memory_id)
            result = {"ok": True, "schema_version": "1",
                      "removed": args.memory_id}
        else:
            raise MemoryError("unknown memory command")
    except MemoryError as exc:
        if args.as_json:
            _print_json_error("memory", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
        return EXIT_OK
    items = result.get("items")
    if items is not None:
        for item in items:
            print("%s  %s" % (item["memory_id"], item["content"]))
    elif "item" in result:
        print(result["item"]["memory_id"])
    else:
        print("removed %s" % result["removed"])
    return EXIT_OK


def _cmd_delegate(args: argparse.Namespace) -> int:
    if args.delegate_command != "run":
        print("pocketchat: unknown delegate command", file=sys.stderr)
        return EXIT_USAGE
    try:
        config = load_config(_config_path(args.config))
        selected = profile_config(config, args.profile)
        if requires_api_key(selected):
            key = load_api_key(selected)
            if key is None:
                raise DelegateError("%s is not set" % selected["api_key_env"])
        else:
            key = ""
        result = run_delegation(
            Path.cwd(), args.prompts, selected,
            max_children=args.max_children,
            timeout_seconds=args.timeout_seconds, api_key=key)
    except (ConfigError, DelegateError) as exc:
        if args.as_json:
            _print_json_error("delegate", str(exc))
        else:
            print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    if args.as_json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("delegation %s  manifest %s"
              % (result["delegation_id"], result["manifest"]))
        for child in result["children"]:
            print("%d  %s  %s" % (child["index"], child["status"],
                                  child.get("reply") or child.get("error") or ""))
    return EXIT_OK if result["ok"] else EXIT_RUN_FAILED


def _cmd_export(args: argparse.Namespace) -> int:
    """Write one self-contained copy of a session, in one of three formats.

    The whole orchestration -- resolving the pair, rendering the body,
    refusing an existing output, writing the file -- lives in the service
    layer and is shared with whatever surface comes next; this command is
    the argument parse and the one-line result.
    """
    if args.as_json:
        try:
            result = export_record(args.session, args.format, args.out,
                                   DEFAULT_SESSIONS_DIR)
        except (SessionFileError, OSError) as exc:
            print(json.dumps({
                "ok": False, "schema_version": "1",
                "error": "export", "message": str(exc),
            }, sort_keys=True))
            return EXIT_USAGE
        print(json.dumps(result, sort_keys=True))
        return EXIT_OK

    try:
        result = export_session(args.session, args.format, args.out,
                                DEFAULT_SESSIONS_DIR)
    except (SessionFileError, OSError) as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
    print(json.dumps(result, sort_keys=True))
    return EXIT_OK



if __name__ == "__main__":
    raise SystemExit(main())
