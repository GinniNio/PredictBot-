"""Tests for data_pipeline/validation.py, using the same small hand-crafted
CSV fixtures as tests/test_schema_inspection.py (tests/fixtures/football_data/).
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.validation import RejectionReason, parse_date, result_to_dict, validate_file

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"


class DateParsingTests(unittest.TestCase):
    def test_parses_four_digit_year(self):
        iso, fmt = parse_date("12/08/2023")
        self.assertEqual(iso, "2023-08-12")
        self.assertEqual(fmt, "%d/%m/%y" if False else "%d/%m/%Y")

    def test_parses_two_digit_year_1990s(self):
        iso, fmt = parse_date("14/08/93")
        self.assertEqual(iso, "1993-08-14")
        self.assertEqual(fmt, "%d/%m/%y")

    def test_unparseable_date_returns_none(self):
        iso, fmt = parse_date("not-a-date")
        self.assertIsNone(iso)
        self.assertIsNone(fmt)

    def test_empty_date_returns_none(self):
        iso, fmt = parse_date("")
        self.assertIsNone(iso)
        self.assertIsNone(fmt)


class CleanFileValidationTests(unittest.TestCase):
    def setUp(self):
        self.result = validate_file(FIXTURES / "clean_modern_season.csv")

    def test_all_rows_usable(self):
        self.assertEqual(self.result.total_rows, 5)
        self.assertEqual(self.result.usable_fixtures, 5)
        self.assertEqual(self.result.rejected_fixtures, 0)

    def test_no_rejection_reasons_fired(self):
        for reason, count in self.result.rejection_reason_counts.items():
            self.assertEqual(count, 0, f"unexpected rejection reason fired on a clean file: {reason}")

    def test_date_format_observed_is_four_digit_year(self):
        self.assertEqual(self.result.date_formats_observed, {"%d/%m/%Y": 5})

    def test_odds_availability_flags_pinnacle_and_no_known_capture_timestamp(self):
        pinnacle_rows = [o for o in self.result.odds_availability if o["is_pinnacle"]]
        self.assertTrue(pinnacle_rows)
        for row in self.result.odds_availability:
            self.assertFalse(row["capture_timestamp_known"])


class DuplicateFixtureTests(unittest.TestCase):
    def setUp(self):
        self.result = validate_file(FIXTURES / "duplicate_fixture.csv")

    def test_duplicate_and_conflicting_reasons_fire(self):
        self.assertGreaterEqual(self.result.rejection_reason_counts[RejectionReason.DUPLICATE_FIXTURE], 2)
        self.assertGreaterEqual(self.result.rejection_reason_counts[RejectionReason.CONFLICTING_FIXTURE], 1)

    def test_exact_duplicate_without_score_conflict_is_not_flagged_as_conflicting(self):
        # Rows 0-1 (Arsenal v Nott'm Forest) are an EXACT repeat: flagged
        # DUPLICATE_FIXTURE but not CONFLICTING_FIXTURE.
        row1_issues = {i.reason for i in self.result.row_results[1].issues}
        self.assertIn(RejectionReason.DUPLICATE_FIXTURE, row1_issues)
        self.assertNotIn(RejectionReason.CONFLICTING_FIXTURE, row1_issues)

    def test_duplicate_with_different_score_is_flagged_conflicting_too(self):
        # Rows 2-3 (Brighton v Wolves) repeat with a different scoreline.
        row3_issues = {i.reason for i in self.result.row_results[3].issues}
        self.assertIn(RejectionReason.DUPLICATE_FIXTURE, row3_issues)
        self.assertIn(RejectionReason.CONFLICTING_FIXTURE, row3_issues)


class MissingOddsTests(unittest.TestCase):
    def test_missing_odds_row_flagged(self):
        result = validate_file(FIXTURES / "missing_odds.csv")
        self.assertGreaterEqual(result.rejection_reason_counts[RejectionReason.MISSING_OR_INVALID_ODDS], 1)
        everton_row = result.row_results[1]
        self.assertFalse(everton_row.usable)
        self.assertTrue(any(i.reason == RejectionReason.MISSING_OR_INVALID_ODDS for i in everton_row.issues))


class IncompleteThreeWayPriceTests(unittest.TestCase):
    def test_incomplete_price_rows_flagged(self):
        result = validate_file(FIXTURES / "incomplete_three_way.csv")
        self.assertEqual(result.rejection_reason_counts[RejectionReason.INCOMPLETE_THREE_WAY_PRICE], 2)
        for idx in (0, 2):
            issues = {i.reason for i in result.row_results[idx].issues}
            self.assertIn(RejectionReason.INCOMPLETE_THREE_WAY_PRICE, issues)


class OldDateFormatTests(unittest.TestCase):
    def test_old_file_parses_two_digit_year_format(self):
        result = validate_file(FIXTURES / "old_date_format_season.csv")
        self.assertEqual(result.date_formats_observed, {"%d/%m/%y": 3})
        self.assertEqual(result.usable_fixtures, 3)

    def test_date_format_differs_from_modern_fixture(self):
        old = validate_file(FIXTURES / "old_date_format_season.csv")
        modern = validate_file(FIXTURES / "clean_modern_season.csv")
        self.assertNotEqual(set(old.date_formats_observed), set(modern.date_formats_observed))


class ResultToDictShapeTests(unittest.TestCase):
    def test_result_to_dict_is_json_shaped_and_lists_typed_reasons(self):
        result = validate_file(FIXTURES / "duplicate_fixture.csv")
        d = result_to_dict(result)
        self.assertIn("rejection_reason_counts", d)
        self.assertIn("rejected_rows", d)
        for row in d["rejected_rows"]:
            for issue in row["issues"]:
                self.assertIn(issue["reason"], RejectionReason.ALL)


class EncodingToleranceTests(unittest.TestCase):
    """Same real crash as schema_inspection's equivalent test — validate_file
    has its own file-reading path and needs the same fallback."""

    def test_cp1252_encoded_file_validates_without_crashing(self):
        result = validate_file(FIXTURES / "cp1252_encoded_season.csv")
        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.rejected_fixtures, 0)


if __name__ == "__main__":
    unittest.main()
