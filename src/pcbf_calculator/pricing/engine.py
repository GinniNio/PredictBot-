"""Universal, sport-agnostic market-pricing engine.

This is Release A's only layer that performs real forecasting-adjacent math,
and it performs none: it is pure de-vigging and expected-value arithmetic
over prices the caller supplies, with no sport knowledge at all. It works
identically for a two-way market (tennis match winner), a three-way market
(soccer 1X2) or an N-way market (an outright field of 20 entrants) — there is
exactly one implementation, not three.

Formulas and conventions (see ``docs/MULTI_SPORT_ARCHITECTURE.md`` for the
full rationale and the judgement calls made where the spec was open):

- Decimal odds convention throughout: a price of ``2.50`` returns 2.50 units
  (stake included) for a 1-unit stake on a winning outcome.
- Implied probability: ``p_implied = 1 / price``.
- Bookmaker margin (overround): ``sum(p_implied) - 1``.
- De-vigging method: **multiplicative / proportional** (the standard default
  method) — ``p_fair_i = p_implied_i / sum(p_implied)``. This is chosen as
  the documented default per the task's instructions; it is the simplest
  method that guarantees the fair probabilities sum to exactly 1 and is
  the most common baseline in the sports-pricing literature. Shin's method
  (which apportions margin according to an assumed insider-trading share)
  is a reasonable alternative for very large fields but is not implemented
  in Release A — noted as a possible Release B+ enhancement.
- Fair odds: ``odds_fair_i = 1 / p_fair_i``.
- Point EV per 1 unit of stake, using the *fair* (de-vigged) probability
  against the *original offered* price (this is the actual edge available to
  a bettor facing that offered price): ``EV_i = stake * (p_fair_i * price_i
  - 1)``. This follows from ``EV = p * payout - (1 - p) * stake`` with
  ``payout = stake * price`` (decimal odds already include stake return):
  ``EV = p*stake*price - (1-p)*stake = stake*(p*price - 1)``.
- Lower-bound EV: a Wilson-score lower confidence bound is computed on the
  fair probability estimate and then propagated through the same EV formula
  in place of the point estimate. The de-vigged probability is treated as if
  it were the observed proportion of an ``effective_sample_size`` Bernoulli
  trials — a documented judgement call (default ``n=200``,
  ``z=1.645`` i.e. a 95% one-sided lower bound), since there is no real
  historical win/loss ledger wired into this layer yet. A larger
  ``effective_sample_size`` should be substituted once real historical
  calibration data is available (see the architecture doc's open
  questions).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

DEFAULT_WILSON_Z = 1.645  # one-sided 95% confidence lower bound
DEFAULT_EFFECTIVE_SAMPLE_SIZE = 200


class PricingFailure(Exception):
    """Typed failure raised by the pricing engine. Never a bare exception."""

    def __init__(self, code: str, field: str | None, reason: str) -> None:
        self.code = code
        self.field = field
        self.reason = reason
        super().__init__(f"{code}: {reason}")


def _validate_outcomes(outcomes: Any) -> dict[str, float]:
    if not isinstance(outcomes, dict) or len(outcomes) == 0:
        raise PricingFailure(
            "INCOMPLETE_OPPOSING_PRICES",
            "market_prices",
            "A market must supply a non-empty mapping of outcome name to price.",
        )
    if len(outcomes) < 2:
        raise PricingFailure(
            "INCOMPLETE_OPPOSING_PRICES",
            "market_prices",
            "A market must supply at least two opposing outcomes.",
        )
    for name, price in outcomes.items():
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise PricingFailure(
                "INVALID_PRICE_VALUE",
                f"market_prices.{name}",
                "Price must be a number (not a boolean, string, or null).",
            )
        if isinstance(price, float) and (math.isnan(price) or math.isinf(price)):
            raise PricingFailure(
                "INVALID_PRICE_VALUE",
                f"market_prices.{name}",
                "Price must be finite.",
            )
        if price <= 1.0:
            raise PricingFailure(
                "INVALID_PRICE_VALUE",
                f"market_prices.{name}",
                "Decimal price must be strictly greater than 1.0.",
            )
    return dict(outcomes)


def _wilson_lower_bound(p_hat: float, n: int, z: float) -> float:
    if n <= 0:
        raise ValueError("effective_sample_size must be positive")
    denom = 1.0 + (z * z) / n
    center = (p_hat + (z * z) / (2 * n)) / denom
    spread = (z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4 * n)) / n)) / denom
    lower = center - spread
    return max(0.0, min(1.0, lower))


@dataclass(frozen=True)
class OutcomePricing:
    outcome: str
    price: float
    implied_probability: float
    fair_probability: float
    fair_odds: float
    point_ev: float
    lower_bound_ev: float
    lower_bound_probability: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "price": self.price,
            "implied_probability": self.implied_probability,
            "fair_probability": self.fair_probability,
            "fair_odds": self.fair_odds,
            "point_ev": self.point_ev,
            "lower_bound_ev": self.lower_bound_ev,
            "lower_bound_probability": self.lower_bound_probability,
        }


def analyze_market(
    outcomes: dict[str, float],
    stake: float = 1.0,
    wilson_z: float = DEFAULT_WILSON_Z,
    effective_sample_size: int = DEFAULT_EFFECTIVE_SAMPLE_SIZE,
) -> dict[str, Any]:
    """Price a complete N-way (>=2 outcome) market. Raises ``PricingFailure``
    on incomplete or invalid opposing prices; never returns a partial or
    fabricated result.

    Returns a dict with ``market_structure`` (``two_way``/``three_way``/
    ``n_way``, derived purely from outcome count — a label, not a different
    code path), ``margin``, ``de_vig_method``, and ``outcomes`` (a list of
    per-outcome pricing dicts, insertion order preserved), plus a
    market-derived ``ranking`` of outcome names ordered by descending point
    EV (highest edge first).
    """
    validated = _validate_outcomes(outcomes)

    implied = {name: 1.0 / price for name, price in validated.items()}
    total_implied = sum(implied.values())
    margin = total_implied - 1.0
    fair = {name: value / total_implied for name, value in implied.items()}

    results: list[OutcomePricing] = []
    for name, price in validated.items():
        fair_prob = fair[name]
        fair_odds = 1.0 / fair_prob
        point_ev = stake * (fair_prob * price - 1.0)
        lower_prob = _wilson_lower_bound(fair_prob, effective_sample_size, wilson_z)
        lower_ev = stake * (lower_prob * price - 1.0)
        results.append(
            OutcomePricing(
                outcome=name,
                price=price,
                implied_probability=implied[name],
                fair_probability=fair_prob,
                fair_odds=fair_odds,
                point_ev=point_ev,
                lower_bound_ev=lower_ev,
                lower_bound_probability=lower_prob,
            )
        )

    outcome_count = len(validated)
    if outcome_count == 2:
        structure = "two_way"
    elif outcome_count == 3:
        structure = "three_way"
    else:
        structure = "n_way"

    ranking = [
        item.outcome
        for item in sorted(results, key=lambda item: item.point_ev, reverse=True)
    ]

    return {
        "market_structure": structure,
        "outcome_count": outcome_count,
        "de_vig_method": "multiplicative_proportional",
        "bookmaker_margin": margin,
        "stake": stake,
        "wilson_z": wilson_z,
        "effective_sample_size": effective_sample_size,
        "outcomes": [item.to_dict() for item in results],
        "ranking_by_point_ev": ranking,
    }
