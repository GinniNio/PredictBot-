const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

/**
 * Runs background.js in a simulated Chrome: alarms, tabs, storage, and an
 * executeScript that "walks" a sport by sending the BET9JA_WALK_DONE
 * message the page-side function would send. Uploads are recorded.
 */
function loadWorker({ uploadOk = true, walkResult = (sport) => ({ schema_version: 'bet9ja-allsports-sport-walk.v3',
  sport, capture_status: 'COMPLETE', fixtures_total: 3, fixtures_with_priced_market: 3, competitions_seen: 2,
  competitions_validated: 2, results: [] }) } = {}) {
  const store = {};
  const alarms = {};
  const listeners = { updated: new Set(), message: null, alarm: null };
  const tabs = { created: [], removed: [], open: [] };
  const uploads = [];
  const injected = [];
  let nextTab = 40;
  const ctx = {
    console, setTimeout, clearTimeout, Date, JSON, Promise, Map, Set, Object, String, Number, Math, Array,
    BET9JA_TIMING: { sport: 2000, settle: 10 },
    fetch: async (url, opts) => {
      uploads.push(JSON.parse(opts.body));
      return uploadOk ? { ok: true, status: 200, json: async () => ({ saved: 'bet9ja-allsports-walk-x.json', message: 'priced' }) }
        : { ok: false, status: 409, json: async () => ({ error: 'data folder is read-only' }) };
    },
  };
  ctx.globalThis = ctx;
  ctx.importScripts = (...files) => files.forEach((f) =>
    vm.runInContext(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'), ctx, { filename: f }));
  ctx.chrome = {
    runtime: { onMessage: { addListener: (fn) => { listeners.message = fn; } },
      sendMessage: (msg) => new Promise((resolve) => listeners.message(msg, {}, resolve)) },
    storage: { local: {
      get: async (k) => (k === null ? { ...store } : Array.isArray(k) ? Object.fromEntries(k.map((x) => [x, store[x]])) : { [k]: store[k] }),
      set: async (o) => Object.assign(store, JSON.parse(JSON.stringify(o))),
      remove: async (ks) => ks.forEach((k) => delete store[k]),
    } },
    alarms: {
      create: async (name, info) => { alarms[name] = info; },
      clear: async (name) => { delete alarms[name]; },
      get: async (name) => alarms[name] || null,
      onAlarm: { addListener: (fn) => { listeners.alarm = fn; } },
    },
    tabs: {
      query: async () => tabs.open.map((id) => ({ id })),
      create: async ({ url }) => { const id = nextTab++; tabs.created.push(url); return { id, url, status: 'complete' }; },
      remove: async (id) => { tabs.removed.push(id); },
      get: async (id) => ({ id, status: 'complete' }),
      onUpdated: { addListener: (fn) => listeners.updated.add(fn), removeListener: (fn) => listeners.updated.delete(fn) },
    },
    scripting: { executeScript: async ({ target, files, func, args }) => {
      if (files) { injected.push(files); return [{ result: undefined }]; }
      const [sportId, sport, runId] = args;
      // the page-side walk: report back by message, as walkInPage does
      setTimeout(() => ctx.chrome.runtime.sendMessage({ type: 'BET9JA_WALK_DONE', run_id: runId, sport, started: 'now',
        result: walkResult(sport, sportId) }), 5);
      return [{ result: { ok: true, sport } }];
    } },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'background.js'), 'utf8'), ctx, { filename: 'background.js' });
  const send = (message) => new Promise((resolve) => listeners.message(message, {}, resolve));
  const until = async (pred) => {
    for (let i = 0; i < 400; i++) {
      const r = await send({ type: 'GET_BET9JA_SCHEDULE' });
      if (pred(r)) return r;
      await new Promise((res) => setTimeout(res, 10));
    }
    throw new Error('timed out');
  };
  return { send, until, store, alarms, tabs, uploads, injected, fire: (name) => listeners.alarm({ name }) };
}

test('saving a schedule creates the alarm; disabling clears it', async () => {
  const w = loadWorker();
  const r = await w.send({ type: 'SET_BET9JA_SCHEDULE', schedule: { enabled: true, every_hours: 4, sports: ['soccer', 'tennis'] } });
  assert.equal(r.schedule.enabled, true);
  assert.equal(w.alarms['bet9ja:walk'].periodInMinutes, 240);
  await w.send({ type: 'SET_BET9JA_SCHEDULE', schedule: { enabled: false } });
  assert.equal(w.alarms['bet9ja:walk'], undefined);
});

test('a scheduled run opens its own Bet9ja tab, walks each sport in turn, uploads each capture, closes the tab', async () => {
  const w = loadWorker();
  await w.send({ type: 'SET_BET9JA_SCHEDULE', schedule: { enabled: true, sports: ['soccer', 'tennis'], start_url: 'https://sports.bet9ja.com/' } });
  w.fire('bet9ja:walk');
  const done = await w.until((r) => r.state && r.state.status === 'COMPLETE');
  assert.deepEqual([...w.tabs.created], ['https://sports.bet9ja.com/']);
  assert.deepEqual([...w.tabs.removed], [40]);
  assert.deepEqual(done.state.sports.map((s) => [s.sport, s.status, s.uploaded]), [['soccer', 'COMPLETE', true], ['tennis', 'COMPLETE', true]]);
  assert.equal(w.uploads.length, 2);
  assert.equal(w.uploads[0].sport, 'soccer');
  assert.equal(w.uploads[0].schema_version, 'bet9ja-allsports-sport-walk.v3');
  assert.ok(w.injected.length >= 2);                                   // parsers injected before each sport
});

test('a capture the app does not take is kept in storage, and the run goes on to the next sport', async () => {
  const w = loadWorker({ uploadOk: false });
  await w.send({ type: 'SET_BET9JA_SCHEDULE', schedule: { enabled: true, sports: ['soccer', 'tennis'] } });
  w.fire('bet9ja:walk');
  const done = await w.until((r) => r.state && r.state.status === 'COMPLETE');
  assert.deepEqual(done.state.sports.map((s) => [s.sport, s.uploaded, s.stored]), [['soccer', false, true], ['tennis', false, true]]);
  assert.ok(Object.keys(w.store).some((k) => k.startsWith('bet9jaCapture:') && k.endsWith(':soccer')));
});

test('a manual walk from the popup is uploaded by the worker; an alarm never starts over a running run', async () => {
  const w = loadWorker();
  const sent = await w.send({ type: 'BET9JA_WALK_DONE', run_id: 'manual', sport: 'darts', started: 'now',
    result: { schema_version: 'bet9ja-allsports-sport-walk.v3', sport: 'darts', capture_status: 'COMPLETE' } });
  assert.equal(sent.uploaded, true);
  assert.equal(w.uploads.length, 1);
  await w.send({ type: 'SET_BET9JA_SCHEDULE', schedule: { enabled: true, sports: ['soccer'] } });
  w.fire('bet9ja:walk');                      // the second alarm finds the first run RUNNING
  await w.until((r) => r.state && r.state.status === 'RUNNING');
  w.fire('bet9ja:walk');
  const done = await w.until((r) => r.state && r.state.status === 'COMPLETE');
  assert.equal(done.state.sports.length, 1);
  assert.equal(w.tabs.created.length, 1);
  assert.equal(w.uploads.length, 2);          // the manual one and the scheduled one, each once
});
