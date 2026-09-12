/**
 * Bet9ja fixture-capture parser -- pure DOM-in, envelope-out logic shared by
 * the content script (real browser) and the Node test suite (jsdom).
 *
 * Scope (Release 1, see README.md):
 *   - Reads the DOM already loaded in the tab. No network requests, no
 *     cookies, no tokens, no account/balance/betslip data are ever read.
 *   - Fully normalizes pre-match Soccer 1X2 only.
 *   - Everything else (other sports, other markets, live/virtual/zoom
 *     products, incomplete markets, unrecognized layout) is preserved in
 *     `unparsed_records` with a typed reason -- never silently dropped,
 *     never guessed at.
 *
 * SELECTOR CONTRACT -- two independent profiles, tried in order:
 *
 *   1. LEGACY (SELECTORS below) -- the original documented, best-effort
 *      assumption about Bet9ja's markup, tuned against the sanitized
 *      dev fixtures in tests/fixtures/. Kept in place for those fixtures
 *      and as a fallback should a future page redesign match it.
 *
 *   2. BET9JA_DESKTOP (BET9JA_DESKTOP_SELECTORS below) -- confirmed via
 *      live inspection of https://sports.bet9ja.com/sport/soccer/1 (the
 *      "Highlights" page) and four competition pages -- Eredivisie,
 *      Premier League, LaLiga, Ligue 1 (round 2, 2026-09-11): rows are
 *      `.table-f` elements that are direct children of a `.sports-table`
 *      container, interspersed with `.sports-head__date` heading elements
 *      that group the rows below them by date (NOT by competition/league
 *      -- no such DOM wrapper has been confirmed; competition pages
 *      instead carry country/competition in their own URL, which
 *      parseBet9jaCompetitionUrl parses directly rather than guessing from
 *      DOM text). The Highlights and competition pages differ in one way:
 *      the Highlights page's row ids carry a "sport-N" segment (confirmed
 *      sport-1 == Soccer); competition-page ids don't, so sport is instead
 *      resolved from the URL there. Used ONLY when the LEGACY root
 *      selector matches nothing at all. Reports CAPTURE_PARTIAL with a
 *      dedicated reason regardless of how cleanly its rows parse: this
 *      page's collapsed-section/lazy-loading markup (if any) is still
 *      unconfirmed, so full-page coverage is not a guarantee the way the
 *      LEGACY root-scoped path is designed to make it. See README.md
 *      "Real-page validation status" for exactly what has and hasn't
 *      been confirmed, and tests/fixtures/bet9ja_desktop_real_sample.html's
 *      own comment for this profile's known open gaps (no live/virtual/
 *      Zoom sample seen yet, so every row is treated as pre-match; no
 *      confirmed date-string format from `.sports-head__date`, so
 *      `date_heading_raw` is recorded for audit but never used to derive
 *      `kickoff_utc`).
 */
