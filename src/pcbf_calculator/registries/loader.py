"""Load, index, and cross-validate the four Release-A registries.

Canonical registry schema (documented in full in
``docs/MULTI_SPORT_ARCHITECTURE.md``):

- ``sports-registry.yaml``: sport-level facts (id, display name, category
  kind, underlying sport, event type, competitor structure).
- ``market-types-registry.yaml``: market-type facts (supported price
  structures, typical markets, settlement requirements).
- ``adapter-registry.yaml``: adapter facts (adapter status, unsupported
  reason, classification ceiling while that status holds, feature
  requirements, uncertainty method).
- ``data-sources-registry.yaml``: data-source facts (runtime status,
  unsupported reason, required data sources).

Every file shares the same ``id`` key so rows join into one category record.
The actual YAML lives under ``registries/data/`` inside this package (so it
ships inside the wheel); ``registries/*.yaml`` at the repository root are
symlinks to the same files and are the canonical place a human edits them.
"""

from __future__ import annotations

import importlib.resources
from typing import Any

from .yamlmini import load_registry

ALLOWED_STATUSES = {
    "PRICING_SUPPORTED",
    "FORECAST_ADAPTER_AVAILABLE",
    "RESEARCH_ONLY",
    "UNSUPPORTED_INPUT",
    "NOT_IMPLEMENTED",
}
# Both runtime_status (data-sources-registry) and adapter_status
# (adapter-registry) are drawn from the same five-value enum.
ALLOWED_RUNTIME_STATUSES = ALLOWED_STATUSES
ALLOWED_ADAPTER_STATUSES = ALLOWED_STATUSES

ALLOWED_CLASSIFICATION_CEILINGS = {"RESEARCH-MODEL", "PAPER", "CASH"}

REQUIRED_CATEGORY_IDS = frozenset(
    {
        "soccer",
        "players_soccer",
        "specials_soccer",
        "specials_combo",
        "antepost_soccer",
        "zoom_soccer",
        "players_zoom_soccer",
        "tennis",
        "zoom_tennis",
        "basketball",
        "volleyball",
        "american_football",
        "players_american_football",
        "baseball",
        "handball",
        "rugby",
        "motor_sports",
        "ice_hockey",
        "cycling",
        "alpine",
        "biathlon",
        "cross_country",
        "badminton",
        "boxing",
        "cricket",
        "darts",
        "floorball",
        "futsal",
        "mma",
        "snooker",
        "table_tennis",
        "outrights",
    }
)

_FILES = {
    "sports": "sports-registry.yaml",
    "market_types": "market-types-registry.yaml",
    "adapters": "adapter-registry.yaml",
    "data_sources": "data-sources-registry.yaml",
}

_REQUIRED_FIELDS = {
    "sports": ("id", "display_name", "kind", "underlying", "event_type", "competitor"),
    "market_types": ("id", "structures", "markets", "settlement"),
    "adapters": (
        "id",
        "adapter_status",
        "adapter_unsupported_reason",
        "classification_ceiling",
        "feature_requirements",
        "uncertainty_method",
    ),
    "data_sources": ("id", "runtime_status", "runtime_unsupported_reason", "data_sources"),
}


