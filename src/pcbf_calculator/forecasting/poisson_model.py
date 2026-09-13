"""Transparent independent-Poisson research baseline for Soccer 1X2.

One deliberately simple, fully reproducible model -- not a novel or tuned
method. For a competition with league-average home/away goal rates
``mu_home``/``mu_away``, and each team's own attack/defence rates relative
to those league averages, the home and away fixture's expected goals are::

    expected_home_goals = mu_home * home_team.home_attack_rate * away_team.away_defence_rate
    expected_away_goals = mu_away * away_team.away_attack_rate * home_team.home_defence_rate

Home and away goals are modelled as **independent** Poisson variables
(no Dixon-Coles low-score correlation adjustment -- this is the plain,
independent baseline the adapter's evidence contract asks for). The score
matrix is summed over ``0..maximum_goals_modelled`` for each team into
home-win / draw / away-win, then normalized to sum to exactly 1 (the score
matrix truncates at ``maximum_goals_modelled``, so a small amount of
probability mass beyond that is folded back in proportionally rather than
silently discarded).

No smoothing constant is ever invented to compensate for a sparse
competition or team-role history -- ``config.py``'s minimum-match
thresholds gate whether this module runs at all for a given fixture (see
``soccer_1x2.py``); this module itself has no fallback default rate.

Odds are never read anywhere in this module -- it is given only the
eligible historical match records and the two team names to forecast. See
``soccer_1x2.py`` for where offered prices are compared against this
module's output, strictly after the probabilities already exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .errors import FORECAST_HISTORY_AFTER_CUTOFF
from .history import MatchRecord


class ModelExecutionError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def eligible_matches(
    all_matches: Sequence[MatchRecord],
    competition: str,
    cutoff_utc: datetime,
    training_window_days: int,
) -> list[MatchRecord]:
    """Every finished match in ``competition``, strictly before
    ``cutoff_utc``, and within ``training_window_days`` of it. Sorted by
    ``(kickoff_utc, source_match_id)`` for fully deterministic downstream
    iteration order."""
    window_start = cutoff_utc - timedelta(days=training_window_days)
    matches = [
        match
        for match in all_matches
        if match.competition == competition
        and match.is_finished
        and window_start <= match.kickoff_utc < cutoff_utc
    ]
    matches.sort(key=lambda m: (m.kickoff_utc, m.source_match_id))
    return matches


def assert_no_leakage(matches: Sequence[MatchRecord], cutoff_utc: datetime) -> None:
    """Defensive invariant: it must be structurally impossible for a match
    at or after the cutoff to reach here. If this ever fires it is a bug
    in eligibility filtering, never a caller input problem."""
    for match in matches:
        if match.kickoff_utc >= cutoff_utc:
            raise ModelExecutionError(
                f"{FORECAST_HISTORY_AFTER_CUTOFF}: match {match.source_match_id!r} "
                f"kickoff_utc {match.kickoff_utc.isoformat()} is not before cutoff "
                f"{cutoff_utc.isoformat()}."
            )


@dataclass(frozen=True)
class LeagueRates:
    mu_home: float
    mu_away: float
    match_count: int


def _fit_league_rates(matches: Sequence[MatchRecord]) -> LeagueRates:
    total_home_goals = sum(m.home_goals for m in matches)
    total_away_goals = sum(m.away_goals for m in matches)
    n = len(matches)
    return LeagueRates(mu_home=total_home_goals / n, mu_away=total_away_goals / n, match_count=n)


@dataclass(frozen=True)
class TeamRates:
    home_attack_rate: float | None
    home_defence_rate: float | None
    away_attack_rate: float | None
    away_defence_rate: float | None
    home_match_count: int
    away_match_count: int


def _fit_team_rates(matches: Sequence[MatchRecord], team: str, league: LeagueRates) -> TeamRates:
    home_role_matches = [m for m in matches if m.home == team]
    away_role_matches = [m for m in matches if m.away == team]

    home_attack_rate = home_defence_rate = None
    if home_role_matches:
        avg_goals_scored_home = sum(m.home_goals for m in home_role_matches) / len(home_role_matches)
        avg_goals_conceded_home = sum(m.away_goals for m in home_role_matches) / len(home_role_matches)
        home_attack_rate = avg_goals_scored_home / league.mu_home if league.mu_home else 0.0
        home_defence_rate = avg_goals_conceded_home / league.mu_away if league.mu_away else 0.0

    away_attack_rate = away_defence_rate = None
    if away_role_matches:
        avg_goals_scored_away = sum(m.away_goals for m in away_role_matches) / len(away_role_matches)
        avg_goals_conceded_away = sum(m.home_goals for m in away_role_matches) / len(away_role_matches)
        away_attack_rate = avg_goals_scored_away / league.mu_away if league.mu_away else 0.0
        away_defence_rate = avg_goals_conceded_away / league.mu_home if league.mu_home else 0.0

    return TeamRates(
        home_attack_rate=home_attack_rate,
        home_defence_rate=home_defence_rate,
        away_attack_rate=away_attack_rate,
        away_defence_rate=away_defence_rate,
        home_match_count=len(home_role_matches),
        away_match_count=len(away_role_matches),
    )


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


@dataclass(frozen=True)
class PoissonForecast:
    expected_home_goals: float
    expected_away_goals: float
    home_win: float
    draw: float
    away_win: float
    probability_mass_captured: float


def score_fixture(
    matches: Sequence[MatchRecord],
    home_team: str,
    away_team: str,
    maximum_goals_modelled: int,
) -> PoissonForecast:
    """Fit league + both teams' rates from ``matches`` (already filtered to
    the fixture's own eligible evidence set -- see ``eligible_matches``)
    and score the fixture. Raises ``ModelExecutionError`` if a rate cannot
    be computed (e.g. zero league-average goals, which would make the
    ratio-based attack/defence rates undefined) -- the caller turns this
    into a typed abstention, never a crash."""
    league = _fit_league_rates(matches)
    if league.mu_home <= 0 or league.mu_away <= 0:
        raise ModelExecutionError(
            f"league average goals must be positive to compute attack/defence rates "
            f"(mu_home={league.mu_home}, mu_away={league.mu_away})."
        )

    home = _fit_team_rates(matches, home_team, league)
    away = _fit_team_rates(matches, away_team, league)
    if home.home_attack_rate is None or home.home_defence_rate is None:
        raise ModelExecutionError(f"no home-role rates available for {home_team!r}.")
    if away.away_attack_rate is None or away.away_defence_rate is None:
        raise ModelExecutionError(f"no away-role rates available for {away_team!r}.")

    expected_home_goals = league.mu_home * home.home_attack_rate * away.away_defence_rate
    expected_away_goals = league.mu_away * away.away_attack_rate * home.home_defence_rate

    home_win = draw = away_win = 0.0
    captured = 0.0
    for i in range(maximum_goals_modelled + 1):
        p_home = _poisson_pmf(i, expected_home_goals)
        for j in range(maximum_goals_modelled + 1):
            p_away = _poisson_pmf(j, expected_away_goals)
            joint = p_home * p_away
            captured += joint
            if i > j:
                home_win += joint
            elif i == j:
                draw += joint
            else:
                away_win += joint

    if captured <= 0:
        raise ModelExecutionError("captured probability mass is zero or negative -- cannot normalize.")

    home_win /= captured
    draw /= captured
    away_win /= captured

    return PoissonForecast(
        expected_home_goals=expected_home_goals,
        expected_away_goals=expected_away_goals,
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        probability_mass_captured=captured,
    )


def leave_one_match_out_sensitivity(
    matches: Sequence[MatchRecord],
    home_team: str,
    away_team: str,
    maximum_goals_modelled: int,
) -> list[PoissonForecast]:
    """Deterministic leave-one-out sensitivity: for each match in the
    fixture's own eligible evidence set, recompute the full fixture
    forecast with that one match removed. Matches are already sorted
    (``eligible_matches``), so this iterates and removes in a fixed,
    reproducible order. Only matches whose removal still leaves both teams
    with at least one role-specific match are attempted -- a removal that
    would make the model itself inapplicable is skipped, never forced."""
    results: list[PoissonForecast] = []
    for index in range(len(matches)):
        remaining = matches[:index] + matches[index + 1 :]
        try:
            results.append(score_fixture(remaining, home_team, away_team, maximum_goals_modelled))
        except ModelExecutionError:
            continue
    return results
