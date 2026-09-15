"""Idempotently writes ``run-bet9ja-research``'s own output into the
existing forecast ledger (``ledgers/forecast_ledger.py``) -- one
``RECORDED`` event per fixture that reached real pricing and a real
model-adapter attempt, whether it produced a forecast (a ranked market)
or a typed abstention. Pricing-quality-excluded and ingestion-quarantined
fixtures never reached the model at all, so they are never written here
-- this module only ever records what ``run-bet9ja-research`` itself
labels ``ranked_selections``/``forecast_abstained``.

Reuses -- never reimplements -- every ledger primitive this depends on:
``ledgers.forecast_ledger.build_recorded_event``/``append_recorded`` for
event construction and the real, single-writer append path, and
``ledgers.storage.find_existing``/``decide_append`` (the same pure
decision primitives ``append_if_new`` itself is built on -- see
``ledgers/storage.py``'s own module docstring) for this module's own
batch preflight, so "would this be a duplicate or a conflict" is decided
by exactly one piece of logic everywhere in this codebase, never two.

**First-seen forecasting -- a recapture is never a second forecast.**
Re-running this pipeline against a LATER capture of a fixture that is
still pre-match re-derives the SAME ``forecast_id`` (natural key:
``fixture_id``/``market_type``/``model_version`` -- never a function of
when it was captured), but with the bookmaker's own odds having moved in
the meantime -- a real, expected fact about a live market, not a
different forecast. Recording a SECOND, later snapshot under the SAME
``forecast_id`` would let one eventual result get scored against two
different market-comparison baselines, distorting this ledger's own
performance evidence (``ledgers/summary.py``, ``forecast_performance_report.py``).
So the FIRST recorded snapshot for a given ``(fixture, market, model)``
stays immutable, permanently -- ``write_batch`` classifies a later
capture of it as ``EXISTING_FIXTURE_REOBSERVED`` (skipped, exactly like
a true duplicate, but tallied separately so a caller can tell "already
recorded, nothing new" apart from "byte-identical resubmission") whenever
the only fields that differ are the ones a live market is EXPECTED to
move on its own -- ``offered_odds``, the odds-derived
``market_devig_probabilities``, ``input_hash``/``output_hash`` (both
derived from those same odds), and this capture's own provenance
(``capture_id``/``source``/``captured_at_utc``) -- see
``REOBSERVATION_VOLATILE_KEYS`` below for the exhaustive list. Every
OTHER payload field is compared strictly: a genuine change to a
fixture's canonical teams/competition/kickoff, its ``model_version``/
``artifact_hash``, its ``model_probabilities`` (the model's own
computed forecast, which must not silently change once recorded), or
(for an abstention) its typed ``stop_reason``, is still a real
``CONFLICT`` -- exactly as before this policy existed -- since none of
those SHOULD ever legitimately differ for the identical fixture/model
pairing; a difference there means something is actually wrong (a bad
capture, an identity collision, a non-deterministic model), not routine
market movement, and must still block the whole batch rather than being
silently accepted. A fixture never before seen (no existing record under
its ``forecast_id`` at all) is, as always, a plain ``APPENDED`` -- this
is how today's genuinely new fixtures still get recorded even in the
same batch as yesterday's reobserved ones.

Deliberately NOT built here: a separate ledger recording every day's
LATER odds observation for closing-line-movement analysis. Nothing in
this module discards that information -- a reobserved event's own
``offered_odds``/``market_devig_probabilities``/``captured_at_utc`` are
still visible in the ``forecast-research-ranked.json``/
``forecast-abstentions.json`` output files ``run_bet9ja_research_session``
already writes for THAT capture's own run, and in
``write_batch``'s own returned ``existing_fixture_reobserved_forecast_ids``
-- only the forecast LEDGER itself, whose whole purpose is one immutable
scored snapshot per fixture, declines to also become a running log of
every day's price. A dedicated market-observation ledger remains a
clean, additive option if that analysis is ever actually needed.

**Atomic batch write.** ``write_batch`` preflights the WHOLE batch --
against the ledger's current on-disk content, plus every earlier item in
this same batch (an intra-batch duplicate resolves against the batch's
own prior item, never treated as a second, independent append) -- before
writing a single byte. If any item in the batch would conflict with an
existing record under its own natural key, the preflight raises
``LedgerBatchConflictError`` and appends nothing at all: a batch that is
partly clean and partly conflicting leaves the ledger completely
unchanged, never a partial write. Only once the whole batch is confirmed
conflict-free does this module actually append (each item's real
``append_recorded`` call then only ever returns ``APPENDED`` or
``DUPLICATE_SKIPPED`` -- the preflight already ruled out ``CONFLICT``).

**Concurrency.** ``write_batch`` holds a real, OS-level exclusive lock
(POSIX advisory, ``fcntl.flock`` on a dedicated ``<ledger>.lock`` file
next to the ledger -- never the ledger file itself, so a plain reader
using ``read_all``/``current_state`` never needs to take it) for its
ENTIRE preflight-then-commit sequence. This is what makes the
preflight-then-commit atomic across separate PROCESSES, not merely
within one call: two ``run-bet9ja-research --ledger-dir`` invocations
racing against the same ledger directory serialize on this lock -- the
second blocks until the first's whole batch has fully committed, so it
can never preflight against a stale "not yet written" on-disk state and
duplicate an append (confirmed directly: without this lock, two
concurrent batches targeting the same new fixture both observed an empty
ledger during preflight and both appended, producing two lines under the
one ``forecast_id`` -- exactly the corruption this lock exists to
prevent). A writer that cannot acquire the lock within
``DEFAULT_LOCK_TIMEOUT_SECONDS`` raises ``LedgerLockTimeoutError`` rather
than hanging forever or writing anyway -- nothing is written either way.
This lock covers this module's own batch-write path only; it does not
retrofit locking onto ``ledgers/storage.py``'s lower-level primitives
(used by ``ledgers/betting_ledger.py`` too, out of scope here -- see this
module's own "no betting-ledger changes" note below).

**Natural key.** Every event's ``forecast_id`` is the ledger's own
existing deterministic key (``ledgers/ids.py::forecast_id``, hashing
``(fixture_id, market_type, model_version)``) -- ``fixture_id`` is the
Bet9ja capture's own stable ``source_fixture_id``, so re-running this
module against the SAME capture session always re-derives the SAME
``forecast_id`` for the SAME fixture, and re-running it against a capture
whose underlying fixture/price/forecast content has genuinely changed
under that same key is exactly what the conflict check is for.

**Nullable model_probabilities, real provenance where available.** A
ranked market's event carries its full H/D/A ``model_probabilities`` plus
``model_version``/``artifact_hash`` (mandatory alongside any real
probability -- enforced by ``forecast_ledger.py``'s own
``_require_model_provenance``, not re-checked here). A forecast
abstention's event carries ``model_probabilities: null`` and its exact
typed ``stop_reason`` (the adapter's own ``FORECAST_*`` code, kept
verbatim) -- ``model_version``/``artifact_hash`` are still included
because this adapter always loads its real, versioned artifact before
declining to forecast (see ``adapters/soccer_1x2_elo_v1/adapter.py``'s
own ``_no_forecast`` helper), so they are genuinely available here, never
guessed.

**Fixed, non-configurable on every event this module ever writes**::

    {
      "selection": null,
      "selection_status": "considered",
      "operator_decision": null,
      "classification": "RESEARCH-MODEL"
    }

No code path in this module can set any of these to anything else --
``_assert_event_stays_research_only`` checks this defensively on every
built event, mirroring the identical defense-in-depth pattern
``bet9ja_research_session.py`` and ``screening/research_batch.py`` both
already use for their own outputs. Ticket construction, stake sizing, and
outcome selection are entirely out of scope for this module -- it never
touches ``ledgers/betting_ledger.py`` at all.

Explicitly out of scope: results retrieval/settlement (``SCORED``
events), selection lifecycle changes (``SELECTION_UPDATED`` events),
ticket placement of any kind, ledger de-duplication or repair across
files written outside this module, database writes, automatic betting.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _ensure_ledgers_importable() -> None:
    """Makes ``ledgers`` importable regardless of which of the two real
    layouts this module is running under. Once packaged into a wheel
    (``pyproject.toml``), ``ledgers`` is installed as a top-level package
    directly alongside ``pcbf_calculator`` -- already importable
    normally, nothing to do. In a repo checkout run via ``PYTHONPATH=src``
    (only ``src/`` on the path, not the repo root), ``ledgers`` is a
    sibling of ``src/`` at the repo root instead -- not on the path at
    all -- so this falls back to inserting that repo root, the same
    pattern ``ledgers/forecast_ledger.py`` and every ``tests/test_ledgers_*.py``
    module already use for themselves. Never inserts a path that doesn't
    actually contain a real ``ledgers`` package, so a genuinely broken
    install still fails with a normal, honest ``ImportError`` below."""

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
from ledgers.storage import (  # noqa: E402
    APPENDED,
    CONFLICT,
    DUPLICATE_SKIPPED,
    _payload_equal_ignoring,
    decide_append,
    find_existing,
    latest_state,
    read_all,
)

__all__ = [
    "LedgerBatchConflictError",
    "LedgerLockTimeoutError",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "REOBSERVATION_VOLATILE_KEYS",
    "RESCHEDULE_TOLERATED_KEYS",
    "build_ledger_events",
    "write_batch",
]

MARKET_TYPE = "1X2"
CLASSIFICATION = "RESEARCH-MODEL"
SELECTION_STATUS = "considered"

# This adapter's own outcome vocabulary (home_win/draw/away_win --
# docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md section 1) and this platform's
# own market_prices convention (home/draw/away -- cli.py's own
# _SOCCER_1X2_OUTCOME_MAP) both map onto the forecast ledger's own H/D/A
# convention (see ledgers/examples/forecast_example.json) -- a fixed,
# narrowly-scoped correspondence, never guessed from string similarity.
_PROBABILITY_OUTCOME_MAP = {"home_win": "H", "draw": "D", "away_win": "A"}
_MARKET_PRICE_OUTCOME_MAP = {"home": "H", "draw": "D", "away": "A"}

# Payload fields a live bookmaker market is EXPECTED to move on its own,
# for the exact same underlying fixture -- see this module's own
# "First-seen forecasting" docstring section above for the full policy.
# An ALLOWLIST, not a denylist: every field NOT named here is compared
# strictly, so a newly-added payload field defaults to blocking a real
# conflict (fail closed) rather than silently being ignored.
#   - offered_odds / market_devig_probabilities: the bookmaker's own
#     current price and its de-vigged fair probability, both expected to
#     differ from one day's capture to the next for an unresolved fixture.
#   - input_hash / output_hash: both derived FROM offered_odds by the
#     pricing engine, so they change whenever the odds do, even though
#     nothing about the fixture or the model itself changed.
#   - capture_id / source / captured_at_utc: this capture RUN's own
#     provenance (which session captured it, when) -- expected to be
#     different every time this pipeline runs, by definition.
REOBSERVATION_VOLATILE_KEYS = frozenset(
    {
        "offered_odds",
        "market_devig_probabilities",
        "input_hash",
        "output_hash",
        "capture_id",
        "source",
        "captured_at_utc",
    }
)

# The reobservation check must ignore everything decide_append's own
# plain-conflict check already ignores (created_at_utc -- regenerated
# fresh on every build_recorded_event call, so it always differs between
# two separate builds regardless of whether anything real changed) PLUS
# every genuinely volatile field above -- never just the latter alone, or
# every single reobservation would still register a spurious difference
# on created_at_utc and never actually classify as one.
_REOBSERVATION_IGNORE_KEYS = REOBSERVATION_VOLATILE_KEYS | frozenset({"created_at_utc"})

# The ADDITIONAL fields a genuine fixture reschedule (a postponement, a
# kickoff-time correction) legitimately changes, on top of everything
# REOBSERVATION_VOLATILE_KEYS already tolerates (a rescheduled fixture's
# later capture also has fresh odds/provenance, exactly like any other
# recapture). Deliberately just these two -- an ALLOWLIST, same fail-
# closed discipline as REOBSERVATION_VOLATILE_KEYS above: canonical teams,
# competition, model_version/artifact_hash, model_probabilities, and an
# abstention's stop_reason are NEVER in this set, so a genuine change to
# any of THOSE alongside a kickoff change is still a real CONFLICT, never
# silently downgraded to a reschedule (see write_batch's own docstring).
RESCHEDULE_TOLERATED_KEYS = frozenset({"kickoff_utc", "scheduled_date"})

_RESCHEDULE_IGNORE_KEYS = _REOBSERVATION_IGNORE_KEYS | RESCHEDULE_TOLERATED_KEYS


class LedgerBatchConflictError(Exception):
    """Raised by ``write_batch`` when preflighting the batch finds one or
    more items that would conflict with an existing ledger record under
    the same natural key (``forecast_id``) -- carries every conflicting
    item so a caller can report all of them at once, never just the
    first. Nothing is written to the ledger when this is raised."""

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.conflicts = conflicts
        summary = "; ".join(
            f"forecast_id={c['forecast_id']!r} (source_fixture_id={c.get('source_fixture_id')!r})" for c in conflicts
        )
        super().__init__(
            f"{len(conflicts)} record(s) would conflict with existing ledger content under an "
            f"unchanged natural key -- nothing written: {summary}"
        )


DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
"""Re-exported from ``ledgers.locking`` for backward compatibility with
existing importers of this module -- the actual lock implementation
(``LedgerLockTimeoutError``, the poll loop) now lives there, shared with
``pcbf_calculator.orchestration.football_data_settlement``'s own
settlement batch. See ``ledgers/locking.py``'s own module docstring."""


