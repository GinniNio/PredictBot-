"""PCBF multi-sport calculation platform.

Three layers (see ``docs/MULTI_SPORT_ARCHITECTURE.md`` for the full spec):

1. ``pricing`` — the universal, sport-agnostic market-pricing engine (real,
   tested math: implied probability, de-vigging, fair odds, EV, lower-bound
   EV).
2. ``adapters`` — the sport/market forecasting adapter interface and
   registry-driven dispatch (framework only in Release A; no per-sport
   forecasting logic).
3. ``decision`` — the PCBF decision layer rule engine (STOP rules,
   classification assignment).

``cli.py`` is the host-neutral JSON-in/JSON-out entry point tying the three
together, matching ``docs/HOST_CONTRACT.md``.
"""

from .cli import main, run_calculator

__all__ = ["main", "run_calculator"]
