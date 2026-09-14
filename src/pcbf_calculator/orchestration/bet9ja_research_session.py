"""One-command Bet9ja forecast research pipeline.

    Bet9ja capture (raw envelope)
    -> validated fixtures        (ingestion/bet9ja.py, unchanged)
    -> market-quality screening  (screening/research_batch.py's own gate,
                                   reused verbatim via market_quality_gate)
    -> Soccer Elo forecasts      (cli.py::run_calculator, category
                                   "soccer" -- the same function every
                                   other PCBF caller uses; the adapter's
                                   own real trained artifact)
    -> model-versus-market comparison (cli.py's own market_comparison)
    -> ranked research queue     (this module's own, documented
                                   research-priority ordering)

One deterministic command replaces what was previously a manual,
multi-command handoff between ``ingest-bet9ja``, ``screen-research-batch``,
and hand-invoking the calculator per fixture::

    python -m pcbf_calculator run-bet9ja-research CAPTURE.json --output-dir OUT/

This module reuses -- and never reimplements -- every validation, pricing,
and forecasting rule it depends on:

- Ingestion (admission/quarantine, kickoff resolution, typed ``BET9JA_*``
  codes) is entirely ``ingestion/bet9ja.py::ingest_assembled_capture``,
  called as-is.
- The market-quality gate (``evidence_quality != NORMAL`` ->
  ``SCREEN_MARKET_QUALITY_NOT_NORMAL``) is
  ``screening/research_batch.py::market_quality_gate``, the exact same
  function that module's own ``screen_research_batch`` calls -- refactored
  out of that module into a reusable form, not duplicated. Changing the
  gate's behavior later means changing it once, in one place, for both
  callers.
- Pricing and forecasting are both ``cli.py::run_calculator``, called with
  the complete fixture block (``home``, ``away``, ``competition``,
  ``kickoff_utc``, ``forecast_cutoff_utc``) the registered soccer adapter
  needs -- unlike ``screening/research_batch.py``'s own ``_price_fixture``,
  which deliberately omits that block and therefore never invokes an
  adapter. ``screen-research-batch``'s own contract (forecast fields
  hard-labeled ``NOT_COMPUTED``) is untouched by this module; this is a
  new, additive orchestration command, not a redefinition of that one.

``forecast_cutoff_utc`` is always the capture's own ``source_captured_at_utc``
(never the wall clock) -- the one honest "as of" timestamp this bridge
actually has. Re-running this module against the same capture file
byte-for-byte reproduces the same four output documents (proven directly
by a repeated-run test).

**Every fixture in the raw envelope ends up in exactly one of four typed
buckets**, each carrying a code kept verbatim from whichever layer
produced it (only one narrowly-scoped code, ``ORCH_MARKET_COMPARISON_UNAVAILABLE``,
originates in this module -- see ``errors.py``):

1. **Ingestion quarantine** -- rejected before ever reaching pricing (a
   real ``ingestion/bet9ja.py`` ``BET9JA_*`` code).
2. **Pricing-quality exclusion** -- ``run_calculator`` itself failed (a
   pricing-layer ``errors.py`` code -- should not happen for a fixture
   that already cleared ingestion, never silently swallowed if it somehow
   does), or the market's own pricing-quality signal was not ``NORMAL``
   (``SCREEN_MARKET_QUALITY_NOT_NORMAL``, the exact same gate
   ``screen-research-batch`` applies).
3. **Typed forecast abstention** -- pricing and market quality both
   cleared, but the adapter declined to forecast (one of
   ``adapters/soccer_1x2_elo_v1/errors.py``'s ``FORECAST_*`` codes).
4. **Forecast available** -- every gate cleared; becomes exactly one
   ranked research market.

**This produces a research shortlist for the Brain, never a betting
decision.** Every ranked market carries the exact same fixed,
non-configurable block, on every item, unconditionally::

    {
      "classification_ceiling": "RESEARCH-MODEL",
      "cash_stake": 0,
      "simulated_stake": 0,
      "recommendation_status": "NOT_AVAILABLE",
      "operator_decision": null
    }

``operator_decision`` names, explicitly, the fact that turning a ranked
market into an EXECUTE/PASS decision is a human-controlled gate this
module never crosses -- it is always ``null`` here, on every item, with no
code path that can set it to anything else (``_assert_ranked_market_stays_research_only``
checks this defensively, mirroring the identical check in
``screening/research_batch.py``). No `decision_input` is ever supplied to
``run_calculator``, so layer 3 (``PAPER``/``CASH``, ``STOP_*`` rules) never
runs here at all.

**Ranking never uses the forecast.** ``research_priority_score`` is the
pricing engine's own, already-documented market-quality prioritization
signal (``pricing/engine.py::_market_quality`` -- "meant to help a host
prioritize which markets are worth research attention," the exact same
score ``screen-research-batch`` already orders its own queue by). This
module ranks by that same score, descending, with the same deterministic
tie-break (``kickoff_utc``, ``source_competition_id``, ``home``, ``away``,
``source_fixture_id``) -- never by model probability, model-vs-market
divergence, or expected value. A ranked market's full forecast and
market-comparison detail is *carried alongside* the ranking, as
enrichment for the Brain's own separate research streams to read, but
never *drives* the ranking and never singles out, names, or reduces one
outcome to a stake anywhere in this module's output.

Output files (``run_session``/CLI):

- ``research-session-report.json`` -- canonical reconciled totals and
  reason counts.
- ``forecast-research-ranked.json`` -- every successful, model-enriched
  research market (model probabilities, market probabilities, per-outcome
  differences, model point EV, artifact provenance, deterministic
  calculation hash).
- ``forecast-abstentions.json`` -- every typed adapter abstention.
- ``ingestion-and-screening-exclusions.json`` -- every ingestion
  quarantine and pricing-quality exclusion.

**Optional forecast-ledger write (``--ledger-dir``).** Omitting it leaves
every behavior above byte-for-byte unchanged -- no ledger is touched.
Supplying it also idempotently writes every ranked market and forecast
abstention into the existing forecast ledger
(``ledgers/forecast_ledger.py``, via ``forecast_ledger_writer.py``'s own
module docstring) as one ``RECORDED`` event each, preflighted as a whole
batch before any write: a single conflicting record leaves BOTH the
ledger and this call's own research output files completely unwritten,
never a partial write. Pricing-quality-excluded and ingestion-quarantined
fixtures never reached the model and are never written to the ledger.
This never writes to ``ledgers/betting_ledger.py`` at all -- no ticket,
stake, or outcome-selection field is ever touched here.

Explicitly out of scope: results retrieval or settlement (``SCORED``
ledger events), external research or web searches, PCBF's five research
streams, EXECUTE/PASS decisions, outcome recommendations, ``PAPER``/``CASH``
admission, stake sizing or ticket construction, model retraining, new team
aliases.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import run_calculator
from ..ingestion.bet9ja import ingest_assembled_capture
from ..screening.research_batch import market_quality_gate
from .errors import ORCH_MARKET_COMPARISON_UNAVAILABLE

SCHEMA_VERSION_SESSION_REPORT = "pcbf-bet9ja-research-session-report.v1"
SCHEMA_VERSION_RANKED = "pcbf-forecast-research-ranked.v1"
SCHEMA_VERSION_ABSTENTIONS = "pcbf-forecast-abstentions.v1"
SCHEMA_VERSION_EXCLUSIONS = "pcbf-ingestion-and-screening-exclusions.v1"

CATEGORY = "soccer"

STAGE_INGESTION = "INGESTION"
STAGE_PRICING_QUALITY = "PRICING_QUALITY"
STAGE_FORECAST = "FORECAST"

RECOMMENDATION_STATUS = "NOT_AVAILABLE"
OPERATOR_DECISION = None


def _fixture_sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        entry.get("kickoff_utc") or "",
        entry.get("source_competition_id") or "",
        entry.get("home") or "",
        entry.get("away") or "",
        entry.get("source_fixture_id") or "",
    )


def _assert_ranked_market_stays_research_only(result: dict[str, Any], market: dict[str, Any]) -> None:
    """Defense-in-depth invariant, independent of the fact that no
    ``decision_input`` is ever supplied above: it must be structurally
    impossible for a market this module ranks to carry any classification
    other than ``RESEARCH-MODEL``, any nonzero stake of either kind, or an
    ``operator_decision`` other than ``null`` (turning this into an
    EXECUTE/PASS decision is a human-controlled Brain gate, never crossed
    here). If this ever fires it is a bug in this module (or a policy
    change upstream this module has not been updated for), never a caller
    input problem."""
    if result["classification_ceiling"] != "RESEARCH-MODEL":
        raise AssertionError(
            "Policy violation: this pipeline must never rank a market whose "
            f"classification_ceiling is {result['classification_ceiling']!r}, only "
            "'RESEARCH-MODEL' -- no decision_input is ever supplied and this module must "
            "never promote a fixture past the registry's own no-forecast cap."
        )
    if result.get("cash_stake") != 0 or result.get("simulated_stake") != 0:
        raise AssertionError(
            "Policy violation: a RESEARCH-MODEL market must carry cash_stake: 0 and "
            f"simulated_stake: 0, got cash_stake={result.get('cash_stake')!r}, "
            f"simulated_stake={result.get('simulated_stake')!r}."
        )
    if market["operator_decision"] is not None:
        raise AssertionError(
            f"Policy violation: operator_decision must always be null here, got "
            f"{market['operator_decision']!r} -- EXECUTE/PASS is a human-controlled Brain "
            "decision, never made by this module."
        )


def _price_and_forecast(fixture: dict[str, Any], forecast_cutoff_utc: str) -> dict[str, Any]:
    market = fixture["market"]
    request = {
        "event_id": fixture["source_fixture_id"],
        "category": CATEGORY,
        "market_prices": {"home": market["home"], "draw": market["draw"], "away": market["away"]},
        "fixture": {
            "home": fixture["home"],
            "away": fixture["away"],
            "competition": fixture["competition"],
            "kickoff_utc": fixture["kickoff_utc"],
            "forecast_cutoff_utc": forecast_cutoff_utc,
        },
    }
    return run_calculator(request)


def run_bet9ja_research_session(envelope: dict[str, Any]) -> dict[str, Any]:
    """Runs the full one-command pipeline against one already-loaded raw
    Bet9ja assembled-capture envelope (the same shape ``ingest-bet9ja``
    consumes).

    Returns a dict with the four output documents this module produces
    (``research_session_report``, ``forecast_research_ranked``,
    ``forecast_abstentions``, ``ingestion_and_screening_exclusions``) --
    this function performs no file I/O itself, so it can be
    tested/composed directly. Never raises for a well-formed envelope
    (``ingest_assembled_capture`` may still raise ``Bet9jaEnvelopeError``
    for a genuinely malformed one, same as ``ingest-bet9ja`` itself) --
    every per-fixture problem becomes a typed, kept-verbatim reason in
    exactly one of four buckets, never a dropped fixture and never an
    aborted run.
    """
    ingestion_result = ingest_assembled_capture(envelope)
    batch = ingestion_result["pcbf_research_batch"]
    quarantined = ingestion_result["fixtures_quarantined"]["quarantined"]
    admitted_fixtures = batch["fixtures"]

    source_capture_session_id = batch["source_capture_session_id"]
    source_captured_at_utc = batch["source_captured_at_utc"]
    forecast_cutoff_utc = source_captured_at_utc

    ranked: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    reason_counts: dict[str, dict[str, int]] = {STAGE_INGESTION: {}, STAGE_PRICING_QUALITY: {}, STAGE_FORECAST: {}}

    def record(
        bucket: list[dict[str, Any]],
        stage: str,
        reason_code: str,
        detail: str | None,
        fixture: dict[str, Any],
        raw: Any = None,
        market_prices: dict[str, Any] | None = None,
        forecast: dict[str, Any] | None = None,
        input_hash: str | None = None,
        calculation_hash: str | None = None,
        classification_ceiling: str | None = None,
    ) -> None:
        reason_counts[stage][reason_code] = reason_counts[stage].get(reason_code, 0) + 1
        entry = {
            "stage": stage,
            "reason": reason_code,
            "detail": detail,
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
            "forecast_cutoff_utc": forecast_cutoff_utc,
        }
        if raw is not None:
            entry["raw"] = raw
        # Only ever populated for a fixture that reached real pricing (the
        # FORECAST stage) -- an ingestion quarantine never had a validated
        # market to report, so this stays absent there rather than
        # fabricated. Threading these through lets the forecast-ledger
        # writer (orchestration/forecast_ledger_writer.py) build a
        # complete RECORDED event straight from this entry, with no
        # second call back into pricing/forecasting.
        if market_prices is not None:
            entry["market_prices"] = market_prices
        if forecast is not None:
            entry["forecast"] = forecast
        if input_hash is not None:
            entry["input_hash"] = input_hash
        if calculation_hash is not None:
            entry["calculation_hash"] = calculation_hash
        if classification_ceiling is not None:
            entry["classification_ceiling"] = classification_ceiling
        bucket.append(entry)

    for quarantine_record in quarantined:
        record(
            excluded,
            STAGE_INGESTION,
            quarantine_record["reason"],
            quarantine_record.get("detail"),
            {
                "source_fixture_id": quarantine_record.get("source_fixture_id"),
                "source_competition_id": quarantine_record.get("source_competition_id"),
                "sport": (quarantine_record.get("raw") or {}).get("sport"),
                "country": None,
                "competition": None,
                "home": ((quarantine_record.get("raw") or {}).get("participants") or {}).get("home"),
                "away": ((quarantine_record.get("raw") or {}).get("participants") or {}).get("away"),
                "kickoff_utc": None,
            },
            raw=quarantine_record.get("raw"),
        )

    for fixture in admitted_fixtures:
        result = _price_and_forecast(fixture, forecast_cutoff_utc)

        if result["status"] != "OK":
            failure = result["failure"] or {}
            record(
                excluded,
                STAGE_PRICING_QUALITY,
                failure.get("code") or "ORCH_PRICING_FAILED",
                failure.get("reason") or "Pricing failed for an unspecified reason.",
                fixture,
                raw=fixture,
            )
            continue

        pricing = result["pricing"]
        gate_result = market_quality_gate(pricing)
        if gate_result is not None:
            reason_code, detail = gate_result
            record(excluded, STAGE_PRICING_QUALITY, reason_code, detail, fixture, raw=fixture)
            continue

        forecast = result["forecast"] or {}
        if not forecast.get("forecast_available"):
            record(
                abstentions,
                STAGE_FORECAST,
                forecast.get("no_forecast_reason") or "FORECAST_UNAVAILABLE",
                None,
                fixture,
                market_prices=fixture["market"],
                forecast=forecast,
                input_hash=result["input_hash"],
                calculation_hash=result["calculation_hash"],
                classification_ceiling=result["classification_ceiling"],
            )
            continue

        market_comparison = result.get("market_comparison")
        if not market_comparison:
            record(
                abstentions,
                STAGE_FORECAST,
                ORCH_MARKET_COMPARISON_UNAVAILABLE,
                None,
                fixture,
                market_prices=fixture["market"],
                forecast=forecast,
                input_hash=result["input_hash"],
                calculation_hash=result["calculation_hash"],
                classification_ceiling=result["classification_ceiling"],
            )
            continue

        market = {
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
            "forecast_cutoff_utc": forecast_cutoff_utc,
            "classification_ceiling": result["classification_ceiling"],
            "cash_stake": result["cash_stake"],
            "simulated_stake": result["simulated_stake"],
            "recommendation_status": RECOMMENDATION_STATUS,
            "operator_decision": OPERATOR_DECISION,
            "research_priority_score": pricing["market_quality"]["research_priority_score"],
            "market_prices": fixture["market"],
            "pricing": pricing,
            "forecast": forecast,
            "market_comparison": market_comparison,
            "input_hash": result["input_hash"],
            "calculation_hash": result["calculation_hash"],
            "_sort_fixture": fixture,
        }
        _assert_ranked_market_stays_research_only(result, market)
        ranked.append(market)

    ranked.sort(key=lambda item: (-item["research_priority_score"], _fixture_sort_key(item["_sort_fixture"])))
    for index, item in enumerate(ranked, start=1):
        item["queue_position"] = index
        del item["_sort_fixture"]

    abstentions.sort(key=lambda item: (item["reason"], _fixture_sort_key(item)))
    excluded.sort(key=lambda item: (item["stage"], item["reason"], _fixture_sort_key(item)))

    raw_fixture_count = len(envelope.get("fixtures") or [])
    quarantined_count = len(quarantined)
    admitted_count = len(admitted_fixtures)
    ranked_count = len(ranked)
    abstained_count = len(abstentions)
    pricing_quality_excluded_count = sum(1 for item in excluded if item["stage"] == STAGE_PRICING_QUALITY)

    if quarantined_count + admitted_count != raw_fixture_count:
        raise AssertionError(
            f"Reconciliation failed: {quarantined_count} quarantined + {admitted_count} admitted "
            f"!= {raw_fixture_count} raw fixtures. This is a bug in ingestion, never a caller "
            "input problem."
        )
    if pricing_quality_excluded_count + abstained_count + ranked_count != admitted_count:
        raise AssertionError(
            f"Reconciliation failed: {pricing_quality_excluded_count} pricing-quality excluded + "
            f"{abstained_count} forecast abstained + {ranked_count} ranked != {admitted_count} "
            "admitted fixtures. This is a bug in this module, never a caller input problem -- "
            "every admitted fixture must end up in exactly one bucket."
        )

    flat_reason_counts = {stage: dict(sorted(counts.items())) for stage, counts in reason_counts.items()}
    model_versions = {item["forecast"]["model_version"] for item in ranked}
    model_artifact_hashes = {item["forecast"]["model_artifact_hash"] for item in ranked}

    research_session_report = {
        "schema_version": SCHEMA_VERSION_SESSION_REPORT,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "forecast_cutoff_utc": forecast_cutoff_utc,
        "note": (
            "Reuses ingestion/bet9ja.py, screening/research_batch.py's own market-quality "
            "gate, and cli.py::run_calculator verbatim -- no validation or pricing logic is "
            "reimplemented here. Every fixture ends up in exactly one of four buckets: "
            "ranked (forecast available), forecast abstention (typed), pricing-quality "
            "exclusion (typed), or ingestion quarantine (typed). classification_ceiling "
            "stays RESEARCH-MODEL and both stakes stay 0 on every ranked market; "
            "operator_decision is always null -- turning a ranked market into an "
            "EXECUTE/PASS decision is a human-controlled gate this pipeline never crosses."
        ),
        "counts": {
            "source_fixtures_raw": raw_fixture_count,
            "quarantined": quarantined_count,
            "admitted": admitted_count,
            "pricing_quality_excluded": pricing_quality_excluded_count,
            "forecast_abstained": abstained_count,
            "ranked_selections": ranked_count,
        },
        "reconciles": True,
        "reason_counts": flat_reason_counts,
        "model_version": next(iter(model_versions)) if len(model_versions) == 1 else sorted(model_versions),
        "model_artifact_hash": next(iter(model_artifact_hashes)) if len(model_artifact_hashes) == 1 else sorted(model_artifact_hashes),
    }

    forecast_research_ranked = {
        "schema_version": SCHEMA_VERSION_RANKED,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "note": (
            "Research shortlist for the Brain's own five research streams and "
            "human-controlled EXECUTE/PASS gate -- never a betting slip. Ranked by "
            "research_priority_score, the pricing engine's own already-documented "
            "market-quality prioritization signal (see pricing/engine.py and "
            "screening/research_batch.py's own module docstrings) -- the same score "
            "screen-research-batch already orders its own queue by. Never ranked by model "
            "probability, model-vs-market divergence, or expected value, and no outcome "
            "is ever singled out, named 'best', or reduced to a stake. operator_decision "
            "is always null on every item."
        ),
        "markets": ranked,
    }
    forecast_abstentions_doc = {
        "schema_version": SCHEMA_VERSION_ABSTENTIONS,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "abstentions": abstentions,
    }
    ingestion_and_screening_exclusions = {
        "schema_version": SCHEMA_VERSION_EXCLUSIONS,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "excluded": excluded,
    }

    return {
        "research_session_report": research_session_report,
        "forecast_research_ranked": forecast_research_ranked,
        "forecast_abstentions": forecast_abstentions_doc,
        "ingestion_and_screening_exclusions": ingestion_and_screening_exclusions,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_session(input_path: Path, output_dir: Path, ledger_dir: Path | None = None) -> dict[str, Any]:
    """Runs the full pipeline and writes the four research output files,
    exactly as before. When ``ledger_dir`` is given (additive, optional --
    omitting it leaves every existing behavior byte-for-byte unchanged),
    also idempotently writes every ranked market and forecast abstention
    into the forecast ledger at ``ledger_dir`` (see
    ``forecast_ledger_writer.py``'s own module docstring) -- preflighted
    as a whole batch, so a single conflicting record raises
    ``forecast_ledger_writer.LedgerBatchConflictError`` and leaves BOTH
    the ledger AND this call's own research output files unwritten
    (checked before either is touched)."""
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    result = run_bet9ja_research_session(envelope)

    if ledger_dir is not None:
        from . import forecast_ledger_writer

        events = forecast_ledger_writer.build_ledger_events(result)
        ledger_path = ledger_dir / "forecast-ledger.jsonl"
        # Raises LedgerBatchConflictError (writing nothing) before any
        # research output file below is written either -- a conflicting
        # batch must leave everything untouched, not just the ledger.
        ledger_write_summary = forecast_ledger_writer.write_batch(ledger_path, events)
        result["ledger_write_summary"] = ledger_write_summary

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "research-session-report.json", result["research_session_report"])
    _write_json(output_dir / "forecast-research-ranked.json", result["forecast_research_ranked"])
    _write_json(output_dir / "forecast-abstentions.json", result["forecast_abstentions"])
    _write_json(output_dir / "ingestion-and-screening-exclusions.json", result["ingestion_and_screening_exclusions"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator run-bet9ja-research",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="Raw exported Bet9ja capture envelope JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into")
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=None,
        help=(
            "Optional: also idempotently write every ranked market and forecast abstention "
            "into the forecast ledger at this directory (forecast-ledger.jsonl). Omit to "
            "leave existing behavior unchanged -- no ledger is touched."
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = run_session(args.input, args.output_dir, ledger_dir=args.ledger_dir)
    except Exception as exc:
        from . import forecast_ledger_writer

        if isinstance(exc, forecast_ledger_writer.LedgerBatchConflictError):
            print(f"CONFLICT: {exc}")
            return 2
        raise

    counts = result["research_session_report"]["counts"]
    print(
        f"OK: {counts['ranked_selections']} ranked research markets, "
        f"{counts['forecast_abstained']} forecast abstentions, "
        f"{counts['pricing_quality_excluded']} pricing-quality excluded, "
        f"{counts['quarantined']} ingestion quarantined "
        f"({counts['source_fixtures_raw']} raw fixtures) -> {args.output_dir}"
    )
    if "ledger_write_summary" in result:
        summary = result["ledger_write_summary"]
        print(
            f"LEDGER: {summary['attempted']} attempted, {summary['appended']} appended, "
            f"{summary['duplicate_skipped']} duplicate-skipped, {summary['conflicted']} conflicted "
            f"({summary['total_ledger_records']} total records) -> {args.ledger_dir}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
