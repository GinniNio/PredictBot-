/* Scheduled Bet9ja walks (0.5.0). Every N hours, while Chrome is open, the
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
const T = Object.assign({ sport: 90 * 60 * 1000, settle: 4000 }, globalThis.BET9JA_TIMING || {});
const SPORT_TIMEOUT_MS = T.sport;           // give up on one sport after this (soccer is the long one)
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

// Runs inside the Bet9ja tab. Starts one sport's walk and returns at once;
// the capture arrives by message when the walk ends, so the worker may be
// suspended and restarted meanwhile without losing it.
function walkInPage(sportId, sportSlug, runId) {
  const started = new Date().toISOString();
  // Waits go through the worker: a hidden tab's own timers are throttled.
  const sleep = (ms) => new Promise((resolve) => {
    try { chrome.runtime.sendMessage({ type: 'BET9JA_SLEEP', ms }, () => resolve()); } catch (_) { setTimeout(resolve, ms); }
  });
  const progress = (p) => { try { chrome.runtime.sendMessage({ type: 'BET9JA_PROGRESS', run_id: runId, sport: sportSlug, ...p }); } catch (_) { /* reloaded */ } };
  window.Bet9jaAllSportsWalker.walkSport(document, {
    sportId, sportSlug, maxGroups: null, maxCompetitionsPerGroup: null,
    now: () => new Date().toISOString(), sleep, progress,
  }).then((result) => {
    chrome.runtime.sendMessage({ type: 'BET9JA_WALK_DONE', run_id: runId, sport: sportSlug, started, result });
  }, (err) => {
    chrome.runtime.sendMessage({ type: 'BET9JA_WALK_DONE', run_id: runId, sport: sportSlug, started,
      error: err && err.message ? err.message : String(err) });
  });
  return { ok: true, sport: sportSlug };
}

async function injectWalker(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['catalogue_parser.js', 'fixture_parser.js', 'sport_walker.js'] });
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

// -------------------------------------------------------------- the run
//
// A run is a list of sports in storage, driven by events rather than by one
// long-lived function: start a sport, return; when the page reports the
// sport (BET9JA_WALK_DONE) or its timeout alarm fires, record the outcome
// and start the next. Chrome may suspend and restart this worker at any
// time during a sport, and the run carries on regardless.

const SPORT_ALARM = 'bet9ja:sport';

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

const current = (state) => (state.sports || []).find((s) => !s.status);

async function scheduledWalk(trigger = 'schedule') {
  const schedule = await loadSchedule();
  if (trigger === 'schedule' && !schedule.enabled) return;
  const running = await loadState();
  if (running && running.status === 'RUNNING' && Date.now() - Date.parse(running.updated_at_utc || 0) < STALE_RUN_MS) {
    return;                                         // one run at a time
  }
  const state = { run_id: Date.now().toString(36), status: 'RUNNING', trigger, started_at_utc: nowIso(),
    sports: (schedule.sports || DEFAULT_SPORTS).map((sport) => ({ sport })), tab_created: false };
  await saveState(state);
  let tab;
  try {
    tab = await bet9jaTab(schedule.start_url);
  } catch (err) {
    return endRun(state, 'FAILED', err && err.message ? err.message : String(err));
  }
  const cur = await loadState();                    // the popup may have stopped it while the tab opened
  if (!cur || cur.run_id !== state.run_id || cur.status !== 'RUNNING') {
    if (tab.created) chrome.tabs.remove(tab.tabId).catch(() => {});
    return;
  }
  cur.tab_created = tab.created;
  cur.tab_id = tab.tabId;
  await saveState(cur);
  await startSport(cur);
}

// Starts the next sport of the run, or ends the run when none is left.
async function startSport(stale) {
  const state = (await loadState()) || stale;       // always the stored state: the popup may have changed it
  if (state.run_id !== stale.run_id || state.status !== 'RUNNING') return;
  const entry = current(state);
  if (!entry) return endRun(state, 'COMPLETE');
  entry.started_at_utc = nowIso();
  await saveState(state);
  await chrome.alarms.create(SPORT_ALARM, { delayInMinutes: Math.max(0.5, SPORT_TIMEOUT_MS / 60000) });
  const sportId = SPORT_IDS[entry.sport];
  if (!sportId) return sportDone(state.run_id, entry.sport, { error: `unknown sport ${entry.sport}` });
  try {
    await injectWalker(state.tab_id);
    await chrome.scripting.executeScript({ target: { tabId: state.tab_id }, func: walkInPage, args: [sportId, entry.sport, state.run_id] });
  } catch (err) {
    await sportDone(state.run_id, entry.sport, { error: err && err.message ? err.message : String(err) });
  }
}

