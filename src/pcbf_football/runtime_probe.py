"""Dependency-free probe for the ChatGPT Python runtime.

This module deliberately contains no forecasting model. Its fixed distribution
exists only to prove that an uploaded wheel can be installed offline, invoked
deterministically, and checked by hash. PCBF must never promote its output above
RESEARCH-MODEL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ARTIFACT_ID = "PCBF_RUNTIME_PROBE"
ARTIFACT_VERSION = "0.0.1"
CLASSIFICATION_CEILING = "RESEARCH-MODEL"
FIXED_PROBABILITIES = {"home": 0.45, "draw": 0.28, "away": 0.27}
REQUIRED_PRICE_KEYS = ("home", "draw", "away")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _failure(event_id: Any, field: str, reason: str, input_hash: str) -> dict[str, Any]:
    payload = {
        "artifact_id": ARTIFACT_ID,
        "artifact_version": ARTIFACT_VERSION,
        "classification_ceiling": CLASSIFICATION_CEILING,
        "event_id": event_id,
        "input_hash": input_hash,
        "status": "FAILED",
        "failure": {"code": "M02_TEST_FAIL", "field": field, "reason": reason},
    }
    payload["calculation_hash"] = _sha256(payload)
    return payload


def run_probe(fixture: Any) -> dict[str, Any]:
    """Validate one fixture and return a deterministic compatibility result."""
    input_hash = _sha256(fixture)
    if not isinstance(fixture, dict):
        return _failure(None, "$", "Input must be a JSON object", input_hash)

    event_id = fixture.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return _failure(event_id, "event_id", "Mandatory event_id is missing", input_hash)

    prices = fixture.get("market_prices")
    if not isinstance(prices, dict):
        return _failure(event_id, "market_prices", "Complete opposing prices are required", input_hash)

    for key in REQUIRED_PRICE_KEYS:
        value = prices.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 1:
            return _failure(
                event_id,
                f"market_prices.{key}",
                "Price must be a number greater than 1",
                input_hash,
            )

    payload = {
        "artifact_id": ARTIFACT_ID,
        "artifact_version": ARTIFACT_VERSION,
        "classification_ceiling": CLASSIFICATION_CEILING,
        "event_id": event_id,
        "input_hash": input_hash,
        "probabilities": FIXED_PROBABILITIES,
        "status": "RUNTIME_PROBE",
        "failure": None,
    }
    payload["calculation_hash"] = _sha256(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Fixture JSON file")
    parser.add_argument("output", type=Path, nargs="?", help="Optional output JSON file")
    args = parser.parse_args(argv)

    fixture = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_probe(fixture)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "RUNTIME_PROBE" else 2
