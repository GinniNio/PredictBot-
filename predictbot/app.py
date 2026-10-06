"""PredictBot local interface: a thin layer over workflow.py.

    python predictbot/app.py "C:\\Users\\you\\OneDrive\\PredictBot"

Opens http://localhost:8000. The data folder is the first argument, else the
PREDICTBOT_DATA environment variable, else ~/OneDrive/PredictBot when OneDrive
exists, else ~/PredictBotData. New capture files in ~/Downloads are copied
into the data folder's captures/ and priced whenever Opportunities loads.

Two laptops share the data folder through OneDrive, one at a time. Only one
copy of the app can run per laptop (it holds port 8000). The synced session
marker is a warning: if the other laptop did not close cleanly, writes stay
blocked until the handover is confirmed on the Pending page.

Informational only: it never places a wager. Stdlib only.
"""

from __future__ import annotations

import html
import json
import os
import socket
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import odds_sources  # noqa: E402
import pcbf  # noqa: E402
import schemas  # noqa: E402
import tickets  # noqa: E402
import workflow  # noqa: E402
from storage import DataFolder, InvalidRecord  # noqa: E402

PORT = 8000
DOWNLOADS = Path.home() / "Downloads"
e = html.escape


def data_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser()
    if os.environ.get("PREDICTBOT_DATA"):
        return Path(os.environ["PREDICTBOT_DATA"]).expanduser()
    onedrive = Path(os.environ.get("OneDrive", Path.home() / "OneDrive"))
    return onedrive / "PredictBot" if onedrive.exists() else Path.home() / "PredictBotData"


DF: DataFolder | None = None
SERVER = None
# One request at a time: pages write records, and two overlapping loads
# must not both append the same new rows.
LOCK = threading.Lock()


def now() -> datetime:
    return datetime.now(timezone.utc)


def pct(x, digits=1):
    return "" if x is None else f"{x * 100:+.{digits}f}%"


def num(x, digits=2):
    return "" if x is None else f"{x:,.{digits}f}"


def table(headers, rows, cls=""):
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class='tw'><table class='{cls}'><tr>{head}</tr>{body}</table></div>"


# -------------------------------------------------------------------- pages

def blocked() -> str | None:
    return DF.write_block()


def form_button(action, label, fields=None, cls=""):
    hidden = "".join(f"<input type='hidden' name='{e(k)}' value='{e(v)}'>" for k, v in (fields or {}).items())
    return f"<form method='post' action='{action}' class='inline {cls}'>{hidden}<button>{e(label)}</button></form>"


def review_banner() -> str:
    r = workflow.latest_review(DF)
    if not r:
        return ("<div class='gate'>Real money: <b>LOCKED</b>. No written evidence review yet; it is the only way to "
                "change this (Performance page). The app never places bets.</div>")
    pm = "Polymarket PICKs allowed" if r.get("polymarket_picks_allowed") == "yes" else "Polymarket stays PM_PAPER"
    cls = "UNLOCKED" if r["decision"] == "allow small real stakes" else ""
    return (f"<div class='gate {cls}'>Latest evidence review {e(r['reviewed_at_utc'])}: <b>{e(r['decision'])}</b>; "
            f"{pm}. The app never places bets.</div>")


def update_now() -> str:
    """Import new captures and price them (Opportunities does this on load)."""
    imported = DF.import_captures(DOWNLOADS)
    msg = [f"{imported} new capture files from Downloads" if imported else "no new capture files"]
    if blocked():
        return "; ".join(msg) + ". Pricing paused: data folder is read-only (see Pending)."
    r = workflow.auto_benchmark(DF, now(), [DOWNLOADS])
    events = {x["selection_id"].rsplit(":", 1)[0] for x in r["records"]}
    msg.append(f"{len(events)} fixtures priced from captured odds pages")
    if r["review"]:
        msg.append(f"{len(r['review'])} matches wait for review")
    if r["closes"]:
        msg.append(f"{len(r['closes'])} closing prices / pre-kickoff snapshots stored")
    return "; ".join(msg) + "."


