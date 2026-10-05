# Migration from the legacy pipeline

`predictbot/` replaces the earlier pipeline. Until the new loop has produced validated records on real days, every legacy area stays in place, read-only. Deleting legacy files is separate cleanup and needs its own reviewed commit.

**Legacy baseline:** `origin/main` at `3e819c8` (PR #72, 2026-09-30). This session couldn't push a tag, so tag it from a laptop before any cleanup:

```
git tag -a legacy-baseline-2026-09-30 3e819c8 -m "Pipeline before predictbot/" && git push origin legacy-baseline-2026-09-30
```

## Legacy areas

| Legacy area | Still used by `predictbot/`? | Replaced by | Delete when |
|---|---|---|---|
| `src/pcbf_calculator/` (pricing, Bet9ja ingestion, decision layer, settlement CLIs, soccer Elo adapter) | No | `pcbf.py` (de-vig, edge, tiers), `workflow.py` (loop), settlement packs | Two weeks of daily use with no need to look back at it, and the Elo model is not revived |
| `ledgers/` (forecast and betting ledger package) | No | `schemas.py` + `storage.py` | Same as above. Its real data was lost with the cloud sessions; only code remains. |
| `data_pipeline/`, `research/`, `registries/`, `scripts/`, `examples/` | No | Nothing yet. Model training is out of scope for this phase. | When a decision is made on reviving a model (`football_score_v1` needs the promoted-team fix first) |
| `tests/` (legacy test suite, about 920 tests) | No | `predictbot/tests/` | With the legacy code it tests |
| `docs/` except `REQUIREMENTS.md` | No | `predictbot/README.md` | With the legacy code |
| `captures/2026-09-23/` | No (read as reference) | Data folder `captures/` | After copying into the synced data folder |
| Root `app.py` (first local app iteration) | Its ticket P&L logic was copied into `tickets.py` | `predictbot/app.py` | Once `predictbot/app.py` is in daily use |
| `browser_extension/bet9ja_capture_allsports/` (stage-1 snapshot tool) | No | `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/` | Now safe; kept only for the history it documents |
| `browser_extension/bet9ja_capture/` (soccer, open bets, settled bets) | **Yes**: its output files are read | Not replaced | Keep |
| `browser_extension/bet9ja-allsports-evidence-capture-v0.2.2/` | **Yes**: its output files are read | Not replaced | Keep (Chrome loads it from this path) |
| `AGENTS.md` (cloud daily routine, wheels, approval gates) | No, and its routine conflicts with the new loop | `predictbot/README.md` | Rewrite in the cleanup commit |
| `backtest.py`, `train_leagues.py`, `weekly_picks.py`, `SETUP_GUIDE.md`, `*.csv` at root | No (ProphitBet era, hardcoded `C:\Users\HUAWAI` paths) | Nothing | Any time |
| `.github/workflows/runtime-probe.yml`, `football-data-feasibility.yml` | No | `.github/workflows/predictbot.yml` | With the legacy code |
| `pyproject.toml`, wheel build | No (predictbot needs no install) | Nothing | With the legacy code |

## Data migration

| Source | Status |
|---|---|
| PCBF Mini ledger (`pcbf-ledger.csv`, 2 rows) plus the 4-row pasted copy | Imported by `workflow.import_pcbf_ledger`, merged by `record_id`. Rows 003 and 004 recompute to PICK (3.100 / +4.83%, 4.367 / +3.05%). Rows 001 and 002 are REJECTED because their benchmark markets were never recorded. |
| Bet9ja capture files on both laptops since 23 Sep | Copy into the synced data folder's `captures/`. Duplicates by content are skipped. |
| Cloud-session forecast and betting ledgers | Lost. Nothing to migrate. |

## Exit criteria for the cleanup commit

1. `predictbot/` tests pass in CI.
2. At least one real day has gone capture → pack → reply → selections → settlement with CLV.
3. The data folder is synced on both laptops and contains all surviving captures.
4. The legacy baseline tag is pushed.
5. The cleanup is a separate commit (or PR), listing every removed path against this table.
