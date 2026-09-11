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
 * ROUND 1 REAL-DOM CORRECTION (2026-09-11): Round 0's discovery selector
 * (an unconfirmed `soccerMenuContainer`, kept deliberately `null`) was
 * exercised against two real capture runs from both supported starting
 * routes (`/sport/soccer/1` and `/sportPage/1/coupons`) and, as expected
 * for an unconfirmed placeholder, discovered zero competitions on both.
 * Live inspection then confirmed the real menu structure:
 *
 *   <ul class="menu-list mt30">
 *     <li class="menu-list__item">
 *       <a class="menu-list__link" href="javascript:;">England Premier League</a>
 *     </li>
 *   </ul>
 *
 * Discovery now uses `.menu-list.mt30 .menu-list__item > .menu-list__link`
 * (see SOCCER_MENU_SELECTORS below) -- but see PARSER_VERSION's own
 * comment for why this file's version string is NOT bumped yet: this
 * selector is confirmed by DOM inspection, not yet by one real successful
 * end-to-end capture (a real click actually landing on a competition page
 * and fixtures actually being parsed from it). That is this round's
 * explicit remaining gap -- see SOCCER_ALL_COMPETITIONS_VALIDATION.md.
 *
 * Every competition link uses `href="javascript:;"` (never a real
 * navigation target), so identity can never come from `href`. Selecting a
 * competition is confirmed to change the page's own URL to
 * `/competition/soccer/...` (parser.js's already-confirmed
 * `parseBet9jaCompetitionUrl` shape) -- `selectCompetition` below waits
 * for exactly that pathname prefix AND the confirmed `.sports-table__matchup`
 * rows to have rendered, rather than the old (also honestly working, but
 * less precise) "matchup fingerprint changed" heuristic Round 0 used.
 *
 * STALE-NODE SAFETY: a competition page is not confirmed to keep the same
 * menu DOM nodes around (it may not render the menu at all, or may
 * re-render it with new elements even if visually identical) -- so this
 * module NEVER reuses a `<a>` element reference across a navigation. It
 * identifies a competition by its stable visible LABEL TEXT (the only
 * stable identifier available, since `href` is always `javascript:;`),
 * returns to the inventory page via `history.back()` after each
 * competition, and re-runs `discoverSoccerCompetitionLinks` fresh before
 * looking up the next link by label. A label that no longer resolves on
 * re-discovery (e.g. the live menu genuinely changed mid-walk) is reported
 * `FAILED`/`LINK_NOT_FOUND_ON_REDISCOVERY` for that one competition,
 * never a crash and never silently skipped.
 *
 * DEDUPLICATION: two different menu labels can resolve to the same
 * destination URL (e.g. both a "Highlights"-style entry and a named
 * league entry landing on the same competition page). Destinations are
 * deduplicated by their sanitized resolved URL -- a second label
 * resolving to an already-visited destination is reported
 * `SKIPPED_DUPLICATE_DESTINATION` and never re-captured. Fixtures within
 * a single captured destination are additionally deduplicated by their
 * stable `fixture_id`, as before.
 *
 * SAFETY: the ONLY element this module ever calls `.click()` on is a
 * competition-menu link discovered inside the confirmed
 * `SOCCER_MENU_SELECTORS.menuContainer` -- see the safety test in
 * tests/soccer_walker.test.js, which greps this file's own source.
 * Returning to the inventory page uses the browser's own `history.back()`
 * navigation primitive, never a click on a guessed "back"/breadcrumb
 * control.
 */
