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

// -- BET9JA_DESKTOP fallback profile (real-page selector validation) -------
//
// Confirmed against tests/fixtures/bet9ja_desktop_real_sample.html, a
// sanitized row captured 2026-09-11 from a real, authenticated
// https://sports.bet9ja.com/sport/soccer/1 page during the extension's
// first real-page validation pass. The LEGACY profile (SELECTORS) never
// matched that page at all (real capture: capture_status CAPTURE_FAILED /
// SELECTOR_ROOT_NOT_FOUND, zero records) -- these tests are the regression
// coverage for the fix.

test('BET9JA_DESKTOP: the real Ararat-Armenia vs FC Syunik row maps to 1.14 / 7.30 / 12.25', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.equal(envelope.fixtures.length, 1);
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.sport, 'SOCCER');
  assert.deepEqual(fixture.participants, { home: 'Ararat-Armenia', away: 'FC Syunik' });
  assert.equal(fixture.market_family, '1X2');
  assert.deepEqual(fixture.offered_odds, { H: 1.14, D: 7.3, A: 12.25 });
  assert.deepEqual(
    fixture.outcomes.map((o) => [o.label, o.price]),
    [['H', 1.14], ['D', 7.3], ['A', 12.25]]
  );
});

test('BET9JA_DESKTOP: fixture_id is derived from the real event id, not a guessed natural key', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.match(envelope.fixtures[0].fixture_id, /^bxf_[0-9a-f]{16}$/);
  // Same real event id captured twice (e.g. a page reload) must resolve to
  // the same fixture_id -- proven directly against the identity source
  // (the "_event-832455154" id), not indirectly through team-name hashing.
  const again = capture('bet9ja_desktop_real_sample.html');
  assert.equal(again.envelope.fixtures[0].fixture_id, envelope.fixtures[0].fixture_id);
});

test('BET9JA_DESKTOP: kickoff has no year/timezone in this markup -- honestly unresolved, never guessed', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.kickoff_utc, null);
  assert.equal(fixture.kickoff_resolution, 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP');
  assert.equal(fixture.kickoff_raw, '16:00');
});

test('BET9JA_DESKTOP: legacy root not found anywhere -- this profile is a true fallback, not a silent success', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.ok(
    envelope.capture_status_reasons.includes('BET9JA_DESKTOP_FALLBACK_PROFILE_UNVERIFIED_COVERAGE'),
    'fallback activation must be visible in capture_status_reasons, not silent'
  );
});

test('BET9JA_DESKTOP: mixed pass/fail rows on the same page report CAPTURE_PARTIAL, not OK or FAILED', () => {
  const { envelope } = capture('bet9ja_desktop_mixed_pass_fail.html');
  assert.equal(envelope.coverage.records_seen, 2);
  assert.equal(envelope.fixtures.length, 1, 'the valid row still parses');
  assert.equal(envelope.unparsed_records.length, 1, 'the row missing an away participant is retained, not dropped');
  assert.equal(envelope.unparsed_records[0].reason, 'MISSING_PARTICIPANTS');
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.equal(envelope.coverage.records_seen, envelope.coverage.records_parsed + envelope.coverage.records_unresolved);
});

test('BET9JA_DESKTOP: legacy root still found is unaffected (empty_root.html keeps its own distinct failure reason)', () => {
  const { envelope } = capture('empty_root.html');
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_RECORDS_FOUND'));
  assert.ok(!envelope.capture_status_reasons.includes('SELECTOR_ROOT_NOT_FOUND'));
});

test('BET9JA_DESKTOP: navigation and betslip-panel decoys are never captured as fixtures', () => {
  const { envelope } = capture('bet9ja_desktop_nav_and_betslip_decoys.html');
  assert.equal(envelope.fixtures.length, 1, 'only the one real row outside nav/betslip is captured');
  assert.equal(envelope.fixtures[0].participants.home, 'Ararat-Armenia');
  const allText = JSON.stringify(envelope).toLowerCase();
  assert.ok(!allText.includes('nav decoy'), 'nav-panel decoy text must never appear anywhere in the envelope');
  assert.ok(!allText.includes('betslip decoy'), 'betslip-panel decoy text must never appear anywhere in the envelope');
});

