"""The daily loop, joining the pure core (pcbf.py) to the data folder
(storage.py). app.py calls only these functions, so the interface can be
replaced without touching model logic or records."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import odds_sources
import pcbf
import results
import schemas
import tickets
from storage import DataFolder


RECENT = timedelta(days=2)   # the daily views read captures from the last two days only


def load_candidates(df: DataFolder, now: datetime, extra_folders=(), since: datetime | None = None) -> dict:
    """Read recent captures, merge by event, screen each candidate."""
    lists, files = [], []
    since = since or now - RECENT
    for path, raw in df.capture_files(extra_folders, since):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            files.append((path.name, "?", "unreadable JSON"))
            continue
        cands = pcbf.candidates_from_capture(data, pcbf.payload_hash(raw), path.name) if isinstance(data, dict) else []
        schema = data.get("schema_version", "?") if isinstance(data, dict) else "?"
        files.append((path.name, schema, f"{len(cands)} fixtures" if cands else "no fixtures (bets or other)"))
        if cands:
            lists.append(cands)
    merged, dups = pcbf.merge_candidates(lists)
    accepted, rejected = [], []
    cutoff = pcbf.utc(since)
    for c in sorted(merged.values(), key=lambda c: (c["kickoff_utc"] or "9", c["sport"], c["competition"])):
        if c["captured_at_utc"] and c["captured_at_utc"] < cutoff:
            continue
        reason = pcbf.screen(c, now)
        (rejected if reason else accepted).append((c, reason))
    return {"files": files, "duplicates": dups, "accepted": [c for c, _ in accepted], "rejected": rejected}


PACK_SIZE = 40
RESEND_AFTER = timedelta(hours=24)


def recently_sent(df: DataFolder, now: datetime) -> set:
    """Events already sent for benchmarking in the last 24h, or already priced."""
    sent = {r["event_id"] for r in df.read_screening()
            if r["status"] == "sent for benchmark" and (pcbf.parse_time(r["screened_at_utc"]) or now) + RESEND_AFTER > now}
    priced = {s["selection_id"].rsplit(":", 2)[0] for s in df.read("selection") if s["tier"] in schemas.PRICED_TIERS}
    return sent | priced


def create_pack(df: DataFolder, now: datetime, sports: set | None = None, extra_folders=(),
                limit: int = PACK_SIZE) -> dict:
    """Next batch of up to `limit` candidates, earliest kickoff first, skipping
    events sent in the last 24h or already priced."""
    loaded = load_candidates(df, now, extra_folders)
    skip = recently_sent(df, now)
    pool = [c for c in loaded["accepted"] if (not sports or c["sport"] in sports) and c["event_id"] not in skip]
    chosen = pool[:limit]
    pack = pcbf.build_pack(chosen, now)
    df.save_pack(pack)
    known = {r["capture_id"] for r in df.read("capture")}
    new_caps = [r for e in pack["entries"].values() for r in e["captures"] if r["capture_id"] not in known]
    df.append("capture", new_caps)
    rows = []
    for c in chosen:
        rows.append(_screen_row(now, pack["pack_id"], c, "sent for benchmark", ""))
    for c, reason in loaded["rejected"]:
        if not sports or c["sport"] in sports:
            rows.append(_screen_row(now, pack["pack_id"], c, "not sent", reason))
    df.append_screening(rows)
    pack["remaining"] = len(pool) - len(chosen)
    return pack


def _screen_row(now, pack_id, c, status, reason):
    return {"screened_at_utc": pcbf.utc(now), "pack_id": pack_id, "event_id": c["event_id"], "sport": c["sport"],
            "competition": c["competition"], "event_name": f"{c['home']} v {c['away']}",
            "kickoff_utc": c["kickoff_utc"], "status": status, "reason": reason, "capture_file": c["capture_file"]}


def apply_reply(df: DataFolder, reply: str, now: datetime) -> dict:
    """Validate the chat's reply against the pack, recompute edges and append
    one selection record per outcome. Nothing is trusted from the chat except
    the benchmark inputs, which are stored so the result can be re-checked."""
    m = re.search(r"\b(R-\d{8}-\d{6})\b", reply)
    pack = df.load_pack("research", m.group(1) if m else None)
    if not pack:
        return {"error": "No research pack found. Create one first."}
    existing = df.read("selection")
    already = {s["selection_id"] for s in existing if s["tier"] in schemas.PRICED_TIERS}
    have_ids = {s["selection_record_id"] for s in existing}
    rules, pm_ok = confirmed_rules(df), pm_picks_allowed(df)
    answered, records, unknown, repeated = set(), [], [], []
    for code, fields, _raw in pcbf.reply_lines(reply):
        entry = pack["entries"].get(code)
        if not entry:
            unknown.append(code)
            continue
        if code in answered:
            continue
        answered.add(code)
        # A line already processed (on this laptop or the other) yields the same
        # record IDs: skip it rather than log it again as a duplicate.
        first_time = pcbf.price_benchmark(entry["captures"], fields, now, pack["pack_id"], set(), rules=rules,
                                          pm_picks_allowed=pm_ok)
        if all(r["selection_record_id"] in have_ids for r in first_time):
            repeated.append(code)
            continue
        recs = pcbf.price_benchmark(entry["captures"], fields, now, pack["pack_id"], already, rules=rules,
                                    pm_picks_allowed=pm_ok)
        recs = [r for r in recs if r["selection_record_id"] not in have_ids]
        have_ids |= {r["selection_record_id"] for r in recs}
        records.extend(recs)
        already |= {r["selection_id"] for r in recs if r["tier"] in schemas.PRICED_TIERS}
    df.append("selection", records)
    missing = [c for c in pack["entries"] if c not in answered]
    return {"pack_id": pack["pack_id"], "records": records, "unknown_codes": unknown, "unanswered": missing,
            "already_recorded": repeated}


def record_bet(df: DataFolder, selection_record_id: str, stake: str, odds: str, placed_at: str,
               ticket: str, currency: str = "NGN", note: str = "") -> dict:
    """An actual wager can only reference a selection already in the ledger."""
    sel = next((s for s in df.read("selection") if s["selection_record_id"] == selection_record_id), None)
    if sel is None:
        raise schemas_error("unknown selection_record_id: price and log the selection first")
    if sel["tier"] == "REJECTED":
        raise schemas_error("that selection was REJECTED; it cannot carry a bet")
    rec = {"bet_id": schemas.stable_id("bet", selection_record_id, placed_at, stake, odds, ticket),
           "selection_record_id": selection_record_id, "placed_at_utc": placed_at, "stake": stake,
           "bookmaker_odds": odds, "currency": currency, "ticket_reference": ticket, "note": note}
    if any(b["bet_id"] == rec["bet_id"] for b in df.read("bet")):
        raise schemas_error("this bet is already recorded")
    df.append("bet", [rec])
    return rec


def schemas_error(msg):
    from storage import InvalidRecord
    return InvalidRecord(msg)


def create_settle_pack(df: DataFolder, now: datetime) -> dict | None:
    settled = {s["selection_record_id"] for s in df.read("settlement")}
    pack = pcbf.build_settle_pack(df.read("selection"), settled, now)
    if pack:
        df.save_pack(pack)
    return pack


def apply_settlement(df: DataFolder, reply: str, now: datetime) -> dict:
    m = re.search(r"\b(S-\d{8}-\d{6})\b", reply)
    pack = df.load_pack("settle", m.group(1) if m else None)
    if not pack:
        return {"error": "No settlement pack found. Create one first."}
    sels = {s["selection_record_id"]: s for s in df.read("selection")}
    settled = {s["selection_record_id"] for s in df.read("settlement")}
    closed = {c["selection_record_id"] for c in df.read("closing")}
    new_st, new_cl, problems, skipped = [], [], [], []
    for code, fields, raw in pcbf.reply_lines(reply):
        entry = pack["entries"].get(code)
        if not entry:
            continue
        if not fields or fields[0].upper() == "NONE":
            skipped.append((code, fields[1] if len(fields) > 1 else ""))
            continue
        closing = None
        if len(fields) >= 5 and fields[1].upper() != "NONE":
            first = sels.get(entry["selection_record_ids"][0])
            n = 3 if first and first["market"] == pcbf.THREE_WAY else 2
            prices = pcbf.parse_prices(fields[4], n)
            ts = pcbf.parse_time(fields[3])
            if prices is None or ts is None:
                problems.append((code, "closing prices or time unreadable", raw))
            else:
                closing = {"source": fields[1].lower(), "url": fields[2], "time": pcbf.utc(ts), "odds": prices}
        for rid in entry["selection_record_ids"]:
            sel = sels.get(rid)
            if not sel or rid in settled:
                continue
            st, cl, err = pcbf.settlement_and_closing(sel, fields[0], closing, now,
                                                      "chat: " + (closing["source"] if closing else "result only"))
            if err and st is None:
                problems.append((code, err, raw))
                break
            if err:
                problems.append((code, err, raw))
            new_st.append(st)
            if cl and rid not in closed:
                new_cl.append(cl)
    df.append("settlement", new_st)
    df.append("closing", new_cl)
    return {"settled": len(new_st), "closing": len(new_cl), "problems": problems, "skipped": skipped}


def import_pcbf_ledger(df: DataFolder, csv_path, now: datetime) -> dict:
    """Bring rows from a PCBF Mini v1.3 ledger CSV in, once each."""
    have = {s["selection_record_id"] for s in df.read("selection")}
    known_caps = {c["capture_id"] for c in df.read("capture")}
    caps, sels = [], []
    with Path(csv_path).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("record_id"):
                continue
            cap, sel = pcbf.from_pcbf_row(row, now)
            if sel["selection_record_id"] in have:
                continue
            if cap["capture_id"] not in known_caps:
                caps.append(cap)
                known_caps.add(cap["capture_id"])
            sels.append(sel)
            have.add(sel["selection_record_id"])
    df.append("capture", caps)
    df.append("selection", sels)
    return {"imported": sels}


_QUOTE_CACHE: dict = {}   # (path, size, mtime) -> quotes parsed from that file


def _quotes_in(paths) -> list[dict]:
    """Quotes from walker captures and feed files. Walk files run to tens of
    MB, so each file is parsed once per app run and kept in memory; a file
    that changes on disk (size or time) is parsed again. The same file in
    Downloads and in captures/ counts once."""
    quotes, seen = [], set()
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        if (path.name, st.st_size) in seen:
            continue
        seen.add((path.name, st.st_size))
        key = (str(path), st.st_size, st.st_mtime_ns)
        if key not in _QUOTE_CACHE:
            try:
                data = json.loads(path.read_bytes().decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                data = None
            if not isinstance(data, dict):
                parsed = []
            elif path.name.startswith("polymarket-feed-"):
                parsed = odds_sources.quotes_from_feed(data, path.name)
            else:
                parsed = odds_sources.quotes_from_walk(data, path.name)
            _QUOTE_CACHE[key] = parsed
        quotes.extend(dict(q) for q in _QUOTE_CACHE[key])
    return quotes


def benchmark_quotes(df: DataFolder, extra_folders=(), now: datetime | None = None) -> list[dict]:
    """Quotes from walker captures and saved Polymarket feed fetches."""
    since = (now - RECENT) if now else None
    return _quotes_in(df.paths("public-odds-walk-*.json", extra_folders, since)
                      + df.paths("polymarket-feed-*.json", (), since))


def rules_text(quotes: list[dict]) -> dict:
    """(source, sport) -> the newest rules text a quote carries (Polymarket
    markets state their own resolution rules)."""
    out = {}
    for q in sorted(quotes, key=lambda q: q["captured_at_utc"]):
        if q.get("rules"):
            out[(q["source"], q["sport"])] = q["rules"]
    return out


def confirmed_rules(df: DataFolder) -> dict:
    """rule_key -> latest status recorded by the operator."""
    out = {}
    for r in sorted(df.read("rule"), key=lambda r: r["confirmed_at_utc"]):
        out[r["rule_key"]] = r["status"]
    return out


def match_decisions(df: DataFolder) -> dict:
    out = {}
    for r in sorted(df.read("match_review"), key=lambda r: r["decided_at_utc"]):
        out[(r["event_id"], r["quote_key"])] = r["decision"]
    return out


def latest_review(df: DataFolder) -> dict | None:
    rows = sorted(df.read("evidence_review"), key=lambda r: r["reviewed_at_utc"])
    return rows[-1] if rows else None


def pm_picks_allowed(df: DataFolder) -> bool:
    r = latest_review(df)
    return bool(r and r.get("polymarket_picks_allowed") == "yes")


def _event(sel_id: str) -> str:
    return sel_id.rsplit(":", 1)[0]


def day_status(df: DataFolder, now: datetime, extra_folders=(), quotes=None, loaded=None) -> dict:
    """Read-only picture of every captured fixture: screened out, priced,
    researched, waiting for a match review, or with no benchmark quote."""
    loaded = loaded or load_candidates(df, now, extra_folders)
    quotes = benchmark_quotes(df, extra_folders, now) if quotes is None else quotes
    decisions = match_decisions(df)
    sels = df.read("selection")
    priced = {_event(s["selection_id"]) for s in sels if s["tier"] in schemas.PRICED_TIERS}
    researched = {}
    for s in sels:
        if s["tier"] in ("RESEARCH", "REJECTED"):
            researched[_event(s["selection_id"])] = s["rejection_reason"]
    rows = []
    for c, reason in loaded["rejected"]:
        rows.append({"c": c, "state": "screened out", "reason": reason})
    for c in loaded["accepted"]:
        ev = f"{c['event_id']}:{c['market']}"
        if ev in priced:
            rows.append({"c": c, "state": "benchmarked", "reason": ""})
            continue
        if ev in researched:
            rows.append({"c": c, "state": "unresolved", "reason": researched[ev]})
            continue
        choice = odds_sources.choose_quote(c, quotes, decisions)
        if choice["status"] == "review":
            rows.append({"c": c, "state": "match review", "reason": choice["review"][0]["why"], "review": choice["review"]})
        elif choice["status"] == "matched":
            rows.append({"c": c, "state": "quote waiting", "reason": "matched quote not yet priced (reload Opportunities)"})
        else:
            rows.append({"c": c, "state": "no benchmark", "reason": "no benchmark quote captured"})
    return {"loaded": loaded, "rows": rows, "quotes": quotes}


def coverage(df: DataFolder, status: dict, now: datetime) -> list[dict]:
    """Per sport: fixtures captured, with usable prices, screened out by the
    rulebook (started, youth, excluded...), benchmarked, unresolved (no
    quote, review, no usable benchmark; with reasons) and on the shortlist."""
    sels = df.read("selection")
    shortlist = {}
    for s in sels:
        if s["tier"] in ("PICK", "PM_PAPER") and bettable(s, now):
            shortlist.setdefault(s["sport"], set()).add(_event(s["selection_id"]))
    out = {}
    today = day_start(now)
    for r in status["rows"]:
        k = pcbf.parse_time(r["c"].get("kickoff_utc"))
        if k is not None and k < today:
            continue   # an earlier day's game from an older capture: not part of today's picture
        sp = r["c"]["sport"]
        d = out.setdefault(sp, {"sport": sp, "captured": 0, "usable": 0, "screened_out": 0, "benchmarked": 0,
                                "unresolved": 0, "reasons": {}, "screen_reasons": {},
                                "shortlist": len(shortlist.get(sp, ()))})
        d["captured"] += 1
        if not r["c"].get("problem"):
            d["usable"] += 1
        if r["state"] == "benchmarked":
            d["benchmarked"] += 1
            continue
        key = r["reason"] if r["state"] in ("screened out", "unresolved") else r["state"]
        key = re.sub(r"\(found: .*\)", "", key).strip()
        key = re.sub(r"(thin|wide|shallow) market:.*", r"\1 market", key)   # group by kind, not by amount
        key = re.sub(r"market depth unknown:.*", "market depth unknown", key)
        key = re.sub(r"only \d+ bookmakers listed.*", "too few bookmakers", key)
        bucket = "screen_reasons" if r["state"] == "screened out" else "reasons"
        d["screened_out" if r["state"] == "screened out" else "unresolved"] += 1
        d[bucket][key] = d[bucket].get(key, 0) + 1
    return [out[k] for k in sorted(out)]


def parse_kick(s: dict):
    return pcbf.parse_time(s.get("kickoff_utc"))


def bettable(s: dict, now: datetime) -> bool:
    """Still at least 60 minutes before kickoff, judged at `now` (when the page
    is shown), not when the row was priced. Unknown kickoff stays listed."""
    k = parse_kick(s)
    return k is None or k - now >= pcbf.MIN_LEAD


def day_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def earlier_captures(status: dict, now: datetime) -> dict:
    """Fixtures loaded from captures taken before today (UTC): how many, how
    many of those games were played on an earlier day, and the oldest file."""
    cutoff = pcbf.utc(day_start(now))
    old = [r["c"] for r in status["rows"] if (r["c"].get("captured_at_utc") or cutoff) < cutoff]
    today = day_start(now)
    played = sum(1 for c in old if (pcbf.parse_time(c.get("kickoff_utc")) or today) < today)
    oldest = min(old, key=lambda c: c["captured_at_utc"]) if old else None
    return {"fixtures": len(old), "earlier_games": played,
            "oldest_utc": oldest["captured_at_utc"] if oldest else "",
            "oldest_file": (oldest.get("capture_file") or "") if oldest else ""}


def auto_benchmark(df: DataFolder, now: datetime, extra_folders=()) -> dict:
    """Price screened-in candidates against captured benchmark pages without
    a chat. Each matched quote goes through the same validation as a chat
    reply line. Fuzzy or ambiguous matches wait for review; in-play and
    closed quotes are skipped; thin, wide or shallow ones are logged as
    RESEARCH."""
    quotes = benchmark_quotes(df, extra_folders, now)
    loaded = load_candidates(df, now, extra_folders)
    decisions, rules, pm_ok = match_decisions(df), confirmed_rules(df), pm_picks_allowed(df)
    existing = df.read("selection")
    already = {s["selection_id"] for s in existing if s["tier"] in schemas.PRICED_TIERS}
    have_ids = {s["selection_record_id"] for s in existing}
    known_caps = {r["capture_id"] for r in df.read("capture")}
    caps, records, skipped, review = [], [], [], []
    for c in loaded["accepted"]:
        choice = odds_sources.choose_quote(c, quotes, decisions)
        name = f"{c['home']} v {c['away']}"
        if choice["status"] == "review":
            review.append((c, choice["review"]))
            continue
        if choice["status"] != "matched":
            continue
        q, order = choice["match"]["quote"], choice["match"]["order"]
        recs = pcbf.capture_records(c)
        problem = odds_sources.quote_problem(q)
        if problem and (problem.startswith("captured after") or problem == "market closed"):
            skipped.append((name, problem, q["url"]))
            continue
        if any(r["selection_id"] in already for r in recs):
            continue
        caps.extend(r for r in recs if r["capture_id"] not in known_caps)
        known_caps |= {r["capture_id"] for r in recs}
        fields = ["NONE", f"{q['source']} {problem}"] if problem else odds_sources.reply_fields(q, order)
        out = pcbf.price_benchmark(recs, fields, now, f"auto-{q['source']}", already, meta=odds_sources.quote_meta(q),
                                   rules=rules, pm_picks_allowed=pm_ok)
        out = [r for r in out if r["selection_record_id"] not in have_ids]   # same quote seen again: nothing new
        for r in out:
            r["origin"] = f"auto: {q['source']} capture"
        have_ids |= {r["selection_record_id"] for r in out}
        records.extend(out)
        already |= {r["selection_id"] for r in out if r["tier"] in schemas.PRICED_TIERS}
    df.append("capture", caps)
    df.append("selection", records)
    closes = snapshot_closes(df, now, quotes)
    return {"quotes": len(quotes), "records": records, "skipped": skipped, "review": review, "closes": closes}


def snapshot_closes(df: DataFolder, now: datetime, quotes: list[dict]) -> list[dict]:
    """For priced selections whose game has started, store the last quote
    from the same source page captured before kickoff, labelled as the
    closing price or as a pre-kickoff snapshot."""
    have = {c["selection_record_id"] for c in df.read("closing")}
    by_url = {}
    for q in quotes:
        by_url.setdefault(q["url"], []).append(q)
    new = []
    for s in df.read("selection"):
        if s["tier"] not in schemas.PRICED_TIERS or s["selection_record_id"] in have:
            continue
        kick, bench_t = parse_kick(s), pcbf.parse_time(s["benchmark_timestamp_utc"])
        if not kick or kick > now or s["benchmark_url"] not in by_url:
            continue
        src = pcbf.canonical_source(s["benchmark_source"])
        later = [q for q in by_url[s["benchmark_url"]]
                 if pcbf.canonical_source(q["source"]) == src            # an OddsPortal page also carries a Pinnacle row
                 and all(0 < p < 1 for p in q["probs"]) and not q.get("closed")
                 and (pcbf.parse_time(q["captured_at_utc"]) or kick) < kick
                 and (not bench_t or pcbf.parse_time(q["captured_at_utc"]) > bench_t)]
        if not later:
            continue
        q = max(later, key=lambda q: q["captured_at_utc"])
        home, _, away = s["event_name"].partition(" v ")
        cand = {"event_id": s["selection_id"], "sport": s["sport"], "kickoff_utc": s["kickoff_utc"],
                "home": home, "away": away, "outcomes": [""] * (3 if s["market"] == pcbf.THREE_WAY else 2)}
        m = odds_sources.find_matches(cand, [q])
        if not m:
            continue
        prices = [1 / q["probs"][i] for i in m[0]["order"]]
        rec, err = pcbf.closing_record(s, q["source"], q["url"], pcbf.parse_time(q["captured_at_utc"]), prices,
                                       f"auto: {q['source']} capture {q.get('file') or ''}".strip())
        if rec:
            new.append(rec)
            have.add(s["selection_record_id"])
    df.append("closing", new)
    return new


def recheck_selection(df: DataFolder, selection_record_id: str, odds: str, now: datetime, note: str = "") -> dict:
    sel = next((s for s in df.read("selection") if s["selection_record_id"] == selection_record_id), None)
    if sel is None or sel["tier"] not in schemas.PRICED_TIERS:
        raise schemas_error("only a priced selection (PICK, PM_PAPER or WATCH) can be rechecked")
    price = pcbf.valid_decimal(odds)
    if price is None:
        raise schemas_error("enter the Bet9ja price as decimal odds, e.g. 2.15")
    rec = pcbf.recheck(sel, price, now, note)
    if not any(r["recheck_id"] == rec["recheck_id"] for r in df.read("recheck")):   # a resubmitted form
        df.append("recheck", [rec])
    return rec


def latest_rechecks(df: DataFolder) -> dict:
    out = {}
    for r in sorted(df.read("recheck"), key=lambda r: r["rechecked_at_utc"]):
        out[r["selection_record_id"]] = r
    return out


def record_match_review(df: DataFolder, event_id: str, quote_key: str, decision: str, now: datetime,
                        event_name: str = "", quote_event: str = "", source: str = "") -> None:
    df.append("match_review", [{"event_id": event_id, "quote_key": quote_key, "decision": decision,
                                "decided_at_utc": pcbf.utc(now), "event_name": event_name,
                                "quote_event": quote_event, "source": source}])


def record_rule(df: DataFolder, key: str, status: str, now: datetime, note: str = "") -> None:
    df.append("rule", [{"rule_key": key, "status": status, "confirmed_at_utc": pcbf.utc(now), "note": note}])


def unconfirmed_rules(df: DataFolder) -> dict:
    """rule_key -> number of selections held at WATCH by it."""
    out = {}
    rules = confirmed_rules(df)
    for s in df.read("selection"):
        if s.get("settlement_check") == "unconfirmed" and s.get("benchmark_source"):
            k = pcbf.rule_key(s["benchmark_source"], s["sport"], s["market"])
            if k not in rules:
                out[k] = out.get(k, 0) + 1
    return out


def record_evidence_review(df: DataFolder, decision: str, summary: str, now: datetime, pm_allowed: bool,
                           reviewer: str = "") -> dict:
    if len(summary.strip()) < 40:
        raise schemas_error("write the review: at least a few sentences on coverage, forecast quality, "
                            "selection results and effort")
    perf = pcbf.performance(df.read("selection"), df.read("settlement"), df.read("closing"), df.read("bet"))
    prob = pcbf.probability_metrics(df.read("selection"), df.read("settlement"))
    snap = {"settled_picks": perf["gate"]["settled_picks"], "pick_mean_clv": perf["by_tier"]["PICK"]["mean_clv"],
            "pick_notional_roi": perf["pick_notional_roi"], "brier": prob["benchmark"]["brier"],
            "events_scored": prob["benchmark"]["events"]}
    rec = {"review_id": schemas.stable_id("rev", pcbf.utc(now), decision, summary), "reviewed_at_utc": pcbf.utc(now),
           "decision": decision, "summary": summary.strip(), "polymarket_picks_allowed": "yes" if pm_allowed else "no",
           "metrics_snapshot": json.dumps(snap), "reviewer": reviewer}
    if not any(r["review_id"] == rec["review_id"] for r in df.read("evidence_review")):
        df.append("evidence_review", [rec])
    return rec


WALKER_SPORT = {"soccer": "football", "ice_hockey": "hockey", "table_tennis": "table-tennis",
                "american_football": "american-football"}


def walker_targets(df: DataFolder, now: datetime) -> dict:
    """Read-only list for the odds walker: today's eligible Bet9ja fixtures
    that still have no benchmark, so it visits only games Bet9ja offers.
    Sports use the odds sites' URL names (soccer -> football)."""
    status = day_status(df, now)
    fixtures = [{"sport": WALKER_SPORT.get(r["c"]["sport"], r["c"]["sport"].replace("_", "-")),
                 "home": r["c"]["home"], "away": r["c"]["away"], "kickoff_utc": r["c"]["kickoff_utc"],
                 "competition": r["c"]["competition"]}
                for r in status["rows"] if r["state"] in ("no benchmark", "match review", "unresolved")]
    return {"schema_version": "predictbot-walker-targets.v1", "generated_at_utc": pcbf.utc(now),
            "fixtures": fixtures, "results": result_targets(df, now)}


