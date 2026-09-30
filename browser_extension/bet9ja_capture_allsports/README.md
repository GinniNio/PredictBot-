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
  sport, groups_seen, competitions_seen,
  competitions_attempted, competitions_successful, competitions_failed,
  results: [{ competition_id, competition_label, source_url_after_click,
              observed_breadcrumb_raw, parse_result, fixtures }],
  failures,
}
```

`parse_result` is one of `populated` / `confirmed_empty` / `unknown_empty`
/ `invalid_content_mismatch`. `observed_breadcrumb_raw` and
`source_url_after_click` are present on **every** result, success or
failure, for audit.

**Validation is fail-closed.** After every click, the resulting page's own
breadcrumb must agree with the competition actually requested (normalized
substring match); a page with no resolvable breadcrumb at all is treated
as a mismatch too, never assumed correct. A mismatch is recorded as
`invalid_content_mismatch` and its fixtures are **never** ingested into
`results[]`.

**Every element is re-queried fresh, never cached across a click** — a
navigation can replace the DOM entirely, and competitions are tracked by
their own stable `competition_id`, never array position, matching this
project's established resume discipline elsewhere
(`bet9ja_capture/soccer_walker.js`).

### Real run, real bugs (2026-09-30T17:00:14Z)

The first run against a live, authenticated session — a full 34-
competition Ice Hockey walk — surfaced two real, previously-unconfirmed
facts about Bet9ja's own SPA behavior, both fixed and now regression-
tested against realistic simulated sites (not just the original "wipes
everything" fake site, which happened to hide both bugs):

1. **Old competitions' fixture tables are never removed.** Direct
   analysis of that run's own output showed e.g. "Alps Hockey League"
   reporting 20 fixtures — 14 genuinely its own, plus the exact same 6
   already reported under "NHL" moments earlier. `fixture_parser.js`
   gained `parseFixturesFromTables(documentLike, tables, context)` (see
   its own header comment); `visitCompetition` now snapshots which
   `.sports-table` elements exist before a click and parses only the
   ones that are new after it. The original whole-document
   `parseFixturesFromDocument` stays correct and unchanged for its own
   contract — a single, freshly-loaded page.
2. **A group's toggle is a real accordion, not a one-way "open."** That
   run's 10 failures (all `MISSING_COMPETITION`) matched a clean pattern:
   almost every one was the competition immediately following a
   successful one in the same group (USA: NHL succeeded, AHL failed;
   Russia: KHL succeeded, VHL failed, MHL succeeded). The "reopen" step
   was clicking the group's toggle unconditionally, which **closes** an
   already-open group instead of ensuring it's open. `ensureGroupOpen`
   now checks whether the group's links already exist before ever
   clicking it.

Five further corrections applied on top of these two:

1. **Separate success/failure counts** — `competitions_visited` is
   renamed `competitions_attempted`, with `competitions_successful` and
   `competitions_failed` reported alongside it.
2. **Competition links are rediscovered before every click**, never
   reused from the list frozen at group-open time — sharpens bug #2
   above: a group's own rendered link set can change between visits
   independent of open/closed state. A competition seen once but never
   reached before disappearing is still recorded (`MISSING_COMPETITION`
   in `failures[]`), not silently dropped.
3. **Table-level validation beyond breadcrumb/URL.** No confirmed
   selector exists for a competition heading distinct from the
   breadcrumb on this sidebar, so a `fixtureOwners` map tracks which
   competition each fixture id was first attributed to across the whole
   walk instead; a fixture id reused from an earlier, different
   competition is rejected (`STALE_FIXTURE_ID_REUSED_FROM_EARLIER_
   COMPETITION`) — real, evidence-grounded defense-in-depth on top of
   the table-diffing fix, not a substitute for a heading selector this
   project doesn't have evidence for yet.
4. **Audit fields on every result** — `observed_breadcrumb_raw` and
   `source_url_after_click`, not only on a mismatch.
5. **3way odds stay nullable, confirmed correct, unchanged** — the real
   run's own output shows `three_way_odds: null` on every Ice Hockey
   fixture, since that market isn't shown by default and the walker
   never clicks a market-tab control.

**`failures[]` covers every required record type**: `EXPAND_SPORT`
(sport root missing or never expands), `OPEN_GROUP` (a group's own
toggle missing, or its competitions never render), `REOPEN_SPORT`/
`REOPEN_GROUP` (failure to re-expand after a navigation),
`MISSING_COMPETITION` (a competition's own link isn't found when needed
— whether at click time or because it vanished before its turn came up),
and `CONTENT_VALIDATION` (the click timed out, or its resulting
breadcrumb/fixture ids didn't match). The last two stages are recorded
here *in addition to* their own `results[]` entry (`invalid_content_
mismatch`, with `failure_reason` for detail) — `failures[]` is the flat
list of everything that went wrong; `results[]` is the full per-
competition record.

**What's still unconfirmed (no live browser access to test against):**
exact accordion open/closed state selectors beyond "links present or
not" (this sidebar has no confirmed CSS `is-open`-style class); real
click-to-render timing — every timeout/poll interval in `sport_walker.js`
remains an explicitly labeled `[UNVERIFIED]` placeholder pending further
real timed runs. Tests exercise the full sequence against simulated
sites, including ones built specifically to reproduce both bugs above
(a real accordion toggle, persistent sidebar, and accumulating fixture
tables) — but a simulated DOM still isn't a live Bet9ja session, and only
further real runs can confirm whether other, not-yet-seen quirks exist.

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
