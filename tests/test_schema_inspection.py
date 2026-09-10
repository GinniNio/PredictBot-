"""Tests for data_pipeline/schema_inspection.py, using small hand-crafted
CSV fixtures under tests/fixtures/football_data/ that mimic Football-Data's
real column layout (never a real, full Football-Data download — see
data_pipeline/FEASIBILITY_DECISION.md for why no such file is checked in).
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.schema_inspection import column_drift, inspect_file, read_csv_rows, result_to_dict

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"


class SchemaInspectionModernFileTests(unittest.TestCase):
    def setUp(self):
        self.result = inspect_file(FIXTURES / "clean_modern_season.csv")

    def test_row_count(self):
        self.assertEqual(self.result.row_count, 5)

    def test_all_core_columns_present(self):
        self.assertEqual(self.result.core_columns_absent, [])
        self.assertEqual(self.result.core_columns_present["home_team"], "HomeTeam")
        self.assertEqual(self.result.core_columns_present["away_team"], "AwayTeam")
        self.assertEqual(self.result.core_columns_present["kickoff_time"], "Time")

    def test_finds_bet365_opening_only_no_closing(self):
        findings = {(f.bookmaker_prefix, f.variant): f for f in self.result.odds_columns_found}
        self.assertIn(("B365", "opening_or_only"), findings)
        self.assertNotIn(("B365", "closing"), findings)
        self.assertTrue(findings[("B365", "opening_or_only")].all_three_present)

    def test_finds_pinnacle_opening_and_closing_as_distinct_fields(self):
        findings = {(f.bookmaker_prefix, f.variant): f for f in self.result.odds_columns_found}
        self.assertIn(("PS", "opening_or_only"), findings)
        self.assertIn(("PS", "closing"), findings)
        opening = findings[("PS", "opening_or_only")]
        closing = findings[("PS", "closing")]
        self.assertTrue(opening.is_pinnacle)
        self.assertTrue(closing.is_pinnacle)
        # Distinct column sets — never merged into one "the odds" value.
        self.assertNotEqual((opening.home_col, opening.draw_col, opening.away_col), (closing.home_col, closing.draw_col, closing.away_col))

    def test_no_odds_column_ever_claims_a_known_capture_timestamp(self):
        # Hard constraint: Football-Data has no captured_at column distinct
        # from the opening/closing label itself.
        for finding in self.result.odds_columns_found:
            self.assertFalse(finding.capture_timestamp_known)

    def test_result_to_dict_is_json_shaped(self):
        d = result_to_dict(self.result)
        self.assertIn("odds_columns_found", d)
        self.assertIsInstance(d["odds_columns_found"], list)
        self.assertIn("capture_timestamp_known", d["odds_columns_found"][0])


class SchemaInspectionEncodingToleranceTests(unittest.TestCase):
    """Reproduces a real crash found running this pipeline against actual
    Football-Data content on GitHub Actions (the sandbox proxy blocks the
    live download, but a GitHub-hosted runner does not): some files are not
    valid UTF-8 (e.g. an accented character in a team name, encoded
    Windows-1252/Latin-1). inspect_file must fall back and still parse the
    file, not crash with UnicodeDecodeError."""

    def test_cp1252_encoded_file_is_parsed_without_crashing(self):
        result = inspect_file(FIXTURES / "cp1252_encoded_season.csv")
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.core_columns_present["home_team"], "HomeTeam")

    def test_cp1252_encoded_accented_team_name_is_preserved(self):
        # Confirms the fallback decode is actually cp1252, not e.g. errors=
        # "replace" silently mangling the character into a placeholder.
        rows = read_csv_rows(FIXTURES / "cp1252_encoded_season.csv")
        away_teams = [row[4] for row in rows[1:]]
        self.assertIn("Métz", away_teams)


class SchemaInspectionOldFileTests(unittest.TestCase):
    def setUp(self):
        self.result = inspect_file(FIXTURES / "old_date_format_season.csv")

    def test_missing_columns_the_modern_file_has(self):
        self.assertIn("kickoff_time", self.result.core_columns_absent)
        self.assertIn("half_time_result", self.result.core_columns_absent)

    def test_no_odds_columns_at_all(self):
        self.assertEqual(self.result.odds_columns_found, [])


class ColumnDriftTests(unittest.TestCase):
    def test_drift_between_modern_and_2000s_season(self):
        modern = inspect_file(FIXTURES / "clean_modern_season.csv")
        older = inspect_file(FIXTURES / "column_drift_2000s_season.csv")
        drift = column_drift(modern.header, older.header)
        # The older file has bookmaker columns the modern one dropped...
        self.assertIn("GBH", drift["only_in_b"])
        self.assertIn("SJH", drift["only_in_b"])
        # ...and the modern file has Pinnacle + closing-line columns the
        # older one never had.
        self.assertIn("PSH", drift["only_in_a"])
        self.assertIn("PSCH", drift["only_in_a"])
        self.assertIn("Time", drift["only_in_a"])
        # Core columns present in both.
        self.assertIn("HomeTeam", drift["in_both"])
        self.assertIn("FTR", drift["in_both"])

    def test_drift_between_modern_and_earliest_season(self):
        modern = inspect_file(FIXTURES / "clean_modern_season.csv")
        earliest = inspect_file(FIXTURES / "old_date_format_season.csv")
        drift = column_drift(modern.header, earliest.header)
        self.assertIn("B365H", drift["only_in_a"])
        self.assertEqual(drift["only_in_b"], [])  # earliest file's columns are a strict subset


if __name__ == "__main__":
    unittest.main()
