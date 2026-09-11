"""End-to-end CLI tests: init -> record-forecast -> place-ticket ->
record-result -> settle-ticket -> export-csv -> summary, plus duplicate
re-import via the CLI (not just the underlying library functions)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import cli
from ledgers.storage import read_all


class CliEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = Path(self.tmp) / "ledger_data"
        self.forecast_input = Path(self.tmp) / "forecast.json"
        self.forecast_input.write_text(
            json.dumps(
                {
                    "fixture_id": "f1",
                    "sport": "soccer",
                    "league": "E0",
                    "kickoff_utc": "2024-01-01T14:00:00+00:00",
                    "market_type": "1X2",
                    "offered_odds": {"H": 1.9, "D": 3.4, "A": 4.2},
                    "model_probabilities": {"H": 0.6, "D": 0.25, "A": 0.15},
                    "model_version": "v1",
                    "artifact_hash": "h",
                    "input_hash": "h",
                    "output_hash": "h",
                    "classification": "RESEARCH-MODEL",
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_empty_ledger_files(self):
        exit_code = cli.main(["init", "--dir", str(self.dir)])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.dir / "forecast-ledger.jsonl").exists())
        self.assertTrue((self.dir / "betting-ledger.jsonl").exists())
        self.assertEqual(read_all(self.dir / "forecast-ledger.jsonl"), [])

    def test_init_is_idempotent(self):
        cli.main(["init", "--dir", str(self.dir)])
        exit_code = cli.main(["init", "--dir", str(self.dir)])
        self.assertEqual(exit_code, 0)

    def test_record_forecast_then_validate_then_duplicate_reimport(self):
        cli.main(["init", "--dir", str(self.dir)])
        exit_code = cli.main(["record-forecast", "--dir", str(self.dir), str(self.forecast_input)])
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(read_all(self.dir / "forecast-ledger.jsonl")), 1)

        validate_code = cli.main(["validate", "--dir", str(self.dir)])
        self.assertEqual(validate_code, 0)

        # Re-importing the SAME file via the CLI must be a safe no-op.
        exit_code2 = cli.main(["record-forecast", "--dir", str(self.dir), str(self.forecast_input)])
        self.assertEqual(exit_code2, 0)
        self.assertEqual(len(read_all(self.dir / "forecast-ledger.jsonl")), 1)

    def test_full_workflow_forecast_ticket_result_settlement_csv(self):
        cli.main(["init", "--dir", str(self.dir)])
        cli.main(["record-forecast", "--dir", str(self.dir), str(self.forecast_input)])

        forecast_id = read_all(self.dir / "forecast-ledger.jsonl")[0]["forecast_id"]

        ticket_input = Path(self.tmp) / "ticket.json"
        ticket_input.write_text(
            json.dumps(
                {
                    "ticket_type": "SINGLE",
                    "unit_stake": 10.0,
                    "max_return": 19.0,
                    "currency": "NGN",
                    "legs": [
                        {
                            "forecast_id": forecast_id,
                            "fixture_id": "f1",
                            "market_type": "1X2",
                            "selection": "H",
                            "placed_odds": 1.9,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        exit_code = cli.main(["place-ticket", "--dir", str(self.dir), str(ticket_input)])
        self.assertEqual(exit_code, 0)
        ticket_id = read_all(self.dir / "betting-ledger.jsonl")[0]["ticket_id"]

        exit_code = cli.main(["update-selection", "--dir", str(self.dir), forecast_id, "placed"])
        self.assertEqual(exit_code, 0)

        exit_code = cli.main(["record-result", "--dir", str(self.dir), forecast_id, "H"])
        self.assertEqual(exit_code, 0)

        exit_code = cli.main(
            ["settle-ticket", "--dir", str(self.dir), ticket_id, "SETTLED", "--leg-results", json.dumps([{"leg_index": 0, "outcome": "WON"}])]
        )
        self.assertEqual(exit_code, 0)

        exit_code = cli.main(["export-csv", "--dir", str(self.dir)])
        self.assertEqual(exit_code, 0)
        self.assertTrue((self.dir / "csv" / "forecasts.csv").exists())
        self.assertTrue((self.dir / "csv" / "tickets.csv").exists())

        exit_code = cli.main(["summary", "--dir", str(self.dir), "None"])  # no batch_id supplied in this fixture
        self.assertEqual(exit_code, 0)

    def test_validate_reports_nonzero_exit_on_schema_violation(self):
        self.dir.mkdir(parents=True)
        (self.dir / "forecast-ledger.jsonl").write_text('{"event_type": "RECORDED"}\n', encoding="utf-8")
        (self.dir / "betting-ledger.jsonl").write_text("", encoding="utf-8")
        exit_code = cli.main(["validate", "--dir", str(self.dir)])
        self.assertNotEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
