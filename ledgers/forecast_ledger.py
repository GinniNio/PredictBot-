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
- ``FIXTURE_RESCHEDULED`` -- appended when a LATER capture of an
  already-recorded fixture reports a genuinely different ``kickoff_utc``
  (and/or the ``scheduled_date`` derived from it) with every other
  identity/model field unchanged -- a real postponement/rescheduling, not
  a content conflict. The original ``RECORDED`` row is NEVER touched or
  replaced (its own ``model_probabilities``/``forecast_cutoff_utc``/
  ``captured_at_utc`` remain the immutable first-seen snapshot); this
  event only carries ``old_kickoff_utc``/``new_kickoff_utc`` (and the
  matching ``old_scheduled_date``/``new_scheduled_date``) plus a
  top-level ``kickoff_utc``/``scheduled_date`` mirroring the NEW values,
  so ``latest_state`` (which merges every event's payload in file order)
  reflects the fixture's CURRENT scheduled time to any reader -- e.g.
  ``pcbf_calculator.orchestration.football_data_settlement``'s own
  settlement-matching index, which already keys off ``latest_state``'s
  merged ``scheduled_date`` and therefore needs no code change at all to
  pick this up. Idempotent the same way ``RECORDED`` is
  (``storage.append_if_new``): re-importing the identical reschedule is a
  safe no-op; a SECOND, DIFFERENT reschedule for the same forecast_id
  (this ledger has only ever recorded one so far) is refused as a
  conflict rather than silently chained -- see
  ``pcbf_calculator.orchestration.forecast_ledger_writer``'s own
  docstring for the caller-side classification logic that decides when a
  later capture counts as a reschedule versus a genuine conflict versus a
  routine odds-only reobservation.

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
EVENT_FIXTURE_RESCHEDULED = "FIXTURE_RESCHEDULED"

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
    competition_code: str | None = None,
    resolved_home_team: str | None = None,
    resolved_away_team: str | None = None,
    scheduled_date: str | None = None,
    model_role: str | None = None,
    candidate_bundle_hash: str | None = None,
    build_identity: str | None = None,
    capture_hash: str | None = None,
    capture_session_id: str | None = None,
    forecast_cutoff_utc: str | None = None,
    recommendation_status: str | None = None,
    alias_hash: str | None = None,
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
    ``offered_odds`` is not a valid, complete market.

    ``competition_code``/``resolved_home_team``/``resolved_away_team``/
    ``scheduled_date`` are the optional, adapter-reported canonical
    identity a forecast's own adapter resolved the fixture to (see
    ``pcbf_calculator.adapters.base.ForecastResult.settlement_identity``)
    -- never re-derived or guessed here. All four are ``None`` together
    when the caller's adapter never resolved (or never reported) this
    identity. This is the join key a later, independent settlement step
    (e.g. matching a football-data.co.uk result file back to the forecast
    it settles) uses instead of the raw, unresolved capture text -- see
    ``pcbf_calculator.orchestration.football_data_settlement``.

    ``model_role``/``candidate_bundle_hash``/``build_identity``/
    ``capture_hash``/``capture_session_id``/``forecast_cutoff_utc``/
    ``recommendation_status``/``alias_hash`` are ALL optional and default
    to ``None`` --
    every existing caller (the incumbent's own
    ``forecast_ledger_writer.py``) omits them, so its own events are
    byte-for-byte unaffected by their addition. They exist for
    ``pcbf_calculator.orchestration.candidate_shadow_forecast``, which
    sets ``model_role="CANDIDATE_SHADOW"`` on every row it writes -- the
    ledger's own natural key (``fixture_id``, ``market_type``,
    ``model_version``) already keeps a candidate's rows from colliding
    with the incumbent's for the same fixture (a candidate's
    ``model_version`` is always distinct), but ``model_role`` makes that
    provenance explicit and queryable without having to know which
    ``model_version`` strings are "incumbent" versus "candidate."
    ``candidate_bundle_hash``/``build_identity`` are the candidate's own
    identity (see ``pcbf_calculator.orchestration.soccer_artifact_refresh``);
    ``capture_hash`` is a content hash of the exact capture file used
    (distinct from ``capture_id``, which is the capture's own SESSION id,
    not a hash of its bytes); ``forecast_cutoff_utc`` is carried as its
    own explicit field here rather than reusing ``captured_at_utc``
    (which the incumbent's own writer already repurposes for this same
    value -- see ``forecast_ledger_writer.py``'s own comment on that);
    ``recommendation_status`` mirrors the same always-``"NOT_AVAILABLE"``
    marker ``run-bet9ja-research`` already stamps onto every ranked
    market, carried into the ledger row itself for a candidate so a
    reader never has to assume it. ``alias_hash`` is the SHA-256 of the
    exact ``team_aliases.json`` file content the forecasting adapter used
    to resolve this fixture's teams (see
    ``adapters.soccer_1x2_elo_v1.default_team_aliases_path``) -- recorded
    so a candidate's own team-name resolution is never an unrecorded
    external dependency: a later reader can confirm a candidate's rows
    used the identical aliases the incumbent always uses (the incumbent
    has no override path and therefore only ever has one possible
    ``alias_hash`` value, re-derivable at any time from the one real,
    shipped file), and ``candidate_shadow_forecast.py`` itself refuses
    (typed ``AliasHashMismatchError``) to append further rows for the
    same candidate once a change to that file would produce a different
    hash than an earlier run under the same ledger already recorded.
    None of these eight fields is ever read by
    ``score_and_append``/``current_state``/any other function in this
    module -- they are provenance only."""

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
        "competition_code": competition_code,
        "resolved_home_team": resolved_home_team,
        "resolved_away_team": resolved_away_team,
        "scheduled_date": scheduled_date,
        "model_role": model_role,
        "candidate_bundle_hash": candidate_bundle_hash,
        "build_identity": build_identity,
        "capture_hash": capture_hash,
        "capture_session_id": capture_session_id,
        "forecast_cutoff_utc": forecast_cutoff_utc,
        "recommendation_status": recommendation_status,
        "alias_hash": alias_hash,
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


def build_fixture_rescheduled_event(
    *,
    forecast_id: str,
    old_kickoff_utc: str | None,
    new_kickoff_utc: str | None,
    old_scheduled_date: str | None = None,
    new_scheduled_date: str | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build (never appends) one ``FIXTURE_RESCHEDULED`` event for an
    ALREADY-recorded ``forecast_id``. ``kickoff_utc``/``scheduled_date``
    at the payload's top level mirror the NEW values verbatim -- see this
    module's own docstring for why that is exactly what lets
    ``latest_state`` reflect the fixture's current schedule to every
    reader with no other code change. ``old_kickoff_utc``/
    ``new_kickoff_utc`` (and the matching ``*_scheduled_date`` pair) are
    kept as their own separate fields so the reschedule itself -- what it
    actually was -- is never lost once a later event's merge overwrites
    the top-level ``kickoff_utc``/``scheduled_date`` again."""

    payload: dict[str, Any] = {
        "created_at_utc": created_at_utc or _now_utc(),
        "kickoff_utc": new_kickoff_utc,
        "scheduled_date": new_scheduled_date,
        "old_kickoff_utc": old_kickoff_utc,
        "new_kickoff_utc": new_kickoff_utc,
        "old_scheduled_date": old_scheduled_date,
        "new_scheduled_date": new_scheduled_date,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_FIXTURE_RESCHEDULED,
        "forecast_id": forecast_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }


