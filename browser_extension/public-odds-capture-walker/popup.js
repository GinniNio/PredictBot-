const walkButton = document.getElementById('walk');
const downloadButton = document.getElementById('download');
const maxEvents = document.getElementById('max-events');
const includeNonsports = document.getElementById('include-nonsports');
const status = document.getElementById('status');
const setStatus = (message) => { status.textContent = message; };

function downloadRun(run) {
  const json = JSON.stringify(run, null, 2);
  const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
  const stamp = (run.completed_at_utc || new Date().toISOString()).replace(/[:.]/g, '-');
  return chrome.downloads.download({ url, filename: `public-odds-walk-${run.source_key || 'unknown'}-${stamp}.json`, saveAs: false });
}

walkButton.addEventListener('click', async () => {
  walkButton.disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error('No active tab found.');
    setStatus('Capturing seed page and walking validated event links...');
    const response = await chrome.runtime.sendMessage({ type: 'START_PUBLIC_ODDS_WALK', tabId: tab.id, maxEvents: Number(maxEvents.value) || 10, scope: includeNonsports.checked ? 'all' : 'sports' });
    if (!response.ok) throw new Error(response.error);
    await downloadRun(response.run);
    setStatus(`Saved ${response.run.captures.length} page captures; ${response.run.discovered_links.length} event links found; ${response.run.failures.length} failures.`);
  } catch (err) {
    setStatus(`Failed: ${err && err.message ? err.message : String(err)}`);
  } finally { walkButton.disabled = false; }
});
downloadButton.addEventListener('click', async () => {
  const response = await chrome.runtime.sendMessage({ type: 'GET_PUBLIC_ODDS_RUN' });
  if (!response.run) return setStatus('No completed run is stored yet.');
  await downloadRun(response.run);
  setStatus('Saved the last stored run.');
});
