"""betting-ledger.jsonl: one append-only event stream per ticket_id.

A ticket is recorded ONCE at PLACED time with its legs nested inside that
single event's payload (never duplicated per leg, and never duplicating
``total_stake`` once per system-bet combination) -- see
``ledgers/schemas/betting_ledger.v1.schema.json``. Settlement is a later,
separate event (``SETTLED``/``VOIDED``/``CASHED_OUT``) referencing the same
``ticket_id``.

Every stake, return, profit/loss, and placed-odds value in this module is
a ``Decimal`` (``ledgers/money.py``), stored in JSON as a decimal STRING
-- a binary float is rejected outright (``money.to_decimal`` raises
``MoneyValueError``), never silently accepted and rounded away.

Ticket-level profit/loss is computed here from the ticket's own stake and
odds; it is a DIFFERENT calculation from forecast scoring
(``ledgers/forecast_ledger.py``'s Brier score/log loss) and the two are
never conflated -- a ticket can win money on a poorly-calibrated forecast,
or lose money on a well-calibrated one, and this module only ever answers
"how much money did this ticket make or lose," never "was the forecast any
good."

Settlement is a ONE-TIME lifecycle transition per ticket
(``storage.append_terminal_if_new``): once SETTLED, VOIDED, or CASHED_OUT
has been recorded, a second settlement attempt is a safe no-op only if it
is the identical event (the same re-import producing the same computed
return/profit, never summed or duplicated); a DIFFERENT settlement --
including a different TYPE, e.g. attempting to CASH_OUT a ticket that was
already SETTLED -- is refused as a conflict. Settling a ticket that was
never PLACED is refused outright (``NOT_YET_PLACED``), never fabricated.

Supported ticket types and their combinatorics (``_combinations``):

- ``SINGLE`` -- exactly 1 leg, 1 combination (the leg alone).
- ``DOUBLE`` -- exactly 2 legs, 1 combination (both legs together).
- ``TREBLE`` -- exactly 3 legs, 1 combination (all three together).
- ``SYSTEM`` -- N legs plus an explicit ``system_sizes`` list (e.g. ``[2,
  3]`` for a Trixie from 3 legs, ``[1, 2, 3, 4]`` for a Lucky15 from 4
  legs); one combination per size-k subset of legs, for every k in
  ``system_sizes``.

Void handling (``_settle_combinations``): a VOID leg is removed from every
combination it belongs to (standard bookmaker treatment) -- a combination
reduced to zero remaining legs is fully refunded (its own unit stake back,
zero profit); a combination with at least one remaining LOST leg loses
entirely; a combination whose every remaining leg WON pays unit_stake
times the product of those legs' own placed odds (void legs contribute
nothing to the odds multiplication, exactly as if they were never part of
the combination).

STOP protection (``check_legs_not_stopped``): a ticket cannot link a leg
to a forecast_id whose CURRENT forecast-ledger state has a non-null
``stop_reason`` -- checked directly against the forecast ledger's own
recorded ``stop_reason``, never against a classification or
operator_decision value a caller could set to a favorable-looking string
to bypass it.
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import ids, money
from ledgers.storage import (
    NOT_YET_PLACED,
    AppendResult,
    all_entity_ids,
    append_if_new,
    append_terminal_if_new,
    latest_state,
    read_all,
)

SCHEMA_VERSION = "1.0.0"
SCHEMA_NAME = "betting_ledger.v1"

EVENT_PLACED = "PLACED"
EVENT_SETTLED = "SETTLED"
EVENT_VOIDED = "VOIDED"
EVENT_CASHED_OUT = "CASHED_OUT"

TERMINAL_EVENTS = {EVENT_SETTLED, EVENT_VOIDED, EVENT_CASHED_OUT}

TICKET_TYPES = ("SINGLE", "DOUBLE", "TREBLE", "SYSTEM")
LEG_WON = "WON"
LEG_LOST = "LOST"
LEG_VOID = "VOID"

SETTLEMENT_COMPUTED = "COMPUTED"
SETTLEMENT_MANUAL = "MANUAL"

DEFAULT_FILENAME = "betting-ledger.jsonl"


class StopLinkedLegError(ValueError):
    """Raised when a ticket references a forecast_id currently carrying a
    non-null ``stop_reason`` in the forecast ledger."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_legs_not_stopped(forecast_ledger_path: Path, legs: list[dict[str, Any]]) -> None:
    """Raise ``StopLinkedLegError`` if any leg's ``forecast_id`` currently
    has a non-null ``stop_reason`` in the forecast ledger at
    ``forecast_ledger_path``. Reads ONLY ``stop_reason`` from that
    forecast's current state -- never ``classification`` or
    ``operator_decision`` -- so no caller-supplied override on either of
    those fields can bypass this check. A leg whose forecast_id has no
    RECORDED event at all is left to schema/reference-integrity concerns
    elsewhere; this function only ever blocks on a CONFIRMED stop_reason."""

    # Local import: avoids a hard circular import at module load time
    # (forecast_ledger.py never imports betting_ledger.py, but importing
    # it eagerly here would still couple the two modules' import order).
    from ledgers.forecast_ledger import current_state as forecast_current_state

    blocked = []
    for leg in legs:
        forecast_id = leg.get("forecast_id")
        if not forecast_id:
            continue
        state = forecast_current_state(forecast_ledger_path, forecast_id)
        stop_reason = state.get("stop_reason")
        if stop_reason:
            blocked.append((leg.get("leg_index"), forecast_id, stop_reason))

    if blocked:
        details = ", ".join(f"leg_index={i} forecast_id={fid!r} stop_reason={reason!r}" for i, fid, reason in blocked)
        raise StopLinkedLegError(f"cannot place a ticket with leg(s) linked to a STOP forecast: {details}")


