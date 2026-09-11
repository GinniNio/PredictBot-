/**
 * Bet9ja OPEN-TICKET-capture parser -- pure DOM-in, envelope-out logic
 * shared by the content script (real browser) and the Node test suite
 * (jsdom). Mirrors parser.js's architecture and honesty guarantees; see
 * that file's own header comment for the general pattern this follows.
 *
 * Scope (see README.md "Open bet ticket capture"):
 *   - Reads the DOM already loaded in the tab's "Open Bets"/"My Bets" view.
 *     No network requests, no cookies, no tokens, no account/balance data
 *     are ever read.
 *   - Read-only. Never places a bet, never cancels/cashes out a bet, never
 *     interacts with the page beyond reading text already rendered.
 *   - Pre-match tickets only. A ticket containing even one live, Virtual,
 *     or Zoom leg is excluded from `tickets[]` entirely (see
 *     TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG below) -- never partially
 *     admitted with the offending leg silently dropped.
 *   - Settled/cashed-out tickets are out of scope for this release (see
 *     "Capture settled bets", a future, separate work item) and are
 *     excluded, not guessed at.
 *   - FAIL CLOSED on ticket-boundary ambiguity: if any leg cannot be
 *     confidently assigned to (and fully parsed within) a given ticket,
 *     the WHOLE ticket is routed to `unresolved_tickets` rather than
 *     partially or speculatively included -- see processTicket() below.
 *     This is the single most important safety property of this parser:
 *     assigning a leg to the wrong ticket would corrupt stake/return math
 *     downstream in the betting ledger, which a missing ticket does not.
 *
 * SELECTOR CONTRACT -- ALL SELECTORS BELOW ARE UNVERIFIED PLACEHOLDERS.
 *
 *   Unlike parser.js's BET9JA_DESKTOP profile (confirmed via live
 *   inspection of the real fixtures/competition pages across 4 rounds of
 *   real-page validation), NO real "Open Bets" page sample has been seen
 *   yet at the time this module was written. TICKET_SELECTORS below is a
 *   best-effort, structurally-plausible first guess only -- modeled on
 *   common bet-slip-history markup patterns and on the identity-via-id
 *   convention already confirmed for fixture rows (`[id*="_event-"]`) --
 *   and MUST be corrected against real DOM evidence before this feature
 *   is considered validated, exactly per the discipline established in
 *   parser.js's own selector-correction history (three real-evidence-driven
 *   rounds were needed there; the same is expected here). Every real
 *   capture is expected to report zero tickets_parsed until that happens;
 *   this is a currently-known, explicitly-flagged limitation, not a bug.
 *
 *   captureFromDocument()'s return always includes
 *   `capture_status_reasons: ['TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER']` for
 *   exactly this reason, regardless of whether any ticket happened to
 *   parse, so a real capture result can never be silently mistaken for a
 *   validated one.
 */
