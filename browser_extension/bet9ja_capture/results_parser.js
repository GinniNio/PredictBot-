/**
 * Bet9ja RESULTS-PAGE-capture parser -- pure DOM-in, envelope-out logic
 * shared by the content script (real browser) and the Node test suite
 * (jsdom). Mirrors settled_bets_parser.js's architecture and fail-closed
 * contract; see that file's own header comment for the general pattern.
 *
 * Scope: https://web.bet9ja.com/sport/results.aspx, opened with the
 * operator's own Event Date/End Date/Sport/Competition filters already
 * applied. This module reads the ALREADY-RENDERED results table for
 * whichever competition group(s) the page shows -- it never selects a
 * filter, never navigates, never submits a form. One click, one
 * synchronous parse, one downloaded envelope (no pagination -- the
 * results table renders its full group in one page, unlike Settled Bets'
 * 190+ page walk).
 *
 * Read-only. Never places a bet, never clicks anything on this page --
 * this module only ever queries and reads text/attributes.
 *
 * REAL-DOM PROFILE -- confirmed via live inspection of the rendered
 * Bet9ja Results page (Italy Serie A, 14/09/2026, 3 real completed
 * fixtures; see tests/fixtures/bet9ja/bet9ja-results-serie-a-2026-09-14-rendered-table.html):
 *
 *   - One `table.RisultatiGruppiContainer` per competition group. Its
 *     FIRST row is a single `td.RisultatiGruppiStyle` cell naming the
 *     group ("Italy Serie A") -- confirmed text, never re-derived from
 *     the URL/filter controls (which this module never reads at all --
 *     see DATE RANGE below).
 *   - Nested inside that group: one `table.RisultatiTbl`, whose header
 *     row is `tr.RisultatiHeader` (ID/Start/Event/Result -- never a data
 *     row) and whose data rows alternate `tr.RisultatiItem` /
 *     `tr.RisultatiAltItem` (confirmed zebra striping only -- both
 *     classes carry identical structure and are treated identically).
 *   - Per data row: `td.RisultatiIDSottoEvento` (Bet9ja's own numeric
 *     result id, e.g. "2592"), `td.RisultatiData` ("DD/MM/YYYY HH:MM",
 *     the page's own displayed local start time), `td.RisultatiSottoEvento`
 *     ("Home - Away", confirmed single " - " separator, same convention
 *     ticket_parser.js's own fixture-text split already uses), and
 *     `td.RisultatiRisultato` containing a NESTED table of score rows.
 *   - Each score row inside `td.RisultatiRisultato`: a `td.RisultatiTipo`
 *     cell holding one `div[title="..."]` labelling the row ("Fin. Ris"
 *     for full-time, "HT Res." for half-time -- both confirmed, exact,
 *     case-sensitive), and a sibling `td.Risultato` cell holding one
 *     `div` with the score text ("N - N"). A `<tr><td colspan="2">
 *     <div class="RisultatiSep"></div></td></tr>` separator row between
 *     them carries no data and is simply skipped (matched by neither
 *     selector below).
 *
 * NOT YET CONFIRMED -- fail closed, never guessed:
 *   - Postponed/abandoned/void/not-yet-played row layout. Only a row
 *     whose "Fin. Ris" score matches a plain `N - N` digit pattern is
 *     ever accepted; anything else (missing title, blank score, a status
 *     word instead of a score) is routed to `unresolved_results` with a
 *     typed reason -- never partially admitted, never guessed at what an
 *     unusual layout might mean.
 *   - The page's own Event Date/End Date/Sport/Competition filter
 *     CONTROLS (as opposed to the results table's own rendered content,
 *     which this module reads directly and reliably). `date_range` and
 *     `competition_filter` are therefore recorded as `null` here, exactly
 *     like settled_bets_parser.js's own `DATE_RANGE_DISPLAY_SELECTOR_
 *     UNVERIFIED` precedent -- the operator-supplied filenames/session
 *     notes remain the record of what was actually queried until a real
 *     selector for these controls is confirmed against live evidence.
 *   - The page's own displayed timezone is NOT read from the DOM (no
 *     confirmed selector exposes it) -- `page_timezone` is the fixed,
 *     externally-confirmed constant `"GMT+01:00"` (see this module's own
 *     `PAGE_TIMEZONE` below), the same treatment `bet9ja_ticket_import.py`'s
 *     `LAGOS_TZ` already gives Bet9ja's placement-time display: a known,
 *     confirmed constant, never inferred from page text that isn't there.
 */