# ------------------------------------------------------ Bet9ja ticket sync

TICKET_SCHEMAS = ("bet9ja-ticket-capture", "bet9ja-settled-bets")
MAX_LEAD = timedelta(days=7)        # a bet is linked to a game kicking off within a week of placing it
PRICE_SLACK = timedelta(minutes=2)  # ticket times are whole minutes


def ticket_captures(df: DataFolder, extra_folders=()) -> list:
    out = []
    for path, raw in df.ticket_files(extra_folders):
        try:
            d = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(d, dict) and str(d.get("schema_version", "")).startswith(TICKET_SCHEMAS):
            out.append((path, d))
    return out


def _result_pos(res: str, idx: int, n: int, score) -> tuple[str | None, str | None]:
    """Leg outcome -> result position for settlement_and_closing, or a reason."""
    outcomes = ["1", "X", "2"] if n == 3 else ["1", "2"]
    if res == "VOID":
        return "VOID", None
    if res == "WON":
        return outcomes[idx], None
    if n == 2:
        return outcomes[1 - idx], None
    if not score:
        return None, "lost 1X2 leg without a score"
    pos = "1" if score[0] > score[1] else ("X" if score[0] == score[1] else "2")
    if pos == outcomes[idx]:
        return None, f"lost leg but score {score[0]}:{score[1]} says it won"
    return pos, None


