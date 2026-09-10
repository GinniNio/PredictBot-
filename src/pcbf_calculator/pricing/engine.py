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
- De-vigging method: **multiplicative / proportional** is the Release A
  default and the only method implemented today —
  ``p_fair_i = p_implied_i / sum(p_implied)``. It is the simplest method
  that guarantees the fair probabilities sum to exactly 1 and is the most
  common baseline in the sports-pricing literature. The method is selected
  through the ``de_vig_method`` parameter (a small registry, see
  ``_DE_VIG_METHODS`` below) rather than hardcoded inline, so a future
  Release B+ adapter can register and select an alternative approved method
  (e.g. Shin's method) without changing this engine's call sites. Every
  calculation result records which method actually ran via ``de_vig_method``
  in the output.
- Fair odds: ``odds_fair_i = 1 / p_fair_i``.
- Point EV per 1 unit of stake, using the *fair* (de-vigged) probability
  against the *original offered* price (this is the actual edge available to
  a bettor facing that offered price): ``EV_i = stake * (p_fair_i * price_i
  - 1)``. This follows from ``EV = p * payout - (1 - p) * stake`` with
  ``payout = stake * price`` (decimal odds already include stake return):
  ``EV = p*stake*price - (1-p)*stake = stake*(p*price - 1)``.
- Lower-bound EV: **requires real calibrated uncertainty** (a sample size,
  variance, or whatever a specific ``uncertainty_method`` needs) about the
  fair probability estimate. Proportional de-vig by construction drives
  point EV to be nearly identical across every outcome in a market — it
  carries no information about how *confident* that estimate is. Release A
  ships no admitted forecasting adapter, so there is no source of real
  uncertainty data anywhere in this codebase, and this engine will **not**
  invent one (a previous version substituted a hardcoded
  ``effective_sample_size`` into a Wilson-score bound; that fabricated
  differentiation between outcomes that looked like signal but was actually
  an arbitrary constant, and has been removed — do not reintroduce it).
  Callers may pass ``uncertainty`` — a mapping of outcome name to a
  calibration dict a future admitted adapter actually supplies (e.g.
  ``{"sample_size": N, "z": Z}``) — and when present for an outcome, a
  Wilson-score lower bound is computed from that *real* data. When absent
  (every case in Release A), the outcome's ``lower_bound_ev`` and
  ``lower_bound_probability`` are ``null`` and ``lower_bound_ev_reason`` is
  the typed code ``UNCERTAINTY_UNAVAILABLE``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import UNCERTAINTY_UNAVAILABLE, UNSUPPORTED_DE_VIG_METHOD

DEFAULT_DE_VIG_METHOD = "multiplicative_proportional"


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


def _multiplicative_proportional_de_vig(implied: dict[str, float]) -> dict[str, float]:
    """Release A's only de-vig method: normalize implied probabilities to
    sum to 1. Kept as a standalone function (rather than inlined) so it can
    sit in ``_DE_VIG_METHODS`` alongside a future alternative method without
    changing ``analyze_market``'s call site."""
    total = sum(implied.values())
    return {name: value / total for name, value in implied.items()}


# Pluggable de-vig method registry. Release A registers exactly one method
# and it is the universal default for every category; the seam exists so a
# future, explicitly-approved Release B+ method (e.g. Shin's method for very
# large outright fields) can be added and selected per-adapter without any
# engine call site changing. Do not silently swap the default here — a new
# default requires updating docs/MULTI_SPORT_ARCHITECTURE.md.
_DE_VIG_METHODS: dict[str, Callable[[dict[str, float]], dict[str, float]]] = {
    "multiplicative_proportional": _multiplicative_proportional_de_vig,
}


def _wilson_lower_bound(p_hat: float, n: float, z: float) -> float:
    """Wilson-score lower confidence bound on a proportion ``p_hat`` observed
    over ``n`` real Bernoulli trials. This is generic, correct statistics —
    the fabrication problem the previous version had was never this formula,
    it was calling it with an invented ``n``. Callers must only pass a real,
    adapter-calibrated sample size."""
    if n <= 0:
        raise ValueError("sample_size must be positive")
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
    lower_bound_ev: float | None
    lower_bound_probability: float | None
    uncertainty_method: str | None
    lower_bound_ev_reason: str | None

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
            "uncertainty_method": self.uncertainty_method,
            "lower_bound_ev_reason": self.lower_bound_ev_reason,
        }


