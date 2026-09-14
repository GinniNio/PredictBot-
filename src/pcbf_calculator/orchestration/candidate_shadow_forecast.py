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

**The injected alias book is never an unrecorded external dependency.**
``alias_hash`` -- the SHA-256 of the exact ``team_aliases.json`` bytes
the injected ``alias_book`` was built from (see
``adapters.soccer_1x2_elo_v1.default_team_aliases_path``) -- is recorded
on every forecast/abstention in every output document AND on every
ledger row this module writes (``ledgers/forecast_ledger.py``'s own new
``alias_hash`` field). Because the incumbent has no override path at
all (``adapters.registry`` always constructs its adapter with
``alias_book=None``, i.e. always the one real, shipped file), the
incumbent's own "alias hash" is always this same, single, re-derivable
value -- so confirming a candidate's recorded ``alias_hash`` against a
fresh ``compute_alias_hash()`` call is exactly "candidate and incumbent
used the same aliases," with nothing incumbent-side to change or record
to make that comparison possible.

``check_alias_hash_consistency`` is called before this module writes
anything: it reads whatever is ALREADY on disk at this candidate's own
ledger path (if anything) and compares the current, live
``alias_hash`` against the value recorded on that ledger's own first
RECORDED event. A mismatch -- ``team_aliases.json`` changed since an
earlier run of this exact candidate against this exact ledger location
-- raises the typed ``AliasHashMismatchError`` BEFORE any new ledger
row or output file is written, rather than silently mixing rows
resolved under two different alias books in one ledger (which could
silently change which fixtures are "eligible" between runs for a reason
having nothing to do with the candidate's own model). This is the
"fail with a typed alias-hash mismatch" half of the two acceptable
designs for reproducibility -- there is no code path that reproduces an
old run under changed aliases instead; an operator who deliberately
wants new aliases picked up must start a fresh candidate ledger
location.

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

``pair_incumbent_and_candidate_results`` (a separate, real utility
function this module also exports) pairs a SINGLE in-process incumbent
run (``bet9ja_research_session.run_bet9ja_research_session``) against a
SINGLE in-process candidate run (``run_shadow_forecast_session``) over
the identical capture, joined by ``source_fixture_id`` instead (a valid,
simpler key when both results are already in hand from the same
capture, unlike the ledger-time join key above, which exists for
exactly the case where a shared fixture id is NOT available -- reading
two ledgers back independently, potentially days apart). Every coverage
difference it finds is a typed ``comparison_status``
(``COMPARISON_BOTH_FORECAST_IDENTICAL``/``_DIFFERENT``,
``COMPARISON_CANDIDATE_ABSTAINED``, ``COMPARISON_INCUMBENT_ABSTAINED``,
``COMPARISON_BOTH_UNRESOLVED_IDENTITY``, ``COMPARISON_BOTH_ABSTAINED_OTHER``)
-- never an unlabeled "these differ."

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

from ..adapters.soccer_1x2_elo_v1 import (
    SoccerOneXTwoEloV1Adapter,
    default_team_aliases_path,
    load_team_alias_book,
)
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

from ledgers.storage import read_all  # noqa: E402

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


class AliasHashMismatchError(Exception):
    """``team_aliases.json`` changed since an earlier run of this exact
    candidate bundle against this exact ledger location -- see this
    module's own docstring, "The injected alias book is never an
    unrecorded external dependency." Raised BEFORE any new ledger row or
    output file is written for this run."""


def compute_alias_hash(data_dir: Path | None = None) -> str:
    """SHA-256 of the exact ``team_aliases.json`` bytes
    ``load_team_alias_book(data_dir)`` would parse -- always the same,
    single value for the incumbent (which never overrides ``data_dir``),
    and the value stamped as ``alias_hash`` on every row this module
    writes."""

    return _sha256_bytes(default_team_aliases_path(data_dir).read_bytes())


def check_alias_hash_consistency(ledger_path: Path, current_alias_hash: str) -> None:
    """Reads whatever is ALREADY on disk at ``ledger_path`` (a no-op if
    nothing is there yet -- a candidate's first run under a fresh ledger
    location has nothing to compare against) and compares
    ``current_alias_hash`` against the ``alias_hash`` recorded on that
    ledger's own first ``RECORDED`` event. Raises
    ``AliasHashMismatchError`` on a mismatch, BEFORE this module writes
    anything new. A ledger with existing rows that predate this field
    entirely (``alias_hash`` missing/``None``) is treated as "nothing to
    compare against" -- never a manufactured mismatch against historical
    rows that simply never recorded one."""

    if not ledger_path.exists():
        return
    records = read_all(ledger_path)
    recorded_alias_hash = None
    for record in records:
        if record.get("event_type") == forecast_ledger.EVENT_RECORDED:
            recorded_alias_hash = (record.get("payload") or {}).get("alias_hash")
            break
    if recorded_alias_hash is None:
        return
    if recorded_alias_hash != current_alias_hash:
        raise AliasHashMismatchError(
            f"team_aliases.json has changed since an earlier run against {ledger_path}: this run's "
            f"alias_hash is {current_alias_hash!r}, but that ledger's own first RECORDED event "
            f"already carries alias_hash={recorded_alias_hash!r}. Refusing to mix rows resolved "
            "under two different alias books in one candidate ledger. Start a fresh --ledger-dir "
            "location if the new aliases are an intentional change."
        )


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
    alias_hash: str,
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
        alias_hash=alias_hash,
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
    alias_hash: str,
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
        alias_hash=alias_hash,
        **_settlement_identity_kwargs(forecast),
    )
    _assert_candidate_event_stays_inert(event)
    return event