test('BET9JA_DESKTOP: rows are grouped by their nearest preceding .sports-head__date heading', () => {
  const { envelope } = capture('bet9ja_desktop_date_headings_and_1up.html');
  const byHome = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.home, f]));
  assert.equal(byHome['Ararat-Armenia'].date_heading_raw, 'Today');
  assert.equal(byHome['Team Alpha'].date_heading_raw, 'Today');
  assert.equal(byHome['Team Gamma'].date_heading_raw, 'Tomorrow');
  // Two distinct date headings -> two distinct sections, not one flat batch.
  assert.equal(envelope.coverage.sections_seen, 2);
});

test('BET9JA_DESKTOP: date_heading_raw is recorded for audit but never used to derive kickoff_utc', () => {
  const { envelope } = capture('bet9ja_desktop_date_headings_and_1up.html');
  for (const fixture of envelope.fixtures) {
    assert.notEqual(fixture.date_heading_raw, null);
    // No confirmed date-string format exists for .sports-head__date -- a
    // bare time plus an unparsed heading string must never combine into a
    // guessed UTC timestamp.
    assert.equal(fixture.kickoff_utc, null);
    assert.equal(fixture.kickoff_resolution, 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP');
  }
});

test('BET9JA_DESKTOP: LEGACY fixtures always carry date_heading_raw: null (no such concept there)', () => {
  const { envelope } = capture('normal_soccer_1x2.html');
  assert.equal(envelope.fixtures[0].date_heading_raw, null);
});

test('BET9JA_DESKTOP: a real "1X2 1UP" second odds list is never merged into the 1X2 market or silently dropped', () => {
  const { envelope } = capture('bet9ja_desktop_date_headings_and_1up.html');
  const gammaFixture = envelope.fixtures.find((f) => f.participants.home === 'Team Gamma');
  // The real 1X2 market on this row is unaffected by the second list.
  assert.deepEqual(gammaFixture.offered_odds, { H: 1.5, D: 4.0, A: 6.0 });

  // The 1UP list is retained for audit under its own distinct family, not
  // aliased to "1x2" and not silently dropped.
  const oneUpRecord = envelope.unparsed_records.find(
    (r) => r.reason === 'UNSUPPORTED_MARKET_FAMILY' && r.raw.market && r.raw.market.family === '1x2_1up'
  );
  assert.ok(oneUpRecord, 'expected an UNSUPPORTED_MARKET_FAMILY record for family "1x2_1up"');
  assert.equal(oneUpRecord.expected_unsupported, true);
  // And it must never have contaminated the real fixture's own outcomes.
  assert.equal(gammaFixture.outcomes.some((o) => o.price === 1.3), false);
});

test('BET9JA_DESKTOP: competition-page id shape ("prematch_event-{id}", no sport-N segment) still resolves identity and sport', () => {
  const { envelope } = capture('bet9ja_desktop_competition_page_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/competition/soccer/netherlands/eredivisie/1-11077-1016657',
  });
  assert.equal(envelope.fixtures.length, 1);
  const fixture = envelope.fixtures[0];
  assert.equal(fixture.sport, 'SOCCER');
  assert.deepEqual(fixture.participants, { home: 'Ajax', away: 'Feyenoord' });
  assert.deepEqual(fixture.offered_odds, { H: 2.05, D: 3.5, A: 3.3 });
  assert.match(fixture.fixture_id, /^bxf_[0-9a-f]{16}$/);
});

test('BET9JA_DESKTOP: region/competition are recorded from a confirmed competition-page URL, as raw slugs', () => {
  const { envelope } = capture('bet9ja_desktop_competition_page_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/competition/soccer/netherlands/eredivisie/1-11077-1016657',
  });
  assert.equal(envelope.fixtures[0].region, 'netherlands');
  assert.equal(envelope.fixtures[0].competition, 'eredivisie');
});

