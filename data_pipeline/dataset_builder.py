"""Dataset builder for the Soccer 1X2 Football-Data pipeline.

Reads the versioned dataset contract
(`docs/adapters/data/soccer_1x2_dataset_contract.yaml`) and already-
downloaded raw Football-Data files (`data_pipeline/raw/<league_code>/
<league_code>_<season_code>.csv`, the exact layout `data_pipeline/
download.py::download_one` writes), and produces:

- One normalized dataset FILE per defined split (gitignored, under
  `data_pipeline/dataset/` — see `.gitignore`; never committed, since it
  reshapes the same third-party Football-Data row content the raw CSVs
  themselves are excluded for).
- One small, committed evidence report (`data_pipeline/reports/
  dataset_build_report.json`/`.md`) with per-split row-count labels, per-
  split/combined SHA-256 hashes, the season-1415 column-drift diagnostic,
  and a capped, sanitized sample of rejected rows.

This module is a BATCH DATASET-PRODUCTION / REPORTING TOOL ONLY. It never
trains a model, never touches `src/pcbf_calculator/`, `src/pcbf_football/`,
the model-admission registry, `trusted_provenance.py`, or
`soccer_1x2_promotion_thresholds.yaml`. It cannot download real files in a
sandboxed test/dev environment either — it only ever reads whatever is
already present under a raw directory (real downloads happen separately,
via `python -m data_pipeline.download`, e.g. in the GitHub Actions
workflow); this module is exercised in this repository's own test suite
exclusively against `tests/fixtures/football_data/` fixtures copied into a
temporary raw-directory layout, never real Football-Data content.

Four labels per split, EXPLICITLY DISTINCT (this task's core reporting
requirement — never conflate them), with the first three always
reconciling exactly: `source_rows == usable_settled_rows +
pending_settlement_rows_included + rejected_unique_rows`:

- `source_rows`               — total rows read from a file (before any
                                  eligibility judgment).
- `usable_settled_rows`        — rows THIS SPLIT'S OWN requirements
                                  (`resolve_split_row_requirements`,
                                  `_row_eligible_for_split`) admit with a
                                  real, non-null result. Split-specific,
                                  never `validate_file`'s generic,
                                  split-unaware `usable_fixtures` — a row
                                  missing a field this split never
                                  required (e.g. an incomplete closing
                                  line for a split that only needs
                                  opening prices) is usable HERE even
                                  though Football-Data's generic per-row
                                  check would flag it.
- `pending_settlement_rows_included` — rows admitted with `result: None`
                                  because this split's contract declares
                                  `result_nullable_until_settlement`
                                  (`split_genuine_prospective_scoring`
                                  only; always `0` for every other
                                  split — see
                                  `ProspectiveNullableResultTests` and
                                  `SplitSpecificEligibilityTests`).
- `rejected_unique_rows`       — `source_rows - (usable_settled_rows +
                                  pending_settlement_rows_included)`:
                                  rows THIS split's own requirements
                                  reject (an always-fatal integrity issue,
                                  or a genuinely missing required field
                                  for THIS split specifically) — ONE count
                                  per row, split-specific, never the
                                  generic file-level rejection count.
- `validation_issue_occurrences` — the sum of every typed rejection
                                  reason's occurrence count from the
                                  file's GENERIC `validate_file` pass
                                  (`FileValidationResult.rejection_reason_counts`)
                                  — kept as background diagnostic
                                  evidence, deliberately NOT part of the
                                  three-way reconciliation above (it can
                                  exceed even `source_rows` when a row
                                  trips several reasons or a file has
                                  multiple odds-column sets each
                                  independently flagged, and it is not
                                  split-aware).

Season-exclusion fail-closed gate: every season strictly before season
`1213` (2012-13, no closing odds at all — see the contract), and season
`1415` (2014-15, a severe, still-unexplained cross-league quality outlier
— see the contract's `season_1415_diagnostic_pending_review` row), are
excluded from EVERY split BY DEFAULT. Including either requires a caller
to explicitly pass `include_excluded_seasons=True` to `build_split` (never
a silently-flippable module-level global, and never a CLI flag that
defaults to "on") — same fail-closed spirit as this repo's existing
`TRUSTED_EXECUTION_PROVENANCE_AVAILABLE`/`PROPOSED_OPERATOR_DECISION`
gates elsewhere (a different concern; this module never reuses those
exact names since this is not a provenance or a promotion-threshold
question, it is a data-availability one).

Reference-bookmaker choice (documented per this task's item 6): OPENING
price observations are built from EVERY bookmaker's opening-or-only column
set found in a file (no single bookmaker's opening line is present across
the pipeline's entire attempted range for every league, so restricting to
one would silently drop coverage) — each bookmaker's opening price is its
own, separately bookmaker-tagged price observation, never merged or
averaged. CLOSING price observations are restricted to PINNACLE's closing
columns only (`is_pinnacle=True`, Football-Data's `PS*`/`PSC*` prefix),
matching the dataset contract's own wording for the closing-line-benchmark
and earlier-research splits (`closing_1x2_prices_complete_pinnacle`) — a
non-Pinnacle bookmaker's closing columns are rare in Football-Data's own
files and are not what this contract's closing-benchmark rows are defined
against, so they are left out of built CLOSING observations (they would
still show up in `schema_inspection.inspect_file`'s raw findings for
anyone inspecting a file directly — this module just does not build a
dataset record from them).

Stdlib only (csv already handled by `schema_inspection`/`validation`,
json, hashlib). No new runtime dependency.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.manifest_yaml import load_yaml_categories_file
from data_pipeline.schema_inspection import column_drift, inspect_header, read_csv_rows_with_encoding
from data_pipeline.validation import FileValidationResult, RejectionReason, _is_numeric_positive, validate_file

CONTRACT_PATH = REPO_ROOT / "docs" / "adapters" / "data" / "soccer_1x2_dataset_contract.yaml"
DEFAULT_RAW_DIR = REPO_ROOT / "data_pipeline" / "raw"
DEFAULT_DATASET_DIR = REPO_ROOT / "data_pipeline" / "dataset"
DEFAULT_REPORT_JSON_PATH = REPO_ROOT / "data_pipeline" / "reports" / "dataset_build_report.json"
DEFAULT_REPORT_MARKDOWN_PATH = REPO_ROOT / "data_pipeline" / "reports" / "dataset_build_report.md"

LEAGUE_CODES = ("E0", "D1", "SP1", "I1", "F1")

# The two season-code pairs the 1415 diagnostic compares, per league (this
# task's item 6/7 — diagnostic evidence only, never an auto-fix or
# reclassification of 1415).
SEASON_1415_DIAGNOSTIC_PAIRS = (("1314", "1415"), ("1415", "1516"))

PRICE_STAGE_OPENING = "OPENING"
PRICE_STAGE_CLOSING = "CLOSING"
PRICE_TIMESTAMP_QUALITY_STAGE_ONLY = "STAGE_ONLY"

# Contract row id -> required label for a model trained on that split's
# closing prices (see soccer_1x2_dataset_contract.yaml's
# admission_rule_closing_line_benchmark_model_label row). Populated from
# the contract itself in `load_contract_splits`, never hand-duplicated.
_CLOSING_LINE_BENCHMARK_MODEL_LABEL = "CLOSING_LINE_BENCHMARK_MODEL"


def _season_start_year(season_code: str) -> int:
    """Same 2-digit-year convention as `data_pipeline.download.season_codes`
    (that function's own comparison logic is a private nested closure, not
    importable, so this is a small, deliberate, documented duplication of
    the identical rule): a 2-digit year >= 50 is 1900s, else 2000s."""

    two_digit = int(season_code[:2])
    return 1900 + two_digit if two_digit >= 50 else 2000 + two_digit


def _split_csv_field(value: str | None) -> list[str]:
    """Split one of the dataset contract's comma-separated string fields
    (see its own docstring on why these are comma-strings, never YAML flow
    sequences) into a list, dropping empty entries (a split with no
    admitted seasons, e.g. `split_excluded_by_default`, stores `""`)."""

    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


# Rejection reasons that exclude a row from EVERY split, unconditionally —
# genuine data-integrity problems no split's required-fields can ever
# waive: a duplicate/conflicting fixture, an unidentified team, an
# unparseable date, an invalid result label, or an impossible scoreline
# are never "acceptable" just because a split doesn't happen to need the
# affected field. Deliberately excludes MISSING_RESULT,
# MISSING_OR_INVALID_ODDS, and INCOMPLETE_THREE_WAY_PRICE — those three
# ARE split-conditional (see `resolve_split_row_requirements` and
# `_row_eligible_for_split` below), since whether a blank result or an
# incomplete/missing odds column set is acceptable depends entirely on
# what that split's own contract row actually requires.
_ALWAYS_FATAL_REASONS = frozenset(
    {
        RejectionReason.DUPLICATE_FIXTURE,
        RejectionReason.MISSING_TEAM_IDENTITY,
        RejectionReason.INVALID_RESULT_LABEL,
        RejectionReason.IMPOSSIBLE_SCORE,
        RejectionReason.CONFLICTING_FIXTURE,
        RejectionReason.UNPARSEABLE_DATE,
    }
)


def resolve_split_row_requirements(split_row: dict[str, Any]) -> dict[str, bool]:
    """Derive per-row inclusion requirements from a split's OWN
    `required_fields` contract string — never a hardcoded per-split_id
    branch, so a future contract edit (e.g. a new split, or a split's
    required_fields changing) is picked up automatically without a
    matching code change. This directly answers the design the operator
    asked for: split-specific validation requirements instead of
    progressively bolting exceptions onto the one generic historical
    validator.

    Recognized `required_fields` tokens (comma-separated, see the
    contract's own grammar note):
    - `result`                          -> `require_result`
    - `result_nullable_until_settlement` -> `allow_null_result` (mutually
      exclusive in practice with `result` — no current split lists both)
    - `kickoff_time`                    -> `require_kickoff_time`
    - `opening_1x2_prices_complete`,
      `opening_and_closing_1x2_prices`  -> `require_opening_complete`
    - `closing_1x2_prices_complete_pinnacle`,
      `opening_and_closing_1x2_prices`  -> `require_closing_complete`
    Any other token (`date`, `home_team`, `away_team`) is always required
    by every split's own row-integrity check (`_ALWAYS_FATAL_REASONS`
    already excludes a row missing team identity or an unparseable date
    unconditionally) and needs no split-specific flag here."""

    tokens = set(_split_csv_field(split_row.get("required_fields")))
    return {
        "require_result": "result" in tokens,
        "allow_null_result": "result_nullable_until_settlement" in tokens,
        "require_kickoff_time": "kickoff_time" in tokens,
        "require_opening_complete": "opening_1x2_prices_complete" in tokens or "opening_and_closing_1x2_prices" in tokens,
        "require_closing_complete": "closing_1x2_prices_complete_pinnacle" in tokens or "opening_and_closing_1x2_prices" in tokens,
    }


def _finding_fully_priced(row: dict[str, str | None], finding) -> bool:
    return all(
        _is_numeric_positive(row.get(col))
        for col in (finding.home_col, finding.draw_col, finding.away_col)
    )


def _opening_prices_complete(row: dict[str, str | None], odds_findings) -> bool:
    """True if AT LEAST ONE opening-or-only bookmaker finding has all
    three outcomes numerically priced for this row. Not every opening
    bookmaker needs to be complete — a fixture missing one bookmaker's
    opening line while another's is fully priced still has usable opening
    prices; see the module docstring's reference-bookmaker-choice note
    for why OPENING observations are built from every bookmaker found."""

    return any(f.variant != "closing" and _finding_fully_priced(row, f) for f in odds_findings)


def _closing_prices_complete(row: dict[str, str | None], odds_findings) -> bool:
    """True if the Pinnacle closing finding (the only bookmaker CLOSING
    observations are ever built from — see the module docstring) has all
    three outcomes numerically priced for this row. A row with an
    incomplete or altogether missing Pinnacle closing line is
    `closing_prices_complete=False` even if every opening bookmaker is
    fully priced — the two completeness checks are independent, per
    split's own, independent requirement flags."""

    return any(f.variant == "closing" and f.is_pinnacle and _finding_fully_priced(row, f) for f in odds_findings)


