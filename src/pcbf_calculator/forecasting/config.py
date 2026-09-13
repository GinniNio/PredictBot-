"""Versioned configuration for the Soccer 1X2 Poisson research adapter.

Deliberately a flat scalar mapping (no nesting, no lists) so it can be
parsed without a YAML dependency, matching this platform's
zero-runtime-dependency constraint (``docs/HOST_CONTRACT.md``). Every
field below is a research setting, not a validated optimal parameter --
see ``poisson_model.py``'s own module docstring. The config is echoed
verbatim into every output file so a reader never has to guess what
produced a given run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "adapter_id",
    "training_window_days",
    "minimum_competition_matches",
    "minimum_team_home_matches",
    "minimum_team_away_matches",
    "maximum_goals_modelled",
)

INTEGER_FIELDS = (
    "training_window_days",
    "minimum_competition_matches",
    "minimum_team_home_matches",
    "minimum_team_away_matches",
    "maximum_goals_modelled",
)


class ConfigError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _parse_flat_yaml(text: str) -> dict[str, Any]:
    """Parse a flat ``key: value`` mapping, one entry per line, no nesting
    and no lists. Comments (``#``) and blank lines are ignored. This is
    intentionally not a general YAML parser -- it exists only to read this
    adapter's own config file shape without a PyYAML dependency."""
    result: dict[str, Any] = {}
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if ":" not in line:
            raise ConfigError(f"Malformed config line (expected 'key: value'): {raw_line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        elif value.lstrip("-").isdigit():
            value = int(value)
        else:
            try:
                value = float(value)
            except ValueError:
                pass
        result[key] = value
    return result


@dataclass(frozen=True)
class Soccer1x2PoissonConfig:
    adapter_id: str
    training_window_days: int
    minimum_competition_matches: int
    minimum_team_home_matches: int
    minimum_team_away_matches: int
    maximum_goals_modelled: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "training_window_days": self.training_window_days,
            "minimum_competition_matches": self.minimum_competition_matches,
            "minimum_team_home_matches": self.minimum_team_home_matches,
            "minimum_team_away_matches": self.minimum_team_away_matches,
            "maximum_goals_modelled": self.maximum_goals_modelled,
        }


def load_config(path: Path) -> Soccer1x2PoissonConfig:
    raw = _parse_flat_yaml(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ConfigError(f"Config is missing required field(s): {missing}")
    for field in INTEGER_FIELDS:
        value = raw[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"Config field {field!r} must be an integer, got {value!r}")
        if value <= 0:
            raise ConfigError(f"Config field {field!r} must be a positive integer, got {value!r}")
    if not isinstance(raw["adapter_id"], str) or not raw["adapter_id"].strip():
        raise ConfigError("Config field 'adapter_id' must be a non-empty string")
    return Soccer1x2PoissonConfig(
        adapter_id=raw["adapter_id"],
        training_window_days=raw["training_window_days"],
        minimum_competition_matches=raw["minimum_competition_matches"],
        minimum_team_home_matches=raw["minimum_team_home_matches"],
        minimum_team_away_matches=raw["minimum_team_away_matches"],
        maximum_goals_modelled=raw["maximum_goals_modelled"],
    )
