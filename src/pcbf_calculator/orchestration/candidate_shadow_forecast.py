"""Candidate SHADOW forecasting -- gives a freshly refreshed candidate its
own real prospective forecast history, over the SAME fixtures the
incumbent forecasts, entirely separate from the incumbent's own ledger,
the active adapter registry, and every decision/staking/ticket module.

    verified candidate bundle (a ``refresh-soccer-artifact`` ``candidate/``
        directory) + a Bet9ja capture
    -> the SAME ingestion + market-quality gate + cutoff semantics
       ``run-bet9ja-research`` itself uses -- reused, never reimplemented,
       via ``bet9ja_research_session.run_bet9ja_research_session``'s own
       ``forecast_override`` hook
    -> forecasting via the CANDIDATE's own adapter instance
       (``SoccerOneXTwoEloV1Adapter(data_dir=candidate_dir, ...)``) --
       never ``adapters.registry``/``cli.run_calculator``'s own default
       path, which can only ever resolve the incumbent
    -> ``candidate-shadow-forecasts.json`` / ``-abstentions.json`` /
       ``-quarantine.json`` / ``-session-report.json``
    -> one ``RECORDED`` ledger event per forecast/abstention, written to a
       PHYSICALLY SEPARATE ledger keyed by this candidate's own
       ``bundle_hash`` (see this module's own "Ledger location" note)

One command::

    python -m pcbf_calculator shadow-forecast-candidate \\
        --candidate-bundle <candidate-directory> \\
        --capture <bet9ja-capture.json> \\
        --ledger-dir <candidate-ledgers> \\
        --output-dir <run-output>

**Reuses, never reimplements:**
- ``soccer_artifact_refresh.verify_candidate_bundle`` -- called FIRST,
  before this module loads a single byte of the candidate's own model
  files. A tampered or internally inconsistent bundle aborts here, before
  any ledger or report file is touched.
- ``ingestion.bet9ja.ingest_assembled_capture`` and
  ``screening.research_batch.market_quality_gate`` -- both entirely
  ``bet9ja_research_session.run_bet9ja_research_session``'s own concern,
  called through it unchanged.
- ``bet9ja_research_session.run_bet9ja_research_session`` itself, via its
  new ``forecast_override`` parameter (added alongside this module,
  default ``None`` -- every existing caller/test of that function and of
  ``cli.run_calculator`` underneath it is unaffected). This is what
  guarantees this module processes the IDENTICAL eligible-fixture
  universe, the IDENTICAL cutoff semantics
  (``forecast_cutoff_utc = source_captured_at_utc``, never wall-clock),
  and the IDENTICAL ingestion/pricing-quality gate as
  ``run-bet9ja-research`` itself -- it is not a second, parallel
  reimplementation of any of that, it is the SAME function call.
- ``adapters.soccer_1x2_elo_v1.SoccerOneXTwoEloV1Adapter`` itself, pointed
  at the candidate's own directory via its existing ``data_dir``
  constructor parameter, with the real, shipped ``team_aliases.json``
  injected via its (new, alongside this module) ``alias_book`` parameter
  -- a candidate bundle ships no ``team_aliases.json`` of its own (it is
  not one of ``refresh-soccer-artifact``'s four candidate files), so
  without this the candidate would silently resolve fixtures against an
  EMPTY alias book and could expose a different eligible-fixture set than
  the incumbent purely because of missing aliasing, not because of any
  real difference in the two models. No adapter inference code is
  duplicated or reimplemented; this is the exact same
  ``ModelArtifact.predict_proba``/``build_feature_vector`` call path the
  incumbent's own forecasts already use.
- ``ledgers.forecast_ledger.build_recorded_event``/``append_recorded``
  (via ``forecast_ledger_writer.write_batch``, itself fully generic over
  ``ledger_path`` and already reused unchanged here) for the real,
  single-writer, atomically-preflighted append path -- this module builds
  its own events (this file's ``_build_forecast_event``/
  ``_build_abstention_event``), reusing
  ``forecast_ledger_writer._hda_probabilities``/``_hda_offered_odds``/
  ``_settlement_identity_kwargs`` for the shared H/D/A and settlement-
  identity mapping, but never a second, parallel ledger-writing
  implementation.

**Never ranks candidate forecasts for operator action.** This module
deliberately drops ``research_priority_score``/``queue_position`` (the
research-queue-ordering concept ``run-bet9ja-research`` computes for its
OWN, human-facing ranked shortlist) from every forecast it writes, and
sorts its own ``candidate-shadow-forecasts.json`` by a fixed, priority-
free tie-break (kickoff, competition, teams, fixture id) instead. A
candidate has zero real prospective evidence before this module runs at
all (see ``soccer_artifact_refresh``'s own docstring); presenting its
forecasts as a prioritized queue would misrepresent them as something an
operator should act on next, which they structurally cannot be --
``operator_decision`` is ``null`` and ``recommendation_status`` is always
``"NOT_AVAILABLE"`` on every row this module ever writes, defensively
checked (``_assert_candidate_event_stays_inert``), with no code path that
can set either to anything else.

**Stable join key for a future incumbent-vs-candidate comparison** (not
computed by this module itself -- every field it needs is already on
every row this module writes, in both the ledger and the output files)::

    capture_hash + settlement_identity + forecast_cutoff_utc + market_type

Never ``forecast_id`` -- a candidate's and the incumbent's forecast ids
for the identical fixture are DELIBERATELY different (the natural key,
``ledgers/ids.py::forecast_id``, includes ``model_version``, and a
candidate's own ``model_version`` string is never the incumbent's) --
relying on ``forecast_id`` equality to pair them up would be relying on
an accident that structurally cannot happen.

**Ledger location.** ``<ledger-dir>/<candidate_bundle_hash>/forecast-ledger.jsonl``
-- one directory per candidate, keyed by that candidate's own
``bundle_hash`` (``candidate_bundle_manifest.json``'s own field), so two
different candidates (or two refreshes of "the same" candidate that
happen to produce a different bundle) can never share one ledger file.
The FILENAME itself is ``forecast_ledger.DEFAULT_FILENAME``
(``"forecast-ledger.jsonl"``, hyphenated) -- deliberately the SAME
filename the incumbent's own ledger uses, not a differently-spelled one
-- because ``football_data_settlement.py``'s ``ingest_football_data_results``
and ``forecast_performance_report.py``'s ``run_performance_report`` both
already hardcode ``ledger_dir / forecast_ledger.DEFAULT_FILENAME``
internally, unconditionally, and are reused HERE completely unmodified:
pointing either one's own ``--ledger-dir`` at
``<ledger-dir>/<candidate_bundle_hash>/`` (this module's own per-candidate
directory) settles or reports on this candidate's ledger through the
existing, real settlement/reporting engines, with zero code changes to
either -- while remaining a physically separate file/directory from the
incumbent's own ledger the whole time. Naming the file anything other
than this exact constant would require forking or parameterizing both of
those modules, which is exactly the "parallel implementation" this
module exists to avoid.

**Atomic, idempotent, never touches the registry or the incumbent's own
files.** ``verify_candidate_bundle`` runs before anything else -- a
tampered/inconsistent bundle raises before the ledger or any output file
is touched. The ledger write itself (``write_batch``) is preflighted as
one whole batch under an OS-level lock BEFORE any output file is written
(mirroring ``bet9ja_research_session.run_session``'s own ordering) -- a
conflicting batch leaves BOTH the ledger and this run's own output files
completely unwritten. Re-running this exact command against the
identical candidate bundle + capture combination is a safe no-op: every
built event compares byte-identical to what is already on disk under the
same natural key, so ``write_batch`` reports every one of them
``DUPLICATE_SKIPPED`` and appends zero new lines. This module never
imports ``adapters.registry``, ``decision.engine``, or
``ledgers.betting_ledger`` -- a candidate bundle has no code path into
any of them, and ``adapters.registry.get_adapter("soccer")`` continues to
resolve only the incumbent's own, real ``_DATA_DIR``, completely
unaffected by anything this module does.

Explicitly out of scope: promotion, admission-registry rows, ranking for
operator action, staking, ticket construction, settlement/scoring
(``SCORED`` events -- run the existing settlement engine against this
module's own ledger location separately, exactly as documented above),
new team aliases, scheduled/automatic shadow-forecast runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from ..adapters.soccer_1x2_elo_v1 import SoccerOneXTwoEloV1Adapter, load_team_alias_book
from .bet9ja_research_session import CATEGORY
from .bet9ja_research_session import run_bet9ja_research_session as _run_bet9ja_research_session
from .soccer_artifact_refresh import verify_candidate_bundle


def _ensure_ledgers_importable() -> None:
    """Same fallback pattern ``forecast_ledger_writer.py`` already uses
    for itself -- see that module's own copy of this helper for the full
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

