"""Tests for research/soccer_1x2_elo_baseline/ — the Elo rating engine,
pre-match feature construction, pure-Python multinomial logistic
regression, temperature calibration, training orchestration, and
evaluation, with a strong emphasis on the no-lookahead/no-leakage
invariants and full-pipeline determinism this baseline depends on.

Only tests/fixtures/football_data/*.csv (existing fixtures plus two small
new ones added for this task — season_2324_for_elo_baseline.csv and
season_2425_for_elo_baseline.csv, a short multi-season sequence reusing
season_1920_with_kickoff.csv's teams) is ever used as input. No real
dataset is fitted on or claimed as fitted here."""

from __future__ import annotations

import copy
import json
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

from data_pipeline.dataset_builder import raw_file_path

from research.soccer_1x2_elo_baseline import calibration, evaluate, hash_provenance, model, train
from research.soccer_1x2_elo_baseline.elo import (
    HOME_ADVANTAGE_ELO_BONUS,
    INITIAL_RATING,
    K_FACTOR,
    PROVISIONAL_MATCH_THRESHOLD,
    EloEngine,
    expected_home_score,
    sort_matches_chronologically,
)
from research.soccer_1x2_elo_baseline.features import (
    DUMMY_LEAGUE_CODES,
    EXCLUSION_REASON_INVALID_RESULT,
    EXCLUSION_REASON_UNPARSEABLE_DATE,
    FEATURE_NAMES,
    SeasonStageTracker,
    build_dataset,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"

# (league, season) -> fixture filename for a small, self-contained
# multi-season chronological sequence: 1920 (existing, 3 matches),
# 2324 (new, calibration split), 2425 (new, locked-test split, single
# match reusing Liverpool/Man City so it sits chronologically after the
# earlier matches for both teams). 2526/2627 reuse existing fixtures.
BASELINE_FIXTURE_MAP = {
    ("E0", "1920"): "season_1920_with_kickoff.csv",
    ("E0", "2324"): "season_2324_for_elo_baseline.csv",
    ("E0", "2425"): "season_2425_for_elo_baseline.csv",
    ("E0", "2526"): "season_2526_prospective.csv",
    ("E0", "2627"): "season_2627_prospective.csv",
}


def _populate_raw_dir(raw_dir: Path, mapping: dict = BASELINE_FIXTURE_MAP) -> None:
    for (league, season), filename in mapping.items():
        dest = raw_file_path(raw_dir, league, season)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / filename, dest)


def _make_record(
    league_code="E0",
    season_code="2324",
    date_raw="12/08/23",
    home_team="TeamA",
    away_team="TeamB",
    result="H",
    price_observations=None,
):
    return {
        "league_code": league_code,
        "season_code": season_code,
        "date_raw": date_raw,
        "match_kickoff_at": "20:00",
        "home_team": home_team,
        "away_team": away_team,
        "result": result,
        "result_pending_settlement": False,
        "price_observations": price_observations if price_observations is not None else [],
    }


# ===========================================================================
# elo.py
# ===========================================================================


class EloEngineTests(unittest.TestCase):
    def test_never_seen_team_gets_initial_rating_and_is_provisional(self):
        engine = EloEngine()
        state = engine.get_pre_match_state("E0", "Nobody FC")
        self.assertEqual(state.rating, INITIAL_RATING)
        self.assertTrue(state.is_provisional)
        self.assertEqual(state.matches_played, 0)

    def test_leakage_boundary_second_match_reflects_first_first_does_not(self):
        """Core no-lookahead invariant: process two matches for the same
        team in known order; the SECOND match's pre-match Elo must reflect
        the first match's outcome, and the FIRST match's own pre-match Elo
        must NOT reflect anything from the second match."""

        engine = EloEngine()
        records = [
            _make_record(date_raw="01/08/23", home_team="Alpha", away_team="Beta", result="H"),
            _make_record(date_raw="08/08/23", home_team="Alpha", away_team="Gamma", result="H"),
        ]
        pre_match_states = engine.process_ordered_matches(records)

        first_pre = pre_match_states[0]
        second_pre = pre_match_states[1]

        # First match: Alpha and Beta both still at the untouched initial
        # rating — nothing about the second match could have leaked back.
        self.assertEqual(first_pre["home_elo_pre"], INITIAL_RATING)
        self.assertEqual(first_pre["away_elo_pre"], INITIAL_RATING)

        # Second match: Alpha's rating must have moved UP from the initial
        # rating (won the first match at home), reflecting match 1's
        # outcome — this is the leakage-boundary assertion.
        self.assertGreater(second_pre["home_elo_pre"], INITIAL_RATING)
        # Gamma (never seen) is still untouched.
        self.assertEqual(second_pre["away_elo_pre"], INITIAL_RATING)

    def test_ratings_never_shared_across_leagues(self):
        engine = EloEngine()
        engine.apply_match_result("E0", "SameName", "Other", "H")
        e0_state = engine.get_pre_match_state("E0", "SameName")
        d1_state = engine.get_pre_match_state("D1", "SameName")
        self.assertNotEqual(e0_state.rating, INITIAL_RATING)
        self.assertEqual(d1_state.rating, INITIAL_RATING)
        self.assertEqual(d1_state.matches_played, 0)

    def test_provisional_flag_clears_after_threshold_matches(self):
        engine = EloEngine()
        for _ in range(PROVISIONAL_MATCH_THRESHOLD - 1):
            engine.apply_match_result("E0", "Grinder", "Opponent", "D")
        self.assertTrue(engine.get_pre_match_state("E0", "Grinder").is_provisional)
        engine.apply_match_result("E0", "Grinder", "Opponent", "D")
        self.assertFalse(engine.get_pre_match_state("E0", "Grinder").is_provisional)

    def test_expected_score_symmetric_and_home_advantage_applied(self):
        # Equal ratings: home side should be favored purely by the home
        # advantage bonus.
        e_home = expected_home_score(1500.0, 1500.0)
        self.assertGreater(e_home, 0.5)
        # With the bonus neutralized (0), the formula is exactly symmetric.
        e_home_no_bonus = expected_home_score(1600.0, 1400.0, home_advantage_elo_bonus=0.0)
        e_away_no_bonus = expected_home_score(1400.0, 1600.0, home_advantage_elo_bonus=0.0)
        self.assertAlmostEqual(e_home_no_bonus, 1.0 - e_away_no_bonus, places=9)
        self.assertTrue(0.0 < e_home_no_bonus < 1.0)

    def test_standard_update_formula_matches_documented_constants(self):
        engine = EloEngine()
        expected = expected_home_score(INITIAL_RATING, INITIAL_RATING)
        engine.apply_match_result("E0", "Home", "Away", "H")
        new_home = engine._ratings[("E0", "Home")]
        self.assertAlmostEqual(new_home, INITIAL_RATING + K_FACTOR * (1.0 - expected), places=9)

    def test_deterministic_given_same_ordered_sequence(self):
        records = [
            _make_record(date_raw="01/08/23", home_team="A", away_team="B", result="H"),
            _make_record(date_raw="02/08/23", home_team="B", away_team="C", result="D"),
            _make_record(date_raw="03/08/23", home_team="C", away_team="A", result="A"),
        ]
        engine1 = EloEngine()
        engine2 = EloEngine()
        result1 = engine1.process_ordered_matches(copy.deepcopy(records))
        result2 = engine2.process_ordered_matches(copy.deepcopy(records))
        self.assertEqual(result1, result2)


