importScripts('source_registry.js', 'odds_page.js');

/* The walk runs here, not in the popup: the popup only starts, stops,
 * resumes and shows progress. State is saved after every page (small run
 * record + one storage entry per captured page), so a closed popup, a
 * suspended service worker or a browser restart loses nothing; the run
 * resumes from its cursor. One run at a time. */

const RUN_KEY = 'publicOddsWalkRun';
const CAP_PREFIX = 'publicOddsWalkCapture:';
const MAX_CAPTURES = 300;
const MAX_LISTINGS = 40;
// Timings; tests shorten them through globalThis.WALKER_TIMING.
const T = Object.assign({ listing: 3000, ready: 15000, poll: 1000, settleCap: 2000, search: 8000 }, globalThis.WALKER_TIMING || {});
const LISTING_SETTLE_MS = T.listing;
const READY_TIMEOUT_MS = T.ready;   // stop waiting for a page's odds after this
const READY_POLL_MS = T.poll;
const STALE_MS = 90000;           // a RUNNING run not updated for this long was interrupted
const DAYS_AHEAD = 1;
const APP_TARGETS = ['http://localhost:8000/walker-targets.json', 'http://127.0.0.1:8000/walker-targets.json'];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const nowIso = () => new Date().toISOString();
let driving = false;

// ------------------------------------------------------------- page access

// Listen before navigating, so a fast load cannot complete unseen (audit D9).
function navigate(tabId, url, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    function listener(updatedTabId, info) {
      if (updatedTabId === tabId && info.status === 'complete') finish(true);
    }
    chrome.tabs.onUpdated.addListener(listener);
    chrome.tabs.update(tabId, { url }).catch((err) => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      reject(err);
    });
  });
}

async function injectPageTools(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['source_registry.js', 'odds_page.js'] });
}

async function callPage(tabId, action, scope = 'sports', sourceKey = '') {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (actionName, scopeName, key) => {
      const P = window.PublicOddsPage;
      const context = { href: location.href, title: document.title, capturedAtUtc: new Date().toISOString() };
      if (actionName === 'discover') return P.discoverEventLinks(document, context.href, scopeName);
      if (actionName === 'listings') return P.discoverListingLinks(document, context.href);
      if (actionName === 'ready') return P.readiness(document, key, Date.now());
      if (actionName === 'result') return P.resultState(document);
      return P.captureCurrentPage(document, context, actionName === 'capture-excerpt' ? 'excerpt' : 'full');
    },
    // chrome.scripting rejects undefined in args ("Value is unserializable").
    args: [String(action), scope == null ? 'sports' : String(scope), String(sourceKey || '')],
  });
  return result;
}

// Wait until the odds have rendered, the match turns out to have started,
// or the timeout passes (audit D7, D10).
async function waitReady(tabId, sourceKey) {
  const until = Date.now() + READY_TIMEOUT_MS;
  let last = { state: 'WAITING', detail: 'not checked' };
  while (Date.now() < until) {
    await injectPageTools(tabId);
    last = await callPage(tabId, 'ready', 'sports', sourceKey);
    if (last.state !== 'WAITING') return last;
    await sleep(READY_POLL_MS);
  }
  return { state: 'NO_ODDS', detail: `no odds after ${READY_TIMEOUT_MS / 1000}s (${last.detail})` };
}

// Results mode: wait until the final result has rendered, or the page says
// the match was not played, or the timeout passes.
async function waitResult(tabId) {
  const until = Date.now() + READY_TIMEOUT_MS;
  let last = { state: 'WAITING', detail: 'not checked' };
  while (Date.now() < until) {
    await injectPageTools(tabId);
    last = await callPage(tabId, 'result');
    if (last.state !== 'WAITING') return last;
    await sleep(READY_POLL_MS);
  }
  return { ...last, state: 'NO_RESULT', detail: `no final result after ${READY_TIMEOUT_MS / 1000}s (${last.detail})` };
}

// ------------------------------------------------------------------ storage

async function loadRun() { return (await chrome.storage.local.get(RUN_KEY))[RUN_KEY] || null; }
async function saveRun(run) { run.updated_at_utc = nowIso(); await chrome.storage.local.set({ [RUN_KEY]: run }); }

async function clearOldCaptures() {
  const all = await chrome.storage.local.get(null);
  const keys = Object.keys(all).filter((k) => k.startsWith(CAP_PREFIX));
  if (keys.length) await chrome.storage.local.remove(keys);
}

