/**
 * Bet9ja SYSTEM-ticket stake-bucket extraction -- structured DOM parsing
 * of `.mybets__systable`, shared by `settled_bets_parser.js` and
 * `ticket_parser.js`. Exists specifically to replace the flattened-text
 * `system_table_raw` field as a source of truth for a SYSTEM ticket's
 * per-fold-size stake breakdown -- see
 * `orchestration/bet9ja_ticket_import.py`'s own module docstring for why
 * that flattened text is never parsed anywhere in this codebase (an
 * earlier regex-based reading of it silently misparsed a bet count, in
 * this same working session).
 *
 * STATUS -- see `STAKE_BUCKETS_LIVE_VALIDATION.md` for the full,
 * up-to-date record. In short: `.mybets__systable`'s CONTAINER selector
 * is confirmed by live inspection (Round 1 of the settled-bets capture
 * work); its INTERNAL row/cell structure is NOT yet confirmed against a
 * real, live Bet9ja page in this working session (no authenticated
 * browser access was available). This module assumes the ordinary,
 * standard shape a data table like this would have -- a header row
 * followed by one `<tr>` per system-type bucket, each with exactly 4
 * cells (System Type, No. Bets, Unit Stake, Stake) -- and is written
 * defensively so that assumption being wrong on a real page produces NO
 * structured output at all (never a partial or silently wrong one),
 * rather than a crash or a guess. Do not treat `parseStakeBuckets`'s
 * return value as trustworthy until `STAKE_BUCKETS_LIVE_VALIDATION.md`
 * records a confirmed live round for it, the same way every other
 * REAL-DOM PROFILE claim in this directory is confirmed before use.
 *
 * FAIL CLOSED, matching every other parser in this directory: if the
 * table has zero rows, if the header row can't be recognized, if any
 * data row does not resolve to exactly the 4 expected cells, if a
 * System Type label doesn't map to a known fold-size shape, if two rows
 * claim the same fold size, or if a row's own "No. Bets" count disagrees
 * with the canonical combination count `C(legCount, foldSize)` -- the
 * WHOLE result is `null`, never a partial array with the bad row simply
 * dropped. A caller that gets `null` back has exactly the same
 * information as it did before this module existed: nothing.
 */
(function (root) {
  // "Singles" -> 1, "Doubles" -> 2, "Trebles" -> 3 -- Bet9ja's own named
  // fold sizes for the first three. Anything else must be parsed as
  // "<N> Fold(s)" (e.g. "4 Folds", "7 Fold") -- see parseFoldSize below.
  const NAMED_FOLD_SIZES = {
    singles: 1,
    doubles: 2,
    trebles: 3,
  };

  function normalizeCellText(text) {
    return (text == null ? '' : String(text)).replace(/\s+/g, ' ').trim();
  }

  // Canonical n-choose-k, computed the same way
  // orchestration/bet9ja_ticket_import.py::_n_choose_k does on the
  // Python side (via itertools.combinations there; a plain factorial-free
  // multiplicative formula here) -- both sides must agree on this number
  // or a real, correctly-parsed row would fail the Python-side identity
  // check for no reason.
  function nChooseK(n, k) {
    if (k < 0 || k > n) return 0;
    let result = 1;
    for (let i = 0; i < k; i += 1) {
      result = (result * (n - i)) / (i + 1);
    }
    return Math.round(result);
  }

  function parseFoldSize(systemTypeText) {
    const normalized = normalizeCellText(systemTypeText).toLowerCase();
    if (Object.prototype.hasOwnProperty.call(NAMED_FOLD_SIZES, normalized)) {
      return NAMED_FOLD_SIZES[normalized];
    }
    const match = normalized.match(/^(\d+)\s*folds?$/);
    if (!match) return null;
    const n = parseInt(match[1], 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  // Same decimal-string acceptance as ids.js/the other parsers in this
  // directory: digits, an optional thousands separator, an optional
  // decimal point -- never a locale-specific parse, never a bare
  // parseFloat (which would silently accept "12.34.56" or "12abc").
  function parseMoneyText(text) {
    const normalized = normalizeCellText(text).replace(/,/g, '');
    if (!/^\d+(\.\d+)?$/.test(normalized)) return null;
    return normalized;
  }

  function parseIntCellText(text) {
    const normalized = normalizeCellText(text).replace(/,/g, '');
    if (!/^\d+$/.test(normalized)) return null;
    return parseInt(normalized, 10);
  }

  /**
   * `systemTableEl`: the `.mybets__systable` element itself (or `null`,
   * for a non-SYSTEM ticket -- returns `null` immediately). `legCount`:
   * the ticket's own already-parsed leg count, used ONLY to validate
   * each row's own "No. Bets" figure against the canonical
   * `C(legCount, foldSize)` -- never to guess a fold size from leg count
   * alone. Returns an array of `{fold_size, combination_count,
   * unit_stake, total_stake}` objects (unit_stake/total_stake as decimal
   * STRINGS, matching every other money field this codebase's Python
   * side expects -- see ledgers/money.py), or `null` if anything at all
   * about the table's shape could not be resolved exactly.
   */
  function parseStakeBuckets(systemTableEl, legCount) {
    if (!systemTableEl || !Number.isInteger(legCount) || legCount < 1) return null;

    const rows = Array.from(systemTableEl.querySelectorAll('tr'));
    if (rows.length < 2) return null; // need at least a header + one data row

    // The first row is always the header (its own cells are never parsed
    // for values, whether they're <th> or class-styled <td>) -- only
    // rows after it are data rows.
    const dataRows = rows.slice(1);
    if (dataRows.length === 0) return null;

    const seenFoldSizes = new Set();
    const buckets = [];

    for (const row of dataRows) {
      const cells = Array.from(row.querySelectorAll('td, th'));
      if (cells.length !== 4) return null; // never guess which 3 of 5 cells matter

      const foldSize = parseFoldSize(cells[0].textContent);
      const combinationCount = parseIntCellText(cells[1].textContent);
      const unitStake = parseMoneyText(cells[2].textContent);
      const totalStake = parseMoneyText(cells[3].textContent);

      if (foldSize === null || combinationCount === null || unitStake === null || totalStake === null) {
        return null;
      }
      if (foldSize > legCount || seenFoldSizes.has(foldSize)) {
        return null;
      }
      const expectedCount = nChooseK(legCount, foldSize);
      if (combinationCount !== expectedCount) {
        return null; // the page's own numbers disagree with combinatorics -- never trusted, never guessed past
      }
      const expectedTotal = (parseFloat(unitStake) * combinationCount).toFixed(2);
      if (parseFloat(totalStake).toFixed(2) !== expectedTotal) {
        return null; // internal inconsistency within the row itself
      }

      seenFoldSizes.add(foldSize);
      buckets.push({
        fold_size: foldSize,
        combination_count: combinationCount,
        unit_stake: unitStake,
        total_stake: totalStake,
      });
    }

    return buckets;
  }

  const api = { parseStakeBuckets, parseFoldSize, nChooseK };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaStakeBuckets = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