from . import forecast_ledger_writer  # noqa: E402
from .forecast_ledger_writer import (  # noqa: E402
    MARKET_TYPE,
    _hda_offered_odds,
    _hda_probabilities,
    _settlement_identity_kwargs,
)

SCHEMA_VERSION_SESSION_REPORT = "pcbf-candidate-shadow-session-report.v1"
SCHEMA_VERSION_FORECASTS = "pcbf-candidate-shadow-forecasts.v1"
SCHEMA_VERSION_ABSTENTIONS = "pcbf-candidate-shadow-abstentions.v1"
SCHEMA_VERSION_QUARANTINE = "pcbf-candidate-shadow-quarantine.v1"

# The ONLY model_role this module's own ledger events ever carry -- see
# ledgers/forecast_ledger.py::build_recorded_event's own docstring for
# why this exists at all (the natural key already keeps a candidate's
# rows from colliding with the incumbent's, this makes the provenance
# explicit and queryable regardless).
MODEL_ROLE_CANDIDATE_SHADOW = "CANDIDATE_SHADOW"

# Mirrors bet9ja_research_session.RECOMMENDATION_STATUS exactly -- a
# candidate shadow forecast is never more "available for a recommendation"
# than the incumbent's own RESEARCH-MODEL forecasts are.
RECOMMENDATION_STATUS = "NOT_AVAILABLE"
OPERATOR_DECISION = None
CLASSIFICATION = "RESEARCH-MODEL"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture_sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """The SAME deterministic tie-break
    ``bet9ja_research_session._fixture_sort_key`` uses for its own ranked
    queue -- reused here as the ONLY sort applied to candidate forecasts
    (no priority-score component at all -- see this module's own "Never
    ranks" docstring section)."""

    return (
        entry.get("kickoff_utc") or "",
        entry.get("source_competition_id") or "",
        entry.get("home") or "",
        entry.get("away") or "",
        entry.get("source_fixture_id") or "",
    )


