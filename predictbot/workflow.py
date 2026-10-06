"""The daily loop, joining the pure core (pcbf.py) to the data folder
(storage.py). app.py calls only these functions, so the interface can be
replaced without touching model logic or records."""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import odds_sources
import pcbf
import schemas
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


def benchmark_quotes(df: DataFolder, extra_folders=(), now: datetime | None = None) -> list[dict]:
    quotes = []
    for path, raw in df.odds_walks(extra_folders, (now - RECENT) if now else None):
        try:
            run = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(run, dict):
            quotes.extend(odds_sources.quotes_from_walk(run, path.name))
    return quotes


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
        choice = odds_sources.choose_quote(c, quotes, decisions)
        if choice["status"] == "review":
            rows.append({"c": c, "state": "match review", "reason": choice["review"][0]["why"], "review": choice["review"]})
        elif choice["status"] == "matched":
            rows.append({"c": c, "state": "quote waiting", "reason": "matched quote not yet priced (run Update)"})
        elif ev in researched:
            rows.append({"c": c, "state": "unresolved", "reason": researched[ev]})
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
        k = parse_kick(s)
        if s["tier"] in ("PICK", "PM_PAPER") and (k is None or k > now):
            shortlist.setdefault(s["sport"], set()).add(_event(s["selection_id"]))
    out = {}
    for r in status["rows"]:
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
        bucket = "screen_reasons" if r["state"] == "screened out" else "reasons"
        d["screened_out" if r["state"] == "screened out" else "unresolved"] += 1
        d[bucket][key] = d[bucket].get(key, 0) + 1
    return [out[k] for k in sorted(out)]


def parse_kick(s: dict):
    return pcbf.parse_time(s.get("kickoff_utc"))


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
        later = [q for q in by_url[s["benchmark_url"]]
                 if (pcbf.parse_time(q["captured_at_utc"]) or kick) < kick
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
    df.append("evidence_review", [rec])
    return rec
