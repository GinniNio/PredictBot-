const { test } = require('node:test');
const assert = require('node:assert/strict');
const session = require('../soccer_session.js');

// --- soccer_session.js: pure ledger/merge logic for durable checkpointed
// Soccer capture sessions. Every function returns a NEW session object
// (never mutates its input) -- see the module's own header comment for the
// resume-authority discipline (match by competition_id, never by array
// position) these tests hold it to.

const NIGERIA_LEAGUE = { competition_id: '1209691', country: 'Nigeria', competition: 'Professional Football League' };
const PREMIER_LEAGUE = { competition_id: '2000001', country: 'England', competition: 'Premier League' };
const CHAMPIONSHIP = { competition_id: '2000002', country: 'England', competition: 'Championship' };
const BOTSWANA_LEAGUE = { competition_id: '1838204', country: 'Botswana', competition: 'Premier League' };

function fixture(id, competitionId, home) {
  return { fixture_id: id, resolved_source_competition_id: competitionId, participants: { home, away: 'Away' } };
}

test('createSession starts every discovered competition as PENDING', () => {
  const s = session.createSession({
    sessionId: 'soccer-20260912-133007',
    capturedAtUtc: '2026-09-12T13:30:07Z',
    captureScope: 'SOCCER_ALL_PREMATCH_COMPETITIONS',
    inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE],
  });
  assert.equal(s.capture_session_id, 'soccer-20260912-133007');
  assert.equal(s.inventory.length, 3);
  assert.ok(s.inventory.every((entry) => entry.status === 'PENDING'));
  assert.equal(session.pendingCompetitionIds(s).length, 3);
  assert.equal(session.failedCompetitionIds(s).length, 0);
});

test('applyBatchDelta (crash-recovery scenario): saving after EVERY batch means only the in-flight batch is ever at risk', () => {
  let s = session.createSession({
    sessionId: 's1',
    capturedAtUtc: '2026-09-12T13:30:07Z',
    inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE],
  });
  // Batch 1: Nigeria captured.
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  // Simulate a crash right here -- `s` is exactly what would have been
  // persisted to chrome.storage.local after batch 1.
  assert.equal(session.summarize(s).completed, 1);
  assert.equal(session.summarize(s).pending, 2);
  assert.deepEqual(session.pendingCompetitionIds(s).sort(), ['1838204', '2000001'].sort());
  assert.equal(session.allFixtures(s).length, 1);
  // Resuming from this exact persisted state must never re-walk Nigeria.
  assert.ok(!session.pendingCompetitionIds(s).includes('1209691'));
});

test('applyBatchDelta: CAPTURED_IN_BATCH, BATCH_EMPTY, and failure outcomes map to the correct ledger status', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE, CHAMPIONSHIP] });
  s = session.applyBatchDelta(s, {
    competitionResults: [
      { source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' },
      { source_competition_id: '2000001', outcome: 'BATCH_EMPTY' },
      { source_competition_id: '1838204', outcome: 'BATCH_FAILED' },
      { source_competition_id: '2000002', outcome: 'COMPETITION_CONTENT_UNRESOLVED' },
    ],
    fixtures: [],
    unparsedRecords: [],
  });
  const byId = Object.fromEntries(s.inventory.map((e) => [e.competition_id, e.status]));
  assert.equal(byId['1209691'], 'COMPLETED');
  assert.equal(byId['2000001'], 'CONFIRMED_EMPTY');
  assert.equal(byId['1838204'], 'FAILED');
  assert.equal(byId['2000002'], 'FAILED');
});

test('applyBatchDelta: NOT_ATTEMPTED_AFTER_EARLY_STOP and SKIPPED_BY_RESUME_FILTER never overwrite the existing ledger status (failed-skip)', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, BOTSWANA_LEAGUE] });
  // First mark Botswana FAILED for real.
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1838204', outcome: 'BATCH_FAILED' }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(s.inventory.find((e) => e.competition_id === '1838204').status, 'FAILED');
  // A later run that merely SKIPPED it (never genuinely re-attempted it)
  // must not silently flip it back to PENDING or anything else.
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1838204', outcome: 'SKIPPED_BY_RESUME_FILTER' }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(s.inventory.find((e) => e.competition_id === '1838204').status, 'FAILED');
});