def _combinations(ticket_type: str, leg_count: int, system_sizes: list[int] | None) -> list[tuple[int, ...]]:
    """Every combination as a tuple of leg indices, for the given ticket
    type. Raises ``ValueError`` on a leg-count/type mismatch (e.g. a
    DOUBLE with 3 legs) -- never silently reinterprets the ticket type."""

    if ticket_type == "SINGLE":
        if leg_count != 1:
            raise ValueError(f"SINGLE requires exactly 1 leg, got {leg_count}")
        return [(0,)]
    if ticket_type == "DOUBLE":
        if leg_count != 2:
            raise ValueError(f"DOUBLE requires exactly 2 legs, got {leg_count}")
        return [(0, 1)]
    if ticket_type == "TREBLE":
        if leg_count != 3:
            raise ValueError(f"TREBLE requires exactly 3 legs, got {leg_count}")
        return [(0, 1, 2)]
    if ticket_type == "SYSTEM":
        if not system_sizes:
            raise ValueError("SYSTEM requires a non-empty system_sizes list")
        for size in system_sizes:
            if size < 1 or size > leg_count:
                raise ValueError(f"system_sizes entry {size} is invalid for {leg_count} legs")
        combos: list[tuple[int, ...]] = []
        for size in system_sizes:
            combos.extend(itertools.combinations(range(leg_count), size))
        return combos
    raise ValueError(f"ticket_type must be one of {TICKET_TYPES}, got {ticket_type!r}")


def combination_count_for(ticket_type: str, leg_count: int, system_sizes: list[int] | None) -> int:
    """The canonical combination count for a ticket shape -- used both to
    populate ``combination_count`` at build time and to validate a
    caller-supplied value matches."""

    return len(_combinations(ticket_type, leg_count, system_sizes))


def _validate_stake_buckets(
    ticket_type: str, leg_count: int, stake_buckets: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[int], Decimal]:
    """Validates a structured per-fold-size stake breakdown -- the real
    shape a Bet9ja SYSTEM ticket can have (a DIFFERENT unit stake per
    fold size, e.g. Singles at one stake, Doubles at another), which a
    single scalar ``unit_stake`` cannot represent at all. Never derived
    from ``system_table_raw`` text here or anywhere else in this module
    -- a caller supplies this as already-structured data (a real
    ``stake_buckets`` field from the capture, once the browser parser
    emits one) or does not use this parameter at all.

    Each bucket is ``{"fold_size": int, "combination_count": int,
    "unit_stake": Decimal|int|str, "total_stake": Decimal|int|str}``.
    Raises ``ValueError`` if: ``ticket_type`` is not ``SYSTEM``; a
    ``fold_size`` is not a valid combination size for ``leg_count``; a
    bucket's own ``combination_count`` disagrees with the canonical
    ``C(leg_count, fold_size)``; a bucket's ``total_stake`` disagrees
    with ``combination_count * unit_stake``; or the fold sizes present
    do not exactly match the derived ``system_sizes`` (no duplicates, no
    gaps papered over).

    Returns ``(normalized_buckets, system_sizes, total_stake_decimal)``."""

    if ticket_type != "SYSTEM":
        raise ValueError(f"stake_buckets is only valid for ticket_type='SYSTEM', got {ticket_type!r}")
    if not stake_buckets:
        raise ValueError("stake_buckets must be a non-empty list")

    fold_sizes: list[int] = []
    normalized: list[dict[str, Any]] = []
    total_stake_d = Decimal(0)
    for i, bucket in enumerate(stake_buckets):
        fold_size = bucket["fold_size"]
        if not isinstance(fold_size, int) or fold_size < 1 or fold_size > leg_count:
            raise ValueError(f"stake_buckets[{i}].fold_size={fold_size!r} is invalid for {leg_count} legs")
        if fold_size in fold_sizes:
            raise ValueError(f"stake_buckets[{i}].fold_size={fold_size!r} is a duplicate of an earlier bucket")
        fold_sizes.append(fold_size)

        expected_count = combination_count_for("SYSTEM", leg_count, [fold_size])
        combination_count = bucket["combination_count"]
        if combination_count != expected_count:
            raise ValueError(
                f"stake_buckets[{i}]: combination_count={combination_count!r} does not match the canonical "
                f"C({leg_count}, {fold_size})={expected_count} for fold_size={fold_size}"
            )

        unit_stake_d = money.to_decimal(bucket["unit_stake"], field_name=f"stake_buckets[{i}].unit_stake")
        total_d = money.to_decimal(bucket["total_stake"], field_name=f"stake_buckets[{i}].total_stake")
        expected_total = unit_stake_d * combination_count
        if total_d != expected_total:
            raise ValueError(
                f"stake_buckets[{i}]: total_stake={total_d} does not equal "
                f"combination_count({combination_count}) * unit_stake({unit_stake_d}) = {expected_total}"
            )

        normalized.append(
            {
                "fold_size": fold_size,
                "combination_count": combination_count,
                "unit_stake": money.decimal_str(unit_stake_d),
                "total_stake": money.decimal_str(total_d),
            }
        )
        total_stake_d += total_d

    return normalized, sorted(fold_sizes), total_stake_d


