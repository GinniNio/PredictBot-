"""PredictBot local interface: a thin layer over workflow.py.

    python predictbot/app.py "C:\\Users\\you\\OneDrive\\PredictBot"

Opens http://localhost:8000. The data folder is the first argument, else the
PREDICTBOT_DATA environment variable, else ~/OneDrive/PredictBot when OneDrive
exists, else ~/PredictBotData. New bet9ja-*.json files in ~/Downloads are
copied into the data folder's captures/ on every page load.

Informational only: it never places a wager. Stdlib only.
"""

from __future__ import annotations

import html
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pcbf  # noqa: E402
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

def page_candidates() -> str:
    imported = DF.import_captures(DOWNLOADS)
    loaded = workflow.load_candidates(DF, now())
    acc, rej = loaded["accepted"], loaded["rejected"]
    sent = workflow.recently_sent(DF, now())
    waiting = [c for c in acc if c["event_id"] not in sent]
    sports = sorted({c["sport"] for c in acc} | {c["sport"] for c, _ in rej})
    per = []
    for s in sports:
        reasons = {}
        for c, r in rej:
            if c["sport"] == s:
                reasons[r] = reasons.get(r, 0) + 1
        per.append([e(s), sum(1 for c in acc if c["sport"] == s),
                    e(", ".join(f"{n} {r}" for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])) or "-")])
    out = [f"<p>{len(loaded['files'])} capture files read{f', {imported} new from Downloads' if imported else ''}; "
           f"{loaded['duplicates']} duplicate fixture copies merged (newest capture kept). "
           f"<b>{len(acc)}</b> candidates, {len(acc) - len(waiting)} already sent or priced in the last 24h, "
           f"<b>{len(waiting)}</b> waiting; {len(rej)} screened out. Packs hold up to {workflow.PACK_SIZE}, earliest kickoff first.</p>",
           table(["Sport", "To benchmark", "Not sent (reason)"], per, "num"),
           "<form method='post' action='/pack'><p>"]
    for s in sports:
        if any(c["sport"] == s for c in acc):
            out.append(f"<label><input type='checkbox' name='sport' value='{e(s)}' checked> {e(s)}</label> ")
    out.append(f"</p><button>Create next research pack ({min(len(waiting), workflow.PACK_SIZE)} fixtures)</button></form>")
    rows = [[e(c["sport"]), e(c["competition"]), e(c["kickoff_utc"]), e(f"{c['home']} v {c['away']}"), e(c["market"]),
             e(" / ".join(f"{float(o):.2f}" for o in c["odds"])), f"{pcbf.book_total([float(o) for o in c['odds']]) * 100:.1f}%"]
            for c in acc]
    out.append("<h2>Candidates</h2>" + table(["Sport", "Competition", "Kickoff UTC", "Game", "Market", "Bet9ja", "Book"], rows))
    rrows = [[e(c["sport"]), e(c["competition"]), e(c["kickoff_utc"]), e(f"{c['home']} v {c['away']}"), e(r)]
             for c, r in rej]
    out.append(f"<details><summary>Not sent ({len(rej)}), with reasons</summary>"
               + table(["Sport", "Competition", "Kickoff UTC", "Game", "Reason"], rrows) + "</details>")
    out.append("<details><summary>Files</summary>" + table(["File", "Schema", "Content"],
               [[e(a), e(b), e(c)] for a, b, c in loaded["files"]]) + "</details>")
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
    shown = [r for r in recs if r["tier"] in ("PICK", "WATCH")]
    shown.sort(key=lambda r: (r["tier"] != "PICK", -float(r["edge_pct"] or 0)))
    rows = [[f"<b class='{r['tier']}'>{r['tier']}</b>", e(r["selection"]), e(r["event_name"]), e(r["kickoff_utc"]),
             e(r["bookmaker_odds"]), e(r["fair_odds"]), pct(float(r["edge_pct"])),
             f"<a href='{e(r['benchmark_url'])}'>{e(r['benchmark_source'])}</a> {e(r['benchmark_timestamp_utc'])}",
             e("; ".join(x for x in (r["caution_flags"], r["research_note"]) if x))] for r in shown]
    other = [[e(r["tier"]), e(r["event_name"]), e(r["selection"]), e(r["rejection_reason"])]
             for r in recs if r["tier"] in ("RESEARCH", "REJECTED")]
    events = {r["selection_id"].rsplit(":", 1)[0]: r["tier"] for r in recs}
    priced = sum(1 for t in events.values() if t in ("PICK", "WATCH"))
    out = [f"<p>Saved {len(recs)} selection records from pack {e(res['pack_id'])} before any bet.</p>",
           table(["Tier", "Selection", "Game", "Kickoff UTC", "Bet9ja", "Fair", "Edge", "Benchmark", "Note"], rows),
           f"<p>Games received {len(events) + len(res['unanswered'])} / priced {priced} / "
           f"not priced {len(events) - priced} / no reply {len(res['unanswered'])}. "
           "Recheck every Bet9ja price before placing.</p>"]
    if other:
        out.append("<h2>Not priced</h2>" + table(["Tier", "Game", "Selection", "Reason"], other))
    if res["unknown_codes"]:
        out.append(f"<p class='err'>Codes not in the pack: {e(', '.join(res['unknown_codes']))}</p>")
    if res["unanswered"]:
        out.append(f"<p class='dim'>No reply line for: {e(', '.join(res['unanswered']))}</p>")
    return "".join(out)


