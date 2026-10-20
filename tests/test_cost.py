"""Cost and latency tests: prices are explicit, and never invented.

A price nobody configured is not a price of zero. Zero is a number, and a
comparison that printed ``0.0000`` next to a profile whose prices were absent
would be reporting a measurement that was never taken. So a missing price is
reported as unknown, and the caller decides how to say that.

Latency is the other half of the same discipline. ``duration_ms`` is the
measured wall time of the model requests themselves, taken from the events the
run recorded, because that is the only clock the evidence carries. Measuring
from outside -- from before the session was constructed to after it returned --
would add the time spent building objects that no request waited for, and two
profiles compared on that number would be compared on how long their setup
took as much as on how fast their provider answered.

Nothing here reaches a provider: the events are written by hand.
"""

import unittest

from pocketchat.cost import estimate_cost
from pocketchat.compare import _request_duration_ms


class EstimateCostTests(unittest.TestCase):
    """The arithmetic, and the refusal to guess a price."""

    def test_a_missing_price_is_unknown_not_zero(self):
        # "No price configured" and "this run was free" are different facts.
        # Returning 0 here would state the second while only knowing the
        # first, which is the one failure this package is most careful about.
        self.assertIsNone(estimate_cost(
            {"input_price_per_million": None,
             "output_price_per_million": None},
            {"input_tokens": 10, "output_tokens": 20},
        ))

    def test_one_missing_side_is_also_unknown(self):
        # A half-configured profile prices nothing at all. Multiplying the
        # side that is configured and dropping the other would understate the
        # cost without saying so.
        self.assertIsNone(estimate_cost(
            {"input_price_per_million": 1.0,
             "output_price_per_million": None},
            {"input_tokens": 1000, "output_tokens": 0},
        ))

    def test_cost_uses_configured_per_million_prices(self):
        self.assertEqual(estimate_cost(
            {"input_price_per_million": 1.0,
             "output_price_per_million": 2.0},
            {"input_tokens": 1000, "output_tokens": 500},
        ), 0.002)

    def test_a_free_profile_costs_zero_because_it_says_so(self):
        # 0.0 is a configured price, not a missing one, so it is a real zero.
        self.assertEqual(estimate_cost(
            {"input_price_per_million": 0.0,
             "output_price_per_million": 0.0},
            {"input_tokens": 1000, "output_tokens": 500},
        ), 0.0)

    def test_missing_usage_counts_are_treated_as_zero(self):
        # A provider that reports no usage is not a run that spent nothing,
        # but there is no number to multiply either, so the sides that are
        # absent contribute nothing and the configured ones still count.
        self.assertEqual(estimate_cost(
            {"input_price_per_million": 1.0,
             "output_price_per_million": 1.0},
            {},
        ), 0.0)


class RequestDurationTests(unittest.TestCase):
    """Latency measured from the events, not from around the session."""

    @staticmethod
    def _events(*specs):
        """Build the events a request leaves: (type, timestamp)."""
        return [{"type": kind, "timestamp": stamp} for kind, stamp in specs]

    def test_duration_is_between_the_request_events_the_trace_carries(self):
        events = self._events(
            ("model.request.started", "2026-10-19T00:00:00.000Z"),
            ("model.response.completed", "2026-10-19T00:00:01.500Z"),
        )
        self.assertEqual(_request_duration_ms(events), 1500)

    def test_duration_of_a_failed_request_is_still_measured(self):
        # A run that failed spent real time failing. Excluding it would make
        # the failed profile look free of the very cost being compared.
        events = self._events(
            ("model.request.started", "2026-10-19T00:00:00.000Z"),
            ("model.response.failed", "2026-10-19T00:00:00.250Z"),
        )
        self.assertEqual(_request_duration_ms(events), 250)

    def test_requests_that_were_never_made_measure_nothing(self):
        # A run that failed before its first request -- a missing credential,
        # a context error -- has no request events at all. Zero is the honest
        # answer there: no request was measured, so nothing was spent.
        events = self._events(
            ("run.started", "2026-10-19T00:00:00.000Z"),
            ("run.failed", "2026-10-19T00:00:00.010Z"),
        )
        self.assertEqual(_request_duration_ms(events), 0)

    def test_more_than_one_request_adds_up(self):
        # A multi-turn run is compared on its whole cost, not its last turn.
        events = self._events(
            ("model.request.started", "2026-10-19T00:00:00.000Z"),
            ("model.response.completed", "2026-10-19T00:00:00.100Z"),
            ("model.request.started", "2026-10-19T00:00:00.100Z"),
            ("model.response.completed", "2026-10-19T00:00:00.400Z"),
        )
        self.assertEqual(_request_duration_ms(events), 400)

    def test_a_trace_that_ends_without_its_response_is_not_counted(self):
        # An interrupted request has a started event and no response event.
        # Counting it would report a duration the trace does not support,
        # because nothing records when the abandoned request ended.
        events = self._events(
            ("model.request.started", "2026-10-19T00:00:00.000Z"),
        )
        self.assertEqual(_request_duration_ms(events), 0)

    def test_a_timestamp_that_does_not_parse_is_not_counted(self):
        events = self._events(
            ("model.request.started", "not a timestamp"),
            ("model.response.completed", "2026-10-19T00:00:01.000Z"),
        )
        self.assertEqual(_request_duration_ms(events), 0)

    def test_a_run_with_no_events_at_all_measures_nothing(self):
        # A run that never wrote an event is not a run that took no time, but
        # there is nothing here to measure. Zero says "nothing was measured"
        # instead of claiming the run was instant.
        self.assertEqual(_request_duration_ms([]), 0)
        self.assertEqual(_request_duration_ms(None), 0)


if __name__ == "__main__":
    unittest.main()