async function storeCapture(run, capture) {
  const key = `${CAP_PREFIX}${run.run_id}:${String(run.counts.captured).padStart(4, '0')}`;
  await chrome.storage.local.set({ [key]: capture });
  run.counts.captured += 1;
}

// The full run file, assembled for download.
async function assembledRun() {
  const run = await loadRun();
  if (!run) return null;
  const all = await chrome.storage.local.get(null);
  const prefix = `${CAP_PREFIX}${run.run_id}:`;
  const captures = Object.keys(all).filter((k) => k.startsWith(prefix)).sort().map((k) => all[k]);
  const { queue, ...rest } = run;
  return { ...rest, captures };
}

function summary(run) {
  if (!run) return null;
  const stale = run.status === 'RUNNING' && Date.now() - Date.parse(run.updated_at_utc || 0) > STALE_MS;
  return { run_id: run.run_id, mode: run.mode || 'odds', status: stale ? 'INTERRUPTED' : run.status, message: run.message || '',
    source_key: run.source_key, seed_url: run.seed_url, counts: run.counts, cursor: run.cursor,
    queued: (run.queue || []).length, targets_status: run.targets_status || '', downloaded: !!run.downloaded,
    completed_at_utc: run.completed_at_utc || null };
}

// -------------------------------------------------------------- the walk

async function appTargetList() {
  for (const url of APP_TARGETS) {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (r.ok) return await r.json();
    } catch (_) { /* app not running on this address */ }
  }
  return null;
}

async function appTargets() {
  const t = await appTargetList();
  return t ? t.fixtures || [] : null;
}

// Results mode: visit the finished games the app is still waiting on (it
// lists their OddsPortal match pages) and copy each final result.
async function beginResults(tabId, maxEvents) {
  const s = summary(await loadRun());
  if (s && s.status === 'RUNNING') throw new Error('A walk is already running. Stop it first, or wait for it to finish.');
  const tab = await chrome.tabs.get(tabId);
  const source = PublicOddsSources.findSource(tab.url || '');
  if (!source || source.key !== 'oddsportal') throw new Error('Open any oddsportal.com page in this tab first.');
  const list = await appTargetList();
  if (list === null) throw new Error('The PredictBot app is not reachable on localhost:8000. Start it, then try again.');
  const targets = list.results || [];
  if (!targets.length) throw new Error('No finished games are waiting for an OddsPortal result.');
  await clearOldCaptures();
  const cap = Math.min(Number(maxEvents) || 10, MAX_CAPTURES);
  const run = {
    schema_version: 'public-odds-capture-walk.v1', walker_version: chrome.runtime.getManifest().version,
    mode: 'results', run_id: Date.now().toString(36), status: 'RUNNING', capture_status: 'RUNNING',
    source_key: 'oddsportal', started_at_utc: nowIso(), seed_url: tab.url, tab_id: tabId,
    browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    browser_utc_offset_minutes: -new Date().getTimezoneOffset(),
    targets_status: `${targets.length} finished games wait for a result`,
    discovered_links: [], skipped: [], failures: [], cursor: 0,
    queue: targets.slice(0, cap).map((t) => ({ url: t.url, label_raw: `${t.home} - ${t.away}`, target: t })),
    counts: { discovered: targets.length, queued: 0, visited: 0, captured: 0, results: 0, not_final: 0, no_result: 0,
              failed: 0, truncated: Math.max(0, targets.length - cap) },
  };
  run.counts.queued = run.queue.length;
  await saveRun(run);
  drive();
  return summary(run);
}

// Search mode: for every Bet9ja game the app still lacks a benchmark for,
// search OddsPortal for one of its teams and visit the matching game right
// after, so games outside any one listing page are found too.
const SEARCH_URL = (term) => `https://www.oddsportal.com/search/${encodeURIComponent(term)}/`;

// OddsPortal's search matches names as written and shows only a few hits,
// so try the most specific words first: a player's whole surname, then each
// distinctive word of the home team (longest first), then the away team's.
const MAX_TERMS = 4;
function searchTerms(target) {
  const words = (name) => PublicOddsPage.nameTokens(name || '').sort((a, b) => b.length - a.length);
  const surname = (name) => (name && name.includes(',') ? PublicOddsPage.nameTokens(name.split(',')[0]).join(' ') : '');
  const terms = [surname(target.home), ...words(target.home), surname(target.away), ...words(target.away)];
  return [...new Set(terms.filter((t) => t && t.length >= 3))].slice(0, MAX_TERMS);
}

