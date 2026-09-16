"""Bet9ja Results-page settlement -- an operational fallback for scoring
forecasts when football-data.co.uk's own result/closing-price file is not
available.

    bet9ja-soccer-results-*.json (browser_extension/bet9ja_capture/
    results_parser.js's own envelope)
    -> normalized settlement batch
    -> ledgers.orchestration.football_data_settlement.plan_settlement
       (the SAME matching/scoring/idempotency engine, reused verbatim --
       see below)
    -> atomic settlement
    -> Brier/log-loss scoring

One command::

    python -m pcbf_calculator ingest-bet9ja-results DIR_OR_FILES \\
        --ledger-dir ledger_data --output-dir runs/settlement-session-id

**Why this exists alongside football_data_settlement.py, not instead of
it.** football-data.co.uk remains the higher-quality source WHEN its
result-and-closing-price file is reachable: it supplies real closing
1X2 odds, letting ``ledgers.forecast_ledger.score_and_append`` also
record an opening-vs-closing market comparison. Bet9ja's own Results
page (https://web.bet9ja.com/sport/results.aspx) supplies only final and
half-time scores -- confirmed via live inspection, see
``results_parser.js``'s own header comment -- never closing 1X2 odds.
This module exists so a forecast can still be SCORED (accuracy/Brier/log
loss) from Bet9ja's own results the moment football-data.co.uk is
unavailable, WITHOUT ever fabricating a closing price: every row this
module scores carries ``closing_odds: null``/``closing_odds_source:
null``, which the forecast-performance report already reports as
``missing_closing_odds`` rather than treating as "no closing-line
comparison attempted." Pre-match (opening) odds from an earlier capture
must never be relabelled as closing odds -- this module never even sees
those, since it only ever reads a results envelope.

**Reuses the existing settlement engine, never a second one.** Matching
identity (``competition_code``, ``resolved_home_team``,
``resolved_away_team``, ``scheduled_date``), the ledger-side settlement
index, atomicity/idempotency/conflict rules, and the actual
``forecast_ledger.score_and_append`` call are ALL
``football_data_settlement.plan_settlement`` and its own surrounding
commit loop, imported and reused directly -- this module only supplies
its own normalization step (``build_bet9ja_results_batch``), which reads
this JSON envelope shape instead of a football-data.co.uk CSV and
produces the identical row shape ``plan_settlement`` already expects.
"Using the existing forecast-ledger settlement engine" is a design
requirement, not an implementation detail: the two sources can never
disagree about what counts as a duplicate or a conflict, because there
is only one piece of code that decides that.

**Identity rule -- never the Bet9ja result ID.** The pre-match capture's
own ``fixture_id`` (``bxf_...``, a hash of pre-match display text -- see
``ledgers/forecast_ledger.py``) and the Results page's own short numeric
result ID (e.g. ``"2592"``) are two DIFFERENT identifiers with no
confirmed correspondence yet -- neither side's capture currently proves
they name the same underlying Bet9ja event. Matching therefore uses the
SAME canonical identity every other settlement source in this codebase
already uses: ``(competition_code, resolved_home_team,
resolved_away_team, scheduled_date)``, resolved through the identical
``adapters.soccer_1x2_elo_v1.identity`` functions
(``resolve_team``/``normalize_name``) the forecast side already used to
record that identity in the first place. ``bet9ja_result_id`` is carried
through every normalized row and every settled-forecast output entry as
supporting PROVENANCE ONLY -- never as part of the matching key, and
never written into the forecast ledger's own SCORED payload (that
schema has no field for "which settlement source produced this row";
see ``ledgers/forecast_ledger.py::score_and_append``'s own payload
shape). Once a future pre-match capture also records Bet9ja's own
native event ID (when the DOM is confirmed to expose one) AND this
module's own results envelope carries the same native ID (not the
Results-page-only numeric ID observed so far), THAT identity can become
the preferred match key -- not before, and never guessed ahead of that
evidence.

**Competition-name mapping -- confirmed evidence only.** The Results
page groups rows under its own competition header text (e.g. "Italy
Serie A"), which is NOT the same string ``adapters.soccer_1x2_elo_v1.
identity.resolve_competition`` already recognizes (that function expects
the bare league name, e.g. "Serie A", the same convention Bet9ja's
pre-match capture and football-data.co.uk's own README both use). Rather
than guess a country-prefix stripping rule for the other 4 covered
leagues' unconfirmed Results-page header text, this module keeps its own
small, explicit map (``RESULTS_COMPETITION_TEXT_TO_LEAGUE_CODE``)
containing ONLY the one header text confirmed via real inspection
(Italy Serie A, 2026-09-14 -- see
``tests/fixtures/bet9ja/results_serie_a_2026_09_14.html``). A group
whose header text is not in this map is quarantined
(``REASON_COMPETITION_NOT_COVERED``), never assumed to be one of the
other 4 leagues by pattern-matching its country name -- widen this map
only against new real Results-page evidence for each additional league,
exactly this project's own evidence-only-correction discipline.

**Time handling.** The Results page displays each fixture's start time
in a FIXED ``GMT+01:00`` offset (confirmed via live inspection, recorded
verbatim by ``results_parser.js`` as ``page_timezone`` in every envelope
it produces) -- never DST-adjusted, and never interpreted as UTC. This
module converts explicitly via ``RESULTS_PAGE_TZ`` (a fixed
``timezone(timedelta(hours=1))``), the same treatment
``bet9ja_ticket_import.py``'s own ``LAGOS_TZ`` (Africa/Lagos, also fixed
UTC+1) already gives a different Bet9ja page's own displayed time --
never a system/adapter-local timezone guess.

**Completed-result acceptance -- fail closed.** Only a row whose
``full_time_score_raw`` is a plain ``N - N`` digit pattern (Bet9ja's own
"Fin. Ris" label, confirmed) is ever scored. A row with a blank, missing,
or non-numeric full-time score (postponed, abandoned, void, or a fixture
that has not yet been played) is quarantined with a typed reason -- never
guessed at, and this module's own DOM layer (``results_parser.js``)
likewise never invents scores for a status vocabulary it hasn't observed
yet (see that file's own "NOT YET CONFIRMED" section).

**Explicit boundaries.** Forecast ledger only -- no write of any kind to
``ledgers/betting_ledger.py``. Never fabricates or backfills a closing
price from any other source. No automated model retraining, no
calibration adjustment, no PAPER/CASH promotion. No outcome or stake
decision of any kind.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ..adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from ..adapters.soccer_1x2_elo_v1.identity import normalize_name, resolve_team  # noqa: E402
from .football_data_settlement import plan_settlement  # noqa: E402


def _ensure_ledgers_importable() -> None:
    """Same fallback ``orchestration/forecast_ledger_writer.py`` and
    ``orchestration/football_data_settlement.py`` already use for
    themselves -- see either module's own copy of this helper for the
    full rationale."""

    try:
        import ledgers  # noqa: F401

        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "ledgers" / "__init__.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_ledgers_importable()

from ledgers import forecast_ledger  # noqa: E402
from ledgers.locking import LedgerLockTimeoutError, exclusive_ledger_lock  # noqa: E402
from ledgers.storage import APPENDED, DUPLICATE_SKIPPED, read_all  # noqa: E402

SCHEMA_VERSION_REPORT = "pcbf-bet9ja-results-settlement-report.v1"
SCHEMA_VERSION_SETTLED = "pcbf-bet9ja-results-settled-forecasts.v1"
SCHEMA_VERSION_UNMATCHED = "pcbf-bet9ja-results-unmatched-results.v1"
SCHEMA_VERSION_CONFLICTS = "pcbf-bet9ja-results-settlement-conflicts.v1"

MARKET_TYPE = "1X2"

# Bet9ja's own confirmed, fixed display offset for the Results page --
# see this module's own docstring "Time handling". Never DST-adjusted.
RESULTS_PAGE_TZ = timezone(timedelta(hours=1))

# Results-page competition GROUP header text -> this codebase's own
# league code -- confirmed via real evidence ONLY. See this module's own
# docstring "Competition-name mapping" for why this is not derived from
# adapters.soccer_1x2_elo_v1.identity.resolve_competition (that function
# expects the bare league name, not this page's own "Country LeagueName"
# header format) and why the other 4 covered leagues are deliberately
# absent until their own Results-page header text is confirmed.
RESULTS_COMPETITION_TEXT_TO_LEAGUE_CODE: dict[str, str] = {
    normalize_name("Italy Serie A"): "I1",
}

_START_RE = re.compile(r"^(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4}) (?P<hour>\d{2}):(?P<minute>\d{2})$")
_SCORE_RE = re.compile(r"^(?P<home>\d+)\s*-\s*(?P<away>\d+)$")

REASON_MISSING_TEAM_IDENTITY = "SETTLE_MISSING_TEAM_IDENTITY"
REASON_UNPARSEABLE_START = "SETTLE_UNPARSEABLE_START_TIME"
REASON_MISSING_FULL_TIME_SCORE = "SETTLE_MISSING_FULL_TIME_SCORE"
REASON_UNPARSEABLE_FULL_TIME_SCORE = "SETTLE_UNPARSEABLE_FULL_TIME_SCORE"
REASON_COMPETITION_NOT_COVERED = "SETTLE_COMPETITION_NOT_COVERED"
REASON_HOME_TEAM_UNRESOLVED = "SETTLE_HOME_TEAM_UNRESOLVED"
REASON_AWAY_TEAM_UNRESOLVED = "SETTLE_AWAY_TEAM_UNRESOLVED"
REASON_CONFLICTING_SOURCE_ROW = "SETTLE_CONFLICTING_SOURCE_ROW"


def discover_input_files(path: Path) -> list[Path]:
    """``path`` a single JSON file -> ``[path]``; a directory -> every
    ``*.json`` directly inside it, sorted (deterministic file order,
    matching ``football_data_settlement.discover_input_files``'s own
    contract). Never recurses into subdirectories."""

    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    return [path]


def _parse_start_to_utc_date(start_raw: str | None) -> str | None:
    """Parses ``results_parser.js``'s own ``"DD/MM/YYYY HH:MM"`` display
    text (Bet9ja's fixed ``GMT+01:00`` Results-page offset -- see this
    module's own docstring "Time handling") into a UTC calendar date
    (``YYYY-MM-DD``), matching ``scheduled_date``'s own convention
    everywhere else in this codebase (derived from ``kickoff_utc.date()``
    in UTC). Returns ``None`` -- never a guessed date -- for unparseable
    text."""

    if not start_raw:
        return None
    match = _START_RE.match(start_raw.strip())
    if not match:
        return None
    try:
        local = datetime(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
            int(match["hour"]),
            int(match["minute"]),
            tzinfo=RESULTS_PAGE_TZ,
        )
    except ValueError:
        return None
    return local.astimezone(timezone.utc).date().isoformat()


def _actual_result_from_score(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if away_goals > home_goals:
        return "A"
    return "D"


def build_bet9ja_results_batch(
    json_paths: list[Path],
    known_teams_by_league: dict[str, set[str]],
    alias_book: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Reads every ``bet9ja-soccer-results-*.json`` envelope in
    ``json_paths`` and normalizes each usable result row into
    ``football_data_settlement.plan_settlement``'s own expected row
    shape (``competition_code``, ``resolved_home_team``,
    ``resolved_away_team``, ``scheduled_date``, ``actual_result``,
    ``closing_odds`` (always ``None`` -- see this module's own docstring),
    ``closing_odds_source`` (always ``None``)), plus this source's own
    ``bet9ja_result_id`` carried through as provenance. Never matches
    against the forecast ledger itself (kept separate, exactly like
    ``football_data_settlement.build_settlement_batch``, so this function
    is testable with no ledger at all).

    Returns ``(normalized_rows, rejected_rows, parse_counts)`` with the
    identical reconciliation contract
    ``football_data_settlement.build_settlement_batch`` documents:
    ``source_rows_total == len(rejected_rows) + len(normalized_rows) +
    duplicate_source_rows_collapsed``."""

    rejected: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    source_rows_total = 0

    for json_path in json_paths:
        envelope = json.loads(json_path.read_text(encoding="utf-8"))
        results = envelope.get("results") or []

        for row_index, raw in enumerate(results):
            source_rows_total += 1
            base = {
                "source_file": str(json_path),
                "source_row_index": row_index,
                "bet9ja_result_id": raw.get("bet9ja_result_id"),
            }

            fixture_raw = (raw.get("fixture_raw") or "").strip()
            parts = fixture_raw.split(" - ")
            home_raw = parts[0].strip() if len(parts) == 2 else ""
            away_raw = parts[1].strip() if len(parts) == 2 else ""
            if not home_raw or not away_raw:
                rejected.append(
                    {
                        **base,
                        "reason": REASON_MISSING_TEAM_IDENTITY,
                        "detail": f"fixture text {fixture_raw!r} did not split into exactly two team names",
                    }
                )
                continue

            scheduled_date = _parse_start_to_utc_date(raw.get("start_raw"))
            if scheduled_date is None:
                rejected.append(
                    {**base, "reason": REASON_UNPARSEABLE_START, "detail": f"could not parse start_raw {raw.get('start_raw')!r}"}
                )
                continue

            full_time_raw = (raw.get("full_time_score_raw") or "").strip()
            if not full_time_raw:
                rejected.append(
                    {
                        **base,
                        "reason": REASON_MISSING_FULL_TIME_SCORE,
                        "detail": "no Fin. Ris score present -- postponed/abandoned/void/not yet played",
                    }
                )
                continue
            score_match = _SCORE_RE.match(full_time_raw)
            if not score_match:
                rejected.append(
                    {
                        **base,
                        "reason": REASON_UNPARSEABLE_FULL_TIME_SCORE,
                        "detail": f"full-time score text {full_time_raw!r} is not a plain N - N numeric score",
                    }
                )
                continue
            actual_result = _actual_result_from_score(int(score_match["home"]), int(score_match["away"]))

            competition_raw = (raw.get("competition_raw") or "").strip()
            league_code = RESULTS_COMPETITION_TEXT_TO_LEAGUE_CODE.get(normalize_name(competition_raw))
            if league_code is None:
                rejected.append(
                    {
                        **base,
                        "reason": REASON_COMPETITION_NOT_COVERED,
                        "detail": f"competition group {competition_raw!r} has no confirmed Results-page mapping yet",
                    }
                )
                continue

            known_teams = known_teams_by_league.get(league_code, set())
            home_result = resolve_team(home_raw, league_code, known_teams, alias_book)
            if home_result.resolved is None:
                rejected.append({**base, "reason": REASON_HOME_TEAM_UNRESOLVED, "detail": home_result.detail})
                continue
            away_result = resolve_team(away_raw, league_code, known_teams, alias_book)
            if away_result.resolved is None:
                rejected.append({**base, "reason": REASON_AWAY_TEAM_UNRESOLVED, "detail": away_result.detail})
                continue

            key = (league_code, home_result.resolved, away_result.resolved, scheduled_date)
            candidates.setdefault(key, []).append(
                {
                    **base,
                    "competition_code": league_code,
                    "home_team_raw": home_raw,
                    "away_team_raw": away_raw,
                    "resolved_home_team": home_result.resolved,
                    "resolved_away_team": away_result.resolved,
                    "scheduled_date": scheduled_date,
                    "actual_result": actual_result,
                    "full_time_score_raw": full_time_raw,
                    "half_time_score_raw": (raw.get("half_time_score_raw") or "").strip() or None,
                    "closing_odds": None,
                    "closing_odds_source": None,
                }
            )

    normalized: list[dict[str, Any]] = []
    duplicate_source_rows_collapsed = 0
    for candidate_rows in candidates.values():
        distinct_results = {r["actual_result"] for r in candidate_rows}
        if len(distinct_results) > 1:
            for row in candidate_rows:
                rejected.append(
                    {
                        **row,
                        "reason": REASON_CONFLICTING_SOURCE_ROW,
                        "detail": (
                            f"same fixture reported with actual_result values {sorted(distinct_results)} "
                            "across source rows in this batch"
                        ),
                    }
                )
            continue
        normalized.append(candidate_rows[0])
        duplicate_source_rows_collapsed += len(candidate_rows) - 1

    for index, row in enumerate(normalized):
        row["row_index"] = index

    parse_counts = {
        "source_rows_total": source_rows_total,
        "rejected_at_parse": len(rejected),
        "duplicate_source_rows_collapsed": duplicate_source_rows_collapsed,
    }
    return normalized, rejected, parse_counts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest_bet9ja_results(json_paths: list[Path], ledger_dir: Path) -> dict[str, Any]:
    """Runs the full settlement pipeline against an already-resolved list
    of Bet9ja results envelopes and an existing (or not-yet-created)
    ledger directory -- reuses ``football_data_settlement.plan_settlement``
    and the identical preflight-then-commit contract (same exclusive
    ledger lock, same atomic-whole-batch guarantee) that module's own
    ``ingest_football_data_results`` uses. See this module's own docstring
    for why the matching/scoring engine is shared rather than
    reimplemented."""

    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
    known_teams_by_league = load_known_teams_by_league()
    alias_book = load_team_alias_book()
    normalized_rows, rejected_at_parse, parse_counts = build_bet9ja_results_batch(
        json_paths, known_teams_by_league, alias_book
    )

    with exclusive_ledger_lock(ledger_path):
        plan = plan_settlement(ledger_path, normalized_rows)

        scored: list[dict[str, Any]] = []
        if not plan["conflicts"]:
            for item in plan["to_score"]:
                result = forecast_ledger.score_and_append(
                    ledger_path,
                    item["forecast_id"],
                    item["actual_result"],
                    closing_odds=item["closing_odds"],
                    closing_odds_source=item["closing_odds_source"],
                )
                if result.status == APPENDED:
                    scored.append(
                        {
                            **item,
                            "brier_score": result.record["payload"]["brier_score"],
                            "log_loss": result.record["payload"]["log_loss"],
                        }
                    )
                elif result.status == DUPLICATE_SKIPPED:  # pragma: no cover -- preflight already ruled this out
                    plan["duplicate_skipped"].append(item)
                else:  # pragma: no cover -- preflight already ruled this out
                    raise AssertionError(
                        f"Policy violation: preflight found no conflict for forecast_id={item['forecast_id']!r}, "
                        "but the real append returned CONFLICT. This is a bug in this module's preflight logic."
                    )

        total_ledger_records = len(read_all(ledger_path))

    matched_row_indices = {
        e["row_index"] for e in (plan["to_score"] + plan["duplicate_skipped"] + plan["conflicts"] + plan["not_scorable"])
    }
    unmatched_row_indices = {e["row_index"] for e in plan["unmatched"]}
    rows_reconciled = (
        len(matched_row_indices | unmatched_row_indices) == len(normalized_rows)
        and matched_row_indices.isdisjoint(unmatched_row_indices)
    )

    status = "CONFLICT" if plan["conflicts"] else "OK"
    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "status": status,
        "source_files": [str(p) for p in json_paths],
        "note": (
            "Reuses football_data_settlement.plan_settlement verbatim (the same matching/"
            "scoring/idempotency engine every settlement source in this codebase shares) -- "
            "only this module's own row normalization (bet9ja-soccer-results-*.json parsing) "
            "differs. closing_odds is always null here -- the Results page supplies no closing "
            "1X2 odds; see this module's own docstring. Preflights the whole batch before any "
            "ledger mutation; a CONFLICT status means nothing was written to the ledger this run."
        ),
        "counts": {
            **parse_counts,
            "rows_matchable": len(normalized_rows),
            "rows_unmatched_no_forecast": len(plan["unmatched"]),
            "forecast_matches_not_scorable": len(plan["not_scorable"]),
            "forecast_matches_scored": len(scored),
            "forecast_matches_duplicate_skipped": len(plan["duplicate_skipped"]),
            "forecast_matches_conflicted": len(plan["conflicts"]),
            "total_ledger_records": total_ledger_records,
        },
        "rows_reconciled": rows_reconciled,
    }

    settled_forecasts = {"schema_version": SCHEMA_VERSION_SETTLED, "settled": scored}
    unmatched_results = {
        "schema_version": SCHEMA_VERSION_UNMATCHED,
        "unmatched": rejected_at_parse + plan["unmatched"] + plan["not_scorable"],
    }
    settlement_conflicts = {"schema_version": SCHEMA_VERSION_CONFLICTS, "conflicts": plan["conflicts"]}

    return {
        "settlement_report": report,
        "settled_forecasts": settled_forecasts,
        "unmatched_results": unmatched_results,
        "settlement_conflicts": settlement_conflicts,
    }


