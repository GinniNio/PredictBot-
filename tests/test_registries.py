import unittest

from pcbf_calculator.registries.loader import (
    ALLOWED_ADAPTER_STATUSES,
    ALLOWED_RUNTIME_STATUSES,
    REQUIRED_CATEGORY_IDS,
    RegistryValidationError,
    load_all_registries,
    validate_registries,
)

REQUIRED_BET9JA_CATEGORIES = {
    "Soccer": "soccer",
    "Players Soccer": "players_soccer",
    "Specials Soccer": "specials_soccer",
    "Specials Combo": "specials_combo",
    "Antepost Soccer": "antepost_soccer",
    "Zoom Soccer": "zoom_soccer",
    "Players Zoom Soccer": "players_zoom_soccer",
    "Tennis": "tennis",
    "Zoom Tennis": "zoom_tennis",
    "Basketball": "basketball",
    "Volleyball": "volleyball",
    "American Football": "american_football",
    "Players American Football": "players_american_football",
    "Baseball": "baseball",
    "Handball": "handball",
    "Rugby": "rugby",
    "Motor Sports": "motor_sports",
    "Ice Hockey": "ice_hockey",
    "Cycling": "cycling",
    "Alpine": "alpine",
    "Biathlon": "biathlon",
    "Cross-Country": "cross_country",
    "Badminton": "badminton",
    "Boxing": "boxing",
    "Cricket": "cricket",
    "Darts": "darts",
    "Floorball": "floorball",
    "Futsal": "futsal",
    "MMA": "mma",
    "Snooker": "snooker",
    "Table Tennis": "table_tennis",
    "Outrights": "outrights",
}


class RegistryConsistencyTests(unittest.TestCase):
    def test_all_32_categories_are_present(self):
        self.assertEqual(len(REQUIRED_CATEGORY_IDS), 32)
        self.assertEqual(set(REQUIRED_BET9JA_CATEGORIES.values()), set(REQUIRED_CATEGORY_IDS))

    def test_registries_are_internally_consistent(self):
        # Raises RegistryValidationError (with the full problem list) on any
        # inconsistency; a clean pass is the assertion itself.
        validate_registries()

    def test_every_display_name_resolves_to_a_sports_registry_row(self):
        registries = load_all_registries()
        sports = registries["sports"]
        for display_name, category_id in REQUIRED_BET9JA_CATEGORIES.items():
            self.assertIn(category_id, sports, f"missing sports-registry row for {display_name}")
            self.assertEqual(sports[category_id]["display_name"], display_name)

    def test_every_status_value_is_one_of_the_five_allowed(self):
        registries = load_all_registries()
        five = {
            "PRICING_SUPPORTED",
            "FORECAST_ADAPTER_AVAILABLE",
            "RESEARCH_ONLY",
            "UNSUPPORTED_INPUT",
            "NOT_IMPLEMENTED",
        }
        self.assertEqual(ALLOWED_RUNTIME_STATUSES, five)
        self.assertEqual(ALLOWED_ADAPTER_STATUSES, five)
        for row in registries["data_sources"].values():
            self.assertIn(row["runtime_status"], five)
        for row in registries["adapters"].values():
            self.assertIn(row["adapter_status"], five)

    def test_release_a_default_statuses(self):
        registries = load_all_registries()
        for category_id, row in registries["adapters"].items():
            self.assertEqual(
                row["adapter_status"], "NOT_IMPLEMENTED", f"{category_id} should be NOT_IMPLEMENTED in Release A"
            )
        for category_id, row in registries["data_sources"].items():
            if category_id == "specials_combo":
                self.assertEqual(row["runtime_status"], "RESEARCH_ONLY")
            else:
                self.assertEqual(row["runtime_status"], "PRICING_SUPPORTED")

    def test_underlying_sport_cross_reference_resolves(self):
        registries = load_all_registries()
        sports = registries["sports"]
        for category_id, row in sports.items():
            underlying = row.get("underlying")
            if underlying is not None:
                self.assertIn(underlying, sports)
                self.assertEqual(sports[underlying]["kind"], "sport")

    def test_broken_registry_raises_validation_error_with_problem_list(self):
        registries = load_all_registries()
        self.assertNotIn("nonexistent_category", registries["sports"])
        broken = {name: dict(rows) for name, rows in registries.items()}
        # Inject an unresolved cross-reference in adapter-registry.
        broken["adapters"] = dict(broken["adapters"])
        broken["adapters"]["nonexistent_category"] = {
            "id": "nonexistent_category",
            "adapter_status": "NOT_IMPLEMENTED",
            "adapter_unsupported_reason": "NO_FORECASTING_ADAPTER_BUILT_YET",
            "classification_ceiling": "PAPER",
            "feature_requirements": [],
            "uncertainty_method": None,
        }
        with self.assertRaises(RegistryValidationError) as ctx:
            validate_registries(broken)
        self.assertTrue(
            any("nonexistent_category" in problem for problem in ctx.exception.problems),
            ctx.exception.problems,
        )

    def test_valid_registries_do_not_raise(self):
        try:
            validate_registries()
        except RegistryValidationError as exc:
            self.fail(f"registries unexpectedly invalid: {exc.problems}")


if __name__ == "__main__":
    unittest.main()
