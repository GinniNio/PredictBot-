"""Evaluation for the Soccer 1X2 Elo baseline.

Reports, for `split_locked_test` and `split_out_of_time_retrospective_holdout`
SEPARATELY (never blended into one merged metric) and for
`split_genuine_prospective_scoring` (reported ONLY as a "rolling
settlement observation" — see the dataset contract's `correction_v2_1_0`
— NEVER as a "test" or "prospective evaluation"):

- Multiclass Brier score and log loss (with a documented probability
  floor).
- Calibration error + a reliability table.
- Accuracy — explicitly a SECONDARY diagnostic, never the headline metric.
- The same metrics broken down BY LEAGUE and BY OUTCOME (H/D/A).
- A LEAGUE-FREQUENCY NAIVE BASELINE, fit on `split_training` ONLY (see
  `train.TrainingResult.training_frequency_by_league`), scored on the same
  evaluation split with the identical machinery.
- A DE-VIGGED OPENING BOOKMAKER PROBABILITIES comparison, reusing
  `pcbf_calculator.pricing.engine.analyze_market` READ-ONLY (this is
  reuse of an already-reviewed pure de-vig function — it registers
  nothing and touches no adapter dispatch table) on each row's first
  complete opening-price bookmaker observation found. Rows with no
  complete opening-price observation are excluded from ONLY this one
  comparison, with the excluded count reported honestly.

Calibration binning choice (documented, single choice): a SINGLE POOLED
binning across all three outcome-probability values per row — for every
row and every outcome `c`, the pair `(predicted_probability_c,
1_if_actual==c_else_0)` is one binning observation, into 10 equal-width
bins over `[0, 1]`. This pools H/D/A probability mass into one reliability
curve; per-outcome calibration (see `by_outcome`) repeats the same binning
restricted to one outcome's own predicted-probability column. Calibration
error is the COUNT-WEIGHTED MEAN ABSOLUTE DIFFERENCE between each
non-empty bin's mean predicted probability and its empirical (actual)
frequency:
`sum(count_b * |mean_pred_b - mean_actual_b|) / sum(count_b)` over
non-empty bins `b`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_SRC_DIR = REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# READ-ONLY reuse of the already-reviewed de-vig implementation — a plain
# Python import of a pure function at call time. This registers nothing,
# touches no adapter dispatch table, and is not "wiring soccer_1x2 into
# the production adapter" (see this module's docstring).
from pcbf_calculator.pricing.engine import PricingFailure, analyze_market

from . import evidence
from .model import CLASS_ORDER
from .train import TrainingResult

LOG_LOSS_PROBABILITY_FLOOR = 1e-12
NUM_CALIBRATION_BINS = 10

# Splits reported as genuine one-way, held-out TEST evidence.
TEST_SPLIT_IDS: tuple[str, ...] = ("split_locked_test", "split_out_of_time_retrospective_holdout")
# This split is NEVER described as a "test" or "prospective evaluation" —
# see the dataset contract's correction_v2_1_0 and this module's docstring.
ROLLING_SETTLEMENT_OBSERVATION_SPLIT_ID = "split_genuine_prospective_scoring"


def multiclass_brier(predicted: dict[str, float], actual_label: str) -> float:
    return sum((predicted.get(c, 0.0) - (1.0 if c == actual_label else 0.0)) ** 2 for c in CLASS_ORDER)


def log_loss_single(predicted: dict[str, float], actual_label: str, floor: float = LOG_LOSS_PROBABILITY_FLOOR) -> float:
    import math

    p = predicted.get(actual_label, 0.0)
    p = min(max(p, floor), 1.0 - floor)
    return -math.log(p)


def _reliability_bins(pairs: list[tuple[float, int]], num_bins: int = NUM_CALIBRATION_BINS) -> list[dict[str, Any]]:
    """`pairs` is a list of `(predicted_probability, actual_indicator)`.
    Bins `[0, 1]` into `num_bins` equal-width bins; the top edge `1.0`
    falls into the last bin."""

    bins: list[dict[str, Any]] = [
        {"bin_index": i, "bin_low": i / num_bins, "bin_high": (i + 1) / num_bins, "sum_pred": 0.0, "sum_actual": 0.0, "count": 0}
        for i in range(num_bins)
    ]
    for pred, actual in pairs:
        index = min(int(pred * num_bins), num_bins - 1)
        bins[index]["sum_pred"] += pred
        bins[index]["sum_actual"] += actual
        bins[index]["count"] += 1

    table: list[dict[str, Any]] = []
    for b in bins:
        count = b["count"]
        table.append(
            {
                "bin_index": b["bin_index"],
                "bin_range": [b["bin_low"], b["bin_high"]],
                "predicted_probability_midpoint": (b["sum_pred"] / count) if count else None,
                "empirical_frequency": (b["sum_actual"] / count) if count else None,
                "count": count,
            }
        )
    return table


def calibration_error_from_table(table: list[dict[str, Any]]) -> float:
    total_count = sum(row["count"] for row in table)
    if total_count == 0:
        return 0.0
    weighted_abs_diff = sum(
        row["count"] * abs(row["predicted_probability_midpoint"] - row["empirical_frequency"])
        for row in table
        if row["count"] > 0
    )
    return weighted_abs_diff / total_count


def score_predictions(rows: list[tuple[dict[str, float], str, str]], _include_by_league: bool = True) -> dict[str, Any]:
    """`rows` is a list of `(predicted_probabilities, actual_label,
    league_code)` triples. Returns overall + by-league + by-outcome
    metrics. Accuracy is included as an explicit SECONDARY diagnostic
    field, never surfaced as the headline number by any caller of this
    function.

    `_include_by_league` is an internal recursion guard: the by-league
    breakdown recurses into this same function once per league, so that
    recursive call passes `_include_by_league=False` to compute only that
    league's own overall/by-outcome numbers without recursing again (this
    also correctly handles the single-league case, where recursing WITH
    by-league enabled would otherwise call itself with an identical row
    set forever)."""

    if not rows:
        return {
            "row_count": 0,
            "brier_score": None,
            "log_loss": None,
            "calibration_error": None,
            "reliability_table": [],
            "accuracy_secondary_diagnostic": None,
            "by_league": {},
            "by_outcome": {},
        }

    n = len(rows)
    briers = [multiclass_brier(pred, actual) for pred, actual, _league in rows]
    losses = [log_loss_single(pred, actual) for pred, actual, _league in rows]
    correct = sum(1 for pred, actual, _league in rows if max(pred, key=pred.get) == actual)

    pooled_pairs: list[tuple[float, int]] = []
    for pred, actual, _league in rows:
        for c in CLASS_ORDER:
            pooled_pairs.append((pred.get(c, 0.0), 1 if actual == c else 0))
    reliability_table = _reliability_bins(pooled_pairs)
    calib_error = calibration_error_from_table(reliability_table)

    by_league: dict[str, Any] = {}
    if _include_by_league:
        leagues = sorted({league for _pred, _actual, league in rows})
        for league_code in leagues:
            league_rows = [(pred, actual, league) for pred, actual, league in rows if league == league_code]
            by_league[league_code] = score_predictions(league_rows, _include_by_league=False)

    by_outcome: dict[str, Any] = {}
    for c in CLASS_ORDER:
        class_briers = [(pred.get(c, 0.0) - (1.0 if actual == c else 0.0)) ** 2 for pred, actual, _league in rows]
        class_pairs = [(pred.get(c, 0.0), 1 if actual == c else 0) for pred, actual, _league in rows]
        class_table = _reliability_bins(class_pairs)
        rows_where_actual = [(pred, actual, league) for pred, actual, league in rows if actual == c]
        by_outcome[c] = {
            "row_count": len(rows),
            "actual_occurrence_count": len(rows_where_actual),
            "brier_contribution_mean": sum(class_briers) / n,
            "log_loss_when_actual": (
                sum(log_loss_single(pred, actual) for pred, actual, _l in rows_where_actual) / len(rows_where_actual)
                if rows_where_actual
                else None
            ),
            "calibration_error": calibration_error_from_table(class_table),
            "reliability_table": class_table,
        }

    return {
        "row_count": n,
        "brier_score": sum(briers) / n,
        "log_loss": sum(losses) / n,
        "calibration_error": calib_error,
        "reliability_table": reliability_table,
        "accuracy_secondary_diagnostic": correct / n,
        "by_league": by_league,
        "by_outcome": by_outcome,
    }


def _first_complete_opening_observation(price_observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first opening-price bookmaker observation with all three
    outcomes numerically priced, in the order `price_observations` already
    lists them (`data_pipeline.dataset_builder`'s own per-file ordering) —
    documented choice: FIRST complete one found, never an aggregate across
    several bookmakers."""

    for obs in price_observations:
        if obs.get("price_stage") != "OPENING":
            continue
        if obs.get("home_odds") and obs.get("draw_odds") and obs.get("away_odds"):
            return obs
    return None