class SortMatchesChronologicallyTests(unittest.TestCase):
    def test_sorts_ascending_and_is_stable_on_ties(self):
        records = [
            _make_record(date_raw="15/08/23", home_team="Late"),
            _make_record(date_raw="01/08/23", home_team="Early"),
            _make_record(date_raw="01/08/23", home_team="EarlyToo"),
        ]
        sorted_records, unparseable = sort_matches_chronologically(records)
        self.assertEqual([r["home_team"] for r in sorted_records], ["Early", "EarlyToo", "Late"])
        self.assertEqual(unparseable, [])

    def test_unparseable_date_excluded_not_crashed(self):
        records = [
            _make_record(date_raw="not-a-date", home_team="Bad"),
            _make_record(date_raw="01/08/23", home_team="Good"),
        ]
        sorted_records, unparseable = sort_matches_chronologically(records)
        self.assertEqual([r["home_team"] for r in sorted_records], ["Good"])
        self.assertEqual(len(unparseable), 1)
        self.assertEqual(unparseable[0]["home_team"], "Bad")


# ===========================================================================
# features.py
# ===========================================================================


class FeaturesTests(unittest.TestCase):
    def test_feature_names_shape_and_league_reference_category_dropped(self):
        self.assertEqual(len(FEATURE_NAMES), 9)
        for code in DUMMY_LEAGUE_CODES:
            self.assertIn(f"league_{code}", FEATURE_NAMES)
        self.assertNotIn("league_E0", FEATURE_NAMES)

    def test_e0_row_has_all_league_dummies_zero(self):
        records = [_make_record(league_code="E0")]
        result = build_dataset(records)
        vector = result.features[0]
        league_start = FEATURE_NAMES.index("league_D1")
        league_end = league_start + len(DUMMY_LEAGUE_CODES)
        self.assertEqual(vector[league_start:league_end], [0.0, 0.0, 0.0, 0.0])

    def test_non_reference_league_sets_exactly_one_dummy(self):
        records = [_make_record(league_code="SP1")]
        result = build_dataset(records)
        vector = result.features[0]
        league_start = FEATURE_NAMES.index("league_D1")
        dummies = vector[league_start : league_start + len(DUMMY_LEAGUE_CODES)]
        self.assertEqual(sum(dummies), 1.0)
        self.assertEqual(dummies[DUMMY_LEAGUE_CODES.index("SP1")], 1.0)

    def test_home_advantage_constant_one_on_every_row(self):
        records = [
            _make_record(date_raw="01/08/23", home_team="A", away_team="B"),
            _make_record(date_raw="02/08/23", home_team="C", away_team="D"),
        ]
        result = build_dataset(records)
        idx = FEATURE_NAMES.index("home_advantage")
        for vector in result.features:
            self.assertEqual(vector[idx], 1.0)

    def test_elo_diff_equals_home_minus_away(self):
        records = [_make_record()]
        result = build_dataset(records)
        vector = result.features[0]
        home = vector[FEATURE_NAMES.index("home_elo_pre")]
        away = vector[FEATURE_NAMES.index("away_elo_pre")]
        diff = vector[FEATURE_NAMES.index("elo_diff")]
        self.assertAlmostEqual(diff, home - away, places=9)

    def test_season_stage_is_min_of_both_teams_matches_played_and_resets_per_season(self):
        records = [
            _make_record(season_code="2021", date_raw="01/08/20", home_team="A", away_team="B"),
            _make_record(season_code="2021", date_raw="08/08/20", home_team="A", away_team="C"),
            # New season: even though A has a long history, season_stage
            # resets to 0 for both sides on their first match of 2122.
            _make_record(season_code="2122", date_raw="01/08/21", home_team="A", away_team="B"),
        ]
        result = build_dataset(records)
        stage_idx = FEATURE_NAMES.index("season_stage")
        self.assertEqual(result.features[0][stage_idx], 0.0)  # A's & B's first match of 2021
        self.assertEqual(result.features[1][stage_idx], 0.0)  # A has played 1 match this season, C has 0 -> min=0

    def test_season_stage_first_match_of_new_season_is_zero_even_with_elo_history(self):
        records = [
            _make_record(season_code="2021", date_raw="01/08/20", home_team="A", away_team="B", result="H"),
            _make_record(season_code="2122", date_raw="01/08/21", home_team="A", away_team="C", result="H"),
        ]
        result = build_dataset(records)
        stage_idx = FEATURE_NAMES.index("season_stage")
        elo_idx = FEATURE_NAMES.index("home_elo_pre")
        # A's Elo should have moved from the 2021 win, but season_stage
        # resets to 0 for A's first 2122 match.
        self.assertGreater(result.features[1][elo_idx], INITIAL_RATING)
        self.assertEqual(result.features[1][stage_idx], 0.0)

    def test_fail_closed_invalid_result_excluded_with_typed_reason(self):
        records = [_make_record(result="X")]
        result = build_dataset(records)
        self.assertEqual(result.features, [])
        self.assertEqual(len(result.exclusions), 1)
        self.assertEqual(result.exclusions[0]["reason"], EXCLUSION_REASON_INVALID_RESULT)

    def test_fail_closed_unparseable_date_excluded_with_typed_reason(self):
        records = [_make_record(date_raw="garbage-date")]
        result = build_dataset(records)
        self.assertEqual(result.features, [])
        self.assertEqual(len(result.exclusions), 1)
        self.assertEqual(result.exclusions[0]["reason"], EXCLUSION_REASON_UNPARSEABLE_DATE)

    def test_null_result_pending_settlement_excluded_never_zero_filled(self):
        records = [_make_record(result=None)]
        result = build_dataset(records)
        self.assertEqual(result.features, [])
        self.assertEqual(result.exclusions[0]["reason"], EXCLUSION_REASON_INVALID_RESULT)

    def test_closing_price_observations_never_affect_feature_vector(self):
        opening_only = [
            _make_record(
                price_observations=[
                    {"price_stage": "OPENING", "home_odds": 2.0, "draw_odds": 3.2, "away_odds": 4.0}
                ]
            )
        ]
        opening_and_closing = [
            _make_record(
                price_observations=[
                    {"price_stage": "OPENING", "home_odds": 2.0, "draw_odds": 3.2, "away_odds": 4.0},
                    {"price_stage": "CLOSING", "home_odds": 1.5, "draw_odds": 4.5, "away_odds": 6.0},
                ]
            )
        ]
        closing_only_no_opening = [
            _make_record(
                price_observations=[
                    {"price_stage": "CLOSING", "home_odds": 1.5, "draw_odds": 4.5, "away_odds": 6.0}
                ]
            )
        ]
        no_prices_at_all = [_make_record(price_observations=[])]

        vectors = [build_dataset(recs).features[0] for recs in (opening_only, opening_and_closing, closing_only_no_opening, no_prices_at_all)]
        for v in vectors[1:]:
            self.assertEqual(v, vectors[0])

    def test_closing_only_record_still_produces_valid_feature_vector(self):
        """A record with ONLY closing price observations and no opening
        ones must still produce a valid feature vector using Elo/league/
        season-stage alone — price data is not a model feature at all."""

        records = [
            _make_record(
                price_observations=[
                    {"price_stage": "CLOSING", "home_odds": 1.9, "draw_odds": 3.4, "away_odds": 4.2}
                ]
            )
        ]
        result = build_dataset(records)
        self.assertEqual(len(result.features), 1)
        self.assertEqual(len(result.features[0]), len(FEATURE_NAMES))
        self.assertEqual(result.exclusions, [])

    def test_provisional_flags_threaded_through_record_refs(self):
        records = [_make_record(home_team="BrandNewTeam", away_team="AlsoNew")]
        result = build_dataset(records)
        self.assertTrue(result.record_refs[0]["home_is_provisional"])
        self.assertTrue(result.record_refs[0]["away_is_provisional"])

    def test_season_stage_tracker_isolated_per_league_and_season(self):
        tracker = SeasonStageTracker()
        tracker.record_played("E0", "2324", "A")
        self.assertEqual(tracker.get_pre_match_count("E0", "2324", "A"), 1)
        self.assertEqual(tracker.get_pre_match_count("E0", "2425", "A"), 0)
        self.assertEqual(tracker.get_pre_match_count("D1", "2324", "A"), 0)


