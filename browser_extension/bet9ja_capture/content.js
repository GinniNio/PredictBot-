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

  // Second, independent entry point for the "Capture open bets" button --
  // injected alongside ticket_parser.js only, never together with a bet
  // placement/cashout action. See ticket_parser.js's own header comment
  // for scope and the fail-closed ticket-boundary contract. This returns
  // a Promise (the MYBETS profile clicks each ticket's own accordion
  // toggle open, parses it, then clicks it closed again, awaiting the
  // DOM update at each step) -- chrome.scripting.executeScript awaits a
  // returned Promise automatically, so popup.js needs no change to
  // receive the resolved envelope.
  window.__bet9jaTicketCaptureRun = function (sourceUrl, pageTitle, capturedAtUtc) {
    return window.Bet9jaTicketCapture.captureFromDocument(document, {
      sourceUrl,
      pageTitle,
      capturedAtUtc,
    });
  };

  // Third, independent entry point for the "Capture all Soccer fixtures"
  // button -- injected alongside parser.js + soccer_walker.js only.
  // Selects every discovered Soccer competition's checkbox on
  // `/sportPage/1/competitions` in batches (Bet9ja's own selection limit
  // is discovered operationally, never hard-coded), clicking "Show
  // Leagues" once per batch and capturing that batch's combined fixtures
  // via parser.js's own captureFromDocument (never re-parsed here),
  // combined into one deduplicated envelope. Returns a Promise for the
  // same reason __bet9jaTicketCaptureRun does -- see its own comment
  // above. Exposes the same polling-based cancel surface as the
  // settled-bets entry point below (a function reference cannot cross
  // the chrome.scripting.executeScript() argument boundary).
  window.__bet9jaSoccerAllCompetitionsCaptureRun = function (sourceUrl, pageTitle, capturedAtUtc) {
    window.__bet9jaSoccerAllCancelRequested = false;
    return window.Bet9jaSoccerWalker.captureAllSoccerCompetitions(document, {
      sourceUrl,
      pageTitle,
      capturedAtUtc,
      shouldCancel: () => window.__bet9jaSoccerAllCancelRequested === true,
    });
  };

  window.__bet9jaSoccerAllRequestCancel = function () {
    window.__bet9jaSoccerAllCancelRequested = true;
    return true;
  };

  // Fourth, independent entry point for the "Capture settled bets" button --
  // injected alongside ticket_parser.js's sibling module, settled_bets_
  // parser.js, only. A real account can have 190+ pages (see that file's
  // own header comment), so this exposes a small polling surface
  // (__bet9jaSettledBetsReadProgress / __bet9jaSettledBetsRequestCancel)
  // instead of a live callback -- a function reference cannot cross the
  // chrome.scripting.executeScript() argument boundary, so popup.js
  // periodically re-injects a tiny read of `window.
  // __bet9jaSettledBetsProgress` while this call is in flight, and a
  // "Cancel" click sets `window.__bet9jaSettledBetsCancelRequested`,
  // which settled_bets_parser.js's `shouldCancel` polls between pages.
  window.__bet9jaSettledBetsCaptureRun = function (sourceUrl, pageTitle, capturedAtUtc) {
    window.__bet9jaSettledBetsProgress = { pageNumber: 0, pagesVisited: 0, pagesAvailable: null, ticketsParsedSoFar: 0 };
    window.__bet9jaSettledBetsCancelRequested = false;
    return window.Bet9jaSettledBetsCapture.captureFromDocument(document, {
      sourceUrl,
      pageTitle,
      capturedAtUtc,
      onProgress: (info) => {
        const prior = window.__bet9jaSettledBetsProgress || { ticketsParsedSoFar: 0 };
        window.__bet9jaSettledBetsProgress = {
          pageNumber: info.pageNumber,
          pagesVisited: info.pagesVisited,
          pagesAvailable: info.pagesAvailable,
          ticketsParsedSoFar: (prior.ticketsParsedSoFar || 0) + (info.pageResult ? info.pageResult.tickets_parsed : 0),
        };
      },
      shouldCancel: () => window.__bet9jaSettledBetsCancelRequested === true,
    });
  };

  window.__bet9jaSettledBetsReadProgress = function () {
    return window.__bet9jaSettledBetsProgress || null;
  };

  window.__bet9jaSettledBetsRequestCancel = function () {
    window.__bet9jaSettledBetsCancelRequested = true;
    return true;
  };
})();