def _market_quality(margin: float, outcome_count: int) -> dict[str, Any]:
    """Market-level quality signals Release A is allowed to rank markets by:
    completeness, margin, and evidence quality (see
    ``docs/MULTI_SPORT_ARCHITECTURE.md`` decision 5). These describe *this
    market's* pricing data, never an outcome's betting merit, and are safe
    to compute without any forecast because they are derived purely from the
    offered prices themselves.

    ``research_priority_score`` is a deterministic combination of the two
    (more outcomes and a tighter/more-competitive margin score higher) meant
    to help a host prioritize which markets are worth research attention
    across many CLI calls; it is a market-comparison signal, not a
    per-outcome recommendation.
    """
    if margin < 0:
        evidence_quality = "ANOMALOUS_NEGATIVE_MARGIN"
    elif margin > 0.5:
        evidence_quality = "LOW_EVIDENCE_HIGH_MARGIN"
    else:
        evidence_quality = "NORMAL"

    tightness = max(0.0, 1.0 - min(margin, 1.0))
    research_priority_score = round(tightness * outcome_count, 6)

    return {
        "completeness": "COMPLETE",
        "bookmaker_margin": margin,
        "evidence_quality": evidence_quality,
        "research_priority_score": research_priority_score,
    }


def analyze_market(
    outcomes: dict[str, float],
    stake: float = 1.0,
    de_vig_method: str = DEFAULT_DE_VIG_METHOD,
    uncertainty: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Price a complete N-way (>=2 outcome) market. Raises ``PricingFailure``
    on incomplete or invalid opposing prices, or an unrecognized
    ``de_vig_method``; never returns a partial or fabricated result.

    ``uncertainty`` is an optional mapping of outcome name to a calibration
    dict (``{"sample_size": N, "z": Z}``) a future admitted forecasting
    adapter supplies. Release A never supplies this (no adapter is admitted
    yet), so by default every outcome's ``lower_bound_ev`` is ``null`` with
    ``lower_bound_ev_reason: "UNCERTAINTY_UNAVAILABLE"`` — this is not a
    placeholder value, it is the correct, honest answer until real
    calibrated uncertainty exists.

    Returns a dict with ``market_structure`` (``two_way``/``three_way``/
    ``n_way``, derived purely from outcome count — a label, not a different
    code path), ``margin``, ``de_vig_method``, ``outcomes`` (a list of
    per-outcome pricing dicts, insertion order preserved), and
    ``market_quality`` (market-level completeness/margin/evidence-quality
    signals Release A may use to rank *markets*, never outcomes — see
    decision 5 in ``docs/MULTI_SPORT_ARCHITECTURE.md``).
    """
    validated = _validate_outcomes(outcomes)

    de_vig_fn = _DE_VIG_METHODS.get(de_vig_method)
    if de_vig_fn is None:
        raise PricingFailure(
            UNSUPPORTED_DE_VIG_METHOD,
            "de_vig_method",
            f"'{de_vig_method}' is not an approved de-vig method. Approved methods: "
            f"{sorted(_DE_VIG_METHODS)}.",
        )

    implied = {name: 1.0 / price for name, price in validated.items()}
    total_implied = sum(implied.values())
    margin = total_implied - 1.0
    fair = de_vig_fn(implied)

    results: list[OutcomePricing] = []
    for name, price in validated.items():
        fair_prob = fair[name]
        fair_odds = 1.0 / fair_prob
        point_ev = stake * (fair_prob * price - 1.0)

        outcome_uncertainty = (uncertainty or {}).get(name)
        if outcome_uncertainty and "sample_size" in outcome_uncertainty:
            n = outcome_uncertainty["sample_size"]
            z = outcome_uncertainty.get("z", 1.645)
            lower_prob = _wilson_lower_bound(fair_prob, n, z)
            lower_ev = stake * (lower_prob * price - 1.0)
            uncertainty_method = outcome_uncertainty.get("method", "wilson_score")
            lower_ev_reason = None
        else:
            lower_prob = None
            lower_ev = None
            uncertainty_method = None
            lower_ev_reason = UNCERTAINTY_UNAVAILABLE

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
                uncertainty_method=uncertainty_method,
                lower_bound_ev_reason=lower_ev_reason,
            )
        )

    outcome_count = len(validated)
    if outcome_count == 2:
        structure = "two_way"
    elif outcome_count == 3:
        structure = "three_way"
    else:
        structure = "n_way"

    return {
        "market_structure": structure,
        "outcome_count": outcome_count,
        "de_vig_method": de_vig_method,
        "bookmaker_margin": margin,
        "stake": stake,
        "outcomes": [item.to_dict() for item in results],
        "market_quality": _market_quality(margin, outcome_count),
    }
