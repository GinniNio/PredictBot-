"""Tests for the Soccer 1X2 Elo v1 adapter's manifest-building tool
(``src/pcbf_calculator/adapters/soccer_1x2_elo_v1/build_manifest.py``).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.adapters.soccer_1x2_elo_v1.build_manifest import (
    EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED,
    EVIDENCE_TRACK_CONFIRMED_ACTIVE,
    build_manifest,
)


def _write(tmp: Path, name: str, payload: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def make_real_shaped_inputs(tmp: Path) -> tuple[Path, Path, Path]:
    artifact = {"training_row_count": 7201, "weights": [[0.0] * 9] * 3, "biases": [0.1, 0.0, -0.1]}
    evaluation_report = {
        "evidence_class": "LIVE_SOURCE_VALIDATED",
        "temperature": 1.06,
        "code_hash": "abc123",
        "frozen_hashes": {
            "combined_hash": "combined-real-hash",
            "split_hashes": {"split_training": "training-hash"},
        },
        "hash_provenance": {
            "combined_check": {"status": "CONFIRMED"},
            "split_checks": [{"split_id": "split_training", "status": "CONFIRMED"}],
        },
        "test_splits": {
            "split_locked_test": {
                "model_metrics": {"row_count": 1752, "brier_score": 0.5896, "log_loss": 0.9884},
                "naive_league_frequency_baseline_metrics": {"brier_score": 0.6526},
                "devigged_opening_odds_metrics": {"brier_score": 0.5734},
            }
        },
    }
    live_snapshot = {
        "last_processed_match_date_utc": "2026-09-01T00:00:00Z",
        "latest_season_by_league": {"E0": "2526"},
        "elo_ratings": {"E0|Arsenal": 1620.5, "E0|Fulham": 1480.2, "D1|Bayern": 1700.0},
        "season_stage_counts": {"E0|2526|Arsenal": 4},
    }
    return (
        _write(tmp, "model_artifact.json", artifact),
        _write(tmp, "evaluation_report.json", evaluation_report),
        _write(tmp, "live_snapshot.json", live_snapshot),
    )


class BuildManifestTests(unittest.TestCase):
    def test_builds_manifest_with_full_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            manifest = build_manifest(
                artifact_path,
                eval_path,
                snap_path,
                "https://github.com/x/y/actions/runs/123",
                "123",
                "deadbeef",
                "soccer_1x2_elo_v1-2026-09",
                "python -m ... build_manifest ...",
            )
            provenance = manifest["provenance"]
            self.assertEqual(provenance["workflow_run_id"], "123")
            self.assertEqual(provenance["training_code_commit_sha"], "deadbeef")
            self.assertEqual(provenance["evidence_class"], "LIVE_SOURCE_VALIDATED")
            self.assertEqual(provenance["hash_provenance_status"], "ALL_CONFIRMED")
            self.assertEqual(provenance["training_match_count"], 7201)
            self.assertEqual(provenance["team_count"], 3)
            self.assertEqual(provenance["competition_count"], 2)
            self.assertEqual(provenance["rating_count"], 3)
            self.assertEqual(provenance["last_processed_match_date_utc"], "2026-09-01T00:00:00Z")
            self.assertIn("split_locked_test", provenance["backtest_evidence"])
            self.assertEqual(manifest["live_snapshot"]["elo_ratings"]["E0|Arsenal"], 1620.5)

    def test_refuses_when_evidence_class_not_live_source_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["evidence_class"] = "SOURCE_UNAVAILABLE"
            eval_path.write_text(json.dumps(evaluation_report))
            with self.assertRaises(ValueError):
                build_manifest(artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd")

    def test_refuses_on_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["hash_provenance"]["combined_check"]["status"] = "MISMATCH"
            eval_path.write_text(json.dumps(evaluation_report))
            with self.assertRaises(ValueError):
                build_manifest(artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd")

    def test_default_evidence_track_is_confirmed_active_and_unchanged_in_behavior(self):
        # The rename from a bare bool to a typed evidence_track enum must
        # not change this function's default behavior at all -- omitting
        # the keyword entirely (every pre-existing caller's own call
        # shape) must still enforce the original CONFIRMED-only gate.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["evidence_class"] = "FIXTURE_ONLY_VALIDATED"
            eval_path.write_text(json.dumps(evaluation_report))
            with self.assertRaises(ValueError):
                build_manifest(
                    artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                    evidence_track=EVIDENCE_TRACK_CONFIRMED_ACTIVE,
                )

    def test_rejects_an_unrecognized_evidence_track_value(self):
        # No third, looser state -- anything other than the two named
        # constants is refused outright, never silently treated as one
        # of them.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            with self.assertRaises(ValueError):
                build_manifest(
                    artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                    evidence_track="SOMETHING_ELSE",
                )
            with self.assertRaises(ValueError):
                build_manifest(
                    artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                    evidence_track=False,  # the old bare-bool spelling must not still work
                )

    def test_candidate_unconfirmed_track_permits_an_unconfirmed_live_source_validated_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["hash_provenance"]["combined_check"]["status"] = "MISMATCH"
            evaluation_report["evidence_class"] = "FIXTURE_ONLY_VALIDATED"
            eval_path.write_text(json.dumps(evaluation_report))
            manifest = build_manifest(
                artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                evidence_track=EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED,
            )
            self.assertEqual(manifest["provenance"]["hash_provenance_status"], "NOT_YET_HUMAN_CONFIRMED")

    def test_candidate_unconfirmed_track_still_refuses_zero_usable_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["evidence_class"] = "SOURCE_UNAVAILABLE"
            eval_path.write_text(json.dumps(evaluation_report))
            with self.assertRaises(ValueError):
                build_manifest(
                    artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                    evidence_track=EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED,
                )

    def test_candidate_unconfirmed_track_still_refuses_a_genuine_live_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            artifact_path, eval_path, snap_path = make_real_shaped_inputs(tmp)
            evaluation_report = json.loads(eval_path.read_text())
            evaluation_report["hash_provenance"]["combined_check"]["status"] = "MISMATCH"
            evaluation_report["hash_provenance"]["has_mismatch"] = True
            # evidence_class stays LIVE_SOURCE_VALIDATED -- real data drift.
            eval_path.write_text(json.dumps(evaluation_report))
            with self.assertRaises(ValueError):
                build_manifest(
                    artifact_path, eval_path, snap_path, "url", "1", "sha", "v", "cmd",
                    evidence_track=EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED,
                )


if __name__ == "__main__":
    unittest.main()
