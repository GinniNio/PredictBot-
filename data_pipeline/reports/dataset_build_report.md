# Soccer 1X2 dataset build report

Contract version `2.1.0` (status: `DRAFT_FOR_OPERATOR_REVIEW`). Combined dataset snapshot hash: `2c950a1a26d69ae20aea4a6b53cdba2edd7e449b1fff3110e1545e554ec57bb7`.

Row-count labels below are EXPLICITLY DISTINCT and never conflated, and the first three always reconcile exactly: `source_rows == usable_settled_rows + pending_settlement_rows_included + rejected_unique_rows`. `source_rows` is every row read; `usable_settled_rows` and `rejected_unique_rows` are THIS SPLIT'S OWN eligibility decision (never the generic, split-unaware validate_file check — see dataset_builder.py's module docstring); `pending_settlement_rows_included` is nonzero only for split_genuine_prospective_scoring (not-yet-played fixtures admitted with a null result); `validation_issue_occurrences` is generic FILE-level diagnostic evidence, deliberately outside the three-way reconciliation.

## Per-split summary

| Split | Status | Season codes | Source rows | Usable (settled) | Pending settlement | Rejected (unique) | Issue occurrences | Dataset SHA-256 | Closing-line-benchmark label required |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| split_training | SOURCE_UNAVAILABLE | 1920, 2021, 2122, 2223 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_calibration_validation | SOURCE_UNAVAILABLE | 2324 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_locked_test | SOURCE_UNAVAILABLE | 2425 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_out_of_time_retrospective_holdout | SOURCE_UNAVAILABLE | 2526 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_genuine_prospective_scoring | SOURCE_UNAVAILABLE | 2627 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_closing_line_benchmark | SOURCE_UNAVAILABLE | 1920, 2021, 2122, 2223, 2324, 2425 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | CLOSING_LINE_BENCHMARK_MODEL |
| split_earlier_research_backtesting | SOURCE_UNAVAILABLE | 1213, 1314, 1516, 1617, 1718, 1819 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | CLOSING_LINE_BENCHMARK_MODEL |

## Season 1415 diagnostic — UNRESOLVED, pending human review

> This diagnostic reports column-header differences only. It does NOT confirm a root cause for the season-1415 usable-fixture-rate collapse observed across all 5 leagues in the live GitHub Actions run at commit 6093d0a. A human must read this evidence (and, when 'no_drift' is true for every league, conclude the anomaly is at the row/value level, not the schema level) before season 1415 is cleared for default inclusion.

| League | 1314 vs 1415 | 1415 vs 1516 |
|---|---|---|
| E0 | (file not available) | (file not available) |
| D1 | (file not available) | (file not available) |
| SP1 | (file not available) | (file not available) |
| I1 | (file not available) | (file not available) |
| F1 | (file not available) | (file not available) |

## Season 1415 rejection-reason breakdown — UNRESOLVED, pending human review

> This diagnostic reports each league's own season-1415 file's typed rejection-reason breakdown (independent of any split's own requirements). It does NOT change season 1415's excluded-by-default status — that remains pending human review (see season_1415_diagnostic_pending_review in the contract) — it only adds evidence for that review, exactly like the column-drift diagnostic above.

### E0

_1415 file not available._

### D1

_1415 file not available._

### SP1

_1415 file not available._

### I1

_1415 file not available._

### F1

_1415 file not available._

## Sanitized rejected-row sample (capped, no odds values)

_No rejected rows sampled (no split had a rejected row, or no raw files were present)._

## Split-specific rejected-row sample (capped, no odds values)

Unlike the generic sample above (identical across every split, sourced from `validate_file`'s own file-level issues), each row here carries the exact `split_rejection_reason` THIS split's own requirements rejected it for — e.g. distinguishing a row rejected for `OPENING_PRICES_INCOMPLETE` (a training/calibration/locked-test-shaped split's own requirement) from one rejected for `CLOSING_PRICES_INCOMPLETE`, never conflated.

_No split-specific rejected rows sampled (no split rejected a row, or no raw files were present)._

> This report describes whatever raw files were actually present under data_pipeline/raw at build time — real downloads happen separately via `python -m data_pipeline.download`. A missing (league, season) file is reported per-file as `missing: true`, never fabricated. Dataset row content itself is written only to data_pipeline/dataset/ (gitignored) — never committed; this report's numbers, hashes, the season-1415 diagnostic, and the sanitized rejected-row sample are the committed evidence.
