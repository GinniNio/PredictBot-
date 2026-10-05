/**
 * Bet9ja all-sports sport walker -- stage 3.
 *
 * The pure parsers (`catalogue_parser.js`, `fixture_parser.js`) only ever
 * read whatever page is already open. This module is the layer above
 * them that DRIVES a sport's own sidebar through every competition it
 * discovers and calls `fixture_parser.js` on each one -- explicitly kept
 * separate from it, per the operator's own instruction, rather than
 * folding navigation into the pure DOM parser.
 *
 * SEQUENCE (operator-specified):
 *   sport root -> expand sport -> click "show more" while label contains
 *   "more" -> enumerate country/group toggles -> open one group ->
 *   enumerate competition links -> click one competition -> wait for the
 *   target breadcrumb + fixture table or empty marker -> run fixture
 *   parser -> return to / reopen sport and continue.
 *
 * EVIDENCE (2026-09-30, real raw `outerHTML` snapshots + the live
 * sidebar export already backing catalogue_parser.js):
 *  - A real Ice Hockey capture (16:25:09Z) confirms the sport root
 *    expanded, its "show more" control present, and 10 country/group
 *    toggles visible -- but ZERO competition links, because no country
 *    group had been opened yet. Confirms the operator's own sequence:
 *    competition links (`a[id*="_g-"]`) only appear AFTER a group's own
 *    toggle is clicked, never before.
 *  - Two other real captures (Basketball/WNBA 16:23:42Z, Ice Hockey/
 *    Russia/KHL 16:26:27Z) confirm `fixture_parser.js` correctly parses
 *    a real competition page once reached (see its own header comment
 *    for the row-structure fix this forced).
 *
 * WHAT IS NOT YET CONFIRMED (no live browser access in this session to
 * test against): exact accordion open/closed state selectors for the
 * sport root or a group toggle (this sidebar has no confirmed
 * `accordion-item--open`-style class the way the retired Soccer
 * `/sportPage/1/competitions` accordion did); how long a click takes to
 * render its result; and whether "navigation" here means a real page
 * load (destroying this content script's own execution) or an in-place
 * SPA DOM swap (per the operator's own rule 2 -- "reopen the sport
 * accordion after each navigation, since navigation replaces the page
 * DOM" -- this module assumes the latter: the same `document` object
 * survives, mutated in place, never a fresh page load requiring
 * re-injection). All timeouts/poll intervals below are therefore
 * explicitly marked [UNVERIFIED] placeholders, not evidence-based
 * constants -- the first real run against a live, authenticated session
 * is what should correct them, the same discipline this project already
 * applied to the retired Soccer walker's own timeout constants.
 *
 * SAFETY: the only elements this module ever calls `.click()` on are a
 * sport's own root toggle, its "show more" toggle, a group's own toggle,
 * and a competition's own link (`a[id*="_g-"]`, confirmed `href` is the
 * literal string "javascript:;" -- never followed as a URL, only
 * clicked). Never a price, selection, Cashout, Live Betting, or betslip
 * control.
 *
 * STALE-REFERENCE DISCIPLINE: every element this module acts on is
 * re-queried fresh from the current `doc` immediately before use, never
 * cached across a click -- per rule 2, a navigation can replace the DOM
 * entirely, and a reference held from before that point may be detached.
 * Competitions are tracked by their own stable `competition_id`, never
 * by array position, matching this project's own established resume
 * discipline elsewhere (see `bet9ja_capture/soccer_walker.js`'s header
 * comment on the same point).
 *
 * VALIDATION: after every competition click, the resulting page's own
 * `fixture_parser.js` breadcrumb (and, when resolvable, its URL sport
 * slug) must agree with the competition actually requested. A page that
 * doesn't resolve a breadcrumb at all is treated as a MISMATCH too
 * (fail-closed) -- there is no confirmed alternative signal to trust
 * instead. On any mismatch this module records `INVALID_CONTENT_
 * MISMATCH` and discards whatever fixtures were parsed; a mismatched
 * page's rows are NEVER ingested into `results[]`.
 */
