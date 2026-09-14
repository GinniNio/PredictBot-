"""The first registered Soccer 1X2 forecasting adapter: wraps the real,
``LIVE_SOURCE_VALIDATED`` Elo + multinomial-logistic-regression research
baseline (``research/soccer_1x2_elo_baseline/``) as an admitted
``SportAdapter``, dispatched through ``adapters/registry.py`` for
``category: "soccer"`` requests.

**This registers the model exactly as it already exists and has already
been evaluated** -- no new modeling methodology, no retraining, no
change to `research/soccer_1x2_elo_baseline/`'s own math. What this
package adds is purely the live-inference path that research module never
needed: turning one caller-supplied fixture (two team names, a
competition, a kickoff time) into a feature vector using a real,
timestamped snapshot of the same Elo ratings that pipeline already
computes, then scoring it with the same ``ModelArtifact.predict_proba``
every backtest number in this repo's README already came from.

Real backtest evidence this adapter's own model already carries (workflow
run recorded in ``data/model_artifact_manifest.json``, ``evidence_class:
LIVE_SOURCE_VALIDATED``, all four frozen-split hashes ``CONFIRMED``):
Brier 0.5896 (locked test) / 0.5930 (out-of-time holdout) -- beats the
naive league-frequency baseline, trails de-vigged opening bookmaker odds.
That comparison is exactly why registering this adapter still changes
nothing about staking: see ``decision/engine.py``'s own module docstring
on why a forecast merely existing is not sufficient for anything above
``RESEARCH-MODEL`` without a model-admission-registry row this repo does
not have.

Identity resolution (``identity.py``), forecast-eligibility gates
(competition coverage, team resolution, artifact cutoff/staleness,
already-started fixtures) and typed abstention (``errors.py``) are new,
adapter-specific code -- the research pipeline itself never needed any of
this, since it only ever scores matches it already has real historical
results for.

Every value this adapter can influence is capped exactly the same way
every other category already is: ``classification_ceiling`` stays
``RESEARCH-MODEL`` because the model-admission registry has no row for
``(soccer_1x2_elo_v1, <this artifact's model_version>)`` -- structural,
not this adapter's own opinion. ``forecast()`` here never returns
anything resembling a recommendation; it returns exactly the same
``ForecastResult`` shape every other adapter (including the no-op stub)
returns.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..base import AdapterInterfaceDeclaration, ForecastResult, SportAdapter
from .config import SoccerEloV1Config, load_config
from .errors import (
    FORECAST_ARTIFACT_AFTER_CUTOFF,
    FORECAST_ARTIFACT_STALE,
    FORECAST_COMPETITION_UNRESOLVED,
    FORECAST_FIXTURE_ALREADY_STARTED,
    FORECAST_INPUT_INCOMPLETE,
    FORECAST_MODEL_EXECUTION_FAILED,
    FORECAST_PROBABILITIES_INVALID,
    FORECAST_TEAM_NOT_IN_ARTIFACT,
    FORECAST_TEAM_UNRESOLVED,
)
from .identity import TeamAliasBook, resolve_competition, resolve_team
from .scoring import ModelArtifact, build_feature_vector

SPORT_ID = "soccer"
ADAPTER_ID = "soccer_1x2_elo_v1"

_DATA_DIR = Path(__file__).resolve().parent / "data"

REQUIRED_FIXTURE_FIELDS = ("home", "away", "competition", "kickoff_utc", "forecast_cutoff_utc")


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


@dataclass(frozen=True)
class _LoadedArtifact:
    model_artifact: ModelArtifact
    temperature: float
    model_version: str
    model_artifact_hash: str
    elo_ratings: dict[tuple[str, str], float]
    season_stage_counts: dict[tuple[str, str, str], int]
    latest_season_by_league: dict[str, str]
    last_processed_match_date_utc: datetime | None


def _load_artifact(data_dir: Path | None = None) -> _LoadedArtifact:
    data_dir = data_dir or _DATA_DIR
    manifest = json.loads((data_dir / "model_artifact_manifest.json").read_text(encoding="utf-8"))
    snapshot = manifest["live_snapshot"]
    artifact_dict = json.loads((data_dir / "model_artifact.json").read_text(encoding="utf-8"))

    elo_ratings: dict[tuple[str, str], float] = {}
    for key, rating in snapshot["elo_ratings"].items():
        league, team = key.split("|", 1)
        elo_ratings[(league, team)] = rating

    season_stage_counts: dict[tuple[str, str, str], int] = {}
    for key, count in snapshot["season_stage_counts"].items():
        league, season, team = key.split("|", 2)
        season_stage_counts[(league, season, team)] = count

    last_processed_raw = snapshot.get("last_processed_match_date_utc")
    return _LoadedArtifact(
        model_artifact=ModelArtifact.from_dict(artifact_dict),
        temperature=manifest["temperature"],
        model_version=manifest["model_version"],
        model_artifact_hash=manifest["artifact_sha256"],
        elo_ratings=elo_ratings,
        season_stage_counts=season_stage_counts,
        latest_season_by_league=snapshot["latest_season_by_league"],
        last_processed_match_date_utc=_parse_utc(last_processed_raw) if last_processed_raw else None,
    )


def _load_alias_book(data_dir: Path) -> TeamAliasBook:
    alias_path = data_dir / "team_aliases.json"
    if alias_path.exists():
        return TeamAliasBook(json.loads(alias_path.read_text(encoding="utf-8")))
    return TeamAliasBook.empty()


def load_known_teams_by_league(data_dir: Path | None = None) -> dict[str, set[str]]:
    """Public accessor: the canonical football-data.co.uk team names this
    artifact's live-ratings snapshot actually knows about, grouped by
    ``league_code`` -- exactly the same ``known_teams`` set ``forecast()``
    resolves incoming fixtures against (see ``identity.resolve_team``).
    Exists so a later, independent step (e.g. settlement-side ingestion
    matching a real-world result back to a forecast) can canonicalize
    names through the IDENTICAL resolution path this adapter itself uses
    at forecast time, rather than a second, separately-maintained notion
    of "known teams" that could quietly drift from this one."""

    artifact = _load_artifact(data_dir)
    result: dict[str, set[str]] = {}
    for league, team in artifact.elo_ratings:
        result.setdefault(league, set()).add(team)
    return result


def load_team_alias_book(data_dir: Path | None = None) -> TeamAliasBook:
    """Public accessor: this adapter's own checked-in team-alias book
    (``data/team_aliases.json``), for the same reuse reason as
    ``load_known_teams_by_league`` above."""

    return _load_alias_book(data_dir or _DATA_DIR)


class SoccerOneXTwoEloV1Adapter(SportAdapter):
    """Registered in ``adapters/registry.py::_ADAPTER_IMPLEMENTATIONS["soccer"]``."""

    def __init__(self, data_dir: Path | None = None, config_path: Path | None = None) -> None:
        data_dir = data_dir or _DATA_DIR
        self._artifact = _load_artifact(data_dir)
        self._config = load_config(config_path)
        self._alias_book = _load_alias_book(data_dir)

    @property
    def declaration(self) -> AdapterInterfaceDeclaration:
        return AdapterInterfaceDeclaration(
            sport_id=SPORT_ID,
            adapter_id=ADAPTER_ID,
            valid_markets=("1x2",),
            settlement_units="match",
            feature_requirements=(
                "home_team_elo_pre_match",
                "away_team_elo_pre_match",
                "league_code",
                "season_stage",
            ),
            data_sources=("football-data.co.uk (historical results, 5 leagues)",),
            uncertainty_method="temperature_calibrated_softmax_no_confidence_interval",
            model_version=self._artifact.model_version,
        )

    def _no_forecast(
        self, reason: str, detail: str | None = None, settlement_identity: dict[str, Any] | None = None
    ) -> ForecastResult:
        return ForecastResult(
            sport_id=SPORT_ID,
            adapter_id=ADAPTER_ID,
            forecast_available=False,
            probabilities=None,
            model_version=self._artifact.model_version,
            uncertainty_method=None,
            no_forecast_reason=reason,
            model_artifact_hash=f"sha256:{self._artifact.model_artifact_hash}",
            settlement_identity=settlement_identity,
        )

    def forecast(self, fixture: dict[str, Any]) -> ForecastResult:
        missing = [f for f in REQUIRED_FIXTURE_FIELDS if not fixture.get(f)]
        if missing:
            return self._no_forecast(FORECAST_INPUT_INCOMPLETE, f"missing field(s): {missing}")

        fixture_kickoff = _parse_utc(fixture["kickoff_utc"])
        forecast_cutoff = _parse_utc(fixture["forecast_cutoff_utc"])
        if fixture_kickoff is None or forecast_cutoff is None:
            return self._no_forecast(FORECAST_INPUT_INCOMPLETE, "kickoff_utc/forecast_cutoff_utc unparseable.")

        if fixture_kickoff <= forecast_cutoff:
            return self._no_forecast(
                FORECAST_FIXTURE_ALREADY_STARTED,
                f"kickoff_utc {fixture['kickoff_utc']} is at or before forecast_cutoff_utc "
                f"{fixture['forecast_cutoff_utc']}.",
            )

        competition_result = resolve_competition(fixture["competition"])
        if competition_result.resolved is None:
            return self._no_forecast(FORECAST_COMPETITION_UNRESOLVED, competition_result.detail)
        league_code = competition_result.resolved

        known_teams = {team for (league, team) in self._artifact.elo_ratings if league == league_code}
        home_result = resolve_team(fixture["home"], league_code, known_teams, self._alias_book)
        if home_result.resolved is None:
            return self._no_forecast(FORECAST_TEAM_UNRESOLVED, home_result.detail)
        away_result = resolve_team(fixture["away"], league_code, known_teams, self._alias_book)
        if away_result.resolved is None:
            return self._no_forecast(FORECAST_TEAM_UNRESOLVED, away_result.detail)
        resolved_home, resolved_away = home_result.resolved, away_result.resolved

        # Real, this-adapter-resolved identity, available from this point
        # on regardless of whether a forecast ultimately follows -- see
        # ``ForecastResult.settlement_identity``'s own docstring for why
        # this is carried through every remaining abstention below, not
        # just the eventual success path. ``scheduled_date`` is the
        # fixture's own kickoff date (never the forecast/capture time) --
        # the same calendar-date join key a settlement source keyed on
        # match date (e.g. football-data.co.uk) would use.
        settlement_identity = {
            "competition_code": league_code,
            "resolved_home_team": resolved_home,
            "resolved_away_team": resolved_away,
            "scheduled_date": fixture_kickoff.date().isoformat(),
        }

        home_elo = self._artifact.elo_ratings.get((league_code, resolved_home))
        away_elo = self._artifact.elo_ratings.get((league_code, resolved_away))
        if home_elo is None or away_elo is None:
            return self._no_forecast(
                FORECAST_TEAM_NOT_IN_ARTIFACT,
                f"resolved team has no Elo rating in the live-ratings snapshot for {league_code!r}.",
                settlement_identity=settlement_identity,
            )

        cutoff = self._artifact.last_processed_match_date_utc
        if cutoff is not None:
            if fixture_kickoff <= cutoff:
                return self._no_forecast(
                    FORECAST_ARTIFACT_AFTER_CUTOFF,
                    f"fixture kickoff_utc {fixture['kickoff_utc']} is at or before this artifact's own "
                    f"last-processed-match date {cutoff.isoformat()}.",
                    settlement_identity=settlement_identity,
                )
            age = forecast_cutoff - cutoff
            if age > timedelta(days=self._config.maximum_artifact_age_days):
                return self._no_forecast(
                    FORECAST_ARTIFACT_STALE,
                    f"artifact age {age.days} days exceeds configured maximum "
                    f"{self._config.maximum_artifact_age_days} days.",
                    settlement_identity=settlement_identity,
                )

        season_code = self._artifact.latest_season_by_league.get(league_code)
        home_stage = self._artifact.season_stage_counts.get((league_code, season_code, resolved_home), 0)
        away_stage = self._artifact.season_stage_counts.get((league_code, season_code, resolved_away), 0)
        season_stage = min(home_stage, away_stage)

        try:
            feature_vector = build_feature_vector(home_elo, away_elo, league_code, season_stage)
            probabilities_hda = self._artifact.model_artifact.predict_proba(
                feature_vector, temperature=self._artifact.temperature
            )
        except Exception as exc:  # noqa: BLE001 -- deliberately broad, converted to a typed abstention
            return self._no_forecast(FORECAST_MODEL_EXECUTION_FAILED, str(exc), settlement_identity=settlement_identity)

        probabilities = {
            "home_win": probabilities_hda.get("H"),
            "draw": probabilities_hda.get("D"),
            "away_win": probabilities_hda.get("A"),
        }
        total = sum(probabilities.values())
        if not all(isinstance(v, (int, float)) and v >= 0 for v in probabilities.values()) or abs(total - 1.0) > 1e-6:
            return self._no_forecast(
                FORECAST_PROBABILITIES_INVALID,
                f"probabilities {probabilities} invalid (sum={total}).",
                settlement_identity=settlement_identity,
            )

        return ForecastResult(
            sport_id=SPORT_ID,
            adapter_id=ADAPTER_ID,
            forecast_available=True,
            probabilities=probabilities,
            model_version=self._artifact.model_version,
            uncertainty_method="temperature_calibrated_softmax_no_confidence_interval",
            no_forecast_reason=None,
            model_artifact_hash=f"sha256:{self._artifact.model_artifact_hash}",
            settlement_identity=settlement_identity,
        )
