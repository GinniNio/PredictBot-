"""Registry-driven adapter dispatch.

An unsupported sport/market combination (no entry in the registries at all)
fails with a typed error rather than crashing or silently proceeding. A
combination that *is* registered but has no concrete forecasting adapter
routes through ``NoForecastAdapter``, which proves the framework works
end-to-end without ever fabricating a forecast.
"""

from __future__ import annotations

from typing import Any

from ..errors import UNSUPPORTED_SPORT_MARKET_COMBINATION
from ..registries.loader import get_category
from .base import AdapterInterfaceDeclaration, ForecastResult, SportAdapter

# Release B/C+ populate this with real per-sport adapter classes, keyed by
# the sport/category id from sports-registry.yaml. Release A ships it empty
# on purpose — no per-sport forecasting logic exists yet.
_ADAPTER_IMPLEMENTATIONS: dict[str, type[SportAdapter]] = {}


class NoForecastAdapter(SportAdapter):
    """Stub adapter proving the dispatch framework without forecasting.

    Registered implicitly for every category present in the registries that
    has no concrete adapter in ``_ADAPTER_IMPLEMENTATIONS``. It always
    declines to forecast, carrying the registry's documented reason.
    """

    def __init__(self, sport_id: str, reason: str) -> None:
        self._sport_id = sport_id
        self._reason = reason

    @property
    def declaration(self) -> AdapterInterfaceDeclaration:
        return AdapterInterfaceDeclaration(
            sport_id=self._sport_id,
            valid_markets=(),
            settlement_units="undeclared_no_adapter",
            feature_requirements=(),
            data_sources=(),
            uncertainty_method="none",
            model_version="none",
        )

    def forecast(self, fixture: dict[str, Any]) -> ForecastResult:
        return ForecastResult(
            sport_id=self._sport_id,
            forecast_available=False,
            probabilities=None,
            model_version=None,
            uncertainty_method=None,
            no_forecast_reason=self._reason,
        )


class UnsupportedCombinationError(Exception):
    """Raised when the category id is not present in the registries at all."""

    def __init__(self, category_id: str) -> None:
        self.code = UNSUPPORTED_SPORT_MARKET_COMBINATION
        self.category_id = category_id
        self.reason = (
            f"'{category_id}' is not present in any registry; there is no basis "
            "for pricing or forecasting this sport/market combination."
        )
        super().__init__(self.reason)


def get_adapter(category_id: str) -> SportAdapter:
    """Return the adapter for ``category_id``.

    Raises ``UnsupportedCombinationError`` if the id is not registered at
    all. Returns a concrete adapter if Release B/C+ has registered one for
    this category, otherwise ``NoForecastAdapter`` carrying the registry's
    typed unsupported reason.
    """
    category = get_category(category_id)
    if category is None:
        raise UnsupportedCombinationError(category_id)

    implementation = _ADAPTER_IMPLEMENTATIONS.get(category_id)
    if implementation is not None:
        return implementation()

    adapter_row = category.get("adapters") or {}
    reason = adapter_row.get("adapter_unsupported_reason") or "NOT_IMPLEMENTED"
    return NoForecastAdapter(sport_id=category_id, reason=reason)


def run_forecast(category_id: str, fixture: dict[str, Any]) -> dict[str, Any]:
    """Resolve and invoke the adapter for ``category_id``, returning its
    ``ForecastResult`` as a dict. Raises ``UnsupportedCombinationError`` for
    an unregistered combination — the caller must turn that into the typed
    ``UNSUPPORTED_SPORT_MARKET_COMBINATION`` failure record."""
    adapter = get_adapter(category_id)
    return adapter.forecast(fixture).to_dict()