def _bet_rank(s: dict, placed, now):
    """Prefer a priced row; then the latest priced before the bet, else the earliest after."""
    p = pcbf.parse_time(s["priced_at_utc"]) or now
    before = placed is None or p <= placed
    return (s["tier"] not in schemas.PRICED_TIERS, not before, -p.timestamp() if before else p.timestamp())


def sync_tickets(df: DataFolder, now: datetime, extra_folders=(), captures=None, write: bool = True) -> dict:
    """Link legs of captured Bet9ja tickets to logged selections and settle
    from settled legs, so nothing is entered by hand. A leg counts as a bet
    only when it is staked as a single (the rulebook is singles only); legs
    that are only in accumulators are reported, not recorded. Idempotent.
    write=False reports what would be linked without recording it."""
    tks = tickets.all_tickets(ticket_captures(df, extra_folders) if captures is None else captures)
    sels = [s for s in df.read("selection") if s["tier"] != "REJECTED"]
    by_names: dict = {}
    for s in sels:
        home, _, away = (s.get("event_name") or "").partition(" v ")
        by_names.setdefault((tickets.norm_name(home), tickets.norm_name(away)), []).append(s)
    bets = df.read("bet")
    linked = {(b["ticket_reference"], b["selection_record_id"]) for b in bets}
    settled = {s["selection_record_id"] for s in df.read("settlement")}
    new_bets, new_st, legs, problems = [], [], [], []
    for t in tks:
        placed = tickets.placed_utc(t["placed_raw"])
        single = tickets.single_stake(t)
        for leg in t["legs"]:
            home, away, kick = tickets.split_fixture(leg.get("fixture_and_time_raw"), placed)
            row = {"ticket": t["id"], "placed": pcbf.utc(placed), "game": f"{home} v {away}" if home else
                   leg.get("fixture_and_time_raw", ""), "market": leg.get("market_raw", ""),
                   "selection": leg.get("selection", ""), "odds": str(leg.get("odds") or ""),
                   "result": tickets.leg_result(leg) or "", "record": "", "status": ""}
            legs.append(row)
            key = tickets.market_key(leg.get("market_raw"))
            named = by_names.get((tickets.norm_name(home), tickets.norm_name(away)), [])
            pool = [s for s in named if pcbf.market_for(s["sport"], key, pcbf.MARKETS[s["market"]]["outcomes"])
                    == s["market"]]
            kicks = {}
            for s in pool:
                k = parse_kick(s)
                ok = k and (not placed or placed <= k <= placed + MAX_LEAD) and (not kick or abs(k - kick) <= timedelta(hours=3))
                if ok:
                    kicks.setdefault(_event(s["selection_id"]), (k, []))[1].append(s)
            if not kicks:
                row["status"] = ("other market" if (named and not pool) or key not in pcbf.MAIN_KEYS
                                 else "game not logged")
                continue
            ev, (k, group) = min(kicks.items(), key=lambda kv: kv[1][0])
            n = pcbf.MARKETS[group[0]["market"]]["outcomes"]
            idx = tickets.side_index(leg.get("selection"), home, away, n)
            if idx is None:
                row["status"] = "selection not understood"
                continue
            same = [s for s in group if str(s.get("selection_index")) == str(idx)]
            sel = min(same, key=lambda s: _bet_rank(s, placed, now)) if same else None
            if sel is None:
                row["status"] = "side not logged"
            elif placed and (pcbf.parse_time(sel["priced_at_utc"]) or now) > placed + PRICE_SLACK:
                row["status"] = "bet placed before the app priced it"
            else:
                row["record"] = sel["selection_record_id"]
                ids = {s["selection_record_id"] for s in same}
                if any((t["id"], i) in linked for i in ids):
                    row["status"] = "linked"
                elif single is None:
                    row["status"] = "accumulator only"
                else:
                    try:
                        odds = float(leg.get("odds"))
                    except (TypeError, ValueError):
                        odds = 0
                    if odds <= 1 or not placed or single <= 0:
                        row["status"] = "odds, stake or time unreadable"
                    else:
                        rec = {"bet_id": schemas.stable_id("bet", "ticket", t["id"], sel["selection_record_id"]),
                               "selection_record_id": sel["selection_record_id"], "placed_at_utc": pcbf.utc(placed),
                               "stake": f"{single:.2f}", "bookmaker_odds": f"{odds:g}", "currency": "NGN",
                               "ticket_reference": t["id"],
                               "note": "auto from Bet9ja ticket capture" + (" (singles in a system ticket)"
                                                                           if t["buckets"] else "")}
                        new_bets.append(rec)
                        linked.add((t["id"], sel["selection_record_id"]))
                        row["status"] = "linked (new)"
            res = tickets.leg_result(leg)
            if res:
                pos, why = _result_pos(res, idx, n, tickets.leg_score(leg))
                if pos is None:
                    problems.append((t["id"], row["game"], why))
                    continue
                for s in group:
                    rid = s["selection_record_id"]
                    if rid in settled:
                        continue
                    st, _cl, err = pcbf.settlement_and_closing(s, pos, None, now, f"bet9ja ticket {t['id']}")
                    if st is None:
                        problems.append((t["id"], row["game"], err))
                        break
                    new_st.append(st)
                    settled.add(rid)
    if write:
        df.append("bet", new_bets)
        df.append("settlement", new_st)
    count = {}
    for r in legs:
        count[r["status"]] = count.get(r["status"], 0) + 1
    return {"tickets": tks, "legs": legs, "bets": len(new_bets), "settled": len(new_st), "counts": count,
            "problems": problems}


