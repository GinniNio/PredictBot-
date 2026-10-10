/* Scheduled Bet9ja walks (0.4.0). Every N hours, while Chrome is open, the
 * worker opens (or reuses) a Bet9ja tab in the background, walks each
 * chosen sport in turn with the same walkSport() the popup runs, and sends
 * each sport's capture to the PredictBot app over localhost. A manual
 * "Walk sport" from the popup is sent the same way. The walk itself runs
 * inside the page; it reports back by message, so a suspended worker still
 * receives the result. No bet, cashout or account control is ever touched. */

const SCHEDULE_KEY = 'bet9jaSchedule';
const STATE_KEY = 'bet9jaScheduleState';
const DEFAULT_START_URL = 'https://sports.bet9ja.com/';
// The sports the PredictBot app prices (its main-market map), in walk order.
const DEFAULT_SPORTS = ['soccer', 'basketball', 'tennis', 'ice_hockey', 'baseball', 'handball', 'volleyball',
  'american_football', 'table_tennis', 'cricket', 'darts', 'floorball', 'futsal', 'mma'];
const DEFAULT_SCHEDULE = { enabled: false, every_hours: 4, sports: DEFAULT_SPORTS, start_url: DEFAULT_START_URL };
const APP_UPLOAD = ['http://localhost:8000/walker-upload', 'http://127.0.0.1:8000/walker-upload'];
// Timings; tests shorten them through globalThis.BET9JA_TIMING.
const T = Object.assign({ sport: 25 * 60 * 1000, settle: 4000 }, globalThis.BET9JA_TIMING || {});
const SPORT_TIMEOUT_MS = T.sport;           // give up on one sport after this
const STALE_RUN_MS = 2 * 60 * 60 * 1000;    // a RUNNING state older than this is a dead run
const nowIso = () => new Date().toISOString();

importScripts('catalogue_parser.js');
const SPORT_IDS = Object.fromEntries(
  (globalThis.Bet9jaAllSportsCatalogueParser.CONFIRMED_SPORTS || []).map((s) => [s.sport_slug, s.sport_id]));

// ---------------------------------------------------------------- storage

async function loadSchedule() {
  return { ...DEFAULT_SCHEDULE, ...((await chrome.storage.local.get(SCHEDULE_KEY))[SCHEDULE_KEY] || {}) };
}
async function loadState() { return (await chrome.storage.local.get(STATE_KEY))[STATE_KEY] || null; }
async function saveState(state) { state.updated_at_utc = nowIso(); await chrome.storage.local.set({ [STATE_KEY]: state }); }

async function applySchedule(schedule) {
  await chrome.storage.local.set({ [SCHEDULE_KEY]: schedule });
  await chrome.alarms.clear('bet9ja:walk');
  if (!schedule.enabled) return;
  const hours = Math.max(0.5, Number(schedule.every_hours) || 4);
  await chrome.alarms.create('bet9ja:walk', { delayInMinutes: 1, periodInMinutes: hours * 60 });
}

// ------------------------------------------------------------- the page

// Runs inside the Bet9ja tab. Walks one sport and reports the capture by
// message, so the result arrives even if the caller's worker was suspended.
function walkInPage(sportId, sportSlug, runId) {
  const started = new Date().toISOString();
  return window.Bet9jaAllSportsWalker.walkSport(document, {
    sportId, sportSlug, maxGroups: null, maxCompetitionsPerGroup: null,
    now: () => new Date().toISOString(),
  }).then((result) => {
    chrome.runtime.sendMessage({ type: 'BET9JA_WALK_DONE', run_id: runId, sport: sportSlug, started, result });
    return { ok: true, sport: sportSlug };
  }, (err) => {
    chrome.runtime.sendMessage({ type: 'BET9JA_WALK_DONE', run_id: runId, sport: sportSlug, started,
      error: err && err.message ? err.message : String(err) });
    return { ok: false, sport: sportSlug };
  });
}

async function injectWalker(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['catalogue_parser.js', 'fixture_parser.js', 'sport_walker.js'] });
}

// Resolves when the page reports this sport's result (or the timeout passes).
const waiting = new Map();   // `${run_id}:${sport}` -> resolve
function awaitWalk(runId, sport, timeoutMs) {
  return new Promise((resolve) => {
    const key = `${runId}:${sport}`;
    const timer = setTimeout(() => { waiting.delete(key); resolve({ error: `no result after ${timeoutMs / 60000} min` }); }, timeoutMs);
    waiting.set(key, (msg) => { clearTimeout(timer); waiting.delete(key); resolve(msg); });
  });
}

async function walkOneSport(tabId, runId, sport) {
  const sportId = SPORT_IDS[sport];
  if (!sportId) return { error: `unknown sport ${sport}` };
  await injectWalker(tabId);
  const done = awaitWalk(runId, sport, SPORT_TIMEOUT_MS);
  chrome.scripting.executeScript({ target: { tabId }, func: walkInPage, args: [sportId, sport, runId] })
    .catch((err) => { const r = waiting.get(`${runId}:${sport}`); if (r) r({ error: err && err.message ? err.message : String(err) }); });
  return done;
}

// --------------------------------------------------------------- upload

