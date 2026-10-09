const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const P = require('../odds_page.js');
const R = require('../source_registry.js');

// ------------------------------------------------------------- page helpers

const TABLE = '<div>Bookmakers</div><div>1</div><div>X</div><div>2</div><div>Payout</div>'
  + '<div>bet365</div><p>1.50</p><p>4.20</p><p>6.50</p><p>95.1%</p>';

function matchPage(title, kickoff, withTable) {
  return `<!doctype html><title>${title}</title><body><div>${kickoff}</div>${withTable ? TABLE : '<div>Loading</div>'}</body>`;
}

test('readiness: ready only once the bookmaker table has rendered', () => {
  const now = new Date(2026, 9, 7, 15, 0).getTime();          // browser-local 7 Oct 15:00
  const doc = (html) => new JSDOM(html).window.document;
  assert.equal(P.readiness(doc(matchPage('A - B Odds', '07 Oct 2026,</div><div>19:00', true)), 'oddsportal', now).state, 'READY');
  assert.equal(P.readiness(doc(matchPage('A - B Odds', '07 Oct 2026,</div><div>19:00', false)), 'oddsportal', now).state, 'WAITING');
  assert.equal(P.readiness(doc(matchPage('[[EventName]] Odds', '', true)), 'oddsportal', now).state, 'WAITING');
  const started = P.readiness(doc(matchPage('A - B Odds', '07 Oct 2026,</div><div>14:30', true)), 'oddsportal', now);
  assert.equal(started.state, 'STARTED');
  assert.match(started.detail, /14:30 has passed/);
});

test('one key per match page; sport from the URL', () => {
  assert.equal(P.eventKey('https://www.oddsportal.com/tennis/h2h/a-AAAAAAAA/b-BBBBBBBB/#z7a2LFt4:home-away;2'),
    P.eventKey('https://www.oddsportal.com/tennis/h2h/a-AAAAAAAA/b-BBBBBBBB/#z7a2LFt4'));
  assert.equal(P.sportOf('https://www.oddsportal.com/basketball/h2h/x-AAAAAAAA/y-BBBBBBBB/', 'oddsportal'), 'basketball');
  assert.equal(P.sportOf('https://www.flashscore.com/match/football/a-AAAAAAAA/b-BBBBBBBB/', 'flashscore'), 'football');
});

test('matches OddsPortal links to Bet9ja fixtures by name and sport', () => {
  const targets = [
    { sport: 'football', home: 'Belouizdad', away: 'Khenchela' },
    { sport: 'tennis', home: 'Giovannini, Luisina', away: 'Karatancheva, Lia' },
    { sport: 'basketball', home: 'BC Juventus Utena', away: 'CB Malaga' },
  ];
  const link = (url, label = '') => ({ url, label_raw: label });
  assert.ok(P.matchesTarget(link('https://www.oddsportal.com/football/h2h/cr-belouizdad-vNJLB2jP/khenchela-lYuJtBj9/#Uw6q0Y3M',
    '23:00CR Belouizdad-Khenchela'), targets, 'oddsportal'));
  assert.ok(P.matchesTarget(link('https://www.oddsportal.com/tennis/h2h/giovannini-luisina-ruSbIQqA/karatancheva-lia-zqwKJ6Yd/'),
    targets, 'oddsportal'));
  assert.ok(!P.matchesTarget(link('https://www.oddsportal.com/football/h2h/mc-alger-AAAAAAAA/khenchela-BBBBBBBB/'), targets, 'oddsportal'));
  assert.ok(!P.matchesTarget(link('https://www.oddsportal.com/basketball/h2h/cr-belouizdad-AAAAAAAA/khenchela-BBBBBBBB/'),
    targets, 'oddsportal'));                                    // right names, wrong sport
  assert.ok(!P.matchesTarget(link('https://www.oddsportal.com/basketball/h2h/bc-aris-AAAAAAAA/cb-san-pablo-burgos-BBBBBBBB/'),
    targets, 'oddsportal'));                                    // shared 'BC' / 'CB' prefixes only
});

