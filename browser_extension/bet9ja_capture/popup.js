/**
 * Popup click handler -- the ONLY entry point that ever runs capture code.
 * Nothing in this extension executes on page load, on a schedule, or in a
 * background service worker; everything below is triggered directly by a
 * button click, which is also what makes the (click-gated) `activeTab`
 * permission usable at all.
 *
 * Permission contract: this file only ever calls chrome.tabs, chrome.
 * scripting, chrome.downloads, and (ONE exception, see below)
 * chrome.storage.local -- matching manifest.json's permissions exactly
 * (activeTab, scripting, downloads, storage). No chrome.cookies, no
 * host_permissions, no fetch/XMLHttpRequest of any kind. Because there is
 * otherwise no persistent storage, duplicate_status for every button
 * EXCEPT "Capture all Soccer fixtures" always starts fresh (NEW /
 * DUPLICATE_WITHIN_CAPTURE only) across separate captures -- cross-capture
 * "have I seen this odds change before" history is intentionally deferred
 * to the ledger's own idempotent re-import (ledgers.forecast_ledger, keyed
 * by the same deterministic fixture_id), not duplicated here.
 *
 * The ONE exception: "Capture all Soccer fixtures" persists its OWN
 * checkpoint ledger (competition ids, statuses, and fixtures already
 * captured -- never cookies, tokens, or account data) to
 * chrome.storage.local under one fixed key, so a crash, an accidental
 * popup close, or a genuinely failed competition never costs the whole
 * multi-hundred-competition walk's prior progress. See soccer_session.js's
 * own header comment for the full checkpointing design and resume
 * authority (a competition's own stable id, never a numeric position).
 */
const button = document.getElementById('capture-button');
const statusEl = document.getElementById('status');

// Defense-in-depth against a double-click or a stray second invocation:
// this flag, plus disabling the button as the very first synchronous
// statement in the handler below, means a second click can produce at
// most a no-op, never a second concurrent capture/download.
let captureInFlight = false;

function setStatus(cssClass, text) {
  statusEl.className = cssClass;
  statusEl.textContent = text;
}

function timestampForFilename(isoTimestamp) {
  // e.g. "2024-08-17T20:45:03.123Z" -> "2024-08-17T20-45-03Z" (filesystem-safe)
  return isoTimestamp.replace(/\.\d+Z$/, 'Z').replace(/:/g, '-');
}

async function triggerDownload(filename, jsonText) {
  const blob = new Blob([jsonText], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  try {
    await chrome.downloads.download({ url, filename, saveAs: false });
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 15000);
  }
}

async function runCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('No active tab found.');
  }

  const capturedAtUtc = new Date().toISOString();

  // Step 1: load the pure parsing modules into the page's isolated world.
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ['ids.js', 'parser.js', 'content.js'],
  });

  // Step 2: invoke the entry point content.js just defined, passing only
  // what's needed to build the envelope -- no host permissions, no
  // cross-tab access, nothing beyond this one active tab's DOM. An empty
  // previousIndex every call (see module docstring above) -- this
  // extension keeps no capture history of its own.
  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (previousIndexArg, sourceUrl, pageTitle, capturedAtUtcArg) =>
      window.__bet9jaCaptureRun(previousIndexArg, sourceUrl, pageTitle, capturedAtUtcArg),
    args: [{}, tab.url || '', tab.title || '', capturedAtUtc],
  });

  const result = injectionResults && injectionResults[0] && injectionResults[0].result;
  if (!result || !result.envelope) {
    throw new Error('Capture produced no result (page may block script injection).');
  }
  return result;
}

