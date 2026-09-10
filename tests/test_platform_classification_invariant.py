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
4. CASH requires all three admission flags — ``backtest_gates_approved``,
   ``prospective_approved``, and ``cash_admission_approved`` — not just the
   last two; a row PENDING/REJECTED on ``prospective_status`` must stay
   ``PAPER``, never ``CASH``.
5. The model-admission lookup is keyed on ``adapter_id``, never ``sport_id``
   (a sport can have multiple adapters); ``resolve_model_admission`` itself
   independently enforces gate ordering (no ``prospective_approved``
   without ``backtest_gates_approved``, no ``cash_admission_approved``
   without both) as defense in depth against a malformed registry row.
6. ``TRUSTED_EXECUTION_PROVENANCE_AVAILABLE`` gates the entire admission
   lookup's authority: while it is ``False`` (the shipped value), even a
   fully-populated, fully-``APPROVED``, hash-matched row must stay
   ``RESEARCH-MODEL``. Flipping it (patched) to ``True`` for an isolated
   test proves it is the actual gate, not dead code.
7. That flag is a hardcoded kill switch, not a trust mechanism: its value
   is a bare ``False`` in source, and no environment variable, CLI
   argument, or request/``decision_input``/``forecast_quality`` field can
   change it or otherwise cause the engine to behave as though it were
   ``True`` (``TrustedProvenanceIsAHardcodedKillSwitchTests``).
