"""Tests for data_pipeline/dataset_builder.py — the Soccer 1X2 dataset
contract's season-range boundaries, the price/timestamp contract fields on
every built record, the explicit rejected_unique_rows-vs-
validation_issue_occurrences distinction, and hash determinism.

Only tests/fixtures/football_data/*.csv is ever used as input — this
sandbox has no live football-data.co.uk access (see
data_pipeline/FEASIBILITY_DECISION.md), so nothing here can or does prove
real dataset content; a real run happens later via GitHub Actions."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.dataset_builder import (
    CLOSING_ODDS_AVAILABLE_FROM_SEASON,
    DATASET_SPLIT_IDS,
    PRICE_STAGE_CLOSING,
    PRICE_STAGE_OPENING,
    PRICE_TIMESTAMP_QUALITY_STAGE_ONLY,
    build_dataset_report,
    build_file_records,
    build_season_1415_diagnostic,
    build_split,
    combined_snapshot_hash,
    excluded_specific_seasons,
    is_excluded_by_default,
    is_within_closing_odds_available_range,
    load_contract_rows,
    raw_file_path,
    resolve_split_season_codes,
    sha256_bytes,
)
from data_pipeline.download import season_codes
from data_pipeline.validation import validate_file

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"

# Maps (league_code, season_code) -> fixture filename, for every test that
# needs a populated raw directory. Only E0 is populated for most tests
# (single-league coverage is enough to prove the builder's logic; a
# multi-league real run is GitHub Actions' job, not this sandbox's).
FIXTURE_MAP = {
    ("E0", "1112"): "season_1112_opening_only.csv",
    ("E0", "1213"): "season_1213_with_closing.csv",
    ("E0", "1314"): "season_1314_for_drift.csv",
    ("E0", "1415"): "season_1415_outlier.csv",
    ("E0", "1516"): "season_1516_for_drift.csv",
    ("E0", "1819"): "season_1819_no_kickoff.csv",
    ("E0", "1920"): "season_1920_with_kickoff.csv",
    ("E0", "2526"): "season_2526_prospective.csv",
}


def _populate_raw_dir(raw_dir: Path, mapping: dict[tuple[str, str], str] = FIXTURE_MAP) -> None:
    for (league, season), filename in mapping.items():
        dest = raw_file_path(raw_dir, league, season)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / filename, dest)


class ContractLoadTests(unittest.TestCase):
    def test_contract_meta_row(self):
        rows = load_contract_rows()
        meta = rows["contract_meta"]
        self.assertEqual(meta["status"], "DRAFT_FOR_OPERATOR_REVIEW")
        self.assertTrue(meta["version"])

    def test_model_dev_and_closing_benchmark_seasons_match_season_codes(self):
        rows = load_contract_rows()
        expected = season_codes("1920", "2425")
        self.assertEqual(resolve_split_season_codes("split_model_dev_and_completed_eval", rows), expected)
        self.assertEqual(resolve_split_season_codes("split_closing_line_benchmark", rows), expected)

    def test_prospective_season_is_exactly_2526(self):
        rows = load_contract_rows()
        self.assertEqual(resolve_split_season_codes("split_prospective_paper_scoring", rows), ["2526"])

    def test_earlier_research_seasons_exclude_1415_by_default(self):
        rows = load_contract_rows()
        expected = [c for c in season_codes("1213", "1819") if c != "1415"]
        self.assertEqual(resolve_split_season_codes("split_earlier_research_backtesting", rows), expected)
        self.assertNotIn("1415", resolve_split_season_codes("split_earlier_research_backtesting", rows))


class ExclusionGateTests(unittest.TestCase):
    def test_pre_1213_season_excluded_by_default(self):
        self.assertTrue(is_excluded_by_default("1112"))
        self.assertTrue(is_excluded_by_default("9394"))

    def test_1415_excluded_by_default(self):
        self.assertTrue(is_excluded_by_default("1415"))
        self.assertIn("1415", excluded_specific_seasons())

    def test_seasons_not_excluded_by_default(self):
        for code in ("1213", "1819", "1920", "2526"):
            self.assertFalse(is_excluded_by_default(code), code)

    def test_opt_in_required_to_include_1415_in_earlier_research_split(self):
        rows = load_contract_rows()
        default_codes = resolve_split_season_codes("split_earlier_research_backtesting", rows)
        opted_in_codes = resolve_split_season_codes(
            "split_earlier_research_backtesting", rows, include_excluded_seasons=True
        )
        self.assertNotIn("1415", default_codes)
        self.assertIn("1415", opted_in_codes)
        # Opting in only ADDS 1415 — every other default-admitted season
        # stays admitted too.
        self.assertTrue(set(default_codes).issubset(set(opted_in_codes)))

    def test_opt_in_never_affects_splits_whose_range_never_included_1415(self):
        rows = load_contract_rows()
        for split_id in ("split_model_dev_and_completed_eval", "split_closing_line_benchmark", "split_prospective_paper_scoring"):
            default_codes = resolve_split_season_codes(split_id, rows)
            opted_in_codes = resolve_split_season_codes(split_id, rows, include_excluded_seasons=True)
            self.assertEqual(default_codes, opted_in_codes, split_id)

    def test_pre_1213_season_never_appears_in_any_split_even_opted_in(self):
        rows = load_contract_rows()
        for split_id in DATASET_SPLIT_IDS:
            for include in (False, True):
                codes = resolve_split_season_codes(split_id, rows, include_excluded_seasons=include)
                self.assertNotIn("1112", codes, (split_id, include))
                self.assertNotIn("9394", codes, (split_id, include))


class RangeBoundaryTests(unittest.TestCase):
    """This task's item 9: explicit range-boundary assertions."""

    def test_1213_included_in_closing_odds_available_range_1112_excluded(self):
        # "closing-line-benchmark range" = the general closing-odds-
        # availability boundary (evidence: closing odds present
        # continuously from 1213 onward, absent before it) — see
        # is_within_closing_odds_available_range's own docstring for why
        # this is distinct from split_closing_line_benchmark's own
        # (deliberately narrower, 1920-2425) season list.
        self.assertEqual(CLOSING_ODDS_AVAILABLE_FROM_SEASON, "1213")
        self.assertTrue(is_within_closing_odds_available_range("1213"))
        self.assertFalse(is_within_closing_odds_available_range("1112"))

    def test_1920_in_model_dev_range_1819_excluded(self):
        rows = load_contract_rows()
        codes = resolve_split_season_codes("split_model_dev_and_completed_eval", rows)
        self.assertIn("1920", codes)
        self.assertNotIn("1819", codes)

    def test_2526_only_in_prospective_split(self):
        rows = load_contract_rows()
        for split_id in DATASET_SPLIT_IDS:
            codes = resolve_split_season_codes(split_id, rows)
            if split_id == "split_prospective_paper_scoring":
                self.assertIn("2526", codes)
            else:
                self.assertNotIn("2526", codes, split_id)


