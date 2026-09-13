"""Typed exclusion-reason code catalogue for the research-batch screening
workflow (``research_batch.py``). Matches this platform's existing
convention (see ``src/pcbf_calculator/errors.py``): every excluded market
carries one of these codes plus a human-readable detail, never a bare drop.

A market excluded here for a pricing-layer reason (a malformed market that
somehow reached this stage) is *not* re-coded — it keeps the pricing
engine's own typed code from ``errors.py`` (e.g. ``INVALID_PRICE_VALUE``)
verbatim, since that code already names the real cause. The one code below
is the only one this module itself originates.

This is a market-quality triage workflow, not a selection/recommendation
one: a code here says something is wrong with a market's *offered pricing
data*, never that an outcome within it is or isn't worth backing. See
``research_batch.py``'s module docstring for the full framing.
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
outcome merit and never implies a recommendation either way.

There is deliberately no "positive edge" exclusion gate. Under Release A's
only de-vig method (multiplicative/proportional), every outcome's
``point_ev`` reduces algebraically to
``1 / sum(implied_probabilities) - 1`` — identical across every outcome in
a market, and non-positive for any market with the nonnegative margin
every real bookmaker market has. A gate on that sign would therefore fire
on essentially every real market and admit almost nothing; it would not be
a real screening signal, just restating the market's already-known margin
sign a second time — and worse, it would read as an outcome-level
"edge" verdict this workflow must never produce. This module instead
queues surviving markets by the pricing engine's own
``market_quality.research_priority_score`` — a market-quality
prioritization signal its own docstring already documents as built for
exactly this purpose — never an outcome ranking, never a recommendation."""

ALL_CODES = frozenset({SCREEN_MARKET_QUALITY_NOT_NORMAL})
