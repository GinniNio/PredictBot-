/**
 * Injected glue between the popup's click handler and parser.js.
 *
 * This file (along with ids.js and parser.js) is injected into the active
 * tab ONLY by chrome.scripting.executeScript, called ONLY from popup.js's
 * "Capture fixtures" click handler -- there is no `content_scripts` entry
 * in manifest.json, so none of this code ever runs on page load, on a
 * timer, or in the background. It defines one entry point and touches
 * nothing beyond the visible fixture DOM: no cookies, no localStorage
 * tokens, no network requests.
 */
(function () {
  window.__bet9jaCaptureRun = function (previousIndex, sourceUrl, pageTitle, capturedAtUtc) {
    return window.Bet9jaCapture.captureFromDocument(document, {
      sourceUrl,
      pageTitle,
      capturedAtUtc,
      previousIndex: previousIndex || {},
    });
  };
})();
