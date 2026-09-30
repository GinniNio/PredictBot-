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

**Still not built: live click/navigation orchestration.** The parsers
above are the pure logic a walker would call (which selectors to read,
which toggles to click and when); actually driving a browser through
that sequence (expand sport → click "more" while needed → expand each
group → click each competition link → wait for
`fixture_parser.js`-parseable content) is unbuilt — this session still
has no live, authenticated browser access to test it against, the same
limitation already documented for the existing Soccer walker.

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

## Files

| File | Purpose |
|---|---|
| `manifest.json` | Manifest V3 definition — `activeTab`/`scripting`/`downloads` only, no `storage`, no host permissions beyond the active tab. |
| `snapshot.js` | Pure snapshot builder (`buildSnapshot`) — no `chrome.*` calls, tested directly in Node via jsdom. |
| `content.js` | The one place that touches the real `document`/`location` — calls `buildSnapshot`. |
| `popup.html` / `popup.js` | UI: injects `snapshot.js` + `content.js`, runs the capture, downloads the JSON. |
| `fixture_parser.js` | Stage 2 — parses one competition page's own fixture rows. |
| `catalogue_parser.js` | Stage 2a — enumerates a sport's competitions from its left-menu catalogue. |
| `tests/snapshot.test.js`, `tests/fixture_parser.test.js`, `tests/catalogue_parser.test.js` | `node --test` + jsdom, mirroring the existing extension's own test convention. |

## Explicit boundaries

No network requests. No account data read or transmitted. No bet
placement, no cashout, no interaction with the page beyond reading its
current DOM. Produces raw HTML evidence only — never a parsed fixture,
never odds, never anything this project's ledgers would ever write.
