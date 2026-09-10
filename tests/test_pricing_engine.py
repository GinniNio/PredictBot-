import unittest

from pcbf_calculator.pricing.engine import PricingFailure, analyze_market


class PricingEngineTests(unittest.TestCase):
    def test_two_way_market(self):
        result = analyze_market({"player_a": 1.8, "player_b": 2.05})
        self.assertEqual(result["market_structure"], "two_way")
        self.assertEqual(result["outcome_count"], 2)
        probs = {o["outcome"]: o["fair_probability"] for o in result["outcomes"]}
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        self.assertGreater(result["bookmaker_margin"], 0)

    def test_three_way_market(self):
        result = analyze_market({"home": 1.9, "draw": 3.4, "away": 4.3})
        self.assertEqual(result["market_structure"], "three_way")
        self.assertEqual(result["outcome_count"], 3)
        probs = {o["outcome"]: o["fair_probability"] for o in result["outcomes"]}
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)

    def test_n_way_market(self):
        prices = {f"runner_{i}": 5.0 + i for i in range(8)}
        result = analyze_market(prices)
        self.assertEqual(result["market_structure"], "n_way")
        self.assertEqual(result["outcome_count"], 8)
        probs = {o["outcome"]: o["fair_probability"] for o in result["outcomes"]}
        self.assertAlmostEqual(sum(probs.values()), 1.0, places=9)
        self.assertEqual(len(result["ranking_by_point_ev"]), 8)

    def test_incomplete_prices_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": 1.9})
        self.assertEqual(ctx.exception.code, "INCOMPLETE_OPPOSING_PRICES")

    def test_missing_prices_object_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market(None)
        self.assertEqual(ctx.exception.code, "INCOMPLETE_OPPOSING_PRICES")

    def test_invalid_price_type_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": "2.0", "away": 2.0})
        self.assertEqual(ctx.exception.code, "INVALID_PRICE_VALUE")
        self.assertEqual(ctx.exception.field, "market_prices.home")

    def test_null_price_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": None, "away": 2.0})
        self.assertEqual(ctx.exception.code, "INVALID_PRICE_VALUE")

    def test_boolean_price_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": True, "away": 2.0})
        self.assertEqual(ctx.exception.code, "INVALID_PRICE_VALUE")

    def test_price_at_or_below_one_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": 1.0, "away": 2.0})
        self.assertEqual(ctx.exception.code, "INVALID_PRICE_VALUE")

    def test_nan_price_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": float("nan"), "away": 2.0})
        self.assertEqual(ctx.exception.code, "INVALID_PRICE_VALUE")

    def test_zero_margin_arbitrage_free_edge_case(self):
        # 1/2 + 1/2 = 1.0 exactly: no vig at all.
        result = analyze_market({"home": 2.0, "away": 2.0})
        self.assertAlmostEqual(result["bookmaker_margin"], 0.0, places=9)
        for outcome in result["outcomes"]:
            self.assertAlmostEqual(outcome["fair_probability"], outcome["implied_probability"], places=9)
            self.assertAlmostEqual(outcome["fair_odds"], outcome["price"], places=9)
            # No edge either way: point EV should be ~0.
            self.assertAlmostEqual(outcome["point_ev"], 0.0, places=9)

    def test_heavily_vigged_market(self):
        # Very short prices on every outcome: implied probabilities sum well above 1.
        result = analyze_market({"home": 1.2, "draw": 1.3, "away": 1.4})
        self.assertGreater(result["bookmaker_margin"], 0.4)
        for outcome in result["outcomes"]:
            # Every fair probability must still land in [0, 1] after de-vigging.
            self.assertGreaterEqual(outcome["fair_probability"], 0.0)
            self.assertLessEqual(outcome["fair_probability"], 1.0)
            # A heavily vigged market should show negative edge on the offered price.
            self.assertLess(outcome["point_ev"], 0.0)

    def test_lower_bound_ev_is_never_above_point_ev(self):
        result = analyze_market({"home": 3.0, "away": 1.5})
        for outcome in result["outcomes"]:
            self.assertLessEqual(outcome["lower_bound_ev"], outcome["point_ev"] + 1e-12)

    def test_deterministic_repeat(self):
        prices = {"home": 2.1, "draw": 3.4, "away": 3.9}
        first = analyze_market(prices)
        second = analyze_market(prices)
        self.assertEqual(first, second)

    def test_stake_scales_ev_linearly(self):
        prices = {"home": 2.5, "away": 1.6}
        unit = analyze_market(prices, stake=1.0)
        doubled = analyze_market(prices, stake=2.0)
        for a, b in zip(unit["outcomes"], doubled["outcomes"]):
            self.assertAlmostEqual(b["point_ev"], a["point_ev"] * 2, places=9)


if __name__ == "__main__":
    unittest.main()