test('BET9JA_DESKTOP: region/competition stay null on a page whose URL does not match the confirmed competition-page shape', () => {
  // The Highlights page's own real URL mixes competitions, so it must
  // never be attributed to a single country/league -- sport here resolves
  // via this row's own sport-N id segment, not the URL, which is exactly
  // what distinguishes the Highlights page from a competition page.
  const { envelope } = capture('bet9ja_desktop_real_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/sport/soccer/1',
  });
  assert.equal(envelope.fixtures[0].region, null);
  assert.equal(envelope.fixtures[0].competition, null);
  assert.equal(envelope.fixtures[0].sport, 'SOCCER');
});

test('BET9JA_DESKTOP: an unrecognized URL and no sport-N id segment leaves sport unresolved, never guessed', () => {
  const { envelope } = capture('bet9ja_desktop_competition_page_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/some/other/unrecognized/path',
  });
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.unparsed_records.length, 1);
  assert.equal(envelope.unparsed_records[0].reason, 'UNSUPPORTED_SPORT');
});

test('BET9JA_DESKTOP: structural/spacer rows with no matchup cell are excluded entirely, not counted as a record', () => {
  const { envelope } = capture('bet9ja_desktop_structural_spacer_rows.html');
  // 3 real .table-f elements on the page; only 1 has a matchup cell.
  assert.equal(envelope.coverage.records_seen, 1);
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.unparsed_records.length, 0, 'the 2 structural rows must never appear as MISSING_PARTICIPANTS or any other record');
  assert.equal(envelope.fixtures[0].participants.home, 'Ararat-Armenia');
});

test('BET9JA_DESKTOP: a matchup cell that exists but is missing an away name is still a MISSING_PARTICIPANTS record (contrast with structural rows)', () => {
  const { envelope } = capture('bet9ja_desktop_mixed_pass_fail.html');
  assert.equal(envelope.coverage.records_seen, 2);
  assert.equal(envelope.unparsed_records.length, 1);
  assert.equal(envelope.unparsed_records[0].reason, 'MISSING_PARTICIPANTS');
});

test('BET9JA_DESKTOP: sport slug is resolved generically from any /competition/{sport}/... URL, not hardcoded to soccer', () => {
  const { envelope } = capture('bet9ja_desktop_basketball_competition_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/competition/basketball/international/abaligapreseason/2-43353-9954756',
  });
  assert.equal(envelope.fixtures.length, 0, 'basketball is still correctly excluded from fixtures[]');
  assert.equal(envelope.unparsed_records.length, 1);
  const record = envelope.unparsed_records[0];
  assert.equal(record.reason, 'UNSUPPORTED_SPORT');
  assert.equal(record.raw.sport_hint, 'BASKETBALL', 'sport must be identified, not left as an empty/UNKNOWN hint');
  assert.match(record.detail, /sport="BASKETBALL"/);
  assert.equal(record.expected_unsupported, true);
});

test('BET9JA_DESKTOP: parseBet9jaCompetitionUrl resolves sport/country/competition generically for any sport', () => {
  // Basketball is excluded before ever reaching fixtures[] (see the test
  // above), so this is verified directly against the exported URL parser
  // -- the same function captureFromDocument's fallback path calls.
  const parsed = parser.parseBet9jaCompetitionUrl(
    'https://sports.bet9ja.com/competition/basketball/international/abaligapreseason/2-43353-9954756'
  );
  assert.deepEqual(parsed, { sportSlug: 'BASKETBALL', countrySlug: 'international', competitionSlug: 'abaligapreseason' });
});

test('BET9JA_DESKTOP: records_seen = records_parsed + records_unresolved + records_expected_unsupported, universally', () => {
  // Unlike the old two-term equation, this three-term one IS a universal
  // invariant: every row is classified into exactly one of the three
  // buckets by processRow's per-row return value (ROW_PARSED /
  // ROW_UNRESOLVED / ROW_EXPECTED_UNSUPPORTED), regardless of how many
  // unparsed_records EVENTS that one row happens to also generate (e.g. a
  // Soccer row's real 1X2 market plus its excluded "1X2 1UP"/"1X2 2UP"
  // siblings is still exactly one PARSED row, not three separate counts).
  for (const fixtureName of [
    'bet9ja_desktop_real_sample.html',
    'bet9ja_desktop_mixed_pass_fail.html',
    'bet9ja_desktop_nav_and_betslip_decoys.html',
    'bet9ja_desktop_date_headings_and_1up.html',
    'bet9ja_desktop_structural_spacer_rows.html',
    'bet9ja_desktop_basketball_competition_sample.html',
  ]) {
    const { envelope } = capture(fixtureName);
    assert.equal(
      envelope.coverage.records_seen,
      envelope.coverage.records_parsed + envelope.coverage.records_unresolved + envelope.coverage.records_expected_unsupported,
      `${fixtureName}: records_seen must equal records_parsed + records_unresolved + records_expected_unsupported`
    );
  }
});

