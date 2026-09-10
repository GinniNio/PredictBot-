"""Sport/market adapter framework (Release A, layer 2 — framework only).

No per-sport forecasting logic lives here. See
``docs/MULTI_SPORT_ARCHITECTURE.md`` for the Release B/C plan.
"""

from .base import AdapterInterfaceDeclaration, ForecastResult, SportAdapter
from .registry import get_adapter, run_forecast

__all__ = [
    "AdapterInterfaceDeclaration",
    "ForecastResult",
    "SportAdapter",
    "get_adapter",
    "run_forecast",
]