button.addEventListener('click', async () => {
  if (captureInFlight) {
    return;
  }
  captureInFlight = true;
  button.disabled = true;
  setStatus('ok', 'Capturing...');
  try {
    const { envelope } = await runCapture();

    const filename = `bet9ja-fixtures-${timestampForFilename(envelope.captured_at_utc)}.json`;
    await triggerDownload(filename, JSON.stringify(envelope, null, 2));

    const summary =
      `${envelope.capture_status}\n` +
      `Sections seen: ${envelope.coverage.sections_seen}\n` +
      `Records seen: ${envelope.coverage.records_seen}\n` +
      `Fixtures normalized: ${envelope.coverage.records_parsed}\n` +
      `Unresolved: ${envelope.coverage.records_unresolved}\n` +
      (envelope.capture_status_reasons.length ? `Reasons: ${envelope.capture_status_reasons.join(', ')}\n` : '') +
      `Saved: ${filename}`;

    const cssClass =
      envelope.capture_status === 'CAPTURE_OK'
        ? 'ok'
        : envelope.capture_status === 'CAPTURE_PARTIAL'
          ? 'partial'
          : 'failed';
    setStatus(cssClass, summary);
  } catch (err) {
    setStatus('error', `Capture failed to run: ${err && err.message ? err.message : String(err)}`);
  } finally {
    button.disabled = false;
    captureInFlight = false;
  }
});

// --- Open-bets ticket capture -----------------------------------------
// Same click-gated, no-storage, no-persistent-history discipline as the
// fixture capture above -- see ticket_parser.js's own header comment for
// scope and the fail-closed ticket-boundary contract this button's output
// depends on.
const ticketButton = document.getElementById('ticket-capture-button');
const ticketStatusEl = document.getElementById('ticket-status');
let ticketCaptureInFlight = false;

function setTicketStatus(cssClass, text) {
  ticketStatusEl.className = cssClass;
  ticketStatusEl.textContent = text;
}

async function runTicketCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('No active tab found.');
  }

  const capturedAtUtc = new Date().toISOString();

  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ['ids.js', 'ticket_parser.js', 'content.js'],
  });

  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (sourceUrl, pageTitle, capturedAtUtcArg) =>
      window.__bet9jaTicketCaptureRun(sourceUrl, pageTitle, capturedAtUtcArg),
    args: [tab.url || '', tab.title || '', capturedAtUtc],
  });

  const result = injectionResults && injectionResults[0] && injectionResults[0].result;
  if (!result || !result.envelope) {
    throw new Error('Ticket capture produced no result (page may block script injection).');
  }
  return result;
}

ticketButton.addEventListener('click', async () => {
  if (ticketCaptureInFlight) {
    return;
  }
  ticketCaptureInFlight = true;
  ticketButton.disabled = true;
  setTicketStatus('ok', 'Capturing open bets...');
  try {
    const { envelope } = await runTicketCapture();

    const filename = `bet9ja-open-bets-${timestampForFilename(envelope.captured_at_utc)}.json`;
    await triggerDownload(filename, JSON.stringify(envelope, null, 2));

    const summary =
      `${envelope.capture_status}\n` +
      `Tickets seen: ${envelope.coverage.tickets_seen}\n` +
      `Tickets parsed: ${envelope.coverage.tickets_parsed}\n` +
      `Tickets unresolved: ${envelope.coverage.tickets_unresolved}\n` +
      `Tickets excluded (out of scope): ${envelope.coverage.tickets_expected_excluded}\n` +
      `Reasons: ${envelope.capture_status_reasons.join(', ')}\n` +
      `Saved: ${filename}`;

    const cssClass =
      envelope.capture_status === 'CAPTURE_OK'
        ? 'ok'
        : envelope.capture_status === 'CAPTURE_PARTIAL'
          ? 'partial'
          : 'failed';
    setTicketStatus(cssClass, summary);
  } catch (err) {
    setTicketStatus('error', `Ticket capture failed to run: ${err && err.message ? err.message : String(err)}`);
  } finally {
    ticketButton.disabled = false;
    ticketCaptureInFlight = false;
  }
});

