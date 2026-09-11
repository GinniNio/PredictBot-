"""Pre-match feature construction for the Soccer 1X2 Elo baseline.

Exactly six NAMED features per fixture (materializing into a 9-column
design matrix, since `league_id` one-hot-encodes into 4 dummy columns):

1. `home_elo_pre`     — home team's Elo rating immediately before this
                         match (see `elo.py`).
2. `away_elo_pre`     — away team's Elo rating immediately before this
                         match.
3. `elo_diff`         — `home_elo_pre - away_elo_pre`.
4. `home_advantage`   — a constant `1.0` on every row. IMPORTANT: this is
                         a DIFFERENT concept from `elo.HOME_ADVANTAGE_ELO_BONUS`.
                         `HOME_ADVANTAGE_ELO_BONUS` is a fixed constant
                         baked into Elo's own expected-score formula (used
                         only to update Elo ratings). `home_advantage`
                         here is a constant-1.0 model INPUT column, one
                         per home-team-perspective row; its fitted
                         logistic-regression coefficient (see `model.py`)
                         is the MODEL's own separately-learned home-field-
                         advantage effect. The two must never be confused:
                         one is an Elo-update constant, the other is a
                         model feature whose coefficient the training
                         process discovers.
5. `league_id`        — one-hot encoded from `league_code`, with `E0`
                         (the reference category) DROPPED, giving four
                         dummy columns: `league_D1`, `league_SP1`,
                         `league_I1`, `league_F1`. Every row for `E0`
                         itself has all four dummies at `0.0` — E0's
                         effect is absorbed into the fitted intercept, so
                         the design matrix stays full-rank without needing
                         to drop the model's own intercept.
6. `season_stage`     — `min(home_matches_played_this_season_so_far,
                         away_matches_played_this_season_so_far)`, scoped
                         to `(league_code, season_code)`, counting only
                         REAL prior matches processed so far THIS SEASON
                         for that team (reset to 0 at the start of each new
                         season, even though a team's Elo rating itself
                         carries over across season boundaries). A team's
                         very first match of a season legitimately has
                         `season_stage=0` for that side — a real observed
                         value, never a missing one.

Fail-closed exclusion (never zero-fill, never guess): a row whose `result`
is not exactly one of `H`/`D`/`A`, or whose `date_raw` cannot be parsed via
`data_pipeline.validation.parse_date` (through `elo.sort_matches_chronologically`),
is EXCLUDED entirely from feature construction, with a typed reason
(`"UNPARSEABLE_DATE"` or `"INVALID_RESULT"`) recorded in the returned
`exclusions` list. This should essentially never fire against real
dataset_builder output (already filtered to be internally consistent per
split) but must exist and is exercised in
`tests/test_soccer_1x2_elo_baseline.py` against a deliberately malformed
synthetic record.

No leakage from `price_observations`: this module never reads a record's
`price_observations` at all — opening/closing prices are comparison-only
in this baseline (see `evaluate.py`), never a model feature. A record with
ONLY closing observations (or no price_observations at all) produces an
identical feature vector to one with a full opening+closing set, since
Elo/league/season-stage are the ONLY inputs read here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .elo import EloEngine, sort_matches_chronologically

REFERENCE_LEAGUE = "E0"
DUMMY_LEAGUE_CODES: tuple[str, ...] = ("D1", "SP1", "I1", "F1")
VALID_RESULTS = ("H", "D", "A")

EXCLUSION_REASON_UNPARSEABLE_DATE = "UNPARSEABLE_DATE"
EXCLUSION_REASON_INVALID_RESULT = "INVALID_RESULT"

# Fixed, documented design-matrix column order — `model.py` and
# `calibration.py` never hardcode this list a second time, they import it
# from here.
FEATURE_NAMES: tuple[str, ...] = (
    "home_elo_pre",
    "away_elo_pre",
    "elo_diff",
    "home_advantage",
    *[f"league_{code}" for code in DUMMY_LEAGUE_CODES],
    "season_stage",
)


class SeasonStageTracker:
    """Counts REAL prior matches processed so far this season for each
    `(league_code, season_code, team_name)` — independent of `EloEngine`'s
    own `matches_played` counter, which never resets across season
    boundaries (a team's Elo history carries over; its season-stage count
    does not)."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, str], int] = {}

    def get_pre_match_count(self, league_code: str, season_code: str, team_name: str) -> int:
        return self._counts.get((league_code, season_code, team_name), 0)

    def record_played(self, league_code: str, season_code: str, team_name: str) -> None:
        key = (league_code, season_code, team_name)
        self._counts[key] = self._counts.get(key, 0) + 1


