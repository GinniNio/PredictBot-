"""Regression tests for the checked-in team-alias entries added for the
Soccer 1X2 Elo v1 adapter
(``src/pcbf_calculator/adapters/soccer_1x2_elo_v1/data/team_aliases.json``).

Each entry here was extracted from a real Bet9ja capture running against
the real, committed model artifact: the Bet9ja display name failed team
resolution (``FORECAST_TEAM_UNRESOLVED``) purely on spelling, verified by
hand to be the same real club as a team already present in the live-ratings
snapshot, and added as an explicit alias -- never a fuzzy match. These
tests exercise the real committed alias file and real committed artifact
(not the adapter's synthetic unit-test fixtures), proving each alias
resolves and that the previously-abstaining fixture now produces a real
forecast.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pcbf_calculator.adapters.soccer_1x2_elo_v1 import SoccerOneXTwoEloV1Adapter
from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook

PACKAGE_DATA_DIR = REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data"

# (league_code, Bet9ja display name, canonical football-data.co.uk name)
REAL_ALIAS_CASES = [
    ("E0", "Manchester Utd", "Man United"),
    ("E0", "Manchester City", "Man City"),
    ("D1", "SV 07 Elversberg", "Elversberg"),
    ("D1", "Eintracht Frankfurt", "Ein Frankfurt"),
    ("D1", "Cologne", "FC Koln"),
    ("D1", "Monchengladbach", "M'gladbach"),
    ("D1", "Borussia Dortmund", "Dortmund"),
    ("F1", "PSG", "Paris SG"),
    ("F1", "Paris FC 98", "Paris FC"),
    ("F1", "Stade Rennes", "Rennes"),
]

# Real fixtures (from the same Bet9ja capture) that previously abstained
# with FORECAST_TEAM_UNRESOLVED before these aliases were added.
REAL_FIXTURES_NOW_RESOLVABLE = [
    {"home": "Manchester Utd", "away": "Manchester City", "competition": "Premier League"},
    {"home": "SV 07 Elversberg", "away": "Bayern Munich", "competition": "Bundesliga"},
    {"home": "Brest", "away": "PSG", "competition": "Ligue 1"},
    {"home": "Eintracht Frankfurt", "away": "Freiburg", "competition": "Bundesliga"},
    {"home": "Hamburg", "away": "Cologne", "competition": "Bundesliga"},
    {"home": "Monchengladbach", "away": "Mainz", "competition": "Bundesliga"},
    {"home": "Paris FC 98", "away": "Strasbourg", "competition": "Ligue 1"},
    {"home": "Stuttgart", "away": "Borussia Dortmund", "competition": "Bundesliga"},
    {"home": "Lyon", "away": "Stade Rennes", "competition": "Ligue 1"},
]


class RealAliasFileTests(unittest.TestCase):
    def test_alias_file_still_declares_its_schema_and_note(self):
        raw = json.loads((PACKAGE_DATA_DIR / "team_aliases.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "soccer-1x2-elo-v1-team-aliases.v1")
        self.assertIn("never guessed", raw["note"])

    def test_every_real_alias_resolves_via_the_checked_in_book(self):
        raw = json.loads((PACKAGE_DATA_DIR / "team_aliases.json").read_text(encoding="utf-8"))
        alias_book = TeamAliasBook(raw)
        for league_code, display_name, canonical_name in REAL_ALIAS_CASES:
            with self.subTest(league=league_code, display_name=display_name):
                self.assertEqual(alias_book.lookup(league_code, display_name), canonical_name)

    def test_every_real_alias_canonical_name_is_in_the_live_ratings_snapshot(self):
        manifest = json.loads((PACKAGE_DATA_DIR / "model_artifact_manifest.json").read_text(encoding="utf-8"))
        ratings_keys = set(manifest["live_snapshot"]["elo_ratings"].keys())
        for league_code, _display_name, canonical_name in REAL_ALIAS_CASES:
            with self.subTest(league=league_code, canonical_name=canonical_name):
                self.assertIn(f"{league_code}|{canonical_name}", ratings_keys)


class RealAdapterEndToEndAliasTests(unittest.TestCase):
    """Loads the real, production adapter (real committed data/ dir, not
    the synthetic per-test fixtures used elsewhere in this test suite) and
    proves the previously-abstaining fixtures now produce real forecasts."""

    def setUp(self) -> None:
        self.adapter = SoccerOneXTwoEloV1Adapter()

    def test_previously_unresolved_fixtures_now_produce_forecasts(self):
        for fixture in REAL_FIXTURES_NOW_RESOLVABLE:
            with self.subTest(home=fixture["home"], away=fixture["away"]):
                result = self.adapter.forecast(
                    {
                        **fixture,
                        # Within the artifact's freshness window (its own
                        # last_processed_match_date_utc plus
                        # maximum_artifact_age_days) -- not a real future
                        # fixture, just a date after this test's real,
                        # committed artifact's last processed match and
                        # before its staleness cutoff.
                        "kickoff_utc": "2026-09-20T14:00:00Z",
                        "forecast_cutoff_utc": "2026-09-20T13:00:00Z",
                    }
                )
                self.assertTrue(
                    result.forecast_available,
                    f"expected a forecast for {fixture['home']} vs {fixture['away']}, "
                    f"got no_forecast_reason={result.no_forecast_reason!r}",
                )
                total = sum(result.probabilities.values())
                self.assertAlmostEqual(total, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
