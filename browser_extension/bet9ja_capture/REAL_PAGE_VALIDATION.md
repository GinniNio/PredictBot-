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

## Round 3 — 2026-09-11 (real downloaded captures, two pages)

Two real downloaded envelopes supplied: one from the Highlights page
(`/sport/soccer/1`, 30 real rows) and one from a Basketball competition
page (`/competition/basketball/international/abaligapreseason/...`, 3
real rows).

**Highlights-page capture: a genuine success, with two real defects
found and fixed in this round.**

What passed (confirmed directly from the downloaded JSON): `CAPTURE_FAILED`
gone; `30 = 18 parsed + 12 unresolved` reconciled; 18 Soccer fixtures
normalized with unique ids and complete H/D/A prices; ordinary 1X2
correctly separated from the real "1X2 1UP" and "1X2 2UP" markets (36
`UNSUPPORTED_MARKET_FAMILY` records = 18 fixtures × 2 excluded markets
each, exactly as expected); all `PRE_MATCH`; no account/balance/betslip
data; `visible_page_only: true`.

Two defects found and **fixed in this round**:

1. **Date grouping failed** — every fixture had `date_heading_raw: null`
   despite `sections_seen: 2`. Root cause: `.sports-head__date` is the
   nearest preceding SIBLING of a `.sports-table`, not a child nested
   inside one (the round-2 assumption). Fixed via
   `findPrecedingDateHeading()`; the previous interleaved-child check is
   kept as a harmless second signal.
2. **12 false rows treated as fixture candidates** — real structural/
   spacer/header `.table-f` elements with no matchup cell and empty time/
   markets, appearing as noisy `MISSING_PARTICIPANTS` records. Fixed by
   requiring a real `.sports-table__matchup` element before a `.table-f`
   is even counted as a candidate row — a row whose matchup cell exists
   but is missing a home/away name specifically still correctly falls
   through to `MISSING_PARTICIPANTS`.

**Basketball competition-page capture: kept as the real unsupported-sport
validation sample.** What passed: `CAPTURE_FAILED` gone; one section,
three rows found; teams/kickoff/three market families extracted
correctly; all three correctly routed to `unparsed_records` as
unsupported; no account/balance/betslip data; sanitized source URL.

One defect found and **fixed in this round**: `sport_hint: ""` /
`sport="UNKNOWN"` despite the URL explicitly containing
`/competition/basketball/...`. `parseBet9jaCompetitionUrl()`'s pattern was
hardcoded to the literal sport `soccer`; generalized to capture any sport
slug, confirmed against this real basketball URL
(`sportSlug: "BASKETBALL"`). Regression-tested directly.