def _row_eligible_for_split(
    row: dict[str, str | None],
    row_issues: list,
    ftr_col: str | None,
    time_col: str | None,
    odds_findings,
    requirements: dict[str, bool],
) -> tuple[bool, bool, str | None]:
    """Decide whether one row belongs in a split's dataset, per that
    split's OWN requirements (`resolve_split_row_requirements`) — never
    the one-size-fits-all `FileValidationResult.row_results[i].usable`
    flag, which conflates every split's needs into a single boolean (the
    real defect this function replaces: a genuinely upcoming prospective
    fixture with complete opening prices but no closing line yet, or a
    training-split row with complete opening prices but an incomplete
    closing line it doesn't even need, must not be excluded just because
    Football-Data's generic per-row check flags an irrelevant column).

    Returns `(eligible, result_pending_settlement, split_rejection_reason)`.
    `split_rejection_reason` is `None` when `eligible` is True; otherwise a
    short typed string identifying WHY, one of:
    `f"ALWAYS_FATAL:{reason}"` (the underlying `RejectionReason` value that
    tripped, e.g. `"ALWAYS_FATAL:MISSING_TEAM_IDENTITY"`), `"MISSING_RESULT"`,
    `"MISSING_KICKOFF_TIME"`, `"OPENING_PRICES_INCOMPLETE"`, or
    `"CLOSING_PRICES_INCOMPLETE"` — this answers precisely, per split, WHY a
    row was rejected (see `sample_split_specific_rejected_rows`), never just
    THAT it was rejected.

    A row failing any `_ALWAYS_FATAL_REASONS` check is never eligible for
    ANY split, regardless of requirements — those are integrity problems,
    not field-availability ones."""

    always_fatal_issue = next((issue for issue in row_issues if issue.reason in _ALWAYS_FATAL_REASONS), None)
    if always_fatal_issue is not None:
        return False, False, f"ALWAYS_FATAL:{always_fatal_issue.reason}"

    result_raw = (row.get(ftr_col) or "").strip() if ftr_col else ""
    result_blank = not result_raw
    # A blank result is fatal only when this split actually requires a
    # result AND has not separately declared results nullable-until-
    # settlement. A split requiring neither (e.g.
    # split_closing_line_benchmark's current contract row lists no result
    # requirement at all) treats a blank result as simply not its
    # concern — never fatal, never flagged pending-settlement (that flag
    # is reserved for the split that explicitly declared
    # result_nullable_until_settlement).
    if result_blank and requirements["require_result"] and not requirements["allow_null_result"]:
        return False, False, "MISSING_RESULT"

    if requirements["require_kickoff_time"]:
        time_raw = (row.get(time_col) or "").strip() if time_col else ""
        if not time_raw:
            return False, False, "MISSING_KICKOFF_TIME"

    if requirements["require_opening_complete"] and not _opening_prices_complete(row, odds_findings):
        return False, False, "OPENING_PRICES_INCOMPLETE"

    if requirements["require_closing_complete"] and not _closing_prices_complete(row, odds_findings):
        return False, False, "CLOSING_PRICES_INCOMPLETE"

    result_pending_settlement = requirements["allow_null_result"] and result_blank
    return True, result_pending_settlement, None


