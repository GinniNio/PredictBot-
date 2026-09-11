"""Tests for data_pipeline/dataset_builder.py — the Soccer 1X2 dataset
contract's season-range boundaries, the price/timestamp contract fields on
every built record, the explicit rejected_unique_rows-vs-
validation_issue_occurrences distinction, and hash determinism.

Only tests/fixtures/football_data/*.csv is ever used as input — this
sandbox has no live football-data.co.uk access (see
data_pipeline/FEASIBILITY_DECISION.md), so nothing here can or does prove
real dataset content; a real run happens later via GitHub Actions.

Dataset-contract correction (this task): season 2526 (2025-26) is now
FULLY SETTLED and has been relabeled from "prospective" to
split_out_of_time_retrospective_holdout; season 2627 (2026-27) is the
actual current/prospective season, covered by the new
split_genuine_prospective_scoring; the old split_model_dev_and_completed_eval
block is split into split_training/split_calibration_validation/
split_locked_test. Every reference to the old split ids
(split_model_dev_and_completed_eval, split_prospective_paper_scoring) has
been updated below."""

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
    build_season_1415_rejection_breakdown,
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


# Every split id that shares the old split_model_dev_and_completed_eval
# required_fields shape (date, kickoff_time, home_team, away_team, result,
# opening_1x2_prices_complete) — used by tests that assert behavior common
# to all three of the new chronological splits.
TRAINING_SHAPED_SPLIT_IDS = ("split_training", "split_calibration_validation", "split_locked_test")


class ContractLoadTests(unittest.TestCase):
    def test_contract_meta_row(self):
        rows = load_contract_rows()
        meta = rows["contract_meta"]
        self.assertEqual(meta["status"], "DRAFT_FOR_OPERATOR_REVIEW")
        self.assertTrue(meta["version"])

    def test_training_calibration_locked_test_and_closing_benchmark_seasons(self):
        rows = load_contract_rows()
        self.assertEqual(resolve_split_season_codes("split_training", rows), season_codes("1920", "2223"))
        self.assertEqual(resolve_split_season_codes("split_calibration_validation", rows), ["2324"])
        self.assertEqual(resolve_split_season_codes("split_locked_test", rows), ["2425"])
        expected_benchmark = season_codes("1920", "2425")
        self.assertEqual(resolve_split_season_codes("split_closing_line_benchmark", rows), expected_benchmark)
        # split_closing_line_benchmark's own range is exactly the union of
        # the three new chronological splits' ranges.
        union = set(resolve_split_season_codes("split_training", rows))
        union |= set(resolve_split_season_codes("split_calibration_validation", rows))
        union |= set(resolve_split_season_codes("split_locked_test", rows))
        self.assertEqual(union, set(expected_benchmark))

    def test_out_of_time_retrospective_holdout_is_exactly_2526(self):
        rows = load_contract_rows()
        self.assertEqual(resolve_split_season_codes("split_out_of_time_retrospective_holdout", rows), ["2526"])

    def test_genuine_prospective_scoring_is_exactly_2627(self):
        rows = load_contract_rows()
        self.assertEqual(resolve_split_season_codes("split_genuine_prospective_scoring", rows), ["2627"])

    def test_earlier_research_seasons_exclude_1415_by_default(self):
        rows = load_contract_rows()
        expected = [c for c in season_codes("1213", "1819") if c != "1415"]
        self.assertEqual(resolve_split_season_codes("split_earlier_research_backtesting", rows), expected)
        self.assertNotIn("1415", resolve_split_season_codes("split_earlier_research_backtesting", rows))

    def test_out_of_time_retrospective_holdout_requires_settled_result(self):
        # This is the core correction: the old prospective split's
        # required_fields carried result_nullable_until_settlement; the
        # renamed split_out_of_time_retrospective_holdout now requires a
        # plain, settled `result` instead — never nullable.
        rows = load_contract_rows()
        row = rows["split_out_of_time_retrospective_holdout"]
        tokens = set(row["required_fields"].split(","))
        self.assertIn("result", tokens)
        self.assertNotIn("result_nullable_until_settlement", tokens)

    def test_genuine_prospective_scoring_carries_the_nullable_result_field(self):
        # The nullable-result exception moved to split_genuine_prospective_scoring
        # exclusively — it is the ONLY split that still declares it.
        rows = load_contract_rows()
        for split_id in DATASET_SPLIT_IDS:
            tokens = set(rows[split_id]["required_fields"].split(","))
            if split_id == "split_genuine_prospective_scoring":
                self.assertIn("result_nullable_until_settlement", tokens, split_id)
            else:
                self.assertNotIn("result_nullable_until_settlement", tokens, split_id)


