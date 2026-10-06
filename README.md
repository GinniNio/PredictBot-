# PredictBot

A local tool that compares Bet9ja prices with fair market prices, logs every priced selection, and measures whether the method works before any real money is staked. It never places bets.

- **App:** [`predictbot/`](predictbot/README.md). Double-click `PredictBot.cmd` (it pulls the latest code, then opens http://localhost:8000), or run `python predictbot/app.py "<synced data folder>"`.
- **Capture extensions** (Chrome, load unpacked):
  - `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/`: Bet9ja fixtures and odds, one sport per file.
  - `browser_extension/public-odds-capture-walker/`: benchmark prices from Polymarket and OddsPortal pages.
  - `browser_extension/bet9ja_capture/`: Bet9ja open and settled bets.
- **Data** lives outside git, in the synced data folder described in `predictbot/README.md`.

The legacy pipeline (calculator CLI, ledgers package, Elo adapter, data pipeline) was removed on this branch. It is preserved at commit `3e819c8` and tag `legacy-baseline-2026-09-30`; see `predictbot/MIGRATION.md` for how to restore any part of it.
