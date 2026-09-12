/**
 * Durable checkpointed Soccer capture sessions.
 *
 * Pure ledger/merge logic ONLY -- this module never touches
 * `chrome.storage`, `chrome.tabs`, or any Chrome API at all, so it runs
 * identically in the popup (persisted via `chrome.storage.local`) and in
 * the Node test suite (persisted via a plain in-memory object). Every
 * function here takes a session object and returns a NEW session object
 * (never mutates its input) -- the caller decides when/how to persist the
 * result.
 *
 * WHY THIS EXISTS (real evidence, 2026-09-12 13:30:07 capture): a single
 * `SHOW_LEAGUES_CONTENT_TIMEOUT` that soccer_walker.js's own Round 11 fix
 * already retries once can still, on a real account, genuinely fail twice
 * -- and even with Round 11's fix, a browser crash, an accidental popup
 * close, or a user-requested stop mid-run would otherwise lose all 353
 * competitions' worth of progress. This module lets a run persist its
 * ledger and fixtures after EVERY batch (not only at the end), so any of
 * those interruptions loses at most the one batch in flight, never the
 * whole session.
 *
 * RESUME AUTHORITY: Bet9ja's own competition inventory changes between
 * runs (a league's round starting or finishing, a fixture window opening
 * -- see soccer_walker.js's own "Inventory stabilization" comment). A
 * numeric position/index is therefore NEVER the resume authority here --
 * every ledger entry is matched exclusively by its own stable
 * `competition_id` (the Bet9ja checkbox id), never by array position.
 * `next_pending_index` exists ONLY as a display aid (see
 * `nextPendingIndex` below) and is never read back to decide what to
 * resume.
 *
 * LEDGER STATUS VALUES: `PENDING` (never yet attempted, or the run never
 * reached it), `COMPLETED` (captured at least one fixture),
 * `CONFIRMED_EMPTY` (resolved with zero fixtures -- a real, audited
 * result, not a failure), `FAILED` (attempted and did not resolve --
 * covers `BATCH_FAILED`/`SELECTION_FAILED`/
 * `COMPETITION_ATTRIBUTION_UNRESOLVED`/`COMPETITION_CONTENT_UNRESOLVED`
 * from soccer_walker.js's own `competition_results[].outcome`).
 */
