"""PredictBot local web app.

    python app.py                    -> opens http://localhost:8000
    python app.py D:/Bet9ja          -> also read this folder

Capture with the browser extension, then refresh the page. The app reads
every Bet9ja capture JSON in your Downloads folder, captures/, my_captures/
and any folder given on the command line (subfolders included).

  My record  - money staked and returned, profit/loss, and where you win
               or lose: legs by sport, competition, market and odds band.
  Fixtures   - latest odds capture with fair probabilities (bookmaker
               margin removed) and the Elo model where it covers the league.

Returns are calculated from leg results and stake buckets, because Bet9ja
does not show payouts for settled system tickets. Read-only: it never
writes to the forecast or betting ledgers. Stdlib only, no installs.
"""

from __future__ import annotations

import html
import itertools
import json
import re
import sys
import webbrowser
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "my_captures"
SCAN_DIRS = [Path.home() / "Downloads", ROOT / "captures", INBOX]
PORT = 8000

sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


# ---------------------------------------------------------------- loading

def load_captures() -> list[tuple[Path, dict]]:
    found, seen = [], set()
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.json")):
            if not path.name.startswith(("bet9ja-", "PredictBot_")) or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "schema_version" in data:
                found.append((path, data))
    return found


def dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


# ------------------------------------------------------------------ bets

def all_tickets(captures) -> list[dict]:
    """One row per ticket id; the newest capture of a ticket wins, so a
    ticket seen open and later settled shows as settled."""
    tickets: dict[str, dict] = {}
    for _path, data in captures:
        schema = str(data.get("schema_version", ""))
        if not schema.startswith(("bet9ja-ticket-capture", "bet9ja-settled-bets")):
            continue
        for t in data.get("tickets") or []:
            tid = t.get("bet9ja_ticket_id")
            if not tid:
                continue
            row = {
                "id": tid,
                "placed_raw": t.get("placed_at_raw") or "",
                "placed": parse_placed(t.get("placed_at_raw")),
                "status": t.get("ticket_status") or t.get("status") or "?",
                "stake": dec(t.get("total_stake")) or Decimal(0),
                "buckets": t.get("stake_buckets") or [],
                "legs": t.get("legs") or [],
                "captured": t.get("captured_at_utc") or data.get("captured_at_utc") or "",
            }
            prev = tickets.get(tid)
            if prev is None or row["captured"] >= prev["captured"]:
                tickets[tid] = row
    for t in tickets.values():
        t["max_win"] = system_return(t, only_won=False)
        t["returned"] = system_return(t, only_won=True) if t["status"] in ("WON", "LOST") else None
    return sorted(tickets.values(), key=lambda r: (r["placed"] or datetime.min, r["id"]), reverse=True)


def parse_placed(raw):
    try:
        return datetime.strptime(raw.strip(), "%d %b %Y %H:%M")
    except (AttributeError, ValueError):
        return None


def system_return(ticket, only_won: bool) -> Decimal | None:
    """Sum over every combination in every stake bucket of unit stake x
    product of odds. only_won: count only combinations whose legs all won
    (void legs count at odds 1). Matches Bet9ja's own Max Win on the
    captured open tickets."""
    legs, buckets = ticket["legs"], ticket["buckets"]
    if not buckets:
        if only_won and legs and all(l.get("leg_status") == "LOST" for l in legs):
            return Decimal(0)
        return None
    statuses = [l.get("leg_status") for l in legs]
    odds = [Decimal(1) if s == "VOID" else (dec(l.get("odds")) or Decimal(0)) for l, s in zip(legs, statuses)]
    total = Decimal(0)
    for b in buckets:
        k, unit = b.get("fold_size"), dec(b.get("unit_stake"))
        if not k or unit is None:
            return None
        for combo in itertools.combinations(range(len(legs)), k):
            if only_won and not all(statuses[i] in ("WON", "VOID") for i in combo):
                continue
            p = unit
            for i in combo:
                p *= odds[i]
            total += p
    return total.quantize(Decimal("0.01"))


SPORT_RULES = [
    ("Tennis", r"\b(atp|wta|itf|challenger|davis cup|billie jean)\b|singles|doubles"),
    ("Baseball", r"\b(mlb|kbo|npb|cpbl)\b|inning"),
    ("Basketball", r"\b(nba|wnba|euroleague|eurocup|ncaab|acb|basket)"),
    ("Ice hockey", r"\b(nhl|khl|vhl|shl|ahl|del|liiga|mhl|hockey)\b"),
    ("Volleyball", r"volley"),
    ("Table tennis", r"table tennis|setka|tt cup"),
    ("Esports", r"\b(cs2|dota|lol|valorant|esport)"),
]


