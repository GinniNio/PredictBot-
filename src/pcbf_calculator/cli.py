"""Host-neutral JSON-in/JSON-out CLI for the multi-sport pricing platform.

Preserves the same contract as ``pcbf_football`` (``docs/HOST_CONTRACT.md``):
a single JSON object in, a single JSON object out, no network calls, no
host-specific code, deterministic byte-identical output for the same input.

Input shape::

    {
      "event_id": "string, required",
      "category": "one of the 32 registry category ids, required",
      "market_prices": {"outcome_name": decimal_price, ...},  // >=2 outcomes
      "stake": 1.0,                 // optional, defaults to 1.0
      "selected_outcome": "home",   // optional; defaults to the top-EV outcome
      "fixture": {...},             // optional, passed through to the adapter
      "decision_input": {           // optional; omit to skip layer 3 entirely
        "evidence": {"sample_size": N, "min_sample_size": N, "cash_min_sample_size": N},
        "freshness": {"data_age_seconds": N, "max_age_seconds": N},
        "liquidity": {"available_stake": N, "min_required_stake": N},
        "uncertainty": {"width": N, "max_width": N}
      }
    }

Output always carries ``status``, ``failure`` (null on success),
``classification_ceiling`` (the host-contract field every project host
checks), and two distinctly-named stake fields, ``cash_stake`` and
``simulated_stake`` (see ``decision/engine.py``'s module docstring,
"Stake is tiered by classification"): ``RESEARCH-MODEL`` -> both ``0``;
``PAPER`` -> ``cash_stake: 0`` but ``simulated_stake`` may be nonzero (a
hypothetical, paper-traded stake, never a real-money one); ``CASH`` ->
``cash_stake`` may be nonzero, ``simulated_stake: 0``. When layer 3
(``decision``) never ran and the registry-default ceiling is not
``RESEARCH-MODEL`` either, both are ``null`` (no stake authorization
decision was made at all). Both fields are a distinct concept from
``pricing``'s own per-outcome ``stake`` (the EV-sizing parameter Release
A's pricing math uses, default 1.0) — they answer "should any stake, real
or simulated, be authorized/tracked for this result," never "how big is the
stake used inside the EV formula." ``pricing``, ``forecast`` and
``decision`` sub-objects are each null when not computed/applicable, and
``pricing`` and ``forecast`` are always kept as two distinct objects —
market profitability is never conflated with forecast quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .adapters.registry import UnsupportedCombinationError, run_forecast
from .decision.engine import DecisionInputError, evaluate as evaluate_decision
from .errors import (
    INVALID_REQUEST,
    MISSING_DECISION_INPUT,
    UNSUPPORTED_INPUT,
    UNSUPPORTED_SPORT_MARKET_COMBINATION,
)
from .pricing.engine import PricingFailure, analyze_market
from .registries.loader import get_category

ARTIFACT_ID = "PCBF_MULTI_SPORT_CALCULATOR"
ARTIFACT_VERSION = "1.0.0"
DEFAULT_CLASSIFICATION_CEILING = "RESEARCH-MODEL"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _failure(code: str, field: str | None, reason: str) -> dict[str, Any]:
    return {"code": code, "field": field, "reason": reason}


def run_calculator(fixture: Any) -> dict[str, Any]:
    """Validate one request and run whichever layers the input supports.

    Never raises for a malformed/unsupported input — every failure path
    returns a typed failure record instead. This is the single entry point
    both the CLI and the test suite call.
    """
    input_hash = _sha256(fixture)

    def build(
        status: str,
        event_id: Any = None,
        category: Any = None,
        failure: dict[str, Any] | None = None,
        classification_ceiling: str = DEFAULT_CLASSIFICATION_CEILING,
        pricing: dict[str, Any] | None = None,
        forecast: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if decision is not None:
            # The decision layer (layer 3) is the single source of truth for
            # cash_stake/simulated_stake once it has run — never
            # recomputed here.
            cash_stake = decision.get("cash_stake")
            simulated_stake = decision.get("simulated_stake")
        elif classification_ceiling == "RESEARCH-MODEL":
            # No decision_input was supplied, but the registry-default
            # ceiling for this category (no admitted forecast, or a
            # forecast whose model version has no APPROVED model-admission
            # row) is already RESEARCH-MODEL — structurally impossible to
            # surface a nonzero stake of either kind here either, mirroring
            # decision/engine.py's own hard lock.
            cash_stake = 0
            simulated_stake = 0
        else:
            # Layer 3 was never invoked, so no real stake authorization
            # decision was made either way.
            cash_stake = None
            simulated_stake = None

        payload = {
            "artifact_id": ARTIFACT_ID,
            "artifact_version": ARTIFACT_VERSION,
            "event_id": event_id,
            "category": category,
            "input_hash": input_hash,
            "status": status,
            "failure": failure,
            "classification_ceiling": classification_ceiling,
            "cash_stake": cash_stake,
            "simulated_stake": simulated_stake,
            "pricing": pricing,
            "forecast": forecast,
            "decision": decision,
        }
        payload["calculation_hash"] = _sha256(payload)
        return payload

    if not isinstance(fixture, dict):
        return build("FAILED", failure=_failure(INVALID_REQUEST, "$", "Input must be a JSON object."))

    event_id = fixture.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return build(
            "FAILED",
            event_id=event_id,
            failure=_failure(INVALID_REQUEST, "event_id", "Mandatory event_id is missing."),
        )

    category = fixture.get("category")
    if not isinstance(category, str) or not category.strip():
        return build(
            "FAILED",
            event_id=event_id,
            failure=_failure(INVALID_REQUEST, "category", "Mandatory category is missing."),
        )

    category_record = get_category(category)
    if category_record is None:
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            failure=_failure(
                UNSUPPORTED_SPORT_MARKET_COMBINATION,
                "category",
                f"'{category}' is not present in any registry.",
            ),
        )

    data_source_row = category_record.get("data_sources") or {}
    runtime_status = data_source_row.get("runtime_status")
    if runtime_status != "PRICING_SUPPORTED":
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            failure=_failure(
                UNSUPPORTED_INPUT,
                "category",
                data_source_row.get("runtime_unsupported_reason")
                or f"category '{category}' has runtime_status '{runtime_status}', not PRICING_SUPPORTED.",
            ),
        )

    stake = fixture.get("stake", 1.0)
    if isinstance(stake, bool) or not isinstance(stake, (int, float)) or stake <= 0:
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            failure=_failure(INVALID_REQUEST, "stake", "stake must be a positive number."),
        )

    try:
        pricing_result = analyze_market(fixture.get("market_prices"), stake=float(stake))
    except PricingFailure as exc:
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            failure=_failure(exc.code, exc.field, exc.reason),
        )

    try:
        forecast_result = run_forecast(category, fixture.get("fixture") or {})
    except UnsupportedCombinationError as exc:
        # Should not happen: category already resolved above. Kept for
        # defense in depth, fail-closed rather than silently proceeding.
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            failure=_failure(exc.code, "category", exc.reason),
        )

    adapter_row = category_record.get("adapters") or {}
    default_ceiling = adapter_row.get("classification_ceiling") or DEFAULT_CLASSIFICATION_CEILING

    decision_input = fixture.get("decision_input")
    if decision_input is None:
        return build(
            "OK",
            event_id=event_id,
            category=category,
            classification_ceiling=default_ceiling,
            pricing=pricing_result,
            forecast=forecast_result,
            decision=None,
        )

    if not isinstance(decision_input, dict):
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            pricing=pricing_result,
            forecast=forecast_result,
            failure=_failure(MISSING_DECISION_INPUT, "decision_input", "decision_input must be an object."),
        )

    selected_outcome = fixture.get("selected_outcome")
    outcomes_by_name = {item["outcome"]: item for item in pricing_result["outcomes"]}
    if selected_outcome is None:
        # Arbitrary, non-recommendation default: the first outcome in the
        # caller's own market_prices order. Release A must never pick a
        # "best" outcome by point EV here — that would be exactly the
        # per-outcome betting recommendation decision 5 forbids without an
        # independent admitted forecast. A caller that cares which outcome
        # is evaluated by the decision layer should pass selected_outcome
        # explicitly.
        selected_outcome = next(iter(outcomes_by_name))
    if selected_outcome not in outcomes_by_name:
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            pricing=pricing_result,
            forecast=forecast_result,
            failure=_failure(
                INVALID_REQUEST,
                "selected_outcome",
                f"selected_outcome '{selected_outcome}' is not one of the priced outcomes.",
            ),
        )

    try:
        decision_result = evaluate_decision(
            decision_input,
            outcomes_by_name[selected_outcome],
            forecast_result,
        )
    except DecisionInputError as exc:
        return build(
            "FAILED",
            event_id=event_id,
            category=category,
            pricing=pricing_result,
            forecast=forecast_result,
            failure=_failure(exc.code, exc.field, exc.reason),
        )

    final_ceiling = decision_result["classification"]
    return build(
        "OK",
        event_id=event_id,
        category=category,
        classification_ceiling=final_ceiling,
        pricing=pricing_result,
        forecast=forecast_result,
        decision=decision_result,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Request JSON file")
    parser.add_argument("output", type=Path, nargs="?", help="Optional output JSON file")
    args = parser.parse_args(argv)

    fixture = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_calculator(fixture)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "OK" else 2
