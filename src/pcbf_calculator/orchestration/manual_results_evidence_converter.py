"""Converts a manual-results EVIDENCE-COLLECTION artifact (an LLM-driven
search pass over a supplied fixture list -- see ``schemas/
manual_results_evidence_input.v1.schema.json`` for the exact upstream
shape) into ``ingest-manual-results``'s own documented input schema
(``manual_results_input.v1``). A separate, governed step from
``ingest-manual-results`` itself -- this module never touches the forecast
ledger beyond a read-only lookup, never writes a ``SCORED`` event, and its
own output is just another file a human reviews before handing it to
``ingest-manual-results`` (still dry-run by default, still requiring
``--confirm`` there).

One command::

    python -m pcbf_calculator convert-manual-results-evidence INPUT_FILES \\
        --ledger-dir ledger_data --output-dir runs/conversion-session-id

**Why a separate command, not a bridge folded into ingest-manual-results
itself.** The evidence-collection artifact is a genuinely different shape,
produced by a genuinely different process (an LLM search pass, not a
human directly filling in ``manual_results_input.v1``), with its own
distinct failure modes this module exists to catch -- an unfinished
match, an evidence summary that doesn't actually say what the result
claims, evidence that was never archived. Fusing the two would mean
``ingest-manual-results``'s own schema (and its own already-reviewed,
already-tested corroboration gate) would need to special-case an upstream
shape it was never designed around. Two small, separately-testable steps
compose better than one large one that does both.

**Never fabricates an evidence hash.** Every source in the evidence-
collection artifact has ``evidence_sha256: null`` until a SEPARATE,
NOT-YET-BUILT evidence-preservation tool actually retrieves and archives
the cited page and computes its hash (the evidence-collection artifact's
own ``llm_instructions`` say so explicitly). A source whose
``evidence_sha256`` is still null is excluded from corroboration here
(``SETTLE_EVIDENCE_NOT_ARCHIVED``) -- this module has no network-fetch or
archival capability of its own, and inventing a hash for content nobody
has actually preserved would defeat the entire point of
``ingest-manual-results``'s own evidence_hash field (a real, in-principle
re-checkable content hash, never an unverifiable assertion).

**Independently re-verifies every claim, never blindly forwards it.**

- Only ``result_status: "VERIFIED"`` rows are considered at all --
  ``NEEDS_REVIEW`` is rejected as ``SETTLE_NOT_VERIFIED``, carrying the
  evidence-collection artifact's own ``review_reason`` through for
  context, never silently dropped.
- ``fixture_id`` is looked up against the CURRENT forecast-ledger state
  (read-only): zero matching forecasts is
  ``SETTLE_FIXTURE_NOT_IN_LEDGER`` (never invents a forecast); more than
  one is ``SETTLE_FIXTURE_ID_AMBIGUOUS`` (never guessed); a match with no
  full settlement identity recorded is ``SETTLE_NO_SETTLEMENT_IDENTITY``.
- The claimed ``competition``/``participants`` are independently
  re-resolved through the SAME ``adapters.soccer_1x2_elo_v1.identity``
  functions every other settlement source uses, and compared against the
  ledger's own already-recorded ``competition_code``/
  ``resolved_home_team``/``resolved_away_team`` for that fixture_id -- a
  disagreement is ``SETTLE_IDENTITY_MISMATCH``, never silently resolved
  by preferring one side.
- Each source's own free-text ``evidence_summary`` is independently
  parsed for a ``home-away`` scoreline and compared against the row's own
  claimed ``home_score``/``away_score`` -- a source whose own cited text
  doesn't actually support the claimed score is excluded
  (``SETTLE_EVIDENCE_SCORE_MISMATCH``), never trusted just because a
  human/LLM labeled the row ``VERIFIED``. A source whose summary can't be
  parsed at all is excluded too (``SETTLE_EVIDENCE_SUMMARY_UNPARSEABLE``).
- ``source_type`` maps to ``authoritative`` via a small, fixed, documented
  allowlist (``AUTHORITATIVE_SOURCE_TYPES`` -- the exact categories the
  evidence-collection artifact's own ``verification_policy.
  authoritative_source_examples`` names); anything else (including
  ``OTHER_CREDIBLE``) is never authoritative.
- The surviving sources (archived AND score-consistent) are run through
  ``manual_results_settlement._check_corroboration`` -- the SAME function
  ``ingest-manual-results`` itself uses, reused verbatim, never a second
  copy -- so a row this converter accepts is guaranteed to also pass that
  command's own corroboration gate.

**Output is advisory, never authoritative on its own.** This command
writes a ``manual-results-input.v1``-shaped file for a human to review and
then hand to ``ingest-manual-results`` -- which still runs its own dry
run by default and still requires ``--confirm`` before writing anything.
Nothing here ever appends to the forecast ledger.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_ledgers_importable() -> None:
    """Same fallback every other orchestration module in this package
    uses -- see any of their own copies of this helper for the full
    rationale."""

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
from ledgers.storage import all_entity_ids, latest_state, read_all  # noqa: E402
from ledgers.validation import _check_node  # noqa: E402

from ..adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from ..adapters.soccer_1x2_elo_v1.identity import resolve_competition, resolve_team  # noqa: E402
from .manual_results_settlement import (  # noqa: E402
    REASON_INSUFFICIENT_CORROBORATION,
    SCHEMA_VERSION_INPUT,
    _check_corroboration,
)

SCHEMA_VERSION_EVIDENCE = "predictbot.manual-results-evidence.v1"
SCHEMA_VERSION_REPORT = "pcbf-manual-results-evidence-conversion-report.v1"

# The evidence-collection artifact's own verification_policy.
# authoritative_source_examples, given a fixed source_type vocabulary --
# never widened without a new confirmed category appearing in a real
# evidence-collection run.
AUTHORITATIVE_SOURCE_TYPES = frozenset({"OFFICIAL_CLUB", "OFFICIAL_COMPETITION", "NEWSWIRE", "MAJOR_PUBLICATION"})

REASON_INVALID_ENVELOPE = "SETTLE_INVALID_ENVELOPE"
REASON_NOT_VERIFIED = "SETTLE_NOT_VERIFIED"
REASON_FIXTURE_NOT_IN_LEDGER = "SETTLE_FIXTURE_NOT_IN_LEDGER"
REASON_FIXTURE_ID_AMBIGUOUS = "SETTLE_FIXTURE_ID_AMBIGUOUS"
REASON_NO_SETTLEMENT_IDENTITY = "SETTLE_NO_SETTLEMENT_IDENTITY"
REASON_IDENTITY_MISMATCH = "SETTLE_IDENTITY_MISMATCH"
REASON_EVIDENCE_NOT_ARCHIVED = "SETTLE_EVIDENCE_NOT_ARCHIVED"
REASON_EVIDENCE_SUMMARY_UNPARSEABLE = "SETTLE_EVIDENCE_SUMMARY_UNPARSEABLE"
REASON_EVIDENCE_SCORE_MISMATCH = "SETTLE_EVIDENCE_SCORE_MISMATCH"
# REASON_INSUFFICIENT_CORROBORATION / REASON_SOURCE_SCORE_DISAGREEMENT are
# imported directly from manual_results_settlement above -- reused, never
# redefined, so the two modules can never drift on what these mean.

_SCORELINE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
_EVIDENCE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def load_manual_results_evidence_schema() -> dict[str, Any]:
    """Loads ``schemas/manual_results_evidence_input.v1.schema.json`` via
    ``importlib.resources`` -- never a checkout-relative path. See
    ``pyproject.toml``'s own ``pcbf_calculator.orchestration`` package-data
    entry (shared with ``manual_results_settlement``'s own schema)."""

    schema_path = resources.files("pcbf_calculator.orchestration").joinpath(
        "schemas", "manual_results_evidence_input.v1.schema.json"
    )
    return json.loads(schema_path.read_text(encoding="utf-8"))


def discover_input_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    return [path]


def _build_fixture_id_index(ledger_path: Path) -> dict[str, list[dict[str, Any]]]:
    """One current-state pass per forecast_id (reusing ``all_entity_ids``/
    ``latest_state`` -- never a second, separately-maintained notion of
    "current state"), indexed by the RECORDED payload's own
    ``fixture_id`` -- the exact join key the evidence-collection artifact
    itself was seeded from."""

    records = read_all(ledger_path)
    index: dict[str, list[dict[str, Any]]] = {}
    for forecast_id in all_entity_ids(records, "forecast_id"):
        state = latest_state(records, "forecast_id", forecast_id)
        fixture_id = state.get("fixture_id")
        if fixture_id is None:
            continue
        index.setdefault(fixture_id, []).append(state)
    return index


def convert_evidence_batch(
    json_paths: list[Path],
    ledger_path: Path,
    known_teams_by_league: dict[str, set[str]],
    alias_book: Any,
    schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Reads every evidence-collection envelope in ``json_paths`` and
    converts each independently-verifiable ``VERIFIED`` result into
    ``ingest-manual-results``'s own input row shape. Returns
    ``(converted_results, rejected, counts)`` with the identical
    reconciliation contract every other batch-builder in this codebase
    documents: ``source_rows_total == len(rejected) + len(converted_results)``
    (a converted result is never also a duplicate-source-row concept here
    -- each fixture_id appears once in the evidence-collection artifact by
    construction)."""

    fixture_index = _build_fixture_id_index(ledger_path)
    rejected: list[dict[str, Any]] = []
    converted: list[dict[str, Any]] = []
    source_rows_total = 0

    for json_path in json_paths:
        envelope = json.loads(json_path.read_text(encoding="utf-8"))
        errors: list[str] = []
        _check_node(envelope, schema, "envelope", errors)
        if errors:
            source_rows_total += 1
            rejected.append(
                {
                    "source_file": str(json_path),
                    "source_row_index": None,
                    "reason": REASON_INVALID_ENVELOPE,
                    "detail": "; ".join(errors),
                }
            )
            continue

        for row_index, raw in enumerate(envelope.get("results") or []):
            source_rows_total += 1
            base = {"source_file": str(json_path), "source_row_index": row_index, "fixture_id": raw["fixture_id"]}

            if raw["result_status"] != "VERIFIED":
                rejected.append(
                    {**base, "reason": REASON_NOT_VERIFIED, "detail": raw.get("review_reason") or "result_status is not VERIFIED"}
                )
                continue

            matches = fixture_index.get(raw["fixture_id"], [])
            if not matches:
                rejected.append({**base, "reason": REASON_FIXTURE_NOT_IN_LEDGER, "detail": "no RECORDED forecast for this fixture_id"})
                continue
            if len(matches) > 1:
                rejected.append(
                    {**base, "reason": REASON_FIXTURE_ID_AMBIGUOUS, "detail": f"{len(matches)} forecasts share this fixture_id"}
                )
                continue
            state = matches[0]

            recorded_competition_code = state.get("competition_code")
            recorded_home = state.get("resolved_home_team")
            recorded_away = state.get("resolved_away_team")
            scheduled_date = state.get("scheduled_date")
            if None in (recorded_competition_code, recorded_home, recorded_away, scheduled_date):
                rejected.append(
                    {**base, "reason": REASON_NO_SETTLEMENT_IDENTITY, "detail": "ledger forecast has no full settlement identity recorded"}
                )
                continue

            competition_result = resolve_competition(raw["competition"])
            home_result = resolve_team(
                raw["participants"]["home"], competition_result.resolved or "", known_teams_by_league.get(competition_result.resolved or "", set()), alias_book
            )
            away_result = resolve_team(
                raw["participants"]["away"], competition_result.resolved or "", known_teams_by_league.get(competition_result.resolved or "", set()), alias_book
            )
            if (
                competition_result.resolved != recorded_competition_code
                or home_result.resolved != recorded_home
                or away_result.resolved != recorded_away
            ):
                rejected.append(
                    {
                        **base,
                        "reason": REASON_IDENTITY_MISMATCH,
                        "detail": (
                            f"evidence claims ({competition_result.resolved!r}, {home_result.resolved!r}, "
                            f"{away_result.resolved!r}) but the ledger recorded "
                            f"({recorded_competition_code!r}, {recorded_home!r}, {recorded_away!r}) for this fixture_id"
                        ),
                    }
                )
                continue

            final_score = {"home": raw["home_score"], "away": raw["away_score"]}
            manual_sources: list[dict[str, Any]] = []
            source_rejections: list[str] = []
            for source in raw["sources"]:
                if source["evidence_sha256"] is None:
                    source_rejections.append(f"{source['source_name']}: {REASON_EVIDENCE_NOT_ARCHIVED}")
                    continue
                if not _EVIDENCE_HASH_RE.match(source["evidence_sha256"]):
                    source_rejections.append(f"{source['source_name']}: {REASON_EVIDENCE_NOT_ARCHIVED} (malformed hash)")
                    continue
                match = _SCORELINE_RE.search(source["evidence_summary"])
                if match is None:
                    source_rejections.append(f"{source['source_name']}: {REASON_EVIDENCE_SUMMARY_UNPARSEABLE}")
                    continue
                reported_score = {"home": int(match.group(1)), "away": int(match.group(2))}
                if reported_score != final_score:
                    source_rejections.append(
                        f"{source['source_name']}: {REASON_EVIDENCE_SCORE_MISMATCH} "
                        f"(evidence says {reported_score}, row claims {final_score})"
                    )
                    continue
                manual_sources.append(
                    {
                        "source_name": source["source_name"],
                        "source_url": source["source_url"],
                        "retrieved_at_utc": source["retrieved_at_utc"],
                        "evidence_hash": f"sha256:{source['evidence_sha256']}",
                        "authoritative": source["source_type"] in AUTHORITATIVE_SOURCE_TYPES,
                        "reported_score": reported_score,
                    }
                )

            # The SAME shared corroboration function ingest-manual-results
            # itself uses, reused verbatim -- a row this converter accepts
            # is guaranteed to also pass that command's own gate. Correctly
            # returns REASON_INSUFFICIENT_CORROBORATION on an empty list
            # (every source excluded above), no special-casing needed here.
            accepted, corroboration_reason = _check_corroboration(final_score, manual_sources)
            if not accepted:
                # All-not-yet-archived is reported as that specific,
                # actionable reason rather than the generic corroboration
                # failure it would otherwise collapse into.
                reason = (
                    REASON_EVIDENCE_NOT_ARCHIVED
                    if not manual_sources and source_rejections and all(REASON_EVIDENCE_NOT_ARCHIVED in r for r in source_rejections)
                    else (corroboration_reason or REASON_INSUFFICIENT_CORROBORATION)
                )
                detail = "; ".join(source_rejections) if source_rejections else "no sources at all"
                rejected.append({**base, "reason": reason, "detail": detail})
                continue

            converted.append(
                {
                    "competition_raw": raw["competition"],
                    "home_team_raw": raw["participants"]["home"],
                    "away_team_raw": raw["participants"]["away"],
                    "scheduled_date": scheduled_date,
                    "completion_status": "COMPLETED",
                    "final_score": final_score,
                    "sources": manual_sources,
                }
            )

    counts = {"source_rows_total": source_rows_total, "rejected_at_conversion": len(rejected)}
    return converted, rejected, counts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_conversion_session(input_path: Path, ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    json_paths = discover_input_files(input_path)
    schema = load_manual_results_evidence_schema()
    known_teams_by_league = load_known_teams_by_league()
    alias_book = load_team_alias_book()
    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME

    converted, rejected, counts = convert_evidence_batch(json_paths, ledger_path, known_teams_by_league, alias_book, schema)

    converted_input = {"schema_version": SCHEMA_VERSION_INPUT, "results": converted}
    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "source_files": [str(p) for p in json_paths],
        "note": (
            "Read-only: never touches the forecast ledger beyond looking up each fixture_id's "
            "current state. Never fabricates an evidence_hash for a source whose evidence_sha256 "
            "is still null -- that source is excluded from corroboration, never silently trusted. "
            "converted-manual-results-input.json is advisory only: hand it to ingest-manual-results "
            "for its own dry run and --confirm."
        ),
        "counts": {
            **counts,
            "converted": len(converted),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "conversion-report.json", report)
    _write_json(output_dir / "converted-manual-results-input.json", converted_input)
    _write_json(output_dir / "conversion-rejected.json", {"schema_version": "pcbf-manual-results-evidence-rejected.v1", "rejected": rejected})

    return {"conversion_report": report, "converted_input": converted_input, "rejected": rejected}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator convert-manual-results-evidence",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="A manual-results-evidence-*.json file, or a directory of them")
    parser.add_argument("--ledger-dir", type=Path, required=True, help="Existing forecast-ledger directory (read-only lookup)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the three output files into")
    args = parser.parse_args(argv)

    result = run_conversion_session(args.input, args.ledger_dir, args.output_dir)
    counts = result["conversion_report"]["counts"]
    print(
        f"CONVERTED: {counts['converted']} ready for ingest-manual-results, "
        f"{counts['rejected_at_conversion']} rejected "
        f"({counts['source_rows_total']} source rows) -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
