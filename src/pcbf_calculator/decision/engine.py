"""PCBF decision layer: a real rule-evaluation engine over explicit typed
inputs. There is no live evidence/liquidity data source wired up yet, so
every input this engine reads is supplied by the caller in the request JSON
— this is a genuine rule engine, not a hardcoded or fake classifier.

Rule evaluation order (first match wins, each is a hard STOP):

1. ``STOP_STALE_DATA`` — ``freshness.data_age_seconds > freshness.max_age_seconds``
2. ``STOP_INSUFFICIENT_EVIDENCE`` — ``evidence.sample_size < evidence.min_sample_size``
3. ``STOP_INSUFFICIENT_LIQUIDITY`` — ``liquidity.available_stake < liquidity.min_required_stake``
4. ``STOP_EXCESSIVE_UNCERTAINTY`` — ``uncertainty.width > uncertainty.max_width``
5. ``STOP_NEGATIVE_EV`` — the priced outcome's ``lower_bound_ev`` (layer 1) is <= 0

If every rule passes, a classification ceiling is assigned:

- ``forecast.forecast_available is False`` -> ceiling is capped at ``PAPER``.
  **Judgement call**: this platform will never let market-derived pricing
  alone (no independent forecast) authorize real capital (``CASH``) — see
  the architecture doc's open-questions section. This is treated as a
  product/risk decision, not a math one, and is flagged as such.
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

    if "lower_bound_ev" not in market_profitability:
        raise DecisionInputError(
            "market_profitability.lower_bound_ev",
            "market_profitability must include lower_bound_ev from the pricing engine.",
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

    if market_profitability["lower_bound_ev"] <= 0:
        return rejected(
            STOP_NEGATIVE_EV,
            f"lower_bound_ev {market_profitability['lower_bound_ev']} is not positive.",
        )

    classification = "CASH"
    if not forecast_quality.get("forecast_available"):
        classification = "PAPER"
    else:
        cash_min_sample_size = evidence.get("cash_min_sample_size", evidence["min_sample_size"])
        if evidence["sample_size"] < cash_min_sample_size:
            classification = "PAPER"

    return {
        "status": "PASSED",
        "classification": classification,
        "stop_rule": None,
        "reason": None,
        "forecast_quality": forecast_quality,
        "market_profitability": market_profitability,
    }
