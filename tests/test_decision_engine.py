import unittest

from pcbf_calculator.decision.engine import DecisionInputError, evaluate

PASSING_INPUT = {
    "evidence": {"sample_size": 500, "min_sample_size": 30, "cash_min_sample_size": 200},
    "freshness": {"data_age_seconds": 10, "max_age_seconds": 300},
    "liquidity": {"available_stake": 1000, "min_required_stake": 100},
    "uncertainty": {"width": 0.05, "max_width": 0.2},
}
POSITIVE_MARKET = {"lower_bound_ev": 0.05, "point_ev": 0.12}
FORECAST_AVAILABLE = {"forecast_available": True}
FORECAST_UNAVAILABLE = {"forecast_available": False}


class DecisionEngineTests(unittest.TestCase):
    def test_fully_passing_input_with_forecast_reaches_cash(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "CASH")
        self.assertIsNone(result["stop_rule"])

    def test_fully_passing_input_without_forecast_is_capped_at_paper(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_UNAVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "PAPER")

    def test_below_cash_sample_size_is_capped_at_paper(self):
        thin_evidence = dict(PASSING_INPUT)
        thin_evidence["evidence"] = {"sample_size": 50, "min_sample_size": 30, "cash_min_sample_size": 200}
        result = evaluate(thin_evidence, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["classification"], "PAPER")

    def test_stop_stale_data(self):
        stale = dict(PASSING_INPUT)
        stale["freshness"] = {"data_age_seconds": 999, "max_age_seconds": 300}
        result = evaluate(stale, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["stop_rule"], "STOP_STALE_DATA")

    def test_stop_insufficient_evidence(self):
        thin = dict(PASSING_INPUT)
        thin["evidence"] = {"sample_size": 5, "min_sample_size": 30}
        result = evaluate(thin, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_INSUFFICIENT_EVIDENCE")

    def test_stop_insufficient_liquidity(self):
        illiquid = dict(PASSING_INPUT)
        illiquid["liquidity"] = {"available_stake": 10, "min_required_stake": 100}
        result = evaluate(illiquid, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_INSUFFICIENT_LIQUIDITY")

    def test_stop_excessive_uncertainty(self):
        uncertain = dict(PASSING_INPUT)
        uncertain["uncertainty"] = {"width": 0.9, "max_width": 0.2}
        result = evaluate(uncertain, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_EXCESSIVE_UNCERTAINTY")

    def test_stop_negative_ev(self):
        negative_market = {"lower_bound_ev": -0.01, "point_ev": 0.02}
        result = evaluate(PASSING_INPUT, negative_market, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_NEGATIVE_EV")

    def test_zero_lower_bound_ev_also_stops(self):
        zero_market = {"lower_bound_ev": 0.0, "point_ev": 0.02}
        result = evaluate(PASSING_INPUT, zero_market, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_NEGATIVE_EV")

    def test_missing_evidence_block_raises_typed_error(self):
        broken = {k: v for k, v in PASSING_INPUT.items() if k != "evidence"}
        with self.assertRaises(DecisionInputError) as ctx:
            evaluate(broken, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(ctx.exception.code, "MISSING_DECISION_INPUT")
        self.assertEqual(ctx.exception.field, "evidence")

    def test_missing_market_profitability_field_raises_typed_error(self):
        with self.assertRaises(DecisionInputError):
            evaluate(PASSING_INPUT, {"point_ev": 0.1}, FORECAST_AVAILABLE)

    def test_missing_forecast_field_raises_typed_error(self):
        with self.assertRaises(DecisionInputError):
            evaluate(PASSING_INPUT, POSITIVE_MARKET, {})

    def test_forecast_quality_and_market_profitability_stay_distinct(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertIn("forecast_quality", result)
        self.assertIn("market_profitability", result)
        self.assertNotEqual(result["forecast_quality"], result["market_profitability"])
        self.assertEqual(result["market_profitability"], POSITIVE_MARKET)
        self.assertEqual(result["forecast_quality"], FORECAST_AVAILABLE)

    def test_rule_order_stale_data_wins_over_later_rules(self):
        # Both freshness and liquidity fail; STOP_STALE_DATA must fire first.
        broken = dict(PASSING_INPUT)
        broken["freshness"] = {"data_age_seconds": 999, "max_age_seconds": 300}
        broken["liquidity"] = {"available_stake": 1, "min_required_stake": 100}
        result = evaluate(broken, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_STALE_DATA")


if __name__ == "__main__":
    unittest.main()
