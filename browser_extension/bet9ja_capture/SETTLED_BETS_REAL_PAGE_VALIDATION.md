# Real-page selector validation — capture settled bets

Point-in-time record of the "Capture settled bets" button's testing
against the actual Bet9ja Settled Bets view, mirroring the process
`REAL_PAGE_VALIDATION.md`, `TICKET_REAL_PAGE_VALIDATION.md`, and
`SOCCER_ALL_COMPETITIONS_VALIDATION.md` used for the other three buttons.
Updated per round; each round gets its own dated section below rather than
overwriting the last.

## Round 1 — 2026-09-11 (confirmed selector contract, no click yet on the
real account)

**Status: real DOM structure confirmed via live authenticated inspection;
settlement-status vocabulary and system-settlement combination breakdown
only partially confirmed. Implemented and unit-tested against synthetic
markup built from this round's evidence; not yet run end-to-end against
the live account (that is Round 2's job, mirroring
TICKET_REAL_PAGE_VALIDATION.md's own Round 2/Round 5/Round 6 progression).**

### Confirmed by live inspection (this round)

- Settled Bets tab: `.mybets__bets-item` elements, one with exact text
  "Settled Bets"; the currently-selected tab carries
  `.mybets__bets-item--current`.
- Tickets: `.mybets .accordion-item`, expanded via `.accordion-toggle`
  (adds `.accordion-item--open`) -- structurally identical to the Open
  Bets view's own confirmed MYBETS profile (ticket_parser.js).
- Ticket type text: `.accordion-text`. Displayed date: `.mybets-date`.
  Ticket id: `.mybets-head__item`. System details: `.mybets__systable`.
- Legs: `.mybets-item` elements containing at least one
  `.mybets-item__row` are candidates; a structural `.mybets-item` with
  zero rows was confirmed present and must be excluded (mirrors
  ticket_parser.js's own 0-row exclusion). A real candidate leg has 4 rows:
  row 1 (`.mybets-bet` selection, `.mybets-odd` odds), row 2 (market, whole
  row text), row 3 (`.mybets-score` score, `.mybets-game` fixture/time,
  `.mybets__info` explicit leg outcome), row 4 (`.mybets-game`
  competition).
- The inspected expanded ticket had 5 real legs, both `Won` and `Lost`
  leg outcomes present in the same system ticket, and a score under
  `.mybets-score` for every real leg.
- Ticket summary: `.mybets-holder`'s `.mybets-holder__info-item` children
  -- the FIRST is the stake, the SECOND is the overall result. Confirmed
  values across 5 real tickets: two displayed bare "Lost" (no payout
  shown at all), three displayed "Won `<amount>`" (e.g. "Won 123.45").
- Pagination: `.mybets .pg-pagination__item`, current page via
  `.pg-pagination__item--current`. Confirmed 191 numbered pages plus
  First/Previous/Next/Last controls (195 pagination elements total) on
  the inspected account; 5 ticket containers on the inspected first page.

### What is implemented (logic, unit-tested against synthetic markup built
from the confirmed shapes above)

- `settled_bets_parser.js`'s `captureFromDocument`: confirms the source
  URL is a My Bets page, clicks the confirmed Settled Bets tab only if not
  already current, waits for `.mybets__bets-item--current` to move, then
  reuses the confirmed ticket/leg/pagination mechanics.
- Ticket summary parsing: "Lost" -> `ticket_status: 'LOST'`,
  `actual_payout: null`, `payout_resolution:
  'NO_EXPLICIT_PAYOUT_DISPLAYED'` -- never a manufactured "0.00". "Won
  `<amount>`" -> `ticket_status: 'WON'`, `actual_payout` as a validated
  decimal string.
- Leg outcome parsing: exact "Won"/"Lost" text under `.mybets__info` maps
  to `leg_status` WON/LOST; anything else (including no confirmed text at
  all) resolves `UNRESOLVED`, never guessed. `VOID`, `PUSH`, `HALF_WON`,
  `HALF_LOST`, `CASHED_OUT`, and `PARTIAL_RETURN` remain valid schema
  values this parser has not yet observed real markup for.
- System tickets: a ticket's own `ticket_status`/`actual_payout` is read
  only from its own summary text, never inferred from "did every leg
  win" -- confirmed necessary, since the inspected system ticket had both
  Won and Lost legs under one ticket result.
- Long pagination support: `context.onProgress` (fires once per page),
  `context.shouldCancel` (checked between pages, produces a
  `CAPTURE_PARTIAL` envelope with everything captured so far, never
  discarded), and `envelope.resume_metadata` (last page completed, a
  plain-language resume hint). `MAX_PAGES_SAFETY_CAP` raised to 500 (well
  above the confirmed 191) so a larger real account is never truncated by
  an unrelated cap.
- Fail-closed rules implemented: missing ticket id, expansion timeout,
  missing/unrecognized ticket-summary text, an unsafe/malformed leg,
  malformed stake/payout text, and `CONFLICTING_DUPLICATE_ID` (same
  `bet9ja_ticket_id` reappearing with different content).
- Safety: the only elements ever clicked are the confirmed Settled Bets
  tab control, `.accordion-toggle`, and a verified numeric
  `.pg-pagination__item` -- enforced by a source-grepping test.

### What is NOT yet confirmed

- **End-to-end run against the live, authenticated account.** This
  round's evidence came from live DOM inspection of the confirmed
  selectors and structure, not from running this exact module against
  the real 191-page account start to finish. Round 2 should run the real
  capture and report `tickets_seen`/`tickets_parsed`/`tickets_unresolved`
  reconciliation across all pages, exactly as
  TICKET_REAL_PAGE_VALIDATION.md's Round 5/6 did for Open Bets.
- **System-settlement combination breakdown.** No selector for a
  won/lost/void combination count has been identified yet --
  `system_settlement` stays entirely `null`-valued until real evidence
  supplies one. Do not guess this ahead of that evidence.
- **Outcomes beyond Won/Lost.** No real example of a Void, Push, Half
  Won, Half Lost, or Cashed Out leg or ticket has been inspected yet --
  widen `normalizeWonLost`'s recognized vocabulary only against real
  evidence of that exact text.
- **`settled_at_raw`/`settled_at_utc`.** No confirmed settlement-date
  selector distinct from `.mybets-date` (the displayed/placed date) has
  been identified -- both stay `null` until real evidence supplies one.
- Whether a leg's `.mybets-score` and the ticket's own summary payout can
  ever disagree in a way this parser should reconcile (e.g. a voided leg
  in an otherwise-won system ticket) -- not yet observed.

### Recommendation for Round 1 (superseded by Round 2's own results below)

Run this module against the real, authenticated account (a representative
subset first, e.g. the first ~5 pages, is a reasonable initial check
before a full run) and report the same kind of reconciliation table
TICKET_REAL_PAGE_VALIDATION.md's Round 5/6 used: pages visited vs.
available, tickets seen/parsed/unresolved, duplicate/conflicting IDs, and
any leg or ticket that resolved `UNRESOLVED` for a reason other than an
outcome word this round didn't yet cover -- so any real gap traces to a
specific page/ticket rather than being inferred from the aggregate counts
alone.

## Round 2 -- 2026-09-11 (real end-to-end capture; expansion-timeout and
Cancelled-outcome corrections)

