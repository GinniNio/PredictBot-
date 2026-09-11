"""Expected-hash provenance checking for the four FROZEN model-development
splits (`train.FROZEN_SPLIT_IDS` + the combined `frozen_dataset_hash`).

This module NEVER writes `expected_hashes.json` — that file is authored and
updated ONLY by a human, in a small, separate, reviewed "evidence PR",
after reading a real live-workflow run's printed hashes. This module only
READS it (`load_expected_hashes`) and COMPARES it against a freshly
computed set of frozen hashes (`check_frozen_hashes`):

- Before any real hash is known, `expected_hashes.json` ships with every
  split's expected value as the literal string `UNFROZEN_PENDING_LIVE_RUN`
  — comparing a freshly computed hash against that placeholder is always a
  `CANDIDATE` status, never a `MISMATCH`. This is what makes the first-ever
  live run (and every run before a human has reviewed and pinned real
  values) safe: it prints its computed hashes as CANDIDATES for a human to
  review, never fails, and never freezes anything on its own.
- Once a human has reviewed a real run's output and pinned real hash
  values into `expected_hashes.json` (recording which run produced them —
  see that file's own `provenance_by_split`), a LATER run whose freshly
  computed hash disagrees with a pinned value is a `MISMATCH` — loud,
  reported, and (via `HashProvenanceReport.has_mismatch`) meant to fail
  that run's CI step. Never silently accepted, never auto-corrected.

Critical operator-mandated guarantee this module exists to enforce: a hash
computed from `tests/fixtures/football_data/` synthetic content must never
be treated as if it were a real, live-data expected value. Nothing in this
module, or in any of this package's own tests, ever writes a non-
`UNFROZEN_PENDING_LIVE_RUN` value into the committed `expected_hashes.json`
— that only ever happens by a human hand-editing the file in its own PR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

UNFROZEN_STATUS = "UNFROZEN_PENDING_LIVE_RUN"

STATUS_CANDIDATE = "CANDIDATE"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_MISMATCH = "MISMATCH"

DEFAULT_EXPECTED_HASHES_PATH = Path(__file__).resolve().parent / "expected_hashes.json"


@dataclass
class SplitHashCheck:
    split_id: str
    expected: str
    computed: str
    status: str  # CANDIDATE | CONFIRMED | MISMATCH

    def to_dict(self) -> dict[str, Any]:
        return {"split_id": self.split_id, "expected": self.expected, "computed": self.computed, "status": self.status}


@dataclass
class HashProvenanceReport:
    split_checks: list[SplitHashCheck]
    combined_check: SplitHashCheck
    has_mismatch: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_checks": [c.to_dict() for c in self.split_checks],
            "combined_check": self.combined_check.to_dict(),
            "has_mismatch": self.has_mismatch,
        }


def load_expected_hashes(path: Path = DEFAULT_EXPECTED_HASHES_PATH) -> dict[str, Any]:
    """Read-only load of `expected_hashes.json`. Never writes it."""

    return json.loads(path.read_text(encoding="utf-8"))


def _check_one(split_id: str, expected: str, computed: str) -> SplitHashCheck:
    if expected == UNFROZEN_STATUS:
        status = STATUS_CANDIDATE
    elif expected == computed:
        status = STATUS_CONFIRMED
    else:
        status = STATUS_MISMATCH
    return SplitHashCheck(split_id=split_id, expected=expected, computed=computed, status=status)


def check_frozen_hashes(
    computed_split_hashes: dict[str, str],
    computed_combined_hash: str,
    expected: dict[str, Any] | None = None,
) -> HashProvenanceReport:
    """Compare freshly computed frozen-split hashes (`train.FrozenHashes`'s
    own `split_hashes`/`combined_hash`) against `expected_hashes.json`'s
    pinned values (or `UNFROZEN_PENDING_LIVE_RUN` placeholders). Pure
    comparison — never writes `expected`, never mutates its caller's
    inputs, never auto-resolves a `MISMATCH`."""

    expected = expected if expected is not None else load_expected_hashes()

    split_checks = [
        _check_one(split_id, expected["split_hashes"].get(split_id, UNFROZEN_STATUS), computed_hash)
        for split_id, computed_hash in computed_split_hashes.items()
    ]
    combined_check = _check_one(
        "frozen_dataset_hash",
        expected.get("frozen_dataset_hash", UNFROZEN_STATUS),
        computed_combined_hash,
    )
    has_mismatch = any(c.status == STATUS_MISMATCH for c in split_checks) or combined_check.status == STATUS_MISMATCH

    return HashProvenanceReport(split_checks=split_checks, combined_check=combined_check, has_mismatch=has_mismatch)
