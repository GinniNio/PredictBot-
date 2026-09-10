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
`data_pipeline/FEASIBILITY_DECISION.md`. As of this pipeline's most recent
run, zero real downloads succeeded (see FEASIBILITY_DECISION.md), so no
report row has ever actually been assigned `SOURCE_NOT_USABLE` yet — the
label is defined and validated here so a future run against real
downloaded-but-bad data has somewhere correct to land, per its own
merits, rather than being forced into one of the other two labels.

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

from data_pipeline.schema_inspection import column_drift, inspect_file, result_to_dict as schema_to_dict
from data_pipeline.validation import result_to_dict as validation_to_dict
from data_pipeline.validation import validate_file

LIVE_SOURCE_VALIDATED = "LIVE_SOURCE_VALIDATED"
FIXTURE_ONLY_VALIDATED = "FIXTURE_ONLY_VALIDATED"
SOURCE_NOT_USABLE = "SOURCE_NOT_USABLE"

# The complete, closed set of valid `source_label` values for a feasibility
# report entry. See the module docstring above for what each one means and
# when it may (and may not) be assigned.
VALID_SOURCE_LABELS = (LIVE_SOURCE_VALIDATED, FIXTURE_ONLY_VALIDATED, SOURCE_NOT_USABLE)


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
        "file_path": str(csv_path),
        "total_rows": validation_result.total_rows,
        "usable_fixtures": validation_result.usable_fixtures,
        "rejected_fixtures": validation_result.rejected_fixtures,
        "rejection_reason_counts": validation_result.rejection_reason_counts,
        "missingness_by_column": validation_result.missingness_by_column,
        "date_formats_observed": validation_result.date_formats_observed,
        "odds_availability": validation_result.odds_availability,
        "core_columns_absent": schema_result.core_columns_absent,
        "header": schema_result.header,
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


def write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = ["# Football-Data feasibility report", ""]
    lines.append(
        "Every row below is labeled with exactly one of three defined values: "
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

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_fixture_only_report() -> dict[str, Any]:
    """Build the feasibility report entirely from the checked-in hand-
    crafted fixtures (tests/fixtures/football_data/), used when no real
    Football-Data download succeeded in this environment. Every entry is
    labeled FIXTURE_ONLY_VALIDATED."""

    fixtures_dir = REPO_ROOT / "tests" / "fixtures" / "football_data"
    fixture_files = [
        ("E0", "English Premier League (fixture proxy)", "fixture:clean_modern_season", "clean_modern_season.csv"),
        ("E0", "English Premier League (fixture proxy)", "fixture:duplicate_fixture", "duplicate_fixture.csv"),
        ("E0", "English Premier League (fixture proxy)", "fixture:missing_odds", "missing_odds.csv"),
        ("E0", "English Premier League (fixture proxy)", "fixture:incomplete_three_way", "incomplete_three_way.csv"),
        ("E0", "English Premier League (fixture proxy)", "fixture:old_date_format_season", "old_date_format_season.csv"),
        ("E0", "English Premier League (fixture proxy)", "fixture:column_drift_2000s_season", "column_drift_2000s_season.csv"),
    ]
    entries = [
        build_entry(code, name, season, fixtures_dir / filename, FIXTURE_ONLY_VALIDATED)
        for code, name, season, filename in fixture_files
    ]
    return build_report(entries)


def main() -> None:
    retrieval_log_path = REPO_ROOT / "data_pipeline" / "retrieval_log.json"
    entries: list[dict[str, Any]] = []

    if retrieval_log_path.exists():
        retrieval_log = json.loads(retrieval_log_path.read_text(encoding="utf-8"))
        for d in retrieval_log.get("downloads", []):
            raw_path = REPO_ROOT / d["raw_path"]
            if raw_path.exists():
                # Never assume a completed download is automatically
                # usable — build_live_entry classifies LIVE_SOURCE_VALIDATED
                # vs. SOURCE_NOT_USABLE from the real validation evidence.
                entries.append(
                    build_live_entry(
                        d["league_code"],
                        d["league_name"],
                        d["season_code"],
                        raw_path,
                    )
                )

    if entries:
        report = build_report(entries)
        live_count = sum(1 for e in entries if e["source_label"] == LIVE_SOURCE_VALIDATED)
        not_usable_count = sum(1 for e in entries if e["source_label"] == SOURCE_NOT_USABLE)
        note = (
            f"{len(entries)} league-season file(s) were actually downloaded from "
            f"football-data.co.uk in this environment: {live_count} classified "
            f"LIVE_SOURCE_VALIDATED, {not_usable_count} classified SOURCE_NOT_USABLE "
            "(real content downloaded, but too broken/incomplete to use). "
            "See data_pipeline/retrieval_log.json."
        )
    else:
        report = build_fixture_only_report()
        note = (
            "No real football-data.co.uk download succeeded in this environment "
            "(see data_pipeline/retrieval_log.json's failed_attempts[]) — every entry below is "
            "FIXTURE_ONLY_VALIDATED, proving only the pipeline's parsing/validation logic, "
            "never real dataset usability. (SOURCE_NOT_USABLE is a third, defined label for a "
            "real download that completes but fails validation badly — it is never assigned "
            "when, as here, no real download succeeded at all; that case is always "
            "FIXTURE_ONLY_VALIDATED.) See data_pipeline/FEASIBILITY_DECISION.md."
        )
    report["note"] = note

    json_path = REPO_ROOT / "data_pipeline" / "reports" / "feasibility_report.json"
    markdown_path = REPO_ROOT / "data_pipeline" / "reports" / "feasibility_report.md"
    write_report(report, json_path, markdown_path)
    print(note)
    print(f"Wrote {json_path} and {markdown_path}")


if __name__ == "__main__":
    main()
