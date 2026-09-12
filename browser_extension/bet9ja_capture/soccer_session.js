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
 * `COMPETITION_ATTRIBUTION_UNRESOLVED`/`COMPETITION_CONTENT_UNRESOLVED`/
 * `COMPETITION_STALE_CONTENT_SUSPECTED` from soccer_walker.js's own
 * `competition_results[].outcome`).
 *
 * ROUND 13 CORRECTION (real evidence: an assembled session export showed
 * 37 competitions marked `CONFIRMED_EMPTY` that ALSO had real fixtures
 * attached under the same id -- a competition can never honestly be
 * both). Two independent safeguards now exist, deliberately overlapping
 * rather than relying on either alone:
 *   1. LEDGER TRANSITIONS ARE MONOTONIC (`isTransitionAllowed` below) --
 *      once a competition is `COMPLETED` or `CONFIRMED_EMPTY`, NOTHING
 *      ever downgrades it again (a same-status "transition" is a no-op,
 *      not a downgrade). Only `PENDING -> {COMPLETED, CONFIRMED_EMPTY,
 *      FAILED}` and `FAILED -> {COMPLETED, CONFIRMED_EMPTY}` are ever
 *      applied; anything else (including `COMPLETED -> CONFIRMED_EMPTY`,
 *      the exact shape of the reported defect) is silently refused by
 *      `applyCompetitionResults` -- a delta that can never happen from a
 *      well-behaved caller, but one this module never trusts blindly.
 *   2. EVERY LEDGER TRANSITION IS RECORDED WITH PROVENANCE
 *      (`segment_index`, `batch_index`, `updated_at_utc`, `fixture_count`
 *      -- see `applyCompetitionResults`), so a future inconsistency can
 *      be traced to the exact run/batch that caused it instead of being
 *      diagnosed blind from the assembled file alone.
 * `buildAssembledEnvelope` ALSO independently validates, at export time,
 * that no `CONFIRMED_EMPTY` id has any fixtures attached
 * (`validateLedgerFixtureConsistency`) -- a final backstop that throws a
 * `SESSION_LEDGER_FIXTURE_CONFLICT` error rather than ever producing a
 * self-contradictory file, even if some future edit ever bypassed the
 * transition guard above.
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
    COMPETITION_STALE_CONTENT_SUSPECTED: 'FAILED',
  };

  // ROUND 13: the only ledger status changes this module will ever apply
  // -- see this file's own header comment. A status not listed as a key
  // here (there are none today -- every real status can still, in
  // principle, need a FAILED retry path) has no allowed outgoing
  // transitions; `COMPLETED` and `CONFIRMED_EMPTY` are both listed with
  // an EMPTY allowed-set, meaning they are terminal: nothing this module
  // does ever downgrades a genuinely confirmed result.
  const ALLOWED_LEDGER_TRANSITIONS = {
    PENDING: new Set(['COMPLETED', 'CONFIRMED_EMPTY', 'FAILED']),
    FAILED: new Set(['COMPLETED', 'CONFIRMED_EMPTY']),
    COMPLETED: new Set(),
    CONFIRMED_EMPTY: new Set(),
  };

  /** A same-status "transition" is a no-op, never a violation -- re-applying an identical outcome (e.g. a delta drained twice) must never be treated as a downgrade attempt. */
  function isTransitionAllowed(fromStatus, toStatus) {
    if (fromStatus === toStatus) return true;
    const allowed = ALLOWED_LEDGER_TRANSITIONS[fromStatus];
    return !!allowed && allowed.has(toStatus);
  }

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
      // Provenance (Round 13, see this file's own header comment) --
      // null/0 until this entry's first genuine transition away from
      // PENDING; `applyCompetitionResults` is the only place these ever
      // change.
      segment_index: null,
      batch_index: null,
      updated_at_utc: null,
      fixture_count: 0,
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
   *
   * ROUND 13: every other write is now gated by TWO independent checks,
   * both silently refusing the write (never throwing -- a malformed or
   * stale delta must never crash the whole session) rather than trusting
   * the caller: `isTransitionAllowed` (see this file's own header
   * comment -- `COMPLETED`/`CONFIRMED_EMPTY` are terminal) and, for a
   * `CONFIRMED_EMPTY` target specifically, that this competition has NO
   * fixtures already recorded in the session (defense in depth alongside
   * the transition guard -- `COMPLETED -> CONFIRMED_EMPTY` is already
   * blocked there, but a competition could in principle reach
   * `CONFIRMED_EMPTY` some other way while fixtures already exist under
   * its id; this closes that gap too). `options.segmentIndex` and each
   * result's own `batch_index` are recorded as this transition's
   * provenance; `options.fixtureCountByCompetition` (built by
   * `applyBatchDelta` from this SAME delta's own fixtures) is recorded as
   * `fixture_count` -- this transition's own contribution, not a running
   * total.
   */
  function applyCompetitionResults(session, competitionResults, options = {}) {
    const segmentIndex = options.segmentIndex != null ? options.segmentIndex : null;
    const fixtureCountByCompetition = options.fixtureCountByCompetition || {};
    const byId = new Map(session.inventory.map((entry) => [entry.competition_id, entry]));
    const timestamp = nowIso();
    for (const result of competitionResults || []) {
      const id = result.source_competition_id;
      if (!id) continue;
      const newStatus = OUTCOME_TO_LEDGER_STATUS[result.outcome];
      if (!newStatus) continue;
      const existing = byId.get(id);
      if (!existing) continue;
      if (!isTransitionAllowed(existing.status, newStatus)) continue;
      if (newStatus === 'CONFIRMED_EMPTY' && (session.fixtures_by_competition[id] || []).length > 0) continue;
      if (existing.status === newStatus) continue; // no-op: nothing new to record
      byId.set(id, {
        ...existing,
        status: newStatus,
        segment_index: segmentIndex,
        batch_index: result.batch_index != null ? result.batch_index : null,
        updated_at_utc: timestamp,
        fixture_count: fixtureCountByCompetition[id] || 0,
      });
    }
    return {
      ...session,
      inventory: session.inventory.map((entry) => byId.get(entry.competition_id) || entry),
      updated_at_utc: timestamp,
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
   * capture finishes. `options.segmentIndex` (which run/segment this
   * delta came from -- popup.js's own `sessionObj.segments.length + 1`)
   * is recorded as provenance on every ledger transition this delta
   * causes (Round 13, see `applyCompetitionResults`'s own comment).
   * Applies competition-result classification BEFORE merging fixtures --
   * the "no fixtures already exist" guard inside
   * `applyCompetitionResults` deliberately still only sees fixtures from
   * PRIOR deltas at that point (this delta's own fixture COUNT is passed
   * separately via `fixtureCountByCompetition`, for provenance only, not
   * as another copy of the guard) -- the guard's purpose is catching a
   * STALE re-confirmation of a competition already settled by an earlier
   * delta, not this same delta's own internally-consistent report.
   */
  function applyBatchDelta(session, { competitionResults, fixtures, unparsedRecords }, options = {}) {
    const fixtureCountByCompetition = {};
    for (const fixture of fixtures || []) {
      const id = fixture.resolved_source_competition_id;
      if (!id) continue;
      fixtureCountByCompetition[id] = (fixtureCountByCompetition[id] || 0) + 1;
    }
    let next = applyCompetitionResults(session, competitionResults || [], {
      segmentIndex: options.segmentIndex,
      fixtureCountByCompetition,
    });
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

  /**
   * Round 13's final backstop (see this file's own header comment): a
   * `CONFIRMED_EMPTY` competition id must have ZERO fixtures attached in
   * the session. Returns an array of `{competition_id, fixture_count}`
   * conflicts -- empty means consistent. Independent of, and in addition
   * to, the transition guard in `applyCompetitionResults` -- this checks
   * the session's ACTUAL current state at export time, not merely
   * whether every individual write along the way was itself valid.
   */
  function validateLedgerFixtureConsistency(session) {
    const confirmedEmptyIds = new Set(session.inventory.filter((entry) => entry.status === 'CONFIRMED_EMPTY').map((entry) => entry.competition_id));
    const conflicts = [];
    for (const [competitionId, fixturesForCompetition] of Object.entries(session.fixtures_by_competition)) {
      if (confirmedEmptyIds.has(competitionId) && fixturesForCompetition.length > 0) {
        conflicts.push({ competition_id: competitionId, fixture_count: fixturesForCompetition.length });
      }
    }
    return conflicts;
  }

  /**
   * The full "Download current results" file -- every completed
   * segment's fixtures, plus the complete competition-status ledger.
   * THROWS (never silently exports a self-contradictory file) with
   * `err.code === 'SESSION_LEDGER_FIXTURE_CONFLICT'` and `err.conflicts`
   * populated if `validateLedgerFixtureConsistency` finds any conflict --
   * see this file's own header comment. The caller (popup.js) is
   * expected to catch this and surface it, never to clear the session on
   * its own -- the ledger's own provenance fields on each conflicting
   * entry are exactly what's needed to trace which segment/batch caused
   * it.
   */
  function buildAssembledEnvelope(session) {
    const conflicts = validateLedgerFixtureConsistency(session);
    if (conflicts.length > 0) {
      const err = new Error(
        `SESSION_LEDGER_FIXTURE_CONFLICT: ${conflicts.length} competition id(s) are CONFIRMED_EMPTY but still have fixtures attached (${conflicts
          .map((c) => c.competition_id)
          .join(', ')})`
      );
      err.code = 'SESSION_LEDGER_FIXTURE_CONFLICT';
      err.conflicts = conflicts;
      throw err;
    }
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
    validateLedgerFixtureConsistency,
    isTransitionAllowed,
  };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaSoccerSession = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
