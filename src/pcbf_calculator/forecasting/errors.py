"""Typed abstention/failure code catalogue for the Soccer 1X2 research
forecast adapter (``poisson_model.py``, ``soccer_1x2.py``, ``evaluate.py``).

Matches this platform's existing convention (see
``src/pcbf_calculator/errors.py`` and ``ingestion/errors.py``): every
abstained market or rejected input carries one of these codes plus a
human-readable detail, never a bare drop. Fail-closed: a market this
adapter cannot honestly forecast is abstained with a typed reason, never
silently skipped and never forced through with an invented probability.
"""

from __future__ import annotations

# --- Input/schema validation (aborts the whole run, never repairs) --------

FORECAST_INPUT_SCHEMA_INVALID = "FORECAST_INPUT_SCHEMA_INVALID"
"""The research-queue input file is not the expected
``research-queue-ranked.json`` shape (missing ``markets``, or a market
missing a required field)."""

FORECAST_HISTORY_SCHEMA_INVALID = "FORECAST_HISTORY_SCHEMA_INVALID"
"""The supplied historical-match file is missing a required top-level
field, or a match record is missing one of the required fields
(source_match_id, competition, kickoff_utc, home, away, home_goals,
away_goals, status, source)."""

FORECAST_HISTORY_DUPLICATE_MATCH_ID = "FORECAST_HISTORY_DUPLICATE_MATCH_ID"
"""Two or more historical match records share the same
``source_match_id``. The whole history load is rejected rather than
picking one arbitrarily."""

FORECAST_HISTORY_INCOMPLETE_MATCH = "FORECAST_HISTORY_INCOMPLETE_MATCH"
"""A historical match record has ``status: FINISHED`` but is missing, or
has non-numeric/negative, ``home_goals``/``away_goals``. A finished match
with no recorded score is not usable evidence and is never guessed."""

# --- Per-market abstention reasons (this market only; the run continues) --

FORECAST_COMPETITION_UNRESOLVED = "FORECAST_COMPETITION_UNRESOLVED"
"""The fixture's competition name does not exactly match (after
normalization) any competition present in the supplied history, and no
checked-in alias resolves it."""

FORECAST_HOME_TEAM_UNRESOLVED = "FORECAST_HOME_TEAM_UNRESOLVED"
"""The fixture's home team name does not resolve to any team name present
in the supplied history for this competition."""

FORECAST_AWAY_TEAM_UNRESOLVED = "FORECAST_AWAY_TEAM_UNRESOLVED"
"""The fixture's away team name does not resolve to any team name present
in the supplied history for this competition."""

FORECAST_AMBIGUOUS_TEAM_ALIAS = "FORECAST_AMBIGUOUS_TEAM_ALIAS"
"""The checked-in alias mapping resolves this display name to more than
one distinct canonical history team name for this competition. Never
guessed by picking the first match."""

FORECAST_FIXTURE_TIME_UNRESOLVED = "FORECAST_FIXTURE_TIME_UNRESOLVED"
"""The fixture's own ``kickoff_utc`` (from the research queue) is missing
or unparseable, so a forecast cutoff cannot be established."""

FORECAST_FIXTURE_ALREADY_STARTED = "FORECAST_FIXTURE_ALREADY_STARTED"
"""The fixture's ``kickoff_utc`` is at or before the forecast cutoff
(``source_captured_at_utc``) -- this is not a future match to forecast."""

FORECAST_COMPETITION_SAMPLE_INSUFFICIENT = "FORECAST_COMPETITION_SAMPLE_INSUFFICIENT"
"""Fewer than ``minimum_competition_matches`` eligible (completed, within
the training window, before cutoff) matches exist for this competition.
No smoothing constant is invented to compensate."""

FORECAST_HOME_SAMPLE_INSUFFICIENT = "FORECAST_HOME_SAMPLE_INSUFFICIENT"
"""Fewer than ``minimum_team_home_matches`` eligible matches exist where
the fixture's home team played at home."""

FORECAST_AWAY_SAMPLE_INSUFFICIENT = "FORECAST_AWAY_SAMPLE_INSUFFICIENT"
"""Fewer than ``minimum_team_away_matches`` eligible matches exist where
the fixture's away team played away."""

FORECAST_PROBABILITIES_INVALID = "FORECAST_PROBABILITIES_INVALID"
"""The model produced a non-finite, negative, or (after normalization)
non-unit-sum probability triple. This is a defensive check against a bug
in the model itself, not an expected caller-input failure."""

FORECAST_MODEL_EXECUTION_FAILED = "FORECAST_MODEL_EXECUTION_FAILED"
"""The model raised an unexpected exception while fitting or scoring this
fixture. Caught and recorded as a typed abstention rather than crashing
the whole run -- one bad fixture must never take down every other market
in the same batch."""

# --- Internal defensive-only code (never a caller-visible abstention) -----

FORECAST_HISTORY_AFTER_CUTOFF = "FORECAST_HISTORY_AFTER_CUTOFF"
"""Defensive invariant only (see ``poisson_model.py::_assert_no_leakage``):
it must be structurally impossible for a historical match at or after a
fixture's own forecast cutoff to ever enter that fixture's model fit. If
this code is ever raised it is a bug in this adapter, never a caller input
problem -- eligibility filtering is supposed to make this unreachable."""

ALL_CODES = frozenset(
    {
        FORECAST_INPUT_SCHEMA_INVALID,
        FORECAST_HISTORY_SCHEMA_INVALID,
        FORECAST_HISTORY_DUPLICATE_MATCH_ID,
        FORECAST_HISTORY_INCOMPLETE_MATCH,
        FORECAST_COMPETITION_UNRESOLVED,
        FORECAST_HOME_TEAM_UNRESOLVED,
        FORECAST_AWAY_TEAM_UNRESOLVED,
        FORECAST_AMBIGUOUS_TEAM_ALIAS,
        FORECAST_FIXTURE_TIME_UNRESOLVED,
        FORECAST_FIXTURE_ALREADY_STARTED,
        FORECAST_COMPETITION_SAMPLE_INSUFFICIENT,
        FORECAST_HOME_SAMPLE_INSUFFICIENT,
        FORECAST_AWAY_SAMPLE_INSUFFICIENT,
        FORECAST_PROBABILITIES_INVALID,
        FORECAST_MODEL_EXECUTION_FAILED,
        FORECAST_HISTORY_AFTER_CUTOFF,
    }
)


class HistoryValidationError(Exception):
    """Raised when the supplied historical-match file itself is invalid.
    Aborts the whole forecast/evaluation run before any fixture is
    processed -- never silently repaired or partially loaded."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


class QueueValidationError(Exception):
    """Raised when the supplied research-queue file itself is invalid."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")
