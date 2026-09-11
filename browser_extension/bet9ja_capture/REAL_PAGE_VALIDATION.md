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
