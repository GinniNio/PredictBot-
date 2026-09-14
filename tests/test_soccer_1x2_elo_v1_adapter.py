"""Tests for the first registered Soccer 1X2 forecasting adapter
(``src/pcbf_calculator/adapters/soccer_1x2_elo_v1/``).

Style matches this repo's existing convention: plain ``unittest.TestCase``.
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

from pcbf_calculator.adapters.soccer_1x2_elo_v1 import SoccerOneXTwoEloV1Adapter
from pcbf_calculator.adapters.soccer_1x2_elo_v1.errors import (
    FORECAST_ARTIFACT_AFTER_CUTOFF,
    FORECAST_ARTIFACT_STALE,
    FORECAST_COMPETITION_UNRESOLVED,
    FORECAST_FIXTURE_ALREADY_STARTED,
    FORECAST_INPUT_INCOMPLETE,
    FORECAST_TEAM_NOT_IN_ARTIFACT,
    FORECAST_TEAM_UNRESOLVED,
)
from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import normalize_name, resolve_competition, resolve_team, TeamAliasBook
from pcbf_calculator.adapters.soccer_1x2_elo_v1 import scoring
from research.soccer_1x2_elo_baseline import model as real_model
from research.soccer_1x2_elo_baseline.features import build_feature_vector as real_build_feature_vector

PACKAGE_DATA_DIR = REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data"


def make_test_artifact_dir(tmp_dir: Path, last_processed_date: str = "2025-01-01T00:00:00Z") -> Path:
    weights = [[0.0] * 9 for _ in range(3)]
    weights[0][2] = 0.9
    weights[2][2] = -0.9
    biases = [0.05, 0.0, -0.05]
    artifact = {
        "feature_names": list(scoring.FEATURE_NAMES),
        "class_order": ["H", "D", "A"],
        "league_dummy_codes": list(scoring.DUMMY_LEAGUE_CODES),
        "weights": weights,
        "biases": biases,
        "standardization": {
            "means": {"home_elo_pre": 1500.0, "away_elo_pre": 1500.0, "elo_diff": 0.0, "season_stage": 15.0},
            "stds": {"home_elo_pre": 120.0, "away_elo_pre": 120.0, "elo_diff": 150.0, "season_stage": 10.0},
        },
        "l2_strength": 0.001,
        "iterations": 2000,
        "learning_rate": 0.1,
        "final_loss": 0.98,
        "training_row_count": 1000,
    }
    import hashlib

    artifact_bytes = (json.dumps(artifact, sort_keys=True) + "\n").encode()
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()

    manifest = {
        "schema_version": "soccer-1x2-elo-v1-manifest.TEST",
        "model_version": "soccer_1x2_elo_v1-TEST",
        "temperature": 1.3,
        "artifact_sha256": artifact_hash,
        "live_snapshot": {
            "last_processed_match_date_utc": last_processed_date,
            "latest_season_by_league": {"E0": "2425"},
            "elo_ratings": {"E0|Team A": 1550.0, "E0|Team B": 1420.0},
            "season_stage_counts": {"E0|2425|Team A": 10, "E0|2425|Team B": 10},
        },
    }
    (tmp_dir / "model_artifact.json").write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (tmp_dir / "model_artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (tmp_dir / "config.yaml").write_text(
        "adapter_id: soccer_1x2_elo_v1-TEST\nmaximum_artifact_age_days: 45\n", encoding="utf-8"
    )
    return tmp_dir


def make_fixture(
    home: str = "Team A",
    away: str = "Team B",
    competition: str = "Premier League",
    kickoff_utc: str = "2025-01-10T15:00:00Z",
    forecast_cutoff_utc: str = "2025-01-01T00:00:00Z",
) -> dict:
    return {
        "home": home,
        "away": away,
        "competition": competition,
        "kickoff_utc": kickoff_utc,
        "forecast_cutoff_utc": forecast_cutoff_utc,
    }


class AdapterForecastTests(unittest.TestCase):
    def test_valid_fixture_produces_hda_probabilities_summing_to_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture())
            self.assertTrue(result.forecast_available)
            self.assertIsNone(result.no_forecast_reason)
            total = sum(result.probabilities.values())
            self.assertAlmostEqual(total, 1.0, places=9)
            self.assertEqual(set(result.probabilities), {"home_win", "draw", "away_win"})

    def test_classification_ceiling_stays_research_model_via_run_calculator(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            # Monkeypatch the registry to use our test artifact for this
            # one call, then restore -- proves the full dispatch path
            # (run_calculator -> registry -> this adapter) end to end.
            from pcbf_calculator.adapters import registry

            original = registry._ADAPTER_IMPLEMENTATIONS.get("soccer")

            class _TestAdapter(SoccerOneXTwoEloV1Adapter):
                def __init__(self):
                    super().__init__(data_dir=data_dir, config_path=data_dir / "config.yaml")

            registry._ADAPTER_IMPLEMENTATIONS["soccer"] = _TestAdapter
            try:
                from pcbf_calculator.cli import run_calculator

                result = run_calculator(
                    {
                        "event_id": "e1",
                        "category": "soccer",
                        "market_prices": {"home": 2.1, "draw": 3.4, "away": 3.9},
                        "fixture": make_fixture(),
                    }
                )
                self.assertTrue(result["forecast"]["forecast_available"])
                self.assertEqual(result["classification_ceiling"], "RESEARCH-MODEL")
                self.assertEqual(result["cash_stake"], 0)
                self.assertEqual(result["simulated_stake"], 0)
                self.assertIsNotNone(result["market_comparison"])
                for key in ("home", "draw", "away"):
                    self.assertIn(key, result["market_comparison"])
            finally:
                if original is not None:
                    registry._ADAPTER_IMPLEMENTATIONS["soccer"] = original

    def test_no_recommendation_fields_anywhere_in_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture())
            banned = {"best_bet", "pick", "recommended_outcome", "recommended_stake", "confidence_pick"}
            result_dict = {
                "sport_id": result.sport_id,
                "adapter_id": result.adapter_id,
                "forecast_available": result.forecast_available,
                "probabilities": result.probabilities,
                "model_version": result.model_version,
                "uncertainty_method": result.uncertainty_method,
                "no_forecast_reason": result.no_forecast_reason,
                "model_artifact_hash": result.model_artifact_hash,
            }
            self.assertEqual(banned & set(result_dict.keys()), set())


class AliasBookInjectionRegressionTests(unittest.TestCase):
    """Direct regression coverage for the ``alias_book`` constructor
    parameter added to ``SoccerOneXTwoEloV1Adapter`` for
    ``pcbf_calculator.orchestration.candidate_shadow_forecast``'s own
    use. Every existing caller (the registry's own default
    instantiation, every other test in this file) never passes it --
    these tests confirm the current packaged-alias behavior
    (``data_dir``'s own ``team_aliases.json``, or an empty book when
    that file is absent) is preserved exactly when it is omitted."""

    def test_alias_book_none_still_reads_data_dirs_own_team_aliases_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            (data_dir / "team_aliases.json").write_text(
                json.dumps({"leagues": {"E0": {"The Team Formerly Known As A": "Team A"}}}),
                encoding="utf-8",
            )
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture(home="The Team Formerly Known As A"))
            self.assertTrue(result.forecast_available, result.no_forecast_reason)

    def test_alias_book_none_with_no_team_aliases_file_falls_back_to_empty_exactly_as_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))  # no team_aliases.json written
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            self.assertEqual(adapter._alias_book.lookup("E0", "Team A"), None)
            result = adapter.forecast(make_fixture(home="Some Alias Only A Real Book Would Know"))
            self.assertFalse(result.forecast_available)
            self.assertEqual(result.no_forecast_reason, FORECAST_TEAM_UNRESOLVED)

    def test_explicit_alias_book_overrides_data_dirs_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            (data_dir / "team_aliases.json").write_text(
                json.dumps({"leagues": {"E0": {"Data Dir Alias": "Team A"}}}),
                encoding="utf-8",
            )
            injected_book = TeamAliasBook({"leagues": {"E0": {"Injected Alias": "Team A"}}})
            adapter = SoccerOneXTwoEloV1Adapter(
                data_dir=data_dir, config_path=data_dir / "config.yaml", alias_book=injected_book
            )
            self.assertIs(adapter._alias_book, injected_book)
            # The injected book's own alias resolves...
            result = adapter.forecast(make_fixture(home="Injected Alias"))
            self.assertTrue(result.forecast_available, result.no_forecast_reason)
            # ...but data_dir's OWN file's alias does not -- it was never
            # consulted once alias_book was supplied explicitly.
            result2 = adapter.forecast(make_fixture(home="Data Dir Alias"))
            self.assertFalse(result2.forecast_available)
            self.assertEqual(result2.no_forecast_reason, FORECAST_TEAM_UNRESOLVED)


class AbstentionTests(unittest.TestCase):
    def test_missing_required_field_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast({"home": "Team A"})
            self.assertFalse(result.forecast_available)
            self.assertEqual(result.no_forecast_reason, FORECAST_INPUT_INCOMPLETE)

    def test_unresolved_competition_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture(competition="Nonexistent League"))
            self.assertEqual(result.no_forecast_reason, FORECAST_COMPETITION_UNRESOLVED)

    def test_unresolved_team_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture(home="Unknown FC"))
            self.assertEqual(result.no_forecast_reason, FORECAST_TEAM_UNRESOLVED)

    def test_already_started_fixture_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp))
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(
                make_fixture(kickoff_utc="2024-12-01T15:00:00Z", forecast_cutoff_utc="2025-01-01T00:00:00Z")
            )
            self.assertEqual(result.no_forecast_reason, FORECAST_FIXTURE_ALREADY_STARTED)

    def test_fixture_before_artifact_cutoff_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp), last_processed_date="2025-06-01T00:00:00Z")
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(make_fixture(kickoff_utc="2025-01-10T15:00:00Z"))
            self.assertEqual(result.no_forecast_reason, FORECAST_ARTIFACT_AFTER_CUTOFF)

    def test_stale_artifact_abstains(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = make_test_artifact_dir(Path(tmp), last_processed_date="2020-01-01T00:00:00Z")
            adapter = SoccerOneXTwoEloV1Adapter(data_dir=data_dir, config_path=data_dir / "config.yaml")
            result = adapter.forecast(
                make_fixture(kickoff_utc="2025-01-10T15:00:00Z", forecast_cutoff_utc="2025-01-01T00:00:00Z")
            )
            self.assertEqual(result.no_forecast_reason, FORECAST_ARTIFACT_STALE)


class IdentityTests(unittest.TestCase):
    def test_normalize_handles_case_whitespace_punctuation(self):
        self.assertEqual(normalize_name("  Man   Utd. "), normalize_name("man utd"))

    def test_competition_resolves_to_league_code(self):
        result = resolve_competition("Premier League")
        self.assertEqual(result.resolved, "E0")

    def test_alias_resolution(self):
        alias_book = TeamAliasBook({"leagues": {"E0": {"Man Utd": "Manchester United"}}})
        result = resolve_team("Man Utd", "E0", {"Manchester United"}, alias_book)
        self.assertEqual(result.resolved, "Manchester United")
        self.assertEqual(result.method, "CHECKED_IN_ALIAS")


class ScoringFidelityTests(unittest.TestCase):
    """Proves the adapter's own vendored scoring math is byte-for-byte
    identical to the real research module's, across a range of
    real-shaped inputs -- see scoring.py's own module docstring."""

    def test_feature_vector_matches_research_module(self):
        cases = [
            (1500.0, 1420.0, "E0", 0),
            (1650.5, 1390.2, "D1", 12),
            (1500.0, 1500.0, "SP1", 25),
            (1000.0, 2000.0, "F1", 5),
        ]
        for home_elo, away_elo, league, stage in cases:
            self.assertEqual(
                scoring.build_feature_vector(home_elo, away_elo, league, stage),
                real_build_feature_vector(home_elo, away_elo, league, stage),
            )

    def test_predict_proba_matches_research_module(self):
        artifact_dict = {
            "feature_names": list(scoring.FEATURE_NAMES),
            "class_order": ["H", "D", "A"],
            "league_dummy_codes": list(scoring.DUMMY_LEAGUE_CODES),
            "weights": [
                [0.5, -0.3, 0.9, 0.1, 0.2, -0.1, 0.05, 0.15, 0.02],
                [0.1, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                [-0.4, 0.2, -0.9, -0.1, -0.1, 0.05, 0.0, -0.05, -0.02],
            ],
            "biases": [0.1, 0.0, -0.1],
            "standardization": {
                "means": {"home_elo_pre": 1500.0, "away_elo_pre": 1500.0, "elo_diff": 0.0, "season_stage": 15.0},
                "stds": {"home_elo_pre": 120.0, "away_elo_pre": 120.0, "elo_diff": 150.0, "season_stage": 10.0},
            },
            "l2_strength": 0.001,
            "iterations": 2000,
            "learning_rate": 0.1,
            "final_loss": 0.5,
            "training_row_count": 500,
        }
        vector = scoring.build_feature_vector(1580.0, 1440.0, "I1", 8)

        adapter_probs = scoring.ModelArtifact.from_dict(artifact_dict).predict_proba(vector, temperature=1.25)
        real_probs = real_model.ModelArtifact.from_dict(artifact_dict).predict_proba(vector, temperature=1.25)
        self.assertEqual(adapter_probs, real_probs)


if __name__ == "__main__":
    unittest.main()
