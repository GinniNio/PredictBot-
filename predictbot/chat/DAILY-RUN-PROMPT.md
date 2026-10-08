Run PCBF Mini v1.4.2 on the Bet9ja fixtures below and give me today's PICKs.

The attached PCBF-MINI-v1.4.md is the only rulebook. Ignore every older PCBF version.
Zero PICKs is a valid result. Do not stretch to fill a table. Do not ask whether to continue.

SETUP
- Use your code tool for every calculation. Run the rulebook's Python block first; if a self-check fails or you cannot run code, stop and say so.
- Use web search for benchmarks and news.
- Attached: Bet9ja capture files (or odds pasted below) and pcbf-ledger.csv (may be empty).
- Optional for EPL / LaLiga / Serie A / Bundesliga / Ligue 1: football_score_v1.py plus E0/SP1/I1/D1/F1 .json.

CONFIRMED SETTLEMENT RULES (from the app's Pending page; anything not listed is unconfirmed)
- none yet

WHAT EACH LABEL MEANS (rulebook "Labels")
- PICK = paper pick: edge >= +3% against a valid same-day Pinnacle, OddsPortal or Oddschecker benchmark, no caution. ₦25 notional.
- WATCH = valid benchmark but edge below +3% (negative included) or any caution. Logged.
- RESEARCH = no usable benchmark. Logged with the reason. No probability, fair odds or edge.
- REJECTED = failed a data check. Logged with the reason.
- Polymarket quoted by you is always WATCH (no order-book evidence). PM_PAPER is assigned only by the app.
- Real money: none. Only a written evidence review by me changes that. Report settled PICKs so far: N, counted from the attached ledger (review point 200). If the attached ledger has no rows, write "see app"; never guess or write 0.

TWO SOURCES THAT NEVER MIX
1. Numbers come only from code: the Python block on benchmark prices, plus the football model column when its files are attached. A team not in the model's params: "not in model". Never substitute or estimate.
2. News comes from search: injuries, lineups, manager changes, weather, integrity. News adds a note. It never changes a number, a label or the ledger.
   Pundit and tipster picks are opinion, not benchmarks. Three sites repeating one take count as one source.

STEPS
1. Parse every attached capture file, every sport. Count games per sport. Apply the rulebook's step 1 skip list, counting each reason. Judge "already started" and "within 60 minutes" against the current UTC time when you run this step, not the capture time, and state that time. Screen youth / reserve / semi-pro only from names in the capture, never from your own knowledge of a league.
2. For each remaining game, find a same-day benchmark in the rulebook's source order, on pinnacle.com, oddsportal.com, oddschecker.com or polymarket.com only. A Pinnacle price counts only from pinnacle.com or Pinnacle's own row on OddsPortal or Oddschecker, never from a preview or aggregator page. Record source, URL, the time you read that page (UTC, per row), the full market, and for OddsPortal the bookmaker rows.
3. Check settlement against the list above. Unconfirmed variable rule: caution. Different rule: REJECTED.
4. Run the Python block for every selection of every priced game. For EPL/LaLiga/Serie A/Bundesliga/Ligue 1, also run(model, home, away) if the files are attached.
5. Label with label(). Run a news check on every PICK.
6. Add every selection to the ledger, all labels. If I say "settle", fill result, closing prices with their label (closing price / pre-kickoff snapshot), closing fair odds, CLV and P&L for open rows.

OUTPUT (this order, nothing else)
1. PICKS table:
   | Selection | Game | Sport/comp | Kickoff UTC | Bet9ja | Min odds | Fair | Edge | Model (football only) | Source, URL, time | News note |
   If none: "No PICKs this batch."
2. WATCH table: same columns, the 10 highest edges. All WATCH rows go in the ledger regardless.
3. RESEARCH: up to 5 games with no benchmark, one line each: game, Bet9ja prices, why no benchmark was found. A news note may follow, as a note only: no rating, no probability.
4. Counts line for every sport in every attached file: games received / benchmarked / screened out (reasons) / no benchmark / rejected. Then: screened at HH:MM UTC; settled PICKs so far: N (or "see app").
5. Updated pcbf-ledger.csv as a download, in the rulebook's columns, one row per selection.

End with: "Recheck every Bet9ja price before placing; skip it if below min odds. No real money without a written evidence review."

FIXTURES BEGIN
