"""Bet9ja capture -> Soccer 1X2 adapter -> ranked research report.

This is the first pipeline stage that actually exercises a registered
forecasting adapter end to end, starting from the raw exported Bet9ja
capture file (the same envelope ``ingest-bet9ja`` consumes) and ending in
one concise, research-facing report plus an append-only predictions
ledger. It collapses what was previously a manual, multi-step process
(ingest -> hand-pick soccer fixtures -> run each through the CLI ->
hand-compare model vs. market) into one deterministic command:

    Bet9ja export (raw envelope)
    -> ingest (ingestion/bet9ja.py::ingest_assembled_capture)
    -> price + forecast every admitted fixture (layer 1 + layer 2, via
       cli.py::run_calculator -- category "soccer", the exact same
       function every other PCBF caller uses; no separate pricing or
       scoring math is implemented here)
    -> rank fixtures with a real forecast by how much the model and the
       market disagree
    -> soccer-research-report.json / soccer-research-report.md /
       soccer-predictions-ledger.jsonl (appended, not overwritten)

**This ranks research attention, never betting outcomes.** The ranking
key (``divergence_score``, see below) says "the model and the market see
this fixture very differently, a researcher should look at it next" --
never "back this side" or "this is an edge." No outcome is ever singled
out, named "best," or reduced to a stake. Every item this module ever
produces carries the exact same fixed, non-configurable block::

    {
      "classification_ceiling": "RESEARCH-MODEL",
      "cash_stake": 0,
      "simulated_stake": 0
    }

``_assert_ranked_selection_stays_research_only`` re-checks this on every
surviving selection as a defensive invariant, mirroring
``screening/research_batch.py``'s own identically-purposed check -- with
zero rows in ``registries/data/model-admission-registry.yaml``,
``run_calculator`` already resolves every fixture to ``RESEARCH-MODEL``
with both stakes at ``0`` before this module does anything; the decision
layer (``PAPER``/``CASH``, ``STOP_*`` rules) never runs here at all, since
no ``decision_input`` is ever supplied (same reasoning as
``screening/research_batch.py``'s own module docstring).

This module is deliberately distinct from ``screening/research_batch.py``:
that module triages *market pricing quality* (a bookmaker-margin gate,
never touching a forecast); this one triages *model-vs-market divergence*
and requires a real forecast to have been produced. The two do not share a
gate and are not meant to replace one another -- a market can be a
market-quality research-queue item, a model-divergence research selection,
both, or neither, independently.

**Nothing is silently dropped.** Every fixture in the raw envelope ends up
in exactly one of three buckets, each carrying a typed reason kept
verbatim from whichever layer produced it (this module invents no new
error codes of its own):

- ``INGESTION`` -- quarantined before ever reaching pricing (an
  ``ingestion/bet9ja.py`` ``BET9JA_*`` code: unsupported sport/status/
  market family, incomplete or invalid prices, missing team name or
  competition attribution, unresolvable kickoff).
- ``PRICING`` -- ``run_calculator`` itself failed (this platform's own
  ``errors.py`` codes) -- should not happen for a fixture that already
  cleared ingestion's own price-completeness checks, but is never
  silently swallowed if it somehow does.
- ``FORECAST`` -- pricing succeeded but the adapter declined to forecast
  (one of ``adapters/soccer_1x2_elo_v1/errors.py``'s typed
  ``FORECAST_*`` codes -- unresolved competition/team, fixture already
  started, artifact stale or past its cutoff, etc.).

Every fixture that clears all three stages becomes exactly one ranked
research selection.

**Determinism.** Every fixture is priced/forecast as of the capture's own
``source_captured_at_utc`` (never the wall clock) -- this is the one
honest "as of" timestamp this bridge actually has, and using it means
re-running this module against the same capture file byte-for-byte
reproduces the same report (proven directly by a repeated-run test). The
predictions ledger's own ``prediction_recorded_at_utc`` field is the same
timestamp, for the same reason.

**Predictions ledger.** Every ranked selection also becomes one row
appended to ``soccer-predictions-ledger.jsonl`` (JSON Lines, one object
per line) -- the real model probabilities, real market prices, and every
identifying field needed to later join this prediction against a real
settled result. ``actual_result``/``settled_at_utc`` are always ``null``
and ``evaluation_status`` is always ``"PENDING_RESULT"`` -- settling a
prediction against a real result is a separate, not-yet-built workflow;
this module only ever writes the "before kickoff" half of that record,
never fabricates or backfills a result. Re-running this module against
the same capture appends the same rows again -- de-duplication is a
caller/settlement-workflow concern, explicitly out of scope here (see
below).

Explicitly out of scope, same discipline as every other stage in this
pipeline: outcome selection, staking of any kind, ``PAPER``/``CASH``
promotion, external odds retrieval, actual results settlement, ledger
de-duplication or reconciliation across multiple runs, database writes,
automatic betting.

Output files (``run_report``/CLI): ``soccer-research-report.json``,
``soccer-research-report.md``, and an appended
``soccer-predictions-ledger.jsonl``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import run_calculator
from ..ingestion.bet9ja import ingest_assembled_capture

SCHEMA_VERSION_REPORT = "pcbf-soccer-research-report.v1"
SCHEMA_VERSION_LEDGER_ROW = "pcbf-soccer-prediction-record.v1"

CATEGORY = "soccer"

STAGE_INGESTION = "INGESTION"
STAGE_PRICING = "PRICING"
STAGE_FORECAST = "FORECAST"


def _fixture_sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        entry.get("kickoff_utc") or "",
        entry.get("source_competition_id") or "",
        entry.get("home") or "",
        entry.get("away") or "",
        entry.get("source_fixture_id") or "",
    )


def _assert_ranked_selection_stays_research_only(result: dict[str, Any]) -> None:
    """Defense-in-depth invariant, independent of the fact that no
    ``decision_input`` is ever supplied above: it must be structurally
    impossible for a selection this module ranks to carry any
    classification other than ``RESEARCH-MODEL``, or any nonzero stake of
    either kind. If this ever fires it is a bug in this module (or a
    policy change upstream this module has not been updated for), never a
    caller input problem."""
    if result["classification_ceiling"] != "RESEARCH-MODEL":
        raise AssertionError(
            "Policy violation: the research report must never rank a selection whose "
            f"classification_ceiling is {result['classification_ceiling']!r}, only "
            "'RESEARCH-MODEL' -- this workflow never supplies decision_input and must "
            "never promote a fixture past the registry's own no-forecast cap."
        )
    if result.get("cash_stake") != 0 or result.get("simulated_stake") != 0:
        raise AssertionError(
            "Policy violation: a RESEARCH-MODEL selection must carry cash_stake: 0 and "
            f"simulated_stake: 0, got cash_stake={result.get('cash_stake')!r}, "
            f"simulated_stake={result.get('simulated_stake')!r}."
        )


def _divergence_score(market_comparison: dict[str, Any]) -> float:
    """The ranking key: the largest absolute difference between the
    model's and the market's implied probability, across all three
    outcomes. Purely a "how much do these two disagree" signal -- it never
    identifies which outcome, side, or direction, and is never used to
    select or name a "best" outcome anywhere in this module's output."""
    return max(abs(outcome["probability_difference"]) for outcome in market_comparison.values())


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