def _hda_probabilities(probabilities: dict[str, float]) -> dict[str, float]:
    return {_PROBABILITY_OUTCOME_MAP[key]: value for key, value in probabilities.items()}


def _hda_offered_odds(market_prices: dict[str, float]) -> dict[str, float]:
    # market_prices is the fixture's own "market" dict (ingestion/bet9ja.py
    # shape: {"family": "1X2", "home": ..., "draw": ..., "away": ...}) --
    # only the three outcome keys map to H/D/A, "family" is not an outcome.
    return {_MARKET_PRICE_OUTCOME_MAP[key]: value for key, value in market_prices.items() if key in _MARKET_PRICE_OUTCOME_MAP}


def _assert_event_stays_research_only(event: dict[str, Any]) -> None:
    """Defense-in-depth invariant: it must be structurally impossible for
    an event this module builds to carry a selection, an operator
    decision, or a classification other than the fixed values documented
    in this module's own docstring. If this ever fires it is a bug in
    this module, never a caller input problem."""

    payload = event["payload"]
    if payload["selection"] is not None:
        raise AssertionError(f"Policy violation: selection must always be null, got {payload['selection']!r}.")
    if payload["selection_status"] != SELECTION_STATUS:
        raise AssertionError(
            f"Policy violation: selection_status must always be {SELECTION_STATUS!r}, "
            f"got {payload['selection_status']!r}."
        )
    if payload["operator_decision"] is not None:
        raise AssertionError(
            f"Policy violation: operator_decision must always be null, got {payload['operator_decision']!r}."
        )
    if payload["classification"] != CLASSIFICATION:
        raise AssertionError(
            f"Policy violation: classification must always be {CLASSIFICATION!r}, got {payload['classification']!r}."
        )


