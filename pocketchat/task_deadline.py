"""Pure monotonic task and step deadline accounting.

The live process uses a monotonic millisecond clock. Serialized records contain
only relative elapsed and remaining durations, so resume establishes new local
anchors instead of persisting an absolute monotonic reading.
"""

from __future__ import annotations

import time
from typing import Any, Callable

SCHEMA_VERSION = "pocketchat-task-deadline.v1"
_STEP_FIELDS = {
    "step_id", "requested_ms", "limit_ms", "elapsed_ms", "remaining_ms",
    "state",
}
_RECORD_FIELDS = {
    "schema_version", "limit_ms", "elapsed_ms", "remaining_ms", "expired",
    "steps",
}


class TaskDeadlineError(ValueError):
    def __init__(self, message: str,
                 error_type: str = "invalid_task_deadline") -> None:
        super().__init__(message)
        self.error_type = error_type


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TaskDeadlineError(f"{name} must be a positive integer")
    return value


def _nonnegative(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskDeadlineError(f"{name} must be a non-negative integer")
    return value


def _step_identifier(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise TaskDeadlineError("step_id must be non-empty")
    return value


def _monotonic_milliseconds() -> int:
    return time.monotonic_ns() // 1_000_000


class TaskDeadline:
    """A single-process task deadline with one active step reservation."""

    def __init__(self, *, limit_ms: int,
                 clock: Callable[[], int] | None = None,
                 elapsed_ms: int = 0,
                 expired: bool = False,
                 steps: list[dict[str, Any]] | None = None) -> None:
        self.limit_ms = _positive(limit_ms, "limit_ms")
        elapsed_ms = _nonnegative(elapsed_ms, "elapsed_ms")
        if elapsed_ms > self.limit_ms:
            raise TaskDeadlineError("elapsed_ms exceeds limit_ms")
        if not isinstance(expired, bool) or expired != (elapsed_ms == self.limit_ms):
            raise TaskDeadlineError("expired disagrees with elapsed_ms")
        if clock is not None and not callable(clock):
            raise TaskDeadlineError("clock must be callable")
        if steps is not None and not isinstance(steps, list):
            raise TaskDeadlineError("steps must be a list")

        self._clock = clock if clock is not None else _monotonic_milliseconds
        self._last_clock_ms: int | None = None
        now = self._read_clock()
        self._task_anchor_ms = now
        self._task_base_elapsed_ms = elapsed_ms
        self._expired = expired
        self._steps: dict[str, dict[str, Any]] = {}
        self._active_step_id: str | None = None
        self._step_anchor_ms: int | None = None

        task_remaining = self.limit_ms - elapsed_ms
        for source in steps or []:
            row = self._validate_step(
                source, task_remaining=task_remaining,
                task_elapsed=elapsed_ms, task_limit=self.limit_ms)
            step_id = row["step_id"]
            if step_id in self._steps:
                raise TaskDeadlineError("duplicate step id")
            if row["state"] == "reserved":
                if self._active_step_id is not None or self._expired:
                    raise TaskDeadlineError("invalid active step state")
                if row["remaining_ms"] > task_remaining:
                    raise TaskDeadlineError(
                        "active step exceeds the task remaining time")
                self._active_step_id = step_id
                self._step_anchor_ms = now
            self._steps[step_id] = row

    @staticmethod
    def _validate_step(source: Any, *, task_remaining: int,
                       task_elapsed: int, task_limit: int) -> dict[str, Any]:
        if not isinstance(source, dict) or set(source) != _STEP_FIELDS:
            raise TaskDeadlineError("invalid step record fields")
        row = dict(source)
        row["step_id"] = _step_identifier(row["step_id"])
        row["requested_ms"] = _positive(row["requested_ms"], "requested_ms")
        row["limit_ms"] = _positive(row["limit_ms"], "step limit_ms")
        row["elapsed_ms"] = _nonnegative(row["elapsed_ms"], "step elapsed_ms")
        row["remaining_ms"] = _nonnegative(
            row["remaining_ms"], "step remaining_ms")
        if row["limit_ms"] > row["requested_ms"]:
            raise TaskDeadlineError("step limit exceeds its request")
        if row["limit_ms"] > task_limit:
            raise TaskDeadlineError("step limit exceeds task limit")
        if row["elapsed_ms"] > task_elapsed:
            raise TaskDeadlineError("step elapsed exceeds task elapsed")
        if row["elapsed_ms"] > row["limit_ms"] \
                or row["remaining_ms"] != row["limit_ms"] - row["elapsed_ms"]:
            raise TaskDeadlineError("step duration fields disagree")
        if row["state"] not in ("reserved", "settled", "expired"):
            raise TaskDeadlineError("invalid step state")
        if row["state"] == "expired":
            if row["remaining_ms"] != 0:
                raise TaskDeadlineError("expired step has remaining time")
        elif row["remaining_ms"] == 0:
            raise TaskDeadlineError("closed step must be marked expired")
        if row["state"] == "reserved" and row["remaining_ms"] > task_remaining:
            raise TaskDeadlineError("active step exceeds task remaining time")
        return row

    def _read_clock(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TaskDeadlineError("clock must return non-negative integer milliseconds")
        if self._last_clock_ms is not None and value < self._last_clock_ms:
            raise TaskDeadlineError("monotonic clock moved backwards",
                                    error_type="deadline_clock_regressed")
        self._last_clock_ms = value
        return value

    def _snapshot(self) -> tuple[int, int, list[dict[str, Any]]]:
        now = self._read_clock()
        task_elapsed = min(
            self.limit_ms,
            self._task_base_elapsed_ms + (now - self._task_anchor_ms),
        )
        task_remaining = self.limit_ms - task_elapsed
        if task_remaining == 0:
            self._expired = True

        rows: list[dict[str, Any]] = []
        for step_id in sorted(self._steps):
            source = self._steps[step_id]
            row = dict(source)
            if step_id == self._active_step_id:
                if self._step_anchor_ms is None:
                    raise TaskDeadlineError("active step has no clock anchor")
                row["elapsed_ms"] = min(
                    row["limit_ms"],
                    source["elapsed_ms"] + (now - self._step_anchor_ms),
                )
                row["remaining_ms"] = row["limit_ms"] - row["elapsed_ms"]
                if row["remaining_ms"] == 0 or self._expired:
                    row["elapsed_ms"] = row["limit_ms"]
                    row["remaining_ms"] = 0
                    row["state"] = "expired"
                    self._steps[step_id] = dict(row)
                    self._active_step_id = None
                    self._step_anchor_ms = None
            rows.append(row)
        return task_elapsed, task_remaining, rows

    @property
    def elapsed_ms(self) -> int:
        elapsed, _, _ = self._snapshot()
        return elapsed

    @property
    def remaining_ms(self) -> int:
        _, remaining, _ = self._snapshot()
        return remaining

    def reserve(self, milliseconds: int, *, step_id: str) -> dict[str, Any]:
        requested = _positive(milliseconds, "milliseconds")
        step_id = _step_identifier(step_id)
        _, task_remaining, rows = self._snapshot()
        by_id = {row["step_id"]: row for row in rows}
        existing = by_id.get(step_id)
        if existing is not None:
            if existing["requested_ms"] != requested:
                raise TaskDeadlineError(
                    f"step id is already recorded with another limit: {step_id}",
                    error_type="duplicate_step")
            return dict(existing)
        if self._expired or task_remaining == 0:
            raise TaskDeadlineError("task deadline is exhausted",
                                    error_type="task_deadline_exceeded")
        if self._active_step_id is not None:
            raise TaskDeadlineError(
                f"step is still in progress: {self._active_step_id}",
                error_type="step_in_progress")
        effective = min(requested, task_remaining)
        row = {
            "step_id": step_id,
            "requested_ms": requested,
            "limit_ms": effective,
            "elapsed_ms": 0,
            "remaining_ms": effective,
            "state": "reserved",
        }
        self._steps[step_id] = row
        self._active_step_id = step_id
        self._step_anchor_ms = self._last_clock_ms
        return dict(row)

    def settle(self, step_id: str) -> dict[str, Any]:
        step_id = _step_identifier(step_id)
        _, _, rows = self._snapshot()
        by_id = {row["step_id"]: row for row in rows}
        row = by_id.get(step_id)
        if row is None:
            raise TaskDeadlineError(f"unknown step: {step_id}",
                                    error_type="unknown_step")
        if row["state"] != "reserved":
            return dict(row)
        row["state"] = "settled"
        self._steps[step_id] = dict(row)
        self._active_step_id = None
        self._step_anchor_ms = None
        return dict(row)

    def expired(self, step_id: str | None = None) -> bool:
        _, _, rows = self._snapshot()
        if step_id is None:
            return self._expired
        step_id = _step_identifier(step_id)
        by_id = {row["step_id"]: row for row in rows}
        row = by_id.get(step_id)
        if row is None:
            raise TaskDeadlineError(f"unknown step: {step_id}",
                                    error_type="unknown_step")
        return row["state"] == "expired"

    def step(self, step_id: str) -> dict[str, Any]:
        step_id = _step_identifier(step_id)
        _, _, rows = self._snapshot()
        for row in rows:
            if row["step_id"] == step_id:
                return dict(row)
        raise TaskDeadlineError(f"unknown step: {step_id}",
                                error_type="unknown_step")

    def to_dict(self) -> dict[str, Any]:
        elapsed, remaining, rows = self._snapshot()
        return {
            "schema_version": SCHEMA_VERSION,
            "limit_ms": self.limit_ms,
            "elapsed_ms": elapsed,
            "remaining_ms": remaining,
            "expired": self._expired,
            "steps": rows,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *,
                  clock: Callable[[], int] | None = None) -> TaskDeadline:
        if not isinstance(payload, dict) or payload.get(
                "schema_version") != SCHEMA_VERSION:
            raise TaskDeadlineError("unsupported task deadline record",
                                    error_type="unsupported_task_deadline")
        if set(payload) != _RECORD_FIELDS:
            raise TaskDeadlineError("invalid task deadline record fields")
        limit_ms = _positive(payload["limit_ms"], "limit_ms")
        elapsed_ms = _nonnegative(payload["elapsed_ms"], "elapsed_ms")
        remaining_ms = _nonnegative(payload["remaining_ms"], "remaining_ms")
        if elapsed_ms > limit_ms or remaining_ms != limit_ms - elapsed_ms:
            raise TaskDeadlineError("task duration fields disagree")
        expired = payload["expired"]
        if not isinstance(expired, bool) or expired != (remaining_ms == 0):
            raise TaskDeadlineError("expired disagrees with remaining_ms")
        steps = payload["steps"]
        if not isinstance(steps, list):
            raise TaskDeadlineError("steps must be a list")
        return cls(
            limit_ms=limit_ms,
            clock=clock,
            elapsed_ms=elapsed_ms,
            expired=expired,
            steps=steps,
        )
