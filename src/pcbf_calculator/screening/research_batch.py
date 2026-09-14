"""PCBF research-batch market-quality triage workflow.

First functional step beyond file preparation (``ingestion/bet9ja.py``
produces the input this consumes, ``pcbf-research-batch.json``): runs every
admitted fixture through this platform's own host-contract pricing/decision
pipeline (``cli.py::run_calculator``, category ``"soccer"``, exactly the
same function every other PCBF caller uses — no separate pricing math is
implemented here), applies one real, non-fabricated pricing-quality gate on
top of that pipeline's own output, and produces a deterministic research
queue of markets worth further attention, plus a typed-reason exclusion
list for everything screened out.

    pcbf-research-batch.json
    -> price each fixture (layer 1, via run_calculator)
    -> exclude markets with non-NORMAL pricing quality
    -> queue surviving markets by pricing-quality priority
    -> research-queue-ranked.json / research-queue-excluded.json / research-queue-report.json

**This is market-quality research triage, not selection or ranking of
betting outcomes.** It answers "which markets have clean, complete,
reasonably-priced data worth a researcher's attention next," never "which
outcome is likely to win" or "which market has a positive edge." Every
market that clears the one gate below becomes one research queue item at
the exact same workflow state (``RESEARCH_QUEUE``) with every prediction/
edge/recommendation/stake field explicitly marked not computed — see
"Required per-market fields" below. Nothing this module produces may ever
be read as a pick, a research market to bet on, or a recommendation of any
kind. There is no "candidate" concept anywhere in this workflow's output —
a market either becomes a research queue item or an excluded market, never
a "candidate" awaiting a betting decision.

Every fixture is submitted with **no** ``decision_input`` block: a Bet9ja
capture carries no real evidence/liquidity/uncertainty data (sample size,
available stake, a calibrated confidence width) for this bridge to supply,
and this platform's own discipline is to never fabricate one just to
exercise a code path (see ``ingestion/bet9ja.py`` and
``decision/engine.py``'s own module docstrings for the same rule applied
elsewhere). Layer 3 (the decision engine, ``STOP_*`` rules, ``PAPER``/
``CASH`` classification) therefore never runs here at all — this is
structural, not a bug: with no admitted forecast for ``category="soccer"``
(``adapter-registry.yaml``: ``adapter_status: DESIGN_IN_PROGRESS``, no
concrete adapter registered in ``adapters/registry.py``),
``run_calculator`` already resolves every fixture's own
``classification_ceiling`` to ``RESEARCH-MODEL`` with
``cash_stake: 0``/``simulated_stake: 0`` on its own, before this module
does anything — triage only decides which of those already-capped markets
are worth queuing for research, never whether one may be promoted past
that cap or singled out as a recommendation.
``_assert_queued_market_stays_research_only`` below re-checks this on
every surviving market as a defensive invariant, mirroring
``decision/engine.py``'s own defense-in-depth checks.

Pricing-quality gate (a hard exclusion with a typed reason):

1. Pricing itself must succeed. A market that reached this stage having
   already passed ``ingestion/bet9ja.py``'s own admission checks (numeric,
   complete, >1 H/D/A prices) should never fail here, but if it somehow
   does, the pricing engine's own typed code (``errors.py``, e.g.
   ``INVALID_PRICE_VALUE``) is kept verbatim as the exclusion reason —
   never re-coded, never silently dropped.
2. ``SCREEN_MARKET_QUALITY_NOT_NORMAL`` — the market's own
   ``evidence_quality`` (``pricing/engine.py::_market_quality``) is not
   ``NORMAL``: an arbitrage-shaped or high-margin, low-evidence market is
   not worth queuing for research regardless of anything else about it.

There is deliberately **no** "positive expected value" gate, and this
module never ranks or singles out an individual H/D/A outcome. Under
Release A's only de-vig method (multiplicative/proportional), every
outcome's ``point_ev`` reduces algebraically to
``1 / sum(implied_probabilities) - 1`` — identical across every outcome in
a market, and non-positive for any market carrying the nonnegative margin
every real bookmaker market has. A gate or ranking key built on that sign
would fire on/favor markets arbitrarily rather than meaningfully, and
critically would read as exactly the outcome-level recommendation this
workflow must never produce; see ``screening/errors.py`` for the full
reasoning. This module records each market's full per-outcome pricing
detail (every outcome, not one singled out) for audit/research purposes,
and never selects, names, or ranks a "best" outcome anywhere.

**A tighter bookmaker margin indicates cleaner, more complete pricing
data — not a better bet.** Queuing order is the pricing engine's own
``market_quality.research_priority_score`` (already documented in
``pricing/engine.py`` as "meant to help a host prioritize which markets
are worth research attention"), descending, with a stable, fully
deterministic tie-break on ``(kickoff_utc, source_competition_id, home,
away, source_fixture_id)`` — the same tie-break shape
``ingestion/bet9ja.py`` uses for its own fixture ordering. This orders
*markets*, never the outcomes within one. No current-clock timestamp
appears anywhere in the output; re-running this module against the same
research batch produces byte-identical output files (proven directly by a
repeated-run test).

**Required per-market fields.** Every research queue item explicitly
carries::

    {
      "workflow_state": "RESEARCH_QUEUE",
      "classification_ceiling": "RESEARCH-MODEL",
      "forecast_probability_status": "NOT_COMPUTED",
      "edge_status": "NOT_COMPUTED",
      "recommendation_status": "NOT_AVAILABLE",
      "stake_status": "NOT_AVAILABLE",
      "cash_stake": 0,
      "simulated_stake": 0
    }

Every value above is fixed and non-configurable for every item this module
ever produces — there is no code path in this workflow that can set any of
them to anything else. A future adapter-driven forecast or recommendation
layer would need its own, separately reviewed workflow; it does not exist
here. This module never records a "best" or "selected" outcome anywhere:
the full per-outcome pricing detail is kept (every outcome, not one
singled out) for audit purposes, clearly labeled as market-implied
de-vigged values — not a forecast, not an edge, not a recommendation — see
"Required per-market fields" above and the pricing engine's own module
docstring for what ``pricing.outcomes`` actually is.

Explicitly out of scope, same as ``ingestion/bet9ja.py``: probability
generation (this module prices with the same de-vigged *market-implied*
math every PCBF category already uses — it trains or forecasts nothing),
outcome selection or ranking, ``PAPER``/``CASH`` promotion, Kelly or any
other staking, external odds retrieval, results settlement,
database/ledger writes, automatic betting.

Output files (``run_screen``/CLI): ``research-queue-report.json``,
``research-queue-ranked.json`` (the research queue itself, ordered by
pricing-quality priority), ``research-queue-excluded.json`` (every
excluded market with its typed reason). "Ranked" here means market order
only — see above; it never ranks or implies anything about the outcomes
within one market.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import run_calculator
from .errors import SCREEN_MARKET_QUALITY_NOT_NORMAL

SCHEMA_VERSION_REPORT = "pcbf-research-queue-report.v1"
SCHEMA_VERSION_QUEUE = "pcbf-research-queue-ranked.v1"
SCHEMA_VERSION_EXCLUDED = "pcbf-research-queue-excluded.v1"

CATEGORY = "soccer"

WORKFLOW_STATE = "RESEARCH_QUEUE"
FORECAST_PROBABILITY_STATUS = "NOT_COMPUTED"
EDGE_STATUS = "NOT_COMPUTED"
RECOMMENDATION_STATUS = "NOT_AVAILABLE"
STAKE_STATUS = "NOT_AVAILABLE"


def _sort_key(fixture: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        fixture.get("kickoff_utc") or "",
        fixture.get("source_competition_id") or "",
        fixture.get("home") or "",
        fixture.get("away") or "",
        fixture.get("source_fixture_id") or "",
    )


def _assert_queued_market_stays_research_only(result: dict[str, Any]) -> None:
    """Defense-in-depth invariant, independent of the fact that no
    ``decision_input`` is ever supplied above: it must be structurally
    impossible for a market this module queues to carry any classification
    other than ``RESEARCH-MODEL``, or any nonzero stake of either kind. If
    this ever fires it is a bug in this module (or a policy change
    upstream this module has not been updated for), never a caller input
    problem."""
    if result["classification_ceiling"] != "RESEARCH-MODEL":
        raise AssertionError(
            "Policy violation: research-batch triage must never queue a market whose "
            f"classification_ceiling is {result['classification_ceiling']!r}, only "
            "'RESEARCH-MODEL' — this workflow never supplies decision_input and must "
            "never promote a market past the registry's own no-forecast cap."
        )
    if result.get("cash_stake") != 0 or result.get("simulated_stake") != 0:
        raise AssertionError(
            "Policy violation: a RESEARCH-MODEL market must carry cash_stake: 0 and "
            f"simulated_stake: 0, got cash_stake={result.get('cash_stake')!r}, "
            f"simulated_stake={result.get('simulated_stake')!r}."
        )


def _price_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    market = fixture["market"]
    request = {
        "event_id": fixture["source_fixture_id"],
        "category": CATEGORY,
        "market_prices": {
            "home": market["home"],
            "draw": market["draw"],
            "away": market["away"],
        },
    }
    return run_calculator(request)


def market_quality_gate(pricing: dict[str, Any]) -> tuple[str, str] | None:
    """The one, shared implementation of this platform's market-quality
    exclusion rule (see this module's own docstring for the full
    reasoning) -- reused verbatim by every module that needs the same
    gate, never reimplemented elsewhere. Takes an already-computed
    ``pricing`` result (``run_calculator(...)["pricing"]``) and returns
    ``(reason_code, detail)`` if it excludes this market, or ``None`` if
    it clears the gate."""
    market_quality = pricing["market_quality"]
    if market_quality["evidence_quality"] != "NORMAL":
        return (
            SCREEN_MARKET_QUALITY_NOT_NORMAL,
            f"market_quality.evidence_quality is {market_quality['evidence_quality']!r} "
            f"(bookmaker_margin={market_quality['bookmaker_margin']}), not NORMAL.",
        )
    return None


def screen_research_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Triage every fixture in an ``ingestion/bet9ja.py``-produced
    ``pcbf-research-batch.json`` object by pricing quality.

    Returns a dict with ``research_queue_report``, ``research_queue`` (the
    ranked research queue itself) and ``excluded_markets`` — mirroring
    ``ingestion.bet9ja.ingest_assembled_capture``'s own three-part return
    shape. Never raises for a well-formed research batch; a fixture this
    module cannot price or does not clear the pricing-quality gate becomes
    an excluded market with a typed reason, never dropped silently and
    never allowed to abort the whole run. Never selects, names, or ranks an
    individual outcome anywhere in its output — see this module's own
    docstring.
    """
    fixtures = batch.get("fixtures") or []
    source_capture_session_id = batch.get("source_capture_session_id")
    source_captured_at_utc = batch.get("source_captured_at_utc")

    queued_markets: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}

    for fixture in fixtures:
        result = _price_fixture(fixture)

        def exclude(reason_code: str, detail: str) -> None:
            reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
            excluded.append(
                {
                    "reason": reason_code,
                    "detail": detail,
                    "source_fixture_id": fixture.get("source_fixture_id"),
                    "source_competition_id": fixture.get("source_competition_id"),
                    "raw": fixture,
                }
            )

        if result["status"] != "OK":
            failure = result["failure"] or {}
            exclude(
                failure.get("code") or "SCREEN_PRICING_FAILED",
                failure.get("reason") or "Pricing failed for an unspecified reason.",
            )
            continue

        pricing = result["pricing"]
        market_quality = pricing["market_quality"]
        gate_result = market_quality_gate(pricing)
        if gate_result is not None:
            reason_code, detail = gate_result
            exclude(reason_code, detail)
            continue

        _assert_queued_market_stays_research_only(result)

        queued_markets.append(
            {
                "workflow_state": WORKFLOW_STATE,
                "source": fixture.get("source"),
                "source_capture_session_id": fixture.get("source_capture_session_id"),
                "source_fixture_id": fixture.get("source_fixture_id"),
                "source_competition_id": fixture.get("source_competition_id"),
                "sport": fixture.get("sport"),
                "country": fixture.get("country"),
                "competition": fixture.get("competition"),
                "home": fixture.get("home"),
                "away": fixture.get("away"),
                "kickoff_utc": fixture.get("kickoff_utc"),
                "classification_ceiling": result["classification_ceiling"],
                "forecast_probability_status": FORECAST_PROBABILITY_STATUS,
                "edge_status": EDGE_STATUS,
                "recommendation_status": RECOMMENDATION_STATUS,
                "stake_status": STAKE_STATUS,
                "cash_stake": result["cash_stake"],
                "simulated_stake": result["simulated_stake"],
                "research_priority_score": market_quality["research_priority_score"],
                "pricing": pricing,
                "forecast": result["forecast"],
                "_sort_fixture": fixture,
            }
        )

    queued_markets.sort(
        key=lambda market: (
            -market["research_priority_score"],
            _sort_key(market["_sort_fixture"]),
        )
    )
    for index, market in enumerate(queued_markets, start=1):
        market["queue_position"] = index
        del market["_sort_fixture"]

    excluded.sort(key=_sort_key)

    total = len(fixtures)
    queued = len(queued_markets)
    excluded_count = len(excluded)
    if queued + excluded_count != total:
        raise AssertionError(
            f"Screening reconciliation failed: {queued} queued + {excluded_count} excluded "
            f"!= {total} source fixtures. This is a bug in this module, never a caller "
            "input problem — every fixture must end up in exactly one bucket."
        )

    research_queue_report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "source_fixtures": total,
        "markets_queued": queued,
        "markets_excluded": excluded_count,
        "reconciles": queued + excluded_count == total,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    research_queue_doc = {
        "schema_version": SCHEMA_VERSION_QUEUE,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "note": (
            "Market-quality research triage only. queue_position and "
            "research_priority_score order MARKETS by pricing-data quality "
            "(a tighter bookmaker margin means cleaner, more complete "
            "pricing data, not a better bet). Every outcome price under "
            "'pricing' is a market-implied de-vigged value, not a forecast. "
            "Nothing here is a forecast, an edge, or a recommendation of "
            "any outcome -- see this module's own docstring."
        ),
        "markets": queued_markets,
    }
    excluded_markets_doc = {
        "schema_version": SCHEMA_VERSION_EXCLUDED,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "excluded": excluded,
    }

    return {
        "research_queue_report": research_queue_report,
        "research_queue": research_queue_doc,
        "excluded_markets": excluded_markets_doc,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_screen(input_path: Path, output_dir: Path) -> dict[str, Any]:
    batch = json.loads(input_path.read_text(encoding="utf-8"))
    result = screen_research_batch(batch)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "research-queue-report.json", result["research_queue_report"])
    _write_json(output_dir / "research-queue-ranked.json", result["research_queue"])
    _write_json(output_dir / "research-queue-excluded.json", result["excluded_markets"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator screen-research-batch",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="pcbf-research-batch.json produced by ingest-bet9ja")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into")
    args = parser.parse_args(argv)

    result = run_screen(args.input, args.output_dir)
    report = result["research_queue_report"]
    print(
        f"OK: {report['markets_queued']} research queue items, {report['markets_excluded']} excluded markets "
        f"({report['source_fixtures']} fixtures) -> {args.output_dir}"
    )
    return 0
