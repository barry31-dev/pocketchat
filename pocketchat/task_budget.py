"""A pure task-level token budget ledger.

Input and configured output estimates are reserved before a provider call.
Known usage replaces that estimate; missing usage preserves the reservation
and blocks further work. Neither an estimate nor a pending reservation is a
claim about provider-reported consumption.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "pocketchat-task-budget.v1"


class TaskBudgetError(ValueError):
    def __init__(self, message: str,
                 error_type: str = "task_budget_exceeded"):
        super().__init__(message)
        self.error_type = error_type


def _nonnegative(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskBudgetError(f"{name} must be a non-negative integer",
                              error_type="invalid_task_budget")
    return value


class TaskBudget:
    def __init__(self, *, limit_tokens: int,
                 requests: list[dict[str, Any]] | None = None):
        if isinstance(limit_tokens, bool) or not isinstance(limit_tokens, int) \
                or limit_tokens < 1:
            raise TaskBudgetError("limit_tokens must be a positive integer",
                                  error_type="invalid_task_budget")
        self.limit_tokens = limit_tokens
        if requests is not None and not isinstance(requests, list):
            raise TaskBudgetError("requests must be a list",
                                  error_type="invalid_task_budget")
        self._requests: dict[str, dict[str, Any]] = {}
        required = {"request_id", "reserved_tokens", "settled_tokens", "usage_known"}
        for source in requests or []:
            if not isinstance(source, dict) or not required <= set(source) \
                    or set(source) - (required | {"state"}):
                raise TaskBudgetError("invalid task budget request",
                                      error_type="invalid_task_budget")
            row = dict(source)
            request_id = row["request_id"]
            if not isinstance(request_id, str) or not request_id \
                    or request_id in self._requests:
                raise TaskBudgetError("invalid or duplicate request id",
                                      error_type="invalid_task_budget")
            _nonnegative(row["reserved_tokens"], "reserved_tokens")
            known = row["usage_known"]
            if not isinstance(known, bool):
                raise TaskBudgetError("usage_known must be a boolean",
                                      error_type="invalid_task_budget")
            # Old records did not distinguish pending from unknown usage.
            # Restore that ambiguity conservatively, never as free headroom.
            state = row.get("state", "settled" if known else "unknown")
            if state not in ("reserved", "settled", "unknown") \
                    or known != (state == "settled"):
                raise TaskBudgetError("invalid request state",
                                      error_type="invalid_task_budget")
            if known:
                _nonnegative(row["settled_tokens"], "settled_tokens")
            elif row["settled_tokens"] is not None:
                raise TaskBudgetError("unknown usage cannot have settled tokens",
                                      error_type="invalid_task_budget")
            row["state"] = state
            self._requests[request_id] = row

    @property
    def reserved_tokens(self) -> int:
        """Total committed usage plus estimates still awaiting known usage."""
        return sum(row["settled_tokens"] if row["usage_known"]
                   else row["reserved_tokens"] for row in self._requests.values())

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.limit_tokens - self.reserved_tokens)

    def reserve(self, tokens: int, *, request_id: str) -> int:
        tokens = _nonnegative(tokens, "tokens")
        if not isinstance(request_id, str) or not request_id:
            raise TaskBudgetError("request_id must be non-empty",
                                  error_type="invalid_task_budget")
        if request_id in self._requests:
            raise TaskBudgetError(f"request id is already recorded: {request_id}",
                                  error_type="duplicate_request")
        if any(row["state"] == "unknown" for row in self._requests.values()):
            raise TaskBudgetError("provider usage is unknown; task budget is blocked",
                                  error_type="task_token_usage_unknown")
        if self.reserved_tokens + tokens > self.limit_tokens:
            raise TaskBudgetError(
                f"task token budget {self.limit_tokens} would be exceeded")
        self._requests[request_id] = {
            "request_id": request_id,
            "reserved_tokens": tokens,
            "settled_tokens": None,
            "usage_known": False,
            "state": "reserved",
        }
        return self.remaining_tokens

    def settle(self, request_id: str, *, input_tokens: int,
               output_tokens: int, reported: bool) -> int:
        row = self._requests.get(request_id)
        if row is None:
            raise TaskBudgetError(f"unknown request: {request_id}",
                                  error_type="unknown_request")
        if row["state"] != "reserved":
            raise TaskBudgetError(f"request already settled: {request_id}",
                                  error_type="request_already_settled")
        if reported is not True:
            row["state"] = "unknown"
            raise TaskBudgetError("provider did not report usage required by task budget",
                                  error_type="task_token_usage_unknown")
        try:
            input_tokens = _nonnegative(input_tokens, "input_tokens")
            output_tokens = _nonnegative(output_tokens, "output_tokens")
        except TaskBudgetError:
            row["state"] = "unknown"
            raise
        settled = input_tokens + output_tokens
        row["settled_tokens"] = settled
        row["usage_known"] = True
        row["state"] = "settled"
        # Record actual consumption even when it exceeds the configured total:
        # the request already happened, and a failed check cannot refund it.
        if self.reserved_tokens > self.limit_tokens:
            raise TaskBudgetError(
                f"settled token usage {settled} would exceed task budget "
                f"{self.limit_tokens}")
        return self.remaining_tokens

    def request(self, request_id: str) -> dict[str, Any]:
        row = self._requests.get(request_id)
        if row is None:
            raise TaskBudgetError(f"unknown request: {request_id}",
                                  error_type="unknown_request")
        return dict(row)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "limit_tokens": self.limit_tokens,
            "reserved_tokens": self.reserved_tokens,
            "requests": [dict(row) for row in
                         sorted(self._requests.values(),
                                key=lambda item: item["request_id"])],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskBudget:
        if not isinstance(payload, dict) or payload.get(
                "schema_version") != SCHEMA_VERSION:
            raise TaskBudgetError("unsupported task budget record",
                                  error_type="unsupported_task_budget")
        if set(payload) != {"schema_version", "limit_tokens", "reserved_tokens", "requests"}:
            raise TaskBudgetError("invalid task budget record fields",
                                  error_type="invalid_task_budget")
        requests = payload.get("requests")
        if not isinstance(requests, list):
            raise TaskBudgetError("task budget requests must be a list",
                                  error_type="invalid_task_budget")
        budget = cls(limit_tokens=payload.get("limit_tokens"), requests=requests)
        if _nonnegative(payload["reserved_tokens"], "reserved_tokens") != budget.reserved_tokens:
            raise TaskBudgetError("task budget total disagrees with requests",
                                  error_type="invalid_task_budget")
        return budget
