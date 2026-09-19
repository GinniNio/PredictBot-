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

``market_comparison`` (new, additive field; ``null`` unless an admitted
forecast exists) reports the priced market's own de-vigged probability
against that independent forecast's probability, per outcome
(``model_probability``, ``market_implied_probability``, ``offered_odds``,
``model_point_ev``, ``probability_difference``) — a research measurement,
computed strictly after both layers already have their own results, never
fed back into either one. It is never a recommendation: no outcome is
selected or named "best." Currently populated only for ``category:
"soccer"`` (the only category with an admitted forecast, see
``adapters/soccer_1x2_elo_v1``), via the fixed ``home_win``/``draw``/
``away_win`` <-> ``home``/``draw``/``away`` correspondence every soccer
1X2 request already uses for ``market_prices``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

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


# Fixed correspondence between an admitted forecast's own outcome ids
# (home_win/draw/away_win -- docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md
# section 1's canonical vocabulary) and this platform's own market_prices
# convention (home/draw/away -- see tests/test_cli_integration.py's
# THREE_WAY_REQUEST and every other example in this repo). Scoped to
# soccer 1X2 only; a future adapter/market with a different outcome
# vocabulary would need its own mapping, never guessed from string
# similarity.
_SOCCER_1X2_OUTCOME_MAP = {"home_win": "home", "draw": "draw", "away_win": "away"}


def _market_comparison(forecast: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any] | None:
    """Independent-forecast-vs-market comparison, computed strictly AFTER
    both layer 1 (pricing) and layer 2 (forecast) have already produced
    their own results -- this function reads them, it never feeds either
    one back into the other. Returns ``None`` when there is no admitted
    forecast to compare (forecast_available is False) or the priced
    market does not use the home/draw/away vocabulary this comparison
    understands.

    Every value here is a research measurement, never a recommendation:
    no outcome is selected, named "best", or singled out — the full H/D/A
    comparison is always returned together. ``market_implied_probability``
    is the pricing engine's own de-vigged ``fair_probability`` (never the
    raw, margin-inflated ``implied_probability``).
    """
    if not forecast.get("forecast_available"):
        return None
    probabilities = forecast.get("probabilities") or {}
    outcomes_by_name = {item["outcome"]: item for item in pricing.get("outcomes", [])}
    if not all(market_key in outcomes_by_name for market_key in _SOCCER_1X2_OUTCOME_MAP.values()):
        return None

    comparison: dict[str, Any] = {}
    for forecast_key, market_key in _SOCCER_1X2_OUTCOME_MAP.items():
        model_probability = probabilities.get(forecast_key)
        if model_probability is None:
            return None
        priced = outcomes_by_name[market_key]
        offered_odds = priced["price"]
        market_implied_probability = priced["fair_probability"]
        comparison[market_key] = {
            "model_probability": model_probability,
            "market_implied_probability": market_implied_probability,
            "offered_odds": offered_odds,
            "model_point_ev": model_probability * offered_odds - 1,
            "probability_difference": model_probability - market_implied_probability,
        }
    return comparison


