"""Tests for data_pipeline/report.py's fixture-only feasibility report path
and its LIVE_SOURCE_VALIDATED / FIXTURE_ONLY_VALIDATED / SOURCE_NOT_USABLE
labeling."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.report import (
    FIXTURE_ONLY_VALIDATED,
    LIVE_SOURCE_VALIDATED,
    SOURCE_NOT_USABLE,
    VALID_SOURCE_LABELS,
    _validate_label,
    build_entry,
    build_fixture_only_report,
    build_live_entry,
    build_report,
    classify_live_download,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"


class BuildEntryLabelTests(unittest.TestCase):
    def test_rejects_unknown_label(self):
        with self.assertRaises(ValueError):
            build_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv", "SOMETHING_ELSE")

    def test_accepts_all_three_valid_labels(self):
        for label in (LIVE_SOURCE_VALIDATED, FIXTURE_ONLY_VALIDATED, SOURCE_NOT_USABLE):
            entry = build_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv", label)
            self.assertEqual(entry["source_label"], label)


class ValidateLabelTests(unittest.TestCase):
    def test_all_three_defined_labels_are_accepted(self):
        self.assertEqual(set(VALID_SOURCE_LABELS), {LIVE_SOURCE_VALIDATED, FIXTURE_ONLY_VALIDATED, SOURCE_NOT_USABLE})
        for label in VALID_SOURCE_LABELS:
            _validate_label(label)  # must not raise

    def test_source_not_usable_is_accepted_even_though_unused_this_run(self):
        # SOURCE_NOT_USABLE is defined for a future run with real
        # downloaded-but-bad data; the validator must accept it today even
        # though no report row is actually assigned it in this run.
        _validate_label(SOURCE_NOT_USABLE)

    def test_a_fourth_invalid_value_still_raises(self):
        with self.assertRaises(ValueError):
            _validate_label("NOT_A_REAL_LABEL")
        with self.assertRaises(ValueError):
            _validate_label("source_not_usable")  # case-sensitive, not a fuzzy match


class ClassifyLiveDownloadTests(unittest.TestCase):
    def test_clean_file_classifies_as_live_source_validated(self):
        label = classify_live_download([], total_rows=5, usable_fixtures=5)
        self.assertEqual(label, LIVE_SOURCE_VALIDATED)

    def test_missing_result_identity_column_classifies_as_source_not_usable(self):
        label = classify_live_download(["full_time_result"], total_rows=10, usable_fixtures=8)
        self.assertEqual(label, SOURCE_NOT_USABLE)

    def test_zero_rows_classifies_as_source_not_usable(self):
        label = classify_live_download([], total_rows=0, usable_fixtures=0)
        self.assertEqual(label, SOURCE_NOT_USABLE)

    def test_every_row_rejected_classifies_as_source_not_usable(self):
        label = classify_live_download([], total_rows=10, usable_fixtures=0)
        self.assertEqual(label, SOURCE_NOT_USABLE)

    def test_build_live_entry_on_a_clean_fixture_lands_on_live_source_validated(self):
        # Stand-in for "a real download succeeded and was clean" — proves
        # build_live_entry's classification path end-to-end.
        entry = build_live_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv")
        self.assertEqual(entry["source_label"], LIVE_SOURCE_VALIDATED)


class FixtureOnlyReportTests(unittest.TestCase):
    def setUp(self):
        self.report = build_fixture_only_report()

    def test_every_entry_is_fixture_only_labeled(self):
        for entry in self.report["entries"]:
            self.assertEqual(entry["source_label"], FIXTURE_ONLY_VALIDATED)

    def test_label_counts_has_zero_live_source_validated_and_zero_source_not_usable(self):
        self.assertEqual(self.report["label_counts"][LIVE_SOURCE_VALIDATED], 0)
        self.assertEqual(self.report["label_counts"][SOURCE_NOT_USABLE], 0)
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