def load_contract_rows(contract_path: Path = CONTRACT_PATH) -> dict[str, dict[str, Any]]:
    """Load `soccer_1x2_dataset_contract.yaml` via the same dependency-free
    restricted-YAML reader as every sibling manifest, keyed by each row's
    `id` — the single source of truth this module reads split boundaries
    and price-field contract text from, so a contract edit never silently
    drifts from what this module actually builds."""

    rows = load_yaml_categories_file(str(contract_path))
    return {row["id"]: row for row in rows}


def excluded_before_season_code(contract_rows: dict[str, dict[str, Any]] | None = None) -> str:
    contract_rows = contract_rows or load_contract_rows()
    return str(contract_rows["split_excluded_by_default"]["excluded_before_season"])


def excluded_specific_seasons(contract_rows: dict[str, dict[str, Any]] | None = None) -> set[str]:
    contract_rows = contract_rows or load_contract_rows()
    return set(_split_csv_field(contract_rows["split_excluded_by_default"].get("excluded_specific_seasons")))


def is_excluded_by_default(season_code: str, contract_rows: dict[str, dict[str, Any]] | None = None) -> bool:
    """True if `season_code` is excluded from every split by default —
    strictly before the contract's `excluded_before_season` (no closing
    odds at all, currently `1213`), or one of its
    `excluded_specific_seasons` (currently just `1415`, the still-
    unexplained cross-league quality outlier). See the contract's
    `split_excluded_by_default` row for the evidence behind both."""

    contract_rows = contract_rows or load_contract_rows()
    if _season_start_year(season_code) < _season_start_year(excluded_before_season_code(contract_rows)):
        return True
    return season_code in excluded_specific_seasons(contract_rows)


# The season Football-Data's closing (Pinnacle) odds become continuously
# available from — evidence: "Closing odds (Pinnacle, PS columns) are
# absent before season 1213 (2012-13), present continuously from 1213
# through 2526" (live run at commit 6093d0a). This is a general
# DATA-AVAILABILITY boundary, distinct from `split_closing_line_benchmark`'s
# own, deliberately NARROWER season range (1920-2425 only, matched to the
# UNION of `split_training` + `split_calibration_validation` +
# `split_locked_test`'s own ranges so every closing-line row has a
# same-range counterpart to compare against — see that split's own
# `exclusion_note` in the contract). Both
# `split_closing_line_benchmark` (1920-2425) and
# `split_earlier_research_backtesting` (1213-1819, minus 1415) sit inside
# this wider availability boundary; season 1112 (pre-1213) sits outside it
# for both.
CLOSING_ODDS_AVAILABLE_FROM_SEASON = "1213"


def is_within_closing_odds_available_range(season_code: str) -> bool:
    """True from `CLOSING_ODDS_AVAILABLE_FROM_SEASON` (1213) onward — the
    general closing-odds-availability boundary described above, not any
    one split's own (possibly narrower) season list."""

    return _season_start_year(season_code) >= _season_start_year(CLOSING_ODDS_AVAILABLE_FROM_SEASON)


DATASET_SPLIT_IDS = (
    "split_training",
    "split_calibration_validation",
    "split_locked_test",
    "split_out_of_time_retrospective_holdout",
    "split_genuine_prospective_scoring",
    "split_closing_line_benchmark",
    "split_earlier_research_backtesting",
)


def resolve_split_season_codes(
    split_id: str,
    contract_rows: dict[str, dict[str, Any]] | None = None,
    include_excluded_seasons: bool = False,
) -> list[str]:
    """Resolve the season codes a given split (by its contract row id)
    actually admits. By default this is exactly the contract's own
    `season_codes` field for that split — which already has `1415`
    removed from `split_earlier_research_backtesting`'s nominal
    [1213, 1819] range (see the contract's own `exclusion_note`).

    `include_excluded_seasons=True` is the one explicit, caller-supplied
    opt-in this module provides to include an excluded season — FAIL
    CLOSED BY DESIGN, matching this repo's established
    fail-closed-unless-explicitly-opted-in convention (see this module's
    docstring). It only ever affects `split_earlier_research_backtesting`
    (the one split whose nominal range actually contains the excluded
    `1415`) — every other split's contract-defined season list already
    contains no excluded season to add back, since none of their ranges
    dip below `1213` or include `1415`."""

    contract_rows = contract_rows or load_contract_rows()
    row = contract_rows[split_id]
    codes = _split_csv_field(row.get("season_codes"))
    if include_excluded_seasons and split_id == "split_earlier_research_backtesting":
        excluded = [c for c in excluded_specific_seasons(contract_rows) if _season_start_year(c) >= _season_start_year(codes[0])]
        codes = sorted(set(codes) | set(excluded), key=_season_start_year)
    return codes


