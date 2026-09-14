"""Tests for the Bet9ja capture -> Soccer 1X2 adapter -> ranked research
report pipeline (``src/pcbf_calculator/reporting/soccer_research_report.py``).

Style matches this repo's existing convention: plain ``unittest.TestCase``,
synthetic envelope builders mirroring ``tests/test_bet9ja_ingestion.py``'s
own real-shaped fixtures, using real football-data.co.uk team/competition
names so the real, committed adapter artifact actually produces forecasts.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.reporting.soccer_research_report import (
    build_soccer_research_report,
    main as report_main,
    run_report,
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


def real_forecastable_fixture(**overrides):
    return make_fixture(**overrides)


class RankedSelectionTests(unittest.TestCase):
    def test_a_real_fixture_produces_one_ranked_selection(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertEqual(report["counts"]["ranked_selections"], 1)
        self.assertEqual(report["counts"]["abstained"], 0)
        selection = report["ranked_selections"][0]
        self.assertEqual(selection["queue_position"], 1)
        self.assertEqual(selection["classification_ceiling"], "RESEARCH-MODEL")
        self.assertEqual(selection["cash_stake"], 0)
        self.assertEqual(selection["simulated_stake"], 0)
        self.assertAlmostEqual(sum(selection["model_probabilities"].values()), 1.0, places=6)
        self.assertIn("home", selection["market_comparison"])
        self.assertIn("draw", selection["market_comparison"])
        self.assertIn("away", selection["market_comparison"])
        self.assertGreaterEqual(selection["divergence_score"], 0.0)

    def test_one_ranked_selection_produces_one_ledger_row(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        result = build_soccer_research_report(envelope)
        self.assertEqual(len(result["predictions_ledger_rows"]), 1)
        row = result["predictions_ledger_rows"][0]
        self.assertEqual(row["actual_result"], None)
        self.assertEqual(row["settled_at_utc"], None)
        self.assertEqual(row["evaluation_status"], "PENDING_RESULT")
        self.assertEqual(row["classification_ceiling"], "RESEARCH-MODEL")
        self.assertEqual(row["cash_stake"], 0)
        self.assertEqual(row["simulated_stake"], 0)
        self.assertEqual(row["prediction_recorded_at_utc"], CAPTURED_AT_UTC)


class AbstentionPreservationTests(unittest.TestCase):
    def test_quarantined_fixture_is_typed_and_never_dropped(self):
        # An already-started fixture (kickoff before capture time) is
        # quarantined at ingestion with a real BET9JA_* code.
        fixture = real_forecastable_fixture(date_heading_raw="Sat 12 Sep", kickoff_raw="00:00")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertEqual(report["counts"]["quarantined"], 1)
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        self.assertEqual(len(report["abstained"]), 1)
        self.assertEqual(report["abstained"][0]["stage"], "INGESTION")
        self.assertTrue(report["abstained"][0]["reason"])

    def test_unresolved_competition_is_typed_forecast_abstention(self):
        # Nigeria's Professional Football League is not one of the 5
        # leagues this artifact covers -- real FORECAST_COMPETITION_UNRESOLVED.
        fixture = real_forecastable_fixture(
            competition_id="1209691", home="Enyimba", away="Rivers United"
        )
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [fixture])
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertEqual(report["counts"]["admitted"], 1)
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        self.assertEqual(report["counts"]["abstained"], 1)
        self.assertEqual(report["abstained"][0]["stage"], "FORECAST")
        self.assertEqual(report["abstained"][0]["reason"], "FORECAST_COMPETITION_UNRESOLVED")

    def test_unresolved_team_is_typed_forecast_abstention(self):
        fixture = real_forecastable_fixture(home="Nonexistent FC", away="Chelsea")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertEqual(report["counts"]["ranked_selections"], 0)
        self.assertEqual(report["abstained"][0]["stage"], "FORECAST")
        self.assertEqual(report["abstained"][0]["reason"], "FORECAST_TEAM_UNRESOLVED")


class NoRecommendationLanguageTests(unittest.TestCase):
    def test_no_ranked_selection_contains_a_selected_outcome_or_stake(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        result = build_soccer_research_report(envelope)
        rendered = json.dumps(result["report"])
        for forbidden in ("recommended_outcome", "best_outcome", "pick", "bet_side"):
            self.assertNotIn(forbidden, rendered)

    def test_report_note_states_research_only(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        result = build_soccer_research_report(envelope)
        self.assertIn("never a betting recommendation".split()[0], result["report_markdown"])
        self.assertIn("research attention", result["report"]["note"])


class ReconciliationTests(unittest.TestCase):
    def test_ranked_plus_abstained_plus_quarantined_equals_raw_fixtures(self):
        fixtures = [
            real_forecastable_fixture(fixture_id="bxf_1"),  # ranked
            real_forecastable_fixture(fixture_id="bxf_2", home="Nonexistent FC"),  # forecast abstention
            real_forecastable_fixture(fixture_id="bxf_3", date_heading_raw="Sat 12 Sep", kickoff_raw="00:00"),  # quarantined
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertTrue(report["reconciles"])
        total = report["counts"]["quarantined"] + report["counts"]["ranked_selections"] + report["counts"]["abstained"]
        self.assertEqual(total, report["counts"]["source_fixtures_raw"])

    def test_empty_envelope_reconciles_cleanly(self):
        envelope = make_envelope([], [])
        result = build_soccer_research_report(envelope)
        report = result["report"]
        self.assertTrue(report["reconciles"])
        self.assertEqual(report["counts"]["source_fixtures_raw"], 0)
        self.assertEqual(result["predictions_ledger_rows"], [])


class RankingOrderTests(unittest.TestCase):
    def test_higher_divergence_ranks_first(self):
        # Two real, resolvable fixtures with different offered odds -> different divergence.
        fixtures = [
            real_forecastable_fixture(fixture_id="bxf_close", odds={"H": 2.6, "D": 3.3, "A": 2.7}),
            real_forecastable_fixture(fixture_id="bxf_wide", odds={"H": 1.2, "D": 8.0, "A": 15.0}),
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        result = build_soccer_research_report(envelope)
        selections = result["report"]["ranked_selections"]
        self.assertEqual(len(selections), 2)
        self.assertGreaterEqual(selections[0]["divergence_score"], selections[1]["divergence_score"])
        self.assertEqual(selections[0]["queue_position"], 1)
        self.assertEqual(selections[1]["queue_position"], 2)


class IdempotencyTests(unittest.TestCase):
    def test_repeated_run_is_byte_identical(self):
        fixtures = [
            real_forecastable_fixture(fixture_id="bxf_1"),
            real_forecastable_fixture(fixture_id="bxf_2", home="Nonexistent FC"),
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        result_a = build_soccer_research_report(copy.deepcopy(envelope))
        result_b = build_soccer_research_report(copy.deepcopy(envelope))
        self.assertEqual(
            json.dumps(result_a["report"], sort_keys=True),
            json.dumps(result_b["report"], sort_keys=True),
        )
        self.assertEqual(result_a["report_markdown"], result_b["report_markdown"])
        self.assertEqual(result_a["predictions_ledger_rows"], result_b["predictions_ledger_rows"])


class CliIntegrationTests(unittest.TestCase):
    def test_cli_writes_three_files_and_appends_ledger(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            exit_code = report_main([str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "soccer-research-report.json").exists())
            self.assertTrue((output_dir / "soccer-research-report.md").exists())
            self.assertTrue((output_dir / "soccer-predictions-ledger.jsonl").exists())

            report = json.loads((output_dir / "soccer-research-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["ranked_selections"], 1)

            ledger_lines = (output_dir / "soccer-predictions-ledger.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_lines), 1)
            json.loads(ledger_lines[0])  # each line is valid standalone JSON

    def test_ledger_appends_rather_than_overwrites_across_runs(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            run_report(input_path, output_dir)
            run_report(input_path, output_dir)

            ledger_lines = (output_dir / "soccer-predictions-ledger.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(ledger_lines), 2)

    def test_pcbf_calculator_main_dispatches_soccer_research_report_subcommand(self):
        from pcbf_calculator.cli import main as cli_main

        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [real_forecastable_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            exit_code = cli_main(["soccer-research-report", str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "soccer-research-report.json").exists())

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


if __name__ == "__main__":
    unittest.main()
