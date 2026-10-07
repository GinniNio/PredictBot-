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
FEED_EVERY = timedelta(minutes=10)      # at most one fetch per 10 minutes
PAGE = 100                              # the feed returns at most 100 events per request
MAX_PAGES = 30
EVENT_FIELDS = ("id", "slug", "title", "startTime", "live", "ended", "closed", "seriesSlug")
MARKET_FIELDS = ("id", "question", "slug", "outcomes", "outcomePrices", "volume", "liquidity", "bestBid", "bestAsk",
                 "lastTradePrice", "spread", "gameStartTime", "closed", "active", "acceptingOrders",
                 "sportsMarketType", "description", "updatedAt", "umaResolutionStatus", "closedTime")
RESULTS_EVERY = timedelta(minutes=30)   # at most one results fetch per 30 minutes
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
    opener = opener or (lambda url: urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "PredictBot (personal, read-only)"}), timeout=30))
    until = now + HORIZON
    events, requests, error = [], [], ""
    for page in range(MAX_PAGES):
        url = GAMMA_EVENTS + "?" + urllib.parse.urlencode({
            "tag_slug": "games", "closed": "false", "order": "startTime", "ascending": "true",
            "start_time_min": _stamp(now), "limit": PAGE, "offset": page * PAGE})
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
            if t:
                events.append(t)
        if late or len(batch) < PAGE:
            break
        if pause:
            time.sleep(pause)
    if not events and error:
        return {"status": "failed", "events": 0, "file": None, "error": error}
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
        return {"status": "failed", "events": 0, "file": None, "error": error}
    name = f"polymarket-results-{now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
    df.save_capture(name, json.dumps({"schema_version": "polymarket-gamma-results.v1", "fetched_at_utc": _stamp(now),
                                      "source": GAMMA_EVENTS, "requests": requests, "error": error,
                                      "note": "finished games the app logged; moneyline markets with resolution status",
                                      "events": events}, ensure_ascii=False).encode("utf-8"))
    return {"status": "fetched", "events": len(events), "file": name, "error": error}
