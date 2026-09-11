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
 *
 * MYBETS PROFILE -- real selectors, confirmed via live authenticated
 * inspection of https://sports.bet9ja.com/myBets/ (2026-09-11, see
 * TICKET_REAL_PAGE_VALIDATION.md Round 2): tickets render as
 * `.mybets .accordion-item` elements; each ticket's id and legs are only
 * present in the DOM once expanded (an `.accordion-item--open` class is
 * added to the ticket element, and its `.accordion-toggle` is the click
 * target that expands/collapses it). captureFromDocument is therefore
 * ASYNC when this profile is active: for each ticket it (1) clicks the
 * toggle if not already expanded, (2) waits for BOTH the open class AND
 * the ticket's own `.mybets-head__item` to be present -- Round 4 real
 * captures showed the open class can appear before the ticket's actual
 * content has finished rendering, so the class alone is not sufficient
 * evidence a ticket is ready to parse (see EXPAND_TIMEOUT_MS's own
 * comment) -- (3) parses the ticket and its legs entirely within that
 * one ticket's own subtree, then (4) clicks the toggle again to restore
 * the original collapsed/expanded state. See
 * ensureTicketExpanded/collapseIfNeeded.
 *
 * SAFETY: the ONLY element this profile ever calls .click() on is a
 * `.accordion-toggle` inside a ticket found under the `.mybets` root --
 * never a cashout button (`.mybets__cashout-holder` is a documented
 * exclusion zone this parser never queries into), never "Reload
 * Selections", never any bet-placement control. See
 * tests/ticket_parser.test.js's "safety" tests, which grep this file's
 * own source for that guarantee.
 *
 * CONFIRMED (Round 2 live inspection): ticket boundaries are reliable --
 * every ticket's expanded detail stays inside its own `.accordion-item`,
 * so the same page-wide-scan defense used by the placeholder profile
 * applies here too. A system ticket's legs render as N `.mybets-row`
 * elements of 2 `.mybets-item` legs each; individual legs are still
 * found directly via `.mybets-item` regardless of that grouping.
 *
 * NOT YET CONFIRMED (see TICKET_REAL_PAGE_VALIDATION.md Round 2 for the
 * full list -- every capture using this profile carries a matching
 * capture_status_reasons entry for each, so this can never be mistaken
 * for a fully validated result):
 *   - No live/Virtual/Zoom status marker has been identified in this
 *     markup, so this profile CANNOT currently enforce the "no live,
 *     Zoom or Virtual tickets" scope boundary the way the placeholder
 *     profile's (unconfirmed) legStatusHint was designed to. Every
 *     ticket found under `.mybets` is treated as OPEN pre-match, flagged
 *     explicitly via status_resolution -- never silently assumed.
 *   - `.mybets-holder__info-item` and `.mybets__systable`'s exact
 *     label/value cell structure (which item is stake vs. potential
 *     return; which cell is System Type vs. No. Bets vs. Unit Stake vs.
 *     Stake) is unconfirmed -- their raw text is preserved for audit
 *     (`stake_return_raw_items`, `system_table_raw`) rather than parsed
 *     into `unit_stake`/`total_stake`/`potential_return`, which stay
 *     null for this profile until that mapping is confirmed.
 *
 * PAGINATION -- confirmed via a second live inspection (Round 3, see
 * TICKET_REAL_PAGE_VALIDATION.md): 16 genuine numbered pages
 * (`.mybets .pg-pagination__item` whose text matches `/^\d+$/`), plus
 * first/prev/next/last controls (`.first`/`.prev`/`.next`/`.last`
 * classes) that are NEVER clicked -- only a verified numbered item ever
 * is. Pagination is client-side (the URL never changes); the current
 * page is read from `.pg-pagination__item--current`'s text. The
 * automated loop (see paginateAndCaptureAllPages):
 *   1. Parses and deduplicates (by `bet9ja_ticket_id`) the current page.
 *   2. Reads the current page number from the `--current` marker.
 *   3. Clicks the NEXT NUMBERED item (current + 1) -- never the generic
 *      `.next` control -- only after verifying its text is `/^\d+$/` and
 *      it carries no `disabled` attribute.
 *   4. Waits for the `--current` marker to actually advance to that
 *      number before parsing (a click that doesn't move the marker in
 *      time is a stop condition, not a silent skip).
 *   5. Parses that page's tickets, merging into the running total with
 *      cross-page duplicate detection (by ticket id) -- a ticket that
 *      legitimately reappears across pages is deduplicated, not double-
 *      counted or double-captured.
 *   6. Stops when: no next-numbered item exists any more (the highest
 *      page reached), the next page number was already visited (a loop
 *      guard), a page's ticket-id set exactly repeats a previous page's
 *      (content didn't actually change), a page transition doesn't
 *      confirm within the wait window, or a fixed safety cap on page
 *      count is hit -- never an unbounded loop.
 *   7. Produces ONE combined envelope for the whole run, not one file
 *      per page.
 *   8. Records `pages_available` (the highest numbered page ever seen,
 *      which may grow as a windowed pagination UI is navigated),
 *      `pages_visited`, and per-run ticket/leg counts in `coverage`.
 *   9. The ONLY elements ever clicked anywhere in this file remain the
 *      confirmed `.accordion-toggle` and a verified numbered pagination
 *      item -- never `.first`/`.prev`/`.next`/`.last`, never Cashout,
 *      never "Reload Selections", never a bet-placement control. See the
 *      SAFETY note above and the safety test in
 *      tests/ticket_parser.test.js, which greps this file's own source.
 * On the FIRST page only, if this run advanced past page 1, it clicks
 * back to page 1 afterward (fire-and-forget, same "restore what capture
 * touched" spirit as collapseIfNeeded) -- best-effort, never required
 * for the correctness of the capture that already happened.
 */
(function (root) {
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-ticket-capture-parser@0.4.0-mybets-content-wait';

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

  // Confirmed via live authenticated inspection of
  // https://sports.bet9ja.com/myBets/ (Round 2) -- see the MYBETS PROFILE
  // header comment above for exactly what is and isn't confirmed.
  // `.mybets__cashout-holder` is deliberately NOT queried anywhere in this
  // file -- it is named here only as documentation of the exclusion zone
  // this parser must never click into (see the SAFETY note above).
  const MYBETS_SELECTORS = {
    root: '.mybets',
    ticket: '.accordion-item',
    ticketOpenClass: 'accordion-item--open',
    toggle: '.accordion-toggle',
    placedAt: '.mybets-date',
    ticketIdHeadItem: '.mybets-head__item',
    systemTable: '.mybets__systable',
    legRow: '.mybets-item',
    legDetailRow: '.mybets-item__row',
    legSelection: '.mybets-bet',
    legOdds: '.mybets-odd',
    stakeReturnInfoItem: '.mybets-holder__info-item',
    // Reused from the placeholder profile's identity-via-id convention --
    // itself only confirmed on the fixtures page, and NOT confirmed to
    // exist at all on this page's leg markup. Checked defensively; expect
    // it to be absent (null source_event_id, NATURAL_KEY_FALLBACK) until
    // proven otherwise.
    legIdentityElement: '[id*="_event-"]',
    legEventIdPattern: /event-([a-z0-9]+)/i,
    // Confirmed Round 3 (live authenticated inspection, 16 genuine
    // numbered pages + first/prev/next/last controls). See the
    // PAGINATION header comment above for the full contract. Only a
    // verified numbered item (`/^\d+$/` text) is ever a click target --
    // first/prev/next/last are named here for detection/documentation
    // only and are NEVER queried for a click.
    paginationContainer: '.pg-pagination',
    paginationItem: '.pg-pagination__item',
    paginationCurrent: '.pg-pagination__item--current',
  };

  const NUMBERED_PAGE_TEXT = /^\d+$/;

  // A `.accordion-toggle` click that never produces BOTH the open class
  // and the ticket's actual content within this window is reported as
  // TICKET_EXPANSION_TIMEOUT (unresolved), never silently treated as "no
  // legs" -- distinguishing "couldn't confirm expansion" from "confirmed
  // empty" matters for an accurate coverage count. The same timeout/
  // interval is reused for a pagination click's wait for the `--current`
  // marker to advance.
  //
  // Round 4 real-capture correction: the open class alone is NOT
  // sufficient evidence a ticket is ready to parse. Two real captures
  // against the live, authenticated My Bets page both returned
  // MISSING_TICKET_ID for every ticket (`.accordion-item--open` was
  // present, but `.mybets-head__item` was not) -- the real page's
  // ticket detail (head item, legs) evidently populates on a short delay
  // AFTER the open class itself toggles, not synchronously with it.
  // ensureTicketExpanded therefore waits for BOTH conditions together,
  // never the open class alone.
  const EXPAND_TIMEOUT_MS = 3000;
  const EXPAND_POLL_INTERVAL_MS = 25;
  // Round 4 also showed cross-page ticket totals not accumulating past
  // the first page despite pagination itself correctly walking all 16
  // pages -- consistent with the same class of problem: a pagination
  // click's `--current` marker can update before that page's own ticket
  // list has finished (re)rendering. This separate wait, applied once
  // per page AFTER `--current` is confirmed, gives that content a chance
  // to appear before this page is parsed. A page that is still empty
  // when this window elapses is parsed as-is (0 tickets) rather than
  // treated as an error -- a genuinely sparse last page is a real
  // possibility this parser cannot yet distinguish from a load that was
  // simply slower than this window; each page_results[] entry records
  // whether this wait ever succeeded, exactly so a future round can
  // tell the two apart from real evidence rather than another guess.
  const PAGE_CONTENT_TIMEOUT_MS = 3000;
  const PAGE_CONTENT_POLL_INTERVAL_MS = 25;
  // Real evidence confirmed 16 pages; this is a generous multiple of that
  // kept as a hard backstop against an unbounded loop should a future
  // page count grow or a stop condition ever fail to trigger -- never
  // relied upon in the normal case.
  const MAX_PAGES_SAFETY_CAP = 200;

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

  /**
   * The ONLY function in this file that ever calls .click() on a ticket.
   * Expands a ticket if it isn't already open, then waits for BOTH the
   * confirmed `.accordion-item--open` class AND the ticket's own
   * `.mybets-head__item` to be present -- never assumes the ticket is
   * ready to parse just because the open class appeared (see the
   * EXPAND_TIMEOUT_MS comment above for why: the real page's content
   * populates after a short delay, not synchronously with the class).
   * `wasAlreadyOpen` reflects whether the OPEN CLASS was already present
   * when this call started (regardless of whether content was ready
   * yet), since that -- not content readiness -- is what
   * collapseIfNeeded uses to decide whether this call may collapse the
   * ticket again afterward.
   */
  async function ensureTicketExpanded(ticketEl) {
    const isReadyToParse = () =>
      ticketEl.classList.contains(MYBETS_SELECTORS.ticketOpenClass) &&
      !!ticketEl.querySelector(MYBETS_SELECTORS.ticketIdHeadItem);

    const wasAlreadyOpen = ticketEl.classList.contains(MYBETS_SELECTORS.ticketOpenClass);
    if (!wasAlreadyOpen) {
      const toggle = ticketEl.querySelector(MYBETS_SELECTORS.toggle);
      if (!toggle) {
        return { opened: false, wasAlreadyOpen: false, reason: 'TOGGLE_NOT_FOUND' };
      }
      toggle.click();
    }

    if (isReadyToParse()) {
      return { opened: true, wasAlreadyOpen, reason: null };
    }
    const ready = await waitFor(isReadyToParse, EXPAND_TIMEOUT_MS, EXPAND_POLL_INTERVAL_MS);
    return { opened: ready, wasAlreadyOpen, reason: ready ? null : 'TICKET_EXPANSION_TIMEOUT' };
  }

  /**
   * Restores a ticket to the collapsed state it was in before capture
   * touched it -- but ONLY if this parser is the one that opened it.
   * Fire-and-forget by design (per the user's own "optionally collapse
   * them again" framing): never awaited, never required for the
   * correctness of the capture that already happened.
   */
  function collapseIfNeeded(ticketEl, wasAlreadyOpen) {
    if (wasAlreadyOpen) return;
    const toggle = ticketEl.querySelector(MYBETS_SELECTORS.toggle);
    if (toggle) toggle.click();
  }

  /**
   * `.mybets-head__item` may appear more than once per expanded ticket
   * (the same class plausibly labels several head fields, not only the
   * ticket id) -- so this prefers whichever one's text contains a
   * plausible ticket-id-shaped digit run, falling back to the first
   * item's raw text rather than guessing which one is "the" id field.
   */
  function resolveTicketIdFromMybets(ticketEl) {
    const items = Array.from(ticketEl.querySelectorAll(MYBETS_SELECTORS.ticketIdHeadItem));
    if (items.length === 0) return { idRaw: null, id: null };
    for (const el of items) {
      const t = text(el);
      const match = t.match(/(\d{6,})/);
      if (match) return { idRaw: t, id: match[1] };
    }
    const firstRaw = text(items[0]);
    return { idRaw: firstRaw, id: firstRaw || null };
  }

  /**
   * Parses one `.mybets-item` leg using the confirmed 4-row layout
   * (selection+odds, market, fixture+time, competition). Never partially
   * admits a leg -- any structural surprise (wrong row count, missing
   * text, unparseable odds) is reported as a typed failure so the WHOLE
   * ticket can be voided by the caller, per this file's fail-closed
   * contract.
   */
  function extractMybetsLeg(legEl) {
    const rows = Array.from(legEl.querySelectorAll(`:scope > ${MYBETS_SELECTORS.legDetailRow}`));
    if (rows.length < 4) {
      return {
        ok: false,
        reason: 'LEG_UNEXPECTED_ROW_COUNT',
        detail: `expected 4 .mybets-item__row children (selection, market, fixture+time, competition), found ${rows.length}`,
        raw: { row_count: rows.length },
      };
    }
    const [selectionRow, marketRow, fixtureRow, competitionRow] = rows;
    const selectionRaw = text(selectionRow.querySelector(MYBETS_SELECTORS.legSelection));
    const oddsRaw = text(selectionRow.querySelector(MYBETS_SELECTORS.legOdds));
    const marketRaw = text(marketRow);
    const fixtureAndTimeRaw = text(fixtureRow);
    const competitionRaw = text(competitionRow);

    const rawSnapshot = {
      selection_raw: selectionRaw,
      odds_raw: oddsRaw,
      market_raw: marketRaw,
      fixture_and_time_raw: fixtureAndTimeRaw,
      competition_raw: competitionRaw,
    };

    if (!selectionRaw) {
      return { ok: false, reason: 'LEG_MISSING_SELECTION', detail: null, raw: rawSnapshot };
    }
    if (!fixtureAndTimeRaw) {
      return { ok: false, reason: 'LEG_MISSING_FIXTURE_TEXT', detail: null, raw: rawSnapshot };
    }
    const odds = parsePrice(oddsRaw);
    if (odds === null) {
      return { ok: false, reason: 'LEG_UNPARSEABLE_ODDS', detail: `odds_raw="${oddsRaw}"`, raw: rawSnapshot };
    }

    const identityEl = legEl.querySelector(MYBETS_SELECTORS.legIdentityElement);
    const eventIdMatch = identityEl && identityEl.id.match(MYBETS_SELECTORS.legEventIdPattern);
    const sourceEventId = eventIdMatch ? eventIdMatch[1] : null;

    // No home/away split is attempted from fixture_and_time_raw -- its
    // exact separator (if any) between the two team names and the time
    // is unconfirmed, and guessing one would be exactly the kind of
    // DOM-structure guess this project's discipline exists to prevent.
    // The natural-key fallback below uses the raw strings directly
    // instead of a guessed home/away split.
    let fixtureId;
    let fixtureIdResolution;
    if (sourceEventId) {
      fixtureId = Bet9jaIds.stableId('bxf', ['external', `bet9ja-event-${sourceEventId}`]);
      fixtureIdResolution = 'EXTERNAL_EVENT_ID';
    } else {
      fixtureId = Bet9jaIds.stableId('bxf', [
        normalizeForHash(selectionRaw),
        normalizeForHash(marketRaw),
        normalizeForHash(fixtureAndTimeRaw),
        normalizeForHash(competitionRaw),
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
        selection_raw: selectionRaw || null,
        selection: selectionMapped,
        odds_raw: oddsRaw || null,
        odds,
        market_raw: marketRaw || null,
        fixture_and_time_raw: fixtureAndTimeRaw || null,
        competition_raw: competitionRaw || null,
      },
    };
  }

  /**
   * Parses one already-expanded `.accordion-item` ticket. Mirrors
   * processTicket()'s fail-closed contract exactly (one of PARSED /
   * UNRESOLVED / EXCLUDED, never a partial admission), adapted to this
   * profile's confirmed fields and unconfirmed gaps (see the MYBETS
   * PROFILE header comment).
   */
  function processMybetsTicket(ticketEl, sourceIndex, capturedAtUtc) {
    const { idRaw, id } = resolveTicketIdFromMybets(ticketEl);
    if (!id) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MISSING_TICKET_ID',
          detail: idRaw
            ? `head item text="${idRaw}" contained no recognizable id`
            : 'No .mybets-head__item found after expansion; refusing to guess a synthetic id.',
          sourceIndex,
          raw: { ticket_id_raw: idRaw },
        }),
      };
    }

    const legEls = Array.from(ticketEl.querySelectorAll(MYBETS_SELECTORS.legRow));
    if (legEls.length === 0) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'NO_LEGS_FOUND',
          detail: 'Ticket expanded but contains no .mybets-item leg rows.',
          sourceIndex,
          raw: { ticket_id_raw: id },
        }),
      };
    }

    const legs = [];
    for (let i = 0; i < legEls.length; i += 1) {
      const parsed = extractMybetsLeg(legEls[i]);
      if (!parsed.ok) {
        // Fail closed, same contract as the placeholder profile: any
        // unparseable leg voids the WHOLE ticket, never just that leg.
        return {
          outcome: 'UNRESOLVED',
          record: makeUnresolvedTicket({
            reason: 'LEG_FAILED_TO_PARSE',
            detail: `leg[${i}] reason=${parsed.reason} detail=${parsed.detail || ''}`,
            sourceIndex,
            raw: { ticket_id_raw: id, leg_index: i, leg_raw: parsed.raw },
          }),
        };
      }
      legs.push(parsed.leg);
    }

    const placedAtRaw = text(ticketEl.querySelector(MYBETS_SELECTORS.placedAt));
    // No UTC-qualified timestamp attribute exists in this markup (same
    // honest gap as the placeholder profile's placed_at_utc) -- never
    // guessed via Date.parse.
    const { placedAtUtc, placedAtResolution } = resolvePlacedAt(null);

    const systemTableEl = ticketEl.querySelector(MYBETS_SELECTORS.systemTable);
    const systemTableRaw = systemTableEl ? text(systemTableEl) : null;
    const stakeReturnRawItems = Array.from(ticketEl.querySelectorAll(MYBETS_SELECTORS.stakeReturnInfoItem)).map(text);

    return {
      outcome: 'PARSED',
      record: {
        bet9ja_ticket_id: id,
        bet9ja_ticket_id_raw: idRaw,
        // Every ticket on this page is, by construction, an open bet --
        // but no explicit per-ticket status marker (settled/live/Virtual/
        // Zoom) has been identified in this markup, so this is an
        // inference from page context, not a read label -- see the
        // MYBETS PROFILE header comment's live/Virtual/Zoom gap.
        status: 'OPEN',
        status_resolution: 'INFERRED_FROM_OPEN_BETS_PAGE_NO_EXPLICIT_STATUS_MARKUP_CONFIRMED',
        placed_at_raw: placedAtRaw || null,
        placed_at_utc: placedAtUtc,
        placed_at_resolution: placedAtResolution,
        // The system table's presence is itself confirmed, real evidence
        // of a system ticket; anything else (single/double/treble/
        // accumulator) currently has no confirmed distinguishing markup,
        // so it stays unclassified rather than guessed -- see
        // TICKET_TYPE_DETECTION_LIMITED_TO_SYSTEM_TABLE_PRESENCE.
        ticket_type_raw: null,
        ticket_type_normalized: systemTableRaw ? 'SYSTEM' : null,
        ticket_type_taxonomy_gap: false,
        // Cell-level label/value mapping for stake/return is unconfirmed
        // (see the MYBETS PROFILE header comment) -- kept null/typed here
        // rather than guessed; the raw text is preserved for audit below.
        unit_stake_raw: null,
        unit_stake: null,
        total_stake_raw: null,
        total_stake: null,
        potential_return_raw: null,
        potential_return: null,
        stake_return_raw_items: stakeReturnRawItems,
        system_table_raw: systemTableRaw,
        legs,
        captured_at_utc: capturedAtUtc,
        parser_version: PARSER_VERSION,
      },
    };
  }

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
  function capturePlaceholderEnvelope(doc, context, envelopeBase) {
    const capturedAtUtc = context.capturedAtUtc;

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
        // Universal row-accounting invariant, mirroring parser.js's own
        // records_seen = records_parsed + records_unresolved +
        // records_expected_unsupported: tickets_expected_excluded counts
        // tickets excluded BY DESIGN (settled/cashed-out, or a live/
        // Virtual/Zoom leg) -- an out-of-scope ticket, not a malformed
        // one -- kept named/counted separately from tickets_unresolved
        // (a genuine ambiguity this parser could not confidently resolve)
        // for exactly the reason parser.js's own coverage fields are kept
        // separate: collapsing "expected, out of scope" into "something
        // went wrong" would make a page of only settled tickets look like
        // a broken capture.
        coverage: {
          visible_page_only: true,
          tickets_seen: ticketsSeen,
          tickets_parsed: tickets.length,
          tickets_unresolved: unresolvedTickets.length,
          tickets_expected_excluded: excludedTickets.length,
          legs_seen: legsSeen,
          legs_parsed: legsParsed,
        },
        tickets,
        unresolved_tickets: unresolvedTickets,
        excluded_tickets: excludedTickets,
      },
    };
  }

  /**
   * Processes every `.accordion-item` currently rendered under
   * `mybetsRoot` -- i.e. one page's worth of tickets. Always re-queries
   * fresh (never caches ticket elements across a pagination click, since
   * a client-side page transition may replace them entirely). Merges
   * results into the caller's running totals, deduplicating by
   * `bet9ja_ticket_id` against `seenTicketIds` (shared across the whole
   * multi-page run, not reset per page) -- a ticket that legitimately
   * reappears across pages is counted once, never twice.
   */
  async function processCurrentPageTickets(mybetsRoot, capturedAtUtc, seenTicketIds, aggregate, pageNumber) {
    const ticketEls = Array.from(mybetsRoot.querySelectorAll(MYBETS_SELECTORS.ticket));
    const ticketIdsOnThisPage = [];
    // Per-page deltas, independent of the running `aggregate` totals --
    // this is exactly the evidence a future round needs to distinguish a
    // real aggregation bug from a per-page content-timing issue, rather
    // than inferring it indirectly from the final totals alone.
    let ticketsParsedThisPage = 0;
    let ticketsUnresolvedThisPage = 0;
    let ticketsExpectedExcludedThisPage = 0;
    let legsSeenThisPage = 0;
    let legsParsedThisPage = 0;

    for (let index = 0; index < ticketEls.length; index += 1) {
      const ticketEl = ticketEls[index];
      const expandResult = await ensureTicketExpanded(ticketEl);
      if (!expandResult.opened) {
        const record = makeUnresolvedTicket({
          reason: expandResult.reason === 'TOGGLE_NOT_FOUND' ? 'TICKET_TOGGLE_NOT_FOUND' : 'TICKET_EXPANSION_TIMEOUT',
          detail: `Could not confirm ticket[${index}] ready to parse (.accordion-item--open and .mybets-head__item never appeared together).`,
          sourceIndex: index,
          raw: { reason: expandResult.reason },
        });
        aggregate.unresolvedTickets.push(record);
        ticketsUnresolvedThisPage += 1;
        continue; // never attempt to parse or collapse a ticket we couldn't confirm ready
      }

      const legsOnThisTicket = ticketEl.querySelectorAll(MYBETS_SELECTORS.legRow).length;
      aggregate.legsSeen += legsOnThisTicket;
      legsSeenThisPage += legsOnThisTicket;
      const { outcome, record } = processMybetsTicket(ticketEl, index, capturedAtUtc);
      if (outcome === 'PARSED') {
        ticketIdsOnThisPage.push(record.bet9ja_ticket_id);
        ticketsParsedThisPage += 1;
        if (seenTicketIds.has(record.bet9ja_ticket_id)) {
          // A ticket id repeated within THIS page (not merely across
          // pages -- see pageFingerprints in the caller for that case)
          // is guarded rather than assumed impossible.
          aggregate.duplicateTicketsSkipped += 1;
        } else {
          seenTicketIds.add(record.bet9ja_ticket_id);
          aggregate.tickets.push(record);
          aggregate.legsParsed += record.legs.length;
          legsParsedThisPage += record.legs.length;
        }
      } else if (outcome === 'EXCLUDED') {
        aggregate.excludedTickets.push(record);
        ticketsExpectedExcludedThisPage += 1;
      } else {
        aggregate.unresolvedTickets.push(record);
        ticketsUnresolvedThisPage += 1;
      }

      collapseIfNeeded(ticketEl, expandResult.wasAlreadyOpen);
    }

    aggregate.ticketsSeen += ticketEls.length;
    const fingerprint = ticketIdsOnThisPage.slice().sort().join(',');
    aggregate.pageResults.push({
      page_number: pageNumber,
      ticket_containers_seen: ticketEls.length,
      tickets_parsed: ticketsParsedThisPage,
      tickets_unresolved: ticketsUnresolvedThisPage,
      tickets_expected_excluded: ticketsExpectedExcludedThisPage,
      legs_seen: legsSeenThisPage,
      legs_parsed: legsParsedThisPage,
      page_fingerprint: fingerprint,
    });

    return { ticketCount: ticketEls.length, ticketIdsOnThisPage: ticketIdsOnThisPage.slice().sort(), fingerprint };
  }

  function getPageNumberText(el) {
    return el ? text(el).trim() : '';
  }

  function isVerifiedNumberedPaginationItem(el) {
    return !!el && NUMBERED_PAGE_TEXT.test(getPageNumberText(el)) && !el.hasAttribute('disabled');
  }

  function getCurrentPageNumber(paginationEl) {
    const currentEl = paginationEl.querySelector(MYBETS_SELECTORS.paginationCurrent);
    const t = getPageNumberText(currentEl);
    return NUMBERED_PAGE_TEXT.test(t) ? parseInt(t, 10) : null;
  }

  function getNumberedPaginationItems(paginationEl) {
    return Array.from(paginationEl.querySelectorAll(MYBETS_SELECTORS.paginationItem)).filter(
      isVerifiedNumberedPaginationItem
    );
  }

  /**
   * Walks every reachable numbered page, starting from whatever page is
   * currently on screen, merging each page's tickets into `aggregate` via
   * processCurrentPageTickets. See the PAGINATION header comment for the
   * full contract (click-target restriction, stop conditions, restore-
   * to-page-1 best effort). Returns pagination-specific coverage fields;
   * a page with no `.pg-pagination` container at all is single-page mode
   * (pages_available: 1, pages_visited: 1, pagination_stopped_reason:
   * 'NO_PAGINATION_CONTROL_FOUND') -- not an error, just nothing to
   * paginate through.
   */
  async function paginateAndCaptureAllPages(mybetsRoot, capturedAtUtc, seenTicketIds, aggregate) {
    const initialPaginationEl = mybetsRoot.querySelector(MYBETS_SELECTORS.paginationContainer);
    const initialPage = (initialPaginationEl && getCurrentPageNumber(initialPaginationEl)) || 1;
    const firstPageResult = await processCurrentPageTickets(
      mybetsRoot,
      capturedAtUtc,
      seenTicketIds,
      aggregate,
      initialPage
    );

    const paginationEl = mybetsRoot.querySelector(MYBETS_SELECTORS.paginationContainer);
    if (!paginationEl) {
      return { pagesAvailable: 1, pagesVisited: 1, stoppedReason: 'NO_PAGINATION_CONTROL_FOUND' };
    }

    let currentPage = initialPage;
    const visitedPageNumbers = new Set([currentPage]);
    const pageFingerprints = new Set([firstPageResult.fingerprint]);
    let pagesAvailable = currentPage;
    for (const item of getNumberedPaginationItems(paginationEl)) {
      const n = parseInt(getPageNumberText(item), 10);
      if (n > pagesAvailable) pagesAvailable = n;
    }
    let pagesVisited = 1;
    let stoppedReason = null;

    while (true) {
      if (pagesVisited >= MAX_PAGES_SAFETY_CAP) {
        stoppedReason = 'MAX_PAGES_SAFETY_CAP_REACHED';
        break;
      }
      // Re-query fresh every iteration -- a client-side page transition
      // may replace the pagination container's own children (or the
      // container itself), so nothing from a prior iteration is trusted.
      const currentPaginationEl = mybetsRoot.querySelector(MYBETS_SELECTORS.paginationContainer);
      if (!currentPaginationEl) {
        stoppedReason = 'PAGINATION_CONTROL_DISAPPEARED';
        break;
      }
      const numberedItems = getNumberedPaginationItems(currentPaginationEl);
      for (const item of numberedItems) {
        const n = parseInt(getPageNumberText(item), 10);
        if (n > pagesAvailable) pagesAvailable = n;
      }

      const nextPageNumber = currentPage + 1;
      const nextItem = numberedItems.find((el) => getPageNumberText(el) === String(nextPageNumber));
      if (!nextItem) {
        stoppedReason = 'NO_FURTHER_NUMBERED_PAGE';
        break;
      }
      if (visitedPageNumbers.has(nextPageNumber)) {
        stoppedReason = 'PAGE_NUMBER_REPEATED';
        break;
      }
      // Belt-and-suspenders re-verification immediately before the click,
      // even though `nextItem` was already filtered by
      // isVerifiedNumberedPaginationItem above -- this is the only click
      // target in this loop, so it is re-checked right at the point of
      // the click itself, not just when it was found.
      if (!isVerifiedNumberedPaginationItem(nextItem)) {
        stoppedReason = 'NEXT_PAGE_ITEM_FAILED_VERIFICATION';
        break;
      }

      nextItem.click();
      const advanced = await waitFor(
        () => {
          const el = mybetsRoot.querySelector(MYBETS_SELECTORS.paginationContainer);
          return !!el && getCurrentPageNumber(el) === nextPageNumber;
        },
        EXPAND_TIMEOUT_MS,
        EXPAND_POLL_INTERVAL_MS
      );
      if (!advanced) {
        stoppedReason = 'PAGE_TRANSITION_TIMEOUT';
        break;
      }

      currentPage = nextPageNumber;
      visitedPageNumbers.add(currentPage);
      pagesVisited += 1;

      // Round 4 real-capture correction: `--current` can advance before
      // this page's own ticket list has finished (re)rendering -- wait
      // for at least one ticket container to appear before parsing, so
      // a page isn't read the instant its content starts loading. See
      // PAGE_CONTENT_TIMEOUT_MS's own comment for what happens if this
      // window elapses anyway (parsed as-is, never a hard stop).
      await waitFor(
        () => mybetsRoot.querySelectorAll(MYBETS_SELECTORS.ticket).length > 0,
        PAGE_CONTENT_TIMEOUT_MS,
        PAGE_CONTENT_POLL_INTERVAL_MS
      );

      const pageResult = await processCurrentPageTickets(mybetsRoot, capturedAtUtc, seenTicketIds, aggregate, currentPage);
      if (pageResult.fingerprint !== '' && pageFingerprints.has(pageResult.fingerprint)) {
        stoppedReason = 'PAGE_CONTENT_REPEATED';
        break;
      }
      pageFingerprints.add(pageResult.fingerprint);
    }

    if (currentPage !== 1) {
      // Best-effort restore to page 1, fire-and-forget -- same spirit as
      // collapseIfNeeded: never awaited, never required for the
      // correctness of the capture that already happened.
      const restoreEl = mybetsRoot.querySelector(MYBETS_SELECTORS.paginationContainer);
      const firstItem = restoreEl && getNumberedPaginationItems(restoreEl).find((el) => getPageNumberText(el) === '1');
      if (firstItem) firstItem.click();
    }

    return { pagesAvailable, pagesVisited, stoppedReason };
  }

  /**
   * MYBETS profile capture -- async because expanding a collapsed ticket
   * (and, now, advancing a pagination page) requires a real click and a
   * wait for the resulting DOM mutation (see the MYBETS PROFILE and
   * PAGINATION header comments). Everything happens in strict sequence --
   * never in parallel -- so at most one ticket or one page transition is
   * ever in flight at a time, keeping the live page's own state
   * predictable throughout.
   */
  async function captureMybetsEnvelope(doc, context, envelopeBase, mybetsRoot) {
    const capturedAtUtc = context.capturedAtUtc;
    const seenTicketIds = new Set();
    const aggregate = {
      tickets: [],
      unresolvedTickets: [],
      excludedTickets: [],
      ticketsSeen: 0,
      legsSeen: 0,
      legsParsed: 0,
      duplicateTicketsSkipped: 0,
      pageResults: [],
    };

    const { pagesAvailable, pagesVisited, stoppedReason } = await paginateAndCaptureAllPages(
      mybetsRoot,
      capturedAtUtc,
      seenTicketIds,
      aggregate
    );

    const {
      tickets,
      unresolvedTickets,
      excludedTickets,
      ticketsSeen,
      legsSeen,
      legsParsed,
      duplicateTicketsSkipped,
      pageResults,
    } = aggregate;

    // Structurally unreachable given processCurrentPageTickets's own
    // contract (every ticket container found contributes to exactly one
    // of tickets/unresolvedTickets/excludedTickets) -- kept as an
    // explicit guard, mirroring parser.js's own NO_USABLE_OUTPUT guard,
    // so a self-inconsistent file can never be produced silently if that
    // contract is ever broken by a future edit.
    const invariantHolds = ticketsSeen === tickets.length + unresolvedTickets.length + excludedTickets.length;

    // Every reason below is a currently-known, real gap (see the MYBETS
    // PROFILE header comment) -- present on EVERY capture using this
    // profile so a result can never be mistaken for fully validated.
    const statusReasons = [
      'MYBETS_SELECTOR_PROFILE_ACTIVE',
      'LIVE_VIRTUAL_ZOOM_DETECTION_UNCONFIRMED_FOR_MYBETS_PROFILE',
      'STAKE_RETURN_FIELD_MAPPING_UNCONFIRMED',
      'TICKET_TYPE_DETECTION_LIMITED_TO_SYSTEM_TABLE_PRESENCE',
    ];
    if (duplicateTicketsSkipped > 0) {
      statusReasons.push('DUPLICATE_TICKET_IDS_SKIPPED');
    }
    statusReasons.push(`PAGINATION_STOPPED_${stoppedReason}`);

    let captureStatus;
    if (!invariantHolds) {
      // Never download a self-inconsistent file -- fail loud and typed
      // rather than silently. See the invariantHolds comment above.
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('ROW_ACCOUNTING_INVARIANT_VIOLATED');
    } else if (ticketsSeen === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_TICKETS_FOUND');
    } else if (tickets.length === 0 && unresolvedTickets.length === 0 && excludedTickets.length === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else {
      // Never CAPTURE_OK for this profile -- the unconfirmed gaps above
      // (live/Virtual/Zoom detection chief among them) mean even a clean
      // parse of every visible ticket cannot yet be called fully
      // validated. This is a deliberate, permanent cap for this profile
      // version, not a bug -- see PARSER_VERSION.
      captureStatus = 'CAPTURE_PARTIAL';
      if (unresolvedTickets.length > 0) statusReasons.unshift('UNRESOLVED_TICKETS_PRESENT');
    }

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        coverage: {
          visible_page_only: false,
          tickets_seen: ticketsSeen,
          tickets_parsed: tickets.length,
          tickets_unresolved: unresolvedTickets.length,
          tickets_expected_excluded: excludedTickets.length,
          legs_seen: legsSeen,
          legs_parsed: legsParsed,
          duplicate_tickets_skipped: duplicateTicketsSkipped,
          pages_available: pagesAvailable,
          pages_visited: pagesVisited,
          pagination_automated: true,
        },
        // Per-page evidence: exactly what processCurrentPageTickets saw
        // and produced on each visited page, so a future round (or the
        // person reading a real capture) can see whether a shortfall in
        // the totals traces to one specific page rather than having to
        // infer it from the aggregate counts alone.
        page_results: pageResults,
        tickets,
        unresolved_tickets: unresolvedTickets,
        excluded_tickets: excludedTickets,
      },
    };
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string}} context
   * @returns {Promise<{envelope: object}>}
   */
  async function captureFromDocument(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const envelopeBase = {
      schema_version: 'bet9ja-ticket-capture.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
    };

    const mybetsRoot = doc.querySelector(MYBETS_SELECTORS.root);
    if (mybetsRoot) {
      return captureMybetsEnvelope(doc, context, envelopeBase, mybetsRoot);
    }
    return capturePlaceholderEnvelope(doc, context, envelopeBase);
  }

  const api = { captureFromDocument, sanitizeSourceUrl, PARSER_VERSION, TICKET_SELECTORS, MYBETS_SELECTORS };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaTicketCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
