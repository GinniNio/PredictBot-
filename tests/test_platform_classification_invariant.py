"""Platform-wide, adversarial proof of the decision engine's classification
invariants (``pcbf_calculator/decision/engine.py``):

1. No combination of caller-supplied STOP-rule inputs, evidence, liquidity,
   or pricing output can promote a ``forecast_available: False`` result
   above ``RESEARCH-MODEL`` — not just "never CASH" (already proven in
   ``tests/test_decision_engine.py``), but "never above RESEARCH-MODEL at
   all," a strictly stronger claim now that PAPER itself requires an
   admission-registry-approved model version.
2. Gate approval is resolved *only* from the committed
   ``registries/data/model-admission-registry.yaml`` via
   ``registries/model_admission.py::resolve_model_admission`` — never from
   any caller-supplied or adapter-reported boolean claim. These tests
   monkeypatch the registry's row-loading function to simulate rows that do
   not exist in the (currently empty) real file, so the lookup/hash-binding
   mechanism itself can be exercised end to end.
3. Stake is provably tiered by classification (``cash_stake`` zero for
   every classification except ``CASH``).
"""

import unittest
from unittest.mock import patch

from pcbf_calculator.decision.engine import evaluate

PASSING_INPUT = {
    "evidence": {"sample_size": 500, "min_sample_size": 30, "cash_min_sample_size": 200},
    "freshness": {"data_age_seconds": 10, "max_age_seconds": 300},
    "liquidity": {"available_stake": 1000, "min_required_stake": 100},
    "uncertainty": {"width": 0.05, "max_width": 0.2},
}
POSITIVE_MARKET = {"lower_bound_ev": 0.05, "point_ev": 0.12, "stake": 25.0}

ADAPTER_ID = "soccer"
MODEL_VERSION = "soccer_1x2_v1.0.0_2027-06-30"
REAL_HASH = "sha256:" + "a" * 64
OTHER_HASH = "sha256:" + "b" * 64

FORECAST_UNAVAILABLE = {"forecast_available": False}

FORECAST_CLAIMING_MODEL = {
    "forecast_available": True,
    "sport_id": ADAPTER_ID,
    "model_version": MODEL_VERSION,
    "model_artifact_hash": REAL_HASH,
}

_MODEL_ADMISSION_PATCH_TARGET = "pcbf_calculator.registries.model_admission.load_model_admission_rows"


def _admission_row(**overrides):
    row = {
        "id": f"{ADAPTER_ID}__{MODEL_VERSION}",
        "adapter_id": ADAPTER_ID,
        "model_version": MODEL_VERSION,
        "model_artifact_hash": REAL_HASH,
        "backtest_gates_status": "APPROVED",
        "prospective_status": "APPROVED",
        "cash_admission_status": "APPROVED",
        "rationale": "test fixture",
    }
    row.update(overrides)
    return row


class NoForecastNeverAboveResearchModelTests(unittest.TestCase):
    """Item 1: exhaustively try to break "no forecast -> never above
    RESEARCH-MODEL" with every maximally-generous input this engine's
    schema exposes, including wild pricing swings."""

    def test_every_stop_rule_input_maximized_plus_wild_pricing_stays_research_model(self):
        maximal_inputs = {
            "evidence": {"sample_size": 10**9, "min_sample_size": 0, "cash_min_sample_size": 0},
            "freshness": {"data_age_seconds": 0, "max_age_seconds": 10**9},
            "liquidity": {"available_stake": 10**9, "min_required_stake": 0},
            "uncertainty": {"width": 0.0, "max_width": 10**9},
        }
        wild_markets = [
            {"lower_bound_ev": 10**6, "point_ev": 10**6, "stake": 10**6},
            {"lower_bound_ev": None, "point_ev": 10**6, "stake": 1.0},
            {"lower_bound_ev": 0.0001, "point_ev": 0.0001, "stake": 1.0},
        ]
        for market in wild_markets:
            result = evaluate(maximal_inputs, market, FORECAST_UNAVAILABLE)
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["classification"], "RESEARCH-MODEL")
            self.assertEqual(result["cash_stake"], 0)
            self.assertEqual(result["simulated_stake"], 0)

    def test_a_forecast_claiming_a_model_with_no_admission_row_also_stays_research_model(self):
        # No mock patch here: the real (empty) registry has no row for
        # ADAPTER_ID/MODEL_VERSION at all.
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)