def coverage_table(status) -> str:
    rows = []
    cols = ("captured", "usable", "screened_out", "benchmarked", "unresolved", "shortlist")
    tot = dict.fromkeys(cols, 0)
    top = lambda d: ", ".join(f"{n} {r}" for r, n in sorted(d.items(), key=lambda kv: -kv[1])[:3])
    for d in workflow.coverage(DF, status, now()):
        for k in cols:
            tot[k] += d[k]
        rows.append([e(d["sport"]), *[d[k] for k in cols], f"<span class='dim'>{e(top(d['reasons']))}</span>",
                     f"<span class='dim'>{e(top(d['screen_reasons']))}</span>"])
    rows.append(["<b>all</b>", *[f"<b>{tot[k]}</b>" for k in cols], "", ""])
    return table(["Sport", "Captured", "Usable prices", "Screened out", "Benchmarked", "Unresolved", "Shortlist",
                  "Why unresolved", "Why screened out"], rows, "num")


def opp_row(s, rc, bets_for):
    rid = s["selection_record_id"]
    mn = s.get("min_odds") or (f"{float(s['fair_odds']) * (1 + pcbf.PICK_EDGE):.2f}" if s.get("fair_odds") else "")
    if rc:
        recheck = (f"{e(rc['rechecked_odds'])} at {e(rc['rechecked_at_utc'][11:])}: {pct(float(rc['edge_pct']))} "
                   f"<b class='{rc['tier']}'>{e(rc['tier'])}</b>")
    else:
        recheck = ""
    recheck += (f"<form method='post' action='/recheck' class='inline'><input type='hidden' name='rid' value='{e(rid)}'>"
                f"<input name='odds' size='4' placeholder='odds now'><button>Recheck</button></form>")
    times = f"quote {e(s['benchmark_timestamp_utc'][11:])}"
    if s.get("benchmark_captured_utc") and s["benchmark_captured_utc"] != s["benchmark_timestamp_utc"]:
        times += f", captured {e(s['benchmark_captured_utc'][11:])}"
    src = f"<a href='{e(s['benchmark_url'])}'>{e(s['benchmark_source'])}</a>"
    basis = e(s.get("price_basis") or "")
    return [f"<b class='{s['tier']}'>{s['tier']}</b>", e(s["kickoff_utc"].replace("T", " ")), e(s["sport"]),
            e(s["event_name"]), e(s["selection"]), e(s["bookmaker_odds"]), f"<b>{e(mn)}</b>", e(s["fair_odds"]),
            pct(float(s["edge_pct"])), f"{src}<br><span class='dim'>{times}<br>{basis}</span>", recheck,
            "bet logged" if rid in bets_for else f"<a href='/bets?rid={e(rid)}'>record bet</a>"]


OPP_HEAD = ["Tier", "Kickoff UTC", "Sport", "Game", "Selection", "Bet9ja", "Min odds", "Fair", "Edge",
            "Benchmark", "Recheck before betting", ""]


def page_opportunities(msg="") -> str:
    note = update_now()
    status = workflow.day_status(DF, now(), [DOWNLOADS])
    sels = DF.read("selection")
    rcs = workflow.latest_rechecks(DF)
    bets_for = {b["selection_record_id"] for b in DF.read("bet")}
    upcoming = [s for s in sels if (workflow.parse_kick(s) or now()) > now()]
    short = sorted([s for s in upcoming if s["tier"] in ("PICK", "PM_PAPER")], key=lambda s: s["kickoff_utc"])
    watch = sorted([s for s in upcoming if s["tier"] == "WATCH"], key=lambda s: -float(s["edge_pct"] or -9))
    pending = sum(1 for r in status["rows"] if r["state"] == "match review")
    out = [f"<p class='msg'>{e(msg)}</p>" if msg else "", f"<p>{e(note)}</p>", review_banner(),
           "<h2>Coverage by sport</h2>", coverage_table(status)]
    if pending:
        out.append(f"<p><a href='/pending'>{pending} fixtures wait for a match review</a></p>")
    out.append(f"<h2>Shortlist ({len(short)})</h2>")
    if short:
        out.append(table(OPP_HEAD, [opp_row(s, rcs.get(s["selection_record_id"]), bets_for) for s in short]))
        out.append("<p class='dim'>Min odds: the lowest Bet9ja price that still clears the +3% edge at this fair "
                   "probability. Recheck the Bet9ja price just before betting; below min odds, skip it. PM_PAPER is "
                   "Polymarket-only and stays paper until an evidence review allows it.</p>")
    else:
        out.append("<p>No PICK or PM_PAPER opportunities for upcoming games. An empty shortlist is a valid result.</p>")
    out.append(f"<details><summary>WATCH ({len(watch)}): valid prices below +3% or with a caution</summary>"
               + table(OPP_HEAD[:10] + ["Cautions"], [opp_row(s, None, bets_for)[:10] + [e(s["caution_flags"])]
                                                     for s in watch[:300]]) + "</details>")
    return "".join(out)


