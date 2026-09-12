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

### Recommendation for Round 1 (superseded by Round 1's own findings below)

Confirm the CSS selector (or DOM pattern) that scopes discovery to ONLY
the Soccer competition menu — e.g. a parent container only present when
Soccer is the active sport, or a `data-sport="soccer"`-style attribute on
an ancestor of the confirmed `.menu-list__link` items. A DevTools
inspection identifying that one container, plus confirming whether
`window.location` changes on a competition click, is enough to move this
from Round 0 (zero real competitions) to a real trial run — following
the same evidence-only correction discipline used throughout this
project. Do not guess the scoping selector ahead of that evidence.

## Round 1 — 2026-09-11 (menu-scoping selector confirmed; two real
zero-competition captures explained; no real successful capture yet)

**Status: the Round 0 placeholder was exercised against two real
captures (from both `/sportPage/1/coupons` and `/sport/soccer/1`), both
correctly reporting zero competitions as designed. Live inspection then
supplied the real menu-scoping selector. Implemented and unit-tested
against synthetic markup built from this round's evidence; no real click
has yet been exercised end-to-end against the live account.**

### Confirmed by live inspection (this round)

- The real competition menu structure:
  ```html
  <ul class="menu-list mt30">
    <li class="menu-list__item">
      <a class="menu-list__link" href="javascript:;">England Premier League</a>
    </li>
  </ul>
  ```
  Discovery is now scoped to
  `.menu-list.mt30 .menu-list__item > .menu-list__link`, not bare
  `.menu-list__link` (which, as predicted in Round 0, matches other
  sports' pickers and unrelated site shortcuts and was the confirmed
  root cause of both Round 0 captures returning `competitions_available: 0`
  from `.menu-list__link` alone never having been scoped to a real
  container).
- Both `/sportPage/1/coupons` and `/sport/soccer/1` are valid Soccer
  routes this module must discover competitions from equally.

### What is implemented (logic, unit-tested against synthetic markup built
from the confirmed shapes above)

- `discoverSoccerCompetitionLinks` scoped to the confirmed
  `.menu-list.mt30` container.
- `selectCompetition` waits for the page's own `location.pathname` to
  start with `/competition/soccer/` AND the confirmed
  `.sports-table__matchup` rows to have rendered — a more precise signal
  than Round 0's "matchup fingerprint changed" heuristic, though that
  heuristic was never wrong, only less specific.
- Country/competition identity still reads from the resolved URL via
  `parser.js`'s confirmed `parseBet9jaCompetitionUrl` — unchanged from
  Round 0.
- Stale-node safety: competitions are identified by stable visible label
  text, never a cached element reference; `history.back()` (never a
  guessed click) returns to the inventory page after each competition,
  and the menu is re-discovered fresh before the next lookup. A label
  missing on re-discovery fails only that one competition
  (`LINK_NOT_FOUND_ON_REDISCOVERY`); a `history.back()` that never
  restores the menu is a safe stop (`COULD_NOT_RETURN_TO_INVENTORY`)
  that preserves everything already captured.
- Two menu labels resolving to the same destination URL are deduplicated
  at the destination level (`SKIPPED_DUPLICATE_DESTINATION`), on top of
  the existing per-fixture `fixture_id` deduplication.
- `PARSER_VERSION` is deliberately UNCHANGED this round
  (`bet9ja-soccer-walker@0.1.0-unverified-menu-selectors`) — see the
  module's own header comment. The menu-scoping selector is confirmed by
  DOM inspection, not yet by one real successful end-to-end capture.

### What is NOT yet confirmed

- **An actual successful click-through against the live account.** This
  round's evidence is a DOM structure inspection, not a real run of this
  updated module against the authenticated page. Round 2 should run a
  real capture and report `competitions_available`/`competitions_visited`/
  `competitions_failed` and any `competition_results[].failure_reason`,
  exactly the reconciliation pattern `TICKET_REAL_PAGE_VALIDATION.md`'s
  later rounds used.
- Whether a competition page keeps the inventory's `.menu-list.mt30`
  around at all, or removes it entirely (this module is written
  defensively for the latter, via `history.back()` + re-discovery, but
  which is actually true has not been observed).
