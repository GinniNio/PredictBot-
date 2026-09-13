"""Deterministic Bet9ja kickoff-time resolver (Africa/Lagos display -> UTC).

Bet9ja's own capture (``browser_extension/bet9ja_capture/parser.js``)
already refuses to guess a kickoff timestamp from ambiguous local text --
it leaves ``kickoff_utc: null`` and records ``date_heading_raw``
("Sat 12 Sep") and ``kickoff_raw`` ("16:00") verbatim instead, exactly so a
later, deliberate resolution step (this module) can derive one honestly,
with its own derivation recorded, rather than the capture guessing at
timezone/locale rules it has no confirmed way to verify from the DOM.

This module is that deliberate step, run only at ingestion time, never
inside the browser extension. The one fact it relies on that IS reliably
true regardless of locale: Bet9ja is a Nigerian sportsbook and its
displayed times are Nigeria's own local time, Africa/Lagos -- West Africa
Time, a FIXED UTC+1 offset with no daylight-saving transitions ever, so
"convert Lagos local to UTC" is exact and never ambiguous by itself. The
genuinely ambiguous part is the YEAR: ``date_heading_raw`` never carries
one. This module infers it only when exactly one candidate year, within a
window near the capture date, makes the declared weekday and the declared
day/month agree -- never by assuming "this year" or "next year" outright.
Any other outcome (zero or multiple matching candidates, an unparseable
weekday/date/time, a resolved kickoff at or before the capture itself) is
reported as an explicit resolution failure, never silently guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .errors import BET9JA_ALREADY_STARTED, BET9JA_KICKOFF_AMBIGUOUS

LAGOS_TZ = ZoneInfo("Africa/Lagos")
SOURCE_TIMEZONE_LABEL = "Africa/Lagos"

# Candidate years are only ever tried within this many days of the
# capture's own Lagos-local date -- generous enough for a schedule
# announced many months ahead, never open-ended. A candidate outside this
# window is not considered at all, even if its weekday happens to match.
CANDIDATE_WINDOW_BACK_DAYS = 1
CANDIDATE_WINDOW_FORWARD_DAYS = 400

_WEEKDAY_ABBREVIATIONS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
# Locale-independent by construction -- never delegated to strptime's own
# locale-sensitive month-name parsing, which would make this resolver's
# output depend on the host machine's configured locale.
_MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


@dataclass(frozen=True)
class KickoffResolution:
    """Outcome of resolving one fixture's kickoff time. ``ok`` is the only
    field a caller needs to branch on; the rest are either the admitted
    fixture's own audit fields (``ok=True``) or diagnostic-only
    (``ok=False``, never surfaced to an admitted fixture)."""

    ok: bool
    reason_code: str | None = None
    reason_detail: str | None = None
    kickoff_utc: str | None = None
    kickoff_resolution: str | None = None
    kickoff_source_timezone: str | None = None
    kickoff_source_date_raw: str | None = None
    kickoff_source_time_raw: str | None = None


def _parse_date_heading(date_heading_raw: str) -> tuple[int, int, int] | None:
    """Returns ``(weekday, day, month)`` (weekday: Monday=0..Sunday=6) or
    ``None`` if the text isn't shaped like Bet9ja's own confirmed
    "Weekday D Mon" heading (e.g. "Sat 12 Sep")."""
    parts = (date_heading_raw or "").strip().split()
    if len(parts) != 3:
        return None
    weekday_text, day_text, month_text = parts
    weekday = _WEEKDAY_ABBREVIATIONS.get(weekday_text[:3].lower())
    month = _MONTH_ABBREVIATIONS.get(month_text[:3].lower())
    if weekday is None or month is None:
        return None
    if not day_text.isdigit():
        return None
    day = int(day_text)
    if not 1 <= day <= 31:
        return None
    return weekday, day, month


def _parse_kickoff_time(kickoff_raw: str) -> tuple[int, int] | None:
    """Returns ``(hour, minute)`` or ``None`` if not a plain "HH:MM" 24h time."""
    text = (kickoff_raw or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def resolve_kickoff_utc(captured_at_utc: str, date_heading_raw: str | None, kickoff_raw: str | None) -> KickoffResolution:
    """Resolves one fixture's kickoff time -- see this module's own
    docstring for the full method. Never raises: every failure mode comes
    back as ``KickoffResolution(ok=False, reason_code=..., ...)``."""
    try:
        captured_dt_utc = datetime.fromisoformat((captured_at_utc or "").replace("Z", "+00:00"))
    except ValueError:
        return KickoffResolution(
            ok=False,
            reason_code=BET9JA_KICKOFF_AMBIGUOUS,
            reason_detail=f"captured_at_utc '{captured_at_utc}' is not a parseable ISO-8601 UTC timestamp.",
        )
    if captured_dt_utc.tzinfo is None:
        captured_dt_utc = captured_dt_utc.replace(tzinfo=timezone.utc)

    parsed_date = _parse_date_heading(date_heading_raw or "")
    parsed_time = _parse_kickoff_time(kickoff_raw or "")
    if parsed_date is None or parsed_time is None:
        return KickoffResolution(
            ok=False,
            reason_code=BET9JA_KICKOFF_AMBIGUOUS,
            reason_detail=(
                f"date_heading_raw '{date_heading_raw}' and/or kickoff_raw '{kickoff_raw}' "
                "are not shaped like Bet9ja's own confirmed \"Weekday D Mon\" / \"HH:MM\" display."
            ),
        )
    declared_weekday, day, month = parsed_date
    hour, minute = parsed_time

    capture_date_lagos = captured_dt_utc.astimezone(LAGOS_TZ).date()
    window_start = capture_date_lagos - timedelta(days=CANDIDATE_WINDOW_BACK_DAYS)
    window_end = capture_date_lagos + timedelta(days=CANDIDATE_WINDOW_FORWARD_DAYS)

    matching_candidates: list[date] = []
    for year in (capture_date_lagos.year - 1, capture_date_lagos.year, capture_date_lagos.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue  # e.g. Feb 30 -- not a real date for this year, never a guess
        if candidate.weekday() != declared_weekday:
            continue
        if not (window_start <= candidate <= window_end):
            continue
        matching_candidates.append(candidate)

    if len(matching_candidates) != 1:
        return KickoffResolution(
            ok=False,
            reason_code=BET9JA_KICKOFF_AMBIGUOUS,
            reason_detail=(
                f"{len(matching_candidates)} candidate year(s) near the capture date "
                f"({window_start.isoformat()}..{window_end.isoformat()}) match declared weekday/date "
                f"'{date_heading_raw}' -- exactly one is required to resolve without guessing."
            ),
        )
    resolved_date = matching_candidates[0]

    kickoff_local = datetime(
        resolved_date.year, resolved_date.month, resolved_date.day, hour, minute, tzinfo=LAGOS_TZ
    )
    kickoff_utc_dt = kickoff_local.astimezone(timezone.utc)

    if kickoff_utc_dt <= captured_dt_utc:
        return KickoffResolution(
            ok=False,
            reason_code=BET9JA_ALREADY_STARTED,
            reason_detail=(
                f"resolved kickoff {kickoff_utc_dt.isoformat()} is at or before "
                f"captured_at_utc {captured_dt_utc.isoformat()}."
            ),
        )

    return KickoffResolution(
        ok=True,
        kickoff_utc=kickoff_utc_dt.isoformat().replace("+00:00", "Z"),
        kickoff_resolution="DERIVED_FROM_BET9JA_LAGOS_DISPLAY",
        kickoff_source_timezone=SOURCE_TIMEZONE_LABEL,
        kickoff_source_date_raw=date_heading_raw,
        kickoff_source_time_raw=kickoff_raw,
    )
