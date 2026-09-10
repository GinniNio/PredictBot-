"""Contract-shape stub for the pre-match Soccer 1X2 forecasting adapter.

This is **not** a Release B implementation. It exists solely to prove that
the ``SportAdapter`` interface (``adapters/base.py``) is implementable
against the design recorded in
``docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md``, and that a fail-closed
missing/insufficient-feature policy is expressible in that shape.

``SoccerOneXTwoAdapter`` contains **zero** real forecasting logic:

- It never computes, fits, loads, or fabricates a probability.
- ``forecast()`` always returns ``ForecastResult(forecast_available=False,
  probabilities=None, ...)`` — regardless of what ``fixture`` contains —
  because no trained, backtested, gate-passing model version exists yet
  (see the spec's backtest acceptance gates, section 14).
- It is deliberately **not** registered in
  ``adapters/registry.py::_ADAPTER_IMPLEMENTATIONS``. Registering it would
  change ``get_adapter("soccer")``'s return type from ``NoForecastAdapter``
  to this class, which is exactly the kind of accidental runtime behavior
  change the ``DESIGN_IN_PROGRESS`` registry status must not cause. Real
  registration happens only in the future implementation PR, once a model
  version has actually cleared the acceptance gates in the spec.

Kept for contract-shape tests only (``tests/test_soccer_1x2_stub.py``).
"""

from __future__ import annotations

from typing import Any

from .base import AdapterInterfaceDeclaration, ForecastResult, SportAdapter

SPORT_ID = "soccer"
ADAPTER_ID = "soccer_1x2"
"""Distinct from ``SPORT_ID``: this is the admission-registry identity for
this specific adapter. Soccer will eventually have other adapters
(totals/over-under, BTTS, ...) sharing ``sport_id: soccer`` but each with
their own distinct ``adapter_id``."""

# Mirrors the "Required features" list in
# docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md section 2. Declared here only so
# the contract-shape test can assert the stub's declaration lines up with
# the spec; the stub does not read fixture data using these names for any
# real computation.
REQUIRED_FEATURE_IDS: tuple[str, ...] = (
    "home_team_elo_pre_match",
    "away_team_elo_pre_match",
    "home_team_rolling_goals_for_last_10",
    "home_team_rolling_goals_against_last_10",
    "away_team_rolling_goals_for_last_10",
    "away_team_rolling_goals_against_last_10",
    # market_snapshot_odds_1x2 (never market_closing_odds_1x2 — see spec
    # Correction 1): decision-time market snapshot, not the closing line,
    # which is not observable at a real pre-match decision horizon.
    "market_snapshot_odds_1x2",
    "days_since_last_match_home",
    "days_since_last_match_away",
)

NO_ADMITTED_MODEL_VERSION = "NO_ADMITTED_MODEL_VERSION"
"""No model version has cleared the backtest acceptance gates (spec section
14) or been prospectively PAPER-admitted (spec section 15). This is the
stub's only possible ``no_forecast_reason`` — it is never
feature-dependent, because there is no model to run features through yet."""


class SoccerOneXTwoAdapter(SportAdapter):
    """Inert contract-shape stub for the pre-match Soccer 1X2 adapter.

    See the module docstring: this proves the interface is implementable
    and that the fail-closed policy holds structurally, nothing more.
    """

    @property
    def declaration(self) -> AdapterInterfaceDeclaration:
        return AdapterInterfaceDeclaration(
            sport_id=SPORT_ID,
            adapter_id=ADAPTER_ID,
            valid_markets=("1x2",),
            settlement_units="full_time_result",
            feature_requirements=REQUIRED_FEATURE_IDS,
            data_sources=("TBD_PER_SPEC_SECTION_DATA_SOURCE_BUCKETS",),
            uncertainty_method="none",
            model_version="none",
        )

    def forecast(self, fixture: dict[str, Any]) -> ForecastResult:
        # Deliberately ignores `fixture` entirely, including any features it
        # may contain. There is no admitted model version to run them
        # through (Release A/this PR: none exists), so evaluating features
        # here would only create the appearance of a real decision path
        # where none exists. Fail closed unconditionally.
        return ForecastResult(
            sport_id=SPORT_ID,
            adapter_id=ADAPTER_ID,
            forecast_available=False,
            probabilities=None,
            model_version=None,
            uncertainty_method=None,
            no_forecast_reason=NO_ADMITTED_MODEL_VERSION,
        )
