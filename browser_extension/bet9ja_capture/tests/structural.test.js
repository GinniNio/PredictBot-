/**
 * Structural (source-inspection) tests for behavior that can't be
 * exercised without a real browser extension host (repeated popup
 * injection, double-click debouncing, the manifest's declared permission
 * set). These assert properties of the actual shipped source files
 * directly -- the same "read the real source, don't trust a description
 * of it" pattern this repo's Python suite uses (e.g.
 * test_soccer_1x2_promotion_thresholds.py's "no source file references
 * the thresholds path" tests).
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const contentJs = fs.readFileSync(path.join(ROOT, 'content.js'), 'utf-8');
const popupJs = fs.readFileSync(path.join(ROOT, 'popup.js'), 'utf-8');
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'manifest.json'), 'utf-8'));

test('content.js registers no event listeners -- repeated injection can never accumulate one', () => {
  assert.ok(!contentJs.includes('addEventListener'), 'content.js must not call addEventListener anywhere');
  assert.ok(!contentJs.includes('chrome.runtime.onMessage'), 'content.js must not register a runtime message listener');
});

test('content.js only ever (re)assigns one global entry point, never appends to a list', () => {
  const assignments = contentJs.match(/window\.__bet9jaCaptureRun\s*=/g) || [];
  assert.equal(assignments.length, 1, 'exactly one assignment to window.__bet9jaCaptureRun expected');
  // A plain assignment always replaces the previous value -- re-injecting
  // content.js twice still leaves exactly one function installed, never
  // two independently-invoked copies.
});

test('popup.js registers the capture button click listener exactly once, at module load (not inside a handler)', () => {
  const listenerMatches = popupJs.match(/button\.addEventListener\(/g) || [];
  assert.equal(listenerMatches.length, 1, 'expected exactly one button.addEventListener call in popup.js');
});

test('popup.js has a synchronous re-entrancy guard before the first await in the click handler', () => {
  const handlerStart = popupJs.indexOf("button.addEventListener('click'");
  assert.notEqual(handlerStart, -1, 'click handler not found');
  const handlerBody = popupJs.slice(handlerStart);
  const firstAwaitIndex = handlerBody.indexOf('await ');
  const guardIndex = handlerBody.indexOf('captureInFlight');
  const disableIndex = handlerBody.indexOf('button.disabled = true');
  assert.ok(guardIndex !== -1 && guardIndex < firstAwaitIndex, 'captureInFlight guard must appear before the first await');
  assert.ok(disableIndex !== -1 && disableIndex < firstAwaitIndex, 'button.disabled = true must appear before the first await');
});

test('popup.js only calls chrome.tabs / chrome.scripting / chrome.downloads / chrome.storage -- no chrome.cookies', () => {
  // Only counts actual method invocations (chrome.<namespace>.<method>(...))
  // -- deliberately not a bare substring match, so this file's own
  // docstring can still mention "chrome.cookies" by name when explaining
  // what it does NOT do, without tripping the check. chrome.storage is
  // the ONE deliberate exception (soccer_session.js's checkpointed
  // capture session, documented at this file's own header) -- every
  // other namespace stays exactly as narrow as before.
  // chrome.storage.local.* is a THREE-segment namespace (storage.local),
  // unlike every other call here -- matched separately so the general
  // pattern below doesn't need to special-case it.
  const chromeCalls = popupJs.match(/chrome\.([a-zA-Z]+)\.[a-zA-Z]+\(/g) || [];
  const namespaces = new Set(chromeCalls.map((c) => c.split('.')[1]));
  const storageLocalCalls = popupJs.match(/chrome\.storage\.local\.[a-zA-Z]+\(/g) || [];
  if (storageLocalCalls.length > 0) namespaces.add('storage');
  assert.ok(namespaces.size > 0, 'expected to find at least one chrome.* call in popup.js');
  assert.deepEqual([...namespaces].sort(), ['downloads', 'scripting', 'storage', 'tabs']);
});

test('manifest permissions are exactly activeTab, scripting, downloads, storage -- no host_permissions, no cookies', () => {
  // storage (Round 12) is scoped to ONE key holding this extension's own
  // Soccer-capture checkpoint ledger -- never cookies, tokens, or account
  // data; see soccer_session.js's own header comment.
  assert.deepEqual([...manifest.permissions].sort(), ['activeTab', 'downloads', 'scripting', 'storage'].sort());
  assert.ok(!('host_permissions' in manifest) || manifest.host_permissions.length === 0);
  assert.ok(!('background' in manifest), 'no background service worker -- no always-on network access surface');
  assert.ok(!('content_scripts' in manifest), 'no content_scripts entry -- capture must only ever run on a click');
});

test('no source file in this extension calls fetch or XMLHttpRequest', () => {
  const sourceFiles = ['content.js', 'popup.js', 'parser.js', 'ids.js', 'ticket_parser.js', 'soccer_walker.js', 'soccer_session.js', 'settled_bets_parser.js'];
  for (const filename of sourceFiles) {
    const source = fs.readFileSync(path.join(ROOT, filename), 'utf-8');
    // Actual call/construction patterns only -- not a bare substring match,
    // so a docstring is free to name "fetch"/"XMLHttpRequest" when
    // explaining what this file does NOT do.
    assert.ok(!/\bfetch\s*\(/.test(source), `${filename} must never call fetch()`);
    assert.ok(!/new\s+XMLHttpRequest\s*\(/.test(source), `${filename} must never construct an XMLHttpRequest`);
  }
});

test('no source file in this extension reads document.cookie', () => {
  const sourceFiles = ['content.js', 'popup.js', 'parser.js', 'ids.js', 'ticket_parser.js', 'soccer_walker.js', 'soccer_session.js', 'settled_bets_parser.js'];
  for (const filename of sourceFiles) {
    const source = fs.readFileSync(path.join(ROOT, filename), 'utf-8');
    assert.ok(!source.includes('document.cookie'), `${filename} must never read document.cookie`);
  }
});

test('content.js only ever (re)assigns one ticket-capture entry point, never appends to a list', () => {
  const assignments = contentJs.match(/window\.__bet9jaTicketCaptureRun\s*=/g) || [];
  assert.equal(assignments.length, 1, 'exactly one assignment to window.__bet9jaTicketCaptureRun expected');
});

test('popup.js registers the ticket-capture button click listener exactly once, at module load', () => {
  const listenerMatches = popupJs.match(/ticketButton\.addEventListener\(/g) || [];
  assert.equal(listenerMatches.length, 1, 'expected exactly one ticketButton.addEventListener call in popup.js');
});

test('popup.js has a synchronous re-entrancy guard before the first await in the ticket-capture click handler', () => {
  const handlerStart = popupJs.indexOf("ticketButton.addEventListener('click'");
  assert.notEqual(handlerStart, -1, 'ticket-capture click handler not found');
  const handlerBody = popupJs.slice(handlerStart);
  const firstAwaitIndex = handlerBody.indexOf('await ');
  const guardIndex = handlerBody.indexOf('ticketCaptureInFlight');
  const disableIndex = handlerBody.indexOf('ticketButton.disabled = true');
  assert.ok(guardIndex !== -1 && guardIndex < firstAwaitIndex, 'ticketCaptureInFlight guard must appear before the first await');
  assert.ok(disableIndex !== -1 && disableIndex < firstAwaitIndex, 'ticketButton.disabled = true must appear before the first await');
});

test('content.js only ever (re)assigns one soccer-all-competitions entry point, never appends to a list', () => {
  const assignments = contentJs.match(/window\.__bet9jaSoccerAllCompetitionsCaptureRun\s*=/g) || [];
  assert.equal(assignments.length, 1, 'exactly one assignment to window.__bet9jaSoccerAllCompetitionsCaptureRun expected');
});

test('popup.js registers exactly one click listener each for Start new/Resume/Retry failed, all delegating to the same shared handler', () => {
  // ROUND 12: the single "Capture all Soccer fixtures" button was
  // replaced with Start new/Resume/Retry failed/Download/Clear session --
  // the first three all share one handler (handleCheckpointedSoccerAction)
  // rather than each duplicating the capture-running logic.
  for (const buttonVar of ['soccerStartNewButton', 'soccerResumeButton', 'soccerRetryFailedButton']) {
    const listenerMatches = popupJs.match(new RegExp(`${buttonVar}\\.addEventListener\\(`, 'g')) || [];
    assert.equal(listenerMatches.length, 1, `expected exactly one ${buttonVar}.addEventListener call in popup.js`);
  }
  assert.ok(popupJs.includes("handleCheckpointedSoccerAction('new')"));
  assert.ok(popupJs.includes("handleCheckpointedSoccerAction('resume')"));
  assert.ok(popupJs.includes("handleCheckpointedSoccerAction('retry')"));
});

test('popup.js has a synchronous re-entrancy guard before the first await in the shared checkpointed-soccer-action handler', () => {
  const handlerStart = popupJs.indexOf('async function handleCheckpointedSoccerAction');
  assert.notEqual(handlerStart, -1, 'handleCheckpointedSoccerAction not found');
  const handlerBody = popupJs.slice(handlerStart);
  const firstAwaitIndex = handlerBody.indexOf('await ');
  const guardIndex = handlerBody.indexOf('soccerAllCaptureInFlight');
  const disableIndex = handlerBody.indexOf('.disabled = true');
  assert.ok(guardIndex !== -1 && guardIndex < firstAwaitIndex, 'soccerAllCaptureInFlight guard must appear before the first await');
  assert.ok(disableIndex !== -1 && disableIndex < firstAwaitIndex, 'button disabling must appear before the first await');
});

test('popup.js registers exactly one click listener each for Download current results and Clear saved session', () => {
  for (const buttonVar of ['soccerDownloadButton', 'soccerClearSessionButton']) {
    const listenerMatches = popupJs.match(new RegExp(`${buttonVar}\\.addEventListener\\(`, 'g')) || [];
    assert.equal(listenerMatches.length, 1, `expected exactly one ${buttonVar}.addEventListener call in popup.js`);
  }
});

test('content.js only ever (re)assigns one settled-bets entry point, never appends to a list', () => {
  const assignments = contentJs.match(/window\.__bet9jaSettledBetsCaptureRun\s*=/g) || [];
  assert.equal(assignments.length, 1, 'exactly one assignment to window.__bet9jaSettledBetsCaptureRun expected');
});

test('popup.js registers the settled-bets button click listener exactly once, at module load', () => {
  const listenerMatches = popupJs.match(/settledButton\.addEventListener\(/g) || [];
  assert.equal(listenerMatches.length, 1, 'expected exactly one settledButton.addEventListener call in popup.js');
});

test('popup.js has a synchronous re-entrancy guard before the first await in the settled-bets click handler', () => {
  const handlerStart = popupJs.indexOf("settledButton.addEventListener('click'");
  assert.notEqual(handlerStart, -1, 'settled-bets click handler not found');
  const handlerBody = popupJs.slice(handlerStart);
  const firstAwaitIndex = handlerBody.indexOf('await ');
  const guardIndex = handlerBody.indexOf('settledCaptureInFlight');
  const disableIndex = handlerBody.indexOf('settledButton.disabled = true');
  assert.ok(guardIndex !== -1 && guardIndex < firstAwaitIndex, 'settledCaptureInFlight guard must appear before the first await');
  assert.ok(disableIndex !== -1 && disableIndex < firstAwaitIndex, 'settledButton.disabled = true must appear before the first await');
});
