const test = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { captureCurrentPage, discoverEventLinks } = require('../odds_page.js');

test('captures a public Polymarket page with raw DOM evidence', () => {
  const dom = new JSDOM('<!doctype html><title>Market</title><main>Yes 0.61</main>');
  const result = captureCurrentPage(dom.window.document, { href: 'https://polymarket.com/event/example', title: 'Market', capturedAtUtc: '2026-10-05T10:00:00.000Z' });
  assert.equal(result.capture_status, 'CAPTURE_OK');
  assert.equal(result.source_key, 'polymarket');
  assert.match(result.html, /Yes 0.61/);
});

test('discovers only rendered Polymarket sports event links in sports scope', () => {
  const dom = new JSDOM('<a href="/sports/atp/atp-cui-vukic-2026-10-05">Cui v Vukic</a><a href="/sports/atp/games">Games</a><a href="/event/bitcoin-updown-5m">Bitcoin up/down</a><a href="https://other.example/event/no">No</a>');
  const result = discoverEventLinks(dom.window.document, 'https://polymarket.com/sports', 'sports');
  assert.deepEqual(result.links, [{ url: 'https://polymarket.com/sports/atp/atp-cui-vukic-2026-10-05', label_raw: 'Cui v Vukic' }]);
});

test('allows Polymarket non-sports only when explicitly selected', () => {
  const dom = new JSDOM('<a href="/event/bitcoin-updown-5m">Bitcoin up/down</a>');
  const result = discoverEventLinks(dom.window.document, 'https://polymarket.com/sports', 'all');
  assert.deepEqual(result.links, [{ url: 'https://polymarket.com/event/bitcoin-updown-5m', label_raw: 'Bitcoin up/down' }]);
});

test('discovers OddsPortal h2h event URLs with and without a hash', () => {
  const dom = new JSDOM('<a href="/football/h2h/cyprus-fumziNU3/latvia-WC2jLpW4/#CKQADDDr">Has hash</a><a href="/football/h2h/bosnia-herzegovina-4c5c2713/poland-db74381b/">No hash</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.oddsportal.com/');
  assert.equal(result.discovery_status, 'READY');
  assert.equal(result.links.length, 2);
});

test('discovers Flashscore fixture routes but not sport pages', () => {
  const dom = new JSDOM('<a href="/football/">Football</a><a href="/match/football/czech-republic-6LHwBDGU/england-j9N9ZNFA/">Match</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.flashscore.com/');
  assert.equal(result.discovery_status, 'READY');
  assert.deepEqual(result.links, [{ url: 'https://www.flashscore.com/match/football/czech-republic-6LHwBDGU/england-j9N9ZNFA/', label_raw: 'Match' }]);
});

test('does not auto-discover Betfair Exchange links without live route evidence', () => {
  const dom = new JSDOM('<a href="/exchange/plus/football/market/12345">Market</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.betfair.com/');
  assert.equal(result.discovery_status, 'EVIDENCE_REQUIRED');
  assert.deepEqual(result.links, []);
});

test('discovers evidenced Oddschecker US event routes but skips competition pages', () => {
  const dom = new JSDOM('<a href="/us/football/nfl">NFL</a><a href="/us/football/nfl/atlanta-falcons-at-new-orleans-saints">Event</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.oddschecker.com/us/');
  assert.equal(result.discovery_status, 'READY');
  assert.deepEqual(result.links, [{ url: 'https://www.oddschecker.com/us/football/nfl/atlanta-falcons-at-new-orleans-saints', label_raw: 'Event' }]);
});

test('discovers evidenced BetExplorer match URLs but skips a league URL', () => {
  const dom = new JSDOM('<a href="/football/spain/laliga2/">League</a><a href="/football/spain/laliga2/cordoba-tenerife/ML4DjdZa/">Match</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.betexplorer.com/');
  assert.equal(result.discovery_status, 'READY');
  assert.deepEqual(result.links, [{ url: 'https://www.betexplorer.com/football/spain/laliga2/cordoba-tenerife/ML4DjdZa/', label_raw: 'Match' }]);
});

