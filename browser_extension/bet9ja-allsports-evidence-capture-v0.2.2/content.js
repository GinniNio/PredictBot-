/**
 * Content-script entry point: runs `buildSnapshot` (snapshot.js, inlined
 * here via chrome.scripting.executeScript's own file injection -- see
 * popup.js) against the REAL page `document` and returns the plain
 * result object. No chrome.* API calls happen inside `buildSnapshot`
 * itself (see that file's own docstring); this file is the one place
 * that actually touches `document`/`location`.
 */

function captureSnapshot() {
  return buildSnapshot(document, {
    href: location.href,
    title: document.title,
    capturedAtUtc: new Date().toISOString(),
  });
}
