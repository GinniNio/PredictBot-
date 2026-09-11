"""Money handling: every stake, return, profit/loss, and betting odds
value in ``betting_ledger.py`` is a ``Decimal``, stored in JSON as a
decimal STRING -- never a binary float. Floats cannot represent most
decimal currency amounts exactly (``0.1 + 0.2 != 0.3``), and that error
compounds across system-bet combinations; this module exists so no
float ever enters a money calculation in this package.

``offered_odds``/``model_probabilities``/``market_devig_probabilities``
in ``forecast_ledger.py`` are NOT money -- they are prices/probabilities,
and this repository's already-reviewed pricing engine
(``pcbf_calculator.pricing.engine``) uses floats for those throughout;
this module's scope is deliberately limited to betting_ledger.py's own
stake/return/profit/odds fields, never forecast probabilities.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class MoneyValueError(ValueError):
    """Raised when a caller supplies a float (or otherwise non-decimal-
    safe value) where a decimal string, int, or ``Decimal`` was
    required."""


def to_decimal(value: Any, *, field_name: str = "value") -> Decimal:
    """Convert ``value`` to a ``Decimal``. Accepts ``Decimal``, ``int``,
    or ``str`` -- NEVER ``float``, which is rejected explicitly (not
    silently routed through ``Decimal(str(value))``, which would hide a
    caller's float mistake behind an accidental-looking correct answer)."""

    if isinstance(value, float):
        raise MoneyValueError(
            f"{field_name}: got a float ({value!r}) -- money fields must be a Decimal, "
            "int, or decimal string (e.g. \"10.00\"), never a binary float."
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise MoneyValueError(f"{field_name}: {value!r} is not a valid decimal string") from exc
    raise MoneyValueError(f"{field_name}: expected Decimal/int/str, got {type(value).__name__}")


def decimal_str(value: Decimal) -> str:
    """Canonical JSON-safe string form of a ``Decimal`` -- ``str(Decimal)``
    is already exact and round-trips through ``Decimal(str(d)) == d``;
    this wrapper exists so every write site uses one obviously-named
    call instead of a bare ``str()``."""

    return str(value)
