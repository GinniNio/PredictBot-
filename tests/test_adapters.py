import unittest

from pcbf_calculator.adapters.registry import (
    NoForecastAdapter,
    UnsupportedCombinationError,
    get_adapter,
    run_forecast,
)


class AdapterFrameworkTests(unittest.TestCase):
    def test_registered_category_with_no_adapter_returns_no_forecast_stub(self):
        adapter = get_adapter("soccer")
        self.assertIsInstance(adapter, NoForecastAdapter)
        result = adapter.forecast({})
        self.assertFalse(result.forecast_available)
        self.assertIsNone(result.probabilities)
        # soccer carries its own DESIGN_IN_PROGRESS reason string (a design
        # spec exists, docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md) — still
        # NoForecastAdapter, still no fabricated forecast.
        self.assertEqual(result.no_forecast_reason, "FORECASTING_ADAPTER_DESIGN_IN_PROGRESS_NOT_YET_BUILT")

    def test_run_forecast_never_fabricates_a_probability(self):
        result = run_forecast("basketball", {"home_team": "A", "away_team": "B"})
        self.assertFalse(result["forecast_available"])
        self.assertIsNone(result["probabilities"])

    def test_unregistered_combination_raises_typed_error(self):
        with self.assertRaises(UnsupportedCombinationError) as ctx:
            get_adapter("underwater_basket_weaving")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_SPORT_MARKET_COMBINATION")

    def test_no_generic_football_assumptions_leak_into_other_sports(self):
        # The stub adapter is purely id-driven; it must not special-case
        # soccer or borrow soccer's declaration/reason for another sport.
        soccer_adapter = get_adapter("soccer")
        tennis_adapter = get_adapter("tennis")
        self.assertEqual(soccer_adapter.declaration.sport_id, "soccer")
        self.assertEqual(tennis_adapter.declaration.sport_id, "tennis")
        self.assertNotEqual(
            soccer_adapter.forecast({}).sport_id, tennis_adapter.forecast({}).sport_id
        )

    def test_every_required_category_resolves_to_an_adapter(self):
        from pcbf_calculator.registries.loader import REQUIRED_CATEGORY_IDS

        for category_id in REQUIRED_CATEGORY_IDS:
            adapter = get_adapter(category_id)
            self.assertEqual(adapter.declaration.sport_id, category_id)


if __name__ == "__main__":
    unittest.main()