async function beginSearch(tabId, maxEvents) {
  const s = summary(await loadRun());
  if (s && s.status === 'RUNNING') throw new Error('A walk is already running. Stop it first, or wait for it to finish.');
  const tab = await chrome.tabs.get(tabId);
  const source = PublicOddsSources.findSource(tab.url || '');
  if (!source || source.key !== 'oddsportal') throw new Error('Open any oddsportal.com page in this tab first.');
  const list = await appTargetList();
  if (list === null) throw new Error('The PredictBot app is not reachable on localhost:8000. Start it, then try again.');
  const targets = list.fixtures || [];
  if (!targets.length) throw new Error('Every Bet9ja game already has a benchmark (or none is captured yet).');
  const queue = targets.map((t, i) => ({ terms: searchTerms(t), i })).filter((x) => x.terms.length)
    .map(({ terms, i }) => ({ kind: 'search', url: SEARCH_URL(terms[0]), label_raw: terms[0], target: i, rest: terms.slice(1) }));
  await clearOldCaptures();
  const run = {
    schema_version: 'public-odds-capture-walk.v1', walker_version: chrome.runtime.getManifest().version,
    mode: 'search', run_id: Date.now().toString(36), status: 'RUNNING', capture_status: 'RUNNING',
    source_key: 'oddsportal', started_at_utc: nowIso(), seed_url: tab.url, tab_id: tabId,
    browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    browser_utc_offset_minutes: -new Date().getTimezoneOffset(),
    targets_status: `${targets.length} Bet9ja games without a benchmark`,
    targets, seen_keys: [], max_events: Math.min(Number(maxEvents) || 10, MAX_CAPTURES),
    discovered_links: [], skipped: [], failures: [], cursor: 0,
    queue,
    counts: { games: queue.length, found: 0, searched: 0, no_results: 0, discovered: 0, queued: 0, visited: 0, captured: 0,
              started: 0, no_odds: 0, failed: 0, not_on_bet9ja: 0, duplicate: 0, truncated: 0 },
  };
  await saveRun(run);
  drive();
  return summary(run);
}

async function visitSearch(run, search) {
  let links = [];
  const until = Date.now() + T.search;
  while (Date.now() < until) {                     // results render after the page loads
    await injectPageTools(run.tab_id);
    links = (await callPage(run.tab_id, 'discover', 'sports')).links || [];
    if (links.length) break;
    await sleep(READY_POLL_MS);
  }
  run.counts.searched += 1;
  if (!links.length) run.counts.no_results += 1;
  const own = run.targets[search.target];
  const hit = own && links.some((l) => PublicOddsPage.matchesTarget(l, [own], 'oddsportal'));
  if (hit) run.counts.found += 1;
  const seen = new Set(run.seen_keys);
  let at = run.cursor + 1;
  if (!hit && search.rest && search.rest.length) {    // try the game's next word right away
    run.queue.splice(at, 0, { ...search, url: SEARCH_URL(search.rest[0]), label_raw: search.rest[0], rest: search.rest.slice(1) });
    at += 1;
  }
  for (const link of links) {
    const key = PublicOddsPage.eventKey(link.url);
    if (seen.has(key)) { run.counts.duplicate += 1; continue; }
    seen.add(key);
    run.counts.discovered += 1;
    if (!PublicOddsPage.matchesTarget(link, run.targets, 'oddsportal')) { run.counts.not_on_bet9ja += 1; continue; }
    if (run.counts.queued >= run.max_events) { run.counts.truncated += 1; continue; }
    run.queue.splice(at, 0, { kind: 'event', url: link.url, label_raw: link.label_raw });   // visit it next
    at += 1;
    run.counts.queued += 1;
    run.discovered_links.push(link);
  }
  run.seen_keys = [...seen];
}

async function visitOdds(run, target, source) {
  const ready = await waitReady(run.tab_id, run.source_key);
  run.counts.visited += 1;
  if (ready.state === 'READY') {
    await injectPageTools(run.tab_id);
    const captured = await callPage(run.tab_id, source && source.excerptPattern ? 'capture-excerpt' : 'capture');
    await storeCapture(run, { role: 'event', discovered_label_raw: target.label_raw, readiness: ready.detail, ...captured });
  } else {
    run.counts[ready.state === 'STARTED' ? 'started' : 'no_odds'] += 1;
    run.skipped.push({ url: target.url, state: ready.state, detail: ready.detail, at_utc: nowIso() });
  }
}