test('reconcileInventory (changed-inventory scenario): a competition that vanished from a fresh discovery keeps its own prior status -- completed work is never discarded', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  // A fresh discovery no longer lists Premier League at all (Bet9ja's own
  // inventory changed between runs) but DOES list a brand-new competition.
  const reconciled = session.reconcileInventory(s, [NIGERIA_LEAGUE, BOTSWANA_LEAGUE]);
  const byId = Object.fromEntries(reconciled.inventory.map((e) => [e.competition_id, e.status]));
  assert.equal(byId['1209691'], 'COMPLETED'); // retained
  assert.equal(byId['2000001'], 'PENDING'); // vanished, but RETAINED, never dropped
  assert.equal(byId['1838204'], 'PENDING'); // newly discovered
  assert.equal(reconciled.inventory.length, 3);
  assert.equal(session.allFixtures(reconciled).length, 1); // fixtures untouched by reconciliation
});

test('reconcileInventory refreshes country/competition display names from the fresh discovery without touching status', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [{ competition_id: '1209691', country: 'Nigeria', competition: 'Old Name' }] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [],
    unparsedRecords: [],
  });
  const reconciled = session.reconcileInventory(s, [{ competition_id: '1209691', country: 'Nigeria', competition: 'New Name' }]);
  const entry = reconciled.inventory.find((e) => e.competition_id === '1209691');
  assert.equal(entry.competition, 'New Name');
  assert.equal(entry.status, 'COMPLETED');
});

test('failedCompetitionIds (retry-failed scenario): only genuinely FAILED ids are returned, never PENDING or COMPLETED ones', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [
      { source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' },
      { source_competition_id: '1838204', outcome: 'BATCH_FAILED' },
    ],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.deepEqual(session.failedCompetitionIds(s), ['1838204']);
  // "Retry failed competitions" must never re-walk Nigeria (COMPLETED) or
  // Premier League (still PENDING -- that's a normal resume's job, not a
  // retry's).
  assert.ok(!session.failedCompetitionIds(s).includes('1209691'));
  assert.ok(!session.failedCompetitionIds(s).includes('2000001'));
});

test('a normal resume never includes FAILED ids -- that is Retry failed competitions\' own separate, explicit job', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1838204', outcome: 'BATCH_FAILED' }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.deepEqual(session.pendingCompetitionIds(s).sort(), ['1209691', '2000001'].sort());
  assert.ok(!session.pendingCompetitionIds(s).includes('1838204'));
});

test('mergeFixtures deduplicates by fixture_id across separate batches/segments (a fixture re-seen on a resume/retry is never downloaded twice)', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  s = session.mergeFixtures(s, [fixture('f1', '1209691', 'Enyimba')]);
  s = session.mergeFixtures(s, [fixture('f1', '1209691', 'Enyimba'), fixture('f2', '1209691', 'Enyimba')]);
  assert.equal(session.allFixtures(s).length, 2);
});

test('buildAssembledEnvelope combines every completed segment\'s fixtures with the full competition-status ledger', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', captureScope: 'SOCCER_ALL_PREMATCH_COMPETITIONS', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [
      { source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' },
      { source_competition_id: '1838204', outcome: 'BATCH_FAILED' },
    ],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [{ reason: 'MISSING_PARTICIPANTS' }],
  });
  const assembled = session.buildAssembledEnvelope(s);
  assert.equal(assembled.capture_session_id, 's1');
  assert.equal(assembled.fixtures.length, 1);
  assert.equal(assembled.competition_ledger.length, 3);
  assert.equal(assembled.summary.completed, 1);
  assert.equal(assembled.summary.failed, 1);
  assert.equal(assembled.summary.pending, 1);
  assert.equal(assembled.unparsed_records.length, 1);
});

