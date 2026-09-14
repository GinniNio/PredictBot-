"""Not a real Python package in spirit -- `docs/` is this repository's
documentation directory, almost entirely plain Markdown with no code.

This file (and its two children,
`docs/adapters/__init__.py`/`docs/adapters/data/__init__.py`) exists ONLY
so `docs.adapters.data` can be packaged into the wheel as an installable
package, purely to ship `soccer_1x2_dataset_contract.yaml` -- a real
runtime dependency of `data_pipeline.dataset_builder.load_contract_rows`
(and therefore of `research.soccer_1x2_elo_baseline`, imported by
`pcbf_calculator.orchestration.soccer_artifact_refresh`), never
Python code. `pyproject.toml`'s own `include` pattern is scoped to
`docs.adapters.data*` specifically, exactly as narrow as this docstring
implies -- every other subdirectory of `docs/` has no `__init__.py` and
is never swept into the wheel.
"""
