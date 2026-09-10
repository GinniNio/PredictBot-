import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.calculator import run_calculator

REPO_ROOT = Path(__file__).parents[1]


class CalculatorScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.fixture = {
            "event_id": "calculator-scaffold-001",
            "market_prices": {"home": 2.05, "draw": 3.3, "away": 3.8},
        }

    def test_scaffold_returns_typed_not_implemented_failure(self):
        result = run_calculator(self.fixture)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failure"]["code"], "NOT_IMPLEMENTED")
        self.assertEqual(result["classification_ceiling"], "RESEARCH-MODEL")
        self.assertEqual(result["event_id"], "calculator-scaffold-001")

    def test_scaffold_never_fabricates_a_probability(self):
        result = run_calculator(self.fixture)
        self.assertNotIn("probabilities", result)

    def test_cli_end_to_end_json_in_json_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.json"
            output_path = Path(tmp) / "output.json"
            input_path.write_text(json.dumps(self.fixture), encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, "-m", "pcbf_calculator", str(input_path), str(output_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["failure"]["code"], "NOT_IMPLEMENTED")


if __name__ == "__main__":
    unittest.main()
