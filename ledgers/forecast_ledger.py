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
  conflict, never silently overwritten. ``model_probabilities`` is
  NULLABLE -- a STOP-rejected candidate that never had a model run on it
  is recorded with ``model_probabilities: null``, never a fabricated
  all-zero (or any other) probability distribution. WHEN
  ``model_probabilities`` is present, ``model_version``/``artifact_hash``/
  ``output_hash`` must also be present (provenance is mandatory alongside
  any real probability -- see ``_require_model_provenance``); this
  package never lets a forecast's mere presence in the ledger imply the
  model that produced it is admitted, registered, or promotable --
  ``classification`` is passed through byte-for-byte from the caller,
  never inferred or upgraded from whether probabilities/provenance are
  present.
- ``SELECTION_UPDATED`` -- the operator's own considered/shortlisted/
  placed/skipped decision, changed over the forecast's lifecycle. Never
  gated for idempotency (unlike RECORDED/SCORED) because a real sequence
  of distinct selection changes over time is expected, not a duplicate
  import of the same one.
- ``SCORED`` -- appended once the real result is known: Brier score, log
  loss (``ledgers/scoring.py``, self-contained, never imported from the
  research backtest module), and an opening-vs-closing market comparison
  reusing the already-reviewed pricing engine (read-only import, registers
  nothing). A ONE-TIME lifecycle transition
  (``storage.append_terminal_if_new``): re-scoring with the identical
  result/closing-odds input is a safe no-op (never a duplicated score);
  re-scoring with a DIFFERENT result or closing odds is refused as a
  conflict, never silently overwriting the original score.

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
from ledgers.storage import (
    AppendResult,
    all_entity_ids,
    append_event,
    append_if_new,
    append_terminal_if_new,
    latest_state,
    read_all,
)

SCHEMA_VERSION = "1.0.0"
SCHEMA_NAME = "forecast_ledger.v1"

EVENT_RECORDED = "RECORDED"
EVENT_SELECTION_UPDATED = "SELECTION_UPDATED"
EVENT_SCORED = "SCORED"

TERMINAL_EVENTS = {EVENT_SCORED}

SELECTION_STATUSES = ("considered", "shortlisted", "placed", "skipped")

DEFAULT_FILENAME = "forecast-ledger.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_model_provenance(
    model_probabilities: dict[str, float] | None,
    model_version: str | None,
    artifact_hash: str | None,
    output_hash: str | None,
) -> None:
    """Ledger-level guardrail: a real model_probabilities value must never
    be recorded without knowing which model/version/artifact produced
    it. Never enforced the other way around -- a STOP-rejected candidate
    with no model run at all legitimately has every one of these fields
    null."""

    if model_probabilities is None:
        return
    missing = [
        name
        for name, value in (("model_version", model_version), ("artifact_hash", artifact_hash), ("output_hash", output_hash))
        if not value
    ]
    if missing:
        raise ValueError(
            f"model_probabilities was supplied but provenance field(s) {missing} were not -- "
            "a real probability must always carry model_version/artifact_hash/output_hash."
        )


