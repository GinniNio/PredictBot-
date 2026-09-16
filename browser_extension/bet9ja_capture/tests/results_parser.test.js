const { test } = require('node:test');
const assert = require('node:assert/strict');
const parser = require('../results_parser.js');
const { loadFixtureDocument, BASE_CONTEXT } = require('./helpers.js');

function capture(fixtureName, extraContext) {
  const doc = loadFixtureDocument(fixtureName);
  return parser.captureFromDocument(doc, { ...BASE_CONTEXT, ...extraContext });
}

test('real Serie A results page: three completed fixtures fully parsed', () => {
  const { envelope } = capture('results_serie_a_2026_09_14.html');
  assert.equal(envelope.schema_version, 'bet9ja-soccer-results.v1');
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
  assert.equal(envelope.page_timezone, 'GMT+01:00');
  assert.deepEqual(envelope.competitions_seen, ['Italy Serie A']);
  assert.equal(envelope.coverage.groups_seen, 1);
  assert.equal(envelope.coverage.results_seen, 3);
  assert.equal(envelope.coverage.results_parsed, 3);
  assert.equal(envelope.coverage.results_unresolved, 0);
  assert.equal(envelope.results.length, 3);
  assert.equal(envelope.unresolved_results.length, 0);

  const torinoRoma = envelope.results.find((r) => r.bet9ja_result_id === '2592');
  assert.ok(torinoRoma, 'Torino - Roma row should be present');
  assert.equal(torinoRoma.competition_raw, 'Italy Serie A');
  assert.equal(torinoRoma.fixture_raw, 'Torino - Roma');
  assert.equal(torinoRoma.start_raw, '14/09/2026 17:30');
  assert.equal(torinoRoma.full_time_score_raw, '0 - 2');
  assert.equal(torinoRoma.half_time_score_raw, '0 - 1');

  const comoParma = envelope.results.find((r) => r.bet9ja_result_id === '1925');
  assert.equal(comoParma.fixture_raw, 'Como - Parma');
  assert.equal(comoParma.full_time_score_raw, '2 - 1');
  assert.equal(comoParma.half_time_score_raw, '1 - 0');

  const interUdinese = envelope.results.find((r) => r.bet9ja_result_id === '1943');
  assert.equal(interUdinese.fixture_raw, 'Inter - Udinese');
  assert.equal(interUdinese.start_raw, '14/09/2026 19:45');
  assert.equal(interUdinese.full_time_score_raw, '5 - 3');
  assert.equal(interUdinese.half_time_score_raw, '2 - 2');

  // Unconfirmed selectors -- see this module's own header comment --
  // must stay explicitly null, never guessed.
  assert.equal(envelope.date_range, null);
});

test('a row missing its Bet9ja result id is routed to unresolved_results, never dropped silently', () => {
  const doc = loadFixtureDocument('results_serie_a_2026_09_14.html');
  const idCell = doc.querySelector('td.RisultatiIDSottoEvento');
  idCell.textContent = '   ';

  const { envelope } = parser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.equal(envelope.coverage.results_seen, 3);
  assert.equal(envelope.coverage.results_parsed, 2);
  assert.equal(envelope.coverage.results_unresolved, 1);
  assert.equal(envelope.unresolved_results[0].reason, 'RESULTS_ROW_MISSING_CORE_FIELD');
});

test('no results groups on the page is a clean CAPTURE_FAILED, never a crash', () => {
  const { envelope } = capture('results_serie_a_2026_09_14.html');
  assert.ok(envelope); // sanity: the happy path above already exercises the real fixture

  const { JSDOM } = require('jsdom');
  const emptyDoc = new JSDOM('<!doctype html><html><body></body></html>').window.document;
  const result = parser.captureFromDocument(emptyDoc, BASE_CONTEXT);
  assert.equal(result.envelope.capture_status, 'CAPTURE_FAILED');
  assert.deepEqual(result.envelope.capture_status_reasons, ['NO_RESULTS_GROUPS_FOUND']);
  assert.equal(result.envelope.results.length, 0);
});
