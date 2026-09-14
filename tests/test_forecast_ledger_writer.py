"""Tests for the forecast-ledger writer
(``src/pcbf_calculator/orchestration/forecast_ledger_writer.py``) and its
``--ledger-dir`` wiring into ``run-bet9ja-research``.

Style matches this repo's existing convention: plain ``unittest.TestCase``,
synthetic envelope builders mirroring ``tests/test_bet9ja_research_session.py``,
using real football-data.co.uk team/competition names so the real,
committed adapter artifact actually produces forecasts.
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers.storage import read_all  # noqa: E402

from pcbf_calculator.orchestration.bet9ja_research_session import (  # noqa: E402
    main as session_main,
    run_bet9ja_research_session,
    run_session,
)
from pcbf_calculator.orchestration import forecast_ledger_writer  # noqa: E402
from pcbf_calculator.orchestration.forecast_ledger_writer import (  # noqa: E402
    LedgerBatchConflictError,
    build_ledger_events,
    write_batch,
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


class RecordedForecastProvenanceTests(unittest.TestCase):
    """Acceptance: successful forecast recorded with complete provenance."""

    def test_ranked_market_produces_one_recorded_event_with_full_provenance(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        result = run_bet9ja_research_session(envelope)
        events = build_ledger_events(result)
        self.assertEqual(len(events), 1)
        event = events[0]
        payload = event["payload"]

        self.assertEqual(event["event_type"], "RECORDED")
        self.assertTrue(event["forecast_id"].startswith("fc_"))
        self.assertIsNotNone(payload["model_probabilities"])
        self.assertEqual(set(payload["model_probabilities"].keys()), {"H", "D", "A"})
        self.assertAlmostEqual(sum(payload["model_probabilities"].values()), 1.0, places=6)
        self.assertTrue(payload["model_version"])
        self.assertTrue(payload["artifact_hash"])
        self.assertTrue(payload["output_hash"])
        self.assertIsNone(payload["stop_reason"])
        self.assertEqual(payload["classification"], "RESEARCH-MODEL")
        self.assertEqual(payload["selection"], None)
        self.assertEqual(payload["selection_status"], "considered")
        self.assertIsNone(payload["operator_decision"])
        # Preserved fields (spec: capture session ID, source fixture ID, forecast cutoff).
        self.assertEqual(payload["capture_id"], "soccer-test-session")
        self.assertEqual(payload["fixture_id"], "bxf_1")
        self.assertEqual(payload["captured_at_utc"], CAPTURED_AT_UTC)


class AbstentionProvenanceTests(unittest.TestCase):
    """Acceptance: typed abstention recorded with nullable probabilities."""

    def test_unresolved_competition_abstention_has_null_probabilities_and_typed_stop_reason(self):
        fixture = make_fixture(competition_id="1209691", home="Enyimba", away="Rivers United")
        envelope = make_envelope([NIGERIA_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        events = build_ledger_events(result)
        self.assertEqual(len(events), 1)
        payload = events[0]["payload"]

        self.assertIsNone(payload["model_probabilities"])
        self.assertEqual(payload["stop_reason"], "FORECAST_COMPETITION_UNRESOLVED")
        # Model version/artifact identity still available -- the adapter
        # always loads its real artifact even when it declines to forecast.
        self.assertTrue(payload["model_version"])
        self.assertTrue(payload["artifact_hash"])
        self.assertEqual(payload["classification"], "RESEARCH-MODEL")
        self.assertEqual(payload["selection_status"], "considered")
        self.assertIsNone(payload["operator_decision"])
        self.assertIsNone(payload["selection"])

    def test_unresolved_team_abstention_has_null_probabilities_and_typed_stop_reason(self):
        fixture = make_fixture(home="Nonexistent FC", away="Chelsea")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [fixture])
        result = run_bet9ja_research_session(envelope)
        events = build_ledger_events(result)
        payload = events[0]["payload"]
        self.assertIsNone(payload["model_probabilities"])
        self.assertEqual(payload["stop_reason"], "FORECAST_TEAM_UNRESOLVED")

    def test_pricing_quality_excluded_and_ingestion_quarantined_fixtures_produce_no_ledger_event(self):
        arbitrage_fixture = make_fixture(fixture_id="bxf_arb", odds={"H": 1.4, "D": 1.4, "A": 1.4})
        quarantined_fixture = make_fixture(fixture_id="bxf_q", date_heading_raw="Sat 12 Sep", kickoff_raw="00:00")
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [arbitrage_fixture, quarantined_fixture])
        result = run_bet9ja_research_session(envelope)
        self.assertEqual(result["research_session_report"]["counts"]["pricing_quality_excluded"], 1)
        self.assertEqual(result["research_session_report"]["counts"]["quarantined"], 1)
        events = build_ledger_events(result)
        self.assertEqual(events, [])


class IdempotentReplayTests(unittest.TestCase):
    """Acceptance: identical rerun appends zero duplicate events."""

    def test_identical_rerun_is_a_complete_idempotent_no_op(self):
        envelope = make_envelope(
            [ENGLAND_LEDGER_ENTRY],
            [make_fixture(fixture_id="bxf_1"), make_fixture(fixture_id="bxf_2", home="Nonexistent FC")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            events = build_ledger_events(result)

            summary_1 = write_batch(ledger_path, events)
            self.assertEqual(summary_1["appended"], 2)
            self.assertEqual(summary_1["duplicate_skipped"], 0)
            self.assertEqual(summary_1["total_ledger_records"], 2)

            result_2 = run_bet9ja_research_session(copy.deepcopy(envelope))
            events_2 = build_ledger_events(result_2)
            summary_2 = write_batch(ledger_path, events_2)
            self.assertEqual(summary_2["appended"], 0)
            self.assertEqual(summary_2["duplicate_skipped"], 2)
            self.assertEqual(summary_2["conflicted"], 0)
            self.assertEqual(summary_2["total_ledger_records"], 2)

            self.assertEqual(len(read_all(ledger_path)), 2)


class ConflictDetectionTests(unittest.TestCase):
    """Acceptance: changed content under an existing natural key fails as
    a conflict, and preflight leaves a mixed batch entirely unwritten."""

    def test_changed_rerun_fails_before_any_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            write_batch(ledger_path, build_ledger_events(result))
            self.assertEqual(len(read_all(ledger_path)), 1)

            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["offered_odds"]["H"] = 2.05  # real, modest change
            changed_result = run_bet9ja_research_session(changed_envelope)
            changed_events = build_ledger_events(changed_result)

            with self.assertRaises(LedgerBatchConflictError) as ctx:
                write_batch(ledger_path, changed_events)
            self.assertEqual(len(ctx.exception.conflicts), 1)
            self.assertIn("bxf_1", str(ctx.exception))
            # Ledger completely unchanged -- still exactly the one original record.
            self.assertEqual(len(read_all(ledger_path)), 1)

    def test_mixed_valid_and_conflicting_batch_remains_fully_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            # Seed the ledger with one real, prior record for bxf_1.
            seed_envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            seed_result = run_bet9ja_research_session(seed_envelope)
            write_batch(ledger_path, build_ledger_events(seed_result))
            self.assertEqual(len(read_all(ledger_path)), 1)

            # A batch with one genuinely NEW fixture (bxf_2, would append
            # cleanly) plus one CHANGED bxf_1 (would conflict) -- the
            # whole batch must be rejected, including the clean item.
            mixed_envelope = make_envelope(
                [ENGLAND_LEDGER_ENTRY],
                [
                    make_fixture(fixture_id="bxf_1", odds={"H": 2.05, "D": 3.4, "A": 4.3}),
                    make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),  # abstention, still a real batch item
                ],
            )
            mixed_result = run_bet9ja_research_session(mixed_envelope)
            mixed_events = build_ledger_events(mixed_result)
            self.assertEqual(len(mixed_events), 2)

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, mixed_events)

            # Still exactly the one original record -- the clean bxf_2
            # item was NOT appended despite not itself conflicting.
            records = read_all(ledger_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["payload"]["fixture_id"], "bxf_1")


class ReconciliationTests(unittest.TestCase):
    """Acceptance: ranked and abstention counts reconcile with attempted
    ledger records."""

    def test_attempted_equals_ranked_plus_abstained(self):
        fixtures = [
            make_fixture(fixture_id="bxf_1"),
            make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),
            make_fixture(fixture_id="bxf_3", odds={"H": 1.4, "D": 1.4, "A": 1.4}),  # pricing-quality excluded
        ]
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], fixtures)
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            result = run_bet9ja_research_session(envelope)
            counts = result["research_session_report"]["counts"]
            events = build_ledger_events(result)
            summary = write_batch(ledger_path, events)

            self.assertEqual(summary["attempted"], counts["ranked_selections"] + counts["forecast_abstained"])
            self.assertEqual(summary["attempted"], 2)  # bxf_3 (pricing-quality excluded) never attempted


class ConcurrencyTests(unittest.TestCase):
    """Acceptance: two simultaneous writers cannot both pass preflight
    and corrupt or duplicate the ledger. Forces the race deterministically
    by widening the window between read_all's on-disk read and the real
    commit (patching ledgers.storage.read_all with a small sleep) --
    without write_batch's own exclusive lock, this reliably reproduces two
    duplicate lines under the same forecast_id; with it, exactly one."""

    def test_two_concurrent_batches_for_the_same_new_fixture_never_duplicate(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_race")])
        result = run_bet9ja_research_session(envelope)
        events = build_ledger_events(result)
        self.assertEqual(len(events), 1)

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"

            original_read_all = forecast_ledger_writer.read_all

            def slow_read_all(path):
                records = original_read_all(path)
                time.sleep(0.2)
                return records

            forecast_ledger_writer.read_all = slow_read_all
            try:
                results = []
                errors = []

                def worker():
                    try:
                        results.append(write_batch(ledger_path, events))
                    except Exception as exc:  # pragma: no cover -- only on a real failure
                        errors.append(exc)

                t1 = threading.Thread(target=worker)
                t2 = threading.Thread(target=worker)
                t1.start()
                time.sleep(0.05)  # ensure t1 is inside its slow read_all before t2 starts
                t2.start()
                t1.join(timeout=10)
                t2.join(timeout=10)
            finally:
                forecast_ledger_writer.read_all = original_read_all

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            total_appended = sum(r["appended"] for r in results)
            total_duplicate_skipped = sum(r["duplicate_skipped"] for r in results)
            self.assertEqual(total_appended, 1)
            self.assertEqual(total_duplicate_skipped, 1)

            records = original_read_all(ledger_path)
            self.assertEqual(len(records), 1, f"expected exactly one record, found {len(records)}: {records}")


class NoBettingLedgerChangeTests(unittest.TestCase):
    def test_writing_the_forecast_ledger_never_touches_a_betting_ledger_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp)
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
            result = run_bet9ja_research_session(envelope)
            write_batch(ledger_dir / "forecast-ledger.jsonl", build_ledger_events(result))
            self.assertFalse((ledger_dir / "betting-ledger.jsonl").exists())
            # forecast-ledger.jsonl.lock is the write lock's own dedicated
            # file (never the ledger file itself) -- expected alongside it.
            self.assertEqual(
                sorted(p.name for p in ledger_dir.iterdir()),
                ["forecast-ledger.jsonl", "forecast-ledger.jsonl.lock"],
            )


class LedgerDirCliOptionTests(unittest.TestCase):
    """Acceptance: existing command remains byte-identical when
    --ledger-dir is absent; and end-to-end wiring when present."""

    def test_omitting_ledger_dir_writes_no_ledger_and_matches_prior_behavior(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"

            result = run_session(input_path, output_dir)  # no ledger_dir
            self.assertNotIn("ledger_write_summary", result)
            self.assertFalse((tmp_path / "ledger_data").exists())
            self.assertTrue((output_dir / "research-session-report.json").exists())

    def test_ledger_dir_option_writes_research_outputs_and_ledger_together(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            output_dir = tmp_path / "out"
            ledger_dir = tmp_path / "ledger_data"

            exit_code = session_main(
                [str(input_path), "--output-dir", str(output_dir), "--ledger-dir", str(ledger_dir)]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "research-session-report.json").exists())
            self.assertTrue((ledger_dir / "forecast-ledger.jsonl").exists())
            records = read_all(ledger_dir / "forecast-ledger.jsonl")
            self.assertEqual(len(records), 1)

    def test_conflicting_ledger_dir_run_exits_nonzero_and_writes_no_research_outputs_either(self):
        envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(envelope), encoding="utf-8")
            ledger_dir = tmp_path / "ledger_data"

            exit_code = session_main(
                [str(input_path), "--output-dir", str(tmp_path / "out1"), "--ledger-dir", str(ledger_dir)]
            )
            self.assertEqual(exit_code, 0)

            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["offered_odds"]["H"] = 2.05
            changed_input_path = tmp_path / "changed-capture.json"
            changed_input_path.write_text(json.dumps(changed_envelope), encoding="utf-8")

            output_dir_2 = tmp_path / "out2"
            exit_code = session_main(
                [str(changed_input_path), "--output-dir", str(output_dir_2), "--ledger-dir", str(ledger_dir)]
            )
            self.assertEqual(exit_code, 2)
            self.assertFalse(output_dir_2.exists())
            self.assertEqual(len(read_all(ledger_dir / "forecast-ledger.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