def build_placed_event(
    *,
    ticket_type: str,
    currency: str,
    legs: list[dict[str, Any]],
    max_return: Any = None,
    unit_stake: Any = None,
    stake_buckets: list[dict[str, Any]] | None = None,
    system_sizes: list[int] | None = None,
    stake_structure_known: bool = True,
    total_stake: Any = None,
    placed_at_utc: str | None = None,
    external_ticket_ref: str | None = None,
    source_raw: dict[str, Any] | None = None,
    source_raw_hash: str | None = None,
) -> dict[str, Any]:
    """Build (never appends) one PLACED event. ``combination_count`` and
    ``total_stake`` are derived, never taken on faith from a caller.
    ``ticket_id`` is derived from ``external_ticket_ref`` when supplied
    (stable even if legs are re-typed in a different order), else from
    the ticket's own content (``ids.ticket_id_from_content``).

    ``unit_stake``, ``max_return``, and every leg's ``placed_odds`` must
    be a ``Decimal``, ``int``, or decimal string (e.g. ``"10.00"``) --
    NEVER a Python ``float`` (``money.to_decimal`` raises
    ``MoneyValueError`` immediately if one is passed). Stored in the
    payload as decimal strings, never as JSON numbers, so no downstream
    reader can round-trip them back into a float by accident.

    Exactly ONE of ``unit_stake`` (uniform across every combination --
    this parameter's own original, pre-existing behavior, unchanged for
    every caller that still uses it) or ``stake_buckets`` (a real,
    structured per-fold-size breakdown -- see
    ``_validate_stake_buckets``'s own docstring for exactly what it
    validates and why a single scalar cannot represent a real Bet9ja
    SYSTEM ticket) must be supplied for a ``SYSTEM`` ticket;
    ``SINGLE``/``DOUBLE``/``TREBLE`` always use ``unit_stake`` (there is
    only ever one combination, so a per-fold-size breakdown is
    meaningless). Supplying neither, or both, raises ``ValueError``.
    When ``stake_buckets`` is used, ``system_sizes`` is derived from the
    buckets' own fold sizes (a caller-supplied ``system_sizes`` must
    then match exactly, or is refused as a conflict) and the payload's
    own ``unit_stake`` field is ``null`` -- there genuinely is no single
    value.

    ``max_return`` defaults to ``None`` -- null in the payload -- for a
    ticket imported AFTER it already settled, where the bookmaker's own
    pre-settlement maximum-return figure was never captured and the
    ticket's real, final ``actual_return`` (recorded separately by
    ``settle_bookmaker_observed``) is what matters, not a never-realized
    upper bound. Every OTHER caller must keep passing a real value here
    -- this default exists for that one historical-import case, not as a
    general license to omit it.

    ``stake_structure_known`` (default ``True``, preserving every
    existing caller's behavior exactly) set to ``False`` records a
    ``SYSTEM`` ticket whose total stake is known and trusted (bookmaker-
    reported) but whose internal fold-size/combination breakdown is NOT
    reconstructable from the capture at all -- neither a real
    ``stake_buckets`` field nor a derivable scalar ``unit_stake``. Both
    ``unit_stake`` and ``stake_buckets`` must be ``None`` in this mode
    (supplying either raises ``ValueError`` -- if the structure is known,
    say so honestly via one of those two, don't also claim it's
    unknown), and the caller must supply ``total_stake`` directly (it can
    no longer be derived from a stake times a combination count that
    doesn't exist here). ``combination_count``/``system_sizes`` are
    ``null``/absent in the resulting payload, and
    ``stake_structure_basis: "TOTAL_ONLY_UNKNOWN_BREAKDOWN"`` is recorded
    so a reader can tell this ticket apart from one with a real,
    verified structure. Such a ticket can ONLY ever be settled via
    ``settle_bookmaker_observed`` -- ``settle_computed`` requires
    ``system_sizes``/a per-combination stake to replay combinatorics,
    which this ticket, by construction, does not have.

    ``source_raw``/``source_raw_hash`` (both optional, default ``None``)
    preserve the exact original capture record this event was built
    from, verbatim, alongside the derived fields above -- so reading the
    ledger back never loses provenance a caller might need later, even
    for a field this module itself does not use for anything."""

    max_return_d = money.to_decimal(max_return, field_name="max_return") if max_return is not None else None

    normalized_legs: list[dict[str, Any]] = []
    for i, leg in enumerate(legs):
        leg = dict(leg)
        leg.setdefault("leg_index", i)
        leg["placed_odds"] = money.decimal_str(money.to_decimal(leg["placed_odds"], field_name=f"legs[{i}].placed_odds"))
        normalized_legs.append(leg)

    stake_structure_basis: str | None = None

    if not stake_structure_known:
        if unit_stake is not None or stake_buckets is not None:
            raise ValueError(
                "stake_structure_known=False means neither unit_stake nor stake_buckets is known -- "
                "supplying either one is a contradiction, never silently preferred over the other."
            )
        if ticket_type != "SYSTEM":
            raise ValueError(f"stake_structure_known=False is only meaningful for ticket_type='SYSTEM', got {ticket_type!r}")
        if total_stake is None:
            raise ValueError("stake_structure_known=False requires total_stake to be supplied directly")
        total_stake_d = money.to_decimal(total_stake, field_name="total_stake")
        if total_stake_d <= 0:
            raise ValueError(f"total_stake must be > 0, got {total_stake_d}")
        combination_count = None
        unit_stake_str = None
        normalized_buckets = None
        stake_structure_basis = "TOTAL_ONLY_UNKNOWN_BREAKDOWN"
    elif stake_buckets is not None:
        if total_stake is not None:
            raise ValueError("total_stake is derived from stake_buckets -- do not also supply it directly")
        if unit_stake is not None:
            raise ValueError("supply exactly one of unit_stake or stake_buckets, not both")
        normalized_buckets, derived_system_sizes, total_stake_d = _validate_stake_buckets(
            ticket_type, len(normalized_legs), stake_buckets
        )
        if system_sizes is not None and sorted(system_sizes) != derived_system_sizes:
            raise ValueError(
                f"system_sizes={system_sizes!r} does not match stake_buckets' own fold sizes {derived_system_sizes!r}"
            )
        system_sizes = derived_system_sizes
        combination_count = combination_count_for(ticket_type, len(normalized_legs), system_sizes)
        unit_stake_str = None
    else:
        if total_stake is not None:
            raise ValueError("total_stake is derived from unit_stake -- do not also supply it directly")
        if unit_stake is None:
            raise ValueError("supply exactly one of unit_stake or stake_buckets")
        unit_stake_d = money.to_decimal(unit_stake, field_name="unit_stake")
        if unit_stake_d <= 0:
            raise ValueError(f"unit_stake must be > 0, got {unit_stake_d}")
        combination_count = combination_count_for(ticket_type, len(normalized_legs), system_sizes)
        total_stake_d = unit_stake_d * combination_count
        unit_stake_str = money.decimal_str(unit_stake_d)
        normalized_buckets = None

    placed_at = placed_at_utc or _now_utc()

    tk_id = (
        ids.ticket_id_from_external_ref(external_ticket_ref)
        if external_ticket_ref
        else ids.ticket_id_from_content(placed_at, ticket_type, unit_stake_str or "0", normalized_legs)
    )

    payload: dict[str, Any] = {
        "placed_at_utc": placed_at,
        "ticket_type": ticket_type,
        "combination_count": combination_count,
        "unit_stake": unit_stake_str,
        "total_stake": money.decimal_str(total_stake_d),
        "max_return": money.decimal_str(max_return_d) if max_return_d is not None else None,
        "currency": currency,
        "legs": normalized_legs,
    }
    if normalized_buckets is not None:
        payload["stake_buckets"] = normalized_buckets
    if system_sizes is not None:
        payload["system_sizes"] = system_sizes
    if stake_structure_basis is not None:
        payload["stake_structure_basis"] = stake_structure_basis
    if external_ticket_ref is not None:
        payload["external_ticket_ref"] = external_ticket_ref
    if source_raw is not None:
        payload["source_raw"] = source_raw
    if source_raw_hash is not None:
        payload["source_raw_hash"] = source_raw_hash

    return {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_PLACED,
        "ticket_id": tk_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }


def append_placed(ledger_path: Path, event: dict[str, Any]) -> AppendResult:
    """Idempotently append a PLACED event built by
    ``build_placed_event``."""

    return append_if_new(ledger_path, event, id_field="ticket_id", ignore_keys_in_payload_comparison=frozenset({"placed_at_utc"}))


def place_ticket_checked(betting_ledger_path: Path, forecast_ledger_path: Path, event: dict[str, Any]) -> AppendResult:
    """The recommended entry point: verifies none of ``event``'s legs
    link to a STOP forecast (``check_legs_not_stopped`` -- raises
    ``StopLinkedLegError`` before anything is appended, regardless of any
    classification/operator_decision the caller might have set) and only
    then appends. ``append_placed`` itself performs no STOP check, and
    stays available for tests that need to bypass it deliberately."""

    check_legs_not_stopped(forecast_ledger_path, event["payload"]["legs"])
    return append_placed(betting_ledger_path, event)


def _settle_combinations(
    combinations: list[tuple[int, ...]],
    leg_results_by_index: dict[int, str],
    placed_odds_by_index: dict[int, Decimal],
    unit_stake: Decimal,
) -> Decimal:
    """Sum of returns across every combination -- see this module's
    docstring for the void-removal rule. All arithmetic in ``Decimal``."""

    total_return = Decimal(0)
    for combo in combinations:
        remaining = [i for i in combo if leg_results_by_index.get(i) != LEG_VOID]
        if not remaining:
            total_return += unit_stake  # every leg in this combo voided -> full refund
            continue
        if any(leg_results_by_index.get(i) == LEG_LOST for i in remaining):
            continue  # any real (non-void) loss loses the whole combination
        # every remaining leg WON (LOST already excluded, VOID already removed)
        payout = unit_stake
        for i in remaining:
            payout *= placed_odds_by_index[i]
        total_return += payout
    return total_return