**On the `records_seen = records_parsed + records_unresolved` claim for
the basketball capture** (reported as `3 ≠ 0 + 0`, "`records_unresolved`
should be 3"): this is **not a defect — it is the documented, intentional
definition of `records_unresolved`**, unchanged since PR #22 and
re-stated explicitly in this PR: `records_unresolved` counts only records
whose reason is a genuine parsing problem (`expected_unsupported: false`
— e.g. `MISSING_PARTICIPANTS`, `INCOMPLETE_1X2_MARKET`); a record that is
correctly identified but simply out of scope for this release
(`expected_unsupported: true` — `UNSUPPORTED_SPORT`, `UNSUPPORTED_MARKET_FAMILY`,
live/virtual/Zoom exclusion) is "working as intended," not unresolved. All
three basketball rows are `UNSUPPORTED_SPORT` with `expected_unsupported: true`,
so `records_unresolved: 0` is correct under that definition — see
`README.md`'s "`unparsed_records[]` typed reasons" table, and
`tests/parser.test.js`'s own reconciliation test, which states this exact
caveat. Changing `records_unresolved` to count every unparsed record
regardless of `expected_unsupported` would be a real, cross-cutting schema
change affecting every existing consumer and test of this envelope — out
of scope for a narrow selector-tuning correction. Flagged here rather than
silently applied or silently ignored; happy to make that change in a
dedicated PR if wanted, once weighed against what it would break.

`date_heading_raw` was also `null` for all three basketball rows —
consistent with defect 1 above (now fixed) rather than a separate issue.

### Pass-criteria checklist update

| Criterion | Status after round 3 | Notes |
|---|---|---|
| Date grouping (`date_heading_raw` populated) | ✅ Fixed and regression-tested | Sibling-based lookup, proven against a 2-`.sports-table` fixture matching the real page's structure. |
| False structural rows excluded | ✅ Fixed and regression-tested | Matchup-cell gate; a genuinely missing home/away on a real matchup cell still audits correctly. |
| Sport identification on non-Soccer competition pages | ✅ Fixed and regression-tested | Generic sport-slug URL pattern; basketball confirmed. |
| Every visible Soccer 1X2 fixture captured once | ✅ Confirmed end-to-end | Real downloaded capture: 30 rows seen, 18 correctly normalized, 12 correctly excluded as structural. |
| `records_seen = records_parsed + records_unresolved` | ✅ Holds, by design, once `expected_unsupported` is accounted for | Confirmed on both real captures; the basketball capture's apparent mismatch is the documented `expected_unsupported` exclusion, not a bug — see above. |
| Live/Zoom/virtual events correctly marked | ❌ **Still the one open gap** | No real sample of any of these three states supplied yet. |

### Recommendation

Ticket capture should remain paused until: (1) this correction PR merges,
and (2) fixture-ID stability is confirmed with a second real capture after
a genuine odds change on the same fixture(s) (not yet directly supplied —
round 1's before/after odds-change proof was a synthetic test fixture, not
two real downloaded captures of the same live fixture). One ordinary
Soccer **competition** page capture (e.g. England Premier League) — as
opposed to the mixed Highlights page — would also close the loop on
`region`/`competition` populating correctly end-to-end from a real
download, not just from the four DOM-inspection-only competition pages
checked in round 2.

## Round 3 amendment — 2026-09-11 (same day, before merge)

Confirmed: the pushback above (`records_unresolved` staying
`expected_unsupported`-exclusive) was correct and is kept as-is. Rather
than redefine an existing field, this amendment adds a new one instead:
`coverage.records_expected_unsupported`, so the full row accounting is
explicit and universal (no longer needing the "holds by design, once
accounted for" caveat from the table above):

```
coverage.records_seen
  = coverage.records_parsed
  + coverage.records_unresolved
  + coverage.records_expected_unsupported
```

This is a genuine per-**row** invariant (see README.md's own section on
it), not per-`unparsed_records`-event: a row is classified once, by
`processRow`'s own return value, regardless of how many separate audit
events it generates. Concretely: a Soccer row with a real 1X2 market plus
its excluded "1X2 1UP"/"1X2 2UP" siblings still counts once toward
`records_parsed` — the 36 real `UNSUPPORTED_MARKET_FAMILY` audit entries
across the 18-fixture Highlights capture never inflate
`records_expected_unsupported`, exactly as required. Regression-tested
directly against that shape
(`bet9ja_desktop_date_headings_and_1up.html`'s 1X2-plus-1UP row) and
against the real basketball sample (all 3 rows: `records_parsed: 0`,
`records_unresolved: 0`, `records_expected_unsupported: 3`).

| Capture | `records_parsed` | `records_unresolved` | `records_expected_unsupported` |
|---|---:|---:|---:|
| Basketball sample (regression test) | 0 | 0 | 3 |
| Soccer 1X2-plus-1UP shape (regression test) | 3 | 0 | 0 |

The full round-1/2/3 Highlights capture (18 parsed, 12 structural rows
excluded before ever reaching `processRow`, 36 audited-but-not-row-counted
1UP/2UP markets) has not yet been re-captured with this exact build to
confirm `records_seen: 18, records_parsed: 18, records_unresolved: 0,
records_expected_unsupported: 0` end-to-end — that is one of the three
real captures requested next (see below).

### Next real captures requested (before merge's outcome is fully closed)

1. **Soccer Highlights** (`/sport/soccer/1`) — expect dates populated
   (`date_heading_raw` non-null) and the 12 structural rows excluded
   (`records_seen` near 18, not 30).
2. **One specific Soccer competition page** (e.g. England Premier
   League) — expect `region`/`competition` populated from the URL.
3. **Basketball competition page** — expect `sport_hint: "BASKETBALL"`
   (not `"UNKNOWN"`) and all rows counted under
   `records_expected_unsupported`, none under `records_unresolved`.

## Round 4 — 2026-09-11 (four real captures against the merged PR #24 build)

Four real downloaded captures supplied: Soccer Highlights (18 fixtures),
Spain LaLiga (10 fixtures), a repeat Soccer Highlights capture 47 seconds
later, and Baseball MLB (15 rows, all excluded).

**Confirmed passing, all four captures:**

- `CAPTURE_FAILED` gone everywhere; every capture satisfies
  `records_seen = records_parsed + records_unresolved + records_expected_unsupported`.
- Highlights: 18 seen, 18 parsed — the round-3 structural-row fix holds
  (was 30 seen before that fix).
- LaLiga: 10/10 parsed, `region: "spain"` / `competition: "laliga"`
  correctly populated from the URL.
- MLB: 15/15 rows correctly `UNSUPPORTED_SPORT` /
  `records_expected_unsupported: 15`, `records_unresolved: 0` — the
  round-3-amendment field is confirmed working end to end on a real,
  different sport (baseball, not basketball this time — same
  `parseBet9jaCompetitionUrl()` generic-sport-slug fix, different sport,
  same correct result).
- Real "1X2 1UP"/"1X2 2UP" markets confirmed still correctly separated
  from ordinary 1X2 on both Soccer captures.
- All 28 normalized Soccer fixtures (18 + 10) have complete H/D/A prices;
  every fixture ID is unique within its own capture.
- The two Highlights captures (14:23:20 and 14:24:47, 47 seconds apart)
  share all 18 fixture IDs exactly — fixture identity survives a repeat
  capture. No price changed between the two, so stability specifically
  **after a genuine odds movement** is still **[UNVERIFIED]**.
- No account, balance, authentication, or betslip data in any of the four
  files.

**One defect found and fixed in this round: date grouping still failed.**

Both Soccer captures (18 + 10 fixtures, 2 and 4 confirmed sections
respectively) came back with `date_heading_raw: null` on every single
fixture — proving the round-3 "preceding sibling of `.sports-table`"
assumption wrong too (it was itself a correction of round 2's wrong
"nested child" assumption). Live DOM re-inspection found the actual
structure: `.sports-table` and a `.sports-head` wrapper (containing
`.sports-head__date`) are both children of a common day-wrapper element —
the heading is a **sibling's descendant**, neither a sibling nor a nested
child. Fixed via `findPrecedingDateHeading()`:

```javascript
const wrapper = table.parentElement;
const headingEl = wrapper?.querySelector(':scope > .sports-head .sports-head__date');
```

Regression-tested by restructuring `bet9ja_desktop_date_headings_and_1up.html`
to the confirmed wrapper shape (two day-wrappers, each with its own
`.sports-head`/`.sports-table` pair) — the same test assertions
(`date_heading_raw` per fixture, `sections_seen: 2`) now pass against the
corrected structure. This is a narrow, single-purpose PR (#25) fixing only
date association — no other selector, accounting, or scope changes.

### Pass-criteria checklist update

| Criterion | Status after round 4 | Notes |
|---|---|---|
| Date grouping (`date_heading_raw` populated) | ✅ Fixed and regression-tested (third attempt) | Sibling's-descendant wrapper lookup, confirmed against two real captures that falsified both prior guesses. |
| Structural-row exclusion holds on a real capture | ✅ Confirmed | Highlights: 18 seen / 18 parsed, not 30. |
| `region`/`competition` from a real competition-page download | ✅ Confirmed | LaLiga: `spain` / `laliga`. |
| `records_expected_unsupported` on a real, different unsupported sport | ✅ Confirmed | MLB: 15/15, `records_unresolved: 0`. |
| Fixture-ID stability across a repeat capture | ✅ Confirmed (no odds change) | 18/18 identical ids, 47 seconds apart. |
| Fixture-ID stability **after a genuine odds change** | ❓ **[UNVERIFIED]** | No price differed between the two captures supplied. |
| Live/Zoom/virtual events correctly marked | ❌ **Still the one open gap** | No real sample of any of these three states supplied yet. |

### Recommendation

Merge PR #25 (this date-association fix) once CI passes, then run one more
Highlights capture to confirm `date_heading_raw` populates correctly
end-to-end against the real page. Ticket capture should wait for that
result. Fixture-ID stability after a genuine odds change and live/Zoom/
virtual marking remain open items beyond this PR's narrow scope.
