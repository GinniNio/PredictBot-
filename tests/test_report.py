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
    SOURCE_NOT_LISTED,
    SOURCE_NOT_USABLE,
    TEST_FIXTURE_IDENTITY,
    VALID_SOURCE_ATTEMPT_LABELS,
    VALID_SOURCE_LABELS,
    _repo_relative_path,
    _validate_label,
    build_entry,
    build_fixture_only_report,
    build_live_entry,
    build_report,
    build_source_attempts,
    classify_live_download,
    summarize_source_attempts,
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

    def test_every_entry_uses_the_neutral_test_fixture_identity_never_a_real_league_name(self):
        # Defect 2: the fixture entries must never carry a real-looking
        # league identity (e.g. "English Premier League (fixture proxy)").
        for entry in self.report["entries"]:
            self.assertEqual(entry["league_code"], TEST_FIXTURE_IDENTITY)
            self.assertEqual(entry["league_name"], TEST_FIXTURE_IDENTITY)
        for row in self.report["summary_table"]:
            self.assertEqual(row["league_code"], TEST_FIXTURE_IDENTITY)
            self.assertEqual(row["league_name"], TEST_FIXTURE_IDENTITY)

    def test_every_entry_file_path_is_repo_relative_never_absolute(self):
        # Defect 3: no machine-specific absolute path in report content.
        for entry in self.report["entries"]:
            self.assertFalse(Path(entry["file_path"]).is_absolute())
            self.assertNotIn(str(REPO_ROOT), entry["file_path"])


class RepoRelativePathTests(unittest.TestCase):
    def test_path_inside_repo_is_rendered_relative(self):
        result = _repo_relative_path(FIXTURES / "clean_modern_season.csv")
        self.assertEqual(result, "tests/fixtures/football_data/clean_modern_season.csv")
        self.assertFalse(Path(result).is_absolute())

    def test_path_outside_repo_falls_back_without_raising(self):
        result = _repo_relative_path(Path("/definitely/outside/repo/file.csv"))
        self.assertEqual(result, "/definitely/outside/repo/file.csv")


class BuildSourceAttemptsTests(unittest.TestCase):
    MANIFEST_ROWS = [
        {
            "league_code": "E0",
            "league_name": "English Premier League",
            "first_season": "9394",
            "latest_completed_season": "9495",
            "unconfirmed_seasons": ["2425", "2526"],
        },
    ]

    def test_one_row_per_manifest_season_plus_gap_seasons(self):
        retrieval_log = {
            "downloads": [],
            "failed_attempts": [
                {"league_code": "E0", "season_code": "9394", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "9495", "error_type": "NOT_ATTEMPTED_HOST_BLOCKED"},
            ],
        }
        rows = build_source_attempts(self.MANIFEST_ROWS, retrieval_log)
        # 2 confirmed-range seasons + 2 unconfirmed gap seasons = 4.
        self.assertEqual(len(rows), 4)
        by_season = {r["season"]: r for r in rows}

        self.assertEqual(by_season["9394"]["download_status"], "CONNECTION_ERROR")
        self.assertEqual(by_season["9394"]["source_label"], FIXTURE_ONLY_VALIDATED)
        self.assertEqual(by_season["9394"]["validation_status"], "NOT_RUN")
        self.assertIsNone(by_season["9394"]["total_rows"])
        self.assertIsNone(by_season["9394"]["usable_fixtures"])

        self.assertEqual(by_season["9495"]["download_status"], "NOT_ATTEMPTED_HOST_BLOCKED")
        self.assertEqual(by_season["9495"]["source_label"], FIXTURE_ONLY_VALIDATED)

        self.assertEqual(by_season["2425"]["download_status"], SOURCE_NOT_LISTED)
        self.assertEqual(by_season["2425"]["source_label"], SOURCE_NOT_LISTED)
        self.assertIsNone(by_season["2425"]["total_rows"])
        self.assertEqual(by_season["2526"]["source_label"], SOURCE_NOT_LISTED)

        for row in rows:
            self.assertIn(row["source_label"], VALID_SOURCE_ATTEMPT_LABELS)

    def test_never_borrows_numbers_from_a_synthetic_fixture(self):
        # The core bug this defect fixes: a real league-season row with no
        # real downloaded file must never carry fixture row/fixture counts.
        retrieval_log = {
            "downloads": [],
            "failed_attempts": [
                {"league_code": "E0", "season_code": "9394", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "9495", "error_type": "CONNECTION_ERROR"},
            ],
        }
        rows = build_source_attempts(self.MANIFEST_ROWS, retrieval_log)
        for row in rows:
            if row["download_status"] != "SUCCESS":
                self.assertIsNone(row["total_rows"])
                self.assertIsNone(row["usable_fixtures"])

    def test_real_retrieval_log_produces_155_confirmed_range_rows(self):
        retrieval_log_path = REPO_ROOT / "data_pipeline" / "retrieval_log.json"
        if not retrieval_log_path.exists():
            self.skipTest("no retrieval_log.json present in this checkout")
        import json as _json

        retrieval_log = _json.loads(retrieval_log_path.read_text(encoding="utf-8"))
        from data_pipeline.download import load_manifest, DEFAULT_MANIFEST_PATH

        manifest_rows = load_manifest(DEFAULT_MANIFEST_PATH)
        rows = build_source_attempts(manifest_rows, retrieval_log)
        confirmed_range_rows = [r for r in rows if r["source_label"] != SOURCE_NOT_LISTED]
        gap_rows = [r for r in rows if r["source_label"] == SOURCE_NOT_LISTED]
        self.assertEqual(len(confirmed_range_rows), 155)
        self.assertEqual(len(gap_rows), 10)  # 5 leagues x 2 unconfirmed seasons
        for row in confirmed_range_rows:
            if row["download_status"] not in ("SUCCESS",):
                self.assertIsNone(row["total_rows"])
                self.assertIsNone(row["usable_fixtures"])


class SummarizeSourceAttemptsTests(unittest.TestCase):
    def test_counts_by_download_status_and_source_label(self):
        rows = [
            {"download_status": "CONNECTION_ERROR", "source_label": FIXTURE_ONLY_VALIDATED},
            {"download_status": "CONNECTION_ERROR", "source_label": FIXTURE_ONLY_VALIDATED},
            {"download_status": SOURCE_NOT_LISTED, "source_label": SOURCE_NOT_LISTED},
        ]
        summary = summarize_source_attempts(rows)
        self.assertEqual(summary["total_rows"], 3)
        self.assertEqual(summary["download_status_counts"]["CONNECTION_ERROR"], 2)
        self.assertEqual(summary["source_label_counts"][FIXTURE_ONLY_VALIDATED], 2)
        self.assertEqual(summary["source_label_counts"][SOURCE_NOT_LISTED], 1)


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