def page_pending(msg="") -> str:
    status = workflow.day_status(DF, now(), [DOWNLOADS])
    out = [f"<p class='msg'>{e(msg)}</p>" if msg else ""]
    # handover / conflicts
    other, conflicts = DF.foreign_session(), DF.conflict_copies()
    if conflicts:
        out.append("<h2>OneDrive sync conflicts</h2><p class='err'>" + e(", ".join(str(c.relative_to(DF.root)) for c in conflicts))
                   + "</p><p>Both laptops wrote while out of sync. Merging keeps every row from both copies once "
                     "(records are append-only), and moves the copy to .backup/.</p>" + form_button("/merge", "Merge conflict copies"))
    if other:
        out.append(f"<h2>Handover</h2><p class='err'>{e(other.get('host', 'Another laptop'))} last ran PredictBot here "
                   f"(started {e(other.get('started_utc', '?'))}, last seen {e(other.get('heartbeat_utc', '?'))}) and did "
                   "not close cleanly.</p><ol><li>On that laptop, close the PredictBot window (Ctrl+C) or shut the "
                   "laptop down.</li><li>Wait until OneDrive on both laptops shows <b>Up to date</b>.</li>"
                   "<li>Check the Pending page shows no sync conflicts.</li><li>Then confirm below.</li></ol>"
                   + form_button("/handover", "I have closed it there and OneDrive is up to date"))
    reason = blocked()
    if reason and not other and not conflicts:
        out.append(f"<p class='err'>Read-only: {e(reason)}</p>")
    # match review
    reviews = [r for r in status["rows"] if r["state"] == "match review"]
    out.append(f"<h2>Match review ({len(reviews)})</h2>")
    if reviews:
        rows = []
        for r in reviews:
            c = r["c"]
            for m in r["review"]:
                q = m["quote"]
                fields = {"event_id": c["event_id"], "quote_key": odds_sources.event_key(q),
                          "event_name": f"{c['home']} v {c['away']}", "quote_event": " v ".join(q["outcomes"][::len(q["outcomes"]) - 1]),
                          "source": q["source"]}
                rows.append([e(c["sport"]), e(c["kickoff_utc"]), e(f"{c['home']} v {c['away']}"),
                             f"<a href='{e(q['url'])}'>{e(fields['quote_event'])}</a> ({e(q['source'])}, start {e(q['start_utc'] or '?')})",
                             e(m["why"]), form_button("/matchreview", "Same game", {**fields, "decision": "accept"})
                             + form_button("/matchreview", "Different", {**fields, "decision": "reject"})])
        out.append(table(["Sport", "Kickoff UTC", "Bet9ja fixture", "Benchmark event", "Why", "Decision"], rows))
    else:
        out.append("<p class='dim'>Nothing to review.</p>")
    # settlement rules
    rules = workflow.unconfirmed_rules(DF)
    out.append(f"<h2>Settlement rules to confirm ({len(rules)})</h2>")
    if rules:
        rows = []
        for k, n in sorted(rules.items(), key=lambda kv: -kv[1]):
            src, sport, market = k.split("|")
            what = pcbf.VARIABLE_RULES.get(market, {}).get(sport) or "resolution rules"
            rows.append([e(src), e(sport), e(market), e(what), n,
                         f"<form method='post' action='/rule' class='inline'><input type='hidden' name='key' value='{e(k)}'>"
                         "<input name='note' placeholder='what you checked' size='18'>"
                         "<button name='status' value='same as bet9ja'>Same as Bet9ja</button>"
                         "<button name='status' value='differs'>Differs</button></form>"])
        out.append(table(["Source", "Sport", "Market", "Check", "Held at WATCH", "Your check"], rows)
                   + "<p class='dim'>Compare the source's rules page with Bet9ja's for this market. Confirming applies "
                     "to future pricing; past records are not changed.</p>")
    else:
        out.append("<p class='dim'>None outstanding.</p>")
    # unbenchmarked
    nob = [r for r in status["rows"] if r["state"] in ("no benchmark", "unresolved")]
    by_sport = {}
    for r in nob:
        by_sport[r["c"]["sport"]] = by_sport.get(r["c"]["sport"], 0) + 1
    out.append(f"<h2>No benchmark yet ({len(nob)})</h2><p>Capture the sport on OddsPortal or Polymarket with the public "
               "odds walker, or send these to a chat as a research pack (optional).</p>"
               + table(["Sport", "Fixtures"], [[e(k), v] for k, v in sorted(by_sport.items())], "num"))
    if nob:
        boxes = "".join(f"<label><input type='checkbox' name='sport' value='{e(k)}' checked> {e(k)}</label> " for k in sorted(by_sport))
        out.append(f"<form method='post' action='/pack'><p>{boxes}</p><button>Create research pack</button> "
                   "<a href='/pack'>open the last pack / paste a reply</a></form>")
    # settlement
    sels = DF.read("selection")
    settled = {x["selection_record_id"] for x in DF.read("settlement")}
    closed = {x["selection_record_id"] for x in DF.read("closing")}
    done = [s for s in sels if s["tier"] in schemas.PRICED_TIERS and (workflow.parse_kick(s) or now()) <= now()]
    unsettled = [s for s in done if s["selection_record_id"] not in settled]
    no_close = [s for s in done if s["selection_record_id"] not in closed]
    out.append(f"<h2>Settlement</h2><p>{len({s['selection_id'].rsplit(':', 1)[0] for s in unsettled})} finished fixtures "
               f"unsettled; {len({s['selection_id'].rsplit(':', 1)[0] for s in no_close})} without a closing price or "
               "pre-kickoff snapshot. Snapshots are stored automatically when the odds walker captured the same page "
               "again before kickoff.</p>"
               + form_button("/settlepack", "Create settlement pack"))
    return "".join(out)