// --- Capture all Soccer fixtures (durable checkpointed session) --------
// UNLIKE every other button in this file, this flow DOES use
// chrome.storage.local (see manifest.json's "storage" permission) --
// deliberately: soccer_session.js's own header comment explains why a
// long, many-batch walk needs a durable checkpoint (a crash, an
// accidental popup close, or a genuinely failed competition must never
// cost the whole run's prior progress). Nothing captured is ever more
// sensitive than what the other buttons already read (public fixture
// odds); this only persists THIS extension's OWN progress ledger, never
// cookies, tokens, or account data. See soccer_walker.js's own header
// comment for capture scope and the confirmed
// `/sportPage/1/competitions` batch-selector contract.
const SOCCER_SESSION_STORAGE_KEY = 'bet9ja_soccer_session';
const soccerStartNewButton = document.getElementById('soccer-start-new-button');
const soccerResumeButton = document.getElementById('soccer-resume-button');
const soccerRetryFailedButton = document.getElementById('soccer-retry-failed-button');
const soccerDownloadButton = document.getElementById('soccer-download-button');
const soccerClearSessionButton = document.getElementById('soccer-clear-session-button');
const soccerAllCancelButton = document.getElementById('soccer-all-cancel-button');
const soccerAllStatusEl = document.getElementById('soccer-all-status');
const soccerSessionSummaryEl = document.getElementById('soccer-session-summary');
const SOCCER_BUTTONS = [soccerStartNewButton, soccerResumeButton, soccerRetryFailedButton, soccerDownloadButton, soccerClearSessionButton];
let soccerAllCaptureInFlight = false;
let soccerProgressIntervalId = null;

function setSoccerAllStatus(cssClass, text) {
  soccerAllStatusEl.className = cssClass;
  soccerAllStatusEl.textContent = text;
}

async function loadSoccerSession() {
  const data = await chrome.storage.local.get(SOCCER_SESSION_STORAGE_KEY);
  return data[SOCCER_SESSION_STORAGE_KEY] || null;
}

async function saveSoccerSession(sessionObj) {
  await chrome.storage.local.set({ [SOCCER_SESSION_STORAGE_KEY]: sessionObj });
}

async function clearSoccerSessionStorage() {
  await chrome.storage.local.remove(SOCCER_SESSION_STORAGE_KEY);
}

/** Renders the "Completed: 139  Failed: 1  Remaining: 213" ledger summary and enables/disables each action to match what the saved session actually allows. */
function renderSoccerSessionSummary(sessionObj) {
  if (!sessionObj) {
    soccerSessionSummaryEl.textContent = 'No saved Soccer capture session.';
    soccerResumeButton.disabled = true;
    soccerRetryFailedButton.disabled = true;
    soccerDownloadButton.disabled = true;
    soccerClearSessionButton.disabled = true;
    return;
  }
  const summary = Bet9jaSoccerSession.summarize(sessionObj);
  const failedIds = Bet9jaSoccerSession.failedCompetitionIds(sessionObj);
  const lastFailed = failedIds.length ? sessionObj.inventory.find((e) => e.competition_id === failedIds[failedIds.length - 1]) : null;
  const lastFailedLine = lastFailed ? `Last failed: ${lastFailed.country || ''} > ${lastFailed.competition || lastFailed.competition_id}\n` : '';
  soccerSessionSummaryEl.textContent =
    `Session: ${sessionObj.capture_session_id}\n` +
    lastFailedLine +
    `Completed: ${summary.completed}  Empty: ${summary.confirmed_empty}  Failed: ${summary.failed}  Remaining: ${summary.pending}`;
  soccerResumeButton.disabled = summary.pending === 0;
  soccerRetryFailedButton.disabled = summary.failed === 0;
  soccerDownloadButton.disabled = summary.completed + summary.confirmed_empty + summary.failed === 0;
  soccerClearSessionButton.disabled = false;
}

async function refreshSoccerSessionUI() {
  renderSoccerSessionSummary(await loadSoccerSession());
}
refreshSoccerSessionUI();

