import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.cli import run_calculator

REPO_ROOT = Path(__file__).parents[1]

TWO_WAY_REQUEST = {
    "event_id": "tennis-match-001",
    "category": "tennis",
    "market_prices": {"player_a": 1.8, "player_b": 2.05},
}

THREE_WAY_REQUEST = {
    "event_id": "soccer-match-001",
    "category": "soccer",
    "market_prices": {"home": 1.9, "draw": 3.4, "away": 4.3},
}


class CliIntegrationTests(unittest.TestCase):
    def test_two_way_request_end_to_end(self):
        result = run_calculator(TWO_WAY_REQUEST)
        self.assertEqual(result["status"], "OK")
        self.assertIsNone(result["failure"])
        self.assertEqual(result["pricing"]["market_structure"], "two_way")
        self.assertFalse(result["forecast"]["forecast_available"])
        self.assertEqual(result["classification_ceiling"], "PAPER")

    def test_three_way_request_end_to_end(self):
        result = run_calculator(THREE_WAY_REQUEST)
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["pricing"]["market_structure"], "three_way")
        self.assertEqual(result["decision"], None)

    def test_pricing_only_output_explicitly_flags_no_forecast(self):
        # Proves the "no adapter -> pricing-only, explicit no-forecast flag"
        # path end to end for a category no adapter is registered for.
        result = run_calculator(
            {"event_id": "e2", "category": "cricket", "market_prices": {"team_a": 1.7, "team_b": 2.2}}
        )
        self.assertEqual(result["status"], "OK")
        self.assertIn("forecast", result)
        self.assertFalse(result["forecast"]["forecast_available"])
        self.assertIsNotNone(result["forecast"]["no_forecast_reason"])
        # Market-derived numbers must never be mistaken for a forecast: the
        # two objects are always separate.
        self.assertNotEqual(result["pricing"], result["forecast"])

    def test_missing_event_id_fails_typed(self):
        result = run_calculator({"category": "soccer", "market_prices": {"home": 1.9, "away": 2.0}})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "INVALID_REQUEST")

    def test_unknown_category_fails_typed(self):
        result = run_calculator(
            {"event_id": "e3", "category": "underwater_hockey", "market_prices": {"a": 1.9, "b": 2.0}}
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "UNSUPPORTED_SPORT_MARKET_COMBINATION")

    def test_research_only_category_fails_typed(self):
        result = run_calculator(
            {"event_id": "e4", "category": "specials_combo", "market_prices": {"leg_win": 4.0, "leg_lose": 1.3}}
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "UNSUPPORTED_INPUT")

    def test_incomplete_prices_fails_typed(self):
        result = run_calculator({"event_id": "e5", "category": "soccer", "market_prices": {"home": 1.9}})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "INCOMPLETE_OPPOSING_PRICES")

    def test_decision_layer_wired_through_cli(self):
        request = dict(THREE_WAY_REQUEST)
        # Use an arbitrage-free (bettor-favorable) price set so the priced
        # outcome clears STOP_NEGATIVE_EV even after the Wilson lower bound.
        request["market_prices"] = {"home": 3.0, "draw": 6.0, "away": 10.0}
        request["decision_input"] = {
            "evidence": {"sample_size": 500, "min_sample_size": 30, "cash_min_sample_size": 200},
            "freshness": {"data_age_seconds": 5, "max_age_seconds": 300},
            "liquidity": {"available_stake": 1000, "min_required_stake": 50},
            "uncertainty": {"width": 0.05, "max_width": 0.5},
        }
        result = run_calculator(request)
        self.assertEqual(result["status"], "OK")
        self.assertIsNotNone(result["decision"])
        # No forecast available for soccer (Release A) -> capped at PAPER
        # regardless of how clean the STOP-rule inputs are.
        self.assertEqual(result["classification_ceiling"], "PAPER")

    def test_decision_layer_missing_block_fails_typed(self):
        request = dict(THREE_WAY_REQUEST)
        request["decision_input"] = {"evidence": {"sample_size": 500, "min_sample_size": 30}}
        result = run_calculator(request)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "MISSING_DECISION_INPUT")

    def test_deterministic_repeat(self):
        first = run_calculator(THREE_WAY_REQUEST)
        second = run_calculator(THREE_WAY_REQUEST)
        self.assertEqual(first, second)

    def test_cli_end_to_end_json_in_json_out_two_way(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            output_path = Path(tmp) / "output.json"
            input_path.write_text(json.dumps(TWO_WAY_REQUEST), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, "-m", "pcbf_calculator", str(input_path), str(output_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "OK")

    def test_cli_run_twice_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            out_a = Path(tmp) / "a.json"
            out_b = Path(tmp) / "b.json"
            input_path.write_text(json.dumps(THREE_WAY_REQUEST), encoding="utf-8")

            for out in (out_a, out_b):
                completed = subprocess.run(
                    [sys.executable, "-m", "pcbf_calculator", str(input_path), str(out)],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

            self.assertEqual(out_a.read_bytes(), out_b.read_bytes())


if __name__ == "__main__":
    unittest.main()
