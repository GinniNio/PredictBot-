"""PredictBot local web app.

    python app.py            -> opens http://localhost:8000

Drop Bet9ja capture JSON files (from the browser extension) onto the page,
or put them in my_captures/ yourself. The app reads every capture in
captures/ and my_captures/ and shows:

  Fixtures  - latest odds capture: bookmaker odds, the market's fair
              probabilities (margin removed), and the Elo model's
              probabilities where the model covers the league.
  My bets   - every ticket from open/settled bet captures, latest
              capture per ticket wins, with stake and win/loss totals.

Read-only: it never writes to the forecast or betting ledgers.
Stdlib only, no installs needed.
"""

from __future__ import annotations

import html
import json
import re
import sys
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
INBOX = ROOT / "my_captures"
SCAN_DIRS = [ROOT / "captures", INBOX]
PORT = 8000

sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


# ---------------------------------------------------------------- loading

def load_captures() -> list[tuple[Path, dict]]:
    found = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and "schema_version" in data:
                found.append((path, data))
    return found


def latest_odds_capture(captures):
    """Newest assembled soccer capture (bet9ja-soccer-all-*); per-run
    segment exports are skipped, the pipeline rejects them."""
    odds = [(p, d) for p, d in captures
            if str(d.get("schema_version", "")).startswith("bet9ja-soccer-session")
            and "-segment-" not in p.name]
    if not odds:
        return None, None
    return max(odds, key=lambda pd: (pd[1].get("captured_at_utc", ""), len(pd[1].get("fixtures") or [])))


# --------------------------------------------------------------- fixtures

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
    rows = []
    for item in result["forecast_research_ranked"].get("markets", []):
        rows.append(_row(item, item["forecast"].get("probabilities"), None))
    for item in result["forecast_abstentions"].get("abstentions", []):
        rows.append(_row(item, None, item.get("reason")))
    rows.sort(key=lambda r: (r["kickoff"] or "", r["competition"] or ""))
    return rows


def _row(item: dict, model: dict | None, reason: str | None) -> dict:
    prices = item.get("market_prices") or {}
    fair = fair_probs(prices)
    model_hda = None
    if model:
        model_hda = {"home": model.get("home_win"), "draw": model.get("draw"), "away": model.get("away_win")}
    edge = None
    if model_hda and prices:
        # Expected return per 1 staked at the bookmaker's price, using the model's probability.
        edges = {k: model_hda[k] * float(prices[k]) - 1 for k in ("home", "draw", "away")
                 if model_hda.get(k) is not None and prices.get(k)}
        if edges:
            best = max(edges, key=edges.get)
            edge = (best, edges[best])
    return {
        "kickoff": item.get("kickoff_utc"),
        "competition": item.get("competition"),
        "country": item.get("country"),
        "home": item.get("home"),
        "away": item.get("away"),
        "prices": prices,
        "fair": fair,
        "model": model_hda,
        "edge": edge,
        "reason": reason,
    }


# ------------------------------------------------------------------- bets

