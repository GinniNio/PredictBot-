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

## Round 1 — 2026-09-11

**Tester-supplied facts** (from two downloaded capture JSON files, both
against the real, authenticated My Bets page):

| Field | Value | Source |
|---|---|---|
| Source URL | `https://sports.bet9ja.com/myBets/` | Both capture JSON files' `source_url` |
| Capture 1 timestamp | 2026-09-11T15:03:30.108Z | `8579e84d-bet9jaopenbets20260911T150330Z.json` |
| Capture 2 timestamp | 2026-09-11T15:03:45.270Z | `e03002df-bet9jaopenbets20260911T150345Z.json` |
| Time between captures | 15.16 seconds | Difference of the two `captured_at_utc` values |

**Result: FAILED, correctly — exactly the predicted Round-0 outcome.**

Both captures returned:
```json
{
  "capture_status": "CAPTURE_FAILED",
  "capture_status_reasons": ["TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER", "NO_TICKETS_FOUND"],
  "coverage": { "tickets_seen": 0, "tickets_parsed": 0, "tickets_unresolved": 0, "tickets_expected_excluded": 0, "legs_seen": 0, "legs_parsed": 0 },
  "tickets": [], "unresolved_tickets": [], "excluded_tickets": []
}
```

Confirmed directly from the two JSON files:
- The extension loaded, ran on a single click, and produced exactly one
  download each time — no crash, no partial output.
- `capture_status: CAPTURE_FAILED` with named reasons
  (`TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER`, `NO_TICKETS_FOUND`) — no
  ticket was ever falsely reported as captured (`tickets: []`,
  `tickets_parsed: 0`).
- `tickets`, `unresolved_tickets`, and `excluded_tickets` are all empty —
  nothing invented, nothing partially admitted. This is the fail-closed
  contract working exactly as designed: `TICKET_SELECTORS.root`/`.ticket`
  matched nothing on the real My Bets page, so the parser correctly
  reported total failure rather than guessing at a boundary.
- `coverage.tickets_seen (0) = tickets_parsed (0) + tickets_unresolved (0)
  + tickets_expected_excluded (0)` — the row-accounting invariant holds
  even in the zero-ticket case.
- `source_url` sanitized correctly (`https://sports.bet9ja.com/myBets/`,
  no query string or fragment).
- No account balance, credentials, cookie, or unrelated page content
  appears anywhere in either file.

**What this round does NOT yet tell us:** whether the account actually had
an open pre-match ticket showing on that page at capture time. A
`NO_TICKETS_FOUND` failure is consistent with either (a) the real page
using different markup than `TICKET_SELECTORS` guesses, or (b) there being
no open ticket at all to find. Round 1 cannot distinguish these — that
distinction needs either confirmation that an open ticket was visible on
screen at capture time, or (preferably, per the fixture-capture precedent)
a live DOM inspection of the My Bets page's actual markup, the same way
the date-heading fix in `REAL_PAGE_VALIDATION.md` round 4 came from the
user's own live inspection rather than another guess.

### Recommendation for Round 2

Before guessing at a `TICKET_SELECTORS` fix: confirm at least one open
pre-match ticket was visibly rendered on `https://sports.bet9ja.com/myBets/`
at capture time, then supply either (a) a sanitized HTML sample of that
ticket's markup (redacting stake/odds values is fine — only the structure
matters), or (b) the output of a browser DevTools inspection identifying:
the ticket container element and how it's identified (class, `id`, or
`data-*` attribute), the ticket-id text location, each leg's home/away/
market/selection/odds elements, and whether legs carry the same
`_event-{id}`-style identity marker confirmed on the fixtures page. Do not
guess a fix from the failure alone — this is exactly the same "no more
guessing without evidence" line the fixture-capture parser held after its
own two wrong date-heading guesses.

## Round 2 — 2026-09-11

**Tester-supplied facts** (from live authenticated DevTools inspection of
`https://sports.bet9ja.com/myBets/`, 5 open tickets visible):

