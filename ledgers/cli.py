"""CLI for the forecast and betting ledgers.

Every subcommand operates on a ledger DIRECTORY (default ``./ledger_data``,
never committed to git -- see this repo's ``.gitignore``), containing
``forecast-ledger.jsonl`` and ``betting-ledger.jsonl``. Manual JSON input
files are the only accepted input in this PR -- no Bet9ja scraping, no
API, no UI.

Subcommands:

- ``init`` -- create an empty ledger directory (idempotent).
- ``validate`` -- validate every line of one or both ledger files against
  their versioned schema.
- ``record-forecast`` -- append a RECORDED forecast event from a JSON
  file (idempotent re-import).
- ``update-selection`` -- append a SELECTION_UPDATED event.
- ``record-result`` -- score a forecast against its real result and
  append a SCORED event.
- ``place-ticket`` -- append a PLACED ticket event from a JSON file
  (idempotent re-import).
- ``settle-ticket`` -- append a SETTLED/VOIDED/CASHED_OUT event.
- ``export-csv`` -- regenerate the derived CSV views.
- ``summary`` -- print the one daily batch summary for a batch_id.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import betting_ledger, csv_export, forecast_ledger, summary, validation
from ledgers.storage import APPENDED, CONFLICT, DUPLICATE_SKIPPED

DEFAULT_LEDGER_DIR = Path("ledger_data")


def _forecast_path(ledger_dir: Path) -> Path:
    return ledger_dir / forecast_ledger.DEFAULT_FILENAME


def _betting_path(ledger_dir: Path) -> Path:
    return ledger_dir / betting_ledger.DEFAULT_FILENAME


def _cmd_init(args: argparse.Namespace) -> int:
    ledger_dir = args.dir
    ledger_dir.mkdir(parents=True, exist_ok=True)
    for path in (_forecast_path(ledger_dir), _betting_path(ledger_dir)):
        if not path.exists():
            path.touch()
    print(f"Initialized ledger directory: {ledger_dir}")
    print(f"  {_forecast_path(ledger_dir)}")
    print(f"  {_betting_path(ledger_dir)}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    ledger_dir = args.dir
    targets = []
    if args.ledger in ("forecast", "both"):
        targets.append((_forecast_path(ledger_dir), forecast_ledger.SCHEMA_NAME))
    if args.ledger in ("betting", "both"):
        targets.append((_betting_path(ledger_dir), betting_ledger.SCHEMA_NAME))

    had_errors = False
    for path, schema_name in targets:
        errors = validation.validate_jsonl_file(path, schema_name)
        if errors:
            had_errors = True
            print(f"{path}: {len(errors)} error(s)")
            for error in errors:
                print(f"  {error}")
        else:
            print(f"{path}: OK")
    return 1 if had_errors else 0


def _report_append(result) -> int:
    if result.status == APPENDED:
        print(f"Appended {result.record['event_type']} for {result.record.get('forecast_id') or result.record.get('ticket_id')}")
        return 0
    if result.status == DUPLICATE_SKIPPED:
        print("Duplicate of an existing record -- skipped (idempotent re-import).")
        return 0
    print("CONFLICT: a record with this id and event_type already exists with DIFFERENT content.")
    print(f"  existing: {json.dumps(result.conflicting_record, sort_keys=True)}")
    print(f"  new:      {json.dumps(result.record, sort_keys=True)}")
    return 2


def _cmd_record_forecast(args: argparse.Namespace) -> int:
    data = json.loads(args.input.read_text(encoding="utf-8"))
    event = forecast_ledger.build_recorded_event(**data)
    result = forecast_ledger.append_recorded(_forecast_path(args.dir), event)
    return _report_append(result)


def _cmd_update_selection(args: argparse.Namespace) -> int:
    forecast_ledger.append_selection_updated(
        _forecast_path(args.dir), args.forecast_id, args.status, note=args.note, operator_decision=args.operator_decision
    )
    print(f"Appended SELECTION_UPDATED ({args.status}) for {args.forecast_id}")
    return 0


def _cmd_record_result(args: argparse.Namespace) -> int:
    closing_odds = json.loads(args.closing_odds) if args.closing_odds else None
    event = forecast_ledger.score_and_append(_forecast_path(args.dir), args.forecast_id, args.result, closing_odds)
    print(f"Appended SCORED for {args.forecast_id}: brier={event['payload']['brier_score']:.4f} log_loss={event['payload']['log_loss']:.4f}")
    return 0


def _cmd_place_ticket(args: argparse.Namespace) -> int:
    data = json.loads(args.input.read_text(encoding="utf-8"))
    event = betting_ledger.build_placed_event(**data)
    result = betting_ledger.append_placed(_betting_path(args.dir), event)
    return _report_append(result)


def _cmd_settle_ticket(args: argparse.Namespace) -> int:
    ledger_path = _betting_path(args.dir)
    if args.event_type == "SETTLED":
        leg_results = json.loads(args.leg_results)
        event = betting_ledger.settle_computed(ledger_path, args.ticket_id, leg_results)
    elif args.event_type == "VOIDED":
        event = betting_ledger.void_ticket(ledger_path, args.ticket_id, reason=args.reason)
    elif args.event_type == "CASHED_OUT":
        if args.actual_return is None:
            print("CASHED_OUT requires --actual-return")
            return 2
        event = betting_ledger.cash_out(ledger_path, args.ticket_id, args.actual_return)
    else:
        print(f"Unknown event_type: {args.event_type}")
        return 2
    print(
        f"Appended {event['event_type']} for {args.ticket_id}: "
        f"actual_return={event['payload']['actual_return']} profit_loss={event['payload']['profit_loss']}"
    )
    return 0


def _cmd_export_csv(args: argparse.Namespace) -> int:
    counts = csv_export.export_all(_forecast_path(args.dir), _betting_path(args.dir), args.out)
    for filename, count in counts.items():
        print(f"{args.out / filename}: {count} row(s)")
    return 0


def _cmd_summary(args: argparse.Namespace) -> int:
    result = summary.daily_batch_summary(_forecast_path(args.dir), _betting_path(args.dir), args.batch_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ledgers.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_dir_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--dir", type=Path, default=DEFAULT_LEDGER_DIR, help="Ledger directory (default: ./ledger_data)")

    p_init = subparsers.add_parser("init", help="Create an empty ledger directory")
    add_dir_arg(p_init)
    p_init.set_defaults(func=_cmd_init)

    p_validate = subparsers.add_parser("validate", help="Validate ledger JSONL files against their schema")
    add_dir_arg(p_validate)
    p_validate.add_argument("--ledger", choices=["forecast", "betting", "both"], default="both")
    p_validate.set_defaults(func=_cmd_validate)

    p_record_forecast = subparsers.add_parser("record-forecast", help="Append a RECORDED forecast event from a JSON file")
    add_dir_arg(p_record_forecast)
    p_record_forecast.add_argument("input", type=Path, help="Forecast JSON file (kwargs for build_recorded_event)")
    p_record_forecast.set_defaults(func=_cmd_record_forecast)

    p_update_selection = subparsers.add_parser("update-selection", help="Append a SELECTION_UPDATED event")
    add_dir_arg(p_update_selection)
    p_update_selection.add_argument("forecast_id")
    p_update_selection.add_argument("status", choices=list(forecast_ledger.SELECTION_STATUSES))
    p_update_selection.add_argument("--note", default=None)
    p_update_selection.add_argument("--operator-decision", dest="operator_decision", default=None)
    p_update_selection.set_defaults(func=_cmd_update_selection)

    p_record_result = subparsers.add_parser("record-result", help="Score a forecast and append a SCORED event")
    add_dir_arg(p_record_result)
    p_record_result.add_argument("forecast_id")
    p_record_result.add_argument("result", choices=["H", "D", "A"])
    p_record_result.add_argument("--closing-odds", dest="closing_odds", default=None, help='JSON, e.g. \'{"H":1.9,"D":3.4,"A":4.2}\'')
    p_record_result.set_defaults(func=_cmd_record_result)

    p_place_ticket = subparsers.add_parser("place-ticket", help="Append a PLACED ticket event from a JSON file")
    add_dir_arg(p_place_ticket)
    p_place_ticket.add_argument("input", type=Path, help="Ticket JSON file (kwargs for build_placed_event)")
    p_place_ticket.set_defaults(func=_cmd_place_ticket)

    p_settle_ticket = subparsers.add_parser("settle-ticket", help="Append a SETTLED/VOIDED/CASHED_OUT event")
    add_dir_arg(p_settle_ticket)
    p_settle_ticket.add_argument("ticket_id")
    p_settle_ticket.add_argument("event_type", choices=["SETTLED", "VOIDED", "CASHED_OUT"])
    p_settle_ticket.add_argument("--leg-results", dest="leg_results", default=None, help='JSON list, e.g. \'[{"leg_index":0,"outcome":"WON"}]\' (SETTLED only)')
    p_settle_ticket.add_argument("--reason", default=None, help="VOIDED only")
    p_settle_ticket.add_argument("--actual-return", dest="actual_return", type=float, default=None, help="CASHED_OUT only")
    p_settle_ticket.set_defaults(func=_cmd_settle_ticket)

    p_export_csv = subparsers.add_parser("export-csv", help="Regenerate derived CSV views for Excel")
    add_dir_arg(p_export_csv)
    p_export_csv.add_argument("--out", type=Path, default=None, help="Output directory (default: <dir>/csv)")
    p_export_csv.set_defaults(func=_cmd_export_csv)

    p_summary = subparsers.add_parser("summary", help="Print the one daily summary for a batch_id")
    add_dir_arg(p_summary)
    p_summary.add_argument("batch_id")
    p_summary.set_defaults(func=_cmd_summary)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "export-csv" and args.out is None:
        args.out = args.dir / "csv"
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
