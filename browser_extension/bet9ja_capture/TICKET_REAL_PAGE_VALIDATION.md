# Real-page selector validation — open bet ticket capture

Point-in-time record of the ticket-capture extension's testing against an
actual Bet9ja "Open Bets"/"My Bets" page, mirroring the process
`REAL_PAGE_VALIDATION.md` used for fixture capture. Updated per round; each
round gets its own dated section below rather than overwriting the last.

## Round 0 — 2026-09-11 (pre-validation baseline)

**Status: NOT YET STARTED. No real page sample has been captured yet.**

This round exists only to record the starting state honestly, exactly as
`REAL_PAGE_VALIDATION.md`'s own Round 1 did for fixture capture before any
real evidence existed for it either.

### What is implemented (logic, unit-tested against synthetic markup only)

- `ticket_parser.js`'s `TICKET_SELECTORS` — see its own header comment:
  **every selector value is an unverified placeholder**, not derived from
  a real DOM sample. It is a structurally plausible first guess modeled on
  common bet-history markup and on the identity-via-`id` convention
  confirmed for fixture rows (`[id*="_event-"]`) — itself only confirmed
  on the *fixtures* page, so its applicability to a ticket/leg view is
  independently unconfirmed.
- Fail-closed ticket-boundary logic: each ticket is its own scoped DOM
  subtree; any leg that fails to parse voids the whole ticket rather than
  admitting a partial one (`tests/ticket_parser.test.js`, "fail closed"
  tests).
- Live/Virtual/Zoom leg exclusion, settled/cashed-out ticket exclusion,
  ticket-type taxonomy-gap flagging, and placement-time honesty (all
  covered by synthetic unit tests — see `README.md`'s "Open bet ticket
  capture" section for the full list).
- `source_event_id` + `fixture_id` (same `bxf_...` scheme as fixture
  capture) exposed per leg, per the explicit requirement that the ticket-
  capture output be cross-referenceable against the fixture-capture
  extension's own fixture identity.

### What is confirmed by real evidence

**Nothing yet.** Expect a first real capture attempt to report:
```json
{
  "capture_status": "CAPTURE_FAILED",
  "capture_status_reasons": ["TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER", "NO_TICKETS_FOUND"],
  "coverage": { "tickets_seen": 0, "tickets_parsed": 0, "tickets_unresolved": 0, "tickets_expected_excluded": 0, "legs_seen": 0, "legs_parsed": 0 },
  "tickets": [],
  "unresolved_tickets": [],
  "excluded_tickets": []
}
```
— the same honest "correctly failed, never guessed" outcome fixture
capture's own Round 1 produced against the real page before its selectors
were corrected. This is the expected, not the concerning, result of a
first attempt.

### What real evidence is needed next (per the user's own test plan)

1. **One single ticket, open, pre-match.** Confirm: ticket container
   selector, ticket id location, placed-at timestamp format (if shown at
   all — Bet9ja may show only a relative or unqualified time, exactly as
   the fixtures page shows kickoff without a year/timezone), stake/return
   field locations, and the leg's identity markup (does the ticket/leg
   view reuse the same `_event-{id}` id convention as the fixtures page,
   or something else entirely?).
2. **One accumulator/multi-leg ticket, open, pre-match.** Confirm how
   Bet9ja's own UI labels this ticket type (the taxonomy-gap candidate —
   `ticket_type_raw` should capture it verbatim) and that each leg is
   correctly scoped to its own ticket container.
3. **One system ticket, open, pre-match.** Confirm system-specific
   markup, if any (e.g. how "2/4" or similar system notation is rendered)
   — `ticket_type_normalized: 'SYSTEM'` alone does not currently capture
   system size/combination notation; this may need a follow-up field once
   real markup is seen.
4. **Expanded vs. collapsed ticket views.** Confirm whether a collapsed
   ticket's legs are present in the DOM at all (same
   `collapsed_sections_detected` concern fixture capture had for
   competition groups) or only rendered on expand — if the latter, a
   collapsed ticket must be flagged as a coverage gap, never silently
   under-counted as "no legs found."
5. **A repeated fixture appearing in two separate open tickets
   simultaneously.** Confirm that each ticket's own leg scan stays
   correctly scoped to its own container (this is a structural guarantee
   in the current code — see `README.md`'s "Fail-closed ticket
   boundaries" — but must still be confirmed against a real page, not just
   synthetic markup, exactly as fixture capture's structural-row exclusion
   was).
6. **Live/Virtual/Zoom ticket exclusion.** No real sample of any of these
   states has been seen for tickets any more than it has for fixtures —
   confirm the real leg-status markup (if any) so
   `TICKET_SELECTORS.legStatusHint` can be corrected rather than assumed.

Per the user's explicit risk framing: *"The main risk is assigning a leg
to the wrong ticket. If ticket boundaries are ambiguous, record the ticket
as unresolved."* Every one of the six scenarios above is a direct test of
that exact risk, and the current code already resolves ambiguity toward
`unresolved_tickets`/`excluded_tickets` rather than a guess — real
evidence is needed to confirm the selectors correctly PARSE a well-formed
ticket in the first place, not just that they fail safely when they
don't.

### Recommendation

Run one real "Capture open bets" click against an account with at least
one open pre-match single, upload the resulting JSON (or the
`tickets_seen: 0` failure, which is equally useful evidence), and record
the result here as Round 1 — following exactly the round-by-round,
evidence-only correction discipline `REAL_PAGE_VALIDATION.md` used for
fixture capture. Do not guess at a selector fix ahead of that evidence.