(function (root) {
  const Bet9jaIds = typeof module !== 'undefined' && module.exports ? require('./ids.js') : root.Bet9jaIds;

  const SESSION_SCHEMA_VERSION = 'bet9ja-soccer-session.v1';

  // soccer_walker.js's own competition_results[].outcome values, mapped
  // to this module's own, smaller ledger status vocabulary. An outcome
  // not listed here (NOT_ATTEMPTED_AFTER_EARLY_STOP, SKIPPED_BY_RESUME_FILTER,
  // or anything future/unrecognized) leaves the ledger entry's existing
  // status UNCHANGED -- both of those outcomes mean "this run never
  // genuinely attempted this competition", which is never grounds to
  // overwrite whatever the ledger already honestly recorded.
  const OUTCOME_TO_LEDGER_STATUS = {
    CAPTURED_IN_BATCH: 'COMPLETED',
    BATCH_EMPTY: 'CONFIRMED_EMPTY',
    BATCH_FAILED: 'FAILED',
    SELECTION_FAILED: 'FAILED',
    COMPETITION_ATTRIBUTION_UNRESOLVED: 'FAILED',
    COMPETITION_CONTENT_UNRESOLVED: 'FAILED',
  };

  function nowIso() {
    return new Date().toISOString();
  }

  /** Stable, dependency-free fingerprint of an inventory's own id set --
   * never a cryptographic hash (this is an equality check, not a security
   * boundary), and deliberately independent of discovery ORDER (sorted
   * first) so two discoveries of the same real inventory fingerprint
   * identically regardless of any incidental DOM-order difference. */
  function inventoryFingerprint(inventory) {
    const sortedIds = (inventory || []).map((c) => c.competition_id).sort();
    return Bet9jaIds.stableId('invfp', sortedIds);
  }

  function ledgerEntryFromDiscovered(discovered) {
    return {
      competition_id: discovered.competition_id,
      country: discovered.country || null,
      competition: discovered.competition || null,
      status: 'PENDING',
    };
  }

  /**
   * Starts a brand-new session from a freshly discovered inventory --
   * every competition begins `PENDING`. This is the ONLY way a session's
   * `capture_session_id` is ever assigned; every other function in this
   * module preserves it.
   */
  function createSession({ sessionId, capturedAtUtc, captureScope, inventory }) {
    const timestamp = capturedAtUtc || nowIso();
    return {
      schema_version: SESSION_SCHEMA_VERSION,
      capture_session_id: sessionId,
      capture_scope: captureScope || null,
      created_at_utc: timestamp,
      updated_at_utc: timestamp,
      inventory_fingerprint: inventoryFingerprint(inventory || []),
      inventory: (inventory || []).map(ledgerEntryFromDiscovered),
      fixtures_by_competition: {},
      unparsed_records: [],
      seen_fixture_ids: [],
      segments: [],
    };
  }

  /**
   * Reconciles a session's own ledger against a FRESHLY discovered
   * inventory (a resume always rediscovers -- see soccer_walker.js's own
   * `discoverStableInventory` -- rather than trusting a stale list).
   * Matches exclusively by `competition_id`: an id present in both keeps
   * its existing status and refreshed country/competition names; an id
   * newly discovered is added as `PENDING`; an id from the OLD ledger no
   * longer present in the fresh inventory is RETAINED as-is (never
   * dropped -- its own completed work, if any, must never be discarded
   * merely because Bet9ja's own inventory listing changed shape between
   * runs).
   */
  function reconcileInventory(session, freshInventory) {
    const byId = new Map(session.inventory.map((entry) => [entry.competition_id, entry]));
    const freshIds = new Set();
    const reconciled = [];
    for (const discovered of freshInventory || []) {
      freshIds.add(discovered.competition_id);
      const existing = byId.get(discovered.competition_id);
      if (existing) {
        reconciled.push({
          ...existing,
          country: discovered.country || existing.country,
          competition: discovered.competition || existing.competition,
        });
      } else {
        reconciled.push(ledgerEntryFromDiscovered(discovered));
      }
    }
    // Old entries whose id vanished from this fresh discovery -- kept,
    // never dropped, appended after every currently-discoverable entry so
    // pendingCompetitionIds/failedCompetitionIds still walk the currently
    // real inventory first.
    for (const entry of session.inventory) {
      if (!freshIds.has(entry.competition_id)) {
        reconciled.push(entry);
      }
    }
    return {
      ...session,
      inventory: reconciled,
      inventory_fingerprint: inventoryFingerprint(freshInventory || []),
      updated_at_utc: nowIso(),
    };
  }

  function competitionIdsWithStatus(session, status) {
    return session.inventory.filter((entry) => entry.status === status).map((entry) => entry.competition_id);
  }

  /** IDs a normal "Resume capture" should process -- never `FAILED` ones (see "Retry failed competitions" below for those). */
  function pendingCompetitionIds(session) {
    return competitionIdsWithStatus(session, 'PENDING');
  }

  /** IDs "Retry failed competitions" should process -- a separate, explicit action, never folded into a normal resume. */
  function failedCompetitionIds(session) {
    return competitionIdsWithStatus(session, 'FAILED');
  }

  /**
   * Applies one run's own `competition_results[]` (soccer_walker.js's
   * own output, verbatim) onto the ledger. `SKIPPED_BY_RESUME_FILTER` and
   * `NOT_ATTEMPTED_AFTER_EARLY_STOP` rows are matched by
   * `OUTCOME_TO_LEDGER_STATUS` coming back `undefined` and are correctly
   * left untouched -- this run never genuinely attempted them, so their
   * prior ledger status (whatever it already honestly was) stands.
   */
  function applyCompetitionResults(session, competitionResults) {
    const byId = new Map(session.inventory.map((entry) => [entry.competition_id, entry]));
    for (const result of competitionResults || []) {
      const id = result.source_competition_id;
      if (!id) continue;
      const newStatus = OUTCOME_TO_LEDGER_STATUS[result.outcome];
      if (!newStatus) continue;
      const existing = byId.get(id);
      if (existing) {
        byId.set(id, { ...existing, status: newStatus });
      }
    }
    return {
      ...session,
      inventory: session.inventory.map((entry) => byId.get(entry.competition_id) || entry),
      updated_at_utc: nowIso(),
    };
  }

  /**
   * Merges freshly captured fixtures into the session, deduplicated by
   * `fixture_id` -- the SAME scheme every other capture button uses, so a
   * fixture legitimately re-seen across a resume/retry run is never
   * downloaded twice. Grouped by `resolved_source_competition_id`
   * (falling back to the literal string `'unattributed'` only in the
   * should-never-happen case of a fixture with no resolved id at all --
   * see parser.js's own comment on that field).
   */
  function mergeFixtures(session, newFixtures) {
    const seen = new Set(session.seen_fixture_ids);
    const byCompetition = {};
    for (const [id, list] of Object.entries(session.fixtures_by_competition)) {
      byCompetition[id] = list.slice();
    }
    for (const fixture of newFixtures || []) {
      if (seen.has(fixture.fixture_id)) continue;
      seen.add(fixture.fixture_id);
      const bucket = fixture.resolved_source_competition_id || 'unattributed';
      if (!byCompetition[bucket]) byCompetition[bucket] = [];
      byCompetition[bucket].push(fixture);
    }
    return {
      ...session,
      fixtures_by_competition: byCompetition,
      seen_fixture_ids: Array.from(seen),
      updated_at_utc: nowIso(),
    };
  }

  function mergeUnparsedRecords(session, newRecords) {
    if (!newRecords || newRecords.length === 0) return session;
    return {
      ...session,
      unparsed_records: session.unparsed_records.concat(newRecords),
      updated_at_utc: nowIso(),
    };
  }

  /**
   * Convenience combining `applyCompetitionResults` + `mergeFixtures` +
   * `mergeUnparsedRecords` for one batch's own delta -- exactly what a
   * `soccer_walker.js` `onBatchComplete` callback hands the caller, so a
   * session can be saved after EVERY batch, not only when the whole
   * capture finishes.
   */
  function applyBatchDelta(session, { competitionResults, fixtures, unparsedRecords }) {
    let next = applyCompetitionResults(session, competitionResults || []);
    next = mergeFixtures(next, fixtures || []);
    next = mergeUnparsedRecords(next, unparsedRecords || []);
    return next;
  }

  function allFixtures(session) {
    return Object.values(session.fixtures_by_competition).flat();
  }

  /** Purely a DISPLAY aid ("Completed: 139, Failed: 1, Remaining: 213") -- never read back to decide what to resume; see this file's own header comment. */
  function summarize(session) {
    const counts = { PENDING: 0, COMPLETED: 0, CONFIRMED_EMPTY: 0, FAILED: 0 };
    for (const entry of session.inventory) {
      if (Object.prototype.hasOwnProperty.call(counts, entry.status)) {
        counts[entry.status] += 1;
      }
    }
    return {
      total: session.inventory.length,
      completed: counts.COMPLETED,
      confirmed_empty: counts.CONFIRMED_EMPTY,
      failed: counts.FAILED,
      pending: counts.PENDING,
    };
  }

  /** Display aid only (see this file's own header comment) -- the count of ledger entries no longer PENDING, never the resume authority itself. */
  function nextPendingIndex(session) {
    return session.inventory.filter((entry) => entry.status !== 'PENDING').length;
  }

  /** The full "Download current results" file -- every completed segment's fixtures, plus the complete competition-status ledger. */
  function buildAssembledEnvelope(session) {
    return {
      schema_version: SESSION_SCHEMA_VERSION,
      capture_session_id: session.capture_session_id,
      capture_scope: session.capture_scope,
      captured_at_utc: session.updated_at_utc,
      inventory_fingerprint: session.inventory_fingerprint,
      summary: summarize(session),
      competition_ledger: session.inventory,
      fixtures: allFixtures(session),
      unparsed_records: session.unparsed_records,
    };
  }

  /** One run's own small segment file -- exactly what that run itself captured, never the whole session. */
  function buildSegmentEnvelope(session, segmentIndex, { competitionResults, fixtures, unparsedRecords }) {
    return {
      schema_version: SESSION_SCHEMA_VERSION,
      capture_session_id: session.capture_session_id,
      segment_index: segmentIndex,
      captured_at_utc: nowIso(),
      competition_results: competitionResults || [],
      fixtures: fixtures || [],
      unparsed_records: unparsedRecords || [],
    };
  }

  const api = {
    SESSION_SCHEMA_VERSION,
    createSession,
    reconcileInventory,
    pendingCompetitionIds,
    failedCompetitionIds,
    applyCompetitionResults,
    mergeFixtures,
    mergeUnparsedRecords,
    applyBatchDelta,
    allFixtures,
    summarize,
    nextPendingIndex,
    buildAssembledEnvelope,
    buildSegmentEnvelope,
    inventoryFingerprint,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerSession = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
