# PredictBot Public Odds Capture Walker

Load this folder as an unpacked Chrome extension. Open a public pre-match page at a supported source, click the extension, set a cap, then start the walk.

The extension captures raw DOM snapshots only. It never logs in, places bets, submits forms, bypasses access controls, or calls private APIs.

Automatic event-link discovery is enabled only for route shapes verified in this build: OddsPortal H2H fixtures, Oddschecker US event pages, Flashscore fixtures, BetExplorer match pages, and Polymarket sports cards. Polymarket's non-sports `/event/...` pages are included only when the popup's non-sports option is selected. Betfair Exchange remains evidence-first because its live public page was unavailable during the latest audit.

## Results (0.4.0)

**Walk results** visits the finished games the PredictBot app is still waiting on. The app lists their OddsPortal match pages (with the match's `#hash`) in `/walker-targets.json` under `results`. Open any oddsportal.com page in the tab, keep the app running, and click it. For each page the walker waits up to 15 s for the `Final result …` line under the teams and copies that text with the page's kickoff date. It doesn't read odds or interpret the score; the app does that. A page that says Postponed, Cancelled, Abandoned, Interrupted, Awarded or Walkover is recorded as not played. The run downloads as `public-odds-walk-oddsportal-results-<time>.json`. The app reads it from Downloads, and each capture has `role: "result"`.

## Polymarket: all sports (0.2.0)

Start at `polymarket.com/sports` (it redirects to `/sports/live`) and set the cap to 100–300. The walk runs in two stages:

1. **Catalogue.** It collects every sport listing in the Sports menu (`/sports/<league>/games`) and visits each one. The 2026-10-05 capture showed 23. `/props`, `/live` and `/futures` are never visited.
2. **Events.** It captures each `/sports/<league>/<event>` page dated yesterday to tomorrow (UTC). For each event it keeps only the embedded market JSON (`html_mode: EXCERPT_outcomePrices`, about 12 KB instead of about 800 KB). The full page length is recorded.

Listing pages render client-side. The walker waits 3 s after load before reading links, and that delay hasn't been checked against a slow connection. If a listing reports `event_links: 0` in `run.listings`, raise `LISTING_SETTLE_MS` in `background.js`.
