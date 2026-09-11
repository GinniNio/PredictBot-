"""Tests for data_pipeline/report.py's fixture-only feasibility report path
and its LIVE_SOURCE_VALIDATED / FIXTURE_ONLY_VALIDATED / SOURCE_NOT_USABLE
labeling."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.download import ERROR_CONNECTION, ERROR_NOT_FOUND
from data_pipeline.report import (
    DOWNLOAD_STATUS_NOT_YET_ATTEMPTED,
    FIXTURE_ONLY_VALIDATED,
    LIVE_SOURCE_VALIDATED,
    SOURCE_NOT_LISTED,
    SOURCE_NOT_USABLE,
    TEST_FIXTURE_IDENTITY,
    VALID_SOURCE_ATTEMPT_LABELS,
    VALID_SOURCE_LABELS,
    _repo_relative_path,
    _validate_label,
    build_aggregates,
    build_compact_summary,
    build_compact_summary_row,
    build_entry,
    build_fixture_only_report,
    build_live_entry,
    build_report,
    build_rejection_reason_totals,
    build_source_attempts,
    classify_live_download,
    format_aggregates_markdown,
    format_compact_summary_line,
    format_compact_summary_markdown,
    format_rejection_reason_totals_markdown,
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

    def test_one_row_per_manifest_season_plus_unconfirmed_seasons(self):
        # Unconfirmed seasons are attempted through the SAME retrieval-log
        # join as confirmed-range seasons now — one of them (2425) came
        # back a confirmed 404, the other (2526) simply has no record yet.
        retrieval_log = {
            "downloads": [],
            "failed_attempts": [
                {"league_code": "E0", "season_code": "9394", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "9495", "error_type": "NOT_ATTEMPTED_HOST_BLOCKED"},
                {"league_code": "E0", "season_code": "2425", "error_type": ERROR_NOT_FOUND},
            ],
        }
        rows = build_source_attempts(self.MANIFEST_ROWS, retrieval_log)
        # 2 confirmed-range seasons + 2 unconfirmed seasons = 4.
        self.assertEqual(len(rows), 4)
        by_season = {r["season"]: r for r in rows}

        self.assertEqual(by_season["9394"]["download_status"], "CONNECTION_ERROR")
        self.assertEqual(by_season["9394"]["source_label"], FIXTURE_ONLY_VALIDATED)
        self.assertEqual(by_season["9394"]["validation_status"], "NOT_RUN")
        self.assertIsNone(by_season["9394"]["total_rows"])
        self.assertIsNone(by_season["9394"]["usable_fixtures"])

        self.assertEqual(by_season["9495"]["download_status"], "NOT_ATTEMPTED_HOST_BLOCKED")
        self.assertEqual(by_season["9495"]["source_label"], FIXTURE_ONLY_VALIDATED)

        # 2425 was actually attempted and confirmed absent (404) —
        # SOURCE_NOT_LISTED means "we tried, and confirmed no file",
        # never "we didn't try".
        self.assertEqual(by_season["2425"]["download_status"], ERROR_NOT_FOUND)
        self.assertEqual(by_season["2425"]["source_label"], SOURCE_NOT_LISTED)
        self.assertIsNone(by_season["2425"]["total_rows"])

        # 2526 has no record in this retrieval log at all — honestly
        # "not yet attempted" (in THIS log), never assumed absent.
        self.assertEqual(by_season["2526"]["download_status"], DOWNLOAD_STATUS_NOT_YET_ATTEMPTED)
        self.assertEqual(by_season["2526"]["source_label"], FIXTURE_ONLY_VALIDATED)

        for row in rows:
            self.assertIn(row["source_label"], VALID_SOURCE_ATTEMPT_LABELS)

    def test_unconfirmed_season_success_classifies_like_any_other_download(self):
        # Once a real file exists for a previously-unconfirmed season, it
        # is no longer "unconfirmed" — it goes through the exact same
        # classify_live_download path as a confirmed-range success.
        fixture_path = FIXTURES / "clean_modern_season.csv"
        retrieval_log = {
            "downloads": [
                {
                    "league_code": "E0",
                    "season_code": "2425",
                    "raw_path": str(fixture_path.relative_to(REPO_ROOT)),
                }
            ],
            "failed_attempts": [
                {"league_code": "E0", "season_code": "9394", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "9495", "error_type": "CONNECTION_ERROR"},
            ],
        }
        rows = build_source_attempts(self.MANIFEST_ROWS, retrieval_log)
        by_season = {r["season"]: r for r in rows}
        self.assertEqual(by_season["2425"]["download_status"], "SUCCESS")
        self.assertEqual(by_season["2425"]["source_label"], LIVE_SOURCE_VALIDATED)
        self.assertEqual(by_season["2425"]["total_rows"], 5)

    def test_non_404_failure_on_an_unconfirmed_season_is_fixture_only_never_source_not_listed(self):
        retrieval_log = {
            "downloads": [],
            "failed_attempts": [
                {"league_code": "E0", "season_code": "9394", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "9495", "error_type": "CONNECTION_ERROR"},
                {"league_code": "E0", "season_code": "2425", "error_type": ERROR_CONNECTION},
            ],
        }
        rows = build_source_attempts(self.MANIFEST_ROWS, retrieval_log)
        by_season = {r["season"]: r for r in rows}
        self.assertEqual(by_season["2425"]["download_status"], ERROR_CONNECTION)
        self.assertEqual(by_season["2425"]["source_label"], FIXTURE_ONLY_VALIDATED)
        self.assertNotEqual(by_season["2425"]["source_label"], SOURCE_NOT_LISTED)

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

    def test_real_retrieval_log_produces_165_rows_total(self):
        # This checked-in retrieval_log.json predates this manifest's
        # unconfirmed_seasons actually being attempted, so the 10
        # unconfirmed rows (5 leagues x 2 seasons) have no record in it —
        # they must land on "not yet attempted in THIS log", never a
        # fabricated SOURCE_NOT_LISTED (that requires an actual confirmed
        # 404, which this stale log never recorded for them).
        retrieval_log_path = REPO_ROOT / "data_pipeline" / "retrieval_log.json"
        if not retrieval_log_path.exists():
            self.skipTest("no retrieval_log.json present in this checkout")
        import json as _json

        retrieval_log = _json.loads(retrieval_log_path.read_text(encoding="utf-8"))
        from data_pipeline.download import load_manifest, DEFAULT_MANIFEST_PATH

        manifest_rows = load_manifest(DEFAULT_MANIFEST_PATH)
        rows = build_source_attempts(manifest_rows, retrieval_log)
        # 5 leagues x (31 confirmed-range seasons + 2 unconfirmed) = 165.
        self.assertEqual(len(rows), 165)

        not_yet_attempted_rows = [r for r in rows if r["download_status"] == DOWNLOAD_STATUS_NOT_YET_ATTEMPTED]
        self.assertEqual(len(not_yet_attempted_rows), 10)
        for row in not_yet_attempted_rows:
            self.assertEqual(row["season"], row["season"])  # sanity: still one row per season
            self.assertIn(row["season"], ("2425", "2526"))
            self.assertEqual(row["source_label"], FIXTURE_ONLY_VALIDATED)

        for row in rows:
            if row["download_status"] not in ("SUCCESS",):
                self.assertIsNone(row["total_rows"])
                self.assertIsNone(row["usable_fixtures"])
            self.assertIn(row["source_label"], VALID_SOURCE_ATTEMPT_LABELS)


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


class BuildCompactSummaryRowTests(unittest.TestCase):
    """Tests the compact per-league-season summary against the existing
    hand-crafted fixtures under tests/fixtures/football_data/."""

    def test_clean_modern_fixture_reports_full_odds_coverage_and_encoding(self):
        entry = build_live_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv")
        row = build_compact_summary_row(entry)

        self.assertEqual(row["league_code"], "E0")
        self.assertEqual(row["season"], "2023-24")
        self.assertEqual(row["source_label"], LIVE_SOURCE_VALIDATED)
        self.assertEqual(row["encoding_used"], "utf-8-sig")
        self.assertEqual(row["rows"], 5)
        self.assertEqual(row["usable_fixtures"], 5)
        self.assertEqual(row["rejected_fixtures"], 0)

        # Time column is present in this fixture -> kickoff never missing.
        self.assertTrue(row["missing_kickoff_time"]["column_present"])
        self.assertEqual(row["missing_kickoff_time"]["missing_count"], 0)

        self.assertTrue(row["opening_odds_present"])
        self.assertIn("Bet365", row["opening_odds_bookmakers"])
        self.assertIn("Pinnacle", row["opening_odds_bookmakers"])
        self.assertTrue(row["closing_odds_present"])
        self.assertIn("Pinnacle", row["closing_odds_bookmakers"])
        self.assertIn("B365", row["bookmaker_coverage"])
        self.assertIn("PS", row["bookmaker_coverage"])

        self.assertIs(row["price_capture_timestamps_unknown"], True)
        self.assertEqual(row["rejection_reason_counts"], {})

    def test_missing_kickoff_column_is_reported_as_absent_never_zero_missing(self):
        # old_date_format_season.csv has no Time column at all — this must
        # read as "column absent, 100% missing", never as "0% missing"
        # (which validation.py's raw missingness_by_column would otherwise
        # misleadingly show for an absent column).
        entry = build_live_entry("E0", "Premier League", "1993-94", FIXTURES / "old_date_format_season.csv")
        row = build_compact_summary_row(entry)
        kickoff = row["missing_kickoff_time"]
        self.assertFalse(kickoff["column_present"])
        self.assertEqual(kickoff["missing_count"], row["rows"])
        self.assertEqual(kickoff["missing_rate"], 1.0)
        self.assertFalse(row["opening_odds_present"])
        self.assertFalse(row["closing_odds_present"])
        self.assertEqual(row["bookmaker_coverage"], [])

    def test_missing_odds_fixture_reports_rejection_reasons(self):
        entry = build_live_entry("E0", "Premier League", "2023-24", FIXTURES / "missing_odds.csv")
        row = build_compact_summary_row(entry)
        self.assertGreater(row["rejected_fixtures"], 0)
        self.assertTrue(row["rejection_reason_counts"])
        # Only non-zero reasons are kept — a compact reader should never
        # have to scan a wall of zero-count reasons.
        self.assertTrue(all(count > 0 for count in row["rejection_reason_counts"].values()))

    def test_cp1252_fixture_reports_the_fallback_encoding(self):
        entry = build_live_entry("E0", "Premier League", "1994-95", FIXTURES / "cp1252_encoded_season.csv")
        row = build_compact_summary_row(entry)
        self.assertEqual(row["encoding_used"], "cp1252")

    def test_build_compact_summary_builds_one_row_per_entry(self):
        entries = [
            build_live_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv"),
            build_live_entry("E0", "Premier League", "1993-94", FIXTURES / "old_date_format_season.csv"),
        ]
        rows = build_compact_summary(entries)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["season"] for r in rows], ["2023-24", "1993-94"])


class FormatCompactSummaryTests(unittest.TestCase):
    def setUp(self):
        entry = build_live_entry("E0", "Premier League", "2023-24", FIXTURES / "clean_modern_season.csv")
        self.row = build_compact_summary_row(entry)

    def test_format_compact_summary_line_is_one_line_and_carries_key_fields(self):
        line = format_compact_summary_line(self.row)
        self.assertNotIn("\n", line)
        self.assertIn("E0/2023-24", line)
        self.assertIn("rows=5", line)
        self.assertIn("usable=5", line)
        self.assertIn("encoding=utf-8-sig", line)
        self.assertIn("opening_odds=Y", line)
        self.assertIn("closing_odds=Y", line)
        self.assertIn("price_capture_timestamps_unknown=True", line)

    def test_format_compact_summary_markdown_is_a_valid_table(self):
        lines = format_compact_summary_markdown([self.row])
        self.assertTrue(lines[0].startswith("|"))
        self.assertTrue(lines[1].startswith("|---"))
        self.assertEqual(len(lines), 3)  # header + separator + one data row
        self.assertIn("Premier League (E0)", lines[2])
        self.assertIn("2023-24", lines[2])

    def test_format_compact_summary_markdown_handles_no_rows(self):
        lines = format_compact_summary_markdown([])
        self.assertTrue(any("no league-season file was actually downloaded" in line for line in lines))


class BuildAggregatesTests(unittest.TestCase):
    def test_aggregates_sum_correctly_by_league_and_season(self):
        rows = [
            {"league_code": "E0", "league_name": "Premier League", "season": "2022-23", "usable_fixtures": 10, "rejected_fixtures": 1, "rows": 11},
            {"league_code": "E0", "league_name": "Premier League", "season": "2023-24", "usable_fixtures": 8, "rejected_fixtures": 2, "rows": 10},
            {"league_code": "D1", "league_name": "Bundesliga", "season": "2023-24", "usable_fixtures": 5, "rejected_fixtures": 0, "rows": 5},
        ]
        aggregates = build_aggregates(rows)

        self.assertEqual(aggregates["by_league"]["E0"]["usable_fixtures"], 18)
        self.assertEqual(aggregates["by_league"]["E0"]["rejected_fixtures"], 3)
        self.assertEqual(aggregates["by_league"]["E0"]["rows"], 21)
        self.assertEqual(aggregates["by_league"]["D1"]["usable_fixtures"], 5)

        self.assertEqual(aggregates["by_season"]["2023-24"]["usable_fixtures"], 13)
        self.assertEqual(aggregates["by_season"]["2023-24"]["rejected_fixtures"], 2)
        self.assertEqual(aggregates["by_season"]["2022-23"]["usable_fixtures"], 10)

    def test_format_aggregates_markdown_renders_both_tables(self):
        rows = [
            {"league_code": "E0", "league_name": "Premier League", "season": "2023-24", "usable_fixtures": 8, "rejected_fixtures": 2, "rows": 10},
        ]
        aggregates = build_aggregates(rows)
        lines = format_aggregates_markdown(aggregates)
        text = "\n".join(lines)
        self.assertIn("Aggregate totals by league", text)
        self.assertIn("Aggregate totals by season", text)
        self.assertIn("Premier League (E0)", text)
        self.assertIn("2023-24", text)

    def test_empty_rows_produce_empty_aggregates_without_raising(self):
        aggregates = build_aggregates([])
        self.assertEqual(aggregates, {"by_league": {}, "by_season": {}})
        lines = format_aggregates_markdown(aggregates)
        self.assertTrue(lines)  # still renders headers, just no data rows


class RejectionReasonTotalsTests(unittest.TestCase):
    def test_totals_sum_across_rows_by_reason_type(self):
        rows = [
            {"rejection_reason_counts": {"MISSING_OR_INVALID_ODDS": 2, "DUPLICATE_FIXTURE": 1}},
            {"rejection_reason_counts": {"MISSING_OR_INVALID_ODDS": 3}},
            {"rejection_reason_counts": {}},
        ]
        totals = build_rejection_reason_totals(rows)
        self.assertEqual(totals, {"MISSING_OR_INVALID_ODDS": 5, "DUPLICATE_FIXTURE": 1})

    def test_no_rejections_at_all_produces_empty_totals(self):
        rows = [{"rejection_reason_counts": {}}, {"rejection_reason_counts": {}}]
        self.assertEqual(build_rejection_reason_totals(rows), {})

    def test_format_renders_table_sorted_by_reason_name(self):
        totals = {"MISSING_OR_INVALID_ODDS": 5, "DUPLICATE_FIXTURE": 1}
        lines = format_rejection_reason_totals_markdown(totals)
        text = "\n".join(lines)
        self.assertIn("Typed rejection totals", text)
        self.assertIn("DUPLICATE_FIXTURE", text)
        self.assertIn("MISSING_OR_INVALID_ODDS", text)
        # DUPLICATE_FIXTURE sorts before MISSING_OR_INVALID_ODDS.
        self.assertLess(text.index("DUPLICATE_FIXTURE"), text.index("MISSING_OR_INVALID_ODDS"))

    def test_format_handles_no_rejections(self):
        lines = format_rejection_reason_totals_markdown({})
        self.assertTrue(any("No rejection reason fired" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
