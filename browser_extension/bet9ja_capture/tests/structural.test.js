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

test('popup.js only calls chrome.tabs / chrome.scripting / chrome.downloads -- no chrome.storage, no chrome.cookies', () => {
  // Only counts actual method invocations (chrome.<namespace>.<method>(...))
  // -- deliberately not a bare substring match, so this file's own
  // docstring can still mention "chrome.storage"/"chrome.cookies" by name
  // when explaining what it does NOT do, without tripping the check.
  const chromeCalls = popupJs.match(/chrome\.([a-zA-Z]+)\.[a-zA-Z]+\(/g) || [];
  const namespaces = new Set(chromeCalls.map((c) => c.split('.')[1]));
  assert.ok(namespaces.size > 0, 'expected to find at least one chrome.* call in popup.js');
  assert.deepEqual([...namespaces].sort(), ['downloads', 'scripting', 'tabs']);
});

test('manifest permissions are exactly activeTab, scripting, downloads -- no host_permissions, no storage, no cookies', () => {
  assert.deepEqual([...manifest.permissions].sort(), ['activeTab', 'downloads', 'scripting'].sort());
  assert.ok(!('host_permissions' in manifest) || manifest.host_permissions.length === 0);
  assert.ok(!('background' in manifest), 'no background service worker -- no always-on network access surface');
  assert.ok(!('content_scripts' in manifest), 'no content_scripts entry -- capture must only ever run on a click');
});

test('no source file in this extension calls fetch or XMLHttpRequest', () => {
  const sourceFiles = ['content.js', 'popup.js', 'parser.js', 'ids.js', 'ticket_parser.js', 'soccer_walker.js', 'settled_bets_parser.js'];
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
  const sourceFiles = ['content.js', 'popup.js', 'parser.js', 'ids.js', 'ticket_parser.js', 'soccer_walker.js', 'settled_bets_parser.js'];
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

test('popup.js registers the soccer-all-competitions button click listener exactly once, at module load', () => {
  const listenerMatches = popupJs.match(/soccerAllButton\.addEventListener\(/g) || [];
  assert.equal(listenerMatches.length, 1, 'expected exactly one soccerAllButton.addEventListener call in popup.js');
});

test('popup.js has a synchronous re-entrancy guard before the first await in the soccer-all-competitions click handler', () => {
  const handlerStart = popupJs.indexOf("soccerAllButton.addEventListener('click'");
  assert.notEqual(handlerStart, -1, 'soccer-all-competitions click handler not found');
  const handlerBody = popupJs.slice(handlerStart);
  const firstAwaitIndex = handlerBody.indexOf('await ');
  const guardIndex = handlerBody.indexOf('soccerAllCaptureInFlight');
  const disableIndex = handlerBody.indexOf('soccerAllButton.disabled = true');
  assert.ok(guardIndex !== -1 && guardIndex < firstAwaitIndex, 'soccerAllCaptureInFlight guard must appear before the first await');
  assert.ok(disableIndex !== -1 && disableIndex < firstAwaitIndex, 'soccerAllButton.disabled = true must appear before the first await');
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
