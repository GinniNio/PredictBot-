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
adapter's job, or deliberately out of scope) and do not count toward
`coverage.records_unresolved`; `false` reasons are genuine parsing
problems worth an operator's attention.

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

## Selector contract (will need real-page tuning)

`parser.js`'s `SELECTORS` object is a documented, best-effort assumption
about Bet9ja's markup, exercised so far only against the sanitized HTML
fixtures in `tests/fixtures/` — **it has not yet been verified against a
real Bet9ja page.** Expect the first real run to require edits to
`SELECTORS` only; the traversal/normalization logic underneath should not
need to change for a pure selector adjustment. See the top-of-file comment
in `parser.js` for the exact contract (root/group/row/market/outcome
selectors and the `data-*` attributes each one reads).

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
`kickoff_raw` preserved, never a guessed UTC value). `tests/privacy.test.js`
and `tests/structural.test.js` cover the allowlist/permission-contract
properties described above.

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

Once this has been run against a real Bet9ja page and its selectors tuned,
the next PR adds ticket capture using the existing `betting_ledger`
schema (`ledgers/schemas/betting_ledger.v1.schema.json`) — see
`docs/LEDGER_DAILY_WORKFLOW.md`.
