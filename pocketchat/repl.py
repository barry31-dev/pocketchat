"""The interactive loop: lines in, prompts out, one session.

This module deliberately does not talk to a model, open a socket or write a
trace. It reads lines and hands them to :class:`~pocketchat.session.Session`,
which owns the trace. That split is the whole reason it is a separate file:
an interactive command that owned its own trace path would be a second entry
point, and the one shape this package refuses to produce is a run that starts
and never ends.

So ``chat`` feeds one long-lived ``Session`` instead of starting one per
line. ``Session.run`` stays the single exit: the REPL ends either by returning
from its iterator (EOF or an exit word) or by letting ``KeyboardInterrupt``
through, so both ways still go through the one place that writes the terminal
event.
"""

from __future__ import annotations

import sys
from typing import Iterator, Optional

PROMPT = "you> "
EXIT_WORDS = ("/exit", "/quit", "/bye")


def read_lines(stream: Optional[object] = None,
               prompt: str = PROMPT) -> Iterator[str]:
    """Yield one prompt per line, until EOF or an explicit exit word.

    Ends on EOF or an exit word. A ``KeyboardInterrupt`` raised while waiting
    for a line is not swallowed: nothing is in flight, so interrupting here
    means the user is ending the conversation, and the run must be recorded as
    cancelled rather than silently continuing.
    """
    handle = stream if stream is not None else sys.stdin
    interactive = bool(getattr(handle, "isatty", lambda: False)())
    while True:
        if interactive:
            try:
                sys.stdout.write(prompt)
                sys.stdout.flush()
            except (OSError, ValueError):
                # A closed stdout means there is nobody left to prompt.
                return
        try:
            line = handle.readline()
        except KeyboardInterrupt:
            if interactive:
                sys.stdout.write("\n")
                sys.stdout.flush()
            # A Ctrl-C while waiting for a line interrupts the conversation
            # itself, not a turn: nothing is in flight. Re-raising is what
            # makes the run end as `cancelled`; swallowing it here would
            # leave a conversation the user has already walked away from.
            raise
        except (OSError, ValueError):
            return
        if not line:
            return
        text = line.rstrip("\r\n")
        stripped = text.strip()
        if stripped.lower() in EXIT_WORDS:
            return
        if not stripped:
            # An empty line is never a prompt. At a terminal it is a slip; in
            # a pipe it is a blank line in a script. Sending it would put an
            # empty user message in the context and a request on the wire for
            # nothing, so both modes skip it.
            if interactive:
                sys.stdout.write("\n")
                sys.stdout.flush()
            continue
        yield stripped


def echo_reply(text: str, stream: Optional[object] = None) -> None:
    """Print one reply. The trace already has it; this is for the human."""
    handle = stream if stream is not None else sys.stdout
    handle.write("%s\n" % text)
    try:
        handle.flush()
    except (OSError, ValueError):
        pass


def goodbye(stream: Optional[object] = None) -> None:
    handle = stream if stream is not None else sys.stdout
    handle.write("bye\n")
    try:
        handle.flush()
    except (OSError, ValueError):
        pass
