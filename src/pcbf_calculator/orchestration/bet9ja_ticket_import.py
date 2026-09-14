"""Bet9ja settled/open ticket capture -> betting-ledger PLACED/SETTLED import.

    Bet9ja settled-bets / open-bets JSON capture(s)
    -> per-ticket normalization + FULL typed-reason evaluation (never
       stops at the first failing check)
    -> exact canonical forecast linkage (never substring matching)
    -> whole-batch PLACED preflight, then whole-batch SETTLED preflight
    -> ledgers/betting_ledger.py::write_batch_placed / write_batch_terminal

One command::

    python -m pcbf_calculator import-bet9ja-tickets FILE [FILE...] \\
        --currency NGN --betting-ledger-dir ledger_data \\
        --forecast-ledger-dir ledger_data --output-dir runs/ticket-import-session-id \\
        [--dry-run]

**Currency.** ``--currency`` is REQUIRED and is never inferred from odds,
stake formatting, or the bookmaker's own name -- Bet9ja's capture carries
no currency field at all. The operator-supplied value is stored verbatim
on every ticket this run produces.

**Every applicable reason is reported, not just the first.** Earlier
versions of this module short-circuited on the first failing check, which
made it impossible to tell whether a settled ticket ALSO lacked a
resolvable stake structure without fixing the first problem and
re-running. ``evaluate_ticket`` now runs every independent check
regardless of earlier failures and returns the full list in
``TicketOutcome.reasons`` (``TicketOutcome.reason`` stays as the first one,
for callers/tests that only care about a single primary reason). The
per-run report's own ``quarantine_reason_matrix`` records every ticket's
full reason list, and ``quarantine_reason_counts`` counts a ticket once
for EVERY reason it carries (so counts can sum to more than the number of
quarantined tickets).

**Settled tickets use the bookmaker's own reported figure, never a
recomputed one.** A settled ticket (``ticket_status`` present -- distinct
from an open ticket's ``status: "OPEN"``) never needs ``potential_return``
at all: what actually got paid is already known. When trusted, already-
structured fields are present --

- ``bet9ja_ticket_id`` (ticket ID)
- ``total_stake``
- ``actual_payout`` (or, for a ``LOST`` ticket, its absence is itself
  meaningful -- see below)
- ``ticket_status`` in ``{"WON", "LOST"}``
- ``currency`` (operator-supplied, always present)
- ``legs`` (selections), each with a recognized ``leg_status``
- ``source_raw_hash`` (always computed by this module)

-- the ticket is recorded with ``settlement_basis: "BOOKMAKER_OBSERVED"``
(``ledgers.betting_ledger.settle_bookmaker_observed``): ``actual_return``
is the bookmaker's own ``actual_payout`` verbatim, NEVER recomputed from
``leg_results``/odds, even when a stake structure happens to be resolvable
-- a real bookmaker figure already reflects voids, promotions, and
rounding a pure combinatorial replay cannot see. ``ticket_status ==
"LOST"`` is treated as ``actual_return = 0`` when ``actual_payout`` is
itself absent (real settled-bets captures never populate it for a lost
ticket) -- this is not a guess about money math, it is what "LOST" means
at the bookmaker; a present, non-null ``actual_payout`` on a LOST ticket
(e.g. a partial void refund) is still honored verbatim instead. A SYSTEM
settled ticket eligible for this path is built with
``stake_structure_known=False`` -- its internal fold-size breakdown is
never needed and never attempted, regardless of whether it happens to be
derivable (see below). ``settle_computed``'s own
``"COMPUTED_FROM_SELECTIONS"`` basis remains available (and tested) for a
caller with a real, resolvable stake structure but no bookmaker-observed
final figure to trust instead -- this module's own real-data paths do
not currently need it, since every real settled capture that reaches the
bookmaker-observed check either has one or is quarantined.

**System stake structure (OPEN tickets only -- see above for settled
ones).** A real Bet9ja SYSTEM ticket can have a DIFFERENT unit stake per
fold size (e.g. Singles at one stake, Doubles at another) -- a single
scalar ``unit_stake`` cannot represent that at all (see
``ledgers/betting_ledger.py::_validate_stake_buckets``). This module
NEVER regex-parses ``system_table_raw`` (a lossy, unspaced text scrape --
see "Why no system_table_raw parsing" below) to recover that structure.
Three paths only, for an OPEN SYSTEM ticket, none of them ever reading
``system_table_raw``:

1. The raw ticket already carries a genuine, structured ``stake_buckets``
   field (once the browser capture is fixed to emit one directly -- see
   ``browser_extension/bet9ja_capture/stake_buckets.js`` and "Capture
   fix" below). Passed straight through to
   ``build_placed_event(stake_buckets=...)``.
2. The raw ticket's own ``ticket_type_raw`` field -- a genuinely
   SEPARATE, already-structured field
   (``browser_extension/bet9ja_capture/ticket_parser.js``'s own
   ``findUnambiguousSystemSplit``, NOT this module reading
   ``system_table_raw``) -- names one of the three fold sizes Bet9ja
   labels directly: ``"Singles"`` (1), ``"Doubles"`` (2), ``"Trebles"``
   (3). That label is never trusted alone: this module independently
   re-verifies it against the ticket's own ``unit_stake``/``total_stake``/
   leg count (``unit_stake * C(leg_count, fold_size) == total_stake``)
   before using it -- a label whose own arithmetic doesn't check out is
   never used, falling through to path 3 instead.
3. Failing that (no usable label -- e.g. an unnamed "N Folds" system),
   the raw ticket's own ``unit_stake``/``total_stake`` fields are both
   present and numeric, AND exactly one fold size ``k`` (1..leg_count)
   satisfies ``C(leg_count, k) == total_stake / unit_stake`` (an integer
   combinatorial identity checked purely from already-structured numbers).
   Exactly one solution is required: zero or more than one is refused,
   never guessed (see ``_derive_fold_size``).

   Because ``C(n, k) == C(n, n-k)`` for every binomial coefficient, path
   3 almost always finds TWO candidates, not one, for any fold size other
   than a full accumulator (``k == leg_count``) or the exact midpoint of
   an even leg count -- exactly the symmetry path 2's label sidesteps
   entirely by naming the fold size directly instead of inferring it from
   a count. This is a deliberate, structural safety property: a wrong
   fold-size guess would silently misprice every settlement this ticket
   is ever scored against.

Any OPEN SYSTEM ticket satisfying none of the three paths is quarantined
``SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE`` -- recorded nowhere in the ledger,
never estimated. In practice, this session's own real open-bets capture
(45 SYSTEM tickets) has 18 tickets with a genuine "Singles"/"Doubles"/
"Trebles" label (path 2) and 0 resolvable by path 3 alone.

**Why no system_table_raw parsing.** ``system_table_raw`` is
``.mybets__systable``'s flattened text with no reliable delimiters (e.g.
``"Singles535.00175.00"`` -- digit boundaries between the bet count and
the two money columns are genuinely ambiguous without the DOM's own cell
structure). An earlier regex-based reading of this exact field, in this
same working session, silently misparsed a bet count -- this module
exists specifically so that mistake is structurally impossible to
repeat: nothing in this file ever opens ``system_table_raw`` for anything
but verbatim, read-only preservation inside ``source_raw``.

**Capture fix (browser extension).**
``browser_extension/bet9ja_capture/stake_buckets.js`` walks
``.mybets__systable``'s real DOM rows/cells (never the flattened text) to
emit a structured ``stake_buckets`` array directly, and both
``settled_bets_parser.js`` and ``ticket_parser.js`` now call it and attach
the result whenever the table's own row/cell shape is unambiguous. It is
still defensive by construction: any row it cannot cleanly resolve into
exactly 4 cells (System Type, No. Bets, Unit Stake, Stake), or whose
"No. Bets" disagrees with the canonical ``C(leg_count, fold_size)`` for
the parsed fold size, is dropped from ``stake_buckets`` rather than
guessed at, and if ANY row is dropped this way the whole
``stake_buckets`` array is omitted (never a partial, silently-wrong
structure) -- see that module's own docstring and
``docs/bet9ja_capture/STAKE_BUCKETS_LIVE_VALIDATION.md`` for exactly what
has, and has not yet, been confirmed against a real, live Bet9ja page
(the row/cell DOM shape assumed here is standard HTML table markup, but
has not been visually confirmed against Bet9ja's own live page -- that
confirmation needs a real captured page, which this working session does
not have access to).

**Forecast linkage.** Exact canonical identity only, via the SAME
resolution ``soccer_1x2_elo_v1``'s own ``forecast()`` and
``football_data_settlement.py``'s own settlement matching use -- never
loose substring matching. A leg whose competition/market/team names don't
resolve, or whose match against the forecast ledger is ambiguous, is left
with ``forecast_id: None`` -- unlinked, never guessed, and NEVER a reason
to refuse recording the ticket itself.

**Import safety.** Every ticket is normalized, evaluated, and linked
entirely in memory before a single ledger byte is written. The accepted
PLACED events commit as one atomic batch
(``betting_ledger.write_batch_placed``); the accepted SETTLED events
(bookmaker-observed) then commit as a second atomic batch
(``betting_ledger.write_batch_terminal``) against the now-updated ledger.
Nothing is written to either if that batch's own preflight finds a
conflict, or if ``dry_run=True``. A conflict in the SETTLED batch does
NOT retroactively undo an already-committed PLACED batch (the two are
genuinely separate, sequential real-world events -- a ticket is placed,
then later settles -- exactly like the existing, separate
``place_ticket_checked``/``settle_computed`` functions already treat
them); the report distinguishes PLACED-batch and SETTLED-batch outcomes
so this is never hidden. Every original Bet9ja field is preserved
verbatim in the PLACED event's own ``source_raw``/``source_raw_hash``,
regardless of acceptance outcome.

This module never retrains or reclassifies any model, and never selects,
recommends, or sizes a stake -- it only ever records tickets an operator
has already placed, and settlements a bookmaker has already reported,
exactly as captured.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ..adapters.soccer_1x2_elo_v1.adapter import load_known_teams_by_league, load_team_alias_book  # noqa: E402
from ..adapters.soccer_1x2_elo_v1.identity import resolve_competition, resolve_team  # noqa: E402


def _ensure_ledgers_importable() -> None:
    """Same fallback ``orchestration/forecast_ledger_writer.py`` and
    ``orchestration/football_data_settlement.py`` already use for
    themselves -- see either module's own copy of this helper for the
    full rationale."""

    try:
        import ledgers  # noqa: F401

        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "ledgers" / "__init__.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_ledgers_importable()

from ledgers import betting_ledger  # noqa: E402
from ledgers.locking import LedgerLockTimeoutError  # noqa: E402
from ledgers.storage import all_entity_ids, latest_state, read_all  # noqa: E402

SCHEMA_VERSION_REPORT = "pcbf-bet9ja-ticket-import-report.v2"

MARKET_TYPE_1X2 = "1X2"
LAGOS_TZ = ZoneInfo("Africa/Lagos")

RECOGNIZED_LEG_OUTCOMES = ("WON", "LOST", "VOID")
RECOGNIZED_TICKET_STATUSES = ("WON", "LOST")

# --- Typed quarantine reasons (ticket-level: the whole ticket is set aside,
# never partially recorded). A ticket can carry more than one of these at
# once -- see TicketOutcome.reasons / run_import's own reason matrix. -----

REASON_UNSUPPORTED_TICKET_TYPE = "TICKET_UNSUPPORTED_TYPE"
REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE = "SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE"
REASON_UNIT_STAKE_MISSING = "TICKET_UNIT_STAKE_MISSING"
REASON_MISSING_MAX_RETURN = "TICKET_MISSING_MAX_RETURN"
REASON_UNSUPPORTED_SETTLEMENT_STATUS = "TICKET_UNSUPPORTED_SETTLEMENT_STATUS"
REASON_MISSING_ACTUAL_PAYOUT = "TICKET_MISSING_ACTUAL_PAYOUT"
REASON_LEG_OUTCOME_MISSING = "TICKET_LEG_OUTCOME_MISSING"
REASON_PLACED_AT_UNPARSEABLE = "TICKET_PLACED_AT_UNPARSEABLE"
REASON_NO_LEGS = "TICKET_NO_LEGS"
REASON_INVALID_TOTAL_STAKE = "TICKET_INVALID_TOTAL_STAKE"
REASON_BUILD_FAILED = "TICKET_BUILD_FAILED"

QUARANTINE_REASON_CODES = frozenset(
    {
        REASON_UNSUPPORTED_TICKET_TYPE,
        REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE,
        REASON_UNIT_STAKE_MISSING,
        REASON_MISSING_MAX_RETURN,
        REASON_UNSUPPORTED_SETTLEMENT_STATUS,
        REASON_MISSING_ACTUAL_PAYOUT,
        REASON_LEG_OUTCOME_MISSING,
        REASON_PLACED_AT_UNPARSEABLE,
        REASON_NO_LEGS,
        REASON_INVALID_TOTAL_STAKE,
        REASON_BUILD_FAILED,
    }
)

# --- Leg-level unlink reasons (never block the ticket) ---------------------

UNLINK_UNSUPPORTED_MARKET = "LEG_UNSUPPORTED_MARKET"
UNLINK_COMPETITION_NOT_COVERED = "LEG_COMPETITION_NOT_COVERED"
UNLINK_FIXTURE_TEXT_UNPARSEABLE = "LEG_FIXTURE_TEXT_UNPARSEABLE"
UNLINK_HOME_TEAM_UNRESOLVED = "LEG_HOME_TEAM_UNRESOLVED"
UNLINK_AWAY_TEAM_UNRESOLVED = "LEG_AWAY_TEAM_UNRESOLVED"
UNLINK_NO_MATCHING_FORECAST = "LEG_NO_MATCHING_FORECAST"
UNLINK_AMBIGUOUS_MATCH = "LEG_AMBIGUOUS_FORECAST_MATCH"

_MONTH_ABBREVIATIONS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Home - Away" then optionally an immediately-appended "D Mon HH:MM"
# (no separating space before the day, e.g. open-bets capture's own
# "Cincinnati Reds - Los Angeles Dodgers14 Sep 23:40"). Greedy ``.*`` so
# the match anchors on the LAST occurrence of this shape at the very end
# of the string, never the first digit that happens to appear inside a
# team name (e.g. "1899 Hoffenheim").
_FIXTURE_TAIL_RE = re.compile(r"^(?P<rest>.*?)(?P<day>\d{1,2}) (?P<month>[A-Za-z]{3}) (?P<hour>\d{2}):(?P<minute>\d{2})$")

# "D Mon YYYY HH:MM", e.g. "14 Sep 2026 17:40" -- placed_at_raw's own
# format on both settled- and open-bets captures, always carrying an
# explicit year (unlike fixture_and_time_raw), so no year-window guess is
# ever needed here.
_PLACED_AT_RE = re.compile(r"^(?P<day>\d{1,2}) (?P<month>[A-Za-z]{3}) (?P<year>\d{4}) (?P<hour>\d{2}):(?P<minute>\d{2})$")

# A resolved fixture date must fall within this many days of the ticket's
# own placement time to be considered a candidate at all -- bounds the
# day/month (no year) text in fixture_and_time_raw to one, unambiguous
# calendar year without needing a declared weekday (unlike
# ingestion/kickoff.py's own resolver, which has one).
_FIXTURE_DATE_WINDOW_BACK_DAYS = 3
_FIXTURE_DATE_WINDOW_FORWARD_DAYS = 120

# "Singles"/"Doubles"/"Trebles" -> fold size, for OPEN SYSTEM tickets
# whose ticket_type_raw (a genuinely separate, already-structured field
# -- NOT parsed from system_table_raw here or anywhere in this module)
# carries one of these three named sizes. See the fold-size derivation
# comment inside evaluate_ticket for exactly why this is trusted (never
# on the label alone -- always re-verified against the ticket's own
# unit_stake/total_stake/leg_count arithmetic first).
_FOLD_LABEL_TO_SIZE = {"singles": 1, "doubles": 2, "trebles": 3}


def _sha256_of(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_month(text: str) -> int | None:
    return _MONTH_ABBREVIATIONS.get(text[:3].lower())


def parse_placed_at_utc(placed_at_raw: str | None) -> str | None:
    """Bet9ja's displayed placement time is Africa/Lagos local (same fixed
    UTC+1, no DST, fact ``ingestion/kickoff.py`` already relies on) and
    ALWAYS carries an explicit year here, so this conversion is exact --
    never a candidate-year search. Returns ``None`` (never a fabricated
    "now") if the text is not shaped like Bet9ja's own confirmed
    "D Mon YYYY HH:MM" display."""

    if not placed_at_raw:
        return None
    match = _PLACED_AT_RE.match(placed_at_raw.strip())
    if not match:
        return None
    month = _parse_month(match.group("month"))
    if month is None:
        return None
    try:
        local = datetime(
            int(match.group("year")), month, int(match.group("day")),
            int(match.group("hour")), int(match.group("minute")), tzinfo=LAGOS_TZ,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ParsedFixtureText:
    home: str
    away: str
    day: int | None
    month: int | None
    hour: int | None
    minute: int | None


def _parse_fixture_and_time(raw: str) -> ParsedFixtureText | None:
    """Splits Bet9ja's own ``fixture_and_time_raw`` into a home/away team
    pair, and a day/month/hour/minute tail WHEN one is present (open-bets
    captures carry it; settled-bets captures do not -- see this module's
    own docstring). Returns ``None`` only when even the ``" - "`` team
    separator cannot be found exactly once -- never a fuzzy split."""

    if raw is None:
        return None
    text = raw.strip()
    tail_match = _FIXTURE_TAIL_RE.match(text)
    if tail_match:
        month = _parse_month(tail_match.group("month"))
        if month is None:
            return None
        rest = tail_match.group("rest")
        day, hour, minute = int(tail_match.group("day")), int(tail_match.group("hour")), int(tail_match.group("minute"))
    else:
        rest, day, month, hour, minute = text, None, None, None, None

    parts = rest.split(" - ")
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(), parts[1].strip()
    if not home or not away:
        return None
    return ParsedFixtureText(home=home, away=away, day=day, month=month, hour=hour, minute=minute)


def _resolve_fixture_scheduled_date(day: int, month: int, anchor_utc: str | None) -> str | None:
    """Resolves ``(day, month)`` -- no year in the source text -- to one
    UTC calendar date, anchored near ``anchor_utc`` (the ticket's own
    resolved placement time, or its ``captured_at_utc`` fallback). Exactly
    one candidate year within the fixed window is required; zero or more
    than one is unresolved, never guessed (mirrors
    ``ingestion/kickoff.py``'s own "never guess the year" discipline, with
    a date window standing in for that module's declared-weekday check,
    since this raw text carries no weekday)."""

    if not anchor_utc:
        return None
    try:
        anchor = datetime.fromisoformat(anchor_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    anchor_date = anchor.astimezone(timezone.utc).date()
    window_start = anchor_date - timedelta(days=_FIXTURE_DATE_WINDOW_BACK_DAYS)
    window_end = anchor_date + timedelta(days=_FIXTURE_DATE_WINDOW_FORWARD_DAYS)

    candidates = []
    for year in (anchor_date.year - 1, anchor_date.year, anchor_date.year + 1):
        try:
            candidate = anchor_date.replace(year=year, month=month, day=day)
        except ValueError:
            continue
        if window_start <= candidate <= window_end:
            candidates.append(candidate)
    if len(candidates) != 1:
        return None
    return candidates[0].isoformat()


def _derive_fold_size(leg_count: int, unit_stake: Decimal, total_stake: Decimal) -> int | None:
    """Derives the single fold size a SYSTEM ticket's stake structure uses,
    from already-structured numbers alone (``len(legs)``, ``unit_stake``,
    ``total_stake``) -- NEVER from ``system_table_raw`` text. Returns the
    unique ``k`` in ``1..leg_count`` such that
    ``C(leg_count, k) == total_stake / unit_stake``, or ``None`` if that
    quotient is not a positive integer, or zero or more than one ``k``
    satisfies it (an ambiguous or unrepresentable shape is refused, never
    guessed at)."""

    if unit_stake <= 0:
        return None
    quotient = total_stake / unit_stake
    if quotient != quotient.to_integral_value():
        return None
    combination_count = int(quotient)
    if combination_count < 1:
        return None
    matches = [k for k in range(1, leg_count + 1) if _n_choose_k(leg_count, k) == combination_count]
    return matches[0] if len(matches) == 1 else None


def _n_choose_k(n: int, k: int) -> int:
    return len(list(itertools.combinations(range(n), k))) if k <= n else 0


@dataclass
class ForecastIndex:
    """Exact-identity lookup over the forecast ledger's CURRENT state (one
    entry per ``forecast_id``, latest event wins) -- built once per
    ``run_import`` call and reused across every ticket's legs. Keyed
    EXACTLY like ``football_data_settlement.py``'s own settlement matching
    key (``competition_code``, ``resolved_home_team``, ``resolved_away_team``,
    ``market_type``), because that is the identity a forecast's own
    adapter already resolved and recorded -- never re-derived here."""

    by_team_key: dict[tuple[str, str, str, str], list[tuple[str | None, str]]] = field(default_factory=dict)

    @classmethod
    def build(cls, forecast_ledger_path: Path) -> "ForecastIndex":
        records = read_all(forecast_ledger_path)
        index: dict[tuple[str, str, str, str], list[tuple[str | None, str]]] = {}
        for fid in all_entity_ids(records, "forecast_id"):
            state = latest_state(records, "forecast_id", fid)
            competition_code = state.get("competition_code")
            home = state.get("resolved_home_team")
            away = state.get("resolved_away_team")
            market_type = state.get("market_type", MARKET_TYPE_1X2)
            if not (competition_code and home and away):
                continue
            key = (competition_code, home, away, market_type)
            index.setdefault(key, []).append((state.get("scheduled_date"), fid))
        return cls(by_team_key=index)

    def resolve_leg(
        self, competition_code: str, home: str, away: str, market_type: str, scheduled_date: str | None
    ) -> tuple[str | None, str]:
        """Returns ``(forecast_id_or_None, reason_code_or_empty)``."""

        candidates = self.by_team_key.get((competition_code, home, away, market_type), [])
        if not candidates:
            return None, UNLINK_NO_MATCHING_FORECAST
        if scheduled_date is not None:
            candidates = [c for c in candidates if c[0] == scheduled_date]
            if not candidates:
                return None, UNLINK_NO_MATCHING_FORECAST
        if len(candidates) != 1:
            return None, UNLINK_AMBIGUOUS_MATCH
        return candidates[0][1], ""


@dataclass
class LegLinkResult:
    forecast_id: str | None
    fixture_id: str
    market_type: str
    selection: str
    placed_odds: Any
    unlink_reason: str | None


def link_leg(raw_leg: dict[str, Any], forecast_index: ForecastIndex, anchor_utc: str | None) -> LegLinkResult:
    """Resolves one raw ticket leg to a canonical forecast_id, or leaves it
    unlinked with a typed reason -- NEVER a substring match, and NEVER a
    reason to drop the leg itself: ``fixture_id``/``market_type``/
    ``selection``/``placed_odds`` are always populated from the leg's own
    raw fields regardless of link outcome."""

    fixture_id = raw_leg.get("fixture_id") or ""
    market_raw = raw_leg.get("market_raw")
    selection = raw_leg.get("selection") or raw_leg.get("selection_raw") or ""
    # Bet9ja's own capture JSON stores money/odds fields inconsistently --
    # sometimes a decimal string, sometimes a bare JSON number -- so every
    # one is normalized through str() here before it ever reaches
    # ledgers/money.py's stricter float rejection, exactly like
    # ``_to_decimal_or_none`` already does for total_stake/unit_stake. A
    # faithful, lossless text render of whatever JSON scalar the capture
    # already parsed, never a second guess at the underlying value.
    raw_odds = raw_leg.get("odds")
    placed_odds = str(raw_odds) if raw_odds is not None else None

    if market_raw != MARKET_TYPE_1X2:
        return LegLinkResult(None, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, UNLINK_UNSUPPORTED_MARKET)

    competition = resolve_competition(raw_leg.get("competition_raw") or "")
    if competition.resolved is None:
        return LegLinkResult(None, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, UNLINK_COMPETITION_NOT_COVERED)

    parsed = _parse_fixture_and_time(raw_leg.get("fixture_and_time_raw") or "")
    if parsed is None:
        return LegLinkResult(None, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, UNLINK_FIXTURE_TEXT_UNPARSEABLE)

    known_teams = load_known_teams_by_league().get(competition.resolved, set())
    alias_book = load_team_alias_book()
    home_result = resolve_team(parsed.home, competition.resolved, known_teams, alias_book)
    if home_result.resolved is None:
        return LegLinkResult(None, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, UNLINK_HOME_TEAM_UNRESOLVED)
    away_result = resolve_team(parsed.away, competition.resolved, known_teams, alias_book)
    if away_result.resolved is None:
        return LegLinkResult(None, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, UNLINK_AWAY_TEAM_UNRESOLVED)

    scheduled_date = None
    if parsed.day is not None and parsed.month is not None:
        scheduled_date = _resolve_fixture_scheduled_date(parsed.day, parsed.month, anchor_utc)

    forecast_id, reason = forecast_index.resolve_leg(
        competition.resolved, home_result.resolved, away_result.resolved, MARKET_TYPE_1X2, scheduled_date
    )
    return LegLinkResult(forecast_id, fixture_id, MARKET_TYPE_1X2, selection, placed_odds, None if forecast_id else reason)


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass
class TicketOutcome:
    bet9ja_ticket_id: str | None
    status: str  # "ACCEPTED" | "QUARANTINED"
    reasons: list[str] = field(default_factory=list)
    event: dict[str, Any] | None = None  # the PLACED event
    settled_event: dict[str, Any] | None = None  # the SETTLED event, when this ticket is bookmaker-observed
    unlinked_leg_count: int = 0

    @property
    def reason(self) -> str | None:
        """The first applicable reason, for a caller that only cares
        about a single primary one -- ``reasons`` carries the full set."""

        return self.reasons[0] if self.reasons else None


def evaluate_ticket(
    raw_ticket: dict[str, Any], *, currency: str, forecast_index: ForecastIndex
) -> TicketOutcome:
    """Runs EVERY independent check against one raw Bet9ja ticket record --
    never stops at the first failure -- and returns either a fully-built
    ``TicketOutcome`` (``status="ACCEPTED"``, ``reasons=[]``) or a
    quarantine outcome carrying every applicable reason
    (``status="QUARANTINED"``, ``reasons`` non-empty). Never raises for a
    malformed ticket."""

    bet9ja_id = raw_ticket.get("bet9ja_ticket_id")
    source_raw_hash = _sha256_of(raw_ticket)
    reasons: list[str] = []

    ticket_type = raw_ticket.get("ticket_type_normalized")
    ticket_type_ok = ticket_type in betting_ledger.TICKET_TYPES
    if not ticket_type_ok:
        reasons.append(REASON_UNSUPPORTED_TICKET_TYPE)

    raw_legs = raw_ticket.get("legs") or []
    if not raw_legs:
        reasons.append(REASON_NO_LEGS)

    total_stake_d = _to_decimal_or_none(raw_ticket.get("total_stake"))
    total_stake_ok = total_stake_d is not None and total_stake_d > 0
    if not total_stake_ok:
        reasons.append(REASON_INVALID_TOTAL_STAKE)

    # A settled capture carries "ticket_status" (WON/LOST); an open
    # capture carries "status": "OPEN" instead -- the two keys never
    # collide, so this is an exact, never-guessed distinction.
    is_settled = raw_ticket.get("ticket_status") is not None

    max_return: str | None = None
    actual_return_value: str | None = None
    leg_results: list[dict[str, Any]] | None = None

    if is_settled:
        ticket_status = raw_ticket.get("ticket_status")
        if ticket_status not in RECOGNIZED_TICKET_STATUSES:
            reasons.append(REASON_UNSUPPORTED_SETTLEMENT_STATUS)
        else:
            actual_payout_raw = raw_ticket.get("actual_payout")
            if ticket_status == "LOST":
                # A LOST ticket returns nothing by definition -- this is
                # not a guess about money math, it is what "LOST" means.
                # A present, non-null actual_payout (e.g. a partial void
                # refund) is still honored verbatim instead of being
                # overridden to zero.
                actual_return_value = str(actual_payout_raw) if actual_payout_raw is not None else "0"
            elif actual_payout_raw is None:
                reasons.append(REASON_MISSING_ACTUAL_PAYOUT)
            else:
                actual_return_value = str(actual_payout_raw)

        built_leg_results: list[dict[str, Any]] = []
        any_leg_outcome_missing = False
        for i, leg in enumerate(raw_legs):
            outcome = leg.get("leg_status")
            if outcome not in RECOGNIZED_LEG_OUTCOMES:
                any_leg_outcome_missing = True
                continue
            built_leg_results.append({"leg_index": i, "outcome": outcome})
        if raw_legs and any_leg_outcome_missing:
            reasons.append(REASON_LEG_OUTCOME_MISSING)
        elif raw_legs:
            leg_results = built_leg_results
    else:
        max_return_raw = raw_ticket.get("potential_return")
        if max_return_raw is None:
            reasons.append(REASON_MISSING_MAX_RETURN)
        else:
            max_return = str(max_return_raw)

    placed_at_utc = parse_placed_at_utc(raw_ticket.get("placed_at_raw"))
    if placed_at_utc is None:
        reasons.append(REASON_PLACED_AT_UNPARSEABLE)

    # Stake structure. A settled SYSTEM ticket never needs this at all
    # (settle_bookmaker_observed never replays combinatorics) -- checked
    # ONLY for an open SYSTEM ticket, or any non-SYSTEM ticket (whose
    # combination_count is always trivially 1, so the only thing that can
    # be missing is the unit_stake number itself).
    unit_stake_arg: Any = None
    stake_buckets_arg: list[dict[str, Any]] | None = None
    system_sizes_arg: list[int] | None = None
    stake_structure_known = True
    total_stake_arg: Any = None

    if ticket_type_ok and ticket_type == "SYSTEM" and is_settled:
        stake_structure_known = False
        total_stake_arg = str(raw_ticket["total_stake"]) if total_stake_ok else None
    elif ticket_type_ok and ticket_type == "SYSTEM":
        if isinstance(raw_ticket.get("stake_buckets"), list) and raw_ticket["stake_buckets"]:
            # Path 1: a genuine, already-structured field -- never built
            # from system_table_raw anywhere in this codebase today.
            # Money fields normalized through str() for the same reason
            # as every other money field in this module (see link_leg).
            stake_buckets_arg = [
                {
                    "fold_size": bucket["fold_size"],
                    "combination_count": bucket["combination_count"],
                    "unit_stake": str(bucket["unit_stake"]),
                    "total_stake": str(bucket["total_stake"]),
                }
                for bucket in raw_ticket["stake_buckets"]
            ]
        else:
            unit_stake_d = _to_decimal_or_none(raw_ticket.get("unit_stake"))
            fold_size = None
            if unit_stake_d is not None and total_stake_ok:
                # Path 2a: ticket_type_raw ("Singles"/"Doubles"/"Trebles")
                # is a genuinely SEPARATE, already-structured field --
                # NOT parsed from system_table_raw by this module.
                # ticket_parser.js's own findUnambiguousSystemSplit()
                # only ever populates it (and this ticket's own
                # unit_stake) from a text split it already verified is
                # the SOLE arithmetically-consistent one
                # (bets * unit_stake == total_stake); this branch simply
                # maps that already-verified label to its fold size,
                # then independently re-confirms the arithmetic itself
                # (never trusting the label alone) before ever using it.
                fold_size = _FOLD_LABEL_TO_SIZE.get(str(raw_ticket.get("ticket_type_raw") or "").strip().lower())
                if fold_size is not None:
                    expected_count = _n_choose_k(len(raw_legs), fold_size)
                    if expected_count < 1 or unit_stake_d * expected_count != total_stake_d:
                        fold_size = None  # the label's own arithmetic doesn't check out -- never trusted anyway
                if fold_size is None:
                    # Path 2b: no fold-size label available (e.g. an
                    # unlabeled "N Folds" system) -- fall back to the
                    # binomial-identity derivation, with its own
                    # documented symmetry limits (see _derive_fold_size).
                    fold_size = _derive_fold_size(len(raw_legs), unit_stake_d, total_stake_d)
            if unit_stake_d is None or fold_size is None:
                reasons.append(REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)
            else:
                unit_stake_arg = str(raw_ticket["unit_stake"])
                system_sizes_arg = [fold_size]
    elif ticket_type_ok:
        unit_stake_d = _to_decimal_or_none(raw_ticket.get("unit_stake"))
        if unit_stake_d is None:
            reasons.append(REASON_UNIT_STAKE_MISSING)
        else:
            unit_stake_arg = str(raw_ticket["unit_stake"])

    # Forecast linkage always runs, regardless of any reason above --
    # never blocks the ticket, and its own result is needed either way
    # to build the accepted event's legs payload.
    linked_legs = [link_leg(leg, forecast_index, placed_at_utc) for leg in raw_legs]
    unlinked_count = sum(1 for leg in linked_legs if leg.forecast_id is None)
    legs_payload = [
        {
            "forecast_id": leg.forecast_id,
            "fixture_id": leg.fixture_id,
            "market_type": leg.market_type,
            "selection": leg.selection,
            "placed_odds": leg.placed_odds,
        }
        for leg in linked_legs
    ]

    if reasons:
        return TicketOutcome(bet9ja_ticket_id=bet9ja_id, status="QUARANTINED", reasons=reasons)

    try:
        placed_event = betting_ledger.build_placed_event(
            ticket_type=ticket_type,
            max_return=max_return,
            currency=currency,
            legs=legs_payload,
            unit_stake=unit_stake_arg,
            stake_buckets=stake_buckets_arg,
            system_sizes=system_sizes_arg,
            stake_structure_known=stake_structure_known,
            total_stake=total_stake_arg,
            placed_at_utc=placed_at_utc,
            external_ticket_ref=bet9ja_id,
            source_raw=raw_ticket,
            source_raw_hash=source_raw_hash,
        )
    except (ValueError, betting_ledger.money.MoneyValueError):
        return TicketOutcome(bet9ja_ticket_id=bet9ja_id, status="QUARANTINED", reasons=[REASON_BUILD_FAILED])

    settled_event = None
    if is_settled:
        # Built entirely in memory, from the PLACED event this same call
        # just produced plus the already-validated total_stake_d above --
        # see _build_settled_event_dict's own docstring for why this does
        # NOT call betting_ledger.settle_bookmaker_observed directly (that
        # function reads a real ledger file; this module builds every
        # ticket's events before either batch is committed).
        settled_event = _build_settled_event_dict(
            placed_event["ticket_id"], actual_return_value, leg_results or [], total_stake_d,
            settled_at_utc=raw_ticket.get("captured_at_utc"),
        )

    return TicketOutcome(
        bet9ja_ticket_id=bet9ja_id,
        status="ACCEPTED",
        event=placed_event,
        settled_event=settled_event,
        unlinked_leg_count=unlinked_count,
    )


def _build_settled_event_dict(
    ticket_id: str,
    actual_return: str,
    leg_results: list[dict[str, Any]],
    total_stake_d: Decimal,
    settled_at_utc: str | None,
) -> dict[str, Any]:
    """Builds a bookmaker-observed SETTLED event dict WITHOUT touching any
    ledger file (unlike ``betting_ledger.settle_bookmaker_observed``,
    which reads the ledger to look up the ticket's own recorded
    ``total_stake`` and to enforce the one-time-transition rule) -- this
    module builds every ticket's PLACED and SETTLED events entirely in
    memory, from the same already-validated raw ticket, before either
    batch is committed. The one-time-transition/duplicate/conflict rules
    are enforced later, for the whole batch at once, by
    ``betting_ledger.write_batch_terminal``."""

    from ledgers import money

    actual_return_d = money.to_decimal(actual_return, field_name="actual_return")
    payload: dict[str, Any] = {
        "settled_at_utc": settled_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "leg_results": leg_results,
        "actual_return": money.decimal_str(actual_return_d),
        "profit_loss": money.decimal_str(actual_return_d - total_stake_d),
        "settlement_method": betting_ledger.SETTLEMENT_MANUAL,
        "settlement_basis": "BOOKMAKER_OBSERVED",
    }
    if settled_at_utc is not None:
        payload["settled_at_resolution"] = "CAPTURE_TIME_UPPER_BOUND"
    return {
        "schema_version": betting_ledger.SCHEMA_VERSION,
        "event_type": betting_ledger.EVENT_SETTLED,
        "ticket_id": ticket_id,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "payload": payload,
    }


def run_import(
    raw_tickets: list[dict[str, Any]],
    *,
    currency: str,
    betting_ledger_path: Path,
    forecast_ledger_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Full in-memory pipeline over ``raw_tickets`` -- evaluates every
    ticket (building its PLACED event, and a SETTLED event when the
    ticket is a bookmaker-observed settlement, or collecting its full
    list of quarantine reasons), then preflights and (unless ``dry_run``)
    commits the accepted PLACED events as one atomic batch, followed by
    the accepted SETTLED events as a second atomic batch. A quarantined
    ticket never blocks the accepted tickets around it in either batch."""

    forecast_index = ForecastIndex.build(forecast_ledger_path)
    outcomes = [evaluate_ticket(t, currency=currency, forecast_index=forecast_index) for t in raw_tickets]

    accepted = [o for o in outcomes if o.status == "ACCEPTED"]
    quarantined = [o for o in outcomes if o.status == "QUARANTINED"]

    quarantine_reason_counts: dict[str, int] = {}
    quarantine_reason_matrix: dict[str, list[str]] = {}
    for o in quarantined:
        quarantine_reason_matrix[str(o.bet9ja_ticket_id)] = sorted(o.reasons)
        for reason in o.reasons:
            quarantine_reason_counts[reason] = quarantine_reason_counts.get(reason, 0) + 1

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "currency": currency,
        "dry_run": dry_run,
        "attempted": len(raw_tickets),
        "accepted": len(accepted),
        "quarantined": len(quarantined),
        "quarantine_reason_counts": dict(sorted(quarantine_reason_counts.items())),
        "quarantine_reason_matrix": dict(sorted(quarantine_reason_matrix.items())),
        "unlinked_leg_tickets": sum(1 for o in accepted if o.unlinked_leg_count > 0),
        "unlinked_leg_count": sum(o.unlinked_leg_count for o in accepted),
        "quarantined_ticket_ids": sorted(str(o.bet9ja_ticket_id) for o in quarantined),
        "settled_via_bookmaker_observed": sum(1 for o in accepted if o.settled_event is not None),
        "placed": {"attempted": 0, "appended": 0, "duplicate_skipped": 0, "conflicted": 0},
        "settled": {"attempted": 0, "appended": 0, "duplicate_skipped": 0, "conflicted": 0},
    }

    if not accepted:
        return report

    placed_events = [o.event for o in accepted]
    report["placed"]["attempted"] = len(placed_events)

    if dry_run:
        report["placed"]["accepted_would_write"] = len(placed_events)
        settled_events = [o.settled_event for o in accepted if o.settled_event is not None]
        report["settled"]["attempted"] = len(settled_events)
        report["settled"]["accepted_would_write"] = len(settled_events)
        return report

    try:
        placed_result = betting_ledger.write_batch_placed(betting_ledger_path, placed_events)
    except betting_ledger.BettingLedgerBatchConflictError as exc:
        report["placed"]["conflicted"] = len(exc.conflicts)
        report["placed"]["conflicting_ticket_ids"] = sorted({c["ticket_id"] for c in exc.conflicts})
        return report

    report["placed"]["appended"] = placed_result["appended"]
    report["placed"]["duplicate_skipped"] = placed_result["duplicate_skipped"]
    report["placed"]["total_ledger_records"] = placed_result["total_ledger_records"]

    settled_events = [o.settled_event for o in accepted if o.settled_event is not None]
    report["settled"]["attempted"] = len(settled_events)
    if not settled_events:
        return report

    try:
        settled_result = betting_ledger.write_batch_terminal(betting_ledger_path, settled_events)
    except betting_ledger.BettingLedgerTerminalBatchConflictError as exc:
        report["settled"]["conflicted"] = len(exc.conflicts)
        report["settled"]["conflicting_ticket_ids"] = sorted({c["ticket_id"] for c in exc.conflicts})
        report["settled"]["not_yet_placed_ticket_ids"] = sorted(exc.not_yet_placed)
        return report

    report["settled"]["appended"] = settled_result["appended"]
    report["settled"]["duplicate_skipped"] = settled_result["duplicate_skipped"]
    report["settled"]["total_ledger_records"] = settled_result["total_ledger_records"]
    return report


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcbf_calculator import-bet9ja-tickets",
        description="Import Bet9ja settled/open ticket captures into the betting ledger as PLACED/SETTLED events.",
    )
    parser.add_argument("inputs", type=Path, nargs="+", help="One or more Bet9ja settled/open ticket capture JSON files")
    parser.add_argument("--currency", required=True, help="Operator-supplied ISO-4217-style currency code, e.g. NGN")
    parser.add_argument("--betting-ledger-dir", type=Path, required=True, help="Directory containing betting-ledger.jsonl")
    parser.add_argument("--forecast-ledger-dir", type=Path, required=True, help="Directory containing forecast-ledger.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the import report into")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; write nothing to the ledger")
    args = parser.parse_args(argv)

    raw_tickets: list[dict[str, Any]] = []
    for input_path in args.inputs:
        envelope = json.loads(input_path.read_text(encoding="utf-8"))
        raw_tickets.extend(envelope.get("tickets") or [])

    try:
        report = run_import(
            raw_tickets,
            currency=args.currency,
            betting_ledger_path=args.betting_ledger_dir / betting_ledger.DEFAULT_FILENAME,
            forecast_ledger_path=args.forecast_ledger_dir / "forecast-ledger.jsonl",
            dry_run=args.dry_run,
        )
    except LedgerLockTimeoutError as exc:
        print(f"REJECTED: could not acquire ledger lock: {exc}")
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.output_dir / "ticket-import-report.json", report)

    placed, settled = report["placed"], report["settled"]
    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}{report['attempted']} attempted, {report['accepted']} accepted, "
        f"{report['quarantined']} quarantined, {report['unlinked_leg_count']} unlinked leg(s) across "
        f"{report['unlinked_leg_tickets']} ticket(s) -- "
        f"PLACED: {placed.get('appended', placed.get('accepted_would_write', 0))} written / {placed['attempted']} attempted "
        f"({placed['conflicted']} conflicted); "
        f"SETTLED (bookmaker-observed): {settled.get('appended', settled.get('accepted_would_write', 0))} written / "
        f"{settled['attempted']} attempted ({settled['conflicted']} conflicted) -> {args.output_dir}"
    )
    return 0 if placed["conflicted"] == 0 and settled["conflicted"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
