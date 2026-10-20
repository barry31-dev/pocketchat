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
from typing import Dict, Iterable, List, Optional, Tuple

from . import __version__
from .compare import (ComparisonManifestError, comparison_manifest_path,
                      run_comparison, write_comparison_manifest)
from .config import (CONFIG_TEMPLATE, ConfigError, load_api_key, load_config,
                     profile_config)
from .replay import ReplayError, replay_snapshots
from .repl import echo_reply, goodbye, read_lines
from .sessions import (SessionFileError, looks_like_session,
                       looks_like_trace, read_header, read_messages,
                       resolve_session_path, session_for_trace)
from .session import Session, SessionError, new_run_id

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


def _read_target(args: argparse.Namespace) -> Optional[Path]:
    """The file a read-only command was pointed at, checked for existence.

    A bare name is looked up under ``./sessions``, so ``show notes`` and
    ``show sessions/notes.jsonl`` address the same file. Anything else is taken
    as written, which is how a trace path is given directly.
    """
    target = Path(args.session)
    if target.exists() and target.is_file():
        return target
    if target.parent == Path("."):
        candidate = Path(DEFAULT_SESSIONS_DIR) / (
            target.name if target.name.endswith(".jsonl") else target.name + ".jsonl"
        )
        if candidate.is_file():
            return candidate
    return None


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
    if args.command == "profile":
        return _cmd_profile(args)
    if args.command == "compare":
        return _cmd_compare(args)
    if args.command == "replay":
        return _cmd_replay(args)
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
    since. The two sides of the pair are each acceptable as an argument, and
    the link between them comes from the session's own header -- the trace is
    named after the run id and the session after whatever the operator called
    it, so matching the file names would pair the wrong conversation.
    """
    target = _read_target(args)
    if target is None:
        print("pocketchat: no such session: %s" % args.session, file=sys.stderr)
        return EXIT_USAGE

    try:
        session, trace = _resolve_pair(target)
    except SessionFileError as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if session is not None:
        header = read_header(session)
        messages = read_messages(session)
        print("session %s  started %s  messages %d"
              % (header.get("session"), header.get("created"), len(messages)))
        for message in messages:
            label = "you" if message["role"] == "user" else "model"
            print("")
            print("%s> %s" % (label, message["content"]))
        print("")

    if trace is None:
        print("pocketchat: %s has no trace to show" % args.session,
              file=sys.stderr)
        return EXIT_USAGE
    if not trace.is_file():
        print("pocketchat: trace not found: %s (recorded by %s)"
              % (trace, session or args.session), file=sys.stderr)
        return EXIT_USAGE

    print("trace %s" % trace)
    for number, line in enumerate(
            trace.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            print("  line %d: invalid JSON: %s" % (number, exc))
            continue
        print("%4d  %-12s %s" % (number, event.get("event_id", "-"),
                                  event.get("type", "-")))
    return EXIT_OK


def _resolve_pair(target: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """Map the named file to its (session, trace) pair.

    Either side is accepted as the argument. A file that is neither -- a JSONL
    file someone else's tool wrote, a log, an empty file -- is refused rather
    than treated as a trace with no events, because "this file is not a
    session" and "this session recorded no events" need different fixes.
    """
    if looks_like_session(target):
        recorded = read_header(target).get("trace")
        trace = None
        if isinstance(recorded, str) and recorded.strip():
            trace = (target.parent / recorded).resolve()
        return target, trace
    if looks_like_trace(target):
        return session_for_trace(target), target
    raise SessionFileError("%s is not a pocketchat session or trace file" % target)


def _cmd_export(args: argparse.Namespace) -> int:
    """Write one self-contained copy of a session, deterministic by construction.

    Exporting twice must produce two identical files. That is not a nicety: a
    diff between two exports is the only way to tell "this session changed"
    from "the exporter is not deterministic", so nothing time-dependent, path
    dependent or id-dependent may enter the output unless it is read back from
    the session's own files.
    """
    target = _read_target(args)
    if target is None:
        print("pocketchat: no such session: %s" % args.session, file=sys.stderr)
        return EXIT_USAGE
    if args.format == "trace":
        return _cmd_export_trace(args, target)
    try:
        session, trace = _resolve_pair(target)
        if session is not None:
            header = read_header(session)
            messages = read_messages(session)
            trace_text = trace.read_text(encoding="utf-8") if (
                trace is not None and trace.is_file()) else ""
    except (SessionFileError, OSError) as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    if session is None:
        # A trace alone carries the events, not the conversation. Exporting it
        # would produce a file that reads as a session but is missing half of
        # what one is, so the session is required rather than optional.
        print("pocketchat: %s has no session file to export" % args.session,
              file=sys.stderr)
        return EXIT_USAGE

    if args.format == "md":
        body = _render_markdown(header, messages)
    else:
        body = _render_jsonl(header, messages, trace_text)

    out = args.out
    if out is None:
        out = session.with_suffix("")
        out = Path(str(out) + ".export." + args.format)
    if out.exists():
        print("pocketchat: %s already exists (use --out)" % out, file=sys.stderr)
        return EXIT_USAGE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(json.dumps({"format": args.format, "session": str(session),
                      "out": str(out), "messages": len(messages),
                      "trace_events": len(_trace_events(trace_text))},
                     sort_keys=True))
    return EXIT_OK


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def _read_export_jsonl(path: Path) -> Tuple[bytes, List[Tuple[int, Dict]]]:
    """Read every record before writing, retaining the original bytes and lines."""
    body = path.read_bytes()
    records = []
    for number, line in enumerate(body.split(b"\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"),
                                parse_constant=_reject_json_constant)
        except ValueError as exc:
            raise SessionFileError(
                "%s line %d: invalid JSON or UTF-8" % (path, number)
            ) from exc
        if not isinstance(record, dict):
            raise SessionFileError(
                "%s line %d: expected a JSON object" % (path, number)
            )
        records.append((number, record))
    if not records:
        raise SessionFileError("%s has no records to export" % path)
    return body, records


def _cmd_export_trace(args: argparse.Namespace, target: Path) -> int:
    """Copy existing events only; full schema validation stays with Trace Doctor."""
    try:
        source = target
        body, records = _read_export_jsonl(target)
        first = records[0][1]
        if "session" in first and "kind" not in first:
            recorded = first.get("trace")
            if not isinstance(recorded, str) or not recorded.strip():
                raise SessionFileError("%s has no trace to export" % target)
            source = (target.parent / recorded).resolve()
            body, records = _read_export_jsonl(source)

        required = {"schema_version", "event_id", "timestamp", "run_id", "type"}
        for number, event in records:
            if "kind" in event or any(
                    not isinstance(event.get(field), str) or not event[field]
                    for field in required):
                raise SessionFileError(
                    "%s line %d: expected a trace event, not a session archive"
                    % (source, number)
                )

        out = args.out or Path(str(target.with_suffix("")) + ".export.trace.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects the input when --out names it.
        with out.open("xb") as handle:
            handle.write(body)
    except FileExistsError:
        print("pocketchat: %s already exists (use --out)" % out, file=sys.stderr)
        return EXIT_USAGE
    except (SessionFileError, OSError) as exc:
        print("pocketchat: %s" % exc, file=sys.stderr)
        return EXIT_USAGE

    print(json.dumps({"format": "trace", "source": str(source), "out": str(out),
                      "trace_events": len(records)}, sort_keys=True))
    return EXIT_OK


def _trace_events(trace_text: str) -> List[Dict]:
    """The events in a trace text, under the archive's own rules.

    The mixed archive skips a blank line and a malformed one rather than
    failing the export, so the count of what it copied has to be taken the
    same way. Counting lines instead reports evidence the file does not
    contain.
    """
    events = []
    for line in trace_text.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _render_jsonl(header: Dict, messages: List[Dict[str, str]],
                  trace_text: str) -> str:
    """A single JSONL document: a header, every message, every event.

    One file, one line per record, the session's own ordering. Nothing is
    generated here except the ``kind`` field that tells a reader which of the
    three a line is.
    """
    lines = [json.dumps({"kind": "session", **header},
                        sort_keys=True, ensure_ascii=False)]
    for message in messages:
        lines.append(json.dumps({"kind": "message", **message},
                                sort_keys=True, ensure_ascii=False))
    for event in _trace_events(trace_text):
        lines.append(json.dumps({"kind": "event", **event},
                                sort_keys=True, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def _render_markdown(header: Dict, messages: List[Dict[str, str]]) -> str:
    """The same conversation as prose, for reading rather than for parsing."""
    out = ["# pocketchat session %s" % header.get("session"), ""]
    created = header.get("created")
    if created:
        out.append("started %s" % created)
        out.append("")
    for message in messages:
        label = "You" if message["role"] == "user" else "Model"
        out.append("**%s.**" % label)
        out.append("")
        out.append(message["content"])
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