# --------------------------------------------- results from benchmark sources

RESULT_AFTER = timedelta(hours=3)   # look for a result this long after kickoff
RESULT_LOOKBACK = timedelta(days=7)
KICKOFF_SHIFT = timedelta(hours=12)  # a result page dated further from the logged kickoff is another match


def due_results(df: DataFolder, now: datetime) -> dict:
    """Unsettled logged games (PICK, PM_PAPER, WATCH, RESEARCH) that kicked
    off between RESULT_LOOKBACK and RESULT_AFTER ago, by event."""
    settled = {s["selection_record_id"] for s in df.read("settlement")}
    groups: dict = {}
    for s in df.read("selection"):
        if s["tier"] == "REJECTED" or s["selection_record_id"] in settled or s.get("market") not in pcbf.MARKETS:
            continue
        k = parse_kick(s)
        if not k or not (now - RESULT_LOOKBACK <= k <= now - RESULT_AFTER):
            continue
        g = groups.setdefault(_event(s["selection_id"]), {"sels": [], "kick": k, "sport": s["sport"],
                                                           "market": s["market"], "event_name": s["event_name"]})
        g["sels"].append(s)
    return groups


def _result_quotes(df: DataFolder, extra_folders, now: datetime) -> list[dict]:
    since = now - RESULT_LOOKBACK - timedelta(days=1)
    return _quotes_in(df.paths("public-odds-walk-*.json", extra_folders, since)
                      + df.paths("polymarket-feed-*.json", (), since))