// Records one sport's outcome (capture, error or timeout) and moves on.
async function sportDone(runId, sport, outcome) {
  const state = await loadState();
  if (!state || state.run_id !== runId) return false;
  const entry = (state.sports || []).find((s) => s.sport === sport && !s.status);
  if (!entry) return false;                         // already recorded (a late or duplicate report)
  await chrome.alarms.clear(SPORT_ALARM);
  if (outcome.error) {
    entry.status = 'failed';
    entry.detail = outcome.error;
  } else {
    entry.status = outcome.result.capture_status;
    entry.detail = summarize(outcome.result);
    const sent = await uploadCapture(outcome.result, sport);
    entry.uploaded = sent.uploaded;
    entry.upload = sent.message;
    if (!sent.uploaded) {                           // keep it: the popup can download it
      await chrome.storage.local.set({ [`bet9jaCapture:${runId}:${sport}`]: outcome.result });
      entry.stored = true;
    }
  }
  entry.finished_at_utc = nowIso();
  await saveState(state);
  if (state.status !== 'RUNNING') return true;      // stopped from the popup: this sport was the last
  if (outcome.error && /No tab with id|tab was closed/i.test(outcome.error)) return endRun(state, 'FAILED', 'The Bet9ja tab was closed.');
  if (outcome.error && /no result after/.test(outcome.error)) {
    // The walk may still be clicking in the page: reload the tab so the
    // next sport never shares a page with it.
    const schedule = await loadSchedule();
    try {
      await chrome.tabs.update(state.tab_id, { url: schedule.start_url || DEFAULT_START_URL });
      await waitForTab(state.tab_id);
      await new Promise((r) => setTimeout(r, T.settle));
    } catch (_) { /* tab gone: the next sport reports it */ }
  }
  await startSport(state);
  return true;
}

async function endRun(state, status, message) {
  await chrome.alarms.clear(SPORT_ALARM);
  const cur = await loadState();
  if (cur && cur.run_id === state.run_id && cur.status === 'RUNNING') {   // not stopped from the popup meanwhile
    state.status = status;
    if (message) state.message = message;
    state.finished_at_utc = nowIso();
    await saveState(state);
  }
  if (state.tab_created && state.tab_id) chrome.tabs.remove(state.tab_id).catch(() => {});
  return true;
}

// A sport that never reported within SPORT_TIMEOUT_MS.
async function sportTimedOut() {
  const state = await loadState();
  const entry = state && state.status === 'RUNNING' && current(state);
  if (entry) await sportDone(state.run_id, entry.sport, { error: `no result after ${SPORT_TIMEOUT_MS / 60000} min` });
}

// A walk's result arriving by message: a run's sport, or a manual walk
// from the popup (uploaded here; captures the app did not take stay for
// download, the popup holds the result).
async function handleWalkDone(msg) {
  const outcome = msg.result ? { result: msg.result } : { error: msg.error || 'walk failed' };
  if (await sportDone(msg.run_id, msg.sport, outcome)) return { handled: true };
  if (!msg.result) return { uploaded: false, message: msg.error || 'walk failed' };
  const sent = await uploadCapture(msg.result, msg.sport);
  const state = (await loadState()) || { sports: [] };
  state.last_upload = { sport: msg.sport, at_utc: nowIso(), ...sent };
  await saveState(state);
  return sent;
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'bet9ja:walk') scheduledWalk('schedule');
  if (alarm.name === SPORT_ALARM) sportTimedOut();
});

// On a worker start: keep the schedule alarm, and pick a run back up. A
// sport still walking in the page reports on its own; one whose timeout
// alarm was lost gets it back, and one whose tab is gone ends the run.
(async () => {
  const schedule = await loadSchedule();
  if (schedule.enabled && !(await chrome.alarms.get('bet9ja:walk'))) await applySchedule(schedule);
  const state = await loadState();
  const entry = state && state.status === 'RUNNING' && current(state);
  if (!entry) return;
  try { await chrome.tabs.get(state.tab_id); } catch (_) { return endRun(state, 'FAILED', 'The Bet9ja tab was closed.'); }
  if (!entry.started_at_utc) return startSport(state);
  const left = SPORT_TIMEOUT_MS - (Date.now() - Date.parse(entry.started_at_utc));
  if (left <= 0) return sportTimedOut();
  if (!(await chrome.alarms.get(SPORT_ALARM))) await chrome.alarms.create(SPORT_ALARM, { delayInMinutes: Math.max(0.5, left / 60000) });
})();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const reply = (p) => p.then((value) => sendResponse({ ok: true, ...value }))
    .catch((err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err) }));
  if (message.type === 'BET9JA_SLEEP') {          // a page's wait, done here where timers are not throttled
    setTimeout(() => sendResponse({ ok: true }), Math.max(0, Math.min(Number(message.ms) || 0, 60000)));
    return true;
  }
  if (message.type === 'BET9JA_PROGRESS') {
    reply((async () => {
      const state = await loadState();
      const entry = state && state.run_id === message.run_id && state.sports.find((s) => s.sport === message.sport && !s.status);
      if (entry) {
        entry.progress = `${message.group} › ${message.competition} (country ${message.group_index} of ${message.groups}, `
          + `${message.attempted} competitions so far)`;
        await saveState(state);
      }
      return {};
    })());
    return true;
  }
  if (message.type === 'BET9JA_WALK_DONE') { reply(handleWalkDone(message)); return true; }
  if (message.type === 'STOP_BET9JA_RUN') {
    reply((async () => {
      const state = await loadState();
      if (state && state.status === 'RUNNING') {
        state.status = 'STOPPED';
        state.message = 'stopped by you; a sport still walking in the page is sent to the app when it finishes';
        await saveState(state);
        await chrome.alarms.clear(SPORT_ALARM);
        if (state.tab_created && state.tab_id) chrome.tabs.remove(state.tab_id).catch(() => {});
      }
      return { state };
    })());
    return true;
  }
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
