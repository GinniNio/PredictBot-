"""Tests for the manual-results evidence-collection converter
(``src/pcbf_calculator/orchestration/manual_results_evidence_converter.py``).

Style matches ``tests/test_manual_results_settlement.py``: plain
``unittest.TestCase``, real football-data.co.uk team/competition names so
the real, committed adapter artifact actually resolves identity, a
directly-built RECORDED event (never through the full research pipeline)
to give the converter a real fixture_id to look up.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger  # noqa: E402

from pcbf_calculator.orchestration import manual_results_evidence_converter as conv  # noqa: E402

VALID_HASH = hashlib.sha256(b"evidence-page-content").hexdigest()


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_evidence_1",
    competition_code: str | None = "E0",
    resolved_home_team: str | None = "Arsenal",
    resolved_away_team: str | None = "Chelsea",
    scheduled_date: str | None = "2026-09-20",
    stop_reason: str | None = None,
):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z" if scheduled_date else "2026-09-20T14:00:00Z",
        market_type="1X2",
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15} if stop_reason is None else None,
        model_version="soccer_1x2_elo_v1-test" if stop_reason is None else None,
        artifact_hash="sha256:test" if stop_reason is None else None,
        output_hash="sha256:test-output" if stop_reason is None else None,
        selection_status="considered",
        stop_reason=stop_reason,
        competition_code=competition_code,
        resolved_home_team=resolved_home_team,
        resolved_away_team=resolved_away_team,
        scheduled_date=scheduled_date,
    )
    result = forecast_ledger.append_recorded(ledger_path, event)
    return result.record["forecast_id"]


def _evidence_result(
    fixture_id="bxf_evidence_1",
    competition="Premier League",
    home="Arsenal",
    away="Chelsea",
    result_status="VERIFIED",
    home_score=2,
    away_score=1,
    review_reason=None,
    sources=None,
):
    if sources is None:
        sources = [_evidence_source(home_score=home_score, away_score=away_score)]
    return {
        "fixture_id": fixture_id,
        "competition": competition,
        "participants": {"home": home, "away": away},
        "scheduled_date": None,
        "kickoff_utc": None,
        "market_type": None,
        "match_status": "COMPLETED" if result_status == "VERIFIED" else "UNKNOWN",
        "home_score": home_score if result_status == "VERIFIED" else None,
        "away_score": away_score if result_status == "VERIFIED" else None,
        "result_status": result_status,
        "verification_method": "ONE_AUTHORITATIVE_SOURCE" if result_status == "VERIFIED" else None,
        "review_reason": review_reason,
        "sources": sources if result_status == "VERIFIED" else [],
    }


def _evidence_source(
    source_name="Arsenal FC",
    source_type="OFFICIAL_CLUB",
    home_score=2,
    away_score=1,
    evidence_sha256=VALID_HASH,
    evidence_summary=None,
):
    return {
        "source_name": source_name,
        "source_type": source_type,
        "source_url": "https://example.com/match-report",
        "retrieved_at_utc": "2026-09-21T09:00:00Z",
        "evidence_summary": evidence_summary if evidence_summary is not None else f"Final score: Arsenal {home_score}-{away_score} Chelsea.",
        "evidence_sha256": evidence_sha256,
    }


def _envelope(results):
    return {"schema_version": conv.SCHEMA_VERSION_EVIDENCE, "results": results}


def _write(tmp: str, name: str, envelope: dict) -> Path:
    path = Path(tmp) / name
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


class SchemaValidationTests(unittest.TestCase):
    def setUp(self):
        self.schema = conv.load_manual_results_evidence_schema()

    def test_a_well_formed_envelope_validates(self):
        from ledgers.validation import _check_node

        errors: list[str] = []
        _check_node(_envelope([_evidence_result()]), self.schema, "envelope", errors)
        self.assertEqual(errors, [])

    def test_wrong_schema_version_is_rejected(self):
        from ledgers.validation import _check_node

        envelope = _envelope([_evidence_result()])
        envelope["schema_version"] = "manual-results-input.v1"  # a different schema's own constant
        errors: list[str] = []
        _check_node(envelope, self.schema, "envelope", errors)
        self.assertNotEqual(errors, [])


class ConvertEvidenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.schema = conv.load_manual_results_evidence_schema()
        self.known_teams = {"E0": {"Arsenal", "Chelsea", "Tottenham", "Liverpool"}}
        from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook

        self.alias_book = TeamAliasBook({"leagues": {}})

    def _convert(self, results, ledger_path):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "evidence.json", _envelope(results))
            return conv.convert_evidence_batch([path], ledger_path, self.known_teams, self.alias_book, self.schema)

    def test_a_fully_archived_authoritative_result_converts_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            converted, rejected, counts = self._convert([_evidence_result()], ledger_path)
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        row = converted[0]
        self.assertEqual(row["competition_raw"], "Premier League")
        self.assertEqual(row["home_team_raw"], "Arsenal")
        self.assertEqual(row["away_team_raw"], "Chelsea")
        self.assertEqual(row["completion_status"], "COMPLETED")
        self.assertEqual(row["final_score"], {"home": 2, "away": 1})
        self.assertEqual(len(row["sources"]), 1)
        self.assertTrue(row["sources"][0]["authoritative"])
        self.assertEqual(row["sources"][0]["evidence_hash"], f"sha256:{VALID_HASH}")
        self.assertEqual(counts["source_rows_total"], 1)

    def test_needs_review_rows_are_rejected_as_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(result_status="NEEDS_REVIEW", review_reason="no evidence found")
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_NOT_VERIFIED)
        self.assertEqual(rejected[0]["detail"], "no evidence found")

    def test_fixture_id_absent_from_the_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            # ledger stays empty
            converted, rejected, _ = self._convert([_evidence_result()], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_FIXTURE_NOT_IN_LEDGER)

    def test_a_forecast_with_no_settlement_identity_is_rejected(self):
        # The real shape an unresolved-competition abstention has --
        # competition_code/resolved_home_team/resolved_away_team all null.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(
                ledger_path,
                competition_code=None,
                resolved_home_team=None,
                resolved_away_team=None,
                scheduled_date=None,
                stop_reason="FORECAST_COMPETITION_UNRESOLVED",
            )
            converted, rejected, _ = self._convert([_evidence_result()], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_NO_SETTLEMENT_IDENTITY)

    def test_evidence_not_yet_archived_is_rejected_never_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(sources=[_evidence_source(evidence_sha256=None)])
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_NOT_ARCHIVED)

    def test_an_unparseable_evidence_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(sources=[_evidence_source(evidence_summary="Match completed, no scoreline given.")])
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_INSUFFICIENT_CORROBORATION)

    def test_a_source_whose_own_text_disagrees_with_the_claimed_score_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(
                home_score=2, away_score=1,
                sources=[_evidence_source(evidence_summary="Final score: Arsenal 3-1 Chelsea.")],
            )
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertIn(rejected[0]["reason"], (conv.REASON_INSUFFICIENT_CORROBORATION,))
        self.assertIn("SETTLE_EVIDENCE_SCORE_MISMATCH", rejected[0]["detail"])

    def test_single_other_credible_source_is_insufficient_corroboration(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(
                sources=[_evidence_source(source_type="OTHER_CREDIBLE", source_name="SportyTrader")]
            )
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_INSUFFICIENT_CORROBORATION)

    def test_two_independent_archived_non_authoritative_sources_are_sufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            hash_b = hashlib.sha256(b"second-source-content").hexdigest()
            result = _evidence_result(
                sources=[
                    _evidence_source(source_type="OTHER_CREDIBLE", source_name="Source A"),
                    _evidence_source(source_type="OTHER_CREDIBLE", source_name="Source B", evidence_sha256=hash_b),
                ]
            )
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        self.assertEqual(len(converted[0]["sources"]), 2)

    def test_competition_or_team_text_mismatched_against_the_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path, competition_code="E0", resolved_home_team="Arsenal", resolved_away_team="Chelsea")
            # Evidence file claims a DIFFERENT competition for this exact fixture_id.
            result = _evidence_result(competition="Bundesliga")
            converted, rejected, _ = self._convert([result], ledger_path)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_IDENTITY_MISMATCH)

    def test_a_malformed_envelope_rejects_the_whole_file_never_a_partial_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "bad.json", {"schema_version": conv.SCHEMA_VERSION_EVIDENCE, "results": "not-a-list"})
            converted, rejected, counts = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema
            )
        self.assertEqual(converted, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], conv.REASON_INVALID_ENVELOPE)

    def test_reconciliation_every_source_row_lands_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            results = [
                _evidence_result(),
                _evidence_result(fixture_id="bxf_missing", result_status="NEEDS_REVIEW", review_reason="x"),
            ]
            converted, rejected, counts = self._convert(results, ledger_path)
        self.assertEqual(counts["source_rows_total"], len(results))
        self.assertEqual(counts["source_rows_total"], len(rejected) + len(converted))


class RunConversionSessionTests(unittest.TestCase):
    def test_never_writes_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            before = ledger_path.read_bytes()
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result()]))

            result = conv.run_conversion_session(input_path, ledger_dir, output_dir)

            after = ledger_path.read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(len(result["converted_input"]["results"]), 1)
            for name in ("conversion-report.json", "converted-manual-results-input.json", "conversion-rejected.json"):
                self.assertTrue((output_dir / name).exists())

    def test_converted_output_validates_against_ingest_manual_results_own_schema(self):
        # The whole point of this converter: its output must be directly
        # consumable by ingest-manual-results without any further editing.
        from pcbf_calculator.orchestration import manual_results_settlement as mrs

        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result()]))

            result = conv.run_conversion_session(input_path, ledger_dir, output_dir)
            errors = mrs.validate_manual_results_envelope(result["converted_input"], mrs.load_manual_results_schema())
        self.assertEqual(errors, [])


class MainCliTests(unittest.TestCase):
    def test_exit_code_is_zero_and_never_touches_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            before = ledger_path.read_bytes()
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result()]))

            exit_code = conv.main([str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(ledger_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
