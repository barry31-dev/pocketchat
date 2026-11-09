"""Terminal UI state for the interactive agent.

The controller in this module owns the parts that are testable without a
terminal: command discovery, pickers and the status line. The curses renderer
below it only draws that state and forwards keys. Keeping the split means the
interactive surface can be verified in a pipe, while the existing REPL remains
the fallback for terminals the renderer cannot use.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import unicodedata
from typing import Any, Dict, List, Optional

from .agent import AgentCommandError, AgentState, apply_command
from .config import ConfigError, profile_config
from .cost import estimate_cost
from .session import Session, new_run_id
from .session_writer import SessionWriterBusy, acquire_session_writer
from .sessions import resolve_session_path


COMMANDS = ("/context", "/exit", "/model", "/reasoning", "/session", "/status")


def empty_status_facts() -> Dict[str, Any]:
    return {
        "usage_known": False,
        "tokens": None,
        "cost_usd": None,
        "ttft_ms": None,
        "attempt_index": None,
        "compactions": 0,
        "last_compaction": None,
    }


def trace_status_facts(trace_path: Path,
                       config: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate display facts from the trace itself."""
    facts = empty_status_facts()
    completed = 0
    usage_known = False
    input_tokens = 0
    output_tokens = 0
    try:
        lines = Path(trace_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return facts
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = event
        event_type = event.get("type") or payload.get("type")
        attempt = payload.get("attempt_index")
        if isinstance(attempt, int) and not isinstance(attempt, bool):
            current = facts["attempt_index"]
            facts["attempt_index"] = max(
                attempt, current if isinstance(current, int) else attempt)
        if event_type == "model.response.completed":
            usage = payload.get("usage")
            valid_usage = (
                isinstance(usage, dict)
                and isinstance(usage.get("input_tokens"), int)
                and not isinstance(usage.get("input_tokens"), bool)
                and isinstance(usage.get("output_tokens"), int)
                and not isinstance(usage.get("output_tokens"), bool)
            )
            reported = payload.get("usage_reported") is True and valid_usage
            usage_known = reported if completed == 0 else usage_known and reported
            completed += 1
            if valid_usage:
                for key, target in (("input_tokens", "input"),
                                    ("output_tokens", "output")):
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        if target == "input":
                            input_tokens += max(0, value)
                        else:
                            output_tokens += max(0, value)
            ttft = payload.get("time_to_first_token_ms")
            if isinstance(ttft, int) and not isinstance(ttft, bool) and ttft >= 0:
                facts["ttft_ms"] = ttft
        elif event_type == "context.compacted":
            facts["compactions"] += 1
            facts["last_compaction"] = dict(payload)
    if completed and usage_known:
        facts["usage_known"] = True
        facts["tokens"] = input_tokens + output_tokens
        facts["cost_usd"] = estimate_cost(config, {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })
    return facts


class TUIUnavailable(RuntimeError):
    """The optional full-screen renderer is unavailable on this platform."""


@dataclass
class AgentTUI:
    state: AgentState
    profiles: Dict[str, dict]
    models: Dict[str, str]
    transcript: List[tuple] = field(default_factory=list)
    config: Optional[Dict[str, Any]] = None
    verbose: bool = False
    workspace: Path = field(default_factory=Path.cwd)
    _picker: Optional[str] = None
    _picker_items: List[str] = field(default_factory=list)
    _picker_index: int = 0
    _session: Optional[Session] = None
    _session_profile: Optional[str] = None
    _session_name: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _generation: int = 0
    _worker: Optional[threading.Thread] = None
    busy: bool = False
    last_error: str = ""
    streaming_content: str = ""
    streaming_reasoning: str = ""
    thinking_started: Optional[float] = None
    reasoning_expanded: bool = False
    transcript_offset: int = 0
    command_index: int = 0
    status_facts: Dict[str, Any] = field(default_factory=empty_status_facts)

    def on_delta(self, kind: str, text: str) -> None:
        """Receive one display delta without changing trace semantics."""
        with self._lock:
            if kind == "reasoning":
                self.streaming_reasoning += text
            elif kind == "content":
                self.streaming_content += text

    def thinking_line(self) -> str:
        if not self.busy:
            return ""
        elapsed = 0.0
        if self.thinking_started is not None:
            elapsed = max(0.0, __import__("time").time() - self.thinking_started)
        return "thinking... %ds · %s" % (int(elapsed), self.state.model)

    def toggle_reasoning(self) -> None:
        self.reasoning_expanded = not self.reasoning_expanded

    def scroll_transcript(self, delta: int, page_height: int,
                          total_lines: Optional[int] = None) -> None:
        total = (self._transcript_line_count() if total_lines is None
                 else total_lines)
        maximum = max(0, total - page_height)
        self.transcript_offset = max(
            0, min(maximum, self.transcript_offset + delta))

    def _transcript_line_count(self) -> int:
        return len(_message_rows(self, 80)) + (6 if self.busy else 0)

    def profile_names(self) -> List[str]:
        return sorted(self.profiles)

    def command_names(self) -> List[str]:
        return list(COMMANDS)

    def matching_commands(self, prefix: str) -> List[str]:
        return [name for name in COMMANDS if name.startswith(prefix)]

    def command_completions(self, input_text: str) -> List[str]:
        prefix = input_text.strip()
        if not prefix.startswith("/") or " " in prefix:
            return []
        needle = prefix[1:].lower()
        matches = [
            name for name, description in COMMAND_HELP
            if name[1:].startswith(needle)
            or needle in description.lower()
        ]
        if not needle:
            matches = [name for name, _ in COMMAND_HELP]
        return matches

    def reset_command_completion(self) -> None:
        self.command_index = 0

    def move_command_completion(self, input_text: str, delta: int) -> None:
        matches = self.command_completions(input_text)
        if not matches:
            self.command_index = 0
            return
        self.command_index = (self.command_index + delta) % len(matches)

    def selected_command(self, input_text: str) -> Optional[str]:
        matches = self.command_completions(input_text)
        if not matches:
            return None
        index = max(0, min(len(matches) - 1, self.command_index))
        return matches[index]

    def apply_command_completion(self, input_text: str) -> Optional[str]:
        selected = self.selected_command(input_text)
        return selected

    @property
    def picker_open(self) -> bool:
        return self._picker is not None

    @property
    def picker_index(self) -> int:
        return self._picker_index

    def open_model_picker(self) -> None:
        self._picker = "model"
        self._picker_items = self.profile_names()
        current = self.state.profile
        self._picker_index = (
            self._picker_items.index(current) if current in self._picker_items else 0)

    def move_picker(self, delta: int) -> None:
        if not self._picker_items:
            return
        self._picker_index = max(
            0, min(len(self._picker_items) - 1, self._picker_index + delta))

    def close_picker(self) -> None:
        self._picker = None
        self._picker_items = []
        self._picker_index = 0

    def picker_options(self) -> List[str]:
        return list(self._picker_items)

    def choose_picker(self, index: int) -> Optional[str]:
        if self._picker != "model":
            return None
        if index < 0 or index >= len(self._picker_items):
            return None
        name = self._picker_items[index]
        self._picker = None
        self._picker_items = []
        self._picker_index = 0
        return self.run_command("/model %s" % name)

    def send(self, text: str) -> str:
        """Handle one submitted line without starting an empty run."""
        stripped = text.strip()
        if not stripped:
            return ""
        if self.busy:
            return "agent: a reply is already running"
        if stripped.startswith("/"):
            return self.run_command(stripped)
        return self._send_once(stripped, record=True)

    def _send_once(self, stripped: str, *, record: bool = False) -> str:
        if self.config is None:
            return "agent: configuration is not loaded"
        try:
            selected = profile_config(self.config, self.state.profile)
        except ConfigError as exc:
            return "agent: %s" % exc
        selected["max_context_chars"] = self.state.context_chars
        selected["reasoning_effort"] = self.state.reasoning
        if (self._session is None or self._session_profile != self.state.profile
                or self._session_name != self.state.session):
            session_path = resolve_session_path(
                Path(self.state.session), "sessions")
            if not session_path.is_absolute():
                session_path = self.workspace / session_path
            run_id = new_run_id()
            trace_path = self.workspace / "traces" / ("%s.jsonl" % run_id)
            try:
                self._session = Session(
                    config=selected,
                    trace_path=trace_path,
                    run_id=run_id,
                    session_path=session_path,
                    verbose=self.verbose,
                    on_delta=self.on_delta,
                )
            except ConfigError as exc:
                return "agent: %s" % exc
            self._session_profile = self.state.profile
            self._session_name = self.state.session
        else:
            self._session.config = selected
        try:
            self._ensure_session_writer(self._session)
        except SessionWriterBusy as exc:
            self.last_error = "session writer unavailable: %s" % exc
            return "agent: %s" % self.last_error
        status = self._session.run([stripped])
        session = self._session
        self.status_facts = trace_status_facts(
            session.trace_path, session.config)
        reply = session.last_reply()
        persistence_failed = session.session_file is None
        # Session.run releases its writer claim but the object still owns the
        # cumulative message and token state for this TUI conversation. Keep
        # that state; the next run lazily acquires a fresh claim without
        # resetting max_session_tokens.
        if status != "completed":
            error = session.last_error if isinstance(session.last_error, dict) else {}
            detail = error.get("type") or status
            self.last_error = detail
            return "agent: run %s: %s" % (status, detail)
        if persistence_failed:
            self.last_error = "session persistence failed; this turn is trace-only"
            if reply:
                return "%s\n\n[%s]" % (reply, self.last_error)
            return "agent: %s" % self.last_error
        self.last_error = ""
        if record:
            self.transcript.append(("you", stripped))
            self.transcript.append(("agent", reply))
        return reply

    def _ensure_session_writer(self, session: Session) -> None:
        """Acquire one fresh writer claim immediately before a run."""
        if session._session_writer is not None or session.session_file is None:
            return
        writer = acquire_session_writer(
            session.session_file.path, owner=session.run_id)
        try:
            writer.bind_session_file(session.session_file.path)
        except SessionWriterBusy:
            writer.release()
            raise
        session._session_writer = writer

    def start_send(self, text: str) -> None:
        """Start one turn without blocking the renderer."""
        stripped = text.strip()
        if not stripped or self.busy:
            return
        if stripped.startswith("/"):
            reply = self.run_command(stripped)
            if reply and reply != "model picker":
                self.transcript.append(("agent", reply))
            return
        with self._lock:
            self.transcript.append(("you", stripped))
            self.transcript_offset = 0
            self.busy = True
            self.streaming_content = ""
            self.streaming_reasoning = ""
            self.thinking_started = __import__("time").time()
            self._generation += 1
            generation = self._generation
        def work() -> None:
            try:
                reply = self._send_once(stripped)
            except KeyboardInterrupt:
                reply = "agent: interrupted"
            except Exception as exc:
                reply = "agent: %s: %s" % (type(exc).__name__, exc)
            with self._lock:
                if generation != self._generation:
                    return
                if self.streaming_reasoning:
                    self.transcript.append(("reasoning", self.streaming_reasoning))
                if reply:
                    self.transcript.append(("agent", reply))
                elif self.streaming_content:
                    self.transcript.append(("agent", self.streaming_content))
                self.busy = False
                self.streaming_content = ""
                self.streaming_reasoning = ""
                self.thinking_started = None
                self._worker = None
        worker = threading.Thread(target=work, daemon=True)
        with self._lock:
            self._worker = worker
        worker.start()

    def _interrupt_worker(self, timeout: float = 2.0) -> None:
        worker = self._worker
        if worker is None or not worker.is_alive():
            return
        thread_id = worker.ident
        if thread_id is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread_id), ctypes.py_object(KeyboardInterrupt))
        worker.join(timeout)

    def cancel_send(self) -> None:
        """Interrupt the current request and drop any late response."""
        with self._lock:
            self._generation += 1
            self.busy = False
            worker = self._worker
        if worker is not None and worker.is_alive():
            self._interrupt_worker()
        with self._lock:
            if self._worker is worker:
                self._worker = None

    def close(self) -> None:
        """Finish the current turn as cancelled before the renderer exits."""
        self.cancel_send()

    def run_command(self, line: str) -> str:
        if line.strip() == "/model":
            self.open_model_picker()
            return "model picker"
        try:
            response = apply_command(self.state, line, self.profiles, self.models)
        except AgentCommandError as exc:
            return "agent: %s" % exc
        return response or ""

    def status_line(self) -> str:
        return self.state.status_line()



