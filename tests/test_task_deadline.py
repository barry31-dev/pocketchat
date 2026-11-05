"""Pure task/step deadline accounting over a monotonic clock."""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from pocketchat.task_deadline import TaskDeadline, TaskDeadlineError


class FakeClock:
    def __init__(self, value: int = 1_000) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value

    def advance(self, milliseconds: int) -> None:
        self.value += milliseconds


class TaskDeadlineTests(unittest.TestCase):
    def test_limits_and_step_inputs_are_positive_integer_milliseconds(self):
        clock = FakeClock()
        for value in (True, 0, -1, 1.5, "10"):
            with self.subTest(limit=value):
                with self.assertRaises(TaskDeadlineError) as caught:
                    TaskDeadline(limit_ms=value, clock=clock)
                self.assertEqual("invalid_task_deadline", caught.exception.error_type)

        deadline = TaskDeadline(limit_ms=100, clock=clock)
        for value in (True, 0, -1, 1.5, "10"):
            with self.subTest(step_limit=value):
                with self.assertRaises(TaskDeadlineError) as caught:
                    deadline.reserve(value, step_id="step-a")
                self.assertEqual("invalid_task_deadline", caught.exception.error_type)
        with self.assertRaises(TaskDeadlineError) as caught:
            deadline.reserve(10, step_id="")
        self.assertEqual("invalid_task_deadline", caught.exception.error_type)

    def test_total_deadline_expires_stickily(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=100, clock=clock)
        self.assertEqual(100, deadline.remaining_ms)
        self.assertFalse(deadline.expired())

        clock.advance(100)
        self.assertTrue(deadline.expired())
        self.assertEqual(100, deadline.elapsed_ms)
        self.assertEqual(0, deadline.remaining_ms)

        clock.advance(500)
        self.assertTrue(deadline.expired())
        self.assertEqual(100, deadline.elapsed_ms)
        with self.assertRaises(TaskDeadlineError) as caught:
            deadline.reserve(1, step_id="late")
        self.assertEqual("task_deadline_exceeded", caught.exception.error_type)

    def test_step_deadline_can_expire_before_the_task(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        self.assertEqual({
            "step_id": "step-a",
            "requested_ms": 100,
            "limit_ms": 100,
            "elapsed_ms": 0,
            "remaining_ms": 100,
            "state": "reserved",
        }, deadline.reserve(100, step_id="step-a"))

        clock.advance(100)
        self.assertTrue(deadline.expired("step-a"))
        self.assertFalse(deadline.expired())
        self.assertEqual("expired", deadline.step("step-a")["state"])
        self.assertEqual("expired", deadline.settle("step-a")["state"])

        self.assertEqual("reserved", deadline.reserve(
            50, step_id="step-b")["state"])

    def test_step_is_capped_by_the_task_remaining_time(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=100, clock=clock)
        clock.advance(60)
        row = deadline.reserve(100, step_id="last-step")
        self.assertEqual(100, row["requested_ms"])
        self.assertEqual(40, row["limit_ms"])
        self.assertEqual(40, row["remaining_ms"])

        clock.advance(40)
        self.assertTrue(deadline.expired())
        self.assertTrue(deadline.expired("last-step"))

    def test_reserve_and_settle_are_idempotent_without_resetting_time(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(100, step_id="step-a")
        clock.advance(25)
        repeated = deadline.reserve(100, step_id="step-a")
        self.assertEqual(25, repeated["elapsed_ms"])
        self.assertEqual(75, repeated["remaining_ms"])

        with self.assertRaises(TaskDeadlineError) as caught:
            deadline.reserve(99, step_id="step-a")
        self.assertEqual("duplicate_step", caught.exception.error_type)

        clock.advance(15)
        settled = deadline.settle("step-a")
        self.assertEqual("settled", settled["state"])
        self.assertEqual(40, settled["elapsed_ms"])
        clock.advance(200)
        self.assertEqual(settled, deadline.settle("step-a"))
        self.assertFalse(deadline.expired("step-a"))

    def test_only_one_step_can_be_reserved_at_a_time(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(100, step_id="step-a")
        with self.assertRaises(TaskDeadlineError) as caught:
            deadline.reserve(100, step_id="step-b")
        self.assertEqual("step_in_progress", caught.exception.error_type)

        deadline.settle("step-a")
        self.assertEqual("reserved", deadline.reserve(
            100, step_id="step-b")["state"])

    def test_checkpoint_restores_relative_time_with_new_anchors(self):
        clock = FakeClock(10_000)
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(400, step_id="step-a")
        clock.advance(100)
        payload = deadline.to_dict()
        self.assertEqual({
            "schema_version", "limit_ms", "elapsed_ms", "remaining_ms",
            "expired", "steps",
        }, set(payload))
        self.assertEqual(100, payload["elapsed_ms"])
        self.assertEqual(900, payload["remaining_ms"])
        self.assertNotIn("anchor", repr(payload).lower())
        self.assertNotIn("started", repr(payload).lower())

        resumed_clock = FakeClock(900_000)
        resumed = TaskDeadline.from_dict(payload, clock=resumed_clock)
        resumed_clock.advance(50)
        self.assertEqual(150, resumed.elapsed_ms)
        self.assertEqual(850, resumed.remaining_ms)
        self.assertEqual(150, resumed.step("step-a")["elapsed_ms"])
        self.assertEqual(250, resumed.step("step-a")["remaining_ms"])
        self.assertEqual("settled", resumed.settle("step-a")["state"])

    def test_closed_steps_stay_closed_after_restore(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(100, step_id="step-a")
        clock.advance(30)
        settled = deadline.settle("step-a")
        payload = deadline.to_dict()

        resumed_clock = FakeClock(50_000)
        resumed = TaskDeadline.from_dict(payload, clock=resumed_clock)
        resumed_clock.advance(500)
        self.assertEqual(settled, resumed.step("step-a"))
        self.assertEqual(settled, resumed.settle("step-a"))

    def test_restore_refuses_unsupported_or_forged_records(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=100, clock=clock)
        deadline.reserve(50, step_id="step-a")
        payload = deadline.to_dict()

        cases = []
        wrong_schema = copy.deepcopy(payload)
        wrong_schema["schema_version"] = "foreign.v1"
        cases.append((wrong_schema, "unsupported_task_deadline"))

        extra_field = copy.deepcopy(payload)
        extra_field["absolute_monotonic_ms"] = 123
        cases.append((extra_field, "invalid_task_deadline"))

        wrong_total = copy.deepcopy(payload)
        wrong_total["remaining_ms"] = 0
        cases.append((wrong_total, "invalid_task_deadline"))

        wrong_step = copy.deepcopy(payload)
        wrong_step["steps"][0]["remaining_ms"] = 0
        cases.append((wrong_step, "invalid_task_deadline"))

        second_active = copy.deepcopy(payload)
        second = copy.deepcopy(second_active["steps"][0])
        second["step_id"] = "step-b"
        second_active["steps"].append(second)
        cases.append((second_active, "invalid_task_deadline"))

        for forged, error_type in cases:
            with self.subTest(error_type=error_type, payload=forged):
                with self.assertRaises(TaskDeadlineError) as caught:
                    TaskDeadline.from_dict(forged, clock=clock)
                self.assertEqual(error_type, caught.exception.error_type)

    def test_restore_rejects_impossible_cross_field_step_state(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(500, step_id="step-a")
        clock.advance(10)
        payload = deadline.to_dict()

        step_elapsed_exceeds_task = copy.deepcopy(payload)
        step_elapsed_exceeds_task["steps"][0]["elapsed_ms"] = 90
        step_elapsed_exceeds_task["steps"][0]["remaining_ms"] = 410

        step_limit_exceeds_task = copy.deepcopy(payload)
        step_limit_exceeds_task["steps"][0]["requested_ms"] = 2_000
        step_limit_exceeds_task["steps"][0]["limit_ms"] = 1_500
        step_limit_exceeds_task["steps"][0]["elapsed_ms"] = 10
        step_limit_exceeds_task["steps"][0]["remaining_ms"] = 1_490
        step_limit_exceeds_task["steps"][0]["state"] = "settled"

        cases = (
            (step_elapsed_exceeds_task, "step elapsed exceeds task elapsed"),
            (step_limit_exceeds_task, "step limit exceeds task limit"),
        )
        for forged, message in cases:
            with self.subTest(forged=forged):
                with self.assertRaises(TaskDeadlineError) as caught:
                    TaskDeadline.from_dict(forged, clock=FakeClock())
                self.assertEqual("invalid_task_deadline", caught.exception.error_type)
                self.assertEqual(message, str(caught.exception))

    def test_restore_rejects_duplicate_step_ids(self):
        clock = FakeClock()
        deadline = TaskDeadline(limit_ms=1_000, clock=clock)
        deadline.reserve(100, step_id="step-a")
        deadline.settle("step-a")
        payload = deadline.to_dict()
        payload["steps"].append(copy.deepcopy(payload["steps"][0]))

        with self.assertRaises(TaskDeadlineError) as caught:
            TaskDeadline.from_dict(payload, clock=FakeClock())
        self.assertEqual("invalid_task_deadline", caught.exception.error_type)
        self.assertEqual("duplicate step id", str(caught.exception))

    def test_backwards_monotonic_clock_is_refused(self):
        clock = FakeClock(100)
        deadline = TaskDeadline(limit_ms=100, clock=clock)
        clock.value = 99
        with self.assertRaises(TaskDeadlineError) as caught:
            deadline.expired()
        self.assertEqual("deadline_clock_regressed", caught.exception.error_type)

    def test_falsey_callable_clock_is_still_used(self):
        class FalseyClock(FakeClock):
            def __bool__(self) -> bool:
                return False

        clock = FalseyClock()
        deadline = TaskDeadline(limit_ms=10, clock=clock)
        clock.advance(10)
        self.assertTrue(deadline.expired())

    def test_pure_core_uses_only_the_injected_monotonic_clock(self):
        clock = FakeClock()
        with mock.patch("builtins.open", side_effect=AssertionError("file I/O")), \
                mock.patch("socket.socket", side_effect=AssertionError("network I/O")), \
                mock.patch("subprocess.run", side_effect=AssertionError("command I/O")), \
                mock.patch("os.kill", side_effect=AssertionError("signal I/O")), \
                mock.patch("time.time", side_effect=AssertionError("wall clock")):
            deadline = TaskDeadline(limit_ms=100, clock=clock)
            deadline.reserve(50, step_id="step-a")
            clock.advance(10)
            self.assertFalse(deadline.expired())
            self.assertFalse(deadline.expired("step-a"))
            self.assertEqual("settled", deadline.settle("step-a")["state"])
            restored = TaskDeadline.from_dict(deadline.to_dict(), clock=clock)
            self.assertEqual(90, restored.remaining_ms)


if __name__ == "__main__":
    unittest.main()
