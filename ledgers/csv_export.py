"""Derived, read-only CSV views of both ledgers for inspection in Excel.

These files are always regenerated from the JSONL ledgers -- never hand-
edited, never treated as the source of truth, and never containing a
spreadsheet formula that computes anything the ledger itself didn't
already compute (guardrail: "No spreadsheet formulas as the source of
truth. CSV is an exported view only."). Re-running export overwrites the
previous CSV files completely; nothing here is appended to.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers.betting_ledger import all_ticket_ids
from ledgers.betting_ledger import current_state as betting_current_state
from ledgers.forecast_ledger import all_forecast_ids
from ledgers.forecast_ledger import current_state as forecast_current_state
from ledgers.summary import ranked_forecasts

FORECASTS_CSV_COLUMNS = [
    "forecast_id", "batch_id", "created_at_utc", "fixture_id", "sport", "league", "kickoff_utc",
    "market_type", "selection", "offered_odds_H", "offered_odds_D", "offered_odds_A",
    "market_probabilities_H", "market_probabilities_D", "market_probabilities_A",
    "model_probabilities_H", "model_probabilities_D", "model_probabilities_A",
    "model_version", "classification", "selection_status", "stop_reason", "operator_decision",
    "actual_result", "brier_score", "log_loss", "settled_at_utc",
]

TICKETS_CSV_COLUMNS = [
    "ticket_id", "placed_at_utc", "ticket_type", "combination_count", "unit_stake",
    "total_stake", "max_return", "currency", "leg_count", "status", "actual_return", "profit_loss",
]

TICKET_LEGS_CSV_COLUMNS = [
    "ticket_id", "leg_index", "forecast_id", "fixture_id", "market_type", "selection", "placed_odds", "outcome",
]


def _get(d: dict[str, Any], *path: str, default: Any = "") -> Any:
    for key in path[:-1]:
        d = d.get(key) or {}
    return d.get(path[-1], default) if isinstance(d, dict) else default


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_forecasts_csv(forecast_ledger_path: Path, out_path: Path) -> int:
    """One row per forecast_id (current merged state, including STOP
    candidates -- this full export is the audit view; use
    ``export_ranked_candidates_csv`` for the STOP-excluded operator
    view). Returns the row count written."""

    rows = []
    for fc_id in all_forecast_ids(forecast_ledger_path):
        s = forecast_current_state(forecast_ledger_path, fc_id)
        rows.append(
            {
                "forecast_id": fc_id,
                "batch_id": s.get("batch_id") or "",
                "created_at_utc": s.get("created_at_utc", ""),
                "fixture_id": s.get("fixture_id", ""),
                "sport": s.get("sport", ""),
                "league": s.get("league", ""),
                "kickoff_utc": s.get("kickoff_utc", ""),
                "market_type": s.get("market_type", ""),
                "selection": s.get("selection") or "",
                "offered_odds_H": _get(s, "offered_odds", "H"),
                "offered_odds_D": _get(s, "offered_odds", "D"),
                "offered_odds_A": _get(s, "offered_odds", "A"),
                "market_probabilities_H": _get(s, "market_probabilities", "H"),
                "market_probabilities_D": _get(s, "market_probabilities", "D"),
                "market_probabilities_A": _get(s, "market_probabilities", "A"),
                "model_probabilities_H": _get(s, "model_probabilities", "H"),
                "model_probabilities_D": _get(s, "model_probabilities", "D"),
                "model_probabilities_A": _get(s, "model_probabilities", "A"),
                "model_version": s.get("model_version", ""),
                "classification": s.get("classification", ""),
                "selection_status": s.get("selection_status", ""),
                "stop_reason": s.get("stop_reason") or "",
                "operator_decision": s.get("operator_decision") or "",
                "actual_result": s.get("actual_result") or "",
                "brier_score": s.get("brier_score", ""),
                "log_loss": s.get("log_loss", ""),
                "settled_at_utc": s.get("settled_at_utc") or "",
            }
        )
    _write_csv(out_path, FORECASTS_CSV_COLUMNS, rows)
    return len(rows)


def export_ranked_candidates_csv(forecast_ledger_path: Path, out_path: Path, batch_id: str | None = None) -> int:
    """The operator-facing ranked view: every STOP/rejected candidate is
    structurally excluded (see ``summary.ranked_forecasts``) -- this file
    never contains a fixture a STOP rule already removed."""

    states = ranked_forecasts(forecast_ledger_path, batch_id=batch_id)
    rows = []
    for s in states:
        rows.append(
            {
                "forecast_id": s.get("forecast_id", ""),
                "batch_id": s.get("batch_id") or "",
                "created_at_utc": s.get("created_at_utc", ""),
                "fixture_id": s.get("fixture_id", ""),
                "sport": s.get("sport", ""),
                "league": s.get("league", ""),
                "kickoff_utc": s.get("kickoff_utc", ""),
                "market_type": s.get("market_type", ""),
                "selection": s.get("selection") or "",
                "offered_odds_H": _get(s, "offered_odds", "H"),
                "offered_odds_D": _get(s, "offered_odds", "D"),
                "offered_odds_A": _get(s, "offered_odds", "A"),
                "market_probabilities_H": _get(s, "market_probabilities", "H"),
                "market_probabilities_D": _get(s, "market_probabilities", "D"),
                "market_probabilities_A": _get(s, "market_probabilities", "A"),
                "model_probabilities_H": _get(s, "model_probabilities", "H"),
                "model_probabilities_D": _get(s, "model_probabilities", "D"),
                "model_probabilities_A": _get(s, "model_probabilities", "A"),
                "model_version": s.get("model_version", ""),
                "classification": s.get("classification", ""),
                "selection_status": s.get("selection_status", ""),
                "stop_reason": "",
                "operator_decision": s.get("operator_decision") or "",
                "actual_result": s.get("actual_result") or "",
                "brier_score": s.get("brier_score", ""),
                "log_loss": s.get("log_loss", ""),
                "settled_at_utc": s.get("settled_at_utc") or "",
            }
        )
    _write_csv(out_path, FORECASTS_CSV_COLUMNS, rows)
    return len(rows)


def export_tickets_csv(betting_ledger_path: Path, out_path: Path) -> int:
    """One row per ticket_id (current merged state)."""

    rows = []
    for tk_id in all_ticket_ids(betting_ledger_path):
        s = betting_current_state(betting_ledger_path, tk_id)
        status = s.get("_event_types_seen", ["PLACED"])[-1]
        rows.append(
            {
                "ticket_id": tk_id,
                "placed_at_utc": s.get("placed_at_utc", ""),
                "ticket_type": s.get("ticket_type", ""),
                "combination_count": s.get("combination_count", ""),
                "unit_stake": s.get("unit_stake", ""),
                "total_stake": s.get("total_stake", ""),
                "max_return": s.get("max_return", ""),
                "currency": s.get("currency", ""),
                "leg_count": len(s.get("legs", [])),
                "status": status,
                "actual_return": s.get("actual_return", ""),
                "profit_loss": s.get("profit_loss", ""),
            }
        )
    _write_csv(out_path, TICKETS_CSV_COLUMNS, rows)
    return len(rows)


def export_ticket_legs_csv(betting_ledger_path: Path, out_path: Path) -> int:
    """One row per (ticket_id, leg) -- detailed inspection, joined against
    the ticket's own latest leg_results when settlement has happened."""

    rows = []
    for tk_id in all_ticket_ids(betting_ledger_path):
        s = betting_current_state(betting_ledger_path, tk_id)
        outcomes_by_index = {r["leg_index"]: r["outcome"] for r in s.get("leg_results", [])}
        for leg in s.get("legs", []):
            rows.append(
                {
                    "ticket_id": tk_id,
                    "leg_index": leg.get("leg_index", ""),
                    "forecast_id": leg.get("forecast_id", ""),
                    "fixture_id": leg.get("fixture_id", ""),
                    "market_type": leg.get("market_type", ""),
                    "selection": leg.get("selection", ""),
                    "placed_odds": leg.get("placed_odds", ""),
                    "outcome": outcomes_by_index.get(leg.get("leg_index"), ""),
                }
            )
    _write_csv(out_path, TICKET_LEGS_CSV_COLUMNS, rows)
    return len(rows)


def export_all(forecast_ledger_path: Path, betting_ledger_path: Path, out_dir: Path) -> dict[str, int]:
    """Export every derived CSV view into ``out_dir``. Returns the row
    count written per file."""

    return {
        "forecasts.csv": export_forecasts_csv(forecast_ledger_path, out_dir / "forecasts.csv"),
        "ranked_candidates.csv": export_ranked_candidates_csv(forecast_ledger_path, out_dir / "ranked_candidates.csv"),
        "tickets.csv": export_tickets_csv(betting_ledger_path, out_dir / "tickets.csv"),
        "ticket_legs.csv": export_ticket_legs_csv(betting_ledger_path, out_dir / "ticket_legs.csv"),
    }
