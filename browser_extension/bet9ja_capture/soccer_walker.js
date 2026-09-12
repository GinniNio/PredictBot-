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
 * CONFIRMED REAL HIERARCHY (live-DOM evidence, 2026-09-12; root corrected
 * Round 7 after PR #38's two real captures both reached this exact route
 * and still returned `NO_COUNTRIES_DISCOVERED`):
 *
 *   `/sportPage/1/competitions`
 *   -> `.accordion.accordion-soccer` (the ACTUAL country-inventory root)
 *      -> `.competitions` (a SIBLING block -- the Popular-competitions
 *         selection/results panel, never a country and never the
 *         discovery root itself; Round 5/6 wrongly used exactly this
 *         element as `pageRoot`)
 *      -> one `.accordion-item` DIRECT CHILD per country
 *         -> `.competitions__group-item` rows, each holding a
 *            `.sportpage__cb-input` checkbox (id = the competition's own
 *            stable numeric id, e.g. `1209691` for Nigeria's
 *            "Professional Football League") with its own
 *            `<label for="{id}">` -- the checkbox itself is marked
 *            `readonly`, so selection happens through its label,
 *            matching the site's own UI behavior.
 *   `.competitions__filter-btn.check-coupon` ("Show Leagues") renders
 *   every currently-checked competition's fixtures into the page's
 *   existing `.sports-table`/`.sports-table__matchup` structure (already
 *   fully supported by parser.js) WITHOUT changing the URL.
 *   `.competitions__filter-btn.clear-all` clears every current
 *   selection, confirmed necessary before starting the next batch.
 *
 * CLIENT-RENDER TIMING (real evidence, 2026-09-12): immediately after
 * DOMContentLoaded the live page has ZERO `.accordion-item` elements at
 * all; the full inventory (141 countries, in the one real inspection)
 * only exists roughly 1.8 SECONDS later. This is the second real cause
 * behind both of PR #38's captures failing at the exact same route: the
 * popup injected and this module started discovery before that render
 * ever completed. `waitForCountryInventoryReady` waits for the root
 * AND for the discovered country COUNT to stop changing across
 * consecutive polls (not merely become non-zero) before discovery ever
 * begins -- see its own comment for the three typed, distinct timeout
 * outcomes this replaced a single immediate `NO_COUNTRIES_DISCOVERED`
 * with.
 *
 * IDENTITY: `source_competition_id` is read directly from the checkbox's
 * own `id` attribute -- no composite `sg-`/`g-` id-parsing is needed here
 * (unlike the retired per-competition accordion), since Bet9ja's own
 * competition id is already the checkbox's id verbatim.
 * `country_name_raw`/`competition_name_raw` are read from each control's
 * own visible text, never prettified.
 *
 * BATCHING (Round 8 correction): a real run selected all 374 discovered
 * competitions into ONE giant batch before ever clicking Show Leagues --
 * nothing had capped batch size below Bet9ja's own (still unconfirmed)
 * selection limit, and the screenshot showed Bet9ja still rendering
 * multiple leagues when the capture gave up waiting. `MAX_COMPETITIONS_
 * PER_BATCH` deliberately caps every batch at exactly ONE competition for
 * this first reliable loop -- "capture everything in one go" means one
 * user click automating many small batches, never one enormous render.
 * Bet9ja's own selection limit (surfaced via a "Maximum selection limit
 * reached!" notification whose exact wording/selector is [UNVERIFIED],
 * see SOCCER_ALL_COMPETITIONS_VALIDATION.md) is still watched
 * operationally (a selection that doesn't stick, or that notification's
 * text becoming visible), but with the cap at 1 it should never actually
 * need to fire in practice. Only once a real run completes reliably at
 * batch size 1 should this cap ever be raised, and only from that same
 * evidence bar -- never lowered or raised blindly by a future edit.
 *
 * SPORT CONTEXT (Round 6, corrected Round 8): parser.js resolves each
 * row's sport either from an id-embedded `sport-N` segment on the row
 * itself, or from the page's own URL matching
 * `/competition/{sport}/{country}/{competition}/` -- confirmed only for
 * single-competition competition pages, and neither is available on
 * `/sportPage/1/competitions`. This module passes a trusted
 * `forced_sport_context` claim that parser.js independently re-verifies.
 *
 * ROUND 10 CORRECTION (real evidence: a 13:05:54 diagnostic capture): a
 * page-level visible sport heading (Round 6) and a rendered "Soccer >"
 * competition breadcrumb (Round 8) were both used as gate signals, and
 * both turned out not to exist in the assumed locations on the real page
 * -- content readiness fully passed (two `.sports-table`s, four ready
 * matchup rows, zero loading indicators) while the page-level heading was
 * unresolved and `.sports-table.previousElementSibling` held date/
 * market-column text, not a breadcrumb. The gate now checks three
 * machine-verifiable conditions instead of any rendered heading: the
 * page's own route, this module's own declared `capture_scope`, and this
 * module's own trusted claim of which competition ids it selected
 * (`selected_competition_ids`, built fresh per batch by
 * `buildForcedSportContext`) -- see parser.js's own comment for the full
 * detail. This narrows what the gate checks; it still fails the whole
 * capture closed (`SPORT_CONTEXT_CONFLICT`) on a genuine mismatch.
 *
 * ATTRIBUTION: since `MAX_COMPETITIONS_PER_BATCH = 1` means every batch
 * that reaches `makeTableCompetitionResolver` contains EXACTLY one
 * selected competition, every rendered `.sports-table` in that batch is
 * now attributed to that one competition unconditionally -- there is no
 * real ambiguity to resolve via a heading when only one competition was
 * ever selected. The earlier breadcrumb-based heading-matching logic
 * (parses a confirmed "Soccer > {country} > {competition}" breadcrumb's
 * own last segment, falling back to a substring search) is kept,
 * unchanged, as a DORMANT fallback for a hypothetical future
 * `MAX_COMPETITIONS_PER_BATCH > 1` -- it is unreachable in production
 * today. A table that cannot be uniquely mapped (only possible on that
 * dormant multi-competition path) is retained as
 * `COMPETITION_ATTRIBUTION_UNRESOLVED` (parser.js's own gate, ahead of
 * every other row classification) rather than guessed. Every fixture still carries
 * `source_batch_index` and `source_competition_ids_in_batch`/
 * `source_competitions_raw_in_batch` (single-element in the normal case);
 * `competition_results[]` classifies every discovered competition
 * individually (`CAPTURED_IN_BATCH`/`BATCH_EMPTY`/
 * `COMPETITION_ATTRIBUTION_UNRESOLVED`/`SELECTION_FAILED`/`BATCH_FAILED`/
 * `NOT_ATTEMPTED_AFTER_EARLY_STOP` -- the last one only for a competition
 * genuinely never reached after an early stop, never confused with a
 * real failure), and each `batch_results[]` entry carries its own honest
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

  // NOT bumped to a non-"-unverified" tag yet: real runs got past
  // country/competition discovery, batching, and content readiness (all
  // confirmed working by two real captures -- see
  // SOCCER_ALL_COMPETITIONS_VALIDATION.md's Round 8/9 sections) but then
  // hit `SPORT_CONTEXT_CONFLICT` on both, traced (Round 10, real
  // diagnostic evidence) to two heading-based gate/attribution checks
  // that assumed rendered content this page never has. This round
  // replaces those checks with machine-verifiable conditions (see the
  // header comment above) -- not yet exercised against the live account.
  // Per this project's evidence-only versioning discipline, the version
  // string advances only after one real successful capture.
  const PARSER_VERSION = 'bet9ja-soccer-walker@0.5.0-round10-sport-context-gate-and-attribution-fix-unverified';

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
  // parser.js independently re-verifies it before ever trusting it, and
  // fails the whole capture closed (`SPORT_CONTEXT_CONFLICT`) if it
  // disagrees -- this module never classifies a single row itself.
  //
  // ROUND 10 CORRECTION: this claim now also carries
  // `selected_competition_ids` -- this module's OWN trusted record of
  // which checkbox ids it actually selected from the Soccer inventory for
  // THIS batch, one of the three machine-verifiable conditions
  // parser.js's gate now checks (see parser.js's own comment for why the
  // two heading-based checks this replaced were retired). Built fresh per
  // batch (never a static constant) since the id list is batch-specific.
  function buildForcedSportContext(currentBatch) {
    return {
      forced_sport_hint: 'SOCCER',
      forced_sport_source: 'SPORTPAGE_ROUTE_ID',
      forced_sport_source_value: '1',
      capture_scope: CAPTURE_SCOPE,
      selected_competition_ids: currentBatch.map((c) => c.checkboxId),
    };
  }

  // Confirmed via live inspection, 2026-09-12. See the header comment
  // above for the full contract. Exported as a mutable object so a
  // future correction never requires touching any other code, and so
  // tests can exercise this logic against synthetic markup shaped like
  // the confirmed real structure.
  const SELECTORS = {
    // ROUND 7 CORRECTION (2026-09-12, real-DOM evidence): `.competitions`
    // is NOT the country-inventory root -- it is the Popular-competitions
    // selection/results block, a SIBLING of the actual country accordion
    // items, not their container. Both uploaded real captures reached
    // the correct route and still reported `NO_COUNTRIES_DISCOVERED`
    // because of exactly this: discovery was scoped to the wrong element,
    // so it could never find the `.accordion-item`s living outside it.
    // The confirmed real container is `.accordion.accordion-soccer`; a
    // `.competitions` block lives INSIDE it as one of several siblings,
    // alongside the real per-country `.accordion-item`s.
    pageRoot: '.accordion.accordion-soccer',
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

  const ACCORDION_TIMEOUT_MS = 3000;
  const SELECTION_TIMEOUT_MS = 1500;
  const SHOW_LEAGUES_CONTENT_TIMEOUT_MS = 5000;
  const CLEAR_ALL_TIMEOUT_MS = 2000;
  const POLL_INTERVAL_MS = 25;

  // Real evidence, 2026-09-12: immediately after DOMContentLoaded the
  // live page has zero `.accordion-item` elements; the full 141-country
  // inventory only exists ~1.8s later, client-rendered. 10000ms/100ms
  // are generous multiples of that observed delay, never a guess.
  // Shared as ONE budget across both "root appears" and "country count
  // settles", matching how the two conditions were observed together in
  // the real page, not as two independently-invented timeouts.
  const SOCCER_COMPETITIONS_ROOT_TIMEOUT_MS = 10000;
  const SOCCER_COMPETITIONS_ROOT_POLL_MS = 100;
  // CORRECTION: two real captures at different times DID discover
  // different totals (102 countries/368 competitions, then 103/374) --
  // but that difference is NOT evidence of same-run instability. Bet9ja's
  // own competition inventory changes over time (a league starting or
  // finishing its round, a fixture window opening) exactly like any
  // other sportsbook's -- comparing counts ACROSS two separate captures,
  // or expecting a fixed total across days, is never a valid basis for
  // detecting an incomplete discovery. What IS real evidence (confirmed
  // the same way the country root itself was in Round 7: 0 elements
  // immediately after DOMContentLoaded, the full list only ~1.8s later)
  // is that a SINGLE capture's own discovery can start reading a country
  // or competition list before Bet9ja finishes rendering it. This module
  // only ever freezes and reconciles the inventory actually visible
  // DURING one capture (see `captured_at_utc` on the envelope) -- a
  // bounded number of full re-discovery passes, not a single snapshot,
  // is what `discoverStableInventory` uses to confirm THIS run's own
  // totals stopped changing within its own short discovery window, never
  // to chase a fixed total across separate captures.
  const INVENTORY_STABILIZATION_MAX_PASSES = 5;
  // Real evidence: 100+ countries and (per the retired per-competition
  // walker's own real capture) dozens of competitions per country are
  // plausible; this is a generous multiple of any plausible real total,
  // kept as a hard backstop against an unbounded loop, never relied upon
  // normally. Any competition beyond the cap is explicitly counted as
  // `competitions_skipped_by_safety_cap`, never silently dropped.
  const MAX_TOTAL_COMPETITIONS_SAFETY_CAP = 2000;
  const MAX_BATCHES_SAFETY_CAP = 2000;

  // ROUND 8 CORRECTION: a real run selected all 374 discovered
  // competitions into ONE batch before ever clicking Show Leagues,
  // because nothing capped batch size below Bet9ja's own (still
  // unconfirmed) selection limit. Bet9ja was still rendering multiple
  // leagues when the capture gave up waiting -- "capture everything in
  // one go" was meant to describe one user click automating 374 batches,
  // never one single enormous Bet9ja render. Deliberately conservative
  // for the first reliable loop: exactly one competition per batch. Once
  // a real run completes reliably, this can be safely raised -- never
  // lowered blindly by a future edit without that same evidence bar.
  const MAX_COMPETITIONS_PER_BATCH = 1;

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

  /**
   * DIRECT children only (`:scope >`), never a full-descendant query --
   * `.accordion-item` also appears nested inside a country's own expanded
   * `.accordion-content` (there is no confirmed limit on accordion
   * nesting), and a full-descendant query would misclassify those as
   * top-level countries. A candidate must also carry its own
   * `:scope > .accordion-toggle .accordion-text` (the country's own
   * name), which the Popular-competitions `.competitions` sibling block
   * (a plain results/selection panel, not an accordion item at all)
   * never has -- filtering on this, not just the class name, is what
   * keeps that sibling block from ever being misread as a country.
   */
  function discoverCountryAccordions(competitionsRoot) {
    if (!competitionsRoot) return [];
    return Array.from(competitionsRoot.querySelectorAll(':scope > .accordion-item')).filter(
      (item) => !!item.querySelector(':scope > .accordion-toggle .accordion-text')
    );
  }

  /**
   * Waits for the client-rendered country inventory to actually exist
   * AND settle before discovery ever begins. Real evidence: immediately
   * after DOMContentLoaded the page has zero `.accordion-item` elements
   * at all; the full inventory (141 countries, in the one real
   * inspection) only exists ~1.8s later. A single "count > 0" check is
   * not enough on its own -- countries render asynchronously and a
   * capture that started discovery too early would silently walk only
   * the first few. This polls until the discovered country COUNT itself
   * stops changing between two consecutive polls (not just becomes
   * non-zero), sharing one overall timeout budget with the root-element
   * wait itself.
   *
   * Three distinct, typed outcomes, never conflated: the root element
   * itself never appearing (`SOCCER_COMPETITIONS_ROOT_TIMEOUT`); the root
   * appearing but never gaining a single country within the budget
   * (`SOCCER_COUNTRY_INVENTORY_TIMEOUT`); and the root appearing with
   * countries that kept appearing/changing without ever settling
   * (`SOCCER_COUNTRY_INVENTORY_UNSTABLE`) -- the latter two are both real
   * evidence of an incomplete inventory, but are different enough
   * (nothing at all vs. still growing) to keep separate for diagnosis.
   */
  async function waitForCountryInventoryReady(doc) {
    const deadline = Date.now() + SOCCER_COMPETITIONS_ROOT_TIMEOUT_MS;

    let root = doc.querySelector(SELECTORS.pageRoot);
    while (!root && Date.now() < deadline) {
      await sleep(SOCCER_COMPETITIONS_ROOT_POLL_MS);
      root = doc.querySelector(SELECTORS.pageRoot);
    }
    if (!root) {
      return { ok: false, reason: 'SOCCER_COMPETITIONS_ROOT_TIMEOUT', root: null };
    }

    let previousCount = null;
    while (Date.now() < deadline) {
      const currentCount = discoverCountryAccordions(root).length;
      if (currentCount > 0 && currentCount === previousCount) {
        return { ok: true, reason: null, root };
      }
      previousCount = currentCount;
      await sleep(SOCCER_COMPETITIONS_ROOT_POLL_MS);
    }

    const finalCount = discoverCountryAccordions(root).length;
    return {
      ok: false,
      reason: finalCount === 0 ? 'SOCCER_COUNTRY_INVENTORY_TIMEOUT' : 'SOCCER_COUNTRY_INVENTORY_UNSTABLE',
      root,
    };
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

  // Real evidence, 2026-09-12: a rendered competition heading is a
  // breadcrumb shaped "Soccer > {country} > {competition}" (e.g.
  // "Soccer > Italy > Serie A"). This is used ONLY for per-table
  // ATTRIBUTION here -- kept entirely separate from parser.js's own
  // page-level sport-context gate (`validateForcedSportContext`), which
  // never parses this breadcrumb into a country/competition, only checks
  // its "Soccer >" prefix as a coarse corroborating signal. A change to
  // one must never silently affect the other.
  const COMPETITION_BREADCRUMB_PATTERN = /^soccer\s*>\s*(.+)$/i;

  /**
   * Parses a confirmed "Soccer > {country} > {competition}" breadcrumb
   * into its segments. Returns `null` for anything not shaped like that
   * (never guessed) -- the caller falls back to whole-text substring
   * matching in that case, for resilience against an unconfirmed heading
   * shape.
   */
  function parseCompetitionBreadcrumb(headingRaw) {
    const match = (headingRaw || '').trim().match(COMPETITION_BREADCRUMB_PATTERN);
    if (!match) return null;
    const rest = match[1].split('>').map((s) => s.trim()).filter(Boolean);
    if (rest.length === 0) return null;
    return {
      // "Soccer > Country > Competition" -> competition is the LAST
      // segment; "Soccer > Competition" (no country segment) is also
      // accepted, since it's not confirmed every real heading carries a
      // country -- country then stays unconfirmed rather than guessed.
      country: rest.length >= 2 ? rest[rest.length - 2] : null,
      competition: rest[rest.length - 1],
    };
  }

  /**
   * Builds a `resolve_table_competition` function (parser.js's own
   * contract -- see its header comment) scoped to exactly the
   * competitions selected in ONE batch.
   *
   * ROUND 10 CORRECTION (real evidence: 13:05:54 diagnostic capture):
   * `MAX_COMPETITIONS_PER_BATCH = 1` means every batch that ever reaches
   * this function today contains EXACTLY one selected competition -- so
   * there is no real ambiguity a heading could ever resolve: every
   * rendered `.sports-table` in that batch's own captured document
   * necessarily belongs to that one competition, because it is the only
   * competition Show Leagues was ever asked to render. The same real
   * capture that motivated this also showed the heading-matching approach
   * below could never have worked here anyway (`.sports-table`'s own
   * previous sibling holds date/market-column text, not a competition
   * breadcrumb) -- so for `currentBatch.length === 1` this function
   * bypasses heading matching entirely and resolves unconditionally.
   *
   * The heading-matching logic (breadcrumb-exact-match, falling back to a
   * substring search) is kept below, UNCHANGED, as a dormant fallback: it
   * only runs if `MAX_COMPETITIONS_PER_BATCH` is ever raised above 1 in a
   * future round with its own real evidence, at which point a genuinely
   * ambiguous multi-competition batch would need it again.
   */
  function makeTableCompetitionResolver(currentBatch) {
    if (currentBatch.length === 1) {
      const only = currentBatch[0];
      return () => ({
        resolved: true,
        sourceCompetitionId: only.checkboxId,
        competitionNameRaw: only.competitionNameRaw,
        countryNameRaw: only.countryNameRaw,
      });
    }

    function uniqueMatch(predicate) {
      const matches = currentBatch.filter(predicate);
      return matches.length === 1 ? matches[0] : null;
    }

    return (tableEl) => {
      const headingRaw = resolveNearestCompetitionHeadingRaw(tableEl);
      if (!headingRaw) return { resolved: false };

      const breadcrumb = parseCompetitionBreadcrumb(headingRaw);
      let match = null;
      if (breadcrumb) {
        const normalizedBreadcrumbCompetition = normalizeForMatch(breadcrumb.competition);
        match = uniqueMatch((c) => normalizedBreadcrumbCompetition && normalizedBreadcrumbCompetition === normalizeForMatch(c.competitionNameRaw));
      }
      if (!match) {
        const normalizedHeading = normalizeForMatch(headingRaw);
        match = uniqueMatch((c) => {
          const normalizedName = normalizeForMatch(c.competitionNameRaw);
          return normalizedName && normalizedHeading.includes(normalizedName);
        });
      }
      if (!match) return { resolved: false };
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
   * Expands one country's accordion (if not already open) and waits for
   * its OWN competition COUNT to stop changing across two consecutive
   * polls -- not merely become non-zero, within THIS capture's own short
   * discovery window. A `> 0` check on the country root already made
   * this same mistake once (Round 7's own fix); the same
   * asynchronous-rendering behavior plausibly applies one level down,
   * per country, too. This is never a check against any OTHER capture's
   * own totals -- two separate real captures discovering different
   * totals (102 countries/368 competitions, then 103/374) reflects
   * Bet9ja's real inventory changing between them, not an unstable
   * discovery within either one.
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
    const deadline = Date.now() + ACCORDION_TIMEOUT_MS;
    let previousCount = null;
    while (Date.now() < deadline) {
      const currentCount = discoverCompetitionsInCountry(countryItem).length;
      if (currentCount > 0 && currentCount === previousCount) {
        return { ok: true, reason: null };
      }
      previousCount = currentCount;
      await sleep(POLL_INTERVAL_MS);
    }
    const finalCount = discoverCompetitionsInCountry(countryItem).length;
    return { ok: false, reason: finalCount === 0 ? 'COUNTRY_COMPETITION_LIST_EMPTY' : 'COUNTRY_COMPETITION_LIST_UNSTABLE' };
  }

  /** One full discovery pass: expand every country, enumerate every competition. */
  async function discoverAllCompetitionsOnePass(competitionsRoot, shouldCancel) {
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
    return { countryItems, countriesVisited, countriesFailed, allCompetitions };
  }

  /**
   * Stabilizing the country ROOT alone (Round 7) does not confirm every
   * country's OWN competition list has also finished rendering by the
   * time it's read -- the same asynchronous-rendering behavior Round 7
   * confirmed for the root plausibly applies one level down too. Re-runs
   * the ENTIRE discovery pass (every country re-expanded, every
   * competition list re-enumerated) until two CONSECUTIVE full passes,
   * WITHIN this one capture's own short discovery window, agree on BOTH
   * the country count and the total competition count, bounded by a
   * fixed number of attempts rather than a single "looks stable"
   * snapshot. This is never a cross-capture check: Bet9ja's real
   * inventory can and does change between separate captures (a league's
   * round starting or finishing, a fixture window opening) -- two
   * different real captures discovering two different totals (102
   * countries/368 competitions, then 103/374) is normal, expected drift
   * over time, not evidence this stabilization loop needs to chase a
   * fixed total across days. Only what is visible during THIS run is
   * ever frozen and reconciled, timestamped by the envelope's own
   * `captured_at_utc`.
   */
  async function discoverStableInventory(competitionsRoot, shouldCancel) {
    let previousSignature = null;
    let lastResult = null;
    for (let attempt = 0; attempt < INVENTORY_STABILIZATION_MAX_PASSES; attempt += 1) {
      const result = await discoverAllCompetitionsOnePass(competitionsRoot, shouldCancel);
      lastResult = result;
      const signature = `${result.countryItems.length}:${result.allCompetitions.length}`;
      if (previousSignature === signature) {
        return { ok: true, result };
      }
      previousSignature = signature;
      if (shouldCancel()) {
        // A genuine user cancellation stops further stabilization
        // attempts and uses whatever was found so far -- best-effort,
        // never a hard failure.
        return { ok: true, result };
      }
    }
    return { ok: false, reason: 'SOCCER_COMPETITION_INVENTORY_UNSTABLE', result: lastResult };
  }

  function getFixtureFingerprint(doc) {
    return Array.from(doc.querySelectorAll(SELECTORS.matchup))
      .map((el) => text(el))
      .join('|');
  }

  // [UNVERIFIED] exact selector for a loading indicator on this page --
  // the screenshot showed `.sports-table` elements existing while their
  // rows were still loading, but no specific indicator markup was
  // captured. Matched by a common class-name pattern rather than a
  // guessed exact class; `.hidden`/`[hidden]` is respected the same way
  // `isLimitNotificationVisible`'s own notification element is, since
  // `offsetParent`-based visibility has no meaning in jsdom (no layout
  // engine) and would make every candidate look permanently invisible in
  // tests.
  function isLoadingIndicatorVisible(doc) {
    const candidates = doc.querySelectorAll('[class*="loading" i], [class*="spinner" i], [class*="skeleton" i]');
    return Array.from(candidates).some((el) => !el.hidden);
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
   * ROUND 8 CORRECTION: the screenshot from a real run showed
   * `.sports-table` elements that already EXISTED while Bet9ja was still
   * rendering their rows -- the old check (fixture text changed, OR the
   * table's mere presence flipped) accepted a table that existed but
   * whose content was still loading, so a capture could run against
   * half-rendered content. Clicks "Show Leagues" and now waits for BOTH:
   * (1) no loading indicator visible, AND (2) the matchup ROW COUNT
   * itself to stop changing across two consecutive polls (not merely
   * become non-zero) -- the same stability discipline already used for
   * the country inventory. A batch stably settling at ZERO rows is
   * accepted as a confirmed-empty result (there is no confirmed
   * "empty-state" element to check for instead), not a failure. Only a
   * genuine bounded timeout without ever reaching that stable state is a
   * hard failure.
   */
  /**
   * Sanitized, bounded audit of the DOM state at the exact moment
   * `showLeaguesAndWait` finishes (success or timeout) -- named fields
   * only, never full HTML -- so a real `SHOW_LEAGUES_CONTENT_TIMEOUT` (or
   * a suspiciously fast/slow success) never has to be diagnosed blind.
   * `empty_states_seen` is always 0: no confirmed empty-state selector
   * exists yet (`[UNVERIFIED]`), recorded honestly rather than guessed.
   */
  function buildReadinessDiagnostics(doc, stablePollCount) {
    return {
      loading_indicators_remaining: Array.from(
        doc.querySelectorAll('[class*="loading" i], [class*="spinner" i], [class*="skeleton" i]')
      ).filter((el) => !el.hidden).length,
      sports_tables_seen: doc.querySelectorAll(SELECTORS.fixtureRoot).length,
      matchup_rows_seen: doc.querySelectorAll(SELECTORS.matchup).length,
      empty_states_seen: 0,
      stable_poll_count: stablePollCount,
    };
  }

  async function showLeaguesAndWait(doc) {
    const button = doc.querySelector(SELECTORS.showLeaguesButton);
    if (!button) {
      return { ok: false, reason: 'SHOW_LEAGUES_BUTTON_NOT_FOUND', diagnostics: null };
    }
    const before = getFixtureFingerprint(doc);
    button.click();

    const deadline = Date.now() + SHOW_LEAGUES_CONTENT_TIMEOUT_MS;
    let previousCount = null;
    let stablePollCount = 0;
    while (Date.now() < deadline) {
      if (isLoadingIndicatorVisible(doc)) {
        // Still loading -- any stability observed so far doesn't count.
        previousCount = null;
        stablePollCount = 0;
      } else {
        const currentCount = doc.querySelectorAll(SELECTORS.matchup).length;
        if (previousCount !== null && currentCount === previousCount) {
          stablePollCount += 1;
          return {
            ok: true,
            reason: null,
            contentChangeConfirmed: getFixtureFingerprint(doc) !== before,
            diagnostics: buildReadinessDiagnostics(doc, stablePollCount),
          };
        }
        previousCount = currentCount;
        stablePollCount = 1;
      }
      await sleep(POLL_INTERVAL_MS);
    }
    return { ok: false, reason: 'SHOW_LEAGUES_CONTENT_TIMEOUT', diagnostics: buildReadinessDiagnostics(doc, stablePollCount) };
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

    // Both uploaded real captures reached this exact route and still
    // reported NO_COUNTRIES_DISCOVERED -- discovery was starting before
    // the client-rendered country inventory existed at all (confirmed
    // real evidence: 0 `.accordion-item` elements immediately after
    // DOMContentLoaded, 141 roughly 1.8s later). NO_COUNTRIES_DISCOVERED
    // is never returned until this readiness wait has fully completed --
    // see waitForCountryInventoryReady's own comment for the three typed
    // outcomes this replaces a single bare "not ready" check with.
    const inventoryReady = await waitForCountryInventoryReady(doc);
    if (!inventoryReady.ok) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: [inventoryReady.reason],
          inventory_source_url: inventorySourceUrl,
          competitions_route_confirmed: true,
          ...emptyEnvelopeShape(),
        },
      };
    }

    const competitionsRoot = inventoryReady.root;

    // --- Phase 1: discovery -- expand every country, enumerate every
    // competition checkbox, and keep re-discovering until BOTH totals
    // stop changing across consecutive full passes. No selection happens
    // yet.
    const stableInventory = await discoverStableInventory(competitionsRoot, shouldCancel);
    if (!stableInventory.ok) {
      const partial = stableInventory.result;
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: [stableInventory.reason],
          inventory_source_url: inventorySourceUrl,
          competitions_route_confirmed: true,
          countries_available: partial ? partial.countryItems.length : 0,
          countries_visited: partial ? partial.countriesVisited : 0,
          countries_failed: partial ? partial.countriesFailed : 0,
          ...emptyEnvelopeShape(),
        },
      };
    }
    const { countryItems, countriesVisited, countriesFailed, allCompetitions } = stableInventory.result;
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

      while (remaining.length > 0 && currentBatch.length < MAX_COMPETITIONS_PER_BATCH) {
        if (shouldCancel()) {
          earlyStopReason = 'USER_CANCELLED';
          // Nothing in this partially-filled batch was ever shown/parsed
          // -- put it back at the front of `remaining` so it is reported
          // as NOT_ATTEMPTED_AFTER_EARLY_STOP below, never silently lost.
          remaining.unshift(...currentBatch);
          break batchLoop;
        }
        const candidate = remaining[0];
        const selectResult = await selectCompetition(doc, candidate.checkboxId);
        if (selectResult.outcome === 'SELECTED') {
          // NOT recorded as "completed" here -- a checkbox sticking is
          // merely a successful selection, not a confirmed capture. See
          // the per-competition classification block below, the ONLY
          // place `lastCompletedCheckboxId` is ever updated.
          currentBatch.push(candidate);
          remaining.shift();
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
          content_readiness_diagnostics: showResult.diagnostics || null,
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
        forced_sport_context: buildForcedSportContext(currentBatch),
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
          content_readiness_diagnostics: showResult.diagnostics || null,
          sport_context_diagnostics: subEnvelope.sport_context_diagnostics || null,
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
        if (outcome === 'CAPTURED_IN_BATCH' || outcome === 'BATCH_EMPTY') {
          // The ONLY place this is ever set -- a genuinely confirmed
          // capture or a confirmed-empty result, never a mere checkbox
          // selection. Iteration order within `currentBatch` (batch size
          // 1 in practice) means this always ends up naming the last
          // competition actually completed, never one merely attempted.
          lastCompletedCheckboxId = comp.checkboxId;
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
        content_readiness_diagnostics: showResult.diagnostics || null,
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
      // Every competition never even selected/attempted after an early
      // stop gets its own explicit, honestly-named result row -- never
      // silently absent from competition_results[], and never counted as
      // "failed" (it was never genuinely attempted at all).
      for (const comp of remaining) {
        competitionResults.push({
          country_name_raw: comp.countryNameRaw || null,
          competition_name_raw: comp.competitionNameRaw || null,
          source_competition_id: comp.checkboxId || null,
          batch_index: null,
          outcome: 'NOT_ATTEMPTED_AFTER_EARLY_STOP',
          failure_reason: null,
        });
      }
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
    // Exported for direct unit testing of the single-competition-batch
    // bypass and the dormant multi-competition heading-matching fallback
    // (see this function's own comment) -- not used by any other module.
    makeTableCompetitionResolver,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerWalker = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