(function (root) {
  const Bet9jaCapture = typeof module !== 'undefined' && module.exports ? require('./parser.js') : root.Bet9jaCapture;
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  // NOT bumped this round on purpose: SOCCER_MENU_SELECTORS below is
  // confirmed by live DOM inspection, but no real click has yet been
  // exercised end-to-end against the live account (that would confirm
  // the pathname-change wait and a real fixture actually being parsed
  // from a real competition page). Per this project's evidence-only
  // versioning discipline, the version string advances only after that
  // one real successful capture -- see SOCCER_ALL_COMPETITIONS_VALIDATION.md.
  const PARSER_VERSION = 'bet9ja-soccer-walker@0.1.0-unverified-menu-selectors';

  // Confirmed via live inspection, Round 1 (see header comment above).
  // Still exported as a mutable object so a future correction (or a test
  // exercising a differently-shaped synthetic page) never requires
  // touching any other code.
  const SOCCER_MENU_SELECTORS = {
    menuContainer: '.menu-list.mt30',
    menuItem: '.menu-list__item',
    competitionLink: '.menu-list__link',
    matchup: '.sports-table__matchup',
  };

  const COMPETITION_PATH_PREFIX = '/competition/soccer/';

  const CONTENT_CHANGE_TIMEOUT_MS = 3000;
  const CONTENT_CHANGE_POLL_INTERVAL_MS = 25;
  const RETURN_TO_INVENTORY_TIMEOUT_MS = 3000;
  const RETURN_TO_INVENTORY_POLL_INTERVAL_MS = 25;
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

  /**
   * Always re-queries fresh -- never caches a NodeList/element reference
   * across a navigation (see STALE-NODE SAFETY above). Scoped to the
   * confirmed `.menu-list.mt30` container so this never matches another
   * sport's competition picker or an unrelated site shortcut sharing the
   * same `.menu-list__link` class.
   */
  function discoverSoccerCompetitionLinks(doc) {
    const container = doc.querySelector(SOCCER_MENU_SELECTORS.menuContainer);
    if (!container) return [];
    return Array.from(
      container.querySelectorAll(`${SOCCER_MENU_SELECTORS.menuItem} > ${SOCCER_MENU_SELECTORS.competitionLink}`)
    ).filter(isCandidateCompetitionLink);
  }

  function currentPathname(doc) {
    return (doc.defaultView && doc.defaultView.location && doc.defaultView.location.pathname) || '';
  }

  function currentHref(doc, fallback) {
    return (doc.defaultView && doc.defaultView.location && doc.defaultView.location.href) || fallback || '';
  }

  /**
   * The ONLY function in this file that ever calls .click() on a
   * competition link. Waits for the confirmed navigation signal: the
   * page's own pathname moving to `/competition/soccer/...` AND at least
   * one confirmed `.sports-table__matchup` row having rendered -- never
   * assumes the click succeeded just because it was dispatched.
   */
  async function selectCompetition(doc, linkEl) {
    linkEl.click();
    const ready = await waitFor(
      () => currentPathname(doc).startsWith(COMPETITION_PATH_PREFIX) && doc.querySelectorAll(SOCCER_MENU_SELECTORS.matchup).length > 0,
      CONTENT_CHANGE_TIMEOUT_MS,
      CONTENT_CHANGE_POLL_INTERVAL_MS
    );
    return { ok: ready, reason: ready ? null : 'CONTENT_DID_NOT_CHANGE' };
  }

  // Reads country/competition identity from the page's OWN url after a
  // click, reusing parser.js's already-confirmed
  // parseBet9jaCompetitionUrl -- never guessed from the clicked link's own
  // (always `javascript:;`) href or its visible text.
  function readCompetitionIdentityFromUrl(doc) {
    const href = currentHref(doc);
    const info = Bet9jaCapture.parseBet9jaCompetitionUrl(href);
    if (!info) return null;
    return { country: info.countrySlug, competition: info.competitionSlug };
  }

  /**
   * Returns to the Soccer inventory page using the browser's own
   * `history.back()` navigation primitive -- never a click on a guessed
   * "back"/breadcrumb control -- and waits for the confirmed menu
   * container to be discoverable again with at least one candidate link.
   * Never assumes the previous page's own DOM nodes survived; the caller
   * always re-runs discoverSoccerCompetitionLinks afterward.
   */
  async function returnToInventory(doc) {
    const win = doc.defaultView;
    if (win && win.history && typeof win.history.back === 'function') {
      win.history.back();
    }
    return waitFor(
      () => discoverSoccerCompetitionLinks(doc).length > 0,
      RETURN_TO_INVENTORY_TIMEOUT_MS,
      RETURN_TO_INVENTORY_POLL_INTERVAL_MS
    );
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

  function emptyFailedEnvelope(envelopeBase, reasons) {
    return {
      envelope: {
        ...envelopeBase,
        capture_status: 'CAPTURE_FAILED',
        capture_status_reasons: reasons,
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

    const initialLinks = discoverSoccerCompetitionLinks(doc);
    const competitionsAvailable = initialLinks.length;

    if (competitionsAvailable === 0) {
      const containerFound = !!doc.querySelector(SOCCER_MENU_SELECTORS.menuContainer);
      return emptyFailedEnvelope(envelopeBase, [
        containerFound ? 'NO_COMPETITIONS_DISCOVERED' : 'SOCCER_MENU_CONTAINER_NOT_FOUND',
      ]);
    }

    // Identify each candidate by its stable visible label -- never by a
    // cached element reference, which a navigation may invalidate (see
    // STALE-NODE SAFETY above). Two menu items sharing the exact same
    // label are only ever visited once; this is a plausible real gap
    // (e.g. an ambiguous label), not silently guessed around.
    const labels = [];
    const seenLabels = new Set();
    for (const el of initialLinks) {
      const label = text(el);
      if (!seenLabels.has(label)) {
        seenLabels.add(label);
        labels.push(label);
      }
    }
    const cappedLabels = labels.slice(0, MAX_COMPETITIONS_SAFETY_CAP);

    const fixtures = [];
    const unparsedRecords = [];
    const competitionResults = [];
    const seenFixtureIds = new Set();
    const seenDestinationUrls = new Set();
    let fixturesSeen = 0;
    let fixturesParsed = 0;
    let fixturesUnresolved = 0;
    let duplicatesSkipped = 0;
    let competitionsVisited = 0;
    let competitionsFailed = 0;
    let stoppedEarlyReason = null;

    for (let i = 0; i < cappedLabels.length; i += 1) {
      const label = cappedLabels[i];

      // Always re-discover immediately before use -- never a reference
      // held from a previous iteration or from the initial discovery
      // pass, since an intervening navigation may have invalidated it.
      const freshLinks = discoverSoccerCompetitionLinks(doc);
      const linkEl = freshLinks.find((el) => text(el) === label);
      if (!linkEl) {
        competitionsFailed += 1;
        competitionResults.push(
          makeCompetitionResult({
            country: null,
            competition: null,
            captureStatus: 'FAILED',
            fixturesSeen: 0,
            fixturesParsed: 0,
            fixturesUnresolved: 0,
            failureReason: 'LINK_NOT_FOUND_ON_REDISCOVERY',
          })
        );
        continue;
      }

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
        // A click that never changed the page is not a navigation --
        // still on the inventory page, so no need to return to it.
        continue;
      }

      const identity = readCompetitionIdentityFromUrl(doc);
      const resolvedHref = currentHref(doc, context.sourceUrl);
      const destinationKey = Bet9jaCapture.sanitizeSourceUrl(resolvedHref);

      if (seenDestinationUrls.has(destinationKey)) {
        duplicatesSkipped += 0; // fixture-level counter is separate; see competition-level result below
        competitionResults.push(
          makeCompetitionResult({
            country: identity ? identity.country : null,
            competition: identity ? identity.competition : null,
            captureStatus: 'SKIPPED_DUPLICATE_DESTINATION',
            fixturesSeen: 0,
            fixturesParsed: 0,
            fixturesUnresolved: 0,
          })
        );
      } else {
        seenDestinationUrls.add(destinationKey);

        const { envelope: subEnvelope } = Bet9jaCapture.captureFromDocument(doc, {
          sourceUrl: resolvedHref,
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

      // Always return to the inventory page after a successful
      // navigation -- including after the LAST competition, so the page
      // is left in a predictable, re-usable state, not stranded on
      // whichever competition the walk happened to end on. A failed
      // return is a safe-stop, not something to guess past.
      {
        const restored = await returnToInventory(doc);
        if (!restored) {
          stoppedEarlyReason = 'COULD_NOT_RETURN_TO_INVENTORY';
          break;
        }
      }
    }

    const statusReasons = ['MENU_SELECTORS_CONFIRMED_VIA_INSPECTION_PENDING_REAL_CAPTURE'];
    if (competitionsFailed > 0) statusReasons.push('SOME_COMPETITIONS_FAILED');
    if (stoppedEarlyReason) statusReasons.push(stoppedEarlyReason);

    let captureStatus;
    if (fixtures.length === 0 && unparsedRecords.length === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else {
      // Never CAPTURE_OK -- the navigation flow itself has no real
      // end-to-end successful capture yet (see PARSER_VERSION's own
      // comment), so even a clean run cannot yet be called fully
      // validated. Same permanent-cap pattern as ticket_parser.js's
      // MYBETS profile and settled_bets_parser.js.
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
