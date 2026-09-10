"""Proves every numeric backtest/prospective/dataset/monitoring threshold
proposed in docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md (sections 4, 5, 14, 15,
17) cannot promote the soccer adapter to PAPER or CASH.

Two things must both hold, independently:

1. Every threshold's declared status is still PROPOSED_OPERATOR_DECISION
   with enforcement DISABLED (never silently flipped to APPROVED/ACTIVE
   without a human doing it deliberately).
2. No runtime code path (src/pcbf_calculator) reads this file at all — so
   even if every threshold were flipped to APPROVED, nothing would happen
   until a future implementation PR deliberately wires a gate-check to
   this file. Promotion today is impossible regardless of these numbers,
   because `soccer` has no registered adapter implementation
   (tests/test_soccer_1x2_stub.py and test_registries.py already cover
   that directly); this file only prevents the *numbers themselves* from
   being mistaken for settled policy.
"""

import re
import unittest
from pathlib import Path

from pcbf_calculator.registries.yamlmini import load_registry

REPO_ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_PATH = REPO_ROOT / "docs" / "adapters" / "data" / "soccer_1x2_promotion_thresholds.yaml"

REQUIRED_FIELDS = ("id", "spec_section", "proposed_value", "status", "enforcement", "rationale")

EXPECTED_THRESHOLD_IDS = {
    "backtest_minimum_sample_size",
    "backtest_date_range_coverage",
    "backtest_calibration_error_bound",
    "backtest_beats_baseline_by_percent",
    "prospective_minimum_live_fixture_count",
    "prospective_live_feature_availability_rate",
    # Correction 3: newly ledgered rows.
    "dataset_minimum_seasons",
    "dataset_minimum_fixture_count",
    "dataset_minimum_feature_coverage_rate",
    "feature_staleness_tolerance_hours",
    "prospective_minimum_shadow_period_weeks",
    "rollback_calibration_error_bound",
    "rollback_feature_availability_threshold",
    "rollback_baseline_comparison_window_fixtures",
    "monitoring_calibration_window_and_cadence",
}


def _load_thresholds():
    text = THRESHOLDS_PATH.read_text(encoding="utf-8")
    return load_registry(text)["categories"]


class SoccerPromotionThresholdStatusTests(unittest.TestCase):
    def test_file_exists_and_parses(self):
        rows = _load_thresholds()
        self.assertGreater(len(rows), 0)

    def test_all_expected_thresholds_are_present(self):
        rows = _load_thresholds()
        ids = {row["id"] for row in rows}
        self.assertEqual(ids, EXPECTED_THRESHOLD_IDS)

    def test_every_threshold_declares_every_required_field(self):
        rows = _load_thresholds()
        for row in rows:
            for field in REQUIRED_FIELDS:
                self.assertIn(field, row, f"threshold '{row.get('id')}' missing field '{field}'")

    def test_no_threshold_is_approved_yet(self):
        # This is the load-bearing assertion: if a human operator later
        # approves one of these thresholds, this test starts failing for
        # that row specifically, forcing a deliberate, visible edit here
        # rather than letting an APPROVED status slip in silently.
        rows = _load_thresholds()
        for row in rows:
            self.assertEqual(
                row["status"],
                "PROPOSED_OPERATOR_DECISION",
                f"threshold '{row['id']}' has status '{row['status']}', expected "
                "PROPOSED_OPERATOR_DECISION until an operator explicitly approves it",
            )

    def test_no_threshold_has_active_enforcement(self):
        rows = _load_thresholds()
        for row in rows:
            self.assertEqual(
                row["enforcement"],
                "DISABLED",
                f"threshold '{row['id']}' has enforcement '{row['enforcement']}', "
                "expected DISABLED — no threshold may actively gate promotion yet",
            )

    def test_every_rationale_names_the_review_requirement(self):
        rows = _load_thresholds()
        for row in rows:
            self.assertIn(
                "review",
                row["rationale"].lower(),
                f"threshold '{row['id']}' rationale does not mention the pending review requirement",
            )


class NoRuntimeCodeConsumesThePromotionThresholdsFileTests(unittest.TestCase):
    """Structural proof that this file is inert today, independent of what
    its `status`/`enforcement` fields say. Mirrors how DESIGN_IN_PROGRESS
    was proven identical to NOT_IMPLEMENTED: by showing the dispatch code
    never reads the thing that might otherwise look authoritative."""

    def test_no_source_file_references_the_thresholds_path_or_filename(self):
        src_dir = REPO_ROOT / "src" / "pcbf_calculator"
        offending = []
        for py_file in src_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if "soccer_1x2_promotion_thresholds" in text:
                offending.append(str(py_file.relative_to(REPO_ROOT)))
        self.assertEqual(
            offending,
            [],
            "runtime code must not reference the promotion-thresholds file "
            f"until a future implementation PR deliberately wires a gate check: {offending}",
        )

    def test_no_source_file_hardcodes_any_proposed_numeric_threshold(self):
        # Defends against someone copying e.g. "0.05" or "500" into the
        # decision engine directly instead of going through the (not yet
        # built) gate-check mechanism, which would silently reintroduce
        # the exact problem this file exists to prevent.
        src_dir = REPO_ROOT / "src" / "pcbf_calculator"
        suspicious_numbers = ("500", "0.05", "0\\.05", "150", "98%", "98\\b")
        offending = []
        for py_file in src_dir.rglob("*.py"):
            if py_file.name == "soccer_1x2_stub.py":
                continue  # already covered by test_soccer_1x2_stub.py's own contract tests
            text = py_file.read_text(encoding="utf-8")
            for pattern in suspicious_numbers:
                if re.search(pattern, text):
                    offending.append((str(py_file.relative_to(REPO_ROOT)), pattern))
        self.assertEqual(
            offending,
            [],
            f"found a proposed backtest-threshold-shaped literal in runtime code: {offending}",
        )


if __name__ == "__main__":
    unittest.main()
