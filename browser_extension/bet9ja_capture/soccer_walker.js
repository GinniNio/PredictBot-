/**
 * Bet9ja Soccer ALL-COMPETITIONS fixture walker -- automates the manual
 * "click every competition, copy its fixtures" workflow the daily
 * intake step used to require. Pure orchestration: every fixture actually
 * comes from parser.js's own `captureFromDocument`, called once per
 * selected competition -- this file never re-implements fixture parsing,
 * only the navigation and aggregation around it.
 *
 * Scope (see README.md "Capture all Soccer fixtures"):
 *   - Soccer only. Pre-match only. Ordinary 1X2 only -- 1UP/2UP, player
 *     markets, specials, live, Zoom, and virtual events are still routed
 *     to parser.js's own typed `unparsed_records` exclusions, unchanged.
 *   - Read-only in the same sense as every other button in this
 *     extension: the only interaction is a client-side click on a
 *     competition-menu link already visible on the page -- no network
 *     requests of its own, no navigation away from the current tab.
 *   - Walks every competition link discovered in the Soccer competition
 *     menu, deduplicates fixtures by their stable `fixture_id` (a real
 *     fixture legitimately reachable from more than one menu entry --
 *     e.g. both a "Highlights" listing and its own competition page --
 *     is captured once, not once per entry it happened to appear under),
 *     and produces ONE combined envelope.
 *
 * SELECTOR CONTRACT -- SOCCER_MENU_SELECTORS.soccerMenuContainer IS AN
 * UNVERIFIED PLACEHOLDER (`null`). Confirmed so far (live inspection,
 * 2026-09-11): competition menu items are `.menu-list__link` elements
 * with `href="javascript:;"` (client-side switches, not real navigation).
 * NOT yet confirmed: the container/selector that scopes this to ONLY the
 * Soccer competition menu -- `.menu-list__link` alone is expected to also
 * match other sports' competition pickers and unrelated site shortcuts
 * sharing the same class, so discovery must not run against the whole
 * document. Until a real scoping selector is supplied,
 * `soccerMenuContainer` stays `null` and this module discovers ZERO
 * competitions on every real page -- an honest `CAPTURE_FAILED` with a
 * named reason, never a guess at scoping. See
 * SOCCER_ALL_COMPETITIONS_VALIDATION.md for exactly what evidence is
 * needed next (the container selector, and how a link's own country/
 * competition identity is exposed, if not via the URL -- see below).
 *
 * IDENTITY: rather than guess a link-level attribute for country/
 * competition (unconfirmed), this reuses parser.js's own
 * `parseBet9jaCompetitionUrl` against the page's OWN URL after each
 * click -- a single-page app that updates the URL via `pushState` on a
 * competition switch will resolve real identity for free from
 * already-confirmed logic; a page that does not is honestly `null`
 * (`source_country`/`source_competition` unresolved), never guessed from
 * the clicked link's text or attributes.
 *
 * CONTENT-CHANGE WAIT: rather than guess a "competition identity changed"
 * DOM signal (also unconfirmed), this waits for the confirmed
 * `.sports-table__matchup` elements' own identity (their `id` attributes,
 * or text when absent) to differ from what was on screen before the
 * click -- evidence-independent: no new selector is needed to detect
 * this, since `.sports-table__matchup` is already confirmed by
 * parser.js's own real-page validation.
 *
 * SAFETY: the ONLY element this module ever calls `.click()` on is a
 * competition-menu link discovered inside the (currently unconfirmed)
 * `soccerMenuContainer` -- see the safety test in
 * tests/soccer_walker.test.js, which greps this file's own source.
 */
