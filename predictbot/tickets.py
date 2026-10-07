"""Bet9ja ticket captures (open / settled bets from the soccer extension).

Bet9ja does not show payouts for settled system tickets, so the return is
calculated from leg results and stake buckets. The formula matched Bet9ja's
own Max Win on all 30 open tickets in the 23 Sep 2026 capture.
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

WAT = timezone(timedelta(hours=1))   # Bet9ja page time


def dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def parse_placed(raw):
    try:
        return datetime.strptime(raw.strip(), "%d %b %Y %H:%M")
    except (AttributeError, ValueError):
        return None


def system_return(ticket: dict, only_won: bool) -> Decimal | None:
    """Sum over every combination in every stake bucket of unit stake times
    the product of odds; only_won counts combinations whose legs all won
    (void legs at odds 1)."""
    legs, buckets = ticket["legs"], ticket["buckets"]
    if not buckets:
        if only_won and legs and all(l.get("leg_status") == "LOST" for l in legs):
            return Decimal(0)
        return None
    statuses = [l.get("leg_status") for l in legs]
    odds = [Decimal(1) if s == "VOID" else (dec(l.get("odds")) or Decimal(0)) for l, s in zip(legs, statuses)]
    total = Decimal(0)
    for b in buckets:
        k, unit = b.get("fold_size"), dec(b.get("unit_stake"))
        if not k or unit is None:
            return None
        for combo in itertools.combinations(range(len(legs)), k):
            if only_won and not all(statuses[i] in ("WON", "VOID") for i in combo):
                continue
            p = unit
            for i in combo:
                p *= odds[i]
            total += p
    return total.quantize(Decimal("0.01"))


def all_tickets(captures) -> list[dict]:
    """captures: (path, data) pairs. One row per ticket id; the newest
    capture of a ticket wins, so a ticket seen open then settled is settled."""
    tickets: dict = {}
    for _path, data in captures:
        schema = str(data.get("schema_version", ""))
        if not schema.startswith(("bet9ja-ticket-capture", "bet9ja-settled-bets")):
            continue
        for t in data.get("tickets") or []:
            tid = t.get("bet9ja_ticket_id")
            if not tid:
                continue
            row = {"id": tid, "placed_raw": t.get("placed_at_raw") or "", "placed": parse_placed(t.get("placed_at_raw")),
                   "status": t.get("ticket_status") or t.get("status") or "?",
                   "stake": dec(t.get("total_stake")) or Decimal(0), "buckets": t.get("stake_buckets") or [],
                   "legs": t.get("legs") or [],
                   "captured": t.get("captured_at_utc") or data.get("captured_at_utc") or ""}
            prev = tickets.get(tid)
            if prev is None or row["captured"] >= prev["captured"]:
                tickets[tid] = row
    for t in tickets.values():
        t["max_win"] = system_return(t, only_won=False)
        t["returned"] = system_return(t, only_won=True) if t["status"] in ("WON", "LOST") else None
    return sorted(tickets.values(), key=lambda r: (r["placed"] or datetime.min, r["id"]), reverse=True)


def summary(tickets: list[dict]) -> dict:
    settled = [t for t in tickets if t["returned"] is not None]
    staked = sum((t["stake"] for t in settled), Decimal(0))
    back = sum((t["returned"] for t in settled), Decimal(0))
    open_ = [t for t in tickets if t["status"] == "OPEN"]
    won = [t for t in settled if t["status"] == "WON"]
    return {"settled": len(settled), "staked": staked, "returned": back, "pnl": back - staked,
            "roi": (back - staked) / staked if staked else None, "open": len(open_),
            "open_staked": sum((t["stake"] for t in open_), Decimal(0)),
            "won_marked": len(won), "won_below_stake": sum(1 for t in won if t["returned"] < t["stake"]),
            "unpriced": sum(1 for t in tickets if t["status"] in ("WON", "LOST") and t["returned"] is None)}


# ------------------------------------------------- legs -> logged selections

KICK_SUFFIX = re.compile(r"(\d{2} [A-Za-z]{3} \d{2}:\d{2})\s*$")
MARKET_RESULT = re.compile(r"\(\s*market result:[^)]*\)", re.I)
KEY_ALIASES = {"moneyline": "2_way"}


def placed_utc(raw):
    """'07 Oct 2026 17:03' (Bet9ja page time, UTC+1) -> aware UTC datetime."""
    t = parse_placed(raw)
    return t.replace(tzinfo=WAT).astimezone(timezone.utc) if t else None


def norm_name(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def split_fixture(raw: str, placed: datetime | None):
    """'Home - Away07 Oct 21:00' -> (home, away, kickoff UTC or None).
    Settled legs carry no time."""
    raw = (raw or "").strip()
    kick = None
    m = KICK_SUFFIX.search(raw)
    if m:
        raw = raw[:m.start()].strip()
        if placed:
            try:
                t = datetime.strptime(f"{m.group(1)} {placed.astimezone(WAT).year}", "%d %b %H:%M %Y")
                kick = t.replace(tzinfo=WAT).astimezone(timezone.utc)
                if kick < placed - timedelta(days=60):          # placed in December, game in January
                    kick = kick.replace(year=kick.year + 1)
            except ValueError:
                kick = None
    if " - " not in raw:
        return None, None, kick
    home, away = raw.split(" - ", 1)
    return home.strip(), away.strip(), kick


def market_key(raw: str) -> str:
    """Ticket market label -> the capture's market key:
    '1-2 (Inc. Extra Inning)' -> '1-2_(inc__extra_inning)', '2 Way' -> '2_way'."""
    k = MARKET_RESULT.sub("", raw or "").strip().lower().replace(" ", "_").replace(".", "_")
    return KEY_ALIASES.get(k, k)


def side_index(selection: str, home: str, away: str, outcomes: int) -> int | None:
    s = norm_name(selection)
    if s and s == norm_name(home) or s == "1":
        return 0
    if s and s == norm_name(away) or s == "2":
        return outcomes - 1
    if outcomes == 3 and s in ("draw", "x"):
        return 1
    return None


def leg_result(leg: dict) -> str | None:
    s = str(leg.get("leg_status") or "").upper()
    if s in ("WON", "LOST"):
        return s
    if s in ("VOID", "VOIDED", "CANCELLED", "CANCELED", "REFUNDED"):
        return "VOID"
    return None


def leg_score(leg: dict):
    for f in ("market_result_raw", "result_raw"):
        m = re.fullmatch(r"\s*(\d+)\s*[:\-]\s*(\d+)\s*", str(leg.get(f) or ""))
        if m:
            return int(m.group(1)), int(m.group(2))
    m = re.search(r"market result:\s*(\d+)\s*:\s*(\d+)", str(leg.get("market_raw") or ""), re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def single_stake(ticket: dict) -> Decimal | None:
    """Stake on each leg as a single: the fold-1 bucket of a system ticket,
    or the whole stake of a one-leg ticket. None when the leg is only part
    of accumulators."""
    for b in ticket.get("buckets") or []:
        if b.get("fold_size") == 1:
            return dec(b.get("unit_stake"))
    if not ticket.get("buckets") and len(ticket.get("legs") or []) == 1:
        return ticket.get("stake") or None
    return None
