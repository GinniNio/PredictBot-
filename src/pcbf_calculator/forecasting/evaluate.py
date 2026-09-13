"""Chronological walk-forward evaluation for the Soccer 1X2 Poisson
research adapter.

For each finished match in the supplied history, in kickoff order, this
module forecasts that match using **only** matches strictly before its own
kickoff (within the configured training window) -- exactly the same
eligibility/gating logic ``soccer_1x2.py`` uses for a live fixture, reused
directly here so evaluation can never silently diverge from live forecast
behavior. There is no live research queue or market price involved at all
-- this command only ever needs the supplied history file and the config.

This produces **evidence for a later promotion decision**, not a
promotion decision itself: no PAPER-admission threshold is defined or
checked anywhere in this module. It reports multiclass Brier score, log
loss, calibration counts by probability band, coverage, and abstention
counts/reasons, and compares the model against two simple baselines
(uniform 1/3-1/3-1/3, and each competition's own historical outcome
frequency -- both computed walk-forward, from the same eligible-at-that-
point matches, never from the full dataset). A market-implied-probability
baseline is not implemented in this PR: the supplied-history contract
(``history.py``) carries no odds field, so there is nothing real to
compare against without inventing one; it can be added once a real
supplied-odds contract exists.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from .config import Soccer1x2PoissonConfig, load_config
from .errors import (
    FORECAST_AWAY_SAMPLE_INSUFFICIENT,
    FORECAST_COMPETITION_SAMPLE_INSUFFICIENT,
    FORECAST_HOME_SAMPLE_INSUFFICIENT,
    FORECAST_MODEL_EXECUTION_FAILED,
    FORECAST_PROBABILITIES_INVALID,
    HistoryValidationError,
)
from .history import MatchRecord, load_history
from .poisson_model import ModelExecutionError, assert_no_leakage, eligible_matches, score_fixture

SCHEMA_VERSION_REPORT = "pcbf-evaluate-soccer-1x2-report.v1"
SCHEMA_VERSION_FORECASTS = "pcbf-evaluate-soccer-1x2-forecasts.v1"
SCHEMA_VERSION_EXCLUSIONS = "pcbf-evaluate-soccer-1x2-exclusions.v1"

PROBABILITY_SUM_TOLERANCE = 1e-6
LOG_LOSS_PROBABILITY_FLOOR = 1e-9
OUTCOME_KEYS = ("home_win", "draw", "away_win")


def _result_label(match: MatchRecord) -> str:
    if match.home_goals > match.away_goals:
        return "home_win"
    if match.home_goals < match.away_goals:
        return "away_win"
    return "draw"


def _brier_component(model: dict[str, float], actual_label: str) -> float:
    return sum((model[key] - (1.0 if key == actual_label else 0.0)) ** 2 for key in OUTCOME_KEYS)


def _log_loss_component(model: dict[str, float], actual_label: str) -> float:
    p = max(model[actual_label], LOG_LOSS_PROBABILITY_FLOOR)
    return -math.log(p)


def _sort_key(match: MatchRecord) -> tuple[str, str]:
    return (match.kickoff_utc.isoformat(), match.source_match_id)


def _reliability_bins(pairs: list[tuple[float, int]]) -> list[dict[str, Any]]:
    """``pairs`` is (predicted_probability, outcome_indicator) for one
    outcome across every evaluated match. 10 fixed-width bins,
    [0.0,0.1) .. [0.9,1.0]."""
    bins = [{"bin_range": [i / 10, (i + 1) / 10], "count": 0, "sum_predicted": 0.0, "sum_actual": 0} for i in range(10)]
    for predicted, actual in pairs:
        index = min(int(predicted * 10), 9)
        bins[index]["count"] += 1
        bins[index]["sum_predicted"] += predicted
        bins[index]["sum_actual"] += actual
    table = []
    for row in bins:
        count = row["count"]
        table.append(
            {
                "bin_range": row["bin_range"],
                "count": count,
                "mean_predicted_probability": (row["sum_predicted"] / count) if count else None,
                "observed_frequency": (row["sum_actual"] / count) if count else None,
            }
        )
    return table


def _baseline_frequency(matches: list[MatchRecord]) -> dict[str, float] | None:
    if not matches:
        return None
    counts = {"home_win": 0, "draw": 0, "away_win": 0}
    for match in matches:
        counts[_result_label(match)] += 1
    n = len(matches)
    return {key: value / n for key, value in counts.items()}


def _metrics_summary(
    predictions: list[dict[str, float]], actual_labels: list[str]
) -> dict[str, Any]:
    n = len(predictions)
    if n == 0:
        return {
            "row_count": 0,
            "brier_score": None,
            "log_loss": None,
            "reliability_table": {key: [] for key in OUTCOME_KEYS},
        }
    brier = sum(_brier_component(p, a) for p, a in zip(predictions, actual_labels)) / n
    log_loss = sum(_log_loss_component(p, a) for p, a in zip(predictions, actual_labels)) / n
    reliability = {}
    for key in OUTCOME_KEYS:
        pairs = [(p[key], 1 if a == key else 0) for p, a in zip(predictions, actual_labels)]
        reliability[key] = _reliability_bins(pairs)
    return {"row_count": n, "brier_score": brier, "log_loss": log_loss, "reliability_table": reliability}


def run_evaluation(history_path: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    history = load_history(history_path)
    finished = sorted((m for m in history if m.is_finished), key=_sort_key)

    forecasts: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    model_predictions: list[dict[str, float]] = []
    baseline_uniform_predictions: list[dict[str, float]] = []
    baseline_frequency_predictions: list[dict[str, float]] = []
    actual_labels: list[str] = []

    def exclude(match: MatchRecord, reason: str, detail: str) -> None:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        exclusions.append(
            {
                "source_match_id": match.source_match_id,
                "competition": match.competition,
                "kickoff_utc": match.kickoff_utc.isoformat().replace("+00:00", "Z"),
                "home": match.home,
                "away": match.away,
                "reason": reason,
                "detail": detail,
            }
        )

    for match in finished:
        cutoff = match.kickoff_utc
        eligible = eligible_matches(history, match.competition, cutoff, config.training_window_days)
        assert_no_leakage(eligible, cutoff)

        home_role_count = sum(1 for m in eligible if m.home == match.home)
        away_role_count = sum(1 for m in eligible if m.away == match.away)

        if len(eligible) < config.minimum_competition_matches:
            exclude(
                match,
                FORECAST_COMPETITION_SAMPLE_INSUFFICIENT,
                f"{len(eligible)} eligible competition matches, need >= {config.minimum_competition_matches}.",
            )
            continue
        if home_role_count < config.minimum_team_home_matches:
            exclude(
                match,
                FORECAST_HOME_SAMPLE_INSUFFICIENT,
                f"{home_role_count} eligible home-role matches, need >= {config.minimum_team_home_matches}.",
            )
            continue
        if away_role_count < config.minimum_team_away_matches:
            exclude(
                match,
                FORECAST_AWAY_SAMPLE_INSUFFICIENT,
                f"{away_role_count} eligible away-role matches, need >= {config.minimum_team_away_matches}.",
            )
            continue

        try:
            point = score_fixture(eligible, match.home, match.away, config.maximum_goals_modelled)
        except ModelExecutionError as exc:
            exclude(match, FORECAST_MODEL_EXECUTION_FAILED, exc.reason)
            continue

        model = {"home_win": point.home_win, "draw": point.draw, "away_win": point.away_win}
        total = sum(model.values())
        if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in model.values()) or abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            exclude(match, FORECAST_PROBABILITIES_INVALID, f"model probabilities {model} invalid (sum={total}).")
            continue

        actual = _result_label(match)
        baseline_freq = _baseline_frequency(eligible) or {"home_win": 1 / 3, "draw": 1 / 3, "away_win": 1 / 3}

        forecasts.append(
            {
                "source_match_id": match.source_match_id,
                "competition": match.competition,
                "kickoff_utc": match.kickoff_utc.isoformat().replace("+00:00", "Z"),
                "home": match.home,
                "away": match.away,
                "actual_result": actual,
                "model": model,
                "baseline_uniform": {"home_win": 1 / 3, "draw": 1 / 3, "away_win": 1 / 3},
                "baseline_competition_frequency": baseline_freq,
                "evidence": {
                    "competition_match_count": len(eligible),
                    "home_role_match_count": home_role_count,
                    "away_role_match_count": away_role_count,
                },
            }
        )
        model_predictions.append(model)
        baseline_uniform_predictions.append({"home_win": 1 / 3, "draw": 1 / 3, "away_win": 1 / 3})
        baseline_frequency_predictions.append(baseline_freq)
        actual_labels.append(actual)

    candidate_count = len(finished)
    evaluated_count = len(forecasts)
    excluded_count = len(exclusions)
    if evaluated_count + excluded_count != candidate_count:
        raise AssertionError(
            f"Evaluation reconciliation failed: {evaluated_count} evaluated + {excluded_count} excluded "
            f"!= {candidate_count} finished matches. This is a bug in this module, never a caller input problem."
        )

    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "adapter_id": config.adapter_id,
        "config": config.to_dict(),
        "candidate_matches": candidate_count,
        "evaluated_matches": evaluated_count,
        "excluded_matches": excluded_count,
        "coverage_rate": (evaluated_count / candidate_count) if candidate_count else None,
        "reconciles": evaluated_count + excluded_count == candidate_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "model_metrics": _metrics_summary(model_predictions, actual_labels),
        "baseline_uniform_metrics": _metrics_summary(baseline_uniform_predictions, actual_labels),
        "baseline_competition_frequency_metrics": _metrics_summary(baseline_frequency_predictions, actual_labels),
        "note": (
            "PAPER-admission thresholds are NOT defined or checked here -- this report is "
            "evidence for a later, separate promotion decision. A market-implied-probability "
            "baseline is not included: the supplied history contract carries no odds field."
        ),
    }
    forecasts_doc = {
        "schema_version": SCHEMA_VERSION_FORECASTS,
        "adapter_id": config.adapter_id,
        "forecasts": forecasts,
    }
    exclusions_doc = {
        "schema_version": SCHEMA_VERSION_EXCLUSIONS,
        "adapter_id": config.adapter_id,
        "exclusions": exclusions,
    }

    return {
        "evaluation_report": report,
        "evaluation_forecasts": forecasts_doc,
        "evaluation_exclusions": exclusions_doc,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_evaluation_cli(history_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    result = run_evaluation(history_path, config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "evaluation-report.json", result["evaluation_report"])
    _write_json(output_dir / "evaluation-forecasts.json", result["evaluation_forecasts"])
    _write_json(output_dir / "evaluation-exclusions.json", result["evaluation_exclusions"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator evaluate-soccer-1x2",
        description=__doc__,
    )
    parser.add_argument("--history", type=Path, required=True, help="Supplied historical-match file (.json or .csv)")
    parser.add_argument("--config", type=Path, required=True, help="Soccer 1X2 Poisson adapter config YAML")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into")
    args = parser.parse_args(argv)

    try:
        result = run_evaluation_cli(args.history, args.config, args.output_dir)
    except HistoryValidationError as exc:
        print(f"FAILED: {exc.code}: {exc.reason}", file=sys.stderr)
        return 2

    report = result["evaluation_report"]
    print(
        f"OK: {report['evaluated_matches']} evaluated, {report['excluded_matches']} excluded "
        f"({report['candidate_matches']} candidate matches, coverage={report['coverage_rate']}) -> {args.output_dir}"
    )
    return 0