| Element | Selector | Notes |
|---|---|---|
| Ticket container | `.mybets .accordion-item` | 5 confirmed on the visible page |
| Ticket header/toggle | `.accordion-toggle` | click target; expanding adds `.accordion-item--open` |
| Placement time | `.mybets-date` | raw text only, no confirmed UTC attribute |
| Collapsed summary | `.mybets-holder` | contains `.mybets-holder__info-item` (stake/return, cell mapping unconfirmed) |
| Ticket id | `.mybets-head__item` | only present after expansion |
| System-bet table | `.mybets__systable` | exposes System Type / No. Bets / Unit Stake / Stake as one block; cell-level selectors unconfirmed |
| Leg | `.mybets-item` | one per leg; a system ticket's 6 legs render as 3 `.mybets-row` groups of 2, but legs are still found directly via `.mybets-item` |
| Leg selection | first `.mybets-item__row .mybets-bet` | |
| Leg odds | `.mybets-odd` (within the same first row) | |
| Leg market | second `.mybets-item__row` | whole-row text |
| Leg fixture + time | third `.mybets-item__row` | whole-row text; no confirmed separator between team names and time, so no home/away split is attempted |
| Leg competition | fourth `.mybets-item__row` | whole-row text |
| Cashout area | `.mybets__cashout-holder` | named exclusion zone — never queried, never clicked |
| Account info | rendered outside `.mybets` | confirms rooting capture at `.mybets` prevents account id/balance/nav leakage |

**Result: implemented as a new MYBETS profile in `ticket_parser.js`,
tried before the placeholder profile.** `captureFromDocument` is now
async: for each `.accordion-item`, it clicks `.accordion-toggle` if not
already open, waits for `.accordion-item--open` to appear, parses the
ticket and its legs, then clicks the toggle again to restore whatever
state the ticket was in before capture touched it. Ticket boundaries are
enforced exactly as confirmed — every ticket's expanded detail stays
inside its own `.accordion-item`, so the existing page-wide-scan defense
(scoped `querySelectorAll` per ticket) applies unchanged.

**What Round 2 does NOT yet confirm** (see `README.md`'s "The MYBETS
profile" section for the full list, each with its own
`capture_status_reasons` entry on every capture using this profile):
- No live/Virtual/Zoom status marker — this profile cannot yet enforce
  that scope boundary; every found ticket is inferred OPEN from page
  context alone.
- `.mybets-holder__info-item` / `.mybets__systable` cell-level label↔value
  mapping — stake/return fields stay typed `null`, raw text preserved for
  audit.
- Ticket-type detection beyond "has a system table" — single/double/
  treble/accumulator have no confirmed distinguishing markup yet.
- **Pagination.** The account showed 20 pagination items in addition to
  the 5 visible tickets, but whether all 20 are genuine page links (vs.
  prev/next/ellipsis/disabled controls) is unconfirmed — this parser will
  not click through unconfirmed pagination markup. This release captures
  only the currently visible page.

### Recommendation for Round 3

Two independent, separable pieces of evidence would unblock the two
remaining gaps that matter most:

1. **Pagination markup** — a DevTools inspection of the 20-item pagination
   control: its container selector, an individual page-link element's
   selector, how the CURRENT page is marked (a class, `aria-current`,
   etc.), and how prev/next/ellipsis/disabled controls are marked/
   distinguished from a real numbered page link. Without this, automated
   multi-page capture stays out of scope rather than risk clicking an
   unintended control.
