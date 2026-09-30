# Bet9ja all-sports fixture capture — Stage 1: evidence collector

A **separate** extension from `browser_extension/bet9ja_capture/` (the
existing Soccer-only 1X2 tool) — per the operator's own instruction, this
does not extend or modify that one. The eventual goal: capture fixtures
across every sport Bet9ja lists, not just Soccer. Confirmed scope (from
the operator's own copy of Bet9ja's live sidebar, 2026-09-30): Soccer,
Players Soccer, Specials Soccer, Specials Combo, Antepost Soccer, Zoom
Soccer, Players Zoom Soccer, Tennis, Zoom Tennis, Basketball, Specials
Basketball, Volleyball, American Football, Players Am. Football,
Baseball, Handball, Rugby, Motor Sports, Ice Hockey, Alpine, Biathlon,
Cross-Country, Aussie Rules, Badminton, Boxing, Cricket, Darts, Floorball,
Futsal, MMA, Snooker, Squash, Table Tennis, Waterpolo, Outrights — plus
the `TODAY` / `3H` / `24H` / `72H` / `ALL` time-window filters the site
itself exposes.

## Why a snapshot tool first

Every existing capture module in this repository (`soccer_walker.js`,
`ticket_parser.js`, `settled_bets_parser.js`, …) was built and repeatedly
corrected against **real, evidence-captured Bet9ja page structure** — see
e.g. `browser_extension/bet9ja_capture/SOCCER_ALL_COMPETITIONS_VALIDATION.md`
and `REAL_PAGE_VALIDATION.md`. None of them were written from a guessed or
remembered DOM shape. This session has no live, authenticated browser
access to Bet9ja at all (confirmed: the only web tool available here,
`WebFetch`, explicitly cannot handle authenticated pages, and Bet9ja's
fixture pages require a logged-in session regardless) — so the real page
structure for every non-Soccer sport is still completely unknown here.

A plain text/visible-content paste of the page (e.g. the sidebar list
above) is not enough to build selectors from: it has no tag names, class
names, ids, or nesting — exactly the information a real parser needs.

So stage 1 is deliberately the smallest possible thing that produces real
evidence without guessing anything: this extension serializes the CURRENT
page's own `document.documentElement.outerHTML` verbatim and downloads it
as JSON. It extracts nothing — no fixtures, no odds, no sport-specific
structure — so there is nothing in it to get wrong.

## How to use it

1. `chrome://extensions` → enable Developer mode → "Load unpacked" → select
   this directory (`browser_extension/bet9ja_capture_allsports/`).
2. On an already-open, already-logged-in Bet9ja tab, navigate to the page
   you want captured (e.g. the all-sports/all-fixtures listing, or one
   specific sport's fixture page — capture one at a time, since different
   sports may render differently).
3. Click the extension icon → "Capture page snapshot". It downloads
   `bet9ja-allsports-raw-snapshot-<timestamp>.json`.
4. Upload that file back into the PredictBot session.

Repeat per sport/page you want covered. The most useful first captures,
in order: (a) the all-sports/all-fixtures listing itself, since that's
the literal target ("all fixtures and sports"); (b) one fixture page per
sport that differs structurally from Soccer's own market shape — Tennis/
Basketball (2-way markets, not 1X2), Ice Hockey (3-way + possible
overtime), American Football/Baseball (spread/total markets) — since
those are the sports already showing up in the real settled-ticket data
this session has seen (ATP Challenger tennis, KHL/DEL 2/F-Liiga hockey,
Liga ACB basketball).

## Stage 2: fixture parser (`fixture_parser.js`)

`parseFixturesFromDocument(documentLike, { href, capturedAtUtc })` extracts
fixture rows from a single competition page's DOM. It reuses, unchanged,
the row-anchor and participant selectors already confirmed for Soccer in
`bet9ja_capture/parser.js`:

- Row anchor: an element `id` matching exactly `prematch_event-<digits>`
  (`[id^="prematch_event-"]` also matches descendant odds/dropdown
  controls that repeat the same id with a suffix — e.g.
  `..._odds_market-1x2_sign-1` — so an exact-match filter excludes them).
- Participants: `.sports-table__home` / `.sports-table__away`.
- Kickoff time: `.sports-table__time`.
- Breadcrumb: `.sports-view__crumbs`.
- Sport identification: parsed from the page's own URL
  (`/competition/{sport-slug}/...`), same as the existing Soccer parser's
  fallback.

**Evidence backing this, by tier (2026-09-30):**

| Sport | Evidence tier |
|---|---|
| Tennis, Basketball/WNBA, Ice Hockey/NHL, American Football/NFL | Byte-verified against real raw page `outerHTML` (stage-1 snapshot tool) |
| Volleyball, Handball | "DOM extraction contract" — a structured live-inspection summary reporting the identical selectors, not raw HTML bytes |

Both tiers report the exact same `fixture_row_selector` and
`participant_model`, so `fixture_parser.js` does not branch on tier — but
the distinction is kept here rather than glossed over, since the contract
tier for Volleyball/Handball has not been independently byte-verified the
way the other four sports were. `tests/fixture_parser.test.js` encodes
every real fixture row from all four 2026-09-30 DOM contracts
(Handball, Volleyball, Ice Hockey, Tennis) as its own test fixture.

**Not yet built:** date-group attribution (e.g. "Thu 1 Oct") — no
date-header selector has been captured yet, so `fixture_parser.js`
returns fixtures without a date field rather than guessing one. A
confirmed empty-state check (`"There are no markets available."`) is also
not wired in yet — callers that need to distinguish "genuinely empty"
from "parser found nothing" should check for that text themselves until
then.

## What's still open

- **Scope per sport**: soccer's `1X2` market is the only market this
  project's forecasting adapter (`soccer_1x2_elo_v1`) currently
  understands (`README.md`: "no other category does" have a registered
  adapter). Capturing another sport's fixtures/odds feeds the pricing
  engine and research queue (market-quality screening, EV calculation)
  exactly like Soccer's abstentions already do today — it does **not**
  produce a forecast for that sport until a matching adapter is built.
- **One parser vs. several**: every sport evidenced so far shares one row
  shape, so `fixture_parser.js` is deliberately generic rather than
  per-sport. A sport whose markup diverges (a different row anchor, no
  home/away split — e.g. Outrights, which has no two-participant matchup
  at all) would need its own handling, not yet built.

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Manifest V3 definition — `activeTab`/`scripting`/`downloads` only, no `storage`, no host permissions beyond the active tab. |
| `snapshot.js` | Pure snapshot builder (`buildSnapshot`) — no `chrome.*` calls, tested directly in Node via jsdom. |
| `content.js` | The one place that touches the real `document`/`location` — calls `buildSnapshot`. |
| `popup.html` / `popup.js` | UI: injects `snapshot.js` + `content.js`, runs the capture, downloads the JSON. |
| `tests/snapshot.test.js` | `node --test` + jsdom, mirrors the existing extension's own test convention. |

## Explicit boundaries

No network requests. No account data read or transmitted. No bet
placement, no cashout, no interaction with the page beyond reading its
current DOM. Produces raw HTML evidence only — never a parsed fixture,
never odds, never anything this project's ledgers would ever write.