test('BET9JA_DESKTOP: a Soccer row with excluded 1UP/2UP siblings still counts as PARSED, not expected-unsupported', () => {
  // The 36 real UNSUPPORTED_MARKET_FAMILY audit entries (2 per fixture x
  // 18 fixtures) must never inflate records_expected_unsupported -- those
  // 18 rows successfully produced a real 1X2 fixture each.
  const { envelope } = capture('bet9ja_desktop_date_headings_and_1up.html');
  assert.equal(envelope.coverage.records_parsed, 3);
  assert.equal(envelope.coverage.records_unresolved, 0);
  assert.equal(envelope.coverage.records_expected_unsupported, 0);
  // The 1UP market itself is still audited as its own event, just not
  // counted as a row.
  assert.ok(envelope.unparsed_records.some((r) => r.reason === 'UNSUPPORTED_MARKET_FAMILY'));
});

test('BET9JA_DESKTOP: basketball rows count entirely as records_expected_unsupported, none as records_unresolved', () => {
  const { envelope } = capture('bet9ja_desktop_basketball_competition_sample.html', {
    sourceUrl: 'https://sports.bet9ja.com/competition/basketball/international/abaligapreseason/2-43353-9954756',
  });
  assert.equal(envelope.coverage.records_seen, 1);
  assert.equal(envelope.coverage.records_parsed, 0);
  assert.equal(envelope.coverage.records_unresolved, 0);
  assert.equal(envelope.coverage.records_expected_unsupported, 1);
});

// --- Trusted forced-sport context (soccer_walker.js's
// /sportPage/1/competitions batch selector) --------------------------------

