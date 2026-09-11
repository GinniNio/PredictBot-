const { test } = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../parser.js');
const { loadFixtureDocument, BASE_CONTEXT } = require('./helpers.js');

function capture(fixtureName, extraContext) {
  const doc = loadFixtureDocument(fixtureName);
  return parser.captureFromDocument(doc, { ...BASE_CONTEXT, ...extraContext });
}

test('normal Soccer 1X2: one fixture fully normalized', () => {
  const { envelope } = capture('normal_soccer_1x2.html');
  assert.equal(envelope.schema_version, 'bet9ja-fixture-capture.v1');
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
  assert.equal(envelope.coverage.sections_seen, 1);
  assert.equal(envelope.coverage.records_seen, 1);
  assert.equal(envelope.coverage.records_parsed, 1);
  assert.equal(envelope.coverage.records_unresolved, 0);
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.unparsed_records.length, 0);

  const fixture = envelope.fixtures[0];
  assert.equal(fixture.sport, 'SOCCER');
  assert.equal(fixture.region, 'England');
  assert.equal(fixture.competition, 'Premier League');
  assert.deepEqual(fixture.participants, { home: 'Arsenal', away: 'Chelsea' });
  assert.equal(fixture.status, 'PRE_MATCH');
  assert.equal(fixture.market_family, '1X2');
  assert.equal(fixture.kickoff_utc, '2024-08-17T14:00:00.000Z');
  assert.equal(fixture.kickoff_resolution, 'EXPLICIT_UTC_ATTRIBUTE');
  assert.deepEqual(fixture.offered_odds, { H: 1.95, D: 3.4, A: 4.2 });
  assert.equal(fixture.duplicate_status, 'NEW');
  assert.equal(fixture.parser_version, parser.PARSER_VERSION);
  assert.match(fixture.fixture_id, /^bxf_[0-9a-f]{16}$/);
});

test('two competitions on one page: both captured, no cross-contamination', () => {
  const { envelope } = capture('two_competitions.html');
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
  assert.equal(envelope.coverage.sections_seen, 2);
  assert.equal(envelope.fixtures.length, 2);
  const byAway = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.away, f]));
  assert.equal(byAway.Everton.competition, 'Premier League');
  assert.equal(byAway.Sevilla.competition, 'La Liga');
  assert.notEqual(byAway.Everton.fixture_id, byAway.Sevilla.fixture_id);
});

test('repeated headings: two groups with identical header text are not merged or dropped', () => {
  const { envelope } = capture('repeated_headings.html');
  assert.equal(envelope.coverage.sections_seen, 2);
  assert.equal(envelope.fixtures.length, 2);
  const fixtureIds = new Set(envelope.fixtures.map((f) => f.fixture_id));
  assert.equal(fixtureIds.size, 2, 'both fixtures under the repeated heading must be distinct records');
});

test('collapsed sections: captures what is in the DOM and flags partial coverage', () => {
  const { envelope } = capture('collapsed_sections.html');
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('COLLAPSED_SECTIONS_DETECTED'));
  assert.equal(envelope.coverage.collapsed_sections_detected, true);
  assert.equal(envelope.fixtures.length, 1, 'the one expanded fixture is still captured');
  assert.equal(envelope.coverage.sections_seen, 2, 'the collapsed section itself is still counted as seen');
});

test('lazy-loaded/partial coverage: placeholder present flags partial, does not block real records', () => {
  const { envelope } = capture('lazy_loaded_partial.html');
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('LAZY_LOADING_DETECTED'));
  assert.equal(envelope.coverage.lazy_loading_detected, true);
  assert.equal(envelope.fixtures.length, 1);
});

test('missing draw price: 1X2 market rejected from model input, retained for audit', () => {
  const { envelope } = capture('missing_draw_price.html');
  assert.equal(envelope.fixtures.length, 0, 'incomplete market must never reach fixtures[]');
  assert.equal(envelope.unparsed_records.length, 1);
  const record = envelope.unparsed_records[0];
  assert.equal(record.reason, 'INCOMPLETE_1X2_MARKET');
  assert.equal(record.expected_unsupported, false);
  assert.equal(envelope.coverage.records_unresolved, 1);
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  // Audit trail retains what WAS readable, including the home/draw/away
  // raw text, so an operator can see exactly what was suspended.
  assert.equal(record.raw.home, 'LA Galaxy');
  assert.equal(record.raw.partial_outcomes.H, 2.3);
  assert.equal(record.raw.partial_outcomes.D, null);
});

test('odds changes across two captures: duplicate_status reflects a real price change', () => {
  const before = capture('odds_changed_before.html');
  assert.equal(before.envelope.fixtures[0].duplicate_status, 'NEW');

  const after = capture('odds_changed_after.html', { previousIndex: before.updatedIndex });
  assert.equal(after.envelope.fixtures.length, 1);
  assert.equal(after.envelope.fixtures[0].duplicate_status, 'SEEN_BEFORE_ODDS_CHANGED');
  assert.equal(after.envelope.fixtures[0].fixture_id, before.envelope.fixtures[0].fixture_id, 'same fixture, recaptured by identity');
  assert.deepEqual(after.envelope.fixtures[0].offered_odds, { H: 1.9, D: 3.6, A: 3.9 });
});

