"""Pure task-level token budget ledger tests."""

from __future__ import annotations

import unittest

from pocketchat.task_budget import TaskBudget, TaskBudgetError


class TaskBudgetTests(unittest.TestCase):
    def test_reserve_settle_and_unknown_accounting(self):
        budget = TaskBudget(limit_tokens=100)
        self.assertEqual(90, budget.reserve(10, request_id="req-1"))
        self.assertEqual(90, budget.settle("req-1", input_tokens=4,
                                           output_tokens=6, reported=True))
        self.assertEqual(90, budget.remaining_tokens)
        with self.assertRaises(TaskBudgetError) as caught:
            budget.settle("missing", input_tokens=0, output_tokens=0,
                          reported=True)
        self.assertEqual("unknown_request", caught.exception.error_type)

    def test_unknown_usage_is_not_zero_or_refunded(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        with self.assertRaises(TaskBudgetError) as caught:
            budget.settle("req-1", input_tokens=0, output_tokens=0,
                          reported=False)
        self.assertEqual("task_token_usage_unknown", caught.exception.error_type)
        self.assertEqual(90, budget.remaining_tokens)
        self.assertFalse(budget.request("req-1")["usage_known"])
        with self.assertRaises(TaskBudgetError) as caught:
            budget.reserve(1, request_id="req-2")
        self.assertEqual("task_token_usage_unknown", caught.exception.error_type)

    def test_same_request_cannot_settle_twice(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        budget.settle("req-1", input_tokens=4, output_tokens=6,
                      reported=True)
        with self.assertRaises(TaskBudgetError) as caught:
            budget.settle("req-1", input_tokens=4, output_tokens=6,
                          reported=True)
        self.assertEqual("request_already_settled", caught.exception.error_type)

    def test_known_usage_above_the_task_limit_is_recorded_and_refused(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        with self.assertRaises(TaskBudgetError) as caught:
            budget.settle("req-1", input_tokens=80, output_tokens=80,
                          reported=True)
        self.assertEqual("task_budget_exceeded", caught.exception.error_type)
        self.assertTrue(budget.request("req-1")["usage_known"])
        self.assertEqual(160, budget.reserved_tokens)
        with self.assertRaises(TaskBudgetError):
            budget.reserve(1, request_id="req-2")

    def test_settlement_uses_total_headroom_not_the_input_estimate(self):
        budget = TaskBudget(limit_tokens=10000)
        budget.reserve(100, request_id="req-1")
        self.assertEqual(9899, budget.settle(
            "req-1", input_tokens=100, output_tokens=1, reported=True))
        self.assertEqual(101, budget.reserved_tokens)

    def test_known_usage_releases_unused_reservation(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(90, request_id="req-1")
        self.assertEqual(90, budget.settle(
            "req-1", input_tokens=8, output_tokens=2, reported=True))
        budget.reserve(90, request_id="req-2")

    def test_restored_unknown_usage_cannot_admit_another_request(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        with self.assertRaises(TaskBudgetError):
            budget.settle("req-1", input_tokens=0, output_tokens=0,
                          reported=False)
        restored = TaskBudget.from_dict(budget.to_dict())
        with self.assertRaises(TaskBudgetError) as caught:
            restored.reserve(1, request_id="req-2")
        self.assertEqual("task_token_usage_unknown", caught.exception.error_type)

    def test_reserved_requests_are_counted_against_the_limit(self):
        budget = TaskBudget(limit_tokens=20)
        budget.reserve(10, request_id="req-1")
        budget.reserve(10, request_id="req-2")
        with self.assertRaises(TaskBudgetError) as caught:
            budget.reserve(1, request_id="req-3")
        self.assertEqual("task_budget_exceeded", caught.exception.error_type)

    def test_restored_budget_rejects_duplicate_requests_and_forged_totals(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        state = budget.to_dict()
        state["requests"].append(dict(state["requests"][0]))
        with self.assertRaises(TaskBudgetError):
            TaskBudget.from_dict(state)
        state = budget.to_dict()
        state["reserved_tokens"] = 0
        with self.assertRaises(TaskBudgetError):
            TaskBudget.from_dict(state)

    def test_requests_are_bounded_and_serializable(self):
        budget = TaskBudget(limit_tokens=100)
        budget.reserve(10, request_id="req-1")
        state = budget.to_dict()
        self.assertEqual("pocketchat-task-budget.v1", state["schema_version"])
        self.assertEqual(100, state["limit_tokens"])
        self.assertEqual(10, state["reserved_tokens"])
        restored = TaskBudget.from_dict(state)
        self.assertEqual(state, restored.to_dict())


if __name__ == "__main__":
    unittest.main()
