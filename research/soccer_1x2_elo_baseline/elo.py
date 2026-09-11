"""Elo rating engine for the Soccer 1X2 Elo baseline.

Ratings are keyed by `(league_code, team_name)` — never shared across
leagues, so an English team's rating is never compared on the same
absolute scale as a French team's. Any real cross-league strength
difference is instead absorbed by `league_id`, a separate model feature
built in `features.py`.

Named constants (never magic numbers):

- `INITIAL_RATING` (1500.0) — assigned to any `(league_code, team_name)`
  never seen before.
- `K_FACTOR` (20.0) — the Elo update's learning-rate constant.
- `HOME_ADVANTAGE_ELO_BONUS` (60.0) — rating points added to the home
  side's EFFECTIVE rating for the expected-score calculation only. This is
  never persisted as part of either team's stored rating — it exists only
  inside `expected_home_score`'s formula.
- `PROVISIONAL_MATCH_THRESHOLD` (10) — a team is `is_provisional=True`
  while it has fewer than this many prior REAL, chronologically-preceding
  rated matches in that league. A promoted/newly-appearing team is never
  silently treated as "just another 1500 team" without this flag visible.

Leakage boundary (the core invariant of this module): a match's own
pre-match feature vector must read Elo state as it existed immediately
BEFORE that match was applied — never after. `EloEngine.process_ordered_matches`
enforces this by construction: for each match, in order, it first reads
and records the pre-match state of both teams, and only afterwards calls
`apply_match_result` to update its internal state. See
`tests/test_soccer_1x2_elo_baseline.py` for a dedicated test proving this
with two matches for the same team.

Determinism: given the same ordered match sequence, this engine always
produces the same rating trajectory. No `random`, no unordered dict/set
iteration affecting output order or arithmetic order (Python dicts
preserve insertion order, but this module never iterates a dict/set in a
way whose ORDER affects any arithmetic result — only single-key lookups).

Chronological ordering: `sort_matches_chronologically` reuses
`data_pipeline.validation.parse_date` for the exact same date-format
handling already proven correct elsewhere in this repo — this module never
reimplements date parsing. Matches whose `date_raw` fails to parse are
excluded from the returned ordered sequence (paired with the original
record and a typed reason) rather than being guessed at; see
`features.py` for how that exclusion is surfaced to a caller building a
full feature/label dataset.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.validation import parse_date

INITIAL_RATING = 1500.0
K_FACTOR = 20.0
HOME_ADVANTAGE_ELO_BONUS = 60.0
PROVISIONAL_MATCH_THRESHOLD = 10

VALID_RESULTS = ("H", "D", "A")


@dataclass(frozen=True)
class EloState:
    """One team's Elo state immediately BEFORE a given match."""

    rating: float
    is_provisional: bool
    matches_played: int


def expected_home_score(home_rating: float, away_rating: float, home_advantage_elo_bonus: float = HOME_ADVANTAGE_ELO_BONUS) -> float:
    """Standard Elo expected-score formula, home side, with the home-
    advantage bonus added to the home side's EFFECTIVE rating for this
    calculation only (never persisted)."""

    effective_home = home_rating + home_advantage_elo_bonus
    return 1.0 / (1.0 + 10.0 ** ((away_rating - effective_home) / 400.0))


def actual_score_for_home(result: str) -> float:
    """1.0 for a home win, 0.5 for a draw, 0.0 for a home loss. `result`
    must be exactly one of `H`/`D`/`A` — callers are expected to have
    already validated this (see `features.py`'s fail-closed exclusion of
    any row whose `result` is not one of the three valid outcomes)."""

    if result == "H":
        return 1.0
    if result == "D":
        return 0.5
    if result == "A":
        return 0.0
    raise ValueError(f"result must be one of {VALID_RESULTS!r}, got {result!r}")