class LeakageAndContractFieldTests(unittest.TestCase):
    """This task's item 8: leakage-shape assertions on actual built records."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmpdir.name) / "raw"
        _populate_raw_dir(self.raw_dir)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _records_for(self, league, season, include_kickoff_time=True):
        csv_path = raw_file_path(self.raw_dir, league, season)
        validation_result = validate_file(csv_path)
        return build_file_records(csv_path, league, season, validation_result, include_kickoff_time)

    def test_closing_observation_never_carries_opening_columns_and_vice_versa(self):
        records = self._records_for("E0", "1213")
        self.assertTrue(records)
        saw_opening = saw_closing = False
        for record in records:
            for obs in record["price_observations"]:
                self.assertIn(obs["price_stage"], (PRICE_STAGE_OPENING, PRICE_STAGE_CLOSING))
                if obs["price_stage"] == PRICE_STAGE_CLOSING:
                    saw_closing = True
                    # A closing observation's own dict has exactly this
                    # shape — it never additionally carries an opening
                    # bookmaker's own separate price fields merged in;
                    # each observation is one bookmaker x one stage only.
                    self.assertEqual(obs["price_stage"], PRICE_STAGE_CLOSING)
                    self.assertTrue(obs["is_pinnacle"])
                else:
                    saw_opening = True
        self.assertTrue(saw_opening)
        self.assertTrue(saw_closing)
        # Never two stages mixed into a single observation dict — every
        # observation is tagged with exactly one price_stage value.
        for record in records:
            stages = [obs["price_stage"] for obs in record["price_observations"]]
            for stage in stages:
                self.assertIn(stage, (PRICE_STAGE_OPENING, PRICE_STAGE_CLOSING))

    def test_every_record_has_unconditional_price_timestamp_contract_fields(self):
        for league_season in (("E0", "1213"), ("E0", "1920"), ("E0", "1819")):
            records = self._records_for(*league_season)
            self.assertTrue(records, league_season)
            for record in records:
                self.assertTrue(record["price_observations"])
                for obs in record["price_observations"]:
                    self.assertIsNone(obs["price_captured_at"])
                    self.assertEqual(obs["price_timestamp_quality"], PRICE_TIMESTAMP_QUALITY_STAGE_ONLY)

    def test_kickoff_time_only_populated_when_split_says_available(self):
        records_1920 = self._records_for("E0", "1920", include_kickoff_time=True)
        self.assertTrue(all(r["match_kickoff_at"] for r in records_1920))

        # Even though season 1819's fixture carries no Time column at all,
        # and season 1213's/1819's split contract says kickoff is
        # unavailable — match_kickoff_at must be None regardless of
        # include_kickoff_time, and must ALSO be forced None when
        # include_kickoff_time=False even for a file that happens to carry
        # a Time column, since the split's contract (not the file) governs
        # this field.
        records_1819 = self._records_for("E0", "1819", include_kickoff_time=False)
        self.assertTrue(records_1819)
        self.assertTrue(all(r["match_kickoff_at"] is None for r in records_1819))

        records_1920_suppressed = self._records_for("E0", "1920", include_kickoff_time=False)
        self.assertTrue(all(r["match_kickoff_at"] is None for r in records_1920_suppressed))

    def test_split_containing_closing_prices_is_tagged_with_required_model_label(self):
        rows = load_contract_rows()
        split_result = build_split(
            "split_closing_line_benchmark", raw_dir=self.raw_dir, contract_rows=rows
        )
        self.assertTrue(split_result["closing_line_benchmark_model_required"])
        self.assertEqual(split_result["closing_line_benchmark_model_label"], "CLOSING_LINE_BENCHMARK_MODEL")

    def test_split_without_any_closing_observation_is_not_tagged(self):
        rows = load_contract_rows()
        split_result = build_split(
            "split_prospective_paper_scoring", raw_dir=self.raw_dir, contract_rows=rows
        )
        # tests/fixtures/football_data/season_2526_prospective.csv carries
        # only opening (B365/PS, no PSC) columns — no closing observation
        # is ever built for it.
        for record in split_result["records"]:
            self.assertFalse(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in record["price_observations"]))
        self.assertFalse(split_result["closing_line_benchmark_model_required"])
        self.assertIsNone(split_result["closing_line_benchmark_model_label"])


class RejectedVsOccurrencesRegressionTests(unittest.TestCase):
    """This task's item 6/8 regression test: rejected_unique_rows and
    validation_issue_occurrences must stay explicitly distinct — a row
    tripping 3 typed reasons at once counts once toward the former, three
    times toward the latter."""

    def test_triple_rejection_row_is_counted_once_but_occurs_three_times(self):
        csv_path = FIXTURES / "triple_rejection_row.csv"
        result = validate_file(csv_path)
        occurrences = sum(result.rejection_reason_counts.values())

        self.assertEqual(result.total_rows, 2)
        self.assertEqual(result.usable_fixtures, 1)
        self.assertEqual(result.rejected_fixtures, 1)
        # Exactly this task's regression guard:
        self.assertEqual(result.rejected_fixtures, result.total_rows - result.usable_fixtures)
        self.assertLess(result.rejected_fixtures, occurrences)
        self.assertEqual(occurrences, 3)

    def test_build_split_totals_keep_the_same_distinction(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            dest = raw_file_path(raw_dir, "E0", "1213")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(FIXTURES / "triple_rejection_row.csv", dest)

            rows = load_contract_rows()
            split_result = build_split("split_earlier_research_backtesting", raw_dir=raw_dir, contract_rows=rows)
            totals = split_result["totals"]
            self.assertEqual(totals["rejected_unique_rows"], totals["source_rows"] - totals["usable_rows"])
            self.assertLess(totals["rejected_unique_rows"], totals["validation_issue_occurrences"])


class Season1415DiagnosticTests(unittest.TestCase):
    def test_diagnostic_reports_no_header_drift_and_stays_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            _populate_raw_dir(raw_dir)
            diagnostic = build_season_1415_diagnostic(raw_dir)
            self.assertEqual(diagnostic["status"], "UNRESOLVED_PENDING_HUMAN_REVIEW")
            e0 = diagnostic["per_league"]["E0"]
            self.assertTrue(e0["1314_vs_1415"]["available"])
            self.assertTrue(e0["1314_vs_1415"]["no_drift"])
            self.assertTrue(e0["1415_vs_1516"]["no_drift"])
            # A league with no files at all is reported honestly as
            # unavailable, never fabricated.
            self.assertFalse(diagnostic["per_league"]["D1"]["1314_vs_1415"]["available"])


class HashDeterminismTests(unittest.TestCase):
    """This task's item 10: same bytes -> same hash, a changed byte ->
    a different hash."""

    def test_sha256_is_deterministic(self):
        data = b"some dataset content"
        self.assertEqual(sha256_bytes(data), sha256_bytes(data))

    def test_sha256_changes_on_one_changed_byte(self):
        self.assertNotEqual(sha256_bytes(b"abc"), sha256_bytes(b"abd"))

    def test_combined_snapshot_hash_is_order_independent_and_deterministic(self):
        hashes_a = {"split_x": "aaa", "split_y": "bbb"}
        hashes_b = {"split_y": "bbb", "split_x": "aaa"}
        self.assertEqual(combined_snapshot_hash(hashes_a), combined_snapshot_hash(hashes_b))

    def test_combined_snapshot_hash_changes_when_a_split_hash_changes(self):
        hashes_a = {"split_x": "aaa", "split_y": "bbb"}
        hashes_b = {"split_x": "aaa", "split_y": "ccc"}
        self.assertNotEqual(combined_snapshot_hash(hashes_a), combined_snapshot_hash(hashes_b))

    def test_build_dataset_report_is_reproducible_for_identical_input(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            raw1, raw2 = Path(tmp1) / "raw", Path(tmp2) / "raw"
            _populate_raw_dir(raw1)
            _populate_raw_dir(raw2)
            rows = load_contract_rows()
            report1 = build_dataset_report(raw_dir=raw1, dataset_dir=Path(tmp1) / "dataset", contract_rows=rows)
            report2 = build_dataset_report(raw_dir=raw2, dataset_dir=Path(tmp2) / "dataset", contract_rows=rows)
            self.assertEqual(report1["dataset_snapshot_sha256"], report2["dataset_snapshot_sha256"])
            for split_id in DATASET_SPLIT_IDS:
                self.assertEqual(
                    report1["splits"][split_id]["dataset_file_sha256"],
                    report2["splits"][split_id]["dataset_file_sha256"],
                )

    def test_build_dataset_report_hash_changes_when_input_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            raw1, raw2 = Path(tmp1) / "raw", Path(tmp2) / "raw"
            _populate_raw_dir(raw1)
            _populate_raw_dir(raw2)
            # Mutate one byte of one file in raw2's copy of a season that
            # actually feeds a built record (an odds value), so the
            # produced dataset content genuinely differs.
            mutated_path = raw_file_path(raw2, "E0", "1213")
            original = mutated_path.read_text(encoding="utf-8")
            mutated_path.write_text(original.replace("3.60", "3.61", 1), encoding="utf-8")

            rows = load_contract_rows()
            report1 = build_dataset_report(raw_dir=raw1, dataset_dir=Path(tmp1) / "dataset", contract_rows=rows)
            report2 = build_dataset_report(raw_dir=raw2, dataset_dir=Path(tmp2) / "dataset", contract_rows=rows)
            self.assertNotEqual(report1["dataset_snapshot_sha256"], report2["dataset_snapshot_sha256"])


class MissingFileReportingTests(unittest.TestCase):
    def test_missing_raw_file_is_reported_never_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"  # deliberately empty
            rows = load_contract_rows()
            split_result = build_split("split_model_dev_and_completed_eval", raw_dir=raw_dir, contract_rows=rows)
            self.assertEqual(split_result["records"], [])
            self.assertTrue(all(f["missing"] for f in split_result["files"]))
            self.assertEqual(split_result["totals"]["source_rows"], 0)


if __name__ == "__main__":
    unittest.main()
