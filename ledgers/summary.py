"""Batch-level reconciliation and the daily operator summary.

Directly addresses the failure modes an earlier, ad hoc session produced
(conflicting received/parsed counts, a candidate carrying two different
classifications, STOP-marked fixtures still appearing in a ranked list,
several overlapping report versions pasted together): this module
enforces, structurally, that a batch has exactly ONE reconciled count and
exactly ONE generated summary, and that a STOP/rejected candidate can
never appear in a ranked view.

- ``ranked_forecasts`` -- every RECORDED forecast for a batch, EXCLUDING
  any with a non-null ``stop_reason`` (STOP items are structurally
  excluded here, not filtered ad hoc by each caller).
- ``daily_batch_summary`` -- the four short totals the operator actually
  needs (fixtures captured, fixtures rejected, candidates reviewed,
  records written to each ledger), computed once per ``batch_id`` from
  the ledgers' own recorded state -- never hand-assembled prose.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import money
from ledgers.betting_ledger import all_ticket_ids
from ledgers.betting_ledger import current_state as betting_current_state
from ledgers.forecast_ledger import all_forecast_ids
from ledgers.forecast_ledger import current_state as forecast_current_state
from ledgers.storage import read_all


def _batch_forecast_states(forecast_ledger_path: Path, batch_id: str) -> list[dict[str, Any]]:
    records = read_all(forecast_ledger_path)
    states = [forecast_current_state(forecast_ledger_path, fc_id) for fc_id in all_forecast_ids(forecast_ledger_path)]
    return [s for s in states if s.get("batch_id") == batch_id]


def ranked_forecasts(forecast_ledger_path: Path, batch_id: str | None = None) -> list[dict[str, Any]]:
    """Every forecast's current state, optionally restricted to one
    ``batch_id``, with every STOP/rejected candidate (``stop_reason`` is
    not null) structurally excluded -- never left to each caller to
    remember to filter."""

    states = [forecast_current_state(forecast_ledger_path, fc_id) for fc_id in all_forecast_ids(forecast_ledger_path)]
    if batch_id is not None:
        states = [s for s in states if s.get("batch_id") == batch_id]
    return [s for s in states if not s.get("stop_reason")]


def daily_batch_summary(forecast_ledger_path: Path, betting_ledger_path: Path, batch_id: str) -> dict[str, Any]:
    """Exactly one summary dict for ``batch_id`` -- the four totals the
    daily workflow reports, plus the batch_id itself so a caller never
    has to guess which batch a summary describes.

    Ticket totals (``tickets_linked``/``total_staked``/``total_returned``/
    ``total_profit_loss``) are aggregated ONCE PER ``ticket_id`` -- each
    ticket's own PLACED ``total_stake`` and (once settled) ``actual_return``/
    ``profit_loss`` are added exactly once, never once per leg, matching
    the same rule the CSV export (``csv_export.export_tickets_csv``, one
    row per ticket_id) already enforces. All money arithmetic is
    ``Decimal`` (``ledgers/money.py``), never a binary float."""

    batch_states = _batch_forecast_states(forecast_ledger_path, batch_id)
    fixtures_captured = len({s["fixture_id"] for s in batch_states if s.get("fixture_id")})
    fixtures_rejected = len({s["fixture_id"] for s in batch_states if s.get("stop_reason")})
    candidates_reviewed = len(
        {s["fixture_id"] for s in batch_states if not s.get("stop_reason") and s.get("selection_status") != "considered"}
    )
    forecast_records_written = sum(s.get("_event_count", 0) for s in batch_states)

    ticket_states = [betting_current_state(betting_ledger_path, tk_id) for tk_id in all_ticket_ids(betting_ledger_path)]
    batch_forecast_ids = {s["forecast_id"] for s in batch_states}

    betting_records_written = 0
    tickets_linked = 0
    total_staked = Decimal(0)
    total_returned = Decimal(0)
    total_profit_loss = Decimal(0)
    for ticket_state in ticket_states:  # one iteration per ticket_id -- never per leg
        legs = ticket_state.get("legs", [])
        if not any(leg.get("forecast_id") in batch_forecast_ids for leg in legs):
            continue
        betting_records_written += ticket_state.get("_event_count", 0)
        tickets_linked += 1
        total_staked += money.to_decimal(ticket_state["total_stake"])
        if "actual_return" in ticket_state:
            total_returned += money.to_decimal(ticket_state["actual_return"])
            total_profit_loss += money.to_decimal(ticket_state["profit_loss"])

    return {
        "batch_id": batch_id,
        "fixtures_captured": fixtures_captured,
        "fixtures_rejected": fixtures_rejected,
        "candidates_reviewed": candidates_reviewed,
        "records_written": {
            "forecast_ledger": forecast_records_written,
            "betting_ledger": betting_records_written,
        },
        "tickets": {
            "tickets_linked": tickets_linked,
            "total_staked": money.decimal_str(total_staked),
            "total_returned": money.decimal_str(total_returned),
            "total_profit_loss": money.decimal_str(total_profit_loss),
        },
    }