def load_candidate_identity(candidate_dir: Path) -> dict[str, str]:
    """``candidate_bundle_hash``/``build_identity`` read from
    ``candidate_bundle_manifest.json`` -- callers must call
    ``verify_candidate_bundle(candidate_dir)`` first (this function itself
    performs no verification)."""

    bundle_manifest = json.loads((candidate_dir / "candidate_bundle_manifest.json").read_text(encoding="utf-8"))
    return {
        "candidate_bundle_hash": bundle_manifest["bundle_hash"],
        "build_identity": bundle_manifest["build_identity"],
    }


def _assert_candidate_event_stays_inert(event: dict[str, Any]) -> None:
    """Defense-in-depth invariant, mirroring
    ``forecast_ledger_writer._assert_event_stays_research_only``: it must
    be structurally impossible for an event this module builds to carry a
    selection, an operator decision, a non-``NOT_AVAILABLE``
    recommendation status, a classification other than
    ``RESEARCH-MODEL``, or a ``model_role`` other than
    ``CANDIDATE_SHADOW``. If this ever fires it is a bug in this module,
    never a caller input problem."""

    payload = event["payload"]
    if payload["selection"] is not None:
        raise AssertionError(f"Policy violation: selection must always be null, got {payload['selection']!r}.")
    if payload["operator_decision"] is not None:
        raise AssertionError(
            f"Policy violation: operator_decision must always be null, got {payload['operator_decision']!r}."
        )
    if payload["recommendation_status"] != RECOMMENDATION_STATUS:
        raise AssertionError(
            f"Policy violation: recommendation_status must always be {RECOMMENDATION_STATUS!r}, "
            f"got {payload['recommendation_status']!r}."
        )
    if payload["classification"] != CLASSIFICATION:
        raise AssertionError(
            f"Policy violation: classification must always be {CLASSIFICATION!r}, got {payload['classification']!r}."
        )
    if payload["model_role"] != MODEL_ROLE_CANDIDATE_SHADOW:
        raise AssertionError(
            f"Policy violation: model_role must always be {MODEL_ROLE_CANDIDATE_SHADOW!r}, "
            f"got {payload['model_role']!r}."
        )


