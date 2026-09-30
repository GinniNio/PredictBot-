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
 *
 * TWO REAL BUGS FOUND FROM A LIVE 34-COMPETITION ICE HOCKEY WALK
 * (2026-09-30T17:00:14Z, the first real run against an authenticated
 * session):
 *
 * 1. **Stale-content accumulation.** Bet9ja's own SPA does NOT remove a
 *    competition's `.sports-table` blocks when navigating to a different
 *    one within the same walk -- they pile up (a rolling window, not
 *    unbounded: later competitions' fixture counts fluctuated rather
 *    than growing forever, but each one's own set kept mixing in earlier
 *    competitions' real fixtures). Confirmed by direct analysis of that
 *    run's own output: e.g. "Alps Hockey League" reported 20 fixtures,
 *    14 genuinely its own plus the exact same 6 fixtures already
 *    reported under "NHL" moments earlier. `visitCompetition` now
 *    snapshots which `.sports-table` elements exist BEFORE a click and
 *    calls `fixture_parser.js`'s `parseFixturesFromTables` on only the
 *    ones that are NEW after it -- never the whole-document
 *    `parseFixturesFromDocument`, which mixes in every earlier
 *    competition's still-present tables.
 * 2. **Group-toggle click was a blind toggle, not an "ensure open".**
 *    The same real run's failures (10 of 34, all `MISSING_COMPETITION`)
 *    matched a clean pattern: almost every one was the competition
 *    immediately following a successful one in the same group (e.g. USA:
 *    NHL succeeded, AHL failed; Russia: KHL succeeded, VHL failed, MHL
 *    succeeded). This is exactly what happens if a group's own toggle is
 *    a real accordion click (open<->closed) and the "reopen" step clicks
 *    it unconditionally: after the first competition, the still-OPEN
 *    group gets toggled CLOSED instead of "ensured open", so the next
 *    competition's link genuinely isn't there; the failure after THAT
 *    one's own reopen-click toggles it back open again, explaining the
 *    open/fail/open/fail/open rhythm. `ensureGroupOpen` now checks
 *    whether the group's competition links already exist before ever
 *    clicking its toggle, both on first open and on every reopen.
 *
 * FIVE FURTHER REQUIREMENTS applied on top of the above (same review):
 *
 * 1. **Separate success/failure counts.** `competitions_visited` is
 *    renamed `competitions_attempted`; `competitions_successful` and
 *    `competitions_failed` are reported alongside it (an "attempt" that
 *    hit `invalid_content_mismatch` counts as failed, everything else --
 *    populated/confirmed_empty/unknown_empty -- as successful).
 * 2. **Rediscover a group's competition links before every click, never
 *    reuse the list frozen at group-open time.** This sharpens bug #2
 *    above: even with `ensureGroupOpen` fixed, a group's own rendered
 *    link SET can genuinely change between visits (a competition can
 *    stop being offered, or start being offered, independent of the
 *    open/closed toggle state) -- the exact root cause the operator
 *    traced the original run's 10 `COMPETITION_LINK_NOT_FOUND_AT_CLICK_
 *    TIME` failures to. The walker now re-runs discovery on every loop
 *    iteration and tracks progress by stable `competition_id`, never a
 *    frozen array or array position. A competition seen in an earlier
 *    pass but absent by the time its own turn comes up is still recorded
 *    (`MISSING_COMPETITION` in `failures[]`) rather than silently
 *    dropped -- it just never wastes a click attempting something that
 *    is confirmed, right now, not to be there.
 * 3. **Table-level validation beyond breadcrumb/URL.** This sidebar has
 *    no confirmed selector for a competition heading distinct from the
 *    breadcrumb, so a literal "heading" check isn't implemented --
 *    instead, a shared `fixtureOwners` map tracks which competition each
 *    fixture id was first attributed to across the WHOLE walk. A newly-
 *    scoped fixture whose id is already owned by a DIFFERENT competition
 *    is rejected as `invalid_content_mismatch`
 *    (`STALE_FIXTURE_ID_REUSED_FROM_EARLIER_COMPETITION`), with zero
 *    fixtures ingested -- real, evidence-grounded defense-in-depth on
 *    top of the table-diffing fix, not a substitute for a heading
 *    selector this project doesn't have evidence for yet.
 * 4. **Audit fields on every result.** `observed_breadcrumb_raw` and
 *    `source_url_after_click` are now present on every `results[]` entry
 *    -- success or failure -- not only on a breadcrumb mismatch.
 * 5. **3way odds stay nullable, never assumed.** Unchanged by this
 *    review, confirmed still correct: `fixture_parser.js`'s
 *    `parseThreeWayOdds` only ever returns a value when the exact
 *    `_odds_market-3way_sign-` ids are actually present in the DOM: the
 *    live 34-competition Ice Hockey walk's own real output shows
 *    `three_way_odds: null` on every single fixture, since Ice Hockey
 *    never renders that market by default and this walker never clicks
 *    a market-tab control to try to force it into view.
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
   * Ensures one group's own competition links are visible WITHOUT
   * assuming its current open/closed state -- checks first, and only
   * clicks the group's toggle if its links aren't already there. See
   * header comment ("TWO REAL BUGS...", #2): clicking unconditionally
   * toggles an already-open group CLOSED, which was confirmed as the
   * real cause of a clean succeed/fail/succeed failure pattern in a live
   * run.
   */
  async function ensureGroupOpen(doc, sportId, sportSlug, group) {
    if (discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).length > 0) {
      return { ok: true, reason: null };
    }
    const groupToggle = findGroupToggle(doc, sportId, sportSlug, group.group_id, group.group_slug);
    if (!groupToggle) {
      return { ok: false, reason: 'GROUP_TOGGLE_NOT_FOUND' };
    }
    groupToggle.click();
    const settled = await waitFor(
      () => discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id).length > 0,
      GROUP_EXPAND_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    return settled ? { ok: true, reason: null } : { ok: false, reason: 'GROUP_EXPAND_TIMEOUT_OR_EMPTY' };
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

  function currentTables(doc) {
    return Array.from(doc.querySelectorAll('.sports-table'));
  }

  /**
   * Clicks one competition link, waits for its own page to settle, runs
   * `fixture_parser.js` SCOPED to only the `.sports-table` elements that
   * are new since before this click (see header comment, bug #1 -- old
   * competitions' tables are never removed by Bet9ja's own SPA), and
   * validates the result -- never mutates `results` itself, so the
   * caller decides what to do with a mismatch.
   *
   * `fixtureOwners` is a shared `Map<fixture_id, competition_id>`
   * threaded through the whole walk. This project has no confirmed
   * selector for a table-level competition heading distinct from the
   * breadcrumb (the only confirmed heading-like text on this sidebar IS
   * the breadcrumb) -- as a real, evidence-grounded substitute for that
   * check, a newly-scoped fixture whose id was already attributed to a
   * DIFFERENT, earlier competition in this same walk is treated as
   * contamination and rejected, on the theory that two genuinely
   * different competitions never legitimately share a fixture id. This
   * is a defense-in-depth check on top of the table-diffing fix, not a
   * replacement for it.
   *
   * Every result -- success or failure -- carries `observed_breadcrumb_raw`
   * and `source_url_after_click` for audit, per the operator's own rule.
   */
  async function visitCompetition(doc, { sportId, sportSlug, groupId, competitionEntry, now, fixtureOwners }) {
    const linkEl = findCompetitionLink(doc, sportId, sportSlug, groupId, competitionEntry.competition_id);
    if (!linkEl) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: null,
        observed_breadcrumb_raw: null,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME',
      };
    }

    const tablesBefore = new Set(currentTables(doc));
    linkEl.click();

    // "Settled" means either genuinely NEW content arrived, or the page
    // is confirmed-empty, or its breadcrumb already agrees with the
    // requested competition -- never just "some fixture_row_count > 0
    // somewhere in the whole document", which would be satisfied
    // instantly by stale tables left over from an earlier competition
    // (bug #1) and never actually wait for this click's own result.
    const settled = await waitFor(() => {
      const newTables = currentTables(doc).filter((t) => !tablesBefore.has(t));
      if (newTables.length > 0) return true;
      const wholeDoc = FixtureParser.parseFixturesFromDocument(doc, { href: currentHref(doc), capturedAtUtc: now() });
      return wholeDoc.empty_state_status === 'CONFIRMED_EMPTY' || breadcrumbAgreesWithCompetition(wholeDoc, competitionEntry);
    }, COMPETITION_LOAD_TIMEOUT_MS, POLL_INTERVAL_MS);

    const capturedAtUtc = now();
    const href = currentHref(doc);
    // Breadcrumb/empty-marker are single, correctly-updated elements
    // (confirmed by the same live run -- every result's breadcrumb-based
    // validation matched its own actually-clicked competition, even
    // while fixture tables were accumulating) -- reading them
    // whole-document is fine. Fixture ROWS are not: only newly-added
    // tables are trusted for those.
    const wholeDocResult = FixtureParser.parseFixturesFromDocument(doc, { href, capturedAtUtc });
    const newTables = currentTables(doc).filter((t) => !tablesBefore.has(t));
    const scopedResult = FixtureParser.parseFixturesFromTables(doc, newTables, { href, capturedAtUtc });
    const observedBreadcrumbRaw = wholeDocResult.breadcrumb_raw;

    if (!settled) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_breadcrumb_raw: observedBreadcrumbRaw,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'COMPETITION_LOAD_TIMEOUT',
      };
    }

    if (!breadcrumbAgreesWithCompetition(wholeDocResult, competitionEntry)) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_breadcrumb_raw: observedBreadcrumbRaw,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'BREADCRUMB_DID_NOT_MATCH_REQUESTED_COMPETITION',
      };
    }

    // Table-level contamination check (see this function's own header
    // comment for why fixture-id ownership stands in for a heading
    // check no confirmed selector exists for yet).
    const contaminatingId = scopedResult.fixtures
      .map((f) => f.fixture_id)
      .find((id) => fixtureOwners.has(id) && fixtureOwners.get(id) !== competitionEntry.competition_id);
    if (contaminatingId) {
      return {
        competition_id: competitionEntry.competition_id,
        competition_label: competitionEntry.label_raw,
        source_url_after_click: href || null,
        observed_breadcrumb_raw: observedBreadcrumbRaw,
        parse_result: 'invalid_content_mismatch',
        fixtures: [],
        failure_reason: 'STALE_FIXTURE_ID_REUSED_FROM_EARLIER_COMPETITION',
      };
    }
    for (const fixture of scopedResult.fixtures) {
      fixtureOwners.set(fixture.fixture_id, competitionEntry.competition_id);
    }

    return {
      competition_id: competitionEntry.competition_id,
      competition_label: competitionEntry.label_raw,
      source_url_after_click: href || null,
      observed_breadcrumb_raw: observedBreadcrumbRaw,
      parse_result: classifyParseResult(scopedResult),
      fixtures: scopedResult.fixtures,
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
    // Fixture ownership shared across the WHOLE walk -- see
    // visitCompetition's own comment on why this stands in for a
    // table-level heading check.
    const fixtureOwners = new Map();
    let groupsSeen = 0;
    let competitionsSeen = 0;
    let competitionsAttempted = 0;
    let competitionsSuccessful = 0;
    let competitionsFailed = 0;

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
        competitions_failed: 0,
        results,
        failures,
      };
    }

    const groups = expanded.groups.slice(0, maxGroups);
    groupsSeen = groups.length;

    for (const group of groups) {
      const opened = await ensureGroupOpen(doc, sportId, sportSlug, group);
      if (!opened.ok) {
        failures.push({ stage: 'OPEN_GROUP', group_id: group.group_id, reason: opened.reason });
        continue;
      }

      // Rediscovered fresh on every iteration -- see visitCompetition's
      // own comment and this function's header note (bug #2, real
      // evidence: a frozen list taken once, before any clicks, produced
      // 10/34 COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME failures in a live
      // run once the group's own rendered link set had moved on).
      // `visitedCompetitionIds` tracks progress by stable id, never
      // array position; `allSeenCompetitionIds` accumulates every id
      // this group has EVER shown across all rediscoveries, for an
      // honest `competitions_seen`.
      const visitedCompetitionIds = new Set();
      const allSeenCompetitionIds = new Set();
      let groupBroken = false;
      // Bounds an otherwise-open while loop against a pathological case
      // (e.g. a group that never stops offering "new" ids) -- far above
      // any plausible real competition count per group.
      const GROUP_SAFETY_CAP = 500;
      let guard = 0;

      while (visitedCompetitionIds.size < maxCompetitionsPerGroup && guard < GROUP_SAFETY_CAP) {
        guard += 1;
        const current = discoverCompetitionLinksForGroup(doc, sportId, sportSlug, group.group_id);
        for (const c of current) allSeenCompetitionIds.add(c.competition_id);
        const competitionEntry = current.find((c) => !visitedCompetitionIds.has(c.competition_id));
        if (!competitionEntry) break;
        visitedCompetitionIds.add(competitionEntry.competition_id);

        const result = await visitCompetition(doc, {
          sportId,
          sportSlug,
          groupId: group.group_id,
          competitionEntry,
          now,
          fixtureOwners,
        });
        results.push(result);
        competitionsAttempted += 1;

        // "Missing competition" and "failed content validation" are both
        // explicit required failure-record types -- recorded here
        // alongside the per-competition `results[]` entry (which already
        // carries the fuller detail: which of the two happened, and any
        // observed breadcrumb) rather than instead of it.
        if (result.parse_result === 'invalid_content_mismatch') {
          competitionsFailed += 1;
          failures.push({
            stage: result.failure_reason === 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME' ? 'MISSING_COMPETITION' : 'CONTENT_VALIDATION',
            competition_id: competitionEntry.competition_id,
            reason: result.failure_reason,
          });
        } else {
          competitionsSuccessful += 1;
        }

        // "Return to / reopen sport and continue" -- the navigation just
        // taken may have replaced the DOM entirely (rule 2), so both the
        // sport and this same group are re-expanded fresh before the
        // next competition in this group is attempted. Every element
        // reference from before this point is treated as potentially
        // stale and never reused.
        const reexpanded = await expandSportAndDiscoverGroups(doc, { sportId, sportSlug });
        if (!reexpanded.ok) {
          failures.push({ stage: 'REOPEN_SPORT', reason: reexpanded.reason, after_competition_id: competitionEntry.competition_id });
          groupBroken = true;
          break;
        }
        const reopenedGroup = await ensureGroupOpen(doc, sportId, sportSlug, group);
        if (!reopenedGroup.ok) {
          failures.push({
            stage: 'REOPEN_GROUP',
            group_id: group.group_id,
            reason: reopenedGroup.reason,
            after_competition_id: competitionEntry.competition_id,
          });
          groupBroken = true;
          break;
        }
      }

      competitionsSeen += allSeenCompetitionIds.size;
      if (allSeenCompetitionIds.size === 0) {
        failures.push({ stage: 'OPEN_GROUP', group_id: group.group_id, reason: 'GROUP_EXPAND_TIMEOUT_OR_EMPTY' });
      }
      // Re-discovery (see above) means a competition seen in an EARLIER
      // pass but absent from every later one is simply never attempted
      // -- correct (no wasted click on something that isn't there
      // anymore), but silently skipping it would hide real information.
      // Record it explicitly: it was real evidence at some point in this
      // walk, then genuinely disappeared before its own turn came up.
      for (const seenId of allSeenCompetitionIds) {
        if (!visitedCompetitionIds.has(seenId)) {
          failures.push({
            stage: 'MISSING_COMPETITION',
            group_id: group.group_id,
            competition_id: seenId,
            reason: 'COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME',
          });
        }
      }
      if (groupBroken) {
        // A structural failure (sport/group failed to reopen) stops
        // THIS group -- the outer loop still tries the next one fresh.
        continue;
      }
    }

    return {
      schema_version: SCHEMA_VERSION,
      sport: sportSlug,
      groups_seen: groupsSeen,
      competitions_seen: competitionsSeen,
      competitions_attempted: competitionsAttempted,
      competitions_successful: competitionsSuccessful,
      competitions_failed: competitionsFailed,
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