const COMPETITIONS_URL_PATTERN = /^https:\/\/sports\.bet9ja\.com\/sportPage\/1\/competitions\/?(?:[?#].*)?$/;
const COMPETITIONS_TARGET_URL = 'https://sports.bet9ja.com/sportPage/1/competitions';
const COUPONS_NAVIGATION_TIMEOUT_MS = 15000;
const COUPONS_NAVIGATION_POLL_MS = 150;

function waitForTabUrlSettled(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    function poll() {
      chrome.tabs.get(tabId, (t) => {
        if (chrome.runtime.lastError || !t) {
          resolve(null);
          return;
        }
        const settled = t.status === 'complete' && COMPETITIONS_URL_PATTERN.test(t.url || '');
        if (settled || Date.now() > deadline) {
          resolve(t);
          return;
        }
        setTimeout(poll, COUPONS_NAVIGATION_POLL_MS);
      });
    }
    poll();
  });
}

// Reaching /sportPage/1/competitions from an arbitrary starting page (the
// Sports homepage, a competition page, or anywhere else) is this
// controller's job via a real tab navigation -- never a DOM click
// soccer_walker.js guesses at from inside an arbitrary page it doesn't
// control. activeTab already grants chrome.tabs.update() for the current
// tab's URL, so no extra manifest permission is needed.
async function ensureOnCompetitionsRoute(tab) {
  if (COMPETITIONS_URL_PATTERN.test(tab.url || '')) {
    return tab;
  }
  await new Promise((resolve, reject) => {
    chrome.tabs.update(tab.id, { url: COMPETITIONS_TARGET_URL }, () => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve();
    });
  });
  const settledTab = await waitForTabUrlSettled(tab.id, COUPONS_NAVIGATION_TIMEOUT_MS);
  if (!settledTab) {
    throw new Error('Could not confirm navigation to https://sports.bet9ja.com/sportPage/1/competitions (tab lookup failed).');
  }
  if (!COMPETITIONS_URL_PATTERN.test(settledTab.url || '')) {
    throw new Error(
      `Could not reach https://sports.bet9ja.com/sportPage/1/competitions (navigation timed out at "${settledTab.url || ''}"). Please open that page manually and try again.`
    );
  }
  return settledTab;
}

/** Injects a fresh, walk-nothing discovery-only call -- see soccer_walker.js's own `discoveryOnly` comment for why a resume/retry needs this BEFORE it can decide which ids to filter to. */
async function discoverSoccerInventory(tabId, tabUrl, tabTitle, capturedAtUtc) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: (sourceUrl, pageTitle, capturedAtUtcArg) =>
      window.__bet9jaSoccerAllCompetitionsCaptureRun(sourceUrl, pageTitle, capturedAtUtcArg, null, true),
    args: [tabUrl || '', tabTitle || '', capturedAtUtc],
  });
  const result = results && results[0] && results[0].result;
  if (!result || !result.envelope || !result.envelope.discovered_competitions) {
    const reasons = (result && result.envelope && result.envelope.capture_status_reasons) || [];
    throw new Error(`Could not discover the Soccer competitions inventory${reasons.length ? `: ${reasons.join(', ')}` : ' (page may block script injection).'}`);
  }
  return result.envelope;
}

/** Drains every batch delta queued in the page since the last poll and persists them immediately -- so a crash, cancel, or closed popup loses at most the one batch in flight. `sessionRef` is a plain {current} box so this can be called repeatedly on an interval while sharing one evolving session object with the caller. */
async function drainAndPersistSoccerDeltas(tabId, sessionRef, segmentIndex) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => (window.__bet9jaSoccerAllDrainPendingDeltas ? window.__bet9jaSoccerAllDrainPendingDeltas() : []),
    });
    const deltas = (results && results[0] && results[0].result) || [];
    if (deltas.length === 0) return;
    let next = sessionRef.current;
    for (const delta of deltas) {
      next = Bet9jaSoccerSession.applyBatchDelta(next, delta, { segmentIndex });
    }
    sessionRef.current = next;
    await saveSoccerSession(next);
    renderSoccerSessionSummary(next);
  } catch (err) {
    // Best-effort only, same discipline as the settled-bets progress poll
    // above -- a failed drain never affects the underlying capture, which
    // keeps running independently and gets one more chance next poll.
  }
}

