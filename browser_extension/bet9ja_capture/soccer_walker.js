/**
 * Bet9ja Soccer ALL-COMPETITIONS fixture walker -- automates the manual
 * "select every competition, copy its fixtures" workflow the daily
 * intake step used to require. Pure orchestration: every fixture actually
 * comes from parser.js's own `captureFromDocument`, called once per
 * selected BATCH of competitions -- this file never re-implements fixture
 * parsing, only the navigation, checkbox selection, and aggregation
 * around it.
 *
 * ROUND 5 REWRITE (2026-09-12) -- SUPERSEDES ROUNDS 1-4 ENTIRELY.
 *
 * Rounds 1-4 walked one competition at a time through
 * `/competition/soccer/{country}/{competition}/{ids}`, requiring a
 * verified return to the Coupons inventory (`/popularCoupons/1`) between
 * every single competition. A real capture against the live account
 * confirmed the whole discovery/selection/parsing pipeline worked, but
 * also hit a real, unresolved failure in that return step
 * (`STOPPED_EARLY_COULD_NOT_RETURN_TO_COUPONS`, followed by
 * `PREMATCH_SOCCER_INVENTORY_NOT_READY` on the next two attempts) -- see
 * SOCCER_ALL_COMPETITIONS_VALIDATION.md's Round 4 section for the full
 * evidence. Real, live testing then found a materially simpler page:
 * Bet9ja's own Competitions selector at `/sportPage/1/competitions`
 * lets you check multiple competitions' boxes and click "Show Leagues"
 * to render every selected competition's fixtures on ONE page, with the
 * URL never changing. This removes the entire
 * return-to-Coupons-between-every-competition failure surface, the
 * `javascript:;` CSP-sensitive competition links, and the repeated
 * `/competition/soccer/...` route-change waits -- there is no per-
 * competition navigation left to verify at all.
 *
 * CONFIRMED REAL HIERARCHY (live-DOM evidence, 2026-09-12):
 *
 *   `/sportPage/1/competitions` -> `.competitions` root -> one
 *   `.accordion-item` per country -> `.competitions__group-item` rows,
 *   each holding a `.sportpage__cb-input` checkbox (id = the competition's
 *   own stable numeric id, e.g. `1209691` for Nigeria's "Professional
 *   Football League") with its own `<label for="{id}">` -- the checkbox
 *   itself is marked `readonly`, so selection happens through its label,
 *   matching the site's own UI behavior. `.competitions__filter-btn.
 *   check-coupon` ("Show Leagues") renders every currently-checked
 *   competition's fixtures into the page's existing `.sports-table`/
 *   `.sports-table__matchup` structure (already fully supported by
 *   parser.js) WITHOUT changing the URL.
 *   `.competitions__filter-btn.clear-all` clears every current
 *   selection, confirmed necessary before starting the next batch.
 *
 * IDENTITY: `source_competition_id` is read directly from the checkbox's
 * own `id` attribute -- no composite `sg-`/`g-` id-parsing is needed here
 * (unlike the retired per-competition accordion), since Bet9ja's own
 * competition id is already the checkbox's id verbatim.
 * `country_name_raw`/`competition_name_raw` are read from each control's
 * own visible text, never prettified.
 *
 * BATCHING: Bet9ja enforces some maximum number of simultaneously
 * selected competitions, surfaced via a "Maximum selection limit
 * reached!" notification -- but neither the exact limit nor that
 * notification's own selector was exposed in the inspected DOM (marked
 * [UNVERIFIED] in SOCCER_ALL_COMPETITIONS_VALIDATION.md). This module
 * never invents a fixed batch size: it selects competitions one at a
 * time, watching each checkbox's own `.checked` state, and treats EITHER
 * a selection that doesn't stick OR any visible element whose text
 * matches the limit-notification wording as "this batch is full" --
 * finalizing (Show Leagues) the current batch and deferring that
 * competition to the next one, discovered operationally rather than
 * guessed at.
 *
 * ATTRIBUTION CAVEAT -- READ BEFORE TRUSTING PER-COMPETITION FIXTURE
 * COUNTS: parser.js resolves each row's sport (and therefore whether it's
 * in scope at all) either from an id-embedded `sport-N` segment on the
 * row itself, or from the page's own URL matching
 * `/competition/{sport}/{country}/{competition}/` -- confirmed only for
 * single-competition competition pages. On this combined
 * `/sportPage/1/competitions` page the URL never changes and it is
 * NOT YET CONFIRMED whether Bet9ja repeats the `sport-N` id segment on
 * every row here too, nor whether multiple selected competitions'
 * fixtures render as visually/structurally distinguishable groups at
 * all. Rather than guess a row-to-competition mapping, this module never
 * attributes an individual fixture to one specific competition when a
 * batch contains more than one: every fixture instead carries
 * `source_batch_index` and the full list of that batch's
 * `source_competition_ids_in_batch`/`source_competitions_raw_in_batch`.
 * Each competition is still classified (captured/empty/failed/deferred)
 * at the BATCH level in `competition_results[]`, and each
 * `batch_results[]` entry carries its own honest
 * records_seen/records_parsed/records_unresolved/
 * records_expected_unsupported straight from that one
 * `captureFromDocument` call -- nothing here invents a per-competition
 * row count no evidence supports.
 *
 * SAFETY: the only elements this module ever calls `.click()` on are: a
 * country's own accordion toggle, a competition's own `<label>` (its
 * associated checkbox is `readonly`, so this IS the site's normal
 * selection path, not a workaround), the "Show Leagues" button, and the
 * "Clear all" button. Never a price, selection, Cashout, Live Betting, or
 * betslip control. See the safety test in tests/soccer_walker.test.js,
 * which greps this file's own source.
 */