- The exact number and identity of discoverable competitions in a real
  session.

### Recommendation for Round 2 (superseded — Round 2 was run for real and
failed; see below)

Run **Capture all Soccer fixtures** from both `/sport/soccer/1` and
`/sportPage/1/coupons` against the real, authenticated account and report
`competitions_available`/`competitions_visited`/`competitions_failed`,
any `competition_results[].failure_reason` values, and whether the final
page state left the menu visible and usable. If a real run completes
with `competitions_visited > 0` and no unexplained `FAILED` entries,
`PARSER_VERSION` can finally drop `unverified-menu-selectors`.

## Round 2 — 2026-09-12 (Round 1 selectors run for real and FAILED
completely; entire discovery/navigation strategy replaced)

**Status: `.menu-list.mt30` discovery was exercised against the real,
authenticated account per the Round 1 recommendation above. It failed on
every count. Live re-inspection then traced the correct hierarchy and
this module was rewritten from scratch against it. Implemented and
unit-tested against synthetic markup built from the new evidence; no real
click-through has yet succeeded end-to-end.**

### The real Round 1 capture's results

```text
competitions_available: 24
competitions_visited: 0
competitions_failed: 24
```

- The first discovered link: `CONTENT_DID_NOT_CHANGE`.
- The remaining 23: `LINK_NOT_FOUND_ON_REDISCOVERY`.
- Browser error: executing the `href="javascript:;"` URL was blocked by
  the page's own Content Security Policy — Chrome correctly refuses to
  run a `javascript:` URL that the intended application handler doesn't
  itself intercept, and Round 1's direct `linkEl.click()` (with no
  `preventDefault()` guard) triggered exactly that.
- The one click that appeared to make progress moved the page to
  `/liveCompetitions` — a LIVE surface, not the pre-match inventory Round
  1 needed.

### Root cause

`.menu-list.mt30` is the general popular-shortcuts sidebar — confirmed to
mix Soccer, tennis, NFL, NHL, and MLB shortcuts in the same list. It was
never the Soccer competition inventory, which explains all three real
failure modes at once: wrong links discovered (so re-discovery by label
frequently failed after the one navigation that did occur), no
application handler behind most of those links (so the CSP block), and
the one link that did have a handler routing to a live-odds surface, not
pre-match.

### Confirmed real hierarchy (this round)

```text
Sports (/) → pre-match Soccer accordion → Coupons
  → /popularCoupons/1 → country accordion → competition control
  → /competition/soccer/{country}/{competition}/{ids}
```

- `#coupons_sport-1_soccer` resolves to `/popularCoupons/1` (confirmed;
  resolving instead to `/liveCompetitions` is the same wrong-surface
  failure mode named above, now caught explicitly and immediately).
- Pre-match Soccer accordion: `#left_prematch_sport-1_soccer_label-toggle`;
  its owning `.accordion-item` scopes all further discovery.
- "Show N A-Z more" countries: `[id$="_buttonmore-toggle"]`, scoped
  inside the Soccer accordion.
- Country toggles: `[id^="left_prematch_sport-1_soccer_sg-"][id$="_label-toggle"]`,
  e.g. `#left_prematch_sport-1_soccer_sg-11058_england_label-toggle`.
- Competition controls: `[id^="left_prematch_sport-1_soccer_sg-"][id*="_g-"]`,
  e.g. `#left_prematch_sport-1_soccer_sg-11058_england_g-170880_premier_league`,
  confirmed to resolve on click to
  `https://sports.bet9ja.com/competition/soccer/england/premierleague/1-11058-170880`
  with 20 real `.sports-table__matchup` rows.

### What is implemented (logic, unit-tested against synthetic markup built
from the confirmed shapes above)

- Discovery scoped entirely to the Soccer accordion's own
  `.accordion-item` — `.menu-list.mt30` is not referenced anywhere in the
  rewritten module.
- `ensureOnCouponsSurface`: accepts `/popularCoupons/1` directly, or
  clicks the confirmed entry control and waits for that route; landing on
  `/liveCompetitions` is an immediate, named failure
  (`WRONG_SURFACE_LIVE_COMPETITIONS`), never accepted as success.