/**
 * Runs one checkpointed segment of the "Capture all Soccer fixtures"
 * flow. `mode` is `'new'` (always starts a brand-new session, discarding
 * any prior saved one), `'resume'` (walks only currently-`PENDING` ids),
 * or `'retry'` (walks only currently-`FAILED` ids -- a separate, explicit
 * action, never folded into a normal resume). Resume/retry always
 * rediscover the real inventory first and reconcile it into the existing
 * session (`Bet9jaSoccerSession.reconcileInventory`) -- Bet9ja's own
 * inventory can change between runs, and an index is never the resume
 * authority (see soccer_session.js's own header comment); only a
 * competition's own stable id is.
 */
async function runCheckpointedSoccerSegment(mode) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('No active tab found.');
  }
  const couponsTab = await ensureOnCompetitionsRoute(tab);
  const capturedAtUtc = new Date().toISOString();

  await chrome.scripting.executeScript({
    target: { tabId: couponsTab.id },
    files: ['ids.js', 'parser.js', 'soccer_walker.js', 'content.js'],
  });

  let sessionObj = await loadSoccerSession();

  if (mode === 'new' || !sessionObj) {
    const discovered = await discoverSoccerInventory(couponsTab.id, couponsTab.url, couponsTab.title, capturedAtUtc);
    sessionObj = Bet9jaSoccerSession.createSession({
      sessionId: `soccer-${timestampForFilename(capturedAtUtc)}`,
      capturedAtUtc,
      captureScope: discovered.capture_scope,
      inventory: discovered.discovered_competitions,
    });
    await saveSoccerSession(sessionObj);
  } else {
    const discovered = await discoverSoccerInventory(couponsTab.id, couponsTab.url, couponsTab.title, capturedAtUtc);
    sessionObj = Bet9jaSoccerSession.reconcileInventory(sessionObj, discovered.discovered_competitions);
    await saveSoccerSession(sessionObj);
  }

  const filterIds = mode === 'retry' ? Bet9jaSoccerSession.failedCompetitionIds(sessionObj) : Bet9jaSoccerSession.pendingCompetitionIds(sessionObj);
  if (filterIds.length === 0) {
    return { sessionObj, envelope: null, segmentIndex: null };
  }

  // Computed ONCE, up front, and reused for every ledger transition THIS
  // run causes AND for its own eventual segment file's own segment_index
  // (see handleCheckpointedSoccerAction below) -- never recomputed
  // mid-run, so every provenance record this run writes agrees with each
  // other even though `sessionObj.segments` itself isn't appended to
  // until the run finishes.
  const segmentIndex = (sessionObj.segments ? sessionObj.segments.length : 0) + 1;

  const sessionRef = { current: sessionObj };
  soccerProgressIntervalId = setInterval(() => drainAndPersistSoccerDeltas(couponsTab.id, sessionRef, segmentIndex), 1000);

  let envelope = null;
  try {
    const injectionResults = await chrome.scripting.executeScript({
      target: { tabId: couponsTab.id },
      func: (sourceUrl, pageTitle, capturedAtUtcArg, filterIdsArg) =>
        window.__bet9jaSoccerAllCompetitionsCaptureRun(sourceUrl, pageTitle, capturedAtUtcArg, filterIdsArg, false),
      args: [couponsTab.url || '', couponsTab.title || '', capturedAtUtc, filterIds],
    });
    const result = injectionResults && injectionResults[0] && injectionResults[0].result;
    if (!result || !result.envelope) {
      throw new Error('Soccer capture produced no result (page may block script injection).');
    }
    envelope = result.envelope;
  } finally {
    clearInterval(soccerProgressIntervalId);
    soccerProgressIntervalId = null;
    // Catches anything queued between the last poll and this run ending
    // -- never rely on the poll interval alone to have caught the last batch.
    await drainAndPersistSoccerDeltas(couponsTab.id, sessionRef, segmentIndex);
  }

  return { sessionObj: sessionRef.current, envelope, segmentIndex };
}