@dataclass
class ScreenLayout:
    rows: List[str]
    cursor_row: int
    cursor_col: int


def _cell_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _cell_width(text) <= width:
        return text
    out = ""
    limit = max(0, width - 1)
    for char in text:
        if _cell_width(out + char) > limit:
            break
        out += char
    return out + "…"


def _wrap(text: str, width: int, prefix: str = "") -> List[str]:
    """Wrap by terminal cell width, preserving CJK and explicit newlines."""
    if width <= 0:
        return [""]
    out: List[str] = []
    current = prefix
    for char in text:
        if char == "\n":
            out.append(current)
            current = prefix
            continue
        if current != prefix and _cell_width(current + char) > width:
            out.append(current)
            current = prefix
        current += char
    out.append(current)
    return out


PROMPT = "❯ "
PROMPT_WIDTH = 2
CONTINUATION = "  "


def _composer_lines(text: str, width: int):
    """Return (start, end, text) visual lines using the configured input columns."""
    input_width = max(1, width - 4)
    lines = []
    current: List[str] = []
    current_width = 0
    start = 0
    for index, char in enumerate(text):
        if char == "\n":
            lines.append((start, index, "".join(current)))
            current = []
            current_width = 0
            start = index + 1
            continue
        char_width = _cell_width(char)
        if current and current_width + char_width > input_width:
            lines.append((start, index, "".join(current)))
            current = []
            current_width = 0
            start = index
        current.append(char)
        current_width += char_width
    lines.append((start, len(text), "".join(current)))
    return lines