test('buildSegmentEnvelope produces one run\'s own small file -- never the whole session', () => {
  const s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  const segment = session.buildSegmentEnvelope(s, 2, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  assert.equal(segment.capture_session_id, 's1');
  assert.equal(segment.segment_index, 2);
  assert.equal(segment.fixtures.length, 1);
});

test('inventoryFingerprint is stable across discovery order but changes when the id set changes', () => {
  const fp1 = session.inventoryFingerprint([NIGERIA_LEAGUE, PREMIER_LEAGUE]);
  const fp2 = session.inventoryFingerprint([PREMIER_LEAGUE, NIGERIA_LEAGUE]);
  const fp3 = session.inventoryFingerprint([NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE]);
  assert.equal(fp1, fp2);
  assert.notEqual(fp1, fp3);
});

test('nextPendingIndex is a display aid only -- it counts processed entries, never something read back to decide what to resume', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE, BOTSWANA_LEAGUE] });
  assert.equal(session.nextPendingIndex(s), 0);
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(session.nextPendingIndex(s), 1);
});

test('every session function returns a NEW object -- the input session is never mutated', () => {
  const original = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  const originalInventorySnapshot = JSON.parse(JSON.stringify(original.inventory));
  session.applyBatchDelta(original, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH' }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  assert.deepEqual(original.inventory, originalInventorySnapshot);
  assert.deepEqual(original.fixtures_by_competition, {});
});

// --- Round 13: monotonic ledger transitions, provenance, and export-time
// consistency validation (real evidence: an assembled session export
// showed 37 CONFIRMED_EMPTY competitions that also had fixtures attached) --

test('a completed competition later reported empty (COMPLETED -> CONFIRMED_EMPTY) is refused -- the ledger stays COMPLETED', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH', batch_index: 0 }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  assert.equal(s.inventory[0].status, 'COMPLETED');
  // A later, stale/incorrect batch tries to reclassify the SAME
  // competition as empty -- this must never be allowed to downgrade it.
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'BATCH_EMPTY', batch_index: 5 }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(s.inventory[0].status, 'COMPLETED');
  assert.equal(session.allFixtures(s).length, 1);
});

test('a completed competition can never be downgraded to FAILED either (COMPLETED -> FAILED refused)', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH', batch_index: 0 }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'BATCH_FAILED', batch_index: 9 }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(s.inventory[0].status, 'COMPLETED');
});

test('fixtures present for an "empty" ledger entry: applyCompetitionResults refuses the CONFIRMED_EMPTY write when fixtures already exist for that id, even from PENDING/FAILED', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  // Fixtures land under this id via a batch delta that (hypothetically,
  // defensively) never itself reported a competitionResult -- session
  // now holds a fixture for a still-PENDING id, an inconsistent state
  // this test manufactures directly to prove the guard catches it.
  s = session.mergeFixtures(s, [fixture('f1', '1209691', 'Enyimba')]);
  assert.equal(s.inventory[0].status, 'PENDING');
  s = session.applyCompetitionResults(s, [{ source_competition_id: '1209691', outcome: 'BATCH_EMPTY', batch_index: 0 }]);
  assert.equal(s.inventory[0].status, 'PENDING'); // refused -- fixtures already exist
});

test('stale content after selection: a FAILED competition (e.g. COMPETITION_STALE_CONTENT_SUSPECTED) can still transition to COMPLETED or CONFIRMED_EMPTY on a genuine retry', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [BOTSWANA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1838204', outcome: 'COMPETITION_STALE_CONTENT_SUSPECTED', batch_index: 3 }],
    fixtures: [],
    unparsedRecords: [],
  });
  assert.equal(s.inventory[0].status, 'FAILED');
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1838204', outcome: 'CAPTURED_IN_BATCH', batch_index: 4 }],
    fixtures: [fixture('f1', '1838204', 'Gaborone United')],
    unparsedRecords: [],
  });
  assert.equal(s.inventory[0].status, 'COMPLETED');
});

test('session merge preserves the strongest valid status: applying an outcome that maps to no ledger status (e.g. an unrecognized/future outcome) never disturbs the existing entry', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH', batch_index: 0 }],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  s = session.applyCompetitionResults(s, [{ source_competition_id: '1209691', outcome: 'SOME_FUTURE_OUTCOME_THIS_MODULE_DOES_NOT_KNOW' }]);
  assert.equal(s.inventory[0].status, 'COMPLETED');
});