// ---------------------------------------------------- the background runner

function loadWalker({ pages, targets, results = [], timing = { listing: 1, ready: 60, poll: 5, settleCap: 1 } }) {
  const store = {};
  const listeners = { updated: new Set(), message: null };
  const tab = { id: 7, url: null, exists: true };
  const ctx = {
    console, setTimeout, clearTimeout, URL, Intl, Date, JSON, Promise, Map, Set, Object, String, Number, Math,
    WALKER_TIMING: timing,
    fetch: async () => (targets === null ? Promise.reject(new Error('refused'))
      : { ok: true, json: async () => ({ fixtures: targets, results }) }),
  };
  ctx.globalThis = ctx;
  ctx.importScripts = (...files) => files.forEach((f) =>
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'), ctx, { filename: f }));
  ctx.chrome = {
    runtime: { getManifest: () => ({ version: 'test' }), onMessage: { addListener: (fn) => { listeners.message = fn; } } },
    storage: { local: {
      get: async (k) => (k === null ? { ...store } : { [k]: store[k] }),
      set: async (o) => Object.assign(store, JSON.parse(JSON.stringify(o))),
      remove: async (ks) => ks.forEach((k) => delete store[k]),
    } },
    tabs: {
      get: async (id) => { if (!tab.exists || id !== tab.id) throw new Error(`No tab with id: ${id}.`); return { ...tab }; },
      update: async (id, { url }) => {
        if (!tab.exists) throw new Error(`No tab with id: ${id}.`);
        tab.url = url;
        if (pages.onVisit) pages.onVisit(url, tab);
        setTimeout(() => listeners.updated.forEach((fn) => fn(id, { status: 'complete' })), 1);
        return { ...tab };
      },
      onUpdated: { addListener: (fn) => listeners.updated.add(fn), removeListener: (fn) => listeners.updated.delete(fn) },
    },
    scripting: { executeScript: async ({ func, args }) => {
      if (!tab.exists) throw new Error(`No tab with id: ${tab.id}.`);
      if (!func) return [{ result: undefined }];
      const html = pages[P.eventKey(tab.url)] || pages[tab.url];
      const dom = new JSDOM(html, { url: tab.url });
      ctx.window = { PublicOddsPage: ctx.PublicOddsPage };
      ctx.document = dom.window.document;
      ctx.location = { href: tab.url };
      return [{ result: func(...args) }];
    } },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8'), ctx, { filename: 'background.js' });
  const send = (message) => new Promise((resolve) => listeners.message(message, {}, resolve));
  const until = async (pred) => {
    for (let i = 0; i < 400; i++) {
      const s = (await send({ type: 'GET_PUBLIC_ODDS_STATUS' })).run;
      if (pred(s)) return s;
      await new Promise((r) => setTimeout(r, 10));
    }
    throw new Error('timed out waiting for the walk');
  };
  return { tab, send, until, store };
}

const SEED = 'https://www.oddsportal.com/football/';
const U = (path) => `https://www.oddsportal.com/${path}`;
const later = new Date(Date.now() + 6 * 3600e3);
const past = new Date(Date.now() - 3 * 3600e3);
const stamp = (d) => `${String(d.getDate()).padStart(2, '0')} ${d.toLocaleString('en', { month: 'short' })} ${d.getFullYear()},</div><div>${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;

function footballDay() {
  const links = [
    ['football/h2h/cr-belouizdad-vNJLB2jP/khenchela-lYuJtBj9/#Uw6q0Y3M', 'CR Belouizdad-Khenchela'],
    ['football/h2h/cr-belouizdad-vNJLB2jP/khenchela-lYuJtBj9/#Uw6q0Y3M:over-under;2', 'same match, other tab'],
    ['football/h2h/constantine-AAAAAAAA/biskra-BBBBBBBB/#x1', 'Constantine-Biskra'],
    ['football/h2h/ben-aknoun-CCCCCCCC/el-biar-DDDDDDDD/#x2', 'Ben Aknoun-El Biar'],
    ['football/h2h/mc-alger-EEEEEEEE/usm-alger-FFFFFFFF/#x3', 'not on Bet9ja'],
    ['basketball/h2h/bc-nokia-GGGGGGGG/helsinki-HHHHHHHH/#x4', 'other sport'],
  ];
  const pages = { [SEED]: '<body>' + links.map(([p, l]) => `<a href="/${p}">${l}</a>`).join('') + '</body>' };
  pages[P.eventKey(U(links[0][0]))] = matchPage('CR Belouizdad - Khenchela Odds', stamp(later), true);
  pages[P.eventKey(U(links[2][0]))] = matchPage('Constantine - Biskra Odds', stamp(past), true);
  pages[P.eventKey(U(links[3][0]))] = matchPage('Ben Aknoun - JS El Biar Odds', stamp(later), false);
  const targets = [{ sport: 'football', home: 'Belouizdad', away: 'Khenchela' },
    { sport: 'football', home: 'Constantine', away: 'Biskra' }, { sport: 'football', home: 'Ben Aknoun', away: 'Js El Biar' }];
  return { pages, targets };
}

test('a walk visits only Bet9ja games, waits for odds, skips started matches', async () => {
  const { pages, targets } = footballDay();
  const w = loadWalker({ pages, targets });
  w.tab.url = SEED;
  const start = await w.send({ type: 'START_PUBLIC_ODDS_WALK', tabId: 7, maxEvents: 50, onlyBet9ja: true });
  assert.equal(start.ok, true, start.error);
  const done = await w.until((s) => s && s.status === 'COMPLETE');
  assert.deepEqual({ ...done.counts }, { discovered: 5, queued: 3, visited: 3, captured: 2, started: 1, no_odds: 1, failed: 0,
    truncated: 0, not_on_bet9ja: 1, other_sport: 1, duplicate: 1 });
  assert.match(done.targets_status, /3 Bet9ja fixtures without a benchmark; 3 of 4 listed games match/);
  const run = (await w.send({ type: 'GET_PUBLIC_ODDS_RUN' })).run;
  assert.deepEqual(run.captures.map((c) => c.role), ['seed', 'event']);   // only the ready match is kept
  assert.equal(run.captures[1].page_title, 'CR Belouizdad - Khenchela Odds');
  assert.equal(run.capture_status, 'CAPTURE_OK');
  assert.deepEqual(run.skipped.map((s) => s.state).sort(), ['NO_ODDS', 'STARTED']);
  assert.equal(run.queue, undefined);
});

test('without the app it walks every game of the sport, and says so', async () => {
  const { pages } = footballDay();
  const w = loadWalker({ pages, targets: null });
  w.tab.url = SEED;
  await w.send({ type: 'START_PUBLIC_ODDS_WALK', tabId: 7, maxEvents: 2, onlyBet9ja: true });
  const done = await w.until((s) => s && s.status === 'COMPLETE');
  assert.match(done.targets_status, /app not reachable/);
  assert.equal(done.counts.queued, 2);
  assert.equal(done.counts.truncated, 2);                     // 4 football games, limit 2: reported, not hidden
});

test('a closed tab interrupts the walk; resume continues from the same page', async () => {
  const { pages, targets } = footballDay();
  let visits = 0;
  pages.onVisit = (url, tab) => { visits += 1; if (visits === 2) tab.exists = false; };
  const w = loadWalker({ pages, targets });
  w.tab.url = SEED;
  await w.send({ type: 'START_PUBLIC_ODDS_WALK', tabId: 7, maxEvents: 50, onlyBet9ja: true });
  const stopped = await w.until((s) => s && s.status === 'INTERRUPTED');
  assert.match(stopped.message, /tab was closed/);
  assert.equal(stopped.counts.failed, 0);                       // no cascade of failures
  const cursor = stopped.cursor;
  w.tab.exists = true;
  w.tab.id = 7;
  const r = await w.send({ type: 'RESUME_PUBLIC_ODDS_WALK', tabId: 7 });
  assert.equal(r.ok, true, r.error);
  const done = await w.until((s) => s && s.status === 'COMPLETE');
  assert.equal(done.cursor, 3);
  assert.ok(cursor < 3);
});

test('only one walk at a time', async () => {
  const { pages, targets } = footballDay();
  const w = loadWalker({ pages, targets, timing: { listing: 1, ready: 300, poll: 50, settleCap: 1 } });
  w.tab.url = SEED;
  assert.equal((await w.send({ type: 'START_PUBLIC_ODDS_WALK', tabId: 7, maxEvents: 50 })).ok, true);
  const second = await w.send({ type: 'START_PUBLIC_ODDS_WALK', tabId: 7, maxEvents: 50 });
  assert.equal(second.ok, false);
  assert.match(second.error, /already running/);
  await w.send({ type: 'STOP_PUBLIC_ODDS_WALK' });
  await w.until((s) => s && s.status === 'STOPPED');
});

// ------------------------------------------------------------ results mode

const resultPage = (header) => `<!doctype html><title>A - B Odds</title><body><div>Hockey</div>${header}`
  + '<div>1X2</div><div>Home/Away</div></body>';

test('resultState reads the final result, flags not-played games, waits otherwise', () => {
  const doc = (html) => new JSDOM(html).window.document;
  const ot = P.resultState(doc(resultPage('<div>06 Oct 2026,</div><div>23:00</div><div>Final result </div>'
    + '<div>5:4 OT</div><div> (0:2, 3:2, 1:0, 1:0) </div>')));
  assert.equal(ot.state, 'FINAL');
  assert.equal(ot.final_result_raw, 'Final result 5:4 OT (0:2, 3:2, 1:0, 1:0)');
  assert.match(ot.kickoff_raw, /^06 Oct 2026,\s*23:00$/);
  assert.equal(P.resultState(doc(resultPage('<div>06 Oct 2026,</div><div>23:00</div><div>Postponed</div>'))).state, 'NOT_FINAL');
  assert.equal(P.resultState(doc(resultPage('<div>06 Oct 2026,</div><div>23:00</div>'))).state, 'WAITING');
  // the page's translation strings contain 'Final result' with no score: not a result
  assert.equal(P.resultState(doc(resultPage('<script>{"final_result":"Final result","x":"y"}</script>'))).state, 'WAITING');
});

test('a results walk visits the pages the app lists and keeps each final result', async () => {
  const done = U('hockey/h2h/kings-AAAAAAAA/panthers-BBBBBBBB/#6s6UtjWi');
  const off = U('football/h2h/a-CCCCCCCC/b-DDDDDDDD/#x9');
  const pages = {
    [SEED]: '<body>OddsPortal</body>',
    [P.eventKey(done)]: resultPage('<div>07 Oct 2026,</div><div>02:00</div><div>Final result 1:2 (1:0, 0:1, 0:1)</div>'),
    [P.eventKey(off)]: resultPage('<div>07 Oct 2026,</div><div>19:00</div><div>Canceled</div>'),
  };
  const results = [{ url: done, sport: 'hockey', home: 'Los Angeles Kings', away: 'Florida Panthers', kickoff_utc: '2026-10-07T00:00Z' },
    { url: off, sport: 'football', home: 'A', away: 'B', kickoff_utc: '2026-10-07T17:00Z' }];
  const w = loadWalker({ pages, targets: [], results });
  w.tab.url = SEED;
  const start = await w.send({ type: 'START_RESULTS_WALK', tabId: 7, maxEvents: 50 });
  assert.equal(start.ok, true, start.error);
  const fin = await w.until((s) => s && s.status === 'COMPLETE');
  assert.equal(fin.mode, 'results');
  assert.equal(fin.counts.results, 1);
  assert.equal(fin.counts.not_final, 1);
  const run = (await w.send({ type: 'GET_PUBLIC_ODDS_RUN' })).run;
  assert.equal(run.captures.length, 1);
  assert.equal(run.captures[0].role, 'result');
  assert.equal(run.captures[0].source_url, done);                 // with the match hash
  assert.equal(run.captures[0].final_result_raw, 'Final result 1:2 (1:0, 0:1, 0:1)');
});

test('a results walk needs an OddsPortal tab and a list from the app', async () => {
  const w = loadWalker({ pages: { 'https://polymarket.com/sports': '<body></body>' }, targets: [], results: [] });
  w.tab.url = 'https://polymarket.com/sports';
  assert.match((await w.send({ type: 'START_RESULTS_WALK', tabId: 7 })).error, /oddsportal\.com/);
  w.tab.url = SEED;
  assert.match((await w.send({ type: 'START_RESULTS_WALK', tabId: 7 })).error, /No finished games/);
});

// ------------------------------------------------------------- search mode

test('a find-all walk searches each Bet9ja game and visits only the matches found', async () => {
  const { pages } = footballDay();
  const S = (t) => `https://www.oddsportal.com/search/${t}/`;
  const bel = 'football/h2h/cr-belouizdad-vNJLB2jP/khenchela-lYuJtBj9/#Uw6q0Y3M';
  pages[S('belouizdad')] = `<body>Next Matches (2)<a href="/${bel}">CR Belouizdad-Khenchela</a>`
    + '<a href="/football/h2h/cr-belouizdad-vNJLB2jP/mc-alger-EEEEEEEE/#z1">CR Belouizdad-MC Alger</a></body>';
  pages[S('constantine')] = '<body>Next Matches (1)<a href="/football/h2h/constantine-AAAAAAAA/biskra-BBBBBBBB/#x1">Constantine-Biskra</a></body>';
  pages[S('aknoun')] = '<body>Next Matches (0) Unfortunately, no matches can be displayed</body>';
  pages[S('biar')] = '<body>Next Matches (0) Unfortunately, no matches can be displayed</body>';
  pages[S('ben')] = '<body>Next Matches (0) Unfortunately, no matches can be displayed</body>';
  const targets = [{ sport: 'football', home: 'CR Belouizdad', away: 'Khenchela' },
    { sport: 'football', home: 'Constantine', away: 'Biskra' }, { sport: 'football', home: 'Ben Aknoun', away: 'Js El Biar' }];
  const w = loadWalker({ pages, targets, timing: { listing: 1, ready: 60, poll: 5, settleCap: 1, search: 40 } });
  w.tab.url = SEED;
  const start = await w.send({ type: 'START_SEARCH_WALK', tabId: 7, maxEvents: 50 });
  assert.equal(start.ok, true, start.error);
  const done = await w.until((s) => s && s.status === 'COMPLETE');
  assert.equal(done.mode, 'search');
  assert.equal(done.counts.games, 3);
  assert.equal(done.counts.found, 2);
  // Ben Aknoun: 'aknoun', 'ben', then 'biar' ('el', 'js' are too short); the others hit first time
  assert.equal(done.counts.searched, 5);
  assert.equal(done.counts.no_results, 3);
  assert.equal(done.counts.not_on_bet9ja, 1);                  // Belouizdad's other game
  assert.equal(done.counts.queued, 2);
  assert.equal(done.counts.captured, 1);                       // Constantine has started: skipped
  assert.equal(done.counts.started, 1);
  const run = (await w.send({ type: 'GET_PUBLIC_ODDS_RUN' })).run;
  assert.deepEqual(run.captures.map((c) => c.role), ['event']);
  assert.equal(run.captures[0].page_title, 'CR Belouizdad - Khenchela Odds');
});

