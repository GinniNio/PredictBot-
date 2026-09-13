"""Bet9ja assembled-capture -> PCBF research-batch ingestion.

    Bet9ja assembled JSON
    -> validate
    -> normalize
    -> resolve fixture times conservatively
    -> deduplicate
    -> quarantine unusable records
    -> PCBF research input

IN SCOPE: validating one assembled Bet9ja Soccer capture export (see
``validate_envelope``), admitting only fully-supported pre-match Soccer
1X2 fixtures (see ``ADMISSION`` in this module's own comments and
``ingestion/errors.py``'s quarantine codes), resolving each admitted
fixture's kickoff time from its Africa/Lagos display text
(``ingestion/kickoff.py``), and writing four deterministic output files.

OUT OF SCOPE (never touched by this module): probability generation,
betting recommendations, Kelly staking, CASH/PAPER admission, external
odds retrieval, results settlement, database/ledger writes, automatic
betting, browser-extension changes, any sport other than Soccer. Every
admitted fixture is unconditionally tagged ``classification_ceiling:
"RESEARCH-MODEL"`` -- this bridge has no registry-admission mechanism and
must never claim one (see ``docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md``
sections 14-17 for the real, human-gated promotion path RESEARCH-MODEL
requires to ever change).

DETERMINISM: given the same assembled export, this module's four output
structures are byte-identical across repeated runs -- no current-clock
timestamp is ever written into them (only the SOURCE capture's own
``captured_at_utc`` and per-transition ``updated_at_utc`` values, already
fixed at capture time), and every list is sorted by a fixed key (kickoff
time, then competition, then team names, then fixture id) rather than
insertion/dict order.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .errors import (
    ALL_CODES,
    BET9JA_ALREADY_STARTED,
    BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES,
    BET9JA_DUPLICATE_COMPETITION_ID,
    BET9JA_DUPLICATE_FIXTURE_ID,
    BET9JA_INCOMPLETE_PRICES,
    BET9JA_INVALID_PRICE,
    BET9JA_KICKOFF_AMBIGUOUS,
    BET9JA_LEDGER_SUM_MISMATCH,
    BET9JA_LEDGER_TOTAL_MISMATCH,
    BET9JA_MISSING_COMPETITION_ATTRIBUTION,
    BET9JA_MISSING_FIELD,
    BET9JA_MISSING_TEAM_NAME,
    BET9JA_SEGMENT_NOT_ASSEMBLED,
    BET9JA_UNKNOWN_FIXTURE_COMPETITION,
    BET9JA_UNSUPPORTED_MARKET_FAMILY,
    BET9JA_UNSUPPORTED_SPORT,
    BET9JA_UNSUPPORTED_STATUS,
    BET9JA_UNTRUSTED_COMPETITION_HAS_FIXTURES,
    Bet9jaEnvelopeError,
)
from .kickoff import resolve_kickoff_utc

__all__ = [
    "ALL_CODES",
    "Bet9jaEnvelopeError",
    "ingest_assembled_capture",
    "validate_envelope",
    "main",
]

CLASSIFICATION_CEILING = "RESEARCH-MODEL"

REQUIRED_ENVELOPE_FIELDS = (
    "capture_scope",
    "capture_session_id",
    "captured_at_utc",
    "competition_ledger",
    "fixtures",
    "inventory_fingerprint",
    "schema_version",
    "summary",
    "unparsed_records",
)

# soccer_session.js's own ledger status vocabulary.
TRUSTED_EMPTY_STATUS = "CONFIRMED_EMPTY"
TRUSTED_NONEMPTY_STATUS = "COMPLETED"
UNTRUSTED_STATUSES = ("FAILED", "PENDING")

SCHEMA_VERSION_VALIDATION = "bet9ja-ingestion-validation.v1"
SCHEMA_VERSION_NORMALIZED = "bet9ja-ingestion-normalized-fixtures.v1"
SCHEMA_VERSION_QUARANTINE = "bet9ja-ingestion-quarantine.v1"
SCHEMA_VERSION_RESEARCH_BATCH = "pcbf-research-batch.v1"


def _fail(condition: bool, code: str, reason: str) -> None:
    if not condition:
        raise Bet9jaEnvelopeError(code, reason)


def validate_envelope(envelope: Any) -> None:
    """Enforces every hard-fail contradiction check this bridge requires
    before ingestion -- raises ``Bet9jaEnvelopeError`` on the FIRST
    contradiction found (never repairs source data, never proceeds past a
    contradiction to collect more)."""
    if not isinstance(envelope, dict):
        raise Bet9jaEnvelopeError(BET9JA_MISSING_FIELD, "Input must be a JSON object.")

    # A segment export (soccer_session.js::buildSegmentEnvelope) carries
    # segment_index/competition_results instead of the assembled shape's
    # competition_ledger/summary -- checked BEFORE the generic
    # missing-field check so the caller gets the specific, actionable
    # error rather than a generic "missing competition_ledger" one.
    if "segment_index" in envelope or "competition_results" in envelope:
        raise Bet9jaEnvelopeError(
            BET9JA_SEGMENT_NOT_ASSEMBLED,
            "Input is a per-run segment export (bet9ja-soccer-session-*-segment-*.json), "
            "not the assembled capture. Use 'Download current results' (bet9ja-soccer-all-*.json) instead.",
        )

    missing = [field for field in REQUIRED_ENVELOPE_FIELDS if field not in envelope]
    _fail(not missing, BET9JA_MISSING_FIELD, f"Missing required field(s): {', '.join(sorted(missing))}.")

    ledger = envelope["competition_ledger"]
    fixtures = envelope["fixtures"]
    summary = envelope["summary"]
    _fail(isinstance(ledger, list), BET9JA_MISSING_FIELD, "competition_ledger must be an array.")
    _fail(isinstance(fixtures, list), BET9JA_MISSING_FIELD, "fixtures must be an array.")
    _fail(isinstance(summary, dict), BET9JA_MISSING_FIELD, "summary must be an object.")

    total = summary.get("total")
    _fail(
        total == len(ledger),
        BET9JA_LEDGER_TOTAL_MISMATCH,
        f"summary.total ({total!r}) != len(competition_ledger) ({len(ledger)}).",
    )

    completed = summary.get("completed", 0)
    confirmed_empty = summary.get("confirmed_empty", 0)
    failed = summary.get("failed", 0)
    pending = summary.get("pending", 0)
    _fail(
        completed + confirmed_empty + failed + pending == total,
        BET9JA_LEDGER_SUM_MISMATCH,
        f"summary components ({completed}+{confirmed_empty}+{failed}+{pending}) != summary.total ({total!r}).",
    )

    status_by_competition_id: dict[str, str] = {}
    for entry in ledger:
        competition_id = entry.get("competition_id")
        _fail(
            competition_id not in status_by_competition_id,
            BET9JA_DUPLICATE_COMPETITION_ID,
            f"competition_id '{competition_id}' appears more than once in competition_ledger.",
        )
        status_by_competition_id[competition_id] = entry.get("status")

    seen_fixture_ids: set[str] = set()
    fixture_count_by_competition_id: dict[str, int] = {}
    for fixture in fixtures:
        fixture_id = fixture.get("fixture_id")
        _fail(
            fixture_id not in seen_fixture_ids,
            BET9JA_DUPLICATE_FIXTURE_ID,
            f"fixture_id '{fixture_id}' appears more than once in fixtures.",
        )
        seen_fixture_ids.add(fixture_id)

        competition_id = fixture.get("resolved_source_competition_id")
        _fail(
            competition_id in status_by_competition_id,
            BET9JA_UNKNOWN_FIXTURE_COMPETITION,
            f"fixture '{fixture_id}' references unknown competition_id '{competition_id}'.",
        )
        fixture_count_by_competition_id[competition_id] = fixture_count_by_competition_id.get(competition_id, 0) + 1

    for competition_id, status in status_by_competition_id.items():
        fixture_count = fixture_count_by_competition_id.get(competition_id, 0)
        if status == TRUSTED_EMPTY_STATUS:
            _fail(
                fixture_count == 0,
                BET9JA_CONFIRMED_EMPTY_HAS_FIXTURES,
                f"competition_id '{competition_id}' is {TRUSTED_EMPTY_STATUS} but has {fixture_count} fixture(s) attributed to it.",
            )
        elif status in UNTRUSTED_STATUSES:
            _fail(
                fixture_count == 0,
                BET9JA_UNTRUSTED_COMPETITION_HAS_FIXTURES,
                f"competition_id '{competition_id}' has status '{status}' but has {fixture_count} fixture(s) attributed to it -- never trusted.",
            )


def _is_valid_price(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 1


def _admit_or_quarantine(
    fixture: dict[str, Any], captured_at_utc: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Returns ``(admitted_fixture_or_None, quarantine_record_or_None)`` --
    exactly one is non-``None``. Never raises: every rejection is a typed
    quarantine reason, never a hard failure (unlike ``validate_envelope``,
    which stops the whole import)."""

    def quarantined(code: str, detail: str) -> tuple[None, dict[str, Any]]:
        return None, {
            "source_fixture_id": fixture.get("fixture_id"),
            "source_competition_id": fixture.get("resolved_source_competition_id"),
            "reason": code,
            "detail": detail,
            "raw": {
                "sport": fixture.get("sport"),
                "status": fixture.get("status"),
                "market_family": fixture.get("market_family"),
                "offered_odds": fixture.get("offered_odds"),
                "participants": fixture.get("participants"),
                "date_heading_raw": fixture.get("date_heading_raw"),
                "kickoff_raw": fixture.get("kickoff_raw"),
            },
        }

    if fixture.get("sport") != "SOCCER":
        return quarantined(BET9JA_UNSUPPORTED_SPORT, f"sport='{fixture.get('sport')}', expected 'SOCCER'.")
    if fixture.get("status") != "PRE_MATCH":
        return quarantined(BET9JA_UNSUPPORTED_STATUS, f"status='{fixture.get('status')}', expected 'PRE_MATCH'.")
    if fixture.get("market_family") != "1X2":
        return quarantined(BET9JA_UNSUPPORTED_MARKET_FAMILY, f"market_family='{fixture.get('market_family')}', expected '1X2'.")

    odds = fixture.get("offered_odds") or {}
    home_price, draw_price, away_price = odds.get("H"), odds.get("D"), odds.get("A")
    if home_price is None or draw_price is None or away_price is None:
        return quarantined(BET9JA_INCOMPLETE_PRICES, f"offered_odds={odds!r} is missing one or more of H/D/A.")
    for label, price in (("H", home_price), ("D", draw_price), ("A", away_price)):
        if not _is_valid_price(price):
            return quarantined(BET9JA_INVALID_PRICE, f"{label} price {price!r} is not a finite number greater than 1.")

    participants = fixture.get("participants") or {}
    home, away = participants.get("home"), participants.get("away")
    if not isinstance(home, str) or not home.strip() or not isinstance(away, str) or not away.strip():
        return quarantined(BET9JA_MISSING_TEAM_NAME, f"participants={participants!r} is missing a home and/or away name.")

    competition_id = fixture.get("resolved_source_competition_id")
    if not competition_id:
        return quarantined(BET9JA_MISSING_COMPETITION_ATTRIBUTION, "resolved_source_competition_id is empty.")

    kickoff = resolve_kickoff_utc(captured_at_utc, fixture.get("date_heading_raw"), fixture.get("kickoff_raw"))
    if not kickoff.ok:
        return quarantined(kickoff.reason_code or BET9JA_KICKOFF_AMBIGUOUS, kickoff.reason_detail or "kickoff could not be resolved.")

    return {
        "source_fixture_id": fixture["fixture_id"],
        "source_competition_id": competition_id,
        "home": home.strip(),
        "away": away.strip(),
        "kickoff_utc": kickoff.kickoff_utc,
        "kickoff_resolution": kickoff.kickoff_resolution,
        "kickoff_source_timezone": kickoff.kickoff_source_timezone,
        "kickoff_source_date_raw": kickoff.kickoff_source_date_raw,
        "kickoff_source_time_raw": kickoff.kickoff_source_time_raw,
        "market": {"family": "1X2", "home": home_price, "draw": draw_price, "away": away_price},
        "duplicate_status": fixture.get("duplicate_status"),
    }, None