def gate_banner(perf) -> str:
    g = perf["gate"]
    state = "UNLOCKED" if g["unlocked"] else "LOCKED"
    return (f"<div class='gate {state}'>Real money: <b>{state}</b>. Settled PICKs {g['settled_picks']} / {g['needed']}; "
            f"mean CLV {'> 0' if g['mean_clv_positive'] else 'not > 0'}; ROI {'> 0' if g['roi_positive'] else 'not > 0'}. "
            "The app never places bets.</div>")


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
    tabs = " ".join(f"<a href='/selections?tier={t}'>{t or 'all'}</a>" for t in ("", "PICK", "WATCH", "RESEARCH", "REJECTED"))
    return (gate_banner(perf_now()) + f"<p>{tabs}</p>"
            "<form method='post' action='/settlepack'><button>Create settlement pack</button> "
            "<span class='dim'>for games that kicked off 3+ hours ago</span></form>"
            + table(["Tier", "Priced", "Sport", "Game", "Selection", "Bet9ja", "Fair", "Edge", "Source", "Result",
                     "CLV", "Reason / cautions", "ID"], rows))


def page_bets(msg="") -> str:
    sels = [s for s in DF.read("selection") if s["tier"] != "REJECTED"][-200:]
    opts = "".join(f"<option value='{e(s['selection_record_id'])}'>{e(s['tier'])} {e(s['event_name'])}: "
                   f"{e(s['selection'])} @ {e(s['bookmaker_odds'])}</option>" for s in reversed(sels))
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


def page_performance() -> str:
    perf = perf_now()
    head = ["Group", "n", "Settled", "With CLV", "Mean CLV", "Median CLV", "CLV +/-", "Win rate", "Unit ROI"]

    def rows(d):
        return [[e(k), v["n"], v["settled"], v["with_clv"], pct(v["mean_clv"]), pct(v["median_clv"]),
                 f"{v['positive_clv']} / {v['negative_clv']}", pct(v["win_rate"], 0).lstrip("+"), pct(v["unit_roi"])]
                for k, v in d.items()]
    b = perf["bets"]
    out = [gate_banner(perf), "<h2>By tier</h2>", table(head, rows(perf["by_tier"]), "num"),
           f"<p>PICK notional ROI (₦25 stakes): <b>{pct(perf['pick_notional_roi'])}</b>. "
           f"Actual bets: {b['n']} logged, {b['settled']} settled, stake-weighted ROI {pct(b['stake_weighted_roi'])}.</p>",
           "<p class='dim'>Early negative CLV is a signal to check data quality, market matching, timestamps and "
           "benchmark source before changing rules. Small samples are noise.</p>"]
    for dim in ("sport", "market", "odds_band", "benchmark_source"):
        out.append(f"<h2>By {dim.replace('_', ' ')}</h2>" + table(head, rows(perf[f"by_{dim}"]), "num"))
    return "".join(out)


