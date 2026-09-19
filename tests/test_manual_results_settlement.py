"""Tests for the manual-results settlement fallback
(``src/pcbf_calculator/orchestration/manual_results_settlement.py``).

Style matches ``tests/test_football_data_settlement.py`` and
``tests/test_bet9ja_results_settlement.py``: plain ``unittest.TestCase``,
real football-data.co.uk team/competition names so the real, committed
adapter artifact actually resolves identity, a directly-built RECORDED
event (never through the full research pipeline) to exercise matching in
isolation.
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

from ledgers import forecast_ledger  # noqa: E402
from ledgers.storage import read_all  # noqa: E402

from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook  # noqa: E402
from pcbf_calculator.orchestration import manual_results_settlement as mrs  # noqa: E402

VALID_HASH_A = "sha256:" + "a" * 64
VALID_HASH_B = "sha256:" + "b" * 64


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_manual_1",
    competition_code: str = "E0",
    resolved_home_team: str = "Arsenal",
    resolved_away_team: str = "Chelsea",
    scheduled_date: str = "2026-09-20",
    model_probabilities: dict[str, float] | None = None,
):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z",
        market_type=mrs.MARKET_TYPE,
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities=model_probabilities if model_probabilities is not None else {"H": 0.6, "D": 0.25, "A": 0.15},
        model_version="soccer_1x2_elo_v1-test",
        artifact_hash="sha256:test",
        output_hash="sha256:test-output",
        selection_status="considered",
        competition_code=competition_code,
        resolved_home_team=resolved_home_team,
        resolved_away_team=resolved_away_team,
        scheduled_date=scheduled_date,
    )
    result = forecast_ledger.append_recorded(ledger_path, event)
    return result.record["forecast_id"]


def _envelope(results):
    return {"schema_version": mrs.SCHEMA_VERSION_INPUT, "results": results}


def _result(
    competition_raw="Premier League",
    home_team_raw="Arsenal",
    away_team_raw="Chelsea",
    scheduled_date="2026-09-20",
    completion_status="COMPLETED",
    final_score=None,
    sources=None,
):
    final_score = final_score if final_score is not None else {"home": 2, "away": 1}
    if sources is None:
        sources = [_source(reported_score=final_score, authoritative=True)]
    return {
        "competition_raw": competition_raw,
        "home_team_raw": home_team_raw,
        "away_team_raw": away_team_raw,
        "scheduled_date": scheduled_date,
        "completion_status": completion_status,
        "final_score": final_score,
        "sources": sources,
    }


def _source(source_name="Source A", authoritative=False, reported_score=None, evidence_hash=VALID_HASH_A):
    return {
        "source_name": source_name,
        "source_url": "https://example.com/result",
        "retrieved_at_utc": "2026-09-21T09:00:00Z",
        "evidence_hash": evidence_hash,
        "authoritative": authoritative,
        "reported_score": reported_score if reported_score is not None else {"home": 2, "away": 1},
    }


def _write(tmp: str, name: str, envelope: dict) -> Path:
    path = Path(tmp) / name
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


class SchemaValidationTests(unittest.TestCase):
    def setUp(self):
        self.schema = mrs.load_manual_results_schema()

    def test_a_well_formed_envelope_validates(self):
        envelope = _envelope([_result()])
        self.assertEqual(mrs.validate_manual_results_envelope(envelope, self.schema), [])

    def test_missing_schema_version_is_rejected(self):
        envelope = {"results": []}
        self.assertNotEqual(mrs.validate_manual_results_envelope(envelope, self.schema), [])

    def test_a_result_with_zero_sources_is_rejected_by_min_items(self):
        envelope = _envelope([_result(sources=[])])
        self.assertNotEqual(mrs.validate_manual_results_envelope(envelope, self.schema), [])

    def test_a_malformed_evidence_hash_is_rejected(self):
        envelope = _envelope([_result(sources=[_source(evidence_hash="not-a-hash")])])
        self.assertNotEqual(mrs.validate_manual_results_envelope(envelope, self.schema), [])


class CorroborationGateTests(unittest.TestCase):
    def test_one_authoritative_source_is_sufficient(self):
        accepted, reason = mrs._check_corroboration({"home": 2, "away": 1}, [_source(authoritative=True)])
        self.assertTrue(accepted)
        self.assertIsNone(reason)

    def test_one_non_authoritative_source_alone_is_insufficient(self):
        accepted, reason = mrs._check_corroboration({"home": 2, "away": 1}, [_source(authoritative=False)])
        self.assertFalse(accepted)
        self.assertEqual(reason, mrs.REASON_INSUFFICIENT_CORROBORATION)

    def test_two_independent_agreeing_sources_are_sufficient(self):
        sources = [_source(source_name="A", authoritative=False), _source(source_name="B", authoritative=False)]
        accepted, reason = mrs._check_corroboration({"home": 2, "away": 1}, sources)
        self.assertTrue(accepted)
        self.assertIsNone(reason)

    def test_two_sources_with_the_same_source_name_never_count_as_independent(self):
        sources = [_source(source_name="A", authoritative=False), _source(source_name="A", authoritative=False)]
        accepted, reason = mrs._check_corroboration({"home": 2, "away": 1}, sources)
        self.assertFalse(accepted)
        self.assertEqual(reason, mrs.REASON_INSUFFICIENT_CORROBORATION)

    def test_a_disagreeing_source_fails_closed_even_if_another_source_is_authoritative(self):
        sources = [
            _source(source_name="A", authoritative=True, reported_score={"home": 2, "away": 1}),
            _source(source_name="B", authoritative=False, reported_score={"home": 3, "away": 1}),
        ]
        accepted, reason = mrs._check_corroboration({"home": 2, "away": 1}, sources)
        self.assertFalse(accepted)
        self.assertEqual(reason, mrs.REASON_SOURCE_SCORE_DISAGREEMENT)


class BuildManualResultsBatchTests(unittest.TestCase):
    def setUp(self):
        self.schema = mrs.load_manual_results_schema()
        self.known_teams = {"E0": {"Arsenal", "Chelsea", "Tottenham", "Liverpool"}}
        self.alias_book = TeamAliasBook({"leagues": {}})  # empty -- every team below resolves by exact match

    def _build(self, results):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "manual-results.json", _envelope(results))
            return mrs.build_manual_results_batch([path], self.known_teams, self.alias_book, self.schema)

    def test_a_well_formed_authoritative_result_normalizes_cleanly(self):
        normalized, rejected, counts = self._build([_result()])
        self.assertEqual(rejected, [])
        self.assertEqual(len(normalized), 1)
        row = normalized[0]
        self.assertEqual(row["competition_code"], "E0")
        self.assertEqual(row["resolved_home_team"], "Arsenal")
        self.assertEqual(row["resolved_away_team"], "Chelsea")
        self.assertEqual(row["actual_result"], "H")
        self.assertIsNone(row["closing_odds"])
        self.assertIsNone(row["closing_odds_source"])
        self.assertEqual(row["settlement_basis"], "MANUAL_VERIFIED")
        self.assertEqual(len(row["manual_sources"]), 1)
        self.assertEqual(counts["source_rows_total"], 1)

    def test_a_structurally_invalid_file_rejects_the_whole_file_never_a_partial_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "bad.json", {"results": "not-a-list"})
            normalized, rejected, counts = mrs.build_manual_results_batch(
                [path], self.known_teams, self.alias_book, self.schema
            )
        self.assertEqual(normalized, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], mrs.REASON_INVALID_ENVELOPE)

    def test_an_unfinished_match_is_rejected(self):
        normalized, rejected, _ = self._build([_result(completion_status="POSTPONED")])
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], mrs.REASON_NOT_COMPLETED)

    def test_insufficient_corroboration_is_rejected(self):
        normalized, rejected, _ = self._build([_result(sources=[_source(authoritative=False)])])
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], mrs.REASON_INSUFFICIENT_CORROBORATION)

    def test_a_competition_outside_the_five_league_allowlist_is_rejected_never_expanding_coverage(self):
        normalized, rejected, _ = self._build([_result(competition_raw="Eredivisie")])
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], mrs.REASON_COMPETITION_NOT_COVERED)

    def test_a_fuzzy_or_unresolved_team_name_is_rejected_never_guessed(self):
        normalized, rejected, _ = self._build([_result(home_team_raw="Arsenal FC (typo)")])
        self.assertEqual(normalized, [])
        self.assertEqual(rejected[0]["reason"], mrs.REASON_HOME_TEAM_UNRESOLVED)

    def test_two_source_rows_disagreeing_on_the_same_fixture_within_one_batch_both_reject(self):
        results = [
            _result(final_score={"home": 2, "away": 1}, sources=[_source(authoritative=True, reported_score={"home": 2, "away": 1})]),
            _result(final_score={"home": 1, "away": 1}, sources=[_source(authoritative=True, reported_score={"home": 1, "away": 1})]),
        ]
        normalized, rejected, _ = self._build(results)
        self.assertEqual(normalized, [])
        self.assertEqual(len(rejected), 2)
        self.assertTrue(all(r["reason"] == mrs.REASON_CONFLICTING_SOURCE_ROW for r in rejected))

    def test_reconciliation_every_source_row_lands_exactly_once(self):
        results = [_result(), _result(competition_raw="Eredivisie"), _result(completion_status="ABANDONED")]
        normalized, rejected, counts = self._build(results)
        self.assertEqual(counts["source_rows_total"], len(results))
        self.assertEqual(
            counts["source_rows_total"],
            len(rejected) + len(normalized) + counts["duplicate_source_rows_collapsed"],
        )


class RunSettlementSessionTests(unittest.TestCase):
    def test_dry_run_default_never_writes_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            input_path = _write(tmp, "manual-results.json", _envelope([_result()]))

            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            before = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)

            result = mrs.run_settlement_session(input_path, ledger_dir, output_dir, confirm=False)
            after = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)

            self.assertEqual(result["settlement_report"]["status"], "DRY_RUN")
            self.assertEqual(result["settlement_report"]["confirmed"], False)
            self.assertEqual(len(result["settled_forecasts"]["settled"]), 0)
            self.assertEqual(before, after)  # byte-for-byte untouched
            for name in ("settlement-report.json", "settled-forecasts.json", "unmatched-results.json", "settlement-conflicts.json"):
                self.assertTrue((output_dir / name).exists())

    def test_confirm_writes_exactly_once_and_rerun_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir_1 = Path(tmp) / "out1"
            output_dir_2 = Path(tmp) / "out2"
            input_path = _write(tmp, "manual-results.json", _envelope([_result()]))

            forecast_id = _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)

            first = mrs.run_settlement_session(input_path, ledger_dir, output_dir_1, confirm=True)
            self.assertEqual(first["settlement_report"]["status"], "OK")
            self.assertEqual(len(first["settled_forecasts"]["settled"]), 1)
            self.assertEqual(first["settled_forecasts"]["settled"][0]["forecast_id"], forecast_id)
            self.assertEqual(first["settled_forecasts"]["settled"][0]["settlement_basis"], "MANUAL_VERIFIED")

            state = forecast_ledger.current_state(ledger_dir / forecast_ledger.DEFAULT_FILENAME, forecast_id)
            self.assertEqual(state["actual_result"], "H")
            self.assertIsNone(state["closing_odds"])
            # Provenance lives in the report only -- never inside the
            # ledger's own SCORED payload (that schema has no field for it).
            self.assertNotIn("settlement_basis", state)
            self.assertNotIn("manual_sources", state)

            second = mrs.run_settlement_session(input_path, ledger_dir, output_dir_2, confirm=True)
            self.assertEqual(second["settlement_report"]["status"], "OK")
            self.assertEqual(len(second["settled_forecasts"]["settled"]), 0)  # safe no-op

            records = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            self.assertEqual(sum(1 for r in records if r["event_type"] == "SCORED"), 1)

    def test_a_conflicting_result_writes_nothing_even_with_confirm(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            forecast_id = _record_forecast(ledger_path)
            forecast_ledger.score_and_append(ledger_path, forecast_id, "H")
            before = read_all(ledger_path)

            input_path = _write(
                tmp,
                "manual-results.json",
                _envelope([_result(final_score={"home": 0, "away": 3}, sources=[_source(authoritative=True, reported_score={"home": 0, "away": 3})])]),
            )

            result = mrs.run_settlement_session(input_path, ledger_dir, output_dir, confirm=True)
            self.assertEqual(result["settlement_report"]["status"], "CONFLICT")
            self.assertEqual(len(result["settlement_conflicts"]["conflicts"]), 1)
            after = read_all(ledger_path)
            self.assertEqual(before, after)  # nothing written despite --confirm

    def test_unmatched_result_never_invents_a_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            input_path = _write(tmp, "manual-results.json", _envelope([_result()]))
            # No RECORDED forecast at all in this ledger.
            result = mrs.run_settlement_session(input_path, ledger_dir, output_dir, confirm=True)
            self.assertEqual(result["settlement_report"]["status"], "OK")
            self.assertEqual(len(result["settled_forecasts"]["settled"]), 0)
            self.assertEqual(len(result["unmatched_results"]["unmatched"]), 1)
            records = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            self.assertEqual(records, [])  # ledger was never even created


class MainCliTests(unittest.TestCase):
    def test_dry_run_exit_code_is_zero_with_no_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            input_path = _write(tmp, "manual-results.json", _envelope([_result()]))
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            exit_code = mrs.main([str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            records = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            self.assertEqual(len(records), 1)  # only the RECORDED row -- no SCORED written without --confirm

    def test_confirm_flag_actually_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            input_path = _write(tmp, "manual-results.json", _envelope([_result()]))
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            exit_code = mrs.main(
                [str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir), "--confirm"]
            )
            self.assertEqual(exit_code, 0)
            records = read_all(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            self.assertEqual(sum(1 for r in records if r["event_type"] == "SCORED"), 1)


if __name__ == "__main__":
    unittest.main()