test('recapturing an unchanged fixture is reported as unchanged, not new', () => {
  const first = capture('normal_soccer_1x2.html');
  const second = capture('normal_soccer_1x2.html', { previousIndex: first.updatedIndex });
  assert.equal(second.envelope.fixtures[0].duplicate_status, 'SEEN_BEFORE_UNCHANGED');
});

test('duplicate fixtures within one capture: recognized by natural key, flagged as duplicate', () => {
  const { envelope } = capture('duplicate_fixtures.html');
  assert.equal(envelope.coverage.records_seen, 2);
  assert.equal(envelope.fixtures.length, 2, 'both rows are still recorded -- duplication is flagged, not silently dropped');
  assert.equal(envelope.fixtures[0].fixture_id, envelope.fixtures[1].fixture_id);
  assert.equal(envelope.fixtures[0].duplicate_status, 'NEW');
  assert.equal(envelope.fixtures[1].duplicate_status, 'DUPLICATE_WITHIN_CAPTURE');
});

test('already-live events: excluded from fixtures[], preserved with a typed reason', () => {
  const { envelope } = capture('already_live.html');
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.unparsed_records.length, 1);
  const record = envelope.unparsed_records[0];
  assert.equal(record.reason, 'LIVE_EVENT_EXCLUDED_FROM_PREMATCH_MODEL_INPUT');
  assert.equal(record.expected_unsupported, true);
  // Expected-unsupported records are "working as intended", not unresolved.
  assert.equal(envelope.coverage.records_unresolved, 0);
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
});

test('Zoom and virtual products: distinguished from real fixtures, both excluded from fixtures[]', () => {
  const { envelope } = capture('zoom_virtual.html');
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.unparsed_records.length, 2);
  const reasons = envelope.unparsed_records.map((r) => r.reason);
  assert.ok(reasons.every((r) => r === 'VIRTUAL_OR_ZOOM_PRODUCT_EXCLUDED'));
  assert.equal(envelope.coverage.records_unresolved, 0);
});

test('accented participant names: preserved exactly, never stripped or transliterated', () => {
  const { envelope } = capture('accented_names.html');
  assert.equal(envelope.fixtures.length, 2);
  const byHome = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.home, f]));
  assert.ok('São Paulo' in byHome);
  assert.equal(byHome['São Paulo'].participants.away, 'Grêmio');
  assert.ok('Málaga' in byHome);
  assert.equal(byHome['Málaga'].participants.away, 'Almería');
  // Round-trip through JSON must not mangle the accents either.
  const roundTripped = JSON.parse(JSON.stringify(envelope));
  assert.equal(roundTripped.fixtures.find((f) => f.participants.home === 'São Paulo').participants.away, 'Grêmio');
});

test('page-layout / selector failure: fails visibly, never an empty silent success', () => {
  const { envelope } = capture('selector_failure.html');
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SELECTOR_ROOT_NOT_FOUND'));
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.unparsed_records.length, 0);
  assert.equal(envelope.coverage.records_seen, 0);
});

test('root found but zero records inside it: also fails visibly, with a distinct reason', () => {
  const { envelope } = capture('empty_root.html');
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_RECORDS_FOUND'));
});

test('every envelope always carries the required top-level shape', () => {
  for (const fixtureName of ['normal_soccer_1x2.html', 'selector_failure.html', 'empty_root.html']) {
    const { envelope } = capture(fixtureName);
    for (const key of [
      'schema_version',
      'capture_id',
      'captured_at_utc',
      'source_url',
      'page_title',
      'coverage',
      'fixtures',
      'unparsed_records',
    ]) {
      assert.ok(key in envelope, `${fixtureName}: missing top-level key "${key}"`);
    }
    for (const key of ['visible_page_only', 'sections_seen', 'records_seen', 'records_parsed', 'records_unresolved']) {
      assert.ok(key in envelope.coverage, `${fixtureName}: missing coverage key "${key}"`);
    }
    assert.equal(envelope.coverage.visible_page_only, true);
  }
});

test('an empty successful capture can never happen: CAPTURE_OK always implies real output', () => {
  for (const fixtureName of [
    'normal_soccer_1x2.html',
    'two_competitions.html',
    'repeated_headings.html',
    'accented_names.html',
  ]) {
    const { envelope } = capture(fixtureName);
    if (envelope.capture_status === 'CAPTURE_OK' || envelope.capture_status === 'CAPTURE_PARTIAL') {
      assert.ok(
        envelope.fixtures.length > 0 || envelope.unparsed_records.length > 0,
        `${fixtureName}: a non-failed capture must never be empty`
      );
    }
  }
});
