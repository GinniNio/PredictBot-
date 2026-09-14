"""Bet9ja settled/open ticket capture -> betting-ledger PLACED-event import.

    Bet9ja settled-bets / open-bets JSON capture(s)
    -> per-ticket normalization + typed quarantine
    -> exact canonical forecast linkage (never substring matching)
    -> whole-batch preflight
    -> ledgers/betting_ledger.py::write_batch_placed

One command::

    python -m pcbf_calculator import-bet9ja-tickets FILE [FILE...] \\
        --currency NGN --betting-ledger-dir ledger_data \\
        --forecast-ledger-dir ledger_data --output-dir runs/ticket-import-session-id \\
        [--dry-run]

**Currency.** ``--currency`` is REQUIRED and is never inferred from odds,
stake formatting, or the bookmaker's own name -- Bet9ja's capture carries
no currency field at all, and every one of this module's own real-data
samples is priced in a way that would be silently, invisibly wrong if
guessed (e.g. assuming NGN for every operator). The operator-supplied
value is stored verbatim on every ticket this run produces.

**System stake structure.** A real Bet9ja SYSTEM ticket can have a
DIFFERENT unit stake per fold size (e.g. Singles at one stake, Doubles at
another) -- a single scalar ``unit_stake`` cannot represent that at all
(see ``ledgers/betting_ledger.py::_validate_stake_buckets``). This module
NEVER regex-parses ``system_table_raw`` (a lossy, unspaced text scrape --
see this module's own "Why no system_table_raw parsing" section below) to
recover that structure. Two paths only:

1. The raw ticket already carries a genuine, structured ``stake_buckets``
   field (once the browser capture is fixed to emit one directly --
   deliberately NOT built in this change, see "Deferred" below). Passed
   straight through to ``build_placed_event(stake_buckets=...)``.
2. The raw ticket's own (already structurally separate, non-
   ``system_table_raw``) ``unit_stake``/``total_stake`` fields are both
   present and numeric, AND exactly one fold size ``k`` (1..leg_count)
   satisfies ``C(leg_count, k) == total_stake / unit_stake`` (an integer
   combinatorial identity checked purely from already-structured numbers
   -- ``total_stake``, ``unit_stake``, and ``len(legs)`` -- never from
   ``system_table_raw`` text). Exactly one solution is required: zero or
   more than one is refused, never guessed (see ``_derive_fold_size``).

   Because ``C(n, k) == C(n, n-k)`` for every binomial coefficient, this
   almost always finds TWO candidates, not one, for any fold size other
   than a full accumulator (``k == leg_count``, whose only other
   candidate would be the invalid ``k == 0``) or the exact midpoint of an
   even leg count (``k == leg_count / 2``, self-paired). In practice this
   means path 2 resolves a straight full-legs accumulator unambiguously,
   but NOT a "Singles" or "Doubles" sub-selection carved out of a larger
   leg count -- those stay quarantined until path 1 exists, exactly the
   same conservative outcome as if this derivation were not attempted at
   all. This is a deliberate, structural safety property, not a
   limitation to work around: a wrong fold-size guess would silently
   misprice every settlement this ticket is ever scored against.

Any SYSTEM ticket satisfying neither path is quarantined with
``SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE`` -- recorded nowhere in the ledger,
never estimated.

**Why no system_table_raw parsing.** ``system_table_raw`` is
``.mybets__systable``'s flattened text with no reliable delimiters (e.g.
``"Singles535.00175.00"`` -- digit boundaries between the bet count and
the two money columns are genuinely ambiguous without the DOM's own
cell structure, which
``browser_extension/bet9ja_capture/SETTLED_BETS_REAL_PAGE_VALIDATION.md``'s
own Round-1 notes confirm is not yet confirmed by live inspection beyond
the container selector). An earlier regex-based reading of this exact
field, in this same working session, silently misparsed a bet count --
this module exists specifically so that mistake is structurally
impossible to repeat: nothing in this file ever opens ``system_table_raw``
for anything but verbatim, read-only preservation inside ``source_raw``.

**Forecast linkage.** Exact canonical identity only, via the SAME
resolution ``soccer_1x2_elo_v1``'s own ``forecast()`` and
``football_data_settlement.py``'s own settlement matching use
(``identity.resolve_competition``/``resolve_team`` against
``load_known_teams_by_league``/``load_team_alias_book``) -- never loose
substring matching. A leg's raw ``fixture_and_time_raw`` team names are
split on a single, unambiguous ``" - "`` separator (see
``_parse_fixture_and_time``); a leg whose competition does not resolve to
one of the five covered leagues, whose market is not ``1X2``, whose
team names cannot be exactly resolved, or whose match against the
forecast ledger is ambiguous (more than one candidate, or zero) is left
with ``forecast_id: None`` -- unlinked, never guessed, and NEVER a reason
to refuse recording the ticket itself: a real wager is recorded whether
or not it can be tied back to a forecast.

**Import safety.** The whole batch is preflighted -- and normalized,
quarantined, and linked -- entirely in memory before a single ledger byte
is written. ``run_import`` always returns a full report (accepted/
duplicate/conflict/quarantined/unlinked-leg counts); nothing is written
to the betting ledger if any accepted ticket would conflict with existing
ledger content (``betting_ledger.write_batch_placed``'s own all-or-nothing
guarantee) or if ``dry_run=True``. Every original Bet9ja ticket field is
preserved verbatim in the PLACED event's own ``source_raw``, alongside a
``source_raw_hash`` content hash, regardless of whether the ticket is
accepted or quarantined.

**Deferred, not built here (explicitly out of scope for this change).**
Fixing ``browser_extension/bet9ja_capture/settled_bets_parser.js`` (and
its open-bets counterpart) to emit a structured ``stake_buckets`` field
directly at capture time. Building it now would require guessing
``.mybets__systable``'s internal row/cell DOM structure, which has never
been confirmed by live inspection -- exactly the kind of unverified
guess this module's own "no system_table_raw parsing" rule exists to
rule out. Until that capture-side fix lands, every SYSTEM ticket that
cannot satisfy path 2 above stays quarantined, however many settled/open
tickets that is in practice.

This module never runs settlement (``ledgers/betting_ledger.py::
settle_computed`` is a fully separate, later step over the SAME
``bet9ja_ticket_id`` once its own SETTLED capture is imported), never
retrains or reclassifies any model, and never selects, recommends, or
sizes a stake -- it only ever records tickets an operator has already
placed, exactly as captured.
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

SCHEMA_VERSION_REPORT = "pcbf-bet9ja-ticket-import-report.v1"

MARKET_TYPE_1X2 = "1X2"
LAGOS_TZ = ZoneInfo("Africa/Lagos")

# --- Typed quarantine reasons (ticket-level: the whole ticket is set aside,
# never partially recorded) -----------------------------------------------

REASON_UNSUPPORTED_TICKET_TYPE = "TICKET_UNSUPPORTED_TYPE"
REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE = "SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE"
REASON_MISSING_MAX_RETURN = "TICKET_MISSING_MAX_RETURN"
REASON_PLACED_AT_UNPARSEABLE = "TICKET_PLACED_AT_UNPARSEABLE"
REASON_NO_LEGS = "TICKET_NO_LEGS"
REASON_INVALID_TOTAL_STAKE = "TICKET_INVALID_TOTAL_STAKE"
REASON_BUILD_FAILED = "TICKET_BUILD_FAILED"

QUARANTINE_REASON_CODES = frozenset(
    {
        REASON_UNSUPPORTED_TICKET_TYPE,
        REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE,
        REASON_MISSING_MAX_RETURN,
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
        from ledgers.forecast_ledger import current_state as forecast_current_state

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


@dataclass
class TicketOutcome:
    bet9ja_ticket_id: str | None
    status: str  # "ACCEPTED" | "QUARANTINED"
    reason: str | None = None
    event: dict[str, Any] | None = None
    unlinked_leg_count: int = 0


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def build_ticket_event(
    raw_ticket: dict[str, Any], *, currency: str, forecast_index: ForecastIndex
) -> TicketOutcome:
    """Builds one PLACED event from one raw Bet9ja ticket record, or
    returns a typed quarantine outcome -- never raises for a malformed
    ticket, and never partially records one (every check below runs
    before ``betting_ledger.build_placed_event`` is ever called)."""

    bet9ja_id = raw_ticket.get("bet9ja_ticket_id")
    source_raw_hash = _sha256_of(raw_ticket)

    def quarantined(reason: str) -> TicketOutcome:
        return TicketOutcome(bet9ja_ticket_id=bet9ja_id, status="QUARANTINED", reason=reason)

    ticket_type = raw_ticket.get("ticket_type_normalized")
    if ticket_type not in betting_ledger.TICKET_TYPES:
        return quarantined(REASON_UNSUPPORTED_TICKET_TYPE)

    raw_legs = raw_ticket.get("legs") or []
    if not raw_legs:
        return quarantined(REASON_NO_LEGS)

    total_stake_d = _to_decimal_or_none(raw_ticket.get("total_stake"))
    if total_stake_d is None or total_stake_d <= 0:
        return quarantined(REASON_INVALID_TOTAL_STAKE)

    max_return_raw = raw_ticket.get("potential_return")
    if max_return_raw is None:
        return quarantined(REASON_MISSING_MAX_RETURN)
    max_return = str(max_return_raw)

    placed_at_utc = parse_placed_at_utc(raw_ticket.get("placed_at_raw"))
    if placed_at_utc is None:
        return quarantined(REASON_PLACED_AT_UNPARSEABLE)

    unit_stake_arg: Any = None
    stake_buckets_arg: list[dict[str, Any]] | None = None
    system_sizes_arg: list[int] | None = None

    if ticket_type == "SYSTEM":
        if isinstance(raw_ticket.get("stake_buckets"), list) and raw_ticket["stake_buckets"]:
            # Path 1: a genuine, already-structured field -- never built
            # from system_table_raw anywhere in this codebase today, but
            # honored the moment a fixed capture emits one. Money fields
            # normalized through str() for the same reason as every other
            # money field in this module (see the comment in link_leg).
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
            if unit_stake_d is None:
                return quarantined(REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)
            fold_size = _derive_fold_size(len(raw_legs), unit_stake_d, total_stake_d)
            if fold_size is None:
                return quarantined(REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)
            unit_stake_arg = str(raw_ticket["unit_stake"])
            system_sizes_arg = [fold_size]
    else:
        unit_stake_d = _to_decimal_or_none(raw_ticket.get("unit_stake"))
        if unit_stake_d is None:
            return quarantined(REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)
        unit_stake_arg = str(raw_ticket["unit_stake"])

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

    try:
        event = betting_ledger.build_placed_event(
            ticket_type=ticket_type,
            max_return=max_return,
            currency=currency,
            legs=legs_payload,
            unit_stake=unit_stake_arg,
            stake_buckets=stake_buckets_arg,
            system_sizes=system_sizes_arg,
            placed_at_utc=placed_at_utc,
            external_ticket_ref=bet9ja_id,
            source_raw=raw_ticket,
            source_raw_hash=source_raw_hash,
        )
    except (ValueError, betting_ledger.money.MoneyValueError):
        return quarantined(REASON_BUILD_FAILED)

    return TicketOutcome(
        bet9ja_ticket_id=bet9ja_id, status="ACCEPTED", event=event, unlinked_leg_count=unlinked_count
    )


def run_import(
    raw_tickets: list[dict[str, Any]],
    *,
    currency: str,
    betting_ledger_path: Path,
    forecast_ledger_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Full in-memory pipeline over ``raw_tickets`` -- builds every
    ticket's PLACED event (or typed quarantine outcome), then preflights
    and (unless ``dry_run``) commits the accepted set as ONE atomic batch
    via ``betting_ledger.write_batch_placed``. Never writes anything --
    even the accepted set -- if that preflight finds a conflict; a
    quarantined ticket never blocks the accepted tickets around it."""

    forecast_index = ForecastIndex.build(forecast_ledger_path)
    outcomes = [build_ticket_event(t, currency=currency, forecast_index=forecast_index) for t in raw_tickets]

    accepted = [o for o in outcomes if o.status == "ACCEPTED"]
    quarantined = [o for o in outcomes if o.status == "QUARANTINED"]
    quarantine_reason_counts: dict[str, int] = {}
    for o in quarantined:
        quarantine_reason_counts[o.reason] = quarantine_reason_counts.get(o.reason, 0) + 1

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "currency": currency,
        "dry_run": dry_run,
        "attempted": len(raw_tickets),
        "accepted": len(accepted),
        "quarantined": len(quarantined),
        "quarantine_reason_counts": dict(sorted(quarantine_reason_counts.items())),
        "unlinked_leg_tickets": sum(1 for o in accepted if o.unlinked_leg_count > 0),
        "unlinked_leg_count": sum(o.unlinked_leg_count for o in accepted),
        "quarantined_ticket_ids": sorted(str(o.bet9ja_ticket_id) for o in quarantined),
        "appended": 0,
        "duplicate_skipped": 0,
        "conflicted": 0,
    }

    if not accepted or dry_run:
        return report

    events = [o.event for o in accepted]
    try:
        write_result = betting_ledger.write_batch_placed(betting_ledger_path, events)
    except betting_ledger.BettingLedgerBatchConflictError as exc:
        report["conflicted"] = len(exc.conflicts)
        report["conflicting_ticket_ids"] = sorted({c["ticket_id"] for c in exc.conflicts})
        return report

    report["appended"] = write_result["appended"]
    report["duplicate_skipped"] = write_result["duplicate_skipped"]
    report["total_ledger_records"] = write_result["total_ledger_records"]
    return report


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcbf_calculator import-bet9ja-tickets",
        description="Import Bet9ja settled/open ticket captures into the betting ledger as PLACED events.",
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

    print(
        f"{'DRY-RUN: ' if args.dry_run else ''}{report['attempted']} attempted, {report['accepted']} accepted "
        f"({report['appended']} appended, {report['duplicate_skipped']} duplicate), "
        f"{report['quarantined']} quarantined, {report['conflicted']} conflicted, "
        f"{report['unlinked_leg_count']} unlinked leg(s) across {report['unlinked_leg_tickets']} ticket(s) "
        f"-> {args.output_dir}"
    )
    return 0 if report["conflicted"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