async function visitResult(run, target) {
  const ready = await waitResult(run.tab_id);
  run.counts.visited += 1;
  if (ready.state === 'FINAL') {
    await storeCapture(run, { role: 'result', capture_status: 'RESULT_OK', source_url: target.url,
      captured_at_utc: nowIso(), final_result_raw: ready.final_result_raw, kickoff_raw: ready.kickoff_raw,
      target: target.target || null });
    run.counts.results += 1;
  } else {
    run.counts[ready.state === 'NOT_FINAL' ? 'not_final' : 'no_result'] += 1;
    run.skipped.push({ url: target.url, state: ready.state, detail: ready.detail, at_utc: nowIso() });
  }
}

async function begin(tabId, maxEvents, scope, onlyBet9ja) {
  const current = await loadRun();
  const s = summary(current);
  if (s && s.status === 'RUNNING') throw new Error('A walk is already running. Stop it first, or wait for it to finish.');
  const tab = await chrome.tabs.get(tabId);
  const startUrl = tab.url || null;
  const source = PublicOddsSources.findSource(startUrl || '');
  if (!source) throw new Error(`Open a supported public source first (this tab is ${startUrl ? new URL(startUrl).hostname : 'not a web page'}).`);
  await clearOldCaptures();
  const run = {
    schema_version: 'public-odds-capture-walk.v1', walker_version: chrome.runtime.getManifest().version,
    run_id: Date.now().toString(36), status: 'RUNNING', capture_status: 'RUNNING',
    source_key: source.key, started_at_utc: nowIso(), seed_url: startUrl, tab_id: tabId,
    // Pages show kickoff in the browser's local time; the app converts it to UTC.
    browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    browser_utc_offset_minutes: -new Date().getTimezoneOffset(),
    discovered_links: [], skipped: [], failures: [], queue: [], cursor: 0,
    counts: { discovered: 0, queued: 0, visited: 0, captured: 0, started: 0, no_odds: 0, failed: 0,
              truncated: 0, not_on_bet9ja: 0, other_sport: 0, duplicate: 0 },
  };
  await saveRun(run);

  await injectPageTools(tabId);
  await storeCapture(run, { role: 'seed', ...(await callPage(tabId, 'capture')) });
  const found = new Map();
  const add = (links) => (links || []).forEach((l) => {
    const key = PublicOddsPage.eventKey(l.url);
    if (found.has(key)) run.counts.duplicate += 1; else found.set(key, l);
  });
  const discovery = await callPage(tabId, 'discover', scope);
  run.discovery_status = discovery.discovery_status || null;
  add(discovery.links);
  if (source.isListingUrl) {                       // Polymarket catalogue stage
    run.listings = [];
    const listings = ((await callPage(tabId, 'listings')).links || []).slice(0, MAX_LISTINGS);
    for (const listing of listings) {
      try {
        if (!await navigate(tabId, listing.url)) throw new Error('PAGE_LOAD_TIMEOUT');
        await sleep(LISTING_SETTLE_MS);
        await injectPageTools(tabId);
        const d = await callPage(tabId, 'discover', scope);
        add(d.links);
        run.listings.push({ url: listing.url, event_links: (d.links || []).length });
      } catch (err) {
        run.failures.push({ url: listing.url, stage: 'listing', reason: err && err.message ? err.message : String(err) });
      }
    }
  }
  let links = [...found.values()].filter((l) => PublicOddsPage.withinDays(l.url, nowIso(), DAYS_AHEAD));
  run.counts.discovered = links.length;
  // Stay in the seed's sport (a volleyball listing links to basketball pages).
  const seedSport = PublicOddsPage.sportOf(startUrl, source.key);
  if (seedSport) {
    const kept = links.filter((l) => PublicOddsPage.sportOf(l.url, source.key) === seedSport);
    run.counts.other_sport = links.length - kept.length;
    links = kept;
  }
  // Visit only games Bet9ja offers, using the running PredictBot app's list.
  if (onlyBet9ja) {
    const targets = await appTargets();
    if (targets === null) {
      run.targets_status = 'PredictBot app not reachable on localhost:8000: walking every listed game';
    } else {
      const kept = links.filter((l) => PublicOddsPage.matchesTarget(l, targets, source.key));
      run.counts.not_on_bet9ja = links.length - kept.length;
      run.targets_status = `${targets.length} Bet9ja fixtures without a benchmark; ${kept.length} of ${links.length} listed games match`;
      links = kept;
    }
  } else {
    run.targets_status = 'walking every listed game (Bet9ja filter off)';
  }
  const cap = Math.min(Number(maxEvents) || 10, MAX_CAPTURES);
  run.counts.truncated = Math.max(0, links.length - cap);
  run.discovered_links = links;
  run.queue = links.slice(0, cap);
  run.counts.queued = run.queue.length;
  await saveRun(run);
  drive();
  return summary(run);
}

