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

## Runs on its own, sends to the app (0.4.0)

The popup's last section, **Run automatically while Chrome is open**, walks every sport you tick every N hours (default 4) in a Bet9ja tab the walker opens itself in the background (or an open Bet9ja tab, if there is one), and sends each sport's capture to the PredictBot app over `localhost:8000/walker-upload`; the app stores it as `bet9ja-allsports-walk-<sport>-<time>.json` and prices it at once. A manual **Walk sport** is sent the same way, with the download as the fallback when the app is not running or its data folder is read-only. Chrome must be open, the app running, and the Bet9ja session signed in in this Chrome profile (the background tab shares it). **Run all sports now** starts a background run without waiting for the alarm. The walk runs inside the page and reports back by message, so a suspended extension worker still receives the result. One sport is given up after 25 minutes; a capture the app did not take is kept in the extension's storage. The default start page is `https://sports.bet9ja.com/`; if the sports sidebar does not render there, set any prematch competition page instead.

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

**Stage 3 (experimental): "Walk sport" button.** The same popup now has a
second section — pick a sport from the dropdown (populated from
`catalogue_parser.js`'s `CONFIRMED_SPORTS`), optionally set a max-groups/
max-competitions-per-group cap for a small first test, and click "Walk
sport". It injects `catalogue_parser.js` + `fixture_parser.js` +
`sport_walker.js` into the active tab, runs `walkSport()` against the
live `document`, and downloads the resulting summary as
`bet9ja-allsports-walk-<sport>-<timestamp>.json`. **This has never been
run against a real Bet9ja session** — every timeout in `sport_walker.js`
is an unverified placeholder (see Stage 3 below), so the honest first use
is a small, capped test run (e.g. `maxGroups: 1`, `maxCompetitions: 2`),
with the resulting JSON (and any error it shows) fed back so the walker
can be corrected against real behavior, the same evidence loop the rest
of this tool was built on.

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

**Resolved (2026-09-30T17:03Z stage2-resolution-evidence capture; see
`fixture_parser.js`'s own header comment for this evidence's tier):**

- **Date attribution**: each `.sports-table`'s fixtures are attributed to
  the date in its own preceding sibling `.sports-head.table`'s
  `.sports-head__date > span` — output as `date_text_raw` per fixture,
  `null` when no such sibling exists. **UTC resolution is deliberately
  not done** — no capture-timezone policy exists yet, so date/time stay
  as bare site text (`date_text_raw`/`kickoff_time_raw`) until one is.
- **Empty-state detection**: `empty_state_status` is one of `NOT_EMPTY`
  (rows found), `CONFIRMED_EMPTY` (zero rows AND the exact marker text
  `"There are no markets available."` at `.gen__holder .search-results
  .gen__txt`), or `UNKNOWN_EMPTY` (zero rows, marker absent) — confirmed
  only for Basketball/WNBA so far, not asserted for any other sport.
- **WNBA "3way" market**: `three_way_odds: {"1", "X", "2"}` per fixture
  when present, decoding the confirmed `1B`/`XB`/`2B` odds-id sign
  suffixes — confirmed for this one market family only, not assumed to
  generalize.

**Correction (2026-09-30T16:23–16:26Z, two real raw `outerHTML`
snapshots — Basketball/WNBA and Ice Hockey/Russia/KHL): a fixture row is
not the `prematch_event-<id>` element by itself.** That id lives on the
`.sports-table__matchup` cell only; `.sports-table__time` and every odds
cell (including the 3way ones above) are its **siblings**, all children
of one shared `.table-f` row wrapper — not descendants of the matchup
cell. Querying them as descendants of the id'd element (this module's own
earlier code) silently returned `null`/empty in production despite
passing tests, because the tests' own synthetic markup wrongly nested
everything inside the row div. Fixed via `resolveRowContainer`
(`fixtureEl.closest('.table-f')`, no fallback — a sport genuinely lacking
this wrapper needs its own evidenced handling, not a guess); independently
confirmed on a second real sport/competition (Ice Hockey/KHL, not just
WNBA — byte-verified from the raw `2026-09-30T16:26:27.020Z` snapshot,
`source_url` `.../icehockey/russia/khl/4-44083-4714776`). Also corrected:
an earlier claim of a `#marketsmenu_market_dropdown` toggle for selecting
the "3way" market does not hold up against either real WNBA snapshot —
`3way` is a plain `.sports-view__bar-markets` category label; its odds
are read from sibling `.sports-table__odds-item` cells by id
(`..._odds_market-3way_sign-1B|XB|2B`), never from a dropdown control id
or type. `tests/fixture_parser.test.js` now loads real fragments cut
directly from three raw snapshots via jsdom's own `outerHTML` (not
hand-typed) — WNBA populated (16:23:42Z), WNBA confirmed-empty
(15:33:17Z), Ice Hockey/KHL populated (16:26:27Z) — under
`tests/fixtures/`, replacing the synthetic markup that originally masked
this bug.

