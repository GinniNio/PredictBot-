"""Benchmark prices read from captured public odds pages (the
public-odds-capture-walker extension, `public-odds-walk-*.json`).

Polymarket (added as a benchmark source by the operator, 2026-10-05) embeds
each market as JSON in the page: outcomes, displayed prices, volume,
liquidity, best bid/ask (of the first outcome) and gameStartTime. On every
captured moneyline so far the displayed price equals the bid/ask midpoint;
each quote records whether that held (`price_basis`).

Every source is normalised to one quote format:
    source, bookmakers, url, league, sport, outcomes, probs, odds,
    start_utc, quote_time_utc, captured_at_utc, price_basis, depth,
    volume, spread, closed, file
Pure functions only; no file or network access.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import timedelta

from pcbf import parse_time, utc

# Depth is judged on resting liquidity and the bid/ask spread. There is no
# traded-volume rule: pre-match volume builds near kickoff, and a $5,000
# volume floor rejected 597 of 627 feed quotes on 6 Oct (operator decision,
# 7 Oct 2026). Rules under evaluation.
MAX_POLYMARKET_SPREAD = 0.04       # best ask - best bid; wider = RESEARCH
MIN_POLYMARKET_LIQUIDITY = 5000.0  # USD resting liquidity on the thinnest leg; shallower or unknown = RESEARCH
SOURCE_PRIORITY = ("pinnacle", "oddsportal", "oddschecker", "polymarket")   # PCBF Mini order
KICKOFF_TOLERANCE = timedelta(hours=3)

LEAGUE_SPORT = {"soccer": "soccer", "fif": "soccer", "unl": "soccer", "ucl": "soccer", "uel": "soccer", "fifa": "soccer",
                "epl": "soccer", "es2": "soccer", "nor": "soccer","atp": "tennis", "wta": "tennis", "itf": "tennis", "challenger": "tennis",
                "modus": "darts", "pdc": "darts", "darts": "darts", "table-tennis": "table_tennis",
                "mlb": "baseball", "nba": "basketball", "nhl": "ice_hockey", "nfl": "american_football"}

MARKET_RE = re.compile(r'"outcomes":\[([^\]]*)\],"outcomePrices":\[([^\]]*)\],"volume":"([\d.]+)"')


def _field(ctx: str, name: str):
    m = re.search(rf'"{name}":"?([^",}}]+)"?', ctx)
    return m.group(1) if m else None


def _markets(html: str) -> list[dict]:
    """Every market object on the page. A market's own fields (bid/ask,
    type, start time) follow its outcome prices; its question precedes them."""
    found = list(MARKET_RE.finditer(html))
    out = []
    for k, m in enumerate(found):
        fwd = html[m.end(): found[k + 1].start() if k + 1 < len(found) else len(html)]
        back = html[found[k - 1].end() if k else 0: m.start()]
        try:
            probs = [float(x) for x in re.findall(r'"([^"]+)"', m.group(2))]
        except ValueError:
            continue
        questions = re.findall(r'"question":"([^"]+)"', back)
        liquidity = re.findall(r'"liquidity":"([\d.]+)"', back)
        bid, ask = _floats(_field(fwd, "bestBid"), _field(fwd, "bestAsk"))
        out.append({"outcomes": re.findall(r'"([^"]+)"', m.group(1)), "probs": probs, "volume": float(m.group(3)),
                    "type": _field(fwd, "sportsMarketType"), "start": _field(fwd, "gameStartTime"),
                    "closed": _field(fwd, "closed") == "true", "question": questions[-1] if questions else "",
                    "spread": (ask - bid) if bid is not None else None, "basis": _basis(probs, bid, ask),
                    "liquidity": float(liquidity[-1]) if liquidity else None})
    return out


def _floats(bid, ask):
    try:
        return (float(bid), float(ask)) if bid not in (None, "") and ask not in (None, "") else (None, None)
    except (TypeError, ValueError):
        return None, None


def _basis(probs, bid, ask) -> str:
    if bid is None:
        return "displayed price (no bid/ask)"
    if probs and abs(probs[0] - (bid + ask) / 2) <= 0.0051:
        return "midpoint (bid/ask)"
    return "displayed price (differs from bid/ask midpoint; may be last trade)"


WIN_Q = re.compile(r"^Will (.+?) win(?: on \d{4}-\d{2}-\d{2})?\?$")
DRAW_Q = re.compile(r"^Will (.+?) vs\.? (.+?) end in a draw\?$")


def polymarket_quotes(capture: dict) -> list[dict]:
    """The event's moneyline from one captured Polymarket page."""
    html = (capture.get("html") or "").replace('\\"', '"')
    url = capture.get("source_url") or ""
    m = re.search(r"/sports/([a-z0-9-]+)/", url)
    league = m.group(1) if m else ""
    sport = next((s for k, s in LEAGUE_SPORT.items() if league.startswith(k)), None)
    taken = utc(parse_time(capture.get("captured_at_utc")))
    base = {"source": "polymarket", "bookmakers": "Polymarket", "url": url.split("?")[0], "league": league,
            "captured_at_utc": taken, "quote_time_utc": taken}
    q = _moneyline(_markets(html), base, sport)
    return [q] if q else []


