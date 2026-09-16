"""Tests for football-data.org forecast-ledger settlement
(``src/pcbf_calculator/orchestration/football_data_org_settlement.py``).

Style matches ``tests/test_bet9ja_results_settlement.py`` -- plain
``unittest.TestCase``, the real, committed ``soccer_1x2_elo_v1``
artifact/team-alias data for identity resolution, and a fake ``http_get``
(never a real network call) so the entire module is exercised without a
real API token or live access -- this environment's own egress policy
blocks api.football-data.org outright, so a live-network test is not
possible here regardless."""

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

from pcbf_calculator.orchestration import football_data_org_settlement as fdo  # noqa: E402
from pcbf_calculator.orchestration import football_data_settlement as fds  # noqa: E402

FAKE_TOKEN = "test-token-never-a-real-one"


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_fdo_settle_1",
    competition_code: str = "I1",
    resolved_home_team: str = "Torino",
    resolved_away_team: str = "Roma",
    scheduled_date: str = "2026-09-14",
    kickoff_utc: str | None = None,
    model_probabilities: dict[str, float] | None = None,
    stop_reason: str | None = None,
):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Italy Serie A",
        kickoff_utc=kickoff_utc or f"{scheduled_date}T16:30:00Z",
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


def _match(
    match_id=1001,
    status="FINISHED",
    home="Torino",
    away="Roma",
    home_goals=0,
    away_goals=2,
    utc_date="2026-09-14T16:30:00Z",
):
    return {
        "id": match_id,
        "utcDate": utc_date,
        "status": status,
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "score": {"fullTime": {"home": home_goals, "away": away_goals}},
    }


def _fake_http_get(matches_by_code):
    """Returns an http_get(url, token) fake that inspects the URL's own
    competition-code path segment and returns that competition's
    pre-canned matches -- never a real network call."""

    def http_get(url, token):
        assert token == FAKE_TOKEN
        for code, matches in matches_by_code.items():
            if f"/competitions/{code}/matches" in url:
                return json.dumps({"matches": matches}).encode("utf-8")
        return json.dumps({"matches": []}).encode("utf-8")

    return http_get


from datetime import datetime, timezone  # noqa: E402
from pcbf_calculator.adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


