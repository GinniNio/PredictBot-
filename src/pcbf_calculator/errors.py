"""Typed failure code catalogue for the multi-sport pricing platform.

Every failure this platform can produce carries one of these codes plus a
human-readable ``reason``, matching the existing ``HOST_RUNTIME_UNAVAILABLE``
/ ``M02_TEST_FAIL`` convention from ``docs/HOST_CONTRACT.md`` and
``src/pcbf_football/runtime_probe.py``: never a bare exception, never a
silently-substituted default value. Each code is documented here and again in
``docs/MULTI_SPORT_ARCHITECTURE.md``.
"""

from __future__ import annotations

# --- Layer 1: universal market-pricing engine -------------------------------

INCOMPLETE_OPPOSING_PRICES = "INCOMPLETE_OPPOSING_PRICES"
"""Fewer than two outcomes were supplied, or the price map was empty/absent.
A market cannot be de-vigged without every opposing outcome present."""

INVALID_PRICE_VALUE = "INVALID_PRICE_VALUE"
"""An outcome's price was not a finite number greater than 1.0 (this
includes booleans, strings, null, NaN, infinity, and prices <= 1.0)."""

# --- CLI-level request validation -------------------------------------------

INVALID_REQUEST = "INVALID_REQUEST"
"""A required top-level request field (event_id, category, stake,
selected_outcome) was missing or the wrong type. Distinct from
INVALID_PRICE_VALUE, which is raised only by the pricing engine itself for
a bad price inside market_prices."""

UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
"""The category resolves in the registries but its data-sources-registry
runtime_status is not PRICING_SUPPORTED (e.g. specials_combo, which is
RESEARCH_ONLY in Release A — see docs/MULTI_SPORT_ARCHITECTURE.md for why).
The registry's runtime_unsupported_reason names the specific cause."""

# --- Layer 2: sport/market adapter framework --------------------------------

UNSUPPORTED_SPORT_MARKET_COMBINATION = "UNSUPPORTED_SPORT_MARKET_COMBINATION"
"""The requested sport/category id is not present in the registries at all
(not even as PRICING_SUPPORTED). This is a hard failure: the platform has no
basis for producing any output, forecast or pricing, for this input."""

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
"""The sport/category is registered and PRICING_SUPPORTED, but no
forecasting adapter has been built for it yet. This is not a hard failure at
the CLI level: layer 1 pricing still runs and is returned, tagged with an
explicit ``forecast.forecast_available: false`` flag. Reused here for the
same meaning as the prior `pcbf_calculator` scaffold's placeholder status."""

# --- Layer 3: PCBF decision layer -------------------------------------------

MISSING_DECISION_INPUT = "MISSING_DECISION_INPUT"
"""The caller asked for a decision-layer classification but omitted one of
the required typed input blocks (evidence, freshness, liquidity,
uncertainty)."""

STOP_STALE_DATA = "STOP_STALE_DATA"
"""freshness.data_age_seconds exceeded freshness.max_age_seconds."""

STOP_INSUFFICIENT_EVIDENCE = "STOP_INSUFFICIENT_EVIDENCE"
"""evidence.sample_size was below evidence.min_sample_size."""

STOP_INSUFFICIENT_LIQUIDITY = "STOP_INSUFFICIENT_LIQUIDITY"
"""liquidity.available_stake was below liquidity.min_required_stake."""

STOP_EXCESSIVE_UNCERTAINTY = "STOP_EXCESSIVE_UNCERTAINTY"
"""uncertainty.width exceeded uncertainty.max_width."""

STOP_NEGATIVE_EV = "STOP_NEGATIVE_EV"
"""The lower-bound EV computed by layer 1 for the priced outcome was <= 0."""

ALL_CODES = frozenset(
    {
        INCOMPLETE_OPPOSING_PRICES,
        INVALID_PRICE_VALUE,
        INVALID_REQUEST,
        UNSUPPORTED_INPUT,
        UNSUPPORTED_SPORT_MARKET_COMBINATION,
        NOT_IMPLEMENTED,
        MISSING_DECISION_INPUT,
        STOP_STALE_DATA,
        STOP_INSUFFICIENT_EVIDENCE,
        STOP_INSUFFICIENT_LIQUIDITY,
        STOP_EXCESSIVE_UNCERTAINTY,
        STOP_NEGATIVE_EV,
    }
)