"""

import os
import unittest
from unittest.mock import patch

import pcbf_calculator.decision.trusted_provenance as trusted_provenance_module
from pcbf_calculator.decision.engine import evaluate

PASSING_INPUT = {
    "evidence": {"sample_size": 500, "min_sample_size": 30, "cash_min_sample_size": 200},
    "freshness": {"data_age_seconds": 10, "max_age_seconds": 300},
    "liquidity": {"available_stake": 1000, "min_required_stake": 100},
    "uncertainty": {"width": 0.05, "max_width": 0.2},
}
POSITIVE_MARKET = {"lower_bound_ev": 0.05, "point_ev": 0.12, "stake": 25.0}

# sport_id and adapter_id are deliberately distinct here (unlike the
# defect this test file used to encode): a sport can have multiple
# adapters, and the model-admission lookup is keyed on adapter_id, never
# sport_id.
SPORT_ID = "soccer"
ADAPTER_ID = "soccer_1x2"
MODEL_VERSION = "soccer_1x2_v1.0.0_2027-06-30"
REAL_HASH = "sha256:" + "a" * 64
OTHER_HASH = "sha256:" + "b" * 64

FORECAST_UNAVAILABLE = {"forecast_available": False}

FORECAST_CLAIMING_MODEL = {
    "forecast_available": True,
    "sport_id": SPORT_ID,
    "adapter_id": ADAPTER_ID,
    "model_version": MODEL_VERSION,
    "model_artifact_hash": REAL_HASH,
}

_MODEL_ADMISSION_PATCH_TARGET = "pcbf_calculator.registries.model_admission.load_model_admission_rows"
_TRUSTED_PROVENANCE_PATCH_TARGET = "pcbf_calculator.decision.engine.TRUSTED_EXECUTION_PROVENANCE_AVAILABLE"


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
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(backtest_gates_status="PENDING")]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")

    def test_rejected_backtest_gates_status_stays_research_model(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(backtest_gates_status="REJECTED")]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "PAPER")


class ProspectiveApprovalRequiredForCashTests(unittest.TestCase):
    """Defect 1: ``prospective_approved`` must be enforced, not just loaded
    and ignored. A row with backtest gates and CASH admission both APPROVED
    but prospective still PENDING/REJECTED must resolve to PAPER, never
    CASH."""

    def test_pending_prospective_status_stays_paper_not_cash(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(prospective_status="PENDING")]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertNotEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], 0)

    def test_rejected_prospective_status_stays_paper_not_cash(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(prospective_status="REJECTED")]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertNotEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], 0)

    def test_fully_approved_row_including_prospective_can_still_reach_cash(self):
        # A fully-approved row already has prospective_status APPROVED
        # (see _admission_row's defaults) -- confirms the fix does not
        # regress the legitimate CASH path.
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "CASH")


class MalformedGateOrderingResolvesClosedTests(unittest.TestCase):
    """Registry validation rules: a row cannot claim a later gate APPROVED
    without its earlier gate(s) also APPROVED. ``resolve_model_admission``
    must defend against this independently of file-level validation."""

    def test_prospective_approved_without_backtest_approved_resolves_closed(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET,
            return_value=[_admission_row(backtest_gates_status="PENDING", prospective_status="APPROVED")],
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)


class PaperAlwaysHasZeroCashStakeTests(unittest.TestCase):
    def test_paper_reachable_case_has_zero_cash_stake_and_permits_simulated_stake(self):
        # backtest_gates_status APPROVED + hash match => backtest tier
        # clears; cash_admission_status not APPROVED keeps it at PAPER
        # rather than CASH.
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(cash_admission_status="PENDING")]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], POSITIVE_MARKET["stake"])

    def test_paper_via_thin_sample_size_below_cash_bar_also_has_zero_cash_stake(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]
        ):
            thin = dict(PASSING_INPUT)
            thin["evidence"] = {"sample_size": 50, "min_sample_size": 30, "cash_min_sample_size": 200}
            result = evaluate(thin, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "PAPER")
        self.assertEqual(result["cash_stake"], 0)


class OnlyMatchingHashCanReachCashTests(unittest.TestCase):
    def test_fully_approved_row_but_mismatched_reported_hash_stays_research_model(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row(model_artifact_hash=REAL_HASH)]
        ):
            tampered = dict(FORECAST_CLAIMING_MODEL)
            tampered["model_artifact_hash"] = OTHER_HASH  # tampered/different build
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, tampered)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "CASH")

    def test_fully_approved_row_with_matching_hash_can_reach_cash(self):
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], POSITIVE_MARKET["stake"])
        self.assertEqual(result["simulated_stake"], 0)


class TrustedExecutionProvenanceGateTests(unittest.TestCase):
    """Defect 3: until trusted execution provenance actually exists, even a
    fully-populated, fully-APPROVED, hash-matched admission row must fail
    closed. Flipping the flag proves it is the actual gate, not dead code."""

    def test_fully_approved_hash_matched_row_stays_research_model_while_flag_is_false(self):
        # TRUSTED_EXECUTION_PROVENANCE_AVAILABLE is False by default (the
        # real, shipped value) -- no patch needed for the flag itself.
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)

    def test_same_fully_approved_row_reaches_cash_once_flag_is_flipped_true(self):
        # Proves the flag is the actual gate: identical registry state,
        # only the flag changes, and the outcome flips from RESEARCH-MODEL
        # to CASH.
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]
        ):
            result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], POSITIVE_MARKET["stake"])

    def test_no_combination_of_maximally_favorable_admission_fields_reaches_paper_or_cash_while_flag_is_false(
        self,
    ):
        # Guard 3 (operator-requested expansion): all three statuses
        # APPROVED, hash matching, sample size huge, cash_min_sample_size
        # zero -- every lever maximally favorable at once. Still must not
        # slip past RESEARCH-MODEL while the flag is False.
        maximally_favorable_input = {
            "evidence": {"sample_size": 10**9, "min_sample_size": 0, "cash_min_sample_size": 0},
            "freshness": {"data_age_seconds": 0, "max_age_seconds": 10**9},
            "liquidity": {"available_stake": 10**9, "min_required_stake": 0},
            "uncertainty": {"width": 0.0, "max_width": 10**9},
        }
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            result = evaluate(maximally_favorable_input, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertNotEqual(result["classification"], "PAPER")
        self.assertNotEqual(result["classification"], "CASH")
        self.assertEqual(result["cash_stake"], 0)
        self.assertEqual(result["simulated_stake"], 0)


class TrustedProvenanceIsAHardcodedKillSwitchTests(unittest.TestCase):
    """Guards 1 and 2 (operator-requested): the flag must be a bare, fixed
    ``False`` in source -- no env var, no request/decision-input field, and
    (per cli.py inspection below) no CLI argument can change it or cause the
    engine to behave as though it were ``True``."""

    def test_flag_is_literally_false_at_import_time(self):
        # Guard 1: the production default is exactly False, with no
        # conditional logic (env var, config lookup) around it in source --
        # a bare `= False`. See decision/trusted_provenance.py.
        self.assertIs(trusted_provenance_module.TRUSTED_EXECUTION_PROVENANCE_AVAILABLE, False)

    def test_environment_variable_cannot_flip_the_flag_or_the_outcome(self):
        # Guard 2a: no plausible env var name changes the flag's value or
        # the engine's behavior. A fully-approved row, evaluated with these
        # env vars set, must still resolve RESEARCH-MODEL.
        plausible_env_vars = {
            "PCBF_TRUSTED_PROVENANCE": "true",
            "TRUSTED_EXECUTION_PROVENANCE_AVAILABLE": "1",
            "PCBF_TRUSTED_EXECUTION_PROVENANCE_AVAILABLE": "True",
        }
        with patch.dict(os.environ, plausible_env_vars):
            # Reflects the actual attribute value each of these env vars
            # would need to influence for the flag to matter -- reload is
            # not performed because the module defines this as a bare
            # literal that never consults os.environ; this asserts that
            # remains true.
            self.assertIs(trusted_provenance_module.TRUSTED_EXECUTION_PROVENANCE_AVAILABLE, False)
            with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
                result = evaluate(PASSING_INPUT, POSITIVE_MARKET, FORECAST_CLAIMING_MODEL)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)

    def test_request_json_cannot_flip_the_flag_or_the_outcome(self):
        # Guard 2b: a self-declared "I am trusted" field anywhere in
        # decision_input or forecast_quality has zero effect -- it is
        # simply never read.
        forged_decision_input = dict(PASSING_INPUT)
        forged_decision_input["trusted_execution_provenance_available"] = True
        forged_decision_input["decision_input"] = {"trusted_execution_provenance_available": True}
        forged_forecast = dict(FORECAST_CLAIMING_MODEL)
        forged_forecast["trusted_execution_provenance_available"] = True
        forged_forecast["TRUSTED_EXECUTION_PROVENANCE_AVAILABLE"] = True
        with patch(_MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]):
            result = evaluate(forged_decision_input, POSITIVE_MARKET, forged_forecast)
        self.assertEqual(result["classification"], "RESEARCH-MODEL")
        self.assertEqual(result["cash_stake"], 0)

    def test_cli_entry_point_has_no_argument_or_env_wiring_for_the_flag(self):
        # Guard 2c: code-inspection check backing the manual grep reported
        # alongside this test -- cli.py never references this flag at all
        # (no import, no argparse flag), and the flag's own defining module
        # never reads an env var or CLI argument to decide its value.
        import inspect

        from pcbf_calculator import cli as cli_module

        cli_source = inspect.getsource(cli_module)
        provenance_source = inspect.getsource(trusted_provenance_module)

        self.assertNotIn("TRUSTED_EXECUTION_PROVENANCE_AVAILABLE", cli_source)
        self.assertNotIn("trusted_provenance", cli_source)
        # cli.py's only argparse arguments are the input/output file paths.
        self.assertIn('parser.add_argument("input"', cli_source)
        self.assertIn('parser.add_argument("output"', cli_source)
        self.assertNotIn('add_argument("--trusted', cli_source)
        self.assertNotIn("os.environ", cli_source)
        self.assertNotIn("getenv", cli_source)

        self.assertNotIn("os.environ", provenance_source)
        self.assertNotIn("getenv", provenance_source)
        self.assertNotIn("sys.argv", provenance_source)
        self.assertNotIn("argparse", provenance_source)


class PricingOutputCannotChangeClassificationTests(unittest.TestCase):
    def test_wildly_different_pricing_outputs_never_change_classification_with_admission_state_fixed(self):
        markets = [
            {"lower_bound_ev": None, "point_ev": 0.0001, "stake": 1.0},  # zero-margin-like, thin edge
            {"lower_bound_ev": None, "point_ev": 500.0, "stake": 1.0},  # huge edge
            {"lower_bound_ev": 0.3, "point_ev": 0.3, "stake": 1.0},  # arbitrage-free-looking
            {"lower_bound_ev": 0.001, "point_ev": 10.0, "stake": 1000.0},  # heavily-vigged-looking, big stake
        ]
        with patch(_TRUSTED_PROVENANCE_PATCH_TARGET, True), patch(
            _MODEL_ADMISSION_PATCH_TARGET, return_value=[_admission_row()]
        ):
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
