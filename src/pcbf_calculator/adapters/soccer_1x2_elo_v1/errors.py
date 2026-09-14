"""Typed no-forecast/abstention reason codes for the Soccer 1X2 Elo v1
adapter. Matches this platform's existing convention (see
``src/pcbf_calculator/errors.py``): every declined forecast carries one of
these codes as ``ForecastResult.no_forecast_reason``, never a bare
``forecast_available: false`` with no explanation.
"""

from __future__ import annotations

FORECAST_INPUT_INCOMPLETE = "FORECAST_INPUT_INCOMPLETE"
"""The fixture object is missing one of the required fields (home, away,
competition, kickoff_utc, forecast_cutoff_utc)."""

FORECAST_COMPETITION_UNRESOLVED = "FORECAST_COMPETITION_UNRESOLVED"
"""The fixture's competition name is not one of the five leagues this
artifact's training data actually covers (Premier League, Bundesliga, La
Liga, Serie A, Ligue 1), or does not match after normalization/alias
resolution."""

FORECAST_TEAM_UNRESOLVED = "FORECAST_TEAM_UNRESOLVED"
"""The fixture's home or away team name does not resolve (exact match
after normalization, or a checked-in alias) to any team name the
artifact's training data contains for that competition."""

FORECAST_TEAM_NOT_IN_ARTIFACT = "FORECAST_TEAM_NOT_IN_ARTIFACT"
"""The team name resolved via identity matching, but no Elo rating exists
for it in the live-ratings snapshot (should not happen given ratings are
built from the same team-name set identity resolution matches against;
kept as a defensive, distinctly-coded check rather than silently treating
it as unresolved)."""

FORECAST_ARTIFACT_AFTER_CUTOFF = "FORECAST_ARTIFACT_AFTER_CUTOFF"
"""The fixture's own kickoff_utc is at or before the artifact's
last-processed-match date -- this fixture is not a genuine future match
relative to what this artifact's ratings already know about, so scoring
it would not be a real forecast."""

FORECAST_ARTIFACT_STALE = "FORECAST_ARTIFACT_STALE"
"""The gap between the artifact's last-processed-match date and the
fixture's forecast_cutoff_utc exceeds the adapter's configured maximum
artifact age. Team strength this old is not trusted to still be accurate
-- real matches this pipeline does not yet know about may have been
played since."""

FORECAST_FIXTURE_ALREADY_STARTED = "FORECAST_FIXTURE_ALREADY_STARTED"
"""The fixture's kickoff_utc is at or before its own forecast_cutoff_utc
-- this is not a future match to forecast."""

FORECAST_PROBABILITIES_INVALID = "FORECAST_PROBABILITIES_INVALID"
"""The model produced a non-finite, negative, or (after softmax) non-
unit-sum probability triple. Defensive check against a bug in this
adapter or the underlying model artifact, not an expected caller-input
failure."""

FORECAST_MODEL_EXECUTION_FAILED = "FORECAST_MODEL_EXECUTION_FAILED"
"""The model raised an unexpected exception while scoring this fixture.
Caught and recorded as a typed no-forecast reason rather than crashing
the whole request."""

ALL_CODES = frozenset(
    {
        FORECAST_INPUT_INCOMPLETE,
        FORECAST_COMPETITION_UNRESOLVED,
        FORECAST_TEAM_UNRESOLVED,
        FORECAST_TEAM_NOT_IN_ARTIFACT,
        FORECAST_ARTIFACT_AFTER_CUTOFF,
        FORECAST_ARTIFACT_STALE,
        FORECAST_FIXTURE_ALREADY_STARTED,
        FORECAST_PROBABILITIES_INVALID,
        FORECAST_MODEL_EXECUTION_FAILED,
    }
)
