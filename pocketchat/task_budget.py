"""A pure task-level token budget ledger.

Requests reserve their local input estimate before a provider call and settle
after it. A missing provider usage report leaves the reservation in place and
marks the request ``usage_known=False``; it is never treated as zero usage or
refunded.
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
        self._requests = {row["request_id"]: dict(row)
                          for row in (requests or [])}
        for row in self._requests.values():
            _nonnegative(row["reserved_tokens"], "reserved_tokens")

    @property
    def reserved_tokens(self) -> int:
        return sum(int(row["reserved_tokens"])
                   for row in self._requests.values())

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.limit_tokens - self.reserved_tokens)

    def reserve(self, tokens: int, *, request_id: str) -> int:
        tokens = _nonnegative(tokens, "tokens")
        if not isinstance(request_id, str) or not request_id:
            raise TaskBudgetError("request_id must be non-empty",
                                  error_type="invalid_task_budget")
        if request_id in self._requests:
            raise TaskBudgetError(
                f"request id is already recorded: {request_id}",
                                  error_type="duplicate_request")
        if self.reserved_tokens + tokens > self.limit_tokens:
            raise TaskBudgetError(
                f"task token budget {self.limit_tokens} would be exceeded")
        self._requests[request_id] = {
            "request_id": request_id,
            "reserved_tokens": tokens,
            "settled_tokens": None,
            "usage_known": False,
        }
        return self.remaining_tokens

    def settle(self, request_id: str, *, input_tokens: int,
               output_tokens: int, reported: bool) -> int:
        row = self._requests.get(request_id)
        if row is None:
            raise TaskBudgetError(f"unknown request: {request_id}",
                                  error_type="unknown_request")
        if row["settled_tokens"] is not None or row["usage_known"]:
            raise TaskBudgetError(
                f"request already settled: {request_id}",
                                  error_type="request_already_settled")
        input_tokens = _nonnegative(input_tokens, "input_tokens")
        output_tokens = _nonnegative(output_tokens, "output_tokens")
        if not isinstance(reported, bool):
            raise TaskBudgetError("reported must be a boolean",
                                  error_type="invalid_task_budget")
        if not reported:
            # Keep the conservative reservation. The caller can inspect the
            # unknown marker rather than treating absent usage as free work.
            return self.remaining_tokens
        settled = input_tokens + output_tokens
        if settled > row["reserved_tokens"]:
            raise TaskBudgetError(
                f"settled token usage {settled} exceeds reserved "
                f"{row['reserved_tokens']}")
        if self.reserved_tokens - row["reserved_tokens"] + settled > self.limit_tokens:
            raise TaskBudgetError(
                f"settled token usage {settled} would exceed task budget "
                f"{self.limit_tokens}")
        row["settled_tokens"] = settled
        row["usage_known"] = True
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
        requests = payload.get("requests")
        if not isinstance(requests, list):
            raise TaskBudgetError("task budget requests must be a list",
                                  error_type="invalid_task_budget")
        return cls(limit_tokens=payload.get("limit_tokens"),
                   requests=requests)
