"""Typed failure/rejection code catalogue for the research-batch screening
workflow (``research_batch.py``). Matches this platform's existing
convention (see ``src/pcbf_calculator/errors.py``): every rejection carries
one of these codes plus a human-readable detail, never a bare drop.

A candidate rejected here for a pricing-layer reason (a malformed market
that somehow reached this stage) is *not* re-coded — it keeps the pricing
engine's own typed code from ``errors.py`` (e.g. ``INVALID_PRICE_VALUE``)
verbatim, since that code already names the real cause. The one code below
is the only one this module itself originates.
"""

from __future__ import annotations

SCREEN_MARKET_QUALITY_NOT_NORMAL = "SCREEN_MARKET_QUALITY_NOT_NORMAL"
"""The pricing engine's own ``market_quality.evidence_quality`` for this
fixture was not ``NORMAL`` (``ANOMALOUS_NEGATIVE_MARGIN`` — an
arbitrage-shaped, almost certainly stale or corrupted price set — or
``LOW_EVIDENCE_HIGH_MARGIN`` — margin over 50%, too little pricing evidence
to trust). This is a real, non-fabricated signal about the *offered
market's* own pricing quality, computed purely from the prices themselves
(``pricing/engine.py::_market_quality``); it never claims anything about
outcome merit.

There is deliberately no "positive edge" reject gate here. Under Release
A's only de-vig method (multiplicative/proportional), every outcome's
``point_ev`` reduces algebraically to ``1 / sum(implied_probabilities) - 1``
— identical across every outcome in a market, and non-positive for any
market with the nonnegative margin every real bookmaker market has. A
reject gate on that sign would therefore fire on essentially every real
market and admit almost nothing; it would not be a real screening signal,
just restating the market's already-known margin sign a second time. This
module instead *ranks* surviving candidates by the pricing engine's own
``market_quality.research_priority_score`` — a market-comparison signal
its own docstring already documents as built for exactly this purpose —
rather than inventing a second, redundant, and vacuous gate."""

ALL_CODES = frozenset({SCREEN_MARKET_QUALITY_NOT_NORMAL})