# ===========================================================================
# model.py
# ===========================================================================


class ModelTests(unittest.TestCase):
    def _toy_dataset(self):
        records = [
            _make_record(date_raw=f"0{i}/08/23", home_team="A", away_team="B", result=("H" if i % 3 == 0 else ("D" if i % 3 == 1 else "A")))
            for i in range(1, 7)
        ]
        result = build_dataset(records)
        return result.features, result.labels

    def test_fit_is_deterministic(self):
        features, labels = self._toy_dataset()
        artifact1 = model.fit(features, labels, iterations=50)
        artifact2 = model.fit(features, labels, iterations=50)
        self.assertEqual(artifact1.to_dict(), artifact2.to_dict())

    def test_predict_proba_sums_to_one_and_is_positive(self):
        features, labels = self._toy_dataset()
        artifact = model.fit(features, labels, iterations=50)
        probs = artifact.predict_proba(features[0])
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        for p in probs.values():
            self.assertGreater(p, 0.0)

    def test_standardization_fit_on_training_only_and_reapplied(self):
        features, labels = self._toy_dataset()
        params = model.fit_standardization(features)
        artifact = model.fit(features, labels, iterations=10, standardization=params)
        self.assertEqual(artifact.standardization.to_dict(), params.to_dict())
        # Applying to a wildly different (evaluation-like) vector must
        # never refit — same means/stds reused.
        other_vector = [5000.0, -100.0, 5100.0, 1.0, 0.0, 0.0, 0.0, 0.0, 20.0]
        standardized = model.apply_standardization(other_vector, params)
        self.assertEqual(len(standardized), len(other_vector))

    def test_serialization_round_trip(self):
        features, labels = self._toy_dataset()
        artifact = model.fit(features, labels, iterations=20)
        restored = model.ModelArtifact.from_dict(artifact.to_dict())
        self.assertEqual(artifact.to_dict(), restored.to_dict())

    def test_zero_iterations_yields_zero_weights(self):
        features, labels = self._toy_dataset()
        artifact = model.fit(features, labels, iterations=0)
        self.assertTrue(all(w == 0.0 for row in artifact.weights for w in row))
        self.assertTrue(all(b == 0.0 for b in artifact.biases))


