"""Tests for the Bet9ja assembled-capture -> PCBF research-batch bridge
(``src/pcbf_calculator/ingestion/``).

Style matches this repo's existing convention (``tests/test_cli_integration.py``
et al.): plain ``unittest.TestCase``, no third-party test framework.
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pcbf_calculator.ingestion.bet9ja import (
    ingest_assembled_capture,
    main as ingest_main,
    run_ingest,
    validate_envelope,
)
from pcbf_calculator.ingestion.errors import (
    BET9JA_ALREADY_STARTED,
    BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES,
    BET9JA_DUPLICATE_COMPETITION_ID,
    BET9JA_DUPLICATE_FIXTURE_ID,
    BET9JA_INCOMPLETE_PRICES,
    BET9JA_INVALID_PRICE,
    BET9JA_KICKOFF_AMBIGUOUS,
    BET9JA_LEDGER_SUM_MISMATCH,
    BET9JA_LEDGER_TOTAL_MISMATCH,
    BET9JA_SEGMENT_NOT_ASSEMBLED,
    BET9JA_UNKNOWN_FIXTURE_COMPETITION,
    BET9JA_UNSUPPORTED_MARKET_FAMILY,
    BET9JA_UNSUPPORTED_SPORT,
    BET9JA_UNSUPPORTED_STATUS,
    Bet9jaEnvelopeError,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "bet9ja"
ROUND13_REAL_EXTRACT = FIXTURES_DIR / "bet9ja-soccer-all-round13-confirmed-empty-with-fixtures-real-extract.json"

# ---------------------------------------------------------------------------
# Synthetic envelope builders. Every field name/shape matches the REAL
# assembled export produced by soccer_session.js::buildAssembledEnvelope
# and parser.js's own fixture shape -- verified against the real Round 13
# extract fixture above and the Round 14 evidence quoted in this project's
# PR history, never invented.
# ---------------------------------------------------------------------------

CAPTURED_AT_UTC = "2026-09-12T15:30:23.000Z"


def make_fixture(
    fixture_id="bxf_1",
    competition_id="1209691",
    home="Enyimba",
    away="Rivers United",
    sport="SOCCER",
    status="PRE_MATCH",
    market_family="1X2",
    odds=None,
    date_heading_raw="Sat 12 Sep",
    kickoff_raw="19:00",
):
    return {
        "fixture_id": fixture_id,
        "sport": sport,
        "status": status,
        "market_family": market_family,
        "offered_odds": odds if odds is not None else {"H": 1.95, "D": 3.4, "A": 4.2},
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


NIGERIA_LEDGER_ENTRY = {"competition_id": "1209691", "country": "Nigeria", "competition": "Professional Football League", "status": "COMPLETED"}
ENGLAND_EMPTY_LEDGER_ENTRY = {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "CONFIRMED_EMPTY"}


def valid_envelope():
    return make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture()])


class EnvelopeRecognitionTests(unittest.TestCase):
    # 1. Valid assembled capture passes.
    def test_valid_assembled_capture_passes(self):
        result = ingest_assembled_capture(valid_envelope())
        self.assertEqual(result["capture_validation"]["admitted_fixtures"], 1)
        self.assertEqual(result["capture_validation"]["quarantined_fixtures"], 0)

    # 2. Segment file is rejected.
    def test_segment_file_is_rejected(self):
        segment = {
            "schema_version": "bet9ja-soccer-session.v1",
            "capture_session_id": "soccer-test-session",
            "segment_index": 1,
            "captured_at_utc": CAPTURED_AT_UTC,
            "competition_results": [{"source_competition_id": "1209691", "outcome": "CAPTURED_IN_BATCH"}],
            "fixtures": [make_fixture()],
            "unparsed_records": [],
        }
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(segment)
        self.assertEqual(ctx.exception.code, BET9JA_SEGMENT_NOT_ASSEMBLED)


class EnvelopeValidationTests(unittest.TestCase):
    # 3. Ledger totals fail reconciliation.
    def test_ledger_total_mismatch_fails(self):
        envelope = valid_envelope()
        envelope["summary"]["total"] = 2  # only 1 ledger entry actually exists
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_LEDGER_TOTAL_MISMATCH)

    def test_ledger_sum_mismatch_fails(self):
        envelope = valid_envelope()
        envelope["summary"]["completed"] = 0  # 0+0+0+0 != total(1)
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_LEDGER_SUM_MISMATCH)

    # 4. Duplicate competition IDs fail.
    def test_duplicate_competition_id_fails(self):
        ledger = [NIGERIA_LEDGER_ENTRY, dict(NIGERIA_LEDGER_ENTRY)]
        envelope = make_envelope(ledger, [make_fixture()])
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_DUPLICATE_COMPETITION_ID)

    # 5. Duplicate fixture IDs fail.
    def test_duplicate_fixture_id_fails(self):
        fixtures = [make_fixture(fixture_id="bxf_1"), make_fixture(fixture_id="bxf_1", home="Other", away="Team")]
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], fixtures)
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_DUPLICATE_FIXTURE_ID)

    # 6. Empty competition with fixtures fails -- real evidence (Round 13).
    def test_confirmed_empty_competition_with_fixtures_fails_real_evidence(self):
        envelope = json.loads(ROUND13_REAL_EXTRACT.read_text(encoding="utf-8"))
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES)

    def test_confirmed_empty_competition_with_fixtures_fails_synthetic(self):
        ledger = [NIGERIA_LEDGER_ENTRY, ENGLAND_EMPTY_LEDGER_ENTRY]
        fixtures = [make_fixture(), make_fixture(fixture_id="bxf_2", competition_id="2000001", home="Arsenal", away="Chelsea")]
        envelope = make_envelope(ledger, fixtures)
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES)

    # 7. Fixture references an unknown competition.
    def test_fixture_references_unknown_competition_fails(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(competition_id="9999999")])
        with self.assertRaises(Bet9jaEnvelopeError) as ctx:
            validate_envelope(envelope)
        self.assertEqual(ctx.exception.code, BET9JA_UNKNOWN_FIXTURE_COMPETITION)

    def test_failed_or_pending_competition_with_fixtures_fails(self):
        ledger = [{"competition_id": "3000001", "country": "Spain", "competition": "LaLiga", "status": "FAILED"}]
        envelope = make_envelope(ledger, [make_fixture(competition_id="3000001")])
        with self.assertRaises(Bet9jaEnvelopeError):
            validate_envelope(envelope)


class FixtureAdmissionTests(unittest.TestCase):
    # 8. Complete 1X2 fixture is admitted.
    def test_complete_1x2_fixture_is_admitted(self):
        result = ingest_assembled_capture(valid_envelope())
        fixtures = result["fixtures_normalized"]["fixtures"]
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0]["source_fixture_id"], "bxf_1")
        self.assertEqual(fixtures[0]["market"], {"family": "1X2", "home": 1.95, "draw": 3.4, "away": 4.2})

    # 9. Missing H, D or A price is quarantined.
    def test_missing_price_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(odds={"H": 1.95, "D": None, "A": 4.2})])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(len(result["fixtures_normalized"]["fixtures"]), 0)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_INCOMPLETE_PRICES)

    # 10. Invalid price is quarantined.
    def test_invalid_price_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(odds={"H": 0.9, "D": 3.4, "A": 4.2})])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_INVALID_PRICE)

    def test_non_numeric_price_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(odds={"H": "SP", "D": 3.4, "A": 4.2})])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_INVALID_PRICE)

    # 11. Non-Soccer fixture is quarantined.
    def test_non_soccer_fixture_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(sport="BASKETBALL")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_UNSUPPORTED_SPORT)

    # 12. Non-pre-match fixture is quarantined.
    def test_non_prematch_fixture_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(status="LIVE")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_UNSUPPORTED_STATUS)

    def test_non_1x2_market_family_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(market_family="OVER_UNDER")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_UNSUPPORTED_MARKET_FAMILY)


class KickoffResolutionTests(unittest.TestCase):
    # 13. Lagos display time resolves deterministically.
    def test_lagos_display_time_resolves_deterministically(self):
        # 2026-09-12 is a real Saturday -- matches "Sat 12 Sep".
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(date_heading_raw="Sat 12 Sep", kickoff_raw="19:00")])
        result = ingest_assembled_capture(envelope)
        fixture = result["fixtures_normalized"]["fixtures"][0]
        self.assertEqual(fixture["kickoff_utc"], "2026-09-12T18:00:00Z")  # Lagos (UTC+1) 19:00 -> UTC 18:00
        self.assertEqual(fixture["kickoff_resolution"], "DERIVED_FROM_BET9JA_LAGOS_DISPLAY")
        self.assertEqual(fixture["kickoff_source_timezone"], "Africa/Lagos")
        self.assertEqual(fixture["kickoff_source_date_raw"], "Sat 12 Sep")
        self.assertEqual(fixture["kickoff_source_time_raw"], "19:00")

    def test_resolution_is_repeatable(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture()])
        first = ingest_assembled_capture(envelope)["fixtures_normalized"]["fixtures"][0]["kickoff_utc"]
        second = ingest_assembled_capture(envelope)["fixtures_normalized"]["fixtures"][0]["kickoff_utc"]
        self.assertEqual(first, second)

    # 14. Weekday/date conflict is quarantined.
    def test_weekday_date_conflict_is_quarantined(self):
        # 2026-09-12 is a Saturday, not a Monday -- no candidate year near
        # the capture date can make "Mon 12 Sep" true, so this is a
        # genuine, permanent conflict, never resolved.
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(date_heading_raw="Mon 12 Sep", kickoff_raw="19:00")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_KICKOFF_AMBIGUOUS)

    def test_unparseable_date_heading_is_quarantined(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(date_heading_raw="not a date", kickoff_raw="19:00")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_KICKOFF_AMBIGUOUS)

    # 15. Already-started fixture is quarantined.
    def test_already_started_fixture_is_quarantined(self):
        # date_heading_raw names the SAME day as the capture, but the
        # kickoff time is well before the capture's own UTC time of day.
        envelope = make_envelope(
            [NIGERIA_LEDGER_ENTRY],
            [make_fixture(date_heading_raw="Sat 12 Sep", kickoff_raw="10:00")],
            captured_at_utc="2026-09-12T15:30:23.000Z",  # 16:30 Lagos -- after a 10:00 Lagos kickoff
        )
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["fixtures_quarantined"]["quarantined"][0]["reason"], BET9JA_ALREADY_STARTED)


class IdempotencyTests(unittest.TestCase):
    # 16. Repeated import is byte-identical.
    def test_repeated_import_is_byte_identical(self, tmp_path_factory=None):
        import tempfile

        envelope = make_envelope(
            [NIGERIA_LEDGER_ENTRY, ENGLAND_EMPTY_LEDGER_ENTRY],
            [make_fixture(), make_fixture(fixture_id="bxf_2", competition_id="1209691", home="A", away="B")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "input.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")

            out1 = tmp_dir / "run1"
            out2 = tmp_dir / "run2"
            run_ingest(input_path, out1)
            run_ingest(input_path, out2)

            for filename in ("capture-validation.json", "fixtures-normalized.json", "fixtures-quarantined.json", "pcbf-research-batch.json"):
                self.assertEqual(
                    (out1 / filename).read_bytes(),
                    (out2 / filename).read_bytes(),
                    f"{filename} differed across repeated imports",
                )

    def test_output_ordering_is_stable_regardless_of_input_order(self):
        fixtures_a = [
            make_fixture(fixture_id="bxf_2", home="Zeta", away="Yankee"),
            make_fixture(fixture_id="bxf_1", home="Alpha", away="Bravo"),
        ]
        fixtures_b = list(reversed(fixtures_a))
        envelope_a = make_envelope([NIGERIA_LEDGER_ENTRY], fixtures_a)
        envelope_b = make_envelope([NIGERIA_LEDGER_ENTRY], fixtures_b)
        result_a = ingest_assembled_capture(envelope_a)["fixtures_normalized"]["fixtures"]
        result_b = ingest_assembled_capture(envelope_b)["fixtures_normalized"]["fixtures"]
        self.assertEqual([f["source_fixture_id"] for f in result_a], [f["source_fixture_id"] for f in result_b])


class ClassificationCeilingTests(unittest.TestCase):
    # 17. Classification ceiling remains RESEARCH-MODEL.
    def test_classification_ceiling_is_always_research_model(self):
        result = ingest_assembled_capture(valid_envelope())
        self.assertEqual(result["fixtures_normalized"]["fixtures"][0]["classification_ceiling"], "RESEARCH-MODEL")
        self.assertEqual(result["pcbf_research_batch"]["classification_ceiling"], "RESEARCH-MODEL")

    def test_zero_admission_result_is_still_valid_when_every_rejection_is_explained(self):
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [make_fixture(sport="TENNIS")])
        result = ingest_assembled_capture(envelope)
        self.assertEqual(result["capture_validation"]["admitted_fixtures"], 0)
        self.assertEqual(result["capture_validation"]["quarantined_fixtures"], 1)
        self.assertEqual(result["capture_validation"]["reason_counts"], {BET9JA_UNSUPPORTED_SPORT: 1})
        self.assertEqual(result["pcbf_research_batch"]["fixtures"], [])


class CliIntegrationTests(unittest.TestCase):
    def test_cli_writes_four_files_and_returns_zero_on_success(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "input.json"
            input_path.write_text(json.dumps(valid_envelope()), encoding="utf-8")
            output_dir = tmp_dir / "out"

            exit_code = ingest_main([str(input_path), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 0)
            for filename in ("capture-validation.json", "fixtures-normalized.json", "fixtures-quarantined.json", "pcbf-research-batch.json"):
                self.assertTrue((output_dir / filename).exists(), f"{filename} was not written")

    def test_cli_returns_nonzero_and_writes_nothing_on_rejected_capture(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            envelope = valid_envelope()
            envelope["summary"]["total"] = 99
            input_path = tmp_dir / "input.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_dir / "out"

            exit_code = ingest_main([str(input_path), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 2)
            self.assertFalse(output_dir.exists())

    def test_pcbf_calculator_main_dispatches_ingest_bet9ja_subcommand(self):
        import tempfile

        from pcbf_calculator.cli import main as calculator_main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "input.json"
            input_path.write_text(json.dumps(valid_envelope()), encoding="utf-8")
            output_dir = tmp_dir / "out"

            exit_code = calculator_main(["ingest-bet9ja", str(input_path), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "pcbf-research-batch.json").exists())


# 18. The successful Round 14 export passes as a regression fixture.
#
# [PENDING REAL EVIDENCE]: the actual clean Round 14 assembled export
# (bet9ja-soccer-all-soccer-2026-09-12T15-30-23Z.json, 322 competitions,
# 1,208 fixtures, zero conflicts) was referenced from a sandbox path this
# session could not read. The file available locally
# (tests/fixtures/bet9ja/bet9ja-soccer-all-round13-confirmed-empty-with-fixtures-real-extract.json)
# is a real-data extract of the PRE-fix, CORRUPTED export -- used above
# (test_confirmed_empty_competition_with_fixtures_fails_real_evidence) to
# prove this bridge correctly REJECTS it, which is itself real regression
# coverage. Once the actual Round 14 clean file is supplied, add it here
# verbatim (e.g. as
# tests/fixtures/bet9ja/bet9ja-soccer-all-round14-confirmed-clean.json)
# and un-skip this test -- never fabricate a substitute "clean" file to
# make this test pass, per this project's own evidence-only discipline.
class Round14RegressionTest(unittest.TestCase):
    @unittest.skip(
        "Pending upload of the real Round 14 clean assembled export "
        "(bet9ja-soccer-all-soccer-2026-09-12T15-30-23Z.json) -- see this "
        "module's own comment immediately above. Never faked."
    )
    def test_round14_confirmed_export_passes_with_zero_conflicts(self):
        round14_fixture = FIXTURES_DIR / "bet9ja-soccer-all-round14-confirmed-clean.json"
        envelope = json.loads(round14_fixture.read_text(encoding="utf-8"))
        result = ingest_assembled_capture(envelope)
        validation = result["capture_validation"]
        self.assertEqual(validation["ledger_reconciliation"]["total"], 322)
        self.assertEqual(validation["competitions_represented"] + validation["quarantined_fixtures"], 322)


if __name__ == "__main__":
    unittest.main()
