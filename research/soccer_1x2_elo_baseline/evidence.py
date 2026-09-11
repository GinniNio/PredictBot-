"""Evidence-class classification for the four FROZEN model-development
splits: is the raw Football-Data content that produced this run's frozen
hashes a confirmed real download, hand-crafted fixture content, missing
entirely, or present but unusable?

Correction (operator review, after the first live run of this baseline):
an earlier version of this classification inferred "real data" from a
split's own `status == "BUILT"` plus a nonzero row count. That is wrong —
a synthetic, hand-crafted fixture under `tests/fixtures/football_data/`
satisfies both conditions just as easily as a real download does (this is
exactly how every one of this package's own tests exercises the pipeline).
Row count and build status say nothing about WHERE the bytes came from.

The only trustworthy signal is `data_pipeline/retrieval_log.json` —
written ONLY by a real invocation of `data_pipeline.download.run()`
against football-data.co.uk, and only for files it actually received
bytes for over the network (see that module's own docstring). A raw file
on disk is confirmed LIVE only when:

1. a retrieval-log entry exists for its exact `(league_code, season_code)`
   pair, recorded as a successful download, AND
2. that entry's own `sha256` (computed by `download.py` on the raw bytes
   the instant they were received, before any parsing) matches a FRESH
   SHA-256 of the file's CURRENT on-disk content.

Re-checking the hash (not just the presence of a log entry) matters: it
means a fixture file dropped at the same `raw_dir` path after an old
retrieval log was written — or a stale retrieval log left over from a
previous, unrelated run — is never mistaken for the download that log
entry actually describes. `tests/fixtures/football_data/` content is
never a byte-for-byte match to anything football-data.co.uk ever served,
so this check has no way to accidentally confirm a fixture as live.

Four possible classifications for one training run's frozen-split inputs
(never for an individual split — this baseline pins one frozen-scope
`frozen_dataset_hash` across all four, so one evidence_class covers all
four consistently):

- `LIVE_SOURCE_VALIDATED` — every raw file backing the four frozen splits
  that exists on disk is confirmed (per the two-part check above) to be
  the real football-data.co.uk download, and at least one usable row was
  produced. The only classification real, live Football-Data content can
  ever receive.
- `FIXTURE_ONLY_VALIDATED` — raw files exist and produced usable rows,
  but at least one of them is NOT confirmed live (the normal case for
  `tests/fixtures/football_data/`, and the safe fallback for any run
  where live confirmation is incomplete or ambiguous — this
  classification is never upgraded to LIVE_SOURCE_VALIDATED on a partial
  match).
- `SOURCE_UNAVAILABLE` — no raw file backing any of the four frozen
  splits exists on disk at all (e.g. a sandbox with no downloaded data
  and no fixtures pointed at `raw_dir`).
- `SOURCE_NOT_USABLE` — at least one raw file exists on disk, but the
  four frozen splits produced zero usable rows between them (e.g. every
  present file failed validation entirely). Distinct from
  `SOURCE_UNAVAILABLE`: the source was present, just not usable.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.dataset_builder import raw_file_path  # noqa: E402

EVIDENCE_LIVE_SOURCE_VALIDATED = "LIVE_SOURCE_VALIDATED"
EVIDENCE_FIXTURE_ONLY_VALIDATED = "FIXTURE_ONLY_VALIDATED"
EVIDENCE_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
EVIDENCE_SOURCE_NOT_USABLE = "SOURCE_NOT_USABLE"

DEFAULT_RETRIEVAL_LOG_PATH = REPO_ROOT / "data_pipeline" / "retrieval_log.json"

_IMPLEMENTATION_STATUS_BY_EVIDENCE_CLASS: dict[str, str] = {
    EVIDENCE_LIVE_SOURCE_VALIDATED: (
        "LIVE-DATA RESEARCH BASELINE: every raw file backing the four frozen "
        "model-development splits (split_training/split_calibration_validation/"
        "split_locked_test/split_out_of_time_retrospective_holdout) is confirmed, "
        "by SHA-256 match against data_pipeline/retrieval_log.json's own record of "
        "a real football-data.co.uk download, to be genuine live source content -- "
        "never a synthetic fixture. The metrics below are real-data research-"
        "baseline evidence. This label is evidentiary only: it does NOT itself "
        "register soccer_1x2, authorize PAPER/CASH, or change any promotion "
        "threshold -- classification stays RESEARCH-MODEL regardless of these "
        "numbers."
    ),
    EVIDENCE_FIXTURE_ONLY_VALIDATED: (
        "IMPLEMENTATION-ONLY: built and tested against fixture content that could "
        "not be confirmed as a real football-data.co.uk download (typically "
        "tests/fixtures/football_data/). Every metric below proves the code "
        "behaves correctly; NONE of it is a real-data performance claim."
    ),
    EVIDENCE_SOURCE_UNAVAILABLE: (
        "NO EVIDENCE: no raw Football-Data file backing any of the four frozen "
        "model-development splits was present on disk at build time. Every row "
        "count below is zero -- this report proves neither real-data performance "
        "nor fixture-based code correctness."
    ),
    EVIDENCE_SOURCE_NOT_USABLE: (
        "SOURCE PRESENT BUT UNUSABLE: at least one raw file backing the four "
        "frozen model-development splits was present on disk, but validation "
        "produced zero usable rows across all four. This proves neither real-data "
        "performance nor fixture-based code correctness."
    ),
}


def describe_evidence_class(evidence_class: str) -> str:
    """Human-readable `implementation_status` text for a given
    `evidence_class` -- the ONLY input this depends on. Never derives its
    text from row counts or split status directly (see this module's
    docstring for why that was the bug being fixed)."""

    return _IMPLEMENTATION_STATUS_BY_EVIDENCE_CLASS[evidence_class]


def _sha256_of_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _load_confirmed_live_hashes(retrieval_log_path: Path) -> dict[tuple[str, str], set[str]]:
    """`(league_code, season_code) -> {sha256, ...}` for every entry this
    retrieval log records as a SUCCESSFUL download. Empty (never an error)
    when the log is missing or unreadable -- an environment that never ran
    a real download simply confirms nothing as live."""

    if not retrieval_log_path.exists():
        return {}
    try:
        payload = json.loads(retrieval_log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    confirmed: dict[tuple[str, str], set[str]] = {}
    for entry in payload.get("downloads", []):
        league_code = entry.get("league_code")
        season_code = entry.get("season_code")
        sha256 = entry.get("sha256")
        if not (league_code and season_code and sha256):
            continue
        confirmed.setdefault((league_code, season_code), set()).add(sha256)
    return confirmed


def classify_evidence(
    *,
    raw_dir: Path,
    season_codes_by_frozen_split: dict[str, list[str]],
    league_codes: tuple[str, ...],
    frozen_row_count: int,
    retrieval_log_path: Path = DEFAULT_RETRIEVAL_LOG_PATH,
) -> str:
    """Classify the source provenance backing the four FROZEN splits.
    `season_codes_by_frozen_split` maps each of `train.FROZEN_SPLIT_IDS` to
    the season codes it actually resolved to (from `build_split`'s own
    `season_codes` field) -- never a hardcoded season list, so a future
    contract edit is picked up automatically. `frozen_row_count` is the
    total row count across all four frozen splits' own built records.

    Never uses `frozen_row_count` (or any split's own `status`) to infer
    LIVE vs FIXTURE -- only to distinguish SOURCE_UNAVAILABLE/
    SOURCE_NOT_USABLE from the two classes where content was actually
    validated. See this module's docstring."""

    confirmed_live = _load_confirmed_live_hashes(retrieval_log_path)

    any_file_present = False
    all_present_confirmed_live = True
    for season_codes in season_codes_by_frozen_split.values():
        for league_code in league_codes:
            for season_code in season_codes:
                path = raw_file_path(raw_dir, league_code, season_code)
                if not path.exists():
                    continue
                any_file_present = True
                local_hash = _sha256_of_file(path)
                if local_hash is None or local_hash not in confirmed_live.get((league_code, season_code), set()):
                    all_present_confirmed_live = False

    if not any_file_present:
        return EVIDENCE_SOURCE_UNAVAILABLE
    if frozen_row_count == 0:
        return EVIDENCE_SOURCE_NOT_USABLE
    if all_present_confirmed_live:
        return EVIDENCE_LIVE_SOURCE_VALIDATED
    return EVIDENCE_FIXTURE_ONLY_VALIDATED
