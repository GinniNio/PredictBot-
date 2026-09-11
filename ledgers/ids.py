"""Stable, deterministic ID generation for both ledgers.

Every ID here is a SHA-256 hash of a fixed, ordered "natural key" string --
never a random UUID, never a timestamp. The same logical forecast or ticket
always hashes to the same ID, which is what makes re-importing the same
JSON file idempotent (see ``forecast_ledger.append_recorded`` and
``betting_ledger.append_placed``): a second import of byte-identical input
produces the same ID, is recognized as a duplicate, and is never appended
twice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

ID_HASH_LENGTH = 16


def _stable_hash(natural_key_parts: list[Any]) -> str:
    """SHA-256 over a JSON-serialized, sorted-key encoding of
    ``natural_key_parts`` -- stable across Python versions/runs because it
    never depends on dict insertion order or float repr quirks beyond
    JSON's own (inputs here are always plain strings/ints)."""

    canonical = json.dumps(natural_key_parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:ID_HASH_LENGTH]


def forecast_id(fixture_id: str, market_type: str, model_version: str) -> str:
    """Deterministic forecast_id from the natural key (fixture, market,
    model version) -- NEVER from created_at or any other field that
    legitimately differs between an original recording and a later
    re-import of the same logical forecast."""

    return "fc_" + _stable_hash(["forecast", fixture_id, market_type, model_version])


def ticket_id_from_external_ref(external_ticket_ref: str) -> str:
    """Deterministic ticket_id from an operator-supplied external
    reference (e.g. a bookmaker slip number) -- the preferred path,
    since it is stable even if the operator re-types the same ticket's
    legs in a different order."""

    return "tk_" + _stable_hash(["ticket_ref", external_ticket_ref])


def ticket_id_from_content(placed_at_utc: str, ticket_type: str, unit_stake: float, legs: list[dict[str, Any]]) -> str:
    """Fallback deterministic ticket_id when no external reference is
    supplied: hashes the ticket's own immutable content (placement time,
    type, unit stake, and each leg's fixture/market/selection/odds, in
    the order given). Re-importing the identical ticket JSON reproduces
    the same ID; changing any leg produces a different one."""

    leg_key = [
        [leg.get("forecast_id"), leg.get("fixture_id"), leg.get("market_type"), leg.get("selection"), leg.get("placed_odds")]
        for leg in legs
    ]
    return "tk_" + _stable_hash(["ticket_content", placed_at_utc, ticket_type, unit_stake, leg_key])