def _settlement_identity_kwargs(forecast: dict[str, Any]) -> dict[str, Any]:
    """Extracts the adapter-reported ``settlement_identity`` (see
    ``pcbf_calculator.adapters.base.ForecastResult.settlement_identity``)
    into ``build_recorded_event``'s own kwarg names -- all four ``None``
    together when the adapter never resolved (or never reported) an
    identity, exactly like every other nullable-together provenance group
    this ledger already carries (e.g. model provenance)."""

    identity = forecast.get("settlement_identity") or {}
    return {
        "competition_code": identity.get("competition_code"),
        "resolved_home_team": identity.get("resolved_home_team"),
        "resolved_away_team": identity.get("resolved_away_team"),
        "scheduled_date": identity.get("scheduled_date"),
    }


def _build_event_for_ranked_market(market: dict[str, Any]) -> dict[str, Any]:
    forecast = market["forecast"]
    event = forecast_ledger.build_recorded_event(
        fixture_id=market["source_fixture_id"],
        sport=market["sport"],
        # The Bet9ja capture's own competition display name (e.g.
        # "Premier League") -- real, always-available data. The
        # forecast's adapter-resolved canonical identity (short league
        # code, canonical team names, scheduled date), when the adapter
        # reports one, is carried separately below via
        # ``_settlement_identity_kwargs`` -- see
        # ``ForecastResult.settlement_identity``'s own docstring for why
        # a settlement step must join on THAT identity, never on this
        # free-text display name.
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
        selection_status=SELECTION_STATUS,
        capture_id=market["source_capture_session_id"],
        source=market["source"],
        # This pipeline's own forecast_cutoff_utc IS the capture's own
        # source_captured_at_utc (see bet9ja_research_session.py's own
        # module docstring) -- there is no separate "forecast cutoff"
        # field in the ledger schema, so this is how that value is
        # preserved through to the ledger record, honestly (the same
        # real timestamp, not a second, different one).
        captured_at_utc=market["forecast_cutoff_utc"],
        selection=None,
        stop_reason=None,
        operator_decision=None,
        **_settlement_identity_kwargs(forecast),
    )
    _assert_event_stays_research_only(event)
    return event


