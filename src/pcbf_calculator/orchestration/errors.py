"""Typed exclusion-reason code catalogue this module originates itself.

Matches this platform's existing convention (see ``src/pcbf_calculator/errors.py``,
``screening/errors.py``): every code kept verbatim from an upstream layer
(``ingestion/bet9ja.py``'s ``BET9JA_*``, ``screening/research_batch.py``'s
``SCREEN_MARKET_QUALITY_NOT_NORMAL``, the pricing engine's own failure
codes, the adapter's own ``FORECAST_*`` codes) is never re-coded here. The
one code below is the only one this module itself originates, for a case
that should never occur in practice for this category's fixed
home/draw/away vocabulary -- see its own docstring.
"""

from __future__ import annotations

ORCH_MARKET_COMPARISON_UNAVAILABLE = "ORCH_MARKET_COMPARISON_UNAVAILABLE"
"""A real forecast was produced, and the market cleared the quality gate,
but ``cli.py::run_calculator``'s own ``market_comparison`` came back
``None`` anyway -- meaning the adapter's outcome vocabulary and the priced
market's outcome set did not line up. Should never fire for this
category's fixed home/draw/away shape; if it somehow does, this fixture is
recorded as a forecast abstention with this code rather than silently
ranked without a market comparison."""

ALL_CODES = frozenset({ORCH_MARKET_COMPARISON_UNAVAILABLE})
