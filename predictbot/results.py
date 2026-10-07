"""Final results from benchmark sources, read without a chat.

Pure parsing only; fetching is in feeds.py, settlement in workflow.py.

Polymarket: a resolved moneyline market prices the winner at 1 and the
loser at 0 (50-50 means cancelled or tied: not used). Soccer comes as three
Yes/No markets (home win, draw, away win) settled on 90 minutes plus
stoppage time, the same as Bet9ja's 1X2.

OddsPortal: the match page shows 'Final result 5:4 OT (0:2, 3:2, 1:0, 1:0)'
once a game is over. Bet9ja's 1X2 settles on regulation time, so after
overtime, extra time or penalties the regulation score is the sum of the
regulation periods. A retirement, walkover, award, cancellation or
abandonment is never settled automatically.

Both return a position in the source's own outcome order (the order of the
quote the game was matched to); workflow.py maps it to Bet9ja's order.
"""

from __future__ import annotations

import json
import re

import odds_sources

# Periods that make up regulation time, for sports settled 1X2 on regulation.
REG_PERIODS = {"soccer": 2, "futsal": 2, "handball": 2, "ice_hockey": 3, "floorball": 3}
NOT_COMPLETED = re.compile(r"ret|w\.?\s*o\b|walkover|award|canc|postp|abn|abandon|interr|susp", re.I)
FINAL = re.compile(r"Final\s+result\s*(\d+)\s*:\s*(\d+)(.{0,120})", re.I)
FLAG = re.compile(r"(?<![A-Za-z])(OT|SO|AET|ET|pen\.?|penalties|ret\.?|retired|w\.?\s*o\.?|walkover|award(?:ed|\.)?|"
                  r"canc(?:elled|eled|\.)?|postp(?:oned|\.)?|abn\.?|abandoned|interr(?:upted|\.)?)(?![A-Za-z])", re.I)
PERIOD = re.compile(r"(\d+)\s*:\s*(\d+)")


def _prices(m: dict):
    try:
        outs = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else list(m.get("outcomes") or [])
        prices = [float(x) for x in (json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str)
                                     else m.get("outcomePrices") or [])]
    except (KeyError, TypeError, ValueError):
        return None, None
    return outs, prices


def _resolved(m: dict) -> bool:
    return bool(m.get("closed")) and str(m.get("umaResolutionStatus") or "").lower() == "resolved"


def polymarket_winner(event: dict) -> tuple[str | None, str]:
    """Winning outcome name ('Draw' for a soccer draw), or None and why."""
    markets = [m for m in event.get("markets") or [] if m.get("sportsMarketType") == "moneyline"]
    for m in markets:
        outs, prices = _prices(m)
        if outs and len(outs) == 2 and outs != ["Yes", "No"]:
            if not _resolved(m):
                return None, "not resolved yet"
            if sorted(prices) == [0.0, 1.0]:
                return outs[prices.index(1.0)], ""
            return None, "resolved 50-50 (cancelled or tied): settle by hand"
    draw = next((m for m in markets if odds_sources.DRAW_Q.match(m.get("question") or "")), None)
    if draw:
        home, away = odds_sources.DRAW_Q.match(draw["question"]).groups()
        wins = {odds_sources.WIN_Q.match(m["question"]).group(1): m for m in markets
                if odds_sources.WIN_Q.match(m.get("question") or "")}
        if home not in wins or away not in wins:
            return None, "soccer markets incomplete"
        legs = [(home, wins[home]), ("Draw", draw), (away, wins[away])]
        if not all(_resolved(m) for _, m in legs):
            return None, "not resolved yet"
        yes = []
        for name, m in legs:
            outs, prices = _prices(m)
            if not outs or "Yes" not in outs or sorted(prices) != [0.0, 1.0]:
                return None, "resolved 50-50 (cancelled or tied): settle by hand"
            if prices[outs.index("Yes")] == 1.0:
                yes.append(name)
        return (yes[0], "") if len(yes) == 1 else (None, "resolution inconsistent")
    return None, "no moneyline market"


def parse_final(raw: str) -> dict | None:
    """'Final result 5:4 OT (0:2, 3:2, 1:0, 1:0)' -> score, flag, periods."""
    m = FINAL.search(" ".join(str(raw or "").split()))
    if not m:
        return None
    rest = m.group(3)
    paren = re.match(r"([^()]{0,25})\(([^)]*)\)", rest)
    head = paren.group(1) if paren else rest[:25]
    periods = [(int(a), int(b)) for a, b in PERIOD.findall(paren.group(2))] if paren else []
    flag = " ".join(f.group(0) for f in FLAG.finditer(head))
    return {"score": (int(m.group(1)), int(m.group(2))), "flag": flag, "periods": periods}


def oddsportal_result(raw: str, sport: str, n: int, variable_rule: bool) -> tuple[int | None, str]:
    """Winning position in OddsPortal's order (home, [draw,] away), or None
    and why. n: outcomes in the logged market (3 = 1X2 on regulation time)."""
    r = parse_final(raw)
    if not r:
        return None, "no final result on the page"
    if NOT_COMPLETED.search(r["flag"]):
        return None, f"not completed normally ({r['flag']}): settle by hand"
    if variable_rule and r["flag"]:
        return None, f"finished with '{r['flag']}', and this sport's settlement varies: settle by hand"
    a, b = r["score"]
    if n == 3:
        k = REG_PERIODS.get(sport)
        if r["flag"] or (k and len(r["periods"]) > k):
            if not k or len(r["periods"]) < k:
                return None, f"finished after '{r['flag'] or 'extra periods'}' without the regulation periods"
            a, b = sum(p[0] for p in r["periods"][:k]), sum(p[1] for p in r["periods"][:k])
        elif k and len(r["periods"]) == k and (sum(p[0] for p in r["periods"]), sum(p[1] for p in r["periods"])) != (a, b):
            return None, "periods do not add up to the final score"
        return (0 if a > b else 1 if a == b else 2), ""
    if a == b:
        return None, "tied: settle by hand"
    return (0 if a > b else 1), ""
