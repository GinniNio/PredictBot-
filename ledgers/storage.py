"""Low-level append/read primitives shared by both ledgers.

A ledger file is plain JSONL: one JSON object per line, appended and never
rewritten in place (append-only event history) -- an existing line is
NEVER opened for writing again by any function in this module; every
write here is a single ``"a"``-mode append of exactly one new line.
``append_if_new`` is the idempotency primitive both ``forecast_ledger.py``
and ``betting_ledger.py`` build on: given a candidate record, it scans the
existing file for another record with the same (event_type, entity_id)
pair.

- No matching record exists -> append, return ``APPENDED``.
- A matching record exists with byte-identical payload -> skip, return
  ``DUPLICATE_SKIPPED`` (the whole point of idempotent re-import: running
  the same input twice is a safe no-op, never a second copy).
- A matching record exists with a DIFFERENT payload -> refuse, return
  ``CONFLICT`` (never silently overwrite one entity's history with
  different content under the same id -- that would corrupt the append-
  only guarantee this package exists to provide).

``append_terminal_if_new`` is the stricter sibling used for one-time
lifecycle transitions (a forecast's SCORED event; a ticket's SETTLED/
VOIDED/CASHED_OUT event): once ANY terminal event exists for an entity,
a second terminal event is a safe no-op ONLY if it is byte-identical to
the one already recorded (the same re-import producing the same
computed return/profit, never a duplicated or re-summed figure); a
DIFFERENT terminal event -- even a different TYPE of terminal event, e.g.
attempting to CASH_OUT a ticket that was already SETTLED -- is refused as
a conflict, never silently layered on top. Every function in this module
that appends still only ever performs a single-line append; the
"terminal" gate is a read-before-write check, not a rewrite of history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APPENDED = "APPENDED"
DUPLICATE_SKIPPED = "DUPLICATE_SKIPPED"
CONFLICT = "CONFLICT"
NOT_YET_PLACED = "NOT_YET_PLACED"


@dataclass
class AppendResult:
    status: str  # APPENDED | DUPLICATE_SKIPPED | CONFLICT | NOT_YET_PLACED
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


def _payload_equal_ignoring(a: dict[str, Any], b: dict[str, Any], ignore_keys: frozenset[str]) -> bool:
    """Payload equality that ignores volatile, wall-clock-only keys (e.g.
    ``settled_at_utc``, recomputed fresh on every call) -- two calls with
    otherwise-identical inputs a moment apart must compare equal, exactly
    like ``TrainingResult.to_deterministic_dict`` excludes
    ``run_timestamp_utc`` elsewhere in this repository for the same
    reason."""

    strip = lambda d: {k: v for k, v in d.items() if k not in ignore_keys}  # noqa: E731
    return strip(a) == strip(b)


def append_if_new(
    path: Path,
    record: dict[str, Any],
    id_field: str,
    ignore_keys_in_payload_comparison: frozenset[str] = frozenset(),
) -> AppendResult:
    """Append ``record`` to the JSONL file at ``path`` unless an existing
    record with the same ``(event_type, record[id_field])`` pair is
    already present -- see this module's docstring for the three possible
    outcomes. ``ignore_keys_in_payload_comparison`` excludes fields that
    legitimately vary between two otherwise-identical builds (e.g. a
    RECORDED event's own ``created_at_utc``, defaulted to "now" when the
    caller doesn't supply one explicitly -- two independent imports of
    the same logical forecast a moment apart must still compare equal)."""

    entity_id = record[id_field]
    event_type = record["event_type"]
    for existing in read_all(path):
        if existing.get(id_field) == entity_id and existing.get("event_type") == event_type:
            if _payload_equal_ignoring(
                existing.get("payload", {}), record.get("payload", {}), ignore_keys_in_payload_comparison
            ):
                return AppendResult(status=DUPLICATE_SKIPPED, record=record, conflicting_record=existing)
            return AppendResult(status=CONFLICT, record=record, conflicting_record=existing)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return AppendResult(status=APPENDED, record=record)


def append_event(path: Path, record: dict[str, Any]) -> None:
    """Append ``record`` unconditionally -- used ONLY for event types that
    may legitimately recur many times for the same entity (e.g. a
    forecast's SELECTION_UPDATED: considered -> shortlisted -> placed is
    several real, distinct events, not duplicates of each other). Never
    used for a one-time lifecycle transition -- those go through
    ``append_terminal_if_new`` instead, and initial creation events go
    through ``append_if_new``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def append_terminal_if_new(
    path: Path,
    record: dict[str, Any],
    id_field: str,
    terminal_event_types: set[str],
    require_prior_event: bool = True,
    ignore_keys_in_payload_comparison: frozenset[str] = frozenset({"settled_at_utc"}),
) -> AppendResult:
    """Append a one-time lifecycle-transition event (a forecast's SCORED;
    a ticket's SETTLED/VOIDED/CASHED_OUT), gated so the transition can
    only ever happen once per entity -- see this module's docstring for
    the exact rules. ``require_prior_event`` (default True) additionally
    refuses the append with status ``NOT_YET_PLACED`` when the entity has
    no prior event at all (e.g. settling a ticket that was never placed).
    ``ignore_keys_in_payload_comparison`` excludes wall-clock-only fields
    from the duplicate/conflict comparison so re-running the same
    settlement a moment later is still recognized as the same event."""

    entity_id = record[id_field]
    records = read_all(path)

    if require_prior_event and not any(r.get(id_field) == entity_id for r in records):
        return AppendResult(status=NOT_YET_PLACED, record=record)

    existing_terminal = [
        r for r in records if r.get(id_field) == entity_id and r.get("event_type") in terminal_event_types
    ]
    if existing_terminal:
        last = existing_terminal[-1]
        same_type = last.get("event_type") == record.get("event_type")
        same_payload = same_type and _payload_equal_ignoring(
            last.get("payload", {}), record.get("payload", {}), ignore_keys_in_payload_comparison
        )
        if same_payload:
            return AppendResult(status=DUPLICATE_SKIPPED, record=record, conflicting_record=last)
        return AppendResult(status=CONFLICT, record=record, conflicting_record=last)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return AppendResult(status=APPENDED, record=record)


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
