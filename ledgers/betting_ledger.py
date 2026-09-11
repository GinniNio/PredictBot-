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


def build_placed_event(
    *,
    ticket_type: str,
    unit_stake: Any,
    max_return: Any,
    currency: str,
    legs: list[dict[str, Any]],
    system_sizes: list[int] | None = None,
    placed_at_utc: str | None = None,
    external_ticket_ref: str | None = None,
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
    reader can round-trip them back into a float by accident."""

    unit_stake_d = money.to_decimal(unit_stake, field_name="unit_stake")
    max_return_d = money.to_decimal(max_return, field_name="max_return")
    if unit_stake_d <= 0:
        raise ValueError(f"unit_stake must be > 0, got {unit_stake_d}")

    normalized_legs: list[dict[str, Any]] = []
    for i, leg in enumerate(legs):
        leg = dict(leg)
        leg.setdefault("leg_index", i)
        leg["placed_odds"] = money.decimal_str(money.to_decimal(leg["placed_odds"], field_name=f"legs[{i}].placed_odds"))
        normalized_legs.append(leg)

    combination_count = combination_count_for(ticket_type, len(normalized_legs), system_sizes)
    total_stake_d = unit_stake_d * combination_count
    placed_at = placed_at_utc or _now_utc()

    tk_id = (
        ids.ticket_id_from_external_ref(external_ticket_ref)
        if external_ticket_ref
        else ids.ticket_id_from_content(placed_at, ticket_type, money.decimal_str(unit_stake_d), normalized_legs)
    )

    payload: dict[str, Any] = {
        "placed_at_utc": placed_at,
        "ticket_type": ticket_type,
        "combination_count": combination_count,
        "unit_stake": money.decimal_str(unit_stake_d),
        "total_stake": money.decimal_str(total_stake_d),
        "max_return": money.decimal_str(max_return_d),
        "currency": currency,
        "legs": normalized_legs,
    }
    if system_sizes is not None:
        payload["system_sizes"] = system_sizes
    if external_ticket_ref is not None:
        payload["external_ticket_ref"] = external_ticket_ref

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
) -> AppendResult:
    """Compute ``actual_return``/``profit_loss`` (as ``Decimal``, stored
    as decimal strings) from the ticket's own PLACED shape and
    ``leg_results`` (a WON/LOST/VOID outcome per ``leg_index``), then
    append the settlement event through ``storage.append_terminal_if_new``
    -- a ONE-TIME transition: re-running with the identical leg_results is
    a safe no-op (``DUPLICATE_SKIPPED``, never a duplicated return); a
    DIFFERENT leg_results, or settling a ticket already VOIDED/CASHED_OUT,
    is refused (``CONFLICT``). Settling a ticket with no PLACED event at
    all returns ``NOT_YET_PLACED`` -- never fabricates one."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "legs" not in state:
        return AppendResult(status=NOT_YET_PLACED, record={"ticket_id": ticket_id, "event_type": event_type, "payload": {}})

    legs = state["legs"]
    placed_odds_by_index = {leg["leg_index"]: money.to_decimal(leg["placed_odds"]) for leg in legs}
    leg_results_by_index = {r["leg_index"]: r["outcome"] for r in leg_results}
    missing = set(placed_odds_by_index) - set(leg_results_by_index)
    if missing:
        raise ValueError(f"leg_results is missing outcomes for leg_index {sorted(missing)}")

    combos = _combinations(state["ticket_type"], len(legs), state.get("system_sizes"))
    unit_stake_d = money.to_decimal(state["unit_stake"])
    actual_return_d = _settle_combinations(combos, leg_results_by_index, placed_odds_by_index, unit_stake_d)
    profit_loss_d = actual_return_d - money.to_decimal(state["total_stake"])

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": {
            "settled_at_utc": _now_utc(),
            "leg_results": leg_results,
            "actual_return": money.decimal_str(actual_return_d),
            "profit_loss": money.decimal_str(profit_loss_d),
            "settlement_method": SETTLEMENT_COMPUTED,
        },
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