def settle_computed(
    ledger_path: Path,
    ticket_id: str,
    leg_results: list[dict[str, Any]],
    event_type: str = EVENT_SETTLED,
    settlement_basis: str | None = None,
) -> AppendResult:
    """Compute ``actual_return``/``profit_loss`` (as ``Decimal``, stored
    as decimal strings) from the ticket's own PLACED shape and
    ``leg_results`` (a WON/LOST/VOID outcome per ``leg_index``), then
    append the settlement event through ``storage.append_terminal_if_new``
    -- a ONE-TIME transition: re-running with the identical leg_results is
    a safe no-op (``DUPLICATE_SKIPPED``, never a duplicated return); a
    DIFFERENT leg_results, or settling a ticket already VOIDED/CASHED_OUT,
    is refused (``CONFLICT``). Settling a ticket with no PLACED event at
    all returns ``NOT_YET_PLACED`` -- never fabricates one. Raises
    ``ValueError`` for a ticket built with ``stake_structure_known=False``
    (``build_placed_event``) -- there is no combinatorial structure here
    to replay; such a ticket can only ever be settled via
    ``settle_bookmaker_observed``.

    ``settlement_basis`` (optional, default ``None`` -- omitted from the
    payload, preserving every existing caller's byte-for-byte behavior)
    lets a caller explicitly tag this settlement
    ``"COMPUTED_FROM_SELECTIONS"`` -- the counterpart to
    ``settle_bookmaker_observed``'s own ``"BOOKMAKER_OBSERVED"`` tag, for
    a caller that wants the two settlement bases distinguishable in the
    ledger. Never set automatically: an existing caller that never passes
    this keeps writing the exact same payload shape it always has."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "legs" not in state:
        return AppendResult(status=NOT_YET_PLACED, record={"ticket_id": ticket_id, "event_type": event_type, "payload": {}})

    if state.get("stake_structure_basis") == "TOTAL_ONLY_UNKNOWN_BREAKDOWN":
        raise ValueError(
            f"ticket_id={ticket_id!r} was placed with stake_structure_known=False -- its combinatorial "
            "structure was never known, so there is nothing for settle_computed to replay. Use "
            "settle_bookmaker_observed instead."
        )

    legs = state["legs"]
    placed_odds_by_index = {leg["leg_index"]: money.to_decimal(leg["placed_odds"]) for leg in legs}
    leg_results_by_index = {r["leg_index"]: r["outcome"] for r in leg_results}
    missing = set(placed_odds_by_index) - set(leg_results_by_index)
    if missing:
        raise ValueError(f"leg_results is missing outcomes for leg_index {sorted(missing)}")

    combos = _combinations(state["ticket_type"], len(legs), state.get("system_sizes"))
    stake_buckets = state.get("stake_buckets")
    if stake_buckets:
        # Each fold size (combination length) settles against ITS OWN
        # unit_stake -- never one uniform stake applied to every
        # combination, which would silently misprice a real Bet9ja
        # SYSTEM ticket whose buckets carry different stakes (see
        # _validate_stake_buckets's own docstring for why this exists).
        unit_stake_by_fold_size = {b["fold_size"]: money.to_decimal(b["unit_stake"]) for b in stake_buckets}
        combos_by_fold_size: dict[int, list[tuple[int, ...]]] = {}
        for combo in combos:
            combos_by_fold_size.setdefault(len(combo), []).append(combo)
        actual_return_d = Decimal(0)
        for fold_size, combo_group in combos_by_fold_size.items():
            if fold_size not in unit_stake_by_fold_size:
                raise ValueError(
                    f"no stake_buckets entry for fold_size={fold_size}, but the ticket's own system_sizes "
                    "produces combinations of that size -- this should be unreachable given "
                    "_validate_stake_buckets's own checks at PLACED time."
                )
            actual_return_d += _settle_combinations(
                combo_group, leg_results_by_index, placed_odds_by_index, unit_stake_by_fold_size[fold_size]
            )
    else:
        unit_stake_d = money.to_decimal(state["unit_stake"])
        actual_return_d = _settle_combinations(combos, leg_results_by_index, placed_odds_by_index, unit_stake_d)
    profit_loss_d = actual_return_d - money.to_decimal(state["total_stake"])

    payload: dict[str, Any] = {
        "settled_at_utc": _now_utc(),
        "leg_results": leg_results,
        "actual_return": money.decimal_str(actual_return_d),
        "profit_loss": money.decimal_str(profit_loss_d),
        "settlement_method": SETTLEMENT_COMPUTED,
    }
    if settlement_basis is not None:
        payload["settlement_basis"] = settlement_basis
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }
    return append_terminal_if_new(ledger_path, event, id_field="ticket_id", terminal_event_types=TERMINAL_EVENTS)


def settle_bookmaker_observed(
    ledger_path: Path,
    ticket_id: str,
    actual_return: Any,
    leg_results: list[dict[str, Any]],
    settled_at_utc: str | None = None,
    settled_at_resolution: str | None = None,
    event_type: str = EVENT_SETTLED,
) -> AppendResult:
    """Records a ticket's settlement from the bookmaker's OWN reported
    final figure (``actual_return``) -- never recomputed from
    combinatorics/odds -- for a historical ticket imported after it
    already settled, where trusting the bookmaker's own number is both
    the only option (the internal stake structure may be entirely
    unknown -- see ``build_placed_event(stake_structure_known=False)``)
    and, even when the structure IS known, generally MORE accurate than
    replaying it (a real bookmaker figure already reflects voids,
    promotions, and rounding a pure combinatorial replay cannot see).
    ``leg_results`` is still required and recorded verbatim (real,
    observed per-leg outcomes preserve real audit information) but is
    NEVER used to derive ``actual_return`` here -- that is exactly what
    distinguishes this function from ``settle_computed``.

    Same ONE-TIME-transition, idempotent/conflict rules as
    ``settle_computed`` (``storage.append_terminal_if_new``): settling a
    ticket with no PLACED event returns ``NOT_YET_PLACED``; re-running
    with an identical ``actual_return``/``leg_results`` is a safe no-op;
    a different one is refused as a ``CONFLICT``.

    ``settled_at_utc`` should be the real settlement timestamp when
    known; when the capture never recorded one (a known, documented gap
    in this repository's own settled-bets browser capture -- see
    ``orchestration/bet9ja_ticket_import.py``), a caller may pass the
    capture's own ``captured_at_utc`` instead, which is only ever an
    UPPER BOUND on the true settlement time (the ticket was already
    settled by the time it was captured), never the true moment itself
    -- pass ``settled_at_resolution`` alongside it (e.g.
    ``"CAPTURE_TIME_UPPER_BOUND"``) so that distinction is recorded, not
    silently lost. Both default to ``None``: omitting
    ``settled_at_utc`` falls back to ``_now_utc()`` like every other
    settlement function in this module; omitting
    ``settled_at_resolution`` simply omits that key from the payload."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "total_stake" not in state:
        return AppendResult(status=NOT_YET_PLACED, record={"ticket_id": ticket_id, "event_type": event_type, "payload": {}})

    actual_return_d = money.to_decimal(actual_return, field_name="actual_return")
    profit_loss_d = actual_return_d - money.to_decimal(state["total_stake"])

    payload: dict[str, Any] = {
        "settled_at_utc": settled_at_utc or _now_utc(),
        "leg_results": leg_results,
        "actual_return": money.decimal_str(actual_return_d),
        "profit_loss": money.decimal_str(profit_loss_d),
        "settlement_method": SETTLEMENT_MANUAL,
        "settlement_basis": "BOOKMAKER_OBSERVED",
    }
    if settled_at_resolution is not None:
        payload["settled_at_resolution"] = settled_at_resolution
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }
    return append_terminal_if_new(ledger_path, event, id_field="ticket_id", terminal_event_types=TERMINAL_EVENTS)


