"""Competition/team identity resolution for the Soccer 1X2 Poisson research
adapter.

Resolution order, exactly per the adapter's evidence contract -- never
fuzzy matching:

1. Exact match after normalization (case, surrounding whitespace, repeated
   internal whitespace, safe punctuation differences).
2. An explicit, checked-in alias entry for this exact competition.
3. Typed abstention (``FORECAST_COMPETITION_UNRESOLVED`` /
   ``FORECAST_HOME_TEAM_UNRESOLVED`` / ``FORECAST_AWAY_TEAM_UNRESOLVED`` /
   ``FORECAST_AMBIGUOUS_TEAM_ALIAS``).

"Safe punctuation differences" is deliberately narrow: it removes periods
and apostrophes and collapses hyphens/ampersands to a single space before
whitespace collapsing, so "Nott'm Forest" and "Nottm Forest" normalize
identically, but it never touches letters themselves -- there is no
fuzzy/edit-distance matching anywhere in this module.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .errors import (
    FORECAST_AMBIGUOUS_TEAM_ALIAS,
    FORECAST_AWAY_TEAM_UNRESOLVED,
    FORECAST_COMPETITION_UNRESOLVED,
    FORECAST_HOME_TEAM_UNRESOLVED,
)

_SAFE_PUNCTUATION_RE = re.compile(r"[.'`’]")
_SEPARATOR_RE = re.compile(r"[-&/]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Case, surrounding/repeated whitespace, and safe punctuation only --
    never a fuzzy/edit-distance match."""
    text = unicodedata.normalize("NFKC", name).strip().casefold()
    text = _SAFE_PUNCTUATION_RE.sub("", text)
    text = _SEPARATOR_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


@dataclass(frozen=True)
class ResolutionResult:
    resolved_name: str | None
    method: str | None  # "EXACT_NORMALIZED" or "CHECKED_IN_ALIAS"
    reason_code: str | None
    detail: str | None


class TeamAliasBook:
    """Wraps the checked-in alias mapping
    (``config/soccer_1x2_team_aliases.json``): per competition, a mapping
    of normalized display name -> canonical history team name."""

    def __init__(self, raw: dict[str, Any]) -> None:
        """``aliases`` per competition maps a display name to either a
        single canonical history team name (the normal case) or a list of
        candidate names -- used only when the alias curator genuinely
        cannot tell which of several real teams a display name refers to,
        so resolution correctly abstains as ambiguous rather than
        guessing the first candidate."""
        self._by_competition: dict[str, dict[str, list[str]]] = {}
        for competition, aliases in (raw.get("competitions") or {}).items():
            normalized_competition = normalize_name(competition)
            bucket = self._by_competition.setdefault(normalized_competition, {})
            for display_name, canonical_name in (aliases or {}).items():
                key = normalize_name(display_name)
                candidates = canonical_name if isinstance(canonical_name, list) else [canonical_name]
                bucket.setdefault(key, []).extend(candidates)

    @classmethod
    def empty(cls) -> "TeamAliasBook":
        return cls({"competitions": {}})

    def lookup(self, competition: str, display_name: str) -> list[str]:
        bucket = self._by_competition.get(normalize_name(competition), {})
        return bucket.get(normalize_name(display_name), [])


def resolve_competition(fixture_competition: str, history_competitions: set[str]) -> ResolutionResult:
    normalized = normalize_name(fixture_competition)
    by_normalized = {normalize_name(name): name for name in history_competitions}
    if normalized in by_normalized:
        return ResolutionResult(by_normalized[normalized], "EXACT_NORMALIZED", None, None)
    return ResolutionResult(
        None,
        None,
        FORECAST_COMPETITION_UNRESOLVED,
        f"competition {fixture_competition!r} (normalized {normalized!r}) matches no "
        "competition present in the supplied history, and no alias resolves it.",
    )


def resolve_team(
    fixture_team: str,
    resolved_competition: str,
    history_teams: set[str],
    alias_book: TeamAliasBook,
    role: str,
) -> ResolutionResult:
    """``role`` is ``"home"`` or ``"away"`` -- only used to select the
    correct typed reason code."""
    unresolved_code = FORECAST_HOME_TEAM_UNRESOLVED if role == "home" else FORECAST_AWAY_TEAM_UNRESOLVED

    normalized = normalize_name(fixture_team)
    by_normalized = {normalize_name(name): name for name in history_teams}
    if normalized in by_normalized:
        return ResolutionResult(by_normalized[normalized], "EXACT_NORMALIZED", None, None)

    alias_candidates = alias_book.lookup(resolved_competition, fixture_team)
    resolved_candidates = sorted({c for c in alias_candidates if normalize_name(c) in by_normalized})
    if len(resolved_candidates) == 1:
        return ResolutionResult(by_normalized[normalize_name(resolved_candidates[0])], "CHECKED_IN_ALIAS", None, None)
    if len(resolved_candidates) > 1:
        return ResolutionResult(
            None,
            None,
            FORECAST_AMBIGUOUS_TEAM_ALIAS,
            f"alias for {fixture_team!r} in competition {resolved_competition!r} resolves to "
            f"more than one distinct history team: {resolved_candidates}.",
        )
    return ResolutionResult(
        None,
        None,
        unresolved_code,
        f"{role} team {fixture_team!r} (normalized {normalized!r}) matches no team present in "
        f"the supplied history for competition {resolved_competition!r}, and no alias resolves it.",
    )