class ForgedApprovalCannotReachPaperOrCashTests(unittest.TestCase):
    """Item: forged caller/adapter-reported approval-shaped fields have
    zero effect — only the committed registry lookup matters."""

    def test_forged_approval_fields_alongside_a_real_looking_forecast_stay_research_model(self):
        forged = dict(FORECAST_CLAIMING_MODEL)
        forged.update(
            {
                "backtest_approved": True,
                "backtest_gates_approved": True,
                "prospective_approved": True,
                "cash_admitted": True,
                "cash_admission_approved": True,
                "admitted": True,
            }
        )
        result = evaluate(PASSING_INPUT, POSITIVE_MARKET, forged)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)


class UnregisteredModelCannotReachPaperTests(unittest.TestCase):
    def test_unknown_model_version_with_populated_but_non_matching_registry(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(model_version="some_other_version")]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")


class UnapprovedBacktestGatesStayResearchModelTests(unittest.TestCase):
    def test_pending_backtest_gates_status_stays_research_model(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(backtest_gates_status="PENDING")]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_rejected_backtest_gates_status_stays_research_model(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(backtest_gates_status="REJECTED")]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "PAPER")


class PaperAlwaysHasZeroCashStakeTests(unittest.TestCase):
    def test_paper_reachable_case_has_zero_cash_stake_and_permits_simulated_stake(self):
        # backtest_gates_status APPROVED + hash match => backtest tier
        # clears; cash_admission_status not APPROVED keeps it at PAPER
        # rather than CASH.
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(cash_admission_status="PENDING")]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], POSITIVE_MARKET["stake"])

    def test_paper_via_thin_sample_size_below_cash_bar_also_has_zero_cash_stake(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            thin = dict(PASSING_INPUT)
            thin["evidence"] = {"sample_size": 50, "min_sample_size": 30, "cash_min_sample_size": 200}
            result = evaluate(thin, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertEqual(result["cash_stake"], 0)


class OnlyMatchingHashCanReachCashTests(unittest.TestCase):
    def test_fully_approved_row_but_mismatched_reported_hash_stays_research_model(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(model_artifact_hash=REAL_HASH)]):
            tampered = dict(FORECAST_CLAIMING_MODEL)
            tampered["model_artifact_hash"] = OTHER_HASH  # tampered/different build
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, tampered)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "CASH")

    def test_fully_approved_row_with_matching_hash_can_reach_cash(self):
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], POSITIVE_MARKET["stake"])
        self.assertEqual(result["simulated_stake"], 0)


class PricingOutputCannotChangeClassificationTests(unittest.TestCase):
    def test_wildly_different_pricing_outputs_never_change_classification_with_admission_state_fixed(self):
        markets = [
            {"lower_bound_ev": None, "point_ev": 0.0001, "stake": 1.0},  # zero-margin-like, thin edge
            {"lower_bound_ev": None, "point_ev": 500.0, "stake": 1.0},  # huge edge
            {"lower_bound_ev": 0.3, "point_ev": 0.3, "stake": 1.0},  # arbitrage-free-looking
            {"lower_bound_ev": 0.001, "point_ev": 10.0, "stake": 1000.0},  # heavily-vigged-looking, big stake
        ]
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            classifications = {
                evaluate(PASSING_INPUT, market, FORECAST_CLAIMING_MODEL)["classification"] for market in markets
            }
        self.assertEqual(classifications, {"CASH"})

        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[]):
            classifications_unadmitted = {
                evaluate(PASSING_INPUT, market, FORECAST_CLAIMING_MODEL)["classification"] for market in markets
            }
        self.assertEqual(classifications_unadmitted, {"RESEARCH-MODEL"})


if __name__ == "__main__":
    unittest.main()
