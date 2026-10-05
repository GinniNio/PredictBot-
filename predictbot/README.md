# PredictBot

PredictBot finds Bet9ja prices that pay more than the fair market price. It runs the PCBF Mini v1.3 rulebook as code on your own computer. An AI chat does only the research: benchmark prices and news. The app does all the arithmetic and keeps the record.

```
python predictbot/app.py "C:\Users\<you>\OneDrive\PredictBot"
```

This opens http://localhost:8000. It needs Python 3.10+ and nothing else installed. It never places a bet.

## Where the odds come from

- **All sports:** the Chrome extension in `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/`. Its "Walk sport" button saves one file per sport (`bet9ja-allsports-walk-<sport>-*.json`, schema `bet9ja-allsports-sport-walk.v2.1`).
- **Soccer, open bets, settled bets:** the older extension in `browser_extension/bet9ja_capture/`, schemas `bet9ja-soccer-session.v1`, `bet9ja-ticket-capture.v1` and `bet9ja-settled-bets.v1`.

Each time a page loads, the app copies new `bet9ja-*.json` files from your Downloads folder into the data folder, with their bytes unchanged.

## The daily loop

1. **Capture** each sport with the extension.
2. **Candidates** page. Every fixture's main market is checked against the rulebook's step-1 rules. Each fixture is either sent for benchmarking or screened out with a reason: already started, locked price, youth or reserve, virtual, excluded country, or unsupported market.
3. **Create research pack.** This makes a batch of up to 40 fixtures, earliest kickoff first, with a fixed reply format. Paste it into Claude, ChatGPT or Gemini.
4. **Paste the chat's reply back.** For every line, the app checks the source, URL, timestamp and price count, then **recomputes the edge itself**. Nothing the chat says about edges or picks is used. Every outcome is saved as a selection record before any bet can exist.
5. **Bets** page. Record actual wagers, which can only point at a logged selection. Real bets are a separate, optional ledger.
6. **Selections → Create settlement pack.** Once games are over, paste results and closing prices back. The app works out the result, CLV and notional P&L.
7. **Performance** page. Shows CLV, ROI, win rate and sample size by tier, sport, market, odds band and benchmark source.

## Benchmark requirement

A price is compared with the **same market, same settlement, same day** from one source, in this order: Pinnacle, then the oddsportal average, then oddschecker's best prices. Edge = Bet9ja price × fair probability − 1. The fair probability is the more cautious of the proportional and power de-vigs, using the rulebook's code verbatim (`pcbf.py`).

## Tiers

| Tier | Meaning |
|---|---|
| `PICK` | Valid benchmark, defined market, current price, edge ≥ +3%, no caution flag. Logged at a notional ₦25. |
| `WATCH` | Valid record, but the edge is below +3%, including negative edges. Also used for an otherwise valid record with a caution flag: stale benchmark, or capture and benchmark more than 6h apart. |
| `RESEARCH` | No usable benchmark: the chat said NONE, or the source isn't one of the three allowed. |
| `REJECTED` | Failed validation: missing URL or timestamp, wrong number of prices, benchmark after kickoff, duplicate selection, or an imported row that can't be recomputed. |

**A priced selection is not a bet.** Every priced outcome is logged, PICK and WATCH alike, as a control group. A bet is something you actually placed on Bet9ja, recorded separately against one of those selections.

## Real-money lock

Locked until there are **200 settled PICKs with mean CLV > 0 and notional ROI > 0** (PCBF Mini step 5). The app shows the lock state and never places wagers. Treat early negative CLV as a reason to check data quality, market matching, timestamps and benchmark source before changing any rule.

## Data folder (synced, not in git)

| Path | Format | Contents |
|---|---|---|
| `captures/*.json` | raw JSON | Bet9ja captures, bytes unchanged |
| `captures/capture-records.csv` | CSV, append-only | One row per selection sent for benchmarking: `capture_id`, timestamps, source URL, event, market, selection, odds, `raw_payload_hash` |
| `selections/selections.csv` | CSV, append-only | Every PICK, WATCH, RESEARCH or REJECTED record: benchmark inputs, fair odds, edge, tier, validation status, reason |
| `selections/screening.csv` | CSV, append-only | Every candidate screened when a pack was made, with its reason |
| `bets/bets.csv` | CSV, append-only | Actual wagers |
| `settlements/settlements.csv` | CSV, append-only | Results and notional returns |
| `closing-prices/closing-prices.csv` | CSV, append-only | Closing market, closing fair odds, CLV |
| `packs/*.json` | JSON | Research and settlement packs, so reply codes resolve on either laptop |
| `pcbf-ledger*.csv` | CSV | Optional. Old PCBF Mini ledgers here are imported once at startup. |

Field definitions are in `schemas.py`. Rows are never edited or deleted, and a record that fails validation is refused before it's written.

## Code

| File | Role |
|---|---|
| `pcbf.py` | Pure, deterministic core: odds validation, market normalisation, de-vig, edge, tiers, validation, CLV, performance |
| `schemas.py` | Record definitions and validation |
| `storage.py` | Append-only reads and writes in the data folder |
| `workflow.py` | The daily loop, joining the core and storage |
| `tickets.py` | Bet9ja ticket P&L from open and settled bet captures |
| `app.py` | Thin stdlib web interface over `workflow.py` |
| `tests/` | `python -m unittest discover -s predictbot/tests` |
