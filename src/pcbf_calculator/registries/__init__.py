"""Registry access layer for the multi-sport pricing platform.

See ``docs/MULTI_SPORT_ARCHITECTURE.md`` for the schema design and the list
of required categories.
"""

from .loader import (
    ALLOWED_ADAPTER_STATUSES,
    ALLOWED_RUNTIME_STATUSES,
    REQUIRED_CATEGORY_IDS,
    RegistryValidationError,
    load_all_registries,
    validate_registries,
)

__all__ = [
    "ALLOWED_ADAPTER_STATUSES",
    "ALLOWED_RUNTIME_STATUSES",
    "REQUIRED_CATEGORY_IDS",
    "RegistryValidationError",
    "load_all_registries",
    "validate_registries",
]