def _composer_visual(text: str, width: int, cursor: Optional[int],
                     max_rows: int):
    lines = _composer_lines(text, width)
    pos = len(text) if cursor is None else max(0, min(cursor, len(text)))
    selected = 0
    for index, (start, _, _) in enumerate(lines):
        if start <= pos:
            selected = index
    if len(lines) > max_rows:
        start = max(0, min(selected - max_rows // 2,
                           len(lines) - max_rows))
        visible = lines[start:start + max_rows]
        selected_visible = selected - start
    else:
        start = 0
        visible = lines
        selected_visible = selected
    rows = []
    for offset, (_, _, content) in enumerate(visible):
        prefix = PROMPT if start + offset == 0 else CONTINUATION
        rows.append(prefix + content)
    line_start, line_end, _ = lines[selected]
    cursor_col = PROMPT_WIDTH + _cell_width(
        text[line_start:min(pos, line_end)])
    return rows, selected_visible, cursor_col


COMMAND_HELP = (
    ("/model", "Switch model"),
    ("/session", "Switch conversation"),
    ("/reasoning", "Set reasoning effort"),
    ("/context", "Set context budget"),
    ("/status", "Show current state"),
    ("/exit", "Exit PocketChat"),
)


def _plain_markdown(text: str) -> List[str]:
    """Turn the common chat-markdown shapes into readable terminal rows."""
    rows: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            rows.append("")
            continue
        if line.startswith("- "):
            line = "• " + line[2:]
        line = line.replace("**", "").replace("__", "")
        rows.append(line)
    return rows or [""]


