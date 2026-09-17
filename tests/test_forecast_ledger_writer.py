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


def _force_created_at(events, value):
    """created_at_utc is regenerated fresh on every build_recorded_event
    call, so two builds a moment apart in a fast test can coincidentally
    land in the same second and mask a real bug in the reobservation
    classifier (which must ignore this field, but only this field, when
    deciding whether a difference is "just a later day's capture").
    Forces every event's own value to something deterministic and
    different, so a reobservation test actually exercises that path
    instead of accidentally passing on timing alone."""

    for event in events:
        event["payload"]["created_at_utc"] = value
    return events


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
    """Acceptance: a genuine change to an IMMUTABLE identity field under
    an existing natural key still fails as a real conflict, and preflight
    still leaves a mixed batch entirely unwritten. A pure odds change is
    no longer a conflict at all -- see ReobservationTests below for that
    policy (first-seen forecasting)."""

    def test_changed_immutable_identity_fails_before_any_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            write_batch(ledger_path, build_ledger_events(result))
            self.assertEqual(len(read_all(ledger_path)), 1)

            # The SAME fixture_id now resolves to different opponents --
            # an actual identity problem (real captures never legitimately
            # do this), never something a live market's own odds movement
            # could ever explain away.
            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["participants"] = {"home": "Chelsea", "away": "Arsenal"}
            changed_result = run_bet9ja_research_session(changed_envelope)
            changed_events = build_ledger_events(changed_result)

            with self.assertRaises(LedgerBatchConflictError) as ctx:
                write_batch(ledger_path, changed_events)
            self.assertEqual(len(ctx.exception.conflicts), 1)
            self.assertIn("bxf_1", str(ctx.exception))
            # Ledger completely unchanged -- still exactly the one original record.
            self.assertEqual(len(read_all(ledger_path)), 1)

    def test_odds_change_alongside_an_immutable_change_still_conflicts(self):
        # A conflicting batch is never silently downgraded to a
        # reobservation just because odds ALSO happened to move --
        # every differing key must be volatile for that classification.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            write_batch(ledger_path, build_ledger_events(result))

            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["offered_odds"]["H"] = 2.05
            changed_envelope["fixtures"][0]["participants"] = {"home": "Chelsea", "away": "Arsenal"}
            changed_result = run_bet9ja_research_session(changed_envelope)
            changed_events = build_ledger_events(changed_result)

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, changed_events)
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
            # cleanly) plus one bxf_1 whose IMMUTABLE identity changed
            # (would conflict) -- the whole batch must be rejected,
            # including the clean item.
            mixed_envelope = make_envelope(
                [ENGLAND_LEDGER_ENTRY],
                [
                    make_fixture(fixture_id="bxf_1", home="Chelsea", away="Arsenal"),
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


class ReobservationTests(unittest.TestCase):
    """Acceptance: first-seen forecasting -- a later capture of an
    already-recorded fixture, differing only in fields a live market is
    EXPECTED to move on its own, is EXISTING_FIXTURE_REOBSERVED (skipped,
    original stays authoritative), never a conflict, and never blocks
    genuinely new fixtures in the same batch."""

    def test_odds_only_recapture_is_reobserved_not_conflicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            write_batch(ledger_path, build_ledger_events(result))
            original_record = read_all(ledger_path)[0]

            recaptured_envelope = copy.deepcopy(envelope)
            recaptured_envelope["fixtures"][0]["offered_odds"] = {"H": 2.05, "D": 3.6, "A": 3.9}
            recaptured_result = run_bet9ja_research_session(recaptured_envelope)
            recaptured_events = _force_created_at(build_ledger_events(recaptured_result), "2026-09-16T00:00:00+00:00")

            summary = write_batch(ledger_path, recaptured_events)  # never raises
            self.assertEqual(summary["appended"], 0)
            self.assertEqual(summary["duplicate_skipped"], 0)
            self.assertEqual(summary["existing_fixture_reobserved"], 1)
            self.assertEqual(summary["existing_fixture_reobserved_forecast_ids"], [original_record["forecast_id"]])
            self.assertEqual(summary["conflicted"], 0)

            # The original record is untouched -- its own (older) odds
            # are still exactly what's on disk, never overwritten with
            # today's.
            records = read_all(ledger_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0], original_record)
            self.assertEqual(records[0]["payload"]["offered_odds"]["H"], 1.9)

    def test_mixed_batch_of_reobserved_and_new_appends_only_the_new_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            seed_envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(seed_envelope)))

            # bxf_1 recaptured with moved odds (reobserved) alongside a
            # genuinely new bxf_2 (appended) -- exactly today's real
            # scenario: yesterday's fixtures reobserved, a handful new.
            mixed_envelope = make_envelope(
                [ENGLAND_LEDGER_ENTRY],
                [
                    make_fixture(fixture_id="bxf_1", odds={"H": 2.05, "D": 3.4, "A": 4.3}),
                    make_fixture(fixture_id="bxf_2"),
                ],
            )
            mixed_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(mixed_envelope)), "2026-09-16T00:00:00+00:00"
            )
            summary = write_batch(ledger_path, mixed_events)
            self.assertEqual(summary["appended"], 1)
            self.assertEqual(summary["existing_fixture_reobserved"], 1)
            self.assertEqual(summary["conflicted"], 0)

            records = read_all(ledger_path)
            self.assertEqual(len(records), 2)
            fixture_ids = {r["payload"]["fixture_id"] for r in records}
            self.assertEqual(fixture_ids, {"bxf_1", "bxf_2"})

    def test_reobservation_never_blocks_repeated_daily_reruns(self):
        # The exact real-world shape this policy exists for: the SAME
        # still-open fixture recaptured on three separate days, odds
        # moving each time, never once raising or losing data.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            for day_number, h_odds in enumerate((1.95, 2.0, 2.1), start=1):
                day_envelope = copy.deepcopy(envelope)
                day_envelope["fixtures"][0]["offered_odds"]["H"] = h_odds
                day_events = _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(day_envelope)),
                    f"2026-09-{15 + day_number:02d}T00:00:00+00:00",
                )
                summary = write_batch(ledger_path, day_events)
                self.assertEqual(summary["existing_fixture_reobserved"], 1)
                self.assertEqual(summary["conflicted"], 0)

            records = read_all(ledger_path)
            self.assertEqual(len(records), 1)  # still exactly one immutable snapshot, ever
            self.assertEqual(records[0]["payload"]["offered_odds"]["H"], 1.9)  # the FIRST-seen odds, forever

    def test_a_manually_changed_model_probability_is_still_a_real_conflict(self):
        # model_probabilities is immutable per this module's own policy --
        # exercised directly (rather than through two real adapter runs,
        # since the real adapter is deterministic and would never itself
        # produce a different probability for the identical team pair) to
        # confirm the classifier itself, not just the common case.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            events = build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope)))
            write_batch(ledger_path, events)

            tampered = copy.deepcopy(events)
            probs = tampered[0]["payload"]["model_probabilities"]
            tampered[0]["payload"]["model_probabilities"] = {"H": probs["A"], "D": probs["D"], "A": probs["H"]}

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, tampered)
            self.assertEqual(len(read_all(ledger_path)), 1)

    def test_a_manually_changed_stop_reason_is_still_a_real_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1", home="Nonexistent FC")])
            events = build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope)))
            self.assertIsNotNone(events[0]["payload"]["stop_reason"])
            write_batch(ledger_path, events)

            tampered = copy.deepcopy(events)
            tampered[0]["payload"]["stop_reason"] = "FORECAST_SOME_OTHER_REASON"

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, tampered)
            self.assertEqual(len(read_all(ledger_path)), 1)