def void_ticket(ledger_path: Path, ticket_id: str, reason: str | None = None) -> AppendResult:
    """Whole-ticket void (e.g. cancelled by the bookmaker before
    settlement, distinct from a single leg being voided within an
    otherwise-live ticket) -- full stake refunded, zero profit/loss, by
    definition, never computed from leg_results. A ONE-TIME transition,
    same rules as ``settle_computed``."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "total_stake" not in state:
        return AppendResult(status=NOT_YET_PLACED, record={"ticket_id": ticket_id, "event_type": EVENT_VOIDED, "payload": {}})

    payload: dict[str, Any] = {
        "settled_at_utc": _now_utc(),
        "actual_return": state["total_stake"],
        "profit_loss": money.decimal_str(Decimal(0)),
        "settlement_method": SETTLEMENT_MANUAL,
    }
    if reason is not None:
        payload["reason"] = reason
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_VOIDED,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": payload,
    }
    return append_terminal_if_new(ledger_path, event, id_field="ticket_id", terminal_event_types=TERMINAL_EVENTS)


def cash_out(ledger_path: Path, ticket_id: str, actual_return: Any) -> AppendResult:
    """A cash-out's return is whatever the bookmaker actually paid -- this
    package never estimates or recomputes a cash-out value from live
    odds; it only records the operator-reported figure (``actual_return``:
    ``Decimal``/``int``/decimal string, never a float) and derives
    profit_loss from it. A ONE-TIME transition, same rules as
    ``settle_computed``."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "total_stake" not in state:
        return AppendResult(status=NOT_YET_PLACED, record={"ticket_id": ticket_id, "event_type": EVENT_CASHED_OUT, "payload": {}})

    actual_return_d = money.to_decimal(actual_return, field_name="actual_return")
    profit_loss_d = actual_return_d - money.to_decimal(state["total_stake"])

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_CASHED_OUT,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": {
            "settled_at_utc": _now_utc(),
            "actual_return": money.decimal_str(actual_return_d),
            "profit_loss": money.decimal_str(profit_loss_d),
            "settlement_method": SETTLEMENT_MANUAL,
        },
    }
    return append_terminal_if_new(ledger_path, event, id_field="ticket_id", terminal_event_types=TERMINAL_EVENTS)


