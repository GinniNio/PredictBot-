"""Tests for ledgers/ids.py and ledgers/validation.py -- stable ID
generation and schema conformance, independent of either ledger's own
append/scoring logic (covered in test_ledgers_forecast.py /
test_ledgers_betting.py)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import ids, validation


class ForecastIdTests(unittest.TestCase):
    def test_same_natural_key_always_produces_same_id(self):
        a = ids.forecast_id("fixture-1", "1X2", "model-v1")
        b = ids.forecast_id("fixture-1", "1X2", "model-v1")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("fc_"))

    def test_different_natural_key_produces_different_id(self):
        a = ids.forecast_id("fixture-1", "1X2", "model-v1")
        b = ids.forecast_id("fixture-2", "1X2", "model-v1")
        c = ids.forecast_id("fixture-1", "1X2", "model-v2")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_id_never_depends_on_created_at(self):
        # forecast_id() doesn't even accept created_at -- this test
        # documents that guarantee at the call-site level a caller would
        # actually hit.
        first = ids.forecast_id("fixture-1", "1X2", "model-v1")
        second = ids.forecast_id("fixture-1", "1X2", "model-v1")
        self.assertEqual(first, second)


class TicketIdTests(unittest.TestCase):
    def test_same_external_ref_always_produces_same_id(self):
        a = ids.ticket_id_from_external_ref("slip-123")
        b = ids.ticket_id_from_external_ref("slip-123")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("tk_"))

    def test_content_hash_stable_for_identical_legs(self):
        legs = [{"forecast_id": "fc_a", "fixture_id": "f1", "market_type": "1X2", "selection": "H", "placed_odds": 1.9}]
        a = ids.ticket_id_from_content("2024-01-01T00:00:00+00:00", "SINGLE", 10.0, legs)
        b = ids.ticket_id_from_content("2024-01-01T00:00:00+00:00", "SINGLE", 10.0, legs)
        self.assertEqual(a, b)

    def test_content_hash_changes_if_a_leg_changes(self):
        legs_a = [{"forecast_id": "fc_a", "fixture_id": "f1", "market_type": "1X2", "selection": "H", "placed_odds": 1.9}]
        legs_b = [{"forecast_id": "fc_a", "fixture_id": "f1", "market_type": "1X2", "selection": "H", "placed_odds": 2.1}]
        a = ids.ticket_id_from_content("2024-01-01T00:00:00+00:00", "SINGLE", 10.0, legs_a)
        b = ids.ticket_id_from_content("2024-01-01T00:00:00+00:00", "SINGLE", 10.0, legs_b)
        self.assertNotEqual(a, b)


class ValidationTests(unittest.TestCase):
    def test_valid_forecast_recorded_record_passes(self):
        record = {
            "schema_version": "1.0.0",
            "event_type": "RECORDED",
            "forecast_id": "fc_" + "0" * 16,
            "recorded_at_utc": "2024-01-01T00:00:00+00:00",
            "payload": {
                "created_at_utc": "2024-01-01T00:00:00+00:00",
                "fixture_id": "f1",
                "sport": "soccer",
                "league": "E0",
                "kickoff_utc": "2024-01-01T14:00:00+00:00",
                "market_type": "1X2",
                "offered_odds": {"H": 1.9, "D": 3.4, "A": 4.2},
                "model_probabilities": {"H": 0.5, "D": 0.3, "A": 0.2},
                "model_version": "v1",
                "artifact_hash": "h",
                "input_hash": "h",
                "output_hash": "h",
                "classification": "RESEARCH-MODEL",
            },
        }
        errors = validation.validate_envelope(record, validation.load_schema("forecast_ledger.v1"))
        self.assertEqual(errors, [])

    def test_missing_required_field_is_reported(self):
        record = {
            "schema_version": "1.0.0",
            "event_type": "RECORDED",
            "forecast_id": "fc_" + "0" * 16,
            "recorded_at_utc": "2024-01-01T00:00:00+00:00",
            "payload": {"fixture_id": "f1"},  # missing almost everything
        }
        errors = validation.validate_envelope(record, validation.load_schema("forecast_ledger.v1"))
        self.assertTrue(any("classification" in e for e in errors))
        self.assertTrue(any("market_type" in e for e in errors))

    def test_wrong_type_is_reported(self):
        record = {
            "schema_version": "1.0.0",
            "event_type": "RECORDED",
            "forecast_id": "fc_" + "0" * 16,
            "recorded_at_utc": "2024-01-01T00:00:00+00:00",
            "payload": {
                "created_at_utc": "2024-01-01T00:00:00+00:00",
                "fixture_id": "f1",
                "sport": "soccer",
                "league": "E0",
                "kickoff_utc": "2024-01-01T14:00:00+00:00",
                "market_type": "1X2",
                "offered_odds": {"H": "not-a-number", "D": 3.4, "A": 4.2},
                "model_probabilities": {"H": 0.5, "D": 0.3, "A": 0.2},
                "model_version": "v1",
                "artifact_hash": "h",
                "input_hash": "h",
                "output_hash": "h",
                "classification": "RESEARCH-MODEL",
            },
        }
        errors = validation.validate_envelope(record, validation.load_schema("forecast_ledger.v1"))
        self.assertTrue(any("offered_odds.H" in e for e in errors))

    def test_unrecognized_event_type_is_reported(self):
        record = {
            "schema_version": "1.0.0",
            "event_type": "NOT_A_REAL_EVENT",
            "forecast_id": "fc_" + "0" * 16,
            "recorded_at_utc": "2024-01-01T00:00:00+00:00",
            "payload": {},
        }
        errors = validation.validate_envelope(record, validation.load_schema("forecast_ledger.v1"))
        self.assertTrue(any("event_type" in e for e in errors))

    def test_validate_jsonl_file_reports_line_numbers(self):
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "forecast-ledger.jsonl"
            path.write_text(
                '{"schema_version": "1.0.0", "event_type": "RECORDED", "forecast_id": "fc_'
                + "0" * 16
                + '", "recorded_at_utc": "2024-01-01T00:00:00+00:00", "payload": {}}\n'
                "not valid json at all\n",
                encoding="utf-8",
            )
            errors = validation.validate_jsonl_file(path, "forecast_ledger.v1")
            self.assertTrue(any(e.startswith("line 1:") for e in errors))
            self.assertTrue(any(e.startswith("line 2:") for e in errors))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_shipped_examples_validate_against_the_forecast_schema(self):
        # The committed example JSON files (kwargs for build_recorded_event,
        # not raw envelopes) must at least produce valid envelopes once run
        # through the builder -- guards against the examples silently
        # drifting out of sync with the schema.
        import json

        from ledgers import forecast_ledger

        examples_dir = REPO_ROOT / "ledgers" / "examples"
        for filename in ("forecast_example.json", "forecast_example_stop.json"):
            data = json.loads((examples_dir / filename).read_text(encoding="utf-8"))
            event = forecast_ledger.build_recorded_event(**data)
            errors = validation.validate_envelope(event, validation.load_schema("forecast_ledger.v1"))
            self.assertEqual(errors, [], f"{filename}: {errors}")


class GitignoreTests(unittest.TestCase):
    """Contract: operational files stay outside Git -- schemas, examples,
    and tooling are committed; real *.jsonl ledger data and generated CSV
    exports are not, regardless of which directory an operator points
    --dir at."""

    def _is_ignored(self, relative_path: str) -> bool:
        import subprocess

        result = subprocess.run(
            ["git", "check-ignore", "--quiet", relative_path], cwd=REPO_ROOT, capture_output=True
        )
        return result.returncode == 0

    def test_any_jsonl_file_anywhere_is_gitignored(self):
        self.assertTrue(self._is_ignored("ledger_data/forecast-ledger.jsonl"))
        self.assertTrue(self._is_ignored("some/other/path/betting-ledger.jsonl"))

    def test_default_csv_export_directory_is_gitignored(self):
        self.assertTrue(self._is_ignored("ledger_data/csv/forecasts.csv"))

    def test_committed_schemas_and_examples_are_never_ignored(self):
        self.assertFalse(self._is_ignored("ledgers/schemas/forecast_ledger.v1.schema.json"))
        self.assertFalse(self._is_ignored("ledgers/schemas/betting_ledger.v1.schema.json"))
        self.assertFalse(self._is_ignored("ledgers/examples/forecast_example.json"))
        self.assertFalse(self._is_ignored("ledgers/examples/ticket_example.json"))

    def test_committed_tooling_modules_are_never_ignored(self):
        for module in ("forecast_ledger.py", "betting_ledger.py", "money.py", "storage.py", "cli.py"):
            self.assertFalse(self._is_ignored(f"ledgers/{module}"))

    def test_existing_committed_csv_fixtures_are_not_caught_by_a_blanket_rule(self):
        # Guards against a future edit tightening the CSV ignore rule to
        # a blanket *.csv, which would silently stop tracking this
        # repo's real, already-committed CSV test fixtures.
        self.assertFalse(self._is_ignored("tests/fixtures/football_data/season_1920_with_kickoff.csv"))


if __name__ == "__main__":
    unittest.main()