(function (root) {
  const Bet9jaCapture = typeof module !== 'undefined' && module.exports ? require('./parser.js') : root.Bet9jaCapture;
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-soccer-walker@0.1.0-unverified-menu-selectors';

  // See SELECTOR CONTRACT above. `soccerMenuContainer` is deliberately
  // exported as a mutable field (not a frozen constant) so a future real
  // selector can be dropped in without touching any other code, and so
  // tests can exercise the real navigation/aggregation logic against a
  // configured container without needing that real selector yet.
  const SOCCER_MENU_SELECTORS = {
    competitionLink: '.menu-list__link',
    soccerMenuContainer: null,
    matchup: '.sports-table__matchup',
  };

  const CONTENT_CHANGE_TIMEOUT_MS = 3000;
  const CONTENT_CHANGE_POLL_INTERVAL_MS = 25;
  // Real evidence (live inspection) found 18 matchups under one
  // "Upcoming" view and multiple named competitions (Premier League,
  // LaLiga, Serie A, Bundesliga, Ligue 1, ...) -- this cap is a generous
  // multiple of any plausible real competition count, kept as a hard
  // backstop against an unbounded loop, never relied upon normally.
  const MAX_COMPETITIONS_SAFETY_CAP = 200;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function waitFor(predicate, timeoutMs, intervalMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (predicate()) return true;
      await sleep(intervalMs);
    }
    return predicate();
  }

  function text(el) {
    if (!el) return '';
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  // A candidate competition link is confirmed real (`href="javascript:;"`,
  // a client-side switch, not a real navigation target) and carries
  // visible text -- a decorative or icon-only `.menu-list__link` with no
  // label is excluded rather than treated as an unnamed competition.
  function isCandidateCompetitionLink(el) {
    const href = (el.getAttribute('href') || '').trim();
    return href === 'javascript:;' && text(el).length > 0;
  }

  function discoverSoccerCompetitionLinks(doc) {
    if (!SOCCER_MENU_SELECTORS.soccerMenuContainer) return [];
    const container = doc.querySelector(SOCCER_MENU_SELECTORS.soccerMenuContainer);
    if (!container) return [];
    return Array.from(container.querySelectorAll(SOCCER_MENU_SELECTORS.competitionLink)).filter(
      isCandidateCompetitionLink
    );
  }

  // Fingerprints the currently-visible matchup rows by their own `id`
  // attribute (falling back to text when absent) -- confirmed evidence
  // (`.sports-table__matchup`'s `id`, e.g.
  // "prematch_event-832455154"/"home_highlights_sport-1_event-..."),
  // never a new, unconfirmed selector. Used only to detect that a click
  // actually changed what's on screen -- never parsed for meaning here.
  function getMatchupFingerprint(doc) {
    return Array.from(doc.querySelectorAll(SOCCER_MENU_SELECTORS.matchup))
      .map((el) => el.id || text(el))
      .sort()
      .join('|');
  }

  /**
   * The ONLY function in this file that ever calls .click(). Clicks a
   * discovered competition link and waits for the confirmed matchup
   * fingerprint to change -- never assumes the click succeeded just
   * because it was dispatched. A competition whose fixtures happen to be
   * byte-identical to whatever was on screen before (unlikely, but not
   * impossible) will be reported as CONTENT_DID_NOT_CHANGE and skipped,
   * per the fail-closed contract below -- never silently merged with the
   * previous competition's fixtures.
   */
  async function selectCompetition(doc, linkEl) {
    const before = getMatchupFingerprint(doc);
    linkEl.click();
    const changed = await waitFor(
      () => getMatchupFingerprint(doc) !== before,
      CONTENT_CHANGE_TIMEOUT_MS,
      CONTENT_CHANGE_POLL_INTERVAL_MS
    );
    return { ok: changed, reason: changed ? null : 'CONTENT_DID_NOT_CHANGE' };
  }

  // Reads country/competition identity from the page's OWN url after a
  // click, reusing parser.js's already-confirmed
  // parseBet9jaCompetitionUrl -- see the IDENTITY note in the header
  // comment for why this is preferred over guessing a link-level
  // attribute. Returns null (never guessed) if the URL doesn't match
  // that confirmed shape, e.g. because this page never updates its own
  // URL on a client-side competition switch -- unconfirmed either way
  // until real evidence says otherwise.
  function readCompetitionIdentityFromUrl(doc) {
    const href = (doc.defaultView && doc.defaultView.location && doc.defaultView.location.href) || '';
    const info = Bet9jaCapture.parseBet9jaCompetitionUrl(href);
    if (!info) return null;
    return { country: info.countrySlug, competition: info.competitionSlug };
  }

  /**
   * Best-effort restore to whatever the Soccer page's default/first view
   * is -- fire-and-forget, same "restore what capture touched" spirit as
   * the ticket-capture extension's own collapse/page-1 restores, never
   * required for the correctness of the capture that already happened.
   * Prefers a link whose own text says "Highlights" (matching visible
   * text a person can already see, not a guessed structural attribute);
   * falls back to the first competition link found, since re-selecting
   * SOME real competition is more useful than leaving the page on
   * whatever the walk happened to end on.
   */
  function restoreOriginalView(doc, allLinks) {
    const highlightsLink = allLinks.find((el) => /highlights/i.test(text(el)));
    const restoreTarget = highlightsLink || allLinks[0];
    if (restoreTarget) restoreTarget.click();
  }

  function makeCompetitionResult({ country, competition, captureStatus, fixturesSeen, fixturesParsed, fixturesUnresolved, failureReason }) {
    return {
      country: country || null,
      competition: competition || null,
      capture_status: captureStatus,
      fixtures_seen: fixturesSeen,
      fixtures_parsed: fixturesParsed,
      fixtures_unresolved: fixturesUnresolved,
      failure_reason: failureReason || null,
    };
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string}} context
   * @returns {Promise<{envelope: object}>}
   */
  async function captureAllSoccerCompetitions(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const envelopeBase = {
      schema_version: 'bet9ja-soccer-all-competitions-capture.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: Bet9jaCapture.sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
    };

    const linkEls = discoverSoccerCompetitionLinks(doc);
    const competitionsAvailable = linkEls.length;

    const fixtures = [];
    const unparsedRecords = [];
    const competitionResults = [];
    const seenFixtureIds = new Set();
    let fixturesSeen = 0;
    let fixturesParsed = 0;
    let fixturesUnresolved = 0;
    let duplicatesSkipped = 0;
    let competitionsVisited = 0;
    let competitionsFailed = 0;

    if (competitionsAvailable === 0) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['SOCCER_MENU_SELECTORS_UNVERIFIED', 'NO_COMPETITIONS_DISCOVERED'],
          scope: 'SOCCER_ALL_DISCOVERED_COMPETITIONS',
          competitions_available: 0,
          competitions_visited: 0,
          competitions_failed: 0,
          fixtures_seen: 0,
          fixtures_parsed: 0,
          fixtures_unresolved: 0,
          duplicates_skipped: 0,
          competition_results: [],
          fixtures: [],
          unparsed_records: [],
        },
      };
    }

    const cappedLinkEls = linkEls.slice(0, MAX_COMPETITIONS_SAFETY_CAP);

    for (let i = 0; i < cappedLinkEls.length; i += 1) {
      const linkEl = cappedLinkEls[i];
      const { ok, reason } = await selectCompetition(doc, linkEl);
      if (!ok) {
        competitionsFailed += 1;
        competitionResults.push(
          makeCompetitionResult({
            country: null,
            competition: null,
            captureStatus: 'FAILED',
            fixturesSeen: 0,
            fixturesParsed: 0,
            fixturesUnresolved: 0,
            failureReason: reason,
          })
        );
        continue;
      }

      const identity = readCompetitionIdentityFromUrl(doc);
      const currentUrl = (doc.defaultView && doc.defaultView.location && doc.defaultView.location.href) || context.sourceUrl;
      const { envelope: subEnvelope } = Bet9jaCapture.captureFromDocument(doc, {
        sourceUrl: currentUrl,
        pageTitle: context.pageTitle,
        capturedAtUtc,
        previousIndex: {},
      });

      fixturesSeen += subEnvelope.coverage.records_seen;
      fixturesUnresolved += subEnvelope.coverage.records_unresolved;

      let compFixturesParsed = 0;
      for (const fixture of subEnvelope.fixtures) {
        if (seenFixtureIds.has(fixture.fixture_id)) {
          duplicatesSkipped += 1;
          continue;
        }
        seenFixtureIds.add(fixture.fixture_id);
        fixtures.push({
          ...fixture,
          source_country: identity ? identity.country : null,
          source_competition: identity ? identity.competition : null,
        });
        compFixturesParsed += 1;
      }
      fixturesParsed += compFixturesParsed;

      for (const record of subEnvelope.unparsed_records) {
        unparsedRecords.push({
          ...record,
          source_country: identity ? identity.country : null,
          source_competition: identity ? identity.competition : null,
        });
      }

      competitionResults.push(
        makeCompetitionResult({
          country: identity ? identity.country : null,
          competition: identity ? identity.competition : null,
          captureStatus: 'COMPLETE',
          fixturesSeen: subEnvelope.coverage.records_seen,
          fixturesParsed: compFixturesParsed,
          fixturesUnresolved: subEnvelope.coverage.records_unresolved,
        })
      );
      competitionsVisited += 1;
    }

    restoreOriginalView(doc, linkEls);

    const statusReasons = ['SOCCER_MENU_SELECTORS_UNVERIFIED'];
    if (competitionsFailed > 0) statusReasons.push('SOME_COMPETITIONS_FAILED');

    let captureStatus;
    if (fixtures.length === 0 && unparsedRecords.length === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else {
      // Never CAPTURE_OK -- the navigation/aggregation flow itself has no
      // real-page validation yet (see SOCCER_MENU_SELECTORS_UNVERIFIED),
      // so even a clean run cannot yet be called fully validated. Same
      // permanent-cap pattern as ticket_parser.js's MYBETS profile.
      captureStatus = 'CAPTURE_PARTIAL';
    }

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        scope: 'SOCCER_ALL_DISCOVERED_COMPETITIONS',
        competitions_available: competitionsAvailable,
        competitions_visited: competitionsVisited,
        competitions_failed: competitionsFailed,
        fixtures_seen: fixturesSeen,
        fixtures_parsed: fixturesParsed,
        fixtures_unresolved: fixturesUnresolved,
        duplicates_skipped: duplicatesSkipped,
        competition_results: competitionResults,
        fixtures,
        unparsed_records: unparsedRecords,
      },
    };
  }

  const api = { captureAllSoccerCompetitions, SOCCER_MENU_SELECTORS, PARSER_VERSION };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerWalker = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
