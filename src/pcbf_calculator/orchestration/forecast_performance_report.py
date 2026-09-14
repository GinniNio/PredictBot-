"""Prospective performance and calibration reporting from the forecast
ledger's ``SCORED`` events.

    forecast ledger (SCORED events)
    -> validated, deduplicated sample
    -> overall + per-outcome calibration + breakdown reports
    -> closing-line comparison (kept fully separate from model-quality metrics)

One command::

    python -m pcbf_calculator report-forecast-performance \\
        --ledger-dir ledger_data --output-dir runs/performance-report

**Read-only.** This module only ever calls ``ledgers.storage.read_all`` on
the forecast ledger's own JSONL file -- it never appends, never mutates,
and never touches ``ledgers/betting_ledger.py``, the model-admission
registry, ``classification_ceiling``, promotion/threshold state, staking,
ticket construction, or any ``operator_decision``. It produces reports,
nothing else.

Every count and metric here is derived ENTIRELY from a forecast's own
current state (the merge of its RECORDED + SCORED events, via
``ledgers.storage.latest_state`` -- reused, never reimplemented),
restricted to forecast_ids that actually carry a ``SCORED`` event. A
forecast that was never scored never appears anywhere in these reports --
this module answers "how did the scored evidence actually turn out,"
never "how many forecasts exist."

**Determinism -- byte-identical output for unchanged ledger content.** No
wall-clock timestamp (``datetime.now()``) appears anywhere in these
reports; the closest analog, ``as_of_last_settled_at_utc``, is the
maximum ``settled_at_utc`` already recorded on disk among the included
forecasts -- a pure function of the ledger's own content, not of when
this command happens to run. Every grouping (breakdown buckets,
calibration bins, the included/excluded lists themselves) is iterated in
a fixed, sorted order, and every JSON file is written with
``sort_keys=True``. Re-running this command against byte-identical
ledger content always produces byte-identical output files.

**Small samples are never silently treated as evidence.** Every count in
every report -- overall, per breakdown bucket, per calibration bin --
carries its own ``small_sample`` flag: ``sample_count <
SMALL_SAMPLE_THRESHOLD``. ``SMALL_SAMPLE_THRESHOLD`` (50) deliberately
reuses the same "full correction" sample-size precedent this project's
own Kasiro Brain history already documents for `pcbf_ml`'s personal
calibration layer (zero correction below 15 settled bets, full only at
50+) -- not a new, arbitrarily-chosen number. This module never infers
readiness for any promotion decision from a small (or, for that matter,
any) sample size -- that stays a human, evidence-reviewed decision.

**Rejected, not silently included.** A ``SCORED`` forecast is excluded
from every metric (and reported, with a typed reason, in
``excluded-records.json``) when:

- ``market_type`` is not ``"1X2"`` -- this module's only supported market
  today (``REASON_UNSUPPORTED_MARKET_TYPE``).
- ``model_probabilities`` is malformed: missing an outcome, non-numeric,
  negative, or not summing to 1 within tolerance
  (``REASON_MALFORMED_PROBABILITIES``) -- the same validity check the
  adapter itself already applies at forecast time
  (``FORECAST_PROBABILITIES_INVALID``), re-checked here rather than
  trusted blindly.
- ``actual_result`` is not one of H/D/A (``REASON_INVALID_ACTUAL_RESULT``).
- The ledger's own stored ``brier_score``/``log_loss`` disagrees with
  what ``ledgers.scoring`` independently recomputes from that SAME
  forecast's own ``model_probabilities``/``actual_result``
  (``REASON_SCORE_MISMATCH``) -- an internal-consistency check, never
  trusting the ledger's stored numbers blindly.
- Two or more forecast_ids share the same real-world ``fixture_id`` but
  report DIFFERENT ``actual_result`` values
  (``REASON_CONFLICTING_ACTUAL_RESULT_FOR_FIXTURE``) -- the same real
  match cannot have two different results; every forecast sharing that
  fixture_id is excluded, never resolved by picking one.
- A single forecast_id's own ledger history somehow carries more than
  one ``SCORED`` event (``REASON_DUPLICATE_SCORED_EVENTS``) -- normal
  operation can never produce this (``ledgers.storage.append_terminal_if_new``
  only ever writes a forecast_id's FIRST ``SCORED`` event; a later
  identical re-import is a no-op and a later different one is refused),
  so this is a defensive check against that invariant somehow being
  violated elsewhere (a hand-edited ledger, a future bug). Without it,
  ``ledgers.storage.latest_state``'s own last-event-wins merge would
  otherwise silently prefer whichever of the two disagreeing SCORED
  events happens to be LAST in file order -- never guessed here either;
  the forecast is excluded instead.

**Explicit reconciliation invariant.** Every eligible scored record --
one entry per forecast_id carrying a SCORED event, per
``load_scored_states`` -- appears in EXACTLY ONE of ``included`` or
``excluded-records.json``, never both, never neither.
``classify_and_filter`` asserts this directly (raising if violated -- a
bug in this module, never a caller input problem), and
``performance-summary.json`` additionally reports it as its own
``reconciles`` boolean, independently recomputed from that report's own
``counts`` block, so a caller reading the report alone can confirm the
totals reconcile without re-deriving anything.

Explicit boundaries: this module produces reports only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


def _ensure_ledgers_importable() -> None:
    """Same fallback ``orchestration/forecast_ledger_writer.py`` and
    ``orchestration/football_data_settlement.py`` already use for
    themselves -- see either module's own copy of this helper for the
    full rationale."""

    try:
        import ledgers  # noqa: F401

        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "ledgers" / "__init__.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_ledgers_importable()

from ledgers import forecast_ledger, scoring  # noqa: E402
from ledgers.storage import all_entity_ids, latest_state, read_all  # noqa: E402

SCHEMA_VERSION_SUMMARY = "pcbf-forecast-performance-summary.v1"
SCHEMA_VERSION_CALIBRATION = "pcbf-forecast-calibration-report.v1"
SCHEMA_VERSION_CLOSING_LINE = "pcbf-forecast-closing-line-report.v1"
SCHEMA_VERSION_EXCLUDED = "pcbf-forecast-performance-excluded-records.v1"

MARKET_TYPE = "1X2"
CLASS_ORDER = scoring.CLASS_ORDER  # ("H", "D", "A")

# Reused, documented sample-size precedent -- see this module's own
# docstring for why this is 50, not a freshly-invented number.
SMALL_SAMPLE_THRESHOLD = 50

PROBABILITY_SUM_TOLERANCE = 1e-6
SCORE_RECOMPUTATION_TOLERANCE = 1e-9

# Fixed, documented reliability-diagram bins for per-outcome calibration --
# 10 equal-width bins over [0, 1], the standard reliability-diagram
# convention. Never derived from the data itself: a data-driven binning
# would make bin edges non-deterministic and non-comparable run to run.
CALIBRATION_BIN_EDGES: tuple[float, ...] = tuple(round(i / 10, 1) for i in range(11))

REASON_UNSUPPORTED_MARKET_TYPE = "REPORT_UNSUPPORTED_MARKET_TYPE"
REASON_MALFORMED_PROBABILITIES = "REPORT_MALFORMED_PROBABILITIES"
REASON_INVALID_ACTUAL_RESULT = "REPORT_INVALID_ACTUAL_RESULT"
REASON_SCORE_MISMATCH = "REPORT_SCORE_MISMATCH"
REASON_CONFLICTING_ACTUAL_RESULT_FOR_FIXTURE = "REPORT_CONFLICTING_ACTUAL_RESULT_FOR_FIXTURE"
REASON_DUPLICATE_SCORED_EVENTS = "REPORT_DUPLICATE_SCORED_EVENTS_FOR_FORECAST"

UNKNOWN_BUCKET = "UNKNOWN"


def _is_valid_probabilities(probabilities: Any) -> bool:
    if not isinstance(probabilities, dict):
        return False
    values = []
    for outcome in CLASS_ORDER:
        value = probabilities.get(outcome)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            return False
        values.append(value)
    return abs(sum(values) - 1.0) <= PROBABILITY_SUM_TOLERANCE


def load_scored_states(ledger_path: Path) -> list[dict[str, Any]]:
    """Every forecast_id's current state (RECORDED + every later event,
    merged via ``latest_state``) that carries at least one ``SCORED``
    event, sorted by ``forecast_id`` for a fixed, deterministic order.
    Returns an empty list for a missing or empty ledger file -- never an
    error (``read_all``'s own documented behavior)."""

    records = read_all(ledger_path)
    states = []
    for forecast_id in all_entity_ids(records, "forecast_id"):
        state = latest_state(records, "forecast_id", forecast_id)
        if "SCORED" in state.get("_event_types_seen", []):
            state = dict(state)
            state["forecast_id"] = forecast_id
            states.append(state)
    states.sort(key=lambda s: s["forecast_id"])
    return states


def classify_and_filter(states: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Splits ``states`` into ``(included, excluded)`` per this module's
    own docstring rules. ``excluded`` entries carry every typed reason
    that applies (never just the first one found), sorted by
    ``forecast_id``."""

    actual_results_by_fixture: dict[str, set[str]] = {}
    for state in states:
        fixture_id = state.get("fixture_id")
        actual_result = state.get("actual_result")
        if fixture_id is not None and actual_result is not None:
            actual_results_by_fixture.setdefault(fixture_id, set()).add(actual_result)
    conflicting_fixture_ids = {
        fixture_id for fixture_id, results in actual_results_by_fixture.items() if len(results) > 1
    }

    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for state in states:
        reasons: list[str] = []
        if state.get("market_type") != MARKET_TYPE:
            reasons.append(REASON_UNSUPPORTED_MARKET_TYPE)
        if state.get("fixture_id") in conflicting_fixture_ids:
            reasons.append(REASON_CONFLICTING_ACTUAL_RESULT_FOR_FIXTURE)
        # In normal operation a forecast_id can never accumulate more than
        # one SCORED event on disk -- storage.append_terminal_if_new only
        # ever writes the FIRST SCORED event for a forecast_id; a later,
        # identical re-import is DUPLICATE_SKIPPED (never written) and a
        # later, DIFFERENT one is CONFLICT (also never written). This is a
        # defensive check against that invariant somehow being violated
        # (a hand-edited ledger, a future bug elsewhere) -- if it fires,
        # ``latest_state``'s own last-event-wins merge would otherwise
        # silently prefer whichever SCORED event happens to be LAST in
        # file order, never flagging the disagreement. Never guessed
        # which of two scores is "the real one" -- both are excluded.
        if state.get("_event_types_seen", []).count("SCORED") > 1:
            reasons.append(REASON_DUPLICATE_SCORED_EVENTS)

        probabilities = state.get("model_probabilities")
        probabilities_valid = _is_valid_probabilities(probabilities)
        if not probabilities_valid:
            reasons.append(REASON_MALFORMED_PROBABILITIES)

        actual_result = state.get("actual_result")
        actual_result_valid = actual_result in CLASS_ORDER
        if not actual_result_valid:
            reasons.append(REASON_INVALID_ACTUAL_RESULT)

        if probabilities_valid and actual_result_valid:
            recomputed_brier = scoring.multiclass_brier(probabilities, actual_result)
            recomputed_log_loss = scoring.log_loss(probabilities, actual_result)
            stored_brier = state.get("brier_score")
            stored_log_loss = state.get("log_loss")
            score_mismatch = (
                not isinstance(stored_brier, (int, float))
                or isinstance(stored_brier, bool)
                or not isinstance(stored_log_loss, (int, float))
                or isinstance(stored_log_loss, bool)
                or abs(recomputed_brier - stored_brier) > SCORE_RECOMPUTATION_TOLERANCE
                or abs(recomputed_log_loss - stored_log_loss) > SCORE_RECOMPUTATION_TOLERANCE
            )
            if score_mismatch:
                reasons.append(REASON_SCORE_MISMATCH)

        if reasons:
            excluded.append({"forecast_id": state["forecast_id"], "fixture_id": state.get("fixture_id"), "reasons": reasons})
        else:
            included.append(state)

    excluded.sort(key=lambda e: e["forecast_id"])

    # Explicit invariant: every eligible scored record (one entry per
    # forecast_id in ``states``, per ``load_scored_states``) must end up
    # in EXACTLY ONE of ``included``/``excluded`` -- never both, never
    # neither. If this ever fires it is a bug in this function, never a
    # caller input problem (every branch above either appends to
    # ``excluded`` or falls through to ``included``, with no path that
    # skips both or does both).
    if len(included) + len(excluded) != len(states):
        raise AssertionError(
            f"Reconciliation failed: {len(included)} included + {len(excluded)} excluded != "
            f"{len(states)} total scored forecast_ids. This is a bug in classify_and_filter, "
            "never a caller input problem -- every scored forecast_id must end up in exactly "
            "one of the two lists."
        )
    included_ids = {state["forecast_id"] for state in included}
    excluded_ids = {entry["forecast_id"] for entry in excluded}
    if included_ids & excluded_ids:
        raise AssertionError(
            f"Reconciliation failed: forecast_id(s) {sorted(included_ids & excluded_ids)} appear in "
            "BOTH included and excluded -- this is a bug in classify_and_filter."
        )

    return included, excluded


def _predicted_outcome(probabilities: dict[str, float]) -> str:
    return max(CLASS_ORDER, key=lambda outcome: probabilities[outcome])


def _empty_metrics() -> dict[str, Any]:
    return {
        "sample_count": 0,
        "small_sample": True,
        "brier_score": None,
        "log_loss": None,
        "accuracy": None,
        "actual_outcome_distribution": {outcome: 0 for outcome in CLASS_ORDER},
        "predicted_outcome_distribution": {outcome: 0 for outcome in CLASS_ORDER},
    }


def compute_metrics(states: list[dict[str, Any]]) -> dict[str, Any]:
    """Model-quality metrics for one group of already-included,
    already-validated states -- reuses each state's own STORED
    ``brier_score``/``log_loss`` (``classify_and_filter`` has already
    confirmed these match an independent recomputation), never
    recomputes them a second time here."""

    if not states:
        return _empty_metrics()

    n = len(states)
    brier_sum = 0.0
    log_loss_sum = 0.0
    correct = 0
    actual_distribution = {outcome: 0 for outcome in CLASS_ORDER}
    predicted_distribution = {outcome: 0 for outcome in CLASS_ORDER}
    for state in states:
        probabilities = state["model_probabilities"]
        actual_result = state["actual_result"]
        brier_sum += state["brier_score"]
        log_loss_sum += state["log_loss"]
        predicted = _predicted_outcome(probabilities)
        if predicted == actual_result:
            correct += 1
        actual_distribution[actual_result] += 1
        predicted_distribution[predicted] += 1

    return {
        "sample_count": n,
        "small_sample": n < SMALL_SAMPLE_THRESHOLD,
        "brier_score": brier_sum / n,
        "log_loss": log_loss_sum / n,
        "accuracy": correct / n,
        "actual_outcome_distribution": actual_distribution,
        "predicted_outcome_distribution": predicted_distribution,
    }


def _forecast_month(state: dict[str, Any]) -> str | None:
    """``created_at_utc``'s own ``"YYYY-MM"`` prefix -- the month a
    forecast was actually PRODUCED (this report's own chosen cohort for
    "prospective" evaluation), never the month of the fixture's kickoff.
    ``None`` (bucketed as ``UNKNOWN_BUCKET``) for a missing or
    unrecognizable value -- never guessed."""

    created_at_utc = state.get("created_at_utc")
    if isinstance(created_at_utc, str) and len(created_at_utc) >= 7 and created_at_utc[4] == "-":
        return created_at_utc[:7]
    return None


def breakdown_by(states: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str | None]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for state in states:
        key = key_fn(state) or UNKNOWN_BUCKET
        groups.setdefault(key, []).append(state)
    return {key: compute_metrics(group) for key, group in sorted(groups.items())}


def _competition_key(state: dict[str, Any]) -> str | None:
    return state.get("competition_code")


def _artifact_hash_key(state: dict[str, Any]) -> str | None:
    return state.get("artifact_hash")


def build_performance_summary(
    included: list[dict[str, Any]], excluded: list[dict[str, Any]], total_scored_events_on_disk: int
) -> dict[str, Any]:
    excluded_reason_counts: dict[str, int] = {}
    for entry in excluded:
        for reason in entry["reasons"]:
            excluded_reason_counts[reason] = excluded_reason_counts.get(reason, 0) + 1

    settled_timestamps = sorted(state["settled_at_utc"] for state in included if state.get("settled_at_utc"))

    return {
        "schema_version": SCHEMA_VERSION_SUMMARY,
        "as_of_last_settled_at_utc": settled_timestamps[-1] if settled_timestamps else None,
        "note": (
            "Every metric here covers only forecast_ids carrying a SCORED event that survived "
            "classify_and_filter's own validity/consistency checks (see excluded-records.json for "
            "what did not). small_sample is true whenever a bucket's sample_count is below "
            f"{SMALL_SAMPLE_THRESHOLD} -- never treat a small-sample bucket as promotion evidence."
        ),
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "counts": {
            "total_scored_events_on_disk": total_scored_events_on_disk,
            "included": len(included),
            "excluded": len(excluded),
            "excluded_by_reason": dict(sorted(excluded_reason_counts.items())),
            "with_closing_odds": sum(1 for state in included if state.get("closing_odds") is not None),
            "missing_closing_odds": sum(1 for state in included if state.get("closing_odds") is None),
        },
        # Explicit invariant, recomputed here (not merely trusted from
        # classify_and_filter's own assertion) so a caller reading this
        # report alone -- without re-deriving included/excluded -- can
        # still confirm every eligible scored record was accounted for
        # exactly once: total_scored_events_on_disk == included +
        # excluded.
        "reconciles": (len(included) + len(excluded)) == total_scored_events_on_disk,
        "overall": compute_metrics(included),
        "breakdown_by_competition": breakdown_by(included, _competition_key),
        "breakdown_by_artifact_hash": breakdown_by(included, _artifact_hash_key),
        "breakdown_by_forecast_month": breakdown_by(included, _forecast_month),
    }


_BIN_INDEX_ROUNDING_DIGITS = 9  # far below bin-edge spacing (0.1); only cancels float representation noise


def _bin_index(probability: float) -> int:
    """Left-inclusive, 0.1-wide bin index (bin 9 is closed on both ends,
    so ``probability == 1.0`` lands in bin 9, never out of range).

    Rounds to ``_BIN_INDEX_ROUNDING_DIGITS`` decimal places before
    scaling -- a probability that is "morally" exactly at a bin edge
    (e.g. a normalize-then-renormalize computation like
    ``0.7 * 3 / 3``, which lands a hair below the true value purely
    from float representation -- see this module's own test suite for
    the exact reproduction) must land in the bin its true value belongs
    to (index 7, ``[0.7, 0.8)``), never the one below it (index 6)
    because of representation error a caller had no control over.
    Confirmed this actually occurs for realistic probability values,
    not merely a theoretical concern -- this is not a correctness
    nicety."""

    rounded = round(probability, _BIN_INDEX_ROUNDING_DIGITS)
    index = int(rounded * (len(CALIBRATION_BIN_EDGES) - 1))
    return max(0, min(index, len(CALIBRATION_BIN_EDGES) - 2))


def build_calibration_report(included: list[dict[str, Any]]) -> dict[str, Any]:
    per_outcome: dict[str, list[dict[str, Any]]] = {}
    for outcome in CLASS_ORDER:
        bin_count = len(CALIBRATION_BIN_EDGES) - 1
        sample_counts = [0] * bin_count
        probability_sums = [0.0] * bin_count
        positive_counts = [0] * bin_count
        for state in included:
            probability = state["model_probabilities"][outcome]
            index = _bin_index(probability)
            sample_counts[index] += 1
            probability_sums[index] += probability
            if state["actual_result"] == outcome:
                positive_counts[index] += 1

        bins = []
        for index in range(bin_count):
            n = sample_counts[index]
            bins.append(
                {
                    "bin_low": CALIBRATION_BIN_EDGES[index],
                    "bin_high": CALIBRATION_BIN_EDGES[index + 1],
                    "sample_count": n,
                    "small_sample": n < SMALL_SAMPLE_THRESHOLD,
                    "mean_predicted_probability": (probability_sums[index] / n) if n else None,
                    "empirical_frequency": (positive_counts[index] / n) if n else None,
                }
            )
        per_outcome[outcome] = bins

    return {
        "schema_version": SCHEMA_VERSION_CALIBRATION,
        "note": (
            "Standard reliability-diagram calibration, per outcome: for every included forecast, "
            "its own predicted probability for that outcome is binned by CALIBRATION_BIN_EDGES "
            "(fixed, documented, never data-driven), and empirical_frequency is the fraction of "
            "forecasts in that bin whose actual_result WAS that outcome. A well-calibrated model "
            "has mean_predicted_probability roughly equal to empirical_frequency in every bin -- "
            "never treat a small_sample bin's own empirical_frequency as a reliable estimate."
        ),
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "bin_edges": list(CALIBRATION_BIN_EDGES),
        "sample_count": len(included),
        "small_sample": len(included) < SMALL_SAMPLE_THRESHOLD,
        "per_outcome": per_outcome,
    }


def build_closing_line_report(included: list[dict[str, Any]]) -> dict[str, Any]:
    """Compares the model's own forecast against the closing market's
    de-vigged probability, on the SAME subset of included forecasts that
    carry complete closing odds -- kept in its own file, and its own
    ``model``/``closing_market`` sub-blocks, never blended with
    performance-summary.json's own model-quality metrics (which cover
    every included forecast, with or without closing odds). Reuses the
    closing-market de-vigged probabilities the ledger itself already
    computed at scoring time (``forecast_ledger._market_comparison``,
    via the already-reviewed pricing engine) -- never recomputed here.
    """

    model_states = []
    closing_states = []
    for state in included:
        if state.get("closing_odds") is None:
            continue
        comparison = state.get("market_comparison") or {}
        closing_probabilities = comparison.get("closing_market_devig_probabilities")
        if not _is_valid_probabilities(closing_probabilities):
            # A pricing failure (or missing comparison) on this specific
            # forecast -- never fabricate a closing probability to force
            # a comparison this forecast's own ledger record can't back.
            continue
        model_states.append(state)
        closing_states.append(
            {
                "model_probabilities": closing_probabilities,
                "actual_result": state["actual_result"],
                "brier_score": scoring.multiclass_brier(closing_probabilities, state["actual_result"]),
                "log_loss": scoring.log_loss(closing_probabilities, state["actual_result"]),
                "competition_code": state.get("competition_code"),
                "artifact_hash": state.get("artifact_hash"),
                "created_at_utc": state.get("created_at_utc"),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION_CLOSING_LINE,
        "note": (
            "model vs. closing_market are two independently computed metric blocks on the exact "
            "same subset of forecasts (those with complete, valid closing odds) -- never averaged "
            "or blended into one number. A lower closing_market Brier/log-loss than model's own "
            "means the closing line still carried more information than the model captured, "
            "exactly the same comparison this project's README already reports from its backtest, "
            "now computed prospectively from real settled forecasts. "
            f"This comparison is an OBSERVATION over exactly {len(model_states)} forecast(s) -- "
            "see model.small_sample/closing_market.small_sample below. A single-digit or "
            "otherwise small sample_count is never evidence for a promotion decision or a claim "
            "of stable, ongoing performance in either direction; it describes what happened on "
            "this specific batch of forecasts, nothing more, until sample_count clears "
            f"{SMALL_SAMPLE_THRESHOLD}."
        ),
        "small_sample_threshold": SMALL_SAMPLE_THRESHOLD,
        "sample_count_with_complete_closing_odds": len(model_states),
        "model": compute_metrics(model_states),
        "closing_market": compute_metrics(closing_states),
        "breakdown_by_competition": {
            "model": breakdown_by(model_states, _competition_key),
            "closing_market": breakdown_by(closing_states, _competition_key),
        },
        "breakdown_by_artifact_hash": {
            "model": breakdown_by(model_states, _artifact_hash_key),
            "closing_market": breakdown_by(closing_states, _artifact_hash_key),
        },
        "breakdown_by_forecast_month": {
            "model": breakdown_by(model_states, _forecast_month),
            "closing_market": breakdown_by(closing_states, _forecast_month),
        },
    }


def build_excluded_records(excluded: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION_EXCLUDED, "excluded": excluded}


def run_performance_report(ledger_dir: Path) -> dict[str, Any]:
    """Runs the full report pipeline against ``ledger_dir``'s forecast
    ledger. Performs no output-file I/O itself (see
    ``write_performance_reports`` for that) so it can be tested/composed
    directly. Never raises for a missing or empty ledger -- every report
    is well-formed with all-zero counts in that case."""

    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
    scored_states = load_scored_states(ledger_path)
    included, excluded = classify_and_filter(scored_states)

    return {
        "performance_summary": build_performance_summary(included, excluded, len(scored_states)),
        "calibration_report": build_calibration_report(included),
        "closing_line_report": build_closing_line_report(included),
        "excluded_records": build_excluded_records(excluded),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_performance_reports(ledger_dir: Path, output_dir: Path) -> dict[str, Any]:
    result = run_performance_report(ledger_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "performance-summary.json", result["performance_summary"])
    _write_json(output_dir / "calibration-report.json", result["calibration_report"])
    _write_json(output_dir / "closing-line-report.json", result["closing_line_report"])
    _write_json(output_dir / "excluded-records.json", result["excluded_records"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator report-forecast-performance",
        description=__doc__,
    )
    parser.add_argument("--ledger-dir", type=Path, required=True, help="Existing forecast-ledger directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the four report files into")
    args = parser.parse_args(argv)

    result = write_performance_reports(args.ledger_dir, args.output_dir)
    counts = result["performance_summary"]["counts"]
    print(
        f"OK: {counts['included']} included, {counts['excluded']} excluded, "
        f"{counts['with_closing_odds']} with closing odds, {counts['missing_closing_odds']} missing closing odds "
        f"({counts['total_scored_events_on_disk']} SCORED events on disk) -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
