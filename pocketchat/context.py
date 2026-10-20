"""The conversation context: accumulate, budget, truncate, record.

Two rules come from the reverse-engineered references and both are load bearing
here. First, truncation is explicit and recorded: when the context budget is
exceeded the oldest turns are dropped, a ``context.compacted`` event says how
many and which, and the reader can see what the model stopped seeing. A silent
summary would leave a trace that reads as a complete history.

Second, truncation never touches a turn that failed. A failed request is
exactly what a post-mortem needs, so the session never appends a failed
exchange to the context at all: it is absent by construction rather than
protected by a rule.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .provider import estimate_tokens


class ContextBudgetExceeded(Exception):
    """The context cannot fit even one turn inside the configured budget."""


def build_initial_messages(system_prompt: str) -> List[Dict[str, str]]:
    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    return messages


def _message_chars(message: Dict[str, str]) -> int:
    return len(message.get("content") or "")


def context_chars(messages: List[Dict[str, str]]) -> int:
    return sum(_message_chars(m) for m in messages)


def apply_budget(messages: List[Dict[str, str]], max_context_chars: int,
                 keep_recent: int = 0) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Trim ``messages`` down to ``max_context_chars`` and report what was cut.

    The system prompt is never dropped: it is the instruction the whole run
    followed, and losing it mid-session changes what the model is. Older turns
    go first, newest last. ``keep_recent`` pins the tail, so the most recent
    exchange survives even when the budget is tight.

    Returns the surviving messages and a report describing the trim. When
    nothing was dropped the report is empty, so the caller skips the event
    instead of recording a compaction that did not happen.
    """
    total = context_chars(messages)
    if total <= max_context_chars:
        return list(messages), {}

    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]

    pinned = rest[-keep_recent:] if keep_recent > 0 else []
    droppable = rest[:len(rest) - len(pinned)]

    # The system prompt always survives; the budget applies to what is left.
    fixed = context_chars(system) + context_chars(pinned)
    if fixed > max_context_chars:
        # Even the pinned part does not fit. Refusing is the honest answer: a
        # context the operator forbade is not a context to send anyway.
        raise ContextBudgetExceeded(
            "the pinned context needs %d characters, the budget is %d"
            % (fixed, max_context_chars)
        )

    kept = list(pinned)
    used = fixed
    for message in reversed(droppable):
        size = _message_chars(message)
        if used + size <= max_context_chars:
            kept.insert(0, message)
            used += size

    kept_ids = {id(m) for m in kept}
    dropped = [m for m in messages if id(m) not in kept_ids]
    if not dropped:
        # Everything survived, which can only happen when the pinned tail plus
        # the system prompt already fits. The budget is met by keeping it all.
        return list(messages), {}

    return system + kept, {
        "strategy": "drop_oldest_turns",
        "dropped_messages": len(dropped),
        "dropped_roles": sorted({m.get("role", "?") for m in dropped}),
        "kept_messages": len(system) + len(kept),
        "kept_chars": used,
        "budget_chars": max_context_chars,
        "estimated_tokens_before": estimate_tokens(messages),
        "estimated_tokens_after": estimate_tokens(system + kept),
    }
