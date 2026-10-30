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
import threading
from dataclasses import dataclass
from http.server import HTTPServer

from pocketchat.cli import main as _cli_main


class StubHTTPServer:
    """A local stub provider with a bounded, deterministic shutdown.

    The suite starts many local servers. Keeping the lifecycle in one place
    makes it harder for a test to leave a serving thread behind, and lets the
    poll interval be short without every test spelling out the same cleanup.
    """

    def __init__(self, handler):
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01),
            daemon=True,
        )
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@dataclass
class Result:
    """The three fields the suites read off a ``CompletedProcess``."""

    returncode: int
    stdout: str
    stderr: str


def run_cli(argv, cwd, env=None, stdin="", stdin_isatty=False,
            run_tui=None):
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
        stream = io.StringIO(stdin)
        stream.isatty = lambda: stdin_isatty  # type: ignore[attr-defined]
        sys.stdin = stream
        patched_tui = None
        if run_tui is not None:
            from pocketchat import cli as _cli_module
            patched_tui = _cli_module.run_tui
            _cli_module.run_tui = run_tui
        with contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = _cli_main(list(argv))
            except SystemExit as exc:  # argparse exits through SystemExit
                code = exc.code if isinstance(exc.code, int) else (
                    0 if exc.code is None else 2)
        return Result(code, out.getvalue(), err.getvalue())
    finally:
        if 'patched_tui' in locals() and patched_tui is not None:
            from pocketchat import cli as _cli_module
            _cli_module.run_tui = patched_tui
        os.chdir(previous_cwd)
        sys.stdin = previous_stdin
        os.environ.clear()
        os.environ.update(previous_env)
