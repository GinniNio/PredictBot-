"""forecast-ledger.jsonl: one append-only event stream per forecast_id.

Every forecast a RESEARCH-classified model produces is recorded here,
including every one never placed as a ticket -- ``selection_status``
starts at ``"considered"`` and only reaches ``"placed"`` via an explicit
``SELECTION_UPDATED`` event (typically appended alongside
``betting_ledger.append_placed`` for the ticket that used it).

Three event types, ever appended for one forecast_id (see
``ledgers/schemas/forecast_ledger.v1.schema.json`` for the full field
list):

- ``RECORDED`` -- the forecast itself, at generation time. Idempotent:
  re-importing the same forecast JSON is a safe no-op
  (``storage.append_if_new``); importing DIFFERENT content under the same
  ``(fixture_id, market_type, model_version)`` natural key is refused as a
  conflict, never silently overwritten.
- ``SELECTION_UPDATED`` -- the operator's own considered/shortlisted/
  placed/skipped decision, changed over the forecast's lifecycle.
- ``SCORED`` -- appended once the real result is known: Brier score, log
  loss (``ledgers/scoring.py``, self-contained, never imported from the
  research backtest module), and an opening-vs-closing market comparison
  reusing the already-reviewed pricing engine (read-only import, registers
  nothing).

This module never trains, modifies, registers, or reclassifies a model --
it only records and scores forecasts a model already produced.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_SRC_DIR = REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from ledgers import ids, scoring
from ledgers.storage import AppendResult, append_always, append_if_new, all_entity_ids, latest_state, read_all

SCHEMA_VERSION = "1.0.0"
SCHEMA_NAME = "forecast_ledger.v1"

EVENT_RECORDED = "RECORDED"
EVENT_SELECTION_UPDATED = "SELECTION_UPDATED"
EVENT_SCORED = "SCORED"

SELECTION_STATUSES = ("considered", "shortlisted", "placed", "skipped")

DEFAULT_FILENAME = "forecast-ledger.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_recorded_event(
    *,
    fixture_id: str,
    sport: str,
    league: str,
    kickoff_utc: str,
    market_type: str,
    offered_odds: dict[str, float],
    model_probabilities: dict[str, float],
    model_version: str,
    artifact_hash: str,
    input_hash: str,
    output_hash: str,
    classification: str,
    created_at_utc: str | None = None,
    selection_status: str = "considered",
    batch_id: str | None = None,
    capture_id: str | None = None,
    source: str | None = None,
    captured_at_utc: str | None = None,
    selection: str | None = None,
    stop_reason: str | None = None,
    operator_decision: str | None = None,
) -> dict[str, Any]:
    """Build (never appends) one RECORDED event. ``forecast_id`` is
    derived deterministically from ``(fixture_id, market_type,
    model_version)`` -- see ``ledgers/ids.py``.

    ``batch_id``/``capture_id``/``source``/``captured_at_utc`` trace a
    forecast back to the capture session (e.g. a manual Bet9ja JSON
    capture, or later a capture tool's output) that supplied its raw
    prices -- distinct from ``created_at_utc``, which is when THIS
    forecast was generated from that captured data. ``stop_reason`` is
    non-null when this candidate was excluded by a hard rule (e.g. a
    jurisdictional STOP) before or instead of a full forecast score --
    such a record is still written (every candidate is recorded, STOP or
    not) but MUST be excluded from any ranked/candidate view (see
    ``ledgers/summary.py::ranked_forecasts``). ``operator_decision`` is
    the human's own recorded judgement, distinct from the mechanical
    ``selection_status`` pipeline stage. ``market_probabilities`` is
    computed here (never taken from the caller) via the already-reviewed
    pricing engine, read-only, from ``offered_odds`` -- ``None`` (with an
    error code, never a crash) if ``offered_odds`` is not a valid,
    complete market."""

    if selection_status not in SELECTION_STATUSES:
        raise ValueError(f"selection_status must be one of {SELECTION_STATUSES}, got {selection_status!r}")

    fc_id = ids.forecast_id(fixture_id, market_type, model_version)
    payload: dict[str, Any] = {
        "created_at_utc": created_at_utc or _now_utc(),
        "fixture_id": fixture_id,
        "sport": sport,
        "league": league,
        "kickoff_utc": kickoff_utc,
        "market_type": market_type,
        "offered_odds": offered_odds,
        "market_probabilities": _fair_probabilities(offered_odds),
        "model_probabilities": model_probabilities,
        "model_version": model_version,
        "artifact_hash": artifact_hash,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "classification": classification,
        "selection_status": selection_status,
        "batch_id": batch_id,
        "capture_id": capture_id,
        "source": source,
        "captured_at_utc": captured_at_utc,
        "selection": selection,
        "stop_reason": stop_reason,
        "operator_decision": operator_decision,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_RECORDED,
        "forecast_id": fc_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }


def _fair_probabilities(offered_odds: dict[str, float]) -> dict[str, Any] | None:
    """De-vigged fair H/D/A probabilities from ``offered_odds``, reusing
    the already-reviewed pricing engine (read-only import; registers
    nothing, touches no adapter dispatch table). ``None`` (with an error
    code) rather than a crash when ``offered_odds`` is missing or not a
    valid, complete market -- a forecast is still recorded either way."""

    from pcbf_calculator.pricing.engine import PricingFailure, analyze_market

    if not offered_odds:
        return None
    try:
        result = analyze_market(offered_odds)
    except PricingFailure as exc:
        return {"error": exc.code}
    return {o["outcome"]: o["fair_probability"] for o in result["outcomes"]}


def append_recorded(ledger_path: Path, event: dict[str, Any]) -> AppendResult:
    """Idempotently append a RECORDED event built by
    ``build_recorded_event``."""

    return append_if_new(ledger_path, event, id_field="forecast_id")


def append_selection_updated(
    ledger_path: Path,
    forecast_id: str,
    selection_status: str,
    note: str | None = None,
    operator_decision: str | None = None,
) -> None:
    if selection_status not in SELECTION_STATUSES:
        raise ValueError(f"selection_status must be one of {SELECTION_STATUSES}, got {selection_status!r}")
    payload: dict[str, Any] = {"selection_status": selection_status}
    if note is not None:
        payload["note"] = note
    if operator_decision is not None:
        payload["operator_decision"] = operator_decision
    append_always(
        ledger_path,
        {
            "schema_version": SCHEMA_VERSION,
            "event_type": EVENT_SELECTION_UPDATED,
            "forecast_id": forecast_id,
            "recorded_at_utc": _now_utc(),
            "payload": payload,
        },
    )


def _market_comparison(offered_odds: dict[str, float], closing_odds: dict[str, float] | None) -> dict[str, Any] | None:
    """De-vigged opening vs. closing fair probabilities, reusing the
    already-reviewed pricing engine (read-only import; registers nothing,
    touches no adapter dispatch table). ``None`` when closing_odds was
    never supplied -- never fabricated."""

    if closing_odds is None:
        return None

    from pcbf_calculator.pricing.engine import PricingFailure, analyze_market

    try:
        opening = analyze_market(offered_odds)
        closing = analyze_market(closing_odds)
    except PricingFailure as exc:
        return {"error": exc.code}

    return {
        "opening_fair_probabilities": {o["outcome"]: o["fair_probability"] for o in opening["outcomes"]},
        "closing_fair_probabilities": {o["outcome"]: o["fair_probability"] for o in closing["outcomes"]},
        "opening_bookmaker_margin": opening["bookmaker_margin"],
        "closing_bookmaker_margin": closing["bookmaker_margin"],
    }


def score_and_append(
    ledger_path: Path,
    forecast_id: str,
    actual_result: str,
    closing_odds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute Brier score and log loss for ``forecast_id`` from its own
    RECORDED ``model_probabilities`` against ``actual_result``, build the
    opening-vs-closing market comparison when ``closing_odds`` is given,
    and append the SCORED event. Raises ``KeyError`` if ``forecast_id`` has
    no RECORDED event yet -- never fabricates one."""

    if actual_result not in scoring.CLASS_ORDER:
        raise ValueError(f"actual_result must be one of {scoring.CLASS_ORDER}, got {actual_result!r}")

    records = read_all(ledger_path)
    state = latest_state(records, "forecast_id", forecast_id)
    if "model_probabilities" not in state:
        raise KeyError(f"forecast_id {forecast_id!r} has no RECORDED event in {ledger_path}")

    probabilities = state["model_probabilities"]
    brier = scoring.multiclass_brier(probabilities, actual_result)
    loss = scoring.log_loss(probabilities, actual_result)
    comparison = _market_comparison(state["offered_odds"], closing_odds)

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_SCORED,
        "forecast_id": forecast_id,
        "recorded_at_utc": _now_utc(),
        "payload": {
            "actual_result": actual_result,
            "brier_score": brier,
            "log_loss": loss,
            "closing_odds": closing_odds,
            "market_comparison": comparison,
            "settled_at_utc": _now_utc(),
        },
    }
    append_always(ledger_path, event)
    return event


def current_state(ledger_path: Path, forecast_id: str) -> dict[str, Any]:
    return latest_state(read_all(ledger_path), "forecast_id", forecast_id)


def all_forecast_ids(ledger_path: Path) -> list[str]:
    return all_entity_ids(read_all(ledger_path), "forecast_id")