def _display_model(tui: AgentTUI) -> str:
    if tui.state.profile and tui.state.profile == tui.state.model:
        return tui.state.model
    if tui.state.profile:
        return "%s · %s" % (tui.state.profile, tui.state.model)
    return tui.state.model


def _compact_context(value: int) -> str:
    if value >= 1000000:
        return "%.1fM" % (value / 1000000.0)
    if value >= 1000:
        return "%.0fk" % (value / 1000.0)
    return str(value)


def _compact_cost(value: float) -> str:
    text = "%.8f" % value
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "cost~ $%s" % (text or "0")


def _status_metrics(tui: AgentTUI) -> List[str]:
    facts = tui.status_facts
    tokens = facts.get("tokens")
    cost = facts.get("cost_usd")
    ttft = facts.get("ttft_ms")
    attempt = facts.get("attempt_index")
    last_compaction = facts.get("last_compaction")
    compaction = "cmp%d" % int(facts.get("compactions") or 0)
    if isinstance(last_compaction, dict):
        dropped = last_compaction.get("dropped_messages")
        kept = last_compaction.get("kept_messages")
        if isinstance(dropped, int) and isinstance(kept, int):
            compaction += " d%d/k%d" % (dropped, kept)
    return [
        "tok %s" % (tokens if isinstance(tokens, int) else "?"),
        _compact_cost(cost) if isinstance(cost, (int, float)) else "cost ?",
        "ttft %dms" % ttft if isinstance(ttft, int) else "ttft ?",
        "req %d" % attempt if isinstance(attempt, int) else "req ?",
        compaction,
    ]