async function uploadCapture(result, sport) {
  const body = JSON.stringify(result);
  let last = 'PredictBot app not reachable on localhost:8000';
  for (const url of APP_UPLOAD) {
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
      const reply = await r.json().catch(() => ({}));
      if (r.ok) return { uploaded: true, message: `${reply.saved || sport}: ${reply.message || ''}` };
      last = reply.error || `app refused the capture (HTTP ${r.status})`;
      break;
    } catch (_) { /* app not running on this address */ }
  }
  return { uploaded: false, message: last };
}

// A walk's result arriving by message. A scheduled run is waiting for it
// and uploads it itself; a manual walk (popup) is uploaded here. Captures
// the app did not take stay for download (the popup holds the result).
async function handleWalkDone(msg) {
  const key = `${msg.run_id}:${msg.sport}`;
  const resolver = waiting.get(key);
  if (resolver) { resolver(msg); return { handled: true }; }
  if (!msg.result) return { uploaded: false, message: msg.error || 'walk failed' };
  const sent = await uploadCapture(msg.result, msg.sport);
  const state = (await loadState()) || { sports: [] };
  state.last_upload = { sport: msg.sport, at_utc: nowIso(), ...sent };
  await saveState(state);
  return sent;
}

// -------------------------------------------------------------- schedule

function summarize(result) {
  if (!result) return 'no capture';
  return `${result.capture_status}, ${result.fixtures_total || 0} fixtures (${result.fixtures_with_priced_market || 0} priced), `
    + `${result.competitions_validated || 0}/${result.competitions_seen || 0} competitions`;
}

function waitForTab(tabId, timeoutMs = 45000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (ok) => { if (!done) { done = true; clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve(ok); } };
    const timer = setTimeout(() => finish(false), timeoutMs);
    function listener(id, info) { if (id === tabId && info.status === 'complete') finish(true); }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.get(tabId).then((t) => { if (t && t.status === 'complete') finish(true); }).catch(() => finish(false));
  });
}

async function bet9jaTab(startUrl) {
  const open = await chrome.tabs.query({ url: '*://*.bet9ja.com/*' });
  if (open.length) return { tabId: open[0].id, created: false };
  const tab = await chrome.tabs.create({ url: startUrl || DEFAULT_START_URL, active: false });
  await waitForTab(tab.id);
  await new Promise((r) => setTimeout(r, T.settle)); // the sidebar renders after load
  return { tabId: tab.id, created: true };
}

async function scheduledWalk(trigger = 'schedule') {
  const schedule = await loadSchedule();
  if (trigger === 'schedule' && !schedule.enabled) return;
  const current = await loadState();
  if (current && current.status === 'RUNNING' && Date.now() - Date.parse(current.updated_at_utc || 0) < STALE_RUN_MS) {
    return;                                         // one run at a time
  }
  const runId = Date.now().toString(36);
  const state = { run_id: runId, status: 'RUNNING', trigger, started_at_utc: nowIso(), sports: [], tab_created: false };
  await saveState(state);
  let tab = null;
  try {
    tab = await bet9jaTab(schedule.start_url);
    state.tab_created = tab.created;
    for (const sport of schedule.sports || DEFAULT_SPORTS) {
      const entry = { sport, started_at_utc: nowIso() };
      state.sports.push(entry);
      await saveState(state);
      let outcome;
      try {
        outcome = await walkOneSport(tab.tabId, runId, sport);
      } catch (err) {
        outcome = { error: err && err.message ? err.message : String(err) };
      }
      if (outcome.error) {
        entry.status = 'failed';
        entry.detail = outcome.error;
        if (/No tab with id|tab was closed/i.test(outcome.error)) { state.message = 'The Bet9ja tab was closed.'; break; }
      } else {
        entry.status = outcome.result.capture_status;
        entry.detail = summarize(outcome.result);
        const sent = await uploadCapture(outcome.result, sport);
        entry.uploaded = sent.uploaded;
        entry.upload = sent.message;
        if (!sent.uploaded) {                       // keep it: the popup can download it
          await chrome.storage.local.set({ [`bet9jaCapture:${runId}:${sport}`]: outcome.result });
          entry.stored = true;
        }
      }
      entry.finished_at_utc = nowIso();
      await saveState(state);
    }
    state.status = 'COMPLETE';
  } catch (err) {
    state.status = 'FAILED';
    state.message = err && err.message ? err.message : String(err);
  } finally {
    state.finished_at_utc = nowIso();
    await saveState(state);
    if (tab && tab.created) chrome.tabs.remove(tab.tabId).catch(() => {});
  }
}

chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === 'bet9ja:walk') scheduledWalk('schedule'); });

(async () => {
  const schedule = await loadSchedule();
  if (schedule.enabled && !(await chrome.alarms.get('bet9ja:walk'))) await applySchedule(schedule);
})();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const reply = (p) => p.then((value) => sendResponse({ ok: true, ...value }))
    .catch((err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err) }));
  if (message.type === 'BET9JA_WALK_DONE') { reply(handleWalkDone(message)); return true; }
  if (message.type === 'GET_BET9JA_SCHEDULE') {
    reply((async () => ({ schedule: await loadSchedule(), state: await loadState(), sports: Object.keys(SPORT_IDS) }))());
    return true;
  }
  if (message.type === 'SET_BET9JA_SCHEDULE') {
    reply(applySchedule({ ...DEFAULT_SCHEDULE, ...message.schedule }).then(loadSchedule).then((schedule) => ({ schedule })));
    return true;
  }
  if (message.type === 'RUN_BET9JA_WALK_NOW') { scheduledWalk('manual'); sendResponse({ ok: true }); return false; }
  return false;
});