def build_recorded_event(
    *,
    fixture_id: str,
    sport: str,
    league: str,
    kickoff_utc: str,
    market_type: str,
    offered_odds: dict[str, float],
    classification: str,
    model_probabilities: dict[str, float] | None = None,
    model_version: str | None = None,
    artifact_hash: str | None = None,
    input_hash: str | None = None,
    output_hash: str | None = None,
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
    model_version)`` -- see ``ledgers/ids.py`` (``model_version`` defaults
    to the literal string ``"no_model"`` in the natural key when no model
    ran at all, e.g. a STOP-rejected candidate, so its forecast_id is
    still stable and distinct from a real model's forecast for the same
    fixture/market).

    ``batch_id``/``capture_id``/``source``/``captured_at_utc`` trace a
    forecast back to the capture session (e.g. a manual Bet9ja JSON
    capture, or later a capture tool's output) that supplied its raw
    prices -- distinct from ``created_at_utc``, which is when THIS
    forecast was generated from that captured data. ``stop_reason`` is
    non-null when this candidate was excluded by a hard rule (e.g. a
    jurisdictional STOP) before or instead of a full forecast score --
    such a record is still written (every candidate is recorded, STOP or
    not) but MUST be excluded from any ranked/candidate view (see
    ``ledgers/summary.py::ranked_forecasts``) and from ticket legs (see
    ``betting_ledger.check_legs_not_stopped``). ``operator_decision`` is
    the human's own recorded judgement, distinct from the mechanical
    ``selection_status`` pipeline stage -- and never a way to bypass the
    STOP-to-ticket check, which reads ``stop_reason`` alone.
    ``market_devig_probabilities`` is computed here (never taken from the
    caller) via the already-reviewed pricing engine, read-only, from
    ``offered_odds`` -- ``None`` (with an error code, never a crash) if
    ``offered_odds`` is not a valid, complete market."""

    if selection_status not in SELECTION_STATUSES:
        raise ValueError(f"selection_status must be one of {SELECTION_STATUSES}, got {selection_status!r}")
    _require_model_provenance(model_probabilities, model_version, artifact_hash, output_hash)

    fc_id = ids.forecast_id(fixture_id, market_type, model_version or "no_model")
    payload: dict[str, Any] = {
        "created_at_utc": created_at_utc or _now_utc(),
        "fixture_id": fixture_id,
        "sport": sport,
        "league": league,
        "kickoff_utc": kickoff_utc,
        "market_type": market_type,
        "offered_odds": offered_odds,
        "market_devig_probabilities": _fair_probabilities(offered_odds),
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
    """De-vigged fair H/D/A probabilities from ``offered_odds`` -- named
    explicitly as MARKET's own de-vigged probability, never the
    ambiguous "market_probability" (which could be misread as an offered/
    implied price rather than a de-vigged fair one). Reuses the already-
    reviewed pricing engine (read-only import; registers nothing, touches
    no adapter dispatch table). ``None`` (with an error code) rather than
    a crash when ``offered_odds`` is missing or not a valid, complete
    market -- a forecast is still recorded either way."""

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
    ``build_recorded_event``. Ignores ``created_at_utc`` when comparing
    against an existing record: two independent builds of the same
    logical forecast, neither passing ``created_at_utc`` explicitly (it
    then defaults to "now"), must still compare as the same forecast."""

    return append_if_new(ledger_path, event, id_field="forecast_id", ignore_keys_in_payload_comparison=frozenset({"created_at_utc"}))


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
    append_event(
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
        "opening_market_devig_probabilities": {o["outcome"]: o["fair_probability"] for o in opening["outcomes"]},
        "closing_market_devig_probabilities": {o["outcome"]: o["fair_probability"] for o in closing["outcomes"]},
        "opening_bookmaker_margin": opening["bookmaker_margin"],
        "closing_bookmaker_margin": closing["bookmaker_margin"],
    }


def score_and_append(
    ledger_path: Path,
    forecast_id: str,
    actual_result: str,
    closing_odds: dict[str, float] | None = None,
) -> AppendResult:
    """Compute Brier score and log loss for ``forecast_id`` from its own
    RECORDED ``model_probabilities`` against ``actual_result``, build the
    opening-vs-closing market comparison when ``closing_odds`` is given,
    and append the SCORED event -- a ONE-TIME lifecycle transition (see
    ``storage.append_terminal_if_new``): re-running this with the
    identical ``actual_result``/``closing_odds`` is a safe no-op
    (``DUPLICATE_SKIPPED``); a DIFFERENT result or closing odds is
    refused (``CONFLICT``), never silently re-scored. Raises ``KeyError``
    if ``forecast_id`` has no RECORDED event at all, and ``ValueError``
    if it does but has no ``model_probabilities`` to score (e.g. a
    STOP-rejected candidate) -- never fabricates a score."""

    if actual_result not in scoring.CLASS_ORDER:
        raise ValueError(f"actual_result must be one of {scoring.CLASS_ORDER}, got {actual_result!r}")

    records = read_all(ledger_path)
    state = latest_state(records, "forecast_id", forecast_id)
    if "fixture_id" not in state:
        raise KeyError(f"forecast_id {forecast_id!r} has no RECORDED event in {ledger_path}")
    if state.get("model_probabilities") is None:
        raise ValueError(f"forecast_id {forecast_id!r} has no model_probabilities to score (model_probabilities is null)")

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
    return append_terminal_if_new(ledger_path, event, id_field="forecast_id", terminal_event_types=TERMINAL_EVENTS)


def current_state(ledger_path: Path, forecast_id: str) -> dict[str, Any]:
    return latest_state(read_all(ledger_path), "forecast_id", forecast_id)


def all_forecast_ids(ledger_path: Path) -> list[str]:
    return all_entity_ids(read_all(ledger_path), "forecast_id")