class EloEngine:
    """Stateful, deterministic Elo rating tracker keyed by
    `(league_code, team_name)`."""

    def __init__(
        self,
        initial_rating: float = INITIAL_RATING,
        k_factor: float = K_FACTOR,
        home_advantage_elo_bonus: float = HOME_ADVANTAGE_ELO_BONUS,
        provisional_match_threshold: int = PROVISIONAL_MATCH_THRESHOLD,
    ) -> None:
        self.initial_rating = initial_rating
        self.k_factor = k_factor
        self.home_advantage_elo_bonus = home_advantage_elo_bonus
        self.provisional_match_threshold = provisional_match_threshold
        self._ratings: dict[tuple[str, str], float] = {}
        self._matches_played: dict[tuple[str, str], int] = {}

    def _key(self, league_code: str, team_name: str) -> tuple[str, str]:
        return (league_code, team_name)

    def get_pre_match_state(self, league_code: str, team_name: str) -> EloState:
        """The team's Elo state as it stands right now (i.e. BEFORE any
        match not yet applied via `apply_match_result`)."""

        key = self._key(league_code, team_name)
        rating = self._ratings.get(key, self.initial_rating)
        matches_played = self._matches_played.get(key, 0)
        is_provisional = matches_played < self.provisional_match_threshold
        return EloState(rating=rating, is_provisional=is_provisional, matches_played=matches_played)

    def apply_match_result(self, league_code: str, home_team: str, away_team: str, result: str) -> None:
        """Update both teams' ratings for one match's REAL result. Must be
        called strictly AFTER that match's pre-match state has already
        been read by the caller (see `process_ordered_matches`) — calling
        this before reading pre-match state is the exact leakage bug this
        module exists to make structurally hard to write."""

        home_key = self._key(league_code, home_team)
        away_key = self._key(league_code, away_team)
        home_rating = self._ratings.get(home_key, self.initial_rating)
        away_rating = self._ratings.get(away_key, self.initial_rating)

        expected_home = expected_home_score(home_rating, away_rating, self.home_advantage_elo_bonus)
        expected_away = 1.0 - expected_home
        actual_home = actual_score_for_home(result)
        actual_away = 1.0 - actual_home

        self._ratings[home_key] = home_rating + self.k_factor * (actual_home - expected_home)
        self._ratings[away_key] = away_rating + self.k_factor * (actual_away - expected_away)
        self._matches_played[home_key] = self._matches_played.get(home_key, 0) + 1
        self._matches_played[away_key] = self._matches_played.get(away_key, 0) + 1

    def process_ordered_matches(self, ordered_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process an already-chronologically-ordered sequence of match
        records (each a dict with `league_code`, `home_team`, `away_team`,
        `result`). For EACH match, in order: first read both teams'
        pre-match Elo state, THEN apply that match's result. Returns one
        dict per input record (same order), carrying:
        `home_elo_pre`, `away_elo_pre`, `home_is_provisional`,
        `away_is_provisional`, `home_matches_played_pre`,
        `away_matches_played_pre` — all read strictly BEFORE this match's
        own result was applied to internal state.

        This function never reorders `ordered_records` itself — ordering
        chronologically is `sort_matches_chronologically`'s job (or the
        caller's), kept separate so this engine has no opinion on date
        parsing at all."""

        results: list[dict[str, Any]] = []
        for record in ordered_records:
            league_code = record["league_code"]
            home_team = record["home_team"]
            away_team = record["away_team"]
            result = record["result"]

            home_state = self.get_pre_match_state(league_code, home_team)
            away_state = self.get_pre_match_state(league_code, away_team)

            results.append(
                {
                    "home_elo_pre": home_state.rating,
                    "away_elo_pre": away_state.rating,
                    "home_is_provisional": home_state.is_provisional,
                    "away_is_provisional": away_state.is_provisional,
                    "home_matches_played_pre": home_state.matches_played,
                    "away_matches_played_pre": away_state.matches_played,
                }
            )

            self.apply_match_result(league_code, home_team, away_team, result)

        return results


def sort_matches_chronologically(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sort `records` (each carrying `date_raw`) ascending by real parsed
    date, using `data_pipeline.validation.parse_date` for the exact same
    date-format handling already proven correct elsewhere in this repo.

    Returns `(sorted_records, unparseable_records)`. A record whose
    `date_raw` cannot be parsed is EXCLUDED from `sorted_records` and
    returned instead in `unparseable_records` (each entry is the original
    record, unmodified) — never guessed at, never given a fallback date.

    Ties (two records with an identical parsed date) are broken by
    original input order (Python's sort is stable), so this function is
    fully deterministic given the same input list in the same input
    order — it never depends on unordered dict/set iteration."""

    parseable: list[tuple[str, int, dict[str, Any]]] = []
    unparseable: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        iso_date, _fmt = parse_date(record.get("date_raw") or "")
        if iso_date is None:
            unparseable.append(record)
            continue
        parseable.append((iso_date, index, record))

    parseable.sort(key=lambda item: (item[0], item[1]))
    return [record for _iso, _idx, record in parseable], unparseable