def run_shadow_forecast_session(
    candidate_dir: Path,
    capture_path: Path,
    ledger_dir: Path,
) -> dict[str, Any]:
    """Runs the full candidate shadow-forecast pipeline against one
    already-verified-on-disk candidate bundle and one Bet9ja capture file.
    Performs no ledger or output-file I/O itself (see ``run_session`` for
    that) so it can be tested/composed directly -- ``ledger_dir`` is only
    needed here to locate this candidate's own ledger path for the
    alias-hash consistency check below, before any of the (comparatively
    expensive) ingestion/pricing/forecasting work runs at all.

    Raises ``ArtifactRefreshError`` subclasses from
    ``verify_candidate_bundle`` (imported from ``soccer_artifact_refresh``)
    if the candidate bundle is missing/tampered, or
    ``AliasHashMismatchError`` if ``team_aliases.json`` has changed since
    an earlier run of this candidate against this ledger location --
    BOTH checked BEFORE this function loads a single byte of the
    candidate's own model files or does anything else.
    """

    verify_candidate_bundle(candidate_dir)
    identity = load_candidate_identity(candidate_dir)

    alias_hash = compute_alias_hash()
    ledger_path = ledger_dir / identity["candidate_bundle_hash"] / forecast_ledger.DEFAULT_FILENAME
    check_alias_hash_consistency(ledger_path, alias_hash)

    candidate_manifest = json.loads((candidate_dir / "model_artifact_manifest.json").read_text(encoding="utf-8"))

    capture_bytes = capture_path.read_bytes()
    capture_hash = _sha256_bytes(capture_bytes)
    envelope = json.loads(capture_bytes.decode("utf-8"))

    # The candidate's OWN adapter instance, loading model/snapshot data
    # exclusively from candidate_dir -- never adapters.registry, never
    # the shipped adapter's own _DATA_DIR for anything but the real,
    # shared team_aliases.json (see this module's own docstring on why
    # that one file is injected separately from data_dir).
    alias_book = load_team_alias_book()
    candidate_adapter = SoccerOneXTwoEloV1Adapter(data_dir=candidate_dir, alias_book=alias_book)

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
        entry["alias_hash"] = alias_hash
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
        entry["alias_hash"] = alias_hash
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
            alias_hash=alias_hash,
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
            alias_hash=alias_hash,
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
        "alias_hash": alias_hash,
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
        "alias_hash": alias_hash,
        "ledger_path": ledger_path,
    }


