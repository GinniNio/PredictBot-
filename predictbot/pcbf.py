"""PredictBot core: PCBF Mini v1.3 as pure, deterministic functions.

No file or network access here. Inputs are dicts and strings; outputs are
records defined in schemas.py. `now` is always passed in, never read from the
clock, so every result is reproducible.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from datetime import datetime, timedelta, timezone

from schemas import stable_id

PICK_EDGE = 0.03                      # PCBF Mini step 4
NOTIONAL_STAKE = 25                   # PCBF Mini step 5, NGN
GATE_PICKS = 200                      # PCBF Mini step 5
MAX_CAPTURE_BENCHMARK_GAP = timedelta(hours=6)
CLOCK_SKEW = timedelta(minutes=10)
BENCHMARK_SOURCES = ("pinnacle", "oddsportal", "oddschecker")

# ------------------------------------------------------------------ markets
# (sport, Bet9ja market_key) -> canonical market. Anything not listed is an
# unsupported market and is rejected rather than guessed.
THREE_WAY = "1X2_REGULATION"
MARKETS = {
    "1X2_REGULATION": {"outcomes": 3, "settlement": "regulation time, 3-way (draw possible)"},
    "MONEYLINE_INC_OT": {"outcomes": 2, "settlement": "winner incl. overtime"},
    "MONEYLINE_INC_EXTRA_INNINGS": {"outcomes": 2, "settlement": "winner incl. extra innings"},
    "MATCH_WINNER": {"outcomes": 2, "settlement": "match winner"},
}
MARKET_MAP = {
    ("soccer", "1x2"): THREE_WAY,
    ("ice_hockey", "1x2"): THREE_WAY, ("ice_hockey", "3way"): THREE_WAY,
    ("futsal", "1x2"): THREE_WAY, ("floorball", "1x2"): THREE_WAY, ("handball", "1x2"): THREE_WAY,
    ("basketball", "2_way"): "MONEYLINE_INC_OT",
    ("american_football", "1_-_2"): "MONEYLINE_INC_OT",
    ("baseball", "1-2_(inc__extra_inning)"): "MONEYLINE_INC_EXTRA_INNINGS",
    ("tennis", "match_winner"): "MATCH_WINNER", ("tennis", "2_way"): "MATCH_WINNER",
    ("volleyball", "2_way"): "MATCH_WINNER",
    ("cricket", "1_-_2"): "MATCH_WINNER", ("mma", "1_-_2"): "MATCH_WINNER",
    ("darts", "1-2"): "MATCH_WINNER", ("darts", "1_-_2"): "MATCH_WINNER",
}
# Preference order when a fixture carries several known main markets.
MAIN_KEYS = ["1x2", "3way", "2_way", "1_-_2", "1-2", "match_winner", "1-2_(inc__extra_inning)"]

BANNED_WORDS = re.compile(r"\b(russia|russian|belarus|iran)\b", re.I)
BANNED_COMPETITION_COUNTRIES = {"turkey", "turkiye", "bulgaria", "united arab emirates", "uae",
                                "russia", "belarus", "iran"}
YOUTH = re.compile(r"\b(u\s?-?\d{2}|under[\s-]?\d{2}|youth|juniors?|reserves?|amateur|"
                   r"semi[\s-]?pro|academy|primavera)\b", re.I)
YOUTH_SUFFIX = re.compile(r"\s(b|ii|iii|u\d{2})$", re.I)
VIRTUAL = re.compile(r"zoom|virtual|simulated|\bsrl\b|e-?soccer|cyber|esports?", re.I)


# ------------------------------------------------------------- rulebook math
# PCBF Mini v1.3 "Python block (run exactly)": copied verbatim.

def devig_prop(odds):
    i = [1/o for o in odds]; s = sum(i); return [x/s for x in i]

def devig_power(odds):
    i = [1/o for o in odds]; lo, hi = 1.0, 5.0
    for _ in range(200):
        k = (lo+hi)/2
        if sum(x**k for x in i) > 1: lo = k
        else: hi = k
    return [x**k for x in i]

def edge(bet9ja_odds, benchmark_odds, idx):
    """benchmark_odds: full market from ONE source, same order as Bet9ja (e.g. [home, draw, away]).
    idx: position of the selection. Returns (fair_odds, edge), using the more cautious devig."""
    p = min(devig_prop(benchmark_odds)[idx], devig_power(benchmark_odds)[idx])
    return round(1/p, 3), round(bet9ja_odds*p - 1, 4)


def fair_probability(benchmark_odds, idx) -> float:
    """The cautious fair probability edge() uses."""
    return min(devig_prop(benchmark_odds)[idx], devig_power(benchmark_odds)[idx])


def book_total(odds) -> float:
    return sum(1 / o for o in odds)


def clv(bookmaker_odds: float, closing_fair_odds: float) -> float:
    """PCBF Mini step 5: CLV = bet9ja_odds / closing_fair_odds - 1."""
    return round(bookmaker_odds / closing_fair_odds - 1, 4)


def tier_for(edge_value: float, cautions: list) -> str:
    return "PICK" if edge_value >= PICK_EDGE and not cautions else "WATCH"


# ----------------------------------------------------------------- parsing

def valid_decimal(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if 1.0 < v < 1000 else None


def parse_price(text: str) -> float | None:
    """Decimal, or fractional a/b converted to a/b + 1 (PCBF note)."""
    t = str(text).strip()
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        return valid_decimal(int(m.group(1)) / int(m.group(2)) + 1) if int(m.group(2)) else None
    return valid_decimal(t)


def parse_prices(text: str, n: int) -> list | None:
    """'2.30 / 3.40 / 3.10', '13/15 / 11/10' or '2.30, 3.40, 3.10' -> n decimals."""
    t = text.strip()
    parts = [p for p in re.split(r"\s*[,;]\s*|\s+/\s+|\s{2,}", t) if p]
    if len(parts) != n:
        parts = re.split(r"\s*/\s*", t)
        if len(parts) == 2 * n:   # all fractional without spaces: a/b/c/d
            parts = [f"{a}/{b}" for a, b in zip(parts[::2], parts[1::2])]
    vals = [parse_price(p) for p in parts]
    return vals if len(vals) == n and all(vals) else None


def utc(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if dt else ""


def parse_time(text) -> datetime | None:
    if not text:
        return None
    t = str(text).strip().replace(" ", "T")
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def norm_sport(s) -> str:
    return re.sub(r"[\s\-]+", "_", str(s or "").strip().lower()) or "unknown"


def payload_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------- captures -> candidates
# A candidate is one fixture's main market with all its outcomes; capture
# records (one per selection) are derived from it.

def _country_from_url(url) -> str:
    rest = str(url or "").split("/competition/", 1)
    bits = rest[1].split("/") if len(rest) == 2 else []
    return bits[1].replace("-", " ") if len(bits) > 1 else ""


def _soccer_kickoff(date_raw, time_raw, captured_at) -> datetime | None:
    """Old soccer capture shows 'Sat 3 Oct' + '13:00' in WAT (UTC+1)."""
    cap = parse_time(captured_at)
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})", date_raw or "")
    t = re.fullmatch(r"(\d{1,2}):(\d{2})", str(time_raw or "").strip())
    if not (cap and m and t):
        return None
    try:
        local = datetime.strptime(f"{m.group(1)} {m.group(2)} {cap.year} {t.group(1)}:{t.group(2)}",
                                  "%d %b %Y %H:%M")
    except ValueError:
        return None
    if local.month < cap.month - 6:
        local = local.replace(year=cap.year + 1)
    return local.replace(tzinfo=timezone(timedelta(hours=1))).astimezone(timezone.utc)


def candidates_from_capture(data: dict, raw_hash: str, file_name: str = "") -> list[dict]:
    """Normalise one capture file into candidates. Unreadable markets become
    candidates with a `problem` so they are reported, not silently dropped."""
    schema = str(data.get("schema_version", ""))
    out = []
    if schema.startswith("bet9ja-allsports-sport-walk"):
        sport = norm_sport(data.get("sport"))
        for res in data.get("results") or []:
            url = res.get("observed_source_url_raw") or res.get("source_url_after_click") or ""
            for fx in res.get("fixtures") or []:
                markets = {m.get("market_key"): m for m in fx.get("markets") or []}
                key = next((k for k in MAIN_KEYS if k in markets and (sport, k) in MARKET_MAP), None)
                c = {"sport": sport, "competition": res.get("competition_label") or "",
                     "country": _country_from_url(url), "source_url": url,
                     "event_id": f"bet9ja:{fx.get('fixture_id')}",
                     "home": fx.get("participant_1") or "", "away": fx.get("participant_2") or "",
                     "kickoff_utc": utc(parse_time(fx.get("kickoff_utc_derived"))),
                     "kickoff_basis": fx.get("kickoff_utc_basis") or "",
                     "captured_at_utc": utc(parse_time(res.get("captured_at_utc") or data.get("captured_at_utc"))),
                     "raw_payload_hash": raw_hash, "capture_file": file_name,
                     "market_key": key, "market": MARKET_MAP.get((sport, key)), "outcomes": [], "odds": [],
                     "problem": None}
                if not fx.get("fixture_id"):
                    c["problem"] = "no fixture_id"
                elif not key:
                    found = ", ".join(sorted(k for k in markets if k)) or "none"
                    c["problem"] = f"unsupported or missing main market (found: {found})"
                else:
                    sels = markets[key].get("selections") or []
                    c["odds"] = [s.get("odds") for s in sels]
                    c["outcomes"] = (["1", "X", "2"] if c["market"] == THREE_WAY
                                     else [c["home"] or "1", c["away"] or "2"])
                    if len(sels) != MARKETS[c["market"]]["outcomes"]:
                        c["problem"] = f"{len(sels)} selections for a {MARKETS[c['market']]['outcomes']}-way market"
                    elif not all(s.get("state") == "open" for s in sels):
                        c["problem"] = "locked price"
                    elif not all(valid_decimal(o) for o in c["odds"]):
                        c["problem"] = "invalid decimal odds"
                out.append(c)
    elif schema.startswith("bet9ja-soccer-session"):
        for fx in data.get("fixtures") or []:
            o = fx.get("offered_odds") or {}
            p = fx.get("participants") or {}
            odds = [o.get("H"), o.get("D"), o.get("A")]
            kickoff = parse_time(fx.get("kickoff_utc")) or _soccer_kickoff(
                fx.get("date_heading_raw"), fx.get("kickoff_raw"), fx.get("captured_at_utc"))
            c = {"sport": "soccer", "competition": fx.get("competition") or "", "country": fx.get("region") or "",
                 "source_url": "", "event_id": f"bet9ja:{fx.get('fixture_id')}",
                 "home": p.get("home") or "", "away": p.get("away") or "", "kickoff_utc": utc(kickoff),
                 "kickoff_basis": "explicit" if fx.get("kickoff_utc") else "DERIVED_ASSUMED_WAT_UTC+1",
                 "captured_at_utc": utc(parse_time(fx.get("captured_at_utc") or data.get("captured_at_utc"))),
                 "raw_payload_hash": raw_hash, "capture_file": file_name,
                 "market_key": "1x2", "market": THREE_WAY, "outcomes": ["1", "X", "2"], "odds": odds,
                 "problem": None}
            if fx.get("market_family") != "1X2":
                c["problem"] = f"unsupported market {fx.get('market_family')}"
            elif fx.get("status") != "PRE_MATCH":
                c["problem"] = f"status {fx.get('status')}"
            elif not all(valid_decimal(x) for x in odds):
                c["problem"] = "invalid decimal odds"
            out.append(c)
    return out


def merge_candidates(lists) -> tuple[dict, int]:
    """Union by event_id across captures; the newest capture of an event wins.
    Returns (event_id -> candidate, number of duplicates seen)."""
    merged, dups = {}, 0
    for lst in lists:
        for c in lst:
            prev = merged.get(c["event_id"])
            if prev is not None:
                dups += 1
                if prev["captured_at_utc"] >= c["captured_at_utc"]:
                    continue
            merged[c["event_id"]] = c
    return merged, dups


def screen(c: dict, now: datetime) -> str | None:
    """PCBF Mini step 1. Returns a rejection reason, or None if the candidate
    should go for benchmarking."""
    if c.get("problem"):
        return c["problem"]
    text = f"{c['competition']} {c['home']} {c['away']}"
    if VIRTUAL.search(text) or VIRTUAL.search(c.get("country", "")):
        return "virtual / simulated event"
    if BANNED_WORDS.search(text) or c.get("country", "").lower() in BANNED_COMPETITION_COUNTRIES:
        return "excluded country or competition"
    if YOUTH.search(f"{text} {c.get('country', '')}") or YOUTH_SUFFIX.search(c["home"]) or YOUTH_SUFFIX.search(c["away"]):
        return "youth / reserve / amateur"
    kickoff = parse_time(c.get("kickoff_utc"))
    if kickoff is None:
        return "kickoff unknown"
    if kickoff <= now:
        return "already started"
    return None


def capture_records(c: dict) -> list[dict]:
    """One capture record per selection of a screened-in candidate."""
    recs = []
    event_name = f"{c['home']} v {c['away']}"
    for i, (label, o) in enumerate(zip(c["outcomes"], c["odds"])):
        sel_id = f"{c['event_id']}:{c['market']}:{i}"
        recs.append({
            "capture_id": stable_id("cap", c["raw_payload_hash"], sel_id),
            "captured_at_utc": c["captured_at_utc"], "source": "bet9ja", "source_url": c["source_url"],
            "sport": c["sport"], "competition": c["competition"], "event_id": c["event_id"],
            "event_name": event_name, "kickoff_utc": c["kickoff_utc"], "market": c["market"],
            "selection_id": sel_id, "selection": selection_name(c, i), "odds_decimal": f"{float(o):.2f}",
            "raw_payload_hash": c["raw_payload_hash"], "market_key": c["market_key"],
            "market_outcomes": "/".join(c["outcomes"]), "market_odds": "/".join(f"{float(x):.2f}" for x in c["odds"]),
            "selection_index": str(i), "country": c.get("country", ""), "kickoff_basis": c.get("kickoff_basis", ""),
            "capture_file": c.get("capture_file", ""),
        })
    return recs


def selection_name(c: dict, i: int) -> str:
    if c["market"] == THREE_WAY:
        return [f"{c['home']} (home)", "Draw", f"{c['away']} (away)"][i]
    return c["outcomes"][i]


# ------------------------------------------------------------ research pack

PACK_RULES = """You are the research step of PCBF Mini v1.3. The app does all arithmetic: do NOT calculate edges or fair odds.
For each fixture, find the SAME market TODAY from ONE source, in this order: Pinnacle (direct or via a comparison site); else oddsportal average; else oddschecker best prices.
Same settlement as stated on the line. A page dated a previous day is stale: you may give it, but say so in the note.
Match prices by team/player NAME, never by position (US sites often list the away side first). Give prices in the SAME ORDER as the line.
Reply NONE for any fixture, league or participant with a named integrity concern. Add a short news note (injury, manager change, big price move) where relevant.
Never invent prices, URLs, times or news. Unknown = NONE.

