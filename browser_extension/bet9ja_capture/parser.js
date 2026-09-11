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
 *   2. BET9JA_DESKTOP (BET9JA_DESKTOP_SELECTORS below) -- verified against
 *      one real, sanitized row from the live desktop sportsbook
 *      (tests/fixtures/bet9ja_desktop_real_sample.html, captured
 *      2026-09-11 from https://sports.bet9ja.com/sport/soccer/1). Used
 *      ONLY when the legacy root selector matches nothing at all --
 *      real Bet9ja rows (`.table-f`) carry no competition-group wrapper
 *      in the one sample seen so far, so this profile processes every
 *      matched row as one flat, ungrouped batch (region/competition
 *      null) and reports it as CAPTURE_PARTIAL with a dedicated reason:
 *      full-page coverage (multiple leagues, collapsed sections,
 *      lazy-loaded pagination) has NOT been verified for this profile
 *      the way the legacy root-scoped path is designed to. See README.md
 *      "Real-page validation status" for exactly what has and hasn't
 *      been confirmed, and tests/fixtures/bet9ja_desktop_real_sample.html's
 *      own comment for the full provenance/limitations of this one
 *      sample (single pre-match row; no live/virtual/Zoom sample seen
 *      yet, so this profile does not attempt to distinguish them).
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

  // Real Bet9ja desktop sportsbook markup, confirmed against exactly one
  // sanitized pre-match row (see the profile-selection comment above and
  // tests/fixtures/bet9ja_desktop_real_sample.html). Nothing in this shape
  // carries the SELECTORS.* attributes above (no data-status, data-sport,
  // data-kickoff-utc, data-market-family) -- identity and market/outcome
  // labels are instead embedded in element `id`s.
  const BET9JA_DESKTOP_SELECTORS = {
    row: '.table-f',
    home: '.sports-table__home',
    away: '.sports-table__away',
    kickoff: '.sports-table__time',
    // Matches the matchup cell's own id, e.g.
    // "home_highlights_sport-1_event-832455154" -- the only place this
    // markup carries a stable per-fixture identifier.
    identityElement: '[id*="_event-"]',
    identityPattern: /sport-([a-z0-9]+)_event-([a-z0-9]+)/i,
    // Matches each odds <li>'s own id, e.g.
    // "..._event-832455154_odds_market-1x2_sign-1" -- family and outcome
    // label are both embedded here; the element's text is the price.
    oddsItem: '.sports-table__odds-item[id*="_market-"]',
    oddsItemIdPattern: /_market-([a-z0-9]+)_sign-(.+)$/i,
  };

  // Confirmed from the one real sample: sport-1 == Soccer (the sample was
  // captured from the https://sports.bet9ja.com/sport/soccer/1 URL). Other
  // sport codes are unconfirmed and intentionally left unmapped (routed to
  // UNSUPPORTED_SPORT rather than guessed).
  const BET9JA_DESKTOP_SPORT_CODE_MAP = { 1: 'SOCCER' };

  // Defensive exclusion for the BET9JA_DESKTOP fallback profile only: since
  // it has no known root/list wrapper to scope its row search to (see
  // above), it searches the whole document for `.table-f` -- this skips
  // any match whose ancestor looks like site navigation or the betslip
  // panel, so neither can ever be mistaken for a fixture row. The LEGACY
  // profile never needs this: its rows are always scoped under a matched
  // root element already.
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
  function extractBet9jaDesktopFields(row) {
    const identityEl = row.querySelector(BET9JA_DESKTOP_SELECTORS.identityElement);
    const identityMatch = identityEl && identityEl.id.match(BET9JA_DESKTOP_SELECTORS.identityPattern);
    const sportCode = identityMatch ? identityMatch[1] : null;
    const externalFixtureRef = identityMatch ? `bet9ja-event-${identityMatch[2]}` : null;

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
      sportHintRaw: (sportCode && BET9JA_DESKTOP_SPORT_CODE_MAP[sportCode]) || '',
      statusAttr: 'PRE', // no live/virtual/Zoom sample seen yet -- see file-top comment
      externalFixtureRef,
      markets: Array.from(marketsByFamily.values()),
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
  }) {
    const { homeRaw, awayRaw, kickoffText, kickoffUtcAttr, sportHintRaw, statusAttr, externalFixtureRef, markets } = fields;
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
      markets: markets.map((m) => ({ family: m.family, line: m.line, outcomes: m.outcomes })),
    };

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
      return;
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
      return;
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
      return;
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
      return;
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
      return;
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
      return;
    }

    // status === 'PRE' from here on.
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
      });
    }
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

    const envelopeBase = {
      schema_version: 'bet9ja-fixture-capture.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
    };

    function finalize({
      sectionsSeen,
      recordsSeen,
      collapsedSectionsDetected,
      lazyLoadingDetected,
      fallbackProfileActive,
      zeroRecordsReason = 'NO_RECORDS_FOUND',
    }) {
      const recordsUnresolved = unparsedRecords.filter((r) => !r.expected_unsupported).length;

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
            collapsed_sections_detected: collapsedSectionsDetected,
            lazy_loading_detected: lazyLoadingDetected,
          },
          fixtures,
          unparsed_records: unparsedRecords,
        },
        updatedIndex,
      };
    }

    const rootEl = doc.querySelector(SELECTORS.root);

    if (!rootEl) {
      // LEGACY root not found -- before reporting a true failure, try the
      // BET9JA_DESKTOP fallback profile (see SELECTOR CONTRACT comment).
      // It has no known root/list wrapper, so it searches the whole
      // document for its own row selector directly, excluding anything
      // that looks like site navigation or the betslip panel.
      const fallbackRows = Array.from(doc.querySelectorAll(BET9JA_DESKTOP_SELECTORS.row)).filter(
        (row) => !row.closest(EXCLUDED_ANCESTOR_SELECTOR)
      );

      if (fallbackRows.length === 0) {
        return finalize({
          sectionsSeen: 0,
          recordsSeen: 0,
          collapsedSectionsDetected: false,
          lazyLoadingDetected: false,
          fallbackProfileActive: false,
          zeroRecordsReason: 'SELECTOR_ROOT_NOT_FOUND',
        });
      }

      fallbackRows.forEach((row, recordIndex) => {
        processRow({
          fields: extractBet9jaDesktopFields(row),
          region: null,
          competition: null,
          sectionIndex: 0,
          recordIndex,
          capturedAtUtc,
          seenFixtureIdsThisCapture,
          previousIndex,
          updatedIndex,
          fixtures,
          unparsedRecords,
        });
      });

      return finalize({
        sectionsSeen: 1,
        recordsSeen: fallbackRows.length,
        collapsedSectionsDetected: false,
        lazyLoadingDetected: doc.querySelector(SELECTORS.lazyPlaceholder) !== null,
        fallbackProfileActive: true,
      });
    }

    const groups = Array.from(rootEl.querySelectorAll(SELECTORS.competitionGroup));
    let recordsSeen = 0;
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
        processRow({
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
      });
    });

    if (rootEl.querySelector(SELECTORS.showMore)) {
      collapsedSectionsDetected = true;
    }

    return finalize({
      sectionsSeen: groups.length,
      recordsSeen,
      collapsedSectionsDetected,
      lazyLoadingDetected,
      fallbackProfileActive: false,
    });
  }

  const api = { captureFromDocument, sanitizeSourceUrl, PARSER_VERSION, SELECTORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
