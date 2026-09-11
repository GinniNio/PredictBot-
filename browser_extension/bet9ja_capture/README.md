# Bet9ja fixture capture — Release 1

A Manifest V3 browser extension (Chrome/Edge) that captures the pre-match
Soccer 1X2 fixtures visible on an already-open, already-authenticated Bet9ja
tab into one normalized JSON file. This replaces copy-pasting a Bet9ja page
into a chat host as the first step of `docs/LEDGER_DAILY_WORKFLOW.md`.

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
- No automatic bet placement, no open-bet or settled-bet capture, no
  result lookup.
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
- **Date-heading grouping**: `.sports-head__date` elements are the
  nearest preceding SIBLING of a `.sports-table` (confirmed via a real
  downloaded capture, round 3 — an earlier assumption that it was nested
  as a child inside the table was wrong: that real capture had 2
  confirmed date sections but every fixture came back with
  `date_heading_raw: null`). Every row in a table inherits that table's
  own preceding heading. Recorded per fixture as `date_heading_raw` for
  audit — deliberately never combined with the bare kickoff time into a
  guessed `kickoff_utc`, since its exact date-string format is still
  unconfirmed.
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

## Loading it unpacked for testing

1. Chrome or Edge → `chrome://extensions` (or `edge://extensions`).
2. Enable **Developer mode**.
3. **Load unpacked** → select this `browser_extension/bet9ja_capture/`
   directory.
4. Open a Bet9ja pre-match page, log in, click the extension icon, click
   **Capture fixtures**.

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

## Boundaries (Release 1)

- No automatic bet placement.
- No open-bet or settled-bet capture.
- No result lookup.
- No background scraping — capture runs only on a user click.
- No backend, database, Render, or Neon.
- No all-sports parser in this release.
- No model, ledger, admission, or staking changes — this extension only
  produces a JSON file; nothing in `ledgers/`, `src/pcbf_calculator/`, or
  `research/` is touched by it or aware of it.
- No Chrome Web Store deployment.

## Next step

The core real-page selector question (does this extension work against
actual Bet9ja markup at all?) is answered: yes, via the BET9JA_DESKTOP
profile, confirmed against both the Highlights page and four competition
pages, public and authenticated. The remaining gap under "What this does
NOT yet confirm" above — live/virtual/Zoom event marking — is the clear
candidate for its own narrow, evidence-backed follow-up PR once a real
sample of each state is captured. Per `REAL_PAGE_VALIDATION.md`'s
Recommendation, one more real downloaded JSON capture against the live,
authenticated page with this build should happen before treating
real-page validation as fully closed. Once that's done, the next PR adds
ticket capture using the existing `betting_ledger` schema
(`ledgers/schemas/betting_ledger.v1.schema.json`) — see
`docs/LEDGER_DAILY_WORKFLOW.md`.