def dec(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def all_tickets(captures) -> list[dict]:
    tickets: dict[str, dict] = {}
    for _path, data in captures:
        schema = str(data.get("schema_version", ""))
        if not schema.startswith(("bet9ja-ticket-capture", "bet9ja-settled-bets")):
            continue
        for t in data.get("tickets") or []:
            tid = t.get("bet9ja_ticket_id")
            if not tid:
                continue
            status = t.get("ticket_status") or t.get("status") or "?"
            row = {
                "id": tid,
                "placed": t.get("placed_at_raw"),
                "type": t.get("ticket_type_normalized") or t.get("ticket_type_raw") or "",
                "status": status,
                "stake": dec(t.get("total_stake")),
                "potential": dec(t.get("potential_return")),
                "payout": dec(t.get("actual_payout")),
                "legs": t.get("legs") or [],
                "captured": t.get("captured_at_utc") or data.get("captured_at_utc") or "",
            }
            prev = tickets.get(tid)
            if prev is None or row["captured"] >= prev["captured"]:
                tickets[tid] = row
    return sorted(tickets.values(), key=lambda r: r["captured"], reverse=True)


def bet_totals(tickets) -> dict:
    def total(rows):
        return sum((r["stake"] or Decimal(0)) for r in rows)
    won = [t for t in tickets if t["status"] == "WON"]
    lost = [t for t in tickets if t["status"] == "LOST"]
    open_ = [t for t in tickets if t["status"] == "OPEN"]
    paid = [t for t in won if t["payout"] is not None]
    return {
        "count": len(tickets), "won": len(won), "lost": len(lost), "open": len(open_),
        "staked_settled": total(won + lost), "staked_open": total(open_),
        "lost_stake": total(lost),
        "payout_known": sum((t["payout"] for t in paid), Decimal(0)),
        "won_missing_payout": len(won) - len(paid),
    }


# ------------------------------------------------------------------- html

e = html.escape


def pct(x):
    return "" if x is None else f"{x * 100:.0f}%"


def money(x):
    return "" if x is None else f"{x:,.2f}"


def kickoff_local(iso):
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


def page(tab: str, message: str = "") -> str:
    captures = load_captures()
    if tab == "bets":
        body = bets_html(captures)
    else:
        body = fixtures_html(captures)
    files = "".join(f"<li>{e(str(p.relative_to(ROOT)))} <small>{e(str(d.get('schema_version')))}</small></li>"
                    for p, d in captures)
    return TEMPLATE.format(
        fx_active="active" if tab != "bets" else "",
        bets_active="active" if tab == "bets" else "",
        message=f'<p class="msg">{e(message)}</p>' if message else "",
        body=body,
        files=files or "<li>none yet</li>",
    )


def fixtures_html(captures) -> str:
    path, capture = latest_odds_capture(captures)
    if capture is None:
        return "<p>No odds capture yet. Drop a <code>bet9ja-soccer-all-soccer-*.json</code> file above.</p>"
    try:
        rows = fixture_rows(capture)
    except Exception as exc:  # show the error, don't crash the page
        return f"<p class='err'>Could not process {e(path.name)}: {e(repr(exc))}</p>"
    modelled = [r for r in rows if r["model"]]
    head = (f"<p>From <b>{e(path.name)}</b> (captured {e(kickoff_local(capture.get('captured_at_utc')))}). "
            f"{len(rows)} fixtures. Model covers <b>{len(modelled)}</b>; "
            f"the rest are outside its leagues (EPL, Bundesliga, La Liga, Serie A, Ligue 1).</p>")
    out = [head, "<label><input type='checkbox' id='onlymodel'> show only fixtures the model covers</label>",
           "<table id='fx'><tr><th>Kickoff</th><th>Competition</th><th>Match</th>"
           "<th>Odds 1 / X / 2</th><th>Market fair %</th><th>Model %</th><th>Best edge</th></tr>"]
    for r in rows:
        p, f, m = r["prices"], r["fair"], r["model"]
        odds = " / ".join(str(p.get(k, "")) for k in ("home", "draw", "away"))
        fair = " / ".join(pct(f[k]) for k in ("home", "draw", "away")) if f else ""
        model = " / ".join(pct(m[k]) for k in ("home", "draw", "away")) if m else "<span class='dim'>not covered</span>"
        edge = ""
        if r["edge"]:
            side, val = r["edge"]
            cls = "pos" if val > 0 else "neg"
            edge = f"<span class='{cls}'>{ {'home': '1', 'draw': 'X', 'away': '2'}[side]} {val * 100:+.1f}%</span>"
        out.append(f"<tr class='{'m' if m else 'nm'}'><td>{e(kickoff_local(r['kickoff']))}</td>"
                   f"<td>{e(r['competition'] or '')}<br><small>{e(r['country'] or '')}</small></td>"
                   f"<td>{e(r['home'] or '')} v {e(r['away'] or '')}</td><td>{e(odds)}</td>"
                   f"<td>{fair}</td><td>{model}</td><td>{edge}</td></tr>")
    out.append("</table><p class='dim'>Edge = model probability x odds - 1. The model's tested accuracy "
               "is below de-vigged bookmaker odds (README), so a positive edge is a research lead, not a bet.</p>")
    return "".join(out)


def bets_html(captures) -> str:
    tickets = all_tickets(captures)
    if not tickets:
        return "<p>No bet captures yet. Drop <code>bet9ja-open-bets-*</code> or <code>bet9ja-settled-bets-*</code> files above.</p>"
    t = bet_totals(tickets)
    out = [f"<div class='cards'>"
           f"<div><b>{t['count']}</b>tickets</div>"
           f"<div><b>{t['won']} / {t['lost']}</b>won / lost</div>"
           f"<div><b>{money(t['staked_settled'])}</b>staked, settled (NGN)</div>"
           f"<div><b>{money(t['lost_stake'])}</b>lost stakes</div>"
           f"<div><b>{t['open']}</b>open ({money(t['staked_open'])} staked)</div>"
           f"</div>"]
    if t["won_missing_payout"]:
        out.append(f"<p class='dim'>{t['won_missing_payout']} won tickets have no payout in the capture "
                   f"(Bet9ja page didn't show it), so profit/loss can't be totalled yet.</p>")
    out.append("<table><tr><th>Placed</th><th>Ticket</th><th>Type</th><th>Legs</th><th>Stake</th>"
               "<th>Potential</th><th>Payout</th><th>Status</th></tr>")
    for r in tickets:
        legs = "".join(
            f"<li>{e(l.get('fixture_and_time_raw') or '')}: <b>{e(str(l.get('selection') or ''))}</b> "
            f"@ {e(str(l.get('odds') or ''))} <small>{e(l.get('leg_status') or '')}</small></li>"
            for l in r["legs"])
        out.append(f"<tr><td>{e(r['placed'] or '')}</td><td>{e(r['id'])}</td><td>{e(r['type'])}</td>"
                   f"<td><details><summary>{len(r['legs'])} legs</summary><ul>{legs}</ul></details></td>"
                   f"<td>{money(r['stake'])}</td><td>{money(r['potential'])}</td><td>{money(r['payout'])}</td>"
                   f"<td class='st {e(r['status'])}'>{e(r['status'])}</td></tr>")
    out.append("</table>")
    return "".join(out)


TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>PredictBot</title>
<style>
body{{font:14px system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px;color:#222;background:#fafafa}}
nav a{{margin-right:16px;text-decoration:none;color:#555;font-weight:600}} nav a.active{{color:#000;border-bottom:2px solid #000}}
#drop{{border:2px dashed #aaa;padding:18px;text-align:center;margin:12px 0;border-radius:8px;background:#fff;cursor:pointer}}
#drop.over{{background:#eef}}
table{{border-collapse:collapse;width:100%;background:#fff}} th,td{{padding:6px 8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}
th{{background:#f0f0f0;position:sticky;top:0}} small,.dim{{color:#888}} .pos{{color:#080;font-weight:600}} .neg{{color:#a00}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}} .cards div{{background:#fff;padding:12px 16px;border-radius:8px;border:1px solid #eee}}
.cards b{{display:block;font-size:20px}} .st.WON{{color:#080}} .st.LOST{{color:#a00}} .st.OPEN{{color:#06c}}
.msg{{background:#efe;padding:8px}} .err{{background:#fee;padding:8px}} ul{{margin:4px 0;padding-left:18px}}
</style></head><body>
<nav><a class="{fx_active}" href="/">Fixtures</a><a class="{bets_active}" href="/bets">My bets</a></nav>
<div id="drop">Drop capture JSON files here, or click to choose<input type="file" id="pick" multiple accept=".json" hidden></div>
{message}{body}
<details><summary>Files loaded</summary><ul>{files}</ul></details>
<script>
const drop=document.getElementById('drop'),pick=document.getElementById('pick');
drop.onclick=()=>pick.click();
drop.ondragover=ev=>{{ev.preventDefault();drop.classList.add('over')}};
drop.ondragleave=()=>drop.classList.remove('over');
async function send(files){{let n=0;for(const f of files){{const r=await fetch('/upload/'+encodeURIComponent(f.name),{{method:'POST',body:f}});if(r.ok)n++;else alert(f.name+': '+await r.text())}}
location.href=location.pathname+'?added='+n}}
drop.ondrop=ev=>{{ev.preventDefault();send(ev.dataTransfer.files)}};
pick.onchange=()=>send(pick.files);
const only=document.getElementById('onlymodel');
if(only)only.onchange=()=>document.querySelectorAll('#fx tr.nm').forEach(r=>r.hidden=only.checked);
</script></body></html>"""


# ----------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path not in ("/", "/bets"):
            self.send_error(404)
            return
        added = re.search(r"added=(\d+)", query)
        msg = f"Added {added.group(1)} file(s)." if added else ""
        self._send(200, page("bets" if path == "/bets" else "fixtures", msg))

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
                raise ValueError("no schema_version field, not a PredictBot capture")
        except ValueError as exc:
            self._send(400, f"rejected: {exc}", "text/plain")
            return
        INBOX.mkdir(exist_ok=True)
        target = INBOX / name
        if target.exists() and target.read_bytes() != raw:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            target = INBOX / f"{target.stem}.{stamp}.json"
        target.write_bytes(raw)  # raw bytes kept unchanged
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
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"PredictBot running at {url}  (Ctrl+C to stop)")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
