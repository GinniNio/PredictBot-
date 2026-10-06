"""Record definitions for the append-only data files.

Every record type has required fields (must be present and non-empty unless
listed in NULLABLE) and optional context fields kept for auditing. Records
are plain dicts of strings so they round-trip through CSV unchanged.
"""

from __future__ import annotations

import hashlib
import re

# Bump DATA_SCHEMA_VERSION whenever a record type gains, loses or changes a
# field. The data folder records the highest version that has written to it;
# an older app refuses to write there (see storage.DataFolder).
DATA_SCHEMA_VERSION = 2
APP_VERSION = "0.3.0"

# PM_PAPER: a Polymarket-only opportunity that passed every check. It is a
# separate paper category until an evidence review allows Polymarket PICKs.
TIERS = ("PICK", "PM_PAPER", "WATCH", "RESEARCH", "REJECTED")
PRICED_TIERS = ("PICK", "PM_PAPER", "WATCH")
RESULTS = ("WIN", "LOSE", "VOID")

CAPTURE = {
    "required": ["capture_id", "captured_at_utc", "source", "source_url", "sport", "competition",
                 "event_id", "event_name", "kickoff_utc", "market", "selection_id", "selection",
                 "odds_decimal", "raw_payload_hash"],
    "optional": ["market_key", "market_outcomes", "market_odds", "selection_index", "country",
                 "kickoff_basis", "capture_file"],
}

SELECTION = {
    "required": ["selection_record_id", "capture_id", "priced_at_utc", "benchmark_source",
                 "benchmark_timestamp_utc", "benchmark_odds", "fair_odds", "edge_pct", "tier",
                 "validation_status", "rejection_reason"],
    "optional": ["selection_id", "event_name", "sport", "competition", "kickoff_utc", "market",
                 "selection", "selection_index", "bookmaker_odds", "market_odds", "benchmark_url",
                 "caution_flags", "research_note", "pack_id", "stake_notional", "origin",
                 # v2: quote vs capture time, provenance, eligibility evidence
                 "benchmark_captured_utc", "benchmark_bookmakers", "benchmark_file", "price_basis",
                 "fair_prob", "min_odds", "settlement_check", "market_depth", "app_version"],
}

BET = {
    "required": ["bet_id", "selection_record_id", "placed_at_utc", "stake", "bookmaker_odds",
                 "currency", "ticket_reference"],
    "optional": ["note"],
}

SETTLEMENT = {
    "required": ["selection_record_id", "settled_at_utc", "result", "return_amount", "settlement_source"],
    "optional": ["note"],
}

CLOSING = {
    "required": ["selection_record_id", "captured_at_utc", "source", "closing_odds",
                 "closing_fair_odds", "clv_pct"],
    # snapshot_label: "closing price" only when taken within CLOSE_WINDOW of
    # kickoff; otherwise "pre-kickoff snapshot, N min before kickoff".
    "optional": ["closing_url", "quote_time_utc", "minutes_before_kickoff", "snapshot_label", "origin"],
}

# A Bet9ja price checked again just before betting. Edge and tier are
# recomputed at the new price against the original benchmark.
RECHECK = {
    "required": ["recheck_id", "selection_record_id", "rechecked_at_utc", "initial_odds", "rechecked_odds",
                 "fair_odds", "edge_pct", "tier"],
    "optional": ["min_odds", "note"],
}

# Operator decision on a fuzzy or ambiguous benchmark match.
MATCH_REVIEW = {
    "required": ["event_id", "quote_key", "decision", "decided_at_utc"],
    "optional": ["event_name", "quote_event", "source", "note"],
}

# Operator confirmation that a benchmark source settles a market the same
# way as Bet9ja (retirement, draws, overtime). Latest row per key wins.
RULE = {
    "required": ["rule_key", "status", "confirmed_at_utc"],
    "optional": ["note"],
}