async function handleCheckpointedSoccerAction(mode) {
  if (soccerAllCaptureInFlight) {
    return;
  }
  soccerAllCaptureInFlight = true;
  SOCCER_BUTTONS.forEach((b) => (b.disabled = true));
  soccerAllCancelButton.hidden = false;
  soccerAllCancelButton.disabled = false;
  soccerAllCancelButton.textContent = 'Cancel';
  setSoccerAllStatus(
    'ok',
    mode === 'new' ? 'Starting new capture...' : mode === 'retry' ? 'Retrying failed competitions...' : 'Resuming capture...'
  );
  try {
    const { sessionObj, envelope, segmentIndex } = await runCheckpointedSoccerSegment(mode);
    renderSoccerSessionSummary(sessionObj);

    if (!envelope) {
      setSoccerAllStatus('ok', mode === 'retry' ? 'No failed competitions to retry.' : 'Nothing pending to resume.');
      return;
    }

    // "Each run can download a small segment file" -- exactly what THIS
    // run captured (never the whole session; see "Download current
    // results" below for the cumulative file). Reuses the SAME
    // segmentIndex this run already used for every ledger transition it
    // caused (see runCheckpointedSoccerSegment's own comment) rather than
    // recomputing it here.
    const segmentEnvelope = Bet9jaSoccerSession.buildSegmentEnvelope(sessionObj, segmentIndex, {
      competitionResults: envelope.competition_results,
      fixtures: envelope.fixtures,
      unparsedRecords: envelope.unparsed_records,
    });
    const segmentFilename = `bet9ja-soccer-session-${sessionObj.capture_session_id}-segment-${String(segmentIndex).padStart(3, '0')}.json`;
    await triggerDownload(segmentFilename, JSON.stringify(segmentEnvelope, null, 2));
    const sessionWithSegment = {
      ...sessionObj,
      segments: [...(sessionObj.segments || []), { segment_index: segmentIndex, filename: segmentFilename }],
    };
    await saveSoccerSession(sessionWithSegment);
    renderSoccerSessionSummary(sessionWithSegment);

    const summary = Bet9jaSoccerSession.summarize(sessionWithSegment);
    const text =
      `${envelope.capture_status}\n` +
      `This run -- captured: ${envelope.competitions_captured}, empty: ${envelope.competitions_empty}, failed: ${envelope.competitions_failed}\n` +
      `Session totals -- completed: ${summary.completed}, empty: ${summary.confirmed_empty}, failed: ${summary.failed}, remaining: ${summary.pending}\n` +
      `Reasons: ${(envelope.capture_status_reasons || []).join(', ') || 'none'}\n` +
      `Saved segment: ${segmentFilename}`;
    const cssClass = envelope.capture_status === 'CAPTURE_COMPLETE' ? 'ok' : envelope.capture_status === 'CAPTURE_PARTIAL' ? 'partial' : 'failed';
    setSoccerAllStatus(cssClass, text);
  } catch (err) {
    if (err && err.code === 'SEGMENT_OUTCOME_FIXTURE_CONFLICT') {
      // Round 14: this run's own classification produced an internally
      // inconsistent result -- see soccer_walker.js's own
      // BATCH_OUTCOME_FIXTURE_CONFLICT invariant, which should make this
      // unreachable, but buildSegmentEnvelope checks independently
      // anyway. The run's own ledger transitions (applied live via
      // drainAndPersistSoccerDeltas as the run went) are NOT rolled back
      // by this -- surfaced plainly so it's investigated, never silently
      // swallowed.
      setSoccerAllStatus('error', `${err.message}\nSegment file NOT saved -- this run's own classification produced an inconsistent result.`);
    } else {
      setSoccerAllStatus('error', `Soccer capture failed to run: ${err && err.message ? err.message : String(err)}`);
    }
  } finally {
    soccerAllCaptureInFlight = false;
    SOCCER_BUTTONS.forEach((b) => (b.disabled = false));
    soccerAllCancelButton.hidden = true;
    await refreshSoccerSessionUI();
  }
}