def result_matches(df: DataFolder, now: datetime, extra_folders=(), groups=None, quotes=None) -> dict:
    """For each due game, the Polymarket and OddsPortal events it matches
    exactly (or as accepted on Pending), with the order mapping Bet9ja's
    positions to the source's. Ambiguous matches are left out."""
    groups = due_results(df, now) if groups is None else groups
    if not groups:
        return {}
    quotes = _result_quotes(df, extra_folders, now) if quotes is None else quotes
    decisions = match_decisions(df)
    out = {}
    for ev, g in groups.items():
        home, _, away = g["event_name"].partition(" v ")
        n = pcbf.MARKETS[g["market"]]["outcomes"]
        cand = {"event_id": ev.rsplit(":", 1)[0], "home": home, "away": away, "sport": g["sport"],
                "kickoff_utc": pcbf.utc(g["kick"]), "outcomes": [None] * n}
        found = {}
        for m in odds_sources.find_matches(cand, quotes):
            q = m["quote"]
            d = (decisions.get((cand["event_id"], odds_sources.quote_key(q)))
                 or decisions.get((cand["event_id"], odds_sources.event_key(q))))
            if d == "reject" or (m["quality"] != "exact" and d != "accept"):
                continue
            if q["source"] in ("polymarket", "oddsportal"):
                found.setdefault(q["source"], {})[odds_sources.event_key(q)] = (q, m["order"])
        out[ev] = {src: next(iter(v.values())) for src, v in found.items() if len(v) == 1}
    return out


