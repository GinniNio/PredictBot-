# Migration from the legacy pipeline

The legacy pipeline was removed from this branch in commit `15e1083`. Nothing is lost: `main` still has it, and so do commit `3e819c8` and the tag `legacy-baseline-2026-09-30`.

## Restore a piece

```
git checkout legacy-baseline-2026-09-30 -- <path>
```

## What was removed

| Path | What it was | Worth reusing? |
|---|---|---|
| `src/pcbf_calculator/` | Calculator CLI, Bet9ja ingestion, decision layer, settlement CLIs | No. `pcbf.py` and `workflow.py` replace it. |
| `src/pcbf_calculator/adapters/soccer_1x2_elo_v1/` | Soccer Elo + logistic-regression 1X2 model with a trained artifact | Possibly, as a starting point if a football model is built. Check its data source's terms first. |
| `ledgers/` | Forecast and betting ledger package | No. `schemas.py` and `storage.py` replace it. Its real data was lost with the cloud sessions. |
| `data_pipeline/`, `research/`, `registries/` | Football-Data retrieval, dataset builder, model research | Only if a football model is built. |
| `tests/` | Legacy test suite | No. |
| `docs/` | Legacy design and host-contract docs | No. `REQUIREMENTS.md` moved to `predictbot/`. |
| `captures/2026-09-23/` | 23 Sep captures | Copied into the synced data folder. |
| Root `app.py`, `backtest.py`, `train_leagues.py`, `weekly_picks.py`, `SETUP_GUIDE.md`, root CSVs, `pyproject.toml`, `release-manifest.template.json` | First local app and ProphitBet-era scripts | Ticket P&L logic already copied into `tickets.py`. |
| `.github/workflows/` legacy workflows | Runtime probe, football-data feasibility, disabled capture processing | No. |
| `browser_extension/bet9ja_capture_allsports/` | Stage-1 snapshot-only extension | No. The v0.2.2 folder (walker 0.3.0) replaces it. |

## Data migration

| Source | Status |
|---|---|
| PCBF Mini ledger (`pcbf-ledger*.csv`) | Imported at app startup by `workflow.import_pcbf_ledger`. Rows 003 and 004 recompute to PICK; rows 001 and 002 are REJECTED because their benchmark markets were never recorded. |
| Bet9ja captures since 23 Sep | Copy into the data folder's `captures/`. Duplicates by content are skipped. |
| Cloud-session forecast and betting ledgers | Lost. Nothing to migrate. |
