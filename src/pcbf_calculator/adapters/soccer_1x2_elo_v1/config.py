"""Versioned configuration for the Soccer 1X2 Elo v1 adapter.

Flat scalar mapping, parsed without a YAML dependency (matches this
platform's zero-runtime-dependency constraint). Every field is a research
setting, not a validated optimal parameter -- see this package's own
module docstring for what still needs real promotion review before any
of these become more than "the adapter's own conservative defaults."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("adapter_id", "maximum_artifact_age_days")


class ConfigError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _parse_flat_yaml(text: str) -> dict[str, Any]:
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
class SoccerEloV1Config:
    adapter_id: str
    maximum_artifact_age_days: int

    def to_dict(self) -> dict[str, Any]:
        return {"adapter_id": self.adapter_id, "maximum_artifact_age_days": self.maximum_artifact_age_days}


# Ships inside the installed package (see pyproject.toml's package-data
# entry for this adapter) -- unlike a caller-supplied CLI --config flag,
# this adapter is dispatched automatically through the existing host
# contract (no per-request config path in the request schema), so its
# own config/artifact/alias files must be discoverable with zero extra
# configuration from any installation of this package.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "data" / "config.yaml"


def load_config(path: Path | None = None) -> SoccerEloV1Config:
    config_path = path or _DEFAULT_CONFIG_PATH
    raw = _parse_flat_yaml(config_path.read_text(encoding="utf-8"))
    missing = [f for f in REQUIRED_FIELDS if f not in raw]
    if missing:
        raise ConfigError(f"Config is missing required field(s): {missing}")
    if not isinstance(raw["maximum_artifact_age_days"], int) or raw["maximum_artifact_age_days"] <= 0:
        raise ConfigError("Config field 'maximum_artifact_age_days' must be a positive integer.")
    if not isinstance(raw["adapter_id"], str) or not raw["adapter_id"].strip():
        raise ConfigError("Config field 'adapter_id' must be a non-empty string.")
    return SoccerEloV1Config(
        adapter_id=raw["adapter_id"],
        maximum_artifact_age_days=raw["maximum_artifact_age_days"],
    )
