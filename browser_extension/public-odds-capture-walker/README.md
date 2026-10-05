# PredictBot Public Odds Capture Walker

Load this folder as an unpacked Chrome extension. Open a public pre-match page at a supported source, click the extension, set a cap, then start the walk.

The extension captures raw DOM snapshots only. It never logs in, places bets, submits forms, bypasses access controls, or calls private APIs.

Automatic event-link discovery is enabled only for route shapes verified in this build: OddsPortal H2H fixtures, Oddschecker US event pages, Flashscore fixtures, BetExplorer match pages, and Polymarket sports cards. Polymarket's non-sports `/event/...` pages are included only when the popup's non-sports option is selected. Betfair Exchange remains evidence-first because its live public page was unavailable during the latest audit.
