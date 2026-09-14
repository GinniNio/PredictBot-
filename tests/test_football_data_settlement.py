"""Tests for football-data.co.uk forecast-ledger settlement
(``src/pcbf_calculator/orchestration/football_data_settlement.py``).

Style matches this repo's existing convention: plain ``unittest.TestCase``.
Reuses this repo's own committed ``tests/fixtures/football_data/*.csv``
files (already used by ``data_pipeline``'s own tests) so CSV-shape
coverage stays in sync with that real evidence, and the real, committed
``soccer_1x2_elo_v1`` artifact/team-alias data for identity resolution --
never a second, synthetic notion of "known teams".
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "football_data"

from ledgers import forecast_ledger  # noqa: E402
from ledgers.storage import read_all  # noqa: E402

from pcbf_calculator.adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook  # noqa: E402
from pcbf_calculator.orchestration import football_data_settlement as fds  # noqa: E402
from pcbf_calculator.orchestration.bet9ja_research_session import run_session  # noqa: E402

KNOWN_TEAMS = load_known_teams_by_league()
ALIAS_BOOK = load_team_alias_book()


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_settle_1",
    competition_code: str = "E0",
    resolved_home_team: str = "Arsenal",
    resolved_away_team: str = "Chelsea",
    scheduled_date: str = "2026-09-20",
    model_probabilities: dict[str, float] | None = None,
    stop_reason: str | None = None,
):
    """Builds and appends one RECORDED event directly (never through the
    full research pipeline) -- exercises this module's matching/scoring
    logic in isolation from ingestion/pricing/forecasting."""

    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z",
        market_type=fds.MARKET_TYPE,
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities=model_probabilities,
        model_version="soccer_1x2_elo_v1-test" if model_probabilities else None,
        artifact_hash="sha256:test" if model_probabilities else None,
        output_hash="sha256:test-output" if model_probabilities else None,
        selection_status="considered",
        stop_reason=stop_reason,
        competition_code=competition_code,
        resolved_home_team=resolved_home_team,
        resolved_away_team=resolved_away_team,
        scheduled_date=scheduled_date,
    )
    forecast_id = event["forecast_id"]
    result = forecast_ledger.append_recorded(ledger_path, event)
    assert result.status == "APPENDED", result
    return forecast_id


class SelectClosingOddsTests(unittest.TestCase):
    def test_pinnacle_closing_preferred_over_bet365_closing(self):
        from data_pipeline.schema_inspection import inspect_header

        header = ["Div", "Date", "HomeTeam", "AwayTeam", "FTR", "B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA"]
        shape = inspect_header(header)
        row = {"B365CH": "1.9", "B365CD": "3.4", "B365CA": "4.3", "PSCH": "1.85", "PSCD": "3.5", "PSCA": "4.4"}
        odds, source = fds.select_closing_odds(row, shape["odds_columns_found"], shape["market_aggregate_columns_found"])
        self.assertEqual(odds, {"H": 1.85, "D": 3.5, "A": 4.4})
        self.assertIn("PSCH/PSCD/PSCA", source)
        self.assertIn("Pinnacle", source)

    def test_falls_back_to_next_bookmaker_when_preferred_one_incomplete_for_this_row(self):
        from data_pipeline.schema_inspection import inspect_header

        header = ["Div", "Date", "HomeTeam", "AwayTeam", "FTR", "B365CH", "B365CD", "B365CA", "PSCH", "PSCD", "PSCA"]
        shape = inspect_header(header)
        # PSC* declared in the header but this specific row is missing PSCA.
        row = {"B365CH": "1.9", "B365CD": "3.4", "B365CA": "4.3", "PSCH": "1.85", "PSCD": "3.5", "PSCA": ""}
        odds, source = fds.select_closing_odds(row, shape["odds_columns_found"], shape["market_aggregate_columns_found"])
        self.assertEqual(odds, {"H": 1.9, "D": 3.4, "A": 4.3})
        self.assertIn("B365CH/B365CD/B365CA", source)

    def test_falls_back_to_market_aggregate_closing_when_no_bookmaker_closing_present(self):
        from data_pipeline.schema_inspection import inspect_header

        header = ["Div", "Date", "HomeTeam", "AwayTeam", "FTR", "AvgCH", "AvgCD", "AvgCA"]
        shape = inspect_header(header)
        row = {"AvgCH": "1.9", "AvgCD": "3.4", "AvgCA": "4.3"}
        odds, source = fds.select_closing_odds(row, shape["odds_columns_found"], shape["market_aggregate_columns_found"])
        self.assertEqual(odds, {"H": 1.9, "D": 3.4, "A": 4.3})
        self.assertIn("market average", source)

    def test_never_substitutes_opening_odds_for_missing_closing(self):
        from data_pipeline.schema_inspection import inspect_header

        header = ["Div", "Date", "HomeTeam", "AwayTeam", "FTR", "B365H", "B365D", "B365A"]  # opening only, no "C" variant
        shape = inspect_header(header)
        row = {"B365H": "1.9", "B365D": "3.4", "B365A": "4.3"}
        odds, source = fds.select_closing_odds(row, shape["odds_columns_found"], shape["market_aggregate_columns_found"])
        self.assertIsNone(odds)
        self.assertIsNone(source)


class BuildSettlementBatchTests(unittest.TestCase):
    def test_clean_row_normalizes_with_pinnacle_closing_odds(self):
        normalized, rejected, counts = fds.build_settlement_batch(
            [FIXTURES_DIR / "clean_modern_season.csv"], KNOWN_TEAMS, ALIAS_BOOK
        )
        self.assertEqual(rejected, [])
        self.assertEqual(counts["source_rows_total"], 5)
        self.assertEqual(len(normalized), 5)
        first = next(r for r in normalized if r["resolved_home_team"] == "Arsenal")
        self.assertEqual(first["competition_code"], "E0")
        self.assertEqual(first["resolved_away_team"], "Nott'm Forest")
        self.assertEqual(first["scheduled_date"], "2023-08-12")
        self.assertEqual(first["actual_result"], "H")
        self.assertEqual(first["closing_odds"], {"H": 1.25, "D": 5.80, "A": 10.00})
        self.assertIn("Pinnacle", first["closing_odds_source"])

    def test_row_with_no_closing_columns_at_all_is_still_scorable_with_null_closing_odds(self):
        normalized, rejected, _counts = fds.build_settlement_batch(
            [FIXTURES_DIR / "missing_odds.csv"], KNOWN_TEAMS, ALIAS_BOOK
        )
        self.assertEqual(rejected, [])
        for row in normalized:
            self.assertIsNone(row["closing_odds"])
            self.assertIsNone(row["closing_odds_source"])

    def test_competition_not_covered_is_rejected_with_typed_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uncovered.csv"
            path.write_text("Div,Date,HomeTeam,AwayTeam,FTR\nSC0,12/08/2023,Celtic,Rangers,H\n", encoding="utf-8")
            normalized, rejected, _counts = fds.build_settlement_batch([path], KNOWN_TEAMS, ALIAS_BOOK)
            self.assertEqual(normalized, [])
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0]["reason"], fds.REASON_COMPETITION_NOT_COVERED)

    def test_unresolved_team_is_rejected_with_typed_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown_team.csv"
            path.write_text("Div,Date,HomeTeam,AwayTeam,FTR\nE0,12/08/2023,Not A Real Team FC,Arsenal,H\n", encoding="utf-8")
            normalized, rejected, _counts = fds.build_settlement_batch([path], KNOWN_TEAMS, ALIAS_BOOK)
            self.assertEqual(normalized, [])
            self.assertEqual(rejected[0]["reason"], fds.REASON_HOME_TEAM_UNRESOLVED)

    def test_missing_result_and_invalid_result_label_are_rejected_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.csv"
            path.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\n"
                "E0,12/08/2023,Arsenal,Everton,\n"
                "E0,13/08/2023,Fulham,Everton,X\n",
                encoding="utf-8",
            )
            normalized, rejected, _counts = fds.build_settlement_batch([path], KNOWN_TEAMS, ALIAS_BOOK)
            self.assertEqual(normalized, [])
            reasons = {r["reason"] for r in rejected}
            self.assertEqual(reasons, {fds.REASON_MISSING_RESULT, fds.REASON_INVALID_RESULT_LABEL})

    def test_exact_duplicate_row_across_two_files_collapses_silently_and_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content = "Div,Date,HomeTeam,AwayTeam,FTR\nE0,12/08/2023,Arsenal,Everton,H\n"
            (tmp_path / "season_a.csv").write_text(content, encoding="utf-8")
            (tmp_path / "season_b.csv").write_text(content, encoding="utf-8")
            normalized, rejected, counts = fds.build_settlement_batch(
                [tmp_path / "season_a.csv", tmp_path / "season_b.csv"], KNOWN_TEAMS, ALIAS_BOOK
            )
            self.assertEqual(len(normalized), 1)
            self.assertEqual(rejected, [])
            self.assertEqual(counts["duplicate_source_rows_collapsed"], 1)
            self.assertEqual(counts["source_rows_total"], 2)

    def test_conflicting_result_across_two_files_for_the_same_fixture_is_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "season_a.csv").write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\nE0,12/08/2023,Arsenal,Everton,H\n", encoding="utf-8"
            )
            (tmp_path / "season_b.csv").write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\nE0,12/08/2023,Arsenal,Everton,A\n", encoding="utf-8"
            )
            normalized, rejected, _counts = fds.build_settlement_batch(
                [tmp_path / "season_a.csv", tmp_path / "season_b.csv"], KNOWN_TEAMS, ALIAS_BOOK
            )
            self.assertEqual(normalized, [])
            self.assertEqual(len(rejected), 2)
            self.assertTrue(all(r["reason"] == fds.REASON_CONFLICTING_SOURCE_ROW for r in rejected))

    def test_row_accounting_invariant_holds_across_a_mixed_file(self):
        normalized, rejected, counts = fds.build_settlement_batch(
            [FIXTURES_DIR / "incomplete_three_way.csv"], KNOWN_TEAMS, ALIAS_BOOK
        )
        self.assertEqual(
            counts["source_rows_total"],
            len(normalized) + len(rejected) + counts["duplicate_source_rows_collapsed"],
        )


class PlanSettlementTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "competition_code": "E0",
            "resolved_home_team": "Arsenal",
            "resolved_away_team": "Chelsea",
            "scheduled_date": "2026-09-20",
            "actual_result": "H",
            "closing_odds": {"H": 1.9, "D": 3.4, "A": 4.3},
            "closing_odds_source": "PSCH/PSCD/PSCA (Pinnacle closing)",
            "source_file": "results.csv",
            "source_row_index": 0,
            "row_index": 0,
        }
        row.update(overrides)
        return row

    def test_matching_forecast_with_probabilities_is_planned_to_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path, model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15})
            plan = fds.plan_settlement(ledger_path, [self._row()])
            self.assertEqual(len(plan["to_score"]), 1)
            self.assertTrue(plan["to_score"][0]["forecast_id"].startswith("fc_"))
            self.assertEqual(plan["unmatched"], [])
            self.assertEqual(plan["conflicts"], [])

    def test_no_matching_forecast_is_reported_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            plan = fds.plan_settlement(ledger_path, [self._row()])
            self.assertEqual(len(plan["unmatched"]), 1)
            self.assertEqual(plan["unmatched"][0]["reason"], fds.REASON_NO_MATCHING_FORECAST)

    def test_stop_rejected_forecast_with_null_probabilities_is_not_scorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path, model_probabilities=None, stop_reason="FORECAST_TEAM_NOT_IN_ARTIFACT")
            plan = fds.plan_settlement(ledger_path, [self._row()])
            self.assertEqual(plan["to_score"], [])
            self.assertEqual(len(plan["not_scorable"]), 1)
            self.assertEqual(plan["not_scorable"][0]["reason"], fds.REASON_NO_SCORABLE_PROBABILITIES)

    def test_forecast_without_a_full_settlement_identity_is_never_matched(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            # competition_code/resolved_home_team/etc. all default to None
            # when not supplied -- an adapter that never resolved/reported
            # identity must never be guessed into a match.
            event = forecast_ledger.build_recorded_event(
                fixture_id="bxf_no_identity",
                sport="SOCCER",
                league="Premier League",
                kickoff_utc="2026-09-20T14:00:00Z",
                market_type=fds.MARKET_TYPE,
                offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
                classification="RESEARCH-MODEL",
                model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15},
                model_version="v",
                artifact_hash="sha256:test",
                output_hash="sha256:test-output",
            )
            forecast_ledger.append_recorded(ledger_path, event)
            plan = fds.plan_settlement(ledger_path, [self._row()])
            self.assertEqual(plan["to_score"], [])
            self.assertEqual(len(plan["unmatched"]), 1)


class IngestFootballDataResultsTests(unittest.TestCase):
    def test_first_import_scores_then_identical_rerun_is_duplicate_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ledger_dir = tmp_path / "ledger_data"
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME, model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15})
            results_csv = tmp_path / "results.csv"
            results_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR,PSCH,PSCD,PSCA\nE0,20/09/2026,Arsenal,Chelsea,H,1.88,3.55,4.15\n",
                encoding="utf-8",
            )

            first = fds.ingest_football_data_results([results_csv], ledger_dir)
            self.assertEqual(first["settlement_report"]["status"], "OK")
            self.assertEqual(len(first["settled_forecasts"]["settled"]), 1)

            second = fds.ingest_football_data_results([results_csv], ledger_dir)
            self.assertEqual(second["settlement_report"]["status"], "OK")
            self.assertEqual(second["settlement_report"]["counts"]["forecast_matches_scored"], 0)
            self.assertEqual(second["settlement_report"]["counts"]["forecast_matches_duplicate_skipped"], 1)

    def test_conflicting_result_aborts_the_whole_batch_with_zero_ledger_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ledger_dir = tmp_path / "ledger_data"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path, model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15})

            first_csv = tmp_path / "first.csv"
            first_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\nE0,20/09/2026,Arsenal,Chelsea,H\n", encoding="utf-8"
            )
            fds.ingest_football_data_results([first_csv], ledger_dir)
            records_before = read_all(ledger_path)

            conflicting_csv = tmp_path / "conflicting.csv"
            conflicting_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\nE0,20/09/2026,Arsenal,Chelsea,A\n", encoding="utf-8"
            )
            result = fds.ingest_football_data_results([conflicting_csv], ledger_dir)

            self.assertEqual(result["settlement_report"]["status"], "CONFLICT")
            self.assertEqual(len(result["settlement_report"]["source_files"]), 1)
            self.assertEqual(result["settled_forecasts"]["settled"], [])
            self.assertEqual(len(result["settlement_conflicts"]["conflicts"]), 1)
            self.assertEqual(read_all(ledger_path), records_before)

    def test_never_creates_or_touches_a_betting_ledger_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ledger_dir = tmp_path / "ledger_data"
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME, model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15})
            results_csv = tmp_path / "results.csv"
            results_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR\nE0,20/09/2026,Arsenal,Chelsea,H\n", encoding="utf-8"
            )
            fds.ingest_football_data_results([results_csv], ledger_dir)
            self.assertEqual(sorted(p.name for p in ledger_dir.iterdir()), ["forecast-ledger.jsonl", "forecast-ledger.jsonl.lock"])


class DiscoverInputFilesTests(unittest.TestCase):
    def test_directory_discovers_every_csv_sorted_never_recursing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "b.csv").write_text("Div,Date,HomeTeam,AwayTeam,FTR\n", encoding="utf-8")
            (tmp_path / "a.csv").write_text("Div,Date,HomeTeam,AwayTeam,FTR\n", encoding="utf-8")
            (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
            nested = tmp_path / "nested"
            nested.mkdir()
            (nested / "c.csv").write_text("Div,Date,HomeTeam,AwayTeam,FTR\n", encoding="utf-8")

            found = fds.discover_input_files(tmp_path)
            self.assertEqual([p.name for p in found], ["a.csv", "b.csv"])

    def test_single_file_returns_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.csv"
            path.write_text("Div,Date,HomeTeam,AwayTeam,FTR\n", encoding="utf-8")
            self.assertEqual(fds.discover_input_files(path), [path])


class EndToEndRealPipelineTests(unittest.TestCase):
    """Closes the full loop this module exists for: capture -> forecast ->
    record -> settle -> score, using the real research-session pipeline
    (never a synthetic ledger row) for the forecast side."""

    def test_full_capture_to_forecast_to_record_to_settle_to_score_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            envelope = {
                "schema_version": "bet9ja-soccer-session.v1",
                "capture_session_id": "settlement-e2e-test",
                "capture_scope": "SOCCER_ALL_PREMATCH_COMPETITIONS",
                "captured_at_utc": "2026-09-12T15:30:23.000Z",
                "inventory_fingerprint": "invfp_e2e",
                "summary": {"total": 1, "completed": 1, "confirmed_empty": 0, "failed": 0, "pending": 0},
                "competition_ledger": [
                    {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "COMPLETED"}
                ],
                "fixtures": [
                    {
                        "fixture_id": "bxf_e2e_1",
                        "sport": "SOCCER",
                        "status": "PRE_MATCH",
                        "market_family": "1X2",
                        "offered_odds": {"H": 1.9, "D": 3.4, "A": 4.3},
                        "participants": {"home": "Arsenal", "away": "Chelsea"},
                        "resolved_source_competition_id": "2000001",
                        "date_heading_raw": "Sun 20 Sep",
                        "kickoff_raw": "14:00",
                        "kickoff_utc": "2026-09-20T14:00:00Z",
                        "duplicate_status": "NEW",
                    }
                ],
                "unparsed_records": [],
            }
            capture_path = tmp_path / "capture.json"
            capture_path.write_text(json.dumps(envelope), encoding="utf-8")

            ledger_dir = tmp_path / "ledger_data"
            research_result = run_session(capture_path, tmp_path / "research_out", ledger_dir=ledger_dir)
            self.assertEqual(research_result["research_session_report"]["counts"]["ranked_selections"], 1)

            results_csv = tmp_path / "results.csv"
            results_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR,PSCH,PSCD,PSCA\nE0,20/09/2026,Arsenal,Chelsea,H,1.88,3.55,4.15\n",
                encoding="utf-8",
            )

            settlement_out = tmp_path / "settlement_out"
            result = fds.run_settlement_session(results_csv, ledger_dir, settlement_out)

            self.assertEqual(result["settlement_report"]["status"], "OK")
            self.assertEqual(len(result["settled_forecasts"]["settled"]), 1)
            settled = result["settled_forecasts"]["settled"][0]
            self.assertEqual(settled["closing_odds"], {"H": 1.88, "D": 3.55, "A": 4.15})
            self.assertIn("Pinnacle", settled["closing_odds_source"])

            records = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            scored = [r for r in records if r["event_type"] == "SCORED"]
            self.assertEqual(len(scored), 1)
            self.assertEqual(scored[0]["payload"]["actual_result"], "H")

            for path in ("settlement-report.json", "settled-forecasts.json", "unmatched-results.json", "settlement-conflicts.json"):
                self.assertTrue((settlement_out / path).exists())


if __name__ == "__main__":
    unittest.main()