def _walk_results(df: DataFolder, extra_folders, now: datetime) -> dict:
    """Final-result captures from the walker's results mode, by match URL
    (without the #hash); the newest capture of a page wins."""
    out = {}
    for path, raw in df._read_unique(df.paths("public-odds-walk-*-results-*.json", extra_folders, now - RESULT_LOOKBACK)):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        offset = data.get("browser_utc_offset_minutes") if isinstance(data, dict) else None
        for cap in (data.get("captures") or []) if isinstance(data, dict) else []:
            if cap.get("role") != "result" or not cap.get("source_url"):
                continue
            key = cap["source_url"].split("#")[0]
            if key not in out or cap.get("captured_at_utc", "") >= out[key]["captured_at_utc"]:
                out[key] = {**cap, "file": path.name, "utc_offset": offset}
    return out


def _page_kickoff(raw: str, offset):
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3}) (\d{4}),\s*(\d{1,2}):(\d{2})", raw or "")
    if not m or offset is None or m.group(2).lower() not in odds_sources.MONTHS:
        return None
    local = datetime(int(m.group(3)), odds_sources.MONTHS[m.group(2).lower()], int(m.group(1)),
                     int(m.group(4)), int(m.group(5)))
    return (local - timedelta(minutes=offset)).replace(tzinfo=timezone.utc)


