/**
 * Bet9ja SETTLED-BETS-capture parser -- pure DOM-in, envelope-out logic
 * shared by the content script (real browser) and the Node test suite
 * (jsdom). Mirrors ticket_parser.js's architecture and fail-closed
 * contract; see that file's own header comment for the general pattern.
 *
 * Scope (see README.md "Capture settled bets"):
 *   - The button opens from Bet9ja My Bets, activates the Settled Bets
 *     tab, walks every genuine numbered results page, expands every
 *     ticket, and downloads one normalized JSON file.
 *   - This module captures Bet9ja's OWN recorded settlement. It does not
 *     search the web, does not recalculate a ticket's outcome from a
 *     displayed score, does not update any ledger, and does not change any
 *     model. `profit_loss` is always `null` here -- computing it from
 *     stake/payout/cashout with decimal-safe arithmetic is explicitly the
 *     ledger importer's job, not this capture's.
 *   - Read-only. Never places a bet, never requests a cashout, never
 *     interacts with the page beyond the click targets explicitly named
 *     below.
 *   - FAIL CLOSED on any settlement ambiguity: a ticket with a missing or
 *     unrecognized status, an unsafe leg assignment, or a malformed
 *     monetary field is routed to `unresolved_tickets` -- never guessed,
 *     never partially admitted. One unresolved ticket never blocks another
 *     ticket or page.
 *
 * REAL-DOM PROFILE -- confirmed via live authenticated inspection of the
 * Bet9ja Settled Bets view (Round 1, see SETTLED_BETS_REAL_PAGE_VALIDATION.md):
 *
 *   - Settled Bets tab: `.mybets__bets-item` elements, one with exact text
 *     "Settled Bets"; the currently-selected tab carries
 *     `.mybets__bets-item--current`. The ONLY click target this module
 *     ever uses to reach the settled view.
 *   - Tickets: `.mybets .accordion-item`, expanded via `.accordion-toggle`
 *     (adds `.accordion-item--open`) -- same mechanics as
 *     ticket_parser.js's MYBETS profile, reused verbatim (confirmed
 *     structurally identical for this view).
 *   - Ticket type text: `.accordion-text`. Ticket id: `.mybets-head__item`.
 *     Displayed date: `.mybets-date`. System details: `.mybets__systable`.
 *   - Legs: `.mybets-item` elements containing at least one
 *     `.mybets-item__row` are candidates (a structural `.mybets-item` with
 *     ZERO rows is excluded at candidacy, confirmed present in the
 *     inspected ticket -- mirrors ticket_parser.js's own 0-row exclusion).
 *     A candidate leg has 4 rows (or 3 when the competition row is
 *     genuinely absent, same acceptance as ticket_parser.js's open-bets
 *     profile):
 *       row 1: `.mybets-bet` (selection), `.mybets-odd` (odds)
 *       row 2: whole-row text (market)
 *       row 3: `.mybets-score` (final score), `.mybets-game` (fixture/
 *              time text), `.mybets__info` (explicit leg outcome --
 *              "Won"/"Lost" confirmed present on every real leg)
 *       row 4 (optional): `.mybets-game` (competition text)
 *   - Ticket summary: `.mybets-holder`'s `.mybets-holder__info-item`
 *     children -- the FIRST item is the stake, the SECOND is the overall
 *     ticket result, confirmed to appear as either the bare text "Lost"
 *     (no payout shown at all) or "Won <amount>" (e.g. "Won 123.45").
 *     `actual_payout` is therefore only ever populated for a WON ticket;
 *     a LOST ticket gets `actual_payout: null` with `payout_resolution:
 *     'NO_EXPLICIT_PAYOUT_DISPLAYED'` -- this module never manufactures a
 *     "0.00" payout that Bet9ja itself never displayed.
 *   - Pagination: `.mybets .pg-pagination__item` (191 numbered pages
 *     confirmed present in the inspected account), current page via
 *     `.pg-pagination__item--current`. First/Previous/Next/Last controls
 *     are confirmed present alongside the numbered items and are NEVER
 *     clicked -- only a verified `/^\d+$/`-text, non-disabled numbered
 *     item ever is (same restriction ticket_parser.js's own pagination
 *     walker uses). MAX_PAGES_SAFETY_CAP is set well above the confirmed
 *     191 to avoid an unrelated cap tripping on a real, larger account.
 *
 * NORMALIZED OUTCOMES -- this evidence-backed first version recognizes
 * only the vocabulary actually observed on the real page: leg outcome
 * text "Won"/"Lost" (case-insensitive, exact) and ticket summary text
 * "Lost" / "Won <amount>". Every other leg/ticket status enum value
 * (`VOID`, `PUSH`, `HALF_WON`, `HALF_LOST`, `CASHED_OUT`,
 * `PARTIAL_RETURN`) stays in the schema as a valid value for a downstream
 * consumer, but this parser never EMITS one yet -- anything not exactly
 * "Won"/"Lost" resolves to `UNRESOLVED`, never guessed. Widen this
 * recognition set only against new real evidence of that markup, exactly
 * per this project's evidence-only-correction discipline.
 *
 * MONETARY VALUES -- captured as validated DECIMAL STRINGS (e.g. "189.00"),
 * never lossy JS floats. See toDecimalString().
 *
 * SYSTEM TICKETS -- a system ticket may contain both `Won` and `Lost` legs
 * (confirmed: the inspected system ticket had both) while still producing
 * one overall ticket result. Three concepts stay separate and are never
 * collapsed into each other: (1) each leg's own `leg_status`, (2) winning/
 * losing COMBINATION counts (`system_settlement`, populated only when the
 * page explicitly provides them -- no such breakdown selector has been
 * confirmed yet, so this stays null-valued today), and (3) the ticket's
 * own `ticket_status`/`actual_payout`, read only from its own explicit
 * summary text, never derived from "did every leg win".
 *
 * LONG PAGINATION -- a real account can have 191+ pages. This module
 * supports: an optional `context.onProgress(info)` callback invoked after
 * every page (so the popup can render live progress), an optional
 * `context.shouldCancel()` predicate checked between pages (a user-
 * initiated stop, not a failure -- produces a `CAPTURE_PARTIAL` envelope
 * with everything captured so far, never discarded), and
 * `envelope.resume_metadata` recording the last page fully completed and
 * a plain-language hint for resuming a run that stopped early.
 *
 * SAFETY: the ONLY elements this module ever calls .click() on are: the
 * confirmed "Settled Bets" `.mybets__bets-item`, `.accordion-toggle`, and
 * a verified numeric `.pg-pagination__item`. Never Cashout, never Reload
 * Selections, never a betting selection, never an account control. See
 * the safety test in tests/settled_bets_parser.test.js, which greps this
 * file's own source.
 */
