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
 */
(function (root) {
  const SCHEMA_VERSION = 'bet9ja-allsports-fixture-capture.v1';

  // See header comment: identical across every sport evidenced so far.
  const FIXTURE_ROW_ID_PATTERN = /^prematch_event-(\d+)$/;

  const SELECTORS = {
    home: '.sports-table__home',
    away: '.sports-table__away',
    time: '.sports-table__time',
    breadcrumb: '.sports-view__crumbs',
  };

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

  function parseFixtureRow(rowEl) {
    const fixtureId = rowEl.id.match(FIXTURE_ROW_ID_PATTERN)[1];
    const participant1 = text(rowEl.querySelector(SELECTORS.home));
    const participant2 = text(rowEl.querySelector(SELECTORS.away));
    return {
      fixture_id: fixtureId,
      participant_1: participant1 || null,
      participant_2: participant2 || null,
      kickoff_time_raw: text(rowEl.querySelector(SELECTORS.time)) || null,
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

    // `[id^="prematch_event-"]` also matches descendant odds/dropdown
    // controls that repeat the parent row's id with a suffix (e.g.
    // "..._odds_market-1x2_sign-1", "..._dropoption-3") -- filtered out
    // by requiring an EXACT match against FIXTURE_ROW_ID_PATTERN, per the
    // confirmed row-counting discipline documented in every contract.
    const rows = Array.from(documentLike.querySelectorAll('[id^="prematch_event-"]')).filter(isExactFixtureRow);

    const fixtures = rows.map(parseFixtureRow);
    const seenIds = new Set();
    const duplicateFixtureIds = [];
    for (const fixture of fixtures) {
      if (seenIds.has(fixture.fixture_id)) duplicateFixtureIds.push(fixture.fixture_id);
      seenIds.add(fixture.fixture_id);
    }

    return {
      schema_version: SCHEMA_VERSION,
      captured_at_utc: capturedAtUtc,
      source_url: href,
      sport_slug_from_url: resolveSportSlugFromUrl(href),
      breadcrumb_raw: breadcrumbRaw,
      fixture_row_count: fixtures.length,
      fixtures,
      duplicate_fixture_ids: duplicateFixtureIds,
    };
  }

  const api = {
    parseFixturesFromDocument,
    resolveSportSlugFromUrl,
    FIXTURE_ROW_ID_PATTERN,
    SCHEMA_VERSION,
    SELECTORS,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaAllSportsFixtureParser = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
