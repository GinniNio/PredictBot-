"""Soccer 1X2 Poisson research forecast: turns a PR #40 research queue plus
a caller-supplied historical-match file into independent H/D/A research
probabilities, compared against the queue's own market prices.

    research-queue-ranked.json + supplied history
    -> resolve competition/team identity (exact-normalized or checked-in alias only)
    -> resolve forecast cutoff (source_captured_at_utc) and reject already-started fixtures
    -> filter eligible history (finished, within the training window, strictly before cutoff)
    -> gate on minimum evidence (competition/home-role/away-role match counts)
    -> fit the independent-Poisson model and score H/D/A
    -> compare against the queue's own market prices (never the reverse)
    -> forecast-report.json / forecast-research-markets.json / forecast-abstentions.json / forecast-evidence-audit.json

This remains research-only, structurally: every produced row carries
``workflow_state: "FORECAST_RESEARCH"``, ``classification_ceiling:
"RESEARCH-MODEL"``, ``recommendation_status: "NOT_AVAILABLE"``,
``stake_status: "NOT_AVAILABLE"``, ``cash_stake: 0``, ``simulated_stake:
0`` -- fixed, non-configurable, and never touched by this module's own
market-comparison math (which only ever *reports* a probability
difference and a point EV, never a recommendation). See
``poisson_model.py`` for why market odds are never read by the model
itself; only this module's comparison step sees them, strictly after
independent probabilities already exist.

Market odds are read from the research queue's own ``pricing.outcomes``
(produced by PR #40's screening workflow, which already de-vigs the
offered prices) -- ``market_implied_probability`` in this module's output
is that de-vigged ``fair_probability``, never the raw (margin-inflated)
``implied_probability``. ``offered_odds`` is the outcome's raw offered
price. Never fabricated, never re-fetched -- these are the exact same
numbers the queue file already carries.

Every input market ends up in exactly one bucket: a forecast research
market, or a typed abstention (``errors.py``) -- never silently dropped.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Soccer1x2PoissonConfig, load_config
from .errors import (
    FORECAST_AWAY_SAMPLE_INSUFFICIENT,
    FORECAST_COMPETITION_SAMPLE_INSUFFICIENT,
    FORECAST_FIXTURE_ALREADY_STARTED,
    FORECAST_FIXTURE_TIME_UNRESOLVED,
    FORECAST_HOME_SAMPLE_INSUFFICIENT,
    FORECAST_INPUT_SCHEMA_INVALID,
    FORECAST_MODEL_EXECUTION_FAILED,
    FORECAST_PROBABILITIES_INVALID,
    HistoryValidationError,
    QueueValidationError,
)
from .history import MatchRecord, load_history
from .identity import TeamAliasBook, resolve_competition, resolve_team
from .poisson_model import (
    ModelExecutionError,
    assert_no_leakage,
    eligible_matches,
    leave_one_match_out_sensitivity,
    score_fixture,
)

SCHEMA_VERSION_REPORT = "pcbf-forecast-soccer-1x2-report.v1"
SCHEMA_VERSION_MARKETS = "pcbf-forecast-soccer-1x2-markets.v1"
SCHEMA_VERSION_ABSTENTIONS = "pcbf-forecast-soccer-1x2-abstentions.v1"
SCHEMA_VERSION_AUDIT = "pcbf-forecast-soccer-1x2-audit.v1"

REQUIRED_ELIGIBILITY = {
    "workflow_state": "RESEARCH_QUEUE",
    "classification_ceiling": "RESEARCH-MODEL",
    "forecast_probability_status": "NOT_COMPUTED",
    "recommendation_status": "NOT_AVAILABLE",
}

PROBABILITY_SUM_TOLERANCE = 1e-6
MINIMUM_SENSITIVITY_RESULTS = 2


def _parse_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sort_key(market: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        market.get("kickoff_utc") or "",
        market.get("competition") or "",
        market.get("home") or "",
        market.get("away") or "",
        market.get("source_fixture_id") or "",
    )


def load_research_queue(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise QueueValidationError(FORECAST_INPUT_SCHEMA_INVALID, f"invalid JSON: {exc}") from None
    if not isinstance(payload, dict) or "markets" not in payload or not isinstance(payload["markets"], list):
        raise QueueValidationError(
            FORECAST_INPUT_SCHEMA_INVALID, "research queue file must be a JSON object with a 'markets' list."
        )
    return payload


def _outcomes_by_name(pricing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["outcome"]: item for item in pricing.get("outcomes", [])}


def _market_comparison(model: dict[str, float], outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for outcome_key, model_key in (("home", "home_win"), ("draw", "draw"), ("away", "away_win")):
        priced = outcomes.get(outcome_key)
        model_probability = model[model_key]
        entry = {"model_probability": model_probability}
        if priced is not None:
            offered_odds = priced["price"]
            market_implied_probability = priced["fair_probability"]
            entry["offered_odds"] = offered_odds
            entry["market_implied_probability"] = market_implied_probability
            entry["model_point_ev"] = model_probability * offered_odds - 1
            entry["probability_difference"] = model_probability - market_implied_probability
        comparison[outcome_key] = entry
    return comparison


@dataclass
class ForecastOutcome:
    market: dict[str, Any] | None
    abstention: dict[str, Any] | None
    audit: dict[str, Any]


def _abstain(reason: str, detail: str, market: dict[str, Any]) -> dict[str, Any]:
    return {
        "reason": reason,
        "detail": detail,
        "source_fixture_id": market.get("source_fixture_id"),
        "source_competition_id": market.get("source_competition_id"),
        "raw": market,
    }


def _process_market(
    market: dict[str, Any],
    history: list[MatchRecord],
    history_competitions: set[str],
    history_teams_by_competition: dict[str, set[str]],
    alias_book: TeamAliasBook,
    config: Soccer1x2PoissonConfig,
) -> ForecastOutcome:
    audit: dict[str, Any] = {
        "source_fixture_id": market.get("source_fixture_id"),
        "competition_resolution": None,
        "home_resolution": None,
        "away_resolution": None,
        "forecast_cutoff_utc": None,
        "sample_counts": None,
    }

    for field, expected in REQUIRED_ELIGIBILITY.items():
        if market.get(field) != expected:
            return ForecastOutcome(
                None,
                _abstain(
                    FORECAST_INPUT_SCHEMA_INVALID,
                    f"market field {field!r} is {market.get(field)!r}, expected {expected!r} to be eligible.",
                    market,
                ),
                audit,
            )

    fixture_kickoff = _parse_utc(market.get("kickoff_utc"))
    if fixture_kickoff is None:
        return ForecastOutcome(
            None,
            _abstain(FORECAST_FIXTURE_TIME_UNRESOLVED, "fixture kickoff_utc is missing or unparseable.", market),
            audit,
        )

    competition_result = resolve_competition(market["competition"], history_competitions)
    audit["competition_resolution"] = {
        "input": market["competition"],
        "resolved": competition_result.resolved_name,
        "method": competition_result.method,
    }
    if competition_result.resolved_name is None:
        return ForecastOutcome(
            None, _abstain(competition_result.reason_code, competition_result.detail, market), audit
        )
    resolved_competition = competition_result.resolved_name
    history_teams = history_teams_by_competition.get(resolved_competition, set())

    home_result = resolve_team(market["home"], resolved_competition, history_teams, alias_book, "home")
    audit["home_resolution"] = {
        "input": market["home"],
        "resolved": home_result.resolved_name,
        "method": home_result.method,
    }
    if home_result.resolved_name is None:
        return ForecastOutcome(None, _abstain(home_result.reason_code, home_result.detail, market), audit)

    away_result = resolve_team(market["away"], resolved_competition, history_teams, alias_book, "away")
    audit["away_resolution"] = {
        "input": market["away"],
        "resolved": away_result.resolved_name,
        "method": away_result.method,
    }
    if away_result.resolved_name is None:
        return ForecastOutcome(None, _abstain(away_result.reason_code, away_result.detail, market), audit)

    resolved_home, resolved_away = home_result.resolved_name, away_result.resolved_name

    forecast_cutoff_utc = market["_forecast_cutoff_utc"]
    audit["forecast_cutoff_utc"] = forecast_cutoff_utc.isoformat().replace("+00:00", "Z")
    if fixture_kickoff <= forecast_cutoff_utc:
        return ForecastOutcome(
            None,
            _abstain(
                FORECAST_FIXTURE_ALREADY_STARTED,
                f"fixture kickoff_utc {market['kickoff_utc']} is at or before the forecast cutoff "
                f"{audit['forecast_cutoff_utc']}.",
                market,
            ),
            audit,
        )

    matches = eligible_matches(history, resolved_competition, forecast_cutoff_utc, config.training_window_days)
    assert_no_leakage(matches, forecast_cutoff_utc)
    home_role_count = sum(1 for m in matches if m.home == resolved_home)
    away_role_count = sum(1 for m in matches if m.away == resolved_away)
    audit["sample_counts"] = {
        "competition_match_count": len(matches),
        "home_role_match_count": home_role_count,
        "away_role_match_count": away_role_count,
    }

    if len(matches) < config.minimum_competition_matches:
        return ForecastOutcome(
            None,
            _abstain(
                FORECAST_COMPETITION_SAMPLE_INSUFFICIENT,
                f"{len(matches)} eligible competition matches, need >= {config.minimum_competition_matches}.",
                market,
            ),
            audit,
        )
    if home_role_count < config.minimum_team_home_matches:
        return ForecastOutcome(
            None,
            _abstain(
                FORECAST_HOME_SAMPLE_INSUFFICIENT,
                f"{home_role_count} eligible home-role matches for {resolved_home!r}, "
                f"need >= {config.minimum_team_home_matches}.",
                market,
            ),
            audit,
        )
    if away_role_count < config.minimum_team_away_matches:
        return ForecastOutcome(
            None,
            _abstain(
                FORECAST_AWAY_SAMPLE_INSUFFICIENT,
                f"{away_role_count} eligible away-role matches for {resolved_away!r}, "
                f"need >= {config.minimum_team_away_matches}.",
                market,
            ),
            audit,
        )

    try:
        point = score_fixture(matches, resolved_home, resolved_away, config.maximum_goals_modelled)
    except ModelExecutionError as exc:
        return ForecastOutcome(None, _abstain(FORECAST_MODEL_EXECUTION_FAILED, exc.reason, market), audit)

    model = {"home_win": point.home_win, "draw": point.draw, "away_win": point.away_win}
    total = sum(model.values())
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in model.values()) or abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
        return ForecastOutcome(
            None,
            _abstain(
                FORECAST_PROBABILITIES_INVALID,
                f"model probabilities {model} do not form a valid unit-sum triple (sum={total}).",
                market,
            ),
            audit,
        )

    sensitivity_results = leave_one_match_out_sensitivity(
        matches, resolved_home, resolved_away, config.maximum_goals_modelled
    )
    if len(sensitivity_results) >= MINIMUM_SENSITIVITY_RESULTS:
        uncertainty = {}
        for key, point_value in model.items():
            values = [point_value] + [getattr(r, key) for r in sensitivity_results]
            uncertainty[key] = {
                "uncertainty_status": "SENSITIVITY_RANGE_AVAILABLE",
                "probability_low": min(values),
                "probability_point": point_value,
                "probability_high": max(values),
                "method": "LEAVE_ONE_MATCH_OUT_SENSITIVITY",
            }
    else:
        uncertainty = {
            key: {"uncertainty_status": "NOT_COMPUTED_INSUFFICIENT_SAMPLE"} for key in model
        }

    outcomes = _outcomes_by_name(market["pricing"])
    comparison = _market_comparison(model, outcomes)

    forecast_row = {
        "workflow_state": "FORECAST_RESEARCH",
        "classification_ceiling": "RESEARCH-MODEL",
        "forecast_probability_status": "COMPUTED_RESEARCH_BASELINE",
        "edge_status": "RESEARCH_ESTIMATE_ONLY",
        "recommendation_status": "NOT_AVAILABLE",
        "stake_status": "NOT_AVAILABLE",
        "cash_stake": 0,
        "simulated_stake": 0,
        "source": market.get("source"),
        "source_capture_session_id": market.get("source_capture_session_id"),
        "source_fixture_id": market.get("source_fixture_id"),
        "source_competition_id": market.get("source_competition_id"),
        "competition": market.get("competition"),
        "resolved_competition": resolved_competition,
        "home": market.get("home"),
        "resolved_home_team": resolved_home,
        "away": market.get("away"),
        "resolved_away_team": resolved_away,
        "kickoff_utc": market.get("kickoff_utc"),
        "forecast_cutoff_utc": audit["forecast_cutoff_utc"],
        "adapter_id": config.adapter_id,
        "model": {
            "expected_home_goals": point.expected_home_goals,
            "expected_away_goals": point.expected_away_goals,
            "home_win": model["home_win"],
            "draw": model["draw"],
            "away_win": model["away_win"],
        },
        "uncertainty": {
            "home_win": uncertainty["home_win"],
            "draw": uncertainty["draw"],
            "away_win": uncertainty["away_win"],
        },
        "market_comparison": comparison,
        "evidence": {
            "competition_match_count": len(matches),
            "home_role_match_count": home_role_count,
            "away_role_match_count": away_role_count,
        },
        "_sort_market": market,
    }
    return ForecastOutcome(forecast_row, None, audit)


def run_forecast(
    queue_path: Path,
    history_path: Path,
    config_path: Path,
    alias_path: Path | None = None,
) -> dict[str, Any]:
    queue = load_research_queue(queue_path)
    config = load_config(config_path)
    history = load_history(history_path)

    if alias_path is not None and alias_path.exists():
        alias_book = TeamAliasBook(json.loads(alias_path.read_text(encoding="utf-8")))
    else:
        alias_book = TeamAliasBook.empty()

    history_competitions = {m.competition for m in history}
    history_teams_by_competition: dict[str, set[str]] = {}
    for match in history:
        bucket = history_teams_by_competition.setdefault(match.competition, set())
        bucket.add(match.home)
        bucket.add(match.away)

    forecast_cutoff_utc = _parse_utc(queue.get("source_captured_at_utc"))
    markets = queue["markets"]

    forecast_rows: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    audit_entries: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}

    for market in markets:
        if forecast_cutoff_utc is None:
            abstention = _abstain(
                FORECAST_FIXTURE_TIME_UNRESOLVED,
                "research queue file has no parseable top-level source_captured_at_utc "
                "to use as the forecast cutoff.",
                market,
            )
            abstentions.append(abstention)
            reason_counts[abstention["reason"]] = reason_counts.get(abstention["reason"], 0) + 1
            audit_entries.append({"source_fixture_id": market.get("source_fixture_id")})
            continue

        market = dict(market)
        market["_forecast_cutoff_utc"] = forecast_cutoff_utc
        outcome = _process_market(
            market, history, history_competitions, history_teams_by_competition, alias_book, config
        )
        audit_entries.append(outcome.audit)
        if outcome.market is not None:
            forecast_rows.append(outcome.market)
        else:
            abstentions.append(outcome.abstention)
            reason_counts[outcome.abstention["reason"]] = reason_counts.get(outcome.abstention["reason"], 0) + 1

    forecast_rows.sort(key=lambda row: _sort_key(row["_sort_market"]))
    for row in forecast_rows:
        del row["_sort_market"]
    abstentions.sort(key=lambda a: _sort_key(a["raw"]))
    audit_entries.sort(key=lambda a: a.get("source_fixture_id") or "")

    total = len(markets)
    forecast_count = len(forecast_rows)
    abstained_count = len(abstentions)
    if forecast_count + abstained_count != total:
        raise AssertionError(
            f"Forecast reconciliation failed: {forecast_count} forecast + {abstained_count} abstained "
            f"!= {total} input markets. This is a bug in this module, never a caller input problem."
        )

    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "adapter_id": config.adapter_id,
        "config": config.to_dict(),
        "source_capture_session_id": queue.get("source_capture_session_id"),
        "source_captured_at_utc": queue.get("source_captured_at_utc"),
        "input_markets": total,
        "forecast_markets": forecast_count,
        "abstained_markets": abstained_count,
        "reconciles": forecast_count + abstained_count == total,
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    markets_doc = {
        "schema_version": SCHEMA_VERSION_MARKETS,
        "adapter_id": config.adapter_id,
        "config": config.to_dict(),
        "markets": forecast_rows,
    }
    abstentions_doc = {
        "schema_version": SCHEMA_VERSION_ABSTENTIONS,
        "adapter_id": config.adapter_id,
        "abstentions": abstentions,
    }
    audit_doc = {
        "schema_version": SCHEMA_VERSION_AUDIT,
        "adapter_id": config.adapter_id,
        "entries": audit_entries,
    }

    return {
        "forecast_report": report,
        "forecast_research_markets": markets_doc,
        "forecast_abstentions": abstentions_doc,
        "forecast_evidence_audit": audit_doc,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_forecast_cli(
    queue_path: Path, history_path: Path, config_path: Path, output_dir: Path, alias_path: Path | None = None
) -> dict[str, Any]:
    result = run_forecast(queue_path, history_path, config_path, alias_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "forecast-report.json", result["forecast_report"])
    _write_json(output_dir / "forecast-research-markets.json", result["forecast_research_markets"])
    _write_json(output_dir / "forecast-abstentions.json", result["forecast_abstentions"])
    _write_json(output_dir / "forecast-evidence-audit.json", result["forecast_evidence_audit"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator forecast-soccer-1x2",
        description=__doc__,
    )
    parser.add_argument("queue", type=Path, help="research-queue-ranked.json produced by screen-research-batch")
    parser.add_argument("--history", type=Path, required=True, help="Supplied historical-match file (.json or .csv)")
    parser.add_argument("--config", type=Path, required=True, help="Soccer 1X2 Poisson adapter config YAML")
    parser.add_argument(
        "--aliases", type=Path, default=None, help="Optional checked-in team-alias JSON file (default: none)"
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write output files into")
    args = parser.parse_args(argv)

    try:
        result = run_forecast_cli(args.queue, args.history, args.config, args.output_dir, args.aliases)
    except (HistoryValidationError, QueueValidationError) as exc:
        print(f"FAILED: {exc.code}: {exc.reason}", file=sys.stderr)
        return 2

    report = result["forecast_report"]
    print(
        f"OK: {report['forecast_markets']} forecast, {report['abstained_markets']} abstained "
        f"({report['input_markets']} input markets) -> {args.output_dir}"
    )
    return 0
