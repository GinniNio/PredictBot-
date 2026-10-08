# 2026-10-01 captures — backfilled 2026-10-04

Raw bytes recovered unchanged from the processing session's scratchpad and
committed retroactively; see `captures/README.md` for why. Same inputs
already consumed by that day's cycle, reported in `DAILY_REPORT_2026-10-01.md`.

- `bet9ja-soccer-all-soccer-2026-10-01T10-02-56Z.json` — assembled aggregate capture, as uploaded.
- `bet9ja-soccer-session-soccer-2026-10-01T10-02-56Z-segment-001.json` — same session's per-run segment export (rejected by ingestion as non-assembled; preserved for completeness).
- `bet9ja-soccer-all-soccer-2026-10-01T10-02-56Z-FINAL.json` — the derived input actually run through `run-bet9ja-research`, excluding the 4 fixture_ids that conflicted with existing ledger records that day (Ecuador "Serie A" competition_id `7869263`, and competition_id `7167226`).
- `conflict-exclusions-2026-10-01.json` — the excluded fixture_ids and exact conflict reasons.
- `bet9ja-open-bets-2026-10-01T09-52-33Z.json` / `bet9ja-settled-bets-2026-10-01T09-53-04Z.json` — ticket captures, as uploaded.
- `PredictBot_Manual_Results_2026-09-30.json` — manual evidence input, as uploaded.
- `input-hashes.sha256` — SHA-256 of each raw input, recorded at capture time.