test('OddsPortal captures leave out scripts, styles and icons; Polymarket keeps its market JSON', () => {
  const dom = new JSDOM('<!doctype html><title>A - B Odds</title><body><script>var big = "x";</script><style>.a{}</style>'
    + '<svg><path d="M0"/></svg><div>Bookmakers</div><p>1.50</p></body>', { url: 'https://www.oddsportal.com/football/h2h/a-AAAAAAAA/b-BBBBBBBB/' });
  const cap = P.captureCurrentPage(dom.window.document, { href: dom.window.location.href, title: 'A - B Odds', capturedAtUtc: 'now' }, 'full');
  assert.equal(cap.html_mode, 'NO_SCRIPTS_STYLES_SVG');
  assert.ok(!/<script|<style|<svg/i.test(cap.html));
  assert.match(cap.html, /Bookmakers/);
});

test('searchState waits for the search to answer', () => {
  const doc = (html) => new JSDOM(html).window.document;
  assert.equal(P.searchState(doc('<body><div class="animate-pulse"></div></body>')).state, 'WAITING');
  assert.equal(P.searchState(doc('<body>Next Matches (0) Unfortunately, no matches</body>')).state, 'EMPTY');
  assert.equal(P.searchState(doc('<body>Next Matches (1)</body>')).state, 'WAITING');         // count shown, rows not yet
  assert.equal(P.searchState(doc('<body>Next Matches (1)<a href="/football/h2h/a-AAAAAAAA/b-BBBBBBBB/">A-B</a></body>')).state, 'READY');
});

