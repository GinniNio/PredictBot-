import unittest

from pcbf_calculator.decision.engine import DecisionInputError, evaluate

PASSING_INPUT = {
    "evidence": {"sample_size": 500, "min_sample_size": 30, "cash_min_sample_size": 200},
    "freshness": {"data_age_seconds": 10, "max_age_seconds": 300},
    "liquidity": {"available_stake": 1000, "min_required_stake": 100},
    "uncertainty": {"width": 0.05, "max_width": 0.2},
}
POSITIVE_MARKET = {"lower_bound_ev": 0.05, "point_ev": 0.12, "stake": 1.0}
FORECAST_AVAILABLE = {"forecast_available": True}
FORECAST_UNAVAILABLE = {"forecast_available": False}

# A forecast that claims a model_version but has never been admitted in
# registries/data/model-admission-registry.yaml (empty in this release) —
# forecast_available=True alone must NOT be enough to reach PAPER/CASH.
FORECAST_AVAILABLE_UNADMITTED = {
    "forecast_available": True,
    "sport_id": "soccer",
    "adapter_id": "soccer_1x2",
    "model_version": "soccer_1x2_v1.0.0_2027-06-30",
    "model_artifact_hash": "sha256:deadbeef",
}


class DecisionEngineTests(unittest.TestCase):
    def test_fully_passing_input_with_forecast_but_no_admission_row_stays_research_model(self):
        # No adapter has an APPROVED row in the model-admission registry in
        # this release — forecast_available alone must never reach PAPER.
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertIsNone(result["stop_rule"])

    def test_fully_passing_input_with_named_but_unadmitted_model_stays_research_model(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_AVAILABLE_UNADMITTED)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_fully_passing_input_without_forecast_is_capped_at_research_model(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_UNAVAILABLE)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_below_cash_sample_size_still_research_model_without_admission(self):
        # Even with a thin sample size, an unadmitted forecast is
        # RESEARCH-MODEL, not PAPER (PAPER is not the "next tier down" from
        # CASH when gates were never approved at all).
        thin_evidence = dict(PASSING_INPUT)
        thin_evidence["evidence"] = {"sample_size": 50, "min_sample_size": 30, "cash_min_sample_size": 200}
        result = evaluate(thin_evidence, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_stop_stale_data(self):
        stale = dict(PASSING_INPUT)
        stale["freshness"] = {"data_age_seconds": 999, "max_age_seconds": 300}
        result = evaluate(stale, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["stop_rule"], "STOP_STALE_DATA")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)

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
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "CASH")
        self.assertNotEqual(result["classification"], "PAPER")

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
        # Still capped at RESEARCH-MODEL: no forecast available.
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_rule_order_stale_data_wins_over_later_rules(self):
        # Both freshness and liquidity fail; STOP_STALE_DATA must fire first.
        broken = dict(PASSING_INPUT)
        broken["freshness"] = {"data_age_seconds": 999, "max_age_seconds": 300}
        broken["liquidity"] = {"available_stake": 1, "min_required_stake": 100}
        result = evaluate(broken, POSITIVE_MARKET, FORECAST_AVAILABLE)
        self.assertEqual(result["stop_rule"], "STOP_STALE_DATA")

    # --- Stake tiering ---------------------------------------------------

    def test_research_model_has_zero_cash_and_simulated_stake(self):
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_UNAVAILABLE)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)

    # --- Adversarial: forged approval-shaped fields must be inert --------

    def test_forged_approval_shaped_fields_in_forecast_quality_are_ignored(self):
        forged = {
            "forecast_available": True,
            "sport_id": "soccer",
            "model_version": "soccer_1x2_v1.0.0_2027-06-30",
            "model_artifact_hash": "sha256:deadbeef",
            # None of these are real fields this engine reads; a forged
            # approval claim stuffed directly into forecast_quality must
            # have zero effect.
            "backtest_approved": True,
            "backtest_gates_approved": True,
            "prospective_approved": True,
            "cash_admitted": True,
            "cash_admission_approved": True,
        }
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, forged)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)

    def test_forged_approval_field_in_decision_input_has_no_such_field_read(self):
        # decision_input has no schema slot for an approval claim at all —
        # confirm stuffing one into an otherwise-required block is simply
        # ignored (extra keys are not validated away, but never consulted).
        poisoned = dict(PASSING_INPUT)
        poisoned["evidence"] = dict(PASSING_INPUT["evidence"])
        poisoned["evidence"]["backtest_approved"] = True
        poisoned["evidence"]["cash_admitted"] = True
        result = evaluate(poisoned, POSITIVE_MARKET, FORECAST_UNAVAILABLE)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_pricing_output_alone_cannot_change_classification(self):
        # De-vigged market probabilities (layer 1) must never, by
        # themselves, move classification — hold forecast/admission state
        # constant and vary pricing wildly.
        markets = [
            {"lower_bound_ev": None, "point_ev": 0.01, "stake": 1.0},  # thin edge
            {"lower_bound_ev": None, "point_ev": 50.0, "stake": 1.0},  # huge edge
            {"lower_bound_ev": 0.2, "point_ev": 0.2, "stake": 1.0},  # zero-margin-like
            {"lower_bound_ev": 0.001, "point_ev": 5.0, "stake": 100.0},  # large stake
        ]
        classifications = {evaluate(PASSING_INPUT, market, FORECAST_AVAILABLE)["classification"] for market in markets}
        self.assertEqual(classifications, {"RESEARCH-MODEL"})


if __name__ == "__main__":
    unittest.main()