def page_performance(msg="") -> str:
    perf = perf_now()
    prob = pcbf.probability_metrics(DF.read("selection"), DF.read("settlement"))
    head = ["Group", "n", "Settled", "With CLV", "Mean CLV", "Median CLV", "CLV +/-", "Win rate", "Unit ROI"]

    def rows(d):
        return [[e(k), v["n"], v["settled"], v["with_clv"], pct(v["mean_clv"]), pct(v["median_clv"]),
                 f"{v['positive_clv']} / {v['negative_clv']}", pct(v["win_rate"], 0).lstrip("+"), pct(v["unit_roi"])]
                for k, v in d.items()]

    def score(label, sc):
        ci = lambda x, se: "" if x is None else f"{x:.4f}" + (f" ± {1.96 * se:.4f}" if se else "")
        return [e(label), sc["events"], ci(sc["brier"], sc["brier_se"]), ci(sc["log_loss"], sc["log_loss_se"])]
    g = perf["gate"]
    out = [f"<p class='msg'>{e(msg)}</p>" if msg else "", review_banner(),
           "<h2>Forecast quality</h2><p class='dim'>Every settled event with a full priced market (PICK, PM_PAPER and "
           "WATCH). Lower is better. ± is a 95% interval across events.</p>",
           table(["Forecast", "Events", "Brier", "Log loss"],
                 [score("benchmark fair probabilities", prob["benchmark"]),
                  score("benchmark, events with Bet9ja prices", prob["benchmark_same_events"]),
                  score("Bet9ja's own prices (de-vigged), same events", prob["bet9ja_same_events"])]
                 + [score(f"by source: {k}", v) for k, v in prob["by_source"].items()], "num"),
           "<h3>Calibration</h3>",
           table(["Forecast band", "Outcomes", "Mean forecast", "Observed"],
                 [[e(c["bin"]), c["n"], f"{c['mean_forecast']:.3f}", f"{c['observed']:.3f}"] for c in prob["calibration"]], "num"),
           "<h2>Paper selections</h2>", table(head, rows(perf["by_tier"]), "num"),
           f"<p>PICK notional ROI (₦25 stakes): <b>{pct(perf['pick_notional_roi'])}</b>. Settled PICKs {g['settled_picks']} "
           f"(rulebook review point: {g['needed']}). CLV from {g['true_closes']} closing prices and {g['snapshots']} "
           "pre-kickoff snapshots.</p>"]
    for dim in ("sport", "market", "odds_band", "benchmark_source"):
        out.append(f"<details><summary>By {dim.replace('_', ' ')}</summary>" + table(head, rows(perf[f"by_{dim}"]), "num") + "</details>")
    b = perf["bets"]
    out.append(f"<h2>Actual wagers (separate from paper)</h2><p>{b['n']} logged against selections, {b['settled']} settled, "
               f"stake-weighted ROI {pct(b['stake_weighted_roi'])}. <a href='/bets'>Bets and Bet9ja ticket P&amp;L</a></p>")
    opts = "".join(f"<option>{e(d)}</option>" for d in schemas.REVIEW_DECISIONS)
    out.append("<h2>Written evidence review</h2><p class='dim'>Replaces automatic unlocking. Cover coverage, forecast "
               "quality, selection results, missing data and effort. The metrics above are saved with it.</p>"
               "<form method='post' action='/evidence'><textarea name='summary' rows='6' placeholder='What the evidence "
               "shows, and why this decision'></textarea><p><select name='decision'>" + opts + "</select> "
               "<label><input type='checkbox' name='pm' value='yes'> allow Polymarket-only PICKs</label> "
               "<input name='reviewer' placeholder='your name'> <button>Save review</button></p></form>")
    past = sorted(DF.read("evidence_review"), key=lambda r: r["reviewed_at_utc"], reverse=True)
    if past:
        out.append(table(["Date", "Decision", "Polymarket PICKs", "Summary"],
                         [[e(r["reviewed_at_utc"]), e(r["decision"]), e(r["polymarket_picks_allowed"]), e(r["summary"])]
                          for r in past]))
    return "".join(out)


