"""Competition/team identity resolution for the Soccer 1X2 Elo v1 adapter.

Resolution order -- never fuzzy matching:

1. Competition: exact match (after name normalization) against the five
   leagues this artifact's real training data actually covers.
2. Team: exact match (after name normalization) against the team names
   present in the live-ratings snapshot for that league, or an explicit
   checked-in alias entry.
3. Typed abstention otherwise.

"Normalization" is deliberately narrow (case, surrounding/repeated
whitespace, safe punctuation only) -- see ``normalize_name``. There is no
edit-distance or fuzzy matching anywhere in this module.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

# The exact five leagues research/soccer_1x2_elo_baseline actually trains
# on (data_pipeline/sources/football_data_sources.yaml's football-data.co.uk
# codes) -- a fixed, known correspondence, not something that needs a
# general alias-lookup mechanism of its own (unlike team names, which do --
# see TeamAliasBook below). "laliga" (no space) is a second, confirmed
# real-evidence key for the SAME already-covered league, never a widening
# of coverage: La Liga's own competition rebranded its public-facing name
# to "LaLiga" (no space) some years ago, and a real Bet9ja capture
# (Espanyol v Elche, 2026-09-18) recorded the
# competition group text as exactly "LaLiga" -- this fixture's own
# forecast abstained with FORECAST_COMPETITION_UNRESOLVED until this
# entry was added. Every other league name here stays exactly as
# football-data.co.uk's own source data spells it.
COMPETITION_NAME_TO_LEAGUE_CODE: dict[str, str] = {
    "premier league": "E0",
    "bundesliga": "D1",
    "la liga": "SP1",
    "laliga": "SP1",
    "serie a": "I1",
    "ligue 1": "F1",
}

_SAFE_PUNCTUATION_RE = re.compile(r"[.'`’]")
_SEPARATOR_RE = re.compile(r"[-&/]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).strip().casefold()
    text = _SAFE_PUNCTUATION_RE.sub("", text)
    text = _SEPARATOR_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


@dataclass(frozen=True)
class ResolutionResult:
    resolved: str | None
    method: str | None  # "EXACT_NORMALIZED" or "CHECKED_IN_ALIAS"
    detail: str | None


def resolve_competition(competition_name: str) -> ResolutionResult:
    normalized = normalize_name(competition_name)
    league_code = COMPETITION_NAME_TO_LEAGUE_CODE.get(normalized)
    if league_code is not None:
        return ResolutionResult(league_code, "EXACT_NORMALIZED", None)
    return ResolutionResult(
        None,
        None,
        f"competition {competition_name!r} (normalized {normalized!r}) is not one of the "
        f"5 leagues this artifact covers: {sorted(COMPETITION_NAME_TO_LEAGUE_CODE)}.",
    )


class TeamAliasBook:
    """Wraps the checked-in alias mapping
    (``config/soccer_1x2_elo_v1_team_aliases.json``): per league code, a
    mapping of normalized display name -> canonical football-data.co.uk
    team name."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._by_league: dict[str, dict[str, str]] = {}
        for league_code, aliases in (raw.get("leagues") or {}).items():
            bucket = self._by_league.setdefault(league_code, {})
            for display_name, canonical_name in (aliases or {}).items():
                bucket[normalize_name(display_name)] = canonical_name

    @classmethod
    def empty(cls) -> "TeamAliasBook":
        return cls({"leagues": {}})

    def lookup(self, league_code: str, display_name: str) -> str | None:
        return self._by_league.get(league_code, {}).get(normalize_name(display_name))


def resolve_team(team_name: str, league_code: str, known_teams: set[str], alias_book: TeamAliasBook) -> ResolutionResult:
    normalized = normalize_name(team_name)
    by_normalized = {normalize_name(name): name for name in known_teams}
    if normalized in by_normalized:
        return ResolutionResult(by_normalized[normalized], "EXACT_NORMALIZED", None)

    aliased = alias_book.lookup(league_code, team_name)
    if aliased is not None and normalize_name(aliased) in by_normalized:
        return ResolutionResult(by_normalized[normalize_name(aliased)], "CHECKED_IN_ALIAS", None)

    return ResolutionResult(
        None,
        None,
        f"team {team_name!r} (normalized {normalized!r}) matches no team present in the "
        f"live-ratings snapshot for league {league_code!r}, and no alias resolves it.",
    )