class FindUnsettledForecastsTests(unittest.TestCase):
    def test_a_scorable_past_kickoff_forecast_is_unsettled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            unsettled = fdo.find_unsettled_forecasts(ledger_path, now_utc=NOW)
            self.assertEqual(len(unsettled), 1)
            self.assertEqual(unsettled[0]["forecast_id"], forecast_id)

    def test_a_future_kickoff_forecast_is_never_unsettled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(
                ledger_path,
                model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22},
                scheduled_date="2026-09-20",
                kickoff_utc="2026-09-20T16:30:00Z",
            )
            unsettled = fdo.find_unsettled_forecasts(ledger_path, now_utc=NOW)
            self.assertEqual(unsettled, [])

    def test_an_abstention_is_never_unsettled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path, model_probabilities=None, stop_reason="FORECAST_STOP_TEST")
            unsettled = fdo.find_unsettled_forecasts(ledger_path, now_utc=NOW)
            self.assertEqual(unsettled, [])

    def test_an_already_scored_forecast_is_never_unsettled(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(ledger_path, forecast_id, "H")
            unsettled = fdo.find_unsettled_forecasts(ledger_path, now_utc=NOW)
            self.assertEqual(unsettled, [])


class BuildBatchTests(unittest.TestCase):
    def setUp(self):
        from pcbf_calculator.adapters.soccer_1x2_elo_v1.adapter import (
            load_known_teams_by_league,
            load_team_alias_book,
        )

        self.known_teams = load_known_teams_by_league()
        self.alias_book = load_team_alias_book()

    def test_a_finished_match_with_known_team_names_normalizes_cleanly(self):
        normalized, rejected, counts = fdo.build_football_data_org_batch(
            {"I1": [_match()]}, self.known_teams, self.alias_book
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(normalized), 1)
        row = normalized[0]
        self.assertEqual(row["competition_code"], "I1")
        self.assertEqual(row["resolved_home_team"], "Torino")
        self.assertEqual(row["resolved_away_team"], "Roma")
        self.assertEqual(row["scheduled_date"], "2026-09-14")
        self.assertEqual(row["actual_result"], "A")
        self.assertIsNone(row["closing_odds"])
        self.assertIsNone(row["closing_odds_source"])

    def test_a_non_finished_status_is_quarantined_defensively(self):
        # Even though the request itself filters on status=FINISHED
        # server-side, this module never trusts that filter alone.
        normalized, rejected, counts = fdo.build_football_data_org_batch(
            {"I1": [_match(status="POSTPONED")]}, self.known_teams, self.alias_book
        )
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], fdo.REASON_MATCH_NOT_FINISHED)

    def test_a_missing_score_is_quarantined_never_guessed(self):
        match = _match()
        match["score"] = {"fullTime": {"home": None, "away": None}}
        normalized, rejected, counts = fdo.build_football_data_org_batch(
            {"I1": [match]}, self.known_teams, self.alias_book
        )
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], fdo.REASON_MISSING_SCORE)

    def test_an_unresolved_team_name_is_quarantined_never_guessed(self):
        # football-data.org's own full-legal-name style ("Torino FC") has
        # no confirmed alias-book entry yet -- must fail closed, not be
        # guessed into a match.
        normalized, rejected, counts = fdo.build_football_data_org_batch(
            {"I1": [_match(home="Torino FC")]}, self.known_teams, self.alias_book
        )
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], fdo.REASON_HOME_TEAM_UNRESOLVED)

    def test_two_matches_disagreeing_on_the_same_fixture_both_quarantine(self):
        normalized, rejected, counts = fdo.build_football_data_org_batch(
            {"I1": [_match(match_id=1, home_goals=0, away_goals=2), _match(match_id=2, home_goals=1, away_goals=1)]},
            self.known_teams,
            self.alias_book,
        )
        self.assertEqual(normalized, [])
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(r["reason"] == fdo.REASON_CONFLICTING_SOURCE_ROW for r in rejected))


