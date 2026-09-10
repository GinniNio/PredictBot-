"""Scaffold entry point for the real PCBF football calculator.

This module deliberately contains no probability model, feature engineering,
calibration, de-vigging or EV math. Those all require a design spec that does
not exist yet (see ``docs/CALCULATOR_KICKOFF.md`` and
``docs/DEVELOPER_HANDOFF.md`` Agent D). Until that design work lands, this
CLI accepts the same host-neutral JSON-in/JSON-out contract as
``pcbf_football`` (see ``docs/HOST_CONTRACT.md``) and returns a typed
``NOT_IMPLEMENTED`` failure rather than ever fabricating a probability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ARTIFACT_ID = "PCBF_CALCULATOR"
ARTIFACT_VERSION = "0.0.1"
CLASSIFICATION_CEILING = "RESEARCH-MODEL"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def run_calculator(fixture: Any) -> dict[str, Any]:
    """Validate one fixture and return a typed not-implemented result.

    This never computes or returns a probability. The real calculation
    (features, calibration, de-vigging, EV math) has no design spec yet and
    is deliberately out of scope for this scaffold.
    """
    input_hash = _sha256(fixture)
    event_id = fixture.get("event_id") if isinstance(fixture, dict) else None

    payload = {
        "artifact_id": ARTIFACT_ID,
        "artifact_version": ARTIFACT_VERSION,
        "classification_ceiling": CLASSIFICATION_CEILING,
        "event_id": event_id,
        "input_hash": input_hash,
        "status": "FAILED",
        "failure": {
            "code": "NOT_IMPLEMENTED",
            "field": None,
            "reason": (
                "The real PCBF football calculator has not been built yet. "
                "This scaffold only proves the host-neutral JSON-in/JSON-out "
                "CLI contract; it never fabricates a probability."
            ),
        },
    }
    payload["calculation_hash"] = _sha256(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Fixture JSON file")
    parser.add_argument("output", type=Path, nargs="?", help="Optional output JSON file")
    args = parser.parse_args(argv)

    fixture = json.loads(args.input.read_text(encoding="utf-8"))
    result = run_calculator(fixture)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 2
