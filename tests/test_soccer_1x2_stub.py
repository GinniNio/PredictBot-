"""Contract-shape tests for SoccerOneXTwoAdapter
(src/pcbf_calculator/adapters/soccer_1x2_stub.py).

Proves: the stub implements the SportAdapter interface, never fabricates a
probability regardless of what fixture data it is given (including a
fixture that supplies every required feature with plausible-looking
values), and is not wired into the registry dispatch — so DESIGN_IN_PROGRESS
cannot accidentally unlock PAPER/CASH for soccer via this stub existing.
"""

import unittest

from pcbf_calculator.adapters.base import ForecastResult, SportAdapter
from pcbf_calculator.adapters.registry import NoForecastAdapter, _ADAPTER_IMPLEMENTATIONS, get_adapter
from pcbf_calculator.adapters.soccer_1x2_stub import (
    NO_ADMITTED_MODEL_VERSION,
    REQUIRED_FEATURE_IDS,
    SoccerOneXTwoAdapter,
)
from pcbf_calculator.decision.engine import evaluate as evaluate_decision

PLAUSIBLE_FULL_FIXTURE = {
    "home_team_elo_pre_match": 1600.0,
    "away_team_elo_pre_match": 1550.0,
    "home_team_rolling_goals_for_last_10": 1.8,
    "home_team_rolling_goals_against_last_10": 0.9,
    "away_team_rolling_goals_for_last_10": 1.3,
    "away_team_rolling_goals_against_last_10": 1.1,
    "market_snapshot_odds_1x2": {"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
    "days_since_last_match_home": 6,
    "days_since_last_match_away": 4,
}


class SoccerOneXTwoStubTests(unittest.TestCase):
    def test_implements_sport_adapter_interface(self):
        adapter = SoccerOneXTwoAdapter()
        self.assertIsInstance(adapter, SportAdapter)
        declaration = adapter.declaration
        self.assertEqual(declaration.sport_id, "soccer")
        self.assertEqual(set(declaration.feature_requirements), set(REQUIRED_FEATURE_IDS))

    def test_never_fabricates_a_probability_with_no_fixture_data(self):
        adapter = SoccerOneXTwoAdapter()
        result = adapter.forecast({})
        self.assertIsInstance(result, ForecastResult)
        self.assertFalse(result.forecast_available)
        self.assertIsNone(result.probabilities)
        self.assertEqual(result.no_forecast_reason, NO_ADMITTED_MODEL_VERSION)

    def test_never_fabricates_a_probability_even_with_every_required_feature_present(self):
        # The critical fail-closed proof: even a fixture that supplies
        # every required feature with plausible values still gets no
        # forecast, because no model version has actually cleared the
        # spec's backtest/promotion gates. Missing-feature handling is not
        # what gates this stub — the absence of an admitted model is.
        adapter = SoccerOneXTwoAdapter()
        result = adapter.forecast(dict(PLAUSIBLE_FULL_FIXTURE))
        self.assertFalse(result.forecast_available)
        self.assertIsNone(result.probabilities)
        self.assertIsNone(result.model_version)
        self.assertIsNone(result.uncertainty_method)

    def test_forecast_result_is_json_shape_compatible(self):
        adapter = SoccerOneXTwoAdapter()
        payload = adapter.forecast(dict(PLAUSIBLE_FULL_FIXTURE)).to_dict()
        self.assertEqual(payload["sport_id"], "soccer")
        self.assertFalse(payload["forecast_available"])
        self.assertIsNone(payload["probabilities"])

    def test_stub_never_authorizes_paper_or_cash_through_the_decision_engine(self):
        # Feed the stub's own (always-declining) forecast into the real
        # decision engine with maximally generous STOP-rule inputs — the
        # same invariant tests/test_decision_engine.py already proves
        # generically, repeated here tied specifically to this adapter's
        # contract shape.
        adapter = SoccerOneXTwoAdapter()
        forecast_quality = adapter.forecast(dict(PLAUSIBLE_FULL_FIXTURE)).to_dict()
        market_profitability = {
            "lower_bound_ev": None,
            "point_ev": 10.0,  # generously positive
        }
        decision_input = {
            "evidence": {"sample_size": 10_000, "min_sample_size": 1, "cash_min_sample_size": 1},
            "freshness": {"data_age_seconds": 0, "max_age_seconds": 999_999},
            "liquidity": {"available_stake": 1_000_000, "min_required_stake": 1},
            "uncertainty": {"width": 0.0, "max_width": 999.0},
        }
        result = evaluate_decision(decision_input, market_profitability, forecast_quality)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "CASH")
        self.assertNotEqual(result["classification"], "PAPER")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)

    def test_stub_is_not_registered_in_the_live_dispatch_table(self):
        # DESIGN_IN_PROGRESS must not accidentally unlock new runtime
        # behavior: get_adapter("soccer") still resolves to the generic
        # NoForecastAdapter, never to this stub, because the stub is
        # deliberately absent from _ADAPTER_IMPLEMENTATIONS.
        self.assertNotIn("soccer", _ADAPTER_IMPLEMENTATIONS)
        adapter = get_adapter("soccer")
        self.assertIsInstance(adapter, NoForecastAdapter)
        self.assertNotIsInstance(adapter, SoccerOneXTwoAdapter)


if __name__ == "__main__":
    unittest.main()
