/**
 * Contract: unparsed_records entries carry fixture-scoped, allowlisted
 * fields only -- never surrounding page text, account name, balance,
 * betslip, ticket data, or navigation content. This is enforced two ways:
 * (1) structurally, parser.js's processRow() only ever reads from the
 * SELECTORS-scoped elements inside one fixture row (see its own top-of-
 * function comment); (2) here, by exhaustively enumerating every key that
 * appears anywhere in every unparsed_records[].raw across every sanitized
 * fixture this suite exercises, and failing if anything outside the
 * allowlist below ever shows up.
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const parser = require('../parser.js');
const { loadFixtureDocument, BASE_CONTEXT } = require('./helpers.js');

const ALLOWED_RAW_KEYS = new Set([
  'home', 'away', 'kickoff_raw', 'sport_hint', 'status_hint', 'date_heading_raw', 'markets', 'market', 'partial_outcomes',
]);
const ALLOWED_MARKET_KEYS = new Set(['family', 'line', 'outcomes']);
const ALLOWED_OUTCOME_KEYS = new Set(['rawLabel', 'rawPrice']);
const ALLOWED_PARTIAL_OUTCOME_KEYS = new Set(['H', 'D', 'A']);

// Substrings that must never appear in ANY unparsed_records raw value --
// a cheap, independent second check beyond the key allowlist, in case a
// future edit stuffs disallowed content into an otherwise-allowlisted key.
const FORBIDDEN_VALUE_SUBSTRINGS = ['balance', 'account', 'betslip', 'cookie', 'token', 'password', 'login', 'session'];

function assertAllowlisted(raw, fixtureLabel) {
  for (const key of Object.keys(raw)) {
    assert.ok(ALLOWED_RAW_KEYS.has(key), `${fixtureLabel}: unparsed_records raw has a non-allowlisted key "${key}"`);
  }
  if ('markets' in raw) {
    for (const market of raw.markets) {
      for (const key of Object.keys(market)) {
        assert.ok(ALLOWED_MARKET_KEYS.has(key), `${fixtureLabel}: market entry has a non-allowlisted key "${key}"`);
      }
      for (const outcome of market.outcomes || []) {
        for (const key of Object.keys(outcome)) {
          assert.ok(ALLOWED_OUTCOME_KEYS.has(key), `${fixtureLabel}: outcome entry has a non-allowlisted key "${key}"`);
        }
      }
    }
  }
  if ('market' in raw) {
    for (const key of Object.keys(raw.market)) {
      assert.ok(ALLOWED_MARKET_KEYS.has(key), `${fixtureLabel}: single-market entry has a non-allowlisted key "${key}"`);
    }
  }
  if ('partial_outcomes' in raw) {
    for (const key of Object.keys(raw.partial_outcomes)) {
      assert.ok(ALLOWED_PARTIAL_OUTCOME_KEYS.has(key), `${fixtureLabel}: partial_outcomes has a non-allowlisted key "${key}"`);
    }
  }
}

function assertNoForbiddenValues(value, fixtureLabel) {
  const text = JSON.stringify(value).toLowerCase();
  for (const banned of FORBIDDEN_VALUE_SUBSTRINGS) {
    assert.ok(!text.includes(banned), `${fixtureLabel}: unparsed_records raw unexpectedly contains "${banned}"`);
  }
}

test('every unparsed_records[].raw across every fixture is allowlisted, fixture-scoped only', () => {
  const fixturesDir = path.join(__dirname, 'fixtures');
  const allFixtureFiles = fs.readdirSync(fixturesDir).filter((f) => f.endsWith('.html'));
  let totalUnparsedChecked = 0;

  for (const filename of allFixtureFiles) {
    const doc = loadFixtureDocument(filename);
    const { envelope } = parser.captureFromDocument(doc, BASE_CONTEXT);
    for (const record of envelope.unparsed_records) {
      totalUnparsedChecked += 1;
      assertAllowlisted(record.raw, filename);
      assertNoForbiddenValues(record.raw, filename);
    }
  }

  assert.ok(totalUnparsedChecked > 0, 'this test exercises no unparsed_records at all -- fixtures must have drifted');
});

test('a fixture row embedded inside unrelated page chrome never leaks that chrome into unparsed_records', () => {
  const { JSDOM } = require('jsdom');
  const doc = new JSDOM(`
    <body>
      <nav class="site-nav">Account balance: $482.10 | Log out | Betslip (3)</nav>
      <div class="odds-board">
        <div class="competition-group">
          <div class="competition-header">Wales - Cymru Premier</div>
          <div class="fixture-row" data-status="PRE" data-sport="RUGBY">
            <div class="participants"><span class="home">Team A</span><span class="away">Team B</span></div>
          </div>
        </div>
      </div>
      <footer>Copyright Bet9ja. Your session token: abc123.</footer>
    </body>
  `).window.document;

  const { envelope } = parser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.unparsed_records.length, 1);
  assertAllowlisted(envelope.unparsed_records[0].raw, 'inline nav/footer test');
  assertNoForbiddenValues(envelope.unparsed_records[0].raw, 'inline nav/footer test');
});
