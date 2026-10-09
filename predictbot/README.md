# PredictBot

PredictBot finds Bet9ja prices that pay more than the fair market price. It runs the PCBF Mini v1.3 rulebook as code on your own laptop: it reads the capture files your browser extensions save, compares Bet9ja with benchmark prices, logs every priced selection, and measures whether the method works. It never places a bet.

## Start it

Double-click **`PredictBot.cmd`** in the repo folder (make a desktop shortcut to it). It updates the code from GitHub, then opens http://localhost:8000.

By hand: `python predictbot/app.py "C:\Users\<you>\OneDrive\PredictBot"`. Python 3.10+ and nothing else. With no folder given it uses `%OneDrive%\PredictBot`.

Stop it with the **Stop PredictBot** button (top right) before switching laptops.

## The daily loop

1. **Capture** with the Chrome extensions:
   - Bet9ja, one sport at a time: `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/` (walker 0.3.0, "Walk sport").
   - Benchmarks: `browser_extension/public-odds-capture-walker/` on OddsPortal, close in time to the Bet9ja walk. **Find all Bet9ja games** (walker 0.5.0) searches OddsPortal for every Bet9ja game the app still lacks a benchmark for; a walk from a sport page only reaches the ~60 matches that page lists. **Polymarket needs no capture**: the app reads its public feed itself (below).
   - Your bets: `browser_extension/bet9ja_capture/` ("Capture open bets", settled bets).
2. **Opportunities.** Opening it copies new files from Downloads into the data folder (bytes unchanged), fetches Polymarket's upcoming games (at most every 10 minutes), and prices every fixture it can match to a benchmark. You see:
   - **Coverage by sport**: fixtures captured, with usable prices, screened out by the rulebook, benchmarked, unresolved (with reasons), on the shortlist.
   - **Shortlist**: PICK and PM_PAPER for upcoming games, with **min odds**: the lowest Bet9ja price that still clears +3% at the fair probability.
   - **Recheck**: type the Bet9ja price you see just before betting. The app stores the initial and rechecked odds and recomputes the edge and tier. Below min odds, skip it.
   - **Your bets, automatically**: open and settled bet captures are read on every load. A ticket leg on a game and side the app logged before the bet becomes a bet record when it is staked as a single (the singles part of a system ticket; accumulator-only legs are reported on the Bets page, not counted). Settled legs settle every logged selection of that game (a lost 1X2 leg needs the score to tell draw from away). Nothing is entered by hand; the manual form on Bets is only for a bet with no capture.
3. **Pending.** Everything waiting on you:
   - **Match review**: a benchmark event whose names only partly agree, or whose page has no kickoff time, or two events that both fit. Pick *Same game* or *Different*. If a higher-priority source is waiting for review, the fixture waits too, so a lower source never wins by default.
   - **Settlement rules to confirm** (below).
   - **No benchmark yet**: capture those sports on OddsPortal or Polymarket, or send them to a chat as a research pack (optional).
   - **Settlement**: finished games settle themselves (see **Results** below). Pending lists the ones still waiting and why. A settlement pack through a chat is only for what is left.
   - **Handover** and **OneDrive conflicts** when they occur.
4. **Performance.** Forecast quality, paper results, actual wagers, and the written evidence review.
5. **Bets.** Your real betting from the open and settled bet captures: every ticket's P&L in total, by week placed and by sport, plus the singles placed on games the app logged, by sport. Ticket captures have no sport, so it is found by matching the teams (or a competition that belongs to one sport) to your Bet9ja sport walks; tickets mixing sports show as *mixed*, games never walked as *unknown*.

An empty shortlist is a valid result. Missing data is shown with a reason, never filled in.

## Benchmarks and tiers

Order of preference (PCBF Mini, with Polymarket added by the operator on 2026-10-05): Pinnacle (direct, or its row on an OddsPortal page), then the OddsPortal average, then Oddschecker, then Polymarket.

| Tier | Meaning |
|---|---|
| `PICK` | Valid benchmark, edge ≥ +3%, no caution. Notional ₦25. |
| `PM_PAPER` | Would be a PICK, but the only benchmark is Polymarket. A separate paper category until an evidence review allows Polymarket PICKs. Notional ₦25. |
| `WATCH` | Valid, but edge below +3% or a caution: stale benchmark, capture and benchmark over 6h apart, settlement rule unconfirmed, or (Polymarket) quote over 2h from the Bet9ja capture or price not a bid/ask midpoint. |
| `RESEARCH` | No usable benchmark: none found, or a Polymarket market that is too wide, too shallow or has no liquidity figure. |
| `REJECTED` | Failed validation: missing URL or time, wrong price count, benchmark after kickoff, duplicate, or a settlement rule that differs from Bet9ja's. |

