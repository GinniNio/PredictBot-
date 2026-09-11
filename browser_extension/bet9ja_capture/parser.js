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
 * SELECTOR CONTRACT -- this is a documented, best-effort assumption about
 * Bet9ja's markup, tuned against the sanitized fixtures in tests/fixtures/.
 * It has NOT yet been verified against a real Bet9ja page. Expect to edit
 * only the SELECTORS object below once real markup is available -- the
 * traversal/normalization logic underneath should not need to change for a
 * pure selector adjustment.
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
    row,
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
    const homeRaw = text(row.querySelector(SELECTORS.home));
    const awayRaw = text(row.querySelector(SELECTORS.away));
    const kickoffEl = row.querySelector(SELECTORS.kickoff);
    const kickoffText = text(kickoffEl);
    const kickoffUtcAttr =
      (kickoffEl && kickoffEl.getAttribute('data-kickoff-utc')) || row.getAttribute('data-kickoff-utc') || null;
    const sportHintRaw = row.getAttribute('data-sport') || '';
    const sport = sportHintRaw.trim().toUpperCase() || 'UNKNOWN';
    const { status, recognized: statusRecognized } = resolveStatus(row.getAttribute('data-status'));
    const externalFixtureRef = row.getAttribute('data-fixture-id') || null;
    const markets = Array.from(row.querySelectorAll(SELECTORS.market)).map(parseMarketElement);

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
      status_hint: row.getAttribute('data-status') || null,
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
          detail: `data-status="${row.getAttribute('data-status') || ''}" is not one of PRE/LIVE/VIRTUAL/ZOOM.`,
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

    const rootEl = doc.querySelector(SELECTORS.root);
    if (!rootEl) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['SELECTOR_ROOT_NOT_FOUND'],
          coverage: {
            visible_page_only: true,
            sections_seen: 0,
            records_seen: 0,
            records_parsed: 0,
            records_unresolved: 0,
            collapsed_sections_detected: false,
            lazy_loading_detected: false,
          },
          fixtures: [],
          unparsed_records: [],
        },
        updatedIndex,
      };
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
          row,
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

    const recordsUnresolved = unparsedRecords.filter((r) => !r.expected_unsupported).length;

    let captureStatus;
    const statusReasons = [];
    if (recordsSeen === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.push('NO_RECORDS_FOUND');
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
          sections_seen: groups.length,
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

  const api = { captureFromDocument, sanitizeSourceUrl, PARSER_VERSION, SELECTORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