def _build_forecast_event(
    market: dict[str, Any],
    *,
    candidate_bundle_hash: str,
    build_identity: str,
    capture_hash: str,
    capture_session_id: str,
) -> dict[str, Any]:
    forecast = market["forecast"]
    event = forecast_ledger.build_recorded_event(
        fixture_id=market["source_fixture_id"],
        sport=market["sport"],
        league=market["competition"],
        kickoff_utc=market["kickoff_utc"],
        market_type=MARKET_TYPE,
        offered_odds=_hda_offered_odds(market["market_prices"]),
        classification=market["classification_ceiling"],
        model_probabilities=_hda_probabilities(forecast["probabilities"]),
        model_version=forecast["model_version"],
        artifact_hash=forecast["model_artifact_hash"],
        input_hash=market.get("input_hash"),
        output_hash=market["calculation_hash"],
        selection_status="considered",
        capture_id=capture_session_id,
        source=market["source"],
        captured_at_utc=market["forecast_cutoff_utc"],
        selection=None,
        stop_reason=None,
        operator_decision=OPERATOR_DECISION,
        model_role=MODEL_ROLE_CANDIDATE_SHADOW,
        candidate_bundle_hash=candidate_bundle_hash,
        build_identity=build_identity,
        capture_hash=capture_hash,
        capture_session_id=capture_session_id,
        forecast_cutoff_utc=market["forecast_cutoff_utc"],
        recommendation_status=RECOMMENDATION_STATUS,
        **_settlement_identity_kwargs(forecast),
    )
    _assert_candidate_event_stays_inert(event)
    return event


def _build_abstention_event(
    item: dict[str, Any],
    *,
    candidate_bundle_hash: str,
    build_identity: str,
    capture_hash: str,
    capture_session_id: str,
) -> dict[str, Any]:
    forecast = item.get("forecast") or {}
    event = forecast_ledger.build_recorded_event(
        fixture_id=item["source_fixture_id"],
        sport=item["sport"],
        league=item["competition"],
        kickoff_utc=item["kickoff_utc"],
        market_type=MARKET_TYPE,
        offered_odds=_hda_offered_odds(item["market_prices"]),
        classification=item["classification_ceiling"],
        model_probabilities=None,
        model_version=forecast.get("model_version"),
        artifact_hash=forecast.get("model_artifact_hash"),
        input_hash=item.get("input_hash"),
        output_hash=item.get("calculation_hash"),
        selection_status="considered",
        capture_id=capture_session_id,
        source=item["source"],
        captured_at_utc=item["forecast_cutoff_utc"],
        selection=None,
        stop_reason=item["reason"],
        operator_decision=OPERATOR_DECISION,
        model_role=MODEL_ROLE_CANDIDATE_SHADOW,
        candidate_bundle_hash=candidate_bundle_hash,
        build_identity=build_identity,
        capture_hash=capture_hash,
        capture_session_id=capture_session_id,
        forecast_cutoff_utc=item["forecast_cutoff_utc"],
        recommendation_status=RECOMMENDATION_STATUS,
        **_settlement_identity_kwargs(forecast),
    )
    _assert_candidate_event_stays_inert(event)
    return event


