"""betting-ledger.jsonl: one append-only event stream per ticket_id.

A ticket is recorded ONCE at PLACED time with its legs nested inside that
single event's payload (never duplicated per leg, and never duplicating
``total_stake`` once per system-bet combination) -- see
``ledgers/schemas/betting_ledger.v1.schema.json``. Settlement is a later,
separate event (``SETTLED``/``VOIDED``/``CASHED_OUT``) referencing the same
``ticket_id``.

Ticket-level profit/loss is computed here from the ticket's own stake and
odds; it is a DIFFERENT calculation from forecast scoring
(``ledgers/forecast_ledger.py``'s Brier score/log loss) and the two are
never conflated -- a ticket can win money on a poorly-calibrated forecast,
or lose money on a well-calibrated one, and this module only ever answers
"how much money did this ticket make or lose," never "was the forecast any
good."

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
"""

from __future__ import annotations

import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import ids
from ledgers.storage import AppendResult, append_always, append_if_new, all_entity_ids, latest_state, read_all

SCHEMA_VERSION = "1.0.0"
SCHEMA_NAME = "betting_ledger.v1"

EVENT_PLACED = "PLACED"
EVENT_SETTLED = "SETTLED"
EVENT_VOIDED = "VOIDED"
EVENT_CASHED_OUT = "CASHED_OUT"

TICKET_TYPES = ("SINGLE", "DOUBLE", "TREBLE", "SYSTEM")
LEG_WON = "WON"
LEG_LOST = "LOST"
LEG_VOID = "VOID"

SETTLEMENT_COMPUTED = "COMPUTED"
SETTLEMENT_MANUAL = "MANUAL"

DEFAULT_FILENAME = "betting-ledger.jsonl"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    unit_stake: float,
    max_return: float,
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
    the ticket's own content (``ids.ticket_id_from_content``)."""

    if unit_stake <= 0:
        raise ValueError(f"unit_stake must be > 0, got {unit_stake}")
    for i, leg in enumerate(legs):
        if "leg_index" not in leg:
            leg["leg_index"] = i

    combination_count = combination_count_for(ticket_type, len(legs), system_sizes)
    total_stake = round(unit_stake * combination_count, 10)
    placed_at = placed_at_utc or _now_utc()

    tk_id = (
        ids.ticket_id_from_external_ref(external_ticket_ref)
        if external_ticket_ref
        else ids.ticket_id_from_content(placed_at, ticket_type, unit_stake, legs)
    )

    payload: dict[str, Any] = {
        "placed_at_utc": placed_at,
        "ticket_type": ticket_type,
        "combination_count": combination_count,
        "unit_stake": unit_stake,
        "total_stake": total_stake,
        "max_return": max_return,
        "currency": currency,
        "legs": legs,
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

    return append_if_new(ledger_path, event, id_field="ticket_id")


def _settle_combinations(
    combinations: list[tuple[int, ...]],
    leg_results_by_index: dict[int, str],
    placed_odds_by_index: dict[int, float],
    unit_stake: float,
) -> float:
    """Sum of returns across every combination -- see this module's
    docstring for the void-removal rule."""

    total_return = 0.0
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
) -> dict[str, Any]:
    """Compute ``actual_return``/``profit_loss`` from the ticket's own
    PLACED shape and ``leg_results`` (a WON/LOST/VOID outcome per
    ``leg_index``), then append the settlement event. Raises ``KeyError``
    if the ticket has no PLACED event yet."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "legs" not in state:
        raise KeyError(f"ticket_id {ticket_id!r} has no PLACED event in {ledger_path}")

    legs = state["legs"]
    placed_odds_by_index = {leg["leg_index"]: leg["placed_odds"] for leg in legs}
    leg_results_by_index = {r["leg_index"]: r["outcome"] for r in leg_results}
    missing = set(placed_odds_by_index) - set(leg_results_by_index)
    if missing:
        raise ValueError(f"leg_results is missing outcomes for leg_index {sorted(missing)}")

    combos = _combinations(state["ticket_type"], len(legs), state.get("system_sizes"))
    actual_return = round(
        _settle_combinations(combos, leg_results_by_index, placed_odds_by_index, state["unit_stake"]), 10
    )
    profit_loss = round(actual_return - state["total_stake"], 10)

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": event_type,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": {
            "settled_at_utc": _now_utc(),
            "leg_results": leg_results,
            "actual_return": actual_return,
            "profit_loss": profit_loss,
            "settlement_method": SETTLEMENT_COMPUTED,
        },
    }
    append_always(ledger_path, event)
    return event


def void_ticket(ledger_path: Path, ticket_id: str, reason: str | None = None) -> dict[str, Any]:
    """Whole-ticket void (e.g. cancelled by the bookmaker before
    settlement, distinct from a single leg being voided within an
    otherwise-live ticket) -- full stake refunded, zero profit/loss, by
    definition, never computed from leg_results."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "total_stake" not in state:
        raise KeyError(f"ticket_id {ticket_id!r} has no PLACED event in {ledger_path}")

    payload: dict[str, Any] = {
        "settled_at_utc": _now_utc(),
        "actual_return": state["total_stake"],
        "profit_loss": 0.0,
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
    append_always(ledger_path, event)
    return event


def cash_out(ledger_path: Path, ticket_id: str, actual_return: float) -> dict[str, Any]:
    """A cash-out's return is whatever the bookmaker actually paid -- this
    package never estimates or recomputes a cash-out value from live
    odds; it only records the operator-reported figure and derives
    profit_loss from it."""

    records = read_all(ledger_path)
    state = latest_state(records, "ticket_id", ticket_id)
    if "total_stake" not in state:
        raise KeyError(f"ticket_id {ticket_id!r} has no PLACED event in {ledger_path}")

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_CASHED_OUT,
        "ticket_id": ticket_id,
        "recorded_at_utc": _now_utc(),
        "payload": {
            "settled_at_utc": _now_utc(),
            "actual_return": actual_return,
            "profit_loss": round(actual_return - state["total_stake"], 10),
            "settlement_method": SETTLEMENT_MANUAL,
        },
    }
    append_always(ledger_path, event)
    return event


def current_state(ledger_path: Path, ticket_id: str) -> dict[str, Any]:
    return latest_state(read_all(ledger_path), "ticket_id", ticket_id)


def all_ticket_ids(ledger_path: Path) -> list[str]:
    return all_entity_ids(read_all(ledger_path), "ticket_id")