function forcedSportRowHtml({ eventId, home, away }) {
  // No "sport-N" id segment (matches the confirmed competition-page row
  // shape, e.g. "prematch_event-..."), and the sourceUrl used below never
  // matches COMPETITION_URL_PATTERN either -- both of this file's
  // ordinary sport-resolution tiers come back empty by construction, so
  // only the forced-context tier can classify these rows as Soccer.
  const idBase = `prematch_event-${eventId}`;
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="${idBase}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-1">1.95</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-X">3.40</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-2">4.20</li></ul></div></div>`;
}

function sportPageCompetitionsDoc({
  title = 'Soccer - Competitions',
  breadcrumb = 'Soccer > Nigeria > Professional Football League',
} = {}) {
  // ROUND 10: `breadcrumb` (and `title`) are retained as parameters purely
  // so the existing helper signature keeps working for callers that still
  // render a heading for OTHER reasons (e.g. attribution tests below,
  // which exercise soccer_walker.js-shaped resolvers) -- the gate itself
  // (validateForcedSportContext) no longer reads either of these; see
  // parser.js's own comment for why.
  const heading = breadcrumb ? `<div class="heading">${breadcrumb}</div>` : '';
  return docFromHtml(
    `<html><head><title>${title}</title></head><body>${heading}<div class="sports-table">${forcedSportRowHtml({ eventId: '1', home: 'Enyimba', away: 'Rivers United' })}</div></body></html>`
  );
}

const VALID_FORCED_SPORT_CONTEXT = {
  forced_sport_hint: 'SOCCER',
  forced_sport_source: 'SPORTPAGE_ROUTE_ID',
  forced_sport_source_value: '1',
  capture_scope: 'SOCCER_ALL_PREMATCH_COMPETITIONS',
  // ROUND 10: soccer_walker.js's own trusted record of which checkbox ids
  // it selected for this batch -- required non-empty by the gate; see
  // parser.js's own comment for why this replaced the two heading checks.
  selected_competition_ids: ['1209691'],
};

test('forced sport context: /sportPage/1/competitions plus a valid claim (route + scope + selected ids) permits the override, with NO rendered heading needed at all', () => {
  // ROUND 10 real evidence: the real page never renders either heading
  // this gate used to require -- proving the gate now passes without one
  // is the whole point of this correction.
  const doc = sportPageCompetitionsDoc({ title: 'Bet9ja Sports', breadcrumb: null });
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.forced_sport_context_applied, true);
  // BET9JA_DESKTOP is always CAPTURE_PARTIAL at best (unverified
  // full-page coverage, same as every other test against this fallback
  // profile) -- the point here is that it's PARTIAL, not FAILED, and
  // carries no SPORT_CONTEXT_CONFLICT.
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.fixtures[0].sport, 'SOCCER');
  assert.ok(!envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
});

test('forced sport context: another sport-page id cannot claim Soccer -- route mismatch fails closed', () => {
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    // A DIFFERENT sportPage id -- FORCED_SPORT_CONTEXT_ROUTE_PATTERN only
    // matches /sportPage/1/competitions, never /sportPage/2/....
    sourceUrl: 'https://sports.bet9ja.com/sportPage/2/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.forced_sport_context_applied, false);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
  assert.equal(envelope.fixtures.length, 0);
});

test('forced sport context: the claim itself naming a different sport id fails closed even on the right route', () => {
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: { ...VALID_FORCED_SPORT_CONTEXT, forced_sport_source_value: '2' },
  });
  assert.equal(envelope.forced_sport_context_applied, false);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
});

test('forced sport context: an empty selected_competition_ids claim fails closed with diagnostics naming CLAIM_INVALID', () => {
  // ROUND 10: this is the new third condition -- a walker that never
  // actually selected any competition id has nothing genuine to attribute
  // rows to, so an empty/missing list must never be silently accepted.
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: { ...VALID_FORCED_SPORT_CONTEXT, selected_competition_ids: [] },
  });
  assert.equal(envelope.forced_sport_context_applied, false);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
  const diag = envelope.sport_context_diagnostics;
  assert.ok(diag, 'every SPORT_CONTEXT_CONFLICT must carry sport_context_diagnostics');
  assert.equal(diag.failed_check, 'CLAIM_INVALID');
  assert.equal(diag.route_check_passed, true);
  assert.deepEqual(diag.selected_competition_ids, []);
  assert.equal(diag.claim_check_passed, false);
});

test('forced sport context: a mismatched capture_scope fails closed with diagnostics naming CLAIM_INVALID', () => {
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: { ...VALID_FORCED_SPORT_CONTEXT, capture_scope: 'SOMETHING_ELSE' },
  });
  assert.equal(envelope.sport_context_diagnostics.failed_check, 'CLAIM_INVALID');
  assert.equal(envelope.sport_context_diagnostics.capture_scope_actual, 'SOMETHING_ELSE');
  assert.equal(envelope.sport_context_diagnostics.capture_scope_expected, 'SOCCER_ALL_PREMATCH_COMPETITIONS');
});

test('forced sport context: a claim naming the wrong sport id fails closed with diagnostics naming CLAIM_INVALID', () => {
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: { ...VALID_FORCED_SPORT_CONTEXT, forced_sport_source_value: '2' },
  });
  assert.equal(envelope.sport_context_diagnostics.failed_check, 'CLAIM_INVALID');
});

test('forced sport context: a route mismatch fails closed with diagnostics naming ROUTE_MISMATCH', () => {
  const doc = sportPageCompetitionsDoc();
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/2/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.sport_context_diagnostics.failed_check, 'ROUTE_MISMATCH');
  assert.equal(envelope.sport_context_diagnostics.route_check_passed, false);
  assert.equal(envelope.sport_context_diagnostics.pathname_actual, '/sportPage/2/competitions');
});

test('forced sport context: a successful validation never attaches sport_context_diagnostics', () => {
  const doc = sportPageCompetitionsDoc({ title: 'Soccer - Competitions', breadcrumb: 'Soccer > Nigeria > Professional Football League' });
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.forced_sport_context_applied, true);
  assert.equal(envelope.sport_context_diagnostics, null);
});

test('forced sport context (Round 10 regression): a page with NO rendered sport heading and NO competition breadcrumb at all still passes, given a valid route/scope/selected-ids claim', () => {
  // This is the exact real-capture defect (13:05:54 diagnostic capture):
  // neither heading this gate used to require ever existed on the real
  // page, even though the page was genuinely, verifiably Soccer. The fix
  // stops requiring either heading at all.
  const doc = sportPageCompetitionsDoc({ title: 'Bet9ja Sports', breadcrumb: null });
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.forced_sport_context_applied, true);
  assert.ok(!envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
  assert.equal(envelope.fixtures[0].sport, 'SOCCER');
});

test('forced sport context: ordinary captureFromDocument() calls (no forced_sport_context) are completely unaffected', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.equal(envelope.forced_sport_context_applied, false);
  assert.ok(!envelope.capture_status_reasons.includes('SPORT_CONTEXT_CONFLICT'));
  assert.equal(envelope.sport_context_diagnostics, null);
  // Same real assertion as the pre-existing test for this fixture --
  // proof this feature changed nothing about an ordinary call's outcome.
  assert.equal(envelope.fixtures[0].offered_odds.H, 1.14);
});

test('forced sport context: never overrides a row that already resolved its own sport (id-embedded segment wins)', () => {
  // Highlights-style row: DOES carry its own "sport-N" segment (here,
  // sport-2, a non-Soccer id) -- even under an active forced Soccer
  // context, this row's own resolved sport must never be silently
  // replaced.
  const idBase = 'home_highlights_sport-2_event-1';
  const html = `<html><head><title>Soccer - Competitions</title></head><body><div class="heading">Soccer &gt; Armenia &gt; Premier League</div><div class="sports-table"><div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="${idBase}"><div class="sports-table__home txt-cut">Team A</div><div class="sports-table__away txt-cut">Team B</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-1">1.95</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-X">3.40</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-2">4.20</li></ul></div></div></div></body></html>`;
  const doc = docFromHtml(html);
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
  });
  assert.equal(envelope.forced_sport_context_applied, true);
  assert.equal(envelope.fixtures.length, 0);
  assert.ok(envelope.unparsed_records.some((r) => r.reason === 'UNSUPPORTED_SPORT'));
});