class RescheduleTests(unittest.TestCase):
    """Acceptance: a later capture of an already-recorded fixture whose
    ONLY genuine difference (beyond what a routine reobservation already
    tolerates) is its kickoff time is a real FIXTURE_RESCHEDULED event,
    never a content conflict and never a second forecast -- the original
    RECORDED row's own model_probabilities/forecast_cutoff stay exactly
    as first recorded."""

    def test_kickoff_only_recapture_is_rescheduled_not_conflicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            result = run_bet9ja_research_session(copy.deepcopy(envelope))
            write_batch(ledger_path, build_ledger_events(result))
            original_record = read_all(ledger_path)[0]
            original_forecast_id = original_record["forecast_id"]
            original_kickoff = original_record["payload"]["kickoff_utc"]

            rescheduled_envelope = copy.deepcopy(envelope)
            rescheduled_envelope["fixtures"][0]["kickoff_raw"] = "20:00"
            rescheduled_result = run_bet9ja_research_session(rescheduled_envelope)
            rescheduled_events = _force_created_at(
                build_ledger_events(rescheduled_result), "2026-09-16T00:00:00+00:00"
            )
            new_kickoff = rescheduled_events[0]["payload"]["kickoff_utc"]
            self.assertNotEqual(new_kickoff, original_kickoff)

            summary = write_batch(ledger_path, rescheduled_events)  # never raises
            self.assertEqual(summary["appended"], 0)
            self.assertEqual(summary["existing_fixture_reobserved"], 0)
            self.assertEqual(summary["existing_fixture_rescheduled"], 1)
            self.assertEqual(summary["existing_fixture_rescheduled_forecast_ids"], [original_forecast_id])
            self.assertEqual(summary["conflicted"], 0)

            records = read_all(ledger_path)
            self.assertEqual(len(records), 2)  # the original RECORDED row, plus one FIXTURE_RESCHEDULED event
            recorded_rows = [r for r in records if r["event_type"] == "RECORDED"]
            reschedule_rows = [r for r in records if r["event_type"] == "FIXTURE_RESCHEDULED"]
            self.assertEqual(len(recorded_rows), 1)
            self.assertEqual(len(reschedule_rows), 1)

            # The original RECORDED row is byte-for-byte untouched -- its
            # own kickoff/model_probabilities/forecast_cutoff never change.
            self.assertEqual(recorded_rows[0], original_record)

            reschedule_payload = reschedule_rows[0]["payload"]
            self.assertEqual(reschedule_payload["old_kickoff_utc"], original_kickoff)
            self.assertEqual(reschedule_payload["new_kickoff_utc"], new_kickoff)
            self.assertEqual(reschedule_payload["kickoff_utc"], new_kickoff)

    def test_rescheduled_fixture_never_gets_a_second_forecast(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))
            original_probabilities = read_all(ledger_path)[0]["payload"]["model_probabilities"]

            rescheduled_envelope = copy.deepcopy(envelope)
            rescheduled_envelope["fixtures"][0]["kickoff_raw"] = "20:00"
            rescheduled_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(rescheduled_envelope)), "2026-09-16T00:00:00+00:00"
            )
            write_batch(ledger_path, rescheduled_events)

            recorded_rows = [r for r in read_all(ledger_path) if r["event_type"] == "RECORDED"]
            self.assertEqual(len(recorded_rows), 1)
            self.assertEqual(recorded_rows[0]["payload"]["model_probabilities"], original_probabilities)

    def test_rerunning_the_same_reschedule_a_second_time_is_a_safe_no_op(self):
        # The exact real-world shape this exists for: a rescheduled
        # fixture's own odds keep moving on later days, at its NEW kickoff
        # -- classified as a routine reobservation against the CURRENT
        # (already-rescheduled) state, never a second reschedule attempt.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            rescheduled_envelope = copy.deepcopy(envelope)
            rescheduled_envelope["fixtures"][0]["kickoff_raw"] = "20:00"
            rescheduled_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(copy.deepcopy(rescheduled_envelope))),
                "2026-09-16T00:00:00+00:00",
            )
            first_summary = write_batch(ledger_path, rescheduled_events)
            self.assertEqual(first_summary["existing_fixture_rescheduled"], 1)
            self.assertEqual(len(read_all(ledger_path)), 2)

            # Same (already-current) new kickoff, odds moved again -- a
            # later day's recapture of the now-rescheduled fixture.
            again_envelope = copy.deepcopy(rescheduled_envelope)
            again_envelope["fixtures"][0]["offered_odds"]["H"] = 1.95
            again_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(again_envelope)), "2026-09-17T00:00:00+00:00"
            )
            second_summary = write_batch(ledger_path, again_events)
            self.assertEqual(second_summary["existing_fixture_rescheduled"], 0)
            self.assertEqual(second_summary["existing_fixture_reobserved"], 1)
            self.assertEqual(second_summary["conflicted"], 0)
            self.assertEqual(len(read_all(ledger_path)), 2)  # never a second reschedule or RECORDED row

    def test_a_team_change_alongside_a_kickoff_change_still_conflicts(self):
        # A conflicting batch is never silently downgraded to a reschedule
        # just because kickoff ALSO happened to move -- every differing
        # key outside RESCHEDULE_TOLERATED_KEYS must still be tolerated on
        # its own terms (here: none of them are).
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["kickoff_raw"] = "20:00"
            changed_envelope["fixtures"][0]["participants"] = {"home": "Chelsea", "away": "Arsenal"}
            changed_events = build_ledger_events(run_bet9ja_research_session(changed_envelope))

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, changed_events)
            self.assertEqual(len(read_all(ledger_path)), 1)

    def test_mixed_batch_of_rescheduled_and_new_appends_only_the_new_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            write_batch(
                ledger_path,
                build_ledger_events(
                    run_bet9ja_research_session(make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1")]))
                ),
            )

            mixed_envelope = make_envelope(
                [ENGLAND_LEDGER_ENTRY],
                [
                    make_fixture(fixture_id="bxf_1", kickoff_raw="20:00"),
                    make_fixture(fixture_id="bxf_2"),
                ],
            )
            mixed_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(mixed_envelope)), "2026-09-16T00:00:00+00:00"
            )
            summary = write_batch(ledger_path, mixed_events)
            self.assertEqual(summary["appended"], 1)
            self.assertEqual(summary["existing_fixture_rescheduled"], 1)
            self.assertEqual(summary["conflicted"], 0)

            records = read_all(ledger_path)
            self.assertEqual(len(records), 3)  # bxf_1 RECORDED + its FIXTURE_RESCHEDULED + bxf_2 RECORDED
            fixture_ids = {r["payload"].get("fixture_id") for r in records if r["event_type"] == "RECORDED"}
            self.assertEqual(fixture_ids, {"bxf_1", "bxf_2"})

    def test_a_second_chained_reschedule_for_the_same_fixture_is_accepted(self):
        # The real-world case this whole fix exists for: a still-
        # unresolved fixture's displayed kickoff moves AGAIN on a later
        # capture, even though it was already rescheduled once. This must
        # be accepted as a second, distinct FIXTURE_RESCHEDULED row -- the
        # bug being fixed here refused it as a CONFLICT instead.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1", kickoff_raw="16:00")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))
            original_kickoff = read_all(ledger_path)[0]["payload"]["kickoff_utc"]

            first_reschedule_envelope = copy.deepcopy(envelope)
            first_reschedule_envelope["fixtures"][0]["kickoff_raw"] = "15:00"
            first_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(first_reschedule_envelope)),
                "2026-09-16T00:00:00+00:00",
            )
            first_summary = write_batch(ledger_path, first_events)
            self.assertEqual(first_summary["existing_fixture_rescheduled"], 1)
            self.assertEqual(first_summary["conflicted"], 0)
            after_first = [r for r in read_all(ledger_path) if r["event_type"] == "FIXTURE_RESCHEDULED"]
            self.assertEqual(len(after_first), 1)
            kickoff_after_first = after_first[0]["payload"]["new_kickoff_utc"]
            self.assertNotEqual(kickoff_after_first, original_kickoff)

            second_reschedule_envelope = copy.deepcopy(first_reschedule_envelope)
            second_reschedule_envelope["fixtures"][0]["kickoff_raw"] = "14:00"
            second_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(second_reschedule_envelope)),
                "2026-09-17T00:00:00+00:00",
            )
            second_summary = write_batch(ledger_path, second_events)
            self.assertEqual(second_summary["conflicted"], 0)
            self.assertEqual(second_summary["existing_fixture_rescheduled"], 1)

            records = read_all(ledger_path)
            recorded_rows = [r for r in records if r["event_type"] == "RECORDED"]
            reschedule_rows = [r for r in records if r["event_type"] == "FIXTURE_RESCHEDULED"]
            self.assertEqual(len(recorded_rows), 1)  # never a second forecast
            self.assertEqual(len(reschedule_rows), 2)  # two distinct, append-only reschedule rows
            # The FIRST reschedule row is untouched -- never rewritten.
            self.assertEqual(reschedule_rows[0], after_first[0])
            self.assertEqual(reschedule_rows[1]["payload"]["old_kickoff_utc"], kickoff_after_first)
            self.assertNotEqual(reschedule_rows[1]["payload"]["new_kickoff_utc"], kickoff_after_first)

            from ledgers.storage import latest_state

            current = latest_state(records, "forecast_id", recorded_rows[0]["forecast_id"])
            self.assertEqual(current["kickoff_utc"], reschedule_rows[1]["payload"]["new_kickoff_utc"])

    def test_three_sequential_reschedules_chain_correctly(self):
        # Proves N-deep chaining, not just one extra hop past the first.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1", kickoff_raw="18:00")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            current_envelope = envelope
            for day, kickoff_raw in enumerate(["17:00", "16:00", "15:00"], start=1):
                current_envelope = copy.deepcopy(current_envelope)
                current_envelope["fixtures"][0]["kickoff_raw"] = kickoff_raw
                events = _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(current_envelope)),
                    f"2026-09-{15 + day:02d}T00:00:00+00:00",
                )
                summary = write_batch(ledger_path, events)
                self.assertEqual(summary["conflicted"], 0, f"reschedule #{day} unexpectedly conflicted")
                self.assertEqual(summary["existing_fixture_rescheduled"], 1)

            records = read_all(ledger_path)
            recorded_rows = [r for r in records if r["event_type"] == "RECORDED"]
            reschedule_rows = [r for r in records if r["event_type"] == "FIXTURE_RESCHEDULED"]
            self.assertEqual(len(recorded_rows), 1)
            self.assertEqual(len(reschedule_rows), 3)
            # Each link's "old" is the previous link's "new" -- a genuine,
            # unbroken chain, never a gap or a rewrite.
            for earlier, later in zip(reschedule_rows, reschedule_rows[1:]):
                self.assertEqual(earlier["payload"]["new_kickoff_utc"], later["payload"]["old_kickoff_utc"])

    def test_a_stale_reschedule_against_an_outdated_base_is_still_a_real_conflict(self):
        # Directly exercises decide_fixture_reschedule_append's own
        # continuity guard: a reschedule claiming to move FROM a kickoff
        # the fixture no longer currently has (because a later reschedule
        # already superseded it) must never be silently accepted just
        # because *a* prior reschedule row exists somewhere in history.
        from ledgers import forecast_ledger
        from ledgers.storage import APPENDED, CONFLICT

        forecast_id = "fc_stale_test"
        recorded = {
            "event_type": "RECORDED",
            "forecast_id": forecast_id,
            "payload": {"kickoff_utc": "2026-09-19T15:00:00Z", "scheduled_date": None},
        }
        first_reschedule = forecast_ledger.build_fixture_rescheduled_event(
            forecast_id=forecast_id,
            old_kickoff_utc="2026-09-19T15:00:00Z",
            new_kickoff_utc="2026-09-19T14:00:00Z",
        )
        second_reschedule = forecast_ledger.build_fixture_rescheduled_event(
            forecast_id=forecast_id,
            old_kickoff_utc="2026-09-19T14:00:00Z",
            new_kickoff_utc="2026-09-19T13:00:00Z",
        )
        existing_records = [recorded, first_reschedule, second_reschedule]

        # A legitimate continuation from the CURRENT tip (13:00) succeeds.
        valid_next = forecast_ledger.build_fixture_rescheduled_event(
            forecast_id=forecast_id,
            old_kickoff_utc="2026-09-19T13:00:00Z",
            new_kickoff_utc="2026-09-19T12:00:00Z",
        )
        self.assertEqual(
            forecast_ledger.decide_fixture_reschedule_append(existing_records, valid_next).status, APPENDED
        )

        # A STALE proposal claiming to move from the ORIGINAL (15:00),
        # already superseded twice, is refused -- never silently chained
        # past a state that isn't actually current.
        stale_next = forecast_ledger.build_fixture_rescheduled_event(
            forecast_id=forecast_id,
            old_kickoff_utc="2026-09-19T15:00:00Z",
            new_kickoff_utc="2026-09-19T11:00:00Z",
        )
        self.assertEqual(
            forecast_ledger.decide_fixture_reschedule_append(existing_records, stale_next).status, CONFLICT
        )

    def test_identical_repeat_of_the_latest_reschedule_is_still_a_safe_no_op(self):
        # Idempotency must survive chaining: rerunning the exact same
        # (already-chained) day's capture twice must never create a
        # duplicate reschedule row, even when it is not the fixture's
        # FIRST reschedule.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1", kickoff_raw="16:00")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            first_reschedule_envelope = copy.deepcopy(envelope)
            first_reschedule_envelope["fixtures"][0]["kickoff_raw"] = "15:00"
            write_batch(
                ledger_path,
                _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(copy.deepcopy(first_reschedule_envelope))),
                    "2026-09-16T00:00:00+00:00",
                ),
            )

            second_reschedule_envelope = copy.deepcopy(first_reschedule_envelope)
            second_reschedule_envelope["fixtures"][0]["kickoff_raw"] = "14:00"
            write_batch(
                ledger_path,
                _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(copy.deepcopy(second_reschedule_envelope))),
                    "2026-09-17T00:00:00+00:00",
                ),
            )
            self.assertEqual(len([r for r in read_all(ledger_path) if r["event_type"] == "FIXTURE_RESCHEDULED"]), 2)

            # Exact same day-3 capture, re-imported (odds identical too --
            # a genuine byte-identical rerun of the SECOND reschedule).
            rerun_summary = write_batch(
                ledger_path,
                _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(copy.deepcopy(second_reschedule_envelope))),
                    "2026-09-17T00:00:00+00:00",
                ),
            )
            self.assertEqual(rerun_summary["conflicted"], 0)
            self.assertEqual(rerun_summary["existing_fixture_rescheduled"], 0)
            self.assertEqual(rerun_summary["existing_fixture_reobserved"], 1)
            records = read_all(ledger_path)
            self.assertEqual(len([r for r in records if r["event_type"] == "FIXTURE_RESCHEDULED"]), 2)

    def test_a_real_conflict_alongside_a_second_reschedule_is_still_refused(self):
        # A genuine field mutation (participants) riding along with a
        # SECOND kickoff change must still hard-conflict -- chaining
        # support must never widen what counts as a tolerated reschedule.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            envelope = make_envelope([ENGLAND_LEDGER_ENTRY], [make_fixture(fixture_id="bxf_1", kickoff_raw="16:00")])
            write_batch(ledger_path, build_ledger_events(run_bet9ja_research_session(copy.deepcopy(envelope))))

            first_reschedule_envelope = copy.deepcopy(envelope)
            first_reschedule_envelope["fixtures"][0]["kickoff_raw"] = "15:00"
            write_batch(
                ledger_path,
                _force_created_at(
                    build_ledger_events(run_bet9ja_research_session(copy.deepcopy(first_reschedule_envelope))),
                    "2026-09-16T00:00:00+00:00",
                ),
            )

            mutated_envelope = copy.deepcopy(first_reschedule_envelope)
            mutated_envelope["fixtures"][0]["kickoff_raw"] = "14:00"
            mutated_envelope["fixtures"][0]["participants"] = {"home": "Chelsea", "away": "Arsenal"}
            mutated_events = _force_created_at(
                build_ledger_events(run_bet9ja_research_session(mutated_envelope)), "2026-09-17T00:00:00+00:00"
            )

            with self.assertRaises(LedgerBatchConflictError):
                write_batch(ledger_path, mutated_events)

            records = read_all(ledger_path)
            self.assertEqual(len(records), 2)  # original RECORDED + only the first reschedule
            self.assertEqual(len([r for r in records if r["event_type"] == "FIXTURE_RESCHEDULED"]), 1)


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

            # A genuine identity change (never a pure odds move -- that's
            # now a reobservation, exercised by the next test below).
            changed_envelope = copy.deepcopy(envelope)
            changed_envelope["fixtures"][0]["participants"] = {"home": "Chelsea", "away": "Arsenal"}
            changed_input_path = tmp_path / "changed-capture.json"
            changed_input_path.write_text(json.dumps(changed_envelope), encoding="utf-8")

            output_dir_2 = tmp_path / "out2"
            exit_code = session_main(
                [str(changed_input_path), "--output-dir", str(output_dir_2), "--ledger-dir", str(ledger_dir)]
            )
            self.assertEqual(exit_code, 2)
            self.assertFalse(output_dir_2.exists())
            self.assertEqual(len(read_all(ledger_dir / "forecast-ledger.jsonl")), 1)

    def test_odds_only_recapture_ledger_dir_run_exits_zero_and_still_writes_research_outputs(self):
        # The exact real-world case this policy fixes: a later day's
        # capture of the SAME fixture, odds moved, must succeed (exit 0)
        # and still produce that day's own research output files --
        # never treated as a conflict, and the ledger keeps exactly one
        # immutable record for the fixture, not two.
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

            recaptured_envelope = copy.deepcopy(envelope)
            recaptured_envelope["fixtures"][0]["offered_odds"]["H"] = 2.05
            recaptured_input_path = tmp_path / "recaptured-capture.json"
            recaptured_input_path.write_text(json.dumps(recaptured_envelope), encoding="utf-8")

            output_dir_2 = tmp_path / "out2"
            exit_code = session_main(
                [str(recaptured_input_path), "--output-dir", str(output_dir_2), "--ledger-dir", str(ledger_dir)]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir_2 / "research-session-report.json").exists())
            records = read_all(ledger_dir / "forecast-ledger.jsonl")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["payload"]["offered_odds"]["H"], 1.9)  # first-seen odds, never overwritten


if __name__ == "__main__":
    unittest.main()
