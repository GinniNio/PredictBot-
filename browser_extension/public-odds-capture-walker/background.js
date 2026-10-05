importScripts('source_registry.js', 'odds_page.js');

const RUN_KEY = 'publicOddsCaptureRun';
const MAX_CAPTURES = 300;
const MAX_LISTINGS = 40;
const LISTING_SETTLE_MS = 3000;   // [UNVERIFIED] time for client-rendered cards to appear
const DAYS_AHEAD = 1;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function waitForTabComplete(tabId, timeoutMs = 20000) {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => finish(false), timeoutMs);
    function listener(updatedTabId, info) {
      if (updatedTabId === tabId && info.status === 'complete') finish(true);
    }
    function finish(ok) {
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(ok);
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function injectPageTools(tabId) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ['source_registry.js', 'odds_page.js'] });
}

async function callPage(tabId, action, scope = 'sports') {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: (actionName, scopeName) => {
      const context = { href: location.href, title: document.title, capturedAtUtc: new Date().toISOString() };
      if (actionName === 'discover') return window.PublicOddsPage.discoverEventLinks(document, context.href, scopeName);
      if (actionName === 'listings') return window.PublicOddsPage.discoverListingLinks(document, context.href);
      return window.PublicOddsPage.captureCurrentPage(document, context, actionName === 'capture-excerpt' ? 'excerpt' : 'full');
    },
    // chrome.scripting rejects undefined in args ("Value is unserializable"),
    // so every argument must be a concrete JSON value.
    args: [String(action), scope == null ? 'sports' : String(scope)],
  });
  return result;
}

async function saveRun(run) {
  await chrome.storage.local.set({ [RUN_KEY]: run });
}

async function walk(tabId, maxEvents, scope) {
  const tab = await chrome.tabs.get(tabId);
  const startUrl = tab.url || null;
  const source = PublicOddsSources.findSource(startUrl || '');
  if (!source) throw new Error(`Open a supported public source first (this tab is ${startUrl ? new URL(startUrl).hostname : 'not a web page'}).`);
  const run = {
    schema_version: 'public-odds-capture-walk.v1', capture_status: 'RUNNING',
    source_key: source.key, started_at_utc: new Date().toISOString(), seed_url: startUrl,
    discovered_links: [], captures: [], failures: [],
    // Pages show kickoff in the browser's local time; record the offset so
    // the app can convert it to UTC.
    browser_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    browser_utc_offset_minutes: -new Date().getTimezoneOffset(),
  };
  await injectPageTools(tabId);
  const seed = await callPage(tabId, 'capture');
  run.captures.push({ role: 'seed', ...seed });
  const discovery = await callPage(tabId, 'discover', scope);
  run.discovery_status = discovery.discovery_status || null;
  const found = new Map((discovery.links || []).map((l) => [l.url, l]));
  // Catalogue stage (Polymarket): visit every sport listing linked from the
  // seed's navigation and collect its event links too.
  if (source.isListingUrl) {
    const listings = ((await callPage(tabId, 'listings')).links || []).slice(0, MAX_LISTINGS);
    run.listings = [];
    for (const listing of listings) {
      try {
        await chrome.tabs.update(tabId, { url: listing.url });
        if (!await waitForTabComplete(tabId)) throw new Error('PAGE_LOAD_TIMEOUT');
        await sleep(LISTING_SETTLE_MS);
        await injectPageTools(tabId);
        const d = await callPage(tabId, 'discover', scope);
        const links = d.links || [];
        links.forEach((l) => { if (!found.has(l.url)) found.set(l.url, l); });
        run.listings.push({ url: listing.url, event_links: links.length });
      } catch (err) {
        run.failures.push({ url: listing.url, stage: 'listing', reason: err && err.message ? err.message : String(err) });
      }
    }
  }
  const today = new Date().toISOString();
  run.discovered_links = [...found.values()].filter((l) => PublicOddsPage.withinDays(l.url, today, DAYS_AHEAD));
  const targets = run.discovered_links.slice(0, Math.min(Number(maxEvents) || 10, MAX_CAPTURES));
  for (const target of targets) {
    try {
      await chrome.tabs.update(tabId, { url: target.url });
      if (!await waitForTabComplete(tabId)) throw new Error('PAGE_LOAD_TIMEOUT');
      // Odds tables render after load (OddsPortal: 4 of 55 match pages had
      // their table when captured immediately on 2026-10-05).
      if (source.settleMs) await sleep(source.settleMs);
      await injectPageTools(tabId);
      const captured = await callPage(tabId, source.excerptPattern ? 'capture-excerpt' : 'capture');
      run.captures.push({ role: 'event', discovered_label_raw: target.label_raw, ...captured });
      await saveRun(run);
    } catch (err) {
      run.failures.push({ url: target.url, reason: err && err.message ? err.message : String(err) });
      await saveRun(run);
    }
  }
  run.capture_status = run.failures.length ? 'CAPTURE_PARTIAL' : 'CAPTURE_OK';
  run.completed_at_utc = new Date().toISOString();
  await saveRun(run);
  return run;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'START_PUBLIC_ODDS_WALK') {
    walk(message.tabId, message.maxEvents, message.scope || 'sports').then((run) => sendResponse({ ok: true, run })).catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message.type === 'GET_PUBLIC_ODDS_RUN') {
    chrome.storage.local.get(RUN_KEY).then((value) => sendResponse({ ok: true, run: value[RUN_KEY] || null }));
    return true;
  }
});