soccerStartNewButton.addEventListener('click', () => handleCheckpointedSoccerAction('new'));
soccerResumeButton.addEventListener('click', () => handleCheckpointedSoccerAction('resume'));
soccerRetryFailedButton.addEventListener('click', () => handleCheckpointedSoccerAction('retry'));

soccerDownloadButton.addEventListener('click', async () => {
  const sessionObj = await loadSoccerSession();
  if (!sessionObj) {
    setSoccerAllStatus('error', 'No saved session to download.');
    return;
  }
  // buildAssembledEnvelope throws SESSION_LEDGER_FIXTURE_CONFLICT rather
  // than ever producing a self-contradictory file (Round 13, see
  // soccer_session.js's own header comment) -- caught here and surfaced
  // plainly. The session is deliberately left untouched either way: a
  // conflict is exactly the kind of thing that needs the ledger's own
  // per-entry provenance (segment_index/batch_index) to diagnose, which
  // clearing the session would destroy.
  try {
    const assembled = Bet9jaSoccerSession.buildAssembledEnvelope(sessionObj);
    const filename = `bet9ja-soccer-all-${sessionObj.capture_session_id}.json`;
    await triggerDownload(filename, JSON.stringify(assembled, null, 2));
    setSoccerAllStatus('ok', `Saved: ${filename}`);
  } catch (err) {
    if (err && err.code === 'SESSION_LEDGER_FIXTURE_CONFLICT') {
      setSoccerAllStatus(
        'error',
        `${err.message}\nExport blocked -- session left untouched. Do not clear the session; the ledger's own provenance fields on the conflicting entries are needed to diagnose this.`
      );
      return;
    }
    setSoccerAllStatus('error', `Could not build the assembled export: ${err && err.message ? err.message : String(err)}`);
  }
});

soccerClearSessionButton.addEventListener('click', async () => {
  await clearSoccerSessionStorage();
  setSoccerAllStatus('ok', 'Saved session cleared.');
  await refreshSoccerSessionUI();
});

soccerAllCancelButton.addEventListener('click', async () => {
  if (!soccerAllCaptureInFlight) {
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => (window.__bet9jaSoccerAllRequestCancel ? window.__bet9jaSoccerAllRequestCancel() : false),
    });
    soccerAllCancelButton.disabled = true;
    soccerAllCancelButton.textContent = 'Cancelling...';
  } catch (err) {
    // Best-effort -- if this fails, the capture simply runs to completion.
  }
});

// --- Capture settled bets -----------------------------------------------
// Same click-gated, no-storage discipline as the buttons above -- see
// settled_bets_parser.js's own header comment for scope and the confirmed
// Round 1 profile. A real account can span 190+ pages, so this button
// shows live per-page progress (via a short poll loop -- see content.js's
// own comment for why a poll, not a callback, crosses the injection
// boundary) and offers a Cancel control that stops the walk cleanly after
// the page currently in flight, producing a CAPTURE_PARTIAL file with
// everything captured so far rather than losing it.
const settledButton = document.getElementById('settled-capture-button');
const settledCancelButton = document.getElementById('settled-cancel-button');
const settledStatusEl = document.getElementById('settled-status');
let settledCaptureInFlight = false;
let settledProgressIntervalId = null;

function setSettledStatus(cssClass, text) {
  settledStatusEl.className = cssClass;
  settledStatusEl.textContent = text;
}

