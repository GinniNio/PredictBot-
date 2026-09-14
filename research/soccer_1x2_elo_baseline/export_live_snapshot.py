"""Exports the freshest available team-strength state this pipeline can
honestly produce, for a live forecasting adapter to consume.

Reuses `train.py::train_pipeline`'s exact same loading/chronological-Elo
machinery (never reimplemented) but additionally captures what
`train_pipeline` itself discards: the final `EloEngine`/`SeasonStageTracker`
state after replaying the FULL real chronological history this pipeline
has access to -- the four frozen splits plus the rolling, append-only
`split_genuine_prospective_scoring` stream (`train.TRAINING_SPLIT_IDS`,
already the same set `train_pipeline` builds its one continuous
Elo/season-stage history across).

This is DERIVED, AGGREGATE data (final per-team rating and season-stage
counters, plus which real match was processed last) -- never raw
third-party row content -- so it is safe to persist and redistribute
under this repository's own existing evidence policy (the same policy
that already allows committing `model_artifact.json`/`evaluation_report.json`
while forbidding redistribution of `data_pipeline/raw/`'s or
`data_pipeline/dataset/`'s actual row content; see
`.github/workflows/football-data-feasibility.yml`'s own upload-artifact
step comment).

**Explicit limitation, not fixed by this module**: this snapshot is only
as fresh as the real historical data this pipeline's own run had access
to at that moment -- `last_processed_match`'s date is the honest
"knowledge cutoff" of every rating and season-stage count in the output.
A live fixture whose kickoff is meaningfully later than this cutoff is
being forecast against team strength that may already be stale (real
matches this pipeline does not yet know about may have been played since).
The consuming adapter is responsible for checking this cutoff against a
configured maximum artifact age and abstaining when it is exceeded --
this module only reports the cutoff honestly, it never hides or pads it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_pipeline.dataset_builder import DEFAULT_RAW_DIR, build_split, load_contract_rows
from data_pipeline.validation import parse_date

from .elo import EloEngine
from .features import SeasonStageTracker, build_dataset
from .train import TRAINING_SPLIT_IDS, compute_code_hash


def build_live_snapshot(raw_dir: Path = DEFAULT_RAW_DIR) -> dict[str, Any]:
    contract_rows = load_contract_rows()

    all_records: list[dict[str, Any]] = []
    split_statuses: dict[str, str] = {}
    for split_id in TRAINING_SPLIT_IDS:
        split_result = build_split(split_id, raw_dir=raw_dir, contract_rows=contract_rows)
        split_statuses[split_id] = split_result["status"]
        for record in split_result["records"]:
            tagged = dict(record)
            tagged["split_id"] = split_id
            all_records.append(tagged)

    elo_engine = EloEngine()
    season_tracker = SeasonStageTracker()
    build_result = build_dataset(all_records, elo_engine=elo_engine, season_tracker=season_tracker)

    # The last chronologically-processed record's own date -- the honest
    # "as of" knowledge cutoff for every rating/season-stage count below.
    last_ref = build_result.record_refs[-1] if build_result.record_refs else None
    last_processed_match = None
    last_processed_utc = None
    if last_ref is not None:
        iso_date, _fmt = parse_date(last_ref["date_raw"])
        last_processed_utc = f"{iso_date}T00:00:00Z" if iso_date is not None else None
        last_processed_match = {
            "league_code": last_ref["league_code"],
            "season_code": last_ref["season_code"],
            "date_raw": last_ref["date_raw"],
            "home_team": last_ref["home_team"],
            "away_team": last_ref["away_team"],
            "result": last_ref["result"],
            "split_id": last_ref.get("split_id"),
        }

    ratings = elo_engine.snapshot_ratings()
    matches_played = elo_engine.snapshot_matches_played()
    season_counts = season_tracker.snapshot_counts()

    # Determine each league's own most-recent season_code among processed
    # records, so a live fixture's season_stage lookup uses the right key
    # (season_stage resets per season -- an older season's count is not
    # meaningful for a new one).
    latest_season_by_league: dict[str, str] = {}
    for ref in build_result.record_refs:
        league_code = ref["league_code"]
        season_code = ref["season_code"]
        if league_code not in latest_season_by_league or season_code >= latest_season_by_league[league_code]:
            latest_season_by_league[league_code] = season_code

    return {
        "schema_version": "soccer-1x2-elo-live-snapshot.v1",
        "code_hash": compute_code_hash(),
        "split_statuses": split_statuses,
        "last_processed_match": last_processed_match,
        "last_processed_match_date_utc": last_processed_utc,
        "team_count": len({key[1] for key in ratings}),
        "competition_count": len({key[0] for key in ratings}),
        "match_count_processed": len(build_result.record_refs),
        "exclusions_count": len(build_result.exclusions),
        "latest_season_by_league": latest_season_by_league,
        "elo_ratings": {f"{league}|{team}": rating for (league, team), rating in ratings.items()},
        "elo_matches_played": {f"{league}|{team}": count for (league, team), count in matches_played.items()},
        "season_stage_counts": {
            f"{league}|{season}|{team}": count for (league, season, team), count in season_counts.items()
        },
    }


def main() -> None:
    snapshot = build_live_snapshot()
    reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "live_snapshot.json"
    out_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"team_count={snapshot['team_count']} competition_count={snapshot['competition_count']} "
          f"match_count_processed={snapshot['match_count_processed']} "
          f"last_processed_match_date_utc={snapshot['last_processed_match_date_utc']}")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
