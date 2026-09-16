"""Tests for Bet9ja Results-page forecast-ledger settlement
(``src/pcbf_calculator/orchestration/bet9ja_results_settlement.py``).

Style matches ``tests/test_football_data_settlement.py`` -- plain
``unittest.TestCase``, the real, committed ``soccer_1x2_elo_v1``
artifact/team-alias data for identity resolution (never a second,
synthetic notion of "known teams"), and real team names confirmed present
in that artifact for Italy Serie A (Como, Parma, Torino, Roma, Inter,
Udinese -- the same three real fixtures this codebase's own 2026-09-14
settlement session actually scored via football-data.co.uk)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger  # noqa: E402
from ledgers.storage import read_all  # noqa: E402

from pcbf_calculator.orchestration import bet9ja_results_settlement as brs  # noqa: E402
from pcbf_calculator.orchestration import football_data_settlement as fds  # noqa: E402


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_results_settle_1",
    competition_code: str = "I1",
    resolved_home_team: str = "Como",
    resolved_away_team: str = "Parma",
    scheduled_date: str = "2026-09-14",
    model_probabilities: dict[str, float] | None = None,
    stop_reason: str | None = None,
):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Italy Serie A",
        kickoff_utc=f"{scheduled_date}T16:30:00Z",
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


def _results_envelope(results):
    return {
        "schema_version": "bet9ja-soccer-results.v1",
        "capture_id": "cap_test",
        "captured_at_utc": "2026-09-16T05:46:00.000Z",
        "source_url": "https://web.bet9ja.com/sport/results.aspx",
        "page_title": "Bet9ja Results",
        "parser_version": "bet9ja-results-parser@0.1.0-serie-a-confirmed",
        "page_timezone": "GMT+01:00",
        "date_range": None,
        "capture_status": "CAPTURE_OK",
        "capture_status_reasons": [],
        "competitions_seen": ["Italy Serie A"],
        "coverage": {
            "groups_seen": 1,
            "results_seen": len(results),
            "results_parsed": len(results),
            "results_unresolved": 0,
        },
        "results": results,
        "unresolved_results": [],
    }


def _result_row(**overrides):
    row = {
        "row_index": 0,
        "competition_raw": "Italy Serie A",
        "bet9ja_result_id": "1925",
        "start_raw": "14/09/2026 17:30",
        "fixture_raw": "Como - Parma",
        "full_time_score_raw": "2 - 1",
        "half_time_score_raw": "1 - 0",
    }
    row.update(overrides)
    return row


def _write_envelope(tmp_dir: Path, filename: str, results) -> Path:
    path = Path(tmp_dir) / filename
    path.write_text(json.dumps(_results_envelope(results)), encoding="utf-8")
    return path


class TimeAndScoreParsingTests(unittest.TestCase):
    def test_gmt_plus_one_start_converts_to_the_correct_utc_calendar_date(self):
        # 17:30 GMT+01:00 on 14 Sep -> 16:30 UTC, same calendar date --
        # matches this codebase's own real 2026-09-14 ledger data.
        self.assertEqual(brs._parse_start_to_utc_date("14/09/2026 17:30"), "2026-09-14")

    def test_a_start_time_near_midnight_can_cross_the_utc_calendar_date(self):
        # 00:15 GMT+01:00 -> 23:15 UTC the PREVIOUS day.
        self.assertEqual(brs._parse_start_to_utc_date("15/09/2026 00:15"), "2026-09-14")

    def test_unparseable_start_time_is_none_never_guessed(self):
        self.assertIsNone(brs._parse_start_to_utc_date("not a date"))
        self.assertIsNone(brs._parse_start_to_utc_date(None))

    def test_actual_result_from_score(self):
        self.assertEqual(brs._actual_result_from_score(2, 1), "H")
        self.assertEqual(brs._actual_result_from_score(0, 2), "A")
        self.assertEqual(brs._actual_result_from_score(1, 1), "D")


class BuildBatchTests(unittest.TestCase):
    def setUp(self):
        from pcbf_calculator.adapters.soccer_1x2_elo_v1.adapter import (
            load_known_teams_by_league,
            load_team_alias_book,
        )

        self.known_teams = load_known_teams_by_league()
        self.alias_book = load_team_alias_book()

    def test_a_real_completed_fixture_normalizes_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(tmp, "results.json", [_result_row()])
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(rejected, [])
            self.assertEqual(len(normalized), 1)
            row = normalized[0]
            self.assertEqual(row["competition_code"], "I1")
            self.assertEqual(row["resolved_home_team"], "Como")
            self.assertEqual(row["resolved_away_team"], "Parma")
            self.assertEqual(row["scheduled_date"], "2026-09-14")
            self.assertEqual(row["actual_result"], "H")
            self.assertIsNone(row["closing_odds"])
            self.assertIsNone(row["closing_odds_source"])
            self.assertEqual(row["bet9ja_result_id"], "1925")
            self.assertEqual(counts["source_rows_total"], 1)
            self.assertEqual(counts["rejected_at_parse"], 0)

    def test_a_blank_full_time_score_is_quarantined_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(tmp, "results.json", [_result_row(full_time_score_raw="")])
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(normalized, [])
            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0]["reason"], brs.REASON_MISSING_FULL_TIME_SCORE)

    def test_a_non_numeric_full_time_score_is_quarantined_never_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(tmp, "results.json", [_result_row(full_time_score_raw="Postponed")])
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(normalized, [])
            self.assertEqual(rejected[0]["reason"], brs.REASON_UNPARSEABLE_FULL_TIME_SCORE)

    def test_an_unconfirmed_competition_group_is_quarantined_not_assumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(
                tmp, "results.json", [_result_row(competition_raw="England Premier League")]
            )
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(normalized, [])
            self.assertEqual(rejected[0]["reason"], brs.REASON_COMPETITION_NOT_COVERED)

    def test_an_unparseable_fixture_text_is_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(tmp, "results.json", [_result_row(fixture_raw="Como Parma")])
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(normalized, [])
            self.assertEqual(rejected[0]["reason"], brs.REASON_MISSING_TEAM_IDENTITY)

    def test_two_source_rows_disagreeing_on_the_same_fixture_both_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_envelope(
                tmp,
                "results.json",
                [
                    _result_row(bet9ja_result_id="1925", full_time_score_raw="2 - 1"),
                    _result_row(bet9ja_result_id="1925", full_time_score_raw="1 - 1"),
                ],
            )
            normalized, rejected, counts = brs.build_bet9ja_results_batch(
                [path], self.known_teams, self.alias_book
            )
            self.assertEqual(normalized, [])
            self.assertEqual(len(rejected), 2)
            self.assertTrue(all(r["reason"] == brs.REASON_CONFLICTING_SOURCE_ROW for r in rejected))


class IngestBet9jaResultsTests(unittest.TestCase):
    def test_a_real_fixture_settles_cleanly_and_reconciles(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})

            input_path = _write_envelope(tmp, "results.json", [_result_row()])
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["counts"]["forecast_matches_scored"], 1)
            self.assertEqual(report["counts"]["forecast_matches_conflicted"], 0)
            self.assertTrue(report["rows_reconciled"])

            settled = result["settled_forecasts"]["settled"]
            self.assertEqual(len(settled), 1)
            self.assertEqual(settled[0]["forecast_id"], forecast_id)
            self.assertEqual(settled[0]["actual_result"], "H")
            self.assertEqual(settled[0]["bet9ja_result_id"], "1925")

            records = read_all(ledger_path)
            scored_events = [r for r in records if r["event_type"] == "SCORED"]
            self.assertEqual(len(scored_events), 1)
            self.assertIsNone(scored_events[0]["payload"]["closing_odds"])
            self.assertIsNone(scored_events[0]["payload"]["closing_odds_source"])

    def test_rerunning_the_identical_batch_is_a_safe_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})

            input_path = _write_envelope(tmp, "results.json", [_result_row()])
            brs.ingest_bet9ja_results([input_path], ledger_dir)
            second = brs.ingest_bet9ja_results([input_path], ledger_dir)

            self.assertEqual(second["settlement_report"]["counts"]["forecast_matches_scored"], 0)
            self.assertEqual(second["settlement_report"]["counts"]["forecast_matches_duplicate_skipped"], 1)
            self.assertEqual(len(read_all(ledger_path)), 2)  # RECORDED + one SCORED, never duplicated

    def test_a_fixture_with_no_matching_forecast_is_unmatched_never_a_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            (ledger_dir / forecast_ledger.DEFAULT_FILENAME).touch()

            input_path = _write_envelope(tmp, "results.json", [_result_row()])
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["counts"]["forecast_matches_scored"], 0)
            self.assertEqual(report["counts"]["rows_unmatched_no_forecast"], 1)

    def test_an_abstention_with_no_model_probabilities_is_not_scorable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path, model_probabilities=None, stop_reason="FORECAST_STOP_TEST")

            input_path = _write_envelope(tmp, "results.json", [_result_row()])
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["counts"]["forecast_matches_scored"], 0)
            self.assertEqual(report["counts"]["forecast_matches_not_scorable"], 1)

    def test_a_genuine_conflict_against_an_existing_different_score_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(ledger_path, forecast_ledger.ids.forecast_id(
                "bxf_results_settle_1", fds.MARKET_TYPE, "soccer_1x2_elo_v1-test"
            ), "D")  # a DIFFERENT result than the "H" the results row will report

            input_path = _write_envelope(tmp, "results.json", [_result_row(full_time_score_raw="2 - 1")])
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["status"], "CONFLICT")
            self.assertEqual(report["counts"]["forecast_matches_conflicted"], 1)
            records = read_all(ledger_path)
            self.assertEqual(len([r for r in records if r["event_type"] == "SCORED"]), 1)  # unchanged

    def test_a_same_result_recapture_of_an_already_football_data_scored_fixture_is_a_duplicate_not_a_conflict(self):
        # The exact real-world defect this fix closes: a Bet9ja Results
        # recapture of a fixture football-data.co.uk already scored (with
        # real closing odds) must never conflict just because this source
        # has no closing odds of its own to offer -- confirmed against
        # today's real 2026-09-14 Serie A fixtures during PR #56 review.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(
                ledger_path, forecast_id, "H", closing_odds={"H": 1.2, "D": 6.0, "A": 15.0}, closing_odds_source="test"
            )

            input_path = _write_envelope(tmp, "results.json", [_result_row(full_time_score_raw="2 - 1")])  # "H"
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["counts"]["forecast_matches_conflicted"], 0)
            self.assertEqual(report["counts"]["forecast_matches_duplicate_skipped"], 1)

            records = read_all(ledger_path)
            scored_events = [r for r in records if r["event_type"] == "SCORED"]
            self.assertEqual(len(scored_events), 1)
            # The existing real closing odds are untouched -- never erased
            # or downgraded to null by this source's own lack of odds.
            self.assertEqual(scored_events[0]["payload"]["closing_odds"], {"H": 1.2, "D": 6.0, "A": 15.0})

    def test_a_duplicate_recapture_alongside_a_genuinely_new_fixture_scores_only_the_new_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            already_scored_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(
                ledger_path, already_scored_id, "H", closing_odds={"H": 1.2, "D": 6.0, "A": 15.0}, closing_odds_source="test"
            )
            new_forecast_id = _record_forecast(
                ledger_path,
                fixture_id="bxf_results_settle_new",
                resolved_home_team="Torino",
                resolved_away_team="Roma",
                model_probabilities={"H": 0.4, "D": 0.3, "A": 0.3},
            )

            input_path = _write_envelope(
                tmp,
                "results.json",
                [
                    _result_row(bet9ja_result_id="1925", fixture_raw="Como - Parma", full_time_score_raw="2 - 1"),
                    _result_row(row_index=1, bet9ja_result_id="2592", fixture_raw="Torino - Roma", full_time_score_raw="0 - 2"),
                ],
            )
            result = brs.ingest_bet9ja_results([input_path], ledger_dir)

            report = result["settlement_report"]
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["counts"]["forecast_matches_conflicted"], 0)
            self.assertEqual(report["counts"]["forecast_matches_duplicate_skipped"], 1)
            self.assertEqual(report["counts"]["forecast_matches_scored"], 1)
            self.assertEqual(result["settled_forecasts"]["settled"][0]["forecast_id"], new_forecast_id)

    def test_rerunning_a_duplicate_recapture_batch_stays_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(
                ledger_path, forecast_id, "H", closing_odds={"H": 1.2, "D": 6.0, "A": 15.0}, closing_odds_source="test"
            )

            input_path = _write_envelope(tmp, "results.json", [_result_row(full_time_score_raw="2 - 1")])
            first = brs.ingest_bet9ja_results([input_path], ledger_dir)
            second = brs.ingest_bet9ja_results([input_path], ledger_dir)

            self.assertEqual(first["settlement_report"]["status"], "OK")
            self.assertEqual(second["settlement_report"]["status"], "OK")
            self.assertEqual(second["settlement_report"]["counts"]["forecast_matches_duplicate_skipped"], 1)
            self.assertEqual(len([r for r in read_all(ledger_path) if r["event_type"] == "SCORED"]), 1)


if __name__ == "__main__":
    unittest.main()