def run_calculator(
    fixture: Any,
    *,
    forecast_override: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate one request and run whichever layers the input supports.

    Never raises for a malformed/unsupported input — every failure path
    returns a typed failure record instead. This is the single entry point
    both the CLI and the test suite call.

    ``forecast_override`` (default ``None``, preserving this function's
    original behavior byte-for-byte for every existing caller) replaces
    the one line that would otherwise call
    ``adapters.registry.run_forecast(category, fixture)`` -- which always
    resolves the registry's own default, incumbent adapter instance, with
    no way to point it at a different model. Given the same
    ``(category, fixture_dict)`` signature as ``run_forecast`` itself, so
    it is a drop-in substitution: everything else (input validation,
    pricing, the decision layer, market comparison, ``calculation_hash``)
    runs identically either way. Used by
    ``pcbf_calculator.orchestration.candidate_shadow_forecast`` to forecast
    with a candidate bundle's own adapter instance instead of the
    incumbent's, without duplicating this function's own validation/
    pricing/comparison logic.
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
        market_comparison: dict[str, Any] | None = None,
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
            "market_comparison": market_comparison,
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
        if forecast_override is not None:
            forecast_result = forecast_override(category, fixture.get("fixture") or {})
        else:
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

    market_comparison_result = _market_comparison(forecast_result, pricing_result)

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
            market_comparison=market_comparison_result,
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
        market_comparison=market_comparison_result,
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    # ``ingest-bet9ja`` is a separate, additive subcommand (Bet9ja capture
    # -> PCBF research-batch ingestion bridge -- see
    # ``ingestion/bet9ja.py``'s own module docstring) dispatched BEFORE
    # the legacy positional parser below, so the documented host-contract
    # invocation (``python -m pcbf_calculator INPUT [OUTPUT]``, no
    # subcommand) is completely unchanged for every existing caller.
    if argv and argv[0] == "ingest-bet9ja":
        from .ingestion.bet9ja import main as ingest_bet9ja_main

        return ingest_bet9ja_main(argv[1:])

    # ``screen-research-batch`` is likewise additive -- see
    # ``screening/research_batch.py``'s own module docstring. It consumes
    # the ``pcbf-research-batch.json`` the command above produces.
    if argv and argv[0] == "screen-research-batch":
        from .screening.research_batch import main as screen_research_batch_main

        return screen_research_batch_main(argv[1:])

    # ``run-bet9ja-research`` is likewise additive -- see
    # ``orchestration/bet9ja_research_session.py``'s own module docstring.
    # It consumes the raw exported Bet9ja capture envelope directly (the
    # same file ``ingest-bet9ja`` consumes), running ingestion, the
    # existing market-quality gate, and the registered soccer forecast
    # adapter end to end in one command. It reuses, and never redefines,
    # ``ingest-bet9ja``'s and ``screen-research-batch``'s own contracts --
    # both remain available unchanged for lower-level/debugging use.
    if argv and argv[0] == "run-bet9ja-research":
        from .orchestration.bet9ja_research_session import main as run_bet9ja_research_main

        return run_bet9ja_research_main(argv[1:])

    # ``ingest-football-data-results`` is likewise additive -- see
    # ``orchestration/football_data_settlement.py``'s own module
    # docstring. It never reads a Bet9ja capture at all: it settles
    # already-recorded forecasts (any source) against football-data.co.uk
    # result/closing-odds files, appending SCORED events to
    # ``ledgers/forecast_ledger.py``. Never touches
    # ``ledgers/betting_ledger.py``.
    if argv and argv[0] == "ingest-football-data-results":
        from .orchestration.football_data_settlement import main as ingest_football_data_results_main

        return ingest_football_data_results_main(argv[1:])

    # ``ingest-bet9ja-results`` is likewise additive -- see
    # ``orchestration/bet9ja_results_settlement.py``'s own module
    # docstring. An operational FALLBACK settlement source for when
    # football-data.co.uk's own result/closing-odds file is unavailable:
    # reuses ``football_data_settlement.plan_settlement`` verbatim (the
    # same matching/scoring/idempotency engine), sourced from
    # ``browser_extension/bet9ja_capture/results_parser.js``'s own
    # envelope instead of a CSV. Never records closing odds (the Results
    # page has none) and never touches ``ledgers/betting_ledger.py``.
    if argv and argv[0] == "ingest-bet9ja-results":
        from .orchestration.bet9ja_results_settlement import main as ingest_bet9ja_results_main

        return ingest_bet9ja_results_main(argv[1:])

    # ``ingest-football-data-org-results`` is likewise additive -- see
    # ``orchestration/football_data_org_settlement.py``'s own module
    # docstring. Queries the football-data.org API directly (reads
    # FOOTBALL_DATA_ORG_TOKEN from the environment, never a CLI argument)
    # for ONLY the covered leagues that have a currently-unsettled
    # forecast, and reuses ``football_data_settlement.plan_settlement``
    # verbatim. Never records closing odds (this source has none) and
    # never touches ``ledgers/betting_ledger.py``.
    if argv and argv[0] == "ingest-football-data-org-results":
        from .orchestration.football_data_org_settlement import main as ingest_football_data_org_results_main

        return ingest_football_data_org_results_main(argv[1:])

    # ``ingest-manual-results`` is likewise additive -- see
    # ``orchestration/manual_results_settlement.py``'s own module
    # docstring. The LAST-RESORT settlement source, for a fixture that
    # football-data.org AND football-data.co.uk both genuinely could not
    # settle: a corroboration-gated (one authoritative source, or two
    # independently agreeing sources), dry-run-by-default command that
    # reuses ``football_data_settlement.plan_settlement`` verbatim. Never
    # records closing odds (a manually-verified score is never a closing
    # price) and never touches ``ledgers/betting_ledger.py``.
    if argv and argv[0] == "ingest-manual-results":
        from .orchestration.manual_results_settlement import main as ingest_manual_results_main

        return ingest_manual_results_main(argv[1:])

    # ``report-forecast-performance`` is likewise additive -- see
    # ``orchestration/forecast_performance_report.py``'s own module
    # docstring. Read-only over the forecast ledger's SCORED events; never
    # writes to any ledger, never touches model admission, classification
    # ceilings, promotion state, staking, ticket construction, or any
    # operator decision.
    if argv and argv[0] == "report-forecast-performance":
        from .orchestration.forecast_performance_report import main as report_forecast_performance_main

        return report_forecast_performance_main(argv[1:])

    # ``refresh-soccer-artifact`` is likewise additive -- see
    # ``orchestration/soccer_artifact_refresh.py``'s own module docstring.
    # CANDIDATE creation only, never promotion: builds a new, immutable
    # candidate bundle from a training run and compares its backtest
    # against the incumbent's, but never registers, classifies, or
    # activates anything -- the currently shipped adapter artifact is
    # never touched.
    if argv and argv[0] == "refresh-soccer-artifact":
        from .orchestration.soccer_artifact_refresh import main as refresh_soccer_artifact_main

        return refresh_soccer_artifact_main(argv[1:])

    # ``shadow-forecast-candidate`` is likewise additive -- see
    # ``orchestration/candidate_shadow_forecast.py``'s own module
    # docstring. Gives a verified candidate bundle its own real
    # prospective forecast history over the same fixtures
    # ``run-bet9ja-research`` forecasts for the incumbent, written to a
    # physically separate candidate ledger -- never the incumbent's own
    # ledger, never through the active adapter registry, never a ranked
    # queue or a recommendation.
    if argv and argv[0] == "shadow-forecast-candidate":
        from .orchestration.candidate_shadow_forecast import main as shadow_forecast_candidate_main

        return shadow_forecast_candidate_main(argv[1:])

    # ``import-bet9ja-tickets`` is likewise additive -- see
    # ``orchestration/bet9ja_ticket_import.py``'s own module docstring. It
    # never reads a football-data.co.uk file: it records REAL PLACED
    # tickets from a Bet9ja settled/open-bets capture into
    # ``ledgers/betting_ledger.py`` (a required ``--currency`` argument on
    # every ticket, structured per-fold-size stake buckets when the
    # capture provides them, exact canonical-identity forecast linkage,
    # never substring matching), never touching
    # ``ledgers/forecast_ledger.py`` beyond reading it read-only to link
    # legs, and never settles a ticket itself (a separate, later step).
    if argv and argv[0] == "import-bet9ja-tickets":
        from .orchestration.bet9ja_ticket_import import main as import_bet9ja_tickets_main

        return import_bet9ja_tickets_main(argv[1:])

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