Reply with ONLY these lines, one per fixture:
<code> | <source: pinnacle / oddsportal / oddschecker> | <page URL> | <time you read it, UTC, YYYY-MM-DDTHH:MMZ> | <prices in line order, decimal, separated by " / "> | <note or ->
<code> | NONE | <reason>"""


def build_pack(candidates: list[dict], now: datetime) -> dict:
    """candidates: screened-in, already sorted. Returns a pack dict with the
    text to paste into a chat and the code -> capture records mapping."""
    pack_id = "R-" + now.strftime("%Y%m%d-%H%M%S")
    entries, lines = {}, []
    for n, c in enumerate(candidates, 1):
        code = f"F{n}"
        recs = capture_records(c)
        entries[code] = {"captures": recs, "market": c["market"], "outcomes": c["outcomes"]}
        prices = " / ".join(r["odds_decimal"] for r in recs)
        where = f"{c['competition']} ({c['country']})" if c.get("country") else c["competition"]
        lines.append(f"{code} | {c['sport']} | {where} | {c['kickoff_utc']} | {c['home']} v {c['away']} | "
                     f"{MARKETS[c['market']]['settlement']} | order: {' / '.join(c['outcomes'])} | Bet9ja {prices}")
    text = (f"PREDICTBOT RESEARCH PACK {pack_id} (created {utc(now)}, {len(entries)} fixtures)\n\n"
            f"{PACK_RULES}\n\nFIXTURES\n" + "\n".join(lines))
    return {"pack_id": pack_id, "kind": "research", "created_utc": utc(now), "entries": entries, "text": text}


# ---------------------------------------------------------- reply -> records

LINE = re.compile(r"^\s*[`*>\-\s]*([FS]\d+)\s*[`*]*\s*\|(.*)$")


def reply_lines(reply: str) -> list[tuple[str, list[str], str]]:
    out = []
    for raw in reply.splitlines():
        m = LINE.match(raw)
        if m:
            out.append((m.group(1), [p.strip() for p in m.group(2).split("|")], raw.strip()))
    return out


def price_benchmark(captures: list[dict], fields: list[str], now: datetime, pack_id: str = "",
                    already_priced: set | None = None) -> list[dict]:
    """Validate one reply line for one fixture and return one selection record
    per outcome. Tiers: PICK / WATCH when valid, RESEARCH when there is no
    usable benchmark, REJECTED when the data fails validation."""
    already_priced = already_priced or set()
    priced_at = utc(now)

    def record(cap, tier, reason="", source="", ts="", bench="", fair="", edge_value="", cautions=(),
               note="", url=""):
        rid = stable_id("sel", cap["capture_id"], tier, source, ts, bench, reason)
        return {
            "selection_record_id": rid, "capture_id": cap["capture_id"], "priced_at_utc": priced_at,
            "benchmark_source": source, "benchmark_timestamp_utc": ts, "benchmark_odds": bench,
            "fair_odds": fair, "edge_pct": edge_value, "tier": tier,
            "validation_status": "VALID" if tier in ("PICK", "WATCH") else ("NO_BENCHMARK" if tier == "RESEARCH" else "INVALID"),
            "rejection_reason": reason, "selection_id": cap["selection_id"], "event_name": cap["event_name"],
            "sport": cap["sport"], "competition": cap["competition"], "kickoff_utc": cap["kickoff_utc"],
            "market": cap["market"], "selection": cap["selection"], "selection_index": cap["selection_index"],
            "bookmaker_odds": cap["odds_decimal"], "market_odds": cap["market_odds"], "benchmark_url": url,
            "caution_flags": "; ".join(cautions), "research_note": note, "pack_id": pack_id,
            "stake_notional": str(NOTIONAL_STAKE) if tier == "PICK" else "", "origin": "research-pack",
        }

    if fields and fields[0].upper() == "NONE":
        reason = fields[1] if len(fields) > 1 else "no benchmark found"
        return [record(c, "RESEARCH", reason=f"no benchmark: {reason}") for c in captures]
    if len(fields) < 4:
        return [record(c, "REJECTED", reason="reply line needs source | URL | time | prices") for c in captures]

    source, url, when, prices_txt = fields[0].lower(), fields[1], fields[2], fields[3]
    note = fields[4] if len(fields) > 4 and fields[4] not in ("", "-") else ""
    n = len(captures)
    ts = parse_time(when)
    prices = parse_prices(prices_txt, n)
    kickoff = parse_time(captures[0]["kickoff_utc"])
    captured = parse_time(captures[0]["captured_at_utc"])

    if not any(s in source for s in BENCHMARK_SOURCES):
        return [record(c, "RESEARCH", reason=f"benchmark source '{fields[0]}' not Pinnacle/oddsportal/oddschecker",
                       note=note, url=url) for c in captures]
    reason = None
    if not url.lower().startswith("http"):
        reason = "missing source URL"
    elif ts is None:
        reason = "missing or unreadable benchmark timestamp"
    elif ts > now + CLOCK_SKEW:
        reason = "benchmark timestamp is in the future"
    elif kickoff and ts >= kickoff:
        reason = "benchmark read after kickoff (in-play price)"
    elif prices is None:
        reason = f"need {n} valid decimal prices in order {' / '.join(c['selection'] for c in captures)}"
    elif any(c["selection_id"] in already_priced for c in captures):
        reason = "duplicate: this event/selection is already priced"
    if reason:
        return [record(c, "REJECTED", reason=reason, source=source, ts=utc(ts), note=note, url=url) for c in captures]

    cautions = []
    if utc(ts)[:10] != priced_at[:10]:
        cautions.append("STALE: benchmark not from today")
    if captured and abs(ts - captured) > MAX_CAPTURE_BENCHMARK_GAP:
        cautions.append("Bet9ja capture and benchmark more than 6h apart")
    if book_total(prices) < 1:
        cautions.append(f"benchmark book {book_total(prices) * 100:.1f}% < 100%: rulebook de-vig understates edge")
    bench = "/".join(f"{p:.3f}" for p in prices)
    out = []
    for i, cap in enumerate(captures):
        fair, e = edge(float(cap["odds_decimal"]), prices, i)
        hard = [x for x in cautions if not x.startswith("benchmark book")]
        out.append(record(cap, tier_for(e, hard), source=source, ts=utc(ts), bench=bench, fair=f"{fair:.3f}",
                          edge_value=f"{e:.4f}", cautions=cautions, note=note, url=url))
    return out


# --------------------------------------------------------------- settlement

def settlement_and_closing(sel: dict, result_pos: str, closing: dict | None, now: datetime,
                           source: str) -> tuple[dict | None, dict | None, str | None]:
    """result_pos: '1', 'X', '2' (position in market order) or 'VOID'.
    closing: {'source','url','time','odds':[...]} or None.
    Returns (settlement record, closing record, error)."""
    outcomes = ["1", "X", "2"] if sel["market"] == THREE_WAY else ["1", "2"]
    r = result_pos.strip().upper()
    if r not in outcomes + ["VOID"]:
        return None, None, f"result '{result_pos}' must be one of {outcomes + ['VOID']}"
    idx = int(sel.get("selection_index") or 0)
    result = "VOID" if r == "VOID" else ("WIN" if outcomes.index(r) == idx else "LOSE")
    stake = float(sel["stake_notional"]) if sel.get("stake_notional") else 0.0
    odds = float(sel["bookmaker_odds"])
    ret = {"WIN": stake * odds, "LOSE": 0.0, "VOID": stake}[result]
    settlement = {"selection_record_id": sel["selection_record_id"], "settled_at_utc": utc(now),
                  "result": result, "return_amount": f"{ret:.2f}", "settlement_source": source}
    closing_rec = None
    if closing:
        prices = closing["odds"]
        if len(prices) != len(outcomes) or not all(valid_decimal(p) for p in prices):
            return settlement, None, "closing prices do not match the market"
        fair, _ = edge(odds, prices, idx)
        closing_rec = {"selection_record_id": sel["selection_record_id"],
                       "captured_at_utc": closing.get("time") or utc(now), "source": closing["source"],
                       "closing_odds": "/".join(f"{p:.3f}" for p in prices), "closing_fair_odds": f"{fair:.3f}",
                       "clv_pct": f"{clv(odds, fair):.4f}", "closing_url": closing.get("url", "")}
    return settlement, closing_rec, None


def build_settle_pack(selections: list[dict], settled_ids: set, now: datetime,
                      after_kickoff=timedelta(hours=3)) -> dict | None:
    """Group unsettled PICK/WATCH/RESEARCH selections whose kickoff has passed."""
    groups = {}
    for s in selections:
        if s["tier"] == "REJECTED" or s["selection_record_id"] in settled_ids:
            continue
        k = parse_time(s.get("kickoff_utc"))
        if k and k + after_kickoff <= now:
            groups.setdefault(s["selection_id"].rsplit(":", 1)[0], []).append(s)
    if not groups:
        return None
    pack_id = "S-" + now.strftime("%Y%m%d-%H%M%S")
    entries, lines = {}, []
    for n, (key, rs) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0]["kickoff_utc"]), 1):
        s0 = rs[0]
        outcomes = ["1", "X", "2"] if s0["market"] == THREE_WAY else ["1", "2"]
        code = f"S{n}"
        entries[code] = {"selection_record_ids": [r["selection_record_id"] for r in rs]}
        names = s0["event_name"].split(" v ")
        order = "1 / X / 2" if len(outcomes) == 3 else f"1 = {names[0]} / 2 = {names[-1]}"
        sources = sorted({r["benchmark_source"] for r in rs if r.get("benchmark_source")}) or ["any listed source"]
        lines.append(f"{code} | {s0['sport']} | {s0['competition']} | {s0['kickoff_utc']} | {s0['event_name']} | "
                     f"{MARKETS[s0['market']]['settlement']} | order: {order} | closing source: {', '.join(sources)}")
    text = (f"PREDICTBOT SETTLEMENT PACK {pack_id} (created {utc(now)}, {len(entries)} fixtures)\n\n"
            "For each finished fixture give the final result for the stated settlement, and the CLOSING prices for the "
            "same market from the same source, read as close to kickoff as possible. Never invent results or prices. "
            "Unknown = NONE.\nResult = the winning position in the line's order (1, X or 2), or VOID.\n\n"
            "Reply with ONLY these lines:\n"
            "<code> | <result> | <closing source> | <page URL> | <closing time UTC, YYYY-MM-DDTHH:MMZ> | <closing prices in order, separated by \" / \">\n"
            "<code> | <result> | NONE\n<code> | NONE | <reason>\n\nFIXTURES\n" + "\n".join(lines))
    return {"pack_id": pack_id, "kind": "settle", "created_utc": utc(now), "entries": entries, "text": text}


# ---------------------------------------------------------------- reporting

def odds_band(o: float) -> str:
    for hi, label in ((1.5, "<1.50"), (2.0, "1.50-1.99"), (3.0, "2.00-2.99"), (5.0, "3.00-4.99")):
        if o < hi:
            return label
    return "5.00+"


def performance(selections: list[dict], settlements: list[dict], closings: list[dict],
                bets: list[dict] | None = None) -> dict:
    """Aggregate CLV, ROI, win rate and sample sizes, separately by tier, and
    PICKs by sport / market / odds band / benchmark source. Unit-stake ROI is
    used for every tier so PICK and WATCH are comparable; the notional-stake
    ROI applies to PICKs only (PCBF step 5)."""
    st = {s["selection_record_id"]: s for s in settlements}
    cl = {c["selection_record_id"]: c for c in closings}

    def summarise(rows):
        settled = [r for r in rows if r["selection_record_id"] in st]
        decided = [r for r in settled if st[r["selection_record_id"]]["result"] in ("WIN", "LOSE")]
        clvs = [float(cl[r["selection_record_id"]]["clv_pct"]) for r in rows if r["selection_record_id"] in cl]
        wins = [r for r in decided if st[r["selection_record_id"]]["result"] == "WIN"]
        unit_pnl = sum(float(r["bookmaker_odds"]) - 1 for r in wins) - (len(decided) - len(wins))
        return {
            "n": len(rows), "settled": len(settled), "with_clv": len(clvs),
            "mean_clv": statistics.fmean(clvs) if clvs else None,
            "median_clv": statistics.median(clvs) if clvs else None,
            "positive_clv": sum(1 for c in clvs if c > 0), "negative_clv": sum(1 for c in clvs if c < 0),
            "win_rate": len(wins) / len(decided) if decided else None,
            "unit_roi": unit_pnl / len(decided) if decided else None,
        }

    priced = [s for s in selections if s["tier"] in ("PICK", "WATCH")]
    out = {"by_tier": {t: summarise([s for s in selections if s["tier"] == t])
                       for t in ("PICK", "WATCH", "RESEARCH", "REJECTED")}}
    picks = [s for s in priced if s["tier"] == "PICK"]
    notional = [(float(s["stake_notional"]), float(st[s["selection_record_id"]]["return_amount"]))
                for s in picks if s["selection_record_id"] in st and s.get("stake_notional")]
    staked = sum(a for a, _ in notional)
    out["pick_notional_roi"] = (sum(b for _, b in notional) - staked) / staked if staked else None
    for dim, fn in (("sport", lambda s: s["sport"]), ("market", lambda s: s["market"]),
                    ("odds_band", lambda s: odds_band(float(s["bookmaker_odds"]))),
                    ("benchmark_source", lambda s: s.get("benchmark_source") or "?")):
        groups = {}
        for s in priced:
            groups.setdefault((s["tier"], fn(s)), []).append(s)
        out[f"by_{dim}"] = {f"{t} | {k}": summarise(v) for (t, k), v in sorted(groups.items())}
    p = out["by_tier"]["PICK"]
    out["gate"] = {
        "settled_picks": p["settled"], "needed": GATE_PICKS,
        "mean_clv_positive": (p["mean_clv"] or 0) > 0, "roi_positive": (out["pick_notional_roi"] or 0) > 0,
    }
    out["gate"]["unlocked"] = (p["settled"] >= GATE_PICKS and out["gate"]["mean_clv_positive"]
                               and out["gate"]["roi_positive"])
    # actual bets: stake-weighted ROI on settled bets
    bets = bets or []
    real = [(float(b["stake"]), float(b["bookmaker_odds"]), st[b["selection_record_id"]]["result"])
            for b in bets if b["selection_record_id"] in st]
    real_staked = sum(s for s, _, _ in real)
    real_return = sum(s * o if r == "WIN" else (s if r == "VOID" else 0) for s, o, r in real)
    out["bets"] = {"n": len(bets), "settled": len(real), "staked": real_staked,
                   "stake_weighted_roi": (real_return - real_staked) / real_staked if real_staked else None}
    return out


# ------------------------------------------------------- legacy ledger rows

PRICES_IN_NOTE = re.compile(r"(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?){1,2})")


def from_pcbf_row(row: dict, now: datetime) -> tuple[dict, dict]:
    """Convert one PCBF Mini v1.3 ledger row into (capture record, selection
    record). The edge is recomputed only when the full benchmark market is
    recoverable from the row; otherwise the row is REJECTED as unverifiable."""
    sel_text = row.get("selection", "")
    three_way = "3-way" in sel_text or row.get("sport") == "soccer"
    n = 3 if three_way else 2
    idx = 0 if "(home" in sel_text else (n - 1 if "(away" in sel_text else (1 if sel_text.lower().startswith("draw") else 0))
    market = THREE_WAY if three_way else ("MONEYLINE_INC_OT" if "OT" in sel_text else "MATCH_WINNER")
    event_id = f"pcbf:{row['record_id']}"
    cap = {
        "capture_id": stable_id("cap", "pcbf-ledger", row["record_id"]),
        "captured_at_utc": utc(parse_time(row.get("benchmark_time"))), "source": "bet9ja (from PCBF ledger)",
        "source_url": "", "sport": row.get("sport", ""), "competition": row.get("competition", ""),
        "event_id": event_id, "event_name": row.get("game", ""), "kickoff_utc": utc(parse_time(row.get("kickoff_utc"))),
        "market": market, "selection_id": f"{event_id}:{market}:{idx}", "selection": sel_text,
        "odds_decimal": f"{float(row['bet9ja_odds']):.2f}", "raw_payload_hash": "pcbf-ledger-row",
        "market_key": "", "market_outcomes": "", "market_odds": "", "selection_index": str(idx),
        "country": "", "kickoff_basis": "from PCBF ledger", "capture_file": "pcbf-ledger.csv",
    }
    bench = None
    m = PRICES_IN_NOTE.search(row.get("note", ""))
    if m:
        bench = parse_prices(m.group(1).replace("/", " / "), n)
    base = {"selection_record_id": stable_id("sel", "pcbf-ledger", row["record_id"]),
            "capture_id": cap["capture_id"], "priced_at_utc": utc(now),
            "benchmark_timestamp_utc": utc(parse_time(row.get("benchmark_time"))),
            "selection_id": cap["selection_id"], "event_name": cap["event_name"], "sport": cap["sport"],
            "competition": cap["competition"], "kickoff_utc": cap["kickoff_utc"], "market": market,
            "selection": sel_text, "selection_index": str(idx), "bookmaker_odds": cap["odds_decimal"],
            "market_odds": "", "benchmark_url": row.get("benchmark_url", ""), "research_note": row.get("note", ""),
            "pack_id": "", "origin": f"pcbf-ledger {row['record_id']}"}
    if not bench:
        rec = {**base, "benchmark_source": "", "benchmark_odds": "", "fair_odds": row.get("fair_odds", ""),
               "edge_pct": row.get("edge", ""), "tier": "REJECTED", "validation_status": "INVALID",
               "rejection_reason": "benchmark market not recorded: edge cannot be recomputed",
               "caution_flags": "", "stake_notional": ""}
        return cap, rec
    fair, e = edge(float(row["bet9ja_odds"]), bench, idx)
    url = row.get("benchmark_url", "").lower()
    source = next((s for s in BENCHMARK_SOURCES if s in url), "unknown")
    ts, kickoff = parse_time(row.get("benchmark_time")), parse_time(row.get("kickoff_utc"))
    cautions = []
    if ts and row.get("date") and utc(ts)[:10] != row["date"]:
        cautions.append("STALE: benchmark not from the pricing day")
    if ts and kickoff and ts >= kickoff:
        cautions.append("benchmark after kickoff")
    tier = tier_for(e, cautions)
    rec = {**base, "benchmark_source": source, "benchmark_odds": "/".join(f"{p:.3f}" for p in bench),
           "fair_odds": f"{fair:.3f}", "edge_pct": f"{e:.4f}", "tier": tier, "validation_status": "VALID",
           "rejection_reason": "", "caution_flags": "; ".join(cautions),
           "stake_notional": str(NOTIONAL_STAKE) if tier == "PICK" else ""}
    return cap, rec
