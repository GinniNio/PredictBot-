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

  const SCHEMA_VERSION = 'bet9ja-allsports-sport-walk.v3';

  // [UNVERIFIED] -- see header comment. Generous placeholders, never
  // presented as evidence-based.
  const POLL_INTERVAL_MS = 25;
  const EXPAND_TIMEOUT_MS = 3000;
  const SHOW_MORE_TIMEOUT_MS = 3000;
  const GROUP_EXPAND_TIMEOUT_MS = 3000;
  const COMPETITION_LOAD_TIMEOUT_MS = 5000;
  const ODDS_LOAD_TIMEOUT_MS = 10000;
  const IDENTITY_SETTLE_TIMEOUT_MS = 5000;
  const MAX_SHOW_MORE_CLICKS = 10;
  const SPORT_MARKET_ARITY = {
    soccer: { '1x2': 3, '3way': 3 },
    futsal: { '1x2': 3, '3way': 3 },
    snooker: { '2_way': 2, match_winner: 2 },
    tennis: { '2_way': 2, match_winner: 2 },
    mma: { '2_way': 2, match_winner: 2 },
    specials_combo: { to_happen: 1 },
  };

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

  function lastBreadcrumbLabel(doc) {
    const item = text(doc.querySelector('.sports-view__crumbs-item:last-child'));
    return item || text(doc.querySelector('.sports-view__crumbs')).split('>').at(-1).trim() || null;
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

  function expectedSelectionCount(sportSlug, market) {
    const code = (market.market_code_raw || '').toLowerCase();
    const label = (market.market_raw || '').toLowerCase();
    if (SPORT_MARKET_ARITY[sportSlug]?.[code]) return SPORT_MARKET_ARITY[sportSlug][code];
    if (/over_under|total|under_over/.test(code) || /over\/under|total/.test(label)) return 2;
    if (/3way|1x2|three_way/.test(code) || /1x2|3way/.test(label)) return 3;
    if (/2_way|moneyline/.test(code) || /2 way/.test(label)) return 2;
    if (/match_winner/.test(code) || /match winner/.test(label)) return null;
    if (/handicap|draw_no_bet/.test(code)) return 2;
    if (['tennis', 'snooker', 'mma'].includes(sportSlug) && /winner|main/i.test(label)) return 2;
    if (['soccer', 'futsal'].includes(sportSlug) && /main|full time/i.test(label)) return 3;
    return null;
  }

  function annotateMarketCoverage(fixtures, sportSlug) {
    for (const fixture of fixtures) {
      fixture.odds_flags = [];
      for (const market of fixture.markets || []) {
        const expected = expectedSelectionCount(sportSlug, market);
        if (expected !== null && market.selections.length !== expected) {
          market.validation_flags = ['ARITY_MISMATCH'];
          fixture.odds_flags.push('ARITY_MISMATCH');
        } else market.validation_flags = [];
      }
      const priced = (fixture.markets || []).some((market) =>
        market.selections.length > 0 && !market.validation_flags.length &&
        market.selections.every((selection) => selection.state === 'open' &&
          typeof selection.odds === 'number' && selection.odds > 1.01));
      fixture.has_priced_market = priced;
      if (!priced) fixture.odds_flags.push('NO_PRICED_MARKET');
      fixture.odds_flags = [...new Set(fixture.odds_flags)];
    }
  }

  function pageTimezone(doc, atUtc) {
    const raw = text(doc.querySelector('.toolbar__timezone'));
    const zone = raw.match(/\b([A-Za-z_]+\/[A-Za-z_]+)\b/)?.[1] || null;
    if (!zone) return { page_timezone: null, page_utc_offset: null, page_timezone_raw: raw || null };
    try {
      const part = new Intl.DateTimeFormat('en', { timeZone: zone, timeZoneName: 'shortOffset' })
        .formatToParts(new Date(atUtc)).find((p) => p.type === 'timeZoneName')?.value;
      const match = part && part.match(/^GMT([+-])(\d{1,2})(?::(\d{2}))?$/);
      const offset = part === 'GMT' ? '+00:00' : match ?
        `${match[1]}${match[2].padStart(2, '0')}:${match[3] || '00'}` : null;
      return { page_timezone: zone, page_utc_offset: offset, page_timezone_raw: raw };
    } catch (_) { return { page_timezone: null, page_utc_offset: null, page_timezone_raw: raw }; }
  }

  function kickoffUtc(fixture, capturedAtUtc, offset) {
    const date = fixture.date_text_raw?.match(/(?:\b\w+\s+)?(\d{1,2})\s+([A-Za-z]{3})\b/);
    const time = fixture.kickoff_time_raw?.match(/^(\d{1,2}):(\d{2})$/);
    if (!date || !time || !offset) return null;
    const month = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'].indexOf(date[2]);
    if (month < 0) return null;
    const baseYear = new Date(capturedAtUtc).getUTCFullYear();
    const minutes = (offset[0] === '-' ? -1 : 1) * (Number(offset.slice(1, 3)) * 60 + Number(offset.slice(4, 6)));
    const candidates = [baseYear - 1, baseYear, baseYear + 1].map((year) =>
      Date.UTC(year, month, Number(date[1]), Number(time[1]), Number(time[2])) - minutes * 60000);
    const target = Date.parse(capturedAtUtc);
    const chosen = candidates.sort((a, b) => Math.abs(a - target) - Math.abs(b - target))[0];
    return Math.abs(chosen - target) <= 183 * 86400000 ? new Date(chosen).toISOString() : null;
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
    let toggle = doc.querySelector(selectors.sportToggle);
    if (!toggle) {
      await waitFor(() => !!doc.querySelector(selectors.sportToggle), EXPAND_TIMEOUT_MS, POLL_INTERVAL_MS);
      toggle = doc.querySelector(selectors.sportToggle);
    }
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
  async function visitCompetition(doc, { sportId, sportSlug, groupId, competitionEntry, now, oddsWaitMs, identityWaitMs }) {
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

    const capturedAtUtc = now();
    const href = currentHref(doc);
    let fixtureResult = FixtureParser.parseFixturesFromDocument(doc, {
      href,
      capturedAtUtc,
      excludeTableElements: tablesBeforeClick,
    });
    // Specials Basketball has a player-market table and no standard
    // competition heading. Its final breadcrumb names the selected match.
    if ((sportSlug === 'specials_basketball' && !fixtureResult.competition_heading_raw) ||
        fixtureResult.empty_state_status === 'CONFIRMED_EMPTY') {
      fixtureResult.competition_heading_raw = lastBreadcrumbLabel(doc);
    }

    if (settled && fixtureResult.fixture_row_count && !fixtureResult.fixtures.some((fixture) =>
      fixture.markets.some((market) => market.selections.some((selection) => selection.odds !== null)))) {
      await waitFor(() => {
        const refreshed = FixtureParser.parseFixturesFromDocument(doc, {
          href: currentHref(doc), capturedAtUtc: now(), excludeTableElements: tablesBeforeClick,
        });
        return refreshed.fixtures.some((fixture) => fixture.markets.some((market) =>
          market.selections.some((selection) => selection.odds !== null)));
      }, oddsWaitMs, POLL_INTERVAL_MS);
      fixtureResult = FixtureParser.parseFixturesFromDocument(doc, {
        href: currentHref(doc), capturedAtUtc: now(), excludeTableElements: tablesBeforeClick,
      });
      if ((sportSlug === 'specials_basketball' && !fixtureResult.competition_heading_raw) ||
          fixtureResult.empty_state_status === 'CONFIRMED_EMPTY') {
        fixtureResult.competition_heading_raw = lastBreadcrumbLabel(doc);
      }
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
        raw_page_snippet: (doc.querySelector('.sports-view__crumbs')?.closest('.sports-view') ||
          doc.querySelector('.sports-view'))?.outerHTML.slice(0, 20000) || null,
      };
    }

    if (!contentAgreesWithCompetition(fixtureResult, competitionEntry) && identityWaitMs > 0) {
      await waitFor(() => {
        const candidate = FixtureParser.parseFixturesFromDocument(doc, {
          href: currentHref(doc), capturedAtUtc: now(), excludeTableElements: tablesBeforeClick,
        });
        if ((sportSlug === 'specials_basketball' && !candidate.competition_heading_raw) ||
            candidate.empty_state_status === 'CONFIRMED_EMPTY') {
          candidate.competition_heading_raw = lastBreadcrumbLabel(doc);
        }
        if (contentAgreesWithCompetition(candidate, competitionEntry)) {
          fixtureResult = candidate;
          return true;
        }
        return false;
      }, identityWaitMs, POLL_INTERVAL_MS);
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
      };
    }

    if (sportSlug === 'specials_basketball' && fixtureResult.fixture_row_count > 0) {
      const markets = fixtureResult.fixtures.flatMap((row) => row.markets.map((market) => ({
        ...market, market_event_id: row.fixture_id, player_raw: row.participant_1,
      })));
      const fixtures = [{
        fixture_id: competitionEntry.competition_id,
        fixture_label_raw: competitionEntry.label_raw,
        date_text_raw: fixtureResult.fixtures[0].date_text_raw,
        kickoff_time_raw: fixtureResult.fixtures[0].kickoff_time_raw,
        markets,
      }];
      annotateMarketCoverage(fixtures, sportSlug);
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_source_url_raw: href || null,
        observed_competition_heading_raw: fixtureResult.competition_heading_raw,
        parse_result: 'populated',
        price_captured_at_utc: now(),
        fixtures,
        attempted_click: true,
      };
    }

    annotateMarketCoverage(fixtureResult.fixtures, sportSlug);
    return {
      competition_id: competitionEntry.competition_id,
      competition_label: competitionEntry.label_raw,
      source_url_after_click: href || null,
      observed_source_url_raw: href || null,
      observed_competition_heading_raw: fixtureResult.competition_heading_raw,
      parse_result: classifyParseResult(fixtureResult),
      price_captured_at_utc: now(),
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
    const capturedAtUtc = now();
    const timezone = pageTimezone(doc, capturedAtUtc);
    const oddsWaitMs = Number.isFinite(options.oddsWaitMs) ? options.oddsWaitMs : ODDS_LOAD_TIMEOUT_MS;
    const identityWaitMs = Number.isFinite(options.identityWaitMs) ? options.identityWaitMs : IDENTITY_SETTLE_TIMEOUT_MS;
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
        capture_started_at_utc: capturedAtUtc,
        captured_at_utc: now(),
        ...timezone,
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
        const result = await visitCompetition(doc, { sportId, sportSlug, groupId: group.group_id, competitionEntry, now, oddsWaitMs, identityWaitMs });
        for (const fixture of result.fixtures) fixture.kickoff_utc = kickoffUtc(fixture, capturedAtUtc, timezone.page_utc_offset);
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

    const fixtures = results.flatMap((result) => result.fixtures);
    const oddsEligible = fixtures;
    const pricedCount = oddsEligible.filter((fixture) => fixture.has_priced_market).length;
    const pricedRatio = oddsEligible.length ? pricedCount / oddsEligible.length : null;
    const discoveryPartial = failures.length || groups.length < expanded.groups.length ||
      results.length < competitionsSeen || Number.isFinite(maxCompetitionsPerGroup) ||
      results.some((result) => result.parse_result === 'unknown_empty');
    return {
      schema_version: SCHEMA_VERSION,
      sport: sportSlug,
      capture_started_at_utc: capturedAtUtc,
      captured_at_utc: now(),
      ...timezone,
      groups_seen: groupsSeen,
      competitions_seen: competitionsSeen,
      competitions_attempted: competitionsAttempted,
      competitions_successful: competitionsSuccessful,
      competitions_validated: competitionsValidated,
      competitions_failed: failures.length,
      capture_status: pricedRatio === 0 && oddsEligible.length ? 'ODDS_MISSING' :
        discoveryPartial || (pricedRatio !== null && pricedRatio < 0.9) ? 'PARTIAL' : 'COMPLETE',
      fixtures_with_priced_market: pricedCount,
      fixtures_with_any_price: oddsEligible.filter((fixture) =>
        (fixture.markets || []).some((market) => market.selections.some((selection) => selection.odds !== null))).length,
      fixtures_total: oddsEligible.length,
      priced_fixture_ratio: pricedRatio,
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
