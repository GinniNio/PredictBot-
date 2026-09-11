"""Feasibility report generator for the Soccer 1X2 data-feasibility
pipeline.

Combines `schema_inspection.py` + `validation.py` output across every file
this pipeline was actually able to run (real downloads if any succeeded,
otherwise the hand-crafted test fixtures) into one report broken down by
league AND season, per this task's item 7:

- total rows, usable fixtures, rejected fixtures (with reason breakdown)
- missingness rates per required feature-adjacent column
- which odds fields are actually available per league/season
- column drift vs. the previous season in the same league (when 2+ seasons
  of the same league are available)

Every league-season row is explicitly labeled with exactly one of:

  LIVE_SOURCE_VALIDATED   — this league-season's data was actually
                            downloaded from football-data.co.uk in this
                            environment and run through schema-inspection/
                            validation against that real, live-fetched
                            file, and that real content passed validation
                            well enough to be usable.
  FIXTURE_ONLY_VALIDATED  — this league-season was only exercised against
                            a small hand-crafted test fixture, or a real
                            download attempt failed/was blocked (i.e. no
                            real Football-Data content was ever obtained
                            to judge one way or the other).
  SOURCE_NOT_USABLE       — a live download DID complete with real
                            Football-Data content for this league-season,
                            but that real content fails validation badly
                            enough to be unusable (e.g. required odds
                            columns missing entirely, a garbled/unparseable
                            format, or insufficient row volume). This is a
                            genuine NEGATIVE FINDING about the source
                            itself, only ever assigned from real observed
                            evidence of a real downloaded file — never used
                            as a stand-in for "we couldn't test it" (that
                            case is always FIXTURE_ONLY_VALIDATED, never
                            SOURCE_NOT_USABLE, even if the reason is a
                            failed/blocked download).

A hand-crafted test fixture can only ever prove the PARSER's behavior
(does the code correctly detect a duplicate fixture, a bad date format,
missing odds, etc.) — it can never prove SOURCE COMPATIBILITY or DATASET
USABILITY for a real league/season. `FIXTURE_ONLY_VALIDATED` rows must
never be read as "this league-season is usable" — see
`data_pipeline/FEASIBILITY_DECISION.md`.

This module also builds a COMPACT per-league-season summary
(`build_compact_summary`/`format_compact_summary_line`/
`format_compact_summary_markdown`) and league/season aggregates
(`build_aggregates`/`format_aggregates_markdown`) for every league-season
that was actually downloaded — this is the PRIMARY reviewable evidence for
a live run: it is printed directly to stdout (the job log) and rendered
into `$GITHUB_STEP_SUMMARY` by the workflow, so a reviewer never has to
download the uploaded JSON artifact to see what this run actually found.
The full JSON/Markdown reports remain the secondary, detailed evidence
record for whoever can download the artifact.

Stdlib only (json). No new runtime dependency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.download import DEFAULT_MANIFEST_PATH, ERROR_NOT_FOUND, load_manifest, season_codes
from data_pipeline.schema_inspection import column_drift, inspect_file, result_to_dict as schema_to_dict
from data_pipeline.validation import result_to_dict as validation_to_dict
from data_pipeline.validation import validate_file


def _repo_relative_path(path: Path) -> str:
    """Render `path` as a path relative to the repository root, never a
    machine-specific absolute path — so `feasibility_report.json`/`.md`
    are reproducible byte-for-byte (modulo timestamps) regardless of where
    this repo happens to be checked out (a different absolute prefix on
    every environment: ChatGPT, Claude Cowork, Gemini, Windows, CI, a
    developer's own machine). Falls back to the path as given only if it
    genuinely is not inside REPO_ROOT (should not happen for anything this
    module writes into a report, but never raises over it)."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()

LIVE_SOURCE_VALIDATED = "LIVE_SOURCE_VALIDATED"
FIXTURE_ONLY_VALIDATED = "FIXTURE_ONLY_VALIDATED"
SOURCE_NOT_USABLE = "SOURCE_NOT_USABLE"

# The complete, closed set of valid `source_label` values for a per-file
# feasibility-report entry (a real download OR a hand-crafted fixture — see
# `build_entry`/`build_live_entry`/`build_fixture_only_report`). See the
# module docstring above for what each one means and when it may (and may
# not) be assigned. This set intentionally stays at three — it is NOT the
# same set used by the source-attempt table below, which additionally
# tracks attempts that were never even run against a real or fixture file
# (see `SOURCE_NOT_LISTED`).
VALID_SOURCE_LABELS = (LIVE_SOURCE_VALIDATED, FIXTURE_ONLY_VALIDATED, SOURCE_NOT_USABLE)

# A source-attempt-table-only label (see `build_source_attempts` below):
# this season WAS actually attempted (its manifest URL was requested, same
# as every other season — including a season this manifest lists under
# `unconfirmed_seasons`, e.g. 2024-25/2025-26 — see
# data_pipeline/sources/football_data_sources.yaml), and the source
# genuinely confirmed, by responding, that no file exists there (an HTTP
# 404 — `data_pipeline.download.ERROR_NOT_FOUND`). This is the "we tried,
# and confirmed there is no file there" case — distinct from a season that
# failed for some other reason (`FIXTURE_ONLY_VALIDATED` — a connection
# error, a non-404 HTTP error, a circuit-breaker skip, or simply not yet
# attempted in whatever retrieval log is being read) and distinct from a
# real download that succeeded (`LIVE_SOURCE_VALIDATED`/`SOURCE_NOT_USABLE`).
# Never assigned to a per-file entry built by `build_entry`/`build_live_entry`
# — those always describe a file that was actually parsed (real download or
# fixture); a `SOURCE_NOT_LISTED` row never has a file to parse, so it never
# appears in `VALID_SOURCE_LABELS`.
SOURCE_NOT_LISTED = "SOURCE_NOT_LISTED"

# The closed set of `source_label` values that may appear in the
# source-attempt table (defect 1) — the three per-file labels above, plus
# SOURCE_NOT_LISTED for a season this manifest does not yet confirm a URL
# for (defect 4).
VALID_SOURCE_ATTEMPT_LABELS = VALID_SOURCE_LABELS + (SOURCE_NOT_LISTED,)

# Identity used for every hand-crafted test-fixture entry (see
# `build_fixture_only_report`) — deliberately neutral and never a real
# league name, so a reader can never mistake a fixture-validation result
# for evidence about a real Football-Data league or season.
TEST_FIXTURE_IDENTITY = "TEST_FIXTURE"

# Download-status values a source-attempt-table row may carry. The first
# five mirror `data_pipeline.download`'s typed `error_type`s (plus a
# success indicator); `NOT_ATTEMPTED_HOST_BLOCKED` mirrors the circuit
# breaker's skip reason; `ERROR_NOT_FOUND` ("HTTP_NOT_FOUND") mirrors a
# confirmed-absent 404 (paired with `source_label: SOURCE_NOT_LISTED`);
# `NOT_YET_ATTEMPTED` means this retrieval log simply has no record for
# this (league, season) yet.
DOWNLOAD_STATUS_SUCCESS = "SUCCESS"
DOWNLOAD_STATUS_NOT_YET_ATTEMPTED = "NOT_YET_ATTEMPTED"
VALID_VALIDATION_STATUSES = ("NOT_RUN", "PASSED", "FAILED")


def _validate_label(label: str) -> None:
    if label not in VALID_SOURCE_LABELS:
        raise ValueError(
            f"label must be one of {', '.join(VALID_SOURCE_LABELS)}, got {label!r}"
        )


# Core columns without which a downloaded file cannot even identify a
# fixture or its result at all — their absence is the bright line between
# "a real file that's merely imperfect" (still LIVE_SOURCE_VALIDATED,
# individual rows rejected as usual) and "a real file that cannot support
# this pipeline's minimum needs" (SOURCE_NOT_USABLE).
_MINIMUM_REQUIRED_CORE_COLUMNS = (
    "home_team",
    "away_team",
    "full_time_result",
    "full_time_home_goals",
    "full_time_away_goals",
)


def classify_live_download(
    schema_result_core_columns_absent: list[str],
    total_rows: int,
    usable_fixtures: int,
) -> str:
    """Decide LIVE_SOURCE_VALIDATED vs. SOURCE_NOT_USABLE for a file that
    WAS actually downloaded from a real source (never called for a fixture-
    only run — that path always uses FIXTURE_ONLY_VALIDATED directly).

    Only ever invoked on real observed validation evidence from a real
    downloaded file — this is what lets a future run with real
    downloaded-but-bad data land on SOURCE_NOT_USABLE correctly, rather
    than being forced into LIVE_SOURCE_VALIDATED (which would overstate
    usability) or FIXTURE_ONLY_VALIDATED (which would understate that a
    real download actually happened)."""

    missing_identity_columns = [c for c in _MINIMUM_REQUIRED_CORE_COLUMNS if c in schema_result_core_columns_absent]
    if missing_identity_columns:
        return SOURCE_NOT_USABLE
    if total_rows == 0:
        return SOURCE_NOT_USABLE
    if usable_fixtures == 0:
        # Every single row failed validation — the file is real, but
        # nothing in it is usable for this pipeline.
        return SOURCE_NOT_USABLE
    return LIVE_SOURCE_VALIDATED


def _assemble_entry(
    league_code: str,
    league_name: str,
    season_label: str,
    csv_path: Path,
    label: str,
    schema_result,
    validation_result,
) -> dict[str, Any]:
    _validate_label(label)
    return {
        "league_code": league_code,
        "league_name": league_name,
        "season": season_label,
        "source_label": label,
        "file_path": _repo_relative_path(csv_path),
        "total_rows": validation_result.total_rows,
        "usable_fixtures": validation_result.usable_fixtures,
        "rejected_fixtures": validation_result.rejected_fixtures,
        "rejection_reason_counts": validation_result.rejection_reason_counts,
        "missingness_by_column": validation_result.missingness_by_column,
        "date_formats_observed": validation_result.date_formats_observed,
        "odds_availability": validation_result.odds_availability,
        "core_columns_absent": schema_result.core_columns_absent,
        "header": schema_result.header,
        "encoding_used": schema_result.encoding_used,
    }


def build_entry(
    league_code: str,
    league_name: str,
    season_label: str,
    csv_path: Path,
    label: str,
) -> dict[str, Any]:
    """Build one feasibility-report entry with an EXPLICIT, caller-supplied
    label (one of `VALID_SOURCE_LABELS`). Use this for `FIXTURE_ONLY_VALIDATED`
    entries (the label is known in advance — it's a fixture, not a live
    download) or when the caller has already independently decided the
    label. For a file that was actually downloaded from a real source, use
    `build_live_entry` instead, which classifies LIVE_SOURCE_VALIDATED vs.
    SOURCE_NOT_USABLE from the real validation evidence rather than
    assuming success."""

    schema_result = inspect_file(csv_path)
    validation_result = validate_file(csv_path)
    return _assemble_entry(league_code, league_name, season_label, csv_path, label, schema_result, validation_result)


def build_live_entry(
    league_code: str,
    league_name: str,
    season_label: str,
    csv_path: Path,
) -> dict[str, Any]:
    """Build one feasibility-report entry for a file that WAS actually
    downloaded from football-data.co.uk. The label is never assumed to be
    LIVE_SOURCE_VALIDATED just because a download succeeded — it is
    classified from the real schema-inspection/validation evidence via
    `classify_live_download`, landing on SOURCE_NOT_USABLE when the real
    downloaded content is too broken/incomplete to use."""

    schema_result = inspect_file(csv_path)
    validation_result = validate_file(csv_path)
    label = classify_live_download(
        schema_result.core_columns_absent,
        validation_result.total_rows,
        validation_result.usable_fixtures,
    )
    return _assemble_entry(league_code, league_name, season_label, csv_path, label, schema_result, validation_result)


def build_report(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the top-level report structure from a list of per-file
    entries built by `build_entry`. Adds a league x season x label summary
    table and aggregate totals."""

    summary_table = [
        {
            "league_code": e["league_code"],
            "league_name": e["league_name"],
            "season": e["season"],
            "source_label": e["source_label"],
            "total_rows": e["total_rows"],
            "usable_fixtures": e["usable_fixtures"],
            "rejected_fixtures": e["rejected_fixtures"],
        }
        for e in entries
    ]

    label_counts = {label: 0 for label in VALID_SOURCE_LABELS}
    for e in entries:
        label_counts[e["source_label"]] = label_counts.get(e["source_label"], 0) + 1

    # Column drift: compare consecutive seasons of the SAME league, in the
    # order entries were given (caller is expected to pass them
    # chronologically per league).
    drift_findings = []
    by_league: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_league.setdefault(e["league_code"], []).append(e)
    for league_code, league_entries in by_league.items():
        for prev, cur in zip(league_entries, league_entries[1:]):
            drift = column_drift(prev["header"], cur["header"])
            if drift["only_in_a"] or drift["only_in_b"]:
                drift_findings.append(
                    {
                        "league_code": league_code,
                        "from_season": prev["season"],
                        "to_season": cur["season"],
                        "columns_dropped": drift["only_in_a"],
                        "columns_added": drift["only_in_b"],
                    }
                )

    return {
        "summary_table": summary_table,
        "label_counts": label_counts,
        "column_drift_findings": drift_findings,
        "entries": entries,
    }


def _column_stat(canonical_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Missing-value stats for one canonical core column of one entry,
    correctly distinguishing "the column doesn't exist in this file at
    all" (100% missing, `column_present: False`) from "the column exists
    but some rows left it blank" (`missingness_by_column`'s own rate,
    `column_present: True`) — `validation.py`'s `missingness_by_column`
    reports 0.0 for a column that is simply absent (it never counted any
    row against it), which would otherwise misleadingly read as "always
    present"."""

    total_rows = entry["total_rows"]
    if canonical_id in entry["core_columns_absent"]:
        return {"column_present": False, "missing_count": total_rows, "missing_rate": 1.0 if total_rows else 0.0}
    rate = entry["missingness_by_column"].get(canonical_id, 0.0)
    return {
        "column_present": True,
        "missing_count": round(rate * total_rows),
        "missing_rate": rate,
    }


def build_compact_summary_row(entry: dict[str, Any]) -> dict[str, Any]:
    """Build one compact, per-league-season summary row (this task's
    primary reviewable evidence — see the module docstring) from one
    already-built per-file entry (`build_live_entry`'s output). Only ever
    meaningful for an entry describing a file that was ACTUALLY downloaded
    (`LIVE_SOURCE_VALIDATED` or `SOURCE_NOT_USABLE`) — a
    `FIXTURE_ONLY_VALIDATED`/`SOURCE_NOT_LISTED` row never has real file
    content to summarize this way."""

    opening = [o for o in entry["odds_availability"] if o["variant"] == "opening_or_only"]
    closing = [o for o in entry["odds_availability"] if o["variant"] == "closing"]

    return {
        "league_code": entry["league_code"],
        "league_name": entry["league_name"],
        "season": entry["season"],
        "source_label": entry["source_label"],
        "encoding_used": entry.get("encoding_used"),
        "rows": entry["total_rows"],
        "usable_fixtures": entry["usable_fixtures"],
        "rejected_fixtures": entry["rejected_fixtures"],
        "missing_kickoff_time": _column_stat("kickoff_time", entry),
        "opening_odds_present": bool(opening),
        "opening_odds_bookmakers": sorted({o["bookmaker_name"] for o in opening}),
        "closing_odds_present": bool(closing),
        "closing_odds_bookmakers": sorted({o["bookmaker_name"] for o in closing}),
        "bookmaker_coverage": sorted({o["bookmaker_prefix"] for o in entry["odds_availability"]}),
        # Universally True today for every odds column this pipeline finds
        # (schema_inspection.py's `capture_timestamp_known` is hard-coded
        # False for every finding — Football-Data carries no captured_at
        # column) — stated explicitly per row rather than only in a module
        # docstring, per this task's item 4.
        "price_capture_timestamps_unknown": True,
        "rejection_reason_counts": {k: v for k, v in entry["rejection_reason_counts"].items() if v},
    }


def build_compact_summary(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the compact per-league-season summary table for every entry
    that was actually downloaded (see `build_compact_summary_row`)."""
    return [build_compact_summary_row(e) for e in entries]


def format_compact_summary_line(row: dict[str, Any]) -> str:
    """One compact, single-line-per-row rendering of a compact summary
    row — for the raw stdout job log, where a full Markdown table is
    harder to scan."""

    opening = "Y(" + ",".join(row["opening_odds_bookmakers"]) + ")" if row["opening_odds_present"] else "N"
    closing = "Y(" + ",".join(row["closing_odds_bookmakers"]) + ")" if row["closing_odds_present"] else "N"
    kickoff = row["missing_kickoff_time"]
    rejections = ",".join(f"{k}={v}" for k, v in row["rejection_reason_counts"].items()) or "none"
    return (
        f"{row['league_code']}/{row['season']} [{row['source_label']}] "
        f"encoding={row['encoding_used']} rows={row['rows']} usable={row['usable_fixtures']} "
        f"rejected={row['rejected_fixtures']} rejections={rejections} "
        f"kickoff_missing={kickoff['missing_count']}/{row['rows']} "
        f"opening_odds={opening} closing_odds={closing} "
        f"bookmakers={','.join(row['bookmaker_coverage']) or 'none'} "
        f"price_capture_timestamps_unknown={row['price_capture_timestamps_unknown']}"
    )


def format_compact_summary_markdown(rows: list[dict[str, Any]]) -> list[str]:
    """Render the compact per-league-season summary table as Markdown, for
    `$GITHUB_STEP_SUMMARY` — this task's primary reviewable evidence, since
    it never depends on downloading the uploaded artifact."""

    lines = [
        "| League | Season | Label | Encoding | Rows | Usable | Rejected | Kickoff missing | "
        "Opening odds | Closing odds | Bookmaker coverage | Price ts unknown | Rejection reasons |",
        "|---|---|---|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| _no league-season file was actually downloaded in this run_ | | | | | | | | | | | | |")
        return lines
    for row in rows:
        opening = "Yes (" + ", ".join(row["opening_odds_bookmakers"]) + ")" if row["opening_odds_present"] else "No"
        closing = "Yes (" + ", ".join(row["closing_odds_bookmakers"]) + ")" if row["closing_odds_present"] else "No"
        kickoff = row["missing_kickoff_time"]
        rejections = ", ".join(f"{k}={v}" for k, v in row["rejection_reason_counts"].items()) or "none"
        lines.append(
            f"| {row['league_name']} ({row['league_code']}) | {row['season']} | {row['source_label']} | "
            f"{row['encoding_used']} | {row['rows']} | {row['usable_fixtures']} | {row['rejected_fixtures']} | "
            f"{kickoff['missing_count']} ({kickoff['missing_rate']:.1%}) | {opening} | {closing} | "
            f"{', '.join(row['bookmaker_coverage']) or 'none'} | {row['price_capture_timestamps_unknown']} | "
            f"{rejections} |"
        )
    return lines


def build_aggregates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the compact per-league-season summary rows two ways (this
    task's item 5): total usable/rejected fixtures per LEAGUE (summed
    across that league's seasons), and total usable/rejected fixtures per
    SEASON (summed across all 5 leagues)."""

    by_league: dict[str, dict[str, Any]] = {}
    by_season: dict[str, dict[str, Any]] = {}
    for row in rows:
        league = by_league.setdefault(
            row["league_code"],
            {"league_name": row["league_name"], "usable_fixtures": 0, "rejected_fixtures": 0, "rows": 0},
        )
        league["usable_fixtures"] += row["usable_fixtures"]
        league["rejected_fixtures"] += row["rejected_fixtures"]
        league["rows"] += row["rows"]

        season = by_season.setdefault(
            row["season"], {"usable_fixtures": 0, "rejected_fixtures": 0, "rows": 0}
        )
        season["usable_fixtures"] += row["usable_fixtures"]
        season["rejected_fixtures"] += row["rejected_fixtures"]
        season["rows"] += row["rows"]

    return {"by_league": by_league, "by_season": by_season}


def build_rejection_reason_totals(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Sum each typed rejection reason (RejectionReason.* keys, per
    `build_compact_summary_row`'s `rejection_reason_counts`) across every
    actually-downloaded league-season row — a single global total per
    reason type, so a reviewer can see e.g. how many rows overall were
    rejected for MISSING_OR_INVALID_ODDS vs DUPLICATE_FIXTURE without
    reading every per-file breakdown individually."""

    totals: dict[str, int] = {}
    for row in rows:
        for reason, count in row["rejection_reason_counts"].items():
            totals[reason] = totals.get(reason, 0) + count
    return totals


def format_rejection_reason_totals_markdown(totals: dict[str, int]) -> list[str]:
    """Render `build_rejection_reason_totals`'s output as a Markdown table."""

    lines = ["#### Typed rejection totals (summed across every downloaded league-season)", ""]
    if not totals:
        lines.append("_No rejection reason fired in any downloaded file._")
        return lines
    lines.append("| Rejection reason | Total occurrences |")
    lines.append("|---|---:|")
    for reason in sorted(totals):
        lines.append(f"| {reason} | {totals[reason]} |")
    return lines


def format_aggregates_markdown(aggregates: dict[str, Any]) -> list[str]:
    """Render `build_aggregates`'s output as two labeled Markdown tables —
    aggregate totals, clearly distinguished from the per-league-season
    detail table above them (this task's item 5)."""

    by_league = aggregates.get("by_league", {})
    by_season = aggregates.get("by_season", {})

    lines = ["#### Aggregate totals by league (summed across that league's downloaded seasons)", ""]
    lines.append("| League | Usable fixtures | Rejected fixtures | Total rows |")
    lines.append("|---|---:|---:|---:|")
    for league_code in sorted(by_league):
        agg = by_league[league_code]
        lines.append(
            f"| {agg['league_name']} ({league_code}) | {agg['usable_fixtures']} | "
            f"{agg['rejected_fixtures']} | {agg['rows']} |"
        )
    lines.append("")
    lines.append("#### Aggregate totals by season (summed across all 5 leagues)")
    lines.append("")
    lines.append("| Season | Usable fixtures | Rejected fixtures | Total rows |")
    lines.append("|---|---:|---:|---:|")
    for season in sorted(by_season):
        agg = by_season[season]
        lines.append(f"| {season} | {agg['usable_fixtures']} | {agg['rejected_fixtures']} | {agg['rows']} |")
    return lines


def _entries_markdown_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(
        "Every per-file entry below is labeled with exactly one of three defined values: "
        "`LIVE_SOURCE_VALIDATED` (a real football-data.co.uk download, run through this "
        "pipeline in this environment, that passed validation well enough to be usable), "
        "`FIXTURE_ONLY_VALIDATED` (only a hand-crafted test fixture was exercised, or the "
        "real download failed/was blocked — no real Football-Data content was ever obtained "
        "to judge this league-season one way or the other), or `SOURCE_NOT_USABLE` (a real "
        "download DID complete with real Football-Data content, but that real content fails "
        "validation badly enough to be unusable — a genuine negative finding about the "
        "source itself, never a stand-in for \"couldn't test it\", which stays "
        "`FIXTURE_ONLY_VALIDATED`). See `data_pipeline/FEASIBILITY_DECISION.md`."
    )
    lines.append("")
    lines.append(f"LIVE_SOURCE_VALIDATED rows: {report['label_counts'].get(LIVE_SOURCE_VALIDATED, 0)}")
    lines.append(f"FIXTURE_ONLY_VALIDATED rows: {report['label_counts'].get(FIXTURE_ONLY_VALIDATED, 0)}")
    lines.append(f"SOURCE_NOT_USABLE rows: {report['label_counts'].get(SOURCE_NOT_USABLE, 0)}")
    lines.append("")
    lines.append("## League x season x label summary")
    lines.append("")
    lines.append("| League | Season | Label | Total rows | Usable | Rejected |")
    lines.append("|---|---|---|---:|---:|---:|")
    for row in report["summary_table"]:
        lines.append(
            f"| {row['league_name']} ({row['league_code']}) | {row['season']} | {row['source_label']} | "
            f"{row['total_rows']} | {row['usable_fixtures']} | {row['rejected_fixtures']} |"
        )
    lines.append("")

    lines.append("## Per-file detail")
    for e in report["entries"]:
        lines.append("")
        lines.append(f"### {e['league_name']} ({e['league_code']}) — {e['season']} — `{e['source_label']}`")
        lines.append(f"- file: `{e['file_path']}`")
        lines.append(f"- total_rows: {e['total_rows']}, usable: {e['usable_fixtures']}, rejected: {e['rejected_fixtures']}")
        lines.append(f"- rejection_reason_counts: `{e['rejection_reason_counts']}`")
        lines.append(f"- date_formats_observed: `{e['date_formats_observed']}`")
        lines.append(f"- core_columns_absent: `{e['core_columns_absent']}`")
        lines.append("- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):")
        for o in e["odds_availability"]:
            lines.append(
                f"  - {o['bookmaker_name']} ({o['bookmaker_prefix']}, {o['variant']}"
                f"{', PINNACLE — treat cautiously, never auto-authoritative' if o['is_pinnacle'] else ''}): "
                f"all_three_present={o['all_three_columns_present_in_header']}, "
                f"capture_timestamp_known={o['capture_timestamp_known']}"
            )

    if report["column_drift_findings"]:
        lines.append("")
        lines.append("## Column drift between seasons")
        for d in report["column_drift_findings"]:
            lines.append(
                f"- {d['league_code']}: {d['from_season']} -> {d['to_season']}: "
                f"dropped={d['columns_dropped']}, added={d['columns_added']}"
            )
    return lines


def write_feasibility_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    """Write the REAL-source feasibility report — the source-attempt table
    (every (league, season) this pipeline actually attempted or explicitly
    marked SOURCE_NOT_LISTED, defect 1/4) plus, when any real download
    succeeded, a per-file entries breakdown built from real validation
    evidence (`build_live_entry`). This file NEVER contains the
    hand-crafted test fixtures — those live exclusively in
    `fixture_validation_report.json`/`.md` (see `write_fixture_validation_report`)."""

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = ["# Football-Data feasibility report — real source-attempt evidence", ""]
    lines.append(
        "This report covers REAL football-data.co.uk download attempts only. It never "
        "contains the hand-crafted parser-behavior test fixtures — those are reported "
        "separately in `data_pipeline/reports/fixture_validation_report.json`/`.md`, which "
        "proves parser BEHAVIOR only and must never be read as evidence about a real league "
        "or season. See `data_pipeline/FEASIBILITY_DECISION.md`."
    )
    lines.append("")

    attempts = report.get("source_attempts", [])
    attempts_summary = report.get("source_attempts_summary", {})
    lines.append("## Source-attempt table")
    lines.append("")
    lines.append(
        f"{attempts_summary.get('total_rows', len(attempts))} (league, season) rows: "
        f"one row per pair this manifest names — every real download attempt this run made "
        f"(succeeded, failed, or skipped by the circuit breaker), plus any season confirmed "
        f"genuinely absent (404) as `SOURCE_NOT_LISTED`. Never fabricated: `total_rows`/"
        "`usable_fixtures` are `null` for every row where no real file was validated."
    )
    lines.append("")
    lines.append(f"download_status counts: `{attempts_summary.get('download_status_counts', {})}`")
    lines.append(f"source_label counts: `{attempts_summary.get('source_label_counts', {})}`")
    lines.append("")
    lines.append("| League | Season | source_label | download_status | validation_status | total_rows | usable_fixtures |")
    lines.append("|---|---|---|---|---|---:|---:|")
    for row in attempts:
        lines.append(
            f"| {row['league_code']} | {row['season']} | {row['source_label']} | {row['download_status']} | "
            f"{row['validation_status']} | {row['total_rows']} | {row['usable_fixtures']} |"
        )
    lines.append("")

    lines.append("## Compact per-league-season summary (primary reviewable evidence)")
    lines.append("")
    lines.append(
        "Every row below describes a league-season file that was ACTUALLY downloaded in this "
        "run (`LIVE_SOURCE_VALIDATED` or `SOURCE_NOT_USABLE`) — this is the same table the "
        "workflow writes to `$GITHUB_STEP_SUMMARY` and prints to the job log, so it never "
        "requires downloading the uploaded artifact to review."
    )
    lines.append("")
    lines.extend(format_compact_summary_markdown(report.get("compact_summary", [])))
    lines.append("")
    lines.extend(format_aggregates_markdown(report.get("aggregates", {})))
    lines.append("")

    if report.get("entries"):
        lines.append("## Real live-download per-file detail")
        lines.append("")
        lines.extend(_entries_markdown_lines(report))
    else:
        lines.append(
            "No real download succeeded in this run, so there is no live per-file entries "
            "section below — see the source-attempt table above for the full attempt record."
        )

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_fixture_validation_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    """Write the SEPARATE fixture-validation report — the 6 hand-crafted
    fixtures' results, always identified as `TEST_FIXTURE_IDENTITY`
    (never a real league name), proving parser/validator BEHAVIOR only.
    This file must never be conflated with `feasibility_report.json`/`.md`
    (real source-attempt evidence) — see `data_pipeline/FEASIBILITY_DECISION.md`."""

    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = ["# Fixture validation report — parser-behavior evidence ONLY", ""]
    lines.append(
        "**This file proves the schema-inspection/validation code's PARSING BEHAVIOR only "
        "— it is NEVER evidence of source compatibility or dataset usability for any real "
        "league or season.** Every entry's league identity is the neutral "
        f"`{TEST_FIXTURE_IDENTITY}` (never a real league name/code), and every entry is "
        "built from a small, hand-crafted CSV checked into `tests/fixtures/football_data/` "
        "— never real downloaded Football-Data content. For the real source-attempt record, "
        "see `data_pipeline/reports/feasibility_report.json`/`.md` and "
        "`data_pipeline/FEASIBILITY_DECISION.md`."
    )
    lines.append("")
    lines.extend(_entries_markdown_lines(report))

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fixture_only_report() -> dict[str, Any]:
    """Build a report ENTIRELY from the checked-in hand-crafted fixtures
    (tests/fixtures/football_data/) — this proves parser/validator
    BEHAVIOR only, never source compatibility or dataset usability for any
    real league or season (see the module docstring and
    `data_pipeline/FEASIBILITY_DECISION.md`).

    Every entry's identity is deliberately the neutral `TEST_FIXTURE_IDENTITY`
    (never a real league name/code) so this can never be mistaken for real
    Football-Data evidence, and every entry is labeled FIXTURE_ONLY_VALIDATED.
    This report is written to its OWN file, `fixture_validation_report.json`/`.md`
    — it is never merged into `feasibility_report.json`/`.md`, which is
    reserved for the real source-attempt evidence (`build_source_attempts`)
    and any real live-download entries."""

    fixtures_dir = REPO_ROOT / "tests" / "fixtures" / "football_data"
    fixture_files = [
        ("fixture:clean_modern_season", "clean_modern_season.csv"),
        ("fixture:duplicate_fixture", "duplicate_fixture.csv"),
        ("fixture:missing_odds", "missing_odds.csv"),
        ("fixture:incomplete_three_way", "incomplete_three_way.csv"),
        ("fixture:old_date_format_season", "old_date_format_season.csv"),
        ("fixture:column_drift_2000s_season", "column_drift_2000s_season.csv"),
    ]
    entries = [
        build_entry(TEST_FIXTURE_IDENTITY, TEST_FIXTURE_IDENTITY, season, fixtures_dir / filename, FIXTURE_ONLY_VALIDATED)
        for season, filename in fixture_files
    ]
    return build_report(entries)


def build_source_attempts(
    manifest_rows: list[dict[str, Any]],
    retrieval_log: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the source-attempt table: exactly one row per (league, season)
    pair this manifest names, joining `football_data_sources.yaml` against
    `retrieval_log.json` — the real record of every download this pipeline
    actually attempted (or, since the circuit breaker, deliberately did NOT
    attempt) in this run.

    This covers every real (league, season) attempt the manifest
    describes, INCLUDING both the confirmed [first_season,
    latest_completed_season] range AND the `unconfirmed_seasons` seasons
    (e.g. 2024-25/2025-26) — `download.py`'s `run()` attempts both ranges
    through the identical download/retry/circuit-breaker logic (see its
    module docstring), so this function joins against the retrieval log the
    same way for every season the manifest names, with no special-casing by
    range. The only season-specific outcome is `SOURCE_NOT_LISTED`, which
    this function assigns only when the retrieval log actually recorded an
    attempt for that (league, season) that came back a confirmed HTTP 404
    (`data_pipeline.download.ERROR_NOT_FOUND`) — i.e. the source genuinely
    has no file there, never "this pipeline didn't try."

    `total_rows`/`usable_fixtures` are populated from real validation
    evidence ONLY when a real file was actually downloaded for that row —
    never copied from an unrelated fixture's numbers, and never fabricated
    for a row whose file was never fetched."""

    downloads_by_key = {(d["league_code"], d["season_code"]): d for d in retrieval_log.get("downloads", [])}
    failed_by_key = {(d["league_code"], d["season_code"]): d for d in retrieval_log.get("failed_attempts", [])}

    rows: list[dict[str, Any]] = []
    for manifest_row in manifest_rows:
        league_code = manifest_row["league_code"]
        league_name = manifest_row["league_name"]

        confirmed_codes = season_codes(str(manifest_row["first_season"]), str(manifest_row["latest_completed_season"]))
        unconfirmed_codes = [str(c) for c in (manifest_row.get("unconfirmed_seasons") or [])]

        for code in confirmed_codes + unconfirmed_codes:
            key = (league_code, code)
            if key in downloads_by_key:
                download = downloads_by_key[key]
                raw_path = REPO_ROOT / download["raw_path"]
                # A real file was actually downloaded — classify it from
                # real validation evidence, exactly like build_live_entry,
                # rather than assuming success just because the transfer
                # completed.
                if raw_path.exists():
                    schema_result = inspect_file(raw_path)
                    validation_result = validate_file(raw_path)
                    label = classify_live_download(
                        schema_result.core_columns_absent,
                        validation_result.total_rows,
                        validation_result.usable_fixtures,
                    )
                    rows.append(
                        {
                            "league_code": league_code,
                            "season": code,
                            "source_label": label,
                            "download_status": DOWNLOAD_STATUS_SUCCESS,
                            "validation_status": "PASSED" if label == LIVE_SOURCE_VALIDATED else "FAILED",
                            "total_rows": validation_result.total_rows,
                            "usable_fixtures": validation_result.usable_fixtures,
                        }
                    )
                else:
                    # Recorded as a successful transfer but the raw file
                    # is not present on disk (raw downloads are git-ignored
                    # — see data_pipeline/download.py's module docstring):
                    # a real file was fetched at some point but this
                    # environment cannot re-validate it now, so this is
                    # honestly FIXTURE_ONLY_VALIDATED/NOT_RUN, never a
                    # fabricated pass/fail.
                    rows.append(
                        {
                            "league_code": league_code,
                            "season": code,
                            "source_label": FIXTURE_ONLY_VALIDATED,
                            "download_status": DOWNLOAD_STATUS_SUCCESS,
                            "validation_status": "NOT_RUN",
                            "total_rows": None,
                            "usable_fixtures": None,
                        }
                    )
            elif key in failed_by_key:
                failure = failed_by_key[key]
                if failure["error_type"] == ERROR_NOT_FOUND:
                    # A real attempt was made against this exact URL and
                    # the source answered with a confirmed 404 — "we
                    # tried, and confirmed there is no file there," never
                    # "we didn't try." See SOURCE_NOT_LISTED's docstring.
                    rows.append(
                        {
                            "league_code": league_code,
                            "season": code,
                            "source_label": SOURCE_NOT_LISTED,
                            "download_status": failure["error_type"],
                            "validation_status": "NOT_RUN",
                            "total_rows": None,
                            "usable_fixtures": None,
                        }
                    )
                else:
                    rows.append(
                        {
                            "league_code": league_code,
                            "season": code,
                            "source_label": FIXTURE_ONLY_VALIDATED,
                            "download_status": failure["error_type"],
                            "validation_status": "NOT_RUN",
                            "total_rows": None,
                            "usable_fixtures": None,
                        }
                    )
            else:
                # Absent from the retrieval log entirely (e.g. the log
                # predates this league/season, or download.py has not been
                # re-run since the manifest changed) — never fabricate an
                # outcome. Applies identically whether this season is in
                # the confirmed range or `unconfirmed_seasons`: this
                # function never assumes an unconfirmed season was skipped
                # just because it is unconfirmed — it only knows that from
                # the retrieval log's own record (or lack of one).
                rows.append(
                    {
                        "league_code": league_code,
                        "season": code,
                        "source_label": FIXTURE_ONLY_VALIDATED,
                        "download_status": DOWNLOAD_STATUS_NOT_YET_ATTEMPTED,
                        "validation_status": "NOT_RUN",
                        "total_rows": None,
                        "usable_fixtures": None,
                    }
                )

    for row in rows:
        if row["source_label"] not in VALID_SOURCE_ATTEMPT_LABELS:
            raise ValueError(f"source_attempts row has an invalid source_label: {row!r}")
        if row["validation_status"] not in VALID_VALIDATION_STATUSES:
            raise ValueError(f"source_attempts row has an invalid validation_status: {row!r}")

    return rows


def summarize_source_attempts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    download_status_counts: dict[str, int] = {}
    source_label_counts: dict[str, int] = {label: 0 for label in VALID_SOURCE_ATTEMPT_LABELS}
    for row in rows:
        download_status_counts[row["download_status"]] = download_status_counts.get(row["download_status"], 0) + 1
        source_label_counts[row["source_label"]] = source_label_counts.get(row["source_label"], 0) + 1
    return {
        "total_rows": len(rows),
        "download_status_counts": download_status_counts,
        "source_label_counts": source_label_counts,
    }


def main() -> None:
    retrieval_log_path = REPO_ROOT / "data_pipeline" / "retrieval_log.json"
    manifest_rows = load_manifest(DEFAULT_MANIFEST_PATH)
    live_entries: list[dict[str, Any]] = []
    source_attempts: list[dict[str, Any]] = []

    if retrieval_log_path.exists():
        retrieval_log = json.loads(retrieval_log_path.read_text(encoding="utf-8"))
        for d in retrieval_log.get("downloads", []):
            raw_path = REPO_ROOT / d["raw_path"]
            if raw_path.exists():
                # Never assume a completed download is automatically
                # usable — build_live_entry classifies LIVE_SOURCE_VALIDATED
                # vs. SOURCE_NOT_USABLE from the real validation evidence.
                live_entries.append(
                    build_live_entry(
                        d["league_code"],
                        d["league_name"],
                        d["season_code"],
                        raw_path,
                    )
                )
        # The source-attempt table (defect 1) is built regardless of
        # whether any download succeeded — it is the honest record of
        # every (league, season) pair this manifest names, including
        # every attempt that failed/was skipped, and every confirmed-absent
        # (404) SOURCE_NOT_LISTED season.
        source_attempts = build_source_attempts(manifest_rows, retrieval_log)
    else:
        retrieval_log = {}

    report = build_report(live_entries)
    report["source_attempts"] = source_attempts
    report["source_attempts_summary"] = summarize_source_attempts(source_attempts)

    # Compact per-league-season summary + league/season aggregates (this
    # task's items 4/5) — the PRIMARY reviewable evidence, printed directly
    # to stdout (the job log) below and also stored here in
    # feasibility_report.json so the workflow's "Write evidence summary to
    # job summary" step can render the same data into
    # $GITHUB_STEP_SUMMARY without needing to download the artifact or
    # recompute anything.
    compact_summary = build_compact_summary(live_entries)
    aggregates = build_aggregates(compact_summary)
    report["compact_summary"] = compact_summary
    report["aggregates"] = aggregates

    if live_entries:
        live_count = sum(1 for e in live_entries if e["source_label"] == LIVE_SOURCE_VALIDATED)
        not_usable_count = sum(1 for e in live_entries if e["source_label"] == SOURCE_NOT_USABLE)
        note = (
            f"{len(live_entries)} league-season file(s) were actually downloaded from "
            f"football-data.co.uk in this environment: {live_count} classified "
            f"LIVE_SOURCE_VALIDATED, {not_usable_count} classified SOURCE_NOT_USABLE "
            "(real content downloaded, but too broken/incomplete to use). "
            f"See data_pipeline/retrieval_log.json and this report's source_attempts[] "
            f"({len(source_attempts)} rows) for the full attempt record. Hand-crafted "
            "parser-behavior fixture evidence is reported separately in "
            "data_pipeline/reports/fixture_validation_report.json."
        )
    else:
        attempted = retrieval_log.get("summary", {}).get("network_attempts_made")
        skipped = retrieval_log.get("summary", {}).get("skipped_host_blocked", 0)
        note = (
            "No real football-data.co.uk download succeeded in this environment "
            f"({attempted if attempted is not None else '0'} league-season file(s) actually "
            f"attempted over the network, {skipped} more skipped by the circuit breaker once "
            "a host-level denial was confirmed — see data_pipeline/retrieval_log.json's "
            "failed_attempts[]/circuit_breaker). This report's source_attempts[] "
            f"({len(source_attempts)} rows) is the real, honest per-(league, season) record — "
            "it is NEVER populated from the hand-crafted test fixtures. Those fixtures prove "
            "only the pipeline's parsing/validation logic, never real dataset usability, and "
            "are reported separately in data_pipeline/reports/fixture_validation_report.json. "
            "See data_pipeline/FEASIBILITY_DECISION.md."
        )
    report["note"] = note

    json_path = REPO_ROOT / "data_pipeline" / "reports" / "feasibility_report.json"
    markdown_path = REPO_ROOT / "data_pipeline" / "reports" / "feasibility_report.md"
    write_feasibility_report(report, json_path, markdown_path)
    print(note)
    print(f"Wrote {json_path} and {markdown_path}")

    # Primary reviewable evidence, printed directly to the job log (this
    # task's item 4) — never dependent on downloading the uploaded
    # artifact zip.
    print("")
    print(f"Compact per-league-season summary ({len(compact_summary)} row(s) actually downloaded):")
    for row in compact_summary:
        print("  " + format_compact_summary_line(row))
    if not compact_summary:
        print("  (none — no league-season file was actually downloaded in this run)")

    print("")
    print("Aggregate totals by league:")
    for league_code in sorted(aggregates["by_league"]):
        agg = aggregates["by_league"][league_code]
        print(
            f"  {agg['league_name']} ({league_code}): usable={agg['usable_fixtures']} "
            f"rejected={agg['rejected_fixtures']} rows={agg['rows']}"
        )
    print("Aggregate totals by season (across all 5 leagues):")
    for season in sorted(aggregates["by_season"]):
        agg = aggregates["by_season"][season]
        print(f"  {season}: usable={agg['usable_fixtures']} rejected={agg['rejected_fixtures']} rows={agg['rows']}")

    fixture_report = build_fixture_only_report()
    fixture_report["note"] = (
        "Every entry in this file is a hand-crafted test fixture "
        f"(tests/fixtures/football_data/), identified only as '{TEST_FIXTURE_IDENTITY}' — "
        "never a real league name. This file proves the schema-inspection/validation code's "
        "PARSING BEHAVIOR only; it is never evidence of source compatibility or dataset "
        "usability for any real league or season. See "
        "data_pipeline/reports/feasibility_report.json for the real source-attempt record."
    )
    fixture_json_path = REPO_ROOT / "data_pipeline" / "reports" / "fixture_validation_report.json"
    fixture_markdown_path = REPO_ROOT / "data_pipeline" / "reports" / "fixture_validation_report.md"
    write_fixture_validation_report(fixture_report, fixture_json_path, fixture_markdown_path)
    print(f"Wrote {fixture_json_path} and {fixture_markdown_path}")


if __name__ == "__main__":
    main()
