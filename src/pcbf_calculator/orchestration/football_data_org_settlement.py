"""football-data.org (api.football-data.org) forecast-ledger settlement.

    forecast ledger's own currently-unsettled forecasts
    -> one football-data.org API request per covered league that actually
       has an unsettled forecast (never a blanket daily pull)
    -> normalized settlement batch
    -> ledgers.orchestration.football_data_settlement.plan_settlement
       (the SAME matching/scoring/idempotency/conflict engine every
       settlement source in this codebase shares -- see below)
    -> atomic settlement
    -> Brier/log-loss scoring

One command::

    FOOTBALL_DATA_ORG_TOKEN=... python -m pcbf_calculator \\
        ingest-football-data-org-results \\
        --ledger-dir ledger_data --output-dir runs/settlement-session-id

**Why this exists.** football-data.co.uk's own CSV remains the preferred
settlement source WHEN a real closing-odds column set is present (see
``football_data_settlement.py``'s own docstring). But its own free-form
per-season CSV must be manually downloaded and uploaded every time a
settlement is needed. football-data.org's REST API (a genuinely
different provider, despite the similar name) covers the same five
leagues this codebase's adapter forecasts (Premier League, Bundesliga,
La Liga, Serie A, Ligue 1 -- confirmed on its published free-tier
coverage) and can be queried directly by this module, removing that
manual step for RESULT scoring specifically. It does NOT replace
football-data.co.uk for closing-odds evidence -- see "Closing odds"
below.

**API token -- never stored, never logged.** Read ONLY from the
``FOOTBALL_DATA_ORG_TOKEN`` environment variable (``TOKEN_ENV_VAR``
below) -- never accepted as a CLI argument (which a shell history or
process list could leak), never written to any ledger, report, hash
manifest, or log line this module produces. A missing token is a typed,
fail-closed error (``MissingApiTokenError``) raised BEFORE any network
call or ledger read -- this module never silently skips settlement or
falls back to a cached/guessed result when the token is absent.

**Fetches only what's actually needed.** Before making any API request,
this module scans the forecast ledger for its own currently UNSETTLED,
SCORABLE forecasts (``model_probabilities`` present, no ``SCORED`` event
yet, a complete settlement identity, and a ``kickoff_utc`` already in the
past -- see ``find_unsettled_forecasts``). It queries football-data.org
ONLY for the covered leagues that actually have at least one such
forecast, over the narrowest ``[dateFrom, dateTo]`` window that covers
them, with ``status=FINISHED`` -- never a blanket "pull today's whole
football-data.org calendar" request, and never more than one request per
covered league per run (at most 5, one per
``COMPETITION_CODE_TO_FOOTBALL_DATA_ORG`` entry). Between requests this
module sleeps (``REQUEST_INTERVAL_SECONDS``, injectable as ``sleep_fn``
for tests) to stay well inside the free plan's published 10-requests/
minute limit even in the worst case of all 5 leagues needing a fetch in
the same run.

**Reuses the existing settlement engine, never a second one.** Exactly
like ``bet9ja_results_settlement.py``, this module imports and reuses
``football_data_settlement.plan_settlement`` VERBATIM -- the identical
matching identity (``competition_code``, ``resolved_home_team``,
``resolved_away_team``, ``scheduled_date``), the identical duplicate/
conflict rules (including the missing-closing-odds-is-never-a-conflict
fix), and the identical atomic preflight-then-commit contract. Only this
module's own row normalization (an API JSON response instead of a CSV or
a browser-capture envelope) differs.

**Team/competition identity -- the SAME resolution, never a second
one.** football-data.org's own team names (e.g. a full legal name with a
club suffix) are resolved through the IDENTICAL
``adapters.soccer_1x2_elo_v1.identity.resolve_team``/``load_known_teams_by_league``/
``load_team_alias_book`` every other settlement source in this codebase
already uses -- an API team name with no confirmed alias-book entry is
quarantined (``REASON_HOME_TEAM_UNRESOLVED``/``REASON_AWAY_TEAM_UNRESOLVED``),
never guessed. ``COMPETITION_CODE_TO_FOOTBALL_DATA_ORG`` maps this
codebase's own five league codes to football-data.org's own competition
codes (``PL``/``BL1``/``PD``/``SA``/``FL1``) -- a fixed, confirmed
correspondence from football-data.org's own published coverage, not a
guess.

**Closing odds -- always unavailable through this source.** football-
data.org's free tier does not supply closing (or any) betting odds.
``closing_odds``/``closing_odds_source`` are always ``None`` on every
row this module produces -- exactly like ``bet9ja_results_settlement.py``,
this is reported as ``missing_closing_odds`` by the existing performance
report, never fabricated or backfilled from another source. Closing-line
analysis stays available only for a fixture football-data.co.uk's own
CSV path has separately settled with real odds.

**API-Football fallback -- deliberately NOT built here.** Querying a
SECOND provider only for a result football-data.org's own response
didn't cover is a genuinely useful future addition, but it needs its own
confirmed API shape, competition-code mapping, and rate-limit handling --
none of which is speculatively built ahead of that real requirement,
per this codebase's own "no speculative built-ahead-of-need" discipline
(see ``football_data_settlement.py``'s own deliberately-deferred
closing-odds-enrichment note for the identical reasoning). A future PR
can add it once that provider's own real response shape is confirmed.

**Fail-closed boundaries.** A match whose API-reported ``status`` is
anything other than ``FINISHED`` (even though the request itself already
filtered on that status server-side -- this module never trusts a
server-side filter alone) is never scored. A response this module cannot
parse as valid JSON, or an HTTP-level failure (timeout, non-2xx, a
connection error), aborts that league's contribution to this run with a
typed reason -- it never silently proceeds with zero results for that
league mistaken for "nothing to settle." Forecast ledger only -- no
write of any kind to ``ledgers/betting_ledger.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ..adapters.soccer_1x2_elo_v1.adapter import (  # noqa: E402
    load_known_teams_by_league,
    load_team_alias_book,
)
from ..adapters.soccer_1x2_elo_v1.identity import resolve_team  # noqa: E402
from .football_data_settlement import plan_settlement  # noqa: E402


def _ensure_ledgers_importable() -> None:
    """Same fallback every other orchestration settlement module in this
    package uses for itself -- see any of their own copies for the full
    rationale."""

    try:
        import ledgers  # noqa: F401

        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "ledgers" / "__init__.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_ledgers_importable()

from ledgers import forecast_ledger  # noqa: E402
from ledgers.locking import LedgerLockTimeoutError, exclusive_ledger_lock  # noqa: E402
from ledgers.storage import APPENDED, DUPLICATE_SKIPPED, all_entity_ids, latest_state, read_all  # noqa: E402

SCHEMA_VERSION_REPORT = "pcbf-football-data-org-settlement-report.v1"
SCHEMA_VERSION_SETTLED = "pcbf-football-data-org-settled-forecasts.v1"
SCHEMA_VERSION_UNMATCHED = "pcbf-football-data-org-unmatched-results.v1"
SCHEMA_VERSION_CONFLICTS = "pcbf-football-data-org-settlement-conflicts.v1"

MARKET_TYPE = "1X2"

TOKEN_ENV_VAR = "FOOTBALL_DATA_ORG_TOKEN"
API_BASE_URL = "https://api.football-data.org/v4"
STATUS_FINISHED = "FINISHED"

# This codebase's own five league codes -> football-data.org's own
# competition codes -- confirmed against that provider's published free-
# tier coverage (https://www.football-data.org/coverage), a fixed
# correspondence, never guessed.
COMPETITION_CODE_TO_FOOTBALL_DATA_ORG: dict[str, str] = {
    "E0": "PL",
    "D1": "BL1",
    "SP1": "PD",
    "I1": "SA",
    "F1": "FL1",
}

# The free plan's own published limit is 10 requests/minute; sleeping
# this long between requests keeps this module well inside that limit
# even in the worst case of every one of the 5 covered leagues needing a
# fetch in the same run (5 requests, ~33s total) -- never a burst.
REQUEST_INTERVAL_SECONDS = 6.5
REQUEST_TIMEOUT_SECONDS = 20

REASON_HOME_TEAM_UNRESOLVED = "SETTLE_HOME_TEAM_UNRESOLVED"
REASON_AWAY_TEAM_UNRESOLVED = "SETTLE_AWAY_TEAM_UNRESOLVED"
REASON_MATCH_NOT_FINISHED = "SETTLE_MATCH_NOT_FINISHED"
REASON_MISSING_SCORE = "SETTLE_MISSING_SCORE"
REASON_CONFLICTING_SOURCE_ROW = "SETTLE_CONFLICTING_SOURCE_ROW"


class MissingApiTokenError(RuntimeError):
    """Raised when ``FOOTBALL_DATA_ORG_TOKEN`` is not set -- before any
    network call or ledger read. The message never echoes the token
    (there is none to echo in this case) and this module never reads the
    token from any other source (a CLI argument, a config file)."""


class ApiRequestError(RuntimeError):
    """Raised for any HTTP-level failure (timeout, non-2xx status,
    connection error) or an unparseable JSON response fetching one
    league's matches. Carries the football-data.org competition code and
    a typed, non-sensitive detail -- never the auth token."""

    def __init__(self, football_data_org_code: str, detail: str) -> None:
        self.football_data_org_code = football_data_org_code
        self.detail = detail
        super().__init__(f"football-data.org request failed for competition {football_data_org_code!r}: {detail}")


def _default_http_get(url: str, token: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> bytes:
    """The one real network call this module ever makes -- stdlib
    ``urllib.request`` only (matches this codebase's zero-new-dependency
    convention; see ``data_pipeline/download.py``'s own identical
    choice). Injectable (``http_get`` parameter throughout this module)
    so every other function here is testable without a real token or
    network access."""

    request = urllib.request.Request(url, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def find_unsettled_forecasts(ledger_path: Path, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """Every forecast in the ledger at ``ledger_path`` that is SCORABLE
    (``model_probabilities`` present), carries a complete settlement
    identity, has NOT already been scored, and whose ``kickoff_utc`` is
    already in the past (querying an API for a fixture that hasn't even
    kicked off yet would only ever come back with nothing, wasting a
    request). Returns one dict per unsettled forecast_id with
    ``forecast_id``/``competition_code``/``resolved_home_team``/
    ``resolved_away_team``/``scheduled_date``/``kickoff_utc``."""

    now = now_utc or datetime.now(timezone.utc)
    records = read_all(ledger_path)
    unsettled: list[dict[str, Any]] = []
    for forecast_id in all_entity_ids(records, "forecast_id"):
        state = latest_state(records, "forecast_id", forecast_id)
        if state.get("market_type") != MARKET_TYPE:
            continue
        if state.get("model_probabilities") is None:
            continue
        if "SCORED" in state.get("_event_types_seen", []):
            continue
        identity = (
            state.get("competition_code"),
            state.get("resolved_home_team"),
            state.get("resolved_away_team"),
            state.get("scheduled_date"),
        )
        if None in identity:
            continue
        kickoff_utc = state.get("kickoff_utc")
        if not kickoff_utc:
            continue
        try:
            kickoff_dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        except ValueError:
            continue
        if kickoff_dt >= now:
            continue
        unsettled.append(
            {
                "forecast_id": forecast_id,
                "competition_code": identity[0],
                "resolved_home_team": identity[1],
                "resolved_away_team": identity[2],
                "scheduled_date": identity[3],
                "kickoff_utc": kickoff_utc,
            }
        )
    return unsettled


def _actual_result_from_score(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if away_goals > home_goals:
        return "A"
    return "D"


def fetch_finished_matches(
    football_data_org_code: str,
    date_from: str,
    date_to: str,
    token: str,
    http_get: Callable[[str, str], bytes] = _default_http_get,
) -> list[dict[str, Any]]:
    """One API request for one football-data.org competition code's
    ``FINISHED`` matches in ``[date_from, date_to]`` (both ``YYYY-MM-DD``,
    inclusive). Returns the raw ``matches`` list from the response --
    normalization happens separately (``build_football_data_org_batch``)
    so this function stays a thin, mockable I/O boundary. Raises
    ``ApiRequestError`` (never the raw urllib/json exception) for any
    HTTP-level failure or unparseable response."""

    url = f"{API_BASE_URL}/competitions/{football_data_org_code}/matches?dateFrom={date_from}&dateTo={date_to}&status={STATUS_FINISHED}"
    try:
        body = http_get(url, token)
    except urllib.error.HTTPError as exc:
        raise ApiRequestError(football_data_org_code, f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ApiRequestError(football_data_org_code, f"connection error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ApiRequestError(football_data_org_code, f"timed out: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ApiRequestError(football_data_org_code, f"response was not valid JSON: {exc}") from exc

    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise ApiRequestError(football_data_org_code, "response had no usable 'matches' list")
    return matches


def build_football_data_org_batch(
    matches_by_league_code: dict[str, list[dict[str, Any]]],
    known_teams_by_league: dict[str, set[str]],
    alias_book: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Normalizes every raw football-data.org match (grouped by THIS
    codebase's own league code, e.g. ``"I1"`` -- never football-data.org's
    own ``"SA"``) into ``football_data_settlement.plan_settlement``'s own
    expected row shape. Mirrors
    ``bet9ja_results_settlement.build_bet9ja_results_batch``'s own
    contract exactly: ``source_rows_total == len(rejected_rows) +
    len(normalized_rows) + duplicate_source_rows_collapsed``."""

    rejected: list[dict[str, Any]] = []
    candidates: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    source_rows_total = 0

    for league_code, matches in matches_by_league_code.items():
        for row_index, match in enumerate(matches):
            source_rows_total += 1
            base = {
                "source_match_id": match.get("id"),
                "source_row_index": row_index,
                "source_file": f"football-data.org:{COMPETITION_CODE_TO_FOOTBALL_DATA_ORG.get(league_code, league_code)}",
            }

            status = match.get("status")
            if status != STATUS_FINISHED:
                rejected.append(
                    {**base, "reason": REASON_MATCH_NOT_FINISHED, "detail": f"status was {status!r}, not FINISHED"}
                )
                continue

            score = match.get("score") or {}
            full_time = score.get("fullTime") or {}
            home_goals, away_goals = full_time.get("home"), full_time.get("away")
            if not isinstance(home_goals, int) or not isinstance(away_goals, int):
                rejected.append(
                    {**base, "reason": REASON_MISSING_SCORE, "detail": f"fullTime score was {full_time!r}"}
                )
                continue
            actual_result = _actual_result_from_score(home_goals, away_goals)

            home_raw = ((match.get("homeTeam") or {}).get("name") or "").strip()
            away_raw = ((match.get("awayTeam") or {}).get("name") or "").strip()
            known_teams = known_teams_by_league.get(league_code, set())
            home_result = resolve_team(home_raw, league_code, known_teams, alias_book)
            if home_result.resolved is None:
                rejected.append({**base, "reason": REASON_HOME_TEAM_UNRESOLVED, "detail": home_result.detail})
                continue
            away_result = resolve_team(away_raw, league_code, known_teams, alias_book)
            if away_result.resolved is None:
                rejected.append({**base, "reason": REASON_AWAY_TEAM_UNRESOLVED, "detail": away_result.detail})
                continue

            utc_date = match.get("utcDate") or ""
            scheduled_date = utc_date.split("T")[0] if "T" in utc_date else utc_date

            key = (league_code, home_result.resolved, away_result.resolved, scheduled_date)
            candidates.setdefault(key, []).append(
                {
                    **base,
                    "competition_code": league_code,
                    "home_team_raw": home_raw,
                    "away_team_raw": away_raw,
                    "resolved_home_team": home_result.resolved,
                    "resolved_away_team": away_result.resolved,
                    "scheduled_date": scheduled_date,
                    "actual_result": actual_result,
                    "closing_odds": None,
                    "closing_odds_source": None,
                }
            )

    normalized: list[dict[str, Any]] = []
    duplicate_source_rows_collapsed = 0
    for candidate_rows in candidates.values():
        distinct_results = {r["actual_result"] for r in candidate_rows}
        if len(distinct_results) > 1:
            for row in candidate_rows:
                rejected.append(
                    {
                        **row,
                        "reason": REASON_CONFLICTING_SOURCE_ROW,
                        "detail": (
                            f"same fixture reported with actual_result values {sorted(distinct_results)} "
                            "across source rows in this batch"
                        ),
                    }
                )
            continue
        normalized.append(candidate_rows[0])
        duplicate_source_rows_collapsed += len(candidate_rows) - 1

    for index, row in enumerate(normalized):
        row["row_index"] = index

    parse_counts = {
        "source_rows_total": source_rows_total,
        "rejected_at_parse": len(rejected),
        "duplicate_source_rows_collapsed": duplicate_source_rows_collapsed,
    }
    return normalized, rejected, parse_counts


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ingest_football_data_org_results(
    ledger_dir: Path,
    token: str | None = None,
    http_get: Callable[[str, str], bytes] = _default_http_get,
    now_utc: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Full pipeline: find this ledger's own unsettled forecasts, query
    football-data.org ONLY for the covered leagues among them, normalize,
    and settle via the shared ``plan_settlement`` engine.

    ``token`` defaults to ``os.environ[TOKEN_ENV_VAR]`` when not passed
    explicitly (tests pass one directly so this function never touches
    the real environment). Raises ``MissingApiTokenError`` immediately --
    before any ledger read or network call -- when no token is available
    from either source."""

    if token is None:
        token = os.environ.get(TOKEN_ENV_VAR)
    if not token:
        raise MissingApiTokenError(
            f"environment variable {TOKEN_ENV_VAR} is not set -- see this module's own docstring "
            "for how to configure it. Refusing to proceed without one."
        )

    ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
    unsettled = find_unsettled_forecasts(ledger_path, now_utc=now_utc)

    by_league: dict[str, list[dict[str, Any]]] = {}
    for item in unsettled:
        code = item["competition_code"]
        if code not in COMPETITION_CODE_TO_FOOTBALL_DATA_ORG:
            continue  # not one of the 5 covered leagues -- never queried, never guessed
        by_league.setdefault(code, []).append(item)

    known_teams_by_league = load_known_teams_by_league()
    alias_book = load_team_alias_book()

    matches_by_league_code: dict[str, list[dict[str, Any]]] = {}
    api_errors: list[dict[str, Any]] = []
    leagues_queried = sorted(by_league)
    for i, league_code in enumerate(leagues_queried):
        if i > 0:
            sleep_fn(REQUEST_INTERVAL_SECONDS)
        dates = [item["scheduled_date"] for item in by_league[league_code]]
        football_data_org_code = COMPETITION_CODE_TO_FOOTBALL_DATA_ORG[league_code]
        try:
            matches_by_league_code[league_code] = fetch_finished_matches(
                football_data_org_code, min(dates), max(dates), token, http_get=http_get
            )
        except ApiRequestError as exc:
            api_errors.append({"competition_code": league_code, "football_data_org_code": football_data_org_code, "detail": exc.detail})

    normalized_rows, rejected_at_parse, parse_counts = build_football_data_org_batch(
        matches_by_league_code, known_teams_by_league, alias_book
    )

    ledger_dir.mkdir(parents=True, exist_ok=True)
    with exclusive_ledger_lock(ledger_path):
        plan = plan_settlement(ledger_path, normalized_rows)

        scored: list[dict[str, Any]] = []
        if not plan["conflicts"]:
            for item in plan["to_score"]:
                result = forecast_ledger.score_and_append(
                    ledger_path,
                    item["forecast_id"],
                    item["actual_result"],
                    closing_odds=item["closing_odds"],
                    closing_odds_source=item["closing_odds_source"],
                )
                if result.status == APPENDED:
                    scored.append(
                        {
                            **item,
                            "brier_score": result.record["payload"]["brier_score"],
                            "log_loss": result.record["payload"]["log_loss"],
                        }
                    )
                elif result.status == DUPLICATE_SKIPPED:  # pragma: no cover -- preflight already ruled this out
                    plan["duplicate_skipped"].append(item)
                else:  # pragma: no cover -- preflight already ruled this out
                    raise AssertionError(
                        f"Policy violation: preflight found no conflict for forecast_id={item['forecast_id']!r}, "
                        "but the real append returned CONFLICT. This is a bug in this module's preflight logic."
                    )

        total_ledger_records = len(read_all(ledger_path))

    matched_row_indices = {
        e["row_index"] for e in (plan["to_score"] + plan["duplicate_skipped"] + plan["conflicts"] + plan["not_scorable"])
    }
    unmatched_row_indices = {e["row_index"] for e in plan["unmatched"]}
    rows_reconciled = (
        len(matched_row_indices | unmatched_row_indices) == len(normalized_rows)
        and matched_row_indices.isdisjoint(unmatched_row_indices)
    )

    status = "CONFLICT" if plan["conflicts"] else "API_ERROR" if api_errors else "OK"
    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "status": status,
        "leagues_queried": leagues_queried,
        "api_errors": api_errors,
        "note": (
            "Reuses football_data_settlement.plan_settlement verbatim (the same matching/scoring/"
            "idempotency engine every settlement source in this codebase shares) -- only this "
            "module's own row normalization (a football-data.org API response) differs. "
            "closing_odds is always null here -- this source supplies no odds. Only leagues with "
            "at least one currently-unsettled forecast were queried. Preflights the whole batch "
            "before any ledger mutation; a CONFLICT status means nothing was written to the "
            "ledger this run. An API_ERROR status means one or more leagues could not be queried "
            "this run (see api_errors[]) -- any OTHER league that WAS queried successfully is "
            "still fully settled."
        ),
        "counts": {
            **parse_counts,
            "unsettled_forecasts_found": len(unsettled),
            "rows_matchable": len(normalized_rows),
            "rows_unmatched_no_forecast": len(plan["unmatched"]),
            "forecast_matches_not_scorable": len(plan["not_scorable"]),
            "forecast_matches_scored": len(scored),
            "forecast_matches_duplicate_skipped": len(plan["duplicate_skipped"]),
            "forecast_matches_conflicted": len(plan["conflicts"]),
            "total_ledger_records": total_ledger_records,
        },
        "rows_reconciled": rows_reconciled,
    }

    settled_forecasts = {"schema_version": SCHEMA_VERSION_SETTLED, "settled": scored}
    unmatched_results = {
        "schema_version": SCHEMA_VERSION_UNMATCHED,
        "unmatched": rejected_at_parse + plan["unmatched"] + plan["not_scorable"],
    }
    settlement_conflicts = {"schema_version": SCHEMA_VERSION_CONFLICTS, "conflicts": plan["conflicts"]}

    return {
        "settlement_report": report,
        "settled_forecasts": settled_forecasts,
        "unmatched_results": unmatched_results,
        "settlement_conflicts": settlement_conflicts,
    }


def run_settlement_session(ledger_dir: Path, output_dir: Path, token: str | None = None) -> dict[str, Any]:
    result = ingest_football_data_org_results(ledger_dir, token=token)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "settlement-report.json", result["settlement_report"])
    _write_json(output_dir / "settled-forecasts.json", result["settled_forecasts"])
    _write_json(output_dir / "unmatched-results.json", result["unmatched_results"])
    _write_json(output_dir / "settlement-conflicts.json", result["settlement_conflicts"])
    return result


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator ingest-football-data-org-results",
        description=__doc__,
    )
    parser.add_argument("--ledger-dir", type=Path, required=True, help="Existing (or new) forecast-ledger directory")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the four output files into")
    args = parser.parse_args(argv)

    try:
        result = run_settlement_session(args.ledger_dir, args.output_dir)
    except MissingApiTokenError as exc:
        print(f"MISSING_TOKEN: {exc}")
        return 2
    except LedgerLockTimeoutError as exc:
        print(f"LOCKED: {exc}")
        return 2

    counts = result["settlement_report"]["counts"]
    status = result["settlement_report"]["status"]
    print(
        f"{status}: {counts['unsettled_forecasts_found']} unsettled forecast(s) found, "
        f"{len(result['settlement_report']['leagues_queried'])} league(s) queried, "
        f"{counts['forecast_matches_scored']} scored, "
        f"{counts['forecast_matches_duplicate_skipped']} duplicate-skipped, "
        f"{counts['forecast_matches_conflicted']} conflicted, "
        f"{counts['rows_unmatched_no_forecast']} unmatched, "
        f"{len(result['settlement_report']['api_errors'])} API error(s) "
        f"-> {args.output_dir}"
    )
    return 2 if status in ("CONFLICT", "API_ERROR") else 0


if __name__ == "__main__":
    raise SystemExit(main())