- `clickSafely()`: the ONLY way this module clicks a `javascript:;`
  control — a one-time, capturing `preventDefault()` listener stops the
  anchor's own default action (the exact action CSP blocked in Round 1)
  while leaving Bet9ja's real handler free to run.
- Countries and competitions are rediscovered by their own stable DOM ID
  before every use, never a cached reference or list position.
- `selectCompetition` waits for THREE conditions together: pathname
  starts with `/competition/soccer/`, that pathname differs from the
  previous competition's own resolved path, and the confirmed
  `.sports-table` root is present — never a content-text diff alone.
- Fail-closed, non-aborting typed failures at every level: a country
  failure (`COUNTRY_CONTROL_NOT_FOUND_ON_REDISCOVERY`,
  `COUNTRY_EXPANSION_TIMEOUT`, `COUNTRY_COMPETITION_LIST_EMPTY`) never
  stops remaining countries; a competition failure
  (`COMPETITION_CONTROL_NOT_FOUND_ON_REDISCOVERY`,
  `COMPETITION_ROUTE_TIMEOUT`, `COMPETITION_ROUTE_NOT_SOCCER`,
  `COMPETITION_CONTENT_TIMEOUT`) never stops remaining competitions in
  that country. A competition confirmed to have zero fixtures
  (`competitions_empty`) is a valid, audited outcome, never a failure.
- `capture_status` is computed from real reconciliation, not permanently
  capped: `CAPTURE_COMPLETE` requires the Coupons route confirmed, the
  Soccer accordion found, the full country inventory expanded, every
  discovered country/competition visited or confirmed empty, zero
  failures, and the country-accounting invariant holding.
  `PARSER_VERSION` itself is still NOT bumped past `-unverified`, since
  none of this has been exercised against the real, live account yet.

### What is NOT yet confirmed

- **An actual successful click-through against the live account.** This
  round's evidence is a live DOM/route inspection, not a real run of this
  rewritten module start to finish. Round 3 should run the real capture
  and report `countries_available`/`countries_visited`/`countries_failed`,
  `competitions_available`/`competitions_visited`/`competitions_empty`/
  `competitions_failed`, and any `competition_results[].failure_reason`,
  exactly the reconciliation pattern `TICKET_REAL_PAGE_VALIDATION.md`'s
  later rounds used.
- Whether the "show N A-Z more" control adds NEW country DOM nodes or
  reveals already-present hidden ones — this module rediscovers fresh
  after a short settle window regardless, but which mechanism is real has
  not been observed.
- Whether returning to `/popularCoupons/1` via `history.back()` after a
  competition reliably restores the Soccer accordion (and the relevant
  country) already open, or whether this module needs to re-open them
  explicitly every time — currently it always re-opens the Soccer
  accordion fresh for the next country, but does not yet re-verify a
  specific country's own open state is preserved across the return.
- The exact number and identity of discoverable countries/competitions in
  a real session.

### Recommendation for Round 3 (partially superseded — a code review
caught a further gap before any real run happened; see below)

Run **Capture all Soccer fixtures** from `https://sports.bet9ja.com/popularCoupons/1`
against the real, authenticated account and report
`coupons_route_confirmed`, `countries_available`/`countries_visited`/
`countries_failed`, `competitions_available`/`competitions_visited`/
`competitions_empty`/`competitions_failed`, and any
`competition_results[].failure_reason` values. If a real run completes
with `competitions_visited > 0`, no unexplained failures, and (ideally)
`capture_status: 'CAPTURE_COMPLETE'`, `PARSER_VERSION` can finally drop
its `-unverified` tag.

## Round 3 — 2026-09-12 (post-review correction, before any real run)

**Status: a code review of the merged Round 2 module found a real
correctness gap and a contradiction with its own PR description, both
before a real capture was ever attempted. Fixed; still unit-tested
against synthetic markup only.**

### What the review found

- **The PR description said `history.back()` was removed. It wasn't.**
  An unverified, fire-and-forget `history.back()` call remained at the
  very end of the whole walk.