def _moneyline(markets: list[dict], base: dict, sport: str | None) -> dict | None:
    """A two-way market (players or teams as outcomes) or, for soccer, three
    Yes/No markets ('Will A win?', 'Will A vs. B end in a draw?', 'Will B
    win?') combined into home / draw / away using their Yes prices."""
    markets = [mk for mk in markets if mk["type"] == "moneyline" and len(mk["outcomes"]) == len(mk["probs"])]
    for mk in markets:
        if len(mk["outcomes"]) == 2 and mk["outcomes"] != ["Yes", "No"]:
            return _finish({**base, "sport": sport, "start_utc": utc(parse_time(mk["start"])), "outcomes": mk["outcomes"],
                            "probs": mk["probs"], "volume": mk["volume"], "spread": mk["spread"], "closed": mk["closed"],
                            "price_basis": mk["basis"], "depth": mk["liquidity"], "rules": mk.get("rules")})
    draw = next((mk for mk in markets if DRAW_Q.match(mk["question"])), None)
    if draw:
        home, away = DRAW_Q.match(draw["question"]).groups()
        wins = {WIN_Q.match(mk["question"]).group(1): mk for mk in markets if WIN_Q.match(mk["question"])}
        if home in wins and away in wins:
            legs = [wins[home], draw, wins[away]]
            spreads = [x["spread"] for x in legs if x["spread"] is not None]
            bases = {x["basis"] for x in legs}
            depths = [x["liquidity"] for x in legs]
            return _finish({**base, "sport": sport or "soccer", "start_utc": utc(parse_time(draw["start"])),
                            "outcomes": [home, "Draw", away], "probs": [x["probs"][0] for x in legs],
                            "volume": min(x["volume"] for x in legs), "spread": max(spreads) if spreads else None,
                            "closed": any(x["closed"] for x in legs),
                            "price_basis": bases.pop() if len(bases) == 1 else "mixed: " + "; ".join(sorted(bases)),
                            "depth": None if None in depths else min(depths), "rules": legs[0].get("rules")})
    return None


# ------------------------------------------------------- polymarket feed
# Polymarket's public data feed (gamma-api.polymarket.com, no key), saved by
# feeds.py as polymarket-feed-*.json: game events with their moneyline
# markets, start time, live / ended flags and each market's rules text.

FEED_TAG_SPORT = {"soccer": "soccer", "tennis": "tennis", "table-tennis": "table_tennis", "basketball": "basketball",
                  "hockey": "ice_hockey", "baseball": "baseball", "cricket": "cricket", "mma": "mma", "ufc": "mma",
                  "boxing": "boxing", "darts": "darts", "handball": "handball", "volleyball": "volleyball",
                  "nfl": "american_football", "american-football": "american_football", "cfb": "american_football"}


def feed_sport(event: dict) -> str | None:
    """Sport from the event's tags; None for esports and anything unmapped."""
    tags = {t.get("slug") for t in event.get("tags") or [] if isinstance(t, dict)}
    if "esports" in tags:
        return None
    return next((FEED_TAG_SPORT[t] for t in sorted(tags) if t in FEED_TAG_SPORT), None)


