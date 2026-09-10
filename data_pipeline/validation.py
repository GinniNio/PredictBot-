"""Row/file-level validation for Football-Data CSV files.

Produces a machine-readable, TYPED rejection report — every rejected row
gets a specific reason code (an enum-like string in `RejectionReason`),
never a generic "invalid". Checks implemented (per this task's item 6):

- Duplicate fixtures (same teams + date more than once in a way that is
  not a legitimate double round-robin — a true duplicate is an EXACT
  repeat of (home, away, date), not the same two teams meeting again on a
  different date).
- Missing team identity / missing result.
- Invalid result labels (outside the file's own 1X2 encoding, H/D/A).
- Impossible scores (negative, non-integer, or absurdly large goal counts).
- Conflicting fixtures (same (home, away, date) reported with a different
  score/result across rows or across files).
- Inconsistent/unparseable date formats (Football-Data has used both
  `DD/MM/YY` and `DD/MM/YYYY` across seasons; detect and normalize rather
  than assume one).
- Missing or invalid odds (non-numeric, non-positive, or absent).
- Incomplete three-way prices (only 1 or 2 of {H, D, A} priced for a
  bookmaker column set that should have all 3).
- Column drift between seasons (delegated to `schema_inspection.column_drift`,
  reported separately since it's a file-level, not row-level, finding).
- Absent price-capture timestamps — Football-Data's odds columns carry no
  `captured_at` distinct from their "opening"/"closing" label; every odds
  column found is explicitly flagged `capture_timestamp_known: False`
  (schema_inspection.py) and this module never claims otherwise.

Stdlib only (csv, datetime). No new runtime dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from data_pipeline.schema_inspection import inspect_header

VALID_RESULT_LABELS = {"H", "D", "A"}
MAX_PLAUSIBLE_GOALS = 20  # anything at/above this is treated as a data-integrity bug, not a real scoreline

# Date formats Football-Data has used across seasons/leagues. Order
# matters only for ambiguous 2-vs-4-digit-year strings; both are attempted
# and the first one that parses cleanly is used, with the format itself
# recorded per row so "which format did this file actually use" is
# reportable, not just "did it parse".
KNOWN_DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y")


class RejectionReason:
    """Enum-like typed rejection reason codes. Plain string constants
    (matching this repo's existing typed-string-code convention, e.g.
    `errors.py`'s `{"code", ...}` objects) rather than a stdlib `Enum`, so
    the rejection report serializes to JSON with zero custom encoding."""

    DUPLICATE_FIXTURE = "DUPLICATE_FIXTURE"
    MISSING_TEAM_IDENTITY = "MISSING_TEAM_IDENTITY"
    MISSING_RESULT = "MISSING_RESULT"
    INVALID_RESULT_LABEL = "INVALID_RESULT_LABEL"
    IMPOSSIBLE_SCORE = "IMPOSSIBLE_SCORE"
    CONFLICTING_FIXTURE = "CONFLICTING_FIXTURE"
    UNPARSEABLE_DATE = "UNPARSEABLE_DATE"
    MISSING_OR_INVALID_ODDS = "MISSING_OR_INVALID_ODDS"
    INCOMPLETE_THREE_WAY_PRICE = "INCOMPLETE_THREE_WAY_PRICE"

    ALL = (
        DUPLICATE_FIXTURE,
        MISSING_TEAM_IDENTITY,
        MISSING_RESULT,
        INVALID_RESULT_LABEL,
        IMPOSSIBLE_SCORE,
        CONFLICTING_FIXTURE,
        UNPARSEABLE_DATE,
        MISSING_OR_INVALID_ODDS,
        INCOMPLETE_THREE_WAY_PRICE,
    )


@dataclass
class RowIssue:
    row_index: int  # 0-based, excluding header
    reason: str
    detail: str


@dataclass
class RowResult:
    row_index: int
    home_team: str | None
    away_team: str | None
    date_raw: str | None
    date_parsed: str | None  # ISO 8601 date, if parseable
    date_format_used: str | None
    usable: bool
    issues: list[RowIssue] = field(default_factory=list)


@dataclass
class FileValidationResult:
    file_path: str
    total_rows: int
    usable_fixtures: int
    rejected_fixtures: int
    rejection_reason_counts: dict[str, int]
    missingness_by_column: dict[str, float]  # canonical id -> fraction missing/empty
    odds_availability: list[dict[str, Any]]
    date_formats_observed: dict[str, int]
    row_results: list[RowResult]


def parse_date(raw: str) -> tuple[str | None, str | None]:
    """Try each known Football-Data date format. Returns
    `(iso_date_or_None, format_used_or_None)`."""

    raw = (raw or "").strip()
    if not raw:
        return None, None
    for fmt in KNOWN_DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        # 2-digit-year format: Football-Data's own convention (like the
        # season-code convention) treats a 2-digit year >= some pivot as
        # 1900s; in practice all of this pipeline's coverage is
        # post-1993, so any 2-digit year here is assumed 1900s if >= 50,
        # else 2000s, matching download.py's season_codes() logic.
        if fmt == "%d/%m/%y":
            year_2digit = parsed.year % 100
            full_year = 1900 + year_2digit if year_2digit >= 50 else 2000 + year_2digit
            parsed = parsed.replace(year=full_year)
        return parsed.date().isoformat(), fmt
    return None, None


def _is_numeric_positive(raw: str) -> bool:
    if raw is None:
        return False
    text = raw.strip()
    if not text:
        return False
    try:
        value = float(text)
    except ValueError:
        return False
    return value > 0


def validate_file(csv_path: str | Path) -> FileValidationResult:
    csv_path = Path(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = list(reader)

    shape = inspect_header(header)
    core = shape["core_columns_present"]
    home_col = core.get("home_team")
    away_col = core.get("away_team")
    date_col = core.get("fixture_date")
    fthg_col = core.get("full_time_home_goals")
    ftag_col = core.get("full_time_away_goals")
    ftr_col = core.get("full_time_result")

    odds_findings = shape["odds_columns_found"]

    seen_fixtures: dict[tuple[str, str, str], list[int]] = {}
    seen_scores: dict[tuple[str, str, str], tuple[Any, Any, Any]] = {}
    row_results: list[RowResult] = []
    reason_counts: dict[str, int] = {reason: 0 for reason in RejectionReason.ALL}
    date_format_counts: dict[str, int] = {}

    missing_counts = {canonical_id: 0 for canonical_id in core}
    odds_missing_counts: dict[str, int] = {}
    for finding in odds_findings:
        for col in (finding.home_col, finding.draw_col, finding.away_col):
            odds_missing_counts[col] = 0

    for idx, row in enumerate(rows):
        issues: list[RowIssue] = []

        home = (row.get(home_col) or "").strip() if home_col else ""
        away = (row.get(away_col) or "").strip() if away_col else ""
        date_raw = (row.get(date_col) or "").strip() if date_col else ""

        for canonical_id, actual_col in core.items():
            if actual_col is None:
                continue
            if not (row.get(actual_col) or "").strip():
                missing_counts[canonical_id] += 1

        if not home or not away:
            issues.append(RowIssue(idx, RejectionReason.MISSING_TEAM_IDENTITY, "home_team or away_team blank"))

        date_iso, date_fmt = (None, None)
        if date_raw:
            date_iso, date_fmt = parse_date(date_raw)
            if date_fmt is None:
                issues.append(RowIssue(idx, RejectionReason.UNPARSEABLE_DATE, f"could not parse date {date_raw!r}"))
            else:
                date_format_counts[date_fmt] = date_format_counts.get(date_fmt, 0) + 1
        else:
            issues.append(RowIssue(idx, RejectionReason.UNPARSEABLE_DATE, "date column blank"))

        result_raw = (row.get(ftr_col) or "").strip() if ftr_col else ""
        if not result_raw:
            issues.append(RowIssue(idx, RejectionReason.MISSING_RESULT, "full-time result column blank"))
        elif result_raw.upper() not in VALID_RESULT_LABELS:
            issues.append(
                RowIssue(idx, RejectionReason.INVALID_RESULT_LABEL, f"result label {result_raw!r} not in H/D/A")
            )

        home_goals_raw = (row.get(fthg_col) or "").strip() if fthg_col else ""
        away_goals_raw = (row.get(ftag_col) or "").strip() if ftag_col else ""
        for label, raw_value in (("home", home_goals_raw), ("away", away_goals_raw)):
            if not raw_value:
                continue
            try:
                value = int(raw_value)
            except ValueError:
                issues.append(RowIssue(idx, RejectionReason.IMPOSSIBLE_SCORE, f"{label} goals {raw_value!r} not an integer"))
                continue
            if value < 0 or value >= MAX_PLAUSIBLE_GOALS:
                issues.append(RowIssue(idx, RejectionReason.IMPOSSIBLE_SCORE, f"{label} goals {value} outside plausible range"))

        # Odds checks: missing/invalid + incomplete three-way, per bookmaker column set found in this file.
        for finding in odds_findings:
            values = {}
            for role, col in (("home", finding.home_col), ("draw", finding.draw_col), ("away", finding.away_col)):
                raw_value = row.get(col)
                values[role] = raw_value
                if col in odds_missing_counts and not _is_numeric_positive(raw_value):
                    odds_missing_counts[col] += 1
            present_flags = [_is_numeric_positive(v) for v in values.values()]
            present_count = sum(present_flags)
            if present_count == 0:
                # Column set exists in the header but this row has no
                # usable price for it at all.
                issues.append(
                    RowIssue(
                        idx,
                        RejectionReason.MISSING_OR_INVALID_ODDS,
                        f"{finding.bookmaker_prefix} ({finding.variant}) has no valid odds for this row",
                    )
                )
            elif 0 < present_count < 3:
                issues.append(
                    RowIssue(
                        idx,
                        RejectionReason.INCOMPLETE_THREE_WAY_PRICE,
                        f"{finding.bookmaker_prefix} ({finding.variant}) only {present_count}/3 outcomes priced",
                    )
                )

        # Duplicate / conflicting fixture detection.
        if home and away and date_iso:
            key = (home, away, date_iso)
            if key in seen_fixtures:
                issues.append(
                    RowIssue(idx, RejectionReason.DUPLICATE_FIXTURE, f"same (home, away, date) already seen at row(s) {seen_fixtures[key]}")
                )
                prior_score = seen_scores.get(key)
                this_score = (home_goals_raw, away_goals_raw, result_raw)
                if prior_score is not None and prior_score != this_score:
                    issues.append(
                        RowIssue(
                            idx,
                            RejectionReason.CONFLICTING_FIXTURE,
                            f"same fixture reported with score/result {prior_score} vs {this_score}",
                        )
                    )
                seen_fixtures[key].append(idx)
            else:
                seen_fixtures[key] = [idx]
                seen_scores[key] = (home_goals_raw, away_goals_raw, result_raw)

        for issue in issues:
            reason_counts[issue.reason] = reason_counts.get(issue.reason, 0) + 1

        row_results.append(
            RowResult(
                row_index=idx,
                home_team=home or None,
                away_team=away or None,
                date_raw=date_raw or None,
                date_parsed=date_iso,
                date_format_used=date_fmt,
                usable=(len(issues) == 0),
                issues=issues,
            )
        )

    total_rows = len(rows)
    usable = sum(1 for r in row_results if r.usable)
    rejected = total_rows - usable

    missingness = {
        canonical_id: (missing_counts[canonical_id] / total_rows if total_rows else 0.0) for canonical_id in core
    }
    odds_availability = []
    for finding in odds_findings:
        cols = (finding.home_col, finding.draw_col, finding.away_col)
        missing_rates = {col: (odds_missing_counts[col] / total_rows if total_rows else 0.0) for col in cols}
        odds_availability.append(
            {
                "bookmaker_prefix": finding.bookmaker_prefix,
                "bookmaker_name": finding.bookmaker_name,
                "is_pinnacle": finding.is_pinnacle,
                "variant": finding.variant,
                "all_three_columns_present_in_header": finding.all_three_present,
                "capture_timestamp_known": False,
                "missing_or_invalid_rate_by_column": missing_rates,
            }
        )

    return FileValidationResult(
        file_path=str(csv_path),
        total_rows=total_rows,
        usable_fixtures=usable,
        rejected_fixtures=rejected,
        rejection_reason_counts=reason_counts,
        missingness_by_column=missingness,
        odds_availability=odds_availability,
        date_formats_observed=date_format_counts,
        row_results=row_results,
    )


def result_to_dict(result: FileValidationResult, include_row_detail: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "file_path": result.file_path,
        "total_rows": result.total_rows,
        "usable_fixtures": result.usable_fixtures,
        "rejected_fixtures": result.rejected_fixtures,
        "rejection_reason_counts": result.rejection_reason_counts,
        "missingness_by_column": result.missingness_by_column,
        "odds_availability": result.odds_availability,
        "date_formats_observed": result.date_formats_observed,
    }
    if include_row_detail:
        out["rejected_rows"] = [
            {
                "row_index": r.row_index,
                "home_team": r.home_team,
                "away_team": r.away_team,
                "date_raw": r.date_raw,
                "issues": [{"reason": i.reason, "detail": i.detail} for i in r.issues],
            }
            for r in result.row_results
            if not r.usable
        ]
    return out
