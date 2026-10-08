# 2026-09-26 captures — backfilled 2026-10-04

Raw bytes recovered unchanged from the processing session's scratchpad and
committed retroactively after the operator noticed no day past 2026-09-23 had
been preserved in git. Not re-processed — these are the same inputs already
consumed by that day's `run-bet9ja-research` / `import-bet9ja-tickets` /
`convert-manual-results-evidence` cycle, reported in that day's daily report.

- `bet9ja-soccer-all-soccer-2026-09-26T10-09-39Z.json` — assembled aggregate capture, as uploaded.
- `bet9ja-soccer-session-soccer-2026-09-26T10-09-39Z-segment-001.json` — same session's per-run segment export (rejected by ingestion as non-assembled; preserved for completeness).
- `bet9ja-soccer-session-soccer-2026-09-26T10-02-16Z-segment-001.json` — an earlier same-day segment export, also non-assembled.
- `bet9ja-soccer-all-soccer-2026-09-25T08-05-03Z_1.json` — a stale prior-day aggregate re-supplied this day; excluded from processing as redundant/stale, preserved for provenance.
- `bet9ja-soccer-all-soccer-2026-09-26T10-09-39Z-RECONCILED.json` — the derived input actually run through `run-bet9ja-research` that day.
- `bet9ja-open-bets-*` / `bet9ja-settled-bets-*` — ticket captures, as uploaded.
- `PredictBot_Manual_Results_2026-09-25.json` — manual evidence input, as uploaded.
