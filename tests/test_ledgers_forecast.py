"""Tests for ledgers/forecast_ledger.py: recording, idempotent re-import,
selection updates, and scoring against a real result."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger, validation
from ledgers.storage import APPENDED, CONFLICT, DUPLICATE_SKIPPED, read_all


def _sample_kwargs(**overrides):
    kwargs = dict(
        fixture_id="fixture-1",
        sport="soccer",
        league="E0",
        kickoff_utc="2024-01-01T14:00:00+00:00",
        market_type="1X2",
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.2},
        model_probabilities={"H": 0.5, "D": 0.3, "A": 0.2},
        model_version="v1",
        artifact_hash="hash-a",
        input_hash="hash-b",
        output_hash="hash-c",
        classification="RESEARCH-MODEL",
    )
    kwargs.update(overrides)
    return kwargs


class ForecastLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "forecast-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recorded_event_validates_against_schema(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        errors = validation.validate_envelope(event, validation.load_schema("forecast_ledger.v1"))
        self.assertEqual(errors, [])

    def test_forecast_id_is_deterministic_from_natural_key(self):
        event1 = forecast_ledger.build_recorded_event(**_sample_kwargs())
        event2 = forecast_ledger.build_recorded_event(**_sample_kwargs(created_at_utc="2099-01-01T00:00:00+00:00"))
        self.assertEqual(event1["forecast_id"], event2["forecast_id"])

    def test_append_recorded_is_appended_once(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        result = forecast_ledger.append_recorded(self.path, event)
        self.assertEqual(result.status, APPENDED)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_duplicate_import_of_identical_forecast_is_a_safe_no_op(self):
        event1 = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event1)
        # A second, independently-built event from the SAME logical input
        # (identical natural key and payload fields except the
        # recorded_at_utc envelope timestamp, which append_if_new never
        # compares) must be recognized as a duplicate, never double-appended.
        event2 = forecast_ledger.build_recorded_event(**_sample_kwargs())
        result = forecast_ledger.append_recorded(self.path, event2)
        self.assertEqual(result.status, DUPLICATE_SKIPPED)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_conflicting_reimport_under_same_id_is_refused(self):
        event1 = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event1)
        # Same natural key (fixture/market/model_version) but different
        # odds -- this must be flagged as a conflict, never silently
        # overwritten or silently appended as a second RECORDED event.
        event2 = forecast_ledger.build_recorded_event(**_sample_kwargs(offered_odds={"H": 2.0, "D": 3.3, "A": 4.0}))
        result = forecast_ledger.append_recorded(self.path, event2)
        self.assertEqual(result.status, CONFLICT)
        self.assertEqual(len(read_all(self.path)), 1)  # still just the original

    def test_every_forecast_is_recorded_including_never_placed(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event)
        state = forecast_ledger.current_state(self.path, event["forecast_id"])
        self.assertEqual(state["selection_status"], "considered")

    def test_selection_status_updates_are_appended_not_mutated(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event)
        forecast_ledger.append_selection_updated(self.path, event["forecast_id"], "shortlisted")
        forecast_ledger.append_selection_updated(self.path, event["forecast_id"], "placed", operator_decision="STAKED")

        records = read_all(self.path)
        self.assertEqual(len(records), 3)  # RECORDED + 2 SELECTION_UPDATED, never mutated in place
        state = forecast_ledger.current_state(self.path, event["forecast_id"])
        self.assertEqual(state["selection_status"], "placed")
        self.assertEqual(state["operator_decision"], "STAKED")

    def test_score_and_append_computes_brier_and_log_loss(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs(model_probabilities={"H": 1.0, "D": 0.0, "A": 0.0}))
        forecast_ledger.append_recorded(self.path, event)
        scored = forecast_ledger.score_and_append(self.path, event["forecast_id"], "H")
        self.assertAlmostEqual(scored["payload"]["brier_score"], 0.0, places=9)
        self.assertAlmostEqual(scored["payload"]["log_loss"], 0.0, places=6)

        state = forecast_ledger.current_state(self.path, event["forecast_id"])
        self.assertEqual(state["actual_result"], "H")
        self.assertAlmostEqual(state["brier_score"], 0.0, places=9)

    def test_score_and_append_computes_market_comparison_when_closing_odds_given(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event)
        scored = forecast_ledger.score_and_append(self.path, event["forecast_id"], "H", closing_odds={"H": 1.8, "D": 3.5, "A": 4.5})
        comparison = scored["payload"]["market_comparison"]
        self.assertIn("opening_fair_probabilities", comparison)
        self.assertIn("closing_fair_probabilities", comparison)

    def test_score_and_append_without_closing_odds_leaves_comparison_null(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        forecast_ledger.append_recorded(self.path, event)
        scored = forecast_ledger.score_and_append(self.path, event["forecast_id"], "H")
        self.assertIsNone(scored["payload"]["market_comparison"])

    def test_scoring_unknown_forecast_id_raises(self):
        with self.assertRaises(KeyError):
            forecast_ledger.score_and_append(self.path, "fc_doesnotexist0000", "H")

    def test_market_probabilities_computed_at_record_time(self):
        event = forecast_ledger.build_recorded_event(**_sample_kwargs())
        self.assertIn("H", event["payload"]["market_probabilities"])
        total = sum(event["payload"]["market_probabilities"].values())
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_stop_reason_recorded_and_never_fabricates_a_probability(self):
        event = forecast_ledger.build_recorded_event(
            **_sample_kwargs(
                model_probabilities={"H": 0.0, "D": 0.0, "A": 0.0},
                stop_reason="JURISDICTIONAL_STOP",
                selection_status="skipped",
            )
        )
        forecast_ledger.append_recorded(self.path, event)
        state = forecast_ledger.current_state(self.path, event["forecast_id"])
        self.assertEqual(state["stop_reason"], "JURISDICTIONAL_STOP")
        self.assertEqual(state["selection_status"], "skipped")

    def test_batch_and_capture_fields_round_trip(self):
        event = forecast_ledger.build_recorded_event(
            **_sample_kwargs(batch_id="batch-1", capture_id="cap-1", source="manual", captured_at_utc="2024-01-01T09:00:00+00:00")
        )
        forecast_ledger.append_recorded(self.path, event)
        state = forecast_ledger.current_state(self.path, event["forecast_id"])
        self.assertEqual(state["batch_id"], "batch-1")
        self.assertEqual(state["capture_id"], "cap-1")
        self.assertEqual(state["source"], "manual")


if __name__ == "__main__":
    unittest.main()
