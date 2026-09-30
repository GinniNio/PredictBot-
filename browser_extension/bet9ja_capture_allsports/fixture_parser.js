/**
 * Bet9ja all-sports fixture-row parser -- stage 2.
 *
 * Reuses, unchanged, the row-anchor and participant selectors already
 * confirmed for Soccer in browser_extension/bet9ja_capture/parser.js
 * (`.sports-table__home`/`.sports-table__away`/`.sports-table__time`,
 * and the `prematch_event-<digits>` EXACT row-id pattern that excludes
 * descendant odds/dropdown controls repeating the same id with a
 * suffix) -- these are the site's own shared sports-table component, not
 * re-derived per sport.
 *
 * Evidence backing this generalization, as of 2026-09-30:
 *  - Byte-verified against real raw page HTML (`outerHTML`, via the
 *    stage-1 snapshot tool): Tennis, Basketball/WNBA, Ice Hockey/NHL,
 *    American Football/NFL. The exact row-id pattern and
 *    `.sports-table__home/__away` classes were read directly from that
 *    HTML.
 *  - "DOM extraction contract" tier (a structured summary of a live
 *    inspection, not raw HTML bytes): Volleyball, Handball. Each
 *    contract independently reports the identical
 *    `fixture_row_selector: "[id^=prematch_event-] filtered by
 *    /^prematch_event-\d+$/"` and `participant_model: "home_team,
 *    away_team"` -- consistent with the byte-verified tier, but not
 *    itself byte-verified against raw markup for these two sports. This
 *    module does not distinguish evidence tiers at runtime (the
 *    selectors are identical either way); callers that care about the
 *    distinction should track it against `sport_slug_from_url`
 *    themselves.
 *
 * A breadcrumb-only page (no fixture rows) is a valid, honest result
 * (`fixture_row_count: 0`) -- Bet9ja's own confirmed empty-state text
 * ("There are no markets available.") is not itself asserted here, since
 * detecting it reliably needs its own selector evidence this module does
 * not yet have; callers that need to distinguish "genuinely empty" from
 * "parser found nothing it recognized" should check for that text
 * themselves until then.
 *
 * UPDATE (2026-09-30T17:03Z): a "stage2-resolution-evidence" capture
 * resolved four of the gaps above, each with its own stated confidence
 * (this is a hand-built structured-evidence JSON, same tier as the
 * earlier "DOM extraction contract" captures -- not a raw outerHTML
 * snapshot):
 *
 *  - **Date attribution** (`CONFIRMED_ON_POPULATED_WNBA`): each
 *    `.sports-table` reads its date from its own immediately preceding
 *    sibling `.sports-head.table`'s `.sports-head__date > span` text
 *    (e.g. "Thu 1 Oct") -- both are children of the same date-group
 *    wrapper. Rows inside a `.sports-table` with no such preceding
 *    sibling get `date_text_raw: null`, never a guessed date. (An
 *    earlier claimed `.table-f > :first-child` fallback for kickoff time
 *    was retired once real bytes showed `.sports-table__time` is simply
 *    the row's own `.table-f`'s first child -- see the CORRECTION note
 *    below; there is no separate fallback path.) Resolving
 *    `date_text_raw`/`kickoff_time_raw` to actual UTC is explicitly NOT
 *    done here -- this capture's own parser_rule says so: "Do not
 *    resolve UTC until the capture timezone policy is explicitly
 *    implemented."
 *  - **Empty-state marker** (`CONFIRMED_FROM_RAW_SNAPSHOT`, WNBA only):
 *    `.gen__holder .search-results .gen__txt` with the exact text "There
 *    are no markets available.". Per this capture's own rule, only
 *    `fixture_row_count === 0` AND that exact marker present counts as
 *    `CONFIRMED_EMPTY`; zero rows without the marker is `UNKNOWN_EMPTY`,
 *    never asserted empty on row-count alone. Not yet confirmed for any
 *    sport besides Basketball/WNBA.
 *  - **WNBA "3way" market** (`CONFIRMED_ON_POPULATED_WNBA`): a
 *    `1`/`X`/`2` market whose odds ids append `_event-<id>_odds_market-
 *    3way_sign-<code>` to the row's own id, with signs literally coded
 *    `1B`/`XB`/`2B` (not `1`/`X`/`2` directly) -- verified against 3 real
 *    WNBA rows' exact odds. This sign-code mapping is confirmed ONLY for
 *    the "3way" market family; it is not assumed to generalize to any
 *    other market.
 *  - **Full sidebar sport catalogue**: moved to `catalogue_parser.js`
 *    (`CONFIRMED_SPORTS`), not duplicated here.
 *
 * Still explicitly open (per that capture's own `remaining_unresolved`):
 * cross-sport confirmation of the empty-state marker, a timezone-to-UTC
 * policy, and raw fixture-table captures for the newer sidebar sports.
 *
 * CORRECTION (2026-09-30T16:23-16:26Z, two real raw `outerHTML`
 * snapshots -- Basketball/WNBA and Ice Hockey/Russia/KHL): a ROW is NOT
 * the `prematch_event-<id>` element by itself. That id lives on the
 * `.sports-table__td.sports-table__matchup` cell only; the time cell
 * (`.sports-table__time`) and every odds cell (including the "3way"
 * items) are its SIBLINGS, not its descendants -- all children of one
 * shared `.table-f` row wrapper:
 *
 *   <div class="table-f">
 *     <div class="sports-table__td sports-table__time"><span>18:00</span></div>
 *     <div class="sports-table__td sports-table__matchup" id="prematch_event-123">
 *       <div class="sports-table__home">A</div>
 *       <div class="sports-table__away">B</div>
 *     </div>
 *     <div class="sports-table__td sports-table__odds">...odds items...</div>
 *   </div>
 *
 * Querying time/odds as descendants of the id'd element (this module's
 * own earlier code) silently returned null/empty in production despite
 * passing tests -- the tests' own synthetic markup wrongly nested time
 * inside the row div. Fixed via `resolveRowContainer` (`fixtureEl.closest(
 * '.table-f')`, no fallback -- see its own comment) shared by
 * `resolveKickoffTime` and `parseThreeWayOdds`; `.sports-table__home`/
 * `__away` needed no fix -- those genuinely are children of the matchup
 * cell. Independently confirmed on a second, different sport/competition
 * (Ice Hockey/KHL, 4 real rows in the byte-verified
 * `2026-09-30T16:26:27.020Z` raw snapshot, source_url
 * `.../competition/icehockey/russia/khl/4-44083-4714776`) -- not a
 * WNBA-specific quirk. `tests/fixture_parser.test.js` now loads REAL
 * fragments cut (via jsdom's own `outerHTML`, not hand-typed) from three
 * raw snapshots -- WNBA populated (16:23:42Z), WNBA confirmed-empty
 * (15:33:17Z), and Ice Hockey/KHL populated (16:26:27Z) -- in
 * `tests/fixtures/`, replacing the synthetic markup that originally
 * masked this bug.
 *
 * Also corrected: the WNBA-3way capture's claimed
 * `#marketsmenu_market_dropdown` toggle is NOT present in either real
 * snapshot. The real markup shows "3way" as a plain market-category tab
 * (`.sports-view__bar-markets`) alongside "2 Way" and "Handicap", not a
 * dropdown menu -- that part of the earlier hand-distilled evidence does
 * not hold up against raw bytes and is not implemented here.
 *
 * Also observed (informational, not yet acted on): the breadcrumb's real
 * textContent has NO surrounding whitespace around its ">" separators
 * (e.g. "Ice Hockey>Russia>KHL"), unlike the spaced form assumed
 * elsewhere in this project's evidence -- any future breadcrumb parsing
 * here should match `>` with `\s*` on both sides, not assume spaces.
 */