**Status: real 23-page, 115-ticket capture run end-to-end against the
live, authenticated account. All page and ticket totals reconciled
exactly. Two real defects found and fixed this round; `capture_status`
remains permanently `CAPTURE_PARTIAL` pending the still-open gaps below.**

### Results

| Check | Result |
| --- | ---: |
| Pages discovered/visited | 23 / 23 |
| Tickets seen | 115 |
| Tickets parsed | 102 |
| Tickets unresolved | 13 |
| Legs seen/parsed | 583 / 583 |
| Unique parsed ticket IDs | 102 / 102 |
| Ticket statuses | 76 won, 26 lost |
| Won tickets with payout | 76 / 76 |
| Lost tickets with null payout | 26 / 26 |
| Sensitive-data indicators | 0 |

`115 seen = 102 parsed + 13 unresolved`; `583 legs seen = 583 legs
parsed`.

### Correction to Round 1's own page-count estimate

Round 1's "191 numbered pages confirmed present" was wrong -- it counted
every pagination DOM element (including First/Previous/Next/Last-style
controls, apparently miscounted as part of one numbered-page list), not
genuine numbered pages. This capture's own page-by-page evidence (23
pages, 5 ticket containers per page, exact reconciliation at every level)
is the validated total for this capture; treat page counts going forward
as scoped to whatever date range is selected on the page (see CAPTURE
SCOPE / DATE RANGE in `settled_bets_parser.js`'s header comment), not the
account's complete history.

### Defect 1 -- 13 intermittent expansion timeouts (fixed)

All 13 unresolved tickets (pages 3, 15, 16, 17, 20, 21, 22) failed with
`TICKET_EXPANSION_TIMEOUT` on their first attempt, despite every
successfully-expanded ticket parsing all of its legs cleanly -- evidence
of an intermittent rendering-timing issue, not a settlement-parsing
defect. Fixed via one bounded retry (collapse-or-confirm-collapsed, then
re-expand and re-check readiness, now also requiring at least one
candidate leg present) plus `readiness_diagnostics` on any ticket that
still fails after the retry. Not yet re-run against the real account to
confirm the retry actually eliminates these on a live page (unit-tested
against synthetic markup only) -- see Round 3 recommendation below.

### Defect 2 -- "Cancelled" is a real leg outcome (fixed)

4 of 583 real legs, across 4 separate tickets, exposed the exact leg
outcome text "Cancelled" -- all four for the same underlying fixture.
Now normalized to `leg_status: 'VOID'` /
`settlement_resolution: 'EXPLICIT_BOOKMAKER_MARKUP'`. Ticket-level VOID
inference remains deliberately disabled -- no real voided TICKET summary
(as opposed to a voided leg inside an otherwise Won/Lost ticket) has been
observed.

### What is still NOT confirmed

- **The expansion-retry fix has not itself been re-run against the live
  account.** This round's fix is evidence-driven (13 real timeouts, all
  clearing on a first-attempt-fails story) but only unit-tested against
  synthetic markup so far.