def run_settlement_session(input_path: Path, ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Discovers Bet9ja results JSON files at ``input_path`` (a file or a
    directory -- see ``discover_input_files``), runs the full settlement
    pipeline, and writes the same four output files
    ``football_data_settlement.run_settlement_session`` writes (empty
    lists, never omitted files, when a bucket has nothing in it)."""

    json_paths = discover_input_files(input_path)
    result = ingest_bet9ja_results(json_paths, ledger_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "settlement-report.json", result["settlement_report"])
    _write_json(output_dir / "settled-forecasts.json", result["settled_forecasts"])
    _write_json(output_dir / "unmatched-results.json", result["unmatched_results"])
    _write_json(output_dir / "settlement-conflicts.json", result["settlement_conflicts"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator ingest-bet9ja-results",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="A bet9ja-soccer-results-*.json file, or a directory of them")
    parser.add_argument("--ledger-dir", type=Path, required=True, help="Existing (or new) forecast-ledger directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the four output files into")
    args = parser.parse_args(argv)

    try:
        result = run_settlement_session(args.input, args.ledger_dir, args.output_dir)
    except LedgerLockTimeoutError as exc:
        print(f"LOCKED: {exc}")
        return 2

    counts = result["settlement_report"]["counts"]
    status = result["settlement_report"]["status"]
    print(
        f"{status}: {counts['forecast_matches_scored']} scored, "
        f"{counts['forecast_matches_duplicate_skipped']} duplicate-skipped, "
        f"{counts['forecast_matches_conflicted']} conflicted, "
        f"{counts['rows_unmatched_no_forecast']} unmatched, "
        f"{counts['forecast_matches_not_scorable']} not scorable, "
        f"{counts['rejected_at_parse']} rejected at parse "
        f"({counts['source_rows_total']} source rows) -> {args.output_dir}"
    )
    return 2 if status == "CONFLICT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