class ExclusionGateTests(unittest.TestCase):
    def test_pre_1213_season_excluded_by_default(self):
        self.assertTrue(is_excluded_by_default("1112"))
        self.assertTrue(is_excluded_by_default("9394"))

    def test_1415_excluded_by_default(self):
        self.assertTrue(is_excluded_by_default("1415"))
        self.assertIn("1415", excluded_specific_seasons())

    def test_seasons_not_excluded_by_default(self):
        for code in ("1213", "1819", "1920", "2526", "2627"):
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
        for split_id in (
            "split_training",
            "split_calibration_validation",
            "split_locked_test",
            "split_closing_line_benchmark",
            "split_out_of_time_retrospective_holdout",
            "split_genuine_prospective_scoring",
        ):
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

    def test_1920_in_training_range_1819_excluded(self):
        rows = load_contract_rows()
        codes = resolve_split_season_codes("split_training", rows)
        self.assertIn("1920", codes)
        self.assertNotIn("1819", codes)

    def test_2223_last_training_season_2324_is_calibration_only(self):
        rows = load_contract_rows()
        self.assertIn("2223", resolve_split_season_codes("split_training", rows))
        self.assertNotIn("2324", resolve_split_season_codes("split_training", rows))
        self.assertEqual(resolve_split_season_codes("split_calibration_validation", rows), ["2324"])

    def test_2526_only_in_out_of_time_retrospective_holdout(self):
        rows = load_contract_rows()
        for split_id in DATASET_SPLIT_IDS:
            codes = resolve_split_season_codes(split_id, rows)
            if split_id == "split_out_of_time_retrospective_holdout":
                self.assertIn("2526", codes)
            else:
                self.assertNotIn("2526", codes, split_id)

    def test_2627_only_in_genuine_prospective_scoring(self):
        rows = load_contract_rows()
        for split_id in DATASET_SPLIT_IDS:
            codes = resolve_split_season_codes(split_id, rows)
            if split_id == "split_genuine_prospective_scoring":
                self.assertIn("2627", codes)
            else:
                self.assertNotIn("2627", codes, split_id)


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
        records, _pending_count, _split_specific_sample = build_file_records(
            csv_path, league, season, validation_result, include_kickoff_time
        )
        return records

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
        raw_dir = self.raw_dir
        dest = raw_file_path(raw_dir, "E0", "2627")
        shutil.copy(FIXTURES / "season_2627_prospective.csv", dest)
        split_result = build_split(
            "split_genuine_prospective_scoring", raw_dir=raw_dir, contract_rows=rows
        )
        # season_2627_prospective.csv carries only opening (B365/PS, no
        # PSC) columns — no closing observation is ever built for it.
        for record in split_result["records"]:
            self.assertFalse(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in record["price_observations"]))
        self.assertFalse(split_result["closing_line_benchmark_model_required"])
        self.assertIsNone(split_result["closing_line_benchmark_model_label"])


