"""Cost from configured prices, and nothing else.

Two numbers a comparison reports next to each other have to be comparable, and
a price is the one that is easiest to fake by accident. Zero is the trap: a
profile whose prices were never configured and a profile whose prices are
actually zero both cost nothing to run, but only the second one has been
measured. Printing ``0.0000`` for the first would state a price nobody set, so
a missing price is returned as ``None`` and the caller decides how to say
"unknown".

The rates are per million tokens because that is the unit every provider
publishes them in, and converting them here keeps the config file readable as
the provider's own price list. No currency conversion happens: the prices in
the config are in whatever unit the operator wrote them, and the field name
says USD only because that is what the providers quote.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def estimate_cost(config: Dict[str, Any],
                  usage: Dict[str, int]) -> Optional[float]:
    """The cost of one run, or ``None`` when it cannot be known.

    ``config`` is the flat per-run config
    :func:`~pocketchat.config.profile_config` returns, and ``usage`` the token
    counts a run accumulated. Either price being absent makes the whole thing
    unknown: a half-configured profile prices neither side, and computing the
    side that is present would understate the cost while looking precise.

    The result is rounded to eight decimal places, which is finer than any
    price a per-million rate produces at a human's token counts but coarse
    enough that the JSON stays readable. The rounding is for display and
    comparison; it is not a claim about how providers bill.
    """
    input_price = config.get("input_price_per_million")
    output_price = config.get("output_price_per_million")
    if input_price is None or output_price is None:
        return None
    return round(
        _count(usage, "input_tokens") * float(input_price) / 1_000_000
        + _count(usage, "output_tokens") * float(output_price) / 1_000_000,
        8,
    )


def _count(usage: Dict[str, int], key: str) -> int:
    """One usage counter, treating an absent or unusable one as zero.

    A provider that reports no usage is not a provider that served nothing,
    but there is no number here to multiply. Zero contributes nothing to the
    sum instead of failing the run: the prices are configured, so the sides
    that were reported still cost what they cost.
    """
    value = usage.get(key) if isinstance(usage, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
