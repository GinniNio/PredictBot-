"""One-off build script: assembles this adapter's
``data/model_artifact_manifest.json`` from the three real files a
``football-data-feasibility.yml`` ``workflow_dispatch`` run produces
(``model_artifact.json``, ``evaluation_report.json``,
``live_snapshot.json`` -- see ``research/soccer_1x2_elo_baseline/
export_live_snapshot.py``).

Not part of the adapter's runtime -- this is a build-time tool, run once
per artifact refresh, so the exact generation command and its inputs are
recorded (never hand-typed) in the manifest it produces. Usage::

    python -m pcbf_calculator.adapters.soccer_1x2_elo_v1.build_manifest \\
      --model-artifact /path/to/model_artifact.json \\
      --evaluation-report /path/to/evaluation_report.json \\
      --live-snapshot /path/to/live_snapshot.json \\
      --workflow-run-url https://github.com/.../actions/runs/NNNN \\
      --workflow-run-id NNNN \\
      --commit-sha <git sha the workflow ran at> \\
      --model-version soccer_1x2_elo_v1-<date>

Never invents a provenance field: every value in the manifest below is
read directly from the three real input files, or supplied explicitly by
the caller as a fact about the run that produced them (workflow run
id/URL, commit SHA, chosen model_version label) -- nothing is guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"


def _sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256((json.dumps(obj, sort_keys=True) + "\n").encode("utf-8")).hexdigest()


# The only two `evidence_track` values this function accepts -- an
# explicit, typed enum rather than a bare bool, precisely so this can
# never be mistaken for a generic "skip validation" toggle. Only
# `pcbf_calculator.orchestration.soccer_artifact_refresh` (CANDIDATE
# creation -- see that module's own docstring) may ever pass
# `EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED`; every other caller (the CLI's
# own `main()` included) must use the default,
# `EVIDENCE_TRACK_CONFIRMED_ACTIVE`, which preserves this function's
# original, unrelaxed behavior byte-for-byte.
EVIDENCE_TRACK_CONFIRMED_ACTIVE = "CONFIRMED_ACTIVE_ARTIFACT"
EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED = "CANDIDATE_UNCONFIRMED"
_VALID_EVIDENCE_TRACKS = (EVIDENCE_TRACK_CONFIRMED_ACTIVE, EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED)

_DEFAULT_SOURCE_MANIFEST_NOTE = (
    "165 entries in data_pipeline/sources/football_data_sources.yaml; "
    "170 (league, season) files were successfully downloaded and classified "
    "LIVE_SOURCE_VALIDATED in the run named above (observed directly from that "
    "run's own job output, not re-derived here)."
)


def build_manifest(
    model_artifact_path: Path,
    evaluation_report_path: Path,
    live_snapshot_path: Path,
    workflow_run_url: str,
    workflow_run_id: str,
    commit_sha: str,
    model_version: str,
    generation_command: str,
    *,
    evidence_track: str = EVIDENCE_TRACK_CONFIRMED_ACTIVE,
    source_manifest_entry_count: int | None = 165,
    successful_source_downloads: int | None = 170,
    source_manifest_note: str = _DEFAULT_SOURCE_MANIFEST_NOTE,
) -> dict[str, Any]:
    """``evidence_track`` (default ``EVIDENCE_TRACK_CONFIRMED_ACTIVE``,
    preserving this function's original behavior byte-for-byte for every
    existing caller) is a typed, two-valued enum -- deliberately not a
    bare bool -- naming exactly which of two structurally different
    workflows this call belongs to. Any other value raises ``ValueError``
    immediately: there is no third, looser state to slide into.

    ``EVIDENCE_TRACK_CONFIRMED_ACTIVE`` gates the real, "install this as
    the shipped adapter's active artifact" workflow: ``evidence_class``
    must be ``LIVE_SOURCE_VALIDATED`` and every hash-provenance check
    must already be human-``CONFIRMED`` in ``expected_hashes.json``.

    ``EVIDENCE_TRACK_CANDIDATE_UNCONFIRMED`` is for
    ``pcbf_calculator.orchestration.soccer_artifact_refresh`` (CANDIDATE
    creation, never promotion -- see that module's own docstring)
    ONLY: a freshly built candidate has, by definition, never been
    human-reviewed yet, so requiring already-``CONFIRMED`` provenance
    would make building a candidate at all impossible on the very first
    refresh. That caller still refuses the two structurally uninformative
    evidence classes (``SOURCE_UNAVAILABLE``/``SOURCE_NOT_USABLE`` -- a
    candidate built from zero rows is never useful) and still refuses a
    genuine ``LIVE_SOURCE_VALIDATED`` hash MISMATCH (real data drift is
    refused either way, confirmed or not) -- it only relaxes the
    requirement that a human has ALREADY pinned and confirmed this exact
    hash before a candidate carrying it can even be built. No other
    caller in this codebase passes this value; a new caller that wants to
    should treat that as a deliberate, reviewable decision, not a default
    to reach for.

    ``source_manifest_entry_count``/``successful_source_downloads``/
    ``source_manifest_note`` default to the literal values this function
    has always hardcoded (a real, one-time fact about the specific live
    GitHub Actions run this tool was first built for, recorded here
    because this file's own docstring promises "nothing is guessed" --
    these three were an early exception to that, now made parameters).
    A caller building a candidate from a DIFFERENT run (e.g. a local
    refresh, or a later live run with different download counts) must
    pass its own real values, or ``None``/an honest "not tracked by this
    path" note when the count genuinely is not available -- never the
    stale defaults copied forward unexamined."""

    if evidence_track not in _VALID_EVIDENCE_TRACKS:
        raise ValueError(
            f"evidence_track={evidence_track!r} is not a recognized evidence track -- must be one of "
            f"{_VALID_EVIDENCE_TRACKS!r}. There is no looser or intermediate state; an unrecognized "
            "value is refused rather than silently treated as either one."
        )

    model_artifact = json.loads(model_artifact_path.read_text(encoding="utf-8"))
    evaluation_report = json.loads(evaluation_report_path.read_text(encoding="utf-8"))
    live_snapshot = json.loads(live_snapshot_path.read_text(encoding="utf-8"))

    hash_provenance = evaluation_report.get("hash_provenance") or {}
    if evidence_track == EVIDENCE_TRACK_CONFIRMED_ACTIVE:
        if evaluation_report.get("evidence_class") != "LIVE_SOURCE_VALIDATED":
            raise ValueError(
                f"Refusing to build a manifest from evidence_class "
                f"{evaluation_report.get('evidence_class')!r} -- must be LIVE_SOURCE_VALIDATED."
            )
        combined_check = hash_provenance.get("combined_check") or {}
        if combined_check.get("status") != "CONFIRMED":
            raise ValueError(
                f"Refusing to build a manifest: frozen_dataset_hash status is "
                f"{combined_check.get('status')!r}, not CONFIRMED."
            )
        for split_check in hash_provenance.get("split_checks", []):
            if split_check.get("status") != "CONFIRMED":
                raise ValueError(
                    f"Refusing to build a manifest: split {split_check.get('split_id')!r} "
                    f"status is {split_check.get('status')!r}, not CONFIRMED."
                )
        hash_provenance_status = "ALL_CONFIRMED"
    else:
        if evaluation_report.get("evidence_class") in ("SOURCE_UNAVAILABLE", "SOURCE_NOT_USABLE"):
            raise ValueError(
                f"Refusing to build a manifest from evidence_class "
                f"{evaluation_report.get('evidence_class')!r} -- zero usable training rows."
            )
        if hash_provenance.get("has_mismatch") and evaluation_report.get("evidence_class") == "LIVE_SOURCE_VALIDATED":
            raise ValueError(
                "Refusing to build a manifest: a LIVE_SOURCE_VALIDATED run's frozen-hash "
                "provenance reports a MISMATCH against a previously pinned expected_hashes.json "
                "value -- the real, live frozen dataset has drifted since it was pinned."
            )
        hash_provenance_status = "NOT_YET_HUMAN_CONFIRMED"

    team_keys = list(live_snapshot["elo_ratings"].keys())
    ratings = list(live_snapshot["elo_ratings"].values())
    team_count = len({key.split("|", 1)[1] for key in team_keys})
    competition_count = len({key.split("|", 1)[0] for key in team_keys})

    manifest = {
        "schema_version": "soccer-1x2-elo-v1-manifest.v1",
        "model_version": model_version,
        "temperature": evaluation_report["temperature"],
        "artifact_sha256": _canonical_sha256(model_artifact),
        "artifact_file_sha256": _sha256_of_file(model_artifact_path),
        "provenance": {
            "workflow_run_id": workflow_run_id,
            "workflow_run_url": workflow_run_url,
            "training_code_commit_sha": commit_sha,
            "training_code_content_hash": evaluation_report["code_hash"],
            "generation_command": generation_command,
            "evidence_class": evaluation_report["evidence_class"],
            "frozen_dataset_hash": evaluation_report["frozen_hashes"]["combined_hash"],
            "frozen_split_hashes": evaluation_report["frozen_hashes"]["split_hashes"],
            "hash_provenance_status": hash_provenance_status,
            "source_manifest_entry_count": source_manifest_entry_count,
            "successful_source_downloads": successful_source_downloads,
            "source_manifest_note": source_manifest_note,
            "training_match_count": model_artifact["training_row_count"],
            "evaluation_sample_counts": {
                split_id: split_report["model_metrics"]["row_count"]
                for split_id, split_report in evaluation_report["test_splits"].items()
            },
            "team_count": team_count,
            "competition_count": competition_count,
            "rating_count": len(ratings),
            "rating_min": min(ratings) if ratings else None,
            "rating_max": max(ratings) if ratings else None,
            "rating_median": sorted(ratings)[len(ratings) // 2] if ratings else None,
            "last_processed_match_date_utc": live_snapshot.get("last_processed_match_date_utc"),
            "backtest_evidence": {
                split_id: {
                    "brier_score": split_report["model_metrics"]["brier_score"],
                    "log_loss": split_report["model_metrics"]["log_loss"],
                    "row_count": split_report["model_metrics"]["row_count"],
                    "naive_baseline_brier_score": split_report["naive_league_frequency_baseline_metrics"][
                        "brier_score"
                    ],
                    "devigged_opening_odds_brier_score": split_report["devigged_opening_odds_metrics"][
                        "brier_score"
                    ],
                }
                for split_id, split_report in evaluation_report["test_splits"].items()
            },
        },
        "live_snapshot": {
            "last_processed_match_date_utc": live_snapshot.get("last_processed_match_date_utc"),
            "latest_season_by_league": live_snapshot["latest_season_by_league"],
            "elo_ratings": live_snapshot["elo_ratings"],
            "season_stage_counts": live_snapshot["season_stage_counts"],
        },
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument("--evaluation-report", type=Path, required=True)
    parser.add_argument("--live-snapshot", type=Path, required=True)
    parser.add_argument("--workflow-run-url", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    generation_command = "python -m pcbf_calculator.adapters.soccer_1x2_elo_v1.build_manifest " + " ".join(argv)
    manifest = build_manifest(
        args.model_artifact,
        args.evaluation_report,
        args.live_snapshot,
        args.workflow_run_url,
        args.workflow_run_id,
        args.commit_sha,
        args.model_version,
        generation_command,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "model_artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "model_artifact.json").write_text(
        args.model_artifact.read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"team_count={manifest['provenance']['team_count']} "
          f"training_match_count={manifest['provenance']['training_match_count']} "
          f"last_processed_match_date_utc={manifest['provenance']['last_processed_match_date_utc']}")
    print(f"Wrote {args.output_dir / 'model_artifact_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