(function (root) {
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-settled-bets-parser@0.2.0-real-settled-profile-round1';

  // Confirmed via live authenticated inspection (Round 1). See the
  // REAL-DOM PROFILE header comment above for the full contract.
  const SELECTORS = {
    tabItem: '.mybets__bets-item',
    tabCurrentClass: 'mybets__bets-item--current',
    root: '.mybets',
    ticket: '.accordion-item',
    ticketOpenClass: 'accordion-item--open',
    toggle: '.accordion-toggle',
    ticketType: '.accordion-text',
    placedAt: '.mybets-date',
    ticketIdHeadItem: '.mybets-head__item',
    systemTable: '.mybets__systable',
    legRow: '.mybets-item',
    legDetailRow: '.mybets-item__row',
    legSelection: '.mybets-bet',
    legOdds: '.mybets-odd',
    legScore: '.mybets-score',
    legGame: '.mybets-game',
    legInfo: '.mybets__info',
    ticketHolder: '.mybets-holder',
    infoItem: '.mybets-holder__info-item',
    paginationContainer: '.pg-pagination',
    paginationItem: '.pg-pagination__item',
    paginationCurrent: '.pg-pagination__item--current',
  };

  const SETTLED_TAB_TEXT = 'Settled Bets';

  // ROUND 1: no confirmed selector for a system combination-won/lost/void
  // breakdown has been identified. Kept as an explicit, mutable placeholder
  // (mirrors soccer_walker.js's own pattern) rather than silently omitted,
  // so a future round can wire it in without a structural change here.
  const SYSTEM_SETTLEMENT_SELECTORS = {
    breakdown: null,
  };

  const NUMBERED_PAGE_TEXT = /^\d+$/;

  const EXPAND_TIMEOUT_MS = 3000;
  const EXPAND_POLL_INTERVAL_MS = 25;
  const PAGE_CONTENT_TIMEOUT_MS = 3000;
  const PAGE_CONTENT_POLL_INTERVAL_MS = 25;
  // Real evidence confirmed 191 pages in the inspected account; this cap
  // is kept well above that (not tightly at it) so a larger real account
  // is never truncated by an unrelated safety limit -- still a hard
  // backstop against a genuinely unbounded loop, never relied upon in the
  // normal case.
  const MAX_PAGES_SAFETY_CAP = 500;

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

  function normalizeForHash(value) {
    return (value || '').toLowerCase().replace(/\s+/g, ' ').trim();
  }

  /**
   * Validates and cleans a monetary string into a decimal STRING (never a
   * lossy float) -- strips thousands separators/currency noise, requires
   * the remainder to look like a plain decimal number, and returns null
   * (never a guess) for anything else.
   */
  function toDecimalString(rawText) {
    if (!rawText) return null;
    const cleaned = String(rawText).replace(/[^0-9.,-]/g, '').replace(/,/g, '').trim();
    if (!/^-?\d+(\.\d+)?$/.test(cleaned)) return null;
    return cleaned;
  }

  function extractLeadingDecimal(rawText) {
    if (!rawText) return null;
    const match = String(rawText).replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
    return match ? toDecimalString(match[0]) : null;
  }

  // See NORMALIZED OUTCOMES above -- exact "Won"/"Lost" text only, for
  // both legs and tickets. Anything else resolves to UNRESOLVED. VOID,
  // PUSH, HALF_WON, HALF_LOST, CASHED_OUT and PARTIAL_RETURN remain valid
  // schema values a downstream consumer may see from a future parser
  // version, but this version never emits them.
  function normalizeWonLost(rawText) {
    const t = (rawText || '').trim().toLowerCase();
    if (t === 'won') return { normalized: 'WON', resolution: 'EXPLICIT_BOOKMAKER_MARKUP' };
    if (t === 'lost') return { normalized: 'LOST', resolution: 'EXPLICIT_BOOKMAKER_MARKUP' };
    if (!t) return { normalized: 'UNRESOLVED', resolution: 'MISSING_STATUS_TEXT' };
    return { normalized: 'UNRESOLVED', resolution: 'UNRECOGNIZED_STATUS_TEXT' };
  }

  function isCandidateMybetsLeg(el) {
    return el.querySelectorAll(`:scope > ${SELECTORS.legDetailRow}`).length > 0;
  }

  function resolveTicketIdFromMybets(ticketEl) {
    const items = Array.from(ticketEl.querySelectorAll(SELECTORS.ticketIdHeadItem));
    if (items.length === 0) return { idRaw: null, id: null };
    for (const el of items) {
      const t = text(el);
      const match = t.match(/(\d{6,})/);
      if (match) return { idRaw: t, id: match[1] };
    }
    const firstRaw = text(items[0]);
    return { idRaw: firstRaw, id: firstRaw || null };
  }

  async function ensureTicketExpanded(ticketEl) {
    const isReadyToParse = () =>
      ticketEl.classList.contains(SELECTORS.ticketOpenClass) && !!ticketEl.querySelector(SELECTORS.ticketIdHeadItem);

    const wasAlreadyOpen = ticketEl.classList.contains(SELECTORS.ticketOpenClass);
    if (!wasAlreadyOpen) {
      const toggle = ticketEl.querySelector(SELECTORS.toggle);
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

  function collapseIfNeeded(ticketEl, wasAlreadyOpen) {
    if (wasAlreadyOpen) return;
    const toggle = ticketEl.querySelector(SELECTORS.toggle);
    if (toggle) toggle.click();
  }

  /**
   * Parses one settled leg from an already-expanded `.mybets-item`. See
   * the REAL-DOM PROFILE header comment for the exact confirmed row
   * layout. Never partially admits a leg -- any structural surprise voids
   * the WHOLE ticket via the caller's fail-closed contract.
   */
  function extractSettledLeg(legEl) {
    const rows = Array.from(legEl.querySelectorAll(`:scope > ${SELECTORS.legDetailRow}`));
    let selectionRow;
    let marketRow;
    let detailRow;
    let competitionRow = null;
    let competitionResolution;
    if (rows.length === 4) {
      [selectionRow, marketRow, detailRow, competitionRow] = rows;
      competitionResolution = 'PRESENT';
    } else if (rows.length === 3) {
      [selectionRow, marketRow, detailRow] = rows;
      competitionResolution = 'COMPETITION_UNAVAILABLE';
    } else {
      return {
        ok: false,
        reason: 'LEG_UNEXPECTED_ROW_COUNT',
        detail: `expected 4 .mybets-item__row children or 3 (competition omitted), found ${rows.length}`,
        raw: {
          row_count: rows.length,
          row_texts: rows.map((row) => text(row)),
          leg_element_text: rows.length === 0 ? text(legEl) : null,
        },
      };
    }

    const selectionRaw = text(selectionRow.querySelector(SELECTORS.legSelection));
    const oddsRaw = text(selectionRow.querySelector(SELECTORS.legOdds));
    const marketRaw = text(marketRow);
    const scoreRaw = text(detailRow.querySelector(SELECTORS.legScore));
    const fixtureAndTimeRaw = text(detailRow.querySelector(SELECTORS.legGame));
    const legOutcomeRaw = text(detailRow.querySelector(SELECTORS.legInfo));
    const competitionGameEl = competitionRow ? competitionRow.querySelector(SELECTORS.legGame) : null;
    const competitionRaw = competitionRow ? text(competitionGameEl || competitionRow) : null;

    const rawSnapshot = {
      selection_raw: selectionRaw,
      odds_raw: oddsRaw,
      market_raw: marketRaw,
      score_raw: scoreRaw,
      fixture_and_time_raw: fixtureAndTimeRaw,
      leg_outcome_raw: legOutcomeRaw,
      competition_raw: competitionRaw,
    };

    if (!selectionRaw) {
      return { ok: false, reason: 'LEG_MISSING_SELECTION', detail: null, raw: rawSnapshot };
    }
    if (!fixtureAndTimeRaw) {
      return { ok: false, reason: 'LEG_MISSING_FIXTURE_TEXT', detail: null, raw: rawSnapshot };
    }
    const odds = toDecimalString(oddsRaw);
    if (odds === null) {
      return { ok: false, reason: 'LEG_UNPARSEABLE_ODDS', detail: `odds_raw="${oddsRaw}"`, raw: rawSnapshot };
    }

    const fixtureId = Bet9jaIds.stableId('bxf', [
      normalizeForHash(selectionRaw),
      normalizeForHash(marketRaw),
      normalizeForHash(fixtureAndTimeRaw),
      normalizeForHash(competitionRaw),
    ]);

    const { normalized: legStatus, resolution: legStatusResolution } = normalizeWonLost(legOutcomeRaw);

    return {
      ok: true,
      leg: {
        fixture_id: fixtureId,
        source_event_id: null,
        selection: selectionRaw.trim() || null,
        selection_raw: selectionRaw || null,
        odds,
        odds_raw: oddsRaw || null,
        market_raw: marketRaw || null,
        fixture_and_time_raw: fixtureAndTimeRaw || null,
        competition_raw: competitionRaw,
        competition_resolution: competitionResolution,
        // No separate market-specific result text is confirmed distinct
        // from the overall score -- market_result_raw mirrors result_raw
        // until real evidence of a different, market-specific value
        // surfaces (e.g. a Totals leg showing a goal count rather than a
        // final score).
        result_raw: scoreRaw || null,
        market_result_raw: scoreRaw || null,
        leg_status: legStatus,
        leg_status_raw: legOutcomeRaw || null,
        settlement_resolution: legStatusResolution,
      },
    };
  }

  /**
   * Parses the confirmed two-item ticket summary: `.mybets-holder`'s
   * FIRST `.mybets-holder__info-item` is the stake, the SECOND is the
   * overall result -- either "Lost" (no payout shown) or "Won <amount>".
   * See the REAL-DOM PROFILE header comment.
   */
  function parseTicketSummary(ticketEl) {
    const items = Array.from(ticketEl.querySelectorAll(SELECTORS.infoItem)).map(text);
    const stakeRaw = items.length > 0 ? items[0] : null;
    const resultRaw = items.length > 1 ? items[1] : null;
    const totalStakeRaw = extractLeadingDecimal(stakeRaw);

    const resultTrimmed = (resultRaw || '').trim();
    if (/^lost$/i.test(resultTrimmed)) {
      return {
        totalStakeRaw,
        ticketStatus: 'LOST',
        ticketStatusRaw: resultTrimmed,
        settlementResolution: 'EXPLICIT_BOOKMAKER_STATUS',
        actualPayoutRaw: null,
        payoutResolution: 'NO_EXPLICIT_PAYOUT_DISPLAYED',
      };
    }
    const wonMatch = resultTrimmed.match(/^won\s*([\d.,]+)?$/i);
    if (wonMatch) {
      const amountRaw = wonMatch[1] || null;
      return {
        totalStakeRaw,
        ticketStatus: 'WON',
        ticketStatusRaw: resultTrimmed,
        settlementResolution: 'EXPLICIT_BOOKMAKER_STATUS',
        actualPayoutRaw: amountRaw,
        payoutResolution: amountRaw ? 'EXPLICIT_BOOKMAKER_AMOUNT' : 'NO_EXPLICIT_PAYOUT_DISPLAYED',
      };
    }
    return {
      totalStakeRaw,
      ticketStatus: 'UNRESOLVED',
      ticketStatusRaw: resultTrimmed || null,
      settlementResolution: resultTrimmed ? 'UNRECOGNIZED_STATUS_TEXT' : 'MISSING_STATUS_TEXT',
      actualPayoutRaw: null,
      payoutResolution: null,
    };
  }

  /**
   * Parses one already-expanded settled ticket. Exactly one of PARSED /
   * UNRESOLVED / EXCLUDED, per this file's fail-closed contract.
   */
  function processSettledTicket(ticketEl, sourceIndex, capturedAtUtc) {
    const { idRaw, id } = resolveTicketIdFromMybets(ticketEl);
    if (!id) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MISSING_TICKET_ID',
          detail: idRaw ? `head item text="${idRaw}" contained no recognizable id` : 'No .mybets-head__item found after expansion.',
          sourceIndex,
          raw: { ticket_id_raw: idRaw },
        }),
      };
    }

    const summary = parseTicketSummary(ticketEl);
    if (summary.ticketStatus === 'UNRESOLVED') {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MISSING_OR_UNRECOGNIZED_SETTLEMENT_STATUS',
          detail: `status_raw="${summary.ticketStatusRaw}"`,
          sourceIndex,
          raw: { ticket_id_raw: id, ticket_status_raw: summary.ticketStatusRaw },
        }),
      };
    }

    const legEls = Array.from(ticketEl.querySelectorAll(SELECTORS.legRow)).filter(isCandidateMybetsLeg);
    if (legEls.length === 0) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'NO_LEGS_FOUND',
          detail: 'Ticket expanded but contains no .mybets-item leg rows with any row content.',
          sourceIndex,
          raw: { ticket_id_raw: id },
        }),
      };
    }

    const legs = [];
    for (let i = 0; i < legEls.length; i += 1) {
      const parsed = extractSettledLeg(legEls[i]);
      if (!parsed.ok) {
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

    const totalStake = summary.totalStakeRaw ? toDecimalString(summary.totalStakeRaw) : null;
    if (summary.totalStakeRaw && totalStake === null) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MALFORMED_STAKE_OR_PAYOUT_TEXT',
          detail: `total_stake_raw="${summary.totalStakeRaw}"`,
          sourceIndex,
          raw: { ticket_id_raw: id },
        }),
      };
    }
    const actualPayout = summary.actualPayoutRaw ? toDecimalString(summary.actualPayoutRaw) : null;
    if (summary.actualPayoutRaw && actualPayout === null) {
      return {
        outcome: 'UNRESOLVED',
        record: makeUnresolvedTicket({
          reason: 'MALFORMED_STAKE_OR_PAYOUT_TEXT',
          detail: `actual_payout_raw="${summary.actualPayoutRaw}"`,
          sourceIndex,
          raw: { ticket_id_raw: id },
        }),
      };
    }

    const placedAtRaw = text(ticketEl.querySelector(SELECTORS.placedAt));
    const ticketTypeRaw = text(ticketEl.querySelector(SELECTORS.ticketType));
    const systemTableEl = ticketEl.querySelector(SELECTORS.systemTable);
    const systemTableRaw = systemTableEl ? text(systemTableEl) : null;

    // ROUND 1: no confirmed combination-breakdown selector exists yet.
    // Only ever populated when the page explicitly provides it -- never
    // inferred from "how many legs won". See SYSTEM TICKETS above.
    const breakdownEl = SYSTEM_SETTLEMENT_SELECTORS.breakdown
      ? ticketEl.querySelector(SYSTEM_SETTLEMENT_SELECTORS.breakdown)
      : null;
    const systemSettlement = {
      combinations_total: null,
      combinations_won: null,
      combinations_lost: null,
      combinations_void: null,
      raw: breakdownEl ? text(breakdownEl) : null,
    };

    return {
      outcome: 'PARSED',
      record: {
        bet9ja_ticket_id: id,
        bet9ja_ticket_id_raw: idRaw,
        ticket_type_raw: ticketTypeRaw || null,
        ticket_type_normalized: systemTableRaw ? 'SYSTEM' : null,
        placed_at_raw: placedAtRaw || null,
        settled_at_raw: null,
        settled_at_utc: null,
        total_stake: totalStake,
        total_stake_raw: summary.totalStakeRaw,
        unit_stake: null,
        unit_stake_raw: null,
        potential_return: null,
        potential_return_raw: null,
        actual_payout: actualPayout,
        actual_payout_raw: summary.actualPayoutRaw,
        payout_resolution: summary.payoutResolution,
        cashout_amount: null,
        cashout_amount_raw: null,
        ticket_status: summary.ticketStatus,
        ticket_status_raw: summary.ticketStatusRaw,
        settlement_resolution: summary.settlementResolution,
        profit_loss: null,
        system_settlement: systemSettlement,
        system_table_raw: systemTableRaw,
        legs,
        captured_at_utc: capturedAtUtc,
        parser_version: PARSER_VERSION,
      },
    };
  }

  function makeUnresolvedTicket({ reason, detail, sourceIndex, raw }) {
    return { reason, detail: detail || null, source_index: sourceIndex, raw };
  }

  function makeExcludedTicket({ reason, detail, sourceIndex, ticketIdRaw }) {
    return { reason, detail: detail || null, source_index: sourceIndex, ticket_id_raw: ticketIdRaw || null };
  }

  /**
   * Processes every `.accordion-item` currently rendered under
   * `mybetsRoot`. Mirrors ticket_parser.js's processCurrentPageTickets,
   * adding CONFLICTING_DUPLICATE_ID: a ticket id repeating (within or
   * across pages) with content that differs from the first-seen version
   * is fail-closed, per the explicit rule "two tickets expose the same ID
   * with conflicting content".
   */
  async function processCurrentPageTickets(mybetsRoot, capturedAtUtc, seenTickets, aggregate, pageNumber) {
    const ticketEls = Array.from(mybetsRoot.querySelectorAll(SELECTORS.ticket));
    const ticketIdsOnThisPage = [];
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
          detail: `Could not confirm ticket[${index}] ready to parse.`,
          sourceIndex: index,
          raw: { reason: expandResult.reason },
        });
        aggregate.unresolvedTickets.push(record);
        ticketsUnresolvedThisPage += 1;
        continue;
      }

      const legsOnThisTicket = Array.from(ticketEl.querySelectorAll(SELECTORS.legRow)).filter(isCandidateMybetsLeg).length;
      aggregate.legsSeen += legsOnThisTicket;
      legsSeenThisPage += legsOnThisTicket;

      const { outcome, record } = processSettledTicket(ticketEl, index, capturedAtUtc);
      if (outcome === 'PARSED') {
        ticketIdsOnThisPage.push(record.bet9ja_ticket_id);
        ticketsParsedThisPage += 1;
        const contentFingerprint = JSON.stringify(record.legs) + record.ticket_status + record.total_stake + record.actual_payout;
        const previouslySeen = seenTickets.get(record.bet9ja_ticket_id);
        if (previouslySeen === undefined) {
          seenTickets.set(record.bet9ja_ticket_id, contentFingerprint);
          aggregate.tickets.push(record);
          aggregate.legsParsed += record.legs.length;
          legsParsedThisPage += record.legs.length;
        } else if (previouslySeen === contentFingerprint) {
          aggregate.duplicateTicketsSkipped += 1;
        } else {
          aggregate.unresolvedTickets.push(
            makeUnresolvedTicket({
              reason: 'CONFLICTING_DUPLICATE_ID',
              detail: `bet9ja_ticket_id="${record.bet9ja_ticket_id}" reappeared with different content.`,
              sourceIndex: index,
              raw: { ticket_id_raw: record.bet9ja_ticket_id },
            })
          );
          ticketsUnresolvedThisPage += 1;
          ticketsParsedThisPage -= 1;
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
    const pageResult = {
      page_number: pageNumber,
      ticket_containers_seen: ticketEls.length,
      tickets_parsed: ticketsParsedThisPage,
      tickets_unresolved: ticketsUnresolvedThisPage,
      tickets_expected_excluded: ticketsExpectedExcludedThisPage,
      legs_seen: legsSeenThisPage,
      legs_parsed: legsParsedThisPage,
      page_fingerprint: fingerprint,
    };
    aggregate.pageResults.push(pageResult);

    return { ticketCount: ticketEls.length, fingerprint, pageResult };
  }

  function getPageNumberText(el) {
    return el ? text(el).trim() : '';
  }

  function isVerifiedNumberedPaginationItem(el) {
    return !!el && NUMBERED_PAGE_TEXT.test(getPageNumberText(el)) && !el.hasAttribute('disabled');
  }

  function getCurrentPageNumber(paginationEl) {
    const currentEl = paginationEl.querySelector(SELECTORS.paginationCurrent);
    const t = getPageNumberText(currentEl);
    return NUMBERED_PAGE_TEXT.test(t) ? parseInt(t, 10) : null;
  }

  function getNumberedPaginationItems(paginationEl) {
    return Array.from(paginationEl.querySelectorAll(SELECTORS.paginationItem)).filter(isVerifiedNumberedPaginationItem);
  }

  /**
   * Walks every reachable numbered page. Mirrors ticket_parser.js's
   * paginateAndCaptureAllPages, plus: `context.onProgress` (invoked after
   * every page so a long, 191-page-scale run can show live progress in
   * the popup) and `context.shouldCancel` (checked between pages -- a
   * user-initiated stop, reported via stoppedReason 'USER_CANCELLED',
   * never discards what was already captured).
   */
  async function paginateAndCaptureAllPages(mybetsRoot, capturedAtUtc, seenTickets, aggregate, context) {
    const onProgress = typeof context.onProgress === 'function' ? context.onProgress : null;
    const shouldCancel = typeof context.shouldCancel === 'function' ? context.shouldCancel : () => false;

    const initialPaginationEl = mybetsRoot.querySelector(SELECTORS.paginationContainer);
    const initialPage = (initialPaginationEl && getCurrentPageNumber(initialPaginationEl)) || 1;
    const firstPageResult = await processCurrentPageTickets(mybetsRoot, capturedAtUtc, seenTickets, aggregate, initialPage);

    const paginationEl = mybetsRoot.querySelector(SELECTORS.paginationContainer);
    let pagesAvailable = initialPage;
    if (paginationEl) {
      for (const item of getNumberedPaginationItems(paginationEl)) {
        const n = parseInt(getPageNumberText(item), 10);
        if (n > pagesAvailable) pagesAvailable = n;
      }
    }
    if (onProgress) {
      onProgress({ pageNumber: initialPage, pagesVisited: 1, pagesAvailable, pageResult: firstPageResult.pageResult });
    }
    if (!paginationEl) {
      return { pagesAvailable: 1, pagesVisited: 1, lastPageCompleted: initialPage, stoppedReason: 'NO_PAGINATION_CONTROL_FOUND' };
    }

    let currentPage = initialPage;
    const visitedPageNumbers = new Set([currentPage]);
    const pageFingerprints = new Set([firstPageResult.fingerprint]);
    let pagesVisited = 1;
    let stoppedReason = null;

    while (true) {
      if (shouldCancel()) {
        stoppedReason = 'USER_CANCELLED';
        break;
      }
      if (pagesVisited >= MAX_PAGES_SAFETY_CAP) {
        stoppedReason = 'MAX_PAGES_SAFETY_CAP_REACHED';
        break;
      }
      const currentPaginationEl = mybetsRoot.querySelector(SELECTORS.paginationContainer);
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
      if (!isVerifiedNumberedPaginationItem(nextItem)) {
        stoppedReason = 'NEXT_PAGE_ITEM_FAILED_VERIFICATION';
        break;
      }

      nextItem.click();
      const advanced = await waitFor(
        () => {
          const el = mybetsRoot.querySelector(SELECTORS.paginationContainer);
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

      await waitFor(
        () => mybetsRoot.querySelectorAll(SELECTORS.ticket).length > 0,
        PAGE_CONTENT_TIMEOUT_MS,
        PAGE_CONTENT_POLL_INTERVAL_MS
      );

      const pageResult = await processCurrentPageTickets(mybetsRoot, capturedAtUtc, seenTickets, aggregate, currentPage);
      if (onProgress) {
        onProgress({ pageNumber: currentPage, pagesVisited, pagesAvailable, pageResult: pageResult.pageResult });
      }
      if (pageResult.fingerprint !== '' && pageFingerprints.has(pageResult.fingerprint)) {
        stoppedReason = 'PAGE_CONTENT_REPEATED';
        break;
      }
      pageFingerprints.add(pageResult.fingerprint);
    }

    if (currentPage !== 1) {
      const restoreEl = mybetsRoot.querySelector(SELECTORS.paginationContainer);
      const firstItem = restoreEl && getNumberedPaginationItems(restoreEl).find((el) => getPageNumberText(el) === '1');
      if (firstItem) firstItem.click();
    }

    return { pagesAvailable, pagesVisited, lastPageCompleted: currentPage, stoppedReason };
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

  function findSettledTabElement(doc) {
    return Array.from(doc.querySelectorAll(SELECTORS.tabItem)).find((el) => text(el) === SETTLED_TAB_TEXT) || null;
  }

  /**
   * Activates the Settled Bets tab. The ONLY click target this function
   * ever uses is the confirmed `.mybets__bets-item` whose text is exactly
   * "Settled Bets" -- never a guessed control.
   */
  async function activateSettledView(doc) {
    const tabEl = findSettledTabElement(doc);
    if (!tabEl) {
      return { activated: false, reason: 'SETTLED_TAB_CONTROL_NOT_FOUND' };
    }
    const isCurrent = () => tabEl.classList.contains(SELECTORS.tabCurrentClass);
    if (!isCurrent()) {
      tabEl.click();
      const activated = await waitFor(isCurrent, EXPAND_TIMEOUT_MS, EXPAND_POLL_INTERVAL_MS);
      if (!activated) {
        return { activated: false, reason: 'SETTLED_TAB_DID_NOT_ACTIVATE' };
      }
    }
    return { activated: true, reason: null };
  }

  function emptyCoverage() {
    return {
      pages_available: 0,
      pages_visited: 0,
      tickets_seen: 0,
      tickets_parsed: 0,
      tickets_unresolved: 0,
      tickets_expected_excluded: 0,
      duplicate_tickets_skipped: 0,
      legs_seen: 0,
      legs_parsed: 0,
    };
  }

  function failedEnvelope(envelopeBase, reasons) {
    return {
      envelope: {
        ...envelopeBase,
        capture_status: 'CAPTURE_FAILED',
        capture_status_reasons: reasons,
        coverage: emptyCoverage(),
        page_results: [],
        tickets: [],
        unresolved_tickets: [],
        excluded_tickets: [],
        resume_metadata: null,
      },
    };
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string,
   *          onProgress?: Function, shouldCancel?: Function}} context
   * @returns {Promise<{envelope: object}>}
   */
  async function captureFromDocument(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const envelopeBase = {
      schema_version: 'bet9ja-settled-bets.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
    };

    const normalizedSourceUrl = (context.sourceUrl || '').toLowerCase();
    if (!normalizedSourceUrl.includes('/mybets')) {
      return failedEnvelope(envelopeBase, ['NOT_ON_MYBETS_PAGE']);
    }

    const { activated, reason: activationFailureReason } = await activateSettledView(doc);
    if (!activated) {
      return failedEnvelope(envelopeBase, [activationFailureReason]);
    }

    const mybetsRoot = doc.querySelector(SELECTORS.root);
    if (!mybetsRoot) {
      return failedEnvelope(envelopeBase, ['MYBETS_ROOT_NOT_FOUND']);
    }

    const seenTickets = new Map();
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

    const { pagesAvailable, pagesVisited, lastPageCompleted, stoppedReason } = await paginateAndCaptureAllPages(
      mybetsRoot,
      capturedAtUtc,
      seenTickets,
      aggregate,
      context
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

    const invariantHolds = ticketsSeen === tickets.length + unresolvedTickets.length + excludedTickets.length;

    const statusReasons = [
      'SYSTEM_SETTLEMENT_BREAKDOWN_SELECTOR_UNVERIFIED',
      'ONLY_WON_LOST_OUTCOMES_RECOGNIZED_SO_FAR',
    ];
    if (duplicateTicketsSkipped > 0) statusReasons.push('DUPLICATE_TICKET_IDS_SKIPPED');
    statusReasons.push(`PAGINATION_STOPPED_${stoppedReason}`);

    let captureStatus;
    if (!invariantHolds) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('ROW_ACCOUNTING_INVARIANT_VIOLATED');
    } else if (ticketsSeen === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_TICKETS_FOUND');
    } else if (tickets.length === 0 && unresolvedTickets.length === 0 && excludedTickets.length === 0) {
      captureStatus = 'CAPTURE_FAILED';
      statusReasons.unshift('NO_USABLE_OUTPUT');
    } else {
      // Permanent cap for this profile version -- the system-settlement
      // combination breakdown selector remains unconfirmed and only
      // Won/Lost outcomes are recognized so far (see header comment), so
      // even a clean, fully-paginated parse cannot yet be called fully
      // validated. Never CAPTURE_OK for this version.
      captureStatus = 'CAPTURE_PARTIAL';
      if (unresolvedTickets.length > 0) statusReasons.unshift('UNRESOLVED_TICKETS_PRESENT');
      if (stoppedReason === 'USER_CANCELLED') statusReasons.unshift('CANCELLED_BEFORE_ALL_PAGES_VISITED');
      else if (pagesVisited < pagesAvailable) statusReasons.unshift('STOPPED_BEFORE_ALL_KNOWN_PAGES_VISITED');
    }

    const canResume = pagesVisited < pagesAvailable || stoppedReason === 'USER_CANCELLED';
    const resumeMetadata = {
      last_page_completed: lastPageCompleted,
      pages_available: pagesAvailable,
      pages_visited: pagesVisited,
      can_resume: canResume,
      resume_hint: canResume
        ? `Navigate to page ${lastPageCompleted + 1} of Settled Bets and run this capture again; already-captured ticket IDs are deduplicated automatically.`
        : null,
    };

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        coverage: {
          pages_available: pagesAvailable,
          pages_visited: pagesVisited,
          tickets_seen: ticketsSeen,
          tickets_parsed: tickets.length,
          tickets_unresolved: unresolvedTickets.length,
          tickets_expected_excluded: excludedTickets.length,
          duplicate_tickets_skipped: duplicateTicketsSkipped,
          legs_seen: legsSeen,
          legs_parsed: legsParsed,
        },
        page_results: pageResults,
        tickets,
        unresolved_tickets: unresolvedTickets,
        excluded_tickets: excludedTickets,
        resume_metadata: resumeMetadata,
      },
    };
  }

  const api = {
    captureFromDocument,
    sanitizeSourceUrl,
    PARSER_VERSION,
    SELECTORS,
    SYSTEM_SETTLEMENT_SELECTORS,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSettledBetsCapture = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