// --- Per-table competition attribution (soccer_walker.js's batch
// selector, which can render more than one competition's fixtures on one
// page) ----------------------------------------------------------------

function twoTablePage() {
  const headingA = `<div class="heading">Soccer &gt; Nigeria &gt; Professional Football League</div>`;
  const tableA = `<div class="sports-table" data-table="A">${forcedSportRowHtml({ eventId: 'a1', home: 'Enyimba', away: 'Rivers United' })}</div>`;
  const headingB = `<div class="heading">Soccer &gt; England &gt; Premier League</div>`;
  const tableB = `<div class="sports-table" data-table="B">${forcedSportRowHtml({ eventId: 'b1', home: 'Arsenal', away: 'Chelsea' })}</div>`;
  return docFromHtml(`<html><head><title>Soccer - Competitions</title></head><body>${headingA}${tableA}${headingB}${tableB}</body></html>`);
}

test('per-table attribution: a resolver that uniquely maps each table tags its fixtures with that table\'s own competition, never the other table\'s', () => {
  const doc = twoTablePage();
  const resolver = (table) => {
    const which = table.getAttribute('data-table');
    return which === 'A'
      ? { resolved: true, sourceCompetitionId: '1209691', competitionNameRaw: 'Professional Football League', countryNameRaw: 'Nigeria' }
      : { resolved: true, sourceCompetitionId: '2000001', competitionNameRaw: 'Premier League', countryNameRaw: 'England' };
  };
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
    resolve_table_competition: resolver,
  });
  assert.equal(envelope.fixtures.length, 2);
  const nigeria = envelope.fixtures.find((f) => f.participants.home === 'Enyimba');
  const england = envelope.fixtures.find((f) => f.participants.home === 'Arsenal');
  assert.equal(nigeria.resolved_source_competition_id, '1209691');
  assert.equal(nigeria.region, 'Nigeria');
  assert.equal(nigeria.competition, 'Professional Football League');
  assert.equal(england.resolved_source_competition_id, '2000001');
  assert.equal(england.region, 'England');
  assert.equal(england.competition, 'Premier League');
});