(function (root) {
  const CatalogueParser =
    typeof module !== 'undefined' && module.exports ? require('./catalogue_parser.js') : root.Bet9jaAllSportsCatalogueParser;
  const FixtureParser =
    typeof module !== 'undefined' && module.exports ? require('./fixture_parser.js') : root.Bet9jaAllSportsFixtureParser;

  const SCHEMA_VERSION = 'bet9ja-allsports-sport-walk.v2.1';

  // [UNVERIFIED] -- see header comment. Generous placeholders, never
  // presented as evidence-based.
  const POLL_INTERVAL_MS = 25;
  // v2.1: raised from 3000 after two Specials Basketball SPORT_EXPAND_TIMEOUT
  // failures on 2026-10-03 (third attempt succeeded). [UNVERIFIED]
  const EXPAND_TIMEOUT_MS = 6000;
  // v2.1: after rows appear, wait this long for breadcrumb AND heading to
  // match the requested competition. 5 Basketball competitions on
  // 2026-10-03 had a correct breadcrumb but a heading still showing the
  // previous route ("Extraliga"). [UNVERIFIED]
  const IDENTITY_SETTLE_TIMEOUT_MS = 3000;
  const SHOW_MORE_TIMEOUT_MS = 3000;
  const GROUP_EXPAND_TIMEOUT_MS = 3000;
  const COMPETITION_LOAD_TIMEOUT_MS = 5000;
  // v2: Bet9ja can render rows before prices. After rows settle, wait up
  // to this long for every row to carry a fully priced market. [UNVERIFIED]
  const ODDS_LOAD_TIMEOUT_MS = 4000;
  // v2: walk is COMPLETE only if at least this share of fixtures is priced.
  const ODDS_COMPLETE_MIN_SHARE = 0.9;
  const MAX_SHOW_MORE_CLICKS = 10;

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

  /**
   * Competition links belonging to ONE specific group, scoped by id
   * prefix (never DOM containment -- see catalogue_parser.js's own
   * comment on why). Re-derived from `CatalogueParser`'s own confirmed
   * pattern rather than duplicating it.
   */
  function discoverCompetitionLinksForGroup(doc, sportId, sportSlug, groupId) {
    const full = CatalogueParser.parseCatalogueFromDocument(doc, { sportId, sportSlug });
    if (!full.ok) return [];
    return full.entries.filter((e) => e.group_id === String(groupId));
  }

  function findGroupToggle(doc, sportId, sportSlug, groupId, groupSlug) {
    return doc.getElementById(`left_prematch_sport-${sportId}_${sportSlug}_sg-${groupId}_${groupSlug}_label-toggle`);
  }

  /**
   * Re-resolves a competition's own link element fresh, matched by its
   * stable `competition_id` and `group_id` -- never by array position or
   * a cached reference from an earlier discovery pass, since a prior
   * navigation may have replaced the DOM (see header comment).
   */
  function findCompetitionLink(doc, sportId, sportSlug, groupId, competitionId) {
    const prefix = `left_prematch_sport-${sportId}_${sportSlug}_sg-${groupId}_`;
    return Array.from(doc.querySelectorAll('a[id*="_g-"]')).find(
      (el) => el.id.startsWith(prefix) && el.id.includes(`_g-${competitionId}_`)
    );
  }

  /**
   * Fail-closed content validation: the visited competition's own label
   * must appear in the resulting page's breadcrumb (normalized: lower-
   * cased, non-alphanumerics collapsed, matching this project's own
   * `slugify`/`normalizeForMatch` convention elsewhere) -- a missing or
   * disagreeing breadcrumb is a mismatch, never assumed correct.
   */
  function breadcrumbAgreesWithCompetition(fixtureResult, competitionEntry) {
    const breadcrumbSlug = CatalogueParser.slugify(fixtureResult.breadcrumb_raw || '');
    if (!breadcrumbSlug) return false;
    const requiredParts = [
      competitionEntry.sport_slug,
      competitionEntry.group_slug,
      competitionEntry.competition_slug || competitionEntry.label_raw,
    ].map((value) => CatalogueParser.slugify(value || ''));
    return requiredParts.every((part) => part && breadcrumbSlug.includes(part));
  }

  function headingAgreesWithCompetition(fixtureResult, competitionEntry) {
    const headingSlug = CatalogueParser.slugify(fixtureResult.competition_heading_raw || '');
    const competitionSlug = CatalogueParser.slugify(competitionEntry.competition_slug || competitionEntry.label_raw || '');
    return !!headingSlug && !!competitionSlug && headingSlug === competitionSlug;
  }

  function contentAgreesWithCompetition(fixtureResult, competitionEntry) {
    return breadcrumbAgreesWithCompetition(fixtureResult, competitionEntry) &&
      headingAgreesWithCompetition(fixtureResult, competitionEntry);
  }

  function classifyParseResult(fixtureResult) {
    if (fixtureResult.fixture_row_count > 0) return 'populated';
    if (fixtureResult.empty_state_status === 'CONFIRMED_EMPTY') return 'confirmed_empty';
    return 'unknown_empty';
  }

  /**
   * Ensures the sport's own accordion is expanded and returns its
   * discovered groups. Safe to call repeatedly (e.g. to "reopen" after a
   * navigation) -- clicking an already-expanded toggle is the one
   * [UNVERIFIED] assumption here (see header comment): this sidebar has
   * no confirmed open/closed class to check first, so a caller that
   * knows the sport is already open should skip calling this again
   * rather than risk toggling it closed.
   */
  async function expandSportAndDiscoverGroups(doc, { sportId, sportSlug }) {
    const selectors = CatalogueParser.buildDiscoverySelectors(sportId, sportSlug);
    const toggle = doc.querySelector(selectors.sportToggle);
    if (!toggle) {
      return { ok: false, reason: 'SPORT_TOGGLE_NOT_FOUND', groups: [] };
    }
    let groups = CatalogueParser.parseGroupsFromDocument(doc, { sportId, sportSlug });
    if (groups.length === 0) {
      toggle.click();
      await waitFor(
        () => CatalogueParser.parseGroupsFromDocument(doc, { sportId, sportSlug }).length > 0,
        EXPAND_TIMEOUT_MS,
        POLL_INTERVAL_MS
      );
      groups = CatalogueParser.parseGroupsFromDocument(doc, { sportId, sportSlug });
    }
    if (groups.length === 0) {
      return { ok: false, reason: 'SPORT_EXPAND_TIMEOUT', groups: [] };
    }

    // "Show more" -- click only while the toggle's own current label
    // still contains "more" (operator's own rule), re-querying it fresh
    // after every click since the label itself changes.
    for (let i = 0; i < MAX_SHOW_MORE_CLICKS; i += 1) {
      const moreToggle = doc.querySelector(selectors.showMoreToggleOptional);
      if (!moreToggle || !CatalogueParser.needsMoreClick(text(moreToggle))) break;
      moreToggle.click();
      await waitFor(
        () => {
          const t = doc.querySelector(selectors.showMoreToggleOptional);
          return !t || !CatalogueParser.needsMoreClick(text(t));
        },
        SHOW_MORE_TIMEOUT_MS,
        POLL_INTERVAL_MS
      );
    }

    return { ok: true, reason: null, groups: CatalogueParser.parseGroupsFromDocument(doc, { sportId, sportSlug }) };
  }

  /**
   * Clicks one competition link, waits for its own page to settle, runs
   * `fixture_parser.js`, and validates the result -- never mutates
   * `results` itself, so the caller decides what to do with a mismatch.
   */
  async function visitCompetition(doc, { sportId, sportSlug, groupId, competitionEntry, now, oddsLoadTimeoutMs }) {
    const linkEl = findCompetitionLink(doc, sportId, sportSlug, groupId, competitionEntry.competition_id);
    if (!linkEl) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: null,
        observed_source_url_raw: null,
        observed_competition_heading_raw: null,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        attempted_click: false,
        failure_reason: 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME',
      };
    }

    // Bet9ja's SPA leaves earlier competition tables in the same document.
    // Snapshot them by node identity before the click so only rows from
    // tables added for THIS route can be admitted afterwards.
    const tablesBeforeClick = Array.from(doc.querySelectorAll('.sports-table'));
    const hrefBeforeClick = currentHref(doc);
    const breadcrumbBeforeClick = text(doc.querySelector('.sports-view__crumbs'));
    linkEl.click();

    const settled = await waitFor(() => {
      const parsed = FixtureParser.parseFixturesFromDocument(doc, {
        href: currentHref(doc),
        capturedAtUtc: now(),
        excludeTableElements: tablesBeforeClick,
      });
      const routeChanged =
        currentHref(doc) !== hrefBeforeClick ||
        text(doc.querySelector('.sports-view__crumbs')) !== breadcrumbBeforeClick ||
        parsed.fixture_row_count > 0;
      // Settling is about fresh content appearing after the click. The
      // identity decision is deliberately made below so a wrong
      // competition becomes an auditable mismatch, not a timeout.
      return routeChanged &&
        (parsed.fixture_row_count > 0 || parsed.empty_state_status === 'CONFIRMED_EMPTY');
    }, COMPETITION_LOAD_TIMEOUT_MS, POLL_INTERVAL_MS);

    // v2.1 identity settle: the SPA can update rows and breadcrumb before
    // the competition heading. Give the heading time to catch up before
    // the fail-closed identity check below. A heading that never updates
    // is still rejected as a mismatch.
    let identityWaitResult = 'NOT_NEEDED';
    if (settled) {
      const parseNow = () => FixtureParser.parseFixturesFromDocument(doc, {
        href: currentHref(doc), capturedAtUtc: now(), excludeTableElements: tablesBeforeClick,
      });
      if (!contentAgreesWithCompetition(parseNow(), competitionEntry)) {
        const agreed = await waitFor(
          () => contentAgreesWithCompetition(parseNow(), competitionEntry),
          IDENTITY_SETTLE_TIMEOUT_MS,
          POLL_INTERVAL_MS
        );
        identityWaitResult = agreed ? 'IDENTITY_SETTLED_AFTER_WAIT' : 'IDENTITY_TIMEOUT';
      }
    }

    // v2 odds wait: rows can appear before their prices. Poll until every
    // row has a fully priced market or the odds timeout expires. A row
    // that is still unpriced is recorded as such, never dropped.
    let oddsWaitResult = 'NOT_NEEDED';
    if (settled) {
      const reparse = () => FixtureParser.parseFixturesFromDocument(doc, {
        href: currentHref(doc), capturedAtUtc: now(), excludeTableElements: tablesBeforeClick,
      });
      const first = reparse();
      if (first.fixture_row_count > 0 && first.odds_coverage && first.odds_coverage.status !== 'ODDS_COMPLETE') {
        const complete = await waitFor(
          () => reparse().odds_coverage.status === 'ODDS_COMPLETE',
          Number.isFinite(oddsLoadTimeoutMs) ? oddsLoadTimeoutMs : ODDS_LOAD_TIMEOUT_MS,
          POLL_INTERVAL_MS
        );
        oddsWaitResult = complete ? 'ODDS_LOADED_AFTER_WAIT' : 'ODDS_TIMEOUT';
      }
    }

    const capturedAtUtc = now();
    const href = currentHref(doc);
    const fixtureResult = FixtureParser.parseFixturesFromDocument(doc, {
      href,
      capturedAtUtc,
      excludeTableElements: tablesBeforeClick,
    });
    // Specials Basketball has a player-market table and no standard
    // competition heading. Its final breadcrumb names the selected match.
    if (sportSlug === 'specials_basketball' && !fixtureResult.competition_heading_raw) {
      fixtureResult.competition_heading_raw = text(doc.querySelector('.sports-view__crumbs-item:last-child')) || null;
    }

    if (!settled) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_source_url_raw: href || null,
        observed_competition_heading_raw: fixtureResult.competition_heading_raw,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        attempted_click: true,
        failure_reason: 'COMPETITION_LOAD_TIMEOUT',
      };
    }

    if (!contentAgreesWithCompetition(fixtureResult, competitionEntry)) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_source_url_raw: href || null,
        observed_competition_heading_raw: fixtureResult.competition_heading_raw,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        attempted_click: true,
        failure_reason: 'COMPETITION_IDENTITY_DID_NOT_MATCH_REQUESTED_COMPETITION',
        observed_breadcrumb_raw: fixtureResult.breadcrumb_raw,
        identity_wait_result: identityWaitResult,
      };
    }

    if (sportSlug === 'specials_basketball' && fixtureResult.fixture_row_count > 0) {
      const marketRows = fixtureResult.fixtures.map((row) => {
        const marketEl = doc.getElementById(`prematch_event-${row.fixture_id}`);
        const container = marketEl && marketEl.closest('.table-f');
        const line = container && text(container.querySelector('.sports-table__odds .dropdown__btn'));
        const odds = {};
        for (const el of container ? container.querySelectorAll('[id*="_odds_market-"]') : []) {
          const sign = el.id.match(/_sign-([OU])$/);
          if (sign && text(el)) odds[sign[1] === 'O' ? 'over' : 'under'] = text(el);
        }
        return {
          market_event_id: row.fixture_id,
          player_raw: row.participant_1,
          line_raw: line || null,
          over_under_odds: Object.keys(odds).length ? odds : null,
        };
      });
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_source_url_raw: href || null,
        observed_competition_heading_raw: fixtureResult.competition_heading_raw,
        parse_result: 'populated',
        captured_at_utc: capturedAtUtc,
        odds_wait_result: oddsWaitResult,
        odds_coverage: {
          fixtures_total: marketRows.length,
          fixtures_with_priced_market: marketRows.filter((r) => r.over_under_odds).length,
          status: marketRows.every((r) => r.over_under_odds) ? 'ODDS_COMPLETE'
            : marketRows.some((r) => r.over_under_odds) ? 'ODDS_PARTIAL' : 'ODDS_MISSING',
        },
        fixtures: [{
          fixture_id: competitionEntry.competition_id,
          fixture_label_raw: competitionEntry.label_raw,
          date_text_raw: fixtureResult.fixtures[0].date_text_raw,
          kickoff_time_raw: fixtureResult.fixtures[0].kickoff_time_raw,
          market_rows: marketRows,
        }],
        attempted_click: true,
      };
    }

    return {
      competition_id: competitionEntry.competition_id,
      competition_label: competitionEntry.label_raw,
      source_url_after_click: href || null,
      observed_source_url_raw: href || null,
      observed_competition_heading_raw: fixtureResult.competition_heading_raw,
      parse_result: classifyParseResult(fixtureResult),
      captured_at_utc: capturedAtUtc,
      odds_wait_result: oddsWaitResult,
      odds_coverage: fixtureResult.odds_coverage,
      fixtures: fixtureResult.fixtures,
      attempted_click: true,
    };
  }

  async function openGroupAndDiscoverCompetitions(doc, { sportId, sportSlug, group }) {
    let entries = discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id);
    if (entries.length > 0) return { entries, reason: null };

    const groupToggle = findGroupToggle(doc, sportId, sportSlug, group.group_id, group.group_slug);
    if (!groupToggle) return { entries: [], reason: 'GROUP_TOGGLE_NOT_FOUND' };
    groupToggle.click();
    await waitFor(
      () => discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).length > 0,
      GROUP_EXPAND_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    entries = discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id);
    return { entries, reason: entries.length ? null : 'GROUP_LINKS_NOT_DISCOVERED' };
  }

  function currentHref(doc) {
    return (doc.defaultView && doc.defaultView.location && doc.defaultView.location.href) || '';
  }

  /**
   * @param {Document} doc
   * @param {{sportId: number, sportSlug: string, now?: () => string,
   *          maxGroups?: number, maxCompetitionsPerGroup?: number}} options
   */
  async function walkSport(doc, options) {
    const sportId = options.sportId;
    const sportSlug = options.sportSlug;
    const now = typeof options.now === 'function' ? options.now : () => new Date().toISOString();
    const maxGroups = Number.isFinite(options.maxGroups) ? options.maxGroups : Infinity;
    const maxCompetitionsPerGroup = Number.isFinite(options.maxCompetitionsPerGroup)
      ? options.maxCompetitionsPerGroup
      : Infinity;

    const results = [];
    const failures = [];
    let groupsSeen = 0;
    let competitionsSeen = 0;
    let competitionsAttempted = 0;
    let competitionsSuccessful = 0;
    let competitionsValidated = 0;

    const expanded = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
    if (!expanded.ok) {
      failures.push({ stage: 'EXPAND_SPORT', reason: expanded.reason });
      return {
        schema_version: SCHEMA_VERSION,
        sport: sportSlug,
        groups_seen: 0,
        competitions_seen: 0,
        competitions_attempted: 0,
        competitions_successful: 0,
        competitions_validated: 0,
        competitions_failed: 1,
        capture_status: 'PARTIAL',
        groups_discovered: 0,
        groups_failed: 0,
        results,
        failures,
      };
    }

    const groups = expanded.groups.slice(0, maxGroups);
    groupsSeen = groups.length;

    for (const group of groups) {
      // The previous group's final competition can leave the document on
      // a route page that contains only the sport root. Restore the
      // sport before opening every group, including the first one after
      // a completed group.
      const sportReadyForGroup = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
      if (!sportReadyForGroup.ok) {
        failures.push({ stage: 'REOPEN_SPORT', group_id: group.group_id, reason: sportReadyForGroup.reason });
        continue;
      }
      const opened = await openGroupAndDiscoverCompetitions(doc, { sportId, sportSlug, group });
      const competitionEntries = opened.entries;
      if (competitionEntries.length === 0) {
        failures.push({ stage: 'OPEN_GROUP', group_id: group.group_id, group_label: group.label_raw, reason: opened.reason });
        continue;
      }
      const plannedEntries = competitionEntries.slice(0, maxCompetitionsPerGroup);
      competitionsSeen += plannedEntries.length;

      for (const plannedEntry of plannedEntries) {
        // Freshly restore the sport/group and resolve this competition by
        // id. A group left open is detected first and is never clicked
        // closed.
        const reexpanded = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
        if (!reexpanded.ok) {
          failures.push({ stage: 'REOPEN_SPORT', reason: reexpanded.reason, after_competition_id: plannedEntry.competition_id });
          break;
        }
        const refreshed = await openGroupAndDiscoverCompetitions(doc, { sportId, sportSlug, group });
        const competitionEntry = refreshed.entries.find((entry) => entry.competition_id === plannedEntry.competition_id) || plannedEntry;
        const result = await visitCompetition(doc, { sportId, sportSlug, groupId: group.group_id, competitionEntry, now, oddsLoadTimeoutMs: options.oddsLoadTimeoutMs });
        results.push(result);
        competitionsAttempted += 1;
        if (result.attempted_click && !result.failure_reason) competitionsSuccessful += 1;
        if (result.parse_result === 'populated' || result.parse_result === 'confirmed_empty') competitionsValidated += 1;

        // "Missing competition" and "failed content validation" are both
        // explicit required failure-record types -- recorded here
        // alongside the per-competition `results[]` entry (which already
        // carries the fuller detail: which of the two happened, and any
        // observed breadcrumb) rather than instead of it.
        if (result.parse_result === 'invalid_content_mismatch') {
          failures.push({
            stage: result.failure_reason === 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME' ? 'MISSING_COMPETITION' : 'CONTENT_VALIDATION',
            competition_id: competitionEntry.competition_id,
            reason: result.failure_reason,
          });
        }

      }
    }

    // v2 odds roll-up across every validated competition.
    let oddsFixturesTotal = 0;
    let oddsFixturesPriced = 0;
    for (const r of results) {
      if (r.odds_coverage) {
        oddsFixturesTotal += r.odds_coverage.fixtures_total;
        oddsFixturesPriced += r.odds_coverage.fixtures_with_priced_market;
      }
    }
    const oddsShare = oddsFixturesTotal ? oddsFixturesPriced / oddsFixturesTotal : null;
    const oddsStatus = oddsFixturesTotal === 0 ? 'NO_FIXTURES'
      : oddsFixturesPriced === 0 ? 'ODDS_MISSING'
        : oddsFixturesPriced === oddsFixturesTotal ? 'ODDS_COMPLETE' : 'ODDS_PARTIAL';
    const structurallyComplete = !(failures.length || groups.length < expanded.groups.length ||
      results.length < competitionsSeen || Number.isFinite(maxCompetitionsPerGroup));
    // COMPLETE now requires prices, not just rows: a walk that found
    // fixtures but no odds is ODDS_MISSING, never COMPLETE.
    const captureStatus = !structurallyComplete ? 'PARTIAL'
      : oddsStatus === 'ODDS_MISSING' ? 'ODDS_MISSING'
        : oddsShare !== null && oddsShare < ODDS_COMPLETE_MIN_SHARE ? 'PARTIAL' : 'COMPLETE';

    let browserTimezone = null;
    try { browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (e) { browserTimezone = null; }

    return {
      schema_version: SCHEMA_VERSION,
      sport: sportSlug,
      captured_at_utc: now(),
      // Kickoff times are shown as Bet9ja renders them. Which timezone
      // that is has not been confirmed; these record the browser's own
      // zone so the analyst can resolve it. [UNVERIFIED]
      browser_timezone: browserTimezone,
      browser_utc_offset_minutes: -new Date().getTimezoneOffset(),
      odds_status: oddsStatus,
      odds_fixtures_total: oddsFixturesTotal,
      odds_fixtures_priced: oddsFixturesPriced,
      groups_seen: groupsSeen,
      competitions_seen: competitionsSeen,
      competitions_attempted: competitionsAttempted,
      competitions_successful: competitionsSuccessful,
      competitions_validated: competitionsValidated,
      competitions_failed: failures.length,
      capture_status: captureStatus,
      groups_discovered: expanded.groups.length,
      groups_failed: failures.filter((failure) => failure.stage === 'OPEN_GROUP' || failure.stage === 'REOPEN_SPORT').length,
      results,
      failures,
    };
  }

  const api = {
    walkSport,
    breadcrumbAgreesWithCompetition,
    classifyParseResult,
    SCHEMA_VERSION,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaAllSportsWalker = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
