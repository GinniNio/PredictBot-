"""Confirms ``run-bet9ja-research`` works from a real, installed wheel --
not just from the repo's own source tree. Builds the wheel and installs it
once (``setUpClass``) into an isolated directory via a real ``pip install``
of the built artifact, then drives the new subcommand through a real
subprocess against that installed package, exactly as an external host
would invoke it (``docs/HOST_CONTRACT.md``'s own "install the wheel, then
invoke the CLI" pattern, extended to this new subcommand).

Installs via ``pip install --target <dir>`` + ``PYTHONPATH`` rather than
into a freshly created virtualenv: a real ``pip install`` of the actual
built wheel either way, but this avoids depending on ``ensurepip``/the
``venv`` module being fully functional on every CI image this test runs
on -- a dependency that isn't the thing this test is actually meant to
prove (that the wheel's contents install and run correctly), and that
failed on this platform's own runner image via ``venv.EnvBuilder``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CAPTURED_AT_UTC = "2026-09-12T15:30:23.000Z"

CAPTURE_ENVELOPE = {
    "schema_version": "bet9ja-soccer-session.v1",
    "capture_session_id": "wheel-install-test-session",
    "capture_scope": "SOCCER_ALL_PREMATCH_COMPETITIONS",
    "captured_at_utc": CAPTURED_AT_UTC,
    "inventory_fingerprint": "invfp_wheel_test",
    "summary": {"total": 1, "completed": 1, "confirmed_empty": 0, "failed": 0, "pending": 0},
    "competition_ledger": [
        {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "COMPLETED"}
    ],
    "fixtures": [
        {
            "fixture_id": "bxf_wheel_1",
            "sport": "SOCCER",
            "status": "PRE_MATCH",
            "market_family": "1X2",
            "offered_odds": {"H": 1.9, "D": 3.4, "A": 4.3},
            "participants": {"home": "Arsenal", "away": "Chelsea"},
            "resolved_source_competition_id": "2000001",
            "date_heading_raw": "Sun 20 Sep",
            "kickoff_raw": "14:00",
            "duplicate_status": "NEW",
        }
    ],
    "unparsed_records": [],
}


class WheelInstallationTests(unittest.TestCase):
    """Slower than the rest of this suite by design (a real wheel build +
    install) -- proves packaging, not just source-tree behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.mkdtemp(prefix="pcbf-wheel-install-test-")
        tmp_path = Path(cls.tmp_dir)
        wheelhouse = tmp_path / "wheelhouse"
        install_dir = tmp_path / "installed"

        build = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(wheelhouse)],
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            raise RuntimeError(f"wheel build failed:\n{build.stdout}\n{build.stderr}")

        install_dir.mkdir(parents=True, exist_ok=True)
        install = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--quiet", "--no-index",
                "--find-links", str(wheelhouse), "--target", str(install_dir), "pcbf-football",
            ],
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise RuntimeError(f"wheel install failed:\n{install.stdout}\n{install.stderr}")
        if not (install_dir / "pcbf_calculator").is_dir():
            raise RuntimeError(f"pip install reported success but pcbf_calculator is missing from {install_dir}")

        cls.install_env = os.environ.copy()
        cls.install_env["PYTHONPATH"] = str(install_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_run_bet9ja_research_works_from_the_installed_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(CAPTURE_ENVELOPE), encoding="utf-8")
            output_dir = tmp_path / "out"

            result = subprocess.run(
                [sys.executable, "-m", "pcbf_calculator", "run-bet9ja-research", str(input_path), "--output-dir", str(output_dir)],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),  # no relation to the repo checkout
                env=self.install_env,
            )
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")

            report = json.loads((output_dir / "research-session-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["ranked_selections"], 1)
            self.assertEqual(report["counts"]["source_fixtures_raw"], 1)
            self.assertTrue(report["reconciles"])

            ranked = json.loads((output_dir / "forecast-research-ranked.json").read_text(encoding="utf-8"))
            market = ranked["markets"][0]
            self.assertEqual(market["classification_ceiling"], "RESEARCH-MODEL")
            self.assertEqual(market["cash_stake"], 0)
            self.assertIsNone(market["operator_decision"])
            self.assertTrue(market["forecast"]["forecast_available"])

            self.assertTrue((output_dir / "forecast-abstentions.json").exists())
            self.assertTrue((output_dir / "ingestion-and-screening-exclusions.json").exists())

    def test_ledger_dir_option_works_from_the_installed_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(CAPTURE_ENVELOPE), encoding="utf-8")
            output_dir = tmp_path / "out"
            ledger_dir = tmp_path / "ledger_data"

            result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "run-bet9ja-research", str(input_path),
                    "--output-dir", str(output_dir), "--ledger-dir", str(ledger_dir),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
            self.assertIn("LEDGER:", result.stdout)

            ledger_path = ledger_dir / "forecast-ledger.jsonl"
            self.assertTrue(ledger_path.exists())
            lines = ledger_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["payload"]["classification"], "RESEARCH-MODEL")
            self.assertIsNotNone(record["payload"]["model_probabilities"])

    def test_host_contract_invocation_also_works_from_the_installed_wheel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "request.json"
            input_path.write_text(
                json.dumps({"event_id": "e1", "category": "tennis", "market_prices": {"a": 1.8, "b": 2.05}}),
                encoding="utf-8",
            )
            output_path = tmp_path / "result.json"

            result = subprocess.run(
                [sys.executable, "-m", "pcbf_calculator", str(input_path), str(output_path)],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