# ===========================================================================
# calibration.py
# ===========================================================================


class CalibrationTests(unittest.TestCase):
    def _fitted_model_and_calibration_rows(self):
        records = [
            _make_record(date_raw=f"0{i}/08/23", home_team="A", away_team="B", result=("H" if i % 2 == 0 else "A"))
            for i in range(1, 5)
        ]
        result = build_dataset(records)
        artifact = model.fit(result.features, result.labels, iterations=20)
        calibration_rows = list(zip(result.features, result.labels))
        return artifact, calibration_rows

    def test_temperature_within_documented_bounds(self):
        artifact, calibration_rows = self._fitted_model_and_calibration_rows()
        t = calibration.fit_temperature(artifact, calibration_rows)
        self.assertGreaterEqual(t, calibration.T_MIN)
        self.assertLessEqual(t, calibration.T_MAX)

    def test_temperature_search_deterministic(self):
        artifact, calibration_rows = self._fitted_model_and_calibration_rows()
        t1 = calibration.fit_temperature(artifact, calibration_rows)
        t2 = calibration.fit_temperature(artifact, calibration_rows)
        self.assertEqual(t1, t2)

    def test_empty_calibration_rows_returns_neutral_temperature(self):
        artifact, _ = self._fitted_model_and_calibration_rows()
        self.assertEqual(calibration.fit_temperature(artifact, []), 1.0)


# ===========================================================================
# train.py — orchestration + leakage/determinism through the full pipeline
# ===========================================================================


class TrainPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.raw_dir = Path(self.tmpdir) / "raw"
        _populate_raw_dir(self.raw_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pipeline_runs_end_to_end_against_fixtures(self):
        result = train.train_pipeline(raw_dir=self.raw_dir)
        self.assertGreater(len(result.rows_by_split["split_training"]), 0)
        self.assertGreater(result.model_artifact.training_row_count, 0)
        self.assertTrue(result.frozen_hashes.combined_hash)
        self.assertEqual(set(result.frozen_hashes.split_hashes), set(train.FROZEN_SPLIT_IDS))
        self.assertTrue(result.code_hash)
        self.assertTrue(result.artifact_hash)
        self.assertTrue(result.combined_hash)
        self.assertTrue(result.prospective_stream.content_hash)
        self.assertTrue(result.prospective_stream.run_timestamp_utc)

    def test_empty_raw_dir_never_crashes(self):
        empty_raw_dir = Path(self.tmpdir) / "empty_raw"
        empty_raw_dir.mkdir()
        result = train.train_pipeline(raw_dir=empty_raw_dir)
        self.assertEqual(result.model_artifact.training_row_count, 0)
        for status in result.split_statuses.values():
            self.assertEqual(status, "SOURCE_UNAVAILABLE")

    def test_run_twice_determinism_check(self):
        is_identical, first, second = train.run_twice_determinism_check(raw_dir=self.raw_dir)
        self.assertTrue(is_identical)
        self.assertEqual(first.combined_hash, second.combined_hash)

    def test_elo_no_lookahead_end_to_end_through_train_pipeline(self):
        """Perturb the chronologically LATER match (2425, split_locked_test)
        and confirm an EARLIER match's (1920, split_training) stored
        pre-match feature vector is byte-identical."""

        baseline_result = train.train_pipeline(raw_dir=self.raw_dir)
        baseline_training_rows = baseline_result.rows_by_split["split_training"]

        perturbed_dir = Path(self.tmpdir) / "perturbed_raw"
        _populate_raw_dir(perturbed_dir)
        locked_test_path = raw_file_path(perturbed_dir, "E0", "2425")
        content = locked_test_path.read_text(encoding="utf-8")
        # Flip the single 2425 match's result from D to H.
        perturbed_content = content.replace(
            "E0,11/08/24,20:00,Liverpool,Man City,2,2,D,",
            "E0,11/08/24,20:00,Liverpool,Man City,3,0,H,",
        )
        self.assertNotEqual(content, perturbed_content)
        locked_test_path.write_text(perturbed_content, encoding="utf-8")

        perturbed_result = train.train_pipeline(raw_dir=perturbed_dir)
        perturbed_training_rows = perturbed_result.rows_by_split["split_training"]

        self.assertEqual(
            [row["feature_vector"] for row in baseline_training_rows],
            [row["feature_vector"] for row in perturbed_training_rows],
        )

    def test_fitting_never_touches_eval_splits(self):
        """Mutating split_locked_test/split_out_of_time_retrospective_holdout/
        split_genuine_prospective_scoring fixture data must never change
        the fitted model artifact's weights or the fitted temperature."""

        baseline_result = train.train_pipeline(raw_dir=self.raw_dir)

        mutated_dir = Path(self.tmpdir) / "mutated_raw"
        _populate_raw_dir(mutated_dir)
        for league, season in (("E0", "2425"), ("E0", "2526"), ("E0", "2627")):
            path = raw_file_path(mutated_dir, league, season)
            path.unlink()

        mutated_result = train.train_pipeline(raw_dir=mutated_dir)

        self.assertEqual(baseline_result.model_artifact.to_dict(), mutated_result.model_artifact.to_dict())
        self.assertEqual(baseline_result.temperature, mutated_result.temperature)

    def test_calibration_step_uses_only_calibration_validation_rows(self):
        """The returned temperature T is identical whether or not
        split_locked_test/split_out_of_time_retrospective_holdout/
        split_genuine_prospective_scoring fixtures exist on disk AT ALL."""

        without_eval_splits_dir = Path(self.tmpdir) / "no_eval_raw"
        without_eval_splits_dir.mkdir()
        for league, season in BASELINE_FIXTURE_MAP:
            if season in ("2425", "2526", "2627"):
                continue
            key = (league, season)
            dest = raw_file_path(without_eval_splits_dir, league, season)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(FIXTURES / BASELINE_FIXTURE_MAP[key], dest)

        full_result = train.train_pipeline(raw_dir=self.raw_dir)
        partial_result = train.train_pipeline(raw_dir=without_eval_splits_dir)
        self.assertEqual(full_result.temperature, partial_result.temperature)
        self.assertEqual(full_result.model_artifact.to_dict(), partial_result.model_artifact.to_dict())

    def test_closing_price_swap_never_changes_feature_vector_or_prediction(self):
        """Two otherwise-identical raw CSVs differing only in closing-price
        content (one with PSC* columns present, one with them blanked)
        must produce identical features and identical model predictions
        for that row."""

        with_closing_dir = Path(self.tmpdir) / "with_closing"
        _populate_raw_dir(with_closing_dir)

        without_closing_dir = Path(self.tmpdir) / "without_closing"
        _populate_raw_dir(without_closing_dir)
        path = raw_file_path(without_closing_dir, "E0", "2425")
        content = path.read_text(encoding="utf-8")
        blanked = content.replace(
            "E0,11/08/24,20:00,Liverpool,Man City,2,2,D,2.20,3.60,3.10,2.18,3.65,3.15,2.15,3.70,3.20",
            "E0,11/08/24,20:00,Liverpool,Man City,2,2,D,2.20,3.60,3.10,2.18,3.65,3.15,,,",
        )
        self.assertNotEqual(content, blanked)
        path.write_text(blanked, encoding="utf-8")

        with_result = train.train_pipeline(raw_dir=with_closing_dir)
        without_result = train.train_pipeline(raw_dir=without_closing_dir)

        with_locked = with_result.rows_by_split["split_locked_test"]
        without_locked = without_result.rows_by_split["split_locked_test"]
        self.assertEqual(
            [row["feature_vector"] for row in with_locked],
            [row["feature_vector"] for row in without_locked],
        )
        for w_row, wo_row in zip(with_locked, without_locked):
            pred_with = with_result.model_artifact.predict_proba(w_row["feature_vector"], temperature=with_result.temperature)
            pred_without = without_result.model_artifact.predict_proba(wo_row["feature_vector"], temperature=without_result.temperature)
            self.assertEqual(pred_with, pred_without)

    def test_missing_or_invalid_rows_recorded_in_exclusions_never_crash(self):
        records = [
            {
                "league_code": "E0",
                "season_code": "2324",
                "date_raw": "not-a-real-date",
                "home_team": "Ghost",
                "away_team": "Phantom",
                "result": "H",
                "result_pending_settlement": False,
                "price_observations": [],
            }
        ]
        from research.soccer_1x2_elo_baseline.features import build_dataset

        result = build_dataset(records)
        self.assertEqual(result.features, [])
        self.assertEqual(result.exclusions[0]["reason"], "UNPARSEABLE_DATE")


class HashSeparationTests(unittest.TestCase):
    def test_hashes_stable_across_repeated_calls(self):
        records_by_split = {"split_training": [_make_record()]}
        h1 = train.compute_frozen_hashes(records_by_split).combined_hash
        h2 = train.compute_frozen_hashes(records_by_split).combined_hash
        self.assertEqual(h1, h2)

        code_hash1 = train.compute_code_hash()
        code_hash2 = train.compute_code_hash()
        self.assertEqual(code_hash1, code_hash2)

    def test_dataset_change_never_affects_code_hash(self):
        records_a = {"split_training": [_make_record(home_team="A")]}
        records_b = {"split_training": [_make_record(home_team="B")]}
        dataset_hash_a = train.compute_frozen_hashes(records_a).combined_hash
        dataset_hash_b = train.compute_frozen_hashes(records_b).combined_hash
        self.assertNotEqual(dataset_hash_a, dataset_hash_b)

        code_hash_before = train.compute_code_hash()
        # Recomputing code_hash is a pure function of package source files
        # only — it cannot possibly depend on which records dict was just
        # hashed, but assert it explicitly for the "hash separation is
        # real, not coincidental" guarantee.
        code_hash_after = train.compute_code_hash()
        self.assertEqual(code_hash_before, code_hash_after)

    def test_code_change_changes_only_code_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp)
            for filename in train._CODE_HASH_SOURCE_FILES:
                shutil.copy(train._THIS_PACKAGE_DIR / filename, package_dir / filename)

            code_hash_before = train.compute_code_hash(package_dir)
            records_by_split = {"split_training": [_make_record()]}
            dataset_hash_before = train.compute_frozen_hashes(records_by_split).combined_hash

            # A non-functional (comment-only) edit to one source file.
            train_py = package_dir / "train.py"
            train_py.write_text(train_py.read_text(encoding="utf-8") + "\n# harmless comment for hash-separation test\n", encoding="utf-8")

            code_hash_after = train.compute_code_hash(package_dir)
            dataset_hash_after = train.compute_frozen_hashes(records_by_split).combined_hash

            self.assertNotEqual(code_hash_before, code_hash_after)
            self.assertEqual(dataset_hash_before, dataset_hash_after)

    def test_combined_hash_derived_from_all_three(self):
        combined1 = train.compute_combined_hash("d1", "c1", "a1")
        combined2 = train.compute_combined_hash("d2", "c1", "a1")
        combined3 = train.compute_combined_hash("d1", "c2", "a1")
        combined4 = train.compute_combined_hash("d1", "c1", "a2")
        self.assertNotEqual(combined1, combined2)
        self.assertNotEqual(combined1, combined3)
        self.assertNotEqual(combined1, combined4)

    def test_prospective_stream_never_part_of_frozen_hashes(self):
        """compute_frozen_hashes only ever accepts the four FROZEN split
        ids — split_genuine_prospective_scoring's content must never
        affect the frozen combined hash."""

        frozen_records = {sid: [_make_record(home_team=sid)] for sid in train.FROZEN_SPLIT_IDS}
        frozen_before = train.compute_frozen_hashes(frozen_records).combined_hash

        prospective_hash_a = train.compute_split_hash([_make_record(home_team="stream-v1")])
        prospective_hash_b = train.compute_split_hash([_make_record(home_team="stream-v2")])
        self.assertNotEqual(prospective_hash_a, prospective_hash_b)

        # Recomputing the frozen hash from the SAME frozen records must be
        # unaffected by whatever the prospective stream's content is —
        # compute_frozen_hashes never even accepts prospective data as
        # input, so this is a structural guarantee, not a coincidence.
        frozen_after = train.compute_frozen_hashes(frozen_records).combined_hash
        self.assertEqual(frozen_before, frozen_after)

    def test_full_pipeline_prospective_stream_change_never_changes_frozen_hashes(self):
        """End-to-end: changing split_genuine_prospective_scoring's raw
        fixture content changes ONLY its own stream hash — never any of
        the four frozen split hashes, the frozen combined hash, or the
        fitted model artifact."""

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            _populate_raw_dir(raw_dir)
            baseline = train.train_pipeline(raw_dir=raw_dir)

            prospective_path = raw_file_path(raw_dir, "E0", "2627")
            content = prospective_path.read_text(encoding="utf-8")
            mutated = content.replace("Bournemouth", "Newcastle")
            self.assertNotEqual(content, mutated)
            prospective_path.write_text(mutated, encoding="utf-8")

            mutated_result = train.train_pipeline(raw_dir=raw_dir)

            self.assertNotEqual(baseline.prospective_stream.content_hash, mutated_result.prospective_stream.content_hash)
            self.assertEqual(baseline.frozen_hashes.split_hashes, mutated_result.frozen_hashes.split_hashes)
            self.assertEqual(baseline.frozen_hashes.combined_hash, mutated_result.frozen_hashes.combined_hash)
            self.assertEqual(baseline.combined_hash, mutated_result.combined_hash)
            self.assertEqual(baseline.model_artifact.to_dict(), mutated_result.model_artifact.to_dict())


