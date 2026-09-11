"""Low-level append/read primitives shared by both ledgers.

A ledger file is plain JSONL: one JSON object per line, appended and never
rewritten in place (append-only event history). ``append_if_new`` is the
idempotency primitive both ``forecast_ledger.py`` and ``betting_ledger.py``
build on: given a candidate record, it scans the existing file for another
record with the same (event_type, entity_id) pair.

- No matching record exists -> append, return ``APPENDED``.
- A matching record exists with byte-identical payload -> skip, return
  ``DUPLICATE_SKIPPED`` (the whole point of idempotent re-import: running
  the same input twice is a safe no-op, never a second copy).
- A matching record exists with a DIFFERENT payload -> refuse, return
  ``CONFLICT`` (never silently overwrite one entity's history with
  different content under the same id -- that would corrupt the append-
  only guarantee this package exists to provide).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPENDED = "APPENDED"
DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
CONFLICT = "CONFLICT"


@dataclass
class AppendResult:
    status: str  # APPENDED | DUPLICATE_SKIPPED | CONFLICT
    record: dict[str, Any]
    conflicting_record: dict[str, Any] | None = None


def read_all(path: Path) -> list[dict[str, Any]]:
    """Every record in a JSONL ledger file, in file order. Returns an
    empty list for a missing or empty file -- never an error; an
    uninitialized/empty ledger is a valid, honest starting state."""

    if not path.exists():
        return []
    records = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            records.append(json.loads(raw_line))
    return records


def append_if_new(path: Path, record: dict[str, Any], id_field: str) -> AppendResult:
    """Append ``record`` to the JSONL file at ``path`` unless an existing
    record with the same ``(event_type, record[id_field])`` pair is
    already present -- see this module's docstring for the three possible
    outcomes."""

    entity_id = record[id_field]
    event_type = record["event_type"]
    for existing in read_all(path):
        if existing.get(id_field) == entity_id and existing.get("event_type") == event_type:
            if existing.get("payload") == record.get("payload"):
                return AppendResult(status=DUPLICATE_SKIPPED, record=record, conflicting_record=existing)
            return AppendResult(status=CONFLICT, record=record, conflicting_record=existing)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return AppendResult(status=APPENDED, record=record)


def append_always(path: Path, record: dict[str, Any]) -> None:
    """Append ``record`` unconditionally -- used only for event types that
    are never re-imported (e.g. SCORED, SETTLED: each is a one-time
    transition appended exactly once by the operator's own workflow, not
    something re-run from a saved input file the way RECORDED/PLACED are).
    Callers needing idempotency must use ``append_if_new`` instead."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def latest_state(records: list[dict[str, Any]], id_field: str, entity_id: str) -> dict[str, Any]:
    """Merge every event for one entity_id, in file order, into a single
    "current state" dict -- later events' payload fields override earlier
    ones, matching this package's append-only-events-not-mutated-records
    design (see this package's module docstrings). Always includes
    ``_event_types_seen`` (in order) and ``_event_count`` so a caller can
    tell how a value was derived, never just the merged fields alone."""

    state: dict[str, Any] = {id_field: entity_id, "_event_types_seen": [], "_event_count": 0}
    for record in records:
        if record.get(id_field) != entity_id:
            continue
        state.update(record.get("payload", {}))
        state["_event_types_seen"].append(record["event_type"])
        state["_event_count"] += 1
    return state


def all_entity_ids(records: list[dict[str, Any]], id_field: str) -> list[str]:
    """Every distinct entity id present, in first-seen order."""

    seen: list[str] = []
    for record in records:
        entity_id = record.get(id_field)
        if entity_id is not None and entity_id not in seen:
            seen.append(entity_id)
    return seen