def settle_from_results(df: DataFolder, now: datetime, extra_folders=(), fetch: bool = True, opener=None) -> dict:
    """Settle finished logged games from Polymarket's resolved markets and
    the walker's OddsPortal result pages. A source is used only where its
    settlement matches Bet9ja's: OddsPortal scores are read on Bet9ja's
    terms (1X2 on regulation time); Polymarket soccer is 90 minutes plus
    stoppage, its moneylines include overtime. Sports whose settlement
    varies (tennis retirements, cricket, MMA, boxing) are settled from
    Polymarket only once you have confirmed its rule on Pending, and from
    OddsPortal only when the game finished normally. Two sources that
    disagree settle nothing."""
    import feeds
    groups = due_results(df, now)
    if not groups:
        return {"settled": 0, "games": 0, "waiting": [], "problems": [], "fetch": None}
    matches = result_matches(df, now, extra_folders, groups)
    rules = confirmed_rules(df)
    slug = lambda q: q["url"].rstrip("/").rsplit("/", 1)[-1]
    def pm_rule(g):
        check, _ = pcbf.settlement_check("polymarket", g["sport"], g["market"], rules)
        variable = bool(pcbf.VARIABLE_RULES.get(g["market"], {}).get(g["sport"]))
        if check == "differs" or (variable and check != "ok"):
            return "Polymarket: settlement rule " + ("differs" if check == "differs" else "unconfirmed (Pending)")
        return None

    fetched = None
    if fetch:
        fetched = feeds.fetch_polymarket_results(df, now, [slug(m["polymarket"][0]) for ev, m in matches.items()
                                                           if "polymarket" in m and not pm_rule(groups[ev])],
                                                 opener=opener)
    pm = {}
    for path, raw in sorted(df.result_files(now - RESULT_LOOKBACK)):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        for ev in (data.get("events") or []) if isinstance(data, dict) else []:
            pm[ev.get("slug")] = {**ev, "file": path.name}
    walked = _walk_results(df, extra_folders, now)
    new_st, waiting, problems, games = [], [], [], 0
    for ev, g in sorted(groups.items(), key=lambda kv: kv[1]["kick"]):
        n = pcbf.MARKETS[g["market"]]["outcomes"]
        variable = bool(pcbf.VARIABLE_RULES.get(g["market"], {}).get(g["sport"]))
        found, why_not = {}, []
        m = matches.get(ev, {})
        if "polymarket" in m:
            q, order = m["polymarket"]
            e = pm.get(slug(q))
            if pm_rule(g):
                why_not.append(pm_rule(g))
            elif e is None:
                why_not.append("Polymarket: not fetched yet")
            elif n == 3 and len(q["outcomes"]) != 3:
                why_not.append("Polymarket: no 3-way market")
            else:
                name, why = results.polymarket_winner(e)
                if name is None:
                    why_not.append("Polymarket: " + why)
                elif name not in q["outcomes"]:
                    why_not.append(f"Polymarket: winner '{name}' not in the matched market")
                else:
                    found["polymarket"] = (order.index(q["outcomes"].index(name)), f"polymarket result {slug(q)}")
        if "oddsportal" in m:
            q, order = m["oddsportal"]
            cap = walked.get(q["url"])
            if cap is None:
                why_not.append("OddsPortal: result page not walked yet")
            else:
                page_kick = _page_kickoff(cap.get("kickoff_raw"), cap.get("utc_offset"))
                if page_kick and abs(page_kick - g["kick"]) > KICKOFF_SHIFT:
                    why_not.append("OddsPortal: page shows a different match date")
                elif not page_kick and "#" not in cap.get("source_url", ""):
                    why_not.append("OddsPortal: page has no match date to check")
                else:
                    pos, why = results.oddsportal_result(cap.get("final_result_raw"), g["sport"], n, variable)
                    if pos is None:
                        why_not.append("OddsPortal: " + why)
                    else:
                        found["oddsportal"] = (order.index(pos), f"oddsportal result {q['url']}")
        if not m:
            why_not.append("no Polymarket or OddsPortal match")
        idxs = {v[0] for v in found.values()}
        if len(idxs) > 1:
            problems.append((g["event_name"], "sources disagree: " + ", ".join(found)))
            continue
        if not found:
            waiting.append((g["event_name"], g["sport"], pcbf.utc(g["kick"]), "; ".join(why_not)))
            continue
        idx, source = found.get("oddsportal") or found["polymarket"]
        pos = (["1", "X", "2"] if n == 3 else ["1", "2"])[idx]
        games += 1
        for s in g["sels"]:
            st, _cl, err = pcbf.settlement_and_closing(s, pos, None, now, source)
            if st is None:
                problems.append((g["event_name"], err))
                break
            new_st.append(st)
    df.append("settlement", new_st)
    return {"settled": len(new_st), "games": games, "waiting": waiting, "problems": problems, "fetch": fetched}


