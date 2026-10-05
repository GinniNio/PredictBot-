"""Benchmark prices read from captured public odds pages (the
public-odds-capture-walker extension, `public-odds-walk-*.json`).

Polymarket (added as a benchmark source by the operator, 2026-10-05) embeds
each market as JSON in the page: outcomes, mid prices that sum to 1, volume,
best bid/ask and gameStartTime. Pure functions only; no file or network access.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import timedelta

from pcbf import parse_time, utc

MIN_POLYMARKET_VOLUME = 5000.0     # USD traded on the moneyline; thinner = RESEARCH
MAX_POLYMARKET_SPREAD = 0.04       # best ask - best bid; wider = RESEARCH
KICKOFF_TOLERANCE = timedelta(hours=3)

LEAGUE_SPORT = {"atp": "tennis", "wta": "tennis", "itf": "tennis", "challenger": "tennis",
                "modus": "darts", "pdc": "darts", "darts": "darts", "table-tennis": "table_tennis",
                "mlb": "baseball", "nba": "basketball", "nhl": "ice_hockey", "nfl": "american_football"}

MARKET_RE = re.compile(r'"outcomes":\[([^\]]*)\],"outcomePrices":\[([^\]]*)\],"volume":"([\d.]+)"')


def _field(ctx: str, name: str):
    m = re.search(rf'"{name}":"?([^",}}]+)"?', ctx)
    return m.group(1) if m else None


def polymarket_quotes(capture: dict) -> list[dict]:
    """Moneyline quotes from one captured Polymarket event page."""
    html = (capture.get("html") or "").replace('\\"', '"')
    url = capture.get("source_url") or ""
    m = re.search(r"/sports/([a-z0-9-]+)/", url)
    league = m.group(1) if m else ""
    sport = next((s for k, s in LEAGUE_SPORT.items() if league.startswith(k)), None)
    out = []
    for mm in MARKET_RE.finditer(html):
        ctx = html[max(0, mm.start() - 4000): mm.end() + 4000]
        if _field(ctx, "sportsMarketType") != "moneyline":
            continue
        names = re.findall(r'"([^"]+)"', mm.group(1))
        try:
            probs = [float(x) for x in re.findall(r'"([^"]+)"', mm.group(2))]
        except ValueError:
            continue
        if len(names) != len(probs) or len(names) != 2:
            continue
        bid, ask = _field(ctx, "bestBid"), _field(ctx, "bestAsk")
        out.append({
            "source": "polymarket", "url": url.split("?")[0], "sport": sport, "league": league,
            "captured_at_utc": utc(parse_time(capture.get("captured_at_utc"))),
            "start_utc": utc(parse_time(_field(ctx, "gameStartTime"))),
            "outcomes": names, "probs": probs, "volume": float(mm.group(3)),
            "spread": (float(ask) - float(bid)) if bid and ask else None,
            "closed": _field(ctx, "closed") == "true",
        })
        break   # the event's own moneyline comes first on its page
    return out


def quotes_from_walk(run: dict) -> list[dict]:
    if run.get("source_key") != "polymarket":
        return []
    quotes = []
    for cap in run.get("captures") or []:
        if cap.get("role") == "event" and cap.get("capture_status") == "CAPTURE_OK":
            quotes.extend(polymarket_quotes(cap))
    return quotes


def quote_problem(q: dict) -> str | None:
    """Why a quote cannot be a benchmark, or None if it can."""
    start, taken = parse_time(q["start_utc"]), parse_time(q["captured_at_utc"])
    if q["closed"]:
        return "market closed"
    if start and taken and taken >= start:
        return "captured after the event started (in-play price)"
    if q["volume"] < MIN_POLYMARKET_VOLUME:
        return f"thin market: ${q['volume']:,.0f} traded < ${MIN_POLYMARKET_VOLUME:,.0f}"
    if q["spread"] is not None and q["spread"] > MAX_POLYMARKET_SPREAD:
        return f"wide market: bid/ask spread {q['spread']:.2f} > {MAX_POLYMARKET_SPREAD:.2f}"
    if any(p <= 0 or p >= 1 for p in q["probs"]):
        return "price at 0 or 1"
    return None


# ------------------------------------------------------------------ matching

def _tokens(name: str) -> set:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z]+", s) if len(t) > 1}


def _surname(bet9ja_name: str) -> str:
    """Bet9ja lists players as 'Surname, Given' (team names have no comma)."""
    base = bet9ja_name.split(",")[0] if "," in bet9ja_name else bet9ja_name
    toks = sorted(_tokens(base), key=len, reverse=True)
    return toks[0] if toks else ""


def same_side(bet9ja_name: str, quote_name: str) -> bool:
    s = _surname(bet9ja_name)
    return bool(s) and s in _tokens(quote_name)


def match_quote(candidate: dict, quotes: list[dict]) -> tuple[dict, list[int]] | None:
    """Find the quote for a Bet9ja candidate (two-way markets only) and the
    order mapping quote outcome -> Bet9ja selection. Matches on sport, both
    surnames and a kickoff within 3 hours."""
    if len(candidate.get("outcomes") or []) != 2:
        return None
    kick = parse_time(candidate.get("kickoff_utc"))
    home, away = candidate["home"], candidate["away"]
    for q in quotes:
        if q["sport"] and q["sport"] != candidate["sport"]:
            continue
        start = parse_time(q["start_utc"])
        if kick and start and abs(kick - start) > KICKOFF_TOLERANCE:
            continue
        a, b = q["outcomes"]
        if same_side(home, a) and same_side(away, b):
            return q, [0, 1]
        if same_side(home, b) and same_side(away, a):
            return q, [1, 0]
    return None


def reply_fields(q: dict, order: list[int]) -> list[str]:
    """The same fields a chat reply line carries, so the core validates a
    captured quote exactly as it validates chat research."""
    prices = [1 / q["probs"][i] for i in order]
    mids = " / ".join("%.3f" % q["probs"][i] for i in order)
    spread = "" if q["spread"] is None else ", spread %.3f" % q["spread"]
    note = "polymarket mid %s, volume $%s%s" % (mids, format(round(q["volume"]), ","), spread)
    return [q["source"], q["url"], q["captured_at_utc"], " / ".join(f"{p:.4f}" for p in prices), note]
