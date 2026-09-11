# Soccer 1X2 Elo baseline — evaluation report

> IMPLEMENTATION-ONLY: built and tested exclusively against synthetic fixtures in tests/fixtures/football_data/. Every metric below proves the code behaves correctly; NONE of it is a real-data performance claim. Real numbers are PENDING THE POST-MERGE LIVE GITHUB ACTIONS WORKFLOW RUN.

## Frozen model-development inputs

- `split_training`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_calibration_validation`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_locked_test`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_out_of_time_retrospective_holdout`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- frozen_dataset_hash (combined, 4 splits only): `8b27f4588429ed96b40c35ffd469bd9ea507d60ab658aa775d1fe921ef8050a5`
- code_hash: `3264a0b18c0ea59e8ec52bd11bf65399418ae9bbcac46e419093f57f8066de8d`
- artifact_hash: `133bed9556a56fb25029557e96f118d103e0847d00c840ea386bdc1df2c36968`
- combined_hash: `58d4b43235099435ba22d0994b26e0877078b9409446eb9155985e8b3a8b42ca`
- temperature: `1.0`

## Prospective stream (rolling, append-only — never part of the frozen hashes above)

- content_hash: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- run_timestamp_utc: `2026-09-11T07:07:23+00:00`
- row_count: 0

## Test splits (reported separately — never blended)

### split_locked_test

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

### split_out_of_time_retrospective_holdout

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

## Rolling settlement observation — split_genuine_prospective_scoring

> ROLLING SETTLEMENT OBSERVATION ONLY — Football-Data's 2627 file is a post-hoc settled-results feed, never a pre-match fixture feed (see dataset contract correction_v2_1_0). These numbers describe already-played 2026-27 matches this pipeline has settlement data for; they are NOT a test and NOT genuine prospective/PAPER evaluation.

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

## Frozen-hash provenance check (against expected_hashes.json)

Every value below is either `CANDIDATE` (no real hash pinned yet — `expected_hashes.json` still says `UNFROZEN_PENDING_LIVE_RUN`), `CONFIRMED` (matches a human-pinned real value), or `MISMATCH` (a pinned value no longer matches — never auto-corrected, fails this run).

| Split | Status | Expected | Computed |
|---|---|---|---|
| split_training | CANDIDATE | `UNFROZEN_PENDING_LIVE_RUN` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_calibration_validation | CANDIDATE | `UNFROZEN_PENDING_LIVE_RUN` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_locked_test | CANDIDATE | `UNFROZEN_PENDING_LIVE_RUN` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_out_of_time_retrospective_holdout | CANDIDATE | `UNFROZEN_PENDING_LIVE_RUN` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| frozen_dataset_hash | CANDIDATE | `UNFROZEN_PENDING_LIVE_RUN` | `8b27f4588429ed96b40c35ffd469bd9ea507d60ab658aa775d1fe921ef8050a5` |

