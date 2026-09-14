"""See `docs/__init__.py`'s own docstring. This package's own data files
(`soccer_1x2_dataset_contract.yaml`, `soccer_1x2_feature_manifest.yaml`,
`soccer_1x2_promotion_thresholds.yaml`) are design/reference artifacts,
never Python code; `data_pipeline.dataset_builder.load_contract_rows`
reads `soccer_1x2_dataset_contract.yaml` from this exact path
(`REPO_ROOT / "docs" / "adapters" / "data" / ...`) at runtime -- the only
one of the three actually loaded by code today.
"""
