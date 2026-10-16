"""Polymarket's public data feed (gamma-api.polymarket.com): free, no key.

Replaces walking Polymarket's pages. Upcoming game events are fetched in
pages of 100, sorted by start time, from now to HORIZON ahead. Each fetch is
saved to the data folder as captures/polymarket-feed-<time>.json (moneyline
markets only, with their rules text), so every price stays traceable to the
response it came from. The only network code in the app; stdlib only.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import odds_sources
from storage import DataFolder

GAMMA_EVENTS = "https://gamma-api.polymarket.com/events"
HORIZON = timedelta(hours=36)
WINDOW = timedelta(hours=6)             # the horizon is fetched in slices this long (offset limit of 2,500)
FEED_EVERY = timedelta(minutes=10)      # at most one fetch per 10 minutes
PAGE = 100                              # the feed returns at most 100 events per request
MAX_PAGES = 30
EVENT_FIELDS = ("id", "slug", "title", "startTime", "live", "ended", "closed", "seriesSlug")
MARKET_FIELDS = ("id", "question", "slug", "outcomes", "outcomePrices", "volume", "liquidity", "bestBid", "bestAsk",
                 "lastTradePrice", "spread", "gameStartTime", "closed", "active", "acceptingOrders",
                 "sportsMarketType", "description", "updatedAt", "umaResolutionStatus", "closedTime")
RESULTS_EVERY = timedelta(minutes=30)   # at most one results fetch per 30 minutes
RETRY_AFTER = timedelta(minutes=10)     # after a failed fetch (offline?), wait before trying again
_FAILED: dict = {}                      # (data folder, kind) -> time of the last failed fetch


def _backing_off(df: DataFolder, kind: str, now: datetime) -> bool:
    t = _FAILED.get((str(df.root), kind))
    return t is not None and now - t < RETRY_AFTER
RESULTS_BATCH = 20                      # slugs per request


def _stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_fetch(df: DataFolder) -> datetime | None:
    files = sorted((df.root / "captures").glob("polymarket-feed-*.json"))
    if not files:
        return None
    try:
        return datetime.strptime(files[-1].stem[len("polymarket-feed-"):], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _trim(ev: dict) -> dict | None:
    """Keep what pricing and provenance need: the event's identity and
    timing, its sport tags, and its moneyline markets in full."""
    if not odds_sources.feed_sport(ev):
        return None
    markets = [{k: m.get(k) for k in MARKET_FIELDS if k in m} for m in ev.get("markets") or []
               if m.get("sportsMarketType") == "moneyline"]
    if not markets:
        return None
    out = {k: ev.get(k) for k in EVENT_FIELDS}
    out["sport"] = {"sport": (ev.get("sport") or {}).get("sport")} if isinstance(ev.get("sport"), dict) else None
    out["tags"] = [{"slug": t.get("slug")} for t in ev.get("tags") or [] if isinstance(t, dict)]
    out["markets"] = markets
    return out


def fetch_polymarket(df: DataFolder, now: datetime, force: bool = False, opener=None, pause: float = 0.3) -> dict:
    """Fetch upcoming Polymarket games and save them. Returns {'status':
    'fetched' | 'recent' | 'failed', 'events', 'file', 'error'}. A failure
    never stops the day: pricing carries on with what is already saved."""
    prev = last_fetch(df)
    if not force and prev and now - prev < FEED_EVERY:
        return {"status": "recent", "events": 0, "file": None, "error": "", "last": prev}
    if not force and _backing_off(df, "feed", now):
        return {"status": "recent", "events": 0, "file": None, "error": "", "last": prev}
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "PredictBot (personal, read-only)"}), timeout=30))
    until = now + HORIZON
    events, requests, error, seen = [], [], "", set()
    # The API refuses offsets past 2,500 (a busy weekend lists more games
    # than that within 36 h), so the horizon is walked in WINDOW slices, each
    # paged from offset 0. A game on a window edge is kept once.
    window_from = now
    while window_from < until and not error:
        window_to = min(window_from + WINDOW, until)
        for page in range(MAX_PAGES):
            url = GAMMA_EVENTS + "?" + urllib.parse.urlencode({
                "tag_slug": "games", "closed": "false", "order": "startTime", "ascending": "true",
                "start_time_min": _stamp(window_from), "start_time_max": _stamp(window_to), "limit": PAGE, "offset": page * PAGE})
            requests.append(url)
            try:
                with opener(url) as r:
                    batch = json.loads(r.read().decode("utf-8"))
            except Exception as exc:  # network, HTTP or JSON: report, keep what we have
                error = f"{type(exc).__name__}: {exc}"
                break
            if not isinstance(batch, list) or not batch:
                break
            late = False
            for ev in batch:
                start = odds_sources.parse_time(ev.get("startTime"))
                if start and start > until:
                    late = True
                    continue
                t = _trim(ev)
                if t and t.get("id") not in seen:
                    seen.add(t.get("id"))
                    events.append(t)
            if late or len(batch) < PAGE:
                break
            if pause:
                time.sleep(pause)
        window_from = window_to
    if not events and error:
        _FAILED[(str(df.root), "feed")] = now
        return {"status": "failed", "events": 0, "file": None, "error": error}
    _FAILED.pop((str(df.root), "feed"), None)
    name = f"polymarket-feed-{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    df.save_capture(name, json.dumps({"schema_version": "polymarket-gamma-feed.v1", "fetched_at_utc": _stamp(now),
                                      "source": GAMMA_EVENTS, "requests": requests, "error": error,
                                      "note": "game events with moneyline markets only; other markets dropped",
                                      "events": events}, ensure_ascii=False).encode("utf-8"))
    return {"status": "fetched", "events": len(events), "file": name, "error": error}


def last_results_fetch(df: DataFolder) -> datetime | None:
    files = sorted((df.root / "captures").glob("polymarket-results-*.json"))
    if not files:
        return None
    try:
        return datetime.strptime(files[-1].stem[len("polymarket-results-"):], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_polymarket_results(df: DataFolder, now: datetime, slugs, force: bool = False, opener=None,
                             pause: float = 0.3) -> dict:
    """Fetch the named Polymarket events (finished games the app logged) and
    save them as captures/polymarket-results-<time>.json, so every result
    traces back to the response it came from. Same shape as a feed file."""
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {"status": "nothing to fetch", "events": 0, "file": None, "error": ""}
    prev = last_results_fetch(df)
    if not force and prev and now - prev < RESULTS_EVERY:
        return {"status": "recent", "events": 0, "file": None, "error": "", "last": prev}
    if not force and _backing_off(df, "results", now):
        return {"status": "recent", "events": 0, "file": None, "error": "", "last": prev}
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "PredictBot (personal, read-only)"}), timeout=30))
    events, requests, error = [], [], ""
    for i in range(0, len(slugs), RESULTS_BATCH):
        url = GAMMA_EVENTS + "?" + urllib.parse.urlencode([("slug", s) for s in slugs[i:i + RESULTS_BATCH]])
        requests.append(url)
        try:
            with opener(url) as r:
                batch = json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # network, HTTP or JSON: report, keep what we have
            error = f"{type(exc).__name__}: {exc}"
            break
        for ev in batch if isinstance(batch, list) else []:
            t = _trim(ev)
            if t:
                t["score"], t["period"] = ev.get("score"), ev.get("period")
                events.append(t)
        if pause and i + RESULTS_BATCH < len(slugs):
            time.sleep(pause)
    if not events and error:
        _FAILED[(str(df.root), "results")] = now
        return {"status": "failed", "events": 0, "file": None, "error": error}
    _FAILED.pop((str(df.root), "results"), None)
    name = f"polymarket-results-{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    df.save_capture(name, json.dumps({"schema_version": "polymarket-gamma-results.v1", "fetched_at_utc": _stamp(now),
                                      "source": GAMMA_EVENTS, "requests": requests, "error": error,
                                      "note": "finished games the app logged; moneyline markets with resolution status",
                                      "events": events}, ensure_ascii=False).encode("utf-8"))
    return {"status": "fetched", "events": len(events), "file": name, "error": error}


# ------------------------------------------------------------ The Odds API
#
# the-odds-api.com: one request returns every upcoming game of one sport
# key (a league, mostly) in one bookmaker region, every bookmaker's prices.
# Pinnacle is in the EU region, so this is the rulebook's first-choice
# benchmark without a page load. The key lives in the data folder
# (the-odds-api.json: {"key": "...", "every_hours": 3, "sports": [...]}),
# never in git and never in a saved capture.

ODDS_API = "https://api.the-odds-api.com/v4"
ODDS_API_CONFIG = "the-odds-api.json"
ODDS_API_EVERY_HOURS = 3
ODDS_API_REGION = "eu"
# Sport keys fetched by default: the leagues Bet9ja and the API both list
# (all 22 checked against the API's sports list on 14 Oct 2026). 22 keys
# every 3 hours is ~5,300 requests a month (the $30 tier); on the free 500
# the fetcher paces itself to about once a day (odds_api_interval).
ODDS_API_DEFAULT_SPORTS = [
    "soccer_epl", "soccer_efl_champ", "soccer_spain_la_liga", "soccer_italy_serie_a", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_netherlands_eredivisie", "soccer_portugal_primeira_liga", "soccer_turkey_super_league",
    "soccer_uefa_champs_league", "soccer_uefa_europa_league", "soccer_uefa_europa_conference_league",
    "soccer_brazil_campeonato", "soccer_usa_mls", "soccer_spain_segunda_division", "soccer_italy_serie_b",
    "basketball_nba", "basketball_euroleague", "icehockey_nhl", "baseball_mlb", "americanfootball_nfl",
    "mma_mixed_martial_arts",
]


def odds_api_config(df: DataFolder) -> dict | None:
    """The operator's settings, or None when there is no key file."""
    try:
        cfg = json.loads((df.root / ODDS_API_CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cfg, dict) or not str(cfg.get("key") or "").strip():
        return None
    return {"key": str(cfg["key"]).strip(), "every_hours": float(cfg.get("every_hours") or ODDS_API_EVERY_HOURS),
            "sports": list(cfg.get("sports") or ODDS_API_DEFAULT_SPORTS), "region": str(cfg.get("region") or ODDS_API_REGION)}


def last_odds_api_fetch(df: DataFolder) -> tuple[datetime | None, int | None]:
    """(time of the last fetch, requests the API said were left then)."""
    files = sorted((df.root / "captures").glob("the-odds-api-*.json"))
    if not files:
        return None, None
    try:
        when = datetime.strptime(files[-1].stem[len("the-odds-api-"):], "%Y-%m-%dT%H-%M-%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, None
    try:
        left = json.loads(files[-1].read_text(encoding="utf-8")).get("requests_remaining")
        return when, int(left) if left is not None else None
    except (OSError, ValueError, TypeError):
        return when, None


def odds_api_interval(cfg: dict, now: datetime, remaining: int | None) -> float:
    """Hours between fetches: the configured interval, stretched so the
    requests left this month last until the month ends (the quota resets
    monthly; one fetch costs one request per sport key)."""
    if remaining is None:
        return cfg["every_hours"]
    nxt = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=32)).replace(day=1)
    hours_left = max((nxt - now).total_seconds() / 3600, 1.0)
    fetches_left = max(remaining // max(len(cfg["sports"]), 1), 0)
    if fetches_left == 0:
        return hours_left + 1                      # nothing left: wait for the reset
    return max(cfg["every_hours"], hours_left / fetches_left)


def fetch_odds_api(df: DataFolder, now: datetime, force: bool = False, opener=None, pause: float = 0.2) -> dict:
    """Fetch every configured sport key's h2h odds and save them as one
    capture. Returns {'status': 'off' | 'fetched' | 'recent' | 'failed',
    'events', 'requests', 'remaining', 'file', 'error'}. The saved file
    carries no URL (the key is in the query string)."""
    cfg = odds_api_config(df)
    if not cfg:
        return {"status": "off", "events": 0, "requests": 0, "remaining": None, "file": None, "error": ""}
    prev, left = last_odds_api_fetch(df)
    if not force and prev and now - prev < timedelta(hours=odds_api_interval(cfg, now, left)):
        return {"status": "recent", "events": 0, "requests": 0, "remaining": left, "file": None, "error": "", "last": prev}
    if not force and _backing_off(df, "oddsapi", now):
        return {"status": "recent", "events": 0, "requests": 0, "remaining": None, "file": None, "error": "", "last": prev}
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "PredictBot (personal, read-only)"}), timeout=30))
    events, errors, remaining, used = [], [], None, None
    for key in cfg["sports"]:
        url = f"{ODDS_API}/sports/{urllib.parse.quote(key)}/odds?" + urllib.parse.urlencode({
            "apiKey": cfg["key"], "regions": cfg["region"], "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"})
        try:
            with opener(url) as r:
                batch = json.loads(r.read().decode("utf-8"))
                headers = getattr(r, "headers", None) or {}
                remaining = headers.get("x-requests-remaining", remaining)
                used = headers.get("x-requests-used", used)
        except Exception as exc:  # one league failing never loses the others
            errors.append(f"{key}: {type(exc).__name__}: {str(exc).replace(cfg['key'], '***')}")
            if "401" in str(exc) or "429" in str(exc):          # bad key or quota: stop asking
                break
            continue
        if isinstance(batch, list):
            events.extend(_trim_odds_api(ev, key) for ev in batch if isinstance(ev, dict))
        if pause:
            time.sleep(pause)
    error = "; ".join(errors)
    if not events and error:
        _FAILED[(str(df.root), "oddsapi")] = now
        return {"status": "failed", "events": 0, "requests": len(cfg["sports"]), "remaining": remaining, "file": None, "error": error}
    _FAILED.pop((str(df.root), "oddsapi"), None)
    name = f"the-odds-api-{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    df.save_capture(name, json.dumps({"schema_version": "the-odds-api-h2h.v1", "fetched_at_utc": _stamp(now),
                                      "region": cfg["region"], "sports": cfg["sports"], "requests_remaining": remaining,
                                      "requests_used": used, "error": error, "events": events}).encode("utf-8"))
    return {"status": "fetched", "events": len(events), "requests": len(cfg["sports"]), "remaining": remaining,
            "file": name, "error": error}


def _trim_odds_api(ev: dict, sport_key: str) -> dict:
    """The event's identity, timing and every bookmaker's h2h prices."""
    books = []
    for b in ev.get("bookmakers") or []:
        h2h = next((m for m in b.get("markets") or [] if m.get("key") == "h2h"), None)
        if h2h:
            books.append({"key": b.get("key"), "title": b.get("title"), "last_update": b.get("last_update"),
                          "outcomes": [{"name": o.get("name"), "price": o.get("price")} for o in h2h.get("outcomes") or []]})
    return {"id": ev.get("id"), "sport_key": ev.get("sport_key") or sport_key, "sport_title": ev.get("sport_title"),
            "commence_time": ev.get("commence_time"), "home_team": ev.get("home_team"), "away_team": ev.get("away_team"),
            "bookmakers": books}