(function (root) {
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-ticket-capture-parser@0.1.0-unverified';

  // See SELECTOR CONTRACT above -- every value here is an unconfirmed
  // best guess, not evidence-derived.
  const TICKET_SELECTORS = {
    root: '[data-bet9ja-capture-ticket-root], .open-bets, .my-bets, #open-bets',
    ticket: '.ticket, .bet-slip-item, [data-ticket-id]',
    ticketIdAttr: 'data-ticket-id',
    ticketIdText: '.ticket__id, .ticket-id',
    placedAt: '.ticket__placed-at, .ticket-date',
    placedAtUtcAttr: 'data-placed-at-utc',
    typeText: '.ticket__type, .bet-type',
    unitStake: '.ticket__unit-stake',
    totalStake: '.ticket__total-stake, .ticket__stake',
    potentialReturn: '.ticket__potential-return, .ticket__possible-win',
    statusText: '.ticket__status',
    legRow: '.ticket__leg, .bet-selection',
    legHome: '.leg__home',
    legAway: '.leg__away',
    legMarket: '.leg__market',
    legSelection: '.leg__selection',
    legOdds: '.leg__odds',
    // Reuses the identity-via-id convention confirmed for fixture rows in
    // parser.js (BET9JA_DESKTOP_SELECTORS.identityElement/eventIdPattern) --
    // itself only confirmed on the fixtures page, so its applicability here
    // is likewise unverified, not merely the selector string.
    legIdentityElement: '[id*="_event-"]',
    legEventIdPattern: /event-([a-z0-9]+)/i,
    legStatusHint: '.leg__status',
  };

  const KNOWN_TICKET_STATUSES = new Set(['OPEN', 'PENDING']);
  const EXCLUDED_TICKET_STATUSES = new Set(['SETTLED', 'WON', 'LOST', 'VOID', 'CASHED_OUT', 'CANCELLED']);
  const LIVE_OR_VIRTUAL_OR_ZOOM = new Set(['LIVE', 'VIRTUAL', 'ZOOM']);

  // Unambiguous synonyms only -- an unrecognized ticket-type string (e.g.
  // Bet9ja's real UI category for a straight 4+-leg all-up bet, which has
  // no corresponding entry in ledgers/betting_ledger.py's own
  // TICKET_TYPES = (SINGLE, DOUBLE, TREBLE, SYSTEM)) is deliberately left
  // unmapped rather than guessed -- see ticket_type_taxonomy_gap below.
  const TICKET_TYPE_MAP = {
    single: 'SINGLE',
    double: 'DOUBLE',
    treble: 'TREBLE',
    triple: 'TREBLE',
    system: 'SYSTEM',
  };

  const OUTCOME_LABEL_MAP = {
    '1': 'H', h: 'H', home: 'H',
    x: 'D', d: 'D', draw: 'D',
    '2': 'A', a: 'A', away: 'A',
  };

  function text(el) {
    if (!el) return '';
    return (el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function normalizeForHash(value) {
    return (value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  function parseDecimal(rawText) {
    const cleaned = (rawText || '').replace(/[^0-9.,-]/g, '').trim();
    if (cleaned === '') return null;
    const normalized = cleaned.replace(',', '.');
    const value = Number(normalized);
    return Number.isFinite(value) ? value : null;
  }

  function parsePrice(rawText) {
    const cleaned = (rawText || '').trim();
    if (cleaned === '' || cleaned === '-' || cleaned.toUpperCase() === 'SP') {
      return null;
    }
    const normalized = cleaned.replace(',', '.');
    const value = Number(normalized);
    if (!Number.isFinite(value) || value <= 1) {
      return null;
    }
    return value;
  }

  // Same strict-timestamp-only honesty guarantee as parser.js's
  // resolveKickoff(): a 4-digit year AND an explicit UTC designator are
  // both mandatory. See that function's own comment for the rationale.
  const STRICT_UTC_TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/;

  function resolvePlacedAt(placedAtUtcAttr) {
    if (!placedAtUtcAttr) {
      return { placedAtUtc: null, placedAtResolution: 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP' };
    }
    if (!STRICT_UTC_TIMESTAMP.test(placedAtUtcAttr)) {
      return { placedAtUtc: null, placedAtResolution: 'UNRESOLVED_AMBIGUOUS_TIMESTAMP' };
    }
    const parsedMs = Date.parse(placedAtUtcAttr);
    if (Number.isNaN(parsedMs)) {
      return { placedAtUtc: null, placedAtResolution: 'UNRESOLVED_AMBIGUOUS_TIMESTAMP' };
    }
    return { placedAtUtc: new Date(parsedMs).toISOString(), placedAtResolution: 'EXPLICIT_UTC_ATTRIBUTE' };
  }

  function resolveTicketType(typeRaw) {
    const key = (typeRaw || '').trim().toLowerCase();
    const normalized = TICKET_TYPE_MAP[key] || null;
    return { normalized, taxonomyGap: key !== '' && normalized === null };
  }

  function resolveTicketId(ticketEl) {
    const attrId = ticketEl.getAttribute(TICKET_SELECTORS.ticketIdAttr);
    if (attrId && attrId.trim()) return attrId.trim();
    const textId = text(ticketEl.querySelector(TICKET_SELECTORS.ticketIdText));
    return textId || null;
  }

  function makeUnresolvedTicket({ reason, detail, sourceIndex, raw }) {
    return { reason, detail: detail || null, source_index: sourceIndex, raw };
  }

  function makeExcludedTicket({ reason, detail, sourceIndex, ticketIdRaw }) {
    return { reason, detail: detail || null, source_index: sourceIndex, ticket_id_raw: ticketIdRaw || null };
  }

  /**
   * Parse one leg row into either a resolved leg object or a typed failure
   * reason -- never a partial leg. `sourceEventId`/`fixtureId` are exposed
   * per the explicit requirement that ticket capture must expose
   * `source_event_id` alongside the same stable `fixture_id` scheme used
   * by the fixture-capture extension, so a later importer can cross-
   * reference a leg against the forecast ledger by fixture identity.
   */
  function parseLeg(legEl) {
    const homeRaw = text(legEl.querySelector(TICKET_SELECTORS.legHome));
    const awayRaw = text(legEl.querySelector(TICKET_SELECTORS.legAway));
    const marketRaw = text(legEl.querySelector(TICKET_SELECTORS.legMarket));
    const selectionRaw = text(legEl.querySelector(TICKET_SELECTORS.legSelection));
    const oddsRaw = text(legEl.querySelector(TICKET_SELECTORS.legOdds));
    const statusHintRaw = text(legEl.querySelector(TICKET_SELECTORS.legStatusHint));

    const rawSnapshot = {
      home: homeRaw,
      away: awayRaw,
      market_raw: marketRaw,
      selection_raw: selectionRaw,
      odds_raw: oddsRaw,
      status_hint_raw: statusHintRaw || null,
    };

    const statusHint = (statusHintRaw || '').trim().toUpperCase();
    if (LIVE_OR_VIRTUAL_OR_ZOOM.has(statusHint)) {
      return { ok: false, reason: 'LEG_LIVE_OR_VIRTUAL_OR_ZOOM', detail: `leg status="${statusHint}"`, raw: rawSnapshot };
    }

    if (!homeRaw || !awayRaw) {
      return { ok: false, reason: 'LEG_MISSING_PARTICIPANTS', detail: null, raw: rawSnapshot };
    }

    const odds = parsePrice(oddsRaw);
    if (odds === null) {
      return { ok: false, reason: 'LEG_UNPARSEABLE_ODDS', detail: `odds_raw="${oddsRaw}"`, raw: rawSnapshot };
    }

    const identityEl = legEl.querySelector(TICKET_SELECTORS.legIdentityElement);
    const eventIdMatch = identityEl && identityEl.id.match(TICKET_SELECTORS.legEventIdPattern);
    const sourceEventId = eventIdMatch ? eventIdMatch[1] : null;

    // fixture_id must match parser.js's own scheme exactly
    // (Bet9jaIds.stableId('bxf', ['external', 'bet9ja-event-<id>'])) so a
    // ticket leg's fixture_id lines up with the same fixture captured by
    // the fixture-capture extension. Without a source event id there is no
    // reliable natural key available here (no kickoff time is shown in a
    // ticket view) -- falling back to a home/away/market/selection key in
    // that case is markedly weaker (no kickoff to disambiguate a rematch
    // later in the season), so fixture_id_resolution flags exactly which
    // path was used rather than presenting both as equally trustworthy.
    let fixtureId;
    let fixtureIdResolution;
    if (sourceEventId) {
      fixtureId = Bet9jaIds.stableId('bxf', ['external', `bet9ja-event-${sourceEventId}`]);
      fixtureIdResolution = 'EXTERNAL_EVENT_ID';
    } else {
      fixtureId = Bet9jaIds.stableId('bxf', [
        normalizeForHash(homeRaw),
        normalizeForHash(awayRaw),
        normalizeForHash(marketRaw),
        normalizeForHash(selectionRaw),
      ]);
      fixtureIdResolution = 'NATURAL_KEY_FALLBACK_NO_SOURCE_EVENT_ID';
    }

    const selectionMapped = OUTCOME_LABEL_MAP[(selectionRaw || '').toLowerCase()] || null;

    return {
      ok: true,
      leg: {
        source_event_id: sourceEventId,
        fixture_id: fixtureId,
        fixture_id_resolution: fixtureIdResolution,
        participants: { home: homeRaw, away: awayRaw },
        market_raw: marketRaw || null,
        selection_raw: selectionRaw || null,
        selection: selectionMapped,
        odds_raw: oddsRaw || null,
        odds,
      },
    };
  }

  /**
   * Parse one ticket container into either a resolved ticket (pushed to
   * `tickets`), an excluded-by-design ticket (settled/live/virtual/Zoom --
   * pushed to `excludedTickets`), or an unresolved ticket (any genuine
   * ambiguity -- pushed to `unresolvedTickets`). Exactly one of the three,
   * per the fail-closed contract described in this file's header comment.
   */
  function processTicket(ticketEl, sourceIndex, capturedAtUtc) {
    const ticketIdRaw = resolveTicketId(ticketEl);
    if (!ticketIdRaw) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MISSING_TICKET_ID',
          detail: 'No ticket id attribute or id element found; refusing to guess a synthetic one.',
          sourceIndex,
          raw: { html_snippet_omitted: true },
        }),
      };
    }

    const statusRaw = text(ticketEl.querySelector(TICKET_SELECTORS.statusText));
    const statusUpper = (statusRaw || 'OPEN').trim().toUpperCase();
    if (EXCLUDED_TICKET_STATUSES.has(statusUpper)) {
      return {
        outcome: 'EXCLUDED',
        record: makeExcludedTicket({
          reason: 'TICKET_STATUS_OUT_OF_SCOPE',
          detail: `status="${statusUpper}" is settled/cashed-out; capture "settled bets" is a separate future work item.`,
          sourceIndex,
          ticketIdRaw,
        }),
      };
    }
    if (!KNOWN_TICKET_STATUSES.has(statusUpper)) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'UNRECOGNIZED_TICKET_STATUS',
          detail: `status="${statusUpper}" is not one of OPEN/PENDING.`,
          sourceIndex,
          raw: { ticket_id_raw: ticketIdRaw, status_raw: statusRaw },
        }),
      };
    }

    const legEls = Array.from(ticketEl.querySelectorAll(TICKET_SELECTORS.legRow));
    if (legEls.length === 0) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'NO_LEGS_FOUND',
          detail: 'Ticket container matched but contains no leg rows.',
          sourceIndex,
          raw: { ticket_id_raw: ticketIdRaw },
        }),
      };
    }

    const legs = [];
    for (let i = 0; i < legEls.length; i += 1) {
      const parsed = parseLeg(legEls[i]);
      if (!parsed.ok) {
        if (parsed.reason === 'LEG_LIVE_OR_VIRTUAL_OR_ZOOM') {
          return {
            outcome: 'EXCLUDED',
            record: makeExcludedTicket({
              reason: 'TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG',
              detail: `leg[${i}]: ${parsed.detail}`,
              sourceIndex,
              ticketIdRaw,
            }),
          };
        }
        // Fail closed: ANY unparseable/ambiguous leg voids the WHOLE
        // ticket rather than admitting it with that leg dropped -- a
        // partial ticket would silently corrupt the stake/return math a
        // downstream importer relies on legs[] being complete for.
        return {
          outcome: 'UNRESOLVED',
          record: makeUnresolvedTicket({
            reason: 'LEG_FAILED_TO_PARSE',
            detail: `leg[${i}] reason=${parsed.reason} detail=${parsed.detail || ''}`,
            sourceIndex,
            raw: { ticket_id_raw: ticketIdRaw, leg_index: i, leg_raw: parsed.raw },
          }),
        };
      }
      legs.push(parsed.leg);
    }

    const placedAtRaw = text(ticketEl.querySelector(TICKET_SELECTORS.placedAt));
    const placedAtUtcAttr = ticketEl.getAttribute(TICKET_SELECTORS.placedAtUtcAttr);
    const { placedAtUtc, placedAtResolution } = resolvePlacedAt(placedAtUtcAttr);

    const typeRaw = text(ticketEl.querySelector(TICKET_SELECTORS.typeText));
    const { normalized: ticketTypeNormalized, taxonomyGap } = resolveTicketType(typeRaw);

    const unitStakeRaw = text(ticketEl.querySelector(TICKET_SELECTORS.unitStake));
    const totalStakeRaw = text(ticketEl.querySelector(TICKET_SELECTORS.totalStake));
    const potentialReturnRaw = text(ticketEl.querySelector(TICKET_SELECTORS.potentialReturn));

    return {
      outcome: 'PARSED',
      record: {
        bet9ja_ticket_id: ticketIdRaw,
        status: statusUpper,
        placed_at_raw: placedAtRaw || null,
        placed_at_utc: placedAtUtc,
        placed_at_resolution: placedAtResolution,
        ticket_type_raw: typeRaw || null,
        ticket_type_normalized: ticketTypeNormalized,
        // True when ticket_type_raw is a real, non-empty value that does
        // not map onto ledgers/betting_ledger.py's TICKET_TYPES (SINGLE/
        // DOUBLE/TREBLE/SYSTEM) -- e.g. a straight 4+-leg all-up bet,
        // which the current ledger schema has no dedicated type for. This
        // is surfaced rather than silently mapped to the nearest guess so
        // the (future) importer step can decide how to handle the gap
        // instead of inheriting a wrong assumption.
        ticket_type_taxonomy_gap: taxonomyGap,
        unit_stake_raw: unitStakeRaw || null,
        unit_stake: parseDecimal(unitStakeRaw),
        total_stake_raw: totalStakeRaw || null,
        total_stake: parseDecimal(totalStakeRaw),
        potential_return_raw: potentialReturnRaw || null,
        potential_return: parseDecimal(potentialReturnRaw),
        legs,
        captured_at_utc: capturedAtUtc,
        parser_version: PARSER_VERSION,
      },
    };
  }

  function sanitizeSourceUrl(rawUrl) {
    if (!rawUrl) return '';
    try {
      const parsed = new URL(rawUrl);
      return `${parsed.origin}${parsed.pathname}`;
    } catch (err) {
      return String(rawUrl).split(/[?#]/)[0];
    }
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string}} context
   * @returns {{envelope: object}}
   */
  function captureFromDocument(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;

    const envelopeBase = {
      schema_version: 'bet9ja-ticket-capture.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
    };

    const rootEl = doc.querySelector(TICKET_SELECTORS.root);
    const ticketEls = rootEl
      ? Array.from(rootEl.querySelectorAll(TICKET_SELECTORS.ticket))
      : Array.from(doc.querySelectorAll(TICKET_SELECTORS.ticket));

    const tickets = [];
    const unresolvedTickets = [];
    const excludedTickets = [];
    let legsSeen = 0;
    let legsParsed = 0;

    ticketEls.forEach((ticketEl, index) => {
      const legCountForThisTicket = ticketEl.querySelectorAll(TICKET_SELECTORS.legRow).length;
      legsSeen += legCountForThisTicket;
      const { outcome, record } = processTicket(ticketEl, index, capturedAtUtc);
      if (outcome === 'PARSED') {
        tickets.push(record);
        legsParsed += record.legs.length;
      } else if (outcome === 'EXCLUDED') {
        excludedTickets.push(record);
      } else {
        unresolvedTickets.push(record);
      }
    });

    const ticketsSeen = ticketEls.length;
    let captureStatus;
    const statusReasons = ['TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER'];
    if (ticketsSeen === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.push('NO_TICKETS_FOUND');
    } else if (tickets.length === 0 && unresolvedTickets.length === 0 && excludedTickets.length === 0) {
      // Structurally unreachable given ticketsSeen > 0 (every ticket
      // element yields a PARSED, UNRESOLVED, or EXCLUDED record) -- kept
      // as an explicit guard, mirroring parser.js's own NO_USABLE_OUTPUT
      // guard, so this can never regress into a silent empty success.
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.push('NO_USABLE_OUTPUT');
    } else {
      captureStatus = 'CAPTURE_OK';
      if (unresolvedTickets.length > 0) {
        captureStatus = 'CAPTURE_PARTIAL';
        statusReasons.push('UNRESOLVED_TICKETS_PRESENT');
      }
      if (excludedTickets.length > 0) {
        // Excluded-by-design (settled/cashed-out, or live/Virtual/Zoom) is
        // the ticket-capture equivalent of parser.js's
        // records_expected_unsupported -- an out-of-scope record is not a
        // failure, exactly per that established precedent, even when it
        // is the ONLY record a capture produces (e.g. a page showing
        // nothing but already-settled tickets).
        statusReasons.push('OUT_OF_SCOPE_TICKETS_EXCLUDED');
      }
    }

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        coverage: {
          visible_page_only: true,
          tickets_seen: ticketsSeen,
          tickets_parsed: tickets.length,
          tickets_unresolved: unresolvedTickets.length,
          tickets_excluded: excludedTickets.length,
          legs_seen: legsSeen,
          legs_parsed: legsParsed,
        },
        tickets,
        unresolved_tickets: unresolvedTickets,
        excluded_tickets: excludedTickets,
      },
    };
  }

  const api = { captureFromDocument, sanitizeSourceUrl, PARSER_VERSION, TICKET_SELECTORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaTicketCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
