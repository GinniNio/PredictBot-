"""PCBF research-batch screening and ranking workflow.

First functional step beyond file preparation (``ingestion/bet9ja.py``
produces the input this consumes, ``pcbf-research-batch.json``): runs every
admitted fixture through this platform's own host-contract pricing/decision
pipeline (``cli.py::run_calculator``, category ``"soccer"``, exactly the
same function every other PCBF caller uses — no separate pricing math is
implemented here), applies one real, non-fabricated screening gate on top
of that pipeline's own output, and produces a deterministic, ranked list of
``RESEARCH-MODEL`` candidates plus a typed-reason rejection list for
everything screened out.

    pcbf-research-batch.json
    -> price each fixture (layer 1, via run_calculator)
    -> screen (market-quality gate)
    -> rank surviving candidates by research priority
    -> research-candidates-ranked.json / research-candidates-rejected.json

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
does anything — screening only decides which of those already-capped
candidates are worth ranking, never whether one may be promoted past that
cap. ``_assert_screened_candidate_stays_research_model`` below re-checks
this on every surviving candidate as a defensive invariant, mirroring
``decision/engine.py``'s own defense-in-depth checks.

Screening gate (a hard reject with a typed reason):

1. Pricing itself must succeed. A market that reached this stage having
   already passed ``ingestion/bet9ja.py``'s own admission checks (numeric,
   complete, >1 H/D/A prices) should never fail here, but if it somehow
   does, the pricing engine's own typed code (``errors.py``, e.g.
   ``INVALID_PRICE_VALUE``) is kept verbatim as the rejection reason —
   never re-coded, never silently dropped.
2. ``SCREEN_MARKET_QUALITY_NOT_NORMAL`` — the market's own
   ``evidence_quality`` (``pricing/engine.py::_market_quality``) is not
   ``NORMAL``: an arbitrage-shaped or high-margin, low-evidence market is
   not worth ranking for research regardless of anything else about it.

There is deliberately **no** "positive expected value" reject gate. Under
Release A's only de-vig method (multiplicative/proportional), every
outcome's ``point_ev`` reduces algebraically to
``1 / sum(implied_probabilities) - 1`` — identical across every outcome in
a market, and non-positive for any market carrying the nonnegative margin
every real bookmaker market has. Gating on that sign would fire on
essentially every real market and admit almost nothing; see
``screening/errors.py`` for the full reasoning. ``best_outcome_point_ev``
is still recorded on every ranked candidate for audit/research purposes —
it is simply not used as a gate or as the ranking key.

Ranking: surviving candidates are ordered by the pricing engine's own
``market_quality.research_priority_score`` (already documented in
``pricing/engine.py`` as "meant to help a host prioritize which markets are
worth research attention"), descending, with a stable, fully deterministic
tie-break on ``(kickoff_utc, source_competition_id, home, away,
source_fixture_id)`` — the same tie-break shape ``ingestion/bet9ja.py``
uses for its own fixture ordering. No current-clock timestamp appears
anywhere in the output; re-running this module against the same research
batch produces byte-identical output files (proven directly by a
repeated-run test).

Explicitly out of scope, same as ``ingestion/bet9ja.py``: probability
generation (this module prices with the same de-vigged *market-implied*
math every PCBF category already uses — it trains or forecasts nothing),
``PAPER``/``CASH`` promotion, Kelly or any other staking, external odds
retrieval, results settlement, database/ledger writes, automatic betting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import run_calculator
from .errors import SCREEN_MARKET_QUALITY_NOT_NORMAL

SCHEMA_VERSION_REPORT = "pcbf-screening-report.v1"
SCHEMA_VERSION_RANKED = "pcbf-research-candidates-ranked.v1"
SCHEMA_VERSION_REJECTED = "pcbf-research-candidates-rejected.v1"

CATEGORY = "soccer"


def _sort_key(fixture: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        fixture.get("kickoff_utc") or "",
        fixture.get("source_competition_id") or "",
        fixture.get("home") or "",
        fixture.get("away") or "",
        fixture.get("source_fixture_id") or "",
    )


def _assert_screened_candidate_stays_research_model(result: dict[str, Any]) -> None:
    """Defense-in-depth invariant, independent of the fact that no
    ``decision_input`` is ever supplied above: it must be structurally
    impossible for a candidate this module ranks to carry any
    classification other than ``RESEARCH-MODEL``, or any nonzero stake of
    either kind. If this ever fires it is a bug in this module (or a policy
    change upstream this module has not been updated for), never a caller
    input problem."""
    if result["classification_ceiling"] != "RESEARCH-MODEL":
        raise AssertionError(
            "Policy violation: research-batch screening must never rank a candidate "
            f"whose classification_ceiling is {result['classification_ceiling']!r}, "
            "only 'RESEARCH-MODEL' — this workflow never supplies decision_input and "
            "must never promote a candidate past the registry's own no-forecast cap."
        )
    if result.get("cash_stake") != 0 or result.get("simulated_stake") != 0:
        raise AssertionError(
            "Policy violation: a RESEARCH-MODEL candidate must carry cash_stake: 0 and "
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


def screen_research_batch(batch: dict[str, Any]) -> dict[str, Any]:
    """Screen and rank every fixture in an
    ``ingestion/bet9ja.py``-produced ``pcbf-research-batch.json`` object.

    Returns a dict with ``screening_report``, ``candidates_ranked`` and
    ``candidates_rejected`` — mirroring ``ingestion.bet9ja.ingest_assembled_capture``'s
    own three-part return shape. Never raises for a well-formed research
    batch; a fixture this module cannot price or does not clear the
    market-quality gate is recorded in ``candidates_rejected`` with a typed
    reason, never dropped silently and never allowed to abort the whole
    run.
    """
    fixtures = batch.get("fixtures") or []
    source_capture_session_id = batch.get("source_capture_session_id")
    source_captured_at_utc = batch.get("source_captured_at_utc")

    ranked_candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}

    for fixture in fixtures:
        result = _price_fixture(fixture)

        def reject(reason_code: str, detail: str) -> None:
            reason_counts[reason_code] = reason_counts.get(reason_code, 0) + 1
            rejected.append(
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
            reject(
                failure.get("code") or "SCREEN_PRICING_FAILED",
                failure.get("reason") or "Pricing failed for an unspecified reason.",
            )
            continue

        pricing = result["pricing"]
        market_quality = pricing["market_quality"]
        if market_quality["evidence_quality"] != "NORMAL":
            reject(
                SCREEN_MARKET_QUALITY_NOT_NORMAL,
                f"market_quality.evidence_quality is {market_quality['evidence_quality']!r} "
                f"(bookmaker_margin={market_quality['bookmaker_margin']}), not NORMAL.",
            )
            continue

        _assert_screened_candidate_stays_research_model(result)

        outcomes = pricing["outcomes"]
        best_outcome = max(outcomes, key=lambda item: item["point_ev"])

        ranked_candidates.append(
            {
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
                "cash_stake": result["cash_stake"],
                "simulated_stake": result["simulated_stake"],
                "research_priority_score": market_quality["research_priority_score"],
                "best_outcome": best_outcome["outcome"],
                "best_outcome_point_ev": best_outcome["point_ev"],
                "pricing": pricing,
                "forecast": result["forecast"],
                "_sort_fixture": fixture,
            }
        )

    ranked_candidates.sort(
        key=lambda candidate: (
            -candidate["research_priority_score"],
            _sort_key(candidate["_sort_fixture"]),
        )
    )
    for index, candidate in enumerate(ranked_candidates, start=1):
        candidate["rank"] = index
        del candidate["_sort_fixture"]

    rejected.sort(key=_sort_key)

    total = len(fixtures)
    admitted = len(ranked_candidates)
    quarantined = len(rejected)
    if admitted + quarantined != total:
        raise AssertionError(
            f"Screening reconciliation failed: {admitted} ranked + {quarantined} rejected "
            f"!= {total} source fixtures. This is a bug in this module, never a caller "
            "input problem — every fixture must end up in exactly one bucket."
        )

    screening_report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "source_fixtures": total,
        "candidates_ranked": admitted,
        "candidates_rejected": quarantined,
        "reconciles": admitted + quarantined == total,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    candidates_ranked_doc = {
        "schema_version": SCHEMA_VERSION_RANKED,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "candidates": ranked_candidates,
    }
    candidates_rejected_doc = {
        "schema_version": SCHEMA_VERSION_REJECTED,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "rejected": rejected,
    }

    return {
        "screening_report": screening_report,
        "candidates_ranked": candidates_ranked_doc,
        "candidates_rejected": candidates_rejected_doc,
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
    _write_json(output_dir / "screening-report.json", result["screening_report"])
    _write_json(output_dir / "research-candidates-ranked.json", result["candidates_ranked"])
    _write_json(output_dir / "research-candidates-rejected.json", result["candidates_rejected"])
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
    report = result["screening_report"]
    print(
        f"OK: {report['candidates_ranked']} ranked, {report['candidates_rejected']} rejected "
        f"({report['source_fixtures']} fixtures) -> {args.output_dir}"
    )
    return 0