**OddsPortal average.** Every complete bookmaker row gets the rulebook's cautious de-vig on its own (the lower of the proportional and power estimates per outcome), then the probabilities are averaged. A margin is never built from prices taken across different books, and Bet9ja's own row is excluded (audit 6 Oct; caution kept per bookmaker, 7 Oct). The averaged probabilities sum to just under 1, which keeps the rulebook's caution on draws and longshots; the rulebook's `edge()` returns them unchanged. Each selection lists the bookmakers used.

**Polymarket feed.** The app reads Polymarket's public data feed (`gamma-api.polymarket.com`, free, no key) instead of walking its pages: every game starting in the next 36 hours, in a few requests. Each fetch is saved as `captures/polymarket-feed-<time>.json` (game events with their moneyline markets, including each market's own rules text), so every price traces back to the response it came from. If the feed is unreachable, the day carries on with earlier fetches and OddsPortal. Esports are skipped. Polymarket's rules text is shown on Pending when you confirm a settlement rule.

**Polymarket evidence.** Each quote records its price basis. On every moneyline captured so far, the displayed price equals the bid/ask midpoint; a quote where it does not is labelled and cannot be PM_PAPER. Depth is judged on resting liquidity (≥ $5,000 on the thinnest leg) and spread (≤ 0.04); a quote with no liquidity figure is RESEARCH. There is no traded-volume rule: pre-match volume builds near kickoff, and a $5,000 floor rejected 597 of 627 feed quotes on 6 Oct (dropped 7 Oct). These thresholds and the 2h freshness limit are rules under evaluation, set in `odds_sources.py` and `pcbf.py`.

**Settlement rules.** A benchmark must settle the way Bet9ja does. Pinnacle, OddsPortal and Oddschecker follow the standard convention for 1X2 (regulation time) and moneylines (overtime included). Where treatment varies, you confirm once per source, sport and market on the Pending page: tennis and table tennis retirement, cricket ties and no-results, MMA and boxing draws, and every Polymarket market. Until confirmed, those selections stay WATCH. A confirmation applies to future pricing; past records are not changed.

**Results.** Every logged game (PICK, PM_PAPER, WATCH and RESEARCH) gets a result without a chat, from three sources, checked once it is 3 hours past kickoff and for up to 7 days:
- **Your settled Bet9ja tickets** (see Bets).
- **Polymarket**: when Opportunities loads, the app fetches the resolved markets of finished games it matched to Polymarket (`gamma-api.polymarket.com`, at most every 30 minutes) and saves each response as `captures/polymarket-results-<time>.json`. A winner priced 1 settles the game. A 50-50 resolution (cancelled or tied) is left for you.
- **OddsPortal**: in the odds walker, **Walk results** visits the finished games' OddsPortal match pages and copies their `Final result` line, e.g. `5:4 OT (0:2, 3:2, 1:0, 1:0)`.

Results are read on Bet9ja's terms. 1X2 settles on regulation time, so after overtime, extra time or penalties the regulation periods are added up (that example is a 4:4 draw). Moneylines include overtime. Retirements, walkovers, awarded, cancelled and abandoned games are never settled automatically. For tennis, table tennis, cricket, MMA and boxing, Polymarket results are used only after you confirm its rule on Pending, and OddsPortal results only when the game finished normally. If two sources disagree, nothing is settled and Pending says so. The settlement records which source and file settled each selection.

**Provenance on every selection**: benchmark source, URL, the underlying bookmakers (OddsPortal lists every row it averaged), quote time, capture time, the capture file, price basis, market depth, settlement check, fair probability, min odds and app version.

## Closing prices and pre-kickoff snapshots

If the odds walker captured the same benchmark page again before kickoff, the app stores the last such quote after the game starts. It is labelled **closing price** only when taken within 5 minutes of kickoff; otherwise **pre-kickoff snapshot, N min before kickoff**. Closing prices pasted from a chat get the same label. A quote taken after kickoff is never used.

## Forecast quality

Performance scores every settled event that has a full priced market (PICK, PM_PAPER and WATCH alike): multi-outcome **Brier score** and **log loss**, with 95% intervals computed across events (outcomes of one match are not independent), by source, and against Bet9ja's own de-vigged prices on the same events. A calibration table compares forecast bands with observed frequencies. The rulebook's cautious de-vig gives probabilities that sum to slightly under 1, so they are normalised for scoring. Paper results and actual wagers are reported separately.