class RegistryValidationError(ValueError):
    """Raised with the full list of registry consistency problems found."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def _read_bundled(filename: str) -> str:
    resource = importlib.resources.files("pcbf_calculator.registries.data").joinpath(filename)
    return resource.read_text(encoding="utf-8")


def _index_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in rows if "id" in row}


def load_all_registries() -> dict[str, dict[str, dict[str, Any]]]:
    """Return each registry's rows indexed by category id.

    Result shape: ``{"sports": {id: row, ...}, "market_types": {...},
    "adapters": {...}, "data_sources": {...}}``.
    """
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for key, filename in _FILES.items():
        parsed = load_registry(_read_bundled(filename))
        result[key] = _index_by_id(parsed["categories"])
    return result


def merged_categories() -> dict[str, dict[str, Any]]:
    """Join all four registries into one record per category id."""
    registries = load_all_registries()
    merged: dict[str, dict[str, Any]] = {}
    for category_id in REQUIRED_CATEGORY_IDS:
        record: dict[str, Any] = {"id": category_id}
        for registry_name, rows in registries.items():
            row = rows.get(category_id)
            if row is not None:
                record[registry_name] = row
        merged[category_id] = record
    return merged


def get_category(category_id: str) -> dict[str, Any] | None:
    registries = load_all_registries()
    if category_id not in registries["sports"]:
        return None
    record: dict[str, Any] = {"id": category_id}
    for registry_name, rows in registries.items():
        record[registry_name] = rows.get(category_id)
    return record


def validate_registries(registries: dict[str, dict[str, Any]] | None = None) -> None:
    """Raise ``RegistryValidationError`` listing every consistency problem.

    Checks performed:
    - every required category id is present in every registry file
    - every row has all required fields for its file
    - every ``*_status`` value is one of the five allowed enum values
    - every ``classification_ceiling`` is one of the three allowed values
    - adapter-registry and data-sources-registry rows resolve back to a
      sports-registry id (the cross-reference check)
    - no unexpected extra ids exist beyond the required category list
    """
    problems: list[str] = []
    if registries is None:
        registries = load_all_registries()

    for registry_name, rows in registries.items():
        missing = REQUIRED_CATEGORY_IDS - rows.keys()
        for missing_id in sorted(missing):
            problems.append(f"{_FILES[registry_name]}: missing required category '{missing_id}'")

        extra = rows.keys() - REQUIRED_CATEGORY_IDS
        for extra_id in sorted(extra):
            problems.append(f"{_FILES[registry_name]}: unexpected category '{extra_id}' not in required list")

        for category_id, row in rows.items():
            for field in _REQUIRED_FIELDS[registry_name]:
                if field not in row:
                    problems.append(
                        f"{_FILES[registry_name]}: category '{category_id}' missing field '{field}'"
                    )

    for category_id, row in registries.get("data_sources", {}).items():
        status = row.get("runtime_status")
        if status is not None and status not in ALLOWED_RUNTIME_STATUSES:
            problems.append(
                f"data-sources-registry.yaml: category '{category_id}' has invalid runtime_status "
                f"'{status}' (allowed: {sorted(ALLOWED_RUNTIME_STATUSES)})"
            )
        if status == "UNSUPPORTED_INPUT" or status == "RESEARCH_ONLY":
            if not row.get("runtime_unsupported_reason"):
                problems.append(
                    f"data-sources-registry.yaml: category '{category_id}' has status '{status}' "
                    "but no runtime_unsupported_reason"
                )

    for category_id, row in registries.get("adapters", {}).items():
        status = row.get("adapter_status")
        if status is not None and status not in ALLOWED_ADAPTER_STATUSES:
            problems.append(
                f"adapter-registry.yaml: category '{category_id}' has invalid adapter_status "
                f"'{status}' (allowed: {sorted(ALLOWED_ADAPTER_STATUSES)})"
            )
        ceiling = row.get("classification_ceiling")
        if ceiling is not None and ceiling not in ALLOWED_CLASSIFICATION_CEILINGS:
            problems.append(
                f"adapter-registry.yaml: category '{category_id}' has invalid classification_ceiling "
                f"'{ceiling}' (allowed: {sorted(ALLOWED_CLASSIFICATION_CEILINGS)})"
            )
        # Cross-reference: every adapter-registry row must resolve to a
        # sports-registry id.
        if category_id not in registries.get("sports", {}):
            problems.append(
                f"adapter-registry.yaml: category '{category_id}' does not resolve to a "
                "sports-registry.yaml entry"
            )

    for category_id, row in registries.get("data_sources", {}).items():
        if category_id not in registries.get("sports", {}):
            problems.append(
                f"data-sources-registry.yaml: category '{category_id}' does not resolve to a "
                "sports-registry.yaml entry"
            )

    for category_id, row in registries.get("market_types", {}).items():
        if category_id not in registries.get("sports", {}):
            problems.append(
                f"market-types-registry.yaml: category '{category_id}' does not resolve to a "
                "sports-registry.yaml entry"
            )

    # underlying_sport cross-reference: any product family's underlying id
    # must itself be a real sport-kind entry (or null for cross-sport ones).
    sports_rows = registries.get("sports", {})
    for category_id, row in sports_rows.items():
        underlying = row.get("underlying")
        if underlying is not None:
            underlying_row = sports_rows.get(underlying)
            if underlying_row is None:
                problems.append(
                    f"sports-registry.yaml: category '{category_id}' has underlying "
                    f"'{underlying}' which is not a known category"
                )
            elif underlying_row.get("kind") != "sport":
                problems.append(
                    f"sports-registry.yaml: category '{category_id}' has underlying "
                    f"'{underlying}' which is not kind='sport'"
                )

    if problems:
        raise RegistryValidationError(problems)
