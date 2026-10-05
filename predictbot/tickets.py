"""Bet9ja ticket captures (open / settled bets from the soccer extension).

Bet9ja does not show payouts for settled system tickets, so the return is
calculated from leg results and stake buckets. The formula matched Bet9ja's
own Max Win on all 30 open tickets in the 23 Sep 2026 capture.
"""

from __future__ import annotations

import itertools
from datetime import datetime
from decimal import Decimal, InvalidOperation


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