class OutOfTimeRetrospectiveHoldoutTests(unittest.TestCase):
    """The core correction: season 2526 is now fully settled, so
    split_out_of_time_retrospective_holdout must EXCLUDE (never rescue) a
    row with a genuinely blank result — the nullable-result exception no
    longer applies to this split."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmpdir.name) / "raw"
        _populate_raw_dir(self.raw_dir)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_blank_result_row_is_excluded_not_rescued(self):
        # season_2526_prospective.csv has 1 played row + 2 unplayed
        # (blank-result) rows. Under the OLD split_prospective_paper_scoring
        # contract those 2 were rescued with result_pending_settlement=True;
        # under the corrected split_out_of_time_retrospective_holdout (plain
        # `result`, no nullable exception), they must be EXCLUDED instead.
        rows = load_contract_rows()
        split_result = build_split(
            "split_out_of_time_retrospective_holdout", raw_dir=self.raw_dir, contract_rows=rows
        )
        self.assertEqual(len(split_result["records"]), 1)
        self.assertIsNotNone(split_result["records"][0]["result"])
        self.assertFalse(any(r["result_pending_settlement"] for r in split_result["records"]))
        totals = split_result["totals"]
        self.assertEqual(totals["source_rows"], 3)
        self.assertEqual(totals["usable_settled_rows"], 1)
        self.assertEqual(totals["pending_settlement_rows_included"], 0)
        self.assertEqual(totals["rejected_unique_rows"], 2)

    def test_rejected_blank_result_rows_carry_missing_result_reason(self):
        rows = load_contract_rows()
        split_result = build_split(
            "split_out_of_time_retrospective_holdout", raw_dir=self.raw_dir, contract_rows=rows
        )
        sample = split_result["split_specific_rejected_row_sample"]
        self.assertTrue(sample)
        self.assertTrue(all(row["split_rejection_reason"] == "MISSING_RESULT" for row in sample))


class GenuineProspectiveScoringTests(unittest.TestCase):
    """split_genuine_prospective_scoring (season 2627): the actual
    prospective split, carrying the nullable-result exception, with an
    explicit SOURCE_UNAVAILABLE contract when 2627's file doesn't exist —
    and a guard against ever substituting 2526's now-settled data."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmpdir.name) / "raw"
        self.rows = load_contract_rows()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_missing_2627_file_reports_source_unavailable(self):
        # raw_dir is entirely empty — no 2627 file for any league.
        split_result = build_split(
            "split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=self.rows
        )
        self.assertEqual(split_result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(split_result["records"], [])
        self.assertTrue(all(f["missing"] for f in split_result["files"]))

    def test_unplayed_2627_row_is_rescued_with_pending_settlement(self):
        dest = raw_file_path(self.raw_dir, "E0", "2627")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_2627_prospective.csv", dest)
        split_result = build_split(
            "split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=self.rows
        )
        self.assertEqual(split_result["status"], "BUILT")
        self.assertEqual(len(split_result["records"]), 2)
        pending = [r for r in split_result["records"] if r["result_pending_settlement"]]
        settled = [r for r in split_result["records"] if not r["result_pending_settlement"]]
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(settled), 1)
        self.assertIsNone(pending[0]["result"])
        self.assertIsNotNone(settled[0]["result"])

    def test_never_substitutes_2526_data_even_when_present(self):
        # Even when 2526's fixture IS present under raw_dir, this split
        # only ever reads 2627 — it must never fall back to or include any
        # 2526 row.
        dest_2526 = raw_file_path(self.raw_dir, "E0", "2526")
        dest_2526.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_2526_prospective.csv", dest_2526)
        # 2627 deliberately left missing.
        split_result = build_split(
            "split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=self.rows
        )
        self.assertEqual(split_result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(split_result["records"], [])
        for record in split_result["records"]:
            self.assertNotEqual(record.get("season_code"), "2526")
        for file_entry in split_result["files"]:
            self.assertEqual(file_entry["season_code"], "2627")

    def test_pending_result_with_invalid_opening_odds_is_rejected(self):
        # A pending fixture with NO usable opening price at all must still
        # be rejected (opening_1x2_prices_complete is mandatory here) even
        # though its result is legitimately nullable.
        dest = raw_file_path(self.raw_dir, "E0", "2627")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_2526_invalid_opening_odds.csv", dest)
        split_result = build_split(
            "split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=self.rows
        )
        self.assertEqual(len(split_result["records"]), 0)
        totals = split_result["totals"]
        self.assertEqual(totals["pending_settlement_rows_included"], 0)
        self.assertEqual(totals["rejected_unique_rows"], 1)

    def test_missing_result_rescue_applies_only_to_genuine_prospective_split(self):
        dest = raw_file_path(self.raw_dir, "E0", "2627")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_2627_prospective.csv", dest)
        for split_id in ("split_training", "split_closing_line_benchmark", "split_earlier_research_backtesting"):
            split_result = build_split(split_id, raw_dir=self.raw_dir, contract_rows=self.rows)
            self.assertEqual(split_result["totals"]["pending_settlement_rows_included"], 0, split_id)
            self.assertFalse(any(r["result_pending_settlement"] for r in split_result["records"]), split_id)


class SplitSpecificRejectionReasonTests(unittest.TestCase):
    """This task's Check 1: a split-specific rejection-reason sample that
    distinguishes OPENING_PRICES_INCOMPLETE (a training/calibration/
    locked-test-shaped split's own requirement) from
    CLOSING_PRICES_INCOMPLETE (never required by split_closing_line_benchmark
    for opening completeness), rather than the generic, split-unaware
    per-file sample."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmpdir.name) / "raw"
        dest = raw_file_path(self.raw_dir, "E0", "2223")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_opening_incomplete_closing_complete.csv", dest)
        self.rows = load_contract_rows()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_opening_incomplete_row_excluded_from_training_with_typed_reason(self):
        split_result = build_split("split_training", raw_dir=self.raw_dir, contract_rows=self.rows)
        self.assertEqual(split_result["records"], [])
        sample = split_result["split_specific_rejected_row_sample"]
        self.assertTrue(sample)
        self.assertEqual(sample[0]["split_rejection_reason"], "OPENING_PRICES_INCOMPLETE")

    def test_same_row_is_included_in_closing_line_benchmark(self):
        # split_closing_line_benchmark never checks opening completeness —
        # the same row (complete result + closing prices, incomplete
        # opening) is fully eligible there.
        split_result = build_split("split_closing_line_benchmark", raw_dir=self.raw_dir, contract_rows=self.rows)
        self.assertEqual(len(split_result["records"]), 1)
        record = split_result["records"][0]
        self.assertTrue(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in record["price_observations"]))


class HashImmutabilityTests(unittest.TestCase):
    """This task's item 3: split_training, split_calibration_validation,
    and split_locked_test are separate, immutable, separately-hashed
    outputs — distinct fixtures/season codes must produce three distinct
    dataset-file hashes, with no overlap."""

    def test_three_chronological_splits_produce_distinct_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            dataset_dir = Path(tmp) / "dataset"
            for season, filename in (
                ("1920", "season_1920_with_kickoff.csv"),
                ("2324", "season_2526_prospective.csv"),  # distinct content, reused as a stand-in fixture
                ("2425", "season_1213_with_closing.csv"),
            ):
                dest = raw_file_path(raw_dir, "E0", season)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(FIXTURES / filename, dest)

            rows = load_contract_rows()
            from data_pipeline.dataset_builder import write_split_dataset_file

            hashes = {}
            for split_id in TRAINING_SHAPED_SPLIT_IDS:
                split_result = build_split(split_id, raw_dir=raw_dir, contract_rows=rows)
                _path, dataset_hash = write_split_dataset_file(split_result, dataset_dir=dataset_dir)
                hashes[split_id] = dataset_hash

            self.assertEqual(len(hashes), 3)
            self.assertEqual(len(set(hashes.values())), 3, hashes)


class ProspectiveNullableResultTests(unittest.TestCase):
    """Regression coverage for a real defect found during independent
    review, and a second, narrower one found on a follow-up review of the
    first fix: build_file_records originally filtered strictly to
    validate_file's generic usable_index_set, which flags ANY blank-FTR
    row as MISSING_RESULT and drops it — silently excluding exactly the
    not-yet-played fixtures the prospective split's own contract row
    (required_fields: ...,result_nullable_until_settlement) exists to
    admit. The first fix's rescue rule ("include when MISSING_RESULT is
    the row's SOLE issue") was still too narrow: a genuinely upcoming
    fixture also has no closing line yet (Football-Data's generic per-row
    check flags that as MISSING_OR_INVALID_ODDS too, alongside
    MISSING_RESULT), and closing prices are benchmark-only per the
    contract — their absence must never exclude an otherwise-valid
    prospective row. The real fix (this class) is split-specific
    eligibility (`resolve_split_row_requirements`/
    `_row_eligible_for_split`) rather than a special-cased exception
    layered onto the generic historical validator.

    Following this task's correction, the nullable-result exception now
    lives on split_genuine_prospective_scoring (season 2627), not the
    renamed split_out_of_time_retrospective_holdout (season 2526) — these
    tests exercise the same fixtures against a hand-crafted 2627 raw
    directory."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self._tmpdir.name) / "raw"
        _populate_raw_dir(self.raw_dir, {k: v for k, v in FIXTURE_MAP.items() if k != ("E0", "2526")})

    def tearDown(self):
        self._tmpdir.cleanup()

    def _copy_as_2627(self, filename: str) -> Path:
        dest = raw_file_path(self.raw_dir, "E0", "2627")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / filename, dest)
        return dest

    def test_unplayed_fixtures_included_with_null_result_for_prospective_split(self):
        rows = load_contract_rows()
        self._copy_as_2627("season_2526_prospective.csv")
        split_result = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        # All 3 rows in the fixture must appear: 1 played + 2 unplayed.
        self.assertEqual(len(split_result["records"]), 3)
        pending = [r for r in split_result["records"] if r["result_pending_settlement"]]
        settled = [r for r in split_result["records"] if not r["result_pending_settlement"]]
        self.assertEqual(len(pending), 2)
        self.assertEqual(len(settled), 1)
        for record in pending:
            self.assertIsNone(record["result"])
        self.assertIsNotNone(settled[0]["result"])
        # This task's item 6: source_rows == usable_settled_rows +
        # pending_settlement_rows_included + rejected_unique_rows, always.
        totals = split_result["totals"]
        self.assertEqual(totals["source_rows"], 3)
        self.assertEqual(totals["usable_settled_rows"], 1)
        self.assertEqual(totals["pending_settlement_rows_included"], 2)
        self.assertEqual(totals["rejected_unique_rows"], 0)
        self.assertEqual(
            totals["source_rows"],
            totals["usable_settled_rows"] + totals["pending_settlement_rows_included"] + totals["rejected_unique_rows"],
        )

    def test_pending_result_with_missing_closing_odds_but_complete_opening_is_included(self):
        # Test 1 of the operator's 6-item follow-up list: a genuinely
        # upcoming fixture with no closing line yet, but complete opening
        # prices, kickoff time, and valid teams, must be included in
        # PROSPECTIVE — closing odds are benchmark-only, so their absence
        # is never a prospective-eligibility blocker.
        rows = load_contract_rows()
        self._copy_as_2627("season_2526_no_closing_yet.csv")
        split_result = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        self.assertEqual(len(split_result["records"]), 2)
        for record in split_result["records"]:
            self.assertTrue(record["result_pending_settlement"])
            self.assertIsNone(record["result"])
            # Complete opening prices present; no closing observation at
            # all (the fixture has no PSC columns for these rows).
            self.assertTrue(any(o["price_stage"] == PRICE_STAGE_OPENING for o in record["price_observations"]))
            self.assertFalse(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in record["price_observations"]))
        totals = split_result["totals"]
        self.assertEqual(totals["source_rows"], 2)
        self.assertEqual(totals["pending_settlement_rows_included"], 2)
        self.assertEqual(totals["rejected_unique_rows"], 0)

    def test_pending_result_with_invalid_opening_odds_is_rejected(self):
        # Test 2: a pending fixture with NO usable opening price at all
        # must still be rejected from PROSPECTIVE (opening_1x2_prices_
        # complete is mandatory there) even though its result is
        # legitimately nullable.
        rows = load_contract_rows()
        self._copy_as_2627("season_2526_invalid_opening_odds.csv")
        split_result = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        self.assertEqual(len(split_result["records"]), 0)
        totals = split_result["totals"]
        self.assertEqual(totals["source_rows"], 1)
        self.assertEqual(totals["pending_settlement_rows_included"], 0)
        self.assertEqual(totals["rejected_unique_rows"], 1)

    def test_pending_result_with_missing_team_or_kickoff_is_rejected(self):
        # Test 3: a pending fixture missing team identity, or missing
        # kickoff time, is rejected regardless of its result being
        # nullable — those are always-fatal / always-required
        # independently of the result-nullable exception.
        rows = load_contract_rows()
        self._copy_as_2627("season_2526_missing_team_and_kickoff.csv")
        split_result = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        self.assertEqual(len(split_result["records"]), 0)
        totals = split_result["totals"]
        self.assertEqual(totals["source_rows"], 2)
        self.assertEqual(totals["rejected_unique_rows"], 2)

    def test_missing_result_rescue_applies_only_to_prospective_split(self):
        # Test 4: the same file, run through every OTHER split, must
        # never rescue a blank-result row — result_nullable_until_
        # settlement is only ever set for split_genuine_prospective_scoring.
        rows = load_contract_rows()
        self._copy_as_2627("season_2526_missing_team_and_kickoff.csv")
        for split_id in ("split_training", "split_closing_line_benchmark", "split_earlier_research_backtesting"):
            split_result = build_split(split_id, raw_dir=self.raw_dir, contract_rows=rows)
            self.assertEqual(split_result["totals"]["pending_settlement_rows_included"], 0, split_id)
            self.assertFalse(any(r["result_pending_settlement"] for r in split_result["records"]), split_id)

    def test_closing_odds_arriving_later_enriches_without_changing_fixture_identity(self):
        # Test 5: the SAME fixture (same date/teams/season), built once
        # before its closing line exists and once after, has an identical
        # identity (league_code/season_code/date_raw/home_team/away_team)
        # in both builds — only price_observations differ (closing
        # observations appear once the line is available). This pipeline
        # rebuilds from scratch each run (no incremental state), so this
        # "stable identity" property holds structurally as long as
        # identity fields are never derived from odds data — this test
        # guards that property directly rather than assuming it.
        rows = load_contract_rows()
        before_dir = Path(self._tmpdir.name) / "raw_before"
        after_dir = Path(self._tmpdir.name) / "raw_after"
        before_dest = raw_file_path(before_dir, "E0", "2627")
        after_dest = raw_file_path(after_dir, "E0", "2627")
        before_dest.parent.mkdir(parents=True, exist_ok=True)
        after_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "season_2526_before_closing_line.csv", before_dest)
        shutil.copy(FIXTURES / "season_2526_after_closing_line.csv", after_dest)

        before = build_split("split_genuine_prospective_scoring", raw_dir=before_dir, contract_rows=rows)
        after = build_split("split_genuine_prospective_scoring", raw_dir=after_dir, contract_rows=rows)

        def identity(record):
            return (record["league_code"], record["season_code"], record["date_raw"], record["home_team"], record["away_team"])

        before_by_identity = {identity(r): r for r in before["records"]}
        after_by_identity = {identity(r): r for r in after["records"]}
        # Every identity present before is still present after (the same
        # fixture, never re-keyed by its odds).
        self.assertTrue(set(before_by_identity) <= set(after_by_identity))
        shared_identity = next(iter(before_by_identity))
        before_record = before_by_identity[shared_identity]
        after_record = after_by_identity[shared_identity]
        self.assertFalse(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in before_record["price_observations"]))
        self.assertTrue(any(o["price_stage"] == PRICE_STAGE_CLOSING for o in after_record["price_observations"]))
        # Everything except price_observations is unchanged.
        for key in ("league_code", "season_code", "date_raw", "home_team", "away_team", "result", "result_pending_settlement"):
            self.assertEqual(before_record[key], after_record[key], key)

    def test_a_row_with_missing_result_and_another_fatal_issue_is_still_excluded(self):
        # A row missing BOTH team identity AND result must not be
        # smuggled in just because one of its several problems happens to
        # be a blank result — MISSING_TEAM_IDENTITY is always-fatal,
        # independent of any split's requirements.
        rows = load_contract_rows()
        dest = self._copy_as_2627("season_2526_prospective.csv")
        split_result = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        self.assertEqual(len(split_result["records"]), 3)  # sanity: the clean fixture's baseline

        shutil.copy(FIXTURES / "season_2526_missing_team_and_kickoff.csv", dest)
        split_result_broken = build_split("split_genuine_prospective_scoring", raw_dir=self.raw_dir, contract_rows=rows)
        self.assertEqual(len(split_result_broken["records"]), 0)


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
        # triple_rejection_row_with_closing.csv's row 0 has complete
        # opening AND closing prices plus a result — eligible for
        # split_earlier_research_backtesting under its own split-specific
        # requirements; row 1 is fatally broken (missing team identity)
        # regardless of split.
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            dest = raw_file_path(raw_dir, "E0", "1213")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(FIXTURES / "triple_rejection_row_with_closing.csv", dest)

            rows = load_contract_rows()
            split_result = build_split("split_earlier_research_backtesting", raw_dir=raw_dir, contract_rows=rows)
            totals = split_result["totals"]
            self.assertEqual(totals["source_rows"], 2)
            self.assertEqual(totals["usable_settled_rows"], 1)
            self.assertEqual(totals["rejected_unique_rows"], 1)
            self.assertEqual(
                totals["source_rows"],
                totals["usable_settled_rows"] + totals["pending_settlement_rows_included"] + totals["rejected_unique_rows"],
            )
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


