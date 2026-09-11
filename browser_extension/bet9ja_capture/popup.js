/**
 * Popup click handler -- the ONLY entry point that ever runs capture code.
 * Nothing in this extension executes on page load, on a schedule, or in a
 * background service worker; everything below is triggered directly by the
 * "Capture fixtures" button click, which is also what makes the
 * (click-gated) `activeTab` permission usable at all.
 */
const STORAGE_KEY = 'bet9jaFixtureIndex';

const button = document.getElementById('capture-button');
const statusEl = document.getElementById('status');

function setStatus(cssClass, text) {
  statusEl.className = cssClass;
  statusEl.textContent = text;
}

function timestampForFilename(isoTimestamp) {
  // e.g. "2024-08-17T20:45:03.123Z" -> "2024-08-17T20-45-03Z" (filesystem-safe)
  return isoTimestamp.replace(/\.\d+Z$/, 'Z').replace(/:/g, '-');
}

function triggerDownload(filename, jsonText) {
  const blob = new Blob([jsonText], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function runCapture() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('No active tab found.');
  }

  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const previousIndex = stored[STORAGE_KEY] || {};
  const capturedAtUtc = new Date().toISOString();

  // Step 1: load the pure parsing modules into the page's isolated world.
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ['ids.js', 'parser.js', 'content.js'],
  });

  // Step 2: invoke the entry point content.js just defined, passing only
  // what's needed to build the envelope -- no host permissions, no
  // cross-tab access, nothing beyond this one active tab's DOM.
  const injectionResults = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: (previousIndexArg, sourceUrl, pageTitle, capturedAtUtcArg) =>
      window.__bet9jaCaptureRun(previousIndexArg, sourceUrl, pageTitle, capturedAtUtcArg),
    args: [previousIndex, tab.url || '', tab.title || '', capturedAtUtc],
  });

  const result = injectionResults && injectionResults[0] && injectionResults[0].result;
  if (!result || !result.envelope) {
    throw new Error('Capture produced no result (page may block script injection).');
  }
  return result;
}

button.addEventListener('click', async () => {
  button.disabled = true;
  setStatus('ok', 'Capturing...');
  try {
    const { envelope, updatedIndex } = await runCapture();

    const merged = { ...(await chrome.storage.local.get(STORAGE_KEY))[STORAGE_KEY], ...updatedIndex };
    await chrome.storage.local.set({ [STORAGE_KEY]: merged });

    const filename = `bet9ja-fixtures-${timestampForFilename(envelope.captured_at_utc)}.json`;
    triggerDownload(filename, JSON.stringify(envelope, null, 2));

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
  }
});