# ------------------------------------------------------------------ server

NAV = [("/", "Candidates"), ("/pack", "Research pack"), ("/selections", "Selections"), ("/bets", "Bets"),
       ("/performance", "Performance")]

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
.PICK{color:var(--pos)} .REJECTED{color:var(--neg)} .RESEARCH{color:var(--dim)} .dim{color:var(--dim)}
.err{color:var(--neg)} .msg{color:var(--pos)} code{font-size:11px;color:var(--dim)}
.gate{padding:10px 12px;border-radius:8px;background:var(--card);border-left:4px solid var(--neg);margin:8px 0}
.gate.UNLOCKED{border-left-color:var(--pos)} details{margin-top:12px} label{margin-right:10px}
"""


def layout(path, body):
    nav = "".join(f"<a class='{'on' if p == path else ''}' href='{p}'>{t}</a>" for p, t in NAV)
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>PredictBot</title><style>{CSS}</style></head><body><header><strong>PredictBot</strong><nav>{nav}</nav>"
            f"<span class='r'>data: {e(str(DF.root))}</span></header>{body}</body></html>")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        pages = {"/": page_candidates, "/pack": lambda: page_pack("research"), "/settle": lambda: page_pack("settle"),
                 "/selections": lambda: page_selections(parse_qs(u.query)), "/bets": page_bets,
                 "/performance": page_performance}
        if u.path not in pages:
            return self.send_error(404)
        self.send(layout(u.path, pages[u.path]()))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
        get = lambda k: (form.get(k) or [""])[0]
        try:
            if u.path == "/pack":
                workflow.create_pack(DF, now(), set(form.get("sport") or []) or None, [])
                return self.redirect("/pack")
            if u.path == "/reply":
                return self.send(layout("/pack", page_reply_result(workflow.apply_reply(DF, get("reply"), now()))))
            if u.path == "/settlepack":
                return self.redirect("/settle" if workflow.create_settle_pack(DF, now()) else "/selections")
            if u.path == "/settle":
                r = workflow.apply_settlement(DF, get("reply"), now())
                body = (f"<p class='err'>{e(r['error'])}</p>" if r.get("error") else
                        f"<p class='msg'>Settled {r['settled']} selections, {r['closing']} closing prices.</p>"
                        + table(["Code", "Problem", "Line"], [[e(a), e(b), e(c)] for a, b, c in r["problems"]])
                        + (f"<p class='dim'>Skipped: {e(str(r['skipped']))}</p>" if r["skipped"] else ""))
                return self.send(layout("/selections", body + page_selections({})))
            if u.path == "/bet":
                workflow.record_bet(DF, get("selection_record_id"), get("stake"), get("odds"), get("placed"),
                                    get("ticket"))
                return self.send(layout("/bets", page_bets("Bet saved.")))
        except InvalidRecord as exc:
            return self.send(layout(u.path, f"<p class='err'>{e(str(exc))}</p>"))
        self.send_error(404)

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
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


def main():
    global DF
    DF = DataFolder(data_root())
    for csv_path in sorted(DF.root.glob("pcbf-ledger*.csv")):
        added = workflow.import_pcbf_ledger(DF, csv_path, now())["imported"]
        if added:
            print(f"Imported {len(added)} rows from {csv_path.name}")
    url = f"http://localhost:{PORT}"
    print(f"Data folder: {DF.root}\nPredictBot running at {url} (Ctrl+C to stop)")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