class IngestFootballDataOrgResultsTests(unittest.TestCase):
    def test_missing_token_fails_closed_before_any_ledger_or_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            with self.assertRaises(fdo.MissingApiTokenError):
                fdo.ingest_football_data_org_results(ledger_dir, token=None, http_get=lambda url, tok: (_ for _ in ()).throw(AssertionError("network was called")))
            self.assertFalse((ledger_dir / forecast_ledger.DEFAULT_FILENAME).exists())

    def test_no_unsettled_forecasts_makes_zero_api_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            http_get = lambda url, tok: (_ for _ in ()).throw(AssertionError("no request should have been made"))
            result = fdo.ingest_football_data_org_results(ledger_dir, token=FAKE_TOKEN, http_get=http_get)
            self.assertEqual(result["settlement_report"]["status"], "OK")
            self.assertEqual(result["settlement_report"]["leagues_queried"], [])

    def test_a_real_unsettled_fixture_settles_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})

            http_get = _fake_http_get({"SA": [_match()]})
            result = fdo.ingest_football_data_org_results(
                ledger_dir, token=FAKE_TOKEN, http_get=http_get, now_utc=NOW, sleep_fn=lambda s: None
            )

            report = result["settlement_report"]
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["leagues_queried"], ["I1"])
            self.assertEqual(report["counts"]["forecast_matches_scored"], 1)
            self.assertEqual(report["counts"]["forecast_matches_conflicted"], 0)
            self.assertTrue(report["rows_reconciled"])
            self.assertEqual(result["settled_forecasts"]["settled"][0]["forecast_id"], forecast_id)

            records = read_all(ledger_path)
            scored_events = [r for r in records if r["event_type"] == "SCORED"]
            self.assertEqual(len(scored_events), 1)
            self.assertIsNone(scored_events[0]["payload"]["closing_odds"])

    def test_rerunning_the_identical_batch_is_a_safe_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})

            http_get = _fake_http_get({"SA": [_match()]})
            fdo.ingest_football_data_org_results(ledger_dir, token=FAKE_TOKEN, http_get=http_get, now_utc=NOW, sleep_fn=lambda s: None)
            second = fdo.ingest_football_data_org_results(
                ledger_dir, token=FAKE_TOKEN, http_get=http_get, now_utc=NOW, sleep_fn=lambda s: None
            )

            # The forecast is now SCORED, so it's no longer "unsettled" --
            # zero API requests on the second run at all.
            self.assertEqual(second["settlement_report"]["leagues_queried"], [])
            self.assertEqual(len(read_all(ledger_path)), 2)  # RECORDED + one SCORED, never duplicated

    def test_a_genuine_conflict_against_an_existing_different_score_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path, model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22})
            forecast_ledger.score_and_append(
                ledger_path, forecast_id, "H", closing_odds={"H": 1.2, "D": 6.0, "A": 15.0}, closing_odds_source="test"
            )

            # This forecast is now ALREADY scored, so find_unsettled_forecasts
            # won't surface it -- exercise plan_settlement's own conflict
            # path directly via build_football_data_org_batch + plan_settlement,
            # the same way test_football_data_settlement.py's own
            # PlanSettlementTests does, to confirm a genuinely different
            # result from this source is still a real conflict.
            normalized, _, _ = fdo.build_football_data_org_batch(
                {"I1": [_match(home_goals=1, away_goals=1)]},  # "D", disagreeing with the existing "H"
                load_known_teams_by_league(),
                load_team_alias_book(),
            )
            plan = fds.plan_settlement(ledger_path, normalized)
            self.assertEqual(len(plan["conflicts"]), 1)
            self.assertEqual(plan["conflicts"][0]["forecast_id"], forecast_id)

    def test_an_api_error_for_one_league_never_blocks_a_different_league(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(
                ledger_path,
                fixture_id="bxf_fdo_ok",
                competition_code="I1",
                resolved_home_team="Torino",
                resolved_away_team="Roma",
                model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22},
            )
            _record_forecast(
                ledger_path,
                fixture_id="bxf_fdo_broken_league",
                competition_code="E0",
                resolved_home_team="Arsenal",
                resolved_away_team="Chelsea",
                model_probabilities={"H": 0.4, "D": 0.3, "A": 0.3},
            )

            def flaky_http_get(url, token):
                if "/competitions/PL/matches" in url:
                    raise fdo.urllib.error.URLError("connection refused")
                return json.dumps({"matches": [_match()]}).encode("utf-8")

            result = fdo.ingest_football_data_org_results(
                ledger_dir, token=FAKE_TOKEN, http_get=flaky_http_get, now_utc=NOW, sleep_fn=lambda s: None
            )

            report = result["settlement_report"]
            self.assertEqual(report["status"], "API_ERROR")
            self.assertEqual(len(report["api_errors"]), 1)
            self.assertEqual(report["api_errors"][0]["competition_code"], "E0")
            # The OTHER league (I1) still settled cleanly despite E0's failure.
            self.assertEqual(report["counts"]["forecast_matches_scored"], 1)
            self.assertEqual(result["settled_forecasts"]["settled"][0]["forecast_id"], forecast_id)

    def test_multiple_leagues_sleep_between_requests_to_respect_the_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_dir.mkdir()
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(
                ledger_path, fixture_id="bxf_a", competition_code="I1",
                resolved_home_team="Torino", resolved_away_team="Roma",
                model_probabilities={"H": 0.5, "D": 0.28, "A": 0.22},
            )
            _record_forecast(
                ledger_path, fixture_id="bxf_b", competition_code="E0",
                resolved_home_team="Arsenal", resolved_away_team="Chelsea",
                model_probabilities={"H": 0.4, "D": 0.3, "A": 0.3},
            )

            sleeps = []
            http_get = _fake_http_get({"SA": [_match()], "PL": [_match(home="Arsenal", away="Chelsea")]})
            fdo.ingest_football_data_org_results(
                ledger_dir, token=FAKE_TOKEN, http_get=http_get, now_utc=NOW, sleep_fn=sleeps.append
            )
            # 2 leagues queried -> exactly 1 inter-request sleep, never a
            # sleep before the very first request.
            self.assertEqual(sleeps, [fdo.REQUEST_INTERVAL_SECONDS])


if __name__ == "__main__":
    unittest.main()