def _build_event_for_abstention(item: dict[str, Any]) -> dict[str, Any]:
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
        selection_status=SELECTION_STATUS,
        capture_id=item["source_capture_session_id"],
        source=item["source"],
        captured_at_utc=item["forecast_cutoff_utc"],
        selection=None,
        stop_reason=item["reason"],
        operator_decision=None,
        **_settlement_identity_kwargs(forecast),
    )
    _assert_event_stays_research_only(event)
    return event


def build_ledger_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Build (never appends) one ``RECORDED`` event per ranked market and
    per forecast abstention in ``result`` -- the dict
    ``bet9ja_research_session.run_bet9ja_research_session`` returns.
    Pricing-quality-excluded and ingestion-quarantined fixtures never
    reached the model and are not forecasts or forecast abstentions --
    they are never represented here.

    Order is deterministic (ranked markets in their own queue order, then
    abstentions in their own sort order -- both already fixed by
    ``run_bet9ja_research_session`` itself), so building the same
    ``result`` twice always produces the same event list in the same
    order.
    """
    events = [_build_event_for_ranked_market(market) for market in result["forecast_research_ranked"]["markets"]]
    events.extend(_build_event_for_abstention(item) for item in result["forecast_abstentions"]["abstentions"])
    return events


def _merged_recorded_payload(staged: list[dict[str, Any]], forecast_id: str) -> dict[str, Any]:
    """The CURRENT merged view of ``forecast_id``'s own RECORDED payload,
    with any already-applied ``FIXTURE_RESCHEDULED`` event's
    ``kickoff_utc``/``scheduled_date`` folded in -- never the raw,
    possibly-stale first-seen ``RECORDED`` payload alone. This is what a
    later capture's own payload must be compared against: a fixture
    already rescheduled once, recaptured again with that SAME (now
    current) kickoff, must classify as a routine ``EXISTING_FIXTURE_
    REOBSERVED`` (only odds/provenance differ), never trigger a second,
    spurious reschedule attempt every single day forever. Strips
    ``latest_state``'s own bookkeeping keys (``_event_types_seen``,
    ``_event_count``, the id field itself) since the caller compares this
    directly against a plain event payload dict, which never carries
    them."""

    state = latest_state(staged, "forecast_id", forecast_id)
    state.pop("_event_types_seen", None)
    state.pop("_event_count", None)
    state.pop("forecast_id", None)
    # FIXTURE_RESCHEDULED's own audit-only fields (old/new kickoff and
    # scheduled_date -- see build_fixture_rescheduled_event) are never
    # part of a RECORDED payload's own shape; strip them so this merged
    # view compares cleanly against a plain event payload, which never
    # carries them either.
    state.pop("old_kickoff_utc", None)
    state.pop("new_kickoff_utc", None)
    state.pop("old_scheduled_date", None)
    state.pop("new_scheduled_date", None)
    return state


def write_batch(ledger_path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Preflights the WHOLE batch -- against the ledger's current on-disk
    content, plus every earlier item in this same batch -- before writing
    a single byte (see this module's own docstring). Raises
    ``LedgerBatchConflictError`` and writes nothing if any item would
    genuinely conflict (an immutable field actually changed -- see
    ``REOBSERVATION_VOLATILE_KEYS``); otherwise commits every NEW,
    changed-only-in-volatile-fields, or changed-only-in-kickoff item via
    the real ``forecast_ledger.append_recorded``/``append_fixture_
    rescheduled`` (each real call is then guaranteed to return only
    ``APPENDED`` or ``DUPLICATE_SKIPPED``, never ``CONFLICT`` -- the
    preflight already ruled that out) and returns a summary dict::

        {
          "attempted": int,
          "appended": int,
          "duplicate_skipped": int,
          "existing_fixture_reobserved": int,
          "existing_fixture_reobserved_forecast_ids": list[str],
          "existing_fixture_rescheduled": int,
          "existing_fixture_rescheduled_forecast_ids": list[str],
          "conflicted": int,   # always 0 on a successful return
          "total_ledger_records": int,  # RECORDED events now on disk
        }

    An item classified ``existing_fixture_reobserved`` is a fixture whose
    FIRST recorded snapshot already exists under its own ``forecast_id``
    -- this call never appends a second one, never touches the original,
    and never raises for it; the original stays the sole, immutable,
    authoritative record. This is exactly how a batch that mixes
    genuinely new fixtures with already-recorded ones (recaptured with
    nothing but moved bookmaker odds) still gets its new fixtures
    written -- see this module's own "First-seen forecasting" docstring
    section above.

    **Fixture reschedules.** A later capture whose payload differs from
    the fixture's CURRENT state (its original ``RECORDED`` row, plus any
    ``FIXTURE_RESCHEDULED`` event already applied -- see
    ``_merged_recorded_payload``) ONLY in ``kickoff_utc``/
    ``scheduled_date`` (``RESCHEDULE_TOLERATED_KEYS``) -- on top of
    whatever ``REOBSERVATION_VOLATILE_KEYS`` already tolerates -- is a
    genuine reschedule, never a content conflict: this function appends a
    ``FIXTURE_RESCHEDULED`` event (``forecast_ledger.
    build_fixture_rescheduled_event``) carrying the old and new kickoff
    (and scheduled_date), counted separately as
    ``existing_fixture_rescheduled``. The original ``RECORDED`` row is
    NEVER touched, replaced, or duplicated -- no new forecast is ever
    generated for a rescheduled fixture, exactly like a reobservation.
    Every OTHER field -- canonical teams/competition, ``model_version``/
    ``artifact_hash``, ``model_probabilities``, an abstention's
    ``stop_reason`` -- is still compared strictly: a genuine change to
    ANY of those, even alongside a kickoff change, is still a real
    ``CONFLICT`` (see ``RESCHEDULE_TOLERATED_KEYS``'s own comment). A
    fixture already rescheduled once and recaptured again with that SAME
    (now current) kickoff correctly classifies as a routine
    ``existing_fixture_reobserved`` on the next run, never a second
    reschedule attempt (comparison is always against the CURRENT merged
    state, never the stale original). A SECOND, DIFFERENT reschedule for
    the same fixture within one run is refused as a real conflict --
    chaining more than one reschedule per fixture is out of scope for
    this narrow fix.

    Holds an exclusive, OS-level lock (``ledgers.locking.exclusive_ledger_lock``)
    for this entire preflight-then-commit sequence, so two concurrent calls
    against the same ``ledger_path`` (from separate processes) cannot
    both preflight against the same stale on-disk state and both append
    -- the second call blocks until the first's whole batch has
    committed. Raises ``LedgerLockTimeoutError`` (nothing written) if the
    lock cannot be acquired within ``DEFAULT_LOCK_TIMEOUT_SECONDS``.
    """
    with exclusive_ledger_lock(ledger_path, timeout=DEFAULT_LOCK_TIMEOUT_SECONDS):
        on_disk = read_all(ledger_path)
        staged: list[dict[str, Any]] = list(on_disk)

        conflicts: list[dict[str, Any]] = []
        reobserved_ids: list[str] = []
        rescheduled_ids: list[str] = []
        skip_ids: set[str] = set()
        reschedule_events_to_append: list[dict[str, Any]] = []
        for event in events:
            existing = find_existing(staged, "forecast_id", event["forecast_id"], event["event_type"])
            plan = decide_append(existing, event, ignore_keys_in_payload_comparison=frozenset({"created_at_utc"}))
            if plan.status == CONFLICT:
                # A strict payload diff found a real difference against
                # the ORIGINAL record -- but compare against the fixture's
                # CURRENT merged state (folding in any reschedule already
                # applied), not the stale original, before deciding what
                # kind of difference this actually is.
                merged_payload = _merged_recorded_payload(staged, event["forecast_id"])
                if _payload_equal_ignoring(merged_payload, event.get("payload", {}), _REOBSERVATION_IGNORE_KEYS):
                    # Every differing key is one this fixture's own live
                    # market is EXPECTED to move on its own: a routine
                    # reobservation, never a real conflict -- skip it
                    # (never appended, never compared against again -- the
                    # currently-staged state already reflects it).
                    reobserved_ids.append(event["forecast_id"])
                    skip_ids.add(event["forecast_id"])
                    continue
                if _payload_equal_ignoring(merged_payload, event.get("payload", {}), _RESCHEDULE_IGNORE_KEYS):
                    # The only OTHER differing keys are kickoff_utc/
                    # scheduled_date: a genuine reschedule, not a content
                    # conflict. Never rebuilds/replaces the original
                    # RECORDED row -- only a new FIXTURE_RESCHEDULED event
                    # is queued, itself preflighted below exactly like any
                    # other event (idempotent re-import, hard conflict on
                    # a second, different reschedule).
                    reschedule_event = forecast_ledger.build_fixture_rescheduled_event(
                        forecast_id=event["forecast_id"],
                        old_kickoff_utc=merged_payload.get("kickoff_utc"),
                        new_kickoff_utc=event["payload"].get("kickoff_utc"),
                        old_scheduled_date=merged_payload.get("scheduled_date"),
                        new_scheduled_date=event["payload"].get("scheduled_date"),
                    )
                    r_existing = find_existing(
                        staged, "forecast_id", reschedule_event["forecast_id"], reschedule_event["event_type"]
                    )
                    r_plan = decide_append(
                        r_existing, reschedule_event, ignore_keys_in_payload_comparison=frozenset({"created_at_utc"})
                    )
                    if r_plan.status == CONFLICT:
                        conflicts.append(
                            {
                                "forecast_id": event["forecast_id"],
                                "source_fixture_id": event["payload"].get("fixture_id"),
                                "new": reschedule_event,
                                "existing": r_plan.conflicting_record,
                            }
                        )
                    elif r_plan.status == APPENDED:
                        staged.append(reschedule_event)
                        reschedule_events_to_append.append(reschedule_event)
                        rescheduled_ids.append(event["forecast_id"])
                    # DUPLICATE_SKIPPED: an identical reschedule is already
                    # staged/on-disk -- nothing new to append, and this
                    # forecast_id is still correctly skipped below either way.
                    skip_ids.add(event["forecast_id"])
                    continue
                conflicts.append(
                    {
                        "forecast_id": event["forecast_id"],
                        "source_fixture_id": event["payload"].get("fixture_id"),
                        "new": event,
                        "existing": plan.conflicting_record,
                    }
                )
            elif plan.status == APPENDED:
                staged.append(event)
            # DUPLICATE_SKIPPED: leave `staged` as-is -- a second identical
            # item later in the same batch still correctly compares against
            # the ALREADY-staged (or on-disk) original, not against itself.

        if conflicts:
            raise LedgerBatchConflictError(conflicts)

        appended = 0
        duplicate_skipped = 0
        for event in events:
            if event["forecast_id"] in skip_ids:
                continue  # existing_fixture_reobserved/rescheduled -- never appended, original stays authoritative
            result = forecast_ledger.append_recorded(ledger_path, event)
            if result.status == APPENDED:
                appended += 1
            elif result.status == DUPLICATE_SKIPPED:
                duplicate_skipped += 1
            else:  # pragma: no cover -- ruled out by the preflight above
                raise AssertionError(
                    f"Policy violation: preflight found no conflict for forecast_id={event['forecast_id']!r}, "
                    f"but the real append returned CONFLICT. This is a bug in this module's preflight logic."
                )

        for reschedule_event in reschedule_events_to_append:
            r_result = forecast_ledger.append_fixture_rescheduled(ledger_path, reschedule_event)
            if r_result.status not in (APPENDED, DUPLICATE_SKIPPED):  # pragma: no cover -- ruled out by the preflight above
                raise AssertionError(
                    f"Policy violation: preflight found no conflict for reschedule of "
                    f"forecast_id={reschedule_event['forecast_id']!r}, but the real append returned CONFLICT. "
                    "This is a bug in this module's preflight logic."
                )

        return {
            "attempted": len(events),
            "appended": appended,
            "duplicate_skipped": duplicate_skipped,
            "existing_fixture_reobserved": len(reobserved_ids),
            "existing_fixture_reobserved_forecast_ids": sorted(set(reobserved_ids)),
            "existing_fixture_rescheduled": len(rescheduled_ids),
            "existing_fixture_rescheduled_forecast_ids": sorted(set(rescheduled_ids)),
            "conflicted": 0,
            "total_ledger_records": len(read_all(ledger_path)),
        }