(function (root) {
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const PARSER_VERSION = 'bet9ja-results-parser@0.1.0-serie-a-confirmed';

  // Bet9ja's own confirmed, fixed display offset for this page -- see this
  // file's own header comment. Never DST-adjusted; if that is ever
  // observed to be wrong, this constant must be revisited against that
  // new evidence, not guessed ahead of it.
  const PAGE_TIMEZONE = 'GMT+01:00';

  const SELECTORS = {
    groupContainer: 'table.RisultatiGruppiContainer',
    groupNameCell: 'td.RisultatiGruppiStyle',
    resultsTable: 'table.RisultatiTbl',
    dataRow: 'tr.RisultatiItem, tr.RisultatiAltItem',
    idCell: 'td.RisultatiIDSottoEvento',
    startCell: 'td.RisultatiData',
    fixtureCell: 'td.RisultatiSottoEvento',
    resultCell: 'td.RisultatiRisultato',
    scoreTypeLabel: 'td.RisultatiTipo div[title]',
    scoreValue: 'td.Risultato > div',
  };

  const FULL_TIME_LABEL = 'Fin. Ris';
  const HALF_TIME_LABEL = 'HT Res.';

  function textOf(el) {
    return el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
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
   * Reads the two score-label rows (`Fin. Ris` / `HT Res.`) inside one
   * row's own `td.RisultatiRisultato` cell. Returns `{ full_time_score_raw,
   * half_time_score_raw }`, either `null` when that label was not found
   * inside this cell at all -- never guessed from row position (the
   * separator row between them carries no label and is naturally skipped
   * by this selector-driven walk).
   */
  function readScores(resultCell) {
    const scores = { full_time_score_raw: null, half_time_score_raw: null };
    if (!resultCell) return scores;
    const labelDivs = resultCell.querySelectorAll(SELECTORS.scoreTypeLabel);
    labelDivs.forEach((labelDiv) => {
      const label = (labelDiv.getAttribute('title') || '').trim();
      const row = labelDiv.closest('tr');
      const valueDiv = row ? row.querySelector(SELECTORS.scoreValue) : null;
      const value = textOf(valueDiv) || null;
      if (label === FULL_TIME_LABEL) {
        scores.full_time_score_raw = value;
      } else if (label === HALF_TIME_LABEL) {
        scores.half_time_score_raw = value;
      }
    });
    return scores;
  }

  /**
   * Parses one competition group's own `table.RisultatiGruppiContainer`
   * into `{ results, unresolved }` for that group alone -- `results`
   * carries every row whose Bet9ja result id, start time, and fixture
   * text were all found (a missing/unparseable SCORE does not belong
   * here at all -- that is this module's own settlement-side concern,
   * never decided in the browser; every row with a readable identity is
   * exported, scores included verbatim, however incomplete).
   */
  function parseGroup(groupEl) {
    const competitionRaw = textOf(groupEl.querySelector(SELECTORS.groupNameCell));
    const results = [];
    const unresolved = [];

    const dataRows = groupEl.querySelectorAll(SELECTORS.dataRow);
    dataRows.forEach((row, rowIndex) => {
      const idRaw = textOf(row.querySelector(SELECTORS.idCell));
      const startRaw = textOf(row.querySelector(SELECTORS.startCell));
      const fixtureRaw = textOf(row.querySelector(SELECTORS.fixtureCell));
      const scores = readScores(row.querySelector(SELECTORS.resultCell));

      const base = {
        row_index: rowIndex,
        competition_raw: competitionRaw || null,
        bet9ja_result_id: idRaw || null,
        start_raw: startRaw || null,
        fixture_raw: fixtureRaw || null,
        full_time_score_raw: scores.full_time_score_raw,
        half_time_score_raw: scores.half_time_score_raw,
      };

      if (!idRaw || !startRaw || !fixtureRaw) {
        unresolved.push({ ...base, reason: 'RESULTS_ROW_MISSING_CORE_FIELD' });
        return;
      }
      results.push(base);
    });

    return { competitionRaw, results, unresolved };
  }

  /**
   * @param {Document} doc
   * @param {{sourceUrl: string, pageTitle: string, capturedAtUtc: string}} context
   * @returns {{envelope: object}}
   */
  function captureFromDocument(doc, context) {
    const capturedAtUtc = context.capturedAtUtc;
    const envelopeBase = {
      schema_version: 'bet9ja-soccer-results.v1',
      capture_id: Bet9jaIds.captureId(capturedAtUtc),
      captured_at_utc: capturedAtUtc,
      source_url: sanitizeSourceUrl(context.sourceUrl),
      page_title: context.pageTitle,
      parser_version: PARSER_VERSION,
      page_timezone: PAGE_TIMEZONE,
      // Unconfirmed selectors -- see this module's own header comment
      // "NOT YET CONFIRMED". Never guessed; left explicitly null rather
      // than derived from an unverified control.
      date_range: null,
    };

    const groups = doc.querySelectorAll(SELECTORS.groupContainer);
    if (groups.length === 0) {
      return {
        envelope: {
          ...envelopeBase,
          capture_status: 'CAPTURE_FAILED',
          capture_status_reasons: ['NO_RESULTS_GROUPS_FOUND'],
          competitions_seen: [],
          coverage: { groups_seen: 0, results_seen: 0, results_parsed: 0, results_unresolved: 0 },
          results: [],
          unresolved_results: [],
        },
      };
    }

    const allResults = [];
    const allUnresolved = [];
    const competitionsSeen = [];

    groups.forEach((groupEl) => {
      const { competitionRaw, results, unresolved } = parseGroup(groupEl);
      if (competitionRaw) competitionsSeen.push(competitionRaw);
      allResults.push(...results);
      allUnresolved.push(...unresolved);
    });

    const resultsSeen = allResults.length + allUnresolved.length;
    const captureStatus = allResults.length === 0 ? 'CAPTURE_FAILED' : allUnresolved.length > 0 ? 'CAPTURE_PARTIAL' : 'CAPTURE_OK';
    const statusReasons = [];
    if (allResults.length === 0) statusReasons.push('NO_USABLE_RESULTS_FOUND');
    if (allUnresolved.length > 0) statusReasons.push('UNRESOLVED_ROWS_PRESENT');

    return {
      envelope: {
        ...envelopeBase,
        capture_status: captureStatus,
        capture_status_reasons: statusReasons,
        competitions_seen: competitionsSeen,
        coverage: {
          groups_seen: groups.length,
          results_seen: resultsSeen,
          results_parsed: allResults.length,
          results_unresolved: allUnresolved.length,
        },
        results: allResults,
        unresolved_results: allUnresolved,
      },
    };
  }

  const api = { captureFromDocument, sanitizeSourceUrl, PARSER_VERSION, SELECTORS, PAGE_TIMEZONE };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaResultsCapture = api;
  }
})(typeof window !== 'undefined' ? window : this);