def guess_sport(leg) -> str:
    text = f"{leg.get('competition_raw') or ''} {leg.get('market_raw') or ''}".lower()
    for sport, pattern in SPORT_RULES:
        if re.search(pattern, text):
            return sport
    market = (leg.get("market_raw") or "").lower()
    if market in ("1x2", "3way", "double chance", "draw no bet", "correct score", "gg/ng", "over/under") or "goal" in market:
        return "Football"
    return "Other / unknown"


def odds_band(o: Decimal) -> str:
    for hi, label in ((Decimal("1.30"), "under 1.30"), (Decimal("1.60"), "1.30 - 1.59"),
                      (Decimal("2.00"), "1.60 - 1.99"), (Decimal("3.00"), "2.00 - 2.99")):
        if o < hi:
            return label
    return "3.00 +"


def settled_legs(tickets) -> list[dict]:
    """Unique picks: the same selection on the same fixture often appears
    on several system tickets and is counted once."""
    out, seen = [], set()
    for t in tickets:
        for l in t["legs"]:
            s, o = l.get("leg_status"), dec(l.get("odds"))
            pick = (l.get("fixture_id") or l.get("fixture_and_time_raw"), l.get("market_raw"), l.get("selection"))
            if s in ("WON", "LOST") and o and o > 0 and pick not in seen:
                seen.add(pick)
                out.append({"won": s == "WON", "odds": o, "sport": guess_sport(l),
                            "competition": l.get("competition_raw") or "?",
                            "market": l.get("market_raw") or "?", "band": odds_band(o)})
    return out


def leg_table(legs, key, limit=None) -> list[tuple]:
    groups = defaultdict(list)
    for l in legs:
        groups[l[key]].append(l)
    rows = []
    for name, ls in groups.items():
        n = len(ls)
        won = sum(l["won"] for l in ls)
        implied = sum(1 / float(l["odds"]) for l in ls) / n
        rows.append((name, n, won, won / n, implied))
    rows.sort(key=lambda r: -r[1])
    if key == "band":
        order = ["under 1.30", "1.30 - 1.59", "1.60 - 1.99", "2.00 - 2.99", "3.00 +"]
        rows.sort(key=lambda r: order.index(r[0]))
    return rows[:limit] if limit else rows


# ------------------------------------------------------------- fixtures

def latest_odds_capture(captures):
    """Newest assembled soccer capture (bet9ja-soccer-all-*); per-run
    segment exports are skipped, the pipeline rejects them."""
    odds = [(p, d) for p, d in captures
            if str(d.get("schema_version", "")).startswith("bet9ja-soccer-session")
            and "-segment-" not in p.name]
    if not odds:
        return None, None
    return max(odds, key=lambda pd: (pd[1].get("captured_at_utc", ""), len(pd[1].get("fixtures") or [])))


def fair_probs(prices: dict) -> dict | None:
    try:
        inv = {k: 1 / float(prices[k]) for k in ("home", "draw", "away")}
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def fixture_rows(capture: dict) -> list[dict]:
    from pcbf_calculator.orchestration.bet9ja_research_session import run_bet9ja_research_session

    result = run_bet9ja_research_session(capture)
    rows = [_fixture_row(i, i["forecast"].get("probabilities"))
            for i in result["forecast_research_ranked"].get("markets", [])]
    rows += [_fixture_row(i, None) for i in result["forecast_abstentions"].get("abstentions", [])]
    rows.sort(key=lambda r: (r["kickoff"] or "", r["competition"] or ""))
    return rows


def _fixture_row(item: dict, model: dict | None) -> dict:
    prices = item.get("market_prices") or {}
    model_hda = None
    if model:
        model_hda = {"home": model.get("home_win"), "draw": model.get("draw"), "away": model.get("away_win")}
    edge = None
    if model_hda and prices:
        edges = {k: model_hda[k] * float(prices[k]) - 1 for k in ("home", "draw", "away")
                 if model_hda.get(k) is not None and prices.get(k)}
        if edges:
            best = max(edges, key=edges.get)
            edge = (best, edges[best])
    return {"kickoff": item.get("kickoff_utc"), "competition": item.get("competition"),
            "country": item.get("country"), "home": item.get("home"), "away": item.get("away"),
            "prices": prices, "fair": fair_probs(prices), "model": model_hda, "edge": edge}


# ------------------------------------------------------------------ html

e = html.escape


