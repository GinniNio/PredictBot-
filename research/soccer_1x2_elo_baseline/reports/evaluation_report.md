# Soccer 1X2 Elo baseline — evaluation report

evidence_class: `SOURCE_UNAVAILABLE`

> NO EVIDENCE: no raw Football-Data file backing any of the four frozen model-development splits was present on disk at build time. Every row count below is zero -- this report proves neither real-data performance nor fixture-based code correctness.

## Frozen model-development inputs

- `split_training`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_calibration_validation`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_locked_test`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- `split_out_of_time_retrospective_holdout`: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- frozen_dataset_hash (combined, 4 splits only): `8b27f4588429ed96b40c35ffd469bd9ea507d60ab658aa775d1fe921ef8050a5`
- code_hash: `411585164618d76c5aa090086e22d5a602ba19c611399b073f1805f65d2223f8`
- artifact_hash: `133bed9556a56fb25029557e96f118d103e0847d00c840ea386bdc1df2c36968`
- combined_hash: `b18ad397f4ffe7c841f2d30ac2a5ad5cbfd21bda92f96569e904607cce2060e2`
- temperature: `1.0`

## Prospective stream (rolling, append-only — never part of the frozen hashes above)

- content_hash: `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`
- run_timestamp_utc: `2026-09-11T07:37:58+00:00`
- row_count: 0

## Test splits (reported separately — never blended)

### split_locked_test

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

**Model — reliability table**

(no rows)

### split_out_of_time_retrospective_holdout

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

**Model — reliability table**

(no rows)

## Rolling settlement observation — split_genuine_prospective_scoring

> ROLLING SETTLEMENT OBSERVATION ONLY — Football-Data's 2627 file is a post-hoc settled-results feed, never a pre-match fixture feed (see dataset contract correction_v2_1_0). These numbers describe already-played 2026-27 matches this pipeline has settlement data for; they are NOT a test and NOT genuine prospective/PAPER evaluation.

**Model** — rows=0
**Naive league-frequency baseline** — rows=0
**De-vigged opening odds** — rows=0
- De-vigged opening odds: excluded 0 row(s) with no complete opening price set

**Model — reliability table**

(no rows)

## Frozen-hash provenance check (against expected_hashes.json)

Every value below is either `CANDIDATE` (no real hash pinned yet — `expected_hashes.json` still says `UNFROZEN_PENDING_LIVE_RUN`), `CONFIRMED` (matches a human-pinned real value), or `MISMATCH` (a pinned value no longer matches — never auto-corrected, fails this run).

| Split | Status | Expected | Computed |
|---|---|---|---|
| split_training | MISMATCH | `f6aaf11b324b33d35b437d64cb2238cc8cf44d6cad3696f6abd17349f539d59e` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_calibration_validation | MISMATCH | `ef794716945f9eb37bb034a17e340140be4e3aa1d913aed1e8bd993369c395c7` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_locked_test | MISMATCH | `6ca9ee18f3d538ad1eecb4ebf935a3448b08afd78e82604f66f3eb00b36961aa` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| split_out_of_time_retrospective_holdout | MISMATCH | `499d5a6464bd62ab8edd6c428c43b1aa1600c214060f9553660b5364dbd612b7` | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| frozen_dataset_hash | MISMATCH | `d251b3aeead34b3b1598dc3a63eb8cbb27fb9ffd443013e8b420002ef8e66b73` | `8b27f4588429ed96b40c35ffd469bd9ea507d60ab658aa775d1fe921ef8050a5` |