(function (root) {
  // In-browser: ids.js is loaded as an earlier <script> in the same
  // content-script context and attaches itself to `window.Bet9jaIds`. In
  // Node (test suite): load it directly via require, since there is no
  // shared global script-loading order to rely on.
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-capture-parser@1.0.0';

  const SELECTORS = {
    root: '[data-bet9ja-capture-root], .odds-board, .prematch-board, #prematch',
    competitionGroup: '.competition-group',
    competitionHeader: '.competition-header',
    fixtureRow: '.fixture-row',
    home: '.participants .home',
    away: '.participants .away',
    kickoff: '.kickoff',
    market: '.market',
    outcome: '.outcome',
    outcomeLabel: '.outcome-label',
    outcomePrice: '.outcome-price',
    showMore: '.show-more-button, [data-show-more]',
    lazyPlaceholder: '.lazy-loading-placeholder, [data-lazy-loading]',
  };

  // Real Bet9ja desktop sportsbook markup, confirmed via live inspection
  // (see the profile-selection comment above). Nothing in this shape
  // carries the SELECTORS.* attributes above (no data-status, data-sport,
  // data-kickoff-utc, data-market-family) -- identity and market/outcome
  // labels are instead embedded in element `id`s.
  const BET9JA_DESKTOP_SELECTORS = {
    // The confirmed board container -- fixture rows are its direct
    // children. A page can have more than one of these (round-2-confirmed
    // real capture: 2 tables, one per date section -- see dateHeading
    // below); every one found is processed.
    root: '.sports-table',
    row: '.table-f',
    // Round-4 real-capture correction (2 more real captures -- Highlights
    // and LaLiga -- both still came back with date_heading_raw: null under
    // the round-2/round-3 "preceding sibling of .sports-table" assumption,
    // proving that wrong too). The real structure: `.sports-table` and a
    // `.sports-head` wrapper (itself containing `.sports-head__date`) are
    // both children of a common day-wrapper element -- i.e. the heading is
    // a SIBLING'S DESCENDANT, not a sibling itself and not a child. See
    // findPrecedingDateHeading(), which looks up via `table.parentElement`.
    // Never parsed into a timestamp -- its exact date-string format is
    // still unconfirmed, and guessing one would defeat the whole point of
    // resolveKickoff()'s honesty guarantee.
    dateHeadingWrapper: '.sports-head',
    dateHeading: '.sports-head__date',
    // Round-2 real-capture correction: 12 of 30 `.table-f` elements on the
    // real page turned out to be structural/spacer/header rows with no
    // matchup cell and empty time/markets -- not fixture candidates at
    // all. Requiring this cell to exist (not just non-empty text) before
    // a `.table-f` is even counted as a candidate row fixes that; a
    // matchup cell that exists but is missing a home or away name is a
    // separate, genuine case and still falls through to
    // MISSING_PARTICIPANTS for audit.
    matchup: '.sports-table__matchup',
    home: '.sports-table__home',
    away: '.sports-table__away',
    kickoff: '.sports-table__time',
    // Matches the matchup cell's own id -- the only place this markup
    // carries a stable per-fixture identifier. Confirmed to appear under
    // two different prefixes depending on page type: the Highlights page
    // uses "home_highlights_sport-1_event-832455154"; competition pages
    // (round-2 tested: Eredivisie, Premier League, LaLiga, Ligue 1) use
    // "prematch_event-832455154" -- no "sport-N" segment at all. The event
    // id itself is therefore matched independently of any prefix
    // (eventIdPattern); the optional sport-code segment is matched
    // separately (sportCodePattern) and is simply absent -- not
    // mismatched -- on competition pages.
    identityElement: '[id*="_event-"]',
    eventIdPattern: /event-([a-z0-9]+)/i,
    sportCodePattern: /sport-([a-z0-9]+)_/i,
    // Matches each odds <li>'s own id, e.g.
    // "..._event-832455154_odds_market-1x2_sign-1" -- family and outcome
    // label are both embedded here; the element's text is the price. The
    // family group intentionally allows "_" and "-" (not just alphanumerics)
    // so a compound family like the confirmed second odds list on this
    // page, "1X2 1UP" (e.g. an id family of "1x2_1up"), is captured as
    // its own distinct family string rather than either failing to match
    // at all or being truncated down to "1x2" and wrongly merged with the
    // real 1X2 market -- ONE_X_TWO_FAMILY_ALIASES only ever contains the
    // exact string "1x2", so "1x2_1up" is correctly routed to
    // UNSUPPORTED_MARKET_FAMILY (audited, never merged, never dropped).
    oddsItem: '.sports-table__odds-item[id*="_market-"]',
    oddsItemIdPattern: /_market-([a-z0-9_-]+)_sign-(.+)$/i,
  };

  // Confirmed from the Highlights-page sample: sport-1 == Soccer (that
  // sample was captured from the https://sports.bet9ja.com/sport/soccer/1
  // URL). Other sport codes are unconfirmed and intentionally left
  // unmapped (routed to UNSUPPORTED_SPORT rather than guessed). This
  // lookup only applies when a row's id carries a "sport-N" segment at
  // all (the Highlights-page id shape) -- competition pages carry no such
  // segment; see parseBet9jaCompetitionUrl below for how those are
  // handled instead.
  const BET9JA_DESKTOP_SPORT_CODE_MAP = { 1: 'SOCCER' };

  // Round-2 confirmed (2026-09-11): four Soccer competition pages --
  // Eredivisie, Premier League, LaLiga, Ligue 1 -- all use the URL shape
  // /competition/{sport-slug}/{country-slug}/{competition-slug}/{id}, and
  // all shared the identical .sports-table/.table-f/.sports-head__date/
  // odds-id structure. A Basketball competition page (round-2 follow-up:
  // /competition/basketball/international/abaligapreseason/...) confirmed
  // the same URL shape generalizes across sports. On these pages (unlike
  // the Highlights page, which mixes competitions and cannot safely be
  // attributed to one country/league), the URL is itself confirmed to
  // reliably carry the sport, country, and competition -- so this parses
  // those three from the URL rather than guessing them from any DOM text.
  // The sport slug is matched generically (not hardcoded to "soccer") so
  // an excluded sport still gets a real, useful sport label (e.g.
  // "BASKETBALL") on its UNSUPPORTED_SPORT audit record instead of a bare
  // "UNKNOWN" -- this pattern only decides WHAT the sport is, never
  // whether it's supported (ONE_X_TWO_FAMILY_ALIASES / the SOCCER-only
  // downstream logic still does that).
  //
  // IMPORTANT scope limit: confirmed only for the five competitions
  // tested above (four Soccer, one Basketball). Every other Bet9ja
  // country/league page is [UNVERIFIED] -- this pattern is intentionally
  // narrow (requires the literal "/competition/{sport}/{country}/
  // {competition}/" shape) so a page with a genuinely different URL
  // layout is left unmatched rather than guessed. The extracted country/
  // competition values are the raw URL slugs verbatim (e.g.
  // "netherlands", "premierleague") -- NOT prettified into a display name
  // (there is no reliable, general way to turn "premierleague" back into
  // "Premier League" from the slug alone), so treat `region`/`competition`
  // from this path as stable identifiers, not confirmed display text.
  const COMPETITION_URL_PATTERN = /\/competition\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)\//i;

  function parseBet9jaCompetitionUrl(sourceUrl) {
    const match = (sourceUrl || '').match(COMPETITION_URL_PATTERN);
    if (!match) return null;
    return { sportSlug: match[1].toUpperCase(), countrySlug: match[2], competitionSlug: match[3] };
  }

  // --- Trusted forced-sport context (soccer_walker.js's
  // `/sportPage/1/competitions` batch selector) --------------------------
  //
  // `/sportPage/1/competitions` never carries a `/competition/{sport}/...`
  // URL (see parseBet9jaCompetitionUrl's own comment), and it is NOT
  // confirmed whether its fixture rows carry an id-embedded `sport-N`
  // segment either -- both of this file's existing sport-resolution tiers
  // can come back empty on that page even though every row is,
  // definitionally, Soccer (it is Bet9ja's own Soccer competitions
  // selector). Rather than have the walker guess a row value, the caller
  // may pass a trusted CLAIM via `context.forced_sport_context`, and this
  // module independently verifies it against the page before ever trusting
  // it -- the caller's claim alone is never sufficient. All three signals
  // below must agree, or the whole capture fails closed with
  // `SPORT_CONTEXT_CONFLICT` rather than silently falling back to
  // per-row UNSUPPORTED_SPORT (a real capture would look identical to a
  // parser bug) or silently trusting an unverified claim (which would
  // defeat the whole point of fail-closed classification).
  const FORCED_SPORT_CONTEXT_ROUTE_PATTERN = /^\/sportPage\/1\/competitions\/?$/;
  // Matches BET9JA_DESKTOP_SPORT_CODE_MAP's own confirmed `1 => SOCCER`
  // mapping (Highlights page row ids) -- the same sport id, on a
  // DIFFERENT page, is corroborating evidence, not a fresh guess.
  const FORCED_SPORT_CONTEXT_SPORT_ID = '1';
  const EXPECTED_FORCED_CAPTURE_SCOPE = 'SOCCER_ALL_PREMATCH_COMPETITIONS';

  // [UNVERIFIED] exact selector for a "visible sport heading" on
  // `/sportPage/1/competitions` -- no such element was captured during
  // live inspection. Two independent, low-false-positive-risk signals are
  // checked instead of guessing one CSS class: the document's own
  // `<title>` (always real, always present), and any element commonly
  // used for an active/selected tab's state whose text is EXACTLY
  // "Soccer" (not just contains it, to avoid a false match on something
  // like "Soccer News"). Neither matching is an honest "unknown", and
  // this function returns '' rather than assuming Soccer -- the caller
  // treats '' as a failed condition, never a pass.
  function resolveVisibleSportHeadingRaw(doc) {
    const titleText = ((doc && doc.title) || '').trim();
    if (/soccer/i.test(titleText)) return titleText;
    const candidates = doc ? doc.querySelectorAll('[class*="active" i], [aria-selected="true"], [class*="selected" i]') : [];
    for (const el of Array.from(candidates)) {
      const t = text(el);
      if (/^soccer$/i.test(t)) return t;
    }
    return '';
  }

  // ROUND 8 CORRECTION: a real capture hit `SPORT_CONTEXT_CONFLICT` on a
  // page that WAS genuinely Soccer, because the only signal available was
  // a rendered competition breadcrumb heading (e.g. "Soccer > Italy >
  // Serie A") -- and this file's own page-level check requires an EXACT
  // "soccer" match, which a breadcrumb (always carrying a country/
  // competition suffix) can never satisfy. The fix is NOT to loosen that
  // exact-match check into a substring match (that would risk accepting
  // "Soccer News" or similar) -- it's to recognize the breadcrumb PREFIX
  // shape as its own, separate, corroborating signal: a rendered heading
  // that BEGINS WITH "Soccer >" is real evidence the page is genuinely
  // showing Soccer competitions, checked independently of (and in
  // addition to, never instead of) the page-level heading check above.
  // This is deliberately a coarse PREFIX check only -- it never attempts
  // to extract a country or competition name (that parsing is
  // `soccer_walker.js`'s own per-table attribution logic, kept
  // completely separate so a change to one can never silently affect the
  // other).
  const SOCCER_BREADCRUMB_PREFIX_PATTERN = /^soccer\s*>/i;

  const HEADING_AUDIT_MAX_LEN = 120;
  const HEADING_AUDIT_MAX_CANDIDATES = 10;

  function truncateForAudit(value) {
    const s = (value || '').trim();
    return s.length > HEADING_AUDIT_MAX_LEN ? `${s.slice(0, HEADING_AUDIT_MAX_LEN)}…` : s;
  }

  // Which of resolveVisibleSportHeadingRaw's OWN candidate patterns an
  // element matched -- a selector NAME for audit, never the element's own
  // full class list or any other markup.
  function pageHeadingSelectorLabel(el) {
    if (el.getAttribute('aria-selected') === 'true') return '[aria-selected="true"]';
    const className = typeof el.className === 'string' ? el.className : '';
    if (/active/i.test(className)) return '[class*="active"]';
    if (/selected/i.test(className)) return '[class*="selected"]';
    return '[class*="active"], [aria-selected="true"], [class*="selected"]';
  }

  /**
   * Mirrors `resolveVisibleSportHeadingRaw`'s own two signal sources but
   * returns every candidate examined (selector name + sanitized,
   * length-capped text -- never full HTML), so a real
   * `SPORT_CONTEXT_CONFLICT` can be diagnosed from the envelope alone
   * instead of guessing at a new selector blind.
   */
  function diagnosePageHeadingCandidates(doc) {
    if (!doc) return [];
    const candidates = [{ selector: 'document.title', text: truncateForAudit(doc.title || '') }];
    const els = doc.querySelectorAll('[class*="active" i], [aria-selected="true"], [class*="selected" i]');
    Array.from(els)
      .slice(0, HEADING_AUDIT_MAX_CANDIDATES)
      .forEach((el) => candidates.push({ selector: pageHeadingSelectorLabel(el), text: truncateForAudit(text(el)) }));
    return candidates;
  }

  /** Same audit discipline as `diagnosePageHeadingCandidates`, for the per-table breadcrumb signal. */
  function diagnoseCompetitionHeadingCandidates(doc) {
    if (!doc) return [];
    const tables = doc.querySelectorAll(BET9JA_DESKTOP_SELECTORS.root);
    const candidates = [];
    Array.from(tables)
      .slice(0, HEADING_AUDIT_MAX_CANDIDATES)
      .forEach((table) => {
        const prev = table.previousElementSibling;
        if (!prev) return;
        const direct = text(prev);
        const candidateText = direct || (prev.firstElementChild ? text(prev.firstElementChild) : '');
        candidates.push({ selector: '.sports-table:previousElementSibling', text: truncateForAudit(candidateText) });
      });
    return candidates;
  }

  /**
   * Returns `{active: false}` when the caller passed no
   * `forced_sport_context` at all (ordinary captureFromDocument calls are
   * completely unaffected). Otherwise `{active: true, ok, sportHint,
   * diagnostics}` -- `ok` is only true when the caller's claim, the
   * page's own URL, an independently-resolved page-level visible sport
   * heading, AND at least one rendered competition heading beginning
   * with "Soccer >" all agree; any disagreement is `{active: true, ok:
   * false}` plus a non-null `diagnostics` object naming exactly which
   * check failed and what was actually found on the page (sanitized,
   * bounded audit text only -- never full HTML), so a real
   * `SPORT_CONTEXT_CONFLICT` never has to be diagnosed by guessing at a
   * new selector blind. The page-level heading and the per-table
   * breadcrumb are two INDEPENDENT signals -- neither substitutes for
   * the other, and neither is ever used to attribute one specific
   * competition (that stays entirely in
   * `resolve_table_competition`/`table_attribution_summary`).
   */
  function validateForcedSportContext(doc, context) {
    const forced = context.forced_sport_context;
    if (!forced) return { active: false, ok: false, sportHint: null, diagnostics: null };

    let pathname = '';
    try {
      pathname = new URL(context.sourceUrl).pathname;
    } catch (err) {
      pathname = '';
    }
    const routeCheckPassed = FORCED_SPORT_CONTEXT_ROUTE_PATTERN.test(pathname);

    const claimValid =
      forced.forced_sport_hint === 'SOCCER' &&
      forced.forced_sport_source === 'SPORTPAGE_ROUTE_ID' &&
      forced.forced_sport_source_value === FORCED_SPORT_CONTEXT_SPORT_ID &&
      forced.capture_scope === EXPECTED_FORCED_CAPTURE_SCOPE;

    if (!claimValid) {
      return {
        active: true,
        ok: false,
        sportHint: null,
        diagnostics: {
          pathname_actual: pathname,
          pathname_expected: FORCED_SPORT_CONTEXT_ROUTE_PATTERN.source,
          forced_sport_hint: forced.forced_sport_hint || null,
          page_heading_candidates: [],
          resolved_page_heading: null,
          competition_heading_candidates: [],
          soccer_breadcrumb_count: 0,
          route_check_passed: routeCheckPassed,
          page_heading_check_passed: false,
          breadcrumb_check_passed: false,
          failed_check: 'CLAIM_INVALID',
        },
      };
    }

    // resolveVisibleSportHeadingRaw already applies its own matching rule
    // per signal (a loose "contains soccer" for the page title, an exact
    // "is soccer" for an active-tab-like element) -- any non-empty result
    // IS the confirming evidence; re-applying a different, stricter regex
    // here would reject its own valid title-based match.
    const headingRaw = routeCheckPassed ? resolveVisibleSportHeadingRaw(doc) : '';
    const pageHeadingCheckPassed = !!headingRaw;
    const competitionHeadingCandidates = routeCheckPassed ? diagnoseCompetitionHeadingCandidates(doc) : [];
    const soccerBreadcrumbCount = competitionHeadingCandidates.filter((c) => SOCCER_BREADCRUMB_PREFIX_PATTERN.test(c.text)).length;
    const breadcrumbCheckPassed = soccerBreadcrumbCount > 0;

    const ok = routeCheckPassed && pageHeadingCheckPassed && breadcrumbCheckPassed;
    if (ok) {
      return { active: true, ok: true, sportHint: forced.forced_sport_hint, diagnostics: null };
    }

    let failedCheck = 'ROUTE_MISMATCH';
    if (routeCheckPassed) failedCheck = pageHeadingCheckPassed ? 'BREADCRUMB_NOT_FOUND' : 'PAGE_HEADING_NOT_RESOLVED';

    return {
      active: true,
      ok: false,
      sportHint: null,
      diagnostics: {
        pathname_actual: pathname,
        pathname_expected: FORCED_SPORT_CONTEXT_ROUTE_PATTERN.source,
        forced_sport_hint: forced.forced_sport_hint,
        page_heading_candidates: routeCheckPassed ? diagnosePageHeadingCandidates(doc) : [],
        resolved_page_heading: headingRaw || null,
        competition_heading_candidates: competitionHeadingCandidates,
        soccer_breadcrumb_count: soccerBreadcrumbCount,
        route_check_passed: routeCheckPassed,
        page_heading_check_passed: pageHeadingCheckPassed,
        breadcrumb_check_passed: breadcrumbCheckPassed,
        failed_check: failedCheck,
      },
    };
  }

  // Defensive exclusion for the BET9JA_DESKTOP fallback profile only,
  // applied to both matched `.sports-table` roots and the rows found
  // inside them: scoping rows to `.sports-table > .table-f` already
  // excludes navigation and the betslip panel by construction (neither is
  // expected to contain a `.sports-table`), but this is kept as a cheap,
  // redundant safety net rather than relying on that assumption alone.
  // The LEGACY profile never needs this: its rows are always scoped under
  // a matched root element with its own dedicated selector already.
  const EXCLUDED_ANCESTOR_SELECTOR =
    '[class*="betslip" i], [class*="bet-slip" i], [class*="sidebar" i], [class*="navigation" i], [class*="nav-" i], nav, header, footer';

  // Recognized aliases for the market family this release fully normalizes.
  const ONE_X_TWO_FAMILY_ALIASES = new Set(['1x2', 'match_result', 'fulltime_result', 'ft_1x2']);
  const OUTCOME_LABEL_MAP = {
    '1': 'H', h: 'H', home: 'H',
    x: 'D', d: 'D', draw: 'D',
    '2': 'A', a: 'A', away: 'A',
  };
  const KNOWN_STATUSES = new Set(['PRE', 'LIVE', 'VIRTUAL', 'ZOOM']);

  function text(el) {
    if (!el) return '';
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function normalizeForHash(value) {
    return (value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function parsePrice(rawText) {
    const cleaned = (rawText || '').trim();
    if (cleaned === '' || cleaned === '-' || cleaned.toUpperCase() === 'SP') {
      return null;
    }
    const normalized = cleaned.replace(',', '.');
    const value = Number(normalized);
    if (!Number.isFinite(value) || value <= 1) {
      // Decimal odds are never <= 1.0 -- a value that parses but fails this
      // sanity bound is treated the same as unparseable, not silently kept.
      return Number.isFinite(value) ? null : null;
    }
    return value;
  }

  function makeUnparsed({ reason, expectedUnsupported, sectionIndex, recordIndex, detail, raw }) {
    return {
      reason,
      expected_unsupported: expectedUnsupported,
      section_index: sectionIndex,
      record_index: recordIndex,
      detail: detail || null,
      raw,
    };
  }

  function parseMarketElement(marketEl) {
    const family = (marketEl.getAttribute('data-market-family') || '').trim();
    const line = (marketEl.getAttribute('data-market-line') || '').trim();
    const outcomeEls = Array.from(marketEl.querySelectorAll(SELECTORS.outcome));
    const outcomes = outcomeEls.map((outcomeEl) => ({
      rawLabel: text(outcomeEl.querySelector(SELECTORS.outcomeLabel)),
      rawPrice: text(outcomeEl.querySelector(SELECTORS.outcomePrice)),
    }));
    return { family, line, outcomes };
  }

  /**
   * LEGACY profile field extraction -- unchanged behavior from before the
   * two-profile split, just moved into its own function so processRow can
   * take an already-extracted, profile-agnostic `fields` object.
   */
  function extractLegacyFields(row) {
    const kickoffEl = row.querySelector(SELECTORS.kickoff);
    return {
      homeRaw: text(row.querySelector(SELECTORS.home)),
      awayRaw: text(row.querySelector(SELECTORS.away)),
      kickoffText: text(kickoffEl),
      kickoffUtcAttr: (kickoffEl && kickoffEl.getAttribute('data-kickoff-utc')) || row.getAttribute('data-kickoff-utc') || null,
      sportHintRaw: row.getAttribute('data-sport') || '',
      statusAttr: row.getAttribute('data-status'),
      externalFixtureRef: row.getAttribute('data-fixture-id') || null,
      markets: Array.from(row.querySelectorAll(SELECTORS.market)).map(parseMarketElement),
      dateHeadingRaw: null, // no dateHeading concept in the LEGACY profile
    };
  }

  /**
   * BET9JA_DESKTOP profile field extraction -- see the SELECTOR CONTRACT
   * comment at the top of this file and BET9JA_DESKTOP_SELECTORS above.
   * Identity, sport, and market/outcome-label all come from parsing
   * element `id` attributes rather than from a data-* attribute, since
   * the real markup carries none of the latter. Kickoff has no attached
   * timestamp at all in the one sample seen -- resolveKickoff() will
   * therefore always report it unresolved for this profile today, which
   * is the honest outcome, not a bug to work around.
   */
  function extractBet9jaDesktopFields(row, dateHeadingRaw, urlCompetitionInfo, forcedSportHint) {
    const identityEl = row.querySelector(BET9JA_DESKTOP_SELECTORS.identityElement);
    const eventIdMatch = identityEl && identityEl.id.match(BET9JA_DESKTOP_SELECTORS.eventIdPattern);
    const sportCodeMatch = identityEl && identityEl.id.match(BET9JA_DESKTOP_SELECTORS.sportCodePattern);
    const externalFixtureRef = eventIdMatch ? `bet9ja-event-${eventIdMatch[1]}` : null;

    // Sport is resolved three ways, tried in order: (1) an id-embedded
    // "sport-N" segment, confirmed only on the Highlights page; (2) absent
    // that, the page's own URL, confirmed only for the four competition
    // pages parseBet9jaCompetitionUrl matches; (3) absent BOTH, a
    // caller-supplied `forcedSportHint`, but ONLY when
    // validateForcedSportContext has already independently verified it
    // against the page (see that function's own comment) -- this tier
    // never runs on an ordinary, context-free call. Tier (3) is gated on
    // `!sportCode`, not merely on tier (1) coming back empty: a row whose
    // id DOES carry a "sport-N" segment, even one this file has no
    // mapping for, has already told us something concrete about itself
    // and must never be silently relabeled by a forced context -- only a
    // row with NO id-embedded sport signal at all is eligible for the
    // forced fallback. Nothing guessed if all three are absent --
    // sportHintRaw stays '' and the row is correctly excluded via
    // UNSUPPORTED_SPORT rather than admitted on a hunch.
    const sportCode = sportCodeMatch ? sportCodeMatch[1] : null;
    const sportHintRaw =
      (sportCode && BET9JA_DESKTOP_SPORT_CODE_MAP[sportCode]) ||
      (urlCompetitionInfo && urlCompetitionInfo.sportSlug) ||
      (!sportCode ? forcedSportHint : null) ||
      '';

    const marketsByFamily = new Map();
    for (const li of Array.from(row.querySelectorAll(BET9JA_DESKTOP_SELECTORS.oddsItem))) {
      const idMatch = li.id.match(BET9JA_DESKTOP_SELECTORS.oddsItemIdPattern);
      if (!idMatch) continue;
      const family = idMatch[1];
      const rawLabel = idMatch[2];
      if (!marketsByFamily.has(family)) {
        marketsByFamily.set(family, { family, line: '', outcomes: [] });
      }
      marketsByFamily.get(family).outcomes.push({ rawLabel, rawPrice: text(li) });
    }

    return {
      homeRaw: text(row.querySelector(BET9JA_DESKTOP_SELECTORS.home)),
      awayRaw: text(row.querySelector(BET9JA_DESKTOP_SELECTORS.away)),
      kickoffText: text(row.querySelector(BET9JA_DESKTOP_SELECTORS.kickoff)),
      kickoffUtcAttr: null, // no UTC-qualified timestamp exists in this markup at all
      sportHintRaw,
      statusAttr: 'PRE', // no live/virtual/Zoom sample seen yet -- see file-top comment
      externalFixtureRef,
      markets: Array.from(marketsByFamily.values()),
      dateHeadingRaw: dateHeadingRaw || null,
    };
  }

  function resolveStatus(rawStatusAttr) {
    const upper = (rawStatusAttr || 'PRE').trim().toUpperCase();
    if (KNOWN_STATUSES.has(upper)) {
      return { status: upper, recognized: true };
    }
    return { status: upper || 'UNKNOWN', recognized: false };
  }

  /**
   * Export only origin + pathname. Strips query parameters and the
   * fragment outright -- either can carry a session/auth token, a referral
   * code, or other identifying material that has no place in a file meant
   * to be shared with a research host. Falls back to a best-effort strip
   * (everything from the first "?" or "#" onward) if the value doesn't
   * parse as a URL at all, so a malformed value still never leaks a query
   * string.
   */
  function sanitizeSourceUrl(rawUrl) {
    if (!rawUrl) return '';
    try {
      const parsed = new URL(rawUrl);
      return `${parsed.origin}${parsed.pathname}`;
    } catch (err) {
      return String(rawUrl).split(/[?#]/)[0];
    }
  }

  function statusToFixtureStatus(status) {
    return { PRE: 'PRE_MATCH', LIVE: 'LIVE', VIRTUAL: 'VIRTUAL', ZOOM: 'ZOOM' }[status] || status;
  }

  // A 4-digit year AND an explicit UTC designator (Z or a numeric offset)
  // are both mandatory -- "2024-08-17T14:00:00Z" passes, but "08-17T14:00"
  // (no year) and "2024-08-17T14:00:00" (no timezone) do not. Never widen
  // this to accept an implicit/local-time string: Date.parse's handling of
  // those is implementation-defined, which is exactly the silent-UTC-guess
  // this function exists to prevent.
  const STRICT_UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/;

  /**
   * Resolve a fixture's kickoff time honestly: only ever returns a real
   * `kickoffUtc` when the page supplied an unambiguous, fully-qualified
   * UTC timestamp. A missing attribute, or one missing its year or
   * timezone, is reported as a typed unresolved state -- never guessed at
   * via Date.parse's ambiguous local-time fallback.
   */
  function resolveKickoff(kickoffUtcAttr) {
    if (!kickoffUtcAttr) {
      return { kickoffUtc: null, kickoffResolution: 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP' };
    }
    if (!STRICT_UTC_TIMESTAMP.test(kickoffUtcAttr)) {
      return { kickoffUtc: null, kickoffResolution: 'UNRESOLVED_AMBIGUOUS_TIMESTAMP' };
    }
    const parsedMs = Date.parse(kickoffUtcAttr);
    if (Number.isNaN(parsedMs)) {
      return { kickoffUtc: null, kickoffResolution: 'UNRESOLVED_AMBIGUOUS_TIMESTAMP' };
    }
    return { kickoffUtc: new Date(parsedMs).toISOString(), kickoffResolution: 'EXPLICIT_UTC_ATTRIBUTE' };
  }

  /**
   * Parse one fixture row into either a normalized fixture (pushed to
   * `fixtures`) or one-or-more typed unparsed_records entries (pushed to
   * `unparsedRecords`) -- never both, never neither (a row with zero
   * markets still produces one unparsed record so it is never silently
   * lost).
   */
  // Row-level classification returned by processRow -- exactly one per
  // call, used by captureFromDocument to reconcile coverage.records_seen
  // = records_parsed + records_unresolved + records_expected_unsupported
  // (see coverage's own comment). A row that produces a fixture is PARSED
  // even if it ALSO produces one or more expected-unsupported audit
  // entries alongside it (e.g. a Soccer row's real 1X2 market plus its
  // excluded "1X2 1UP"/"1X2 2UP" siblings) -- those extra unparsed_records
  // entries are still recorded for audit, but the row itself already
  // succeeded, so it must never also be counted as expected-unsupported.
  const ROW_PARSED = 'PARSED';
  const ROW_UNRESOLVED = 'UNRESOLVED';
  const ROW_EXPECTED_UNSUPPORTED = 'EXPECTED_UNSUPPORTED';

  function processRow({
    fields,
    region,
    competition,
    sectionIndex,
    recordIndex,
    capturedAtUtc,
    seenFixtureIdsThisCapture,
    previousIndex,
    updatedIndex,
    fixtures,
    unparsedRecords,
    attributionUnresolved = false,
    resolvedSourceCompetitionId = null,
  }) {
    const { homeRaw, awayRaw, kickoffText, kickoffUtcAttr, sportHintRaw, statusAttr, externalFixtureRef, markets, dateHeadingRaw } = fields;
    const sport = (sportHintRaw || '').trim().toUpperCase() || 'UNKNOWN';
    const { status, recognized: statusRecognized } = resolveStatus(statusAttr);

    // Privacy allowlist: this is the ONLY set of fields ever attached to an
    // unparsed_records entry. It is fixture-scoped (home/away/kickoff/sport/
    // status/market data drawn from the SELECTORS-scoped row element only)
    // -- never the row's surrounding page text, never anything from outside
    // this one row. See tests/privacy.test.js, which enumerates this exact
    // key set and fails if a future edit adds anything outside it.
    const rawSnapshot = {
      home: homeRaw,
      away: awayRaw,
      kickoff_raw: kickoffText,
      sport_hint: sportHintRaw,
      status_hint: statusAttr || null,
      date_heading_raw: dateHeadingRaw || null,
      markets: markets.map((m) => ({ family: m.family, line: m.line, outcomes: m.outcomes })),
    };

    // Attribution takes precedence over every other classification: a
    // caller-supplied per-table competition resolver (soccer_walker.js's
    // batch selector, which can render more than one competition's
    // fixtures on one page) that could not uniquely map this row's own
    // `.sports-table` to one of the batch's selected competitions means
    // this row's identity itself is unresolved -- retaining it as a real
    // fixture would risk mislabeling it under the wrong competition, a
    // worse outcome than an honest unresolved record. Never reached on an
    // ordinary call with no resolver (`attributionUnresolved` defaults to
    // false).
    if (attributionUnresolved) {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'COMPETITION_ATTRIBUTION_UNRESOLVED',
          expectedUnsupported: false,
          sectionIndex,
          recordIndex,
          detail: 'This row\'s .sports-table could not be uniquely mapped to one of the batch\'s selected competitions.',
          raw: rawSnapshot,
        })
      );
      return ROW_UNRESOLVED;
    }

    if (!homeRaw || !awayRaw) {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'MISSING_PARTICIPANTS',
          expectedUnsupported: false,
          sectionIndex,
          recordIndex,
          detail: 'Row is missing a home and/or away participant name.',
          raw: rawSnapshot,
        })
      );
      return ROW_UNRESOLVED;
    }

    if (!statusRecognized) {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'UNRECOGNIZED_STATUS',
          expectedUnsupported: false,
          sectionIndex,
          recordIndex,
          detail: `status="${statusAttr || ''}" is not one of PRE/LIVE/VIRTUAL/ZOOM.`,
          raw: rawSnapshot,
        })
      );
      return ROW_UNRESOLVED;
    }

    if (sport !== 'SOCCER') {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'UNSUPPORTED_SPORT',
          expectedUnsupported: true,
          sectionIndex,
          recordIndex,
          detail: `sport="${sport}" has no adapter in this release.`,
          raw: rawSnapshot,
        })
      );
      return ROW_EXPECTED_UNSUPPORTED;
    }

    if (markets.length === 0) {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'NO_MARKETS_FOUND',
          expectedUnsupported: false,
          sectionIndex,
          recordIndex,
          detail: 'Row matched the fixture-row selector but contains no market elements.',
          raw: rawSnapshot,
        })
      );
      return ROW_UNRESOLVED;
    }

    if (status === 'LIVE') {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'LIVE_EVENT_EXCLUDED_FROM_PREMATCH_MODEL_INPUT',
          expectedUnsupported: true,
          sectionIndex,
          recordIndex,
          detail: 'Release 1 captures pre-match markets only; live odds move too fast to trust as a snapshot.',
          raw: rawSnapshot,
        })
      );
      return ROW_EXPECTED_UNSUPPORTED;
    }
    if (status === 'VIRTUAL' || status === 'ZOOM') {
      unparsedRecords.push(
        makeUnparsed({
          reason: 'VIRTUAL_OR_ZOOM_PRODUCT_EXCLUDED',
          expectedUnsupported: true,
          sectionIndex,
          recordIndex,
          detail: `status="${status}" is not a real-world pre-match fixture.`,
          raw: rawSnapshot,
        })
      );
      return ROW_EXPECTED_UNSUPPORTED;
    }

    // status === 'PRE' from here on. A row can iterate several markets
    // (e.g. Soccer's real 1X2 plus excluded "1X2 1UP"/"1X2 2UP" siblings)
    // -- rowProducedFixture / rowHasGenuineUnresolved classify the ROW as
    // a whole once the loop finishes, per the ROW_* doc comment above.
    let rowProducedFixture = false;
    let rowHasGenuineUnresolved = false;
    for (const market of markets) {
      const familyKey = (market.family || '').toLowerCase();
      if (!ONE_X_TWO_FAMILY_ALIASES.has(familyKey)) {
        unparsedRecords.push(
          makeUnparsed({
            reason: 'UNSUPPORTED_MARKET_FAMILY',
            expectedUnsupported: true,
            sectionIndex,
            recordIndex,
            detail: `market_family="${market.family || ''}" has no adapter in this release.`,
            raw: { ...rawSnapshot, market },
          })
        );
        continue;
      }

      const outcomesByLabel = {};
      let unrecognizedLabel = null;
      for (const outcome of market.outcomes) {
        const mapped = OUTCOME_LABEL_MAP[(outcome.rawLabel || '').toLowerCase()];
        if (!mapped) {
          unrecognizedLabel = outcome.rawLabel;
          break;
        }
        outcomesByLabel[mapped] = parsePrice(outcome.rawPrice);
      }
      if (unrecognizedLabel !== null) {
        unparsedRecords.push(
          makeUnparsed({
            reason: 'UNRECOGNIZED_OUTCOME_LABEL',
            expectedUnsupported: false,
            sectionIndex,
            recordIndex,
            detail: `Outcome label "${unrecognizedLabel}" is not one of H/D/A (or 1/X/2, Home/Draw/Away).`,
            raw: { ...rawSnapshot, market },
          })
        );
        rowHasGenuineUnresolved = true;
        continue;
      }

      const missing = ['H', 'D', 'A'].filter((k) => outcomesByLabel[k] === undefined || outcomesByLabel[k] === null);
      if (missing.length > 0) {
        unparsedRecords.push(
          makeUnparsed({
            reason: 'INCOMPLETE_1X2_MARKET',
            expectedUnsupported: false,
            sectionIndex,
            recordIndex,
            detail: `Missing decimal price for: ${missing.join(', ')}. Rejected from model input, retained for audit.`,
            raw: { ...rawSnapshot, market, partial_outcomes: outcomesByLabel },
          })
        );
        rowHasGenuineUnresolved = true;
        continue;
      }

      const { kickoffUtc, kickoffResolution } = resolveKickoff(kickoffUtcAttr);

      const naturalKey = [
        'SOCCER',
        normalizeForHash(region),
        normalizeForHash(competition),
        normalizeForHash(homeRaw),
        normalizeForHash(awayRaw),
        kickoffUtc || normalizeForHash(kickoffText),
        familyKey,
        (market.line || '').toLowerCase(),
      ];
      const fixtureId = externalFixtureRef
        ? Bet9jaIds.stableId('bxf', ['external', externalFixtureRef])
        : Bet9jaIds.stableId('bxf', naturalKey);

      const outcomesHashInput = JSON.stringify([outcomesByLabel.H, outcomesByLabel.D, outcomesByLabel.A]);

      let duplicateStatus;
      if (seenFixtureIdsThisCapture.has(fixtureId)) {
        duplicateStatus = 'DUPLICATE_WITHIN_CAPTURE';
      } else {
        const previous = previousIndex[fixtureId];
        if (!previous) {
          duplicateStatus = 'NEW';
        } else if (previous.outcomesHash === outcomesHashInput) {
          duplicateStatus = 'SEEN_BEFORE_UNCHANGED';
        } else {
          duplicateStatus = 'SEEN_BEFORE_ODDS_CHANGED';
        }
      }
      seenFixtureIdsThisCapture.add(fixtureId);
      updatedIndex[fixtureId] = { outcomesHash: outcomesHashInput, lastCapturedAtUtc: capturedAtUtc };

      fixtures.push({
        fixture_id: fixtureId,
        sport: 'SOCCER',
        region: region || null,
        competition: competition || null,
        participants: { home: homeRaw, away: awayRaw },
        kickoff_raw: kickoffText || null,
        kickoff_utc: kickoffUtc,
        kickoff_resolution: kickoffResolution,
        // Raw text of the nearest preceding date-heading element (e.g.
        // BET9JA_DESKTOP's `.sports-head__date`) when the profile that
        // produced this fixture has one; null for LEGACY. Deliberately
        // never combined into kickoff_utc -- its date format is
        // unconfirmed, so doing that would be exactly the kind of guess
        // resolveKickoff() exists to prevent.
        date_heading_raw: dateHeadingRaw || null,
        status: statusToFixtureStatus(status),
        market_family: '1X2',
        market_line: market.line || '',
        outcomes: [
          { label: 'H', price: outcomesByLabel.H },
          { label: 'D', price: outcomesByLabel.D },
          { label: 'A', price: outcomesByLabel.A },
        ],
        offered_odds: { H: outcomesByLabel.H, D: outcomesByLabel.D, A: outcomesByLabel.A },
        captured_at_utc: capturedAtUtc,
        duplicate_status: duplicateStatus,
        parser_version: PARSER_VERSION,
        // Populated only when a per-table competition resolver was
        // supplied AND uniquely resolved this fixture's own
        // `.sports-table` -- null on every ordinary call. This is a
        // single, confirmed id (never a batch-wide guess): a table whose
        // attribution could not be resolved never reaches this far at all
        // (see the attributionUnresolved gate above).
        resolved_source_competition_id: resolvedSourceCompetitionId || null,
      });
      rowProducedFixture = true;
    }

    if (rowProducedFixture) return ROW_PARSED;
    if (rowHasGenuineUnresolved) return ROW_UNRESOLVED;
    return ROW_EXPECTED_UNSUPPORTED;
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string, previousIndex?: object}} context
   * @returns {{envelope: object, updatedIndex: object}}
   */
  function captureFromDocument(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const previousIndex = context.previousIndex || {};
    const updatedIndex = {};
    const fixtures = [];
    const unparsedRecords = [];
    const seenFixtureIdsThisCapture = new Set();

    const forcedSportContextResult = validateForcedSportContext(doc, context);

    const envelopeBase = {
      schema_version: 'bet9ja-fixture-capture.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
      forced_sport_context_applied: forcedSportContextResult.active && forcedSportContextResult.ok,
    };

    function finalize({
      sectionsSeen,
      recordsSeen,
      recordsUnresolved,
      recordsExpectedUnsupported,
      collapsedSectionsDetected,
      lazyLoadingDetected,
      fallbackProfileActive,
      zeroRecordsReason = 'NO_RECORDS_FOUND',
      tableAttributionSummary = null,
      sportContextDiagnostics = null,
    }) {
      let captureStatus;
      const statusReasons = [];
      if (recordsSeen === 0) {
        captureStatus = 'CAPTURE_FAILED';
        statusReasons.push(zeroRecordsReason);
      } else {
        captureStatus = 'CAPTURE_OK';
        if (collapsedSectionsDetected) {
          captureStatus = 'CAPTURE_PARTIAL';
          statusReasons.push('COLLAPSED_SECTIONS_DETECTED');
        }
        if (lazyLoadingDetected) {
          captureStatus = 'CAPTURE_PARTIAL';
          statusReasons.push('LAZY_LOADING_DETECTED');
        }
        if (recordsUnresolved > 0) {
          captureStatus = 'CAPTURE_PARTIAL';
          statusReasons.push('UNRESOLVED_RECORDS_PRESENT');
        }
        if (fallbackProfileActive) {
          // Honest, not punitive: the BET9JA_DESKTOP fallback has only ever
          // been confirmed against one isolated row (see the SELECTOR
          // CONTRACT comment at the top of this file) with no known
          // competition-grouping, collapsed-section, or pagination markup
          // to check against -- so full-page coverage is unverified for
          // this profile even when every row it did find parsed cleanly.
          captureStatus = 'CAPTURE_PARTIAL';
          statusReasons.push('BET9JA_DESKTOP_FALLBACK_PROFILE_UNVERIFIED_COVERAGE');
        }
        if (fixtures.length === 0 && unparsedRecords.length === 0) {
          // Structurally unreachable given recordsSeen > 0 (every row yields
          // at least one fixture or unparsed_records entry) -- kept as an
          // explicit guard so an empty successful download can never happen
          // silently if that invariant is ever broken by a future edit.
          captureStatus = 'CAPTURE_FAILED';
          statusReasons.push('NO_USABLE_OUTPUT');
        }
      }

      return {
        envelope: {
          ...envelopeBase,
          capture_status: captureStatus,
          capture_status_reasons: statusReasons,
          coverage: {
            visible_page_only: true,
            sections_seen: sectionsSeen,
            records_seen: recordsSeen,
            records_parsed: fixtures.length,
            records_unresolved: recordsUnresolved,
            records_expected_unsupported: recordsExpectedUnsupported,
            collapsed_sections_detected: collapsedSectionsDetected,
            lazy_loading_detected: lazyLoadingDetected,
          },
          // Only non-null when resolve_table_competition was supplied --
          // see that context option's own comment. One entry per
          // `.sports-table`: `{source_competition_id, resolved, row_count}`.
          table_attribution_summary: tableAttributionSummary,
          // Non-null ONLY when a forced_sport_context claim was rejected
          // -- names exactly which check failed (`failed_check`) and what
          // was actually found on the page (sanitized, bounded audit
          // text), so a real SPORT_CONTEXT_CONFLICT never has to be
          // diagnosed by guessing at a new selector blind.
          sport_context_diagnostics: sportContextDiagnostics,
          fixtures,
          unparsed_records: unparsedRecords,
        },
        updatedIndex,
      };
    }

    if (forcedSportContextResult.active && !forcedSportContextResult.ok) {
      // The caller asked this capture to trust a forced Soccer
      // classification, but this module's own independent check of the
      // page's route and/or visible sport heading disagreed with it (or
      // the caller's claim itself was malformed). Fail the WHOLE capture
      // closed rather than silently falling back to per-row
      // UNSUPPORTED_SPORT (indistinguishable from a real parser defect)
      // or trusting an unverified claim.
      return finalize({
        sectionsSeen: 0,
        recordsSeen: 0,
        recordsUnresolved: 0,
        recordsExpectedUnsupported: 0,
        collapsedSectionsDetected: false,
        lazyLoadingDetected: false,
        fallbackProfileActive: false,
        zeroRecordsReason: 'SPORT_CONTEXT_CONFLICT',
        sportContextDiagnostics: forcedSportContextResult.diagnostics,
      });
    }

    const rootEl = doc.querySelector(SELECTORS.root);

    if (!rootEl) {
      // LEGACY root not found -- before reporting a true failure, try the
      // BET9JA_DESKTOP fallback profile (see SELECTOR CONTRACT comment).
      // Confirmed root: `.sports-table`, whose direct children include
      // `.table-f` fixture rows (gated on a real matchup cell -- see
      // BET9JA_DESKTOP_SELECTORS.matchup's own comment). A page can have
      // more than one `.sports-table`; every one found is processed. The
      // ancestor exclusion is applied at both levels as a redundant
      // safety net (see EXCLUDED_ANCESTOR_SELECTOR's own comment).
      const desktopTables = Array.from(doc.querySelectorAll(BET9JA_DESKTOP_SELECTORS.root)).filter(
        (table) => !table.closest(EXCLUDED_ANCESTOR_SELECTOR)
      );

      if (desktopTables.length === 0) {
        return finalize({
          sectionsSeen: 0,
          recordsSeen: 0,
          recordsUnresolved: 0,
          recordsExpectedUnsupported: 0,
          collapsedSectionsDetected: false,
          lazyLoadingDetected: false,
          fallbackProfileActive: false,
          zeroRecordsReason: 'SELECTOR_ROOT_NOT_FOUND',
        });
      }

      // Computed once per capture (it depends only on the page's own URL,
      // confirmed reliable for the competition-page shapes tested --
      // see parseBet9jaCompetitionUrl's own comment) rather than per row.
      // null on any page that doesn't match (e.g. the Highlights page,
      // which mixes competitions and cannot be safely attributed to one).
      const urlCompetitionInfo = parseBet9jaCompetitionUrl(context.sourceUrl);
      const fallbackRegion = urlCompetitionInfo ? urlCompetitionInfo.countrySlug : null;
      const fallbackCompetition = urlCompetitionInfo ? urlCompetitionInfo.competitionSlug : null;

      // Per-table competition attribution (soccer_walker.js's batch
      // selector only -- a page can render more than one competition's
      // fixtures at once there, unlike every other confirmed page shape
      // this profile handles). `null` on an ordinary call: every table on
      // the page then keeps using the single page-wide
      // fallbackRegion/fallbackCompetition above, completely unchanged.
      // When supplied, called ONCE per `.sports-table` with that table
      // element; must return either `{resolved: true, sourceCompetitionId,
      // competitionNameRaw, countryNameRaw}` or `{resolved: false}` --
      // never guessed at by this file, which has no confirmed knowledge of
      // which competitions were actually selected.
      const resolveTableCompetition =
        typeof context.resolve_table_competition === 'function' ? context.resolve_table_competition : null;

      // A `.sports-table`'s date heading lives in a `.sports-head` wrapper
      // that is a SIBLING of the table, both children of a common
      // day-wrapper element -- confirmed only after two prior wrong
      // guesses (nested child, then preceding sibling) both came back
      // date_heading_raw: null against real captures. Scoped to `:scope >`
      // so a heading from a different day-wrapper is never picked up.
      function findPrecedingDateHeading(table) {
        const wrapper = table.parentElement;
        if (!wrapper) return null;
        const headingEl = wrapper.querySelector(
          `:scope > ${BET9JA_DESKTOP_SELECTORS.dateHeadingWrapper} ${BET9JA_DESKTOP_SELECTORS.dateHeading}`
        );
        return headingEl ? text(headingEl) : null;
      }

      // A row candidate must carry a real matchup cell -- see
      // BET9JA_DESKTOP_SELECTORS.matchup's own comment (12 of 30 real
      // `.table-f` elements on a real page turned out to be structural/
      // spacer rows with no matchup cell at all).
      function isCandidateRow(el) {
        return (
          el.matches(BET9JA_DESKTOP_SELECTORS.row) &&
          el.querySelector(BET9JA_DESKTOP_SELECTORS.matchup) &&
          !el.closest(EXCLUDED_ANCESTOR_SELECTOR)
        );
      }

      let sectionsSeen = 0;
      let recordsSeen = 0;
      let recordsUnresolved = 0;
      let recordsExpectedUnsupported = 0;
      // Only populated when resolveTableCompetition was supplied -- one
      // entry per `.sports-table`, so a caller (soccer_walker.js's batch
      // selector) can classify each SELECTED competition as captured,
      // confirmed-empty, or attribution-unresolved, rather than assuming
      // every selected competition succeeded merely because the batch as
      // a whole produced some fixtures.
      const tableAttributionSummary = resolveTableCompetition ? [] : null;

      desktopTables.forEach((table) => {
        // One date heading per table (see findPrecedingDateHeading), so
        // every row in a table shares one section -- unlike the LEGACY
        // profile's per-competition-group sections, there is no confirmed
        // sub-grouping within one `.sports-table` to reset on.
        const currentDateHeading = findPrecedingDateHeading(table);
        let currentSectionIndex = -1; // -1 == no row counted under this table yet
        let recordIndexInSection = 0;
        let candidateRowCountInTable = 0;

        // Resolved ONCE per table, never per row -- a resolver deciding
        // differently for two rows in the same table would itself be
        // evidence of a bug, not a per-row concept.
        const tableAttribution = resolveTableCompetition ? resolveTableCompetition(table) || { resolved: false } : null;
        const attributionUnresolved = !!(tableAttribution && !tableAttribution.resolved);
        const tableRegion = tableAttribution && tableAttribution.resolved ? tableAttribution.countryNameRaw || null : fallbackRegion;
        const tableCompetition =
          tableAttribution && tableAttribution.resolved ? tableAttribution.competitionNameRaw || null : fallbackCompetition;
        const tableResolvedCompetitionId =
          tableAttribution && tableAttribution.resolved ? tableAttribution.sourceCompetitionId || null : null;

        Array.from(table.children).forEach((child) => {
          if (!isCandidateRow(child)) {
            return;
          }
          candidateRowCountInTable += 1;
          if (currentSectionIndex === -1) {
            sectionsSeen += 1;
            currentSectionIndex = sectionsSeen - 1;
            recordIndexInSection = 0;
          }
          recordsSeen += 1;
          const rowClassification = processRow({
            fields: extractBet9jaDesktopFields(
              child,
              currentDateHeading,
              urlCompetitionInfo,
              forcedSportContextResult.ok ? forcedSportContextResult.sportHint : null
            ),
            region: tableRegion,
            competition: tableCompetition,
            sectionIndex: currentSectionIndex,
            recordIndex: recordIndexInSection,
            capturedAtUtc,
            seenFixtureIdsThisCapture,
            previousIndex,
            updatedIndex,
            fixtures,
            unparsedRecords,
            attributionUnresolved,
            resolvedSourceCompetitionId: tableResolvedCompetitionId,
          });
          if (rowClassification === ROW_UNRESOLVED) recordsUnresolved += 1;
          if (rowClassification === ROW_EXPECTED_UNSUPPORTED) recordsExpectedUnsupported += 1;
          recordIndexInSection += 1;
        });

        if (tableAttributionSummary) {
          tableAttributionSummary.push({
            source_competition_id: tableResolvedCompetitionId,
            resolved: !!(tableAttribution && tableAttribution.resolved),
            row_count: candidateRowCountInTable,
          });
        }
      });

      return finalize({
        sectionsSeen,
        recordsSeen,
        recordsUnresolved,
        recordsExpectedUnsupported,
        collapsedSectionsDetected: false,
        lazyLoadingDetected: doc.querySelector(SELECTORS.lazyPlaceholder) !== null,
        fallbackProfileActive: true,
        tableAttributionSummary,
      });
    }

    const groups = Array.from(rootEl.querySelectorAll(SELECTORS.competitionGroup));
    let recordsSeen = 0;
    let recordsUnresolved = 0;
    let recordsExpectedUnsupported = 0;
    let collapsedSectionsDetected = false;
    let lazyLoadingDetected = doc.querySelector(SELECTORS.lazyPlaceholder) !== null;

    groups.forEach((group, sectionIndex) => {
      const headerEl = group.querySelector(SELECTORS.competitionHeader);
      const headerText = text(headerEl);
      const separatorIndex = headerText.indexOf(' - ');
      const region = separatorIndex >= 0 ? headerText.slice(0, separatorIndex).trim() : null;
      const competition = separatorIndex >= 0 ? headerText.slice(separatorIndex + 3).trim() : headerText || null;

      const isCollapsed =
        group.getAttribute('aria-expanded') === 'false' ||
        (headerEl && headerEl.getAttribute('aria-expanded') === 'false');
      if (isCollapsed) {
        collapsedSectionsDetected = true;
      }
      if (group.querySelector(SELECTORS.showMore)) {
        collapsedSectionsDetected = true;
      }

      const rows = Array.from(group.querySelectorAll(SELECTORS.fixtureRow));
      rows.forEach((row, recordIndex) => {
        recordsSeen += 1;
        const rowClassification = processRow({
          fields: extractLegacyFields(row),
          region,
          competition,
          sectionIndex,
          recordIndex,
          capturedAtUtc,
          seenFixtureIdsThisCapture,
          previousIndex,
          updatedIndex,
          fixtures,
          unparsedRecords,
        });
        if (rowClassification === ROW_UNRESOLVED) recordsUnresolved += 1;
        if (rowClassification === ROW_EXPECTED_UNSUPPORTED) recordsExpectedUnsupported += 1;
      });
    });

    if (rootEl.querySelector(SELECTORS.showMore)) {
      collapsedSectionsDetected = true;
    }

    return finalize({
      sectionsSeen: groups.length,
      recordsSeen,
      recordsUnresolved,
      recordsExpectedUnsupported,
      collapsedSectionsDetected,
      lazyLoadingDetected,
      fallbackProfileActive: false,
    });
  }

  const api = { captureFromDocument, sanitizeSourceUrl, parseBet9jaCompetitionUrl, PARSER_VERSION, SELECTORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