def result_targets(df: DataFolder, now: datetime) -> list[dict]:
    """Finished, unsettled games with an OddsPortal match page and no
    walked result yet: the walker's results mode visits these."""
    groups = due_results(df, now)
    if not groups:
        return []
    walked = _walk_results(df, (), now)
    out = []
    for ev, m in result_matches(df, now, (), groups).items():
        if "oddsportal" not in m:
            continue
        q, _ = m["oddsportal"]
        cap = walked.get(q["url"])
        if cap and results.parse_final(cap.get("final_result_raw")):
            continue
        g = groups[ev]
        home, _, away = g["event_name"].partition(" v ")
        out.append({"url": q.get("page_url") or q["url"], "sport": WALKER_SPORT.get(g["sport"], g["sport"].replace("_", "-")),
                    "home": home, "away": away, "kickoff_utc": pcbf.utc(g["kick"])})
    return sorted(out, key=lambda r: r["kickoff_utc"])


# ------------------------------------------------------- betting breakdowns

_SPORT_CACHE: dict = {}   # (path, size, mtime) -> {(home, away): sport} from one Bet9ja capture
MARKET_SPORT = {"1-2_(inc__extra_inning)": "baseball"}   # ticket market labels that name the sport


def sport_index(df: DataFolder, extra_folders=()) -> dict:
    """(home, away) -> sport, and ('competition', name) -> the sports that
    competition name appears under, from every Bet9ja walk capture and every
    logged selection. Ticket captures carry team names but no sport; the
    same names appear in the walks, filed under their sport."""
    index = {}
    for path in df.paths("bet9ja-*.json", extra_folders):
        if path.name.startswith(("bet9ja-open-bets", "bet9ja-settled-bets")):
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        key = (str(path), st.st_size, st.st_mtime_ns)
        if key not in _SPORT_CACHE:
            pairs = {}
            try:
                raw = path.read_bytes()
                data = json.loads(raw.decode("utf-8"))
                cands = pcbf.candidates_from_capture(data, pcbf.payload_hash(raw), path.name) if isinstance(data, dict) else []
            except (OSError, UnicodeDecodeError, ValueError):
                cands = []
            for c in cands:
                if c.get("home") and c.get("away") and c.get("sport"):
                    pairs[(tickets.norm_name(c["home"]), tickets.norm_name(c["away"]))] = c["sport"]
                if c.get("competition") and c.get("sport"):
                    pairs.setdefault(("competition", tickets.norm_name(c["competition"])), set()).add(c["sport"])
            _SPORT_CACHE[key] = pairs
        for k, v in _SPORT_CACHE[key].items():
            if k[0] == "competition":
                index.setdefault(k, set()).update(v)
            else:
                index[k] = v
    for s in df.read("selection"):
        home, _, away = (s.get("event_name") or "").partition(" v ")
        if s.get("sport"):
            index[(tickets.norm_name(home), tickets.norm_name(away))] = s["sport"]
    return index


def leg_sport(leg: dict, index: dict) -> str | None:
    """By the game's teams; else by its competition when that name belongs
    to one sport only in the walks ('Champions League' does not); else by a
    market label that names the sport."""
    home, away, _ = tickets.split_fixture(leg.get("fixture_and_time_raw"), None)
    comp = index.get(("competition", tickets.norm_name(leg.get("competition_raw"))), set())
    return (index.get((tickets.norm_name(home), tickets.norm_name(away)))
            or (next(iter(comp)) if len(comp) == 1 else None)
            or MARKET_SPORT.get(tickets.market_key(leg.get("market_raw"))))


def betting_breakdown(df: DataFolder, tks: list[dict], extra_folders=()) -> dict:
    """Ticket results by sport and by week (placed), and the linked singles
    (bets on games the app logged) by sport. A ticket's sport is its legs'
    sport; legs from different sports make it 'mixed'; a leg whose game
    never appeared in a Bet9ja walk is 'unknown'."""
    index = sport_index(df, extra_folders)

    def sport_of(t):
        sports = {leg_sport(leg, index) or "unknown" for leg in t["legs"]}
        return sports.pop() if len(sports) == 1 else ("mixed" if sports else "unknown")

    sels = {s["selection_record_id"]: s for s in df.read("selection")}
    st = {s["selection_record_id"]: s for s in df.read("settlement")}
    singles: dict = {}
    for b in df.read("bet"):
        sel = sels.get(b["selection_record_id"])
        sport = (sel or {}).get("sport") or "unknown"
        r = singles.setdefault(sport, {"key": sport, "bets": 0, "settled": 0, "staked": 0.0, "returned": 0.0, "won": 0})
        r["bets"] += 1
        res = st.get(b["selection_record_id"], {}).get("result")
        if res:
            stake, odds = float(b["stake"]), float(b["bookmaker_odds"])
            r["settled"] += 1
            r["staked"] += stake
            r["returned"] += stake * odds if res == "WIN" else (stake if res == "VOID" else 0.0)
            r["won"] += res == "WIN"
    for r in singles.values():
        r["pnl"] = r["returned"] - r["staked"]
        r["roi"] = r["pnl"] / r["staked"] if r["staked"] else None
    by = lambda rows: sorted(rows, key=lambda r: (-float(r["staked"]), r["key"]))
    return {"by_sport": by(tickets.breakdown(tks, sport_of)),
            "by_week": sorted(tickets.breakdown(tks, lambda t: tickets.week_start(t["placed"])),
                              key=lambda r: r["key"], reverse=True),
            "singles_by_sport": by(singles.values())}
