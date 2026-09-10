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

from pcbf_calculator.registries.model_admission import (
    ALLOWED_GATE_STATUSES,
    REQUIRED_FIELDS,
    load_model_admission_rows,
    lookup_model_admission,
    resolve_model_admission,
    validate_model_admission_registry,
)


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


if __name__ == "__main__":
    unittest.main()