def _status_bar(tui: AgentTUI, width: int) -> str:
    state = "working" if tui.busy else "ready"
    model = _display_model(tui)
    context = _compact_context(tui.state.context_chars)
    left = "─ %s │ %s │ %s ctx" % (state, model, context)
    metrics = _status_metrics(tui)
    candidates = [" │ ".join(metrics + [tui.state.session])]
    candidates.extend(
        " │ ".join(metrics[index:] + [tui.state.session])
        for index in range(1, len(metrics))
    )
    candidates.append(tui.state.session)
    for right in candidates:
        if _cell_width(left) + _cell_width(right) + 3 <= width:
            gap = width - _cell_width(left) - _cell_width(right) - 1
            return _clip(left + " " * gap + right + " ", width)
    return _clip(left + " ", width)


def _message_rows(tui: AgentTUI, width: int) -> List[str]:
    rows: List[str] = []
    seen_user = False
    for role, text in tui.transcript:
        if role == "you":
            if seen_user:
                rows.extend([" ───", ""])
            seen_user = True
        if role == "reasoning":
            if tui.reasoning_expanded:
                rows.append("  ◌ thought")
                rows.extend("    " + line for line in _plain_markdown(text))
            else:
                rows.append("  ◌ thought  Ctrl+T expand")
            continue
        if role == "you":
            rows.extend(_wrap(text, max(8, width - 2), prefix=" ❯ "))
            rows.append("")
            continue
        for index, line in enumerate(_plain_markdown(text)):
            if index == 0:
                rows.extend(_wrap(line, max(8, width - 3), prefix=" ┊ "))
            elif line:
                rows.extend(_wrap(line, max(8, width - 3), prefix="   "))
            else:
                rows.append("")
        rows.append("")
    if tui.busy:
        if tui.streaming_reasoning:
            if tui.reasoning_expanded:
                rows.append("  ◌ thought")
                rows.extend("    " + line for line in
                            _plain_markdown(tui.streaming_reasoning))
            else:
                rows.append("  ◌ thinking  Ctrl+T expand")
            rows.append("")
        if tui.streaming_content:
            for index, line in enumerate(_plain_markdown(tui.streaming_content)):
                if index == 0:
                    rows.extend(_wrap(line, max(8, width - 3), prefix=" ┊ "))
                elif line:
                    rows.extend(_wrap(line, max(8, width - 3), prefix="   "))
                else:
                    rows.append("")
            rows.append("")
        rows.append("  " + tui.thinking_line())
        rows.append("")
    return rows


def _intro_rows(tui: AgentTUI, width: int) -> List[str]:
    if tui.transcript or tui.busy:
        return []
    return [
        " PocketChat",
        "",
        " model    %s" % _display_model(tui),
        " session  %s" % tui.state.session,
        " context  %s" % _compact_context(tui.state.context_chars),
        "",
        " Type a message, or / for commands.",
        "",
    ]


def _command_palette(tui: AgentTUI, input_text: str, width: int,
                     height: int) -> List[str]:
    matches = tui.command_completions(input_text)
    if not matches or tui.picker_open:
        return []
    rows = [" Commands"]
    available = max(1, height - 10)
    start = max(0, min(tui.command_index - available // 2,
                       max(0, len(matches) - available)))
    for index, name in enumerate(matches[start:start + available], start):
        description = dict(COMMAND_HELP).get(name, "")
        marker = "❯" if index == tui.command_index else " "
        rows.append(_clip(" %s %-12s %s" % (marker, name, description), width))
    if start:
        rows.append(_clip("   ↑ %d more" % start, width))
    if start + available < len(matches):
        rows.append(_clip("   ↓ %d more" % (len(matches) - start - available), width))
    return rows

