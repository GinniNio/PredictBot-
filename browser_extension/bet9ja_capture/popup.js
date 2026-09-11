/**
 * Popup click handler -- the ONLY entry point that ever runs capture code.
 * Nothing in this extension executes on page load, on a schedule, or in a
 * background service worker; everything below is triggered directly by the
 * "Capture fixtures" button click, which is also what makes the
 * (click-gated) `activeTab` permission usable at all.
 *
 * Permission contract: this file only ever calls chrome.tabs, chrome.
 * scripting, and chrome.downloads -- matching manifest.json's permissions
 * exactly (activeTab, scripting, downloads). No chrome.storage, no
 * chrome.cookies, no host_permissions, no fetch/XMLHttpRequest of any
 * kind. Because there is no persistent storage, duplicate_status across
 * separate captures always starts fresh (NEW / DUPLICATE_WITHIN_CAPTURE
 * only) -- cross-capture "have I seen this odds change before" history is
 * intentionally deferred to the ledger's own idempotent re-import
 * (ledgers.forecast_ledger, keyed by the same deterministic fixture_id),
 * not duplicated here.
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
      `Tickets excluded (out of scope): ${envelope.coverage.tickets_excluded}\n` +
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