(function (root) {
  const SCHEMA_VERSION = 'bet9ja-allsports-fixture-capture.v1';

  // See header comment: identical across every sport evidenced so far.
  const FIXTURE_ROW_ID_PATTERN = /^prematch_event-(\d+)$/;

  const SELECTORS = {
    home: '.sports-table__home',
    away: '.sports-table__away',
    time: '.sports-table__time',
    // The row's own `.table-f` wrapper -- see ROW STRUCTURE note above.
    rowContainer: '.table-f',
    breadcrumb: '.sports-view__crumbs',
    dateGroupHeader: '.sports-head.table',
    dateText: '.sports-head__date > span',
    emptyStateMarker: '.gen__holder .search-results .gen__txt',
  };

  const EMPTY_STATE_TEXT_EXACT = 'There are no markets available.';

  // Confirmed only for the WNBA "3way" market (see header comment) --
  // sign codes are literally suffixed "B", not the bare 1/X/2 they map to.
  const THREE_WAY_SIGN_MAP = { '1B': '1', XB: 'X', '2B': '2' };

  // Bet9ja's own competition URL shape:
  // /competition/{sport-slug}/{country}/{competition}/{ids}
  const URL_SPORT_SLUG_PATTERN = /\/competition\/([a-z]+)\//i;

  function text(el) {
    if (!el) return '';
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function resolveSportSlugFromUrl(href) {
    const match = (href || '').match(URL_SPORT_SLUG_PATTERN);
    return match ? match[1].toLowerCase() : null;
  }

  function isExactFixtureRow(el) {
    return !!el.id && FIXTURE_ROW_ID_PATTERN.test(el.id);
  }

  /**
   * The fixture row's own `.table-f` wrapper -- see ROW STRUCTURE header
   * note. `fixtureEl` (the `prematch_event-<id>` element) is the MATCHUP
   * cell only, a SIBLING of the time/odds cells, not their parent; time
   * and odds must be looked up in this shared container, never as
   * descendants of `fixtureEl` itself. No fallback if `.table-f` isn't
   * found -- byte-verified real evidence (WNBA and Ice Hockey/KHL) shows
   * this wrapper on every real row seen so far; a sport that genuinely
   * lacks it needs its own evidenced handling, not a guessed substitute.
   */
  function resolveRowContainer(fixtureEl) {
    return fixtureEl.closest(SELECTORS.rowContainer);
  }

  function resolveKickoffTime(fixtureEl) {
    const row = resolveRowContainer(fixtureEl);
    return text(row && row.querySelector(SELECTORS.time)) || null;
  }

  /**
   * Resolves the date-group text for a `.sports-table` from its own
   * immediately preceding sibling `.sports-head.table` -- both children
   * of the same date-group wrapper (confirmed real relationship, see
   * header comment). Returns null (never guessed) if that sibling isn't
   * present or doesn't carry the confirmed classes.
   */
  function resolveDateGroupText(tableEl) {
    const prev = tableEl.previousElementSibling;
    if (!prev || !prev.classList.contains('sports-head') || !prev.classList.contains('table')) {
      return null;
    }
    return text(prev.querySelector(SELECTORS.dateText)) || null;
  }

  /**
   * Extracts the confirmed WNBA "3way" market for one row, keyed by its
   * own event id. Odds items are looked up in the row's `.table-f`
   * wrapper (see ROW STRUCTURE header note), NOT as descendants of the
   * matchup element itself; they are siblings of it. Returns null if no
   * matching odds items are found -- this market is not present on every
   * fixture, and absence is not itself an error.
   */
  function parseThreeWayOdds(fixtureEl, fixtureId) {
    const row = resolveRowContainer(fixtureEl);
    if (!row) return null;

    const prefix = `prematch_event-${fixtureId}_event-${fixtureId}_odds_market-3way_sign-`;
    const odds = {};
    for (const el of row.querySelectorAll(`[id^="${prefix}"]`)) {
      const signCode = el.id.slice(prefix.length);
      const outcome = THREE_WAY_SIGN_MAP[signCode];
      if (outcome) odds[outcome] = text(el);
    }
    return Object.keys(odds).length > 0 ? odds : null;
  }

  function parseFixtureRow(rowEl, dateTextRaw) {
    const fixtureId = rowEl.id.match(FIXTURE_ROW_ID_PATTERN)[1];
    const participant1 = text(rowEl.querySelector(SELECTORS.home));
    const participant2 = text(rowEl.querySelector(SELECTORS.away));
    return {
      fixture_id: fixtureId,
      participant_1: participant1 || null,
      participant_2: participant2 || null,
      kickoff_time_raw: resolveKickoffTime(rowEl),
      date_text_raw: dateTextRaw,
      three_way_odds: parseThreeWayOdds(rowEl, fixtureId),
      missing_participants: !participant1 || !participant2,
    };
  }

  /**
   * @param {Document} documentLike
   * @param {{href: string, capturedAtUtc: string}} context
   */
  function parseFixturesFromDocument(documentLike, context) {
    if (!documentLike || typeof documentLike.querySelectorAll !== 'function') {
      throw new Error('parseFixturesFromDocument requires a Document-like object.');
    }
    const href = (context && context.href) || '';
    const capturedAtUtc = (context && context.capturedAtUtc) || null;

    const breadcrumbRaw = text(documentLike.querySelector(SELECTORS.breadcrumb)) || null;

    // Rows are attributed to a date via their OWN containing
    // `.sports-table` group (see resolveDateGroupText) rather than a
    // single document-wide query, so each row can carry its own
    // date_text_raw. A row that (unexpectedly) isn't inside any
    // `.sports-table` is still captured, with date_text_raw: null,
    // never dropped.
    const tables = Array.from(documentLike.querySelectorAll('.sports-table'));
    const rowsInTables = new Set();
    const fixtures = [];
    for (const tableEl of tables) {
      const dateTextRaw = resolveDateGroupText(tableEl);
      // `[id^="prematch_event-"]` also matches descendant odds/dropdown
      // controls that repeat the parent row's id with a suffix (e.g.
      // "..._odds_market-1x2_sign-1", "..._dropoption-3") -- filtered out
      // by requiring an EXACT match against FIXTURE_ROW_ID_PATTERN, per
      // the confirmed row-counting discipline documented in every
      // contract.
      const rows = Array.from(tableEl.querySelectorAll('[id^="prematch_event-"]')).filter(isExactFixtureRow);
      for (const rowEl of rows) {
        rowsInTables.add(rowEl);
        fixtures.push(parseFixtureRow(rowEl, dateTextRaw));
      }
    }
    const strayRows = Array.from(documentLike.querySelectorAll('[id^="prematch_event-"]'))
      .filter(isExactFixtureRow)
      .filter((el) => !rowsInTables.has(el));
    for (const rowEl of strayRows) {
      fixtures.push(parseFixtureRow(rowEl, null));
    }

    const seenIds = new Set();
    const duplicateFixtureIds = [];
    for (const fixture of fixtures) {
      if (seenIds.has(fixture.fixture_id)) duplicateFixtureIds.push(fixture.fixture_id);
      seenIds.add(fixture.fixture_id);
    }

    const emptyStateMarkerPresent = text(documentLike.querySelector(SELECTORS.emptyStateMarker)) === EMPTY_STATE_TEXT_EXACT;
    // Confirmed only for Basketball/WNBA so far -- see header comment.
    // Never asserts CONFIRMED_EMPTY from a zero row count alone.
    const emptyStateStatus = fixtures.length > 0 ? 'NOT_EMPTY' : emptyStateMarkerPresent ? 'CONFIRMED_EMPTY' : 'UNKNOWN_EMPTY';

    return {
      schema_version: SCHEMA_VERSION,
      captured_at_utc: capturedAtUtc,
      source_url: href,
      sport_slug_from_url: resolveSportSlugFromUrl(href),
      breadcrumb_raw: breadcrumbRaw,
      fixture_row_count: fixtures.length,
      empty_state_status: emptyStateStatus,
      fixtures,
      duplicate_fixture_ids: duplicateFixtureIds,
    };
  }

  const api = {
    parseFixturesFromDocument,
    resolveSportSlugFromUrl,
    parseThreeWayOdds,
    FIXTURE_ROW_ID_PATTERN,
    THREE_WAY_SIGN_MAP,
    EMPTY_STATE_TEXT_EXACT,
    SCHEMA_VERSION,
    SELECTORS,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaAllSportsFixtureParser = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