async function drive() {
  if (driving) return;
  driving = true;
  try {
    let run = await loadRun();
    const source = run && PublicOddsSources.SOURCES.find((s) => s.key === run.source_key);
    while (run && run.status === 'RUNNING' && run.cursor < run.queue.length) {
      const target = run.queue[run.cursor];
      try {
        if (!await navigate(run.tab_id, target.url)) throw new Error('PAGE_LOAD_TIMEOUT');
        if (source && source.settleMs) await sleep(Math.min(source.settleMs, T.settleCap));
        if (run.mode === 'results') await visitResult(run, target);
        else if (target.kind === 'search') await visitSearch(run, target);
        else await visitOdds(run, target, source);
      } catch (err) {
        const msg = err && err.message ? err.message : String(err);
        if (/No tab with id|tab was closed|Tabs cannot be edited/i.test(msg)) {
          run.status = 'INTERRUPTED';
          run.message = 'The walker tab was closed. Open the same site in a tab, then click Resume.';
          await saveRun(run);
          return;
        }
        run.counts.failed += 1;
        run.failures.push({ url: target.url, reason: msg, at_utc: nowIso() });
      }
      run.cursor += 1;
      const latest = await loadRun();                // the popup may have stopped the run
      if (latest && latest.status === 'STOPPED') { run.status = 'STOPPED'; run.message = 'Stopped by you.'; }
      await saveRun(run);
    }
    if (run && (run.status === 'RUNNING' || run.status === 'STOPPED')) {
      if (run.status === 'RUNNING') run.status = 'COMPLETE';
      run.capture_status = run.failures.length || run.status === 'STOPPED' ? 'CAPTURE_PARTIAL' : 'CAPTURE_OK';
      run.completed_at_utc = nowIso();
      await saveRun(run);
    }
  } finally {
    driving = false;
  }
}

async function resume(tabId) {
  const run = await loadRun();
  const s = summary(run);
  if (!run || !['INTERRUPTED', 'STOPPED'].includes(s.status)) throw new Error('There is no interrupted walk to resume.');
  if (run.cursor >= run.queue.length) throw new Error('That walk already reached its last page.');
  run.status = 'RUNNING';
  run.message = '';
  if (tabId) run.tab_id = tabId;
  await saveRun(run);
  drive();
  return summary(run);
}

// A run left RUNNING by a suspended or restarted service worker resumes on
// its own when the worker wakes, if its tab still exists.
(async () => {
  const run = await loadRun();
  if (run && run.status === 'RUNNING' && run.cursor < (run.queue || []).length) {
    try { await chrome.tabs.get(run.tab_id); drive(); } catch (_) {
      run.status = 'INTERRUPTED';
      run.message = 'The browser restarted. Open the same site in a tab, then click Resume.';
      await saveRun(run);
    }
  }
})();

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const reply = (p) => p.then((value) => sendResponse({ ok: true, ...value }))
    .catch((err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err) }));
  if (message.type === 'START_PUBLIC_ODDS_WALK') {
    reply(begin(message.tabId, message.maxEvents, message.scope || 'sports', message.onlyBet9ja !== false)
      .then((run) => ({ run })));
    return true;
  }
  if (message.type === 'START_SEARCH_WALK') {
    reply(beginSearch(message.tabId, message.maxEvents).then((run) => ({ run })));
    return true;
  }
  if (message.type === 'START_RESULTS_WALK') {
    reply(beginResults(message.tabId, message.maxEvents).then((run) => ({ run })));
    return true;
  }
  if (message.type === 'GET_PUBLIC_ODDS_STATUS') { reply(loadRun().then((run) => ({ run: summary(run) }))); return true; }
  if (message.type === 'GET_PUBLIC_ODDS_RUN') { reply(assembledRun().then((run) => ({ run }))); return true; }
  if (message.type === 'STOP_PUBLIC_ODDS_WALK') {
    reply(loadRun().then(async (run) => {
      if (run && run.status === 'RUNNING') { run.status = 'STOPPED'; run.message = 'Stopping after the current page.'; await saveRun(run); }
      return { run: summary(run) };
    }));
    return true;
  }
  if (message.type === 'RESUME_PUBLIC_ODDS_WALK') { reply(resume(message.tabId).then((run) => ({ run }))); return true; }
  if (message.type === 'MARK_DOWNLOADED') {
    reply(loadRun().then(async (run) => { if (run) { run.downloaded = true; await saveRun(run); } return { run: summary(run) }; }));
    return true;
  }
  return false;
});
