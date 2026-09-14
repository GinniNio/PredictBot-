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

import contextlib
import fcntl
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator


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
from ledgers.storage import (  # noqa: E402
    APPENDED,
    CONFLICT,
    DUPLICATE_SKIPPED,
    decide_append,
    find_existing,
    read_all,
)

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


class LedgerLockTimeoutError(Exception):
    """Raised when ``write_batch`` cannot acquire the exclusive ledger
    lock within ``DEFAULT_LOCK_TIMEOUT_SECONDS`` -- another process is
    currently writing to the same ledger. Nothing is written when this is
    raised, same as ``LedgerBatchConflictError``."""


DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_POLL_INTERVAL_SECONDS = 0.1  # arbitrary short poll interval, unrelated to any backtest/promotion threshold


@contextlib.contextmanager
def _exclusive_ledger_lock(ledger_path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Held for ``write_batch``'s entire preflight-then-commit sequence
    -- see this module's own "Concurrency" section for why. A real,
    OS-level (POSIX advisory, ``fcntl.flock``) exclusive lock on a
    dedicated ``<ledger_path>.lock`` file next to the ledger, never the
    ledger file itself, so a plain reader (``read_all``/``current_state``)
    is never blocked by a writer. Polls rather than blocking indefinitely
    on the lock, so a writer that cannot acquire it within ``timeout``
    raises ``LedgerLockTimeoutError`` (nothing written) instead of
    hanging forever behind a stuck or crashed prior writer."""

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise LedgerLockTimeoutError(
                        f"Could not acquire the write lock for {ledger_path} within {timeout}s -- "
                        "another process appears to be writing to this ledger. Nothing was written."
                    )
                time.sleep(_LOCK_POLL_INTERVAL_SECONDS)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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


def _build_event_for_ranked_market(market: dict[str, Any]) -> dict[str, Any]:
    forecast = market["forecast"]
    event = forecast_ledger.build_recorded_event(
        fixture_id=market["source_fixture_id"],
        sport=market["sport"],
        # The football-data.co.uk short league code (e.g. "E0") this
        # forecast's adapter resolved internally is not exposed by
        # cli.py::run_calculator's own output -- using the Bet9ja
        # capture's own competition display name here (e.g. "Premier
        # League") instead is real, always-available data, never a
        # guessed or re-derived code.
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


def write_batch(ledger_path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Preflights the WHOLE batch -- against the ledger's current on-disk
    content, plus every earlier item in this same batch -- before writing
    a single byte (see this module's own docstring). Raises
    ``LedgerBatchConflictError`` and writes nothing if any item would
    conflict; otherwise commits every item via the real
    ``forecast_ledger.append_recorded`` (each real call is then
    guaranteed to return only ``APPENDED`` or ``DUPLICATE_SKIPPED``,
    never ``CONFLICT`` -- the preflight already ruled that out) and
    returns a summary dict::

        {
          "attempted": int,
          "appended": int,
          "duplicate_skipped": int,
          "conflicted": int,   # always 0 on a successful return
          "total_ledger_records": int,  # RECORDED events now on disk
        }
    Holds an exclusive, OS-level lock (see ``_exclusive_ledger_lock``) for
    this entire preflight-then-commit sequence, so two concurrent calls
    against the same ``ledger_path`` (from separate processes) cannot
    both preflight against the same stale on-disk state and both append
    -- the second call blocks until the first's whole batch has
    committed. Raises ``LedgerLockTimeoutError`` (nothing written) if the
    lock cannot be acquired within ``DEFAULT_LOCK_TIMEOUT_SECONDS``.
    """
    with _exclusive_ledger_lock(ledger_path):
        on_disk = read_all(ledger_path)
        staged: list[dict[str, Any]] = list(on_disk)

        conflicts: list[dict[str, Any]] = []
        for event in events:
            existing = find_existing(staged, "forecast_id", event["forecast_id"], event["event_type"])
            plan = decide_append(existing, event, ignore_keys_in_payload_comparison=frozenset({"created_at_utc"}))
            if plan.status == CONFLICT:
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

        return {
            "attempted": len(events),
            "appended": appended,
            "duplicate_skipped": duplicate_skipped,
            "conflicted": 0,
            "total_ledger_records": len(read_all(ledger_path)),
        }
