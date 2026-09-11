import unittest

from pcbf_calculator.pricing.engine import PricingFailure, _market_quality, analyze_market


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
        self.assertEqual(len(result["outcomes"]), 8)

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

    def test_lower_bound_ev_is_null_without_calibrated_uncertainty(self):
        # No admitted forecasting adapter supplies calibrated uncertainty in
        # Release A, so the engine must never fabricate a confidence bound
        # (e.g. from an invented sample size) — lower_bound_ev must be null,
        # typed as UNCERTAINTY_UNAVAILABLE, for every outcome.
        result = analyze_market({"home": 3.0, "away": 1.5})
        for outcome in result["outcomes"]:
            self.assertIsNone(outcome["lower_bound_ev"])
            self.assertIsNone(outcome["lower_bound_probability"])
            self.assertIsNone(outcome["uncertainty_method"])
            self.assertEqual(outcome["lower_bound_ev_reason"], "UNCERTAINTY_UNAVAILABLE")

    def test_lower_bound_ev_computed_when_real_uncertainty_supplied(self):
        # Once a future admitted adapter supplies real calibrated
        # uncertainty (never an invented constant), the engine can and must
        # compute a real confidence-bounded EV from it.
        result = analyze_market(
            {"home": 3.0, "away": 1.5},
            uncertainty={"home": {"sample_size": 500, "z": 1.645}},
        )
        by_name = {o["outcome"]: o for o in result["outcomes"]}
        home = by_name["home"]
        away = by_name["away"]
        self.assertIsNotNone(home["lower_bound_ev"])
        self.assertIsNone(home["lower_bound_ev_reason"])
        self.assertEqual(home["uncertainty_method"], "wilson_score")
        self.assertLessEqual(home["lower_bound_ev"], home["point_ev"] + 1e-12)
        # away had no uncertainty data supplied -> still unavailable.
        self.assertIsNone(away["lower_bound_ev"])
        self.assertEqual(away["lower_bound_ev_reason"], "UNCERTAINTY_UNAVAILABLE")

    def test_unsupported_de_vig_method_fails_typed(self):
        with self.assertRaises(PricingFailure) as ctx:
            analyze_market({"home": 1.9, "away": 2.0}, de_vig_method="shin")
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_DE_VIG_METHOD")

    def test_de_vig_method_recorded_on_result(self):
        result = analyze_market({"home": 1.9, "away": 2.0})
        self.assertEqual(result["de_vig_method"], "multiplicative_proportional")

    def test_market_quality_fields_present_and_computed(self):
        # Release A may rank markets (never outcomes) by completeness,
        # margin, and evidence quality.
        result = analyze_market({"home": 1.9, "draw": 3.4, "away": 4.3})
        quality = result["market_quality"]
        self.assertEqual(quality["completeness"], "COMPLETE")
        self.assertAlmostEqual(quality["bookmaker_margin"], result["bookmaker_margin"], places=9)
        self.assertIn(quality["evidence_quality"], {"NORMAL", "LOW_EVIDENCE_HIGH_MARGIN", "ANOMALOUS_NEGATIVE_MARGIN"})
        self.assertIsInstance(quality["research_priority_score"], float)

    def test_no_outcome_level_ranking_field_exists(self):
        # ranking_by_point_ev implied a per-outcome betting recommendation
        # from proportional-de-vig EV alone; it must not exist in Release A
        # output.
        result = analyze_market({"home": 1.9, "draw": 3.4, "away": 4.3})
        self.assertNotIn("ranking_by_point_ev", result)

    def test_deterministic_repeat(self):
        prices = {"home": 2.1, "draw": 3.4, "away": 3.9}
        first = analyze_market(prices)
        second = analyze_market(prices)
        self.assertEqual(first, second)

    def test_arbitrage_shaped_market_is_flagged_anomalous_negative_margin(self):
        # Implied probabilities summing to less than 1.0 -- a bettor backing
        # every outcome at these prices locks in a guaranteed profit
        # regardless of outcome. Real markets do occasionally show this
        # shape (a pricing error, or a stale/racing quote a moment before
        # correction) and it must be flagged loudly, never silently priced
        # as if it were an ordinary market.
        result = analyze_market({"home": 2.5, "away": 2.5})
        self.assertLess(result["bookmaker_margin"], 0.0)
        self.assertEqual(result["market_quality"]["evidence_quality"], "ANOMALOUS_NEGATIVE_MARGIN")

    def test_margin_tier_boundaries_are_exact(self):
        # _market_quality's own tier boundaries: margin < 0 ->
        # ANOMALOUS_NEGATIVE_MARGIN, margin > 0.5 -> LOW_EVIDENCE_HIGH_MARGIN,
        # otherwise NORMAL. Exercised directly against the exact boundary
        # values (0.0 and 0.5) rather than through analyze_market's own
        # price -> margin arithmetic, which cannot reliably hit an exact
        # float boundary from real prices.
        self.assertEqual(_market_quality(0.0, 3)["evidence_quality"], "NORMAL")
        self.assertEqual(_market_quality(0.5, 3)["evidence_quality"], "NORMAL")  # 0.5 itself is NOT high-margin
        self.assertEqual(_market_quality(0.5 + 1e-9, 3)["evidence_quality"], "LOW_EVIDENCE_HIGH_MARGIN")
        self.assertEqual(_market_quality(-1e-9, 3)["evidence_quality"], "ANOMALOUS_NEGATIVE_MARGIN")

    def test_stake_scales_ev_linearly(self):
        prices = {"home": 2.5, "away": 1.6}
        unit = analyze_market(prices, stake=1.0)
        doubled = analyze_market(prices, stake=2.0)
        for a, b in zip(unit["outcomes"], doubled["outcomes"]):
            self.assertAlmostEqual(b["point_ev"], a["point_ev"] * 2, places=9)


if __name__ == "__main__":
    unittest.main()
