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

### Recommendation for Round 2

Run this module against the real, authenticated 191-page account (a
representative subset first, e.g. the first ~5 pages, is a reasonable
initial check before a full run given the page count) and report the same
kind of reconciliation table TICKET_REAL_PAGE_VALIDATION.md's Round 5/6
used: pages visited vs. available, tickets seen/parsed/unresolved,
duplicate/conflicting IDs, and any leg or ticket that resolved
`UNRESOLVED` for a reason other than an outcome word this round didn't
yet cover -- so any real gap traces to a specific page/ticket rather than
being inferred from the aggregate counts alone.
