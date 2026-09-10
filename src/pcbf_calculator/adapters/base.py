"""Adapter interface contract every future per-sport forecasting adapter
must implement (Release B/C+). Release A ships this interface and a single
no-op stub that proves the framework end-to-end; it contains zero
sport-specific forecasting logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AdapterInterfaceDeclaration:
    """The declared capabilities of one sport adapter.

    Every field here is a required declaration a real adapter must supply
    (per the task spec: "valid markets, settlement units, feature
    requirements, data sources, uncertainty method, and model version").
    """

    sport_id: str
    valid_markets: tuple[str, ...]
    settlement_units: str
    feature_requirements: tuple[str, ...]
    data_sources: tuple[str, ...]
    uncertainty_method: str
    model_version: str


class SportAdapter(ABC):
    """Base class a future per-sport forecasting adapter must subclass.

    Release A never subclasses this with real forecasting logic. The only
    concrete subclass shipped in this release is ``NoForecastAdapter``
    (see ``registry.py``), which proves the dispatch framework without
    fabricating a forecast.
    """

    @property
    @abstractmethod
    def declaration(self) -> AdapterInterfaceDeclaration:
        """Static capability declaration for this adapter."""

    @abstractmethod
    def forecast(self, fixture: dict[str, Any]) -> "ForecastResult":
        """Produce a forecast for one fixture, or explicitly decline to.

        Must never fabricate a probability. An adapter that has no basis
        for a forecast (missing features, unmodeled market, etc.) must
        return a ``ForecastResult`` with ``forecast_available=False`` and a
        typed ``no_forecast_reason`` — never raise, never guess.
        """


@dataclass(frozen=True)
class ForecastResult:
    """Uniform result shape every adapter (including the no-op stub) returns.

    ``forecast_available`` is the single explicit flag layer 3 and the CLI
    output use to guarantee market-derived pricing (layer 1) can never be
    mistaken for an independent forecast (layer 2, per the task's
    "sport without a forecasting adapter still gets pricing analysis"
    requirement).
    """

    sport_id: str
    forecast_available: bool
    probabilities: dict[str, float] | None
    model_version: str | None
    uncertainty_method: str | None
    no_forecast_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport_id": self.sport_id,
            "forecast_available": self.forecast_available,
            "probabilities": self.probabilities,
            "model_version": self.model_version,
            "uncertainty_method": self.uncertainty_method,
            "no_forecast_reason": self.no_forecast_reason,
        }