## Stage 3: sport walker (`sport_walker.js`)

Drives a sport's own sidebar through every competition it discovers and
runs `fixture_parser.js` on each one — kept explicitly separate from the
pure parsers per the operator's own instruction. Sequence: expand sport →
click "show more" while its label contains "more" → enumerate groups →
open one group → enumerate its competition links → click one competition
→ wait for the resulting breadcrumb/fixture-table/empty-marker → parse →
reopen the sport (and the same group) → continue to the next competition.

Output shape:

```
{
  sport, capture_status, capture_started_at_utc, captured_at_utc,
  page_timezone, page_utc_offset, groups_discovered, groups_seen, groups_failed,
  competitions_seen, competitions_attempted,
  competitions_successful, competitions_validated, competitions_failed,
  results: [{ competition_id, competition_label, source_url_after_click,
              observed_source_url_raw, observed_competition_heading_raw,
              parse_result, fixtures }],
  failures,
}
```

`capture_status` is `PARTIAL` if a group or competition fails, a configured
limit truncates discovery, or the sport cannot open. Inspect `failures` and
rerun after resolving them. `GROUP_LINKS_NOT_DISCOVERED` means the group
toggle was found but its links did not materialize; `GROUP_TOGGLE_NOT_FOUND`
means the toggle itself was absent. Competition IDs may contain commas and
other punctuation in every sport, so discovery preserves the full suffix.

The shared fixture parser records the visible market cells in `markets[]`,
including their raw header and code, line, selection labels, numeric odds,
raw price text and open/locked/suspended state. Locked odds have `odds: null`.
The walker flags `ARITY_MISMATCH` and `NO_PRICED_MARKET` per fixture.
Specials Combo `to_happen` is a legitimate one-selection market; its
compound selection text and visible price count as a fully priced market.
`COMPLETE` requires at least 90% of fixtures to have a market whose every
selection is open with a price above 1.01; zero priced fixtures yield
`ODDS_MISSING`. `fixtures_with_any_price` also counts partial markets.
It waits up to 10 seconds for prices and 5 seconds for the selected page's
heading to settle. Each result records `price_captured_at_utc`.

Specials Basketball uses match competitions with player markets. Its group
and match IDs contain spaces and hyphens. The walker records one match fixture
per competition and places each visible player market in `fixtures[0].markets`.
A blank or locked odd remains `null`; player market IDs are never counted as
separate team fixtures.

The page clock's IANA timezone (when shown, such as `Africa/Lagos`) and its
UTC offset are saved with the capture. `kickoff_utc` is derived only when the
page timezone, raw date, and time are all available; otherwise it is `null`.
Outright layouts without fixture rows still need an observed populated-page
DOM sample to map rider and price elements. A timeout includes a bounded raw
page snippet to diagnose that layout.

`parse_result` is one of `populated` / `confirmed_empty` / `unknown_empty`
/ `invalid_content_mismatch`.

**Validation is fail-closed.** After every click, the resulting page's own
breadcrumb and active competition heading must agree with the requested
sport, group, and competition. The walker excludes table nodes that
existed before that click, preventing retained SPA tables from being
misattributed to the new route. A mismatch is recorded as
`invalid_content_mismatch` and its fixtures are **never** ingested into
`results[]`. Confirmed real evidence backing the design (2026-09-30): a
real Ice Hockey capture (16:25:09Z) showed the sport expanded with its
"show more" control and 10 groups visible, but zero competition links —
because no group had been opened yet, exactly matching this module's own
"open one group" step before competition links exist.

