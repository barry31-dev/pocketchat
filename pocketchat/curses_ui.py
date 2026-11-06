"""The standard-library curses renderer for the interactive agent.

This module is deliberately thin: ``apply_key`` turns one key into a controller
action and ``run_curses`` draws ``compose_screen``. Keeping key handling out of
the screen loop is what lets it be tested without a terminal.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass, field
from typing import List, Optional

from .tui import AgentTUI, compose_screen_layout


@dataclass
class InputState:
    text: str = ""
    cursor: int = 0
    history: List[str] = field(default_factory=list)
    history_index: Optional[int] = None


def _insert(state: InputState, value: str) -> None:
    state.text = state.text[:state.cursor] + value + state.text[state.cursor:]
    state.cursor += len(value)


def _backspace(state: InputState) -> None:
    if state.cursor:
        state.text = state.text[:state.cursor - 1] + state.text[state.cursor:]
        state.cursor -= 1


def _history(state: InputState, delta: int) -> None:
    if not state.history:
        return
    if state.history_index is None:
        state.history_index = len(state.history)
    state.history_index = max(0, min(len(state.history) - 1,
                                     state.history_index + delta))
    state.text = state.history[state.history_index]
    state.cursor = len(state.text)


def apply_key(tui: AgentTUI, state: InputState, key) -> str:
    """Apply one key and return ``continue``, ``exit`` or ``send``.

    The caller owns the event loop and sends ``state.text`` after ``send``.
    Keeping the return value explicit avoids a renderer that exits or sends as
    a side effect of a key helper.
    """
    if isinstance(key, str) and key == "\x04":
        return "exit"
    if isinstance(key, str) and key == "\x03":
        if tui.busy:
            tui.cancel_send()
            return "continue"
        return "exit"
    if tui.picker_open:
        if key in (curses.KEY_UP, "k"):
            tui.move_picker(-1)
        elif key in (curses.KEY_DOWN, "j"):
            tui.move_picker(1)
        elif key in (curses.KEY_ENTER, "\n"):
            reply = tui.choose_picker(tui.picker_index)
            if reply:
                tui.transcript.append(("agent", reply))
        elif key in ("\x1b", "q"):
            tui.close_picker()
        return "continue"
    command_matches = tui.command_completions(state.text)
    if command_matches and key in (curses.KEY_UP, curses.KEY_DOWN):
        tui.move_command_completion(
            state.text, -1 if key == curses.KEY_UP else 1)
        return "continue"
    if command_matches and key == "\t":
        selected = tui.apply_command_completion(state.text)
        if selected is not None:
            state.text = selected
            state.cursor = len(selected)
            tui.reset_command_completion()
        return "continue"
    if command_matches and key == "\x1b":
        state.text = ""
        state.cursor = 0
        tui.reset_command_completion()
        return "continue"
    if command_matches and key in (curses.KEY_ENTER, "\n"):
        selected = tui.apply_command_completion(state.text)
        if selected is not None and selected.strip() != state.text.strip():
            state.text = selected
            state.cursor = len(selected)
            tui.reset_command_completion()
            return "continue"
    if isinstance(key, str) and key == "\x14":
        tui.toggle_reasoning()
        return "continue"
    if isinstance(key, tuple) and len(key) == 2 and key[0] == "mouse":
        if key[1] & getattr(curses, "BUTTON4_PRESSED", 0):
            tui.scroll_transcript(1, max(4, 8))
        elif key[1] & getattr(curses, "BUTTON5_PRESSED", 0):
            tui.scroll_transcript(-1, max(4, 8))
        return "continue"
    if key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
        height, _ = getattr(state, "screen_size", (24, 80))
        page = max(4, height - 8)
        tui.scroll_transcript(
            page if key == curses.KEY_PPAGE else -page,
            page)
        return "continue"
    if key == curses.KEY_END:
        tui.transcript_offset = 0
        return "continue"
    if key == curses.KEY_UP:
        _history(state, -1)
        return "continue"
    if key == curses.KEY_DOWN:
        _history(state, 1)
        return "continue"
    if key == curses.KEY_LEFT:
        state.cursor = max(0, state.cursor - 1)
        return "continue"
    if key == curses.KEY_RIGHT:
        state.cursor = min(len(state.text), state.cursor + 1)
        return "continue"
    if key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
        _backspace(state)
        return "continue"
    if key == "\t":
        matches = tui.matching_commands(state.text.strip())
        if len(matches) == 1:
            state.text = matches[0]
            state.cursor = len(state.text)
        return "continue"
    if key == 10:
        _insert(state, "\n")
        return "continue"
    if key == "\r":
        _insert(state, "\n")
        return "continue"
    if key in (curses.KEY_ENTER, "\n"):
        text = state.text.strip()
        if not text:
            return "continue"
        if text.lower() in ("/exit", "/quit", "/bye"):
            return "exit"
        state.history.append(text)
        state.history_index = None
        state.text = ""
        state.cursor = 0
        tui.reset_command_completion()
        tui.start_send(text)
        return "continue" if text.startswith("/") else "send"
    if isinstance(key, str) and key.isprintable():
        _insert(state, key)
        tui.reset_command_completion()
    return "continue"


def read_key(window):
    """Read one key, including keys hidden behind a nodelay ``curses.error``.

    ``get_wch`` raises when nodelay finds nothing; on some terminals it also
    raises instead of returning a control character. The fallback ``getch`` is
    what keeps Ctrl+D/Ctrl+C and the arrow keys from being mistaken for an
    idle screen.
    """
    try:
        value = window.get_wch()
    except curses.error:
        value = window.getch()
        if value == -1:
            raise
    if value == curses.KEY_MOUSE:
        try:
            _id, _x, _y, _z, state = curses.getmouse()
        except curses.error:
            return value
        return ("mouse", state)
    if value == "\x1b" or value == 27:
        try:
            second = window.getch()
        except curses.error:
            return "\x1b"
        if second != 91:
            return "\x1b"
        try:
            third = window.getch()
        except curses.error:
            return "\x1b"
        return {
            65: curses.KEY_UP,
            66: curses.KEY_DOWN,
            67: curses.KEY_RIGHT,
            68: curses.KEY_LEFT,
        }.get(third, "\x1b")
    if isinstance(value, int) and 0 <= value < 256:
        return chr(value)
    return value


def _enable_mouse_wheel() -> None:
    """Request wheel events without making click reporting a requirement."""
    try:
        curses.mousemask(getattr(curses, "BUTTON4_PRESSED", 0)
                         | getattr(curses, "BUTTON5_PRESSED", 0))
    except (AttributeError, curses.error):
        pass


def _draw(stdscr, tui: AgentTUI, state: InputState) -> None:
    height, width = stdscr.getmaxyx()
    layout = compose_screen_layout(
        tui,
        input_text=state.text,
        width=width,
        height=max(8, height),
        cursor=state.cursor,
    )
    lines = layout.rows
    stdscr.erase()
    for row, line in enumerate(lines[:height]):
        try:
            stdscr.addnstr(row, 0, line, max(0, width - 1))
        except curses.error:
            pass
    try:
        stdscr.move(layout.cursor_row, layout.cursor_col)
    except curses.error:
        pass
    stdscr.refresh()


def _loop(stdscr, tui: AgentTUI, max_iterations=None, sleeper=None) -> int:
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    stdscr.keypad(True)
    _enable_mouse_wheel()
    stdscr.nodelay(True)
    state = InputState()
    iterations = 0
    while True:
        iterations += 1
        _draw(stdscr, tui, state)
        if max_iterations is not None and iterations >= max_iterations:
            tui.close()
            return 0
        try:
            key = read_key(stdscr)
        except curses.error:
            try:
                (sleeper or curses.napms)(30 if tui.busy else 60)
            except KeyboardInterrupt:
                if tui.busy:
                    tui.cancel_send()
                    continue
                tui.close()
                return 130
            continue
        except KeyboardInterrupt:
            if tui.busy:
                tui.cancel_send()
                continue
            tui.close()
            return 130
        action = apply_key(tui, state, key)
        if action == "exit":
            tui.close()
            return 0
    return 0


def run_loop_for_test(stdscr, tui, max_iterations=None) -> int:
    return _loop(stdscr, tui, max_iterations=max_iterations,
                 sleeper=lambda milliseconds: None)


def run_curses(tui: AgentTUI) -> int:
    """Run the full-screen agent until Ctrl+D or /exit."""
    try:
        return int(curses.wrapper(lambda stdscr: _loop(stdscr, tui)))
    except (curses.error, OSError):
        tui.close()
        return 2