def page_pack(kind="research") -> str:
    pack = DF.load_pack(kind)
    if not pack:
        return "<p>No pack yet.</p>"
    action = "/reply" if kind == "research" else "/settle"
    return (f"<p>Copy this into Claude, ChatGPT or Gemini, then paste the reply below. Pack <b>{e(pack['pack_id'])}</b>, "
            f"{len(pack['entries'])} fixtures.</p>"
            f"<textarea id='pack' readonly rows='14'>{e(pack['text'])}</textarea>"
            "<p><button onclick=\"navigator.clipboard.writeText(document.getElementById('pack').value);"
            "this.textContent='Copied'\">Copy pack</button></p>"
            f"<form method='post' action='{action}'><h2>Paste the chat's reply</h2>"
            f"<textarea name='reply' rows='12' placeholder='{e(pack['pack_id'])} lines...'></textarea>"
            "<p><button>Check and save</button></p></form>")


def page_reply_result(res: dict) -> str:
    if res.get("error"):
        return f"<p class='err'>{e(res['error'])}</p>"
    recs = res["records"]
    shown = [r for r in recs if r["tier"] in schemas.PRICED_TIERS]
    shown.sort(key=lambda r: (schemas.TIERS.index(r["tier"]), -float(r["edge_pct"] or 0)))
    rows = [[f"<b class='{r['tier']}'>{r['tier']}</b>", e(r["selection"]), e(r["event_name"]), e(r["kickoff_utc"]),
             e(r["bookmaker_odds"]), e(r["fair_odds"]), pct(float(r["edge_pct"])),
             f"<a href='{e(r['benchmark_url'])}'>{e(r['benchmark_source'])}</a> {e(r['benchmark_timestamp_utc'])}",
             e("; ".join(x for x in (r["caution_flags"], r["research_note"]) if x))] for r in shown]
    other = [[e(r["tier"]), e(r["event_name"]), e(r["selection"]), e(r["rejection_reason"])]
             for r in recs if r["tier"] in ("RESEARCH", "REJECTED")]
    events = {r["selection_id"].rsplit(":", 1)[0]: r["tier"] for r in recs}
    priced = sum(1 for t in events.values() if t in schemas.PRICED_TIERS)
    out = [f"<p>Saved {len(recs)} selection records from pack {e(res['pack_id'])} before any bet.</p>",
           table(["Tier", "Selection", "Game", "Kickoff UTC", "Bet9ja", "Fair", "Edge", "Benchmark", "Note"], rows),
           f"<p>Games received {len(events) + len(res['unanswered'])} / priced {priced} / "
           f"not priced {len(events) - priced} / no reply {len(res['unanswered'])}. "
           "Recheck every Bet9ja price before placing.</p>"]
    if other:
        out.append("<h2>Not priced</h2>" + table(["Tier", "Game", "Selection", "Reason"], other))
    if res.get("already_recorded"):
        out.append(f"<p class='dim'>Already recorded earlier (nothing new written): {e(', '.join(res['already_recorded']))}</p>")
    if res["unknown_codes"]:
        out.append(f"<p class='err'>Codes not in the pack: {e(', '.join(res['unknown_codes']))}</p>")
    if res["unanswered"]:
        out.append(f"<p class='dim'>No reply line for: {e(', '.join(res['unanswered']))}</p>")
    return "".join(out)


def perf_now():
    return pcbf.performance(DF.read("selection"), DF.read("settlement"), DF.read("closing"), DF.read("bet"))