def pct(x):
    return "" if x is None else f"{x * 100:.0f}%"


def money(x):
    return "" if x is None else f"{x:,.0f}"


def local_time(iso):
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


def record_html(captures) -> str:
    tickets = all_tickets(captures)
    if not tickets:
        return ("<p>No bet captures found. Use the extension's <b>Capture settled bets</b> and "
                "<b>Capture open bets</b> buttons, then refresh this page.</p>")
    settled = [t for t in tickets if t["returned"] is not None]
    open_ = [t for t in tickets if t["status"] == "OPEN"]
    staked = sum(t["stake"] for t in settled)
    back = sum(t["returned"] for t in settled)
    pnl = back - staked
    roi = pnl / staked if staked else None
    marked_won = [t for t in settled if t["status"] == "WON"]
    won_below_stake = [t for t in marked_won if t["returned"] < t["stake"]]
    skipped = [t for t in tickets if t["status"] in ("WON", "LOST") and t["returned"] is None]

    out = ["<div class='cards'>",
           f"<div><b>{money(staked)}</b>staked (settled)</div>",
           f"<div><b>{money(back)}</b>returned</div>",
           f"<div class='{'pos' if pnl >= 0 else 'neg'}'><b>{'+' if pnl >= 0 else ''}{money(pnl)}</b>profit / loss"
           f"{f' ({roi * 100:+.0f}%)' if roi is not None else ''}</div>",
           f"<div><b>{len(settled)}</b>settled tickets</div>",
           f"<div><b>{len(open_)}</b>open, {money(sum(t['stake'] for t in open_))} staked, "
           f"max win {money(sum(t['max_win'] or 0 for t in open_))}</div>",
           "</div>"]
    if marked_won:
        out.append(f"<p class='note'>Bet9ja marked {len(marked_won)} system tickets <b>Won</b> because at least "
                   f"one combination won. {len(won_below_stake)} of them returned less than their stake.</p>")
    if skipped:
        out.append(f"<p class='dim'>{len(skipped)} settled ticket(s) left out: no stake breakdown captured.</p>")

    legs = settled_legs(settled)
    if legs:
        won = sum(l["won"] for l in legs)
        implied = sum(1 / float(l["odds"]) for l in legs) / len(legs)
        out.append(f"<h2>Your picks</h2><p>{len(legs)} unique settled picks (each counted once, however many tickets it was on). They won <b>{won / len(legs) * 100:.0f}%</b>; "
                   f"their odds implied <b>{implied * 100:.0f}%</b>. A gap below zero means picks lose more often than "
                   f"the odds suggest. Rows with fewer than about 20 picks are mostly noise.</p><div class='grid'>")
        for title, key, limit in (("By sport (guessed)", "sport", None), ("By odds", "band", None),
                                  ("By market", "market", 10), ("By competition", "competition", 15)):
            out.append(f"<div><h3>{title}</h3><table class=num><tr><th></th><th>Picks</th><th>Won</th><th>Odds implied</th><th>Gap</th></tr>")
            for name, n, w, rate, imp in leg_table(legs, key, limit):
                gap = rate - imp
                out.append(f"<tr><td>{e(str(name))}</td><td>{n}</td><td>{rate * 100:.0f}%</td><td>{imp * 100:.0f}%</td>"
                           f"<td class='{'pos' if gap >= 0 else 'neg'}'>{gap * 100:+.0f}</td></tr>")
            out.append("</table></div>")
        out.append("</div>")

    by_size = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    for t in settled:
        s = by_size[len(t["legs"])]
        s[0] += 1; s[1] += t["stake"]; s[2] += t["returned"]
    out.append("<h2>By ticket size</h2><table class=num><tr><th>Legs per ticket</th><th>Tickets</th><th>Staked</th>"
               "<th>Returned</th><th>Profit / loss</th></tr>")
    for size in sorted(by_size):
        n, st, rt = by_size[size]
        out.append(f"<tr><td>{size}</td><td>{n}</td><td>{money(st)}</td><td>{money(rt)}</td>"
                   f"<td class='{'pos' if rt >= st else 'neg'}'>{money(rt - st)}</td></tr>")
    out.append("</table>")

    by_day = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    for t in settled:
        d = by_day[t["placed"].strftime("%Y-%m-%d") if t["placed"] else "unknown"]
        d[0] += 1; d[1] += t["stake"]; d[2] += t["returned"]
    out.append("<h2>By day placed</h2><table class=num><tr><th>Day</th><th>Tickets</th><th>Staked</th><th>Returned</th>"
               "<th>Profit / loss</th></tr>")
    for day in sorted(by_day, reverse=True):
        n, st, rt = by_day[day]
        out.append(f"<tr><td>{e(day)}</td><td>{n}</td><td>{money(st)}</td><td>{money(rt)}</td>"
                   f"<td class='{'pos' if rt >= st else 'neg'}'>{money(rt - st)}</td></tr>")
    out.append("</table>")

    out.append("<details><summary>All tickets</summary><table><tr><th>Placed</th><th>Ticket</th><th>Legs</th>"
               "<th>Stake</th><th>Returned</th><th>Status</th></tr>")
    for t in tickets:
        legs_html = "".join(
            f"<li>{e(l.get('fixture_and_time_raw') or '')}: <b>{e(str(l.get('selection') or ''))}</b> "
            f"@ {e(str(l.get('odds') or ''))} <small>{e(l.get('leg_status') or '')}</small></li>" for l in t["legs"])
        shown = money(t["returned"]) if t["returned"] is not None else (f"max {money(t['max_win'])}" if t["max_win"] else "")
        out.append(f"<tr><td>{e(t['placed_raw'])}</td><td>{e(t['id'])}</td>"
                   f"<td><details><summary>{len(t['legs'])}</summary><ul>{legs_html}</ul></details></td>"
                   f"<td>{money(t['stake'])}</td><td>{shown}</td><td class='st {e(t['status'])}'>{e(t['status'])}</td></tr>")
    out.append("</table></details>")
    out.append("<p class='dim'>Returns are calculated from leg results and stake buckets (void legs at odds 1). "
               "Bet9ja bonuses or tax, if any, are not included.</p>")
    return "".join(out)


