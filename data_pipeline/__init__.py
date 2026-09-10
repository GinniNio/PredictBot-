"""Soccer 1X2 data-feasibility pipeline (Football-Data.co.uk).

This package is additive and self-contained: it does not import from, and
is not imported by, `src/pcbf_calculator` or `src/pcbf_football`. It
proves whether Football-Data can support the feature pipeline specified in
`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md` — it does not build, train,
calibrate, or evaluate any forecasting model, and it does not touch any
promotion/admission gate. See `docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`
("Data source honesty", "Next sequence") and
`data_pipeline/FEASIBILITY_DECISION.md` for the full context and
conclusion.
"""
