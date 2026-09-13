"""Loads and validates the supplied historical Soccer match file for the
Poisson research adapter.

Accepts either JSON or CSV (chosen by the input file's extension), both
carrying the same required fields: ``source_match_id``, ``competition``,
``kickoff_utc``, ``home``, ``away``, ``home_goals``, ``away_goals``,
``status``, ``source``.

JSON shape::

    {
      "source": "SUPPLIED_HISTORY",
      "matches": [
        {"source_match_id": "match-123", "competition": "Premier League",
         "kickoff_utc": "2026-08-20T19:00:00Z", "home": "Team A",
         "away": "Team B", "home_goals": 2, "away_goals": 1,
         "status": "FINISHED"}
      ]
    }

A match record's own ``source`` field is optional in JSON and falls back
to the top-level ``source`` value when omitted (this is reading the file's
own stated provenance, never inventing one); a top-level ``source`` is
still required if any match omits its own. CSV has no top-level object, so
every row must carry its own ``source`` column.

No network retrieval happens anywhere in this module -- the file is read
exactly as supplied by the caller. Fails the whole load (nothing is
partially accepted) on: a missing required field, a duplicate
``source_match_id``, or a ``FINISHED`` match with a missing/invalid
(negative or non-integer) goal count. A match whose ``status`` is not
``FINISHED`` is retained but simply never eligible for model fitting (see
``poisson_model.py``) -- this is expected, not an error.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import (
    FORECAST_HISTORY_DUPLICATE_MATCH_ID,
    FORECAST_HISTORY_INCOMPLETE_MATCH,
    FORECAST_HISTORY_SCHEMA_INVALID,
    HistoryValidationError,
)

REQUIRED_MATCH_FIELDS = (
    "source_match_id",
    "competition",
    "kickoff_utc",
    "home",
    "away",
    "status",
)
# home_goals/away_goals are deliberately NOT in the generic required-field
# check above: they are legitimately absent for a not-yet-played match.
# Their presence/validity is checked separately, only when status is
# FINISHED (see _build_record below) -- a FINISHED match missing them is
# FORECAST_HISTORY_INCOMPLETE_MATCH, a distinct, more specific reason than
# the generic schema-invalid check.

FINISHED_STATUS = "FINISHED"


def _parse_utc_timestamp(raw: Any, source_match_id: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID,
            f"match {source_match_id!r}: kickoff_utc must be a non-empty ISO-8601 string.",
        )
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID,
            f"match {source_match_id!r}: kickoff_utc {raw!r} is not a parseable ISO-8601 timestamp ({exc}).",
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MatchRecord:
    source_match_id: str
    competition: str
    kickoff_utc: datetime
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    status: str
    source: str

    @property
    def is_finished(self) -> bool:
        return self.status == FINISHED_STATUS


def _coerce_goals(value: Any, field: str, source_match_id: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise HistoryValidationError(
            FORECAST_HISTORY_INCOMPLETE_MATCH,
            f"match {source_match_id!r}: {field} must be a non-negative integer, got a boolean.",
        )
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        raise HistoryValidationError(
            FORECAST_HISTORY_INCOMPLETE_MATCH,
            f"match {source_match_id!r}: {field} value {value!r} is not an integer.",
        ) from None
    if as_int != float(value) or as_int < 0:
        raise HistoryValidationError(
            FORECAST_HISTORY_INCOMPLETE_MATCH,
            f"match {source_match_id!r}: {field} value {value!r} must be a non-negative integer.",
        )
    return as_int


def _build_record(raw: dict[str, Any], top_level_source: str | None) -> MatchRecord:
    missing = [field for field in REQUIRED_MATCH_FIELDS if raw.get(field) in (None, "")]
    if missing:
        identifier = raw.get("source_match_id", "<unknown>")
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID,
            f"match {identifier!r} is missing required field(s): {missing}.",
        )

    source_match_id = str(raw["source_match_id"])
    source = raw.get("source") or top_level_source
    if not source:
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID,
            f"match {source_match_id!r} has no 'source' and the file supplies no top-level "
            "'source' to fall back to.",
        )

    status = str(raw["status"]).strip()
    home_goals = _coerce_goals(raw.get("home_goals"), "home_goals", source_match_id)
    away_goals = _coerce_goals(raw.get("away_goals"), "away_goals", source_match_id)
    if status == FINISHED_STATUS and (home_goals is None or away_goals is None):
        raise HistoryValidationError(
            FORECAST_HISTORY_INCOMPLETE_MATCH,
            f"match {source_match_id!r} has status FINISHED but is missing home_goals/away_goals.",
        )

    return MatchRecord(
        source_match_id=source_match_id,
        competition=str(raw["competition"]),
        kickoff_utc=_parse_utc_timestamp(raw["kickoff_utc"], source_match_id),
        home=str(raw["home"]),
        away=str(raw["away"]),
        home_goals=home_goals,
        away_goals=away_goals,
        status=status,
        source=str(source),
    )


def _load_json(path: Path) -> list[MatchRecord]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HistoryValidationError(FORECAST_HISTORY_SCHEMA_INVALID, f"invalid JSON: {exc}") from None
    if not isinstance(payload, dict) or "matches" not in payload:
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID, "history file must be a JSON object with a 'matches' list."
        )
    matches = payload["matches"]
    if not isinstance(matches, list):
        raise HistoryValidationError(FORECAST_HISTORY_SCHEMA_INVALID, "'matches' must be a list.")
    top_level_source = payload.get("source")
    records = [_build_record(raw, top_level_source) for raw in matches]
    return records


def _load_csv(path: Path) -> list[MatchRecord]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        records = [_build_record(dict(row), top_level_source=None) for row in reader]
    return records


def load_history(path: Path) -> list[MatchRecord]:
    """Load and validate a historical-match file. Raises
    ``HistoryValidationError`` on any schema problem -- the whole file is
    rejected, never partially accepted."""
    suffix = path.suffix.lower()
    if suffix == ".json":
        records = _load_json(path)
    elif suffix == ".csv":
        records = _load_csv(path)
    else:
        raise HistoryValidationError(
            FORECAST_HISTORY_SCHEMA_INVALID,
            f"unsupported history file extension {suffix!r} -- must be .json or .csv.",
        )

    seen_ids: dict[str, int] = {}
    for record in records:
        seen_ids[record.source_match_id] = seen_ids.get(record.source_match_id, 0) + 1
    duplicates = sorted(match_id for match_id, count in seen_ids.items() if count > 1)
    if duplicates:
        raise HistoryValidationError(
            FORECAST_HISTORY_DUPLICATE_MATCH_ID,
            f"duplicate source_match_id value(s): {duplicates}.",
        )

    return records
