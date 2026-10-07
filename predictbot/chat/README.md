# Running the full chat workflow (optional)

The app does all of this itself. Use these files only to see what a chat produces on its own, and to compare it with the app.

1. Open Claude, ChatGPT or Gemini and attach:
   - `PCBF-MINI-v1.4.md` (the rulebook)
   - today's Bet9ja capture files from your data folder's `captures/`
   - `pcbf-ledger-template.csv` renamed to `pcbf-ledger.csv` (empty: header only), or the chat's ledger from a previous run
2. Paste `SESSION-START-PROMPT.txt`, then `DAILY-RUN-PROMPT.md`. Update its "CONFIRMED SETTLEMENT RULES" list from the app's Pending page first.
3. Save the chat's ledger as `pcbf-ledger-<date>.csv` in the data folder (next to `captures/`) and restart the app. It imports the rows once, recomputes every edge and label from the benchmark prices, and keeps the chat's own label in the note ("chat said PICK").
4. Compare on the Full log page: rows with origin `pcbf-ledger ...` are the chat's; the rest are the app's.

Differences to expect: the app reads Polymarket's feed (bid/ask, liquidity) and can label PM_PAPER, while a chat's Polymarket quote is always WATCH; the app's OddsPortal capture lists every bookmaker row, while a chat may read fewer.
