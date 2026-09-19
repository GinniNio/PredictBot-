"""Governed manual-results settlement -- a documented, corroboration-gated
fallback for scoring forecasts from a human-supplied results file when
BOTH football-data.org (the primary, live-API settlement source -- see
``orchestration/football_data_org_settlement.py``) AND football-data.co.uk
(``orchestration/football_data_settlement.py``) cannot settle a fixture.

    manual-results-*.json (this module's own documented input schema --
    see ``schemas/manual_results_input.v1.schema.json``)
    -> structural schema validation
    -> per-result business-rule checks (completion, corroboration,
       cross-source score agreement)
    -> adapters.soccer_1x2_elo_v1.identity's own competition/team
       resolution (never a second, separately-maintained mapping)
    -> football_data_settlement.plan_settlement (the SAME matching/
       scoring/idempotency engine every settlement source in this
       codebase reuses verbatim)
    -> DRY RUN by default: four reports, zero ledger writes
    -> --confirm: the same plan, committed under the same exclusive lock

One command::

    python -m pcbf_calculator ingest-manual-results INPUT_FILES \\
        --ledger-dir ledger_data --output-dir runs/settlement-session-id \\
        [--confirm]

**Why this exists at all, and when to reach for it.** football-data.org
queries live and covers a result the moment it is final, with zero
manual file handling -- it is, and stays, the PRIMARY settlement route.
football-data.co.uk's own downloadable file is the secondary route when
a real closing price matters. This module is the LAST resort: a human
(or an LLM acting on a human's behalf) has independently looked up a
result some other way and wants it recorded, for a fixture the two
automated sources above genuinely could not settle (not yet published,
league not covered by either feed, or some other confirmed gap). Nothing
in this module can tell whether the automated sources were tried first
-- that ordering is an operational discipline enforced by the operator
invoking this command, documented here and in this repository's own
README, never by code.

**The corroboration gate -- this module's entire reason for existing
separately from a plain "trust me" results file.** Every result entry
must carry one or more ``sources``. Each source records ``source_name``,
``source_url``, ``retrieved_at_utc``, ``evidence_hash`` (a content hash
of whatever the human/LLM actually retrieved -- a saved page snapshot, a
screenshot, an API response body -- so the evidence itself is at least
in principle re-checkable later, not just asserted), ``authoritative``
(a human judgment call about whether this ONE source alone is trustworthy
enough, e.g. the competition's own official site), and its own
independently-reported ``reported_score``. A result is accepted only
when:

- at least one source is marked ``authoritative`` and its
  ``reported_score`` matches the result's own top-level ``final_score``,
  OR
- at least two sources with DISTINCT ``source_name`` values both report a
  ``reported_score`` matching the top-level ``final_score``.

ANY source (authoritative or not) whose own ``reported_score`` disagrees
with the top-level ``final_score`` fails the whole result closed
(``REASON_SOURCE_SCORE_DISAGREEMENT``) -- corroborating sources are never
averaged, voted on, or silently dropped to make a majority; a genuine
disagreement anywhere is reported, never resolved by guessing which
source is right. A result with fewer than the required corroboration
(one non-authoritative source alone, or zero sources -- caught by the
schema's own ``minItems: 1``) is refused as
``REASON_INSUFFICIENT_CORROBORATION``, never accepted on trust.

**Never expands model coverage.** Competition resolution reuses
``adapters.soccer_1x2_elo_v1.identity.resolve_competition`` completely
unmodified -- the same fixed 5-league allowlist every forecast and every
other settlement source in this codebase already uses. A results file
naming a competition outside that allowlist is rejected
(``REASON_COMPETITION_NOT_COVERED``), exactly like every other settlement
source; this module has no code path that could widen coverage, since it
never defines its own competition mapping at all. Team resolution reuses
``identity.resolve_team`` unmodified too -- exact match only, never
fuzzy; a name that doesn't exactly match a real team in the shipped
artifact's own ratings (or a checked-in alias) is refused
(``REASON_HOME_TEAM_UNRESOLVED``/``REASON_AWAY_TEAM_UNRESOLVED``), never
guessed at.

**Matches only forecasts already in the ledger.** Reuses
``football_data_settlement.plan_settlement`` verbatim for the actual
ledger-matching/scoring/idempotency/conflict decision -- the identical
``(competition_code, resolved_home_team, resolved_away_team,
scheduled_date)`` join key, the identical duplicate-vs-conflict rules,
the identical one-time-SCORED-transition guarantee. A result with no
matching RECORDED forecast is reported in ``unmatched-results.json``,
never invented as a new forecast -- this module never writes to
``ledgers/forecast_ledger.py``'s ``RECORDED`` event type, only ``SCORED``.

**Provenance -- recorded in this command's own reports, never inside the
forecast ledger's own SCORED payload.** ``ledgers/schemas/
forecast_ledger.v1.schema.json``'s ``SCORED`` event has no field for
"which settlement source produced this row" -- the same design choice
``bet9ja_results_settlement.py``'s own ``bet9ja_result_id`` already
makes, for the identical reason (the ledger's own schema is shared by
every settlement source; adding a source-specific field to it for one
caller would be a real, cross-cutting schema change, out of scope here).
Every scored entry in ``settled-forecasts.json`` instead carries
``settlement_basis: "MANUAL_VERIFIED"`` and the full ``manual_sources``
evidence list (name/url/retrieval time/evidence hash/authoritative flag
for every corroborating source), so the evidence trail is fully
recorded and auditable from this command's own output, exactly where
``bet9ja_result_id`` already lives for that other source.

**Dry run by default, explicit confirmation to write.** Every invocation
without ``--confirm`` runs the complete pipeline -- schema validation,
corroboration checks, identity resolution, and a real preflight against
the ledger's current on-disk content -- and writes the same four report
files a real run would, but never touches the ledger. ``--confirm``
performs the identical plan a second time (the ledger could have changed
between the dry run and the confirmed run; re-planning immediately before
committing, under the same exclusive lock, is the only way to guarantee
what gets written is what was actually just previewed) and commits it.
Idempotent and fail-closed exactly like every other settlement source:
re-running an already-committed batch is a safe no-op
(``DUPLICATE_SKIPPED``); a different result or closing-odds value for an
already-``SCORED`` forecast is refused as a real conflict, and a batch
containing ANY conflict writes nothing at all, even with ``--confirm``.

**Explicit boundaries.** Forecast ledger only -- no write of any kind to
``ledgers/betting_ledger.py``. ``closing_odds``/``closing_odds_source``
are always ``None`` -- a manually-verified final score is never a
closing-price source. No automated model retraining, no calibration
adjustment, no PAPER/CASH promotion, no outcome or stake decision of any
kind.
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
from ledgers.validation import _check_node  # noqa: E402

from ..adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from ..adapters.soccer_1x2_elo_v1.identity import resolve_competition, resolve_team  # noqa: E402
from .football_data_settlement import plan_settlement  # noqa: E402

SCHEMA_VERSION_INPUT = "manual-results-input.v1"
SCHEMA_VERSION_REPORT = "pcbf-manual-results-settlement-report.v1"
SCHEMA_VERSION_SETTLED = "pcbf-manual-results-settled-forecasts.v1"
SCHEMA_VERSION_UNMATCHED = "pcbf-manual-results-unmatched-results.v1"
SCHEMA_VERSION_CONFLICTS = "pcbf-manual-results-settlement-conflicts.v1"

SETTLEMENT_BASIS = "MANUAL_VERIFIED"
MARKET_TYPE = "1X2"

REASON_INVALID_ENVELOPE = "SETTLE_INVALID_ENVELOPE"
REASON_NOT_COMPLETED = "SETTLE_NOT_COMPLETED"
REASON_INSUFFICIENT_CORROBORATION = "SETTLE_INSUFFICIENT_CORROBORATION"
REASON_SOURCE_SCORE_DISAGREEMENT = "SETTLE_SOURCE_SCORE_DISAGREEMENT"
REASON_COMPETITION_NOT_COVERED = "SETTLE_COMPETITION_NOT_COVERED"
REASON_HOME_TEAM_UNRESOLVED = "SETTLE_HOME_TEAM_UNRESOLVED"
REASON_AWAY_TEAM_UNRESOLVED = "SETTLE_AWAY_TEAM_UNRESOLVED"
REASON_CONFLICTING_SOURCE_ROW = "SETTLE_CONFLICTING_SOURCE_ROW"


def load_manual_results_schema() -> dict[str, Any]:
    """Loads ``schemas/manual_results_input.v1.schema.json`` via
    ``importlib.resources``, resolved against this package wherever it is
    actually installed -- never a checkout-relative path. See
    ``pyproject.toml``'s own ``[tool.setuptools.package-data]`` entry for
    ``pcbf_calculator.orchestration`` -- this file must ship in the wheel
    or an installed package's ``ingest-manual-results`` raises
    ``FileNotFoundError`` the moment it tries to validate anything."""

    schema_path = resources.files("pcbf_calculator.orchestration").joinpath(
        "schemas", "manual_results_input.v1.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_manual_results_envelope(envelope: Any, schema: dict[str, Any]) -> list[str]:
    """Structural validation only (shape, required fields, types,
    patterns) via ``ledgers.validation``'s own hand-rolled checker --
    reused, never reimplemented, exactly like every other schema check in
    this codebase. Business rules (corroboration, score agreement,
    completion status, identity resolution) are checked separately in
    ``build_manual_results_batch``, which only ever runs after this
    passes."""

    errors: list[str] = []
    _check_node(envelope, schema, "envelope", errors)
    return errors


def discover_input_files(path: Path) -> list[Path]:
    """``path`` a single JSON file -> ``[path]``; a directory -> every
    ``*.json`` directly inside it, sorted (deterministic file order,
    matching every other settlement source's own contract). Never
    recurses into subdirectories."""

    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    return [path]


def _actual_result_from_score(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if away_goals > home_goals:
        return "A"
    return "D"


def _check_corroboration(final_score: dict[str, int], sources: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Returns ``(accepted, rejection_reason)``. ``rejection_reason`` is
    ``None`` iff ``accepted`` is ``True``. See this module's own docstring
    "The corroboration gate" for the exact rule."""

    for source in sources:
        if source["reported_score"] != final_score:
            return False, REASON_SOURCE_SCORE_DISAGREEMENT

    if any(source["authoritative"] for source in sources):
        return True, None

    distinct_source_names = {source["source_name"] for source in sources}
    if len(distinct_source_names) >= 2:
        return True, None

    return False, REASON_INSUFFICIENT_CORROBORATION


def build_manual_results_batch(
    json_paths: list[Path],
    known_teams_by_league: dict[str, set[str]],
    alias_book: Any,
    schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Reads every manual-results-*.json envelope in ``json_paths`` and
    normalizes each usable result into
    ``football_data_settlement.plan_settlement``'s own expected row shape
    (``competition_code``, ``resolved_home_team``, ``resolved_away_team``,
    ``scheduled_date``, ``actual_result``, ``closing_odds`` (always
    ``None``), ``closing_odds_source`` (always ``None``)), plus this
    source's own ``settlement_basis``/``manual_sources`` provenance
    carried through as supporting evidence only (never part of the
    matching key, never written into the forecast ledger's own SCORED
    payload -- see this module's own docstring "Provenance"). Never
    matches against the forecast ledger itself (kept separate, exactly
    like every other settlement source's own batch-builder, so this
    function is testable with no ledger at all).

    Returns ``(normalized_rows, rejected_rows, parse_counts)`` with the
    identical reconciliation contract every other settlement source's own
    batch-builder documents: ``source_rows_total == len(rejected_rows) +
    len(normalized_rows) + duplicate_source_rows_collapsed``. A whole
    file that fails structural schema validation contributes ZERO
    normalized rows and exactly one rejected entry for that file (never a
    partial, best-effort parse of a malformed envelope)."""

    rejected: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    source_rows_total = 0

    for json_path in json_paths:
        envelope = json.loads(json_path.read_text(encoding="utf-8"))
        schema_errors = validate_manual_results_envelope(envelope, schema)
        if schema_errors:
            source_rows_total += 1
            rejected.append(
                {
                    "source_file": str(json_path),
                    "source_row_index": None,
                    "reason": REASON_INVALID_ENVELOPE,
                    "detail": "; ".join(schema_errors),
                }
            )
            continue

        results = envelope.get("results") or []
        for row_index, raw in enumerate(results):
            source_rows_total += 1
            base = {"source_file": str(json_path), "source_row_index": row_index}

            if raw["completion_status"] != "COMPLETED":
                rejected.append(
                    {
                        **base,
                        "reason": REASON_NOT_COMPLETED,
                        "detail": f"completion_status is {raw['completion_status']!r}, not COMPLETED",
                    }
                )
                continue

            final_score = raw["final_score"]
            accepted, corroboration_reason = _check_corroboration(final_score, raw["sources"])
            if not accepted:
                rejected.append(
                    {
                        **base,
                        "reason": corroboration_reason,
                        "detail": (
                            f"{len(raw['sources'])} source(s), "
                            f"{sum(1 for s in raw['sources'] if s['authoritative'])} authoritative, "
                            f"{len({s['source_name'] for s in raw['sources']})} distinct source_name(s)"
                        ),
                    }
                )
                continue

            competition_result = resolve_competition(raw["competition_raw"])
            if competition_result.resolved is None:
                rejected.append({**base, "reason": REASON_COMPETITION_NOT_COVERED, "detail": competition_result.detail})
                continue
            league_code = competition_result.resolved

            known_teams = known_teams_by_league.get(league_code, set())
            home_result = resolve_team(raw["home_team_raw"], league_code, known_teams, alias_book)
            if home_result.resolved is None:
                rejected.append({**base, "reason": REASON_HOME_TEAM_UNRESOLVED, "detail": home_result.detail})
                continue
            away_result = resolve_team(raw["away_team_raw"], league_code, known_teams, alias_book)
            if away_result.resolved is None:
                rejected.append({**base, "reason": REASON_AWAY_TEAM_UNRESOLVED, "detail": away_result.detail})
                continue

            actual_result = _actual_result_from_score(final_score["home"], final_score["away"])
            manual_sources = [
                {
                    "source_name": s["source_name"],
                    "source_url": s["source_url"],
                    "retrieved_at_utc": s["retrieved_at_utc"],
                    "evidence_hash": s["evidence_hash"],
                    "authoritative": s["authoritative"],
                }
                for s in raw["sources"]
            ]

            key = (league_code, home_result.resolved, away_result.resolved, raw["scheduled_date"])
            candidates.setdefault(key, []).append(
                {
                    **base,
                    "competition_code": league_code,
                    "home_team_raw": raw["home_team_raw"],
                    "away_team_raw": raw["away_team_raw"],
                    "resolved_home_team": home_result.resolved,
                    "resolved_away_team": away_result.resolved,
                    "scheduled_date": raw["scheduled_date"],
                    "actual_result": actual_result,
                    "closing_odds": None,
                    "closing_odds_source": None,
                    "settlement_basis": SETTLEMENT_BASIS,
                    "manual_sources": manual_sources,
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


def run_settlement_session(
    input_path: Path, ledger_dir: Path, output_dir: Path, confirm: bool = False
) -> dict[str, Any]:
    """Discovers JSON files at ``input_path`` (a file or a directory --
    see ``discover_input_files``), runs the full manual-results settlement
    pipeline, and writes the four output files this module always
    produces (empty lists, never omitted files, when a bucket has nothing
    in it -- same convention every other settlement source already
    uses).

    ``confirm=False`` (the default): the complete pipeline runs --
    schema validation, corroboration, identity resolution, and a real
    preflight against the ledger's CURRENT on-disk content -- but the
    ledger is never opened for writing. ``confirm=True``: the identical
    plan is computed a SECOND time (never reused from the dry run --
    the ledger could have changed in between) and committed, under the
    same exclusive lock every other settlement source uses for its own
    preflight-then-commit sequence."""

    json_paths = discover_input_files(input_path)
    schema = load_manual_results_schema()
    known_teams_by_league = load_known_teams_by_league()
    alias_book = load_team_alias_book()
    normalized_rows, rejected_at_parse, parse_counts = build_manual_results_batch(
        json_paths, known_teams_by_league, alias_book, schema
    )

    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
    scored: list[dict[str, Any]] = []

    with exclusive_ledger_lock(ledger_path):
        plan = plan_settlement(ledger_path, normalized_rows)

        if confirm and not plan["conflicts"]:
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

    status = "CONFLICT" if plan["conflicts"] else ("DRY_RUN" if not confirm else "OK")
    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "status": status,
        "confirmed": confirm,
        "source_files": [str(p) for p in json_paths],
        "note": (
            "Reuses adapters.soccer_1x2_elo_v1.identity's own competition/team resolution and "
            "football_data_settlement.plan_settlement verbatim -- no matching/scoring/idempotency "
            "logic is reimplemented here. Without --confirm this is a dry run: every check above "
            "ran for real (including a real preflight against the ledger's current content), but "
            "nothing was written. A CONFLICT status means nothing was written to the ledger this "
            "run regardless of --confirm (see settlement-conflicts.json)."
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

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "settlement-report.json", report)
    _write_json(output_dir / "settled-forecasts.json", settled_forecasts)
    _write_json(output_dir / "unmatched-results.json", unmatched_results)
    _write_json(output_dir / "settlement-conflicts.json", settlement_conflicts)

    return {
        "settlement_report": report,
        "settled_forecasts": settled_forecasts,
        "unmatched_results": unmatched_results,
        "settlement_conflicts": settlement_conflicts,
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator ingest-manual-results",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="A manual-results-*.json file, or a directory of them")
    parser.add_argument("--ledger-dir", type=Path, required=True, help="Existing (or new) forecast-ledger directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the four output files into")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually commit to the ledger. Omitting this runs a complete dry run: every check "
        "runs for real, but nothing is written.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_settlement_session(args.input, args.ledger_dir, args.output_dir, confirm=args.confirm)
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
