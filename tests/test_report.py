"""Tests for data_pipeline/report.py's fixture-only feasibility report path
and its LIVE_SOURCE_VALIDATED / FIXTURE_ONLY_VALIDATED labeling."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.report import (
    FIXTURE_ONLY_VALIDATED,
    LIVE_SOURCE_VALIDATED,
    build_entry,
    build_fixture_only_report,
    build_report,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"


class BuildEntryLabelTests(unittest.TestCase):
    def test_rejects_unknown_label(self):
        with self.assertRaises(ValueError):
            build_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv", "SOMETHING_ELSE")

    def test_accepts_both_valid_labels(self):
        for label in (LIVE_SOURCE_VALIDATED, FIXTURE_ONLY_VALIDATED):
            entry = build_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv", label)
            self.assertEqual(entry["source_label"], label)


class FixtureOnlyReportTests(unittest.TestCase):
    def setUp(self):
        self.report = build_fixture_only_report()

    def test_every_entry_is_fixture_only_labeled(self):
        for entry in self.report["entries"]:
            self.assertEqual(entry["source_label"], FIXTURE_ONLY_VALIDATED)

    def test_label_counts_has_zero_live_source_validated(self):
        self.assertEqual(self.report["label_counts"][LIVE_SOURCE_VALIDATED], 0)
        self.assertGreater(self.report["label_counts"][FIXTURE_ONLY_VALIDATED], 0)

    def test_summary_table_carries_the_label_field(self):
        for row in self.report["summary_table"]:
            self.assertIn("source_label", row)
            self.assertEqual(row["source_label"], FIXTURE_ONLY_VALIDATED)


class BuildReportColumnDriftTests(unittest.TestCase):
    def test_drift_detected_between_two_entries_of_same_league(self):
        modern = build_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv", FIXTURE_ONLY_VALIDATED)
        older = build_entry("E0", "Premier League", "2003-04", FIXTURES / "column_drift_2000s_season.csv", FIXTURE_ONLY_VALIDATED)
        report = build_report([older, modern])
        self.assertEqual(len(report["column_drift_findings"]), 1)
        finding = report["column_drift_findings"][0]
        self.assertIn("GBH", finding["columns_dropped"])
        self.assertIn("PSH", finding["columns_added"])


if __name__ == "__main__":
    unittest.main()