def run_shadow_forecast_session(
    candidate_dir: Path,
    capture_path: Path,
) -> dict[str, Any]:
    """Runs the full candidate shadow-forecast pipeline against one
    already-verified-on-disk candidate bundle and one Bet9ja capture file.
    Performs no ledger or output-file I/O itself (see ``run_session`` for
    that) so it can be tested/composed directly.

    Raises ``ArtifactRefreshError`` subclasses from
    ``verify_candidate_bundle`` (imported from ``soccer_artifact_refresh``)
    if the candidate bundle is missing/tampered -- BEFORE this function
    loads a single byte of the candidate's own model files.
    """

    verify_candidate_bundle(candidate_dir)
    identity = load_candidate_identity(candidate_dir)
    candidate_manifest = json.loads((candidate_dir / "model_artifact_manifest.json").read_text(encoding="utf-8"))

    capture_bytes = capture_path.read_bytes()
    capture_hash = _sha256_bytes(capture_bytes)
    envelope = json.loads(capture_bytes.decode("utf-8"))

    # The candidate's OWN adapter instance, loading model/snapshot data
    # exclusively from candidate_dir -- never adapters.registry, never
    # the shipped adapter's own _DATA_DIR for anything but the real,
    # shared team_aliases.json (see this module's own docstring on why
    # that one file is injected separately from data_dir).
    candidate_adapter = SoccerOneXTwoEloV1Adapter(data_dir=candidate_dir, alias_book=load_team_alias_book())

    def forecast_override(category: str, fixture: dict[str, Any]) -> dict[str, Any]:
        assert category == CATEGORY, f"candidate_shadow_forecast only supports {CATEGORY!r}, got {category!r}"
        return candidate_adapter.forecast(fixture).to_dict()

    result = _run_bet9ja_research_session(envelope, forecast_override=forecast_override)

    session_report_in = result["research_session_report"]
    capture_session_id = session_report_in["source_capture_session_id"]
    counts_in = session_report_in["counts"]

    forecasts: list[dict[str, Any]] = []
    for market in result["forecast_research_ranked"]["markets"]:
        entry = {k: v for k, v in market.items() if k not in ("research_priority_score", "queue_position")}
        entry["market_type"] = MARKET_TYPE
        entry["candidate_bundle_hash"] = identity["candidate_bundle_hash"]
        entry["build_identity"] = identity["build_identity"]
        entry["capture_hash"] = capture_hash
        entry["capture_session_id"] = capture_session_id
        entry["model_role"] = MODEL_ROLE_CANDIDATE_SHADOW
        entry["operator_decision"] = OPERATOR_DECISION
        entry["recommendation_status"] = RECOMMENDATION_STATUS
        forecasts.append(entry)
    forecasts.sort(key=_fixture_sort_key)

    abstentions: list[dict[str, Any]] = []
    for item in result["forecast_abstentions"]["abstentions"]:
        entry = dict(item)
        entry["market_type"] = MARKET_TYPE
        entry["candidate_bundle_hash"] = identity["candidate_bundle_hash"]
        entry["build_identity"] = identity["build_identity"]
        entry["capture_hash"] = capture_hash
        entry["capture_session_id"] = capture_session_id
        entry["model_role"] = MODEL_ROLE_CANDIDATE_SHADOW
        abstentions.append(entry)

    quarantine: list[dict[str, Any]] = []
    for item in result["ingestion_and_screening_exclusions"]["excluded"]:
        entry = dict(item)
        entry["candidate_bundle_hash"] = identity["candidate_bundle_hash"]
        entry["build_identity"] = identity["build_identity"]
        entry["capture_hash"] = capture_hash
        entry["capture_session_id"] = capture_session_id
        quarantine.append(entry)

    if len(quarantine) + len(abstentions) + len(forecasts) != counts_in["source_fixtures_raw"]:
        raise AssertionError(
            f"Reconciliation failed: {len(quarantine)} quarantined/excluded + {len(abstentions)} "
            f"forecast abstained + {len(forecasts)} successful forecasts != "
            f"{counts_in['source_fixtures_raw']} raw fixtures. This is a bug in this module or in "
            "bet9ja_research_session's own reconciliation, never a caller input problem -- every "
            "attempted fixture must end up in exactly one bucket."
        )

    ledger_events = [
        _build_forecast_event(
            market,
            candidate_bundle_hash=identity["candidate_bundle_hash"],
            build_identity=identity["build_identity"],
            capture_hash=capture_hash,
            capture_session_id=capture_session_id,
        )
        for market in result["forecast_research_ranked"]["markets"]
    ]
    ledger_events.extend(
        _build_abstention_event(
            item,
            candidate_bundle_hash=identity["candidate_bundle_hash"],
            build_identity=identity["build_identity"],
            capture_hash=capture_hash,
            capture_session_id=capture_session_id,
        )
        for item in result["forecast_abstentions"]["abstentions"]
    )

    candidate_shadow_forecasts = {
        "schema_version": SCHEMA_VERSION_FORECASTS,
        "candidate_bundle_hash": identity["candidate_bundle_hash"],
        "build_identity": identity["build_identity"],
        "candidate_model_version": candidate_manifest["model_version"],
        "capture_hash": capture_hash,
        "source_capture_session_id": capture_session_id,
        "note": (
            "Sorted by a fixed, priority-free tie-break (kickoff, competition, teams, fixture id) "
            "-- never by research_priority_score or any other signal implying operator priority. "
            "This candidate has zero prospective evidence before this run; these forecasts are "
            "never a research queue and never an EXECUTE/PASS recommendation. Join to the "
            "incumbent's own forecast for the identical fixture via capture_hash + "
            "settlement_identity (competition_code/resolved_home_team/resolved_away_team/"
            "scheduled_date, from the forecast's own settlement_identity) + forecast_cutoff_utc + "
            "market_type -- never forecast_id, which differs by construction."
        ),
        "forecasts": forecasts,
    }
    candidate_shadow_abstentions = {
        "schema_version": SCHEMA_VERSION_ABSTENTIONS,
        "candidate_bundle_hash": identity["candidate_bundle_hash"],
        "build_identity": identity["build_identity"],
        "capture_hash": capture_hash,
        "source_capture_session_id": capture_session_id,
        "abstentions": abstentions,
    }
    candidate_shadow_quarantine = {
        "schema_version": SCHEMA_VERSION_QUARANTINE,
        "candidate_bundle_hash": identity["candidate_bundle_hash"],
        "build_identity": identity["build_identity"],
        "capture_hash": capture_hash,
        "source_capture_session_id": capture_session_id,
        "note": (
            "Ingestion quarantine and pricing-quality exclusions -- both computed independently of "
            "which model (incumbent or candidate) would go on to forecast, so this bucket is "
            "structurally identical to what the incumbent's own run-bet9ja-research would produce "
            "against the identical capture."
        ),
        "quarantine": quarantine,
    }
    candidate_shadow_session_report = {
        "schema_version": SCHEMA_VERSION_SESSION_REPORT,
        "candidate_bundle_hash": identity["candidate_bundle_hash"],
        "build_identity": identity["build_identity"],
        "candidate_model_version": candidate_manifest["model_version"],
        "capture_hash": capture_hash,
        "source_capture_session_id": capture_session_id,
        "source_captured_at_utc": session_report_in["source_captured_at_utc"],
        "forecast_cutoff_utc": session_report_in["forecast_cutoff_utc"],
        "note": (
            "Reuses ingestion/bet9ja.py, screening/research_batch.py's own market-quality gate, "
            "and bet9ja_research_session.run_bet9ja_research_session verbatim (via its own "
            "forecast_override hook) -- the identical eligible-fixture universe and cutoff "
            "semantics run-bet9ja-research itself uses, forecasted by this candidate's own "
            "adapter instance instead of the incumbent's. Every raw fixture ends up in exactly "
            "one of three buckets: successful forecast, typed forecast abstention, or quarantine "
            "(ingestion + pricing-quality exclusions, folded together). Never ranked, never a "
            "recommendation -- classification_ceiling stays RESEARCH-MODEL and "
            "recommendation_status stays NOT_AVAILABLE on every forecast; operator_decision is "
            "always null."
        ),
        "counts": {
            "source_fixtures_raw": counts_in["source_fixtures_raw"],
            "quarantined_and_excluded": len(quarantine),
            "forecast_abstained": len(abstentions),
            "successful_forecasts": len(forecasts),
        },
        "reconciles": True,
        "reason_counts": session_report_in["reason_counts"],
    }

    return {
        "candidate_shadow_forecasts": candidate_shadow_forecasts,
        "candidate_shadow_abstentions": candidate_shadow_abstentions,
        "candidate_shadow_quarantine": candidate_shadow_quarantine,
        "candidate_shadow_session_report": candidate_shadow_session_report,
        "ledger_events": ledger_events,
        "candidate_bundle_hash": identity["candidate_bundle_hash"],
        "build_identity": identity["build_identity"],
    }