def page_selections(query) -> str:
    tier = (query.get("tier") or [""])[0]
    sels = DF.read("selection")
    st = {s["selection_record_id"]: s for s in DF.read("settlement")}
    cl = {c["selection_record_id"]: c for c in DF.read("closing")}
    rows = []
    for s in reversed(sels):
        if tier and s["tier"] != tier:
            continue
        rid = s["selection_record_id"]
        rows.append([f"<b class='{s['tier']}'>{s['tier']}</b>", e(s["priced_at_utc"]), e(s["sport"]), e(s["event_name"]),
                     e(s["selection"]), e(s["bookmaker_odds"]), e(s["fair_odds"]),
                     pct(float(s["edge_pct"])) if s["edge_pct"] else "", e(s["benchmark_source"]),
                     e(st[rid]["result"]) if rid in st else "", pct(float(cl[rid]["clv_pct"])) if rid in cl else "",
                     e(s["rejection_reason"] or s["caution_flags"]), f"<code>{e(rid)}</code>"])
    tabs = " ".join(f"<a href='/selections?tier={t}'>{t or 'all'}</a>" for t in ("",) + schemas.TIERS)
    return (f"<p>Every selection record, newest first. {tabs}</p>"
            + table(["Tier", "Priced", "Sport", "Game", "Selection", "Bet9ja", "Fair", "Edge", "Source", "Result",
                     "CLV", "Reason / cautions", "ID"], rows))


