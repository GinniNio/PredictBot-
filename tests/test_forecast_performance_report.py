"""Tests for prospective performance/calibration reporting
(``src/pcbf_calculator/orchestration/forecast_performance_report.py``).

Style matches this repo's existing convention: plain ``unittest.TestCase``,
building ledger fixtures directly via ``ledgers.forecast_ledger`` (never
through the full research/settlement pipeline, except for one end-to-end
test that closes the whole loop).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger  # noqa: E402
from ledgers.storage import read_all  # noqa: E402

from pcbf_calculator.orchestration import forecast_performance_report as fpr  # noqa: E402


def _record_and_score(
    ledger_path: Path,
    fixture_id: str,
    competition_code: str,
    resolved_home_team: str,
    resolved_away_team: str,
    scheduled_date: str,
    model_probabilities: dict[str, float],
    actual_result: str,
    artifact_hash: str = "sha256:test",
    model_version: str = "soccer_1x2_elo_v1-test",
    closing_odds: dict[str, float] | None = None,
    created_at_utc: str | None = None,
) -> str:
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z",
        market_type=fpr.MARKET_TYPE,
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities=model_probabilities,
        model_version=model_version,
        artifact_hash=artifact_hash,
        output_hash="sha256:output-" + fixture_id,
        selection_status="considered",
        competition_code=competition_code,
        resolved_home_team=resolved_home_team,
        resolved_away_team=resolved_away_team,
        scheduled_date=scheduled_date,
        created_at_utc=created_at_utc,
    )
    forecast_id = event["forecast_id"]
    result = forecast_ledger.append_recorded(ledger_path, event)
    assert result.status == "APPENDED", result
    score_result = forecast_ledger.score_and_append(ledger_path, forecast_id, actual_result, closing_odds=closing_odds)
    assert score_result.status == "APPENDED", score_result
    return forecast_id


class LoadScoredStatesTests(unittest.TestCase):
    def test_only_scored_forecasts_are_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20", {"H": 0.6, "D": 0.25, "A": 0.15}, "H"
            )
            # An unscored RECORDED forecast -- must never appear.
            unscored = forecast_ledger.build_recorded_event(
                fixture_id="bxf_2", sport="SOCCER", league="Premier League",
                kickoff_utc="2026-09-21T14:00:00Z", market_type="1X2",
                offered_odds={"H": 1.9, "D": 3.4, "A": 4.3}, classification="RESEARCH-MODEL",
                model_probabilities={"H": 0.5, "D": 0.3, "A": 0.2}, model_version="v", artifact_hash="sha256:x",
                output_hash="sha256:y",
            )
            forecast_ledger.append_recorded(ledger_path, unscored)

            states = fpr.load_scored_states(ledger_path)
            self.assertEqual(len(states), 1)
            self.assertEqual(states[0]["fixture_id"], "bxf_1")

    def test_missing_ledger_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            self.assertEqual(fpr.load_scored_states(ledger_path), [])


class ClassifyAndFilterTests(unittest.TestCase):
    def test_valid_forecast_is_included(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20", {"H": 0.6, "D": 0.25, "A": 0.15}, "H"
            )
            states = fpr.load_scored_states(ledger_path)
            included, excluded = fpr.classify_and_filter(states)
            self.assertEqual(len(included), 1)
            self.assertEqual(excluded, [])

    def test_probabilities_not_summing_to_one_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            fid = _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20", {"H": 0.6, "D": 0.25, "A": 0.15}, "H"
            )
            lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            for rec in lines:
                if rec["forecast_id"] == fid and rec["event_type"] == "RECORDED":
                    rec["payload"]["model_probabilities"] = {"H": 0.6, "D": 0.25, "A": 0.3}
            ledger_path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

            states = fpr.load_scored_states(ledger_path)
            included, excluded = fpr.classify_and_filter(states)
            self.assertEqual(included, [])
            self.assertEqual(len(excluded), 1)
            self.assertIn(fpr.REASON_MALFORMED_PROBABILITIES, excluded[0]["reasons"])

    def test_negative_probability_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            fid = _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20", {"H": 0.6, "D": 0.25, "A": 0.15}, "H"
            )
            lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            for rec in lines:
                if rec["forecast_id"] == fid and rec["event_type"] == "RECORDED":
                    rec["payload"]["model_probabilities"] = {"H": 1.1, "D": 0.2, "A": -0.3}
            ledger_path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(included, [])
            self.assertIn(fpr.REASON_MALFORMED_PROBABILITIES, excluded[0]["reasons"])

    def test_stored_score_disagreeing_with_recomputation_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            fid = _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20", {"H": 0.6, "D": 0.25, "A": 0.15}, "H"
            )
            lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            for rec in lines:
                if rec["forecast_id"] == fid and rec["event_type"] == "SCORED":
                    rec["payload"]["brier_score"] = 0.0  # a wrong, tampered value
            ledger_path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(included, [])
            self.assertIn(fpr.REASON_SCORE_MISMATCH, excluded[0]["reasons"])

    def test_two_forecasts_for_the_same_fixture_with_different_results_are_both_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_shared", "SP1", "Real Madrid", "Barcelona", "2026-09-22",
                {"H": 0.5, "D": 0.3, "A": 0.2}, "H", artifact_hash="sha256:a", model_version="va",
            )
            _record_and_score(
                ledger_path, "bxf_shared", "SP1", "Real Madrid", "Barcelona", "2026-09-22",
                {"H": 0.4, "D": 0.3, "A": 0.3}, "A", artifact_hash="sha256:b", model_version="vb",
            )
            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(included, [])
            self.assertEqual(len(excluded), 2)
            self.assertTrue(all(fpr.REASON_CONFLICTING_ACTUAL_RESULT_FOR_FIXTURE in e["reasons"] for e in excluded))

    def test_non_1x2_market_type_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            event = forecast_ledger.build_recorded_event(
                fixture_id="bxf_other_market", sport="SOCCER", league="Premier League",
                kickoff_utc="2026-09-20T14:00:00Z", market_type="OU2.5",
                offered_odds={"H": 1.9, "D": 3.4, "A": 4.3}, classification="RESEARCH-MODEL",
                model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15}, model_version="v", artifact_hash="sha256:x",
                output_hash="sha256:y",
            )
            forecast_ledger.append_recorded(ledger_path, event)
            forecast_ledger.score_and_append(ledger_path, event["forecast_id"], "H")

            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(included, [])
            self.assertIn(fpr.REASON_UNSUPPORTED_MARKET_TYPE, excluded[0]["reasons"])


class ComputeMetricsTests(unittest.TestCase):
    def test_empty_group_reports_zero_sample_and_small_sample_true(self):
        metrics = fpr.compute_metrics([])
        self.assertEqual(metrics["sample_count"], 0)
        self.assertTrue(metrics["small_sample"])
        self.assertIsNone(metrics["brier_score"])

    def test_accuracy_and_distributions_computed_correctly(self):
        states = [
            {"model_probabilities": {"H": 0.7, "D": 0.2, "A": 0.1}, "actual_result": "H", "brier_score": 0.1, "log_loss": 0.2},
            {"model_probabilities": {"H": 0.2, "D": 0.3, "A": 0.5}, "actual_result": "A", "brier_score": 0.3, "log_loss": 0.4},
            {"model_probabilities": {"H": 0.6, "D": 0.2, "A": 0.2}, "actual_result": "D", "brier_score": 0.5, "log_loss": 0.6},
        ]
        metrics = fpr.compute_metrics(states)
        self.assertEqual(metrics["sample_count"], 3)
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["brier_score"], (0.1 + 0.3 + 0.5) / 3)
        self.assertEqual(metrics["predicted_outcome_distribution"], {"H": 2, "D": 0, "A": 1})
        self.assertEqual(metrics["actual_outcome_distribution"], {"H": 1, "D": 1, "A": 1})


class CalibrationReportTests(unittest.TestCase):
    def test_fixed_bin_edges_never_data_driven(self):
        report = fpr.build_calibration_report([])
        self.assertEqual(report["bin_edges"], [round(i / 10, 1) for i in range(11)])

    def test_probability_lands_in_the_correct_bin(self):
        states = [
            {"model_probabilities": {"H": 0.75, "D": 0.15, "A": 0.10}, "actual_result": "H"},
        ]
        report = fpr.build_calibration_report(states)
        home_bins = report["per_outcome"]["H"]
        populated = [b for b in home_bins if b["sample_count"] > 0]
        self.assertEqual(len(populated), 1)
        self.assertEqual(populated[0]["bin_low"], 0.7)
        self.assertEqual(populated[0]["bin_high"], 0.8)
        self.assertEqual(populated[0]["empirical_frequency"], 1.0)

    def test_probability_of_exactly_one_lands_in_the_last_bin_not_out_of_range(self):
        states = [{"model_probabilities": {"H": 1.0, "D": 0.0, "A": 0.0}, "actual_result": "H"}]
        report = fpr.build_calibration_report(states)
        home_bins = report["per_outcome"]["H"]
        self.assertEqual(home_bins[-1]["sample_count"], 1)


class ClosingLineReportTests(unittest.TestCase):
    def test_separates_model_and_closing_market_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.8, "D": 0.15, "A": 0.05}, "A", closing_odds={"H": 1.9, "D": 3.4, "A": 4.3},
            )
            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(excluded, [])
            report = fpr.build_closing_line_report(included)
            self.assertEqual(report["sample_count_with_complete_closing_odds"], 1)
            self.assertIn("model", report)
            self.assertIn("closing_market", report)
            # The overconfident model (0.8 on H, actual A) should score
            # worse than the de-vigged closing market's own probability.
            self.assertGreater(report["model"]["brier_score"], report["closing_market"]["brier_score"])

    def test_forecasts_without_closing_odds_are_excluded_from_this_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H", closing_odds=None,
            )
            included, _excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            report = fpr.build_closing_line_report(included)
            self.assertEqual(report["sample_count_with_complete_closing_odds"], 0)
            # But it's still counted in the overall performance summary.
            summary = fpr.build_performance_summary(included, [], len(included))
            self.assertEqual(summary["counts"]["included"], 1)
            self.assertEqual(summary["counts"]["missing_closing_odds"], 1)


class DeterminismTests(unittest.TestCase):
    def test_byte_identical_output_across_repeated_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ledger_dir = tmp_path / "ledger_data"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H", closing_odds={"H": 1.9, "D": 3.4, "A": 4.3},
            )
            _record_and_score(
                ledger_path, "bxf_2", "D1", "Bayern Munich", "Dortmund", "2026-09-21",
                {"H": 0.5, "D": 0.3, "A": 0.2}, "D",
            )

            out1 = tmp_path / "out1"
            out2 = tmp_path / "out2"
            fpr.write_performance_reports(ledger_dir, out1)
            fpr.write_performance_reports(ledger_dir, out2)

            for filename in ("performance-summary.json", "calibration-report.json", "closing-line-report.json", "excluded-records.json"):
                self.assertEqual((out1 / filename).read_bytes(), (out2 / filename).read_bytes(), filename)

    def test_no_wall_clock_timestamp_field_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            result = fpr.run_performance_report(ledger_dir)
            rendered = json.dumps(result)
            self.assertNotIn("generated_at", rendered)


class SmallSampleTests(unittest.TestCase):
    def test_nine_scored_forecasts_are_flagged_small_sample_everywhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            for i in range(9):
                _record_and_score(
                    ledger_path, f"bxf_{i}", "E0", f"Team{i}A", f"Team{i}B", "2026-09-20",
                    {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
                )
            included, excluded = fpr.classify_and_filter(fpr.load_scored_states(ledger_path))
            self.assertEqual(excluded, [])
            summary = fpr.build_performance_summary(included, excluded, len(included))
            self.assertEqual(summary["counts"]["included"], 9)
            self.assertTrue(summary["overall"]["small_sample"])
            calibration = fpr.build_calibration_report(included)
            self.assertTrue(calibration["small_sample"])


class NoLedgerMutationTests(unittest.TestCase):
    def test_running_the_report_never_appends_to_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            before = read_all(ledger_path)
            fpr.write_performance_reports(ledger_dir, Path(tmp) / "out")
            after = read_all(ledger_path)
            self.assertEqual(before, after)

    def test_never_creates_a_betting_ledger_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            fpr.write_performance_reports(ledger_dir, Path(tmp) / "out")
            self.assertFalse((ledger_dir / "betting-ledger.jsonl").exists())


class MissingLedgerTests(unittest.TestCase):
    def test_missing_ledger_directory_produces_well_formed_all_zero_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = fpr.run_performance_report(Path(tmp) / "does_not_exist")
            self.assertEqual(result["performance_summary"]["counts"]["included"], 0)
            self.assertEqual(result["performance_summary"]["overall"]["sample_count"], 0)


class CalibrationBinBoundaryTests(unittest.TestCase):
    """A probability that is "morally" exactly at a bin edge (e.g.
    0.7) can be computed as a value fractionally BELOW it purely from
    floating-point representation -- e.g. a normalize-then-renormalize
    style computation (``0.7 * 3 / 3 == 0.6999999999999998``, not
    ``0.7``) -- confirmed to actually occur, not a theoretical concern.
    ``_bin_index`` must still place it in the bin its true value
    belongs to."""

    def test_float_representation_noise_just_below_an_edge_lands_in_the_upper_bin(self):
        noisy_point_seven = 0.7 * 3 / 3
        self.assertNotEqual(noisy_point_seven, 0.7)  # confirms the float noise is real
        self.assertEqual(fpr._bin_index(noisy_point_seven), 7)  # bin [0.7, 0.8), not [0.6, 0.7)

    def test_every_noisy_decile_lands_in_its_true_bin(self):
        for i in range(1, 10):
            noisy = (i / 10) * 3 / 3
            with self.subTest(i=i, noisy=noisy):
                self.assertEqual(fpr._bin_index(noisy), i)

    def test_exact_bin_edges_from_calibration_bin_edges_itself_are_stable(self):
        for i, edge in enumerate(fpr.CALIBRATION_BIN_EDGES[:-1]):
            self.assertEqual(fpr._bin_index(edge), i)


class DuplicateScoredEventTests(unittest.TestCase):
    def test_a_forecast_id_with_two_scored_events_on_disk_is_excluded_never_silently_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            fid = _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            # Simulate a ledger that somehow accumulated a second, DIFFERENT
            # SCORED event for the same forecast_id (never producible by
            # the real write path -- append_terminal_if_new refuses this).
            lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            scored_line = next(r for r in lines if r["forecast_id"] == fid and r["event_type"] == "SCORED")
            tampered = json.loads(json.dumps(scored_line))
            tampered["payload"]["actual_result"] = "A"
            tampered["payload"]["brier_score"] = 0.0
            tampered["payload"]["log_loss"] = 0.0
            lines.append(tampered)
            ledger_path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")

            states = fpr.load_scored_states(ledger_path)
            self.assertEqual(len(states), 1)
            self.assertEqual(states[0]["_event_types_seen"].count("SCORED"), 2)

            included, excluded = fpr.classify_and_filter(states)
            self.assertEqual(included, [])
            self.assertEqual(len(excluded), 1)
            self.assertIn(fpr.REASON_DUPLICATE_SCORED_EVENTS, excluded[0]["reasons"])


class ReconciliationInvariantTests(unittest.TestCase):
    def test_reconciles_is_true_and_totals_match_for_a_mixed_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / forecast_ledger.DEFAULT_FILENAME
            # One valid forecast.
            _record_and_score(
                ledger_path, "bxf_1", "E0", "Arsenal", "Chelsea", "2026-09-20",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            # One malformed-probabilities forecast.
            fid2 = _record_and_score(
                ledger_path, "bxf_2", "D1", "Bayern Munich", "Dortmund", "2026-09-21",
                {"H": 0.6, "D": 0.25, "A": 0.15}, "H",
            )
            lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
            for rec in lines:
                if rec["forecast_id"] == fid2 and rec["event_type"] == "RECORDED":
                    rec["payload"]["model_probabilities"] = {"H": 0.6, "D": 0.25, "A": 0.3}
            ledger_path.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
            # Two forecasts sharing a fixture_id with conflicting results.
            _record_and_score(
                ledger_path, "bxf_shared", "SP1", "Real Madrid", "Barcelona", "2026-09-22",
                {"H": 0.5, "D": 0.3, "A": 0.2}, "H", artifact_hash="sha256:a", model_version="va",
            )
            _record_and_score(
                ledger_path, "bxf_shared", "SP1", "Real Madrid", "Barcelona", "2026-09-22",
                {"H": 0.4, "D": 0.3, "A": 0.3}, "A", artifact_hash="sha256:b", model_version="vb",
            )

            states = fpr.load_scored_states(ledger_path)
            included, excluded = fpr.classify_and_filter(states)
            self.assertEqual(len(included) + len(excluded), len(states))
            self.assertEqual(len(included), 1)
            self.assertEqual(len(excluded), 3)

            # No forecast_id appears in both lists.
            included_ids = {s["forecast_id"] for s in included}
            excluded_ids = {e["forecast_id"] for e in excluded}
            self.assertEqual(included_ids & excluded_ids, set())

            summary = fpr.build_performance_summary(included, excluded, len(states))
            self.assertTrue(summary["reconciles"])
            self.assertEqual(
                summary["counts"]["included"] + summary["counts"]["excluded"],
                summary["counts"]["total_scored_events_on_disk"],
            )

    def test_reconciles_is_true_for_the_empty_case(self):
        summary = fpr.build_performance_summary([], [], 0)
        self.assertTrue(summary["reconciles"])


if __name__ == "__main__":
    unittest.main()
