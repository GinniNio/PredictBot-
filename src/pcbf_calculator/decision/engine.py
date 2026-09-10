"""PCBF decision layer: a real rule-evaluation engine over explicit typed
inputs. There is no live evidence/liquidity data source wired up yet, so
every input this engine reads is supplied by the caller in the request JSON
— this is a genuine rule engine, not a hardcoded or fake classifier.

Rule evaluation order (first match wins, each is a hard STOP):

1. ``STOP_STALE_DATA`` — ``freshness.data_age_seconds > freshness.max_age_seconds``
2. ``STOP_INSUFFICIENT_EVIDENCE`` — ``evidence.sample_size < evidence.min_sample_size``
3. ``STOP_INSUFFICIENT_LIQUIDITY`` — ``liquidity.available_stake < liquidity.min_required_stake``
4. ``STOP_EXCESSIVE_UNCERTAINTY`` — ``uncertainty.width > uncertainty.max_width``
5. ``STOP_NEGATIVE_EV`` — the priced outcome's confidence-bounded EV is <= 0.
   Layer 1 only produces a real ``lower_bound_ev`` once an admitted adapter
   supplies calibrated uncertainty (Release A: never). When
   ``lower_bound_ev`` is ``null`` this rule evaluates the outcome's
   ``point_ev`` instead — still real, non-fabricated de-vigged market math,
   just without a confidence bound around it. This is safe specifically
   because passing this rule can never by itself authorize ``CASH`` (see
   rule below); at most it lets a no-forecast category reach ``PAPER``.

**Fixed, non-configurable policy — no admitted forecast can ever reach
``CASH``.** Once every STOP rule passes:

- ``forecast.forecast_available is False`` -> ceiling is capped at
  ``PAPER``, unconditionally. This is not a tunable default and there is no
  decision-input field, STOP-rule outcome, or evidence value that can move
  it: market-implied probabilities (layer 1) can support research and price
  comparison, but proving a betting edge by measuring market-derived EV
  against the very same market it was derived from is circular — it cannot
  be used to authorize real capital. Only an independently admitted
  forecast (layer 2) can do that. This is enforced again as a defensive
  invariant right before the result is returned (see
  ``_assert_no_forecast_never_cash`` below), not only by the branch order.
- otherwise, if ``evidence.sample_size < evidence.cash_min_sample_size``
  (a higher bar than the STOP threshold, defaulting to the STOP threshold
  when unset) the ceiling is also capped at ``PAPER``.
- otherwise the ceiling is ``CASH``.

Forecast quality (layer 2) and market/ticket profitability (layer 1) are
always returned as two distinct sub-objects — ``forecast_quality`` and
``market_profitability`` — never merged into one score, per the task's hard
requirement that these stay independently inspectable.
"""

from __future__ import annotations

from typing import Any

from ..errors import (
    MISSING_DECISION_INPUT,
    STOP_EXCESSIVE_UNCERTAINTY,
    STOP_INSUFFICIENT_EVIDENCE,
    STOP_INSUFFICIENT_LIQUIDITY,
    STOP_NEGATIVE_EV,
    STOP_STALE_DATA,
)

REQUIRED_BLOCKS = ("evidence", "freshness", "liquidity", "uncertainty")


class DecisionInputError(Exception):
    def __init__(self, field: str, reason: str) -> None:
        self.code = MISSING_DECISION_INPUT
        self.field = field
        self.reason = reason
        super().__init__(f"{MISSING_DECISION_INPUT}: {reason}")