def _feed_market(m: dict) -> dict | None:
    try:
        outcomes = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else list(m.get("outcomes") or [])
        probs = [float(x) for x in (json.loads(m["outcomePrices"]) if isinstance(m.get("outcomePrices"), str)
                                    else m.get("outcomePrices") or [])]
    except (KeyError, TypeError, ValueError):
        return None
    bid, ask = _floats(m.get("bestBid"), m.get("bestAsk"))
    try:
        liquidity = float(m["liquidity"]) if m.get("liquidity") not in (None, "") else None
        volume = float(m.get("volume") or 0)
    except (TypeError, ValueError):
        liquidity, volume = None, 0.0
    return {"outcomes": outcomes, "probs": probs, "volume": volume, "type": m.get("sportsMarketType"),
            "start": m.get("gameStartTime"), "question": m.get("question") or "",
            "closed": bool(m.get("closed")) or m.get("acceptingOrders") is False,
            "spread": (ask - bid) if bid is not None else None, "basis": _basis(probs, bid, ask),
            "liquidity": liquidity, "rules": (m.get("description") or "")[:1500]}


def quotes_from_feed(feed: dict, file_name: str = "") -> list[dict]:
    """One quote per game event in a saved Polymarket feed file."""
    taken = utc(parse_time(feed.get("fetched_at_utc")))
    out = []
    for ev in feed.get("events") or []:
        sport = feed_sport(ev)
        if not sport:
            continue
        league = (ev.get("sport") or {}).get("sport") if isinstance(ev.get("sport"), dict) else ev.get("seriesSlug") or ""
        base = {"source": "polymarket", "bookmakers": "Polymarket", "league": league or "",
                "url": f"https://polymarket.com/sports/{league}/{ev.get('slug')}", "captured_at_utc": taken,
                "quote_time_utc": taken, "file": file_name, "live": bool(ev.get("live")), "ended": bool(ev.get("ended"))}
        q = _moneyline([x for x in map(_feed_market, ev.get("markets") or []) if x], base, sport)
        if q:
            out.append(q)
    return out


def _finish(q: dict) -> dict:
    """Fill the shared quote fields every source must carry."""
    q.setdefault("odds", [round(1 / p, 4) if p > 0 else None for p in q["probs"]])
    for k in ("bookmakers", "price_basis", "depth", "volume", "spread", "file", "league", "rules"):
        q.setdefault(k, None)
    q.setdefault("quote_time_utc", q.get("captured_at_utc"))
    return q


# ------------------------------------------------------------------ oddsportal