def page_bets(msg="", rid="") -> str:
    sels = [s for s in DF.read("selection") if s["tier"] != "REJECTED"][-300:]
    opts = "".join(f"<option value='{e(s['selection_record_id'])}'{' selected' if s['selection_record_id'] == rid else ''}>"
                   f"{e(s['tier'])} {e(s['event_name'])}: {e(s['selection'])} @ {e(s['bookmaker_odds'])}</option>"
                   for s in reversed(sels))
    bets = DF.read("bet")
    form = ("<form method='post' action='/bet' class='bet'><h2>Record an actual bet</h2>"
            f"<select name='selection_record_id'>{opts}</select>"
            "<input name='stake' placeholder='stake NGN' required> <input name='odds' placeholder='odds taken' required> "
            f"<input name='placed' value='{pcbf.utc(now())}'> <input name='ticket' placeholder='Bet9ja ticket ID'> "
            "<button>Save bet</button></form><p class='dim'>Only selections already logged can carry a bet.</p>")
    bt = table(["Placed", "Stake", "Odds", "Ticket", "Selection record"],
               [[e(b["placed_at_utc"]), e(b["stake"]), e(b["bookmaker_odds"]), e(b["ticket_reference"]),
                 f"<code>{e(b['selection_record_id'])}</code>"] for b in reversed(bets)])
    caps = []
    for path, raw in DF.capture_files([DOWNLOADS]):
        try:
            d = json.loads(raw.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(d, dict) and str(d.get("schema_version", "")).startswith(("bet9ja-ticket", "bet9ja-settled")):
            caps.append((path, d))
    tk = tickets.all_tickets(caps)
    s = tickets.summary(tk)
    rec = (f"<h2>Bet9ja tickets (from open / settled bet captures)</h2><p>{s['settled']} settled tickets: staked "
           f"{num(s['staked'], 0)}, returned {num(s['returned'], 0)} (calculated), P&amp;L <b>{num(s['pnl'], 0)}</b>"
           f"{' (' + pct(float(s['roi']), 0) + ')' if s['roi'] is not None else ''}. {s['open']} open, "
           f"{num(s['open_staked'], 0)} staked. {s['won_below_stake']} of {s['won_marked']} 'won' tickets returned "
           "less than their stake.</p>") if tk else "<p class='dim'>No Bet9ja ticket captures found.</p>"
    return (f"<p class='msg'>{e(msg)}</p>" if msg else "") + form + bt + rec


# ------------------------------------------------------------------ server

NAV = [("/", "Opportunities"), ("/pending", "Pending"), ("/performance", "Performance")]
MORE = [("/bets", "Bets"), ("/selections", "Full log")]

CSS = """
:root{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1b;--dim:#85857f;--line:#e6e6e2;--pos:#16794a;--neg:#b42318;--acc:#1f5fbf}
@media (prefers-color-scheme:dark){:root{--bg:#151515;--card:#1f1f1f;--fg:#ececec;--dim:#8f8f8f;--line:#2e2e2e;--pos:#4cc38a;--neg:#ff6b5e;--acc:#7aa7ff}}
body{font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px;color:var(--fg);background:var(--bg)}
header{display:flex;gap:18px;align-items:baseline;flex-wrap:wrap;margin-bottom:10px}
header strong{font-size:18px} nav a{margin-right:12px;color:var(--dim);text-decoration:none;font-weight:600}
nav a.on{color:var(--fg);border-bottom:2px solid var(--fg)} .r{margin-left:auto;color:var(--dim);font-size:12px}
h2{font-size:15px;margin:24px 0 8px} a{color:var(--acc)}
.tw{overflow-x:auto} table{border-collapse:collapse;width:100%;background:var(--card)}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-size:12px} table.num td:not(:first-child){text-align:right}
textarea{width:100%;box-sizing:border-box;font:12px ui-monospace,monospace;background:var(--card);color:var(--fg);border:1px solid var(--line)}
button{padding:6px 14px;font-weight:600;cursor:pointer} input,select{padding:5px;max-width:100%}
.PICK,.PM_PAPER{color:var(--pos)} .PM_PAPER{font-style:italic} .REJECTED{color:var(--neg)} .RESEARCH{color:var(--dim)} .dim{color:var(--dim)}
.err{color:var(--neg)} .msg{color:var(--pos)} code{font-size:11px;color:var(--dim)}
.gate{padding:10px 12px;border-radius:8px;background:var(--card);border-left:4px solid var(--neg);margin:8px 0}
.gate.UNLOCKED{border-left-color:var(--pos)} details{margin-top:12px} label{margin-right:10px}
form.inline{display:inline-block;margin:0 4px 0 0} .banner{padding:10px 12px;border-radius:8px;background:var(--card);
border-left:4px solid var(--neg);margin:8px 0} nav .more{font-weight:400} h3{font-size:14px;margin:16px 0 6px}
"""


def layout(path, body):
    nav = "".join(f"<a class='{'on' if p == path else ''}' href='{p}'>{t}</a>" for p, t in NAV)
    nav += "".join(f"<a class='more {'on' if p == path else ''}' href='{p}'>{t}</a>" for p, t in MORE)
    reason = blocked()
    banner = (f"<div class='banner'><b>Read-only.</b> {e(reason)} <a href='/pending'>Pending</a></div>" if reason else "")
    stop = form_button("/stop", "Stop PredictBot")
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>PredictBot</title><style>{CSS}</style></head><body><header><strong>PredictBot</strong><nav>{nav}</nav>"
            f"<span class='r'>{e(DF.host)} · app {schemas.APP_VERSION} · data v{DF.data_version()} · {e(str(DF.root))} {stop}</span>"
            f"</header>{banner}{body}</body></html>")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with LOCK:
            self._get()

    def do_POST(self):
        with LOCK:
            self._post()

    def _get(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        msg = (q.get("msg") or [""])[0]
        pages = {"/": lambda: page_opportunities(msg), "/pending": lambda: page_pending(msg),
                 "/performance": lambda: page_performance(msg),
                 "/pack": lambda: page_pack("research"), "/settle": lambda: page_pack("settle"),
                 "/selections": lambda: page_selections(q), "/bets": lambda: page_bets(msg, (q.get("rid") or [""])[0])}
        if u.path not in pages:
            return self.send_error(404)
        try:
            self.send(layout(u.path, pages[u.path]()))
        except InvalidRecord as exc:
            self.send(layout(u.path, f"<p class='err'>{e(str(exc))}</p>"))

    def _post(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
        get = lambda k: (form.get(k) or [""])[0]
        try:
            if u.path == "/pack":
                workflow.create_pack(DF, now(), set(form.get("sport") or []) or None, [DOWNLOADS])
                return self.redirect("/pack")
            if u.path == "/reply":
                return self.send(layout("/pack", page_reply_result(workflow.apply_reply(DF, get("reply"), now()))))
            if u.path == "/settlepack":
                if workflow.create_settle_pack(DF, now()):
                    return self.redirect("/settle")
                return self.send(layout("/pending", page_pending("No finished fixtures to settle yet (3h after kickoff).")))
            if u.path == "/settle":
                r = workflow.apply_settlement(DF, get("reply"), now())
                body = (f"<p class='err'>{e(r['error'])}</p>" if r.get("error") else
                        f"<p class='msg'>Settled {r['settled']} selections, {r['closing']} closing prices.</p>"
                        + table(["Code", "Problem", "Line"], [[e(a), e(b), e(c)] for a, b, c in r["problems"]])
                        + (f"<p class='dim'>Skipped: {e(str(r['skipped']))}</p>" if r["skipped"] else ""))
                return self.send(layout("/pending", body))
            if u.path == "/bet":
                workflow.record_bet(DF, get("selection_record_id"), get("stake"), get("odds"), get("placed"),
                                    get("ticket"))
                return self.redirect("/bets", "Bet saved.")
            if u.path == "/recheck":
                r = workflow.recheck_selection(DF, get("rid"), get("odds"), now())
                return self.redirect("/", f"Recheck saved: {r['rechecked_odds']} gives edge "
                                          f"{float(r['edge_pct']) * 100:+.1f}%, {r['tier']} (min odds {r['min_odds']}).")
            if u.path == "/matchreview":
                workflow.record_match_review(DF, get("event_id"), get("quote_key"), get("decision"), now(),
                                             get("event_name"), get("quote_event"), get("source"))
                return self.redirect("/pending", "Match decision saved. Accepted matches are priced on the next "
                                                 "Opportunities load.")
            if u.path == "/rule":
                workflow.record_rule(DF, get("key"), get("status"), now(), get("note"))
                return self.redirect("/pending", "Settlement rule saved.")
            if u.path == "/evidence":
                workflow.record_evidence_review(DF, get("decision"), get("summary"), now(), get("pm") == "yes",
                                                get("reviewer"))
                return self.redirect("/performance", "Evidence review saved.")
            if u.path == "/handover":
                DF.confirm_handover(now())
                return self.redirect("/pending", f"Handover confirmed: {DF.host} now runs PredictBot.")
            if u.path == "/stop":
                DF.release_session(now())
                threading.Thread(target=SERVER.shutdown, daemon=True).start()
                return self.send("<!doctype html><meta charset='utf-8'><title>PredictBot stopped</title>"
                                 f"<style>{CSS}</style><h2>PredictBot stopped on {e(DF.host)}</h2><p>Before using the "
                                 "other laptop, wait until OneDrive shows <b>Up to date</b> on both.</p>")
            if u.path == "/merge":
                done = DF.merge_conflict_copies()
                return self.redirect("/pending", "Merged: " + ", ".join(f"{f} (+{n} rows)" for f, n in done))
        except InvalidRecord as exc:
            return self.send(layout(u.path, f"<p class='err'>{e(str(exc))}</p>"))
        self.send_error(404)

    def redirect(self, where, msg=""):
        from urllib.parse import quote
        self.send_response(303)
        self.send_header("Location", where + (f"?msg={quote(msg)}" if msg else ""))
        self.end_headers()

    def send(self, body):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    """Holding the port is the per-laptop lock: a second copy cannot bind it.
    Windows lets SO_REUSEADDR sockets share a port, so use exclusive use there."""
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def open_server(port: int = PORT) -> Server | None:
    """The app's server, or None if PredictBot already runs on this laptop."""
    try:
        return Server(("127.0.0.1", port), Handler)
    except OSError:
        return None


def host_name() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname() or "this laptop"


def heartbeat_loop(stop: threading.Event) -> None:
    while not stop.wait(60):
        if not DF.heartbeat(now()):
            print("Another laptop has taken over the data folder; this app is now read-only.")


def main():
    global DF, SERVER
    url = f"http://localhost:{PORT}"
    server = SERVER = open_server()
    if server is None:
        print(f"PredictBot is already running on this laptop (port {PORT} is taken): opening {url}")
        webbrowser.open(url)
        return
    DF = DataFolder(data_root(), host_name())
    other = DF.claim_session(now())
    if other:
        print(f"Warning: {other.get('host')} last ran PredictBot on this data folder (last seen "
              f"{other.get('heartbeat_utc')}) and did not close it. Writes are blocked until you confirm the "
              "handover on the Pending page.")
    if not DF.write_block():
        for csv_path in sorted(DF.root.glob("pcbf-ledger*.csv")):
            added = workflow.import_pcbf_ledger(DF, csv_path, now())["imported"]
            if added:
                print(f"Imported {len(added)} rows from {csv_path.name}")
    print(f"Data folder: {DF.root}\nPredictBot running at {url} on {DF.host}.\nStop it with the Stop PredictBot "
          "button (or Ctrl+C here) before switching laptops.")
    stop = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(stop,), daemon=True).start()
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        DF.release_session(now())
        print("Stopped. Wait for OneDrive to show Up to date before using the other laptop.")


if __name__ == "__main__":
    main()
