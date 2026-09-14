"""Tests for the one-command Bet9ja forecast research pipeline
(``src/pcbf_calculator/orchestration/bet9ja_research_session.py``).

Style matches this repo's existing convention: plain ``unittest.TestCase``,
synthetic envelope builders mirroring ``tests/test_bet9ja_ingestion.py``
and ``tests/test_soccer_research_report.py``, using real football-data.co.uk
team/competition names so the real, committed adapter artifact actually
produces forecasts.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.orchestration.bet9ja_research_session import (
    main as session_main,
    run_bet9ja_research_session,
    run_session,
)

CAPTURED_AT_UTC = "2026-09-12T15:30:23.000Z"


def make_fixture(
    fixture_id="bxf_1",
    competition_id="2000001",
    home="Arsenal",
    away="Chelsea",
    sport="SOCCER",
    status="PRE_MATCH",
    market_family="1X2",
    odds=None,
    date_heading_raw="Sun 20 Sep",
    kickoff_raw="14:00",
):
    return {
        "fixture_id": fixture_id,
        "sport": sport,
        "status": status,
        "market_family": market_family,
        "offered_odds": odds if odds is not None else {"H": 1.9, "D": 3.4, "A": 4.3},
        "participants": {"home": home, "away": away},
        "resolved_source_competition_id": competition_id,
        "date_heading_raw": date_heading_raw,
        "kickoff_raw": kickoff_raw,
        "duplicate_status": "NEW",
    }


def make_envelope(ledger, fixtures, summary=None, captured_at_utc=CAPTURED_AT_UTC):
    if summary is None:
        statuses = [entry["status"] for entry in ledger]
        summary = {
            "total": len(ledger),
            "completed": statuses.count("COMPLETED"),
            "confirmed_empty": statuses.count("CONFIRMED_EMPTY"),
            "failed": statuses.count("FAILED"),
            "pending": statuses.count("PENDING"),
        }
    return {
        "schema_version": "bet9ja-soccer-session.v1",
        "capture_session_id": "soccer-test-session",
        "capture_scope": "SOCCER_ALL_PREMATCH_COMPETITIONS",
        "captured_at_utc": captured_at_utc,
        "inventory_fingerprint": "invfp_test",
        "summary": summary,
        "competition_ledger": ledger,
        "fixtures": fixtures,
        "unparsed_records": [],
    }


ENGLAND_LEDGER_ENTRY = {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "COMPLETED"}
NIGERIA_LEDGER_ENTRY = {"competition_id": "1209691", "country": "Nigeria", "competition": "Professional Football League", "status": "COMPLETED"}

ARBITRAGE_ODDS = {"H": 1.4, "D": 1.4, "A": 1.4}  # sums well over 1 implied -> ANOMALOUS_NEGATIVE_MARGIN
HIGH_MARGIN_ODDS = {"H": 1.2, "D": 1.2, "A": 1.2}


class RankedMarketTests(unittest.TestCase):
    def test_a_real_fixture_produces_one_ranked_market(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertEqual(report["counts"]["ranked_selections"], 1)
        self.assertEqual(report["counts"]["forecast_abstained"], 0)
        self.assertEqual(report["counts"]["pricing_quality_excluded"], 0)
        market = result["forecast_research_ranked"]["markets"][0]
        self.assertEqual(market["queue_position"], 1)
        self.assertEqual(market["classification_ceiling"], "RESEARCH-MODEL")
        self.assertEqual(market["cash_stake"], 0)
        self.assertEqual(market["simulated_stake"], 0)
        self.assertEqual(market["recommendation_status"], "NOT_AVAILABLE")
        self.assertIsNone(market["operator_decision"])
        self.assertIn("research_priority_score", market)
        self.assertAlmostEqual(sum(market["forecast"]["probabilities"].values()), 1.0, places=6)
        self.assertIn("home", market["market_comparison"])
        self.assertIn("draw", market["market_comparison"])
        self.assertIn("away", market["market_comparison"])
        self.assertIn("model_point_ev", market["market_comparison"]["home"])
        self.assertIn("calculation_hash", market)
        self.assertEqual(market["forecast"]["model_version"], report["model_version"])


class AbstentionBucketTests(unittest.TestCase):
    def test_quarantined_fixture_goes_to_exclusions_never_dropped(self):
        fixture = make_fixture(date_heading_raw="Sat 12 Sep", kickoff_raw="00:00")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertEqual(report["counts"]["quarantined"], 1)
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        excluded = result["ingestion_and_screening_exclusions"]["excluded"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["stage"], "INGESTION")
        self.assertTrue(excluded[0]["reason"])

    def test_unresolved_competition_is_typed_forecast_abstention(self):
        fixture = make_fixture(competition_id="1209691", home="Enyimba", away="Rivers United")
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        self.assertEqual(report["counts"]["forecast_abstained"], 1)
        abstentions = result["forecast_abstentions"]["abstentions"]
        self.assertEqual(abstentions[0]["stage"], "FORECAST")
        self.assertEqual(abstentions[0]["reason"], "FORECAST_COMPETITION_UNRESOLVED")

    def test_unresolved_team_is_typed_forecast_abstention(self):
        fixture = make_fixture(home="Nonexistent FC", away="Chelsea")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        abstentions = result["forecast_abstentions"]["abstentions"]
        self.assertEqual(len(abstentions), 1)
        self.assertEqual(abstentions[0]["reason"], "FORECAST_TEAM_UNRESOLVED")

    def test_stale_artifact_is_typed_forecast_abstention(self):
        # A kickoff far enough past the committed artifact's own freshness
        # window (last_processed_match_date_utc + maximum_artifact_age_days)
        # to abstain FORECAST_ARTIFACT_STALE.
        fixture = make_fixture(date_heading_raw="Sun 14 Feb", kickoff_raw="14:00")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture], captured_at_utc="2027-02-10T00:00:00.000Z")
        result = run_bet9ja_research_session(envelope)
        abstentions = result["forecast_abstentions"]["abstentions"]
        self.assertEqual(len(abstentions), 1)
        self.assertEqual(abstentions[0]["reason"], "FORECAST_ARTIFACT_STALE")

    def test_already_started_fixture_is_quarantined_not_forecast_abstained(self):
        fixture = make_fixture(date_heading_raw="Sat 12 Sep", kickoff_raw="00:00")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        excluded = result["ingestion_and_screening_exclusions"]["excluded"]
        self.assertEqual(excluded[0]["stage"], "INGESTION")
        self.assertEqual(result["research_session_report"]["counts"]["forecast_abstained"], 0)


class PricingQualityGateTests(unittest.TestCase):
    def test_arbitrage_shaped_market_is_pricing_quality_excluded_not_ranked(self):
        fixture = make_fixture(odds=ARBITRAGE_ODDS)
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        self.assertEqual(report["counts"]["pricing_quality_excluded"], 1)
        excluded = result["ingestion_and_screening_exclusions"]["excluded"]
        self.assertEqual(excluded[0]["stage"], "PRICING_QUALITY")
        self.assertEqual(excluded[0]["reason"], "SCREEN_MARKET_QUALITY_NOT_NORMAL")

    def test_normal_market_is_ranked(self):
        fixture = make_fixture()
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        self.assertEqual(result["research_session_report"]["counts"]["ranked_selections"], 1)


class NoOutcomeSelectionTests(unittest.TestCase):
    def test_no_ranked_market_contains_a_selected_outcome_pick_or_recommendation(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        result = run_bet9ja_research_session(envelope)
        rendered = json.dumps(result["forecast_research_ranked"])
        for forbidden in ("recommended_outcome", "best_outcome", '"pick"', "bet_side", "selected_outcome"):
            self.assertNotIn(forbidden, rendered)

    def test_every_ranked_market_retains_all_three_outcomes(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        result = run_bet9ja_research_session(envelope)
        market = result["forecast_research_ranked"]["markets"][0]
        self.assertEqual(set(market["market_comparison"].keys()), {"home", "draw", "away"})
        self.assertEqual(set(market["forecast"]["probabilities"].keys()), {"home_win", "draw", "away_win"})

    def test_no_nonzero_stake_or_classification_above_research_model_anywhere(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        result = run_bet9ja_research_session(envelope)
        for market in result["forecast_research_ranked"]["markets"]:
            self.assertEqual(market["classification_ceiling"], "RESEARCH-MODEL")
            self.assertEqual(market["cash_stake"], 0)
            self.assertEqual(market["simulated_stake"], 0)
            self.assertIsNone(market["operator_decision"])


class ReconciliationTests(unittest.TestCase):
    def test_four_buckets_reconcile_against_raw_fixtures(self):
        fixtures = [
            make_fixture(fixture_id="bxf_1"),  # ranked
            make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),  # forecast abstention
            make_fixture(fixture_id="bxf_3", odds=ARBITRAGE_ODDS),  # pricing-quality excluded
            make_fixture(fixture_id="bxf_4", date_heading_raw="Sat 12 Sep", kickoff_raw="00:00"),  # ingestion quarantine
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertTrue(report["reconciles"])
        counts = report["counts"]
        total = counts["quarantined"] + counts["pricing_quality_excluded"] + counts["forecast_abstained"] + counts["ranked_selections"]
        self.assertEqual(total, counts["source_fixtures_raw"])
        self.assertEqual(counts["source_fixtures_raw"], 4)

    def test_empty_envelope_reconciles_cleanly(self):
        envelope = make_envelope([], [])
        result = run_bet9ja_research_session(envelope)
        report = result["research_session_report"]
        self.assertTrue(report["reconciles"])
        self.assertEqual(report["counts"]["source_fixtures_raw"], 0)
        self.assertEqual(result["forecast_research_ranked"]["markets"], [])


class IdempotencyTests(unittest.TestCase):
    def test_repeated_run_is_byte_identical(self):
        fixtures = [
            make_fixture(fixture_id="bxf_1"),
            make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),
            make_fixture(fixture_id="bxf_3", odds=ARBITRAGE_ODDS),
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        result_a = run_bet9ja_research_session(copy.deepcopy(envelope))
        result_b = run_bet9ja_research_session(copy.deepcopy(envelope))
        for key in ("research_session_report", "forecast_research_ranked", "forecast_abstentions", "ingestion_and_screening_exclusions"):
            self.assertEqual(
                json.dumps(result_a[key], sort_keys=True),
                json.dumps(result_b[key], sort_keys=True),
                f"mismatch in {key}",
            )


class ExistingCommandsUnaffectedTests(unittest.TestCase):
    def test_screen_research_batch_contract_is_unchanged(self):
        from pcbf_calculator.screening.research_batch import screen_research_batch

        batch = {
            "schema_version": "pcbf-research-batch.v1",
            "source": "BET9JA",
            "source_capture_session_id": "s1",
            "source_captured_at_utc": CAPTURED_AT_UTC,
            "classification_ceiling": "RESEARCH-MODEL",
            "fixtures": [
                {
                    "source": "BET9JA",
                    "source_capture_session_id": "s1",
                    "source_fixture_id": "bxf_1",
                    "source_competition_id": "2000001",
                    "sport": "SOCCER",
                    "country": "England",
                    "competition": "Premier League",
                    "home": "Arsenal",
                    "away": "Chelsea",
                    "kickoff_utc": "2026-09-20T14:00:00Z",
                    "classification_ceiling": "RESEARCH-MODEL",
                    "market": {"family": "1X2", "home": 1.9, "draw": 3.4, "away": 4.3},
                }
            ],
        }
        result = screen_research_batch(batch)
        market = result["research_queue"]["markets"][0]
        # screen-research-batch's own contract: it never passes the
        # fixture block the adapter needs, so its own forecast field
        # always reports unavailable, and its own status fields stay
        # hard-labeled NOT_COMPUTED/NOT_AVAILABLE -- this orchestration
        # module must never have silently redefined that.
        self.assertEqual(market["forecast_probability_status"], "NOT_COMPUTED")
        self.assertFalse(market["forecast"]["forecast_available"])
        self.assertEqual(market["forecast"]["no_forecast_reason"], "FORECAST_INPUT_INCOMPLETE")


class CliIntegrationTests(unittest.TestCase):
    def test_cli_writes_four_files(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            exit_code = session_main([str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "research-session-report.json").exists())
            self.assertTrue((output_dir / "forecast-research-ranked.json").exists())
            self.assertTrue((output_dir / "forecast-abstentions.json").exists())
            self.assertTrue((output_dir / "ingestion-and-screening-exclusions.json").exists())

            report = json.loads((output_dir / "research-session-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["ranked_selections"], 1)

    def test_pcbf_calculator_main_dispatches_run_bet9ja_research_subcommand(self):
        from pcbf_calculator.cli import main as cli_main

        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            exit_code = cli_main(["run-bet9ja-research", str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "research-session-report.json").exists())

    def test_host_contract_invocation_is_unaffected(self):
        from pcbf_calculator.cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "request.json"
            input_path.write_text(
                json.dumps({"event_id": "e1", "category": "tennis", "market_prices": {"a": 1.8, "b": 2.05}}),
                encoding="utf-8",
            )
            output_path = tmp_path / "result.json"
            exit_code = cli_main([str(input_path), str(output_path)])
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())

    def test_ingest_bet9ja_and_screen_research_batch_subcommands_still_dispatch(self):
        from pcbf_calculator.cli import main as cli_main

        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            ingest_out = tmp_path / "ingest-out"

            exit_code = cli_main(["ingest-bet9ja", str(input_path), "--output-dir", str(ingest_out)])
            self.assertEqual(exit_code, 0)
            batch_path = ingest_out / "pcbf-research-batch.json"
            self.assertTrue(batch_path.exists())

            screen_out = tmp_path / "screen-out"
            exit_code = cli_main(["screen-research-batch", str(batch_path), "--output-dir", str(screen_out)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((screen_out / "research-queue-ranked.json").exists())


if __name__ == "__main__":
    unittest.main()
