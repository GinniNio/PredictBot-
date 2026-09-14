"""Namespace container for this repository's research packages
(currently only `research.soccer_1x2_elo_baseline` — see that package's
own docstring for its scope and boundaries).

This file exists so `research` is a real, importable Python package (not
merely a directory) — required for
`pcbf_calculator.orchestration.soccer_artifact_refresh` (the artifact
refresh lifecycle) to import `research.soccer_1x2_elo_baseline`'s
training/evaluation code from an installed wheel, not just a repo
checkout. `pyproject.toml` packages `research` into the wheel for exactly
this reason, alongside the same treatment already given to `ledgers` and
`data_pipeline`.
"""