def run_session(candidate_dir: Path, capture_path: Path, ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Runs the full pipeline and writes every output. The ledger write
    happens BEFORE any output file is written (mirroring
    ``bet9ja_research_session.run_session``'s own ordering): a conflicting
    batch (``forecast_ledger_writer.LedgerBatchConflictError``) or a lock
    timeout (``LedgerLockTimeoutError``) leaves BOTH the ledger and this
    run's own output files completely unwritten."""

    session = run_shadow_forecast_session(candidate_dir, capture_path)

    ledger_path = ledger_dir / session["candidate_bundle_hash"] / forecast_ledger.DEFAULT_FILENAME
    ledger_write_summary = forecast_ledger_writer.write_batch(ledger_path, session["ledger_events"])
    session["ledger_write_summary"] = ledger_write_summary
    session["ledger_path"] = str(ledger_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "candidate-shadow-forecasts.json", session["candidate_shadow_forecasts"])
    _write_json(output_dir / "candidate-shadow-abstentions.json", session["candidate_shadow_abstentions"])
    _write_json(output_dir / "candidate-shadow-quarantine.json", session["candidate_shadow_quarantine"])
    _write_json(output_dir / "candidate-shadow-session-report.json", session["candidate_shadow_session_report"])
    return session


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator shadow-forecast-candidate",
        description=__doc__,
    )
    parser.add_argument(
        "--candidate-bundle", type=Path, required=True,
        help="Path to a refresh-soccer-artifact candidate/ directory (verified before loading).",
    )
    parser.add_argument("--capture", type=Path, required=True, help="Raw Bet9ja assembled-capture envelope JSON.")
    parser.add_argument(
        "--ledger-dir", type=Path, required=True,
        help="Root directory for candidate ledgers -- this run writes under <ledger-dir>/<candidate_bundle_hash>/.",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into.")
    args = parser.parse_args(argv)

    try:
        result = run_session(args.candidate_bundle, args.capture, args.ledger_dir, args.output_dir)
    except Exception as exc:
        if isinstance(exc, forecast_ledger_writer.LedgerBatchConflictError):
            print(f"CONFLICT: {exc}")
            return 2
        if isinstance(exc, forecast_ledger_writer.LedgerLockTimeoutError):
            print(f"LOCKED: {exc}")
            return 2
        from .soccer_artifact_refresh import ArtifactRefreshError

        if isinstance(exc, ArtifactRefreshError):
            print(f"ABORTED: {exc}")
            return 2
        raise

    counts = result["candidate_shadow_session_report"]["counts"]
    summary = result["ledger_write_summary"]
    print(
        f"OK: candidate_bundle_hash={result['candidate_bundle_hash']} "
        f"{counts['successful_forecasts']} successful forecasts, "
        f"{counts['forecast_abstained']} forecast abstentions, "
        f"{counts['quarantined_and_excluded']} quarantined/excluded "
        f"({counts['source_fixtures_raw']} raw fixtures) -> {args.output_dir}"
    )
    print(
        f"LEDGER: {summary['attempted']} attempted, {summary['appended']} appended, "
        f"{summary['duplicate_skipped']} duplicate-skipped, {summary['conflicted']} conflicted "
        f"({summary['total_ledger_records']} total records) -> {result['ledger_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