2. **One real "Capture open bets" click against the now-confirmed MYBETS
   selectors**, uploading the resulting JSON. Expect `tickets_parsed: 5`
   (or however many tickets are open at capture time) if the confirmed
   selectors hold up outside the DevTools inspection session that produced
   them — record whatever the result actually is here as Round 3,
   including any surprise (e.g. a ticket type or leg shape not covered by
   this round's inspection).

## Round 3 — 2026-09-11

**Tester-supplied facts** (from a second live authenticated inspection of
`https://sports.bet9ja.com/myBets/`, focused specifically on pagination
after Round 2's ticket/leg evidence):

**Correction to Round 2:** the "20 pagination items" reported there was
the total number of pagination *elements*, not 20 pages. The real count
is **16 genuine numbered pages plus 4 navigation controls** (first/prev/
next/last).

| Element | Selector | Notes |
|---|---|---|
| Pagination container | `.mybets .pg-pagination` | |
| All controls (raw) | `.mybets .pg-pagination__item` | includes numbered pages AND first/prev/next/last |
| Numbered pages | `.mybets .pg-pagination__item` filtered by `/^\d+$/` text | distinguishes real page links from named controls |
| Current page | `.mybets .pg-pagination__item--current` | |
| First / Prev / Next / Last | `.first` / `.prev` / `.next` / `.last` classes | confirmed to exist; never clicked by this parser |
| Disabled state | `element.hasAttribute("disabled")` | confirmed present on `.last` when already on the highest page |

**Observed behavior, all confirmed by controlled interaction:**
- Clicking page `2` changed the current marker from `1` to `2`.
- The URL remained `https://sports.bet9ja.com/myBets/` throughout —
  pagination is client-side, not a page navigation.
- Each tested page displayed 5 `.accordion-item` ticket containers.
- Clicking `Last` selected page `16` (confirms 16 is the true highest
  page).
- On page 16, `.last` carried the `disabled` attribute (confirms the
  `disabled` signal is real and present at the actual boundary).
- The browser was restored to page 1 after inspection (manually, in this
  round — now done automatically by the implementation below).

**Result: automated pagination implemented in `ticket_parser.js`.**
`paginateAndCaptureAllPages` now walks every numbered page starting from
whichever page is on screen when capture begins: parse the current page →
read the current page number from `--current` → click the next NUMBERED
item (never `.next`) → wait for `--current` to actually advance → parse →
repeat. It stops on the highest page, a revisited page number, exactly
repeated page content, an unconfirmed transition (timeout), or a fixed
safety cap — never an unbounded loop. It deduplicates by `bet9ja_ticket_id`
across the whole run and restores the browser to page 1 afterward
(fire-and-forget, best effort). The ONLY click targets anywhere in the
file remain the confirmed accordion toggle and a verified numbered
pagination item — `.first`/`.prev`/`.next`/`.last` are named for
detection only and are never queried for a click, enforced by both a
behavioral test (a click-counter harness asserting zero clicks on those
four controls across a multi-page run) and the existing source-grepping
safety test.

**What Round 3 does NOT change:** the live/Virtual/Zoom detection gap,
the stake/return cell-mapping gap, and the ticket-type-detection-beyond-
system-tables gap from Round 2 are all unaffected by pagination — every
MYBETS-profile capture still reports `CAPTURE_PARTIAL`, never
`CAPTURE_OK`, until those close.

### Recommendation for Round 4

One real "Capture open bets" click against the now fully-selector-
confirmed MYBETS profile (ticket/leg markup from Round 2, pagination from
Round 3), ideally on the same 16-page account, uploading the resulting
JSON. Expect `tickets_parsed` to equal the true total ticket count across
all 16 pages (not just the 5 on the first page), `coverage.pages_visited:
16`, and `coverage.pages_available: 16`. Any surprise there — a ticket
shape not covered by this round's synthetic tests, a genuine pagination
edge case (e.g. a windowed page-number display that doesn't show all 16
numbers at once), or a real live/Virtual/Zoom ticket appearing on the
page — is exactly the next piece of evidence to record here, per the same
discipline used throughout this project.

## Round 4 — 2026-09-11

**Tester-supplied facts** (from two real "Capture open bets" clicks against
the same 16-page authenticated account, both producing the same outcome):

| Check | Result |
|---|---:|
| Capture attempts | 2 |
| Pages available | 16 |
| Pages reported visited | 16 |
| Tickets seen | 5 |
| Tickets parsed | 0 |
| Tickets unresolved | 5 |
| Legs parsed | 0 |
| Sensitive-data indicators | 0 |

Both captures: `CAPTURE_PARTIAL`, every one of the 5 unresolved tickets
failed with `MISSING_TICKET_ID` and the detail `.mybets-head__item`
absent after automated expansion.

**Result: two real defects found and fixed, both in `ticket_parser.js`.**

### Defect 1 — expansion read before content rendered

`.accordion-item--open` appeared (expansion "succeeded" by the old
check), but `.mybets-head__item` was not yet present when the ticket was
parsed immediately afterward. The real page's ticket detail (head item,
legs) evidently populates on a short delay AFTER the open class itself
toggles, not synchronously with it — the open class alone was never
sufficient evidence a ticket was ready to parse.

**Fix:** `ensureTicketExpanded` now waits for BOTH
`.accordion-item--open` AND a `.mybets-head__item` element to be present
inside the same ticket container before considering it ready. A ticket
that never satisfies both within the timeout window is now reported as
`TICKET_EXPANSION_TIMEOUT` (renamed from the old, single-condition
`TICKET_EXPAND_TIMEOUT`).

### Defect 2 — cross-page ticket totals not accumulating

16 pages of 5 ticket containers each should never finish with
`tickets_seen: 5` — only page 1 contributed. Root-caused to the same
class of problem as Defect 1: a pagination click's `--current` marker
can update before that page's own ticket list has finished
(re)rendering, so `processCurrentPageTickets` was called immediately
after `--current` was confirmed and found the new page still empty,
every time after the first.

**Fix:** a second wait, applied once per page right after `--current` is
confirmed (before that page is parsed), gives the ticket list a chance
to render. If this window elapses with no ticket containers present, the
page is parsed as-is (0 tickets) rather than treated as an error — this
parser still cannot fully distinguish "genuinely empty last page" from
"slower than this window", so `page_results[]` (new, see below) records
enough detail for a future round to tell the two apart from real
evidence.

### Also added, per the narrowed PR #30 scope

- **`page_results[]`** — a new top-level array, one entry per visited
  page (`page_number`, `ticket_containers_seen`, `tickets_parsed`,
  `tickets_unresolved`, `tickets_expected_excluded`, `legs_seen`,
  `legs_parsed`, `page_fingerprint`), so a shortfall in the final totals
  can be traced to a specific page instead of inferred indirectly.
- **Runtime row-accounting invariant check** — `coverage.tickets_seen ===
  tickets_parsed + tickets_unresolved + tickets_expected_excluded` is now
  asserted just before the envelope is returned. A violation (structurally
  unreachable given how every ticket container is processed, but guarded
  anyway) forces `CAPTURE_FAILED` with `ROW_ACCOUNTING_INVARIANT_VIOLATED`
  rather than ever producing a self-inconsistent downloaded file.
- **Regression tests**: a 16-page × 5-ticket synthetic run asserting
  `tickets_seen: 80` before deduplication (the exact shape of this
  round's real defect); a `page_results[]` shape/uniqueness test; and a
  direct regression test for Defect 2 (a page whose ticket list appends
  150ms after its `--current` marker moves, confirming it is still
  parsed correctly rather than read empty).
- Deduplication by `ticket_id` and the click-target restriction
  (`.accordion-toggle` + verified numbered pagination items only) are
  both preserved unchanged.
- Out of scope for this correction, per the user's explicit instruction:
  settlement, live/Virtual/Zoom detection, and ledger import.

Full suite after the fix: 121/121 JS tests green, 458/458 Python tests
green.

### Recommendation for Round 5

One more real "Capture open bets" click on the same 16-page account
against these two fixes. Expect `tickets_seen`/`tickets_parsed` to
finally reflect the true total across all 16 pages (not 5), and
`page_results[]` to show a nonzero `ticket_containers_seen` on every one
of the 16 entries. If any page still comes back with 0 containers, its
`page_results[]` entry pinpoints exactly which page and is the next
concrete piece of evidence — e.g. the `PAGE_CONTENT_TIMEOUT_MS` window
may need lengthening, or the real page may have a different loading
signal than "ticket containers eventually appear" that would need its
own DOM inspection.

## Round 5 — 2026-09-11

**Result: pagination confirmed fixed. Ticket normalization needed one
more correction round before the file can feed the betting ledger.**

| Check | Result |
|---|---:|
| Pages available | 16 |
| Pages visited | 16 |
| Ticket containers found | 80 |
| Per-page results | 16 entries |
| Containers per page | 5 |
| Reconciliation | 80 = 19 parsed + 61 unresolved |
| Sensitive-data indicators | 0 |

Pagination (Round 3/4 work) is confirmed working end to end: the
extension walked the entire 16-page account history in one click and
combined the results into one file, with `tickets_seen` now correctly
accumulating across every page (the exact Round 4 defect, now closed).

**What failed:** only 19 of 80 ticket containers were accepted; 61 failed
with `LEG_FAILED_TO_PARSE` → `LEG_UNEXPECTED_ROW_COUNT`:

| Failure shape | Occurrences |
|---|---:|
| Leg contained 3 rows instead of 4 | 49 |
| Apparent leg contained 0 rows | 12 |

Three field-quality problems were also found in the 19 accepted tickets:
`total_stake`/`unit_stake`/`potential_return` missing on every one;
`source_event_id` missing on all 126 parsed legs; and 107 of 126
normalized `selection` values were `null` despite `selection_raw`
containing readable selections (team names, handicap lines).

### Fixes (this round)

1. **3-row legs are accepted.** The competition row is genuinely absent
   on some real legs — now parsed as `competition_raw: null` /
   `competition_resolution: 'COMPETITION_UNAVAILABLE'`, never rejected.
2. **0-row `.mybets-item` elements are excluded at candidacy.** Confirmed
   structural elements sharing the leg class, not genuine legs — no
   longer fail-close the ticket they're found in.
3. **`selection` always mirrors trimmed `selection_raw`.** The H/D/A
   mapping attempt is dropped for this profile — real selections are
   team names and market-specific labels, not simple codes.
4. **Stake/return mapping — confirmed for system tickets.**
   `total_stake`/`potential_return` from `.mybets-holder__info-item`'s
   `"Stake:"`/`"Max Win:"` labels (comma-thousands-separator amounts
   parsed correctly, e.g. `"1,308.10"`). `unit_stake`/`ticket_type_raw`
   from `.mybets__systable`'s concatenated-values shape, ONLY when the
   System Type text is letters-only and the No.Bets/Unit Stake digit
   split is arithmetically unambiguous — verified against all 19 real
   tickets: **13 parse and validate cleanly** (e.g.
   `"Singles835.00280.00"` → 8 × 35.00 = 280.00); **6 are genuinely
   ambiguous from text alone** (a digit-prefixed type like `"4
   Folds283.0084.00"`, or a multi-row full-cover system like
   `"Doubles156.0090.00Trebles202.0040.00"`) and are correctly left
   unparsed rather than guessed — `total_stake`/`potential_return` still
   populate for all 19 regardless, since those come from the info items,
   never the table.
5. `source_event_id` stays `null` (never guessed); the header comment and
   README now state plainly that `fixture_id` is provisional,
   natural-key-only identity until stronger evidence is found.

**Verification:** an end-to-end replay of all 19 real Round 5 tickets
(reconstructing their exact real HTML from the uploaded JSON's own raw
fields) through the fixed parser produced 19/19 parsed, 0 stake
mismatches, and 0 return mismatches against the real uploaded values —
this fix is confirmed correct against real data, not just synthetic
tests.

### Out of scope for this round (per explicit instruction)

Settlement capture, live/Virtual/Zoom detection, and ledger import were
not started.

### Recommendation for Round 6

One more real "Capture open bets" click on the same account. Expect all
80 ticket containers to now parse (or, for any that don't, a new typed
reason rather than `LEG_UNEXPECTED_ROW_COUNT`/`MISSING_TICKET_ID`
recurring). In particular: does a non-system ticket (single/double/
treble/accumulator) appear? If so, its `stake_return_raw_items` and any
type-label markup are the next piece of evidence needed to close the two
remaining named gaps (stake/return mapping and ticket-type detection
beyond system-table presence) for that category.