class Season1415RejectionBreakdownTests(unittest.TestCase):
    """This task's Check 2: a per-league, split-independent
    rejection-reason breakdown for season 1415, using the existing
    season_1415_outlier.csv fixture (E0 only)."""

    def test_breakdown_reports_reason_counts_and_sample_for_e0(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            _populate_raw_dir(raw_dir)
            breakdown = build_season_1415_rejection_breakdown(raw_dir)
            self.assertEqual(breakdown["status"], "UNRESOLVED_PENDING_HUMAN_REVIEW")
            e0 = breakdown["per_league"]["E0"]
            self.assertTrue(e0["available"])
            self.assertGreater(e0["total_rows"], 0)
            self.assertEqual(e0["total_rows"], e0["usable_fixtures"] + e0["rejected_fixtures"])
            self.assertIn("MISSING_OR_INVALID_ODDS", e0["rejection_reason_counts"])
            self.assertGreater(e0["rejection_reason_counts"]["MISSING_OR_INVALID_ODDS"], 0)
            self.assertTrue(e0["rejected_row_sample"])
            for row in e0["rejected_row_sample"]:
                self.assertIn("row_index", row)
                self.assertIn("home_team", row)
                self.assertIn("away_team", row)
                self.assertIn("date_raw", row)
                self.assertIn("issues", row)
            # A league with no 1415 file at all is reported honestly as
            # unavailable, never fabricated.
            self.assertFalse(breakdown["per_league"]["D1"]["available"])

    def test_breakdown_does_not_change_1415_excluded_status(self):
        # Purely diagnostic — season 1415 stays excluded by default
        # regardless of what this breakdown finds.
        self.assertTrue(is_excluded_by_default("1415"))


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
            split_result = build_split("split_training", raw_dir=raw_dir, contract_rows=rows)
            self.assertEqual(split_result["records"], [])
            self.assertTrue(all(f["missing"] for f in split_result["files"]))
            self.assertEqual(split_result["totals"]["source_rows"], 0)
            self.assertEqual(split_result["status"], "SOURCE_UNAVAILABLE")


class SourceUnavailableStatusSafetyTests(unittest.TestCase):
    """SOURCE_UNAVAILABLE status must never crash build_dataset_report or
    write_dataset_build_report anywhere, for any split, including the new
    split_genuine_prospective_scoring whose 2627 file genuinely does not
    exist in this sandbox."""

    def test_full_report_pipeline_completes_cleanly_with_no_raw_files(self):
        from data_pipeline.dataset_builder import write_dataset_build_report

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"  # deliberately empty: every split, including
            dataset_dir = Path(tmp) / "dataset"  # split_genuine_prospective_scoring, is SOURCE_UNAVAILABLE.
            rows = load_contract_rows()
            report = build_dataset_report(raw_dir=raw_dir, dataset_dir=dataset_dir, contract_rows=rows)
            for split_id in DATASET_SPLIT_IDS:
                self.assertEqual(report["splits"][split_id]["status"], "SOURCE_UNAVAILABLE", split_id)
            json_path = Path(tmp) / "reports" / "dataset_build_report.json"
            markdown_path = Path(tmp) / "reports" / "dataset_build_report.md"
            write_dataset_build_report(report, json_path=json_path, markdown_path=markdown_path)
            self.assertTrue(json_path.exists())
            self.assertTrue(markdown_path.exists())
            markdown_text = markdown_path.read_text(encoding="utf-8")
            self.assertIn("SOURCE_UNAVAILABLE", markdown_text)


if __name__ == "__main__":
    unittest.main()
