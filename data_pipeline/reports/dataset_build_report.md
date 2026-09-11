# Soccer 1X2 dataset build report

Contract version `1.0.0` (status: `DRAFT_FOR_OPERATOR_REVIEW`). Combined dataset snapshot hash: `8a460f1223fb8fa7b089ef7f305fbb028522b7a1428624d6617c75430e3371e4`.

Row-count labels below are EXPLICITLY DISTINCT and never conflated, and the first three always reconcile exactly: `source_rows == usable_settled_rows + pending_settlement_rows_included + rejected_unique_rows`. `source_rows` is every row read; `usable_settled_rows` and `rejected_unique_rows` are THIS SPLIT'S OWN eligibility decision (never the generic, split-unaware validate_file check — see dataset_builder.py's module docstring); `pending_settlement_rows_included` is nonzero only for split_prospective_paper_scoring (not-yet-played fixtures admitted with a null result); `validation_issue_occurrences` is generic FILE-level diagnostic evidence, deliberately outside the three-way reconciliation.

## Per-split summary

| Split | Season codes | Source rows | Usable (settled) | Pending settlement | Rejected (unique) | Issue occurrences | Dataset SHA-256 | Closing-line-benchmark label required |
|---|---|---:|---:|---:|---:|---:|---|---|
| split_model_dev_and_completed_eval | 1920, 2021, 2122, 2223, 2324, 2425 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_closing_line_benchmark | 1920, 2021, 2122, 2223, 2324, 2425 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | CLOSING_LINE_BENCHMARK_MODEL |
| split_prospective_paper_scoring | 2526 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | no |
| split_earlier_research_backtesting | 1213, 1314, 1516, 1617, 1718, 1819 | 0 | 0 | 0 | 0 | 0 | `37517e5f3dc6…` | CLOSING_LINE_BENCHMARK_MODEL |

## Season 1415 diagnostic — UNRESOLVED, pending human review

> This diagnostic reports column-header differences only. It does NOT confirm a root cause for the season-1415 usable-fixture-rate collapse observed across all 5 leagues in the live GitHub Actions run at commit 6093d0a. A human must read this evidence (and, when 'no_drift' is true for every league, conclude the anomaly is at the row/value level, not the schema level) before season 1415 is cleared for default inclusion.

| League | 1314 vs 1415 | 1415 vs 1516 |
|---|---|---|
| E0 | (file not available) | (file not available) |
| D1 | (file not available) | (file not available) |
| SP1 | (file not available) | (file not available) |
| I1 | (file not available) | (file not available) |
| F1 | (file not available) | (file not available) |

## Sanitized rejected-row sample (capped, no odds values)

_No rejected rows sampled (no split had a rejected row, or no raw files were present)._

> This report describes whatever raw files were actually present under data_pipeline/raw at build time — real downloads happen separately via `python -m data_pipeline.download`. A missing (league, season) file is reported per-file as `missing: true`, never fabricated. Dataset row content itself is written only to data_pipeline/dataset/ (gitignored) — never committed; this report's numbers, hashes, the season-1415 diagnostic, and the sanitized rejected-row sample are the committed evidence.