- **The real bug: no return between competitions within a country.**
  Round 2's loop selected every competition in a country back-to-back
  with NO return to Coupons in between. Since a competition page is not
  confirmed to keep the inventory's accordion DOM around, this would
  silently fail to rediscover every competition after the first in any
  country with 2+ competitions (`COMPETITION_CONTROL_NOT_FOUND_ON_REDISCOVERY`
  on the second competition onward) -- the synthetic tests never caught
  this because the test harness never actually removed the accordion
  from the DOM on a competition click.
- **Starting from an arbitrary page (e.g. `/liveCompetitions`) simply
  failed** rather than reaching `/popularCoupons/1` -- correct as a
  fail-closed behavior, but not the intended one-click experience.
- **"Show more" expansion was never actually verified** -- clicked, then
  a fixed sleep, regardless of whether anything changed.
- Missing accounting: only the country-level equation was checked; no
  competition-level equation, no per-competition duplicate-fixture
  accounting, and safety-cap omissions were silently dropped rather than
  named.
- No cancellation support, no `resume_metadata`.

### Fixes

- `returnToCouponsAndReopen` now runs (and its outcome is fully VERIFIED
  -- the resolved pathname, the reopened Soccer accordion, and the
  reopened country's own competition list are all re-checked) after
  EVERY competition attempt, success or failure. The old end-of-run
  `history.back()` cleanup is removed entirely.
- Reaching `/popularCoupons/1` from an arbitrary starting page is now
  popup.js's job: `ensureOnPopularCouponsRoute` performs a real
  `chrome.tabs.update` navigation of the active tab BEFORE
  `soccer_walker.js` is even injected, and rejects a redirect to
  `/liveCompetitions` immediately. `soccer_walker.js` itself no longer
  clicks a Coupons-entry control at all -- it only ever checks the
  current route.
- "Show more" now waits a bounded window for the country count to
  actually grow and records `country_inventory_growth_observed`
  (`true`/`false`) honestly either way, since neither expansion mechanism
  (new DOM nodes vs. revealing hidden ones) is confirmed.
- Full reconciliation: `countries_available`/`competitions_available`
  each account for visited + failed + skipped-by-safety-cap +
  skipped-by-early-stop; each `competition_results[]` entry now carries
  `duplicate_fixtures_skipped` so a cross-competition duplicate is never
  mistaken for unexplained row loss on that specific competition.
- `context.shouldCancel()` (same pattern as `settled_bets_parser.js`) is
  checked between competitions and countries; a cancellation (or any
  other early stop) produces `resume_metadata`.

### What is still NOT confirmed

- **Everything from Round 2 remains unconfirmed** -- this correction has
  not itself been run against the live account; only unit-tested against
  synthetic markup (now specifically modeling a competition page that
  removes the accordion from the DOM, the exact case Round 2's bug
  needed).
- Whether `history.back()` (still the only available primitive for
  "undo this module's own `pushState` navigation") reliably restores the
  Soccer accordion's DOM at all, or whether Bet9ja tears it down and
  rebuilds it differently -- `returnToCouponsAndReopen`'s own
  verification is designed to catch either case as a safe stop, but which
  is actually true has not been observed.
- Whether `chrome.tabs.update` to `/popularCoupons/1` from
  `/liveCompetitions` (or elsewhere) behaves as a same-document SPA
  navigation or a full page reload in the real browser -- popup.js polls
  `chrome.tabs.get` for `status: 'complete'` either way, but has not been
  exercised against the real site.

### Recommendation for Round 4

Run **Capture all Soccer fixtures** starting from a page OTHER than
`/popularCoupons/1` (e.g. the Sports homepage or `/liveCompetitions`)
against the real, authenticated account, confirming popup.js's own
navigation reaches the Coupons route first. Report
`coupons_route_confirmed`, `countries_available`/`countries_visited`/
`countries_failed`, `competitions_available`/`competitions_visited`/
`competitions_empty`/`competitions_failed`, `country_inventory_expanded`/
`country_inventory_growth_observed`, and any
`competition_results[].failure_reason` values -- especially checking
whether a country with 2+ competitions gets ALL of them, not just the
first (the exact Round 2 bug this round fixed). If a real run completes
with `competitions_visited > 0`, no unexplained failures, and (ideally)
`capture_status: 'CAPTURE_COMPLETE'`, `PARSER_VERSION` can finally drop
its `-unverified` tag.
