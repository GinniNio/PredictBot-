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

Four labels, EXPLICITLY DISTINCT (this task's core reporting requirement
— never conflate them):

- `source_rows`               — total rows read from a file (before any
                                  validation judgment).
- `usable_rows`                — rows `validation.validate_file` found no
                                  issue with at all. These are always
                                  written into a split's dataset file;
                                  `split_prospective_paper_scoring` ALSO
                                  writes a row whose sole issue is a blank
                                  (not-yet-settled) result, with
                                  `result: None` — see
                                  `pending_settlement_rows_included` below
                                  and `build_file_records`'s own
                                  docstring.
- `rejected_unique_rows`       — `source_rows - usable_rows`: ONE count
                                  per row, even if that row tripped several
                                  typed rejection reasons at once.
- `validation_issue_occurrences` — the sum of every typed rejection
                                  reason's occurrence count
                                  (`FileValidationResult.rejection_reason_counts`)
                                  — CAN exceed `rejected_unique_rows` when a
                                  row trips multiple reasons, or a file has
                                  multiple odds-column sets each
                                  independently flagged. This distinction
                                  already exists correctly in
                                  `validation.py` (`rejected_fixtures` vs.
                                  `rejection_reason_counts`) — this module
                                  only surfaces it explicitly, never
                                  reimplements or blurs it. See
                                  `tests/test_dataset_builder.py`'s
                                  `RejectedVsOccurrencesRegressionTests`
                                  for the regression test guarding this.

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
from data_pipeline.validation import FileValidationResult, RejectionReason, validate_file

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
# own, deliberately NARROWER season range (1920-2425 only, matched to
# `split_model_dev_and_completed_eval`'s own range so every closing-line
# row has a same-range completed-eval counterpart to compare against — see
# that split's own `exclusion_note` in the contract). Both
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
    "split_model_dev_and_completed_eval",
    "split_closing_line_benchmark",
    "split_prospective_paper_scoring",
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
    allow_null_result: bool = False,
) -> list[dict[str, Any]]:
    """Build normalized per-match records for every USABLE row of one
    file, plus — only when `allow_null_result=True` (the
    `split_prospective_paper_scoring` split only, per its contract row's
    `result_nullable_until_settlement` required field) — every row whose
    ONLY validation issue is `MISSING_RESULT` (a fixture that simply
    hasn't been played yet has a blank FTR column and nothing else wrong
    with it). Such a row is included with `result: None` rather than
    dropped, since dropping it would silently exclude exactly the
    not-yet-settled fixtures the prospective split exists to score. A row
    with `MISSING_RESULT` alongside any OTHER issue (bad date, missing
    team identity, etc.) is still excluded — a genuinely malformed row is
    never smuggled in just because one of its several problems happens to
    be a blank result. Every other split leaves `allow_null_result` at its
    default `False` and behaves exactly as before (usable rows only).

    `include_kickoff_time` reflects the split's own contract row (kickoff
    time is only meaningful from season 1920 onward — see
    `price_field_match_kickoff_at`); when False, `match_kickoff_at` is
    always `None`, even if the file happens to carry a `Time` column
    (never inferred from the file — from the split's contract instead, so
    a stray `Time` column in an older-season file can never leak a
    kickoff time this split's contract says is unavailable)."""

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

    records: list[dict[str, Any]] = []
    usable_index_set = {r.row_index for r in validation_result.row_results if r.usable}
    null_result_index_set: set[int] = set()
    if allow_null_result:
        for r in validation_result.row_results:
            if r.usable or r.row_index in usable_index_set:
                continue
            reasons = {issue.reason for issue in r.issues}
            if reasons == {RejectionReason.MISSING_RESULT}:
                null_result_index_set.add(r.row_index)

    for idx, row in enumerate(rows):
        if idx not in usable_index_set and idx not in null_result_index_set:
            continue
        result_raw = (row.get(ftr_col) or "").strip() if ftr_col else ""
        records.append(
            {
                "league_code": league_code,
                "season_code": season_code,
                "date_raw": (row.get(date_col) or "").strip() if date_col else None,
                "match_kickoff_at": ((row.get(time_col) or "").strip() or None) if (include_kickoff_time and time_col) else None,
                "home_team": (row.get(home_col) or "").strip() if home_col else None,
                "away_team": (row.get(away_col) or "").strip() if away_col else None,
                "result": result_raw.upper() or None,
                "result_pending_settlement": idx in null_result_index_set,
                "price_observations": _price_observations_for_row(row, odds_findings, source_retrieved_at),
            }
        )
    return records


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
    # Driven by the contract's own `required_fields` string, never a
    # hardcoded split_id check — a row whose ONLY validation issue is a
    # blank result is included with result=None for a split whose
    # contract explicitly says results are nullable-until-settlement (see
    # build_file_records' own docstring for why: dropping such a row
    # would silently exclude exactly the not-yet-played fixtures a
    # prospective split exists to score).
    allow_null_result = "result_nullable_until_settlement" in (split_row.get("required_fields") or "")

    retrieved_at_by_key: dict[tuple[str, str], str | None] = {}
    if retrieval_log:
        for d in retrieval_log.get("downloads", []):
            retrieved_at_by_key[(d["league_code"], d["season_code"])] = d.get("retrieved_at_utc")

    records: list[dict[str, Any]] = []
    files_evidence: list[dict[str, Any]] = []
    rejected_sample: list[dict[str, Any]] = []
    totals = {
        "source_rows": 0,
        "usable_rows": 0,
        "rejected_unique_rows": 0,
        "validation_issue_occurrences": 0,
        # Only ever nonzero for a split with allow_null_result=True: rows
        # whose sole issue was a blank (not-yet-settled) result, included
        # in `records` with result=None rather than dropped. These are
        # counted separately from `usable_rows` (validate_file's generic,
        # split-unaware usability check) precisely so len(records) is
        # always reconcilable from this totals dict:
        # len(records) == usable_rows + pending_settlement_rows_included.
        "pending_settlement_rows_included": 0,
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
            file_records = build_file_records(
                csv_path,
                league_code,
                season_code,
                validation_result,
                include_kickoff_time,
                source_retrieved_at=retrieved_at_by_key.get((league_code, season_code)),
                allow_null_result=allow_null_result,
            )
            records.extend(file_records)
            rejected_sample.extend(sample_rejected_rows(validation_result, league_code, season_code))
            pending_settlement_in_file = sum(1 for r in file_records if r["result_pending_settlement"])

            totals["source_rows"] += validation_result.total_rows
            totals["usable_rows"] += validation_result.usable_fixtures
            totals["rejected_unique_rows"] += validation_result.rejected_fixtures
            totals["validation_issue_occurrences"] += occurrences
            totals["pending_settlement_rows_included"] += pending_settlement_in_file

            files_evidence.append(
                {
                    "league_code": league_code,
                    "season_code": season_code,
                    "missing": False,
                    "file_path": str(csv_path.relative_to(REPO_ROOT)) if csv_path.is_relative_to(REPO_ROOT) else str(csv_path),
                    "source_rows": validation_result.total_rows,
                    "usable_rows": validation_result.usable_fixtures,
                    "rejected_unique_rows": validation_result.rejected_fixtures,
                    "validation_issue_occurrences": occurrences,
                    "pending_settlement_rows_included": pending_settlement_in_file,
                    "rejection_reason_counts": {k: v for k, v in validation_result.rejection_reason_counts.items() if v},
                }
            )

    has_closing = any(
        obs["price_stage"] == PRICE_STAGE_CLOSING for record in records for obs in record["price_observations"]
    )
    closing_line_benchmark_model_required = bool(split_row.get("closing_line_benchmark_model_required")) or has_closing

    return {
        "split_id": split_id,
        "use": split_row.get("use"),
        "season_codes": season_codes_for_split,
        "include_excluded_seasons": include_excluded_seasons,
        "records": records,
        "files": files_evidence,
        "totals": totals,
        "rejected_row_sample": rejected_sample[:50],
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


def build_dataset_report(
    raw_dir: Path = DEFAULT_RAW_DIR,
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    contract_rows: dict[str, dict[str, Any]] | None = None,
    retrieval_log: dict[str, Any] | None = None,
    include_excluded_seasons: bool = False,
) -> dict[str, Any]:
    """Build every defined split, write each split's dataset file, and
    assemble the full evidence report (this task's item 6's primary
    output). Splits are always all four `DATASET_SPLIT_IDS` — a caller
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
            "totals": split_result["totals"],
            "files": split_result["files"],
            "rejected_row_sample": split_result["rejected_row_sample"],
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
    text = (
        f"source_rows={totals['source_rows']}, usable_rows={totals['usable_rows']}, "
        f"rejected_unique_rows={totals['rejected_unique_rows']}, "
        f"validation_issue_occurrences={totals['validation_issue_occurrences']}"
    )
    if totals.get("pending_settlement_rows_included"):
        text += f", pending_settlement_rows_included={totals['pending_settlement_rows_included']}"
    return text


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
        "Four row-count labels below are EXPLICITLY DISTINCT and never conflated: "
        "`source_rows` (every row read), `usable_rows` (passed every validation "
        "check), `rejected_unique_rows` (`source_rows - usable_rows`, one per row), "
        "and `validation_issue_occurrences` (sum of every typed rejection reason's "
        "occurrence count — can exceed `rejected_unique_rows` when a row trips "
        "multiple reasons)."
    )
    lines.append("")
    lines.append("## Per-split summary")
    lines.append("")
    lines.append("| Split | Season codes | Source rows | Usable | Rejected (unique) | Issue occurrences | Dataset SHA-256 | Closing-line-benchmark label required |")
    lines.append("|---|---|---:|---:|---:|---:|---|---|")
    for split_id, split in report["splits"].items():
        totals = split["totals"]
        lines.append(
            f"| {split_id} | {', '.join(split['season_codes']) or '(none)'} | {totals['source_rows']} | "
            f"{totals['usable_rows']} | {totals['rejected_unique_rows']} | {totals['validation_issue_occurrences']} | "
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