test('a game already found by another search is not searched again', async () => {
  const { pages } = footballDay();
  const S = (t) => `https://www.oddsportal.com/search/${t}/`;
  // Belouizdad's search also lists Constantine's game
  pages[S('belouizdad')] = '<body>Next Matches (2)<a href="/football/h2h/cr-belouizdad-vNJLB2jP/khenchela-lYuJtBj9/#Uw6q0Y3M">CR Belouizdad-Khenchela</a>'
    + '<a href="/football/h2h/constantine-AAAAAAAA/biskra-BBBBBBBB/#x1">Constantine-Biskra</a></body>';
  const visited = [];
  pages.onVisit = (url) => visited.push(url);
  const targets = [{ sport: 'football', home: 'CR Belouizdad', away: 'Khenchela' },
    { sport: 'football', home: 'Constantine', away: 'Biskra' }];
  const w = loadWalker({ pages, targets, timing: { listing: 1, ready: 60, poll: 5, settleCap: 1, search: 40 } });
  w.tab.url = SEED;
  await w.send({ type: 'START_SEARCH_WALK', tabId: 7, maxEvents: 50 });
  const done = await w.until((s) => s && s.status === 'COMPLETE');
  assert.equal(done.counts.searched, 1);
  assert.equal(done.counts.found, 2);
  assert.equal(done.counts.found_elsewhere, 1);
  assert.ok(!visited.includes(S('constantine')));
  assert.equal(done.counts.queued, 2);
});

test('a find-all walk needs the app and an OddsPortal tab', async () => {
  const w = loadWalker({ pages: {}, targets: null });
  w.tab.url = SEED;
  assert.match((await w.send({ type: 'START_SEARCH_WALK', tabId: 7 })).error, /not reachable/);
  const w2 = loadWalker({ pages: {}, targets: [] });
  w2.tab.url = SEED;
  assert.match((await w2.send({ type: 'START_SEARCH_WALK', tabId: 7 })).error, /already has a benchmark/);
});

test('allowlisted hosts only (unchanged)', () => {
  assert.equal(R.findSource('https://oddsportal.evil.example/'), null);
});