def build_soccer_research_report(envelope: dict[str, Any]) -> dict[str, Any]:
    """Runs the full Bet9ja-export -> ranked-research-report pipeline
    against one already-loaded raw Bet9ja assembled-capture envelope (the
    same shape ``ingest-bet9ja`` consumes).

    Returns a dict with ``report`` (the concise JSON report),
    ``report_markdown`` (the human-readable summary), and
    ``predictions_ledger_rows`` (one row per ranked selection, ready to be
    appended to the JSON Lines ledger -- this function performs no file
    I/O itself, so it can be tested/composed directly). Never raises for a
    well-formed envelope (``ingest_assembled_capture`` may still raise
    ``Bet9jaEnvelopeError`` for a genuinely malformed one, same as
    ``ingest-bet9ja`` itself) -- every per-fixture problem becomes a typed,
    kept-verbatim reason in exactly one bucket, never a dropped fixture and
    never an aborted run.
    """
    ingestion_result = ingest_assembled_capture(envelope)
    batch = ingestion_result["pcbf_research_batch"]
    quarantined = ingestion_result["fixtures_quarantined"]["quarantined"]
    admitted_fixtures = batch["fixtures"]

    source_capture_session_id = batch["source_capture_session_id"]
    source_captured_at_utc = batch["source_captured_at_utc"]
    forecast_cutoff_utc = source_captured_at_utc

    ranked: list[dict[str, Any]] = []
    abstained: list[dict[str, Any]] = []
    post_ingestion_abstained_count = 0
    reason_counts: dict[str, dict[str, int]] = {STAGE_INGESTION: {}, STAGE_PRICING: {}, STAGE_FORECAST: {}}

    def record_abstention(stage: str, reason_code: str, detail: str | None, fixture: dict[str, Any]) -> None:
        nonlocal post_ingestion_abstained_count
        if stage != STAGE_INGESTION:
            post_ingestion_abstained_count += 1
        reason_counts[stage][reason_code] = reason_counts[stage].get(reason_code, 0) + 1
        abstained.append(
            {
                "stage": stage,
                "reason": reason_code,
                "detail": detail,
                "source_fixture_id": fixture.get("source_fixture_id"),
                "source_competition_id": fixture.get("source_competition_id"),
                "sport": fixture.get("sport"),
                "country": fixture.get("country"),
                "competition": fixture.get("competition"),
                "home": fixture.get("home"),
                "away": fixture.get("away"),
                "kickoff_utc": fixture.get("kickoff_utc"),
            }
        )

    for record in quarantined:
        record_abstention(
            STAGE_INGESTION,
            record["reason"],
            record.get("detail"),
            {
                "source_fixture_id": record.get("source_fixture_id"),
                "source_competition_id": record.get("source_competition_id"),
                "sport": (record.get("raw") or {}).get("sport"),
                "country": None,
                "competition": None,
                "home": ((record.get("raw") or {}).get("participants") or {}).get("home"),
                "away": ((record.get("raw") or {}).get("participants") or {}).get("away"),
                "kickoff_utc": None,
            },
        )

    for fixture in admitted_fixtures:
        result = _price_and_forecast(fixture, forecast_cutoff_utc)

        if result["status"] != "OK":
            failure = result["failure"] or {}
            record_abstention(
                STAGE_PRICING,
                failure.get("code") or "REPORT_PRICING_FAILED",
                failure.get("reason") or "Pricing failed for an unspecified reason.",
                fixture,
            )
            continue

        forecast = result["forecast"] or {}
        if not forecast.get("forecast_available"):
            record_abstention(
                STAGE_FORECAST,
                forecast.get("no_forecast_reason") or "FORECAST_UNAVAILABLE",
                None,
                fixture,
            )
            continue

        market_comparison = result.get("market_comparison")
        if not market_comparison:
            # A real forecast without a market_comparison would mean the
            # adapter's outcome vocabulary and the priced market's outcome
            # set did not line up -- should never happen for this
            # category's fixed home/draw/away shape, but if it somehow
            # does this fixture is not silently ranked without one.
            record_abstention(STAGE_FORECAST, "REPORT_MARKET_COMPARISON_UNAVAILABLE", None, fixture)
            continue

        _assert_ranked_selection_stays_research_only(result)

        ranked.append(
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
                "forecast_cutoff_utc": forecast_cutoff_utc,
                "classification_ceiling": result["classification_ceiling"],
                "cash_stake": result["cash_stake"],
                "simulated_stake": result["simulated_stake"],
                "adapter_id": forecast.get("adapter_id"),
                "sport_id": forecast.get("sport_id"),
                "model_version": forecast.get("model_version"),
                "model_artifact_hash": forecast.get("model_artifact_hash"),
                "uncertainty_method": forecast.get("uncertainty_method"),
                "model_probabilities": forecast.get("probabilities"),
                "market_prices": {
                    "home": fixture["market"]["home"],
                    "draw": fixture["market"]["draw"],
                    "away": fixture["market"]["away"],
                },
                "market_comparison": market_comparison,
                "divergence_score": _divergence_score(market_comparison),
                "calculation_hash": result["calculation_hash"],
                "_sort_fixture": fixture,
            }
        )

    ranked.sort(key=lambda item: (-item["divergence_score"], _fixture_sort_key(item["_sort_fixture"])))
    for index, item in enumerate(ranked, start=1):
        item["queue_position"] = index
        del item["_sort_fixture"]

    abstained.sort(key=lambda item: (item["stage"], item["reason"], _fixture_sort_key(item)))

    raw_fixture_count = len(envelope.get("fixtures") or [])
    quarantined_count = len(quarantined)
    admitted_count = len(admitted_fixtures)
    ranked_count = len(ranked)

    if quarantined_count + admitted_count != raw_fixture_count:
        raise AssertionError(
            f"Reconciliation failed: {quarantined_count} quarantined + {admitted_count} admitted "
            f"!= {raw_fixture_count} raw fixtures. This is a bug in ingestion, never a caller "
            "input problem."
        )
    if ranked_count + post_ingestion_abstained_count != admitted_count:
        raise AssertionError(
            f"Reconciliation failed: {ranked_count} ranked + {post_ingestion_abstained_count} "
            f"abstained (post-ingestion) != {admitted_count} admitted fixtures. This is a bug "
            "in this module, never a caller input problem -- every admitted fixture must end "
            "up ranked or abstained."
        )

    flat_reason_counts = {stage: dict(sorted(counts.items())) for stage, counts in reason_counts.items()}

    model_versions = {item["model_version"] for item in ranked}
    model_artifact_hashes = {item["model_artifact_hash"] for item in ranked}
    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "source_capture_session_id": source_capture_session_id,
        "source_captured_at_utc": source_captured_at_utc,
        "forecast_cutoff_utc": forecast_cutoff_utc,
        "note": (
            "Ranks fixtures by model-vs-market probability divergence, for research "
            "attention only. divergence_score is 'how much do the model and the market "
            "disagree', never which outcome to back -- no outcome is ever singled out, "
            "named 'best', or reduced to a stake. classification_ceiling stays "
            "RESEARCH-MODEL with cash_stake/simulated_stake fixed at 0 on every item, "
            "always -- see this module's own module docstring."
        ),
        "counts": {
            "source_fixtures_raw": raw_fixture_count,
            "quarantined": quarantined_count,
            "admitted": admitted_count,
            "ranked_selections": ranked_count,
            "abstained": post_ingestion_abstained_count,
        },
        "reconciles": True,
        "reason_counts": flat_reason_counts,
        "model_version": next(iter(model_versions)) if len(model_versions) == 1 else sorted(model_versions),
        "model_artifact_hash": next(iter(model_artifact_hashes)) if len(model_artifact_hashes) == 1 else sorted(model_artifact_hashes),
        "ranked_selections": ranked,
        "abstained": abstained,
    }

    ledger_rows = []
    for item in ranked:
        ledger_rows.append(
            {
                "schema_version": SCHEMA_VERSION_LEDGER_ROW,
                "source": item["source"],
                "source_capture_session_id": item["source_capture_session_id"],
                "source_fixture_id": item["source_fixture_id"],
                "source_competition_id": item["source_competition_id"],
                "sport": item["sport"],
                "country": item["country"],
                "competition": item["competition"],
                "home": item["home"],
                "away": item["away"],
                "kickoff_utc": item["kickoff_utc"],
                "forecast_cutoff_utc": item["forecast_cutoff_utc"],
                "adapter_id": item["adapter_id"],
                "sport_id": item["sport_id"],
                "model_version": item["model_version"],
                "model_artifact_hash": item["model_artifact_hash"],
                "uncertainty_method": item["uncertainty_method"],
                "model_probabilities": item["model_probabilities"],
                "market_prices": item["market_prices"],
                "market_comparison": item["market_comparison"],
                "divergence_score": item["divergence_score"],
                "classification_ceiling": item["classification_ceiling"],
                "cash_stake": item["cash_stake"],
                "simulated_stake": item["simulated_stake"],
                "calculation_hash": item["calculation_hash"],
                "prediction_recorded_at_utc": source_captured_at_utc,
                "actual_result": None,
                "settled_at_utc": None,
                "evaluation_status": "PENDING_RESULT",
            }
        )

    return {
        "report": report,
        "report_markdown": _render_markdown(report),
        "predictions_ledger_rows": ledger_rows,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Soccer 1X2 Research Report",
        "",
        f"- Capture session: `{report['source_capture_session_id']}`",
        f"- Captured at: `{report['source_captured_at_utc']}`",
        f"- Forecast cutoff used for every fixture: `{report['forecast_cutoff_utc']}`",
        f"- Model version: `{report['model_version']}`",
        "",
        "## Counts",
        "",
        f"- Raw fixtures: {counts['source_fixtures_raw']}",
        f"- Quarantined at ingestion: {counts['quarantined']}",
        f"- Admitted: {counts['admitted']}",
        f"- **Ranked research selections: {counts['ranked_selections']}**",
        f"- Abstained (pricing/forecast): {counts['abstained']}",
        "",
        "_Ranked by model-vs-market divergence, for research attention only -- never a "
        "betting recommendation. No outcome is ever singled out or reduced to a stake._",
        "",
        "## Ranked selections",
        "",
    ]
    if not report["ranked_selections"]:
        lines.append("_None this run._")
    else:
        lines.append("| # | Competition | Fixture | Kickoff (UTC) | Divergence |")
        lines.append("|---|---|---|---|---|")
        for item in report["ranked_selections"]:
            lines.append(
                f"| {item['queue_position']} | {item['competition']} | {item['home']} vs {item['away']} | "
                f"{item['kickoff_utc']} | {item['divergence_score']:.4f} |"
            )
    lines.append("")
    lines.append("## Abstained (by stage / reason)")
    lines.append("")
    any_reasons = any(counts_by_reason for counts_by_reason in report["reason_counts"].values())
    if not any_reasons:
        lines.append("_None this run._")
    else:
        lines.append("| Stage | Reason | Count |")
        lines.append("|---|---|---|")
        for stage, counts_by_reason in report["reason_counts"].items():
            for reason_code, count in counts_by_reason.items():
                lines.append(f"| {stage} | {reason_code} | {count} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_ledger_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def run_report(input_path: Path, output_dir: Path) -> dict[str, Any]:
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    result = build_soccer_research_report(envelope)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "soccer-research-report.json", result["report"])
    (output_dir / "soccer-research-report.md").write_text(result["report_markdown"], encoding="utf-8")
    _append_ledger_rows(output_dir / "soccer-predictions-ledger.jsonl", result["predictions_ledger_rows"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator soccer-research-report",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="Raw exported Bet9ja capture envelope JSON")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into")
    args = parser.parse_args(argv)

    result = run_report(args.input, args.output_dir)
    counts = result["report"]["counts"]
    print(
        f"OK: {counts['ranked_selections']} ranked research selections, {counts['abstained']} abstained "
        f"({counts['quarantined']} quarantined, {counts['admitted']} admitted, "
        f"{counts['source_fixtures_raw']} raw fixtures) -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
