"""Tests for ledgers/money.py directly -- the float-rejection guardrail
every stake/return/profit/odds value in betting_ledger.py depends on."""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import money


class ToDecimalTests(unittest.TestCase):
    def test_float_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            money.to_decimal(10.5)

    def test_float_zero_is_rejected(self):
        # A special case worth its own test: 0.0 is falsy in Python, so a
        # naive `if not value: raise` check would miss it -- to_decimal
        # must reject it on TYPE, not on truthiness.
        with self.assertRaises(money.MoneyValueError):
            money.to_decimal(0.0)

    def test_decimal_passes_through_unchanged(self):
        d = Decimal("10.50")
        self.assertIs(money.to_decimal(d), d)

    def test_int_is_accepted(self):
        self.assertEqual(money.to_decimal(10), Decimal(10))

    def test_valid_decimal_string_is_accepted(self):
        self.assertEqual(money.to_decimal("10.50"), Decimal("10.50"))

    def test_invalid_string_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            money.to_decimal("not-a-number")

    def test_none_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            money.to_decimal(None)

    def test_error_message_includes_field_name(self):
        with self.assertRaises(money.MoneyValueError) as ctx:
            money.to_decimal(1.5, field_name="unit_stake")
        self.assertIn("unit_stake", str(ctx.exception))

    def test_decimal_arithmetic_is_exact_where_float_would_drift(self):
        # The canonical float-imprecision example: 0.1 + 0.2 != 0.3 in
        # binary float, but is exact in Decimal.
        total = money.to_decimal("0.1") + money.to_decimal("0.2")
        self.assertEqual(total, Decimal("0.3"))


class DecimalStrTests(unittest.TestCase):
    def test_round_trips_through_decimal(self):
        d = Decimal("10.50")
        s = money.decimal_str(d)
        self.assertIsInstance(s, str)
        self.assertEqual(Decimal(s), d)


if __name__ == "__main__":
    unittest.main()
