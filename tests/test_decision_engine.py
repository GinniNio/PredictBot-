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

    def test_no_forecast_can_never_reach_cash_even_with_perfect_stop_inputs(self):
        # Hard, non-configurable policy: try every lever a caller controls
        # (STOP-rule thresholds, evidence sample size, cash_min_sample_size)
        # to force CASH for a no-forecast category, and confirm none of them
        # can. Market-implied probabilities alone can never authorize CASH.
        generous_input = {
            "evidence": {"sample_size": 1_000_000, "min_sample_size": 1, "cash_min_sample_size": 1},
            "freshness": {"data_age_seconds": 0, "max_age_seconds": 999999},
            "liquidity": {"available_stake": 1_000_000, "min_required_stake": 1},
            "uncertainty": {"width": 0.0, "max_width": 999},
        }
        result = evaluate(generous_input, POSITIVE_MARKET, FORECAST_UNAVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "PAPER")
        self.assertNotEqual(result["classification"], "CASH")

    def test_stop_negative_ev_falls_back_to_point_ev_when_lower_bound_unavailable(self):
        # Release A: lower_bound_ev is null (no admitted adapter supplies
        # calibrated uncertainty). The rule must still function, using the
        # real (non-fabricated) point_ev instead of raising or silently
        # passing.
        no_lower_bound_market = {"lower_bound_ev": None, "point_ev": -0.01}
        result = evaluate(PASSING_INPUT, no_lower_bound_market, FORECAST_UNAVAILABLE)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["stop_rule"], "STOP_NEGATIVE_EV")

    def test_stop_negative_ev_passes_on_positive_point_ev_when_lower_bound_unavailable(self):
        no_lower_bound_market = {"lower_bound_ev": None, "point_ev": 0.03}
        result = evaluate(PASSING_INPUT, no_lower_bound_market, FORECAST_UNAVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        # Still capped at PAPER: no forecast available.
        self.assertEqual(result["classification"], "PAPER")

    def test_rule_order_stale_data_wins_over_later_rules(self):
        # Both freshness and liquidity fail; STOP_STALE_DATA must fire first.
        broken = dict(PASSING_INPUT)
        broken["freshness"] = {"data_age_seconds": 999, "max_age_seconds": 300}
        broken["liquidity"] = {"available_stake": 1, "min_required_stake": 100}
        result = evaluate(broken, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_STALE_DATA")


if __name__ == "__main__":
    unittest.main()