def raw_file_path(raw_dir: Path, league_code: str, season_code: str) -> Path:
    """Same layout `data_pipeline.download.download_one` writes to."""
    return raw_dir / league_code / f"{league_code}_{season_code}.csv"


def _price_observations_for_row(
    row: dict[str, str | None],
    odds_findings,
    source_retrieved_at: str | None,
) -> list[dict[str, Any]]:
    """Build this row's price observations. Each observation is tagged
    with EXACTLY ONE `price_stage` (`OPENING` or `CLOSING`) and carries
    ONLY that stage's three odds columns — an opening observation never
    carries a closing column's value and vice versa (this task's item 8's
    core leakage-shape assertion; guaranteed structurally here since each
    observation is built from one `OddsColumnFinding` of one variant at a
    time, never merged with another variant's columns).

    Every observation carries all four price/timestamp-contract fields
    from `soccer_1x2_dataset_contract.yaml`, unconditionally:
    `price_captured_at` is always `None`, `price_timestamp_quality` is
    always `"STAGE_ONLY"`, `price_stage` is exactly one of the two enum
    values, and `source_retrieved_at` is this pipeline's own download
    provenance (never a stand-in for the actual odds observation time)."""

    observations: list[dict[str, Any]] = []
    for finding in odds_findings:
        if finding.variant == "closing" and not finding.is_pinnacle:
            # See module docstring: CLOSING observations are restricted to
            # Pinnacle, matching the contract's own
            # "closing_1x2_prices_complete_pinnacle" wording.
            continue
        stage = PRICE_STAGE_CLOSING if finding.variant == "closing" else PRICE_STAGE_OPENING
        home_raw = row.get(finding.home_col)
        draw_raw = row.get(finding.draw_col)
        away_raw = row.get(finding.away_col)

        def _to_float(raw: str | None) -> float | None:
            try:
                value = float((raw or "").strip())
            except ValueError:
                return None
            return value if value > 0 else None

        observations.append(
            {
                "bookmaker_prefix": finding.bookmaker_prefix,
                "bookmaker_name": finding.bookmaker_name,
                "is_pinnacle": finding.is_pinnacle,
                "price_stage": stage,
                "home_odds": _to_float(home_raw),
                "draw_odds": _to_float(draw_raw),
                "away_odds": _to_float(away_raw),
                # Price/timestamp contract fields — identical on EVERY
                # observation this module ever builds, per
                # soccer_1x2_dataset_contract.yaml.
                "price_captured_at": None,
                "price_timestamp_quality": PRICE_TIMESTAMP_QUALITY_STAGE_ONLY,
                "source_retrieved_at": source_retrieved_at,
            }
        )
    return observations