async function pollSettledProgress(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => (window.__bet9jaSettledBetsReadProgress ? window.__bet9jaSettledBetsReadProgress() : null),
    });
    const progress = results && results[0] && results[0].result;
    if (progress) {
      const pagesAvailableText = progress.pagesAvailable ? `/${progress.pagesAvailable}` : '';
      setSettledStatus(
        'ok',
        `Capturing settled bets...\nPage ${progress.pageNumber}${pagesAvailableText} (visited ${progress.pagesVisited})\nTickets parsed so far: ${progress.ticketsParsedSoFar}`
      );
    }
  } catch (err) {
    // Best-effort only -- a failed progress poll never affects the
    // underlying capture, which keeps running independently.
  }
}

async function runSettledBetsCapture(tabId) {
  await chrome.scripting.executeScript({
    target: { tabId },
    files: ['ids.js', 'settled_bets_parser.js', 'content.js'],
  });

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const capturedAtUtc = new Date().toISOString();

  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId },
    func: (sourceUrl, pageTitle, capturedAtUtcArg) =>
      window.__bet9jaSettledBetsCaptureRun(sourceUrl, pageTitle, capturedAtUtcArg),
    args: [tab.url || '', tab.title || '', capturedAtUtc],
  });

  const result = injectionResults && injectionResults[0] && injectionResults[0].result;
  if (!result || !result.envelope) {
    throw new Error('Settled bets capture produced no result (page may block script injection).');
  }
  return result;
}

settledButton.addEventListener('click', async () => {
  if (settledCaptureInFlight) {
    return;
  }
  settledCaptureInFlight = true;
  settledButton.disabled = true;
  settledCancelButton.hidden = false;
  setSettledStatus('ok', 'Capturing settled bets...');

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    setSettledStatus('error', 'Capture failed to run: No active tab found.');
    settledButton.disabled = false;
    settledCancelButton.hidden = true;
    settledCaptureInFlight = false;
    return;
  }

  settledProgressIntervalId = setInterval(() => pollSettledProgress(tab.id), 1000);
  try {
    const { envelope } = await runSettledBetsCapture(tab.id);

    const filename = `bet9ja-settled-bets-${timestampForFilename(envelope.captured_at_utc)}.json`;
    await triggerDownload(filename, JSON.stringify(envelope, null, 2));

    const resumeLine = envelope.resume_metadata && envelope.resume_metadata.can_resume
      ? `Resume: ${envelope.resume_metadata.resume_hint}\n`
      : '';
    const summary =
      `${envelope.capture_status}\n` +
      `Pages visited: ${envelope.coverage.pages_visited}/${envelope.coverage.pages_available}\n` +
      `Tickets seen: ${envelope.coverage.tickets_seen}\n` +
      `Tickets parsed: ${envelope.coverage.tickets_parsed}\n` +
      `Tickets unresolved: ${envelope.coverage.tickets_unresolved}\n` +
      `Tickets excluded (out of scope): ${envelope.coverage.tickets_expected_excluded}\n` +
      resumeLine +
      `Reasons: ${envelope.capture_status_reasons.join(', ')}\n` +
      `Saved: ${filename}`;

    const cssClass =
      envelope.capture_status === 'CAPTURE_OK' ? 'ok' : envelope.capture_status === 'CAPTURE_PARTIAL' ? 'partial' : 'failed';
    setSettledStatus(cssClass, summary);
  } catch (err) {
    setSettledStatus('error', `Settled bets capture failed to run: ${err && err.message ? err.message : String(err)}`);
  } finally {
    if (settledProgressIntervalId) {
      clearInterval(settledProgressIntervalId);
      settledProgressIntervalId = null;
    }
    settledButton.disabled = false;
    settledCancelButton.hidden = true;
    settledCaptureInFlight = false;
  }
});

settledCancelButton.addEventListener('click', async () => {
  if (!settledCaptureInFlight) {
    return;
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => (window.__bet9jaSettledBetsRequestCancel ? window.__bet9jaSettledBetsRequestCancel() : false),
    });
    settledCancelButton.disabled = true;
    settledCancelButton.textContent = 'Cancelling...';
  } catch (err) {
    // Best-effort -- if this fails, the capture simply runs to completion.
  }
});