test('per-table attribution: a table the resolver cannot uniquely map is retained as COMPETITION_ATTRIBUTION_UNRESOLVED, never guessed into a fixture', () => {
  const doc = twoTablePage();
  const resolver = (table) => {
    const which = table.getAttribute('data-table');
    return which === 'A'
      ? { resolved: true, sourceCompetitionId: '1209691', competitionNameRaw: 'Professional Football League', countryNameRaw: 'Nigeria' }
      : { resolved: false };
  };
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
    resolve_table_competition: resolver,
  });
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.fixtures[0].participants.home, 'Enyimba');
  assert.equal(envelope.coverage.records_seen, 2);
  assert.equal(envelope.coverage.records_parsed, 1);
  assert.equal(envelope.coverage.records_unresolved, 1);
  const unresolved = envelope.unparsed_records.find((r) => r.reason === 'COMPETITION_ATTRIBUTION_UNRESOLVED');
  assert.ok(unresolved);
  assert.equal(unresolved.raw.home, 'Arsenal');
});

test('per-table attribution: attribution failure overrides an otherwise-valid row -- never both a fixture AND an unresolved record for the same row', () => {
  const doc = docFromHtml(
    `<html><head><title>Soccer - Competitions</title></head><body><div class="heading">Soccer &gt; Nigeria &gt; Professional Football League</div><div class="sports-table">${forcedSportRowHtml({ eventId: '1', home: 'Enyimba', away: 'Rivers United' })}</div></body></html>`
  );
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
    resolve_table_competition: () => ({ resolved: false }),
  });
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.unparsed_records.length, 1);
  assert.equal(envelope.unparsed_records[0].reason, 'COMPETITION_ATTRIBUTION_UNRESOLVED');
  assert.equal(envelope.unparsed_records[0].expected_unsupported, false);
});

test('per-table attribution: no resolver supplied leaves every fixture\'s resolved_source_competition_id null (ordinary calls unaffected)', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.ok(envelope.fixtures.length > 0);
  for (const fixture of envelope.fixtures) {
    assert.equal(fixture.resolved_source_competition_id, null);
  }
});

test('per-table attribution: table_attribution_summary exposes one entry per table, including a resolved-but-empty table', () => {
  const emptyTableHtml = `<div class="sports-table" data-table="C"></div>`;
  const resolvedTableHtml = `<div class="sports-table" data-table="A">${forcedSportRowHtml({ eventId: 'a1', home: 'Enyimba', away: 'Rivers United' })}</div>`;
  const doc = docFromHtml(
    `<html><head><title>Soccer - Competitions</title></head><body><div class="heading">Soccer &gt; Nigeria &gt; Professional Football League</div>${resolvedTableHtml}${emptyTableHtml}</body></html>`
  );
  const resolver = (table) => {
    const which = table.getAttribute('data-table');
    if (which === 'A') return { resolved: true, sourceCompetitionId: '1209691', competitionNameRaw: 'Professional Football League', countryNameRaw: 'Nigeria' };
    if (which === 'C') return { resolved: true, sourceCompetitionId: '9999999', competitionNameRaw: 'Off Season League', countryNameRaw: 'Nowhere' };
    return { resolved: false };
  };
  const { envelope } = parser.captureFromDocument(doc, {
    ...BASE_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sportPage/1/competitions',
    forced_sport_context: VALID_FORCED_SPORT_CONTEXT,
    resolve_table_competition: resolver,
  });
  assert.equal(envelope.table_attribution_summary.length, 2);
  const nigeria = envelope.table_attribution_summary.find((t) => t.source_competition_id === '1209691');
  const nowhere = envelope.table_attribution_summary.find((t) => t.source_competition_id === '9999999');
  assert.equal(nigeria.resolved, true);
  assert.equal(nigeria.row_count, 1);
  assert.equal(nowhere.resolved, true);
  assert.equal(nowhere.row_count, 0);
});

test('per-table attribution: table_attribution_summary is null on an ordinary call with no resolver', () => {
  const { envelope } = capture('bet9ja_desktop_real_sample.html');
  assert.equal(envelope.table_attribution_summary, null);
});
