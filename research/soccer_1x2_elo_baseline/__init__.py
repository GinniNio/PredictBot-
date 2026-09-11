"""Soccer 1X2 Elo + logistic-regression baseline (research, dependency-free).

This package is a standalone research baseline for the Soccer 1X2 dataset
contract (`docs/adapters/data/soccer_1x2_dataset_contract.yaml`). It is
kept structurally separate from both `data_pipeline/` (imported from,
read-only — see `data_pipeline.dataset_builder.build_split`) and
`src/pcbf_calculator/` (imported from, read-only — only
`pcbf_calculator.pricing.engine.analyze_market`, reused as-is for the
de-vigged-opening-odds comparison baseline in `evaluate.py`).

It never touches `src/pcbf_calculator/adapters/` (any file), the
model-admission registry, or `soccer_1x2_promotion_thresholds.yaml` — this
baseline stays completely unregistered and is not wired into the
production adapter dispatch table in any way. See each module's own
docstring for its scope.

Stdlib only. No numpy/scipy/sklearn/pandas anywhere in this package.
"""