def current_state(ledger_path: Path, ticket_id: str) -> dict[str, Any]:
    return latest_state(read_all(ledger_path), "ticket_id", ticket_id)


def all_ticket_ids(ledger_path: Path) -> list[str]:
    return all_entity_ids(read_all(ledger_path), "ticket_id")


class BettingLedgerBatchConflictError(Exception):
    """Raised by ``write_batch_placed`` when preflighting the batch finds
    one or more PLACED events that would conflict with an existing
    ledger record under the same natural key (``ticket_id``) -- carries
    every conflicting item so a caller can report all of them at once.
    Nothing is written to the ledger when this is raised."""

    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.conflicts = conflicts
        summary = "; ".join(f"ticket_id={c['ticket_id']!r}" for c in conflicts)
        super().__init__(
            f"{len(conflicts)} record(s) would conflict with existing ledger content under an "
            f"unchanged natural key -- nothing written: {summary}"
        )


def write_batch_placed(ledger_path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Preflights a WHOLE batch of PLACED events -- against the ledger's
    current on-disk content, plus every earlier item in this same batch
    -- before writing a single byte. Mirrors
    ``pcbf_calculator.orchestration.forecast_ledger_writer.write_batch``'s
    own contract exactly (same preflight-then-commit-under-lock shape,
    same summary dict shape), reusing the identical
    ``ledgers.storage.decide_append``/``find_existing`` primitives so
    "would this be a duplicate or a conflict" is decided by exactly one
    piece of logic across both ledgers, never two.

    Raises ``BettingLedgerBatchConflictError`` (nothing written) if any
    item would conflict; otherwise commits every item via the real
    ``append_placed`` (each real call is then guaranteed ``APPENDED`` or
    ``DUPLICATE_SKIPPED``, never ``CONFLICT`` -- the preflight already
    ruled that out) and returns::

        {
          "attempted": int, "appended": int, "duplicate_skipped": int,
          "conflicted": int,  # always 0 on a successful return
          "total_ledger_records": int,
        }

    Holds ``ledgers.locking.exclusive_ledger_lock`` for the entire
    preflight-then-commit sequence, exactly like
    ``forecast_ledger_writer.write_batch`` -- two concurrent imports
    against the same ``ledger_path`` cannot both preflight against the
    same stale on-disk state and both append. Raises
    ``LedgerLockTimeoutError`` (nothing written) if the lock cannot be
    acquired within the default timeout."""

    from ledgers.locking import DEFAULT_LOCK_TIMEOUT_SECONDS, exclusive_ledger_lock
    from ledgers.storage import APPENDED, CONFLICT, DUPLICATE_SKIPPED, decide_append, find_existing

    with exclusive_ledger_lock(ledger_path, timeout=DEFAULT_LOCK_TIMEOUT_SECONDS):
        on_disk = read_all(ledger_path)
        staged: list[dict[str, Any]] = list(on_disk)

        conflicts: list[dict[str, Any]] = []
        for event in events:
            existing = find_existing(staged, "ticket_id", event["ticket_id"], event["event_type"])
            plan = decide_append(existing, event, ignore_keys_in_payload_comparison=frozenset({"placed_at_utc"}))
            if plan.status == CONFLICT:
                conflicts.append({"ticket_id": event["ticket_id"], "new": event, "existing": plan.conflicting_record})
            elif plan.status == APPENDED:
                staged.append(event)
            # DUPLICATE_SKIPPED: leave `staged` as-is, same reasoning as
            # forecast_ledger_writer.write_batch's own comment here.

        if conflicts:
            raise BettingLedgerBatchConflictError(conflicts)

        appended = 0
        duplicate_skipped = 0
        for event in events:
            result = append_placed(ledger_path, event)
            if result.status == APPENDED:
                appended += 1
            elif result.status == DUPLICATE_SKIPPED:
                duplicate_skipped += 1
            else:  # pragma: no cover -- ruled out by the preflight above
                raise AssertionError(
                    f"Policy violation: preflight found no conflict for ticket_id={event['ticket_id']!r}, "
                    f"but the real append returned CONFLICT. This is a bug in this function's preflight logic."
                )

        return {
            "attempted": len(events),
            "appended": appended,
            "duplicate_skipped": duplicate_skipped,
            "conflicted": 0,
            "total_ledger_records": len(read_all(ledger_path)),
        }


class BettingLedgerTerminalBatchConflictError(Exception):
    """Raised by ``write_batch_terminal`` when preflighting the batch
    finds one or more terminal events (SETTLED/VOIDED/CASHED_OUT) that
    would conflict with existing ledger content, or that reference a
    ``ticket_id`` with no prior event on disk or earlier in the same
    batch. Nothing is written to the ledger when this is raised."""

    def __init__(self, conflicts: list[dict[str, Any]], not_yet_placed: list[str]) -> None:
        self.conflicts = conflicts
        self.not_yet_placed = not_yet_placed
        parts = []
        if conflicts:
            parts.append("conflicting: " + "; ".join(f"ticket_id={c['ticket_id']!r}" for c in conflicts))
        if not_yet_placed:
            parts.append("not yet placed: " + ", ".join(repr(t) for t in not_yet_placed))
        super().__init__(
            f"{len(conflicts)} conflicting and {len(not_yet_placed)} not-yet-placed terminal event(s) -- "
            f"nothing written: {'; '.join(parts)}"
        )


def write_batch_terminal(
    ledger_path: Path,
    events: list[dict[str, Any]],
    terminal_event_types: set[str] = TERMINAL_EVENTS,
    ignore_keys_in_payload_comparison: frozenset[str] = frozenset({"settled_at_utc"}),
) -> dict[str, Any]:
    """Preflights a WHOLE batch of terminal events (SETTLED/VOIDED/
    CASHED_OUT -- e.g. from ``settle_bookmaker_observed`` or
    ``settle_computed``) against the ledger's current on-disk content
    plus every earlier item in this same batch, exactly like
    ``write_batch_placed`` does for PLACED events -- the same
    "nothing written on any conflict" guarantee, for a batch of
    settlements instead of a batch of placements.

    A terminal event whose ``ticket_id`` has no event at all on disk or
    earlier in this same batch is treated as blocking the whole batch too
    (never silently skipped) -- a caller (e.g.
    ``orchestration/bet9ja_ticket_import.py``) that always commits its
    own PLACED batch first should never actually hit this in practice;
    it exists as a hard safety net, not an expected outcome.

    Raises ``BettingLedgerTerminalBatchConflictError`` (nothing written)
    on any conflict or not-yet-placed reference; otherwise commits every
    item via ``storage.append_terminal_if_new`` and returns the same
    summary shape ``write_batch_placed`` does. Holds
    ``ledgers.locking.exclusive_ledger_lock`` for the whole
    preflight-then-commit sequence."""

    from ledgers.locking import DEFAULT_LOCK_TIMEOUT_SECONDS, exclusive_ledger_lock
    from ledgers.storage import APPENDED, DUPLICATE_SKIPPED, _payload_equal_ignoring

    with exclusive_ledger_lock(ledger_path, timeout=DEFAULT_LOCK_TIMEOUT_SECONDS):
        on_disk = read_all(ledger_path)
        staged: list[dict[str, Any]] = list(on_disk)

        conflicts: list[dict[str, Any]] = []
        not_yet_placed: list[str] = []
        for event in events:
            ticket_id = event["ticket_id"]
            if not any(r.get("ticket_id") == ticket_id for r in staged):
                not_yet_placed.append(ticket_id)
                continue
            existing_terminal = [
                r for r in staged if r.get("ticket_id") == ticket_id and r.get("event_type") in terminal_event_types
            ]
            if existing_terminal:
                last = existing_terminal[-1]
                same_type = last.get("event_type") == event.get("event_type")
                same_payload = same_type and _payload_equal_ignoring(
                    last.get("payload", {}), event.get("payload", {}), ignore_keys_in_payload_comparison
                )
                if same_payload:
                    continue  # DUPLICATE_SKIPPED -- leave staged as-is
                conflicts.append({"ticket_id": ticket_id, "new": event, "existing": last})
                continue
            staged.append(event)

        if conflicts or not_yet_placed:
            raise BettingLedgerTerminalBatchConflictError(conflicts, not_yet_placed)

        appended = 0
        duplicate_skipped = 0
        for event in events:
            result = append_terminal_if_new(
                ledger_path,
                event,
                id_field="ticket_id",
                terminal_event_types=terminal_event_types,
                ignore_keys_in_payload_comparison=ignore_keys_in_payload_comparison,
            )
            if result.status == APPENDED:
                appended += 1
            elif result.status == DUPLICATE_SKIPPED:
                duplicate_skipped += 1
            else:  # pragma: no cover -- ruled out by the preflight above
                raise AssertionError(
                    f"Policy violation: preflight found no conflict for ticket_id={event['ticket_id']!r}, "
                    f"but the real append returned {result.status!r}. This is a bug in this function's preflight logic."
                )

        return {
            "attempted": len(events),
            "appended": appended,
            "duplicate_skipped": duplicate_skipped,
            "conflicted": 0,
            "total_ledger_records": len(read_all(ledger_path)),
        }