def _sort_key(fixture: dict[str, Any]) -> tuple[str, str, str, str, str]:
    # Stable ordering per this module's own docstring: kickoff_utc,
    # competition_id, home, away, fixture_id -- never insertion order, so
    # re-ingesting the same export byte-identically reproduces this order.
    return (
        fixture["kickoff_utc"] or "",
        fixture["source_competition_id"] or "",
        fixture["home"] or "",
        fixture["away"] or "",
        fixture["source_fixture_id"] or "",
    )


def ingest_assembled_capture(envelope: dict[str, Any]) -> dict[str, Any]:
    """Runs the full ingestion pipeline against one already-loaded
    assembled-capture dict. Raises ``Bet9jaEnvelopeError`` if
    ``validate_envelope`` finds any contradiction; otherwise returns a
    dict with the four deterministic output structures
    (``capture_validation``, ``fixtures_normalized``,
    ``fixtures_quarantined``, ``pcbf_research_batch``) -- this function
    does no file I/O itself, so it can be tested/composed directly."""
    validate_envelope(envelope)

    capture_session_id = envelope["capture_session_id"]
    captured_at_utc = envelope["captured_at_utc"]
    ledger = envelope["competition_ledger"]
    fixtures = envelope["fixtures"]

    ledger_by_id = {entry["competition_id"]: entry for entry in ledger}

    admitted: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    duplicate_status_counts: dict[str, int] = {}
    for fixture in fixtures:
        duplicate_status = fixture.get("duplicate_status") or "NEW"
        duplicate_status_counts[duplicate_status] = duplicate_status_counts.get(duplicate_status, 0) + 1
        admitted_fixture, quarantine_record = _admit_or_quarantine(fixture, captured_at_utc)
        if admitted_fixture is not None:
            admitted.append(admitted_fixture)
        else:
            quarantined.append(quarantine_record)  # type: ignore[arg-type]

    admitted.sort(key=_sort_key)
    quarantined.sort(key=lambda record: (record["source_competition_id"] or "", record["source_fixture_id"] or "", record["reason"]))

    normalized_fixtures = []
    research_fixtures = []
    for fixture in admitted:
        competition_id = fixture["source_competition_id"]
        ledger_entry = ledger_by_id[competition_id]
        base = {
            "source": "BET9JA",
            "source_capture_session_id": capture_session_id,
            "source_fixture_id": fixture["source_fixture_id"],
            "source_competition_id": competition_id,
            "sport": "SOCCER",
            "country": ledger_entry.get("country"),
            "competition": ledger_entry.get("competition"),
            "home": fixture["home"],
            "away": fixture["away"],
            "kickoff_utc": fixture["kickoff_utc"],
            "kickoff_resolution": fixture["kickoff_resolution"],
            "kickoff_source_timezone": fixture["kickoff_source_timezone"],
            "kickoff_source_date_raw": fixture["kickoff_source_date_raw"],
            "kickoff_source_time_raw": fixture["kickoff_source_time_raw"],
            "market": fixture["market"],
            "classification_ceiling": CLASSIFICATION_CEILING,
        }
        normalized_fixtures.append(base)
        research_fixtures.append(dict(base))

    quarantine_reason_counts: dict[str, int] = {}
    for record in quarantined:
        quarantine_reason_counts[record["reason"]] = quarantine_reason_counts.get(record["reason"], 0) + 1

    ledger_summary = envelope["summary"]
    capture_validation = {
        "schema_version": SCHEMA_VERSION_VALIDATION,
        "source_capture_session_id": capture_session_id,
        "source_captured_at_utc": captured_at_utc,
        "source_fixtures": len(fixtures),
        "admitted_fixtures": len(admitted),
        "quarantined_fixtures": len(quarantined),
        "duplicate_fixtures": sum(count for status, count in duplicate_status_counts.items() if status != "NEW"),
        "competitions_represented": len({f["source_competition_id"] for f in admitted}),
        "ledger_reconciliation": {
            "total": ledger_summary.get("total"),
            "completed": ledger_summary.get("completed"),
            "confirmed_empty": ledger_summary.get("confirmed_empty"),
            "failed": ledger_summary.get("failed"),
            "pending": ledger_summary.get("pending"),
            "reconciles": True,  # validate_envelope already raised above if not
        },
        "reason_counts": dict(sorted(quarantine_reason_counts.items())),
    }

    fixtures_normalized = {
        "schema_version": SCHEMA_VERSION_NORMALIZED,
        "source_capture_session_id": capture_session_id,
        "source_captured_at_utc": captured_at_utc,
        "fixtures": normalized_fixtures,
    }
    fixtures_quarantined = {
        "schema_version": SCHEMA_VERSION_QUARANTINE,
        "source_capture_session_id": capture_session_id,
        "source_captured_at_utc": captured_at_utc,
        "quarantined": quarantined,
    }
    pcbf_research_batch = {
        "schema_version": SCHEMA_VERSION_RESEARCH_BATCH,
        "source": "BET9JA",
        "source_capture_session_id": capture_session_id,
        "source_captured_at_utc": captured_at_utc,
        "classification_ceiling": CLASSIFICATION_CEILING,
        "fixtures": research_fixtures,
    }

    return {
        "capture_validation": capture_validation,
        "fixtures_normalized": fixtures_normalized,
        "fixtures_quarantined": fixtures_quarantined,
        "pcbf_research_batch": pcbf_research_batch,
    }