- **System-settlement combination breakdown** -- still no confirmed
  selector; `system_settlement` stays entirely `null`-valued.
- **The date-range display control** -- no confirmed selector for reading
  back the page's own currently-selected date range;
  `date_range.from_raw`/`.to_raw` stay `null`.
- **Outcomes beyond Won/Lost/Cancelled** -- no real example of Push, Half
  Won, Half Lost, Cashed Out, or a voided TICKET (not just leg) has been
  observed yet.
- **`settled_at_raw`/`settled_at_utc`** -- still no confirmed
  settlement-date selector distinct from `.mybets-date`.

### Recommendation for Round 2 follow-up (superseded by Round 3's own
results below)

Repeat one real capture. Closure criteria before the settled-bets ledger
importer begins: 23 pages visited (or whatever the then-current date
range and account contain), tickets seen matching the account (subject to
change), expansion timeouts eliminated or materially reduced by the
retry, the four cancelled legs normalizing to `VOID`, and ticket/page
reconciliation remaining exact.

## Round 3 -- 2026-09-12 (pagination timing regression found and fixed)

**Status: a real 12-page capture surfaced a genuine defect --
`ROW_ACCOUNTING_INVARIANT_VIOLATED` -- traced to a pagination-timing race,
not a settlement-parsing bug. Fixed; not yet re-run against the live
account.**

