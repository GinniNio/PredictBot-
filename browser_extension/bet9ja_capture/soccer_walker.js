/**
 * Bet9ja Soccer ALL-COMPETITIONS fixture walker -- automates the manual
 * "click every competition, copy its fixtures" workflow the daily
 * intake step used to require. Pure orchestration: every fixture actually
 * comes from parser.js's own `captureFromDocument`, called once per
 * selected competition -- this file never re-implements fixture parsing,
 * only the navigation and aggregation around it.
 *
 * ROUND 2 REWRITE (2026-09-12) -- SUPERSEDES ROUND 1 ENTIRELY.
 *
 * Round 1 (`.menu-list.mt30` discovery, `/sport/soccer/1` /
 * `/sportPage/1/coupons` as starting routes, `history.back()` to an
 * unverified "inventory page") was run for real and failed completely:
 * `.menu-list.mt30` turned out to be the general popular-shortcuts
 * sidebar (mixing Soccer, tennis, NFL, NHL, and MLB shortcuts) -- NOT the
 * Soccer competition inventory. Every discovered "competition" link's
 * `href="javascript:;"` default action was blocked by the page's own
 * Content Security Policy when clicked directly (Chrome correctly
 * refusing to execute a `javascript:` URL that the intended application
 * handler didn't intercept), and the one click that did appear to
 * "succeed" landed on `/liveCompetitions` -- a live surface, not the
 * pre-match inventory. This file's entire discovery/navigation strategy
 * is replaced below; none of Round 1's selectors are reused.
 *
 * CONFIRMED REAL HIERARCHY (live-DOM evidence, 2026-09-12):
 *
 *   Sports (/) -> pre-match Soccer accordion -> Coupons
 *     -> /popularCoupons/1 -> country accordion -> competition link
 *     -> /competition/soccer/{country}/{competition}/{ids}
 *
 *   - Entry surface: `#coupons_sport-1_soccer` resolves to
 *     `/popularCoupons/1` (allow an optional trailing slash). Resolving
 *     instead to `/liveCompetitions` means the walker landed on a LIVE
 *     surface, never the pre-match inventory -- an immediate, named
 *     failure (`WRONG_SURFACE_LIVE_COMPETITIONS`), never treated as
 *     success.
 *   - Pre-match Soccer accordion root: `#left_prematch_sport-1_soccer_label-toggle`.
 *     Its owning `.accordion-item` is the ONLY boundary this module ever
 *     searches inside for country/competition discovery -- never the
 *     global `.menu-list.mt30` sidebar, which mixes multiple sports'
 *     shortcuts and is not scoped to Soccer at all.
 *   - "Show N A-Z more" countries: `[id$="_buttonmore-toggle"]`, scoped
 *     inside the Soccer accordion -- matched by stable ID SUFFIX, never
 *     by its visible text (the displayed count changes).
 *   - Country toggles: `[id^="left_prematch_sport-1_soccer_sg-"][id$="_label-toggle"]`,
 *     e.g. `#left_prematch_sport-1_soccer_sg-11058_england_label-toggle`.
 *   - Competition controls: `[id^="left_prematch_sport-1_soccer_sg-"][id*="_g-"]`,
 *     scoped inside one country's own expanded content, e.g.
 *     `#left_prematch_sport-1_soccer_sg-11058_england_g-170880_premier_league`,
 *     resolving on click to
 *     `/competition/soccer/england/premierleague/1-11058-170880` (20 real
 *     `.sports-table__matchup` rows confirmed present).
 *
 * IDENTITY: `country_name_raw`/`competition_name_raw` are read from each
 * control's own visible text (never prettified/translated); the stable
 * `source_group_id`/`source_competition_id` are parsed from the
 * confirmed ID pattern itself (the `sg-` and `g-` segments) -- the
 * control's OWN ID, re-queried fresh every time, is the rediscovery key
 * this module uses to survive Bet9ja re-rendering accordion content
 * asynchronously; list position and visible text are never relied on for
 * identity.
 *
 * NEVER GUESS A CLICK'S DESTINATION: every competition control's `href`
 * is confirmed to be `javascript:;` (never a real navigation target).
 * This module never reads that value, assigns it to `location.href`,
 * calls `window.open()` with it, or otherwise constructs/dispatches a
 * `javascript:` navigation manually -- it only ever calls the confirmed
 * control's own `.click()`, which triggers Bet9ja's own registered
 * application handler. A one-time, capturing `preventDefault()` listener
 * is attached immediately before that click specifically to stop the
 * anchor's default `javascript:` action itself from executing (the exact
 * action Chrome's CSP blocked in Round 1) while leaving Bet9ja's own
 * handler free to run.
 *
 * SAFETY: the ONLY elements this module ever calls `.click()` on are:
 * the confirmed Coupons entry control, the Soccer accordion toggle, a
 * confirmed country toggle, a confirmed competition control, and (best
 * effort, only to collapse a country this module itself opened) a
 * country's own toggle again. Never a price, selection, Cashout, Live
 * Betting, or betslip control. See the safety test in
 * tests/soccer_walker.test.js, which greps this file's own source.
 */
