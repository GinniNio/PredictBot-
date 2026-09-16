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
        # --no-deps: this is an offline install against only the local
        # wheelhouse (--no-index) -- pcbf-football's own conditional
        # Windows dependency (tzdata; sys_platform == "win32") would
        # otherwise make pip try to resolve a real package from an empty
        # index and fail on Windows. This test only ever checks that the
        # package's own directories land in install_dir, never that a
        # declared dependency is actually installable.
        install = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "--quiet", "--no-index", "--no-deps",
                "--find-links", str(wheelhouse), "--target", str(install_dir), "pcbf-football",
            ],
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            raise RuntimeError(f"wheel install failed:\n{install.stdout}\n{install.stderr}")
        if not (install_dir / "pcbf_calculator").is_dir():
            raise RuntimeError(f"pip install reported success but pcbf_calculator is missing from {install_dir}")
        if not (install_dir / "data_pipeline").is_dir():
            raise RuntimeError(f"pip install reported success but data_pipeline is missing from {install_dir}")

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

    def test_full_forecast_lifecycle_works_from_the_installed_wheel(self):
        """Closes the full loop from a real installed wheel: capture ->
        forecast -> record (run-bet9ja-research --ledger-dir) -> settle ->
        score (ingest-football-data-results) -> report
        (report-forecast-performance), proving
        data_pipeline/schema_inspection.py, validation.py, and
        orchestration/forecast_performance_report.py -- packaged
        alongside pcbf_calculator and ledgers -- are genuinely importable
        from the wheel, not just the repo checkout."""

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "capture.json"
            input_path.write_text(json.dumps(CAPTURE_ENVELOPE), encoding="utf-8")
            ledger_dir = tmp_path / "ledger_data"

            record_result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "run-bet9ja-research", str(input_path),
                    "--output-dir", str(tmp_path / "research_out"), "--ledger-dir", str(ledger_dir),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(record_result.returncode, 0, f"stdout={record_result.stdout!r} stderr={record_result.stderr!r}")

            results_csv = tmp_path / "results.csv"
            results_csv.write_text(
                "Div,Date,HomeTeam,AwayTeam,FTR,PSCH,PSCD,PSCA\nE0,20/09/2026,Arsenal,Chelsea,H,1.88,3.55,4.15\n",
                encoding="utf-8",
            )
            output_dir = tmp_path / "settlement_out"

            settle_result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "ingest-football-data-results", str(results_csv),
                    "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(settle_result.returncode, 0, f"stdout={settle_result.stdout!r} stderr={settle_result.stderr!r}")

            settled = json.loads((output_dir / "settled-forecasts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(settled["settled"]), 1)
            self.assertEqual(settled["settled"][0]["closing_odds"], {"H": 1.88, "D": 3.55, "A": 4.15})

            performance_out = tmp_path / "performance_out"
            report_result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "report-forecast-performance",
                    "--ledger-dir", str(ledger_dir), "--output-dir", str(performance_out),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(report_result.returncode, 0, f"stdout={report_result.stdout!r} stderr={report_result.stderr!r}")

            for filename in (
                "performance-summary.json", "calibration-report.json",
                "closing-line-report.json", "excluded-records.json",
            ):
                self.assertTrue((performance_out / filename).exists(), filename)

            summary = json.loads((performance_out / "performance-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"]["included"], 1)
            self.assertEqual(summary["counts"]["excluded"], 0)
            self.assertEqual(summary["counts"]["with_closing_odds"], 1)
            self.assertTrue(summary["overall"]["small_sample"])

    def test_refresh_soccer_artifact_works_from_the_installed_wheel(self):
        """Proves the artifact refresh lifecycle -- CANDIDATE creation
        only, never promotion -- runs end to end from a real installed
        wheel: `research.soccer_1x2_elo_baseline` (training/evaluation)
        and `research/soccer_1x2_elo_baseline/expected_hashes.json`
        (packaged as package-data) are genuinely importable/readable
        from the wheel, not just the repo checkout, and there is no
        `.git` checkout in the installed target dir at all -- exercising
        `current_commit_sha`'s own documented fallback for real."""

        from data_pipeline.dataset_builder import raw_file_path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            fixtures_dir = REPO_ROOT / "tests" / "fixtures" / "football_data"
            mapping = {
                ("E0", "1920"): "season_1920_with_kickoff.csv",
                ("E0", "2324"): "season_2324_for_elo_baseline.csv",
                ("E0", "2425"): "season_2425_for_elo_baseline.csv",
                ("E0", "2526"): "season_2526_prospective.csv",
                ("E0", "2627"): "season_2627_prospective.csv",
            }
            for (league, season), filename in mapping.items():
                dest = raw_file_path(raw_dir, league, season)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(fixtures_dir / filename, dest)

            incumbent_manifest_path = tmp_path / "incumbent_manifest.json"
            shutil.copy(
                REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data" / "model_artifact_manifest.json",
                incumbent_manifest_path,
            )
            incumbent_performance_dir = tmp_path / "incumbent_perf"
            incumbent_performance_dir.mkdir()

            output_dir = tmp_path / "candidate_out"
            result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "refresh-soccer-artifact",
                    "--training-input", str(raw_dir),
                    "--incumbent-manifest", str(incumbent_manifest_path),
                    "--incumbent-performance-dir", str(incumbent_performance_dir),
                    "--output-dir", str(output_dir),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(result.returncode, 0, f"stdout={result.stdout!r} stderr={result.stderr!r}")

            for relative_path in (
                "candidate/model_artifact.json",
                "candidate/live_snapshot.json",
                "candidate/evaluation_report.json",
                "candidate/model_artifact_manifest.json",
                "candidate/candidate_bundle_manifest.json",
                "artifact-comparison.json",
                "promotion-review.json",
            ):
                self.assertTrue((output_dir / relative_path).exists(), relative_path)

            review = json.loads((output_dir / "promotion-review.json").read_text(encoding="utf-8"))
            self.assertEqual(review["recommendation"], "HOLD_FOR_PROSPECTIVE_EVIDENCE")

            manifest = json.loads((output_dir / "candidate" / "model_artifact_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["provenance"]["training_code_commit_sha"], "UNKNOWN_NOT_A_GIT_CHECKOUT")

    def test_shadow_forecast_candidate_works_from_the_installed_wheel(self):
        """Builds a real candidate bundle (via refresh-soccer-artifact,
        already proven to work from this same installed wheel above),
        then shadow-forecasts it against a real Bet9ja capture, all
        through the installed wheel's own console script -- proving
        candidate_shadow_forecast.py's own dependency injection into
        SoccerOneXTwoEloV1Adapter/cli.run_calculator/
        bet9ja_research_session.run_bet9ja_research_session, and its own
        reuse of forecast_ledger_writer.write_batch, all work from a
        real installed wheel, not just the repo checkout."""

        from data_pipeline.dataset_builder import raw_file_path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_dir = tmp_path / "raw"
            fixtures_dir = REPO_ROOT / "tests" / "fixtures" / "football_data"
            mapping = {
                ("E0", "1920"): "season_1920_with_kickoff.csv",
                ("E0", "2324"): "season_2324_for_elo_baseline.csv",
                ("E0", "2425"): "season_2425_for_elo_baseline.csv",
                ("E0", "2526"): "season_2526_prospective.csv",
                ("E0", "2627"): "season_2627_prospective.csv",
            }
            for (league, season), filename in mapping.items():
                dest = raw_file_path(raw_dir, league, season)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(fixtures_dir / filename, dest)

            incumbent_manifest_path = tmp_path / "incumbent_manifest.json"
            shutil.copy(
                REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data" / "model_artifact_manifest.json",
                incumbent_manifest_path,
            )
            incumbent_performance_dir = tmp_path / "incumbent_perf"
            incumbent_performance_dir.mkdir()

            candidate_out = tmp_path / "candidate_out"
            refresh_result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "refresh-soccer-artifact",
                    "--training-input", str(raw_dir),
                    "--incumbent-manifest", str(incumbent_manifest_path),
                    "--incumbent-performance-dir", str(incumbent_performance_dir),
                    "--output-dir", str(candidate_out),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(refresh_result.returncode, 0, f"stdout={refresh_result.stdout!r} stderr={refresh_result.stderr!r}")
            candidate_dir = candidate_out / "candidate"
            candidate_bundle_hash = json.loads(
                (candidate_dir / "candidate_bundle_manifest.json").read_text(encoding="utf-8")
            )["bundle_hash"]

            # Liverpool/Man City -- confirmed rated by this candidate's own
            # tiny training fixture set (Arsenal/Chelsea's own rows in
            # that fixture data are unplayed/prospective and never
            # produce an Elo rating).
            capture_envelope = {
                "schema_version": "bet9ja-soccer-session.v1",
                "capture_session_id": "wheel-install-shadow-test-session",
                "capture_scope": "SOCCER_ALL_PREMATCH_COMPETITIONS",
                "captured_at_utc": CAPTURED_AT_UTC,
                "inventory_fingerprint": "invfp_wheel_shadow_test",
                "summary": {"total": 1, "completed": 1, "confirmed_empty": 0, "failed": 0, "pending": 0},
                "competition_ledger": [
                    {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "COMPLETED"}
                ],
                "fixtures": [
                    {
                        "fixture_id": "bxf_wheel_shadow_1",
                        "sport": "SOCCER",
                        "status": "PRE_MATCH",
                        "market_family": "1X2",
                        "offered_odds": {"H": 1.9, "D": 3.4, "A": 4.3},
                        "participants": {"home": "Liverpool", "away": "Man City"},
                        "resolved_source_competition_id": "2000001",
                        "date_heading_raw": "Sun 20 Sep",
                        "kickoff_raw": "14:00",
                        "duplicate_status": "NEW",
                    }
                ],
                "unparsed_records": [],
            }
            capture_path = tmp_path / "shadow_capture.json"
            capture_path.write_text(json.dumps(capture_envelope), encoding="utf-8")

            ledger_dir = tmp_path / "candidate_ledgers"
            shadow_out = tmp_path / "shadow_out"
            shadow_result = subprocess.run(
                [
                    sys.executable, "-m", "pcbf_calculator", "shadow-forecast-candidate",
                    "--candidate-bundle", str(candidate_dir),
                    "--capture", str(capture_path),
                    "--ledger-dir", str(ledger_dir),
                    "--output-dir", str(shadow_out),
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
                env=self.install_env,
            )
            self.assertEqual(shadow_result.returncode, 0, f"stdout={shadow_result.stdout!r} stderr={shadow_result.stderr!r}")
            self.assertIn(f"candidate_bundle_hash={candidate_bundle_hash}", shadow_result.stdout)

            for filename in (
                "candidate-shadow-forecasts.json",
                "candidate-shadow-abstentions.json",
                "candidate-shadow-quarantine.json",
                "candidate-shadow-session-report.json",
            ):
                self.assertTrue((shadow_out / filename).exists(), filename)

            forecasts = json.loads((shadow_out / "candidate-shadow-forecasts.json").read_text(encoding="utf-8"))
            self.assertEqual(len(forecasts["forecasts"]), 1)
            row = forecasts["forecasts"][0]
            self.assertTrue(row["forecast"]["forecast_available"])
            self.assertEqual(row["model_role"], "CANDIDATE_SHADOW")
            self.assertEqual(row["candidate_bundle_hash"], candidate_bundle_hash)
            self.assertIsNone(row["operator_decision"])
            self.assertEqual(row["recommendation_status"], "NOT_AVAILABLE")
            self.assertTrue(row["alias_hash"])

            ledger_path = ledger_dir / candidate_bundle_hash / "forecast-ledger.jsonl"
            self.assertTrue(ledger_path.exists())
            record = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["payload"]["model_role"], "CANDIDATE_SHADOW")
            self.assertEqual(record["payload"]["candidate_bundle_hash"], candidate_bundle_hash)
            self.assertEqual(record["payload"]["alias_hash"], row["alias_hash"])

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