### Results

The selected range contained 12 pages; capture stopped after page 2 with
`CAPTURE_FAILED`:

| Field | Value |
| --- | ---: |
| pages_available | 12 |
| pages_visited | 2 |
| tickets_seen | 10 |
| tickets_parsed | 5 |
| duplicate_tickets_skipped | 5 |
| legs_seen | 56 |
| legs_parsed | 28 |

Page 2 returned the exact same five ticket IDs as page 1 (byte-identical
`page_fingerprint`). The pagination walker correctly detected the repeat
(`PAGINATION_STOPPED_PAGE_CONTENT_REPEATED`) and stopped, but the
row-accounting invariant then incorrectly flagged the envelope as failed:
the equation omitted `duplicate_tickets_skipped` entirely (`10 seen = 5
parsed + 0 unresolved + 0 excluded` left 5 tickets unaccounted for, even
though they were legitimately, deliberately skipped as duplicates).
`legs_seen` (56) was also double what it should have been (28) --
page 2's five duplicate tickets' legs were added to `legs_seen` a second
time even though they contributed nothing to `legs_parsed`.

### Root cause

The `--current` pagination marker advanced to page 2 before page 2's own
ticket content had actually re-rendered -- the DOM still showed page 1's
five tickets verbatim when this module started parsing "page 2". This is
a rendering-timing race, the pagination analog of Round 2's ticket-
expansion timing defect.

### Fixes

1. **Content-fingerprint wait.** Before clicking the next page, this
   module now records the current page's own collapsed-ticket text
   (`getCollapsedPageFingerprint` -- no new selector, reuses the already-
   confirmed ticket-container selector). After the `--current` marker
   advances, it waits for that fingerprint to actually change too, with
   one longer bounded retry before giving up and stopping safely
   (`PAGE_CONTENT_DID_NOT_UPDATE`) -- never parsing a page whose content
   hasn't genuinely arrived yet.
2. **Row-accounting invariant corrected** to include
   `duplicate_tickets_skipped` as its own valid, accounted-for outcome:
   `tickets_seen = tickets_parsed + tickets_unresolved +
   tickets_expected_excluded + duplicate_tickets_skipped`.
3. **`legs_seen` no longer double-counts a duplicate ticket's legs** --
   they are counted once, when the ticket is first seen, and tallied
   separately in the new `duplicate_ticket_legs_skipped` coverage field
   for diagnostic visibility.

`PARSER_VERSION` bumped to
`bet9ja-settled-bets-parser@0.4.0-round3-pagination-timing-fix`.

### What is still NOT confirmed

- **The pagination timing fix has not itself been re-run against the live
  account.** Evidence-driven (the exact real 12-page failure reproduces
  the fix's target scenario) but only unit-tested against synthetic
  markup so far.
- Everything named as unconfirmed in Round 2 (system-settlement
  breakdown, date-range display control, outcomes beyond Won/Lost/
  Cancelled, `settled_at_raw`/`settled_at_utc`) remains unconfirmed.
- The Round 2 expansion-retry fix has similarly not yet been re-run
  against the live account (this Round 3 capture stopped at page 2 before
  reaching later pages that previously showed expansion timeouts, so
  `Cancelled -> VOID` and the expansion retry were not re-exercised here).

### Recommendation for Round 4

Repeat one real capture covering the full selected 12-page range (or
whatever the then-current range contains). Closure criteria before the
settled-bets ledger importer begins: all pages in the selected range
visited, no `ROW_ACCOUNTING_INVARIANT_VIOLATED`, `legs_seen ==
legs_parsed` whenever `duplicate_tickets_skipped` and
`tickets_unresolved` are both 0, and the earlier Round 2 closure criteria
(expansion timeouts eliminated or materially reduced, `Cancelled -> VOID`
still correct) reconfirmed on this larger run.