def _write_json(path: Path, payload: Any) -> None:
    # sort_keys + fixed separators, matching cli.py::_canonical_bytes's own
    # determinism discipline -- byte-identical output for byte-identical
    # input, every time, on every machine.
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_ingest(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """File-I/O wrapper around ``ingest_assembled_capture``: reads
    ``input_path``, writes the four output files into ``output_dir``
    (created if absent), and returns the same four-structure dict
    ``ingest_assembled_capture`` does. Propagates ``Bet9jaEnvelopeError``
    on any contradiction -- no partial output is ever written on failure."""
    envelope = json.loads(input_path.read_text(encoding="utf-8"))
    result = ingest_assembled_capture(envelope)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "capture-validation.json", result["capture_validation"])
    _write_json(output_dir / "fixtures-normalized.json", result["fixtures_normalized"])
    _write_json(output_dir / "fixtures-quarantined.json", result["fixtures_quarantined"])
    _write_json(output_dir / "pcbf-research-batch.json", result["pcbf_research_batch"])
    return result


def main(argv: list[str] | None = None) -> int:
    """``python -m pcbf_calculator ingest-bet9ja INPUT --output-dir DIR``
    entry point -- see ``cli.py`` for how this is dispatched. Prints one
    operational summary line to stdout (run timing/paths only -- never
    written into the deterministic output files themselves, per this
    module's own determinism discipline) and returns a process exit code
    (0 on success, 2 on a rejected/contradictory capture)."""
    parser = argparse.ArgumentParser(
        prog="pcbf_calculator ingest-bet9ja",
        description="Validate and normalize an assembled Bet9ja Soccer capture into a PCBF research batch.",
    )
    parser.add_argument("input", type=Path, help="Assembled Bet9ja capture JSON file (bet9ja-soccer-all-*.json)")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the four output files into")
    args = parser.parse_args(argv)

    try:
        result = run_ingest(args.input, args.output_dir)
    except Bet9jaEnvelopeError as exc:
        print(f"REJECTED: {exc.code}: {exc.reason}")
        return 2
    except (json.JSONDecodeError, OSError) as exc:
        print(f"REJECTED: could not read '{args.input}': {exc}")
        return 2

    validation = result["capture_validation"]
    print(
        f"OK: {validation['admitted_fixtures']} admitted, {validation['quarantined_fixtures']} quarantined "
        f"({validation['competitions_represented']} competitions) -> {args.output_dir}"
    )
    return 0
