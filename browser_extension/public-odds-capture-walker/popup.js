const $ = (id) => document.getElementById(id);
const setStatus = (message) => { $('status').textContent = message; };
const send = (message) => chrome.runtime.sendMessage(message);

async function activeTabId() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error('No active tab found.');
  return tab.id;
}

function describe(run) {
  if (!run) return 'No walk yet.';
  const c = run.counts || {};
  if (run.mode === 'results') {
    return [
      `Results walk ${run.status}${run.message ? ': ' + run.message : ''}`,
      run.targets_status,
      `Pages ${run.cursor} of ${run.queued} visited: ${c.results || 0} final results, ${c.not_final || 0} not played or not completed, ${c.no_result || 0} without a result yet, ${c.failed || 0} failed.`,
      c.truncated ? `${c.truncated} more over the page limit: run it again.` : '',
    ].filter(Boolean).join('\n');
  }
  return [
    `${run.status}${run.message ? ': ' + run.message : ''}`,
    `${run.source_key} from ${run.seed_url || '?'}`,
    run.targets_status,
    `Pages ${run.cursor} of ${run.queued} visited: ${c.captured || 0} captured with odds (incl. start page), ${c.started || 0} already started, ${c.no_odds || 0} without odds, ${c.failed || 0} failed.`,
    `Found ${c.discovered || 0} games; ${c.other_sport || 0} other sport, ${c.not_on_bet9ja || 0} not on Bet9ja, ${c.truncated || 0} over the page limit.`,
  ].filter(Boolean).join('\n');
}

async function downloadRun() {
  const response = await send({ type: 'GET_PUBLIC_ODDS_RUN' });
  const run = response.run;
  if (!run) return setStatus('No walk is stored yet.');
  const json = JSON.stringify(run);
  const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
  const stamp = (run.completed_at_utc || new Date().toISOString()).replace(/[:.]/g, '-');
  const kind = run.mode === 'results' ? '-results' : '';
  await chrome.downloads.download({ url, filename: `public-odds-walk-${run.source_key || 'unknown'}${kind}-${stamp}.json`, saveAs: false });
  await send({ type: 'MARK_DOWNLOADED' });
}

async function refresh() {
  const response = await send({ type: 'GET_PUBLIC_ODDS_STATUS' });
  const run = response.run;
  setStatus(describe(run));
  const running = run && run.status === 'RUNNING';
  $('walk').disabled = running;
  $('results').disabled = running;
  $('stop').disabled = !running;
  $('resume').disabled = !run || !['INTERRUPTED', 'STOPPED'].includes(run.status) || run.cursor >= run.queued;
  if (run && ['COMPLETE', 'STOPPED'].includes(run.status) && !run.downloaded && run.cursor >= run.queued) await downloadRun();
}

$('walk').addEventListener('click', async () => {
  try {
    setStatus('Reading the start page and listing games...');
    const response = await send({ type: 'START_PUBLIC_ODDS_WALK', tabId: await activeTabId(),
      maxEvents: Number($('max-events').value) || 100, onlyBet9ja: $('only-bet9ja').checked,
      scope: $('include-nonsports').checked ? 'all' : 'sports' });
    if (!response.ok) throw new Error(response.error);
    await refresh();
  } catch (err) { setStatus(`Failed: ${err && err.message ? err.message : String(err)}`); }
});
$('results').addEventListener('click', async () => {
  try {
    setStatus('Asking the PredictBot app which finished games need a result...');
    const response = await send({ type: 'START_RESULTS_WALK', tabId: await activeTabId(),
      maxEvents: Number($('max-events').value) || 100 });
    if (!response.ok) throw new Error(response.error);
    await refresh();
  } catch (err) { setStatus(`Failed: ${err && err.message ? err.message : String(err)}`); }
});
$('stop').addEventListener('click', async () => { await send({ type: 'STOP_PUBLIC_ODDS_WALK' }); await refresh(); });
$('resume').addEventListener('click', async () => {
  const response = await send({ type: 'RESUME_PUBLIC_ODDS_WALK', tabId: await activeTabId() });
  if (!response.ok) setStatus(`Failed: ${response.error}`); else await refresh();
});
$('download').addEventListener('click', downloadRun);
refresh();
setInterval(refresh, 1500);