**Every element is re-queried fresh, never cached across a click** — a
navigation can replace the DOM entirely (per the operator's own rule),
and competitions are tracked by their own stable `competition_id`, never
array position, matching this project's established resume discipline
elsewhere (`bet9ja_capture/soccer_walker.js`).

**`failures[]` covers every required record type**: `EXPAND_SPORT`
(sport root missing or never expands), `OPEN_GROUP` (a group's own
toggle missing, or its competitions never render), `REOPEN_SPORT` (the
sport fails to re-expand after a navigation), `MISSING_COMPETITION` (a
competition's own link isn't found at the moment it's clicked — e.g. it
didn't survive a reopen), and `CONTENT_VALIDATION` (the click timed out,
or its resulting breadcrumb didn't match). The last two are recorded
here *in addition to* their own `results[]` entry (`invalid_content_
mismatch`, with `failure_reason` for detail) — `failures[]` is the flat
list of everything that went wrong; `results[]` is the full per-
competition record.

**What's still unconfirmed (no live browser access to test against):**
exact accordion open/closed state selectors (this sidebar has no
confirmed "is-open" class the way the retired Soccer accordion did); real
click-to-render timing: every timeout/poll interval in `sport_walker.js`
is an explicitly labeled `[UNVERIFIED]` placeholder, not an evidence-based
constant, pending a real timed run. Tests exercise the full sequence
(expand → show-more → group → competition → reopen → next competition)
against a simulated site with a delegated click listener, including a
deliberately mismatched competition and a confirmed-empty one — but a
simulated DOM is not a live Bet9ja session, and timing/exact toggle
behavior can only be corrected against one.

## Stage 2a: competition catalogue (`catalogue_parser.js`)

**Correction (2026-09-30): a single competition page (e.g. NFL, NHL) is
not a sport's crawl scope — it's one leaf of it.** The real scope is the
left-menu sport catalogue: every country/group exposed under a sport's
own accordion, and every competition link beneath each group.
`fixture_parser.js` still parses one competition page's own fixtures once
visited; `catalogue_parser.js` is the layer above it that enumerates
*which* competition pages exist to visit, from the sport-level catalogue.

Backed by two real "left-menu sport catalogue" captures
(2026-09-30T15:50:00Z):

| Sport | sport_id | Countries | Competitions |
|---|---|---|---|
| American Football | 70 | 2 (USA, Canada) | 3 (NFL, NCAA Regular Season, CFL) |
| Ice Hockey | 4 | 19 | 34 |

`parseCatalogueSummary(catalogue)` normalizes that JSON into a flat list
of `{country_raw, competition_raw, natural_key}` entries, tested directly
against both real captures, unchanged. Two things confirmed by this real
data and enforced by the parser:

- **A country group can have zero competitions.** Ice Hockey's own
  capture exposed Russia as a country group with no competition link at
  all — kept in `countries_with_zero_competitions`, never dropped.
- **Competition names repeat across countries** ("Extraliga" under Czech
  Republic, Belarus, *and* Slovakia) — the natural key is always
  `sport::country::competition`, never competition name alone, per the
  capture's own note. `parseCatalogueSummary` also reconciles the
  catalogue's own declared `competition_count` against the sum of its
  group lists (both real captures reconcile exactly: 3 and 34) and flags
  disagreement rather than trusting either number blindly.

`parseCatalogueFromDocument(documentLike, { sportId, sportSlug })` is the
DOM-level counterpart for when a real sidebar page (not just a summary)
is captured — it reads real competition-link ids using the
`left_prematch_sport-<id>_<slug>_sg-<group>_g-<competition>` id pattern.

**Resolved (2026-09-30T17:03Z live sidebar inspection — see
`catalogue_parser.js`'s own header comment for this evidence's tier: a
hand-distilled report, not a raw extension snapshot):**

- **Competition links require `.click()`, not `href`.** Verified against
  real markup (Ice Hockey → Switzerland → National League/Swiss League):
  the link's `href` is the literal string `javascript:;`; navigation
  happens through the click handler. Every entry from
  `parseCatalogueFromDocument` now carries `requires_click_navigation:
  true`, and the confirmed read selector is `a[id*="_g-"]`.
- **Group-level discovery**: `parseGroupsFromDocument(documentLike,
  {sportId, sportSlug})` enumerates a sport's own country/group toggles
  (the level *above* competition links) — confirmed real for Ice Hockey
  (19 groups, exactly reconciling the earlier summary catalogue's
  country count), Tennis (8), Volleyball (5), Handball (10 + a
  show-more toggle), American Football (2).
- **The "show more" toggle's click contract**: `needsMoreClick(labelRaw)`
  — click only while the toggle's own visible label still contains
  "more" (Handball: "Show 4 A-Z more" → click; Ice Hockey, already
  expanded: "Show less" → stop).
- **Full sidebar sport catalogue**: `CONFIRMED_SPORTS` — all 34 top-level
  sport roots with real `left_prematch_sport-<id>_<slug>_label-toggle`
  ids, including Bandy (15) and Field Hockey (321), which previously had
  no id evidence at all (only a plain-text sidebar paste).

Live click/navigation orchestration itself is now built as `sport_walker.js`
— see Stage 3 below.

## What's still open

- **Scope per sport**: soccer's `1X2` market is the only market this
  project's forecasting adapter (`soccer_1x2_elo_v1`) currently
  understands (`README.md`: "no other category does" have a registered
  adapter). Capturing another sport's fixtures/odds feeds the pricing
  engine and research queue (market-quality screening, EV calculation)
  exactly like Soccer's abstentions already do today — it does **not**
  produce a forecast for that sport until a matching adapter is built.
- **One fixture parser vs. several**: every sport evidenced so far shares
  one row shape, so `fixture_parser.js` is deliberately generic rather
  than per-sport. A sport whose markup diverges (a different row anchor,
  no home/away split — e.g. Outrights, which has no two-participant
  matchup at all) would need its own handling, not yet built.
- **Catalogue coverage**: American Football and Ice Hockey have full
  country/competition catalogues; Tennis, Volleyball, and Handball now
  have real group-level discovery (`parseGroupsFromDocument`) but not a
  full competition-count catalogue like the first two; Basketball only
  has single-competition-page (WNBA) evidence. The other 29 sports in
  `CONFIRMED_SPORTS` have a confirmed root id and nothing else — no
  group, competition, or fixture evidence at all.
- **Timezone policy**: `date_text_raw`/`kickoff_time_raw` are bare site
  text (e.g. "Thu 1 Oct" / "00:00") — no UTC conversion is implemented,
  by explicit design (see Stage 2's own note), pending a stated capture
  timezone.
- **Empty-state marker**: confirmed only for Basketball/WNBA. A future
  capture confirming (or contradicting) the same marker on another
  sport's genuinely empty page is the next evidence needed before
  `UNKNOWN_EMPTY` can safely become `CONFIRMED_EMPTY` elsewhere.
- **`sport_walker.js` is untested against a live session.** Its tests
  exercise the full sequence against a simulated site, not real Bet9ja —
  toggle open/closed state, click-to-render timing, and whether a click
  truly never reloads the page (vs. an in-place SPA swap) all need a real
  timed run to confirm or correct.

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Manifest V3 definition — `activeTab`/`scripting`/`downloads` only, no `storage`, no host permissions beyond the active tab. |
| `snapshot.js` | Pure snapshot builder (`buildSnapshot`) — no `chrome.*` calls, tested directly in Node via jsdom. |
| `content.js` | The one place that touches the real `document`/`location` — calls `buildSnapshot`. |
| `popup.html` / `popup.js` | UI: injects `snapshot.js` + `content.js`, runs the capture, downloads the JSON. |
| `fixture_parser.js` | Stage 2 — parses one competition page's own fixture rows. |
| `catalogue_parser.js` | Stage 2a — enumerates a sport's competitions from its left-menu catalogue. |
| `sport_walker.js` | Stage 3 — drives a sport's sidebar through every competition and calls `fixture_parser.js` on each. |
| `tests/snapshot.test.js`, `tests/fixture_parser.test.js`, `tests/catalogue_parser.test.js`, `tests/sport_walker.test.js` | `node --test` + jsdom, mirroring the existing extension's own test convention. |

## Explicit boundaries

No network requests. No account data read or transmitted. No bet
placement, no cashout, no interaction with the page beyond reading its
current DOM. Produces raw HTML evidence only — never a parsed fixture,
never odds, never anything this project's ledgers would ever write.
