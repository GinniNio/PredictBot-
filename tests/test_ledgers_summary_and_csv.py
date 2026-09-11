"""Tests for ledgers/summary.py and ledgers/csv_export.py: the STOP-
exclusion invariant, one summary per batch, and derived CSV views."""

from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import betting_ledger, csv_export, forecast_ledger, summary


def _record(fc_path, fixture_id, batch_id, stop_reason=None, selection_status="considered"):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="soccer",
        league="E0",
        kickoff_utc="2024-01-01T14:00:00+00:00",
        market_type="1X2",
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.2},
        model_probabilities={"H": 0.0, "D": 0.0, "A": 0.0} if stop_reason else {"H": 0.5, "D": 0.3, "A": 0.2},
        model_version="v1",
        artifact_hash="h",
        input_hash="h",
        output_hash="h",
        classification="RESEARCH-MODEL",
        batch_id=batch_id,
        stop_reason=stop_reason,
        selection_status=selection_status,
    )
    forecast_ledger.append_recorded(fc_path, event)
    return event["forecast_id"]


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fc_path = Path(self.tmp) / "forecast-ledger.jsonl"
        self.bet_path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stop_items_structurally_excluded_from_ranked_forecasts(self):
        _record(self.fc_path, "f1", "batch-1")
        _record(self.fc_path, "f2", "batch-1", stop_reason="JURISDICTIONAL_STOP", selection_status="skipped")

        ranked = summary.ranked_forecasts(self.fc_path, batch_id="batch-1")
        fixture_ids = {r["fixture_id"] for r in ranked}
        self.assertIn("f1", fixture_ids)
        self.assertNotIn("f2", fixture_ids)

    def test_one_summary_per_batch_with_correct_counts(self):
        _record(self.fc_path, "f1", "batch-1", selection_status="shortlisted")
        _record(self.fc_path, "f2", "batch-1", stop_reason="JURISDICTIONAL_STOP", selection_status="skipped")
        _record(self.fc_path, "f3", "batch-2")  # different batch, must not leak into batch-1's summary

        result = summary.daily_batch_summary(self.fc_path, self.bet_path, "batch-1")
        self.assertEqual(result["batch_id"], "batch-1")
        self.assertEqual(result["fixtures_captured"], 2)
        self.assertEqual(result["fixtures_rejected"], 1)
        self.assertEqual(result["candidates_reviewed"], 1)  # only f1 (shortlisted, not STOP)
        self.assertEqual(result["records_written"]["forecast_ledger"], 2)

    def test_summary_is_a_single_dict_never_a_list_of_conflicting_versions(self):
        _record(self.fc_path, "f1", "batch-1")
        result = summary.daily_batch_summary(self.fc_path, self.bet_path, "batch-1")
        self.assertIsInstance(result, dict)
        self.assertIn("fixtures_captured", result)


class CsvExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fc_path = Path(self.tmp) / "forecast-ledger.jsonl"
        self.bet_path = Path(self.tmp) / "betting-ledger.jsonl"
        self.out_dir = Path(self.tmp) / "csv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_forecasts_csv_includes_stop_items_ranked_csv_excludes_them(self):
        _record(self.fc_path, "f1", "batch-1")
        _record(self.fc_path, "f2", "batch-1", stop_reason="JURISDICTIONAL_STOP", selection_status="skipped")

        csv_export.export_forecasts_csv(self.fc_path, self.out_dir / "forecasts.csv")
        csv_export.export_ranked_candidates_csv(self.fc_path, self.out_dir / "ranked_candidates.csv")

        with (self.out_dir / "forecasts.csv").open() as f:
            all_rows = list(csv.DictReader(f))
        with (self.out_dir / "ranked_candidates.csv").open() as f:
            ranked_rows = list(csv.DictReader(f))

        self.assertEqual({r["fixture_id"] for r in all_rows}, {"f1", "f2"})
        self.assertEqual({r["fixture_id"] for r in ranked_rows}, {"f1"})

    def test_tickets_csv_and_ticket_legs_csv(self):
        event = betting_ledger.build_placed_event(
            ticket_type="DOUBLE",
            unit_stake=10.0,
            max_return=100.0,
            currency="NGN",
            legs=[
                {"forecast_id": "fc_a", "fixture_id": "f1", "market_type": "1X2", "selection": "H", "placed_odds": 1.9},
                {"forecast_id": "fc_b", "fixture_id": "f2", "market_type": "1X2", "selection": "A", "placed_odds": 2.0},
            ],
        )
        betting_ledger.append_placed(self.bet_path, event)
        betting_ledger.settle_computed(
            self.bet_path, event["ticket_id"], [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}]
        )

        csv_export.export_tickets_csv(self.bet_path, self.out_dir / "tickets.csv")
        csv_export.export_ticket_legs_csv(self.bet_path, self.out_dir / "ticket_legs.csv")

        with (self.out_dir / "tickets.csv").open() as f:
            ticket_rows = list(csv.DictReader(f))
        with (self.out_dir / "ticket_legs.csv").open() as f:
            leg_rows = list(csv.DictReader(f))

        self.assertEqual(len(ticket_rows), 1)
        self.assertEqual(ticket_rows[0]["status"], "SETTLED")
        self.assertEqual(len(leg_rows), 2)
        self.assertEqual({r["outcome"] for r in leg_rows}, {"WON"})

    def test_export_all_writes_every_view(self):
        _record(self.fc_path, "f1", "batch-1")
        counts = csv_export.export_all(self.fc_path, self.bet_path, self.out_dir)
        self.assertEqual(set(counts), {"forecasts.csv", "ranked_candidates.csv", "tickets.csv", "ticket_legs.csv"})
        for filename in counts:
            self.assertTrue((self.out_dir / filename).exists())

    def test_csv_is_a_regenerated_export_never_appended_to(self):
        _record(self.fc_path, "f1", "batch-1")
        csv_export.export_forecasts_csv(self.fc_path, self.out_dir / "forecasts.csv")
        _record(self.fc_path, "f2", "batch-1")
        csv_export.export_forecasts_csv(self.fc_path, self.out_dir / "forecasts.csv")
        with (self.out_dir / "forecasts.csv").open() as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)  # not 1 (from run 1) + 1 (from run 2) = 3 -- full overwrite each time


if __name__ == "__main__":
    unittest.main()