def build_file_records(
    csv_path: Path,
    league_code: str,
    season_code: str,
    validation_result: FileValidationResult,
    include_kickoff_time: bool,
    source_retrieved_at: str | None = None,
    requirements: dict[str, bool] | None = None,
    split_specific_sample_cap: int = 20,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Build normalized per-match records for one file, using SPLIT-
    SPECIFIC row requirements (`requirements`, from
    `resolve_split_row_requirements`) rather than
    `FileValidationResult.row_results[i].usable`'s one-size-fits-all
    flag — see `_row_eligible_for_split`'s own docstring for the real
    defect this replaces: a genuinely upcoming prospective fixture with
    complete opening prices but no closing line yet (or a training-split
    row with complete opening prices but an incomplete closing line it
    doesn't even need) must not be excluded just because Football-Data's
    generic per-row check flags a column this split never required.
    `requirements=None` defaults to requiring nothing beyond the always-
    fatal integrity checks (every field optional) — every real call site
    passes an explicit `requirements` dict from the split's own contract
    row.

    `include_kickoff_time` reflects the split's own contract row (kickoff
    time is only meaningful from season 1920 onward — see
    `price_field_match_kickoff_at`); when False, `match_kickoff_at` is
    always `None`, even if the file happens to carry a `Time` column
    (never inferred from the file — from the split's contract instead, so
    a stray `Time` column in an older-season file can never leak a
    kickoff time this split's contract says is unavailable).

    Returns `(records, pending_settlement_count, split_specific_rejected_sample)`.
    `pending_settlement_count` is
    `sum(r["result_pending_settlement"] for r in records)`, returned
    directly rather than requiring the caller to re-derive it, so
    `source_rows == len(records) + <rows this function excluded>` and
    `pending_settlement_count` stay trivially reconcilable from one call.
    `split_specific_rejected_sample` is a small, SANITIZED sample (capped
    at `split_specific_sample_cap`, default 20) of rows THIS split's own
    requirements excluded — `row_index`, `home_team`, `away_team`,
    `date_raw`, and `split_rejection_reason` only, no odds values — this
    answers precisely WHY a row was excluded from THIS split specifically
    (e.g. `OPENING_PRICES_INCOMPLETE` vs `CLOSING_PRICES_INCOMPLETE`),
    which the existing generic, file-level `rejected_row_sample`
    (`sample_rejected_rows`, from `validate_file`'s own generic issues,
    identical across every split) cannot distinguish. This is IN ADDITION
    TO that generic sample — it is never removed or repurposed."""

    requirements = requirements or {
        "require_result": False,
        "allow_null_result": False,
        "require_kickoff_time": False,
        "require_opening_complete": False,
        "require_closing_complete": False,
    }

    all_rows, _encoding = read_csv_rows_with_encoding(csv_path)
    header = all_rows[0] if all_rows else []
    from itertools import zip_longest

    rows = [
        {key: value for key, value in zip_longest(header, raw_row, fillvalue=None) if key is not None}
        for raw_row in all_rows[1:]
    ]
    shape = inspect_header(header)
    core = shape["core_columns_present"]
    odds_findings = shape["odds_columns_found"]

    home_col, away_col, date_col, ftr_col = (
        core.get("home_team"),
        core.get("away_team"),
        core.get("fixture_date"),
        core.get("full_time_result"),
    )
    time_col = core.get("kickoff_time")

    issues_by_index = {r.row_index: r.issues for r in validation_result.row_results}

    records: list[dict[str, Any]] = []
    pending_settlement_count = 0
    split_specific_sample: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        row_issues = issues_by_index.get(idx, [])
        eligible, result_pending, split_rejection_reason = _row_eligible_for_split(
            row, row_issues, ftr_col, time_col, odds_findings, requirements
        )
        if not eligible:
            if len(split_specific_sample) < split_specific_sample_cap:
                split_specific_sample.append(
                    {
                        "league_code": league_code,
                        "season_code": season_code,
                        "row_index": idx,
                        "home_team": (row.get(home_col) or "").strip() if home_col else None,
                        "away_team": (row.get(away_col) or "").strip() if away_col else None,
                        "date_raw": (row.get(date_col) or "").strip() if date_col else None,
                        "split_rejection_reason": split_rejection_reason,
                    }
                )
            continue
        result_raw = (row.get(ftr_col) or "").strip() if ftr_col else ""
        if result_pending:
            pending_settlement_count += 1
        records.append(
            {
                "league_code": league_code,
                "season_code": season_code,
                "date_raw": (row.get(date_col) or "").strip() if date_col else None,
                "match_kickoff_at": ((row.get(time_col) or "").strip() or None) if (include_kickoff_time and time_col) else None,
                "home_team": (row.get(home_col) or "").strip() if home_col else None,
                "away_team": (row.get(away_col) or "").strip() if away_col else None,
                "result": result_raw.upper() or None,
                "result_pending_settlement": result_pending,
                "price_observations": _price_observations_for_row(row, odds_findings, source_retrieved_at),
            }
        )
    return records, pending_settlement_count, split_specific_sample


def sample_rejected_rows(
    validation_result: FileValidationResult,
    league_code: str,
    season_code: str,
    per_reason_cap: int = 5,
    total_cap: int = 50,
) -> list[dict[str, Any]]:
    """A small, SANITIZED sample of rejected rows — `row_index`,
    `home_team`, `away_team`, `date_raw`, `issues` (typed reason list)
    only, NEVER an odds/price value — capped at `per_reason_cap` (default
    5) example rows per rejection reason and `total_cap` (default 50)
    rows overall. A row tripping several reasons at once can count toward
    more than one reason's per-reason cap simultaneously (it is one
    sanitized row either way, listing every reason it actually tripped) —
    this sampler never claims to give an unbiased count per reason, only
    a bounded set of illustrative examples; the actual occurrence counts
    live in `validation_result.rejection_reason_counts`."""

    from data_pipeline.validation import RejectionReason

    reason_seen_counts = {reason: 0 for reason in RejectionReason.ALL}
    sample: list[dict[str, Any]] = []
    for row in validation_result.row_results:
        if row.usable:
            continue
        if len(sample) >= total_cap:
            break
        reasons_here = [issue.reason for issue in row.issues]
        if any(reason_seen_counts[r] < per_reason_cap for r in reasons_here):
            sample.append(
                {
                    "league_code": league_code,
                    "season_code": season_code,
                    "row_index": row.row_index,
                    "home_team": row.home_team,
                    "away_team": row.away_team,
                    "date_raw": row.date_raw,
                    "issues": reasons_here,
                }
            )
            for r in reasons_here:
                reason_seen_counts[r] += 1
    return sample


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def combined_snapshot_hash(per_split_hashes: dict[str, str]) -> str:
    """Hash of the SORTED list of `"<split_id>:<hash>"` strings — sorted so
    this is stable regardless of split-processing order, and a
    determinism test proves identical inputs always produce the identical
    combined hash (this task's item 10)."""

    joined = "\n".join(f"{split_id}:{h}" for split_id, h in sorted(per_split_hashes.items()))
    return sha256_bytes(joined.encode("utf-8"))


def build_split(
    split_id: str,
    raw_dir: Path = DEFAULT_RAW_DIR,
    contract_rows: dict[str, dict[str, Any]] | None = None,
    include_excluded_seasons: bool = False,
    retrieval_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one split's records + evidence, for whichever of that
    split's (league, season) files actually exist under `raw_dir` — a
    missing file is reported (`missing: True`) rather than raising, since
    a real run may not have every league/season successfully downloaded."""

    contract_rows = contract_rows or load_contract_rows()
    split_row = contract_rows[split_id]
    season_codes_for_split = resolve_split_season_codes(split_id, contract_rows, include_excluded_seasons)
    include_kickoff_time = split_row.get("kickoff_time_available", True) is not False
    # Driven entirely by the contract's own `required_fields` string,
    # never a hardcoded split_id branch — see
    # `resolve_split_row_requirements`'s own docstring.
    requirements = resolve_split_row_requirements(split_row)

    retrieved_at_by_key: dict[tuple[str, str], str | None] = {}
    if retrieval_log:
        for d in retrieval_log.get("downloads", []):
            retrieved_at_by_key[(d["league_code"], d["season_code"])] = d.get("retrieved_at_utc")

    records: list[dict[str, Any]] = []
    files_evidence: list[dict[str, Any]] = []
    rejected_sample: list[dict[str, Any]] = []
    split_specific_rejected_sample: list[dict[str, Any]] = []
    totals = {
        "source_rows": 0,
        # `usable_settled_rows` and `rejected_unique_rows` below are THIS
        # SPLIT'S OWN eligibility decision (`_row_eligible_for_split`),
        # never validate_file's generic, split-unaware usable_fixtures/
        # rejected_fixtures — a row this split doesn't need closing odds
        # for, say, is usable here even if the generic per-row check
        # flags an incomplete closing column it never asked about. These
        # three ALWAYS reconcile exactly, per split, per file, and in
        # aggregate: source_rows == usable_settled_rows +
        # pending_settlement_rows_included + rejected_unique_rows.
        "usable_settled_rows": 0,
        "pending_settlement_rows_included": 0,
        "rejected_unique_rows": 0,
        # Generic, FILE-level (not split-specific) diagnostic count from
        # validate_file — kept as background evidence, deliberately never
        # folded into the three-way reconciliation above.
        "validation_issue_occurrences": 0,
    }

    for league_code in LEAGUE_CODES:
        for season_code in season_codes_for_split:
            csv_path = raw_file_path(raw_dir, league_code, season_code)
            if not csv_path.exists():
                files_evidence.append(
                    {"league_code": league_code, "season_code": season_code, "missing": True}
                )
                continue

            validation_result = validate_file(csv_path)
            occurrences = sum(validation_result.rejection_reason_counts.values())
            file_records, pending_settlement_in_file, file_split_specific_sample = build_file_records(
                csv_path,
                league_code,
                season_code,
                validation_result,
                include_kickoff_time,
                source_retrieved_at=retrieved_at_by_key.get((league_code, season_code)),
                requirements=requirements,
            )
            records.extend(file_records)
            rejected_sample.extend(sample_rejected_rows(validation_result, league_code, season_code))
            if len(split_specific_rejected_sample) < 50:
                split_specific_rejected_sample.extend(
                    file_split_specific_sample[: 50 - len(split_specific_rejected_sample)]
                )
            usable_settled_in_file = len(file_records) - pending_settlement_in_file
            rejected_unique_in_file = validation_result.total_rows - len(file_records)

            totals["source_rows"] += validation_result.total_rows
            totals["usable_settled_rows"] += usable_settled_in_file
            totals["pending_settlement_rows_included"] += pending_settlement_in_file
            totals["rejected_unique_rows"] += rejected_unique_in_file
            totals["validation_issue_occurrences"] += occurrences

            files_evidence.append(
                {
                    "league_code": league_code,
                    "season_code": season_code,
                    "missing": False,
                    "file_path": str(csv_path.relative_to(REPO_ROOT)) if csv_path.is_relative_to(REPO_ROOT) else str(csv_path),
                    "source_rows": validation_result.total_rows,
                    "usable_settled_rows": usable_settled_in_file,
                    "pending_settlement_rows_included": pending_settlement_in_file,
                    "rejected_unique_rows": rejected_unique_in_file,
                    "validation_issue_occurrences": occurrences,
                    "rejection_reason_counts": {k: v for k, v in validation_result.rejection_reason_counts.items() if v},
                }
            )

    has_closing = any(
        obs["price_stage"] == PRICE_STAGE_CLOSING for record in records for obs in record["price_observations"]
    )
    closing_line_benchmark_model_required = bool(split_row.get("closing_line_benchmark_model_required")) or has_closing

    # SOURCE_UNAVAILABLE when EVERY (league, season) file this split's
    # season list resolves to is missing under raw_dir — a generic check
    # over `files_evidence`, never hardcoded to one split id (e.g.
    # split_genuine_prospective_scoring's 2627 file not yet existing on
    # football-data.co.uk), so any split could in principle report this
    # status if none of its files exist yet. `files_evidence` is only
    # empty when `season_codes_for_split` itself is empty (no split in
    # DATASET_SPLIT_IDS has that today) — treated as BUILT (there is
    # nothing to be "unavailable" about an intentionally empty split).
    status = "SOURCE_UNAVAILABLE" if files_evidence and all(f["missing"] for f in files_evidence) else "BUILT"

    return {
        "split_id": split_id,
        "use": split_row.get("use"),
        "season_codes": season_codes_for_split,
        "include_excluded_seasons": include_excluded_seasons,
        "status": status,
        "records": records,
        "files": files_evidence,
        "totals": totals,
        "rejected_row_sample": rejected_sample[:50],
        "split_specific_rejected_row_sample": split_specific_rejected_sample[:50],
        "closing_line_benchmark_model_required": closing_line_benchmark_model_required,
        "closing_line_benchmark_model_label": _CLOSING_LINE_BENCHMARK_MODEL_LABEL if closing_line_benchmark_model_required else None,
    }


def write_split_dataset_file(split_result: dict[str, Any], dataset_dir: Path = DEFAULT_DATASET_DIR) -> tuple[Path, str]:
    """Write one split's records to its own JSON file under `dataset_dir`
    (gitignored — see `.gitignore`) and return `(path, sha256_hex)`."""

    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / f"{split_result['split_id']}.json"
    payload = json.dumps(split_result["records"], indent=2, sort_keys=True) + "\n"
    data = payload.encode("utf-8")
    path.write_bytes(data)
    return path, sha256_bytes(data)


def build_season_1415_diagnostic(raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, Any]:
    """Diagnostic evidence ONLY (this task's item 6/7): `column_drift`
    between season 1314's header and 1415's, and 1415's and 1516's, per
    league. This function never reclassifies or auto-fixes 1415 — it only
    reports what it finds so a human can judge whether the 1415 outlier
    (usable-fixture rate collapsing to ~9% across all 5 leagues
    simultaneously) is a real Football-Data source defect for that one
    season or a schema-recognition gap in this pipeline's own column
    matching. See the contract's `season_1415_diagnostic_pending_review`
    row — its `status` stays `UNRESOLVED_PENDING_HUMAN_REVIEW` regardless
    of what this diagnostic finds."""

    per_league: dict[str, Any] = {}
    for league_code in LEAGUE_CODES:
        league_result: dict[str, Any] = {}
        headers: dict[str, list[str] | None] = {}
        for season_code in ("1314", "1415", "1516"):
            csv_path = raw_file_path(raw_dir, league_code, season_code)
            if csv_path.exists():
                rows, _encoding = read_csv_rows_with_encoding(csv_path)
                headers[season_code] = rows[0] if rows else []
            else:
                headers[season_code] = None

        for from_season, to_season in SEASON_1415_DIAGNOSTIC_PAIRS:
            key = f"{from_season}_vs_{to_season}"
            if headers[from_season] is None or headers[to_season] is None:
                league_result[key] = {"available": False}
            else:
                drift = column_drift(headers[from_season], headers[to_season])
                league_result[key] = {
                    "available": True,
                    "columns_only_in_first": drift["only_in_a"],
                    "columns_only_in_second": drift["only_in_b"],
                    "no_drift": not drift["only_in_a"] and not drift["only_in_b"],
                }
        per_league[league_code] = league_result

    return {
        "status": "UNRESOLVED_PENDING_HUMAN_REVIEW",
        "statement": (
            "This diagnostic reports column-header differences only. It does NOT "
            "confirm a root cause for the season-1415 usable-fixture-rate collapse "
            "observed across all 5 leagues in the live GitHub Actions run at commit "
            "6093d0a. A human must read this evidence (and, when 'no_drift' is true "
            "for every league, conclude the anomaly is at the row/value level, not "
            "the schema level) before season 1415 is cleared for default inclusion."
        ),
        "per_league": per_league,
    }


def _sanitized_rejected_sample_from_validation(
    validation_result: FileValidationResult, league_code: str, season_code: str, cap: int = 10
) -> list[dict[str, Any]]:
    """A small, sanitized (no odds values) sample of a SINGLE file's own
    rejected rows straight from `validate_file` — `row_index`,
    `home_team`, `away_team`, `date_raw`, `issues`, capped at `cap`
    (default 10). Used by `season_1415_rejection_breakdown`, independent
    of any split."""

    sample: list[dict[str, Any]] = []
    for row in validation_result.row_results:
        if row.usable:
            continue
        if len(sample) >= cap:
            break
        sample.append(
            {
                "league_code": league_code,
                "season_code": season_code,
                "row_index": row.row_index,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "date_raw": row.date_raw,
                "issues": [issue.reason for issue in row.issues],
            }
        )
    return sample


def build_season_1415_rejection_breakdown(raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, Any]:
    """Diagnostic evidence ONLY (this task's item 5/check-2), alongside
    `build_season_1415_diagnostic`'s own column-drift evidence: for each of
    the 5 leagues, run `validate_file()` directly against that league's
    OWN 1415 raw file (independent of any split's requirements) and report
    its `rejection_reason_counts`, `total_rows`, `usable_fixtures`,
    `rejected_fixtures`, plus a small sanitized sample (capped at 10 per
    league, no odds values) of that file's own rejected rows.

    This does NOT change season 1415's excluded-by-default status in any
    way (see `split_excluded_by_default` and
    `season_1415_diagnostic_pending_review` in the contract) — it remains
    excluded pending human review; this section only adds diagnostic
    evidence a human can read to make that decision, exactly like the
    existing column-drift diagnostic."""

    per_league: dict[str, Any] = {}
    for league_code in LEAGUE_CODES:
        csv_path = raw_file_path(raw_dir, league_code, "1415")
        if not csv_path.exists():
            per_league[league_code] = {"available": False}
            continue
        validation_result = validate_file(csv_path)
        per_league[league_code] = {
            "available": True,
            "total_rows": validation_result.total_rows,
            "usable_fixtures": validation_result.usable_fixtures,
            "rejected_fixtures": validation_result.rejected_fixtures,
            "rejection_reason_counts": {k: v for k, v in validation_result.rejection_reason_counts.items() if v},
            "rejected_row_sample": _sanitized_rejected_sample_from_validation(validation_result, league_code, "1415"),
        }

    return {
        "status": "UNRESOLVED_PENDING_HUMAN_REVIEW",
        "statement": (
            "This diagnostic reports each league's own season-1415 file's typed "
            "rejection-reason breakdown (independent of any split's own "
            "requirements). It does NOT change season 1415's excluded-by-default "
            "status — that remains pending human review (see "
            "season_1415_diagnostic_pending_review in the contract) — it only adds "
            "evidence for that review, exactly like the column-drift diagnostic "
            "above."
        ),
        "per_league": per_league,
    }


def build_dataset_report(
    raw_dir: Path = DEFAULT_RAW_DIR,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    contract_rows: dict[str, dict[str, Any]] | None = None,
    retrieval_log: dict[str, Any] | None = None,
    include_excluded_seasons: bool = False,
) -> dict[str, Any]:
    """Build every defined split, write each split's dataset file, and
    assemble the full evidence report (this task's item 6's primary
    output). Splits are always all seven `DATASET_SPLIT_IDS` — a caller
    who wants a single split's result should call `build_split` directly."""

    contract_rows = contract_rows or load_contract_rows()
    splits_report: dict[str, Any] = {}
    per_split_hashes: dict[str, str] = {}

    for split_id in DATASET_SPLIT_IDS:
        split_result = build_split(
            split_id,
            raw_dir=raw_dir,
            contract_rows=contract_rows,
            include_excluded_seasons=include_excluded_seasons,
            retrieval_log=retrieval_log,
        )
        dataset_path, dataset_hash = write_split_dataset_file(split_result, dataset_dir=dataset_dir)
        per_split_hashes[split_id] = dataset_hash
        splits_report[split_id] = {
            "use": split_result["use"],
            "season_codes": split_result["season_codes"],
            "include_excluded_seasons": split_result["include_excluded_seasons"],
            "status": split_result["status"],
            "totals": split_result["totals"],
            "files": split_result["files"],
            "rejected_row_sample": split_result["rejected_row_sample"],
            "split_specific_rejected_row_sample": split_result["split_specific_rejected_row_sample"],
            "dataset_file": str(dataset_path.relative_to(REPO_ROOT)) if dataset_path.is_relative_to(REPO_ROOT) else str(dataset_path),
            "dataset_file_sha256": dataset_hash,
            "closing_line_benchmark_model_required": split_result["closing_line_benchmark_model_required"],
            "closing_line_benchmark_model_label": split_result["closing_line_benchmark_model_label"],
        }

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_version": contract_rows["contract_meta"]["version"],
        "contract_status": contract_rows["contract_meta"]["status"],
        "splits": splits_report,
        "dataset_snapshot_sha256": combined_snapshot_hash(per_split_hashes),
        "season_1415_diagnostic": build_season_1415_diagnostic(raw_dir),
        "season_1415_rejection_breakdown": build_season_1415_rejection_breakdown(raw_dir),
        "note": (
            "This report describes whatever raw files were actually present under "
            f"{raw_dir.relative_to(REPO_ROOT) if raw_dir.is_relative_to(REPO_ROOT) else raw_dir} at build "
            "time — real downloads happen separately via `python -m data_pipeline.download`. "
            "A missing (league, season) file is "
            "reported per-file as `missing: true`, never fabricated. Dataset row "
            "content itself is written only to data_pipeline/dataset/ (gitignored) — "
            "never committed; this report's numbers, hashes, the season-1415 "
            "diagnostic, and the sanitized rejected-row sample are the committed "
            "evidence."
        ),
    }


def _format_totals_markdown(totals: dict[str, int]) -> str:
    return (
        f"source_rows={totals['source_rows']}, usable_settled_rows={totals['usable_settled_rows']}, "
        f"pending_settlement_rows_included={totals['pending_settlement_rows_included']}, "
        f"rejected_unique_rows={totals['rejected_unique_rows']}, "
        f"validation_issue_occurrences={totals['validation_issue_occurrences']}"
    )


def write_dataset_build_report(
    report: dict[str, Any],
    json_path: Path = DEFAULT_REPORT_JSON_PATH,
    markdown_path: Path = DEFAULT_REPORT_MARKDOWN_PATH,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = ["# Soccer 1X2 dataset build report", ""]
    lines.append(
        f"Contract version `{report['contract_version']}` (status: `{report['contract_status']}`). "
        f"Combined dataset snapshot hash: `{report['dataset_snapshot_sha256']}`."
    )
    lines.append("")
    lines.append(
        "Row-count labels below are EXPLICITLY DISTINCT and never conflated, and the "
        "first three always reconcile exactly: `source_rows == usable_settled_rows + "
        "pending_settlement_rows_included + rejected_unique_rows`. `source_rows` is "
        "every row read; `usable_settled_rows` and `rejected_unique_rows` are THIS "
        "SPLIT'S OWN eligibility decision (never the generic, split-unaware "
        "validate_file check — see dataset_builder.py's module docstring); "
        "`pending_settlement_rows_included` is nonzero only for "
        "split_genuine_prospective_scoring (not-yet-played fixtures admitted with a "
        "null result); `validation_issue_occurrences` is generic FILE-level "
        "diagnostic evidence, deliberately outside the three-way reconciliation."
    )
    lines.append("")
    lines.append("## Per-split summary")
    lines.append("")
    lines.append("| Split | Status | Season codes | Source rows | Usable (settled) | Pending settlement | Rejected (unique) | Issue occurrences | Dataset SHA-256 | Closing-line-benchmark label required |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|---|")
    for split_id, split in report["splits"].items():
        totals = split["totals"]
        lines.append(
            f"| {split_id} | {split['status']} | {', '.join(split['season_codes']) or '(none)'} | {totals['source_rows']} | "
            f"{totals['usable_settled_rows']} | {totals['pending_settlement_rows_included']} | "
            f"{totals['rejected_unique_rows']} | {totals['validation_issue_occurrences']} | "
            f"`{split['dataset_file_sha256'][:12]}…` | {split['closing_line_benchmark_model_label'] or 'no'} |"
        )
    lines.append("")

    lines.append("## Season 1415 diagnostic — UNRESOLVED, pending human review")
    lines.append("")
    diag = report["season_1415_diagnostic"]
    lines.append(f"> {diag['statement']}")
    lines.append("")
    lines.append("| League | 1314 vs 1415 | 1415 vs 1516 |")
    lines.append("|---|---|---|")
    for league_code, pair_results in diag["per_league"].items():
        cells = []
        for key in ("1314_vs_1415", "1415_vs_1516"):
            r = pair_results[key]
            if not r.get("available"):
                cells.append("(file not available)")
            elif r["no_drift"]:
                cells.append("no header drift")
            else:
                cells.append(f"only_in_first={r['columns_only_in_first']}, only_in_second={r['columns_only_in_second']}")
        lines.append(f"| {league_code} | {cells[0]} | {cells[1]} |")
    lines.append("")

    lines.append("## Season 1415 rejection-reason breakdown — UNRESOLVED, pending human review")
    lines.append("")
    breakdown = report["season_1415_rejection_breakdown"]
    lines.append(f"> {breakdown['statement']}")
    lines.append("")
    for league_code, league_result in breakdown["per_league"].items():
        lines.append(f"### {league_code}")
        lines.append("")
        if not league_result.get("available"):
            lines.append("_1415 file not available._")
            lines.append("")
            continue
        lines.append(
            f"total_rows={league_result['total_rows']}, "
            f"usable_fixtures={league_result['usable_fixtures']}, "
            f"rejected_fixtures={league_result['rejected_fixtures']}"
        )
        lines.append("")
        lines.append("| Rejection reason | Count |")
        lines.append("|---|---:|")
        for reason, count in league_result["rejection_reason_counts"].items():
            lines.append(f"| {reason} | {count} |")
        lines.append("")
        if league_result["rejected_row_sample"]:
            lines.append("Sanitized rejected-row sample:")
            lines.append("")
            for row in league_result["rejected_row_sample"]:
                lines.append(
                    f"- row {row['row_index']}: {row['home_team']} vs {row['away_team']} "
                    f"({row['date_raw']}) — {row['issues']}"
                )
            lines.append("")

    lines.append("## Sanitized rejected-row sample (capped, no odds values)")
    lines.append("")
    any_sample = False
    for split_id, split in report["splits"].items():
        sample = split["rejected_row_sample"]
        if not sample:
            continue
        any_sample = True
        lines.append(f"### {split_id}")
        lines.append("")
        for row in sample:
            lines.append(
                f"- {row['league_code']}/{row['season_code']} row {row['row_index']}: "
                f"{row['home_team']} vs {row['away_team']} ({row['date_raw']}) — {row['issues']}"
            )
        lines.append("")
    if not any_sample:
        lines.append("_No rejected rows sampled (no split had a rejected row, or no raw files were present)._")
        lines.append("")

    lines.append("## Split-specific rejected-row sample (capped, no odds values)")
    lines.append("")
    lines.append(
        "Unlike the generic sample above (identical across every split, sourced from "
        "`validate_file`'s own file-level issues), each row here carries the exact "
        "`split_rejection_reason` THIS split's own requirements rejected it for — e.g. "
        "distinguishing a row rejected for `OPENING_PRICES_INCOMPLETE` (a "
        "training/calibration/locked-test-shaped split's own requirement) from one "
        "rejected for `CLOSING_PRICES_INCOMPLETE`, never conflated."
    )
    lines.append("")
    any_split_specific_sample = False
    for split_id, split in report["splits"].items():
        sample = split["split_specific_rejected_row_sample"]
        if not sample:
            continue
        any_split_specific_sample = True
        lines.append(f"### {split_id}")
        lines.append("")
        for row in sample:
            lines.append(
                f"- {row['league_code']}/{row['season_code']} row {row['row_index']}: "
                f"{row['home_team']} vs {row['away_team']} ({row['date_raw']}) — {row['split_rejection_reason']}"
            )
        lines.append("")
    if not any_split_specific_sample:
        lines.append("_No split-specific rejected rows sampled (no split rejected a row, or no raw files were present)._")
        lines.append("")

    lines.append(f"> {report['note']}")

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    retrieval_log_path = REPO_ROOT / "data_pipeline" / "retrieval_log.json"
    retrieval_log = None
    if retrieval_log_path.exists():
        retrieval_log = json.loads(retrieval_log_path.read_text(encoding="utf-8"))

    report = build_dataset_report(retrieval_log=retrieval_log)
    write_dataset_build_report(report)

    print(f"Contract version {report['contract_version']} (status: {report['contract_status']}).")
    print(f"Dataset snapshot hash: {report['dataset_snapshot_sha256']}")
    for split_id, split in report["splits"].items():
        print(f"  {split_id}: {_format_totals_markdown(split['totals'])}")
    print(f"Wrote {DEFAULT_REPORT_JSON_PATH} and {DEFAULT_REPORT_MARKDOWN_PATH}")
    print(
        "Season 1415 remains UNRESOLVED_PENDING_HUMAN_REVIEW — see the report's "
        "season_1415_diagnostic section."
    )


if __name__ == "__main__":
    main()
