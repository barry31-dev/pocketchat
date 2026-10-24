"""In-process CLI driving, shared by the suites.

Running the CLI in a subprocess costs a full interpreter start per call,
which is most of the suite's wall time. ``cli.main(argv)`` is a pure function
of argv, so most tests drive it in process: same exit codes, same stdout,
same files on disk, no network and no credential -- and the working
directory, environment and standard streams are restored before returning.

The subprocess stays the golden path: one end-to-end test per command family
keeps a real interpreter, a real pipe and a real exit in the suite.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from dataclasses import dataclass

from pocketchat.cli import main as _cli_main


@dataclass
class Result:
    """The three fields the suites read off a ``CompletedProcess``."""

    returncode: int
    stdout: str
    stderr: str


def run_cli(argv, cwd, env=None, stdin=""):
    """Run one CLI command in this process and return its result.

    ``env`` maps names to values; a value of ``None`` removes the variable,
    which is how a missing-credential path is tested. A value is never
    inherited quietly: only the names the caller passes are set, and the
    caller's whole environment is restored afterwards, so a variable one test
    sets cannot leak into the next.
    """
    out, err = io.StringIO(), io.StringIO()
    previous_cwd = os.getcwd()
    previous_env = dict(os.environ)
    previous_stdin = sys.stdin
    try:
        os.chdir(str(cwd))
        for key, value in (env or {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        # There is no contextlib.redirect_stdin; the REPL reads ``sys.stdin``
        # at call time, so assigning it here reaches ``chat`` the same way.
        sys.stdin = io.StringIO(stdin)
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = _cli_main(list(argv))
            except SystemExit as exc:  # argparse exits through SystemExit
                code = exc.code if isinstance(exc.code, int) else (
                    0 if exc.code is None else 2)
        return Result(code, out.getvalue(), err.getvalue())
    finally:
        os.chdir(previous_cwd)
        sys.stdin = previous_stdin
        os.environ.clear()
        os.environ.update(previous_env)
