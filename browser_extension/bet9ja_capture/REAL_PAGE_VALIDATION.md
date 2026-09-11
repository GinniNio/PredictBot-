# Real-page selector validation — round 1

Point-in-time record of the extension's first test against an actual Bet9ja
page, per the validation process in `README.md` ("Real-page validation
status"). Updated per round; each round gets its own dated section below
rather than overwriting the last.

## Round 1 — 2026-09-11

**Tester-supplied facts** (from the two downloaded capture JSON files and
one sanitized HTML sample provided in this session):

| Field | Value | Source |
|---|---|---|
| Bet9ja page type | Pre-match Soccer sportsbook | `page_title`: "Bet9ja Nigeria Sport Betting, Premier League Odds, Casino, Bet" |
| Source URL | `https://sports.bet9ja.com/sport/soccer/1` | Both capture JSON files' `source_url` (already sanitized: no query string, no fragment) |
| Capture 1 timestamp | 2026-09-11T13:13:45.634Z | `e15a1e17-bet9jafixtures20260911T131345Z.json` |
| Capture 2 timestamp | 2026-09-11T13:15:29.921Z | `21774aed-bet9jafixtures20260911T131529Z.json` |
| Browser + version | **[UNVERIFIED — not supplied by tester]** | Not present in either capture JSON; the envelope schema does not record it. Fill in before treating this round as complete evidence. |

**Result: FAILED, correctly.**

Both captures returned:
```json
{
  "capture_status": "CAPTURE_FAILED",
  "capture_status_reasons": ["SELECTOR_ROOT_NOT_FOUND"],
  "coverage": { "sections_seen": 0, "records_seen": 0, "records_parsed": 0, "records_unresolved": 0, "visible_page_only": true },
  "fixtures": [],
  "unparsed_records": []
}
```

Confirmed directly from the two JSON files:
- The extension loaded, ran on a single click, and produced exactly one
  download each time (`✅` per capture — one click, one download).
- `capture_status: CAPTURE_FAILED` with a named reason
  (`SELECTOR_ROOT_NOT_FOUND`) — no records were ever falsely reported as
  captured (`fixtures: []`, `records_parsed: 0`).
- `coverage.visible_page_only: true` in both.
- `source_url` contains no query string or fragment.
- No account, balance, cookie, token, or betslip data anywhere in either
  file (both are effectively empty envelopes plus metadata).

**Root cause**: the real Bet9ja desktop DOM does not match any LEGACY
`SELECTORS.root` candidate. The tester supplied one sanitized real fixture
row (`tests/fixtures/bet9ja_desktop_real_sample.html`) showing the actual
shape: `.table-f` rows with participant/kickoff/odds data, identity, sport,
and market/outcome-label all embedded in element `id`s rather than in the
`data-*` attributes the LEGACY profile expects.

**Fix delivered in this PR**: a second selector profile,
BET9JA_DESKTOP (`parser.js`'s `BET9JA_DESKTOP_SELECTORS`), used only when
the LEGACY root is not found. Regression-tested directly against the
supplied sample — see `tests/parser.test.js`'s `BET9JA_DESKTOP: ...` tests
and `README.md`'s "Real-page validation status" for exactly what is and
isn't confirmed by this one sample.

### Pass-criteria checklist (per the original 11-point list)

| Criterion | Status after this fix | Notes |
|---|---|---|
| Every visible Soccer 1X2 fixture captured once | ⏳ **Needs round 2** | Regression-proven for the one supplied row; a live page has many more — needs a full-page recapture to confirm. |
| H/D/A prices map to correct participants | ✅ Confirmed | `tests/parser.test.js`: real row maps to exactly `1.14 / 7.30 / 12.25`. |
| `records_seen = records_parsed + records_unresolved` | ✅ Confirmed for rows without an expected-unsupported reason | Holds for every fixture tested here; does NOT hold in general for a live/virtual/Zoom/unsupported-sport row (see README) — needs round 2 against a page containing those. |
| Fixture IDs survive odds changes and page reordering | ✅ Confirmed for odds changes (via existing DOM-order/odds-change tests + real-event-id-based identity) | Real reordering/reload not yet re-tested against the live page under the new build. |
| Kickoff UTC only when year+timezone resolvable | ✅ Confirmed | Real row's bare `"16:00"` correctly resolves `UNRESOLVED_NO_EXPLICIT_TIMESTAMP`, not a guess. |
| Unsupported markets retained with typed reasons | ⏳ **Needs round 2** | No real unsupported-market sample seen yet (round 1's failing page never reached row-level parsing at all). |
| Live/Zoom/virtual events correctly marked | ❌ **Open gap, not yet supported for real markup** | No sample of any of these three states has been captured against BET9JA_DESKTOP; it currently marks every matched row `PRE_MATCH` unconditionally. Needs a real sample + follow-up PR. |
| Source URL has no query string/fragment | ✅ Confirmed | Both real captures. |
| No account/balance/ticket/cookie/betslip data | ✅ Confirmed | Both real captures; also defended structurally by the nav/betslip ancestor-exclusion test. |
| One click creates one download | ✅ Confirmed | Both real captures. |
| `visible_page_only: true` | ✅ Confirmed | Both real captures. |

### Recommendation

**Do not proceed to ticket capture yet.** Merge this selector-tuning PR,
then run round 2 against the live page with the new build:
1. Confirm the BET9JA_DESKTOP profile now captures real fixtures instead
   of `CAPTURE_FAILED` (should resolve the ⏳ full-page-coverage item).
2. Deliberately find and capture one visible unsupported-market or
   mixed-sport page (⏳ item above).
3. Deliberately find one visible live and, if reachable, one virtual/Zoom
   event on the real site (❌ item above) — if BET9JA_DESKTOP mis-tags
   these as `PRE_MATCH`, that is a real, expected finding for a follow-up
   selector-tuning PR, not a regression in this one.
4. Note the browser + version this time.

Once round 2's checklist is fully ✅ (or every remaining ❌/⏳ is
consciously accepted as an explicit, documented Release-1 limitation
rather than an oversight), proceed to ticket capture.

## Round 2 — 2026-09-11 (live DOM inspection + multi-competition check)

**Tester-supplied facts**, this time from direct live-DOM inspection
(browser element inspector) of `https://sports.bet9ja.com/sport/soccer/1`,
both public and authenticated, plus four competition pages:

| Check | Public | Logged in |
|---|---:|---:|
| Fixture rows (`.table-f`) | 30 | 30 |
| Home-team elements | 21 | 21 |
| Away-team elements | 21 | 21 |
| Matchup event IDs | 21 | 21 |
| Standard 1X2 price elements | 63 | 63 |
| Date headings (`.sports-head__date`) | 2 | 2 |

First fixture identical in both views: Ararat-Armenia vs FC Syunik, 16:00,
`1.14 / 7.30 / 12.25`. Browser + version was again not supplied — still
**[UNVERIFIED]**.

Additionally, four live competition pages were checked and confirmed to
share the identical structure:

| Competition | Fixture rows | Structure |
|---|---:|---|
| [Netherlands Eredivisie](https://sports.bet9ja.com/competition/soccer/netherlands/eredivisie/1-11077-1016657) | 12 | Same |
| [England Premier League](https://sports.bet9ja.com/competition/soccer/england/premierleague/1-11058-170880) | 31 | Same |
| [Spain LaLiga](https://sports.bet9ja.com/competition/soccer/spain/laliga/1-11441-180928) | 39 | Same |
| [France Ligue 1](https://sports.bet9ja.com/competition/soccer/france/ligue1/1-11442-950503) | 59 | Same |

One confirmed variation: the Highlights page's row ids use
`home_highlights_sport-1_event-{id}`; competition-page ids use
`prematch_event-{id}` (no `sport-N` segment). Competition-page URLs
themselves reliably encode country and competition
(`/competition/soccer/{country}/{competition}/...`); the Highlights page
mixes competitions and cannot be safely attributed to one.

**Fix delivered in this round** (same PR): `BET9JA_DESKTOP_SELECTORS` now
uses `.sports-table` as a confirmed root (not a document-wide scan),
`.sports-head__date` for date-based row grouping (`date_heading_raw`,
recorded but never parsed into a timestamp), a prefix-agnostic event-id
pattern that resolves identity on both id shapes, and
`parseBet9jaCompetitionUrl()` for country/competition/sport on confirmed
competition-page URLs. A confirmed second odds list ("1X2 1UP") is
excluded from the real 1X2 market by its own distinct id family
(`1x2_1up`) while still being retained for audit as
`UNSUPPORTED_MARKET_FAMILY` — never merged, never silently dropped.

### Pass-criteria checklist update

| Criterion | Status after round 2 | Notes |
|---|---|---|
| Every visible Soccer 1X2 fixture captured once | ✅ Confirmed structurally | Root now scoped to the real `.sports-table` container (not a document-wide scan); row/element counts match exactly between public and authenticated views. Still needs one real downloaded JSON from this exact build to close the loop end-to-end (see Recommendation). |
| Unsupported markets retained with typed reasons | ✅ Confirmed | The real "1X2 1UP" second market is regression-tested: routed to `UNSUPPORTED_MARKET_FAMILY`, never merged into 1X2, never dropped. |
| Live/Zoom/virtual events correctly marked | ❌ **Still an open gap** | No real sample of any of these three states supplied yet. |
| Fixture IDs survive odds changes and page reordering | ✅ Confirmed | Unchanged from round 1; now also proven stable across the two id shapes (Highlights vs. competition page). |
| No account/balance/ticket/cookie/betslip data | ✅ Confirmed, twice over | Round 1's real captures, PLUS round 2's structural DOM inspection: login-added account chrome does not appear inside `.sports-table`, any row, or any 1X2 price element. |

### Recommendation

**Reload the extension locally and run round-two JSON capture** against
the same authenticated page with this build, to get one more real,
downloaded envelope (not just a DOM-structure inspection) confirming:
`capture_status` is no longer `CAPTURE_FAILED`, `records_seen` is close to
30, and `records_parsed` + `records_unresolved` accounts for all of them
per-row. Live/Zoom/virtual marking remains the one clearly open gap for a
future selector-tuning PR (needs a real sample of each state). Once the
local JSON capture confirms the above, this PR is ready to merge and
ticket capture can begin.