def compose_screen_layout(tui: AgentTUI, *, input_text: str, width: int,
                          height: int, cursor: Optional[int] = None) -> ScreenLayout:
    """Compose rows and the exact hardware cursor position."""
    width = max(1, width)
    height = max(3, height)
    header = _clip(" PocketChat  ·  %s" % tui.state.session, width)
    picker_rows: List[str] = []
    if tui._picker:
        available = max(1, height - 9)
        total = len(tui._picker_items)
        if total <= available:
            start, visible = 0, tui._picker_items
        else:
            half = max(1, available // 2)
            start = max(0, min(total - available, tui._picker_index - half))
            visible = tui._picker_items[start:start + available]
        picker_rows = [" Select model", " ↑/↓ choose   Enter apply   Esc cancel", ""]
        if start:
            picker_rows.append("   ↑ %d more" % start)
        for offset, name in enumerate(visible):
            index = start + offset
            marker = "›" if index == tui._picker_index else " "
            model = tui.models.get(name, "")
            picker_rows.append(_clip(
                " %s %-22s %s" % (marker, name, model), width))
        if total > start + len(visible):
            picker_rows.append("   ↓ %d more" % (total - start - len(visible)))
        picker_rows = picker_rows[:available]
    palette_rows = _command_palette(tui, input_text, width, height)
    composer_limit = max(1, height - 6 - len(picker_rows) - len(palette_rows))
    composer_rows, composer_cursor_row, cursor_col = _composer_visual(
        input_text or "", width, cursor, composer_limit)
    reserved = 2 + 1 + len(picker_rows) + len(palette_rows) + len(composer_rows) + 3
    body_height = max(1, height - reserved)
    body = _intro_rows(tui, width) + _message_rows(tui, width)
    if tui.transcript_offset:
        end = max(0, len(body) - tui.transcript_offset)
        body = body[max(0, end - body_height):end]
    else:
        body = body[-body_height:]
    if tui.transcript_offset and body:
        body[0] = _clip(" ↑ scrollback · End latest", width)
    body += [""] * max(0, body_height - len(body))
    body = body[:body_height]
    rows = [header, ""] + body
    rows.extend(picker_rows)
    rows.extend(palette_rows)
    composer_start = len(rows) + 1
    rows.extend([_clip("─" * width, width)] + composer_rows)
    rows.extend([_clip("─" * width, width),
                 _status_bar(tui, width)])
    padded: List[str] = []
    for row in rows[:height]:
        clipped = _clip(row, width)
        padded.append(clipped + " " * max(0, width - _cell_width(clipped)))
    cursor_row = min(height - 1, composer_start + composer_cursor_row)
    return ScreenLayout(padded, cursor_row, min(max(0, width - 1), cursor_col))


def compose_screen(tui: AgentTUI, *, input_text: str, width: int,
                   height: int, cursor: Optional[int] = None) -> List[str]:
    return compose_screen_layout(
        tui, input_text=input_text, width=width, height=height,
        cursor=cursor).rows

def run_tui(state, profiles, models, *, config=None, workspace=None,
            verbose=False):
    """Open the full-screen agent and return its exit code.

    The renderer is deliberately imported here: the controller and all of its
    tests work without a terminal, while a piped invocation never pays for or
    depends on curses. The fallback to the line REPL lives in the caller.
    """
    try:
        from .curses_ui import run_curses
    except ModuleNotFoundError as exc:
        if exc.name not in {"curses", "_curses"}:
            raise
        raise TUIUnavailable("the full-screen UI requires curses") from exc

    controller = AgentTUI(
        state=state,
        profiles=profiles,
        models=models,
        config=config,
        verbose=verbose,
        workspace=Path(workspace) if workspace is not None else Path.cwd(),
    )
    return run_curses(controller)
