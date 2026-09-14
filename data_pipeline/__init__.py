"""Soccer 1X2 data-feasibility pipeline (Football-Data.co.uk).

This package is additive and does not import from `src/pcbf_calculator` or
`src/pcbf_football`. It originally proved whether Football-Data can
support the feature pipeline specified in
`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md` — it does not build, train,
calibrate, or evaluate any forecasting model, and it does not touch any
promotion/admission gate. See `docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`
("Data source honesty", "Next sequence") and
`data_pipeline/FEASIBILITY_DECISION.md` for the full context and
conclusion.

**One deliberate, reviewed exception to "not imported by":**
`pcbf_calculator.orchestration.football_data_settlement` (forecast-ledger
settlement against Football-Data result/closing-odds files) imports this
package's own `schema_inspection.py` (CSV column/bookmaker detection) and
`validation.py` (`parse_date`, `VALID_RESULT_LABELS`) directly, rather
than re-implementing the same CSV-shape logic a second time under
`pcbf_calculator`. This package still builds, trains, and evaluates
nothing, and still touches no promotion/admission gate — only its
already-reviewed, read-only file-shape parsing is reused. `pyproject.toml`
packages `data_pipeline` into the wheel alongside `pcbf_calculator` and
`ledgers` for exactly this reason.
"""