(function (root) {
  const Bet9jaCapture = typeof module !== 'undefined' && module.exports ? require('./parser.js') : root.Bet9jaCapture;
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  // NOT bumped yet: this is a from-evidence rewrite of the navigation
  // strategy, but no real click-through has yet succeeded end-to-end
  // against the live account (see "What is NOT yet confirmed" in
  // SOCCER_ALL_COMPETITIONS_VALIDATION.md). Per this project's
  // evidence-only versioning discipline, the version string advances
  // only after that one real successful capture.
  const PARSER_VERSION = 'bet9ja-soccer-walker@0.2.0-round2-prematch-accordion-unverified';

  const INVENTORY_PROFILE = 'BET9JA_PREMATCH_SOCCER_ACCORDION';
  const START_ROUTE_PATTERN = /^\/popularCoupons\/1\/?$/;
  const LIVE_COMPETITIONS_PATH = '/liveCompetitions';
  const COMPETITION_PATH_PREFIX = '/competition/soccer/';

  // Confirmed via live inspection, 2026-09-12. See the header comment
  // above for the full contract. Exported as a mutable object so a
  // future correction never requires touching any other code, and so
  // tests can exercise this logic against synthetic markup shaped like
  // the confirmed real IDs.
  const SELECTORS = {
    couponsEntryControl: '#coupons_sport-1_soccer',
    soccerAccordionToggle: '#left_prematch_sport-1_soccer_label-toggle',
    accordionOpenClass: 'accordion-item--open',
    showMoreCountriesSuffix: '[id$="_buttonmore-toggle"]',
    countryTogglePattern: '[id^="left_prematch_sport-1_soccer_sg-"][id$="_label-toggle"]',
    competitionControlPattern: '[id^="left_prematch_sport-1_soccer_sg-"][id*="_g-"]',
    matchup: '.sports-table__matchup',
    fixtureRoot: '.sports-table',
  };

  const COUNTRY_ID_PATTERN = /^left_prematch_sport-1_soccer_sg-([a-z0-9]+)_(.+)_label-toggle$/i;
  const COMPETITION_ID_PATTERN = /^left_prematch_sport-1_soccer_sg-([a-z0-9]+)_(.+?)_g-([a-z0-9]+)_(.+)$/i;

  const ROUTE_TIMEOUT_MS = 3000;
  const ACCORDION_TIMEOUT_MS = 3000;
  const POLL_INTERVAL_MS = 25;
  const SHOW_MORE_SETTLE_MS = 500;
  // Real evidence: 20 real matchups on one inspected competition page and
  // multiple countries/competitions in the accordion -- these caps are
  // generous multiples of any plausible real count, kept as hard
  // backstops against an unbounded loop, never relied upon normally.
  const MAX_COUNTRIES_SAFETY_CAP = 300;
  const MAX_COMPETITIONS_PER_COUNTRY_SAFETY_CAP = 200;

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

  function currentPathname(doc) {
    return (doc.defaultView && doc.defaultView.location && doc.defaultView.location.pathname) || '';
  }

  function currentHref(doc, fallback) {
    return (doc.defaultView && doc.defaultView.location && doc.defaultView.location.href) || fallback || '';
  }

  function findOwningAccordionItem(el) {
    return el ? el.closest('.accordion-item') : null;
  }

  function isAccordionOpen(el) {
    const item = findOwningAccordionItem(el);
    return !!item && item.classList.contains(SELECTORS.accordionOpenClass);
  }

  /**
   * The ONLY safe way this module ever clicks a confirmed control whose
   * `href` is `javascript:;`: a one-time, capturing listener neutralizes
   * the anchor's own default `javascript:` action (the exact action
   * Chrome's CSP blocked in Round 1 when this module clicked such a
   * control directly with no such guard) while leaving Bet9ja's own
   * registered application handler free to run.
   */
  function clickSafely(el) {
    const preventJavascriptHref = (event) => event.preventDefault();
    el.addEventListener('click', preventJavascriptHref, { capture: true, once: true });
    el.click();
  }

  function parseCountryId(id) {
    const match = id.match(COUNTRY_ID_PATTERN);
    if (!match) return null;
    return { sourceGroupId: match[1], countrySlug: match[2] };
  }

  function parseCompetitionId(id) {
    const match = id.match(COMPETITION_ID_PATTERN);
    if (!match) return null;
    return { sourceGroupId: match[1], countrySlug: match[2], sourceCompetitionId: match[3], competitionSlug: match[4] };
  }

  function findSoccerAccordionItem(doc) {
    const toggle = doc.querySelector(SELECTORS.soccerAccordionToggle);
    if (!toggle) return null;
    return findOwningAccordionItem(toggle) || toggle.parentElement;
  }

  function discoverCountryToggles(soccerAccordionItem) {
    if (!soccerAccordionItem) return [];
    return Array.from(soccerAccordionItem.querySelectorAll(SELECTORS.countryTogglePattern)).filter(
      (el) => !!parseCountryId(el.id)
    );
  }

  function discoverCompetitionControls(countryAccordionItem) {
    if (!countryAccordionItem) return [];
    return Array.from(countryAccordionItem.querySelectorAll(SELECTORS.competitionControlPattern)).filter(
      (el) => !!parseCompetitionId(el.id)
    );
  }

  /**
   * Confirms the tab is on (or reaches) the verified pre-match Soccer
   * Coupons surface. Never falls back to treating any other route as
   * usable -- landing on `/liveCompetitions` is an immediate, named
   * failure, exactly the surface Round 1 silently (and wrongly) accepted.
   */
  async function ensureOnCouponsSurface(doc) {
    if (START_ROUTE_PATTERN.test(currentPathname(doc))) {
      return { ok: true, reason: null };
    }
    if (currentPathname(doc) === LIVE_COMPETITIONS_PATH) {
      return { ok: false, reason: 'WRONG_SURFACE_LIVE_COMPETITIONS' };
    }
    const entryControl = doc.querySelector(SELECTORS.couponsEntryControl);
    if (!entryControl) {
      return { ok: false, reason: 'NOT_ON_POPULAR_COUPONS_PAGE' };
    }
    clickSafely(entryControl);
    const resolved = await waitFor(
      () => START_ROUTE_PATTERN.test(currentPathname(doc)) || currentPathname(doc) === LIVE_COMPETITIONS_PATH,
      ROUTE_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!resolved) {
      return { ok: false, reason: 'SOCCER_COUPONS_ROUTE_TIMEOUT' };
    }
    if (currentPathname(doc) === LIVE_COMPETITIONS_PATH) {
      return { ok: false, reason: 'WRONG_SURFACE_LIVE_COMPETITIONS' };
    }
    return { ok: true, reason: null };
  }

  /**
   * Opens the pre-match Soccer accordion (if not already open) and
   * confirms at least one country control has actually rendered inside
   * it -- never falls back to the global shortcuts menu if this
   * accordion doesn't populate.
   */
  async function ensureSoccerAccordionOpen(doc) {
    const toggle = doc.querySelector(SELECTORS.soccerAccordionToggle);
    if (!toggle) {
      return { ok: false, reason: 'PREMATCH_SOCCER_INVENTORY_NOT_READY', soccerAccordionFound: false };
    }
    if (!isAccordionOpen(toggle)) {
      clickSafely(toggle);
    }
    const ready = await waitFor(() => discoverCountryToggles(findSoccerAccordionItem(doc)).length > 0, ACCORDION_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (!ready) {
      return { ok: false, reason: 'PREMATCH_SOCCER_INVENTORY_NOT_READY', soccerAccordionFound: true };
    }
    return { ok: true, reason: null, soccerAccordionFound: true };
  }

  /**
   * Clicks the "show N A-Z more" countries control by its stable ID
   * SUFFIX only -- never by its visible text, which names a changing
   * count. Absence of this control is not a failure: the walker simply
   * continues with whatever countries are already available, and the
   * caller records `country_inventory_expanded: false` so the resulting
   * capture is correctly capped at CAPTURE_PARTIAL rather than presented
   * as a false complete inventory.
   */
  async function expandCountryInventory(soccerAccordionItem) {
    if (!soccerAccordionItem) {
      return { expanded: false, reason: 'SOCCER_ACCORDION_NOT_FOUND' };
    }
    const showMoreEl = soccerAccordionItem.querySelector(SELECTORS.showMoreCountriesSuffix);
    if (!showMoreEl) {
      return { expanded: false, reason: 'SHOW_MORE_CONTROL_ABSENT' };
    }
    const beforeCount = discoverCountryToggles(soccerAccordionItem).length;
    clickSafely(showMoreEl);
    // A short, fixed settle window -- not a long wait keyed on the count
    // growing, since the real page's own expansion mechanism (new DOM
    // nodes vs. revealing already-present hidden ones) is not confirmed
    // either way; whichever it turns out to be, this module always
    // rediscovers fresh afterward rather than trusting a stale count.
    await sleep(SHOW_MORE_SETTLE_MS);
    void beforeCount; // kept for a future round that confirms which expansion mechanism is real
    return { expanded: true, reason: null };
  }

  /**
   * Expands one country (rediscovered fresh by its own stable id, never
   * a cached element reference) and confirms at least one competition
   * control has rendered inside it.
   */
  async function expandCountry(doc, countryId) {
    const el = doc.getElementById(countryId);
    if (!el) {
      return { ok: false, reason: 'COUNTRY_CONTROL_NOT_FOUND_ON_REDISCOVERY', accordionItem: null };
    }
    const accordionItem = findOwningAccordionItem(el);
    if (!isAccordionOpen(el)) {
      clickSafely(el);
    }
    const opened = await waitFor(() => isAccordionOpen(el), ACCORDION_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (!opened) {
      return { ok: false, reason: 'COUNTRY_EXPANSION_TIMEOUT', accordionItem };
    }
    const hasCompetitions = await waitFor(
      () => discoverCompetitionControls(accordionItem).length > 0,
      ACCORDION_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!hasCompetitions) {
      return { ok: false, reason: 'COUNTRY_COMPETITION_LIST_EMPTY', accordionItem };
    }
    return { ok: true, reason: null, accordionItem };
  }

  /** Best-effort collapse -- never required for correctness. */
  function collapseCountry(doc, countryId) {
    const el = doc.getElementById(countryId);
    if (el && isAccordionOpen(el)) {
      clickSafely(el);
    }
  }

  /**
   * Selects one competition (rediscovered fresh by its own stable id)
   * and waits for all three confirmed conditions: the URL pathname moves
   * to `/competition/soccer/...`, that URL differs from the previous
   * competition's own resolved URL (never a content-text diff -- two
   * competitions can share similar headings or both show no fixtures),
   * and the confirmed `.sports-table` fixture root (or a confirmed empty
   * state -- zero rows is itself a valid, auditable outcome) is present.
   */
  async function selectCompetition(doc, competitionId, previousResolvedPath) {
    const el = doc.getElementById(competitionId);
    if (!el) {
      return { ok: false, reason: 'COMPETITION_CONTROL_NOT_FOUND_ON_REDISCOVERY' };
    }
    clickSafely(el);

    const routeReady = await waitFor(
      () => currentPathname(doc).startsWith(COMPETITION_PATH_PREFIX) && currentPathname(doc) !== previousResolvedPath,
      ROUTE_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!routeReady) {
      if (currentPathname(doc) === LIVE_COMPETITIONS_PATH) {
        return { ok: false, reason: 'WRONG_SURFACE_LIVE_COMPETITIONS' };
      }
      if (currentPathname(doc).startsWith(COMPETITION_PATH_PREFIX) === false && currentPathname(doc) !== previousResolvedPath) {
        return { ok: false, reason: 'COMPETITION_ROUTE_NOT_SOCCER' };
      }
      return { ok: false, reason: 'COMPETITION_ROUTE_TIMEOUT' };
    }

    const contentReady = await waitFor(() => !!doc.querySelector(SELECTORS.fixtureRoot), ROUTE_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (!contentReady) {
      return { ok: false, reason: 'COMPETITION_CONTENT_TIMEOUT' };
    }

    return { ok: true, reason: null, resolvedPath: currentPathname(doc), resolvedHref: currentHref(doc) };
  }

  function makeCompetitionResult({
    countryNameRaw,
    competitionNameRaw,
    sourceGroupId,
    sourceCompetitionId,
    competitionControlId,
    resolvedUrl,
    routeConfirmed,
    recordsSeen,
    recordsParsed,
    recordsUnresolved,
    recordsExpectedUnsupported,
    failureReason,
  }) {
    return {
      country_name_raw: countryNameRaw || null,
      competition_name_raw: competitionNameRaw || null,
      source_group_id: sourceGroupId || null,
      source_competition_id: sourceCompetitionId || null,
      competition_control_id: competitionControlId || null,
      resolved_url: resolvedUrl || null,
      route_confirmed: !!routeConfirmed,
      records_seen: recordsSeen || 0,
      records_parsed: recordsParsed || 0,
      records_unresolved: recordsUnresolved || 0,
      records_expected_unsupported: recordsExpectedUnsupported || 0,
      failure_reason: failureReason || null,
    };
  }

  function emptyEnvelopeShape() {
    return {
      countries_available: 0,
      countries_visited: 0,
      countries_failed: 0,
      competitions_available: 0,
      competitions_visited: 0,
      competitions_empty: 0,
      competitions_failed: 0,
      duplicates_skipped: 0,
      competition_results: [],
      fixtures: [],
      unparsed_records: [],
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
      schema_version: 'bet9ja-soccer-all-competitions-capture.v2',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      capture_scope: 'SOCCER_ALL_PREMATCH_COMPETITIONS',
      source_url: Bet9jaCapture.sanitizeSourceUrl(context.sourceUrl),
      inventory_profile: INVENTORY_PROFILE,
      parser_version: PARSER_VERSION,
    };

    const couponsResult = await ensureOnCouponsSurface(doc);
    if (!couponsResult.ok) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: [couponsResult.reason],
          inventory_source_url: Bet9jaCapture.sanitizeSourceUrl(currentHref(doc, context.sourceUrl)),
          soccer_accordion_found: false,
          coupons_route_confirmed: false,
          country_inventory_expanded: false,
          countries_available: 0,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const inventorySourceUrl = Bet9jaCapture.sanitizeSourceUrl(currentHref(doc, context.sourceUrl));

    const accordionResult = await ensureSoccerAccordionOpen(doc);
    if (!accordionResult.ok) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: [accordionResult.reason],
          inventory_source_url: inventorySourceUrl,
          soccer_accordion_found: accordionResult.soccerAccordionFound,
          coupons_route_confirmed: true,
          country_inventory_expanded: false,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const soccerAccordionItem = findSoccerAccordionItem(doc);
    const expandResult = await expandCountryInventory(soccerAccordionItem);
    const countryInventoryExpanded = expandResult.expanded;
    const countryInventoryExpansionReason = expandResult.expanded ? null : expandResult.reason;

    const countryToggles = discoverCountryToggles(soccerAccordionItem);
    const countries = [];
    const seenCountryIds = new Set();
    for (const el of countryToggles) {
      const parsed = parseCountryId(el.id);
      if (!parsed || seenCountryIds.has(el.id)) continue;
      seenCountryIds.add(el.id);
      countries.push({
        countryId: el.id,
        countryNameRaw: text(el),
        sourceGroupId: parsed.sourceGroupId,
      });
    }
    const countriesAvailable = countries.length;

    if (countriesAvailable === 0) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['NO_COUNTRIES_DISCOVERED'],
          inventory_source_url: inventorySourceUrl,
          soccer_accordion_found: true,
          coupons_route_confirmed: true,
          country_inventory_expanded: countryInventoryExpanded,
          country_inventory_expansion_reason: countryInventoryExpansionReason,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const cappedCountries = countries.slice(0, MAX_COUNTRIES_SAFETY_CAP);

    const fixtures = [];
    const unparsedRecords = [];
    const competitionResults = [];
    const seenFixtureIds = new Set();
    let countriesVisited = 0;
    let countriesFailed = 0;
    let competitionsAvailable = 0;
    let competitionsVisited = 0;
    let competitionsEmpty = 0;
    let competitionsFailed = 0;
    let duplicatesSkipped = 0;
    let previousResolvedPath = currentPathname(doc);

    for (const country of cappedCountries) {
      const expand = await expandCountry(doc, country.countryId);
      if (!expand.ok) {
        countriesFailed += 1;
        competitionResults.push(
          makeCompetitionResult({
            countryNameRaw: country.countryNameRaw,
            sourceGroupId: country.sourceGroupId,
            failureReason: expand.reason,
          })
        );
        continue;
      }

      const competitionEls = discoverCompetitionControls(expand.accordionItem);
      const competitionEntries = [];
      const seenCompetitionIds = new Set();
      for (const el of competitionEls) {
        const parsed = parseCompetitionId(el.id);
        if (!parsed || seenCompetitionIds.has(el.id)) continue;
        seenCompetitionIds.add(el.id);
        competitionEntries.push({
          competitionId: el.id,
          competitionNameRaw: text(el),
          sourceCompetitionId: parsed.sourceCompetitionId,
        });
      }
      competitionsAvailable += competitionEntries.length;
      const cappedCompetitions = competitionEntries.slice(0, MAX_COMPETITIONS_PER_COUNTRY_SAFETY_CAP);

      let countryHadAnySuccess = false;
      for (const competition of cappedCompetitions) {
        const selectResult = await selectCompetition(doc, competition.competitionId, previousResolvedPath);
        if (!selectResult.ok) {
          competitionsFailed += 1;
          competitionResults.push(
            makeCompetitionResult({
              countryNameRaw: country.countryNameRaw,
              competitionNameRaw: competition.competitionNameRaw,
              sourceGroupId: country.sourceGroupId,
              sourceCompetitionId: competition.sourceCompetitionId,
              competitionControlId: competition.competitionId,
              failureReason: selectResult.reason,
            })
          );
          continue;
        }

        previousResolvedPath = selectResult.resolvedPath;
        const { envelope: subEnvelope } = Bet9jaCapture.captureFromDocument(doc, {
          sourceUrl: selectResult.resolvedHref,
          pageTitle: context.pageTitle,
          capturedAtUtc,
          previousIndex: {},
        });

        let compFixturesParsed = 0;
        for (const fixture of subEnvelope.fixtures) {
          if (seenFixtureIds.has(fixture.fixture_id)) {
            duplicatesSkipped += 1;
            continue;
          }
          seenFixtureIds.add(fixture.fixture_id);
          fixtures.push({
            ...fixture,
            source_country: country.countryNameRaw,
            source_competition: competition.competitionNameRaw,
            source_group_id: country.sourceGroupId,
            source_competition_id: competition.sourceCompetitionId,
          });
          compFixturesParsed += 1;
        }
        for (const record of subEnvelope.unparsed_records) {
          unparsedRecords.push({
            ...record,
            source_country: country.countryNameRaw,
            source_competition: competition.competitionNameRaw,
            source_group_id: country.sourceGroupId,
            source_competition_id: competition.sourceCompetitionId,
          });
        }

        const recordsSeen = subEnvelope.coverage.records_seen;
        if (recordsSeen === 0) {
          competitionsEmpty += 1;
        } else {
          competitionsVisited += 1;
        }
        countryHadAnySuccess = true;

        competitionResults.push(
          makeCompetitionResult({
            countryNameRaw: country.countryNameRaw,
            competitionNameRaw: competition.competitionNameRaw,
            sourceGroupId: country.sourceGroupId,
            sourceCompetitionId: competition.sourceCompetitionId,
            competitionControlId: competition.competitionId,
            resolvedUrl: Bet9jaCapture.sanitizeSourceUrl(selectResult.resolvedHref),
            routeConfirmed: true,
            recordsSeen,
            recordsParsed: compFixturesParsed,
            recordsUnresolved: subEnvelope.coverage.records_unresolved,
            recordsExpectedUnsupported: subEnvelope.coverage.records_expected_unsupported,
          })
        );
      }

      if (countryHadAnySuccess || cappedCompetitions.length === 0) {
        countriesVisited += 1;
      } else {
        countriesFailed += 1;
      }

      collapseCountry(doc, country.countryId);
    }

    // Best-effort return to the verified Coupons route -- never required
    // for the correctness of the capture that already happened. Uses the
    // browser's own history navigation, never a guessed click, and its
    // outcome is not re-verified here (a future capture re-confirms the
    // route itself via ensureOnCouponsSurface).
    if (!START_ROUTE_PATTERN.test(currentPathname(doc))) {
      const win = doc.defaultView;
      if (win && win.history && typeof win.history.back === 'function') {
        win.history.back();
      }
    }

    const statusReasons = [];
    if (!countryInventoryExpanded) statusReasons.push(`COUNTRY_INVENTORY_NOT_FULLY_EXPANDED_${countryInventoryExpansionReason}`);
    if (countriesFailed > 0) statusReasons.push('SOME_COUNTRIES_FAILED');
    if (competitionsFailed > 0) statusReasons.push('SOME_COMPETITIONS_FAILED');

    const countriesInvariantHolds = countriesAvailable === countriesVisited + countriesFailed;
    if (!countriesInvariantHolds) statusReasons.unshift('COUNTRY_ACCOUNTING_INVARIANT_VIOLATED');

    let captureStatus;
    if (fixtures.length === 0 && unparsedRecords.length === 0 && competitionsEmpty === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else if (
      countryInventoryExpanded &&
      countriesFailed === 0 &&
      competitionsFailed === 0 &&
      countriesInvariantHolds
    ) {
      // Every discovered country and competition was successfully
      // visited (or confirmed empty), the full country inventory was
      // expanded, and no accounting invariant failed -- per the explicit
      // CAPTURE_COMPLETE bar this module was asked to enforce.
      captureStatus = 'CAPTURE_COMPLETE';
    } else {
      captureStatus = 'CAPTURE_PARTIAL';
    }

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        inventory_source_url: inventorySourceUrl,
        soccer_accordion_found: true,
        coupons_route_confirmed: true,
        country_inventory_expanded: countryInventoryExpanded,
        country_inventory_expansion_reason: countryInventoryExpansionReason,
        countries_available: countriesAvailable,
        countries_visited: countriesVisited,
        countries_failed: countriesFailed,
        competitions_available: competitionsAvailable,
        competitions_visited: competitionsVisited,
        competitions_empty: competitionsEmpty,
        competitions_failed: competitionsFailed,
        duplicates_skipped: duplicatesSkipped,
        competition_results: competitionResults,
        fixtures,
        unparsed_records: unparsedRecords,
      },
    };
  }

  const api = {
    captureAllSoccerCompetitions,
    SELECTORS,
    PARSER_VERSION,
    parseCountryId,
    parseCompetitionId,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerWalker = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