## Real money: written evidence review

Nothing unlocks automatically. A review on the Performance page records a written summary, a decision (continue paper, change method, stop, allow small real stakes) and whether Polymarket-only opportunities may be PICKs. The metrics at that moment are saved with it. The rulebook's 200 settled PICKs is shown as a review point.

## Two laptops

One GitHub repo; each laptop runs its own copy of the app; both use the same OneDrive data folder. Code never goes in OneDrive.

**One designated writer.** Only one laptop writes at a time. A synced lock file cannot guarantee that: both laptops can read a stale copy before OneDrive syncs. So the switch is an explicit handover:
1. On the laptop you are leaving, click **Stop PredictBot** (or press Ctrl+C in its window).
2. Wait until OneDrive shows **Up to date** on both laptops.
3. Start PredictBot on the other laptop. It opens read-only and names the current writer.
4. On its Pending page, click **Make <this laptop> the writer**. The record files are backed up to `.backup/handover-<time>-<laptop>/` first.

Restarting on the same laptop needs no handover.

**What catches mistakes:**
- **One copy per laptop.** The app holds port 8000; a second start just opens the running one.
- **Writer marker** (`predictbot-session.json`) names the designated writer. Any other laptop is read-only until the handover. If the old writer was in fact still running, it sees the new marker within a minute and stops writing.
- **OneDrive conflict copies** (`selections-HP.csv`, `settlements (1).csv`) block writes. **Merge** on Pending adds the copy's rows to the original (every original row kept; a copy row that matches an original row apart from timestamps is the same record written twice and is skipped) and moves the copy to `.backup/`.
- **Versions.** `predictbot-data.json` records the data-schema version and the last writer. A laptop with older code refuses to write until you `git pull` (the launcher does this).
- **Idempotent imports.** Captures are stored once by content hash. Pricing the same quote again, or pasting the same chat reply on either laptop, writes nothing new.

The handover is the protection; these checks are guards and indicators, not a guarantee.

## Data folder (synced, not in git)

| Path | Contents |
|---|---|
| `captures/*.json` | Raw captures, bytes unchanged; `polymarket-feed-*.json` are the app's own feed fetches |
| `captures/capture-records.csv` | One row per Bet9ja selection priced |
| `selections/selections.csv` | Every PICK, PM_PAPER, WATCH, RESEARCH and REJECTED record |
| `selections/screening.csv` | Candidates screened when a research pack was made |
| `rechecks/rechecks.csv` | Bet9ja prices checked again before betting |
| `bets/bets.csv` | Actual wagers |
| `settlements/settlements.csv` | Results and notional returns |
| `closing-prices/closing-prices.csv` | Closing prices and labelled pre-kickoff snapshots, CLV |
| `reviews/match-reviews.csv` | Your match decisions |
| `reviews/evidence-reviews.csv` | Written evidence reviews |
| `rules/settlement-rules.csv` | Settlement rules you confirmed |
| `packs/*.json` | Research and settlement packs |
| `predictbot-data.json`, `predictbot-session.json` | Data version; the designated writer |

Rows are never edited or deleted. When a newer app adds columns, a file is rewritten once with the wider header (values unchanged, the original kept in `.backup/`). The daily views read the last two days of captures.

## Code

| File | Role |
|---|---|
| `pcbf.py` | Pure core: rulebook de-vig and edge, tiers, settlement rules, rechecks, snapshot labels, forecast scoring, performance |
| `odds_sources.py` | Captured pages and feed fetches to one quote format; matching and review grading |
| `feeds.py` | Fetches Polymarket's public feed and resolved results (the app's only network code) |
| `results.py` | Reads final results: Polymarket resolutions and OddsPortal `Final result` lines (settlement: `workflow.settle_from_results`) |
| `schemas.py` | Record definitions, versions, validation |
| `storage.py` | Append-only files, write guard, versions, conflict merge, session marker |
| `workflow.py` | The daily loop joining core and storage |
| `tickets.py` | Bet9ja ticket P&L, by-week and by-sport breakdowns, and leg parsing from bet captures (linking and settlement: `workflow.sync_tickets`; sport lookup: `workflow.betting_breakdown`) |
| `app.py` | Thin stdlib web interface |
| `chat/` | Optional: the PCBF Mini v1.4 rulebook and prompts for running the whole workflow in a chat, to compare with the app |
| `tests/` | `python -m unittest discover -s predictbot/tests` |
