# PredictBot

PredictBot finds Bet9ja prices that pay more than the fair market price. It runs the PCBF Mini v1.3 rulebook as code on your own laptop: it reads the capture files your browser extensions save, compares Bet9ja with benchmark prices, logs every priced selection, and measures whether the method works. It never places a bet.

## Start it

Double-click **`PredictBot.cmd`** in the repo folder (make a desktop shortcut to it). It updates the code from GitHub, then opens http://localhost:8000.

By hand: `python predictbot/app.py "C:\Users\<you>\OneDrive\PredictBot"`. Python 3.10+ and nothing else. With no folder given it uses `%OneDrive%\PredictBot`.

Stop it with the **Stop PredictBot** button (top right) before switching laptops.

## The daily loop

1. **Capture** with the Chrome extensions:
   - Bet9ja, one sport at a time: `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/` (walker 0.3.0, "Walk sport").
   - Benchmarks: `browser_extension/public-odds-capture-walker/` on Polymarket (`polymarket.com/sports`) and OddsPortal sport pages. Capture these close in time to the Bet9ja walk.
   - Your bets: `browser_extension/bet9ja_capture/` ("Capture open bets", settled bets).
2. **Opportunities.** Opening it copies new files from Downloads into the data folder (bytes unchanged) and prices every fixture it can match to a captured benchmark. You see:
   - **Coverage by sport**: fixtures captured, with usable prices, screened out by the rulebook, benchmarked, unresolved (with reasons), on the shortlist.
   - **Shortlist**: PICK and PM_PAPER for upcoming games, with **min odds**: the lowest Bet9ja price that still clears +3% at the fair probability.
   - **Recheck**: type the Bet9ja price you see just before betting. The app stores the initial and rechecked odds and recomputes the edge and tier. Below min odds, skip it.
3. **Pending.** Everything waiting on you:
   - **Match review**: a benchmark event whose names only partly agree, or whose page has no kickoff time, or two events that both fit. Pick *Same game* or *Different*. If a higher-priority source is waiting for review, the fixture waits too, so a lower source never wins by default.
   - **Settlement rules to confirm** (below).
   - **No benchmark yet**: capture those sports on OddsPortal or Polymarket, or send them to a chat as a research pack (optional).
   - **Settlement**: create a settlement pack for finished games and paste the chat's results back.
   - **Handover** and **OneDrive conflicts** when they occur.
4. **Performance.** Forecast quality, paper results, actual wagers, and the written evidence review.

An empty shortlist is a valid result. Missing data is shown with a reason, never filled in.

## Benchmarks and tiers

Order of preference (PCBF Mini, with Polymarket added by the operator on 2026-10-05): Pinnacle (direct, or its row on an OddsPortal page), then the OddsPortal average, then Oddschecker, then Polymarket.

| Tier | Meaning |
|---|---|
| `PICK` | Valid benchmark, edge ≥ +3%, no caution. Notional ₦25. |
| `PM_PAPER` | Would be a PICK, but the only benchmark is Polymarket. A separate paper category until an evidence review allows Polymarket PICKs. Notional ₦25. |
| `WATCH` | Valid, but edge below +3% or a caution: stale benchmark, capture and benchmark over 6h apart, settlement rule unconfirmed, or (Polymarket) quote over 2h from the Bet9ja capture or price not a bid/ask midpoint. |
| `RESEARCH` | No usable benchmark: none found, or a thin, wide or shallow Polymarket market. |
| `REJECTED` | Failed validation: missing URL or time, wrong price count, benchmark after kickoff, duplicate, or a settlement rule that differs from Bet9ja's. |

**OddsPortal average.** Every complete bookmaker row is de-vigged on its own (proportionally), then the probabilities are averaged; a margin is never built from prices taken across different books, and Bet9ja's own row is excluded (operator's odds-source audit, 6 Oct 2026). Each selection lists the bookmakers used. Because the averaged probabilities already sum to 1, the rulebook's cautious power de-vig changes nothing for OddsPortal quotes; it still applies to prices pasted from a chat.

**Polymarket evidence.** Each quote records its price basis. On every moneyline captured so far, the displayed price equals the bid/ask midpoint; a quote where it does not is labelled and cannot be PM_PAPER. Thresholds (volume ≥ $5,000, spread ≤ 0.04, liquidity ≥ $5,000 on the thinnest leg, quote within 2h) are rules under evaluation, set in `odds_sources.py` and `pcbf.py`.

**Settlement rules.** A benchmark must settle the way Bet9ja does. Pinnacle, OddsPortal and Oddschecker follow the standard convention for 1X2 (regulation time) and moneylines (overtime included). Where treatment varies, you confirm once per source, sport and market on the Pending page: tennis and table tennis retirement, cricket ties and no-results, MMA and boxing draws, and every Polymarket market. Until confirmed, those selections stay WATCH. A confirmation applies to future pricing; past records are not changed.

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
| `captures/*.json` | Raw captures, bytes unchanged |
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
| `odds_sources.py` | Captured benchmark pages to one quote format; matching and review grading |
| `schemas.py` | Record definitions, versions, validation |
| `storage.py` | Append-only files, write guard, versions, conflict merge, session marker |
| `workflow.py` | The daily loop joining core and storage |
| `tickets.py` | Bet9ja ticket P&L from bet captures |
| `app.py` | Thin stdlib web interface |
| `tests/` | `python -m unittest discover -s predictbot/tests` |
