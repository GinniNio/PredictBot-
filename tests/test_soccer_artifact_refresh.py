"""Tests for the Soccer 1X2 Elo v1 artifact refresh lifecycle
(``src/pcbf_calculator/orchestration/soccer_artifact_refresh.py``) --
CANDIDATE creation only, never promotion.

Style matches this repo's existing convention: plain ``unittest.TestCase``,
reusing ``tests/test_soccer_1x2_elo_baseline.py``'s own
``BASELINE_FIXTURE_MAP``/raw_dir-population pattern so this module's own
training runs exercise the same real, reviewed fixture data that
module's own tests already do -- never a second, separately-maintained
notion of "test training data".
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_pipeline.dataset_builder import raw_file_path  # noqa: E402

from pcbf_calculator.orchestration import soccer_artifact_refresh as sar  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"

# Same fixture map test_soccer_1x2_elo_baseline.py's own TrainPipelineTests
# already uses -- see that module for what each entry covers.
BASELINE_FIXTURE_MAP = {
    ("E0", "1920"): "season_1920_with_kickoff.csv",
    ("E0", "2324"): "season_2324_for_elo_baseline.csv",
    ("E0", "2425"): "season_2425_for_elo_baseline.csv",
    ("E0", "2526"): "season_2526_prospective.csv",
    ("E0", "2627"): "season_2627_prospective.csv",
}

REAL_INCUMBENT_MANIFEST_PATH = (
    REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data" / "model_artifact_manifest.json"
)


def _populate_raw_dir(raw_dir: Path, mapping: dict = BASELINE_FIXTURE_MAP) -> None:
    for (league, season), filename in mapping.items():
        dest = raw_file_path(raw_dir, league, season)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / filename, dest)


class RefreshSoccerArtifactTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pcbf-artifact-refresh-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.tmp_path = Path(self.tmpdir)

        self.raw_dir = self.tmp_path / "raw"
        _populate_raw_dir(self.raw_dir)

        self.incumbent_manifest_path = self.tmp_path / "incumbent_manifest.json"
        shutil.copy(REAL_INCUMBENT_MANIFEST_PATH, self.incumbent_manifest_path)
        self.incumbent_manifest_mtime_before = self.incumbent_manifest_path.stat().st_mtime
        self.incumbent_manifest_bytes_before = self.incumbent_manifest_path.read_bytes()

        self.incumbent_performance_dir = self.tmp_path / "incumbent_perf"
        self.incumbent_performance_dir.mkdir()
        (self.incumbent_performance_dir / "performance-summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "pcbf-forecast-performance-summary.v1",
                    "reconciles": True,
                    "small_sample_threshold": 50,
                    "overall": {"sample_count": 9, "small_sample": True, "brier_score": 0.49, "log_loss": 0.845, "accuracy": 0.667},
                }
            ),
            encoding="utf-8",
        )

    def _run(self, output_dir: Path):
        return sar.refresh_soccer_artifact(
            training_input=self.raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=self.incumbent_performance_dir,
            output_dir=output_dir,
            generation_command="python -m pcbf_calculator refresh-soccer-artifact --training-input X",
        )

    def test_produces_every_required_output_file(self):
        output_dir = self.tmp_path / "candidate_out"
        self._run(output_dir)

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

    def test_recommendation_is_always_hold_for_prospective_evidence(self):
        result = self._run(self.tmp_path / "candidate_out")
        self.assertEqual(
            result["promotion_review"]["recommendation"],
            sar.RECOMMENDATION_HOLD_FOR_PROSPECTIVE_EVIDENCE,
        )
        self.assertEqual(result["promotion_review"]["recommendation"], "HOLD_FOR_PROSPECTIVE_EVIDENCE")

    def test_candidate_prospective_metrics_are_always_null(self):
        result = self._run(self.tmp_path / "candidate_out")
        self.assertIsNone(result["artifact_comparison"]["candidate_prospective_metrics"])
        self.assertIsNone(result["promotion_review"]["candidate_prospective_metrics_summary"])

    def test_incumbent_prospective_metrics_are_reported_separately_and_never_compared(self):
        result = self._run(self.tmp_path / "candidate_out")
        comparison = result["artifact_comparison"]
        incumbent_prospective = comparison["incumbent_prospective_metrics"]
        self.assertIsNotNone(incumbent_prospective)
        self.assertEqual(incumbent_prospective["sample_count"], 9)
        self.assertTrue(incumbent_prospective["small_sample"])
        # The backtest_comparison block must never contain the incumbent's
        # prospective numbers -- only backtest_evidence-shaped dicts.
        for split_entry in comparison["backtest_comparison"].values():
            self.assertNotIn("sample_count", split_entry.get("incumbent") or {})

    def test_missing_incumbent_performance_file_is_handled_gracefully(self):
        empty_perf_dir = self.tmp_path / "no_performance_yet"
        empty_perf_dir.mkdir()
        result = sar.refresh_soccer_artifact(
            training_input=self.raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=empty_perf_dir,
            output_dir=self.tmp_path / "candidate_out",
            generation_command="cmd",
        )
        self.assertIsNone(result["artifact_comparison"]["incumbent_prospective_metrics"])
        # Still succeeds and still recommends HOLD -- a missing prospective
        # history is a legitimate state, never a hard failure.
        self.assertEqual(result["promotion_review"]["recommendation"], "HOLD_FOR_PROSPECTIVE_EVIDENCE")

    def test_incumbent_manifest_is_never_mutated(self):
        self._run(self.tmp_path / "candidate_out")
        self.assertEqual(self.incumbent_manifest_path.read_bytes(), self.incumbent_manifest_bytes_before)
        self.assertEqual(self.incumbent_manifest_path.stat().st_mtime, self.incumbent_manifest_mtime_before)

    def test_never_touches_the_real_shipped_adapter_data_directory(self):
        real_data_dir = REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data"
        before = {p: p.read_bytes() for p in real_data_dir.iterdir() if p.is_file()}
        self._run(self.tmp_path / "candidate_out")
        after = {p: p.read_bytes() for p in real_data_dir.iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_refuses_to_overwrite_an_existing_output_dir(self):
        output_dir = self.tmp_path / "candidate_out"
        self._run(output_dir)
        with self.assertRaises(sar.CandidateOutputExistsError):
            self._run(output_dir)

    def test_two_identical_inputs_produce_byte_identical_bundle_hash_and_three_of_four_files(self):
        out_a = self.tmp_path / "out_a"
        out_b = self.tmp_path / "out_b"
        self._run(out_a)
        self._run(out_b)

        bundle_a = json.loads((out_a / "candidate" / "candidate_bundle_manifest.json").read_text())
        bundle_b = json.loads((out_b / "candidate" / "candidate_bundle_manifest.json").read_text())
        self.assertEqual(bundle_a["bundle_hash"], bundle_b["bundle_hash"])
        self.assertEqual(bundle_a["build_identity"], bundle_b["build_identity"])
        # The whole bundle manifest file must match too, EXCEPT
        # evaluation_report.json's own raw file_sha256 -- its
        # deterministic_content_hash (what bundle_hash is actually built
        # from) already matches above; file_sha256 is the raw file's own
        # hash, recorded for audit only, and legitimately differs because
        # of the file's one real wall-clock field (which can even happen
        # to coincide across a fast test run at second precision -- never
        # assert on it directly, timing-sensitive either way).
        self.assertEqual(
            bundle_a["files"]["evaluation_report.json"]["deterministic_content_hash"],
            bundle_b["files"]["evaluation_report.json"]["deterministic_content_hash"],
        )
        normalized_a = json.loads(json.dumps(bundle_a))
        normalized_b = json.loads(json.dumps(bundle_b))
        del normalized_a["files"]["evaluation_report.json"]["file_sha256"]
        del normalized_b["files"]["evaluation_report.json"]["file_sha256"]
        self.assertEqual(normalized_a, normalized_b)

        for filename in ("model_artifact.json", "live_snapshot.json", "model_artifact_manifest.json"):
            self.assertEqual(
                (out_a / "candidate" / filename).read_bytes(),
                (out_b / "candidate" / filename).read_bytes(),
                filename,
            )

        # evaluation_report.json legitimately differs by exactly one
        # real timestamp field -- everything else must still match.
        report_a = json.loads((out_a / "candidate" / "evaluation_report.json").read_text())
        report_b = json.loads((out_b / "candidate" / "evaluation_report.json").read_text())
        self.assertEqual(
            sar._strip_prospective_run_timestamp(report_a),
            sar._strip_prospective_run_timestamp(report_b),
        )

    def test_generation_command_excludes_output_dir_but_keeps_real_inputs(self):
        result = self._run(self.tmp_path / "candidate_out")
        recorded = result["candidate_manifest"]["provenance"]["generation_command"]
        self.assertNotIn("candidate_out", recorded)
        self.assertIn("--training-input", recorded)

    def test_verify_candidate_bundle_passes_on_a_freshly_built_candidate(self):
        output_dir = self.tmp_path / "candidate_out"
        self._run(output_dir)
        sar.verify_candidate_bundle(output_dir / "candidate")  # must not raise

    def test_verify_candidate_bundle_detects_a_tampered_or_mixed_in_file(self):
        output_dir = self.tmp_path / "candidate_out"
        self._run(output_dir)
        candidate_dir = output_dir / "candidate"

        live_snapshot_path = candidate_dir / "live_snapshot.json"
        data = json.loads(live_snapshot_path.read_text())
        data["team_count"] = 999999
        live_snapshot_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaises(sar.CandidateBundleVerificationError) as ctx:
            sar.verify_candidate_bundle(candidate_dir)
        self.assertIn("live_snapshot.json", str(ctx.exception))

    def test_hash_or_provenance_failure_aborts_before_publishing_output_dir(self):
        # A LIVE_SOURCE_VALIDATED-labeled evaluation report whose frozen
        # hashes MISMATCH a pinned expected value is refused -- monkeypatch
        # run_training_and_evaluation to simulate exactly that failure
        # mode without needing a real live download.
        original = sar.run_training_and_evaluation

        def _raise(*args, **kwargs):
            raise sar.HashProvenanceError("simulated mismatch for this test")

        sar.run_training_and_evaluation = _raise
        try:
            output_dir = self.tmp_path / "candidate_out_never_created"
            with self.assertRaises(sar.HashProvenanceError):
                self._run(output_dir)
            self.assertFalse(output_dir.exists())
        finally:
            sar.run_training_and_evaluation = original

    def test_backtest_comparison_never_crashes_on_a_zero_row_split(self):
        # A candidate trained on a raw_dir that produces zero rows for one
        # of the two backtest splits (row_count=0 -> brier_score/log_loss
        # are None) must not raise -- delta is simply omitted, never a
        # crash or a fabricated zero.
        sparse_raw_dir = self.tmp_path / "sparse_raw"
        dest = raw_file_path(sparse_raw_dir, "E0", "1920")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_1920_with_kickoff.csv", dest)

        result = sar.refresh_soccer_artifact(
            training_input=sparse_raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=self.incumbent_performance_dir,
            output_dir=self.tmp_path / "sparse_candidate_out",
            generation_command="cmd",
        )
        for split_entry in result["artifact_comparison"]["backtest_comparison"].values():
            self.assertIn("delta_note", split_entry)

    def test_swapping_in_a_valid_file_from_a_genuinely_different_run_is_detected(self):
        # Stronger than tampering a single field: build two candidates
        # from genuinely different, real training inputs, then swap one
        # run's own valid, self-consistent live_snapshot.json into the
        # other's candidate directory. Both files are individually
        # "valid" (well-formed, real output of this same pipeline) --
        # verify_candidate_bundle must still catch the mix.
        sparse_raw_dir = self.tmp_path / "sparse_raw_for_swap"
        dest = raw_file_path(sparse_raw_dir, "E0", "1920")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_1920_with_kickoff.csv", dest)

        out_full = self.tmp_path / "out_full_for_swap"
        out_sparse = self.tmp_path / "out_sparse_for_swap"
        self._run(out_full)
        sar.refresh_soccer_artifact(
            training_input=sparse_raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=self.incumbent_performance_dir,
            output_dir=out_sparse,
            generation_command="cmd",
        )

        full_snapshot = (out_full / "candidate" / "live_snapshot.json").read_bytes()
        sparse_snapshot = (out_sparse / "candidate" / "live_snapshot.json").read_bytes()
        self.assertNotEqual(full_snapshot, sparse_snapshot, "fixtures must actually differ for this test to mean anything")

        shutil.copy(out_sparse / "candidate" / "live_snapshot.json", out_full / "candidate" / "live_snapshot.json")
        # build_identity disagreement is caught first (a more specific
        # diagnosis of the same underlying mix) -- see
        # test_mixing_files_from_two_executions_sharing_the_same_code_hash_
        # fails_before_publication for the case where the two runs share
        # an identical code_hash and only build_identity tells them apart.
        with self.assertRaises(sar.BuildIdentityMismatchError) as ctx:
            sar.verify_candidate_bundle(out_full / "candidate")
        self.assertIn("live_snapshot.json", str(ctx.exception))

    def test_build_identity_is_stamped_identically_across_all_five_locations(self):
        output_dir = self.tmp_path / "candidate_out_build_identity"
        self._run(output_dir)
        candidate_dir = output_dir / "candidate"

        model_artifact = json.loads((candidate_dir / "model_artifact.json").read_text())
        live_snapshot = json.loads((candidate_dir / "live_snapshot.json").read_text())
        evaluation_report = json.loads((candidate_dir / "evaluation_report.json").read_text())
        manifest = json.loads((candidate_dir / "model_artifact_manifest.json").read_text())
        bundle_manifest = json.loads((candidate_dir / "candidate_bundle_manifest.json").read_text())

        identity = model_artifact["build_identity"]
        self.assertTrue(identity)
        for payload in (live_snapshot, evaluation_report, manifest, bundle_manifest):
            self.assertEqual(payload["build_identity"], identity)

    def test_two_executions_sharing_the_same_code_hash_but_different_data_have_different_build_identity(self):
        # The exact scenario a bare code_hash comparison cannot catch:
        # two runs of the IDENTICAL training code (necessarily the same
        # code_hash, since both run in this same process/checkout)
        # against genuinely different training data must still produce
        # DIFFERENT build_identity values.
        sparse_raw_dir = self.tmp_path / "sparse_raw_for_identity"
        dest = raw_file_path(sparse_raw_dir, "E0", "1920")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_1920_with_kickoff.csv", dest)

        out_full = self.tmp_path / "out_full_for_identity"
        out_sparse = self.tmp_path / "out_sparse_for_identity"
        self._run(out_full)
        sar.refresh_soccer_artifact(
            training_input=sparse_raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=self.incumbent_performance_dir,
            output_dir=out_sparse,
            generation_command="cmd",
        )

        report_full = json.loads((out_full / "candidate" / "evaluation_report.json").read_text())
        report_sparse = json.loads((out_sparse / "candidate" / "evaluation_report.json").read_text())
        self.assertEqual(
            report_full["code_hash"], report_sparse["code_hash"],
            "both runs use the identical training code in this same process/checkout",
        )
        self.assertNotEqual(
            report_full["build_identity"], report_sparse["build_identity"],
            "identical code_hash must NOT imply identical build_identity -- the data differs",
        )

    def test_mixing_files_from_two_executions_sharing_the_same_code_hash_fails_before_publication(self):
        # Builds two full candidates (same training-package code in this
        # process => identical code_hash for both), then mixes one run's
        # live_snapshot.json into the other's candidate directory before
        # the bundle/manifest/publish steps run -- reproducing the exact
        # defect a bare code_hash comparison would miss. Must fail BEFORE
        # anything is published, via a dedicated build_identity check
        # rather than only incidentally via a content-hash mismatch.
        sparse_raw_dir = self.tmp_path / "sparse_raw_for_mixing"
        dest = raw_file_path(sparse_raw_dir, "E0", "1920")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_1920_with_kickoff.csv", dest)

        # Build the "foreign" run's own live_snapshot.json bytes (with its
        # own, different build_identity already stamped in) by running
        # the sparse input through the real pipeline first.
        out_sparse = self.tmp_path / "out_sparse_for_mixing"
        sar.refresh_soccer_artifact(
            training_input=sparse_raw_dir,
            incumbent_manifest_path=self.incumbent_manifest_path,
            incumbent_performance_dir=self.incumbent_performance_dir,
            output_dir=out_sparse,
            generation_command="cmd",
        )
        foreign_live_snapshot_bytes = (out_sparse / "candidate" / "live_snapshot.json").read_bytes()

        foreign_code_hash = json.loads(foreign_live_snapshot_bytes)["code_hash"]

        # Now run the full-fixture input, but splice in the foreign
        # live_snapshot.json in place of this run's own -- right after
        # write, before build_manifest/bundle/publish run.
        original_write_json = sar._write_json
        write_calls = {"n": 0}

        def _tampered_write_json(path, payload):
            write_calls["n"] += 1
            if path.name == "live_snapshot.json":
                self.assertEqual(
                    payload["code_hash"], foreign_code_hash,
                    "sanity check: both runs share the identical code_hash",
                )
                path.write_bytes(foreign_live_snapshot_bytes)
                return
            original_write_json(path, payload)

        sar._write_json = _tampered_write_json
        try:
            output_dir = self.tmp_path / "candidate_out_mixed_identity"
            with self.assertRaises(sar.BuildIdentityMismatchError):
                self._run(output_dir)
            self.assertFalse(output_dir.exists())
        finally:
            sar._write_json = original_write_json

    def test_a_directory_appearing_at_output_dir_after_the_initial_check_is_refused_not_nested(self):
        # Simulates the real TOCTOU window between refresh_soccer_artifact's
        # own early output_dir.exists() guard and its final publish step
        # (e.g. a concurrent refresh-soccer-artifact run finishing first
        # against the identical --output-dir). Publication must refuse
        # outright -- never silently nest the new build inside the
        # existing directory while reporting success.
        output_dir = self.tmp_path / "raced_output_dir"
        output_dir.mkdir()
        marker = output_dir / "PRE_EXISTING_MARKER.txt"
        marker.write_text("do not touch me", encoding="utf-8")

        original_exists = Path.exists
        call_count = {"n": 0}

        def _fake_exists(self):
            if self == output_dir:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return False  # let refresh_soccer_artifact's own early check pass
            return original_exists(self)

        Path.exists = _fake_exists
        try:
            with self.assertRaises(sar.CandidateOutputExistsError):
                self._run(output_dir)
        finally:
            Path.exists = original_exists

        self.assertEqual(sorted(p.name for p in output_dir.iterdir()), ["PRE_EXISTING_MARKER.txt"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "do not touch me")

    def test_publish_falls_back_correctly_across_a_simulated_cross_device_boundary(self):
        output_dir = self.tmp_path / "cross_device_out"
        original_rename = os.rename
        calls = []

        def _fake_rename(src, dst, *a, **kw):
            calls.append((str(src), str(dst)))
            if len(calls) == 1:
                raise OSError(errno.EXDEV, "Invalid cross-device link (simulated)")
            return original_rename(src, dst, *a, **kw)

        os.rename = _fake_rename
        try:
            result = self._run(output_dir)
        finally:
            os.rename = original_rename

        self.assertTrue((output_dir / "candidate" / "model_artifact.json").exists())
        sar.verify_candidate_bundle(output_dir / "candidate")  # must not raise
        self.assertGreaterEqual(len(calls), 2)  # the failed same-fs attempt, then the fallback's own final rename
        leftover_hidden_dirs = [
            p.name for p in output_dir.parent.iterdir()
            if p.name.startswith(".pcbf-soccer-artifact-refresh-staging-")
        ]
        self.assertEqual(leftover_hidden_dirs, [])
        self.assertEqual(result["bundle_manifest"]["bundle_hash"], json.loads(
            (output_dir / "candidate" / "candidate_bundle_manifest.json").read_text()
        )["bundle_hash"])


class CurrentCommitShaTests(unittest.TestCase):
    def test_never_raises_even_outside_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            sha = sar.current_commit_sha(repo_root=Path(tmp))
            self.assertIsInstance(sha, str)
            self.assertTrue(sha)


class ArgvExcludingOutputDirTests(unittest.TestCase):
    def test_strips_space_separated_form(self):
        argv = ["--training-input", "a", "--output-dir", "b", "--incumbent-manifest", "c"]
        filtered = sar._argv_excluding_output_dir(argv)
        self.assertNotIn("--output-dir", filtered)
        self.assertNotIn("b", filtered)
        self.assertIn("--training-input", filtered)

    def test_strips_equals_form(self):
        argv = ["--output-dir=b", "--training-input", "a"]
        filtered = sar._argv_excluding_output_dir(argv)
        self.assertEqual(filtered, ["--training-input", "a"])


if __name__ == "__main__":
    unittest.main()