def score_devigged_opening_odds(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """`rows` is a list of the same row dicts `train.py` produces
    (`{"feature_vector", "label", "record_ref"}`). For each row with at
    least one complete opening-price bookmaker observation, de-vig that
    bookmaker's prices via `pcbf_calculator.pricing.engine.analyze_market`
    (READ-ONLY reuse — see this module's docstring) and score the result
    against the actual outcome with the identical machinery used for the
    model. Rows with no complete opening-price observation are excluded
    from ONLY this comparison; `excluded_row_count` reports that honestly
    — never a synthetic/guessed price is substituted."""

    scored_triples: list[tuple[dict[str, float], str, str]] = []
    excluded_row_count = 0
    for row in rows:
        record_ref = row["record_ref"]
        observation = _first_complete_opening_observation(record_ref.get("price_observations", []))
        if observation is None:
            excluded_row_count += 1
            continue
        try:
            market = analyze_market(
                {"H": observation["home_odds"], "D": observation["draw_odds"], "A": observation["away_odds"]}
            )
        except PricingFailure:
            excluded_row_count += 1
            continue
        predicted = {item["outcome"]: item["fair_probability"] for item in market["outcomes"]}
        scored_triples.append((predicted, row["label"], record_ref["league_code"]))

    metrics = score_predictions(scored_triples)
    metrics["excluded_row_count"] = excluded_row_count
    metrics["included_row_count"] = len(scored_triples)
    return metrics


def score_naive_league_frequency_baseline(rows: list[dict[str, Any]], training_frequency_by_league: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Score the constant per-league H/D/A frequency vector (fit on
    `split_training` ONLY — never peeking at this evaluation split) against
    `rows` with the identical Brier/log-loss/calibration machinery."""

    default_frequency = {"H": 1.0 / 3, "D": 1.0 / 3, "A": 1.0 / 3}
    triples: list[tuple[dict[str, float], str, str]] = []
    for row in rows:
        record_ref = row["record_ref"]
        league_code = record_ref["league_code"]
        predicted = training_frequency_by_league.get(league_code, default_frequency)
        triples.append((predicted, row["label"], league_code))
    return score_predictions(triples)


def score_model(rows: list[dict[str, Any]], training_result: TrainingResult) -> dict[str, Any]:
    triples: list[tuple[dict[str, float], str, str]] = []
    for row in rows:
        record_ref = row["record_ref"]
        predicted = training_result.model_artifact.predict_proba(row["feature_vector"], temperature=training_result.temperature)
        triples.append((predicted, row["label"], record_ref["league_code"]))
    return score_predictions(triples)


def build_evaluation_report(training_result: TrainingResult) -> dict[str, Any]:
    """Build the full evaluation report: `split_locked_test` and
    `split_out_of_time_retrospective_holdout` under `"test_splits"`
    (reported SEPARATELY, one key each), and
    `split_genuine_prospective_scoring` under
    `"rolling_settlement_observation"` — deliberately NOT named
    "prospective_evaluation" or "test" anywhere in this report's own key
    names or text."""

    report: dict[str, Any] = {
        # Derived ONLY from `evidence_class` — never from row counts or
        # split status directly. See `evidence.py`'s docstring: a
        # synthetic, hand-crafted fixture can report a nonzero row count
        # and `status == "BUILT"` just as easily as a real download, so
        # neither is a trustworthy signal on its own.
        "evidence_class": training_result.evidence_class,
        "implementation_status": evidence.describe_evidence_class(training_result.evidence_class),
        "frozen_hashes": training_result.frozen_hashes.to_dict(),
        "code_hash": training_result.code_hash,
        "artifact_hash": training_result.artifact_hash,
        "combined_hash": training_result.combined_hash,
        "prospective_stream_observation": training_result.prospective_stream.to_dict(),
        "temperature": training_result.temperature,
        "split_statuses": training_result.split_statuses,
        "test_splits": {},
        "rolling_settlement_observation": None,
    }

    for split_id in TEST_SPLIT_IDS:
        rows = training_result.rows_by_split.get(split_id, [])
        report["test_splits"][split_id] = {
            "row_count": len(rows),
            "model_metrics": score_model(rows, training_result),
            "naive_league_frequency_baseline_metrics": score_naive_league_frequency_baseline(
                rows, training_result.training_frequency_by_league
            ),
            "devigged_opening_odds_metrics": score_devigged_opening_odds(rows),
        }

    rolling_rows = training_result.rows_by_split.get(ROLLING_SETTLEMENT_OBSERVATION_SPLIT_ID, [])
    report["rolling_settlement_observation"] = {
        "label": (
            "ROLLING SETTLEMENT OBSERVATION ONLY — Football-Data's 2627 file is a "
            "post-hoc settled-results feed, never a pre-match fixture feed (see "
            "dataset contract correction_v2_1_0). These numbers describe already-"
            "played 2026-27 matches this pipeline has settlement data for; they are "
            "NOT a test and NOT genuine prospective/PAPER evaluation."
        ),
        "row_count": len(rolling_rows),
        "model_metrics": score_model(rolling_rows, training_result),
        "naive_league_frequency_baseline_metrics": score_naive_league_frequency_baseline(
            rolling_rows, training_result.training_frequency_by_league
        ),
        "devigged_opening_odds_metrics": score_devigged_opening_odds(rolling_rows),
    }

    return report


def _format_metrics_markdown(name: str, metrics: dict[str, Any]) -> list[str]:
    lines = [f"**{name}** — rows={metrics['row_count']}"]
    if metrics["row_count"]:
        lines.append(
            f"- Brier: {metrics['brier_score']:.4f}, log loss: {metrics['log_loss']:.4f}, "
            f"calibration error: {metrics['calibration_error']:.4f}, "
            f"accuracy (secondary diagnostic): {metrics['accuracy_secondary_diagnostic']:.4f}"
        )
    return lines


def _format_reliability_table_markdown(name: str, metrics: dict[str, Any]) -> list[str]:
    """Render the pooled (H/D/A-combined) reliability table backing
    `metrics['calibration_error']` — bin range, sample count, mean
    predicted confidence, and observed (empirical) accuracy per bin.
    Empty bins are still listed (count=0), never silently dropped, so a
    reader can see which confidence ranges this split never predicted
    into."""

    table = metrics.get("reliability_table") or []
    lines = [f"**{name} — reliability table**", ""]
    if not table:
        lines.append("(no rows)")
        lines.append("")
        return lines
    lines.append("| Bin range | Count | Mean confidence | Observed accuracy |")
    lines.append("|---|---:|---:|---:|")
    for row in table:
        low, high = row["bin_range"]
        confidence = f"{row['predicted_probability_midpoint']:.4f}" if row["count"] else "—"
        observed = f"{row['empirical_frequency']:.4f}" if row["count"] else "—"
        lines.append(f"| [{low:.1f}, {high:.1f}) | {row['count']} | {confidence} | {observed} |")
    lines.append("")
    return lines


def write_evaluation_report(
    report: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = ["# Soccer 1X2 Elo baseline — evaluation report", ""]
    lines.append(f"evidence_class: `{report['evidence_class']}`")
    lines.append("")
    lines.append(f"> {report['implementation_status']}")
    lines.append("")
    lines.append("## Frozen model-development inputs")
    lines.append("")
    for split_id, split_hash in report["frozen_hashes"]["split_hashes"].items():
        lines.append(f"- `{split_id}`: `{split_hash}`")
    lines.append(f"- frozen_dataset_hash (combined, 4 splits only): `{report['frozen_hashes']['combined_hash']}`")
    lines.append(f"- code_hash: `{report['code_hash']}`")
    lines.append(f"- artifact_hash: `{report['artifact_hash']}`")
    lines.append(f"- combined_hash: `{report['combined_hash']}`")
    lines.append(f"- temperature: `{report['temperature']}`")
    lines.append("")
    lines.append("## Prospective stream (rolling, append-only — never part of the frozen hashes above)")
    lines.append("")
    stream = report["prospective_stream_observation"]
    lines.append(f"- content_hash: `{stream['content_hash']}`")
    lines.append(f"- run_timestamp_utc: `{stream['run_timestamp_utc']}`")
    lines.append(f"- row_count: {stream['row_count']}")
    lines.append("")
    lines.append("## Test splits (reported separately — never blended)")
    lines.append("")
    for split_id, split_report in report["test_splits"].items():
        lines.append(f"### {split_id}")
        lines.append("")
        lines.extend(_format_metrics_markdown("Model", split_report["model_metrics"]))
        lines.extend(_format_metrics_markdown("Naive league-frequency baseline", split_report["naive_league_frequency_baseline_metrics"]))
        devig = split_report["devigged_opening_odds_metrics"]
        lines.extend(_format_metrics_markdown("De-vigged opening odds", devig))
        lines.append(f"- De-vigged opening odds: excluded {devig['excluded_row_count']} row(s) with no complete opening price set")
        lines.append("")
        lines.extend(_format_reliability_table_markdown("Model", split_report["model_metrics"]))

    rolling = report["rolling_settlement_observation"]
    lines.append("## Rolling settlement observation — split_genuine_prospective_scoring")
    lines.append("")
    lines.append(f"> {rolling['label']}")
    lines.append("")
    lines.extend(_format_metrics_markdown("Model", rolling["model_metrics"]))
    lines.extend(_format_metrics_markdown("Naive league-frequency baseline", rolling["naive_league_frequency_baseline_metrics"]))
    devig = rolling["devigged_opening_odds_metrics"]
    lines.extend(_format_metrics_markdown("De-vigged opening odds", devig))
    lines.append(f"- De-vigged opening odds: excluded {devig['excluded_row_count']} row(s) with no complete opening price set")
    lines.append("")
    lines.extend(_format_reliability_table_markdown("Model", rolling["model_metrics"]))

    hash_provenance_report = report.get("hash_provenance")
    if hash_provenance_report is not None:
        lines.append("## Frozen-hash provenance check (against expected_hashes.json)")
        lines.append("")
        lines.append(
            "Every value below is either `CANDIDATE` (no real hash pinned yet — "
            "`expected_hashes.json` still says `UNFROZEN_PENDING_LIVE_RUN`), "
            "`CONFIRMED` (matches a human-pinned real value), or `MISMATCH` (a "
            "pinned value no longer matches — never auto-corrected, fails this run)."
        )
        lines.append("")
        lines.append("| Split | Status | Expected | Computed |")
        lines.append("|---|---|---|---|")
        for check in hash_provenance_report["split_checks"]:
            lines.append(f"| {check['split_id']} | {check['status']} | `{check['expected']}` | `{check['computed']}` |")
        combined = hash_provenance_report["combined_check"]
        lines.append(f"| {combined['split_id']} | {combined['status']} | `{combined['expected']}` | `{combined['computed']}` |")
        lines.append("")

    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


DEFAULT_REPORT_JSON_PATH = REPO_ROOT / "research" / "soccer_1x2_elo_baseline" / "reports" / "evaluation_report.json"
DEFAULT_REPORT_MARKDOWN_PATH = REPO_ROOT / "research" / "soccer_1x2_elo_baseline" / "reports" / "evaluation_report.md"
DEFAULT_ARTIFACT_PATH = REPO_ROOT / "research" / "soccer_1x2_elo_baseline" / "reports" / "model_artifact.json"


def main(
    raw_dir: Path | None = None,
    report_json_path: Path = DEFAULT_REPORT_JSON_PATH,
    report_markdown_path: Path = DEFAULT_REPORT_MARKDOWN_PATH,
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
) -> None:
    """CLI entry point: run the full train+evaluate pipeline against
    whatever `data_pipeline/raw/` actually contains (real downloads in a
    live GitHub Actions run; nothing in this sandbox — see
    `train.py`'s module docstring on EVIDENCE STATUS), write the
    evaluation report JSON/Markdown, and check the freshly computed frozen
    hashes against `expected_hashes.json` (see `hash_provenance.py`).

    `raw_dir`/`report_json_path`/`report_markdown_path`/`artifact_path` all
    default to this package's real, committed locations — a caller (the
    workflow, or `python -m research.soccer_1x2_elo_baseline.evaluate`)
    never needs to pass any of them. They exist as explicit parameters
    purely so tests can point every side-effecting path somewhere
    temporary, instead of either mutating this repo's real committed
    report files or relying on mocking a function's already-bound default
    argument (which does not work in Python — default values are captured
    once, at `def` time, not re-read per call).

    Exit status: non-zero if `hash_provenance.check_frozen_hashes` reports
    ANY `MISMATCH` — a pinned real hash that a later run's computed hash no
    longer matches. Before any real hash is pinned (every value in
    `expected_hashes.json` is still `UNFROZEN_PENDING_LIVE_RUN`), every
    check is a `CANDIDATE`, never a `MISMATCH`, so a run against real
    Football-Data data — or even this sandbox's empty/fixture-only state —
    never fails on that account alone. This exit code is the actual
    enforcement mechanism the operator asked for: the workflow step this
    function is invoked from FAILS on a real hash mismatch, it does not
    just print a warning that could be missed."""

    import sys

    from . import hash_provenance
    from .train import DEFAULT_RAW_DIR, run_twice_determinism_check
    from .evidence import EVIDENCE_LIVE_SOURCE_VALIDATED

    effective_raw_dir = raw_dir if raw_dir is not None else DEFAULT_RAW_DIR
    is_identical, first_result, _second_result = run_twice_determinism_check(raw_dir=effective_raw_dir)
    print(f"run_twice_determinism_check: is_identical={is_identical}")
    result = first_result

    hash_report = hash_provenance.check_frozen_hashes(
        result.frozen_hashes.split_hashes, result.frozen_hashes.combined_hash
    )

    report = build_evaluation_report(result)
    report["hash_provenance"] = hash_report.to_dict()
    write_evaluation_report(report, report_json_path, report_markdown_path)
    print(f"Wrote {report_json_path} and {report_markdown_path}")

    # `train.main()`'s own path writes this artifact, but the workflow only
    # ever invokes THIS module's main() — write it here too (same content,
    # same default path) so a real live run's actual fitted artifact is
    # what gets uploaded as evidence, never the stale fixture-based one
    # this PR committed locally.
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(result.model_artifact.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {artifact_path}")

    print(f"evidence_class: {result.evidence_class}")
    print("Frozen-hash provenance check (against expected_hashes.json — never auto-updated):")
    for check in hash_report.split_checks:
        print(f"  {check.split_id}: status={check.status} expected={check.expected} computed={check.computed}")
    print(
        f"  frozen_dataset_hash: status={hash_report.combined_check.status} "
        f"expected={hash_report.combined_check.expected} computed={hash_report.combined_check.computed}"
    )

    print(
        "soccer_1x2 stays completely UNREGISTERED regardless of these numbers — no "
        "model-admission-registry row, no promotion-threshold change, no adapter "
        "wiring happens here or anywhere in this PR."
    )

    # A pinned real hash is only ever expected to match a run whose evidence
    # is CONFIRMED LIVE — comparing a pinned real hash against a fixture-
    # only, missing, or unusable run's computed hash is an apples-to-
    # oranges comparison by construction (that run never touched the real
    # data the pinned hash describes), so it must never fail the run. Only
    # a MISMATCH on a run whose own evidence_class is
    # EVIDENCE_LIVE_SOURCE_VALIDATED means what the operator asked this
    # check to mean: "the real, live frozen dataset itself has drifted
    # since it was pinned."
    if hash_report.has_mismatch and result.evidence_class == EVIDENCE_LIVE_SOURCE_VALIDATED:
        print(
            "FAILING: one or more frozen-split hashes no longer match the pinned "
            "expected_hashes.json value, and this run's evidence_class is "
            "LIVE_SOURCE_VALIDATED — the real frozen dataset has drifted since it "
            "was pinned. This is never auto-corrected — a human must review why "
            "and, if the new content is correct, pin the new hash in its own small "
            "evidence PR."
        )
        sys.exit(1)
    elif hash_report.has_mismatch:
        print(
            "NOTE: one or more computed hashes disagree with a pinned "
            "expected_hashes.json value, but this run's evidence_class is "
            f"{result.evidence_class} (not LIVE_SOURCE_VALIDATED) — a fixture, "
            "missing, or unusable-source run was never expected to reproduce the "
            "real, pinned hash, so this is not treated as a failure."
        )


if __name__ == "__main__":
    main()
