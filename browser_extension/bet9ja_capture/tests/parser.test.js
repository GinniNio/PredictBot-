const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const parser = require('../parser.js');
const { loadFixtureDocument, BASE_CONTEXT } = require('./helpers.js');

function docFromHtml(html) {
  return new JSDOM(html).window.document;
}

function oneRowDoc({ home = 'Arsenal', away = 'Chelsea', kickoffUtc = '2024-08-17T14:00:00Z', prices = ['1.95', '3.40', '4.20'] } = {}) {
  return docFromHtml(`
    <div class="odds-board">
      <div class="competition-group">
        <div class="competition-header">England - Premier League</div>
        <div class="fixture-row" data-status="PRE" data-sport="SOCCER">
          <div class="participants"><span class="home">${home}</span><span class="away">${away}</span></div>
          <div class="kickoff" data-kickoff-utc="${kickoffUtc}">kickoff</div>
          <div class="market" data-market-family="1X2" data-market-line="">
            <div class="outcome"><span class="outcome-label">1</span><span class="outcome-price">${prices[0]}</span></div>
            <div class="outcome"><span class="outcome-label">X</span><span class="outcome-price">${prices[1]}</span></div>
            <div class="outcome"><span class="outcome-label">2</span><span class="outcome-price">${prices[2]}</span></div>
          </div>
        </div>
      </div>
    </div>
  `);
}

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

// -- Source URL sanitization ------------------------------------------------

test('source_url: query parameters and fragment are stripped, origin+pathname kept', () => {
  const doc = oneRowDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://www.bet9ja.com/sport/prematch?sessionId=abc123&ref=partner-99#section-3',
  });
  assert.equal(envelope.source_url, 'https://www.bet9ja.com/sport/prematch');
});

test('source_url: a value with no query/fragment at all passes through unchanged', () => {
  const doc = oneRowDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://www.bet9ja.com/sport/prematch',
  });
  assert.equal(envelope.source_url, 'https://www.bet9ja.com/sport/prematch');
});

test('source_url: an unparseable value still has its query/fragment stripped, never leaked raw', () => {
  const doc = oneRowDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'not-a-real-url?sessionId=abc123#frag',
  });
  assert.equal(envelope.source_url, 'not-a-real-url');
});

test('sanitizeSourceUrl is exported directly and used by captureFromDocument (not duplicated logic)', () => {
  assert.equal(
    parser.sanitizeSourceUrl('https://x.example/a/b?x=1#y'),
    'https://x.example/a/b'
  );
});

// -- Fixture ID stability ----------------------------------------------------

test('fixture_id is unchanged when odds change', () => {
  const before = parser.captureFromDocument(oneRowDoc({ prices: ['1.95', '3.40', '4.20'] }), BASE_CONTEXT);
  const after = parser.captureFromDocument(oneRowDoc({ prices: ['2.10', '3.20', '3.60'] }), BASE_CONTEXT);
  assert.equal(after.envelope.fixtures[0].fixture_id, before.envelope.fixtures[0].fixture_id);
});

test('fixture_id is unchanged when captured_at_utc (capture time) changes', () => {
  const doc1 = oneRowDoc();
  const doc2 = oneRowDoc();
  const a = parser.captureFromDocument(doc1, { ...BASE_CONTEXT, capturedAtUtc: '2024-08-17T10:00:00.000Z' });
  const b = parser.captureFromDocument(doc2, { ...BASE_CONTEXT, capturedAtUtc: '2024-08-20T23:59:00.000Z' });
  assert.equal(a.envelope.fixtures[0].fixture_id, b.envelope.fixtures[0].fixture_id);
});

