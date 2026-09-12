# Bet9ja fixture + open-ticket capture

A Manifest V3 browser extension (Chrome/Edge) with four independent
buttons:

- **Capture fixtures** (Release 1, real-page validated — see "Real-page
  validation status" below): captures the pre-match Soccer 1X2 fixtures
  visible on an already-open, already-authenticated Bet9ja tab into one
  normalized JSON file. This replaces copy-pasting a Bet9ja page into a
  chat host as the first step of `docs/LEDGER_DAILY_WORKFLOW.md`.
- **Capture all Soccer fixtures** (see "Capture all Soccer fixtures"
  below — Round 5 rewrite against the confirmed batch competition
  selector on `/sportPage/1/competitions`, superseding Rounds 1-4's
  per-competition walker after a real capture hit an unresolved
  return-between-competitions failure; architecture and aggregation logic
  implemented and tested, pending one real end-to-end click-through):
  selects every country's competitions in batches and clicks "Show
  Leagues" to capture each batch's combined pre-match ordinary-1X2
  fixtures via the same engine as **Capture fixtures**, into one
  deduplicated combined file — retiring manual per-competition selection.
- **Capture open bets** (see "Open bet ticket capture" below — real
  selectors confirmed through Round 6 for ticket boundaries/id/legs/
  pagination/most stake fields; 80/80 real tickets parsed cleanly on the
  most recent real capture): expands, parses, and re-collapses each open
  ticket accordion, walking automatically through every numbered page —
  ticket id, placement time, each leg's selection/odds/market/fixture/
  source event id — into one combined normalized JSON file, so what you
  actually bet can be recorded in the betting ledger without
  hand-transcription.
- **Capture settled bets** (see "Capture settled bets" below — real
  selectors confirmed and run end-to-end: a real 23-page, 115-ticket
  capture reconciled exactly, with a bounded expansion retry and
  Won/Lost/Cancelled leg-outcome recognition): opens from My Bets →
  Settled Bets, walks every numbered results page within whatever date
  range is currently selected, expands each ticket, and downloads one
  file of Bet9ja's own recorded settlement (ticket/leg outcomes, stake,
  payout) — never a recalculated result, never a ledger update.

This is deliberately the smallest useful slice — see "Boundaries" below for
everything it does not do yet.

## What it does

1. You open a Bet9ja pre-match page in Chrome or Edge and are already
   logged in.
2. You click the extension's **Capture fixtures** button.
3. It reads the DOM already rendered in that tab — no page navigation, no
   requests of its own.
4. It normalizes every visible pre-match Soccer 1X2 market it recognizes.
5. Everything it does not yet know how to normalize (another sport,
   another market, a live/virtual/Zoom product, an incomplete market, an
   unrecognized layout) is kept in `unparsed_records` with a typed reason —
   never silently dropped, never guessed at.
6. It downloads one `bet9ja-fixtures-{timestamp}.json` file matching the
   envelope below.

## What it never does

- No automatic Bet9ja scraping — it runs only when you click the button
  (there is no `content_scripts` entry in `manifest.json`, so no code from
  this extension executes on page load, on a timer, or in the background).
- No direct network requests of any kind — the extension has no
  `host_permissions` and never calls `fetch`/`XMLHttpRequest`.
- No reading of cookies, auth tokens, balances, account details, or
  betslip contents — the parser (`parser.js`) only ever touches the
  fixture/market DOM nodes described in the selector contract below, and
  `unparsed_records[].raw` is built from an explicit field allowlist (see
  below) rather than ever capturing a row's surrounding page content.
- `source_url` is sanitized to origin + pathname only — query parameters
  and the fragment (where a session identifier, referral code, or similar
  could live) are always stripped before the envelope is built.
- No persistent storage of any kind (`manifest.json` declares no
  `storage` permission) — nothing this extension captures is remembered
  between runs.