test('every ledger transition records provenance (segment_index, batch_index, updated_at_utc, fixture_count)', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  assert.equal(s.inventory[0].segment_index, null);
  assert.equal(s.inventory[0].fixture_count, 0);
  s = session.applyBatchDelta(
    s,
    {
      competitionResults: [{ source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH', batch_index: 19 }],
      fixtures: [fixture('f1', '1209691', 'Enyimba'), fixture('f2', '1209691', 'Enyimba')],
      unparsedRecords: [],
    },
    { segmentIndex: 1 }
  );
  const entry = s.inventory[0];
  assert.equal(entry.status, 'COMPLETED');
  assert.equal(entry.segment_index, 1);
  assert.equal(entry.batch_index, 19);
  assert.equal(entry.fixture_count, 2);
  assert.ok(entry.updated_at_utc);
});

test('export-time ledger/fixture consistency: buildAssembledEnvelope throws SESSION_LEDGER_FIXTURE_CONFLICT if a CONFIRMED_EMPTY id somehow still has fixtures attached', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE] });
  // Manufacture the exact reported inconsistency directly (bypassing the
  // write-time guards) to prove the export-time backstop catches it
  // independently, per this file's own "two independent safeguards"
  // header comment.
  s = { ...s, inventory: s.inventory.map((e) => ({ ...e, status: 'CONFIRMED_EMPTY' })) };
  s = session.mergeFixtures(s, [fixture('f1', '1209691', 'Enyimba')]);
  assert.throws(
    () => session.buildAssembledEnvelope(s),
    (err) => err.code === 'SESSION_LEDGER_FIXTURE_CONFLICT' && err.conflicts.length === 1 && err.conflicts[0].competition_id === '1209691'
  );
});

test('validateLedgerFixtureConsistency returns an empty array for a genuinely consistent session', () => {
  let s = session.createSession({ sessionId: 's1', capturedAtUtc: 't', inventory: [NIGERIA_LEAGUE, PREMIER_LEAGUE] });
  s = session.applyBatchDelta(s, {
    competitionResults: [
      { source_competition_id: '1209691', outcome: 'CAPTURED_IN_BATCH', batch_index: 0 },
      { source_competition_id: '2000001', outcome: 'BATCH_EMPTY', batch_index: 1 },
    ],
    fixtures: [fixture('f1', '1209691', 'Enyimba')],
    unparsedRecords: [],
  });
  assert.deepEqual(session.validateLedgerFixtureConsistency(s), []);
  assert.doesNotThrow(() => session.buildAssembledEnvelope(s));
});

test('isTransitionAllowed is exported and matches the documented transition table exactly', () => {
  assert.equal(session.isTransitionAllowed('PENDING', 'COMPLETED'), true);
  assert.equal(session.isTransitionAllowed('PENDING', 'CONFIRMED_EMPTY'), true);
  assert.equal(session.isTransitionAllowed('PENDING', 'FAILED'), true);
  assert.equal(session.isTransitionAllowed('FAILED', 'COMPLETED'), true);
  assert.equal(session.isTransitionAllowed('FAILED', 'CONFIRMED_EMPTY'), true);
  assert.equal(session.isTransitionAllowed('COMPLETED', 'CONFIRMED_EMPTY'), false);
  assert.equal(session.isTransitionAllowed('COMPLETED', 'FAILED'), false);
  assert.equal(session.isTransitionAllowed('CONFIRMED_EMPTY', 'COMPLETED'), false);
  assert.equal(session.isTransitionAllowed('CONFIRMED_EMPTY', 'FAILED'), false);
  // A same-status "transition" is always a no-op, never a violation.
  assert.equal(session.isTransitionAllowed('COMPLETED', 'COMPLETED'), true);
  assert.equal(session.isTransitionAllowed('CONFIRMED_EMPTY', 'CONFIRMED_EMPTY'), true);
});