# ===========================================================================
# evaluate.py
# ===========================================================================


class EvaluateMetricsTests(unittest.TestCase):
    def test_multiclass_brier_perfect_prediction_is_zero(self):
        self.assertAlmostEqual(evaluate.multiclass_brier({"H": 1.0, "D": 0.0, "A": 0.0}, "H"), 0.0, places=9)

    def test_multiclass_brier_uniform_prediction(self):
        brier = evaluate.multiclass_brier({"H": 1 / 3, "D": 1 / 3, "A": 1 / 3}, "H")
        expected = (1 / 3 - 1) ** 2 + (1 / 3) ** 2 + (1 / 3) ** 2
        self.assertAlmostEqual(brier, expected, places=9)

    def test_log_loss_floored_never_infinite(self):
        loss = evaluate.log_loss_single({"H": 0.0, "D": 0.0, "A": 1.0}, "H")
        self.assertTrue(loss < float("inf"))
        self.assertGreater(loss, 0.0)

    def test_calibration_error_formula_perfectly_calibrated_is_zero(self):
        table = [
            {"bin_index": 0, "predicted_probability_midpoint": 0.5, "empirical_frequency": 0.5, "count": 10},
            {"bin_index": 1, "predicted_probability_midpoint": None, "empirical_frequency": None, "count": 0},
        ]
        self.assertAlmostEqual(evaluate.calibration_error_from_table(table), 0.0, places=9)

    def test_score_predictions_by_league_and_outcome_present(self):
        rows = [
            ({"H": 0.6, "D": 0.2, "A": 0.2}, "H", "E0"),
            ({"H": 0.5, "D": 0.3, "A": 0.2}, "D", "D1"),
        ]
        metrics = evaluate.score_predictions(rows)
        self.assertIn("E0", metrics["by_league"])
        self.assertIn("D1", metrics["by_league"])
        self.assertIn("H", metrics["by_outcome"])
        self.assertIn("D", metrics["by_outcome"])
        self.assertIn("A", metrics["by_outcome"])
        self.assertIn("accuracy_secondary_diagnostic", metrics)

    def test_naive_baseline_uses_only_supplied_training_frequency(self):
        rows = [{"feature_vector": [], "label": "H", "record_ref": {"league_code": "E0", "price_observations": []}}]
        training_frequency = {"E0": {"H": 0.9, "D": 0.05, "A": 0.05}}
        metrics = evaluate.score_naive_league_frequency_baseline(rows, training_frequency)
        self.assertEqual(metrics["row_count"], 1)
        # Brier for a single H row with p=[0.9,0.05,0.05]:
        expected_brier = (0.9 - 1) ** 2 + 0.05**2 + 0.05**2
        self.assertAlmostEqual(metrics["brier_score"], expected_brier, places=9)

    def test_devig_excludes_rows_without_complete_opening_price(self):
        rows = [
            {"feature_vector": [], "label": "H", "record_ref": {"league_code": "E0", "price_observations": []}},
            {
                "feature_vector": [],
                "label": "H",
                "record_ref": {
                    "league_code": "E0",
                    "price_observations": [
                        {"price_stage": "OPENING", "home_odds": 2.0, "draw_odds": 3.2, "away_odds": 4.0}
                    ],
                },
            },
        ]
        metrics = evaluate.score_devigged_opening_odds(rows)
        self.assertEqual(metrics["excluded_row_count"], 1)
        self.assertEqual(metrics["included_row_count"], 1)

    def test_rolling_settlement_observation_never_labeled_test(self):
        raw_dir_tmp = tempfile.mkdtemp()
        try:
            raw_dir = Path(raw_dir_tmp) / "raw"
            _populate_raw_dir(raw_dir)
            result = train.train_pipeline(raw_dir=raw_dir)
            report = evaluate.build_evaluation_report(result)
            label = report["rolling_settlement_observation"]["label"]
            self.assertNotIn("prospective evaluation", label.lower())
            # The label explicitly clarifies "NOT a test" — that is the
            # correct terminology correction, not a violation of it. What
            # must never appear is an AFFIRMATIVE description of this
            # split as a test or as prospective evaluation.
            self.assertNotIn("is a test", label.lower())
            self.assertIn("not a test", label.lower())
            self.assertIn("rolling_settlement_observation", report)
            self.assertIn("test_splits", report)
            self.assertIn("split_locked_test", report["test_splits"])
            self.assertIn("split_out_of_time_retrospective_holdout", report["test_splits"])
        finally:
            shutil.rmtree(raw_dir_tmp, ignore_errors=True)

    def test_full_evaluation_report_smoke(self):
        raw_dir_tmp = tempfile.mkdtemp()
        try:
            raw_dir = Path(raw_dir_tmp) / "raw"
            _populate_raw_dir(raw_dir)
            result = train.train_pipeline(raw_dir=raw_dir)
            report = evaluate.build_evaluation_report(result)
            for split_id, split_report in report["test_splits"].items():
                self.assertIn("model_metrics", split_report)
                self.assertIn("naive_league_frequency_baseline_metrics", split_report)
                self.assertIn("devigged_opening_odds_metrics", split_report)
        finally:
            shutil.rmtree(raw_dir_tmp, ignore_errors=True)