def fixtures_html(captures) -> str:
    path, capture = latest_odds_capture(captures)
    if capture is None:
        return "<p>No odds capture found. Use the extension's soccer capture, then refresh.</p>"
    try:
        rows = fixture_rows(capture)
    except Exception as exc:
        return f"<p class='err'>Could not process {e(path.name)}: {e(repr(exc))}</p>"
    modelled = sum(1 for r in rows if r["model"])
    out = [f"<p>{e(path.name)}, captured {e(local_time(capture.get('captured_at_utc')))}. {len(rows)} fixtures; "
           f"the model covers {modelled} (EPL, Bundesliga, La Liga, Serie A, Ligue 1 only).</p>",
           "<table><tr><th>Kickoff</th><th>Competition</th><th>Match</th><th>Odds 1 / X / 2</th>"
           "<th>Fair %</th><th>Model %</th><th>Edge</th></tr>"]
    for r in rows:
        p, f, m = r["prices"], r["fair"], r["model"]
        edge = ""
        if r["edge"]:
            side, val = r["edge"]
            edge = f"<span class='{'pos' if val > 0 else 'neg'}'>{ {'home': '1', 'draw': 'X', 'away': '2'}[side]} {val * 100:+.0f}%</span>"
        out.append(f"<tr><td>{e(local_time(r['kickoff']))}</td><td>{e(r['competition'] or '')} "
                   f"<small>{e(r['country'] or '')}</small></td><td>{e(r['home'] or '')} v {e(r['away'] or '')}</td>"
                   f"<td>{e(' / '.join(str(p.get(k, '')) for k in ('home', 'draw', 'away')))}</td>"
                   f"<td>{' / '.join(pct(f[k]) for k in ('home', 'draw', 'away')) if f else ''}</td>"
                   f"<td>{' / '.join(pct(m[k]) for k in ('home', 'draw', 'away')) if m else '<span class=dim>-</span>'}</td>"
                   f"<td>{edge}</td></tr>")
    out.append("</table>")
    return "".join(out)


def page(tab: str, message: str = "") -> str:
    captures = load_captures()
    body = fixtures_html(captures) if tab == "fixtures" else record_html(captures)
    body = body.replace("<table", "<div class='tw'><table").replace("</table>", "</table></div>")
    folders = "".join(f"<li>{e(str(d))}{'' if d.exists() else ' (not found)'}</li>" for d in SCAN_DIRS)
    return TEMPLATE.format(rec="active" if tab != "fixtures" else "", fx="active" if tab == "fixtures" else "",
                           message=f"<p class='msg'>{e(message)}</p>" if message else "", body=body,
                           n=len(captures), folders=folders)


TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PredictBot</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#1d1d1b;--dim:#8a8a85;--line:#e7e7e3;--pos:#16794a;--neg:#b42318}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--card:#1f1f1f;--fg:#ececec;--dim:#8f8f8f;--line:#2e2e2e;--pos:#4cc38a;--neg:#ff6b5e}}}}
body{{font:14px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:var(--fg);background:var(--bg)}}
header{{display:flex;gap:20px;align-items:baseline;flex-wrap:wrap;margin-bottom:8px}}
header strong{{font-size:18px}} nav a{{margin-right:14px;color:var(--dim);text-decoration:none;font-weight:600}}
nav a.active{{color:var(--fg);border-bottom:2px solid var(--fg)}} header .r{{margin-left:auto;color:var(--dim)}}
h2{{font-size:16px;margin:28px 0 8px}} h3{{font-size:13px;margin:0 0 6px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(150px,100%),1fr));gap:10px;margin:12px 0}}
.cards div{{background:var(--card);padding:12px 14px;border-radius:10px;border:1px solid var(--line);color:var(--dim)}}
.cards b{{display:block;font-size:22px;color:var(--fg)}} .cards .pos b{{color:var(--pos)}} .cards .neg b{{color:var(--neg)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr));gap:16px}} .tw{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:var(--card);border-radius:8px;overflow:hidden}}
th,td{{padding:5px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--dim);font-weight:600;font-size:12px}} table.num td:not(:first-child),table.num th:not(:first-child){{text-align:right}}
small,.dim{{color:var(--dim)}} .pos{{color:var(--pos)}} .neg{{color:var(--neg)}}
.note{{background:var(--card);border-left:3px solid var(--neg);padding:8px 12px}}
.st.WON{{color:var(--pos)}} .st.LOST{{color:var(--neg)}} .msg{{color:var(--pos)}} .err{{color:var(--neg)}}
details{{margin-top:16px}} ul{{margin:4px 0;padding-left:18px;text-align:left}}
#drop{{border:1px dashed var(--line);padding:8px;text-align:center;color:var(--dim);border-radius:8px;margin-top:24px;cursor:pointer}}
@media (max-width:600px){{.hide-sm{{display:none}}}}
</style></head><body>
<header><strong>PredictBot</strong><nav><a class="{rec}" href="/">My record</a><a class="{fx}" href="/fixtures">Fixtures</a></nav>
<span class="r">{n} capture files &middot; <a href="" style="color:inherit">refresh</a></span></header>
{message}{body}
<details><summary class="dim">Folders read</summary><ul>{folders}</ul></details>
<div id="drop">Files somewhere else? Drop them here (copied to my_captures)<input type="file" id="pick" multiple accept=".json" hidden></div>
<script>
const drop=document.getElementById('drop'),pick=document.getElementById('pick');
drop.onclick=()=>pick.click();
drop.ondragover=ev=>ev.preventDefault();
async function send(files){{let n=0;for(const f of files){{const r=await fetch('/upload/'+encodeURIComponent(f.name),{{method:'POST',body:f}});if(r.ok)n++;else alert(f.name+': '+await r.text())}}
location.href=location.pathname+'?added='+n}}
drop.ondrop=ev=>{{ev.preventDefault();send(ev.dataTransfer.files)}};
pick.onchange=()=>send(pick.files);
</script></body></html>"""


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path not in ("/", "/fixtures"):
            self.send_error(404)
            return
        added = re.search(r"added=(\d+)", query)
        self._send(200, page("fixtures" if path == "/fixtures" else "record",
                             f"Added {added.group(1)} file(s)." if added else ""))

    def do_POST(self):
        if not self.path.startswith("/upload/"):
            self.send_error(404)
            return
        name = Path(unquote(self.path[len("/upload/"):])).name
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if not name.endswith(".json"):
            self._send(400, "only .json files", "text/plain")
            return
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or "schema_version" not in data:
                raise ValueError("no schema_version field, not a Bet9ja capture")
        except ValueError as exc:
            self._send(400, f"rejected: {exc}", "text/plain")
            return
        INBOX.mkdir(exist_ok=True)
        target = INBOX / name
        if target.exists() and target.read_bytes() != raw:
            target = INBOX / f"{target.stem}.{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json"
        target.write_bytes(raw)
        self._send(200, "ok", "text/plain")

    def _send(self, code, text, ctype="text/html; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    for arg in sys.argv[1:]:
        folder = Path(arg).expanduser().resolve()
        if not folder.is_dir():
            sys.exit(f"Not a folder: {folder}")
        SCAN_DIRS.append(folder)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print("Reading captures from:", *SCAN_DIRS, sep="\n  ")
    print(f"PredictBot running at {url}  (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