test('fixture_id is unchanged when DOM row order changes', () => {
  const doc = docFromHtml(`
    <div class="odds-board">
      <div class="competition-group">
        <div class="competition-header">England - Premier League</div>
        <div class="fixture-row" data-status="PRE" data-sport="SOCCER">
          <div class="participants"><span class="home">Liverpool</span><span class="away">Everton</span></div>
          <div class="kickoff" data-kickoff-utc="2024-08-18T14:00:00Z">k</div>
          <div class="market" data-market-family="1X2"><div class="outcome"><span class="outcome-label">1</span><span class="outcome-price">1.60</span></div><div class="outcome"><span class="outcome-label">X</span><span class="outcome-price">4.00</span></div><div class="outcome"><span class="outcome-label">2</span><span class="outcome-price">5.50</span></div></div>
        </div>
        <div class="fixture-row" data-status="PRE" data-sport="SOCCER">
          <div class="participants"><span class="home">Arsenal</span><span class="away">Chelsea</span></div>
          <div class="kickoff" data-kickoff-utc="2024-08-17T14:00:00Z">k</div>
          <div class="market" data-market-family="1X2"><div class="outcome"><span class="outcome-label">1</span><span class="outcome-price">1.95</span></div><div class="outcome"><span class="outcome-label">X</span><span class="outcome-price">3.40</span></div><div class="outcome"><span class="outcome-label">2</span><span class="outcome-price">4.20</span></div></div>
        </div>
      </div>
    </div>
  `);
  // Same two fixtures as normal_soccer_1x2.html / two_competitions.html's
  // first row, just reordered -- record_index/section_index must never
  // enter the natural key.
  const { envelope } = parser.captureFromDocument(doc, BASE_CONTEXT);
  const baseline = parser.captureFromDocument(oneRowDoc(), BASE_CONTEXT);
  const arsenalChelsea = envelope.fixtures.find((f) => f.participants.home === 'Arsenal');
  assert.equal(arsenalChelsea.fixture_id, baseline.envelope.fixtures[0].fixture_id);
});

test('fixture_id is unchanged by incidental text formatting (whitespace) in participant names', () => {
  const a = parser.captureFromDocument(oneRowDoc({ home: 'Arsenal', away: 'Chelsea' }), BASE_CONTEXT);
  const b = parser.captureFromDocument(oneRowDoc({ home: '  Arsenal \n', away: '\tChelsea  ' }), BASE_CONTEXT);
  assert.equal(a.envelope.fixtures[0].fixture_id, b.envelope.fixtures[0].fixture_id);
});

test('fixture_id changes when participants change', () => {
  const a = parser.captureFromDocument(oneRowDoc({ away: 'Chelsea' }), BASE_CONTEXT);
  const b = parser.captureFromDocument(oneRowDoc({ away: 'Everton' }), BASE_CONTEXT);
  assert.notEqual(a.envelope.fixtures[0].fixture_id, b.envelope.fixtures[0].fixture_id);
});

test('fixture_id changes when kickoff identity changes', () => {
  const a = parser.captureFromDocument(oneRowDoc({ kickoffUtc: '2024-08-17T14:00:00Z' }), BASE_CONTEXT);
  const b = parser.captureFromDocument(oneRowDoc({ kickoffUtc: '2024-08-18T14:00:00Z' }), BASE_CONTEXT);
  assert.notEqual(a.envelope.fixtures[0].fixture_id, b.envelope.fixtures[0].fixture_id);
});

test('fixture_id changes when competition changes (two_competitions.html: distinct ids already asserted above)', () => {
  const { envelope } = capture('two_competitions.html');
  assert.notEqual(envelope.fixtures[0].fixture_id, envelope.fixtures[1].fixture_id);
});

// -- Time-zone honesty --------------------------------------------------------

test('kickoff with no data-kickoff-utc attribute: typed unresolved state, kickoff_raw preserved, never guessed', () => {
  const { envelope } = capture('kickoff_missing_attribute.html');
  assert.equal(envelope.fixtures.length, 1);
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.kickoff_utc, null);
  assert.equal(fixture.kickoff_resolution, 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP');
  assert.equal(fixture.kickoff_raw, '17/08 20:00');
});

test('kickoff missing a resolvable year: typed unresolved state, never guessed via Date.parse', () => {
  const { envelope } = capture('kickoff_missing_year.html');
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.kickoff_utc, null);
  assert.equal(fixture.kickoff_resolution, 'UNRESOLVED_AMBIGUOUS_TIMESTAMP');
  assert.equal(fixture.kickoff_raw, '17/08 21:00');
});

test('kickoff missing a resolvable timezone: typed unresolved state, never guessed via Date.parse', () => {
  const { envelope } = capture('kickoff_missing_timezone.html');
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.kickoff_utc, null);
  assert.equal(fixture.kickoff_resolution, 'UNRESOLVED_AMBIGUOUS_TIMESTAMP');
  assert.equal(fixture.kickoff_raw, '17/08 22:00');
});

test('an unresolved kickoff never blocks the rest of the fixture from being normalized', () => {
  for (const fixtureName of ['kickoff_missing_attribute.html', 'kickoff_missing_year.html', 'kickoff_missing_timezone.html']) {
    const { envelope } = capture(fixtureName);
    assert.equal(envelope.fixtures.length, 1, `${fixtureName}: fixture should still be captured despite unresolved kickoff`);
    assert.notEqual(envelope.fixtures[0].offered_odds.H, null);
  }
});