- No automatic bet placement, no cashout, no settled-bet capture ("Capture
  settled bets" is a separate future work item), no result lookup.
- The open-ticket capture button is read-only in exactly the same sense:
  it never places, edits, or cashes out a bet. It also fails closed —
  see "Open bet ticket capture" below — rather than guess which ticket a
  leg belongs to.
- No backend, database, or hosted service of any kind.
- No all-sports parser — only Soccer 1X2 is normalized in this release.
- Not published to the Chrome Web Store — load it unpacked (below).

## Capture envelope

```json
{
  "schema_version": "bet9ja-fixture-capture.v1",
  "capture_id": "cap_...",
  "captured_at_utc": "2024-08-17T14:00:00.000Z",
  "source_url": "https://www.bet9ja.com/sport/prematch",
  "page_title": "Bet9ja - Prematch",
  "parser_version": "bet9ja-capture-parser@1.0.0",
  "capture_status": "CAPTURE_OK",
  "capture_status_reasons": [],
  "coverage": {
    "visible_page_only": true,
    "sections_seen": 1,
    "records_seen": 1,
    "records_parsed": 1,
    "records_unresolved": 0,
    "records_expected_unsupported": 0,
    "collapsed_sections_detected": false,
    "lazy_loading_detected": false
  },
  "fixtures": [
    {
      "fixture_id": "bxf_...",
      "sport": "SOCCER",
      "region": "England",
      "competition": "Premier League",
      "participants": { "home": "Arsenal", "away": "Chelsea" },
      "kickoff_raw": "17/08 15:00",
      "kickoff_utc": "2024-08-17T14:00:00.000Z",
      "kickoff_resolution": "EXPLICIT_UTC_ATTRIBUTE",
      "status": "PRE_MATCH",
      "market_family": "1X2",
      "market_line": "",
      "outcomes": [
        { "label": "H", "price": 1.95 },
        { "label": "D", "price": 3.4 },
        { "label": "A", "price": 4.2 }
      ],
      "offered_odds": { "H": 1.95, "D": 3.4, "A": 4.2 },
      "captured_at_utc": "2024-08-17T14:00:00.000Z",
      "duplicate_status": "NEW",
      "parser_version": "bet9ja-capture-parser@1.0.0"
    }
  ],
  "unparsed_records": []
}
```

`fixtures[]` entries carry an `offered_odds` field (`{H, D, A}`) in addition
to the generic `outcomes` array specifically so a normalized fixture can be
passed straight into `ledgers.forecast_ledger.build_recorded_event`'s
`offered_odds` keyword argument — see `docs/LEDGER_DAILY_WORKFLOW.md`. This
release does not call the ledger CLI itself; that remains a separate,
explicit step.

### `capture_status`

- `CAPTURE_OK` — at least one record was found and there is no known
  coverage gap.
- `CAPTURE_PARTIAL` — real records were captured, but the page shows a
  structural reason to distrust completeness (a collapsed section, a
  lazy-loading placeholder still visible, or at least one unresolved
  record). `capture_status_reasons` names which.
- `CAPTURE_FAILED` — nothing could be captured at all (the expected root
  element wasn't found, i.e. the page layout no longer matches this
  extension's selectors, or the root was found but contained zero
  records).

**An empty successful download is prohibited by construction**: it is
structurally impossible for `capture_status` to be `CAPTURE_OK` or
`CAPTURE_PARTIAL` while both `fixtures` and `unparsed_records` are empty
(`parser.js`'s `captureFromDocument` asserts this before returning) — see
`tests/parser.test.js`'s `an empty successful capture can never happen`
test.

### `unparsed_records[]` typed reasons

| Reason | `expected_unsupported` | Meaning |
|---|---|---|
| `UNSUPPORTED_SPORT` | true | Row's sport isn't Soccer — no adapter yet. |
| `UNSUPPORTED_MARKET_FAMILY` | true | Market isn't 1X2 — no adapter yet. |
| `LIVE_EVENT_EXCLUDED_FROM_PREMATCH_MODEL_INPUT` | true | Row is in-play; Release 1 captures pre-match only. |
| `VIRTUAL_OR_ZOOM_PRODUCT_EXCLUDED` | true | Row is a virtual/Zoom product, not a real-world fixture. |
| `INCOMPLETE_1X2_MARKET` | false | One or more of H/D/A has no usable price (e.g. suspended) — rejected from model input, kept for audit. |
| `MISSING_PARTICIPANTS` | false | Row matched but has no home/away team text. |
| `UNRECOGNIZED_OUTCOME_LABEL` | false | A market's outcome label isn't H/D/A (or 1/X/2, Home/Draw/Away). |
| `UNRECOGNIZED_STATUS` | false | `data-status` isn't one of PRE/LIVE/VIRTUAL/ZOOM. |
| `NO_MARKETS_FOUND` | false | Row matched but has no market elements at all. |

`expected_unsupported: true` reasons are "working as intended" (a future
adapter's job, or deliberately out of scope) and count toward
`coverage.records_expected_unsupported` instead of
`coverage.records_unresolved`; `false` reasons are genuine parsing
problems worth an operator's attention and count toward
`coverage.records_unresolved`.

### `coverage`'s row-accounting invariant

Every row `captureFromDocument` examines is classified into exactly one
of three buckets — `records_parsed` (produced a fixture),
`records_unresolved` (a genuine problem — some `false`-`expected_unsupported`
reason above), or `records_expected_unsupported` (correctly identified but
out of scope for this release — some `true`-`expected_unsupported` reason
above) — so this always holds, for any page:

```
coverage.records_seen
  = coverage.records_parsed
  + coverage.records_unresolved
  + coverage.records_expected_unsupported
```

This is a genuine per-**row** invariant, not a per-`unparsed_records`-**event**
one: `unparsed_records` is an audit-event log, and one row can generate
several events (a real Soccer row's true 1X2 market plus its excluded
"1X2 1UP"/"1X2 2UP" siblings produces one fixture — the row counts once,
toward `records_parsed` — alongside two separate `UNSUPPORTED_MARKET_FAMILY`
audit entries that do NOT inflate `records_expected_unsupported`, since
that row already succeeded). `tests/parser.test.js`'s
`records_seen = records_parsed + records_unresolved + records_expected_unsupported, universally`
test proves this holds across every real and synthetic fixture in this
package, including one with exactly that 1X2-plus-1UP shape.

### `unparsed_records[].raw` field allowlist (privacy contract)

Every `raw` object is built from exactly this key set — nothing else is
ever attached, regardless of what else exists on the page around the
fixture row:

`home`, `away`, `kickoff_raw`, `sport_hint`, `status_hint`, `markets`
(each with only `family`/`line`/`outcomes`, each outcome with only
`rawLabel`/`rawPrice`), plus `market`/`partial_outcomes` on the
market-level rejection reasons. `tests/privacy.test.js` enumerates this
allowlist directly against the real parser output (across every fixture
in `tests/fixtures/`, plus a synthetic page with a nav bar and footer
containing an account balance, a "log out" link, a betslip count, and a
session token) and fails if a future edit ever attaches anything outside
it — page navigation, account/balance/betslip text, and similar page
chrome can never reach an unparsed record even by accident.

### Deterministic, identity-based IDs

`fixture_id` is derived (`ids.js::stableId`) from either the page's own
`data-fixture-id` attribute (preferred, when present) or a natural key of
sport/region/competition/participants/kickoff/market — never from odds,
`captured_at_utc`, DOM row order/position, or incidental text formatting
(whitespace) in a participant's name, all of which can and do change
between two otherwise-identical captures of the same fixture (see
`tests/parser.test.js`'s "Fixture ID stability" tests). It DOES change
whenever participants, competition, kickoff, or market identity change.

Recapturing the same fixture therefore produces the same `fixture_id`.
Release 1 ships no persistent storage (see "Permission contract" below),
so `duplicate_status` only ever takes two values in practice: `NEW`, or
`DUPLICATE_WITHIN_CAPTURE` for a fixture repeated twice on the same page
(a real Bet9ja layout pattern — both occurrences are still recorded,
never silently collapsed). `parser.js` itself still accepts an optional
`previousIndex` argument and can report `SEEN_BEFORE_UNCHANGED` /
`SEEN_BEFORE_ODDS_CHANGED` against it (exercised directly in
`tests/parser.test.js`) — cross-capture history is intentionally left to
the ledger's own idempotent re-import (`ledgers.forecast_ledger`, keyed by
this same deterministic `fixture_id`) rather than duplicated here behind
a `storage` permission this extension doesn't otherwise need.

### Permission contract

`manifest.json` declares exactly three permissions: `activeTab`,
`scripting`, `downloads`. No `host_permissions`, no `storage`, no
`cookies`, no `background` service worker, no `content_scripts` entry.
`tests/structural.test.js` asserts this directly against the real
manifest and source files (including that `popup.js` only ever calls
`chrome.tabs`/`chrome.scripting`/`chrome.downloads`, and that no source
file calls `fetch`, constructs an `XMLHttpRequest`, or reads
`document.cookie`).

### Repeated capture / double-click safety

Clicking **Capture fixtures** twice in quick succession, or the popup
injecting its scripts more than once, produces exactly one response and
one download per confirmed capture:

- `content.js` never calls `addEventListener` — it only ever
  (re-)assigns one global function (`window.__bet9jaCaptureRun`), so
  re-injecting it twice still leaves exactly one entry point installed,
  never two independently-invoked copies or an accumulated listener.
- `popup.js` registers its button's click listener exactly once, at
  module load.
- The click handler sets a `captureInFlight` guard and disables the
  button as the very first synchronous statements, before any `await` —
  a second click while a capture is running is a no-op, not a second
  concurrent capture.

`tests/structural.test.js` asserts these properties directly against the
real source files. A full click-double-click integration test would need
a real extension host (e.g. Puppeteer driving an actual loaded
extension) — deliberately out of scope for Release 1's zero-new-runtime-
dependency, Node-`jsdom`-only test suite; the structural checks above are
the honest substitute.

## Selector contract

`parser.js` tries two independent profiles, in order:

1. **LEGACY** (`SELECTORS`) — the original documented, best-effort
   assumption about Bet9ja's markup, exercised against the sanitized dev
   fixtures in `tests/fixtures/`. Kept as-is for those fixtures and as a
   fallback should a future redesign happen to match it.
2. **BET9JA_DESKTOP** (`BET9JA_DESKTOP_SELECTORS`) — used only when the
   LEGACY root selector matches nothing at all.

### Real-page validation status

**Round 1** (2026-09-11, against an authenticated
`https://sports.bet9ja.com/sport/soccer/1`) found the LEGACY profile did
not match the real page at all — `capture_status: CAPTURE_FAILED` /
`SELECTOR_ROOT_NOT_FOUND`, confirmed from two real downloaded captures.
One sanitized real fixture row was supplied
(`tests/fixtures/bet9ja_desktop_real_sample.html`).

**Round 2** (same day) confirmed the full selector contract via direct
live-page inspection, on both the public and an authenticated view of the
same page:

| Check | Public | Logged in |
|---|---:|---:|
| Fixture rows (`.table-f`) | 30 | 30 |
| Home-team elements | 21 | 21 |
| Away-team elements | 21 | 21 |
| Matchup event IDs | 21 | 21 |
| Standard 1X2 price elements | 63 | 63 |
| Date headings (`.sports-head__date`) | 2 | 2 |

The first fixture (Ararat-Armenia vs FC Syunik, `16:00`,
`1.14 / 7.30 / 12.25`) was identical in both views. **Login does not
change the fixture DOM** — the extension needs no authenticated-vs-public
branching. What login does add is account-related page chrome elsewhere
on the page; none of it appears inside `.sports-table`, any `.table-f`
row, or any 1X2 price element — confirmed structurally, not assumed.

Note the 30 vs. 21 gap above: 9 of the 30 `.table-f` rows do not carry
the standard home/away/matchup-id structure (e.g. promotional or
otherwise non-standard rows). This is not a defect — those rows correctly
fall through to `MISSING_PARTICIPANTS` in `unparsed_records` rather than
being silently skipped or fabricated.

`tests/parser.test.js`'s `BET9JA_DESKTOP: ...` tests are the regression
coverage for all of this, including the real Ararat-Armenia vs FC Syunik
row mapping to exactly `1.14 / 7.30 / 12.25` on the correct participants.

**What this confirms:**

- **Root and row selectors**: `.sports-table` (confirmed as "the fixture
  accordion/table" — capture is rooted inside it, never the page body or
  the outer `.container`) with `.table-f` as its direct-child rows.
- Participant selectors (`.sports-table__home` / `.sports-table__away`)
  and the 1X2 odds list (`.sports-table__odds-item` with the outcome
  label embedded in each `<li>`'s own `id`, e.g. `..._market-1x2_sign-1`)
  all work against real markup.
- **Date-heading grouping**: `.sports-table` and a `.sports-head` wrapper
  (itself containing `.sports-head__date`) are both children of a common
  day-wrapper element — the heading is a sibling's descendant, not a
  sibling itself and not a nested child. This took three rounds of real
  captures to pin down: round 2 guessed nested-child (wrong — real
  capture, `date_heading_raw: null` despite 2 confirmed sections), round 3
  guessed preceding-sibling (also wrong — two more real captures,
  Highlights and LaLiga, both still `null`), round 4 confirmed the actual
  wrapper structure end to end. Every row in a table inherits that table's
  own heading. Recorded per fixture as `date_heading_raw` for audit —
  deliberately never combined with the bare kickoff time into a guessed
  `kickoff_utc`, since its exact date-string format is still unconfirmed.
- **The "1X2 1UP" second market never contaminates ordinary 1X2.** The
  real page carries a second `.sports-table__odds-list` per row for a
  "1X2 1UP" market; its odds-item `id`s use a distinct family segment
  (e.g. `..._market-1x2_1up_sign-1`) that the family-extraction pattern
  captures as its own value — never aliased to `"1x2"` — so it is routed
  to `UNSUPPORTED_MARKET_FAMILY` (audited, `expected_unsupported: true`)
  rather than merged into the real market or silently dropped.
- Fixture identity is derived from the real per-event `id`
  (`..._event-832455154`) rather than a guessed natural key, so
  recapturing the same fixture (e.g. after a page reload or an odds
  change) resolves to the same `fixture_id`.
- Kickoff honesty holds against real markup too: this row's only kickoff
  text is a bare time (`"16:00"`, no date, no year, no timezone) — the
  parser correctly reports `UNRESOLVED_NO_EXPLICIT_TIMESTAMP` rather than
  guessing a UTC value, exactly as designed.
- `source_url` is sanitized correctly on the real page
  (`https://sports.bet9ja.com/sport/soccer/1`, no query string observed
  in practice).
- No account, balance, cookie, token, or betslip data appeared in either
  real downloaded capture, and none is structurally reachable from
  `.sports-table` on either the public or authenticated page.
- **Login vs. public parity**: identical fixture-row/element counts and
  an identical first fixture confirm no authenticated-only branch is
  needed for this page.
- **Structural/spacer rows are excluded, not fabricated into fixtures.**
  A real capture showed 12 of 30 `.table-f` elements have no matchup cell
  at all (empty structural/header/spacer rows). A row candidate now
  requires a real `.sports-table__matchup` element to exist before it is
  even counted as a record — these 12 no longer appear as noisy
  `MISSING_PARTICIPANTS` audit entries, or anywhere else.
- **Sport is resolved generically from any `/competition/{sport}/...`
  URL**, not hardcoded to Soccer — confirmed against a real Basketball
  competition page. An excluded sport now identifies correctly (e.g.
  `sport_hint: "BASKETBALL"`) instead of falling back to `"UNKNOWN"`.

**What this does NOT yet confirm** (no sample seen; do not assume this
profile handles these correctly until it has been):

- **Competition/region grouping.** No competition/league wrapper has been
  observed for this page — rows are grouped by date only (see above),
  never by competition (`region`/`competition` stay `null` for this
  profile). Because of this, and because this page's collapsed-section/
  lazy-loading markup (if any) is still unconfirmed, the profile always
  reports `CAPTURE_PARTIAL` with a dedicated reason,
  `BET9JA_DESKTOP_FALLBACK_PROFILE_UNVERIFIED_COVERAGE`, even when every
  row it finds parses cleanly.
- **Live, virtual, and Zoom event markup.** No sample of any of these
  three states has been captured yet, so BET9JA_DESKTOP currently treats
  every matched row as `PRE_MATCH` unconditionally. This is a real,
  open gap — do not treat a live/virtual/Zoom event captured under this
  profile as reliably tagged until a real sample of each is supplied and
  a follow-up selector-tuning PR adds the corresponding detection.
- **Other sports.** Only `sport-1 == SOCCER` is confirmed (inferred from
  the sample's own `/sport/soccer/1` source URL); every other sport code
  is deliberately left unmapped rather than guessed.
- **`.sports-head__date`'s exact text/date format** — recorded verbatim
  as `date_heading_raw`, never parsed into a timestamp until a real
  sample of its actual text is captured and a safe, unambiguous format is
  confirmed.

A defensive ancestor-exclusion guard (`EXCLUDED_ANCESTOR_SELECTOR`) is
kept as a redundant safety net on top of the `.sports-table` root scoping
above (proven with synthetic nav/betslip decoy fixtures, each mimicking
the full real root+row shape) — never rely on it alone; the primary
guarantee is the confirmed root scoping itself.

Follow-up selector-tuning PRs should attach a new sanitized sample and
extend `BET9JA_DESKTOP_SELECTORS` (competition grouping, live/virtual/
Zoom markup, the date-heading format) the same way this one did — one
narrow PR per gap, backed by one real, evidenced sample, never guessed
ahead of evidence.

## Open bet ticket capture

A second, independent button and parser module (`ticket_parser.js`),
wired the same way as fixture capture: `popup.js`'s **Capture open bets**
click handler injects `ids.js` + `ticket_parser.js` + `content.js`, calls
`window.__bet9jaTicketCaptureRun`, and downloads one
`bet9ja-open-bets-{timestamp}.json` file. It shares `ids.js`'s deterministic
`stableId` scheme so a leg's `fixture_id` matches the fixture-capture
extension's own `fixture_id` for the same Bet9ja event — see
`legs[].fixture_id_resolution` below.

**SELECTOR STATUS: real profile confirmed (Round 2), with named gaps.**
`ticket_parser.js` now tries a confirmed real profile (`MYBETS_SELECTORS`,
root `.mybets`) first, falling back to the original 100%-unverified
`TICKET_SELECTORS` placeholder only when `.mybets` isn't found. See
"The MYBETS profile" below for exactly what Round 2's live authenticated
inspection confirmed and what remains unconfirmed — tracked in
`TICKET_REAL_PAGE_VALIDATION.md`.

### The MYBETS profile — confirmed selectors, async expand/parse/collapse

Confirmed via live inspection of the authenticated
`https://sports.bet9ja.com/myBets/` page (Round 2, 2026-09-11): tickets
render as `.mybets .accordion-item` elements, and each ticket's id and
legs are only present in the DOM once expanded (an `.accordion-item--open`
class is added; `.accordion-toggle` is the click target). This makes
`captureFromDocument` **async** when this profile is active: for each
ticket it clicks the toggle if not already open, waits for the confirmed
open class **AND** the ticket's own `.mybets-head__item` to be present
together — Round 4 real captures showed the open class can appear before
the ticket's actual content has finished rendering, so the class alone
is not sufficient evidence a ticket is ready to parse (a timeout here is
`TICKET_EXPANSION_TIMEOUT`) — parses the ticket and its legs entirely
within that ticket's own subtree, then clicks the toggle again to restore
the ticket to whatever state it was in before capture touched it. One
click, one file — you never manually expand a ticket yourself.

**Safety:** the *only* element this profile ever calls `.click()` on is a
`.accordion-toggle` inside a ticket under `.mybets` — never Cashout,
never "Reload Selections", never any bet-placement control.
`tests/ticket_parser.test.js`'s "safety" test greps this file's own
compiled source for that guarantee, the same "read the real source"
discipline `tests/structural.test.js` uses elsewhere in this project.

Confirmed structure per ticket: `.mybets-date` (placement time, raw text
only — no UTC-qualified timestamp exists in this markup, so
`placed_at_utc` stays null, never guessed), `.mybets-head__item` (ticket
id, only present after expansion), `.mybets-item` (one per leg, four
`.mybets-item__row` children each: selection+odds, market, fixture+time,
competition — or **three**, when the competition row is genuinely
absent; see Round 5 below), `.mybets__systable` (present only on system
tickets). Ticket boundaries are reliable — every ticket's expanded
detail stays inside its own `.accordion-item` — so the same
page-wide-scan defense described in "Fail-closed ticket boundaries"
below applies here too. A system ticket's 6 legs render as 3
`.mybets-row` groups of 2 `.mybets-item` legs each; individual legs are
still found directly via `.mybets-item`, independent of that visual
grouping.

**Round 5 real-capture corrections** (a real 16-page, 80-ticket capture
— see `TICKET_REAL_PAGE_VALIDATION.md`, verified end-to-end against all
19 successfully-parsed real tickets: 0 stake/return mismatches):

- **The 4-row leg layout is not universal.** 49 of 532 real legs had
  exactly 3 rows — the competition row genuinely absent — now accepted
  with `competition_raw: null` / `competition_resolution:
  'COMPETITION_UNAVAILABLE'`, never rejected. Any other row count is
  still fail-closed and unresolved, now preserving each found row's own
  text (`row_texts`) — and, for a 0-row element, its own text — for
  audit.
- **Structural, row-less `.mybets-item` elements are excluded at
  candidacy.** 12 of 532 real `.mybets-item` elements had zero
  `.mybets-item__row` children at all — confirmed structural elements
  sharing the leg class, not genuine legs (mirroring `parser.js`'s own
  structural/spacer-row exclusion). These no longer count as a
  fail-closed leg, or as a leg at all.
- **`selection` now always mirrors the trimmed `selection_raw`
  verbatim.** Real selections are team names and other market-specific
  labels ("Stade Rennes", "Czechia (Home -1.5)"), not H/D/A-style codes —
  the old attempted H/D/A mapping returned `null` for 107 of 126 real
  legs, discarding real data. Numeric-style labels ("1", "2") pass
  through unchanged, same as any other selection.
- **Stake/return fields are now mapped — for system tickets.**
  `total_stake`/`potential_return` come from
  `.mybets-holder__info-item`'s confirmed `"Stake: <amount>"`/`"Max Win:
  <amount>"` labels (reliable regardless of system complexity — amounts
  use a comma THOUSANDS separator, e.g. `"1,308.10"`, parsed
  accordingly). `unit_stake`/`ticket_type_raw` come from
  `.mybets__systable`'s confirmed concatenated-values shape (e.g.
  `"Singles835.00280.00"` = 8 bets × 35.00 unit stake = 280.00 stake) —
  but ONLY when the System Type text is letters-only and the digit split
  is arithmetically unambiguous (13 of 19 real tickets); a digit-prefixed
  type (`"4 Folds"`) or a multi-row full-cover system (6 of 19) is left
  unparsed rather than guessed, with `system_table_raw` still preserved
  for audit either way.
- **`source_event_id` has never once been found on a real leg** (0 of
  126). Every real leg's `fixture_id` is therefore provisional,
  natural-key-only identity — treat cross-referencing against the
  forecast ledger by `fixture_id` as provisional until stronger evidence
  is found.

**Named, currently-open gaps** (every MYBETS-profile capture carries a
matching `capture_status_reasons` entry for each, and can never report
`CAPTURE_OK` — capped at `CAPTURE_PARTIAL` — until they close):

- **No live/Virtual/Zoom marker identified yet.** This profile cannot
  currently enforce the "no live, Zoom or Virtual tickets" boundary the
  way the placeholder profile's (also unconfirmed) leg-status hint was
  designed to — every ticket found is treated as an open pre-match bet by
  page-context inference (`status_resolution:
  'INFERRED_FROM_OPEN_BETS_PAGE_NO_EXPLICIT_STATUS_MARKUP_CONFIRMED'`),
  never silently assumed without saying so.
  (`LIVE_VIRTUAL_ZOOM_DETECTION_UNCONFIRMED_FOR_MYBETS_PROFILE`)
- **Stake/return mapping confirmed for system tickets only.** No real
  sample of a non-system ticket (single/double/treble/accumulator) has
  been captured yet, so the mapping above remains unconfirmed for those.
  (`STAKE_RETURN_FIELD_MAPPING_CONFIRMED_ONLY_FOR_SYSTEM_TICKETS`)
- **Ticket type detection is limited to system-table presence.** A
  `.mybets__systable` element is real, confirmed evidence of a system
  ticket (`ticket_type_normalized: 'SYSTEM'`); no confirmed markup
  distinguishes single/double/treble/accumulator from each other yet, so
  they all currently report `ticket_type_normalized: null`.
  (`TICKET_TYPE_DETECTION_LIMITED_TO_SYSTEM_TABLE_PRESENCE`)

Pagination is automated (confirmed Round 3 — see "Pagination" below).

### Pagination

Confirmed via a second live authenticated inspection (Round 3, see
`TICKET_REAL_PAGE_VALIDATION.md`): 16 genuine numbered pages
(`.mybets .pg-pagination__item` whose text matches `/^\d+$/`), plus
first/prev/next/last controls that this parser **never clicks** — only a
verified numbered item is ever a click target, re-checked immediately
before the click itself, not just when it's found. Pagination is
client-side (the URL never changes); the current page number is read
from `.pg-pagination__item--current`'s own text.

The automated loop: parse and deduplicate (by `bet9ja_ticket_id`) the
current page → read the current page number → click the next numbered
page (current + 1) → wait for the `--current` marker to actually advance
to that number before parsing anything → merge that page's tickets into
the running total, deduplicating cross-page (a ticket id repeated on two
pages is counted once; a fixture legitimately appearing in two different
tickets is not affected — those keep their own distinct ticket ids) →
repeat. It stops when: no further numbered page exists (the run reached
the highest page), a page number would be revisited (a loop guard), a
page's exact ticket-id set repeats a previous page's (the transition
looked successful but the content didn't actually change), a page
transition doesn't confirm within the wait window (a timeout, never a
silent skip), or a fixed safety cap on page count is hit. Every stop
reason is recorded verbatim as `PAGINATION_STOPPED_<reason>` in
`capture_status_reasons`. If the run advanced past page 1, it clicks back
to page 1 afterward — fire-and-forget, same "restore what capture
touched" spirit as ticket collapse, never required for the correctness of
the capture that already happened.

One combined envelope covers the whole run — `coverage.pages_available`
(the highest numbered page ever seen; can grow as a windowed pagination
UI is navigated), `coverage.pages_visited`, and `coverage.
duplicate_tickets_skipped` alongside the usual ticket/leg counts. There is
still no per-ticket live/Virtual/Zoom or settled-status signal on this
page (see the gap above) — pagination completeness doesn't change that.

**Round 4 real-capture correction:** two real captures against a 16-page
account both showed `tickets_seen: 5` (only page 1's tickets) despite
`pages_visited: 16` — the `--current` marker can advance before that
page's own ticket list has actually (re)rendered, so parsing immediately
after confirming `--current` read every subsequent page as empty. A
second wait, applied once per page right after `--current` is confirmed
(before that page is parsed), gives the ticket list a chance to appear.
A page still empty when this window elapses is parsed as-is (0 tickets),
never treated as an error — a genuinely sparse last page is a real
possibility this parser cannot yet fully distinguish from a slow load;
`page_results[]` (below) records enough per-page detail for a future
round to tell the two apart from evidence.

Every capture also includes a top-level `page_results[]` array — one
entry per visited page (`page_number`, `ticket_containers_seen`,
`tickets_parsed`, `tickets_unresolved`, `tickets_expected_excluded`,
`legs_seen`, `legs_parsed`, `page_fingerprint`) — so a shortfall in the
final totals can be traced to a specific page rather than inferred
indirectly. `coverage`'s own row-accounting invariant (below) is also
checked at runtime just before the envelope is returned: a violation
(structurally unreachable given how every ticket container is processed,
but guarded anyway) forces `CAPTURE_FAILED` with
`ROW_ACCOUNTING_INVARIANT_VIOLATED` rather than ever downloading a
self-inconsistent file.

### `coverage`'s row-accounting invariant

`tickets_seen = tickets_parsed + tickets_unresolved + tickets_expected_excluded`,
every capture, no exceptions — mirroring `parser.js`'s own
`records_seen = records_parsed + records_unresolved + records_expected_unsupported`.
`tickets_expected_excluded` counts tickets excluded BY DESIGN (settled/
cashed-out, or a live/Virtual/Zoom leg) — an out-of-scope ticket, not a
malformed one — kept separate from `tickets_unresolved` (a genuine
ambiguity this parser could not confidently resolve) so a page of only
settled tickets is never mistaken for a broken capture.

### Fail-closed ticket boundaries

The single most important safety property of this parser: **a leg is only
ever read from inside its own ticket's own DOM subtree** (scoped
`querySelectorAll` calls under each ticket container element), never a
flat, page-wide leg scan — so a leg can never be structurally attributed to
the wrong ticket as long as ticket-container detection itself is
unambiguous. On top of that:

- A ticket missing its own id is never assigned a synthetic one — it is
  routed to `unresolved_tickets` (`MISSING_TICKET_ID`).
- If ANY leg inside a ticket fails to parse (missing participants,
  unparseable odds, or is itself live/Virtual/Zoom), that decision applies
  to the WHOLE ticket, never just the offending leg — a partial ticket
  would silently corrupt the stake/return math a downstream importer
  depends on `legs[]` being complete for. A live/Virtual/Zoom leg routes
  the whole ticket to `excluded_tickets` (`TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG`,
  per the "no live, Zoom or Virtual tickets" scope boundary); any other
  per-leg failure routes it to `unresolved_tickets`
  (`LEG_FAILED_TO_PARSE`).
- A settled/won/lost/void/cashed-out ticket status is excluded as
  out-of-scope (`excluded_tickets`, `TICKET_STATUS_OUT_OF_SCOPE`) — not
  treated as an error, mirroring `parser.js`'s own
  `records_expected_unsupported` precedent (an out-of-scope record is not
  a failure, even when it's the only thing a capture produces).
- An unrecognized ticket status (anything other than OPEN/PENDING or one
  of the excluded statuses above) is never guessed at — it goes to
  `unresolved_tickets` (`UNRECOGNIZED_TICKET_STATUS`).

### Output shape (`bet9ja-ticket-capture.v1`)

Deliberately **not** a direct `ledgers/betting_ledger.py`
`build_placed_event()`-ready payload: that function requires a
`forecast_id` per leg, and `forecast_id` is derived from
`(fixture_id, market_type, model_version)` — `model_version` has no
meaning at ticket-capture time in the browser. Instead, each leg exposes
both `source_event_id` (the raw Bet9ja event id, when the identity element
is found) and a computed `fixture_id` (same `bxf_...` scheme as fixture
capture), so a future, separate one-command importer step can resolve
`forecast_id` by matching `fixture_id` against the forecast ledger — see
`docs/LEDGER_DAILY_WORKFLOW.md`'s planned "IMPORT OPEN BETS" step.

Per ticket: `bet9ja_ticket_id`, `status`, `placed_at_raw` /
`placed_at_utc` / `placed_at_resolution` (same never-guess-a-timestamp
honesty as fixture capture's `kickoff_resolution`), `ticket_type_raw` /
`ticket_type_normalized` / `ticket_type_taxonomy_gap`, `unit_stake`,
`total_stake`, `potential_return` (each with a `_raw` sibling), and
`legs[]`.

**Named taxonomy gap:** `ledgers/betting_ledger.py`'s `TICKET_TYPES` is
`(SINGLE, DOUBLE, TREBLE, SYSTEM)` — there is no entry for a straight
4+-leg all-up "Accumulator"/"Fourfold" bet, a real Bet9ja UI category.
`ticket_type_raw` always preserves the exact text seen (placeholder
profile: the raw ticket-type label; MYBETS profile, since Round 5: the
confirmed "System Type" sub-value, e.g. `"Trebles"`, when
`.mybets__systable`'s digit split is unambiguous — see "The MYBETS
profile" above); `ticket_type_normalized` is only ever set for an
unambiguous SINGLE/DOUBLE/TREBLE/SYSTEM match, and
`ticket_type_taxonomy_gap: true` flags every other non-empty raw type —
surfaced for a future importer to decide, never silently mapped to the
nearest guess. This does not exclude the ticket; it is still fully
captured.

MYBETS-profile tickets carry additional profile-specific audit fields not
present on placeholder-profile output: `bet9ja_ticket_id_raw` (the full
`.mybets-head__item` text the id was extracted from), `system_table_raw`/
`stake_return_raw_items` (raw text, always preserved regardless of
whether `unit_stake`/`ticket_type_raw` parsed — see "The MYBETS profile"
above), and each leg's `competition_resolution` (`'PRESENT'` or
`'COMPETITION_UNAVAILABLE'`, since Round 5). Both profiles emit the same
core key set otherwise, so a downstream reader can treat `tickets[]`
uniformly regardless of which profile produced a given record.

## Capture all Soccer fixtures

A third, independent button and module (`soccer_walker.js`) that retires
manually selecting each competition.

**ROUND 5 REWRITE (2026-09-12) — Rounds 1-4 are fully retired.** Rounds
1-4 walked one competition at a time through
`/competition/soccer/{country}/{competition}/{ids}`, requiring a verified
return to a Coupons inventory page between every single competition. A
real capture against the live account confirmed the whole discovery/
selection/parsing pipeline worked, but also hit a real, unresolved
failure in that return step. Real, live testing then found a materially
simpler page: Bet9ja's own Competitions selector at
`/sportPage/1/competitions` lets you check multiple competitions' boxes
and click "Show Leagues" to render every selected competition's fixtures
on ONE page, with the URL never changing — removing the entire
return-between-competitions failure surface, the `javascript:;`
CSP-sensitive competition links, and the repeated route-change waits
Rounds 1-4 needed. See `SOCCER_ALL_COMPETITIONS_VALIDATION.md` for the
full Round 1-4 postmortems and this round's confirmed replacement
evidence.

**Confirmed real hierarchy:**

```text
/sportPage/1/competitions → .competitions root → one .accordion-item
  per country → .competitions__group-item rows, each a
  .sportpage__cb-input checkbox (id = the competition's own stable
  numeric id) + its own <label for="{id}"> → "Show Leagues"
  (.competitions__filter-btn.check-coupon) renders every checked
  competition's fixtures into the existing .sports-table/
  .sports-table__matchup structure WITHOUT changing the URL →
  .competitions__filter-btn.clear-all resets the selection
```

- Reaching `/sportPage/1/competitions` from an arbitrary starting page is
  **popup.js's job**, via a real `chrome.tabs.update` navigation of the
  active tab (`activeTab` already grants this), run BEFORE
  `soccer_walker.js` is even injected — `soccer_walker.js` itself only
  ever checks the current route, it never clicks anything to try to reach
  it.
- `source_competition_id` is read directly off the checkbox's own `id`
  attribute — no composite id-parsing needed, unlike the retired
  per-competition accordion.
- The checkbox is marked `readonly` in the real markup, so selection
  happens through its own `<label>`, matching the site's normal UI
  behaviour — never a raw `.click()` on the checkbox itself.

**Batching without a guessed limit.** Bet9ja enforces some maximum number
of simultaneously selected competitions, surfaced via a "Maximum
selection limit reached!" notification — but neither the exact limit nor
that notification's own selector was exposed by the inspection that found
this page (`[UNVERIFIED]` in `SOCCER_ALL_COMPETITIONS_VALIDATION.md`).
This module never invents a fixed batch size: it selects competitions one
at a time, watching each checkbox's own `.checked` state, and treats
EITHER a selection that doesn't stick OR any visible text matching the
limit-notification wording as "this batch is full" — finalizing (Show
Leagues) the current batch and deferring that competition to the next
one.

**Attribution caveat.** `parser.js` resolves each row's sport either from
an id-embedded `sport-N` segment on the row itself, or from the page's
own URL matching `/competition/{sport}/{country}/{competition}/` —
confirmed only for single-competition competition pages. On this combined
page the URL never changes, and it is **not yet confirmed** whether
Bet9ja repeats the `sport-N` id segment on every row here too, nor
whether multiple selected competitions' fixtures render as
distinguishable groups at all. Rather than guess a row-to-competition
mapping, this module never attributes an individual fixture to one
specific competition when a batch contains more than one: every fixture
instead carries `source_batch_index` and the full list of that batch's
`source_competition_ids_in_batch`/`source_competitions_raw_in_batch`.
Each competition is still classified (captured/empty/failed/deferred) at
the batch level.

Every fixture is captured by the exact same `parser.js` engine **Capture
fixtures** uses (never re-implemented here) — the same
Soccer/pre-match/ordinary-1X2 scope, the same typed `unparsed_records`
exclusions, and the same `fixture_id` scheme, so a fixture legitimately
duplicated across batches is deduplicated, not captured twice.

### Output shape (`bet9ja-soccer-all-competitions-capture.v3`)

`capture_scope: 'SOCCER_ALL_PREMATCH_COMPETITIONS'`,
`inventory_source_url`, `inventory_profile`, `competitions_route_confirmed`,
`countries_available`/`countries_visited`/`countries_failed`,
`competitions_available`/`competitions_captured`/`competitions_empty`/
`competitions_failed`/`competitions_skipped_by_safety_cap`/
`competitions_skipped_by_early_stop`, `duplicates_skipped`,
`resume_metadata` (`can_resume`, `last_completed_competition_id`,
`resume_hint` — populated whenever the walk stops early), `batch_results[]`
— one entry per attempted batch (`batch_index`, `competition_ids`, `ok`,
`failure_reason`, `records_seen`/`records_parsed`/`records_unresolved`/
`records_expected_unsupported`/`duplicate_fixtures_skipped`,
`content_change_confirmed` — whether Show Leagues' own fixture output was
actually observed to change, recorded honestly `false` when the result was
textually identical to what was already on screen, e.g. a genuinely empty
or duplicate batch), and `competition_results[]` — one entry per
discovered competition (`country_name_raw`, `competition_name_raw`,
`source_competition_id`, `batch_index`, `outcome` — one of
`CAPTURED_IN_BATCH`, `BATCH_EMPTY`, `BATCH_FAILED`, `SELECTION_FAILED` —
`failure_reason`). `fixtures[]` and `unparsed_records[]` carry every
normalized `parser.js` field plus `source_batch_index`/
`source_competition_ids_in_batch`/`source_competitions_raw_in_batch`.

Full reconciliation is enforced, as its own
`COMPETITION_ACCOUNTING_INVARIANT_VIOLATED` status reason if it ever
fails: `competitions_available = competitions_captured +
competitions_empty + competitions_failed +
competitions_skipped_by_safety_cap + competitions_skipped_by_early_stop`.

**`capture_status` is `CAPTURE_COMPLETE`, `CAPTURE_PARTIAL`, or
`CAPTURE_FAILED`** — this module's own top status name deliberately
differs from the other three buttons' `CAPTURE_OK`. `CAPTURE_COMPLETE`
requires: every discovered country/competition visited or confirmed
empty, zero failures, zero safety-cap skips, no early stop, and the
competition-accounting invariant holding.
`PARSER_VERSION` is deliberately NOT bumped past `-unverified` yet — this
hierarchy is confirmed by direct DOM testing, not yet by one real batch
capture succeeding end-to-end against the live account (see
`SOCCER_ALL_COMPETITIONS_VALIDATION.md`'s Round 5 section).

### Cancellation

An optional `context.shouldCancel()` predicate (same pattern as
`settled_bets_parser.js`'s long-pagination support) is checked between
countries during discovery and before each competition selection attempt.
A **Cancel** button in the popup sets
`window.__bet9jaSoccerAllCancelRequested`; cancelling is a safe stop, not
a failure, and produces `resume_metadata` naming the last completed
competition.

### Safety

The only elements this module ever calls `.click()` on are: a country's
own accordion toggle, a competition's own `<label>` (its associated
checkbox is `readonly`, so this IS the site's normal selection path), the
"Show Leagues" button, and the "Clear all" button — never a price,
selection, Cashout, Live Betting, or betslip control, and never the
checkbox itself directly. A dedicated "safety" test greps the compiled
source (comments stripped) for all of these guarantees, plus confirming
`location.href` is never assigned and `window.open()` is never called.

## Capture settled bets

A fourth, independent button and module (`settled_bets_parser.js`) that
captures Bet9ja's own recorded settlement from My Bets → Settled Bets. It
does not search the web, does not recalculate a ticket's outcome from a
displayed score, does not update the betting ledger, and does not change
any model — `profit_loss` is always `null` here; computing it from stake,
payout, and cashout with decimal-safe arithmetic is the (future) ledger
importer's job, not this capture's.

**SELECTOR STATUS: real profile confirmed and run end-to-end (Round 2) —
see `SETTLED_BETS_REAL_PAGE_VALIDATION.md`.** The Settled Bets tab
(`.mybets__bets-item`, exact text "Settled Bets", selected state
`.mybets__bets-item--current`), ticket/leg structure (largely the same
`.mybets`/`.accordion-item` shape `ticket_parser.js`'s Open Bets profile
already confirmed, with a third leg row carrying `.mybets-score`/
`.mybets-game`/`.mybets__info`), and pagination
(`.mybets .pg-pagination__item`) are all real, confirmed selectors — not
placeholders. A real 23-page, 115-ticket capture reconciled exactly
(115 seen = 102 parsed + 13 unresolved; 583/583 legs). An earlier estimate
of 191 pages was wrong (a raw count of every pagination element, not
genuine numbered pages) — the real, page-by-page-validated total is
whatever the current account and selected date range contain; see
"Capture scope" below.

Two things remain genuinely unconfirmed: no selector for a system
ticket's won/lost/void **combination breakdown** has been identified yet
(`system_settlement` stays `null`-valued until one is), and no confirmed
selector exists yet for the page's own displayed **date-range** control
(`date_range.from_raw`/`.to_raw` stay `null`). Leg outcome text
recognizes "Won"/"Lost"/"Cancelled" (Round 2 real evidence: 4 of 583 real
legs, across 4 tickets sharing the same underlying fixture, showed the
exact text "Cancelled" and now normalize to `leg_status: 'VOID'`); `PUSH`,
`HALF_WON`, `HALF_LOST`, `CASHED_OUT`, `PARTIAL_RETURN`, and ticket-level
`VOID` remain valid schema values this parser has not yet seen real
markup for — ticket-level VOID inference stays deliberately disabled
until a real voided TICKET summary (not just a voided leg) is observed.
An unrecognized ticket-summary word still fails that whole ticket closed
(`MISSING_OR_UNRECOGNIZED_SETTLEMENT_STATUS`) rather than being guessed.
This keeps `capture_status` permanently capped at `CAPTURE_PARTIAL` for
this version, the same pattern `ticket_parser.js`'s MYBETS profile uses.

A lost ticket never gets a manufactured `"0.00"` payout — Bet9ja's own
"Lost" summary text carries no payout at all, so `actual_payout: null`
with `payout_resolution: 'NO_EXPLICIT_PAYOUT_DISPLAYED'` is the honest
result. A won ticket's "Won `<amount>`" text yields `actual_payout` as a
validated decimal STRING (e.g. `"123.45"`), never a lossy float — odds
are captured as decimal strings for the same reason. A system ticket may
contain both Won and Lost legs while still producing one overall ticket
result (confirmed: the inspected system ticket had both) — this module
never infers the ticket's own outcome from "did every leg win"; it reads
only the ticket's own explicit summary text.

### Expansion retry (Round 2)

A real capture found 13 of 115 tickets timing out on their FIRST
expansion attempt (`TICKET_EXPANSION_TIMEOUT`) despite every
successfully-expanded ticket parsing all of its legs cleanly — an
intermittent rendering-timing issue, not a settlement-parsing defect.
`ensureTicketExpanded` now allows exactly ONE bounded retry: the ticket
is returned to a collapsed state (or confirmed already collapsed), then
re-expanded and re-checked for readiness — now requiring, alongside the
open class and ticket id, at least one candidate (non-structural)
`.mybets-item` too. A retry that still fails keeps
`TICKET_EXPANSION_TIMEOUT` and attaches `readiness_diagnostics`
(`open_class_seen`, `ticket_id_seen`, `leg_candidates_seen`) to the
`unresolved_tickets` entry, so a future round has concrete evidence of
exactly what state the ticket was left in.

### Capture scope (date range)

Bet9ja's Settled Bets view is scoped to whatever date range is currently
selected on the page itself. This module is READ-ONLY with respect to
that range — it never selects or changes dates; you pick the range on
the page, then click **Capture settled bets** once, and it captures every
page inside that range. Every envelope records `capture_scope:
'USER_SELECTED_DATE_RANGE'` and `date_range` (`from_raw`, `to_raw`,
`timezone`) honestly — both raw fields stay `null` until a selector for
the page's own displayed range control is confirmed. `pages_available`
means pages within whatever range was selected, never the account's
complete history.

### Long pagination (real accounts can span many pages)

A real 12-page capture (Round 3) found the `--current` pagination marker
advancing before the page's own ticket content had actually re-rendered
— page 2 was parsed as a byte-for-byte repeat of page 1, and the
row-accounting invariant didn't account for the resulting duplicates,
producing a false `ROW_ACCOUNTING_INVARIANT_VIOLATED`. Fixed: after the
marker advances, this module now waits for the page's own collapsed
ticket content to change too (one longer bounded retry before giving up
and stopping safely via `PAGE_CONTENT_DID_NOT_UPDATE`), the invariant now
counts `duplicate_tickets_skipped` as a valid outcome, and a duplicate
ticket's legs are counted once (not once per page it reappears on,
tallied separately in `duplicate_ticket_legs_skipped`). See
`SETTLED_BETS_REAL_PAGE_VALIDATION.md` Round 3.

- `context.onProgress(info)` fires once per page — the popup polls a
  small in-page progress object (`window.__bet9jaSettledBetsProgress`)
  once a second while the run is in flight and renders "Page N/M
  (visited K) · Tickets parsed so far: J", since a live callback cannot
  cross the `chrome.scripting.executeScript()` argument boundary.
- A **Cancel** button sets `window.__bet9jaSettledBetsCancelRequested`,
  which `context.shouldCancel` polls between pages — a user-initiated
  stop produces a `CAPTURE_PARTIAL` file with everything captured so far,
  never a discarded run.
- `envelope.resume_metadata` (`last_page_completed`, `can_resume`, a
  plain-language `resume_hint`) tells you where to pick back up if a run
  stops early — deduplication by `bet9ja_ticket_id` means re-running from
  page 1 is always safe too, just slower.
- `MAX_PAGES_SAFETY_CAP` is 500 as a hard backstop, never relied on
  normally.

### Output shape (`bet9ja-settled-bets.v1`)

`capture_status`, `capture_status_reasons`, `capture_scope`, `date_range`,
`coverage` (`pages_available`/`pages_visited`, `tickets_seen`/
`tickets_parsed`/`tickets_unresolved`/`tickets_expected_excluded`,
`duplicate_tickets_skipped`, `duplicate_ticket_legs_skipped`,
`legs_seen`/`legs_parsed`), `page_results[]`,
`tickets[]`, `unresolved_tickets[]` (each carrying `readiness_diagnostics`
when the failure was an expansion timeout), `excluded_tickets[]`, and
`resume_metadata`. Each ticket carries `bet9ja_ticket_id`,
`ticket_type_raw`/`ticket_type_normalized`, `placed_at_raw`, monetary
fields as decimal strings (`total_stake`, `actual_payout`,
`payout_resolution`), `ticket_status`/`ticket_status_raw`/
`settlement_resolution`, `profit_loss: null` (always), `system_settlement`
(`combinations_total`/`_won`/`_lost`/`_void`, `raw` — all `null` until a
breakdown selector is confirmed), `system_table_raw`, and `legs[]`. Each
leg carries `fixture_id`, `selection`/`selection_raw`, `odds` (decimal
string), `market_raw`, `fixture_and_time_raw`, `competition_raw`/
`competition_resolution`, `result_raw`/`market_result_raw` (the displayed
score), and `leg_status` (`WON`/`LOST`/`VOID`/`UNRESOLVED` so far)/
`leg_status_raw`/`settlement_resolution`.

### Safety

The only elements this module ever calls `.click()` on are the confirmed
Settled Bets tab control, `.accordion-toggle`, and a verified numeric
`.pg-pagination__item` — never Cashout, never Reload Selections, never a
betting selection or account control. A dedicated "safety" test greps the
compiled source for this guarantee, the same discipline used for
`ticket_parser.js` and `soccer_walker.js`.

## Loading it unpacked for testing

1. Chrome or Edge → `chrome://extensions` (or `edge://extensions`).
2. Enable **Developer mode**.
3. **Load unpacked** → select this `browser_extension/bet9ja_capture/`
   directory.
4. Open a Bet9ja pre-match page, log in, click the extension icon, click
   **Capture fixtures**. Try **Capture all Soccer fixtures** from
   `https://sports.bet9ja.com/sportPage/1/competitions` (or from any other
   page — popup.js navigates there itself) — expect
   `competitions_route_confirmed: true`, `competitions_available` > 0,
   and a batch-by-batch walk through every discovered competition; report
   back `countries_visited`/`countries_failed`,
   `competitions_captured`/`competitions_failed`, `batch_results[]`, and
   any `competition_results[].failure_reason` so `PARSER_VERSION` can drop
   its `-unverified` tag once a real run succeeds end-to-end. Open your
   "Open Bets"/"My Bets" page and click **Capture open bets** — it will
   expand each ticket in turn and
   walk every numbered pagination page automatically before downloading
   one combined file. Switch to Settled Bets (or just click **Capture
   settled bets** from Open Bets — it will activate the tab itself) to
   try the fourth button; on a large account, watch the live per-page
   progress and use **Cancel** to confirm a stopped run still downloads a
   valid partial file.

Not published to the Chrome Web Store in this release.

## Tests

```bash
cd browser_extension/bet9ja_capture
npm install   # installs jsdom (dev-only; the extension itself ships zero
              # runtime dependencies -- it only uses standard browser APIs)
npm test
```

`tests/parser.test.js` runs the real `parser.js` against sanitized HTML
fixtures (`tests/fixtures/*.html`) covering: normal Soccer 1X2, two
competitions on one page, repeated headings, collapsed sections,
lazy-loaded/partial coverage, a missing draw price, odds changing between
two captures, duplicate fixtures on one page, an already-live event, a
Zoom/virtual product, accented participant names, a page-layout/selector
failure, source-URL sanitization, fixture-ID stability (unchanged by
odds/capture-time/DOM-order/whitespace, changed by participants/
competition/kickoff), and time-zone honesty (a missing, year-less, or
timezone-less `data-kickoff-utc` all produce a typed unresolved state with
`kickoff_raw` preserved, never a guessed UTC value), and the BET9JA_DESKTOP
real-page fallback profile: a real sanitized row, a mixed pass/fail page,
a nav/betslip-decoy exclusion page, sibling-based date-heading grouping
plus the real "1X2 1UP" second-market exclusion, structural/spacer-row
exclusion (a matchup-less `.table-f`), the competition-page id/URL shape
(`prematch_event-{id}`, country/competition parsed from the URL), and
generic sport-slug resolution from a non-Soccer competition URL
(Basketball) — see "Real-page validation status" above.
`tests/privacy.test.js` and `tests/structural.test.js` cover the
allowlist/permission-contract properties described above.

`tests/ticket_parser.test.js` runs `ticket_parser.js` against two sets of
synthetic HTML:

- The original placeholder-profile tests (root `.open-bets`, not
  real-page-derived): a single ticket, an accumulator/treble, a system
  ticket, an unrecognized ticket-type category (taxonomy-gap flagged, not
  guessed), a repeated fixture appearing in two separate tickets without
  cross-contamination, expanded/collapsed wrapper markup, the fail-closed
  ticket-boundary contract, a missing ticket id, live/Virtual/Zoom
  exclusion, settled/cashed-out exclusion, placement-time honesty, the
  `source_event_id`-less natural-key fixture-id fallback, the
  no-tickets-found failure case, an unrecognized ticket status, and the
  privacy allowlist on unresolved/excluded ticket records.
- The MYBETS-profile tests (root `.mybets`, modeling the confirmed real
  structure — see "The MYBETS profile" above): the async expand → parse →
  collapse cycle for a collapsed ticket, an already-open ticket staying
  open (never force-collapsed), a collapsed ticket being restored after
  capture, five tickets each expanded/parsed/collapsed in strict sequence
  without cross-contamination, a 6-leg system ticket's `.mybets__systable`
  driving `ticket_type_normalized: 'SYSTEM'` with its confirmed stake
  columns mapped (Round 5), a 3-row leg (competition omitted) being
  accepted rather than rejected, a structural 0-row `.mybets-item` being
  excluded at candidacy (both Round 5), a digit-prefixed System Type
  ("4 Folds") and a multi-row full-cover system both left unparsed rather
  than guessed while `total_stake`/`potential_return` still populate from
  the always-unambiguous info items, selection normalization (named,
  numeric, and handicap-style selections all pass through as the trimmed
  raw text), the fail-closed contract for a genuinely malformed leg row
  count and unparseable odds, a missing-ticket-id refusal, an
  expand-timeout distinct from an empty ticket, the `source_event_id`/
  `fixture_id` exposure and its natural-key fallback, the coverage
  invariant, the permanent `CAPTURE_PARTIAL` cap while named gaps remain
  open, single-page-only (no-pagination-control) mode, and a privacy test
  confirming account info rendered outside `.mybets` never leaks in.
- The pagination tests (a jsdom harness that simulates client-side page
  transitions via a swapped ticket-list container and a moved `--current`
  marker, plus first/prev/next/last click counters): walking every
  numbered page and merging tickets, never clicking first/prev/next/last,
  restoring to page 1 after a multi-page walk, cross-page deduplication by
  ticket id, stopping on repeated page content, stopping on a page
  transition that never confirms (a timeout, not a silent skip), the
  `pages_available`/`pages_visited`/`duplicate_tickets_skipped` coverage
  fields, a 16-page × 5-ticket run accumulating to 80 ticket containers
  before deduplication (the exact Round 4 regression), the
  `page_results[]` per-page evidence array, and a page whose ticket list
  finishes rendering shortly AFTER `--current` advances (a direct
  regression test for the Round 4 content-timing fix).
  A dedicated "safety" test greps the compiled `ticket_parser.js` source
  itself to confirm `.click()` is only ever called on the confirmed
  accordion toggle or a verified numbered pagination item.

`tests/soccer_walker.test.js` runs `soccer_walker.js` against a synthetic
jsdom harness modeling the confirmed `/sportPage/1/competitions` batch
selector (`.competitions` root, `.accordion-item` country groups,
`.competitions__group-item` checkbox+label rows, `.competitions__filter-
btn.check-coupon`/`.clear-all`), with a configurable maximum-selection
limit that reverts a checkbox and shows a "Maximum selection limit
reached!" notification, exactly modeling Bet9ja's own unconfirmed limit
discovered operationally rather than hard-coded: a document not on
`/sportPage/1/competitions` failing closed with no click attempted, the
correct route with no countries discovered failing closed, a single
country/competition full batch capture reporting `CAPTURE_COMPLETE`,
multiple countries/competitions with no limit landing in one batch, a
selection limit reached mid-run splitting into two batches (both
captured, `Clear all` verified between them, each batch's fixtures tagged
with only ITS OWN competition ids), a competition with a missing label
being `SELECTION_FAILED` without stopping the run, a country whose
accordion never opens being `countries_failed` without stopping other
countries, Show Leagues never updating its output being a safe stop (not
a crash) when there is truly no evidence anything rendered, a missing
Show Leagues/Clear all button failing safely, cancellation before the
second batch being a safe stop with `resume_metadata`, the
competition-accounting invariant, a competition with zero configured
fixture rows being a confirmed `BATCH_EMPTY` outcome rather than a
failure, cross-batch duplicate fixtures deduplicated by `fixture_id` and
counted (never double-counted), and a safety test (comments stripped
first) confirming every `.click()` call site is a country toggle, a
competition's own label, or the Show Leagues/Clear all buttons — never
the checkbox itself, never a raw `location.href`/`window.open()`
navigation.

`tests/settled_bets_parser.test.js` runs `settled_bets_parser.js` against
a synthetic jsdom harness modeling the Round 1/Round 2 confirmed
structure: a source URL not on `/myBets/` refused before touching the
DOM, activating the Settled Bets tab by clicking the confirmed control, a
missing tab control failing closed, a lost ticket producing no
manufactured payout, a won ticket's payout parsed as a decimal string, a
system ticket with both Won and Lost legs producing one ticket result
(never inferred from "all legs won"), an unrecognized ticket-summary word
failing the whole ticket closed, an unrecognized leg outcome resolving
`UNRESOLVED` without voiding the ticket, a 3-row leg (competition
omitted) being accepted, a structural 0-row leg being excluded at
candidacy, a malformed leg row count voiding the ticket, a missing ticket
id, two tickets on one page resolved independently, the row-accounting
invariant, the permanent `CAPTURE_PARTIAL` cap, pagination walking every
numbered page while never clicking First/Previous/Next/Last, the
`onProgress` callback firing once per page, `shouldCancel` stopping the
walk early with `resume_metadata` populated, a full run reporting
`can_resume: false`, a leg with the exact text "Cancelled" normalizing to
`VOID` (ticket-level VOID inference staying disabled), a ticket whose
first expansion attempt times out but whose one retry succeeds being
parsed (not left unresolved), a ticket whose expansion never succeeds
even after that retry staying unresolved with the exact
`readiness_diagnostics` shape, `capture_scope`/`date_range` being
present (and honestly `null`-valued) on every envelope including a failed
one, a page marker that advances before its content does being tolerated
via the content-fingerprint retry, content that never updates being a
safe stop (`PAGE_CONTENT_DID_NOT_UPDATE`) rather than a false duplicate
parse, the row-accounting invariant correctly accounting for a legitimate
cross-page duplicate ticket, and a duplicate ticket's legs being counted
once in `legs_seen` (tallied separately in
`duplicate_ticket_legs_skipped`). A dedicated "safety" test greps the
compiled source to confirm `.click()` is only ever called on the
confirmed tab control, accordion toggle, or a verified numbered
pagination item.

## Boundaries (Release 1 fixtures / this release's tickets and settled bets)

- No automatic bet placement, no cashout, no "Reload Selections" — the
  MYBETS profile's only `.click()` target anywhere in this file is the
  confirmed `.accordion-toggle`, enforced by a dedicated source-grepping
  test (see "Tests" above).
- No betting-ledger import, no result search, no forecast scoring, no
  model retraining — "Capture settled bets" only captures Bet9ja's own
  recorded settlement; a separate importer (`ledgers.cli
  import-bet9ja-settled-bets`) is a later, not-yet-built work item — see
  `docs/LEDGER_DAILY_WORKFLOW.md`.
- No result lookup.
- No background scraping — capture runs only on a user click, and only
  ever expands/collapses ticket accordions and clicks verified numbered
  pagination pages already reachable from the current Open Bets tab (you
  must start there; no cross-tab or cross-page URL navigation is
  performed — pagination here is client-side clicking, never a URL
  change).
- Pagination automatically walks every numbered page and restores the
  browser to page 1 afterward — never clicks `.first`/`.prev`/`.next`/
  `.last` (see "Pagination" above).
- No backend, database, Render, or Neon.
- No all-sports parser in this release.
- No live, Zoom, or Virtual ticket capture from the placeholder profile;
  the MYBETS profile cannot yet detect these at all (named gap, see
  above) and treats every found ticket as open pre-match until real
  evidence of a live/Virtual/Zoom marker is found.
- No model, ledger, admission, or staking changes — this extension only
  produces JSON files; nothing in `ledgers/`, `src/pcbf_calculator/`, or
  `research/` is touched by it or aware of it. `forecast_id` resolution
  (matching a captured ticket's legs against the forecast ledger) is a
  separate, future importer step, not this extension's job.
- No Chrome Web Store deployment.

## Next step

Fixture capture's core real-page selector question (does this extension
work against actual Bet9ja markup at all?) is answered: yes, via the
BET9JA_DESKTOP profile, confirmed across 4 rounds of real-page validation
(see `REAL_PAGE_VALIDATION.md`) — real-page validated fixture identity
stability, honest kickoff/date handling, and privacy exclusion are all
confirmed. Its remaining named gap is live/virtual/Zoom event marking,
still unconfirmed for lack of a real sample of each state.

Open bet ticket capture's core real-page selector question is now also
answered for ticket boundaries, ids, legs, and pagination (the MYBETS
profile, confirmed Rounds 2–3) — see "The MYBETS profile" and
"Pagination" above for exactly what's confirmed vs. still named as an
open gap (live/Virtual/Zoom detection, ticket-type detection beyond
system tickets, and stake/return mapping for non-system tickets). Round
4's first real multi-page captures found two real implementation defects
(ticket expansion and pagination both reading content before it had
actually rendered) — fixed and regression-tested. Round 5's first
complete 16-page, 80-ticket capture found and fixed four more real
defects (the 4-row leg assumption, structural 0-row leg elements, the
H/D/A selection mapping, and stake/return field mapping) — verified
end-to-end against all 19 real tickets it successfully parsed (0
mismatches); see the Round 4 and Round 5 notes above and in
`TICKET_REAL_PAGE_VALIDATION.md`. The very next step is running one more
real "Capture open bets" click on the same account against these fixes
and recording the result as Round 6 — in particular, whether all 80
tickets now parse cleanly, and whether a non-system ticket (single/
double/treble/accumulator) appears to finally confirm or correct the
stake/return and ticket-type gaps that remain open for that category —
following the same evidence-driven correction discipline used throughout
this project.
