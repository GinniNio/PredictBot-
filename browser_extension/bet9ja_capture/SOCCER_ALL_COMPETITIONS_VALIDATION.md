# Real-page selector validation — capture all Soccer fixtures

Point-in-time record of the "Capture all Soccer fixtures" walker's
testing against the actual Bet9ja Soccer page, mirroring the process
`REAL_PAGE_VALIDATION.md` and `TICKET_REAL_PAGE_VALIDATION.md` used for
fixture capture and open-ticket capture. Updated per round; each round
gets its own dated section below rather than overwriting the last.

## Round 0 — 2026-09-11 (pre-validation baseline)

**Status: architecture and aggregation logic implemented and
unit-tested against synthetic markup; menu-scoping selector unconfirmed.**

### Confirmed by live inspection (this round)

- `https://sports.bet9ja.com/sport/soccer/1` renders only the selected
  tab's current fixture set — "Highlights" and "Upcoming" are separate
  client-side views, not both present in the DOM simultaneously.
- The inspected "Upcoming" view contained 18 matchups under one date
  heading.
- Competition choices (Premier League, LaLiga, Serie A, Bundesliga,
  Ligue 1, and others) are client-side menu controls using
  `href="javascript:;"` — not real navigation links.
- Their fixtures load only after selection, so no single DOM scrape can
  collect every competition's fixtures at once.
- Competition menu items carry the class `.menu-list__link`.

### What is implemented (logic, unit-tested against synthetic markup only)

- `soccer_walker.js`'s `captureAllSoccerCompetitions`: discovers
  candidate competition links (`.menu-list__link` with
  `href="javascript:;"` and non-empty visible text) inside a configured
  container, clicks each one in turn, waits for the confirmed
  `.sports-table__matchup` elements' identity to change, captures via
  `parser.js`'s own `captureFromDocument` (never re-implemented),
  deduplicates by `fixture_id` across competitions, and restores a
  best-effort default view afterward.
- Fail-closed per-competition: a click that doesn't change
  `.sports-table__matchup`'s identity within the wait window is reported
  `FAILED` (`CONTENT_DID_NOT_CHANGE`) in its own `competition_results[]`
  entry, never merged with the previous competition's fixtures.
- `source_country`/`source_competition` resolved from the page's own URL
  after each click, reusing `parser.js`'s already-confirmed
  `parseBet9jaCompetitionUrl` — no new, unconfirmed link-attribute
  selector needed for identity.
- The exact envelope shape requested: `scope`,
  `competitions_available`/`visited`/`failed`,
  `fixtures_seen`/`parsed`/`unresolved`, `duplicates_skipped`, and
  `competition_results[]` (`country`, `competition`, `capture_status`,
  per-competition fixture counts, `failure_reason`).

### What is NOT yet confirmed

- **The menu-scoping selector.** `.menu-list__link` is expected to also
  match other sports' competition pickers and unrelated site shortcuts
  sharing the same class — `SOCCER_MENU_SELECTORS.soccerMenuContainer`
  is `null` until a real selector scoping discovery to ONLY the Soccer
  competition menu is confirmed. Every real capture today therefore
  reports `competitions_available: 0` and `CAPTURE_FAILED` with reasons
  `SOCCER_MENU_SELECTORS_UNVERIFIED` / `NO_COMPETITIONS_DISCOVERED` — the
  same honest "correctly failed, never guessed" starting point every
  other button in this extension began at (see `REAL_PAGE_VALIDATION.md`
  Round 1 and `TICKET_REAL_PAGE_VALIDATION.md` Round 1).
- Whether a click on a competition link updates the page's own URL at
  all (needed for `source_country`/`source_competition` to resolve) —
  unconfirmed either way; if it does not, those fields will honestly stay
  `null` per-fixture rather than fall back to a guess.
- The exact number and identity of discoverable competitions in a real
  session (the live inspection this round confirmed *that* competition
  links exist and how they behave, not an exhaustive list of them).

### Recommendation for Round 1

Confirm the CSS selector (or DOM pattern) that scopes discovery to ONLY
the Soccer competition menu — e.g. a parent container only present when
Soccer is the active sport, or a `data-sport="soccer"`-style attribute on
an ancestor of the confirmed `.menu-list__link` items. A DevTools
inspection identifying that one container, plus confirming whether
`window.location` changes on a competition click, is enough to move this
from Round 0 (zero real competitions) to a real trial run — following
the same evidence-only correction discipline used throughout this
project. Do not guess the scoping selector ahead of that evidence.
