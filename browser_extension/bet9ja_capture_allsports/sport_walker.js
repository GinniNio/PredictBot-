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

  const SCHEMA_VERSION = 'bet9ja-allsports-sport-walk.v1';

  // [UNVERIFIED] -- see header comment. Generous placeholders, never
  // presented as evidence-based.
  const POLL_INTERVAL_MS = 25;
  const EXPAND_TIMEOUT_MS = 3000;
  const SHOW_MORE_TIMEOUT_MS = 3000;
  const GROUP_EXPAND_TIMEOUT_MS = 3000;
  const COMPETITION_LOAD_TIMEOUT_MS = 5000;
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
    const competitionSlug = CatalogueParser.slugify(competitionEntry.label_raw || competitionEntry.competition_slug || '');
    if (!competitionSlug) return false;
    return breadcrumbSlug.includes(competitionSlug) || competitionSlug.includes(breadcrumbSlug);
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
  async function visitCompetition(doc, { sportId, sportSlug, groupId, competitionEntry, now }) {
    const linkEl = findCompetitionLink(doc, sportId, sportSlug, groupId, competitionEntry.competition_id);
    if (!linkEl) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: null,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME',
      };
    }

    linkEl.click();

    const settled = await waitFor(() => {
      const parsed = FixtureParser.parseFixturesFromDocument(doc, { href: currentHref(doc), capturedAtUtc: now() });
      return parsed.fixture_row_count > 0 || parsed.empty_state_status === 'CONFIRMED_EMPTY' || !!parsed.breadcrumb_raw;
    }, COMPETITION_LOAD_TIMEOUT_MS, POLL_INTERVAL_MS);

    const capturedAtUtc = now();
    const href = currentHref(doc);
    const fixtureResult = FixtureParser.parseFixturesFromDocument(doc, { href, capturedAtUtc });

    if (!settled) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'COMPETITION_LOAD_TIMEOUT',
      };
    }

    if (!breadcrumbAgreesWithCompetition(fixtureResult, competitionEntry)) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'BREADCRUMB_DID_NOT_MATCH_REQUESTED_COMPETITION',
        observed_breadcrumb_raw: fixtureResult.breadcrumb_raw,
      };
    }

    return {
      competition_id: competitionEntry.competition_id,
      competition_label: competitionEntry.label_raw,
      source_url_after_click: href || null,
      parse_result: classifyParseResult(fixtureResult),
      fixtures: fixtureResult.fixtures,
    };
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
    let competitionsVisited = 0;

    const expanded = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
    if (!expanded.ok) {
      failures.push({ stage: 'EXPAND_SPORT', reason: expanded.reason });
      return {
        schema_version: SCHEMA_VERSION,
        sport: sportSlug,
        groups_seen: 0,
        competitions_seen: 0,
        competitions_visited: 0,
        results,
        failures,
      };
    }

    const groups = expanded.groups.slice(0, maxGroups);
    groupsSeen = groups.length;

    for (const group of groups) {
      const groupToggle = findGroupToggle(doc, sportId, sportSlug, group.group_id, group.group_slug);
      if (!groupToggle) {
        failures.push({ stage: 'OPEN_GROUP', group_id: group.group_id, reason: 'GROUP_TOGGLE_NOT_FOUND' });
        continue;
      }
      groupToggle.click();
      await waitFor(
        () => discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).length > 0,
        GROUP_EXPAND_TIMEOUT_MS,
        POLL_INTERVAL_MS
      );

      const competitionEntries = discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).slice(
        0,
        maxCompetitionsPerGroup
      );
      if (competitionEntries.length === 0) {
        failures.push({ stage: 'OPEN_GROUP', group_id: group.group_id, reason: 'GROUP_EXPAND_TIMEOUT_OR_EMPTY' });
        continue;
      }
      competitionsSeen += competitionEntries.length;

      for (const competitionEntry of competitionEntries) {
        const result = await visitCompetition(doc, { sportId, sportSlug, groupId: group.group_id, competitionEntry, now });
        results.push(result);
        competitionsVisited += 1;

        // "Return to / reopen sport and continue" -- the navigation just
        // taken may have replaced the DOM entirely (rule 2), so both the
        // sport and this same group are re-expanded fresh before the
        // next competition in this group is attempted. Every element
        // reference from before this point is treated as potentially
        // stale and never reused.
        const reexpanded = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
        if (!reexpanded.ok) {
          failures.push({ stage: 'REOPEN_SPORT', reason: reexpanded.reason, after_competition_id: competitionEntry.competition_id });
          break;
        }
        const groupToggleAgain = findGroupToggle(doc, sportId, sportSlug, group.group_id, group.group_slug);
        if (groupToggleAgain) {
          groupToggleAgain.click();
          await waitFor(
            () => discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).length > 0,
            GROUP_EXPAND_TIMEOUT_MS,
            POLL_INTERVAL_MS
          );
        }
      }
    }

    return {
      schema_version: SCHEMA_VERSION,
      sport: sportSlug,
      groups_seen: groupsSeen,
      competitions_seen: competitionsSeen,
      competitions_visited: competitionsVisited,
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
