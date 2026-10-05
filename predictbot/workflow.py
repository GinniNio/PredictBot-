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


def load_candidates(df: DataFolder, now: datetime, extra_folders=()) -> dict:
    """Read every capture, merge by event, screen each candidate."""
    lists, files = [], []
    for path, raw in df.capture_files(extra_folders):
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
    for c in sorted(merged.values(), key=lambda c: (c["kickoff_utc"] or "9", c["sport"], c["competition"])):
        reason = pcbf.screen(c, now)
        (rejected if reason else accepted).append((c, reason))
    return {"files": files, "duplicates": dups, "accepted": [c for c, _ in accepted], "rejected": rejected}


PACK_SIZE = 40
RESEND_AFTER = timedelta(hours=24)


def recently_sent(df: DataFolder, now: datetime) -> set:
    """Events already sent for benchmarking in the last 24h, or already priced."""
    sent = {r["event_id"] for r in df.read_screening()
            if r["status"] == "sent for benchmark" and (pcbf.parse_time(r["screened_at_utc"]) or now) + RESEND_AFTER > now}
    priced = {s["selection_id"].rsplit(":", 2)[0] for s in df.read("selection") if s["tier"] in ("PICK", "WATCH")}
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
    already = {s["selection_id"] for s in existing if s["tier"] in ("PICK", "WATCH")}
    answered, records, unknown = set(), [], []
    for code, fields, _raw in pcbf.reply_lines(reply):
        entry = pack["entries"].get(code)
        if not entry:
            unknown.append(code)
            continue
        if code in answered:
            continue
        answered.add(code)
        recs = pcbf.price_benchmark(entry["captures"], fields, now, pack["pack_id"], already)
        records.extend(recs)
        already |= {r["selection_id"] for r in recs if r["tier"] in ("PICK", "WATCH")}
    df.append("selection", records)
    missing = [c for c in pack["entries"] if c not in answered]
    return {"pack_id": pack["pack_id"], "records": records, "unknown_codes": unknown, "unanswered": missing}


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


def benchmark_quotes(df: DataFolder, extra_folders=()) -> list[dict]:
    quotes = []
    for _path, raw in df.odds_walks(extra_folders):
        try:
            run = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(run, dict):
            quotes.extend(odds_sources.quotes_from_walk(run))
    return quotes


def auto_benchmark(df: DataFolder, now: datetime, extra_folders=()) -> dict:
    """Price screened-in candidates against captured benchmark pages
    (Polymarket) without a chat. Each matched quote goes through the same
    validation as a chat reply line. Unusable quotes (in-play, thin, wide)
    are reported, and thin or wide ones are logged as RESEARCH."""
    quotes = benchmark_quotes(df, extra_folders)
    loaded = load_candidates(df, now, extra_folders)
    already = {s["selection_id"] for s in df.read("selection") if s["tier"] in ("PICK", "WATCH")}
    known_caps = {r["capture_id"] for r in df.read("capture")}
    caps, records, skipped = [], [], []
    for c in loaded["accepted"]:
        found = odds_sources.match_quote(c, quotes)
        if not found:
            continue
        q, order = found
        recs = pcbf.capture_records(c)
        name = f"{c['home']} v {c['away']}"
        problem = odds_sources.quote_problem(q)
        if problem and problem.startswith("captured after") or (problem == "market closed"):
            skipped.append((name, problem, q["url"]))
            continue
        if any(r["selection_id"] in already for r in recs):
            skipped.append((name, "already priced", q["url"]))
            continue
        caps.extend(r for r in recs if r["capture_id"] not in known_caps)
        known_caps |= {r["capture_id"] for r in recs}
        if problem:
            fields = ["NONE", f"polymarket {problem}"]
        else:
            fields = odds_sources.reply_fields(q, order)
        out = pcbf.price_benchmark(recs, fields, now, "auto-polymarket", already)
        for r in out:
            r["origin"] = "auto: polymarket capture"
        records.extend(out)
        already |= {r["selection_id"] for r in out if r["tier"] in ("PICK", "WATCH")}
    df.append("capture", caps)
    df.append("selection", records)
    return {"quotes": len(quotes), "records": records, "skipped": skipped}