# Written evidence review. Real money and Polymarket PICK eligibility change
# only through one of these, never automatically.
EVIDENCE_REVIEW = {
    "required": ["review_id", "reviewed_at_utc", "decision", "summary"],
    "optional": ["polymarket_picks_allowed", "metrics_snapshot", "reviewer"],
}

SCHEMAS = {"capture": CAPTURE, "selection": SELECTION, "bet": BET, "settlement": SETTLEMENT,
           "closing": CLOSING, "recheck": RECHECK, "match_review": MATCH_REVIEW, "rule": RULE,
           "evidence_review": EVIDENCE_REVIEW}
REVIEW_DECISIONS = ("continue paper", "change method", "stop", "allow small real stakes")
RULE_STATUSES = ("same as bet9ja", "differs")
MATCH_DECISIONS = ("accept", "reject")

# Fields allowed to be empty: a REJECTED or RESEARCH selection has no prices to
# report, and an unsettled stake has no return yet.
NULLABLE = {
    "selection": {"benchmark_source", "benchmark_timestamp_utc", "benchmark_odds", "fair_odds",
                  "edge_pct", "rejection_reason"},
    "capture": {"kickoff_utc", "source_url"},
    "bet": {"ticket_reference"},
    "settlement": set(),
    "closing": set(),
    "recheck": set(),
    "match_review": set(),
    "rule": set(),
    "evidence_review": set(),
}

ISO_MINUTE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")


def fields(kind: str) -> list[str]:
    s = SCHEMAS[kind]
    return s["required"] + s["optional"]


def validate(kind: str, rec: dict) -> list[str]:
    """Return a list of problems; empty means the record is valid."""
    errors = []
    for f in SCHEMAS[kind]["required"]:
        if f not in rec:
            errors.append(f"missing {f}")
        elif rec[f] in ("", None) and f not in NULLABLE[kind]:
            errors.append(f"empty {f}")
    for f, v in rec.items():
        if f.endswith("_utc") and v and not ISO_MINUTE.match(str(v)):
            errors.append(f"{f} not YYYY-MM-DDTHH:MMZ: {v}")
    if kind == "selection":
        if rec.get("tier") not in TIERS:
            errors.append(f"tier must be one of {TIERS}")
        if rec.get("tier") in PRICED_TIERS:
            for f in ("benchmark_source", "benchmark_timestamp_utc", "benchmark_odds", "fair_odds", "edge_pct"):
                if not rec.get(f):
                    errors.append(f"{rec['tier']} needs {f}")
        if rec.get("tier") == "REJECTED" and not rec.get("rejection_reason"):
            errors.append("REJECTED needs rejection_reason")
    if kind == "settlement" and rec.get("result") not in RESULTS:
        errors.append(f"result must be one of {RESULTS}")
    if kind == "rule" and rec.get("status") not in RULE_STATUSES:
        errors.append(f"status must be one of {RULE_STATUSES}")
    if kind == "match_review" and rec.get("decision") not in MATCH_DECISIONS:
        errors.append(f"decision must be one of {MATCH_DECISIONS}")
    if kind == "evidence_review" and rec.get("decision") not in REVIEW_DECISIONS:
        errors.append(f"decision must be one of {REVIEW_DECISIONS}")
    if kind == "recheck":
        try:
            if float(rec.get("rechecked_odds", "")) <= 1:
                errors.append("rechecked odds must be > 1")
        except ValueError:
            errors.append("rechecked odds must be a number")
    if kind == "bet":
        try:
            if float(rec.get("stake", "")) <= 0 or float(rec.get("bookmaker_odds", "")) <= 1:
                errors.append("stake must be > 0 and odds > 1")
        except ValueError:
            errors.append("stake and bookmaker_odds must be numbers")
    return errors


def stable_id(prefix: str, *parts) -> str:
    """Deterministic ID from the record's identifying inputs."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"{prefix}_{h}"