def run_session(candidate_dir: Path, capture_path: Path, ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Runs the full pipeline and writes every output. The ledger write
    happens BEFORE any output file is written (mirroring
    ``bet9ja_research_session.run_session``'s own ordering): a conflicting
    batch (``forecast_ledger_writer.LedgerBatchConflictError``) or a lock
    timeout (``LedgerLockTimeoutError``) leaves BOTH the ledger and this
    run's own output files completely unwritten. ``AliasHashMismatchError``
    (from ``run_shadow_forecast_session`` itself) leaves everything --
    ledger included -- untouched too, since it is raised before this
    function is even entered."""

    session = run_shadow_forecast_session(candidate_dir, capture_path, ledger_dir)

    ledger_path = session["ledger_path"]
    ledger_write_summary = forecast_ledger_writer.write_batch(ledger_path, session["ledger_events"])
    session["ledger_write_summary"] = ledger_write_summary
    session["ledger_path"] = str(ledger_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "candidate-shadow-forecasts.json", session["candidate_shadow_forecasts"])
    _write_json(output_dir / "candidate-shadow-abstentions.json", session["candidate_shadow_abstentions"])
    _write_json(output_dir / "candidate-shadow-quarantine.json", session["candidate_shadow_quarantine"])
    _write_json(output_dir / "candidate-shadow-session-report.json", session["candidate_shadow_session_report"])
    return session


# Typed comparison_status values pair_incumbent_and_candidate_results can
# produce -- every coverage difference between the incumbent and a
# candidate is one of these, never an unlabeled "different" bucket.
COMPARISON_BOTH_FORECAST_IDENTICAL = "BOTH_FORECAST_IDENTICAL_PROBABILITIES"
COMPARISON_BOTH_FORECAST_DIFFERENT = "BOTH_FORECAST_DIFFERENT_PROBABILITIES"
COMPARISON_CANDIDATE_ABSTAINED = "CANDIDATE_ABSTAINED_INCUMBENT_FORECAST"
COMPARISON_INCUMBENT_ABSTAINED = "INCUMBENT_ABSTAINED_CANDIDATE_FORECAST"
COMPARISON_BOTH_UNRESOLVED_IDENTITY = "BOTH_ABSTAINED_UNRESOLVED_IDENTITY"
COMPARISON_BOTH_ABSTAINED_OTHER = "BOTH_ABSTAINED_OTHER_REASON"
COMPARISON_NOT_ELIGIBLE_ON_BOTH_SIDES = "NOT_ELIGIBLE_ON_BOTH_SIDES"

# A forecast-vs-forecast pair is never labeled fully comparable
# (COMPARISON_BOTH_FORECAST_IDENTICAL/_DIFFERENT) unless the incumbent's
# and candidate's own alias_hash are both known and equal -- see
# pair_incumbent_and_candidate_results's own docstring.
ALIAS_PROVENANCE_UNAVAILABLE = "ALIAS_PROVENANCE_UNAVAILABLE"
ALIAS_HASH_MISMATCH = "ALIAS_HASH_MISMATCH"

_UNRESOLVED_IDENTITY_REASONS = {"FORECAST_COMPETITION_UNRESOLVED", "FORECAST_TEAM_UNRESOLVED"}


def _index_by_fixture_id(
    forecasts: list[dict[str, Any]],
    abstentions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for market in forecasts:
        indexed[market["source_fixture_id"]] = {
            "forecast_available": True,
            "forecast": market["forecast"],
            "reason": None,
            "alias_hash": market.get("alias_hash"),
        }
    for item in abstentions:
        indexed[item["source_fixture_id"]] = {
            "forecast_available": False,
            "forecast": None,
            "reason": item["reason"],
            "alias_hash": item.get("alias_hash"),
        }
    return indexed


def pair_incumbent_and_candidate_results(
    incumbent_result: dict[str, Any],
    candidate_session_result: dict[str, Any],
    *,
    incumbent_alias_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Pairs ``bet9ja_research_session.run_bet9ja_research_session``'s own
    incumbent result with ``run_shadow_forecast_session``'s own candidate
    result, joined by ``source_fixture_id`` -- both runs process the
    IDENTICAL capture, so the raw fixture id is a valid, simple join key
    for this one in-process comparison. This is deliberately NOT the same
    join key this module's own docstring documents for a LATER,
    out-of-process comparison against real settlement data
    (``capture_hash`` + ``settlement_identity`` + ``forecast_cutoff_utc``
    + ``market_type``) -- that one exists precisely because a shared raw
    fixture id is not available once results are read back from a ledger
    file days or weeks later, independent of the run that produced them.

    Every coverage difference between the incumbent and the candidate
    gets a typed ``comparison_status`` (one of the ``COMPARISON_*``
    constants above) -- never an unlabeled "these differ" result. Only
    ELIGIBLE fixtures (ones that reached the FORECAST stage on at least
    one side -- i.e. cleared the shared ingestion/pricing-quality gate,
    which is identical for both runs against the same capture) are
    considered; ingestion-quarantined/pricing-quality-excluded fixtures
    never reach either adapter and are out of scope for this comparison
    entirely.

    ``incumbent_alias_hash`` -- the incumbent's own ``alias_hash`` for
    this run. The incumbent's own output carries no such field itself
    (its ledger/output rows are never touched by this PR -- see this
    module's own docstring on why), so a caller comparing two LIVE,
    in-process runs over the identical capture passes
    ``compute_alias_hash()`` here (the incumbent has no override path at
    all, so this is always exactly what it used). A caller instead
    pairing against an OLDER, already-settled incumbent ledger row that
    predates this field entirely (or any other case where the incumbent
    side's alias provenance is genuinely unknown) passes ``None``.

    A forecast-vs-forecast pair is NEVER labeled fully comparable
    (``COMPARISON_BOTH_FORECAST_IDENTICAL``/``_DIFFERENT``) unless BOTH
    sides' ``alias_hash`` are known and equal -- a probability difference
    that could be entirely explained by the two sides resolving team
    names differently (rather than by any real difference between the
    two models) must never be reported as a clean, comparable
    probability difference. ``incumbent_alias_hash`` missing (``None``)
    yields ``ALIAS_PROVENANCE_UNAVAILABLE``; both present but unequal
    yields ``ALIAS_HASH_MISMATCH``; either way, the raw probabilities are
    still included so a caller can inspect them, just never under a
    status implying they were safely compared. Abstention-based statuses
    (``COMPARISON_CANDIDATE_ABSTAINED``/etc.) are unaffected -- each
    already carries its own specific, typed abstention reason regardless
    of alias provenance, and settlement/performance reporting on either
    ledger independently is likewise unaffected (this function does not
    gate anything but its own comparison output)."""

    incumbent_by_fixture = _index_by_fixture_id(
        incumbent_result["forecast_research_ranked"]["markets"],
        incumbent_result["forecast_abstentions"]["abstentions"],
    )
    candidate_by_fixture = _index_by_fixture_id(
        candidate_session_result["candidate_shadow_forecasts"]["forecasts"],
        candidate_session_result["candidate_shadow_abstentions"]["abstentions"],
    )

    pairs: list[dict[str, Any]] = []
    for fixture_id in sorted(set(incumbent_by_fixture) | set(candidate_by_fixture)):
        incumbent_side = incumbent_by_fixture.get(fixture_id)
        candidate_side = candidate_by_fixture.get(fixture_id)

        if incumbent_side is None or candidate_side is None:
            # Structurally should not happen against the identical
            # capture (ingestion/pricing-quality are model-independent
            # and reused verbatim on both sides) -- recorded as its own
            # typed status rather than silently skipped or crashing, so
            # a real divergence here is visible, not hidden.
            pairs.append(
                {
                    "source_fixture_id": fixture_id,
                    "comparison_status": COMPARISON_NOT_ELIGIBLE_ON_BOTH_SIDES,
                    "incumbent_reached_forecast_stage": incumbent_side is not None,
                    "candidate_reached_forecast_stage": candidate_side is not None,
                }
            )
            continue

        if incumbent_side["forecast_available"] and candidate_side["forecast_available"]:
            candidate_alias_hash = candidate_side["alias_hash"]
            if incumbent_alias_hash is None or candidate_alias_hash is None:
                status = ALIAS_PROVENANCE_UNAVAILABLE
            elif incumbent_alias_hash != candidate_alias_hash:
                status = ALIAS_HASH_MISMATCH
            else:
                same = incumbent_side["forecast"]["probabilities"] == candidate_side["forecast"]["probabilities"]
                status = COMPARISON_BOTH_FORECAST_IDENTICAL if same else COMPARISON_BOTH_FORECAST_DIFFERENT
            pairs.append(
                {
                    "source_fixture_id": fixture_id,
                    "comparison_status": status,
                    "incumbent_alias_hash": incumbent_alias_hash,
                    "candidate_alias_hash": candidate_alias_hash,
                    "incumbent_probabilities": incumbent_side["forecast"]["probabilities"],
                    "candidate_probabilities": candidate_side["forecast"]["probabilities"],
                }
            )
        elif incumbent_side["forecast_available"] and not candidate_side["forecast_available"]:
            pairs.append(
                {
                    "source_fixture_id": fixture_id,
                    "comparison_status": COMPARISON_CANDIDATE_ABSTAINED,
                    "candidate_abstention_reason": candidate_side["reason"],
                }
            )
        elif candidate_side["forecast_available"] and not incumbent_side["forecast_available"]:
            pairs.append(
                {
                    "source_fixture_id": fixture_id,
                    "comparison_status": COMPARISON_INCUMBENT_ABSTAINED,
                    "incumbent_abstention_reason": incumbent_side["reason"],
                }
            )
        else:
            both_unresolved = (
                incumbent_side["reason"] in _UNRESOLVED_IDENTITY_REASONS
                and candidate_side["reason"] in _UNRESOLVED_IDENTITY_REASONS
            )
            pairs.append(
                {
                    "source_fixture_id": fixture_id,
                    "comparison_status": COMPARISON_BOTH_UNRESOLVED_IDENTITY if both_unresolved else COMPARISON_BOTH_ABSTAINED_OTHER,
                    "incumbent_abstention_reason": incumbent_side["reason"],
                    "candidate_abstention_reason": candidate_side["reason"],
                }
            )
    return pairs


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
