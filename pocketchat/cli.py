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
import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import __version__
from .branching import BranchError, create_branch_from
from .config import (CONFIG_TEMPLATE, ConfigError, load_api_key, load_config,
                     profile_config)
from .compare import (ComparisonManifestError, comparison_manifest_path,
                      run_comparison, write_comparison_manifest)
from .indexing import (IndexingError, build_index, dump_rows, index_path,
                       index_status)
from .queries import (QueryError, collect_history, diff_sessions,
                      render_diff_lines, render_history_lines,
                      render_search_lines, search_messages)
from .replay import ReplayError, replay_snapshots
from .selection import SelectionError, record_selection
from .repl import echo_reply, goodbye, read_lines
from .service import export_session, session_overview
from .sessions import (SessionFileError, read_header, read_messages,
                       resolve_session_argument, resolve_session_path,
                       NOT_RECORDED)
from .session import Session, SessionError, new_run_id
from .workspace import (WorkspaceError, as_dict, collect_workspace,
                        render_lines)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

DEFAULT_CONFIG = "pocketchat.json"
DEFAULT_SESSIONS_DIR = "sessions"


def _config_path(value: Optional[Path]) -> Path:
    return Path(value) if value else Path.cwd() / DEFAULT_CONFIG


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
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    run = subparsers.add_parser("run", help="run one or more prompts non-interactively")
    add_common(run)
    run.add_argument("prompts", nargs="+", help="prompts to send, in order")
    run.add_argument("--session", type=Path, default=None,
                     help="name or resume a session file; without it there is none")
    run.add_argument("--quiet", action="store_true",
                     help="print only the final status line")

    chat = subparsers.add_parser("chat", help="hold a conversation interactively")
    add_common(chat)
    chat.add_argument("--session", type=Path, default=None,
                     help="name or resume a session file; without it there is none")
    chat.add_argument("--quiet", action="store_true",
                      help="print only the final status line")

    show = subparsers.add_parser("show", help="print one session, then its trace")
    show.add_argument("session", type=Path,
                      help="a session file, or the trace it recorded; a bare "
                           "name is read from ./sessions")

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

    profile = subparsers.add_parser(
        "profile", help="show the profiles a configuration offers")
    profile_subparsers = profile.add_subparsers(dest="profile_command", required=True)
    profile_list = profile_subparsers.add_parser(
        "list", help="list the profiles, and whether each credential is set")
    profile_list.add_argument("--config", type=Path, default=None,
                              help="configuration file (default: ./%s)" % DEFAULT_CONFIG)
    profile_list.add_argument("--json", action="store_true", dest="as_json",
                              help="print one JSON object instead of one line per profile")

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

    if args.command == "init":
        return _cmd_init(args)
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "chat":
        return _cmd_chat(args)
    if args.command == "show":
        return _cmd_show(args)
    if args.command == "export":
        return _cmd_export(args)
    if args.command == "branch":
        return _cmd_branch(args)
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


def _cmd_init(args: argparse.Namespace) -> int:
    target = args.path or Path.cwd() / DEFAULT_CONFIG
    if target.exists() and not args.force:
        print("pocketchat: %s already exists (use --force to overwrite)" % target,
              file=sys.stderr)
        return EXIT_USAGE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
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
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    try:
        result = create_branch_from(source, args.from_turn, args.name,
                                    DEFAULT_SESSIONS_DIR)
    except BranchError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if args.as_json:
        print(json.dumps(result, sort_keys=True))
        return EXIT_OK

    print("branch %s  from %s  at %s  (turns 1-%d)"
          % (result["branch_id"], result["parent_branch_id"] or NOT_RECORDED,
             result["parent_turn_id"], result["turns"]))
    print("conversation %s  session %s" % (result["conversation_id"],
                                           result["session"]))
    return EXIT_OK


def _cmd_profile(args: argparse.Namespace) -> int:
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
        })

    if args.as_json:
        print(json.dumps({"default_profile": default_profile, "profiles": entries},
                         sort_keys=True))
        return EXIT_OK
    width = max(len(entry["name"]) for entry in entries)
    model_width = max(len(entry["model"]) for entry in entries)
    url_width = max(len(entry["base_url"]) for entry in entries)
    for entry in entries:
        print("%-*s  %-*s  %-*s  %-8s %s%s" % (
            width, entry["name"],
            model_width, entry["model"],
            url_width, entry["base_url"],
            "set" if entry["key_set"] else "not set",
            entry["api_key_env"], "  (default)" if entry["default"] else ""))
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
        if load_api_key(record) is None:
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
        }, sort_keys=True))
        return EXIT_OK if report["status"] != "stale" else EXIT_RUN_FAILED

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
    if status == "stale":
        for name in report["mismatched"]:
            print("  changed: %s" % name)
        print("pocketchat: the index disagrees with the workspace; "
              "rebuild it with --rebuild")
        return EXIT_RUN_FAILED
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    prepared = _prepare(args)
    if isinstance(prepared, int):
        return prepared
    session, trace_path = prepared
    try:
        status = session.run(args.prompts)
    except SessionError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_RUN_FAILED
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
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    # The profile is resolved here, before anything is written, so a
    # misspelled name is a usage error rather than a run that goes out on a
    # profile the operator did not choose. Selecting the default profile
    # yields the config unchanged, which is why this is unconditional.
    try:
        config = profile_config(config, getattr(args, "profile", None))
    except ConfigError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if load_api_key(config) is None:
        print("pocketchat: %s is not set" % config["api_key_env"], file=sys.stderr)
        return EXIT_USAGE

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
            print("pocketchat: %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        if isinstance(recorded, str) and recorded.strip():
            recorded_trace = (session_path.parent / recorded).resolve()
            if Path(args.trace).resolve() != recorded_trace:
                print(
                    "pocketchat: session %s records trace %s; --trace must "
                    "match it or be omitted" % (session_path, recorded_trace),
                    file=sys.stderr,
                )
                return EXIT_USAGE

    try:
        session = Session(config=config, trace_path=trace_path, run_id=run_id,
                          session_path=session_path, verbose=args.verbose)
    except ConfigError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE
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
    if not args.quiet and status == "completed" and session.turn_count:
        # The last reply is the one the human is looking at.
        echo_reply(session.last_reply())
    if not args.quiet:
        goodbye()
    print(json.dumps({"status": status, "run_id": session.run_id,
                      "turns": session.turn_count,
                      "trace": str(trace_path)}, sort_keys=True))
    return EXIT_OK if status == "completed" else EXIT_RUN_FAILED


def _cmd_show(args: argparse.Namespace) -> int:
    """Print one session: the conversation, then the events that recorded it.

    Both are read from the files rather than from a running process, so this
    command works after the fact and cannot be changed by what an operator did
    since. The reading lives in the service layer; what is left here is the
    printing -- the conversation first, then the trace, then the reason no
    trace follows when there is none.
    """
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
    return EXIT_OK


def _cmd_export(args: argparse.Namespace) -> int:
    """Write one self-contained copy of a session, in one of three formats.

    The whole orchestration -- resolving the pair, rendering the body,
    refusing an existing output, writing the file -- lives in the service
    layer and is shared with whatever surface comes next; this command is
    the argument parse and the one-line result.
    """
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