(function (root) {
  const Bet9jaCapture = typeof module !== 'undefined' && module.exports ? require('./parser.js') : root.Bet9jaCapture;
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  // NOT bumped to a non-"-unverified" tag yet: this batch-selector
  // rewrite has not itself been exercised end-to-end against the live
  // account (see "What is NOT yet confirmed" in
  // SOCCER_ALL_COMPETITIONS_VALIDATION.md's Round 5 section). Per this
  // project's evidence-only versioning discipline, the version string
  // advances only after that one real successful capture.
  const PARSER_VERSION = 'bet9ja-soccer-walker@0.4.0-round5-batch-selector-unverified';

  const INVENTORY_PROFILE = 'BET9JA_SPORTPAGE_COMPETITIONS_SELECTOR';
  const START_ROUTE_PATTERN = /^\/sportPage\/1\/competitions\/?$/;
  const CAPTURE_SCOPE = 'SOCCER_ALL_PREMATCH_COMPETITIONS';

  // Trusted capture-context claim passed to parser.js's own
  // `validateForcedSportContext` -- `/sportPage/1/competitions` is
  // explicitly Bet9ja's OWN Soccer competitions surface (sport id `1`,
  // the same id already confirmed as SOCCER elsewhere in this codebase),
  // so a row with no id-embedded sport segment and no `/competition/...`
  // URL to fall back on (both are genuinely absent on this route) should
  // never be misclassified UNSUPPORTED_SPORT. This is only ever a CLAIM:
  // parser.js independently re-verifies the route and a visible sport
  // heading before ever trusting it, and fails the whole capture closed
  // (`SPORT_CONTEXT_CONFLICT`) if they disagree -- this module never
  // classifies a single row itself.
  const FORCED_SPORT_CONTEXT = {
    forced_sport_hint: 'SOCCER',
    forced_sport_source: 'SPORTPAGE_ROUTE_ID',
    forced_sport_source_value: '1',
    capture_scope: CAPTURE_SCOPE,
  };

  // Confirmed via live inspection, 2026-09-12. See the header comment
  // above for the full contract. Exported as a mutable object so a
  // future correction never requires touching any other code, and so
  // tests can exercise this logic against synthetic markup shaped like
  // the confirmed real structure.
  const SELECTORS = {
    pageRoot: '.competitions',
    accordionItem: '.accordion-item',
    accordionToggle: '.accordion-toggle',
    accordionText: '.accordion-toggle .accordion-text',
    // Inherited from the confirmed pre-match Soccer accordion (Round 2)
    // as the best-supported assumption for Bet9ja's shared accordion
    // widget -- NOT independently confirmed on THIS specific page. If a
    // real run reports countries expanding but zero competitions ever
    // discovered, this is the first thing to re-check against real
    // markup.
    accordionOpenClass: 'accordion-item--open',
    groupItem: '.competitions__group-item',
    checkbox: '.sportpage__cb-input',
    clearAllButton: '.competitions__filter-btn.clear-all',
    showLeaguesButton: '.competitions__filter-btn.check-coupon',
    fixtureRoot: '.sports-table',
    matchup: '.sports-table__matchup',
  };

  // [UNVERIFIED] exact wording/selector of Bet9ja's own "Maximum
  // selection limit reached!" notification -- matched by visible text
  // anywhere in the document rather than a guessed class name, so a
  // batch-full condition is still detected honestly even though the
  // notification's own markup was never inspected.
  const MAX_LIMIT_TEXT_PATTERN = /maximum selection limit/i;

  const ROUTE_READY_TIMEOUT_MS = 3000;
  const ACCORDION_TIMEOUT_MS = 3000;
  const SELECTION_TIMEOUT_MS = 1500;
  const SHOW_LEAGUES_CONTENT_TIMEOUT_MS = 5000;
  const CLEAR_ALL_TIMEOUT_MS = 2000;
  const POLL_INTERVAL_MS = 25;
  // Real evidence: 100+ countries and (per the retired per-competition
  // walker's own real capture) dozens of competitions per country are
  // plausible; this is a generous multiple of any plausible real total,
  // kept as a hard backstop against an unbounded loop, never relied upon
  // normally. Any competition beyond the cap is explicitly counted as
  // `competitions_skipped_by_safety_cap`, never silently dropped.
  const MAX_TOTAL_COMPETITIONS_SAFETY_CAP = 2000;
  const MAX_BATCHES_SAFETY_CAP = 200;

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

  function isAccordionOpen(item) {
    return !!item && item.classList.contains(SELECTORS.accordionOpenClass);
  }

  function findCompetitionsRoot(doc) {
    return doc.querySelector(SELECTORS.pageRoot);
  }

  function discoverCountryAccordions(competitionsRoot) {
    if (!competitionsRoot) return [];
    return Array.from(competitionsRoot.querySelectorAll(SELECTORS.accordionItem));
  }

  function discoverCompetitionsInCountry(countryItem) {
    if (!countryItem) return [];
    const rows = Array.from(countryItem.querySelectorAll(SELECTORS.groupItem));
    const entries = [];
    for (const row of rows) {
      const checkbox = row.querySelector(SELECTORS.checkbox);
      if (!checkbox || !checkbox.id) continue;
      const span = row.querySelector('span');
      entries.push({ checkboxId: checkbox.id, competitionNameRaw: text(span || row) });
    }
    return entries;
  }

  function isLimitNotificationVisible(doc) {
    // No confirmed selector for the notification element -- matched by
    // visible text anywhere in the body instead of guessing a class.
    return MAX_LIMIT_TEXT_PATTERN.test(text(doc.body));
  }

  function normalizeForMatch(value) {
    return (value || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  /**
   * The live single-league test confirmed the rendered content includes
   * the competition heading IMMEDIATELY BEFORE its fixture table -- the
   * one piece of confirmed structural evidence this function relies on.
   * The exact heading element/selector itself is [UNVERIFIED] (never
   * captured), so this checks the table's own previous sibling's text,
   * then that sibling's own text content one level down, rather than
   * guessing a class name. Returns '' (never guessed) if neither yields
   * text.
   */
  function resolveNearestCompetitionHeadingRaw(tableEl) {
    const prev = tableEl.previousElementSibling;
    if (!prev) return '';
    const direct = text(prev);
    if (direct) return direct;
    const nested = prev.firstElementChild ? text(prev.firstElementChild) : '';
    return nested;
  }

  /**
   * Builds a `resolve_table_competition` function (parser.js's own
   * contract -- see its header comment) scoped to exactly the
   * competitions selected in ONE batch. A table's heading is matched
   * against each candidate's own `competitionNameRaw` (never the other
   * way around, since the heading's exact format is unconfirmed but a
   * competition's own display name is known verbatim from discovery) --
   * resolved only when EXACTLY ONE candidate's name appears in the
   * heading text; zero or multiple matches is an honest
   * `{resolved: false}`, never a guess at which one is more likely.
   */
  function makeTableCompetitionResolver(currentBatch) {
    return (tableEl) => {
      const headingRaw = resolveNearestCompetitionHeadingRaw(tableEl);
      if (!headingRaw) return { resolved: false };
      const normalizedHeading = normalizeForMatch(headingRaw);
      const matches = currentBatch.filter((c) => {
        const normalizedName = normalizeForMatch(c.competitionNameRaw);
        return normalizedName && normalizedHeading.includes(normalizedName);
      });
      if (matches.length !== 1) return { resolved: false };
      const match = matches[0];
      return {
        resolved: true,
        sourceCompetitionId: match.checkboxId,
        competitionNameRaw: match.competitionNameRaw,
        countryNameRaw: match.countryNameRaw,
      };
    };
  }

  /**
   * Confirms the tab is ALREADY on the verified Competitions surface.
   * Never clicks anything to try to get there -- reaching
   * `/sportPage/1/competitions` from an arbitrary starting page is
   * popup.js's job (a real `chrome.tabs.update` navigation, done before
   * this module is even injected), the same pattern used for the
   * previous rounds' Coupons surface.
   */
  function ensureOnCompetitionsSurface(doc) {
    if (START_ROUTE_PATTERN.test(currentPathname(doc))) {
      return { ok: true, reason: null };
    }
    return { ok: false, reason: 'NOT_ON_COMPETITIONS_PAGE' };
  }

  /**
   * Expands one country's accordion (if not already open) and confirms
   * at least one competition row has actually rendered inside it.
   */
  async function expandCountryAccordion(countryItem) {
    const toggle = countryItem.querySelector(SELECTORS.accordionToggle);
    if (!toggle) {
      return { ok: false, reason: 'COUNTRY_TOGGLE_NOT_FOUND' };
    }
    if (!isAccordionOpen(countryItem)) {
      toggle.click();
    }
    const opened = await waitFor(() => isAccordionOpen(countryItem), ACCORDION_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (!opened) {
      return { ok: false, reason: 'COUNTRY_EXPANSION_TIMEOUT' };
    }
    const hasCompetitions = await waitFor(
      () => discoverCompetitionsInCountry(countryItem).length > 0,
      ACCORDION_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!hasCompetitions) {
      return { ok: false, reason: 'COUNTRY_COMPETITION_LIST_EMPTY' };
    }
    return { ok: true, reason: null };
  }

  function getFixtureFingerprint(doc) {
    return Array.from(doc.querySelectorAll(SELECTORS.matchup))
      .map((el) => text(el))
      .join('|');
  }

  /**
   * Selects one competition by clicking its own `<label for="{id}">` --
   * the checkbox itself is `readonly`, so this is the site's own
   * selection path, never a workaround. Returns one of three outcomes,
   * never guessed: SELECTED (checkbox is now checked), LIMIT_REACHED (the
   * checkbox never checked AND the limit notification became visible --
   * this competition is deferred to the next batch), or SELECTION_FAILED
   * (the checkbox never checked and no limit notification appeared --
   * an unexplained failure specific to this one competition, not treated
   * as a batch-full condition).
   */
  async function selectCompetition(doc, checkboxId) {
    const checkbox = doc.querySelector(`[id="${checkboxId}"]`);
    if (!checkbox) {
      return { outcome: 'SELECTION_FAILED', reason: 'CHECKBOX_NOT_FOUND' };
    }
    const label = doc.querySelector(`label[for="${checkboxId}"]`);
    if (!label) {
      return { outcome: 'SELECTION_FAILED', reason: 'LABEL_NOT_FOUND' };
    }
    if (checkbox.checked) {
      return { outcome: 'SELECTED', reason: null };
    }
    label.click();
    const settled = await waitFor(
      () => checkbox.checked === true || isLimitNotificationVisible(doc),
      SELECTION_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (checkbox.checked) {
      return { outcome: 'SELECTED', reason: null };
    }
    if (!settled || isLimitNotificationVisible(doc)) {
      return { outcome: 'LIMIT_REACHED', reason: 'MAXIMUM_SELECTION_LIMIT_REACHED' };
    }
    return { outcome: 'SELECTION_FAILED', reason: 'SELECTION_DID_NOT_STICK' };
  }

  /**
   * Clicks "Show Leagues" and waits for the combined fixture output to
   * render. A batch's real result can legitimately be textually
   * IDENTICAL to whatever was already on screen -- a genuinely empty
   * batch (no matchup rows either before or after), or a batch whose
   * fixtures happen to duplicate the previous batch's -- so "the
   * fingerprint never changed" is not, by itself, proof the click did
   * nothing. Only when there was never any `.sports-table` root present
   * either before or after the click is that treated as a hard failure;
   * otherwise this is reported honestly as `contentChangeConfirmed:
   * false` rather than forced into either a false failure or a blindly
   * trusted success.
   */
  async function showLeaguesAndWait(doc) {
    const button = doc.querySelector(SELECTORS.showLeaguesButton);
    if (!button) {
      return { ok: false, reason: 'SHOW_LEAGUES_BUTTON_NOT_FOUND' };
    }
    const before = getFixtureFingerprint(doc);
    const rootPresentBefore = !!doc.querySelector(SELECTORS.fixtureRoot);
    button.click();
    // Two independent signals, whichever fires first: the fixture text
    // itself changing (the common case), or the `.sports-table` root's
    // very presence flipping (absent -> present, e.g. a genuinely empty
    // batch's first-ever render) -- both are real evidence of an update,
    // so neither needs to wait out the full timeout when the other is
    // available quickly.
    const contentChanged = await waitFor(() => {
      if (getFixtureFingerprint(doc) !== before) return true;
      return !!doc.querySelector(SELECTORS.fixtureRoot) !== rootPresentBefore;
    }, SHOW_LEAGUES_CONTENT_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (contentChanged) {
      return { ok: true, reason: null, contentChangeConfirmed: true };
    }
    const rootPresentAfter = !!doc.querySelector(SELECTORS.fixtureRoot);
    if (!rootPresentBefore && !rootPresentAfter) {
      return { ok: false, reason: 'SHOW_LEAGUES_CONTENT_TIMEOUT' };
    }
    return { ok: true, reason: null, contentChangeConfirmed: false };
  }

  /** Clicks "Clear all" and confirms every selection actually reset before the next batch. */
  async function clearAllAndWait(doc, checkboxIds) {
    const button = doc.querySelector(SELECTORS.clearAllButton);
    if (!button) {
      return { ok: false, reason: 'CLEAR_ALL_BUTTON_NOT_FOUND' };
    }
    button.click();
    const cleared = await waitFor(
      () => checkboxIds.every((id) => {
        const el = doc.querySelector(`[id="${id}"]`);
        return !el || !el.checked;
      }),
      CLEAR_ALL_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!cleared) {
      return { ok: false, reason: 'CLEAR_ALL_TIMEOUT' };
    }
    return { ok: true, reason: null };
  }

  function emptyEnvelopeShape() {
    return {
      countries_available: 0,
      countries_visited: 0,
      countries_failed: 0,
      competitions_available: 0,
      competitions_captured: 0,
      competitions_empty: 0,
      competitions_failed: 0,
      competitions_skipped_by_safety_cap: 0,
      competitions_skipped_by_early_stop: 0,
      duplicates_skipped: 0,
      batch_results: [],
      competition_results: [],
      fixtures: [],
      unparsed_records: [],
      resume_metadata: null,
    };
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string,
   *          shouldCancel?: Function}} context
   * @returns {Promise<{envelope: object}>}
   */
  async function captureAllSoccerCompetitions(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const shouldCancel = typeof context.shouldCancel === 'function' ? context.shouldCancel : () => false;
    const envelopeBase = {
      schema_version: 'bet9ja-soccer-all-competitions-capture.v3',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      capture_scope: CAPTURE_SCOPE,
      source_url: Bet9jaCapture.sanitizeSourceUrl(context.sourceUrl),
      inventory_profile: INVENTORY_PROFILE,
      parser_version: PARSER_VERSION,
    };

    const surfaceResult = ensureOnCompetitionsSurface(doc);
    if (!surfaceResult.ok) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: [surfaceResult.reason],
          inventory_source_url: Bet9jaCapture.sanitizeSourceUrl(currentHref(doc, context.sourceUrl)),
          competitions_route_confirmed: false,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const inventorySourceUrl = Bet9jaCapture.sanitizeSourceUrl(currentHref(doc, context.sourceUrl));

    const rootReady = await waitFor(() => !!findCompetitionsRoot(doc), ROUTE_READY_TIMEOUT_MS, POLL_INTERVAL_MS);
    if (!rootReady) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['COMPETITIONS_ROOT_NOT_READY'],
          inventory_source_url: inventorySourceUrl,
          competitions_route_confirmed: true,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const competitionsRoot = findCompetitionsRoot(doc);
    const countriesReady = await waitFor(
      () => discoverCountryAccordions(competitionsRoot).length > 0,
      ROUTE_READY_TIMEOUT_MS,
      POLL_INTERVAL_MS
    );
    if (!countriesReady) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['NO_COUNTRIES_DISCOVERED'],
          inventory_source_url: inventorySourceUrl,
          competitions_route_confirmed: true,
          ...emptyEnvelopeShape(),
        },
      };
    }

    // --- Phase 1: discovery -- expand every country, enumerate every
    // competition checkbox. No selection happens yet.
    const countryItems = discoverCountryAccordions(competitionsRoot);
    let countriesVisited = 0;
    let countriesFailed = 0;
    const allCompetitions = [];
    for (const countryItem of countryItems) {
      if (shouldCancel()) break;
      const countryNameRaw = text(countryItem.querySelector(SELECTORS.accordionText));
      const expand = await expandCountryAccordion(countryItem);
      if (!expand.ok) {
        countriesFailed += 1;
        continue;
      }
      countriesVisited += 1;
      for (const entry of discoverCompetitionsInCountry(countryItem)) {
        allCompetitions.push({ ...entry, countryNameRaw });
      }
    }
    const countriesAvailable = countryItems.length;

    if (allCompetitions.length === 0) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['NO_COMPETITIONS_DISCOVERED'],
          inventory_source_url: inventorySourceUrl,
          competitions_route_confirmed: true,
          countries_available: countriesAvailable,
          countries_visited: countriesVisited,
          countries_failed: countriesFailed,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const cappedCompetitions = allCompetitions.slice(0, MAX_TOTAL_COMPETITIONS_SAFETY_CAP);
    const competitionsSkippedBySafetyCap = allCompetitions.length - cappedCompetitions.length;
    const competitionsAvailable = allCompetitions.length;

    // --- Phase 2: batch selection + capture.
    const remaining = [...cappedCompetitions];
    const fixtures = [];
    const unparsedRecords = [];
    const competitionResults = [];
    const batchResults = [];
    const seenFixtureIds = new Set();
    let duplicatesSkipped = 0;
    let competitionsCaptured = 0;
    let competitionsEmpty = 0;
    let competitionsFailed = 0;
    let competitionsSkippedByEarlyStop = 0;
    let earlyStopReason = null;
    let lastCompletedCheckboxId = null;
    let batchIndex = 0;

    batchLoop: while (remaining.length > 0 && batchIndex < MAX_BATCHES_SAFETY_CAP) {
      if (shouldCancel()) {
        earlyStopReason = 'USER_CANCELLED';
        break;
      }

      const currentBatch = [];
      let stuckWithNoSelection = false;

      while (remaining.length > 0) {
        if (shouldCancel()) {
          earlyStopReason = 'USER_CANCELLED';
          break batchLoop;
        }
        const candidate = remaining[0];
        const selectResult = await selectCompetition(doc, candidate.checkboxId);
        if (selectResult.outcome === 'SELECTED') {
          currentBatch.push(candidate);
          remaining.shift();
          lastCompletedCheckboxId = candidate.checkboxId;
        } else if (selectResult.outcome === 'LIMIT_REACHED') {
          if (currentBatch.length === 0) {
            // Never even one competition could be selected in this
            // batch -- a real anomaly, not a normal batch-full case.
            // Never loop forever on the same stuck competition.
            stuckWithNoSelection = true;
          }
          break;
        } else {
          // SELECTION_FAILED -- specific to this one competition. Record
          // it now, drop it, and keep filling the current batch with the
          // rest.
          competitionsFailed += 1;
          competitionResults.push({
            country_name_raw: candidate.countryNameRaw || null,
            competition_name_raw: candidate.competitionNameRaw || null,
            source_competition_id: candidate.checkboxId || null,
            batch_index: null,
            outcome: 'SELECTION_FAILED',
            failure_reason: selectResult.reason,
          });
          remaining.shift();
        }
      }

      if (stuckWithNoSelection) {
        earlyStopReason = 'BATCH_STUCK_NO_SELECTION_POSSIBLE';
        break;
      }

      if (currentBatch.length === 0) {
        // Nothing left to do (remaining is empty) or every remaining
        // competition already failed individually above.
        break;
      }

      const showResult = await showLeaguesAndWait(doc);
      if (!showResult.ok) {
        competitionsFailed += currentBatch.length;
        for (const comp of currentBatch) {
          competitionResults.push({
            country_name_raw: comp.countryNameRaw || null,
            competition_name_raw: comp.competitionNameRaw || null,
            source_competition_id: comp.checkboxId || null,
            batch_index: batchIndex,
            outcome: 'BATCH_FAILED',
            failure_reason: showResult.reason,
          });
        }
        batchResults.push({
          batch_index: batchIndex,
          competition_ids: currentBatch.map((c) => c.checkboxId),
          ok: false,
          failure_reason: showResult.reason,
          records_seen: 0,
          records_parsed: 0,
          records_unresolved: 0,
          records_expected_unsupported: 0,
        });
        earlyStopReason = showResult.reason;
        batchIndex += 1;
        break;
      }

      const { envelope: subEnvelope } = Bet9jaCapture.captureFromDocument(doc, {
        sourceUrl: currentHref(doc, context.sourceUrl),
        pageTitle: context.pageTitle,
        capturedAtUtc,
        previousIndex: {},
        forced_sport_context: FORCED_SPORT_CONTEXT,
        resolve_table_competition: makeTableCompetitionResolver(currentBatch),
      });

      if (subEnvelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT')) {
        // parser.js's own independent check of the route/heading
        // disagreed with this module's trusted claim -- never guess past
        // this, fail the batch (and the whole run) closed rather than
        // risk mislabeling every fixture in it.
        competitionsFailed += currentBatch.length;
        for (const comp of currentBatch) {
          competitionResults.push({
            country_name_raw: comp.countryNameRaw || null,
            competition_name_raw: comp.competitionNameRaw || null,
            source_competition_id: comp.checkboxId || null,
            batch_index: batchIndex,
            outcome: 'BATCH_FAILED',
            failure_reason: 'SPORT_CONTEXT_CONFLICT',
          });
        }
        batchResults.push({
          batch_index: batchIndex,
          competition_ids: currentBatch.map((c) => c.checkboxId),
          ok: false,
          failure_reason: 'SPORT_CONTEXT_CONFLICT',
          records_seen: 0,
          records_parsed: 0,
          records_unresolved: 0,
          records_expected_unsupported: 0,
        });
        earlyStopReason = 'SPORT_CONTEXT_CONFLICT';
        batchIndex += 1;
        break;
      }

      let batchDuplicates = 0;
      for (const fixture of subEnvelope.fixtures) {
        if (seenFixtureIds.has(fixture.fixture_id)) {
          duplicatesSkipped += 1;
          batchDuplicates += 1;
          continue;
        }
        seenFixtureIds.add(fixture.fixture_id);
        // Precise attribution when parser.js's own per-table resolver
        // uniquely mapped this fixture's table (the expected, common
        // case for this batch selector); the batch-wide list is kept only
        // as a defensive fallback for the (should-never-happen) case of a
        // fixture with no resolved id at all despite an active resolver.
        const resolvedId = fixture.resolved_source_competition_id;
        const resolvedComp = resolvedId ? currentBatch.find((c) => c.checkboxId === resolvedId) : null;
        fixtures.push({
          ...fixture,
          source_batch_index: batchIndex,
          source_competition_ids_in_batch: resolvedComp ? [resolvedComp.checkboxId] : currentBatch.map((c) => c.checkboxId),
          source_competitions_raw_in_batch: resolvedComp
            ? [resolvedComp.competitionNameRaw]
            : currentBatch.map((c) => c.competitionNameRaw),
        });
      }
      for (const record of subEnvelope.unparsed_records) {
        unparsedRecords.push({ ...record, source_batch_index: batchIndex });
      }

      // Per-competition classification from parser.js's own
      // table_attribution_summary -- never assumed captured merely
      // because the batch as a whole produced some fixtures. A
      // competition is CAPTURED_IN_BATCH only if its own table resolved
      // AND had at least one row; BATCH_EMPTY only if its own table
      // resolved with zero rows (a confirmed, audited empty result); any
      // competition whose table never resolved at all is
      // COMPETITION_ATTRIBUTION_UNRESOLVED -- an honest "don't know",
      // never silently folded into either of the other two outcomes.
      const attributionByCompetitionId = new Map(
        (subEnvelope.table_attribution_summary || [])
          .filter((t) => t.resolved && t.source_competition_id)
          .map((t) => [t.source_competition_id, t])
      );
      for (const comp of currentBatch) {
        const attribution = attributionByCompetitionId.get(comp.checkboxId);
        let outcome;
        if (!attribution) {
          outcome = 'COMPETITION_ATTRIBUTION_UNRESOLVED';
          competitionsFailed += 1;
        } else if (attribution.row_count === 0) {
          outcome = 'BATCH_EMPTY';
          competitionsEmpty += 1;
        } else {
          outcome = 'CAPTURED_IN_BATCH';
          competitionsCaptured += 1;
        }
        competitionResults.push({
          country_name_raw: comp.countryNameRaw || null,
          competition_name_raw: comp.competitionNameRaw || null,
          source_competition_id: comp.checkboxId || null,
          batch_index: batchIndex,
          outcome,
          failure_reason: outcome === 'COMPETITION_ATTRIBUTION_UNRESOLVED' ? 'COMPETITION_ATTRIBUTION_UNRESOLVED' : null,
        });
      }
      batchResults.push({
        batch_index: batchIndex,
        competition_ids: currentBatch.map((c) => c.checkboxId),
        ok: true,
        failure_reason: null,
        records_seen: subEnvelope.coverage.records_seen,
        records_parsed: subEnvelope.coverage.records_parsed,
        records_unresolved: subEnvelope.coverage.records_unresolved,
        records_expected_unsupported: subEnvelope.coverage.records_expected_unsupported,
        duplicate_fixtures_skipped: batchDuplicates,
        content_change_confirmed: !!showResult.contentChangeConfirmed,
        table_attribution_summary: subEnvelope.table_attribution_summary || [],
      });

      const clearResult = await clearAllAndWait(doc, currentBatch.map((c) => c.checkboxId));
      if (!clearResult.ok) {
        earlyStopReason = clearResult.reason;
        batchIndex += 1;
        break;
      }

      batchIndex += 1;
    }

    if (remaining.length > 0 && !earlyStopReason) {
      // The safety cap on batches was hit with competitions still
      // pending -- an early stop in all but name.
      earlyStopReason = 'BATCHES_SAFETY_CAP_REACHED';
    }
    if (remaining.length > 0) {
      competitionsSkippedByEarlyStop += remaining.length;
    }

    const statusReasons = [];
    if (competitionsFailed > 0) statusReasons.push('SOME_COMPETITIONS_FAILED');
    if (competitionResults.some((r) => r.outcome === 'COMPETITION_ATTRIBUTION_UNRESOLVED')) {
      statusReasons.push('SOME_COMPETITIONS_ATTRIBUTION_UNRESOLVED');
    }
    if (competitionsSkippedBySafetyCap > 0) statusReasons.push('COMPETITIONS_SKIPPED_BY_SAFETY_CAP');
    if (countriesFailed > 0) statusReasons.push('SOME_COUNTRIES_FAILED');
    if (earlyStopReason) statusReasons.push(`STOPPED_EARLY_${earlyStopReason}`);

    const competitionsInvariantHolds =
      competitionsAvailable ===
      competitionsCaptured + competitionsEmpty + competitionsFailed + competitionsSkippedBySafetyCap + competitionsSkippedByEarlyStop;
    if (!competitionsInvariantHolds) statusReasons.unshift('COMPETITION_ACCOUNTING_INVARIANT_VIOLATED');

    let captureStatus;
    if (fixtures.length === 0 && unparsedRecords.length === 0 && competitionsEmpty === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else if (
      countriesFailed === 0 &&
      competitionsFailed === 0 &&
      competitionsSkippedBySafetyCap === 0 &&
      !earlyStopReason &&
      competitionsInvariantHolds
    ) {
      captureStatus = 'CAPTURE_COMPLETE';
    } else {
      captureStatus = 'CAPTURE_PARTIAL';
    }

    const canResume = !!earlyStopReason;
    const resumeMetadata = canResume
      ? {
          can_resume: true,
          last_completed_competition_id: lastCompletedCheckboxId,
          resume_hint:
            'Re-run this capture; already-captured fixtures are deduplicated automatically, so resuming from the beginning is always safe, just slower than resuming from the exact competition named here.',
        }
      : { can_resume: false, last_completed_competition_id: null, resume_hint: null };

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        inventory_source_url: inventorySourceUrl,
        competitions_route_confirmed: true,
        countries_available: countriesAvailable,
        countries_visited: countriesVisited,
        countries_failed: countriesFailed,
        competitions_available: competitionsAvailable,
        competitions_captured: competitionsCaptured,
        competitions_empty: competitionsEmpty,
        competitions_failed: competitionsFailed,
        competitions_skipped_by_safety_cap: competitionsSkippedBySafetyCap,
        competitions_skipped_by_early_stop: competitionsSkippedByEarlyStop,
        duplicates_skipped: duplicatesSkipped,
        batch_results: batchResults,
        competition_results: competitionResults,
        fixtures,
        unparsed_records: unparsedRecords,
        resume_metadata: resumeMetadata,
      },
    };
  }

  const api = {
    captureAllSoccerCompetitions,
    SELECTORS,
    PARSER_VERSION,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerWalker = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
