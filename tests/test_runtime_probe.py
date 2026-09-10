import json
import unittest
from pathlib import Path

from pcbf_football.runtime_probe import FIXED_PROBABILITIES, run_probe


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "event_id": "runtime-proof-001",
            "market_prices": {"home": 2.05, "draw": 3.3, "away": 3.8},
        }

    def test_result_is_byte_for_byte_deterministic(self):
        first = json.dumps(run_probe(self.fixture), sort_keys=True)
        second = json.dumps(run_probe(self.fixture), sort_keys=True)
        self.assertEqual(first, second)

    def test_probe_has_no_cash_authority(self):
        result = run_probe(self.fixture)
        self.assertEqual(result["status"], "RUNTIME_PROBE")
        self.assertEqual(result["classification_ceiling"], "RESEARCH-MODEL")

    def test_fixed_distribution_sums_to_one(self):
        self.assertAlmostEqual(sum(FIXED_PROBABILITIES.values()), 1.0)

    def test_missing_price_fails_closed(self):
        del self.fixture["market_prices"]["away"]
        result = run_probe(self.fixture)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "M02_TEST_FAIL")
        self.assertEqual(result["failure"]["field"], "market_prices.away")

    def test_null_is_not_silently_zeroed(self):
        self.fixture["market_prices"]["home"] = None
        result = run_probe(self.fixture)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["field"], "market_prices.home")

    def test_checked_in_expected_result_matches(self):
        expected_path = Path(__file__).parents[1] / "examples" / "expected-result.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        self.assertEqual(run_probe(self.fixture), expected)


if __name__ == "__main__":
    unittest.main()
