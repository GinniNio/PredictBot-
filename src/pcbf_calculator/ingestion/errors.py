"""Typed failure/quarantine code catalogue for the Bet9ja ingestion bridge.

Mirrors ``pcbf_calculator/errors.py``'s own convention: every code is a
plain string constant with a docstring explaining exactly when it fires,
collected in ``ALL_CODES`` for tests to enumerate against. Two distinct
severities:

- **Envelope errors** (``BET9JA_ENVELOPE_*`` prefix omitted for brevity,
  but all raised as ``Bet9jaEnvelopeError``) ABORT THE WHOLE IMPORT. These
  are contradictions in the capture file itself -- this bridge never
  silently repairs source data, per its own module docstring.
- **Quarantine reasons** attach to one fixture and never abort the
  import -- the fixture is set aside in ``fixtures-quarantined.json``
  with its own reason, and every OTHER admissible fixture still proceeds.
"""

from __future__ import annotations

# --- Envelope-level (hard-fail, whole import aborted) -----------------------

BET9JA_SEGMENT_NOT_ASSEMBLED = "BET9JA_SEGMENT_NOT_ASSEMBLED"
"""The input file is a per-run SEGMENT export
(``soccer_session.js::buildSegmentEnvelope`` -- carries ``segment_index``/
``competition_results`` instead of ``competition_ledger``/``summary``),
never the cumulative ASSEMBLED export
(``soccer_session.js::buildAssembledEnvelope``) this bridge requires."""

BET9JA_MISSING_FIELD = "BET9JA_MISSING_FIELD"
"""A required top-level assembled-envelope field is absent or the wrong
type."""

BET9JA_LEDGER_TOTAL_MISMATCH = "BET9JA_LEDGER_TOTAL_MISMATCH"
"""``summary.total`` does not equal ``len(competition_ledger)``."""

BET9JA_LEDGER_SUM_MISMATCH = "BET9JA_LEDGER_SUM_MISMATCH"
"""``summary.completed + summary.confirmed_empty + summary.failed +
summary.pending`` does not equal ``summary.total``."""

BET9JA_DUPLICATE_COMPETITION_ID = "BET9JA_DUPLICATE_COMPETITION_ID"
"""The same ``competition_id`` appears more than once in
``competition_ledger``."""

BET9JA_DUPLICATE_FIXTURE_ID = "BET9JA_DUPLICATE_FIXTURE_ID"
"""The same ``fixture_id`` appears more than once in ``fixtures``."""

BET9JA_UNKNOWN_FIXTURE_COMPETITION = "BET9JA_UNKNOWN_FIXTURE_COMPETITION"
"""A fixture's ``resolved_source_competition_id`` does not match any
``competition_id`` in ``competition_ledger``."""

BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES = "BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES"
"""A competition the ledger marks ``CONFIRMED_EMPTY`` has one or more
fixtures attributed to it -- exactly the real defect
(soccer_walker.js/soccer_session.js Rounds 13-14) this bridge refuses to
trust past. A capture with this contradiction must be corrected at its
own source, never patched here."""

BET9JA_UNTRUSTED_COMPETITION_HAS_FIXTURES = "BET9JA_UNTRUSTED_COMPETITION_HAS_FIXTURES"
"""A competition the ledger marks ``FAILED`` or ``PENDING`` (never
genuinely, trustedly completed) has fixtures attributed to it -- those
fixtures were never confirmed captured and must never be admitted."""

ENVELOPE_ERROR_CODES = frozenset(
    {
        BET9JA_SEGMENT_NOT_ASSEMBLED,
        BET9JA_MISSING_FIELD,
        BET9JA_LEDGER_TOTAL_MISMATCH,
        BET9JA_LEDGER_SUM_MISMATCH,
        BET9JA_DUPLICATE_COMPETITION_ID,
        BET9JA_DUPLICATE_FIXTURE_ID,
        BET9JA_UNKNOWN_FIXTURE_COMPETITION,
        BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES,
        BET9JA_UNTRUSTED_COMPETITION_HAS_FIXTURES,
    }
)


class Bet9jaEnvelopeError(Exception):
    """Typed, fatal envelope-validation failure. Never a bare exception --
    matches ``pricing/engine.py::PricingFailure``'s own convention."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}: {reason}")


# --- Fixture-level (quarantined, import continues) --------------------------

BET9JA_UNSUPPORTED_SPORT = "BET9JA_UNSUPPORTED_SPORT"
"""``sport`` is not exactly ``"SOCCER"``."""

BET9JA_UNSUPPORTED_STATUS = "BET9JA_UNSUPPORTED_STATUS"
"""``status`` is not exactly ``"PRE_MATCH"`` (live/virtual/Zoom or any
other status is out of scope for this release)."""

BET9JA_UNSUPPORTED_MARKET_FAMILY = "BET9JA_UNSUPPORTED_MARKET_FAMILY"
"""``market_family`` is not exactly ``"1X2"``."""

BET9JA_INCOMPLETE_PRICES = "BET9JA_INCOMPLETE_PRICES"
"""One or more of the H/D/A prices (``offered_odds.H``/``.D``/``.A``) is
missing (``None``/absent)."""

BET9JA_INVALID_PRICE = "BET9JA_INVALID_PRICE"
"""One or more of the H/D/A prices is present but not a finite number
greater than 1 (mirrors ``pcbf_calculator/errors.py::INVALID_PRICE_VALUE``'s
own bar -- decimal odds are never <= 1.0)."""

BET9JA_MISSING_TEAM_NAME = "BET9JA_MISSING_TEAM_NAME"
"""``participants.home`` or ``participants.away`` is empty/absent."""

BET9JA_MISSING_COMPETITION_ATTRIBUTION = "BET9JA_MISSING_COMPETITION_ATTRIBUTION"
"""``resolved_source_competition_id`` is empty/absent on the fixture
itself (distinct from the envelope-level
``BET9JA_UNKNOWN_FIXTURE_COMPETITION``, which fires when an id IS present
but does not resolve against the ledger)."""

BET9JA_KICKOFF_AMBIGUOUS = "BET9JA_KICKOFF_AMBIGUOUS"
"""The displayed date heading and/or kickoff time could not be resolved
to exactly one unambiguous UTC timestamp -- either the weekday/date
evidence conflicts, or more than one (or zero) candidate year near the
capture date matches the displayed weekday. Never guessed -- see
``kickoff.py``'s own module docstring."""

BET9JA_ALREADY_STARTED = "BET9JA_ALREADY_STARTED"
"""The resolved kickoff time is at or before the capture's own
``captured_at_utc`` -- a pre-match research batch must never include a
fixture that had already kicked off (or later) at capture time."""

QUARANTINE_REASON_CODES = frozenset(
    {
        BET9JA_UNSUPPORTED_SPORT,
        BET9JA_UNSUPPORTED_STATUS,
        BET9JA_UNSUPPORTED_MARKET_FAMILY,
        BET9JA_INCOMPLETE_PRICES,
        BET9JA_INVALID_PRICE,
        BET9JA_MISSING_TEAM_NAME,
        BET9JA_MISSING_COMPETITION_ATTRIBUTION,
        BET9JA_KICKOFF_AMBIGUOUS,
        BET9JA_ALREADY_STARTED,
    }
)

ALL_CODES = ENVELOPE_ERROR_CODES | QUARANTINE_REASON_CODES