test('flashscore.info is a supported host', () => {
  const dom = new JSDOM('<a href="/match/football/czech-republic-6LHwBDGU/england-j9N9ZNFA/">Match</a>');
  const result = discoverEventLinks(dom.window.document, 'https://www.flashscore.info/');
  assert.equal(result.discovery_status, 'READY');
  assert.equal(result.links.length, 1);
});

test('background passes no undefined args to executeScript', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'background.js'), 'utf8');
  assert.match(src, /args: \[String\(action\), scope == null \? 'sports' : String\(scope\), String\(sourceKey \|\| ''\)\]/);
  assert.doesNotMatch(src, /args: \[action, scope\]/);
});

test('only allowlisted hosts and their subdomains are recognised', () => {
  const { findSource } = require('../source_registry.js');
  for (const u of ['https://www.oddsportal.com/tennis/', 'https://m.oddsportal.com/', 'https://www.flashscore.info/']) {
    assert.ok(findSource(u), u);
  }
  for (const u of ['https://oddsportal.evil.example/', 'https://www.oddsportal.evil.example/football/',
                   'https://notoddsportal.example.com/', 'https://oddsportalx.com/', 'https://www.oddsportal1.com/football/',
                   'https://oddsportal.ng/football/', 'https://www.flashscore.co.uk/', 'https://evil-oddsportal.com/']) {
    assert.equal(findSource(u), null, u);
  }
});

const { discoverListingLinks, withinDays } = require('../odds_page.js');

test('Polymarket catalogue: listing pages from the Sports menu, not props/live/futures', () => {
  const dom = new JSDOM('<a href="/sports/atp/games">ATP</a><a href="/sports/modus/games">Darts</a>' +
    '<a href="/sports/pga/props">Golf</a><a href="/sports/live">Live</a><a href="/sports/futures">Futures</a>' +
    '<a href="/sports/atp/games?x=1">dup</a><a href="/sports/atp/atp-djokovi-medvede-2026-10-05">Match</a>');
  const r = discoverListingLinks(dom.window.document, 'https://polymarket.com/sports/live');
  assert.deepEqual(r.links.map((l) => l.url), ['https://polymarket.com/sports/atp/games', 'https://polymarket.com/sports/modus/games']);
});

test('event dates: yesterday to tomorrow kept, futures dropped, undated kept', () => {
  const today = '2026-10-05T09:00:00.000Z';
  assert.ok(withinDays('https://polymarket.com/sports/atp/atp-a-b-2026-10-05', today, 1));
  assert.ok(withinDays('https://polymarket.com/sports/modus/modus-a-b-2026-10-06-75214640', today, 1));
  assert.ok(!withinDays('https://polymarket.com/sports/nfl/nfl-a-b-2026-10-12', today, 1));
  assert.ok(withinDays('https://polymarket.com/sports/ufc/some-event', today, 1));
});

test('Polymarket event capture keeps only the embedded market JSON', () => {
  const big = 'x'.repeat(50000);
  const market = '{"outcomes":["A B","C D"],"outcomePrices":["0.4","0.6"],"volume":"9000"}';
  const dom = new JSDOM(`<!doctype html><title>t</title><body>${big}<script>${market}</script>${big}</body>`);
  const r = captureCurrentPage(dom.window.document,
    { href: 'https://polymarket.com/sports/atp/atp-a-c-2026-10-05', title: 't', capturedAtUtc: '2026-10-05T09:00:00Z' }, 'excerpt');
  assert.equal(r.html_mode, 'EXCERPT_outcomePrices');
  assert.ok(r.html.includes(market));
  assert.ok(r.html.length < 13000 && r.html_length > 100000);
});

test('OddsPortal in-play tabs are not followed', () => {
  const dom = new JSDOM('<a href="/football/h2h/cyprus-fumziNU3/latvia-WC2jLpW4/inplay-odds/#CKQADDDr">live</a>' +
    '<a href="/football/h2h/belgium-GbB957na/france-QkGeVG1n/#EmmmJQ3L">pre</a>');
  const r = discoverEventLinks(dom.window.document, 'https://www.oddsportal.com/football/');
  assert.deepEqual(r.links.map((l) => l.label_raw), ['pre']);
});
