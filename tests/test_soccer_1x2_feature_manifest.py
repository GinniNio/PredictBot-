"""Shape validation for the structured companion to
docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md's feature list
(docs/adapters/data/soccer_1x2_feature_manifest.yaml).

This is a design-time artifact, not a runtime registry — it is not loaded
by pcbf_calculator at runtime. These tests only prove the manifest is
internally consistent with itself and with the leakage-control claims made
in the spec (section 7): every required feature must declare an
availability timestamp that is not post-match-only.
"""

import unittest
from pathlib import Path

from pcbf_calculator.registries.yamlmini import load_registry

MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "adapters" / "data" / "soccer_1x2_feature_manifest.yaml"
)

REQUIRED_FIELDS = ("id", "required", "definition", "source", "availability_relative_to_kickoff")

# Per the operator's merge gate: every MANDATORY feature must declare type,
# unit, source, availability timestamp, null policy and validation rule.
# (source and availability_relative_to_kickoff are already covered by
# REQUIRED_FIELDS above; these are the remaining four.)
MANDATORY_FEATURE_FIELDS = ("type", "unit", "null_policy", "validation_rule")

# Mirrors adapters/soccer_1x2_stub.py::REQUIRED_FEATURE_IDS — kept as a
# separate literal here (not an import) so this test also catches drift
# between the stub's declared feature list and the manifest itself.
EXPECTED_REQUIRED_IDS = {
    "home_team_elo_pre_match",
    "away_team_elo_pre_match",
    "home_team_rolling_goals_for_last_10",
    "home_team_rolling_goals_against_last_10",
    "away_team_rolling_goals_for_last_10",
    "away_team_rolling_goals_against_last_10",
    "market_snapshot_odds_1x2",
    "days_since_last_match_home",
    "days_since_last_match_away",
}


def _load_manifest():
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    return load_registry(text)["categories"]


class SoccerFeatureManifestShapeTests(unittest.TestCase):
    def test_manifest_file_exists_and_parses(self):
        rows = _load_manifest()
        self.assertGreater(len(rows), 0)

    def test_every_feature_declares_every_required_field(self):
        rows = _load_manifest()
        for row in rows:
            for field in REQUIRED_FIELDS:
                self.assertIn(field, row, f"feature row {row.get('id')} missing field '{field}'")
                self.assertIsNotNone(row[field], f"feature row {row.get('id')} has null '{field}'")

    def test_every_mandatory_feature_declares_type_unit_null_policy_and_validation_rule(self):
        # Merge gate: a required (mandatory) feature is unusable in an
        # implementation PR unless a contributor can tell what it is
        # (type, unit), what to do when it's missing (null_policy), and
        # how to sanity-check it (validation_rule) without re-deriving
        # all of that from prose.
        rows = _load_manifest()
        for row in rows:
            if row["required"] is True:
                for field in MANDATORY_FEATURE_FIELDS:
                    self.assertIn(field, row, f"mandatory feature '{row['id']}' missing field '{field}'")
                    self.assertTrue(
                        str(row[field]).strip(),
                        f"mandatory feature '{row['id']}' has an empty '{field}'",
                    )

    def test_required_field_is_boolean(self):
        rows = _load_manifest()
        for row in rows:
            self.assertIsInstance(row["required"], bool, f"'{row['id']}'.required must be true/false, not a string")

    def test_no_duplicate_feature_ids(self):
        rows = _load_manifest()
        ids = [row["id"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)), "duplicate feature id in manifest")

    def test_required_features_match_the_spec_and_stub(self):
        rows = _load_manifest()
        required_ids = {row["id"] for row in rows if row["required"] is True}
        self.assertEqual(required_ids, EXPECTED_REQUIRED_IDS)

    def test_no_required_feature_is_post_match_only(self):
        # Direct check on the leakage claim in spec section 7: a required
        # feature can never be sourced from data only available after the
        # match it is meant to help predict.
        rows = _load_manifest()
        for row in rows:
            if row["required"] is True:
                self.assertNotIn(
                    "POST_MATCH",
                    row["availability_relative_to_kickoff"].upper(),
                    f"required feature '{row['id']}' is declared post-match-only; it cannot be required",
                )

    def test_the_documented_leakage_rejection_is_present_and_marked_optional(self):
        # post_match_actual_goals_scored is kept as a documented rejection
        # (spec section 2), not a usable feature — it must be present,
        # explicitly required: false, and explicitly flagged post-match.
        rows = {row["id"]: row for row in _load_manifest()}
        self.assertIn("post_match_actual_goals_scored", rows)
        rejected = rows["post_match_actual_goals_scored"]
        self.assertFalse(rejected["required"])
        self.assertIn("POST_MATCH", rejected["availability_relative_to_kickoff"].upper())


if __name__ == "__main__":
    unittest.main()
