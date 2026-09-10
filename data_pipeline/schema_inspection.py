"""Schema inspection for Football-Data CSV files.

Reports what a given Football-Data file ACTUALLY contains for the columns
this pipeline cares about — fixture date, kickoff time, home/away team
identity, full-time goals, 1X2 result, and every bookmaker's odds columns
found (opening AND closing tracked as distinct fields, never averaged) —
as PRESENT or ABSENT. Never assumes a fixed schema across seasons:
Football-Data's own column layout has drifted historically (new
bookmakers added/dropped, closing-odds columns added in later seasons,
etc.), so every file is inspected independently.

Stdlib only (csv). No new runtime dependency.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Canonical "core" columns Football-Data files are expected to carry, and
# the exact header spellings this pipeline recognizes for each. Multiple
# spellings are listed because Football-Data's own header names have not
# been perfectly stable across every season/league file historically.
CORE_COLUMNS: dict[str, list[str]] = {
    "fixture_date": ["Date"],
    "kickoff_time": ["Time"],  # only present in more recent seasons
    "home_team": ["HomeTeam", "HT"],
    "away_team": ["AwayTeam", "AT"],
    "full_time_home_goals": ["FTHG", "HG"],
    "full_time_away_goals": ["FTAG", "AG"],
    "full_time_result": ["FTR", "Res"],
    "half_time_home_goals": ["HTHG"],
    "half_time_away_goals": ["HTAG"],
    "half_time_result": ["HTR"],
    "league_division": ["Div"],
}

# Known bookmaker odds column PREFIXES Football-Data has used across its
# history, mapped to a human-readable bookmaker name and whether this
# bookmaker is Pinnacle (which needs the dedicated staleness caveat,
# spec section 18 bucket 1 / manifest market_snapshot_odds_1x2 row).
# Each bookmaker may contribute up to two distinct triples:
#   "{prefix}H/D/A"   -> pre-closing / opening-ish odds (Football-Data does
#                        not itself label these as strictly "opening" for
#                        every bookmaker/season; see note below)
#   "{prefix}CH/CD/CA" -> closing odds (the "C" suffix convention Football-
#                        Data introduced in later seasons for a subset of
#                        bookmakers)
# These are tracked as SEPARATE fields throughout this pipeline — never
# merged or averaged into one "the odds" value.
KNOWN_BOOKMAKER_PREFIXES: dict[str, dict[str, Any]] = {
    "B365": {"name": "Bet365", "is_pinnacle": False},
    "BW": {"name": "Bet&Win", "is_pinnacle": False},
    "IW": {"name": "Interwetten", "is_pinnacle": False},
    "PS": {"name": "Pinnacle", "is_pinnacle": True},
    "P": {"name": "Pinnacle (legacy column prefix)", "is_pinnacle": True},
    "WH": {"name": "William Hill", "is_pinnacle": False},
    "VC": {"name": "VC Bet", "is_pinnacle": False},
    "LB": {"name": "Ladbrokes", "is_pinnacle": False},
    "SB": {"name": "Sporting Bet", "is_pinnacle": False},
    "GB": {"name": "Gamebookers", "is_pinnacle": False},
    "SJ": {"name": "Stan James", "is_pinnacle": False},
    "SY": {"name": "Stanleybet", "is_pinnacle": False},
    "BS": {"name": "Blue Square", "is_pinnacle": False},
    "1XB": {"name": "1xBet", "is_pinnacle": False},
}

# Market-aggregate columns (not a single bookmaker's own price, but a
# summary statistic across the bookmakers Football-Data tracks for that
# file/season). Tracked distinctly — never conflated with any one
# bookmaker's own price, and their own "closing" variant is a distinct
# column set too.
MARKET_AGGREGATE_PREFIXES: dict[str, str] = {
    "Max": "Market maximum odds across tracked bookmakers",
    "Avg": "Market average odds across tracked bookmakers",
}


@dataclass
class OddsColumnFinding:
    bookmaker_prefix: str
    bookmaker_name: str
    is_pinnacle: bool
    variant: str  # "opening_or_only" | "closing"
    home_col: str
    draw_col: str
    away_col: str
    all_three_present: bool
    # Hard constraint (spec Correction 1 / section 18): Football-Data
    # carries no column recording WHEN a given odds column's value was
    # actually captured — "opening"/"closing" are the file's own labels,
    # never an actual `captured_at` timestamp. This is always False for
    # every column this module finds; it is an explicit field (not an
    # omission) so nothing downstream can quietly assume otherwise.
    capture_timestamp_known: bool = False


@dataclass
class SchemaInspectionResult:
    file_path: str
    header: list[str]
    core_columns_present: dict[str, str | None]  # canonical id -> actual header found, or None
    core_columns_absent: list[str]
    odds_columns_found: list[OddsColumnFinding]
    market_aggregate_columns_found: list[dict[str, Any]]
    unrecognized_columns: list[str]
    row_count: int


def _find_core_column(header: list[str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        if candidate in header:
            return candidate
    return None


def inspect_header(header: list[str]) -> dict[str, Any]:
    """Pure header-shape inspection — no row data needed. Split out so
    validation.py and tests can reuse it without re-reading the file."""

    core_present: dict[str, str | None] = {}
    core_absent: list[str] = []
    for canonical_id, candidates in CORE_COLUMNS.items():
        found = _find_core_column(header, candidates)
        core_present[canonical_id] = found
        if found is None:
            core_absent.append(canonical_id)

    odds_findings: list[OddsColumnFinding] = []
    consumed: set[str] = set()
    # Sort longer prefixes first so e.g. "PS" is checked before the
    # single-letter legacy "P" prefix does not spuriously "consume" PSH.
    for prefix in sorted(KNOWN_BOOKMAKER_PREFIXES, key=len, reverse=True):
        meta = KNOWN_BOOKMAKER_PREFIXES[prefix]
        for variant, suffix_set in (
            ("opening_or_only", ("H", "D", "A")),
            ("closing", ("CH", "CD", "CA")),
        ):
            home_col, draw_col, away_col = (f"{prefix}{s}" for s in suffix_set)
            if home_col in consumed or draw_col in consumed or away_col in consumed:
                continue
            present = [c in header for c in (home_col, draw_col, away_col)]
            if any(present):
                consumed.update({home_col, draw_col, away_col} & set(header))
                odds_findings.append(
                    OddsColumnFinding(
                        bookmaker_prefix=prefix,
                        bookmaker_name=meta["name"],
                        is_pinnacle=meta["is_pinnacle"],
                        variant=variant,
                        home_col=home_col,
                        draw_col=draw_col,
                        away_col=away_col,
                        all_three_present=all(present),
                    )
                )

    aggregate_findings: list[dict[str, Any]] = []
    for prefix, description in MARKET_AGGREGATE_PREFIXES.items():
        for variant, suffix_set in (("opening_or_only", ("H", "D", "A")), ("closing", ("CH", "CD", "CA"))):
            home_col, draw_col, away_col = (f"{prefix}{s}" for s in suffix_set)
            if home_col in consumed or draw_col in consumed or away_col in consumed:
                continue
            present = [c in header for c in (home_col, draw_col, away_col)]
            if any(present):
                consumed.update({home_col, draw_col, away_col} & set(header))
                aggregate_findings.append(
                    {
                        "prefix": prefix,
                        "description": description,
                        "variant": variant,
                        "home_col": home_col,
                        "draw_col": draw_col,
                        "away_col": away_col,
                        "all_three_present": all(present),
                    }
                )

    recognized = set(core_present.values()) - {None}
    for finding in odds_findings:
        recognized.update({finding.home_col, finding.draw_col, finding.away_col})
    for agg in aggregate_findings:
        recognized.update({agg["home_col"], agg["draw_col"], agg["away_col"]})
    unrecognized = [c for c in header if c not in recognized]

    return {
        "core_columns_present": core_present,
        "core_columns_absent": core_absent,
        "odds_columns_found": odds_findings,
        "market_aggregate_columns_found": aggregate_findings,
        "unrecognized_columns": unrecognized,
    }


def inspect_file(csv_path: str | Path) -> SchemaInspectionResult:
    csv_path = Path(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            header = []
        row_count = sum(1 for _ in reader)

    shape = inspect_header(header)
    return SchemaInspectionResult(
        file_path=str(csv_path),
        header=header,
        core_columns_present=shape["core_columns_present"],
        core_columns_absent=shape["core_columns_absent"],
        odds_columns_found=shape["odds_columns_found"],
        market_aggregate_columns_found=shape["market_aggregate_columns_found"],
        unrecognized_columns=shape["unrecognized_columns"],
        row_count=row_count,
    )


def result_to_dict(result: SchemaInspectionResult) -> dict[str, Any]:
    return {
        "file_path": result.file_path,
        "header": result.header,
        "row_count": result.row_count,
        "core_columns_present": result.core_columns_present,
        "core_columns_absent": result.core_columns_absent,
        "odds_columns_found": [
            {
                "bookmaker_prefix": f.bookmaker_prefix,
                "bookmaker_name": f.bookmaker_name,
                "is_pinnacle": f.is_pinnacle,
                "variant": f.variant,
                "home_col": f.home_col,
                "draw_col": f.draw_col,
                "away_col": f.away_col,
                "all_three_present": f.all_three_present,
                "capture_timestamp_known": f.capture_timestamp_known,
            }
            for f in result.odds_columns_found
        ],
        "market_aggregate_columns_found": result.market_aggregate_columns_found,
        "unrecognized_columns": result.unrecognized_columns,
    }


def column_drift(header_a: list[str], header_b: list[str]) -> dict[str, list[str]]:
    """Report which columns exist in header A but not B, and vice versa —
    the "column drift between seasons" check (spec's "Next sequence" step
    2 / this task's item 6)."""

    set_a, set_b = set(header_a), set(header_b)
    return {
        "only_in_a": sorted(set_a - set_b),
        "only_in_b": sorted(set_b - set_a),
        "in_both": sorted(set_a & set_b),
    }