def league_one_hot(league_code: str) -> list[float]:
    """Four dummy columns in `DUMMY_LEAGUE_CODES` order. `E0` (or any
    league_code not in `DUMMY_LEAGUE_CODES`) yields all zeros — the
    reference category."""

    return [1.0 if league_code == code else 0.0 for code in DUMMY_LEAGUE_CODES]


def build_feature_vector(home_elo_pre: float, away_elo_pre: float, league_code: str, season_stage: int) -> list[float]:
    """Assemble one row's 9-column feature vector in `FEATURE_NAMES` order."""

    return [
        home_elo_pre,
        away_elo_pre,
        home_elo_pre - away_elo_pre,
        1.0,
        *league_one_hot(league_code),
        float(season_stage),
    ]


@dataclass
class FeatureBuildResult:
    """`features[i]` / `labels[i]` / `record_refs[i]` are aligned by index
    and in strict chronological processing order (which may differ from
    the caller's input order — see `elo.sort_matches_chronologically`).
    `exclusions` carries every row this module refused to build a feature
    vector for, each a dict with the original record's identifying fields
    plus a typed `"reason"`."""

    features: list[list[float]] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    record_refs: list[dict[str, Any]] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)


def _exclusion_entry(record: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "split_id": record.get("split_id"),
        "league_code": record.get("league_code"),
        "season_code": record.get("season_code"),
        "home_team": record.get("home_team"),
        "away_team": record.get("away_team"),
        "date_raw": record.get("date_raw"),
        "reason": reason,
    }


def build_dataset(
    records: list[dict[str, Any]],
    elo_engine: EloEngine | None = None,
    season_tracker: SeasonStageTracker | None = None,
) -> FeatureBuildResult:
    """Build pre-match feature vectors + labels for `records` (each a
    dataset_builder-shaped record dict: `league_code`, `season_code`,
    `date_raw`, `home_team`, `away_team`, `result`, ...), processed in
    STRICT chronological order.

    `elo_engine`/`season_tracker` default to fresh instances; pass shared
    ones (e.g. from `train.py`) to build features across a concatenation
    of several splits with ONE continuous Elo/season-stage history —
    processing order is what actually matters for the no-lookahead
    invariant, not which object instance is used.

    Any record whose `result` is not exactly one of `H`/`D`/`A`, or whose
    `date_raw` cannot be parsed, is excluded entirely (never zero-filled)
    and recorded in the result's `exclusions` list with a typed reason.
    `price_observations` is never read by this function, regardless of
    what stage(s) of price it does or does not carry."""

    elo_engine = elo_engine if elo_engine is not None else EloEngine()
    season_tracker = season_tracker if season_tracker is not None else SeasonStageTracker()

    sorted_records, unparseable_date_records = sort_matches_chronologically(records)

    result = FeatureBuildResult()
    for record in unparseable_date_records:
        result.exclusions.append(_exclusion_entry(record, EXCLUSION_REASON_UNPARSEABLE_DATE))

    for record in sorted_records:
        raw_result = record.get("result")
        if raw_result not in VALID_RESULTS:
            result.exclusions.append(_exclusion_entry(record, EXCLUSION_REASON_INVALID_RESULT))
            continue

        league_code = record["league_code"]
        season_code = record["season_code"]
        home_team = record["home_team"]
        away_team = record["away_team"]

        home_state = elo_engine.get_pre_match_state(league_code, home_team)
        away_state = elo_engine.get_pre_match_state(league_code, away_team)
        home_stage = season_tracker.get_pre_match_count(league_code, season_code, home_team)
        away_stage = season_tracker.get_pre_match_count(league_code, season_code, away_team)
        season_stage = min(home_stage, away_stage)

        feature_vector = build_feature_vector(home_state.rating, away_state.rating, league_code, season_stage)

        result.features.append(feature_vector)
        result.labels.append(raw_result)
        result.record_refs.append(
            {
                "split_id": record.get("split_id"),
                "league_code": league_code,
                "season_code": season_code,
                "date_raw": record.get("date_raw"),
                "home_team": home_team,
                "away_team": away_team,
                "result": raw_result,
                "home_elo_pre": home_state.rating,
                "away_elo_pre": away_state.rating,
                "home_is_provisional": home_state.is_provisional,
                "away_is_provisional": away_state.is_provisional,
                "season_stage": season_stage,
                "price_observations": record.get("price_observations", []),
            }
        )

        elo_engine.apply_match_result(league_code, home_team, away_team, raw_result)
        season_tracker.record_played(league_code, season_code, home_team)
        season_tracker.record_played(league_code, season_code, away_team)

    return result
