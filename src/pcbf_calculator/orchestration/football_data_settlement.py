"""Football-data.co.uk results and closing-odds forecast-ledger settlement.

    football-data.co.uk files
    -> normalized settlement batch
    -> deterministic forecast-ledger matching
    -> atomic settlement
    -> Brier/log-loss scoring
    -> closing-price comparison

One command::

    python -m pcbf_calculator ingest-football-data-results DIR_OR_FILES \\
        --ledger-dir ledger_data --output-dir runs/settlement-session-id

**Why football-data.co.uk, not Bet9ja's own settled-bets capture.** Bet9ja's
settled-bets capture (``browser_extension/bet9ja_capture/settled_bets_parser.js``)
remains the source for TICKETS ACTUALLY PLACED -- ``docs/LEDGER_DAILY_WORKFLOW.md``
already treats forecast scoring and ticket profit/loss as distinct
processes. It cannot settle every research forecast, because a forecast
that was never placed as a ticket never appears in betting history at all
-- and this pipeline's own research queue (``run-bet9ja-research``) exists
precisely to surface forecasts on fixtures that may never be bet on.
football-data.co.uk covers the five leagues ``soccer_1x2_elo_v1`` actually
forecasts and supplies both match results and, for many rows, real
closing-price columns, independent of whether any given forecast was ever
placed. Bet9ja ticket settlement stays a separate, later bridge using the
existing settled-bets capture and ``ledgers/betting_ledger.py`` -- this
module never touches that ledger at all.

**Matching key -- never team names alone.**
``(competition_code, resolved_home_team, resolved_away_team,
scheduled_date, market_type)``. On the settlement side, ``competition_code``
is football-data.co.uk's own ``Div`` column (already that source's native
league code, e.g. ``"E0"``) and ``resolved_home_team``/``resolved_away_team``
are canonicalized through the IDENTICAL resolution
``soccer_1x2_elo_v1``'s own ``forecast()`` uses at forecast time
(``adapters.soccer_1x2_elo_v1.identity.resolve_team``, against that
adapter's own live-ratings known-team set and checked-in alias book --
see ``adapter.load_known_teams_by_league``/``load_team_alias_book``). On
the forecast side, the same fields were recorded verbatim by
``forecast_ledger_writer.py`` from the adapter's own
``ForecastResult.settlement_identity`` (see that dataclass field's own
docstring for why). A row or a forecast that never carries all four key
fields is never guessed into a match -- it stays unresolved with a typed
reason.

**Closing-odds rules.**

- Only a column set ``data_pipeline.schema_inspection`` itself labels
  ``"closing"`` is ever used -- opening/pre-closing odds are never
  substituted for a missing closing price.
- Recorded only when all three (H/D/A) are present and numeric-positive
  for that specific row (a file can declare a closing column set in its
  header while a given row still has a gap).
- Preference order when a file supplies more than one usable closing-odds
  source for a row: Pinnacle (``PSC*``, or the legacy ``PC*`` prefix) >
  every other single-bookmaker closing column set (in
  ``schema_inspection.KNOWN_BOOKMAKER_PREFIXES``'s own declared order) >
  market-aggregate closing (``AvgC*`` then ``MaxC*``). Pinnacle is
  preferred as a policy choice (this repository's own recognized sharp
  benchmark), never asserted as ground truth -- see
  ``data_pipeline/NORMALIZED_SCHEMA.md``'s own "never auto-authoritative"
  principle. ``closing_odds_source`` always names exactly which columns
  were used, or is ``null`` alongside a ``null`` ``closing_odds``.
- A forecast is still scored when no closing-odds source is usable for
  its row -- ``closing_odds``/``closing_odds_source`` stay ``null``,
  never fabricated and never backfilled from opening odds.
- No network access, no retrieval, no invented prices -- this module only
  ever reads the files given on its own command line.

**Atomicity, idempotency, concurrency.** The whole batch (every parsed,
matched row) is preflighted against the ledger's current on-disk content
before a single ``SCORED`` event is written: if any matched forecast
already carries a ``SCORED`` event with a DIFFERENT
``actual_result``/``closing_odds``, the ENTIRE batch is aborted -- nothing
is written to the ledger -- exactly the same "any conflict blocks the
whole atomic batch, never a partial write" contract
``orchestration/forecast_ledger_writer.py``'s own ``write_batch`` already
established for ``RECORDED`` events, reusing the identical OS-level
exclusive lock (``ledgers.locking.exclusive_ledger_lock``) for this
module's entire preflight-then-commit sequence. Re-running this module
against the same files is a safe, idempotent no-op (every already-scored
forecast comes back ``DUPLICATE_SKIPPED``).

**Explicit boundaries (this PR).** Forecast ledger only -- no write of any
kind to ``ledgers/betting_ledger.py``. No Bet9ja capture/scraping changes.
No automated model retraining. No calibration adjustments. No
``PAPER``/``CASH`` promotion. No outcome or stake decision of any kind --
this module only ever appends ``SCORED`` events to forecasts that already
exist, reporting exactly what happened, never recommending anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import zip_longest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.schema_inspection import (  # noqa: E402
    KNOWN_BOOKMAKER_PREFIXES,
    inspect_header,
    read_csv_rows_with_encoding,
)
from data_pipeline.validation import VALID_RESULT_LABELS, parse_date  # noqa: E402

from ..adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from ..adapters.soccer_1x2_elo_v1.identity import COMPETITION_NAME_TO_LEAGUE_CODE, resolve_team  # noqa: E402


def _ensure_ledgers_importable() -> None:
    """Same fallback ``orchestration/forecast_ledger_writer.py`` already
    uses for itself -- see that module's own copy of this helper for the
    full rationale. Duplicated here (rather than imported) because it is a
    path-setup side effect that must run before this module's own
    top-level ``from ledgers import ...`` below, independent of whether
    ``forecast_ledger_writer`` happens to have been imported yet."""

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
from ledgers.storage import APPENDED, DUPLICATE_SKIPPED, all_entity_ids, latest_state, read_all  # noqa: E402

SCHEMA_VERSION_REPORT = "pcbf-football-data-settlement-report.v1"
SCHEMA_VERSION_SETTLED = "pcbf-football-data-settled-forecasts.v1"
SCHEMA_VERSION_UNMATCHED = "pcbf-football-data-unmatched-results.v1"
SCHEMA_VERSION_CONFLICTS = "pcbf-football-data-settlement-conflicts.v1"

MARKET_TYPE = "1X2"
KNOWN_LEAGUE_CODES = frozenset(COMPETITION_NAME_TO_LEAGUE_CODE.values())

# Preference order for which closing-odds column set to record when a file
# supplies more than one usable one for a row -- see this module's own
# docstring "Closing-odds rules" for why. "PS"/"P" (Pinnacle, current and
# legacy column prefixes) checked first; every other bookmaker in
# schema_inspection's own declared order after that.
_CLOSING_BOOKMAKER_PRIORITY = ("PS", "P") + tuple(
    p for p in KNOWN_BOOKMAKER_PREFIXES if p not in ("PS", "P")
)

REASON_MISSING_TEAM_IDENTITY = "SETTLE_MISSING_TEAM_IDENTITY"
REASON_UNPARSEABLE_DATE = "SETTLE_UNPARSEABLE_DATE"
REASON_MISSING_RESULT = "SETTLE_MISSING_RESULT"
REASON_INVALID_RESULT_LABEL = "SETTLE_INVALID_RESULT_LABEL"
REASON_COMPETITION_NOT_COVERED = "SETTLE_COMPETITION_NOT_COVERED"
REASON_HOME_TEAM_UNRESOLVED = "SETTLE_HOME_TEAM_UNRESOLVED"
REASON_AWAY_TEAM_UNRESOLVED = "SETTLE_AWAY_TEAM_UNRESOLVED"
REASON_CONFLICTING_SOURCE_ROW = "SETTLE_CONFLICTING_SOURCE_ROW"
REASON_NO_MATCHING_FORECAST = "SETTLE_NO_MATCHING_FORECAST"
REASON_NO_SCORABLE_PROBABILITIES = "SETTLE_NO_SCORABLE_PROBABILITIES"


def discover_input_files(path: Path) -> list[Path]:
    """``path`` a single CSV file -> ``[path]``; a directory -> every
    ``*.csv`` directly inside it, sorted (deterministic file order, which
    matters for this module's own "first-encountered row wins" duplicate
    handling -- see ``build_settlement_batch``). Never recurses into
    subdirectories -- an explicit, narrow scope rather than an implicit
    "find every CSV anywhere" that could pick up an unrelated file."""

    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    return [path]


def _is_numeric_positive(raw: Any) -> bool:
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    try:
        value = float(text)
    except ValueError:
        return False
    return value > 0


def _extract_three_way(row: dict[str, Any], home_col: str, draw_col: str, away_col: str) -> dict[str, float] | None:
    values: dict[str, float] = {}
    for outcome, col in (("H", home_col), ("D", draw_col), ("A", away_col)):
        raw = row.get(col)
        if not _is_numeric_positive(raw):
            return None
        values[outcome] = float(raw)
    return values


def select_closing_odds(
    row: dict[str, Any],
    odds_findings: list[Any],
    aggregate_findings: list[dict[str, Any]],
) -> tuple[dict[str, float] | None, str | None]:
    """Picks the one closing-odds source to record for this row, per this
    module's own docstring "Closing-odds rules" -- never opening/
    pre-closing odds, and only a column set that is fully priced (H, D,
    and A all present and numeric-positive) for THIS row, even if the
    file's header declares it. Returns ``(None, None)`` when nothing
    usable is found -- never a fabricated or backfilled price."""

    closing_by_prefix = {f.bookmaker_prefix: f for f in odds_findings if f.variant == "closing"}
    for prefix in _CLOSING_BOOKMAKER_PRIORITY:
        finding = closing_by_prefix.get(prefix)
        if finding is None:
            continue
        odds = _extract_three_way(row, finding.home_col, finding.draw_col, finding.away_col)
        if odds is not None:
            name = KNOWN_BOOKMAKER_PREFIXES[prefix]["name"]
            return odds, f"{finding.home_col}/{finding.draw_col}/{finding.away_col} ({name} closing)"

    for agg_prefix, label in (("Avg", "market average"), ("Max", "market maximum")):
        agg = next(
            (a for a in aggregate_findings if a["prefix"] == agg_prefix and a["variant"] == "closing"),
            None,
        )
        if agg is None:
            continue
        odds = _extract_three_way(row, agg["home_col"], agg["draw_col"], agg["away_col"])
        if odds is not None:
            return odds, f"{agg['home_col']}/{agg['draw_col']}/{agg['away_col']} ({label} closing)"

    return None, None


def build_settlement_batch(
    csv_paths: list[Path],
    known_teams_by_league: dict[str, set[str]],
    alias_book: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Reads every file in ``csv_paths`` and normalizes each usable row
    into this module's own settlement-row shape (``competition_code``,
    ``resolved_home_team``, ``resolved_away_team``, ``scheduled_date``,
    ``actual_result``, ``closing_odds``, ``closing_odds_source``), never
    matching the forecast ledger itself (see ``plan_settlement`` for that
    -- kept separate so this function can be tested with no ledger at
    all).

    Returns ``(normalized_rows, rejected_rows, parse_counts)``.
    ``rejected_rows`` carries a typed ``reason`` for every source row this
    function could not normalize, INCLUDING a row later found to
    genuinely conflict with another row's reported result for the exact
    same fixture within this same batch (``REASON_CONFLICTING_SOURCE_ROW``
    -- fail closed, per this module's own docstring: never guess which of
    two disagreeing source rows is correct). A row that is an EXACT repeat
    of an already-accepted row (legitimate re-download / overlapping
    season-boundary files) is silently collapsed to the first-encountered
    occurrence (deterministic by ``csv_paths``' own sort order) --
    ``parse_counts["duplicate_source_rows_collapsed"]`` accounts for every
    such extra occurrence, so every raw source row is still accounted for
    exactly once: ``source_rows_total == len(rejected_rows) +
    len(normalized_rows) + duplicate_source_rows_collapsed``.
    """

    rejected: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    source_rows_total = 0

    for csv_path in csv_paths:
        rows, _encoding = read_csv_rows_with_encoding(csv_path)
        header = rows[0] if rows else []
        data_rows = rows[1:]
        shape = inspect_header(header)
        core = shape["core_columns_present"]
        div_col = core.get("league_division")
        home_col = core.get("home_team")
        away_col = core.get("away_team")
        date_col = core.get("fixture_date")
        ftr_col = core.get("full_time_result")
        odds_findings = shape["odds_columns_found"]
        aggregate_findings = shape["market_aggregate_columns_found"]

        for row_index, raw_row in enumerate(data_rows):
            source_rows_total += 1
            row = {key: value for key, value in zip_longest(header, raw_row, fillvalue=None) if key is not None}
            base = {"source_file": str(csv_path), "source_row_index": row_index}

            div = (row.get(div_col) or "").strip() if div_col else ""
            home_raw = (row.get(home_col) or "").strip() if home_col else ""
            away_raw = (row.get(away_col) or "").strip() if away_col else ""
            date_raw = (row.get(date_col) or "").strip() if date_col else ""
            result_raw = (row.get(ftr_col) or "").strip().upper() if ftr_col else ""

            if not home_raw or not away_raw:
                rejected.append({**base, "reason": REASON_MISSING_TEAM_IDENTITY, "detail": "home_team or away_team blank"})
                continue

            date_iso, _date_fmt = parse_date(date_raw)
            if date_iso is None:
                rejected.append(
                    {**base, "reason": REASON_UNPARSEABLE_DATE, "detail": f"could not parse date {date_raw!r}"}
                )
                continue

            if not result_raw:
                # Football-Data leaves FTR blank for a match that never
                # completed normally (postponed, abandoned, not yet
                # played) -- never guessed, never scored.
                rejected.append({**base, "reason": REASON_MISSING_RESULT, "detail": "full-time result column blank"})
                continue
            if result_raw not in VALID_RESULT_LABELS:
                rejected.append(
                    {**base, "reason": REASON_INVALID_RESULT_LABEL, "detail": f"result label {result_raw!r} not in H/D/A"}
                )
                continue

            if div not in KNOWN_LEAGUE_CODES:
                rejected.append(
                    {
                        **base,
                        "reason": REASON_COMPETITION_NOT_COVERED,
                        "detail": f"Div {div!r} is not one of the 5 covered leagues {sorted(KNOWN_LEAGUE_CODES)}",
                    }
                )
                continue

            known_teams = known_teams_by_league.get(div, set())
            home_result = resolve_team(home_raw, div, known_teams, alias_book)
            if home_result.resolved is None:
                rejected.append({**base, "reason": REASON_HOME_TEAM_UNRESOLVED, "detail": home_result.detail})
                continue
            away_result = resolve_team(away_raw, div, known_teams, alias_book)
            if away_result.resolved is None:
                rejected.append({**base, "reason": REASON_AWAY_TEAM_UNRESOLVED, "detail": away_result.detail})
                continue

            closing_odds, closing_odds_source = select_closing_odds(row, odds_findings, aggregate_findings)

            key = (div, home_result.resolved, away_result.resolved, date_iso)
            candidates.setdefault(key, []).append(
                {
                    **base,
                    "competition_code": div,
                    "home_team_raw": home_raw,
                    "away_team_raw": away_raw,
                    "resolved_home_team": home_result.resolved,
                    "resolved_away_team": away_result.resolved,
                    "scheduled_date": date_iso,
                    "actual_result": result_raw,
                    "closing_odds": closing_odds,
                    "closing_odds_source": closing_odds_source,
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


def _index_ledger_by_settlement_identity(
    records: list[dict[str, Any]],
) -> tuple[dict[tuple[str, str, str, str], list[str]], dict[str, dict[str, Any]]]:
    """One current-state pass per forecast_id (reusing
    ``ledgers.storage.all_entity_ids``/``latest_state`` -- never a second,
    separately-maintained notion of "current state"), indexed by the same
    ``(competition_code, resolved_home_team, resolved_away_team,
    scheduled_date)`` key ``build_settlement_batch`` produces on the
    settlement side. A forecast whose RECORDED event never carried a full
    settlement identity (any of the four fields ``None``) or whose
    ``market_type`` is not ``"1X2"`` is excluded from the index entirely
    -- never guessed into a match."""

    index: dict[tuple[str, str, str, str], list[str]] = {}
    states: dict[str, dict[str, Any]] = {}
    for forecast_id in all_entity_ids(records, "forecast_id"):
        state = latest_state(records, "forecast_id", forecast_id)
        states[forecast_id] = state
        if state.get("market_type") != MARKET_TYPE:
            continue
        key = (
            state.get("competition_code"),
            state.get("resolved_home_team"),
            state.get("resolved_away_team"),
            state.get("scheduled_date"),
        )
        if None in key:
            continue
        index.setdefault(key, []).append(forecast_id)
    return index, states


def plan_settlement(ledger_path: Path, normalized_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Preflights every normalized row against the ledger's CURRENT
    on-disk content -- no mutation here. Every entry in every returned
    list carries the originating row's own ``row_index`` (see
    ``build_settlement_batch``), so a caller can reconcile "every
    normalized row ends up in exactly one of: unmatched, or contributing
    at least one entry across to_score/duplicate_skipped/conflicts/
    not_scorable" without assuming a row matches at most one forecast_id
    (it can legitimately match more than one, e.g. two distinct
    ``model_version``s forecasting the very same fixture)."""

    records = read_all(ledger_path)
    index, states = _index_ledger_by_settlement_identity(records)

    to_score: list[dict[str, Any]] = []
    duplicate_skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    not_scorable: list[dict[str, Any]] = []

    for row in normalized_rows:
        key = (row["competition_code"], row["resolved_home_team"], row["resolved_away_team"], row["scheduled_date"])
        forecast_ids = index.get(key)
        if not forecast_ids:
            unmatched.append({**row, "reason": REASON_NO_MATCHING_FORECAST})
            continue
        for forecast_id in forecast_ids:
            state = states[forecast_id]
            if state.get("model_probabilities") is None:
                not_scorable.append(
                    {
                        **row,
                        "forecast_id": forecast_id,
                        "reason": REASON_NO_SCORABLE_PROBABILITIES,
                        "stop_reason": state.get("stop_reason"),
                    }
                )
                continue
            if "SCORED" in state.get("_event_types_seen", []):
                same_result = state.get("actual_result") == row["actual_result"]
                same_odds = state.get("closing_odds") == row["closing_odds"]
                entry = {**row, "forecast_id": forecast_id}
                if same_result and same_odds:
                    duplicate_skipped.append(entry)
                else:
                    conflicts.append(
                        {
                            **entry,
                            "existing_actual_result": state.get("actual_result"),
                            "existing_closing_odds": state.get("closing_odds"),
                            "new_actual_result": row["actual_result"],
                            "new_closing_odds": row["closing_odds"],
                        }
                    )
                continue
            to_score.append({**row, "forecast_id": forecast_id})

    return {
        "to_score": to_score,
        "duplicate_skipped": duplicate_skipped,
        "conflicts": conflicts,
        "unmatched": unmatched,
        "not_scorable": not_scorable,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest_football_data_results(csv_paths: list[Path], ledger_dir: Path) -> dict[str, Any]:
    """Runs the full settlement pipeline against an already-resolved list
    of CSV files and an existing (or not-yet-created) ledger directory.
    Performs no output-file I/O itself (see ``run_settlement_session`` for
    that) so it can be tested/composed directly.

    Holds the SAME exclusive ledger lock
    (``ledgers.locking.exclusive_ledger_lock``)
    ``orchestration/forecast_ledger_writer.py``'s own ``write_batch`` uses,
    for this call's ENTIRE preflight-then-commit sequence -- a concurrent
    ``run-bet9ja-research --ledger-dir`` RECORDED-event write and a
    concurrent settlement run against the same ledger directory serialize
    on this one lock, never both observing the same stale on-disk state.
    """

    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
    known_teams_by_league = load_known_teams_by_league()
    alias_book = load_team_alias_book()
    normalized_rows, rejected_at_parse, parse_counts = build_settlement_batch(
        csv_paths, known_teams_by_league, alias_book
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
        "source_files": [str(p) for p in csv_paths],
        "note": (
            "Reuses data_pipeline's own CSV schema inspection/date parsing and "
            "adapters.soccer_1x2_elo_v1's own identity resolution verbatim -- no "
            "validation or canonicalization logic is reimplemented here. Preflights the "
            "whole batch before any ledger mutation; a CONFLICT status means nothing was "
            "written to the ledger this run (see settlement-conflicts.json)."
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

    settled_forecasts = {
        "schema_version": SCHEMA_VERSION_SETTLED,
        "settled": scored,
    }
    unmatched_results = {
        "schema_version": SCHEMA_VERSION_UNMATCHED,
        "unmatched": rejected_at_parse + plan["unmatched"] + plan["not_scorable"],
    }
    settlement_conflicts = {
        "schema_version": SCHEMA_VERSION_CONFLICTS,
        "conflicts": plan["conflicts"],
    }

    return {
        "settlement_report": report,
        "settled_forecasts": settled_forecasts,
        "unmatched_results": unmatched_results,
        "settlement_conflicts": settlement_conflicts,
    }


def run_settlement_session(input_path: Path, ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Discovers CSV files at ``input_path`` (a file or a directory -- see
    ``discover_input_files``), runs the full settlement pipeline, and
    writes the four output files this module always produces (empty
    lists, never omitted files, when a bucket has nothing in it -- same
    convention ``bet9ja_research_session.py`` already uses for its own
    four outputs)."""

    csv_paths = discover_input_files(input_path)
    result = ingest_football_data_results(csv_paths, ledger_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "settlement-report.json", result["settlement_report"])
    _write_json(output_dir / "settled-forecasts.json", result["settled_forecasts"])
    _write_json(output_dir / "unmatched-results.json", result["unmatched_results"])
    _write_json(output_dir / "settlement-conflicts.json", result["settlement_conflicts"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator ingest-football-data-results",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="A football-data.co.uk CSV file, or a directory of them")
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