class HashProvenanceTests(unittest.TestCase):
    """Operator-mandated correction: this PR must never freeze hashes
    calculated from synthetic fixtures (or any local run) as if they were
    the real, live-data expected values. `expected_hashes.json` ships with
    every split UNFROZEN_PENDING_LIVE_RUN; `hash_provenance.py` only ever
    READS that file and compares — it never writes it, and a real hash
    only ever gets pinned there by a human, in a separate PR."""

    def test_shipped_expected_hashes_file_is_all_unfrozen(self):
        # Guards against ever accidentally committing a real-looking hash
        # (e.g. one computed from this sandbox's synthetic fixtures) as if
        # it were a genuine live-data expected value.
        expected = hash_provenance.load_expected_hashes()
        for split_id in train.FROZEN_SPLIT_IDS:
            self.assertEqual(expected["split_hashes"][split_id], hash_provenance.UNFROZEN_STATUS, split_id)
        self.assertEqual(expected["frozen_dataset_hash"], hash_provenance.UNFROZEN_STATUS)

    def test_unfrozen_expected_is_always_candidate_never_mismatch(self):
        expected = {
            "split_hashes": {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS},
            "frozen_dataset_hash": hash_provenance.UNFROZEN_STATUS,
        }
        computed_split_hashes = {sid: f"deadbeef-{sid}" for sid in train.FROZEN_SPLIT_IDS}
        report = hash_provenance.check_frozen_hashes(computed_split_hashes, "deadbeef-combined", expected=expected)
        self.assertFalse(report.has_mismatch)
        for check in report.split_checks:
            self.assertEqual(check.status, hash_provenance.STATUS_CANDIDATE)
        self.assertEqual(report.combined_check.status, hash_provenance.STATUS_CANDIDATE)

    def test_pinned_hash_matching_computed_is_confirmed(self):
        split_id = train.FROZEN_SPLIT_IDS[0]
        expected = {
            "split_hashes": {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS},
            "frozen_dataset_hash": hash_provenance.UNFROZEN_STATUS,
        }
        expected["split_hashes"][split_id] = "abc123"
        computed = {sid: "other" for sid in train.FROZEN_SPLIT_IDS}
        computed[split_id] = "abc123"
        report = hash_provenance.check_frozen_hashes(computed, hash_provenance.UNFROZEN_STATUS, expected=expected)
        self.assertFalse(report.has_mismatch)
        confirmed = [c for c in report.split_checks if c.split_id == split_id][0]
        self.assertEqual(confirmed.status, hash_provenance.STATUS_CONFIRMED)

    def test_pinned_hash_not_matching_computed_is_mismatch(self):
        split_id = train.FROZEN_SPLIT_IDS[0]
        expected = {
            "split_hashes": {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS},
            "frozen_dataset_hash": hash_provenance.UNFROZEN_STATUS,
        }
        expected["split_hashes"][split_id] = "pinned-real-hash"
        computed = {sid: "other" for sid in train.FROZEN_SPLIT_IDS}
        computed[split_id] = "a-different-hash-because-real-data-changed"
        report = hash_provenance.check_frozen_hashes(computed, hash_provenance.UNFROZEN_STATUS, expected=expected)
        self.assertTrue(report.has_mismatch)
        mismatched = [c for c in report.split_checks if c.split_id == split_id][0]
        self.assertEqual(mismatched.status, hash_provenance.STATUS_MISMATCH)

    def test_combined_hash_mismatch_also_flagged(self):
        expected = {
            "split_hashes": {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS},
            "frozen_dataset_hash": "pinned-combined",
        }
        computed = {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS}
        report = hash_provenance.check_frozen_hashes(computed, "a-different-combined-hash", expected=expected)
        self.assertTrue(report.has_mismatch)
        self.assertEqual(report.combined_check.status, hash_provenance.STATUS_MISMATCH)

    def test_check_frozen_hashes_never_mutates_expected_input(self):
        expected = {
            "split_hashes": {sid: hash_provenance.UNFROZEN_STATUS for sid in train.FROZEN_SPLIT_IDS},
            "frozen_dataset_hash": hash_provenance.UNFROZEN_STATUS,
        }
        expected_copy = copy.deepcopy(expected)
        computed = {sid: "x" for sid in train.FROZEN_SPLIT_IDS}
        hash_provenance.check_frozen_hashes(computed, "y", expected=expected)
        self.assertEqual(expected, expected_copy)

    def test_evaluate_main_exits_nonzero_on_mismatch(self):
        # Full-pipeline guard: evaluate.main() must exit(1) when
        # expected_hashes.json (loaded via the real, unpatched
        # load_expected_hashes) has a pinned value that disagrees with a
        # freshly computed hash. Patch only the expected-hashes loader —
        # never the computed side — to simulate "a human already pinned a
        # real hash, and this run's data has since drifted from it." All
        # output paths point into a temp dir so this test never touches
        # this repo's real committed report/artifact files.
        raw_dir_tmp = tempfile.mkdtemp()
        try:
            raw_dir = Path(raw_dir_tmp) / "raw"
            _populate_raw_dir(raw_dir)
            out_dir = Path(raw_dir_tmp) / "out"

            import unittest.mock as mock

            fake_expected = {
                "split_hashes": {sid: "a-hash-that-will-never-match-fixture-content" for sid in train.FROZEN_SPLIT_IDS},
                "frozen_dataset_hash": "a-hash-that-will-never-match-fixture-content",
            }
            with mock.patch.object(hash_provenance, "load_expected_hashes", return_value=fake_expected):
                with self.assertRaises(SystemExit) as ctx:
                    evaluate.main(
                        raw_dir=raw_dir,
                        report_json_path=out_dir / "evaluation_report.json",
                        report_markdown_path=out_dir / "evaluation_report.md",
                        artifact_path=out_dir / "model_artifact.json",
                    )
            self.assertNotEqual(ctx.exception.code, 0)
        finally:
            shutil.rmtree(raw_dir_tmp, ignore_errors=True)

    def test_evaluate_main_writes_real_model_artifact_not_stale_placeholder(self):
        # Regression test for the real defect found during review: the
        # workflow only ever invokes evaluate.main() (never train.main()),
        # but model_artifact.json was previously written ONLY by
        # train.main() — meaning a live run's uploaded "model artifact"
        # would silently stay whatever was last committed to git, never
        # the real fitted artifact. evaluate.main() must now write it too,
        # with content matching the actual TrainingResult it just computed.
        raw_dir_tmp = tempfile.mkdtemp()
        try:
            raw_dir = Path(raw_dir_tmp) / "raw"
            _populate_raw_dir(raw_dir)
            out_dir = Path(raw_dir_tmp) / "out"
            artifact_path = out_dir / "model_artifact.json"

            expected_result = train.train_pipeline(raw_dir=raw_dir)

            evaluate.main(
                raw_dir=raw_dir,
                report_json_path=out_dir / "evaluation_report.json",
                report_markdown_path=out_dir / "evaluation_report.md",
                artifact_path=artifact_path,
            )

            self.assertTrue(artifact_path.exists(), "evaluate.main() must write model_artifact.json itself")
            written = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(written, expected_result.model_artifact.to_dict())
        finally:
            shutil.rmtree(raw_dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