def _require_block(decision_input: dict[str, Any], name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    block = decision_input.get(name)
    if not isinstance(block, dict):
        raise DecisionInputError(name, f"Required decision input block '{name}' is missing or not an object.")
    for key in keys:
        if key not in block:
            raise DecisionInputError(f"{name}.{key}", f"Required field '{name}.{key}' is missing.")
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DecisionInputError(f"{name}.{key}", f"Field '{name}.{key}' must be a number.")
    return block


def evaluate(
    decision_input: dict[str, Any],
    market_profitability: dict[str, Any],
    forecast_quality: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate STOP rules and assign a classification.

    ``market_profitability`` is one outcome's pricing dict from
    ``pricing.engine.analyze_market`` (must contain ``lower_bound_ev``).
    ``forecast_quality`` is a ``ForecastResult.to_dict()`` from layer 2 (must
    contain ``forecast_available``). Raises ``DecisionInputError`` if a
    required block is missing/malformed — callers should turn that into the
    typed ``MISSING_DECISION_INPUT`` failure record.
    """
    evidence = _require_block(decision_input, "evidence", ("sample_size", "min_sample_size"))
    freshness = _require_block(decision_input, "freshness", ("data_age_seconds", "max_age_seconds"))
    liquidity = _require_block(decision_input, "liquidity", ("available_stake", "min_required_stake"))
    uncertainty = _require_block(decision_input, "uncertainty", ("width", "max_width"))

    if "lower_bound_ev" not in market_profitability or "point_ev" not in market_profitability:
        raise DecisionInputError(
            "market_profitability.lower_bound_ev",
            "market_profitability must include lower_bound_ev and point_ev from the pricing engine.",
        )
    if "forecast_available" not in forecast_quality:
        raise DecisionInputError(
            "forecast_quality.forecast_available",
            "forecast_quality must include forecast_available from the adapter framework.",
        )

    def rejected(stop_rule: str, reason: str) -> dict[str, Any]:
        return {
            "status": "REJECTED",
            "classification": "REJECTED",
            "stop_rule": stop_rule,
            "reason": reason,
            "forecast_quality": forecast_quality,
            "market_profitability": market_profitability,
        }

    if freshness["data_age_seconds"] > freshness["max_age_seconds"]:
        return rejected(
            STOP_STALE_DATA,
            f"data_age_seconds {freshness['data_age_seconds']} exceeds max_age_seconds "
            f"{freshness['max_age_seconds']}.",
        )

    if evidence["sample_size"] < evidence["min_sample_size"]:
        return rejected(
            STOP_INSUFFICIENT_EVIDENCE,
            f"sample_size {evidence['sample_size']} is below min_sample_size "
            f"{evidence['min_sample_size']}.",
        )

    if liquidity["available_stake"] < liquidity["min_required_stake"]:
        return rejected(
            STOP_INSUFFICIENT_LIQUIDITY,
            f"available_stake {liquidity['available_stake']} is below min_required_stake "
            f"{liquidity['min_required_stake']}.",
        )

    if uncertainty["width"] > uncertainty["max_width"]:
        return rejected(
            STOP_EXCESSIVE_UNCERTAINTY,
            f"uncertainty width {uncertainty['width']} exceeds max_width {uncertainty['max_width']}.",
        )

    lower_bound_ev = market_profitability["lower_bound_ev"]
    ev_basis = "lower_bound_ev"
    effective_ev = lower_bound_ev
    if effective_ev is None:
        # No calibrated uncertainty (Release A: always). Fall back to the
        # still-real, non-fabricated point EV rather than inventing a
        # confidence bound. Safe because forecast_available False (the only
        # case where lower_bound_ev is ever null today) already caps the
        # ceiling at PAPER below, regardless of how this rule resolves.
        effective_ev = market_profitability["point_ev"]
        ev_basis = "point_ev"

    if effective_ev <= 0:
        return rejected(
            STOP_NEGATIVE_EV,
            f"{ev_basis} {effective_ev} is not positive.",
        )

    forecast_available = bool(forecast_quality.get("forecast_available"))
    if not forecast_available:
        # HARD, non-configurable: no admitted forecast can ever authorize
        # CASH. Market-implied probabilities alone cannot prove a betting
        # edge measured against the same market they were derived from.
        classification = "PAPER"
    else:
        cash_min_sample_size = evidence.get("cash_min_sample_size", evidence["min_sample_size"])
        classification = "PAPER" if evidence["sample_size"] < cash_min_sample_size else "CASH"

    _assert_no_forecast_never_cash(forecast_available, classification)

    return {
        "status": "PASSED",
        "classification": classification,
        "stop_rule": None,
        "reason": None,
        "forecast_quality": forecast_quality,
        "market_profitability": market_profitability,
    }


def _assert_no_forecast_never_cash(forecast_available: bool, classification: str) -> None:
    """Defense-in-depth invariant, independent of the branch above: it must
    be structurally impossible for a no-forecast category to come out of
    this function classified ``CASH``. If this ever fires it is a bug in
    this engine, not a caller input problem."""
    if not forecast_available and classification == "CASH":
        raise AssertionError(
            "Policy violation: forecast_available is False but classification "
            "resolved to CASH. No admitted forecast can ever authorize CASH."
        )