MIN_ODDSPORTAL_BOOKMAKERS = 5
OP_SPORT = {"football": "soccer", "tennis": "tennis", "basketball": "basketball", "hockey": "ice_hockey",
            "baseball": "baseball", "handball": "handball", "volleyball": "volleyball", "darts": "darts",
            "table-tennis": "table_tennis", "futsal": "futsal", "cricket": "cricket", "american-football": "american_football"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _visible_text(html: str) -> str:
    body = html[html.find("<body"):] if "<body" in html else html
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", body, flags=re.S)
    t = re.sub(r"<[^>]+>", "|", t)
    t = t.replace("&amp;", "&").replace("&nbsp;", " ").replace("&#x27;", "'")
    return re.sub(r"\s*\|[\s|]*", "|", t)


def oddsportal_quotes(capture: dict, utc_offset_minutes) -> list[dict]:
    """OddsPortal match page: the 'Bookmakers | 1 | X | 2 | Payout' table.
    The benchmark is the 'oddsportal average' of the rulebook, built from
    every complete bookmaker row (exchanges and Bet9ja excluded): each row is
    de-vigged proportionally, then the probabilities are averaged. `odds`
    keeps the plain average of the prices for display."""
    url = (capture.get("source_url") or "").split("#")[0]
    if "/h2h/" not in url or "inplay-odds" in url:
        return []
    text = _visible_text(capture.get("html") or "")
    head = re.search(r"\|Bookmakers\|((?:[12X]\|){2,3})Payout\|", text)
    title = re.match(r"(.+?) - (.+?) Odds, Predictions", capture.get("page_title") or "")
    if not head or not title:
        return []
    n = head.group(1).count("|")
    table = text[head.end(): min(i for i in (text.find("|My coupon", head.end()), text.find("Betting Exchanges", head.end()),
                                                len(text)) if i >= 0)]
    row = re.compile(r"([^|]+)\|(?:claim bonus\|)?" + r"(\d+\.\d+)\|" * n + r"([\d.]+)%")
    rows = [(m.group(1), [float(m.group(k)) for k in range(2, 2 + n)]) for m in row.finditer(table)]
    if not rows:
        return []
    # Bet9ja is never part of its own benchmark.
    rows = [(name.strip(), odds) for name, odds in rows if "bet9ja" not in name.lower()]
    if not rows:
        return []
    avg = [sum(r[1][k] for r in rows) / len(rows) for k in range(n)]
    # Each bookmaker's margin is removed separately, then the probability
    # vectors are averaged (operator's odds-source audit, 2026-10-06). A
    # margin is never formed from prices taken across different books.
    per_book = [[(1 / o) / sum(1 / x for x in odds) for o in odds] for _, odds in rows]
    fair = [sum(v[k] for v in per_book) / len(per_book) for k in range(n)]
    kick = None
    km = re.search(r"\|(\d{2}) ([A-Za-z]{3}) (\d{4}),\|(\d{2}):(\d{2})\|", text)
    if km and utc_offset_minutes is not None and km.group(2).lower() in MONTHS:
        from datetime import datetime, timezone, timedelta
        local = datetime(int(km.group(3)), MONTHS[km.group(2).lower()], int(km.group(1)), int(km.group(4)), int(km.group(5)))
        kick = (local - timedelta(minutes=utc_offset_minutes)).replace(tzinfo=timezone.utc)
    sm = re.search(r"oddsportal\.[a-z.]+/([a-z-]+)/", url)
    home, away = title.group(1).strip(), title.group(2).strip()
    outcomes = [home, "Draw", away] if n == 3 else [home, away]
    taken = utc(parse_time(capture.get("captured_at_utc")))
    base = {"url": url, "league": "", "sport": OP_SPORT.get(sm.group(1) if sm else "", None),
            "captured_at_utc": taken, "quote_time_utc": taken, "start_utc": utc(kick), "outcomes": outcomes,
            "volume": None, "spread": None, "closed": False}
    out = [_finish({**base, "source": "oddsportal", "probs": fair, "odds": [round(o, 3) for o in avg],
                    "bookmakers": "; ".join(r[0] for r in rows), "bookmaker_count": len(rows),
                    "price_basis": f"mean of {len(rows)} bookmakers' de-vigged probabilities", "depth": len(rows)})]
    pin = next((r for r in rows if r[0].lower().startswith("pinnacle")), None)
    if pin:   # PCBF: Pinnacle first, "direct or via a comparison site"
        out.append(_finish({**base, "source": "pinnacle", "probs": [1 / o for o in pin[1]], "odds": pin[1],
                            "bookmakers": pin[0], "bookmaker_count": 1,
                            "price_basis": "Pinnacle row on the OddsPortal page", "depth": 1}))
    return out


def quotes_from_walk(run: dict, file_name: str = "") -> list[dict]:
    key = run.get("source_key")
    quotes = []
    for cap in run.get("captures") or []:
        if cap.get("capture_status") != "CAPTURE_OK":
            continue
        if key == "polymarket" and cap.get("role") == "event":
            quotes.extend(polymarket_quotes(cap))
        elif key == "oddsportal":
            quotes.extend(oddsportal_quotes(cap, run.get("browser_utc_offset_minutes")))
    for q in quotes:
        q["file"] = file_name
    return quotes


def quote_key(q: dict) -> str:
    return f"{q['source']}|{q['url']}|{q['captured_at_utc']}"


def quote_problem(q: dict) -> str | None:
    """Why a quote cannot be a benchmark, or None if it can."""
    start, taken = parse_time(q["start_utc"]), parse_time(q["captured_at_utc"])
    if q["closed"] or q.get("ended"):
        return "market closed"
    if q.get("live"):
        return "captured after the event started (feed marks it live)"
    if q["source"] == "polymarket" and not start:
        return "event start time not readable: cannot rule out an in-play price"
    if start and taken and taken >= start:
        return "captured after the event started (in-play price)"
    if q["source"] == "oddsportal":
        if q["bookmaker_count"] < MIN_ODDSPORTAL_BOOKMAKERS:
            return f"only {q['bookmaker_count']} bookmakers listed (< {MIN_ODDSPORTAL_BOOKMAKERS})"
        return None
    if q["source"] != "polymarket":
        return None
    if q["spread"] is not None and q["spread"] > MAX_POLYMARKET_SPREAD:
        return f"wide market: bid/ask spread {q['spread']:.2f} > {MAX_POLYMARKET_SPREAD:.2f}"
    if q.get("depth") is None:
        return "market depth unknown: no liquidity figure"
    if q["depth"] < MIN_POLYMARKET_LIQUIDITY:
        return f"shallow market: ${q['depth']:,.0f} liquidity < ${MIN_POLYMARKET_LIQUIDITY:,.0f}"
    if any(p <= 0 or p >= 1 for p in q["probs"]):
        return "price at 0 or 1"
    return None


# ------------------------------------------------------------------ matching

ABBREVIATIONS = {"st": "saint", "ste": "sainte", "utd": "united", "intl": "international"}


def _tokens(name: str) -> set:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {ABBREVIATIONS.get(t, t) for t in re.split(r"[^a-z]+", s) if len(t) > 1}


def _surname(bet9ja_name: str) -> str:
    """Bet9ja lists players as 'Surname, Given' (team names have no comma)."""
    base = bet9ja_name.split(",")[0] if "," in bet9ja_name else bet9ja_name
    toks = sorted(_tokens(base), key=len, reverse=True)
    return toks[0] if toks else ""


TEAM_NOISE = {"fc", "cf", "sc", "ac", "afc", "club", "de", "the", "fk", "sk", "if", "bk", "cd", "ca", "sv"}
# Words shared by many unrelated teams: a match on these alone proves nothing.
GENERIC = {"united", "city", "town", "real", "sporting", "athletic", "atletico", "county", "rovers", "wanderers",
           "st", "saint", "san", "santa", "inter", "dynamo", "dinamo", "olympic", "racing", "national", "women",
           "utd", "sport", "sports", "team", "university", "academy", "hotspur", "albion", "north", "south",
           "west", "east", "new", "old"}


def side_match(bet9ja_name: str, quote_name: str) -> str | None:
    """'exact', 'partial' (needs review) or None.
    Players: Bet9ja 'Surname, Given'; the surname must appear in the quote
    name, and a given name there must agree. Teams: a shared distinctive
    word; exact when one name's words contain the other's."""
    q = _tokens(quote_name)
    if "," in bet9ja_name:
        s = _surname(bet9ja_name)
        if not s or s not in q:
            return None
        given = _tokens(bet9ja_name.split(",", 1)[1])
        other = q - _tokens(bet9ja_name.split(",")[0])
        if not other or not given or given & other or any(o[0] == g[0] for o in other for g in given if len(o) == 1):
            return "exact"
        return "partial"
    a, b = _tokens(bet9ja_name) - TEAM_NOISE, q - TEAM_NOISE
    if not (a & b) - GENERIC:
        return None
    return "exact" if a <= b or b <= a else "partial"


def same_side(bet9ja_name: str, quote_name: str) -> bool:
    return side_match(bet9ja_name, quote_name) is not None


def find_matches(candidate: dict, quotes: list[dict]) -> list[dict]:
    """Every quote that could be this Bet9ja fixture: same sport, same number
    of outcomes, both sides matched, kickoff within 3h (or, without a page
    start time, captured on Bet9ja's date or the day before). Each match is
    'exact' or 'review' with the reason."""
    n = len(candidate.get("outcomes") or [])
    if n not in (2, 3):
        return []
    kick = parse_time(candidate.get("kickoff_utc"))
    home, away = candidate["home"], candidate["away"]
    out = []
    for q in quotes:
        if q["sport"] and q["sport"] != candidate["sport"]:
            continue
        if len(q["outcomes"]) != n:
            continue
        start = parse_time(q["start_utc"])
        if kick and start and abs(kick - start) > KICKOFF_TOLERANCE:
            continue
        if kick and not start and q["captured_at_utc"][:10] not in (utc(kick)[:10], utc(kick - timedelta(days=1))[:10]):
            continue
        a, b = q["outcomes"][0], q["outcomes"][-1]
        for order, (x, y) in ((list(range(n)), (a, b)), ([1, 0] if n == 2 else [2, 1, 0], (b, a))):
            m1, m2 = side_match(home, x), side_match(away, y)
            if m1 and m2:
                why = []
                if "partial" in (m1, m2):
                    why.append(f"names only partly agree ({home} / {x}; {away} / {y})")
                if not start:
                    why.append("no kickoff time on the source page; matched by date")
                out.append({"quote": q, "order": order, "quality": "review" if why else "exact",
                            "why": "; ".join(why)})
                break
    return out


def choose_quote(candidate: dict, quotes: list[dict], decisions: dict | None = None) -> dict:
    """Pick the benchmark quote for a candidate.
    decisions: (event_id, quote_key) -> 'accept' / 'reject' from match reviews.
    Returns {'status': 'matched' | 'review' | 'none', 'match': ..., 'review': [...]}.
    Exact matches (or accepted reviews) are ranked by the PCBF source order,
    then newest capture. Two different events from the best source matching
    the same fixture is ambiguous and goes to review."""
    decisions = decisions or {}
    ev = candidate["event_id"]
    found = []
    for m in find_matches(candidate, quotes):
        d = decisions.get((ev, quote_key(m["quote"]))) or decisions.get((ev, event_key(m["quote"])))
        if d == "reject":
            continue
        if d == "accept":
            m = {**m, "quality": "exact", "why": "accepted by operator"}
        found.append(m)
    exact = [m for m in found if m["quality"] == "exact"]
    review = [m for m in found if m["quality"] == "review"]
    rank = lambda m: (SOURCE_PRIORITY.index(m["quote"]["source"]) if m["quote"]["source"] in SOURCE_PRIORITY else 9)
    if not exact:
        return {"status": "review" if review else "none", "match": None, "review": review}
    best = min(rank(m) for m in exact)
    if any(rank(m) < best for m in review):
        # A higher-priority source may have this game: decide that first,
        # rather than price from a lower source by default.
        return {"status": "review", "match": None, "review": [m for m in review if rank(m) < best]}
    top = [m for m in exact if rank(m) == best]
    if len({event_key(m["quote"]) for m in top}) > 1:
        return {"status": "review", "match": None,
                "review": [{**m, "why": "several different events match this fixture"} for m in top]}
    top.sort(key=lambda m: m["quote"]["captured_at_utc"], reverse=True)
    return {"status": "matched", "match": top[0], "review": [], "all": exact}


def event_key(q: dict) -> str:
    """The same event captured at different times shares this key; match
    review decisions are stored against it so they survive re-captures."""
    return f"{q['source']}|{q['url']}"


def match_quote(candidate: dict, quotes: list[dict]) -> tuple[dict, list[int]] | None:
    """The exact match only (kept for callers that need no review queue)."""
    r = choose_quote(candidate, quotes)
    return (r["match"]["quote"], r["match"]["order"]) if r["status"] == "matched" else None


def quote_meta(q: dict) -> dict:
    """Provenance stored on each selection priced from a captured quote."""
    return {"captured_utc": q["captured_at_utc"], "bookmakers": q.get("bookmakers") or "",
            "file": q.get("file") or "", "price_basis": q.get("price_basis") or "",
            "depth": "" if q.get("depth") is None else f"{q['depth']:.0f}"}


def reply_fields(q: dict, order: list[int]) -> list[str]:
    """The same fields a chat reply line carries, so the core validates a
    captured quote exactly as it validates chat research."""
    prices = [1 / q["probs"][i] for i in order]
    if q["source"] == "oddsportal":
        note = "oddsportal: mean of %d bookmakers' de-vigged probabilities%s" % (q["bookmaker_count"], "" if q["start_utc"] else
                                                           "; kickoff time not on page in UTC, matched by names + date")
        return [q["source"], q["url"], q["captured_at_utc"], " / ".join(f"{p:.4f}" for p in prices), note]
    if q["source"] == "pinnacle":
        return [q["source"], q["url"], q["captured_at_utc"], " / ".join(f"{p:.4f}" for p in prices),
                "Pinnacle row on OddsPortal page"]
    mids = " / ".join("%.3f" % q["probs"][i] for i in order)
    spread = "" if q["spread"] is None else ", spread %.3f" % q["spread"]
    note = "polymarket mid %s, volume $%s%s" % (mids, format(round(q["volume"]), ","), spread)
    return [q["source"], q["url"], q["captured_at_utc"], " / ".join(f"{p:.4f}" for p in prices), note]