def append_fixture_rescheduled(ledger_path: Path, event: dict[str, Any]) -> AppendResult:
    """Idempotently append a ``FIXTURE_RESCHEDULED`` event built by
    ``build_fixture_rescheduled_event`` -- same idempotency shape as
    ``append_recorded`` (a byte-identical reschedule re-imports as a safe
    no-op; a genuinely different one for the same forecast_id is refused
    as a conflict, never silently chained)."""

    return append_if_new(
        ledger_path, event, id_field="forecast_id", ignore_keys_in_payload_comparison=frozenset({"created_at_utc"})
    )


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
    closing_odds_source: str | None = None,
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
    STOP-rejected candidate) -- never fabricates a score.

    ``closing_odds_source`` names exactly which source column(s) supplied
    ``closing_odds`` (e.g. ``"PSCH/PSCD/PSCA (Pinnacle closing)"``) --
    ``None`` whenever ``closing_odds`` itself is ``None``. Recorded purely
    for audit; never used in the Brier/log-loss computation itself."""

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
            "closing_odds_source": closing_odds_source,
            "market_comparison": comparison,
            "settled_at_utc": _now_utc(),
        },
    }
    return append_terminal_if_new(ledger_path, event, id_field="forecast_id", terminal_event_types=TERMINAL_EVENTS)


def current_state(ledger_path: Path, forecast_id: str) -> dict[str, Any]:
    return latest_state(read_all(ledger_path), "forecast_id", forecast_id)


def all_forecast_ids(ledger_path: Path) -> list[str]:
    return all_entity_ids(read_all(ledger_path), "forecast_id")
