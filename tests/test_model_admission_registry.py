"""Shape/consistency tests for the model-admission registry
(``src/pcbf_calculator/registries/data/model-admission-registry.yaml``),
loaded via ``pcbf_calculator.registries.model_admission``.

This registry IS consulted by runtime code (``decision/engine.py``), unlike
the design-time-only soccer 1X2 promotion-thresholds ledger — these tests
prove its shape is valid and that, in this release, it ships with zero
admitted rows (fail-closed by default; see
``tests/test_platform_classification_invariant.py`` for the
decision-engine-side proof that an empty/absent/mismatched lookup always
resolves to "not approved").
"""

import unittest
from unittest.mock import patch

from pcbf_calculator.registries.model_admission import (
    ALLOWED_GATE_STATUSES,
    REQUIRED_FIELDS,
    load_model_admission_rows,
    lookup_model_admission,
    resolve_model_admission,
    validate_model_admission_registry,
)

_LOAD_ROWS_PATCH_TARGET = "pcbf_calculator.registries.model_admission.load_model_admission_rows"

_BASE_ROW = {
    "id": "soccer_1x2__v1",
    "adapter_id": "soccer_1x2",
    "model_version": "v1",
    "model_artifact_hash": "sha256:" + "a" * 64,
    "backtest_gates_status": "APPROVED",
    "prospective_status": "APPROVED",
    "cash_admission_status": "APPROVED",
    "rationale": "test fixture",
}


def _row(**overrides):
    row = dict(_BASE_ROW)
    row.update(overrides)
    return row


class ModelAdmissionRegistryShapeTests(unittest.TestCase):
    def test_registry_file_parses(self):
        # An empty categories list is a valid, expected shape for this file
        # today — must not raise.
        rows = load_model_admission_rows()
        self.assertIsInstance(rows, list)

    def test_registry_ships_with_zero_admitted_rows_in_this_release(self):
        rows = load_model_admission_rows()
        self.assertEqual(rows, [], "no adapter has cleared admission in this release")

    def test_validate_reports_no_problems(self):
        self.assertEqual(validate_model_admission_registry(), [])

    def test_every_required_field_and_allowed_status_documented(self):
        # Static shape checks on the module's own contract, independent of
        # the (currently empty) file content.
        self.assertIn("adapter_id", REQUIRED_FIELDS)
        self.assertIn("model_artifact_hash", REQUIRED_FIELDS)
        self.assertEqual(ALLOWED_GATE_STATUSES, {"PENDING", "APPROVED", "REJECTED"})

    def test_lookup_on_empty_registry_returns_none(self):
        self.assertIsNone(lookup_model_admission("soccer", "soccer_1x2_v1.0.0_2027-06-30"))

    def test_lookup_with_missing_ids_returns_none(self):
        self.assertIsNone(lookup_model_admission(None, None))
        self.assertIsNone(lookup_model_admission("soccer", None))
        self.assertIsNone(lookup_model_admission(None, "soccer_1x2_v1.0.0_2027-06-30"))

    def test_resolve_on_empty_registry_is_fully_closed(self):
        resolved = resolve_model_admission("soccer", "soccer_1x2_v1.0.0_2027-06-30", "sha256:" + "a" * 64)
        self.assertEqual(
            resolved,
            {
                "backtest_gates_approved": False,
                "prospective_approved": False,
                "cash_admission_approved": False,
            },
        )


class GateOrderingValidationTests(unittest.TestCase):
    """New cross-field validation rules: prospective/cash approval cannot
    skip an earlier gate on the same row."""

    def test_valid_fully_approved_row_reports_no_problems(self):
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[_row()]):
            self.assertEqual(validate_model_admission_registry(), [])

    def test_prospective_approved_without_backtest_approved_is_a_problem(self):
        malformed = _row(backtest_gates_status="PENDING", prospective_status="APPROVED", cash_admission_status="PENDING")
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[malformed]):
            problems = validate_model_admission_registry()
        self.assertTrue(any("prospective_status APPROVED" in p for p in problems))

    def test_cash_approved_without_backtest_approved_is_a_problem(self):
        malformed = _row(backtest_gates_status="PENDING", prospective_status="PENDING", cash_admission_status="APPROVED")
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[malformed]):
            problems = validate_model_admission_registry()
        self.assertTrue(any("cash_admission_status APPROVED" in p for p in problems))

    def test_cash_approved_without_prospective_approved_is_a_problem(self):
        malformed = _row(backtest_gates_status="APPROVED", prospective_status="PENDING", cash_admission_status="APPROVED")
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[malformed]):
            problems = validate_model_admission_registry()
        self.assertTrue(any("cash_admission_status APPROVED" in p for p in problems))


class ResolveDefendsAgainstMalformedRowOrderingTests(unittest.TestCase):
    """Defense in depth: even if a malformed row somehow exists in the
    file, ``resolve_model_admission`` must never report a later flag True
    when an earlier one is not APPROVED -- independent of whether
    ``validate_model_admission_registry`` already flagged it."""

    def test_malformed_row_is_flagged_by_validation_and_resolves_closed(self):
        malformed = _row(backtest_gates_status="PENDING", prospective_status="APPROVED")
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[malformed]):
            problems = validate_model_admission_registry()
            self.assertTrue(len(problems) >= 1)

            resolved = resolve_model_admission(
                malformed["adapter_id"], malformed["model_version"], malformed["model_artifact_hash"]
            )
        self.assertEqual(
            resolved,
            {
                "backtest_gates_approved": False,
                "prospective_approved": False,
                "cash_admission_approved": False,
            },
        )

    def test_malformed_cash_approved_row_resolves_closed_for_cash_flag(self):
        malformed = _row(backtest_gates_status="APPROVED", prospective_status="PENDING", cash_admission_status="APPROVED")
        with patch(_LOAD_ROWS_PATCH_TARGET, return_value=[malformed]):
            resolved = resolve_model_admission(
                malformed["adapter_id"], malformed["model_version"], malformed["model_artifact_hash"]
            )
        self.assertTrue(resolved["backtest_gates_approved"])
        self.assertFalse(resolved["prospective_approved"])
        self.assertFalse(resolved["cash_admission_approved"])


if __name__ == "__main__":
    unittest.main()
