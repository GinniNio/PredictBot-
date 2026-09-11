"""Tests for ledgers/betting_ledger.py: ticket placement (idempotent
re-import), and settlement math for singles, doubles, trebles, system
bets, voids, and cashout -- ticket profit/loss kept separate from
forecast scoring throughout (no test here ever touches Brier score or
log loss)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import betting_ledger, validation
from ledgers.storage import APPENDED, CONFLICT, DUPLICATE_SKIPPED, read_all


def _leg(i, odds, forecast_id=None):
    return {
        "leg_index": i,
        "forecast_id": forecast_id or f"fc_leg{i}",
        "fixture_id": f"fixture-{i}",
        "market_type": "1X2",
        "selection": "H",
        "placed_odds": odds,
    }


class BettingLedgerPlacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_placed_event_validates_against_schema(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake=10.0, max_return=19.0, currency="NGN", legs=[_leg(0, 1.9)]
        )
        errors = validation.validate_envelope(event, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])

    def test_total_stake_never_duplicated_across_system_legs(self):
        # A Trixie (system_sizes [2,3] from 3 legs) has 4 combinations --
        # total_stake must be unit_stake * 4, recorded ONCE on the ticket,
        # never once per combination as separate stake fields.
        legs = [_leg(0, 1.9), _leg(1, 2.1), _leg(2, 1.8)]
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", unit_stake=2.0, max_return=100.0, currency="NGN", legs=legs, system_sizes=[2, 3]
        )
        self.assertEqual(event["payload"]["combination_count"], 4)
        self.assertAlmostEqual(event["payload"]["total_stake"], 8.0, places=9)
        self.assertEqual(len(event["payload"]["legs"]), 3)  # legs list itself is never duplicated per combination

    def test_idempotent_reimport_of_identical_ticket_is_a_safe_no_op(self):
        event1 = betting_ledger.build_placed_event(
            ticket_type="SINGLE",
            unit_stake=10.0,
            max_return=19.0,
            currency="NGN",
            legs=[_leg(0, 1.9)],
            placed_at_utc="2024-01-01T00:00:00+00:00",
            external_ticket_ref="slip-1",
        )
        betting_ledger.append_placed(self.path, event1)
        event2 = betting_ledger.build_placed_event(
            ticket_type="SINGLE",
            unit_stake=10.0,
            max_return=19.0,
            currency="NGN",
            legs=[_leg(0, 1.9)],
            placed_at_utc="2024-01-01T00:00:00+00:00",
            external_ticket_ref="slip-1",
        )
        result = betting_ledger.append_placed(self.path, event2)
        self.assertEqual(result.status, DUPLICATE_SKIPPED)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_conflicting_reimport_under_same_external_ref_is_refused(self):
        event1 = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake=10.0, max_return=19.0, currency="NGN", legs=[_leg(0, 1.9)], external_ticket_ref="slip-2"
        )
        betting_ledger.append_placed(self.path, event1)
        event2 = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake=20.0, max_return=38.0, currency="NGN", legs=[_leg(0, 1.9)], external_ticket_ref="slip-2"
        )
        result = betting_ledger.append_placed(self.path, event2)
        self.assertEqual(result.status, CONFLICT)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_mismatched_leg_count_for_ticket_type_is_rejected(self):
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="DOUBLE", unit_stake=10.0, max_return=19.0, currency="NGN", legs=[_leg(0, 1.9)]
            )


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _place(self, ticket_type, unit_stake, legs, system_sizes=None):
        event = betting_ledger.build_placed_event(
            ticket_type=ticket_type, unit_stake=unit_stake, max_return=9999.0, currency="NGN", legs=legs, system_sizes=system_sizes
        )
        betting_ledger.append_placed(self.path, event)
        return event["ticket_id"]

    def test_single_won(self):
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertAlmostEqual(event["payload"]["actual_return"], 19.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], 9.0, places=9)

    def test_single_lost(self):
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "LOST"}])
        self.assertAlmostEqual(event["payload"]["actual_return"], 0.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], -10.0, places=9)

    def test_single_void_refunds_stake(self):
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "VOID"}])
        self.assertAlmostEqual(event["payload"]["actual_return"], 10.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], 0.0, places=9)

    def test_double_both_won(self):
        tk_id = self._place("DOUBLE", 10.0, [_leg(0, 1.9), _leg(1, 2.0)])
        event = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}]
        )
        self.assertAlmostEqual(event["payload"]["actual_return"], 10.0 * 1.9 * 2.0, places=9)

    def test_double_one_leg_lost_loses_whole_combination(self):
        tk_id = self._place("DOUBLE", 10.0, [_leg(0, 1.9), _leg(1, 2.0)])
        event = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "LOST"}]
        )
        self.assertAlmostEqual(event["payload"]["actual_return"], 0.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], -10.0, places=9)

    def test_double_one_leg_void_collapses_to_single_on_remaining_leg(self):
        tk_id = self._place("DOUBLE", 10.0, [_leg(0, 1.9), _leg(1, 2.0)])
        event = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "VOID"}]
        )
        # Void leg removed -> pays as a single on leg 0's own odds.
        self.assertAlmostEqual(event["payload"]["actual_return"], 10.0 * 1.9, places=9)

    def test_treble_all_won(self):
        tk_id = self._place("TREBLE", 5.0, [_leg(0, 1.5), _leg(1, 1.8), _leg(2, 2.2)])
        event = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "WON"}],
        )
        self.assertAlmostEqual(event["payload"]["actual_return"], 5.0 * 1.5 * 1.8 * 2.2, places=9)

    def test_treble_one_lost_loses_everything(self):
        tk_id = self._place("TREBLE", 5.0, [_leg(0, 1.5), _leg(1, 1.8), _leg(2, 2.2)])
        event = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "LOST"}, {"leg_index": 2, "outcome": "WON"}],
        )
        self.assertAlmostEqual(event["payload"]["actual_return"], 0.0, places=9)

    def test_system_trixie_all_three_won_pays_all_four_combinations(self):
        # Trixie from 3 legs: 3 doubles + 1 treble = 4 combinations.
        legs = [_leg(0, 2.0), _leg(1, 2.0), _leg(2, 2.0)]
        tk_id = self._place("SYSTEM", 1.0, legs, system_sizes=[2, 3])
        event = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": i, "outcome": "WON"} for i in range(3)],
        )
        # 3 doubles @ 1*2*2=4 each (=12) + 1 treble @ 1*2*2*2=8 => 20 total.
        self.assertAlmostEqual(event["payload"]["actual_return"], 20.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], 20.0 - 4.0, places=9)  # total_stake = 1.0 * 4 combos

    def test_system_trixie_one_leg_lost_only_the_untouched_double_survives(self):
        legs = [_leg(0, 2.0), _leg(1, 2.0), _leg(2, 2.0)]
        tk_id = self._place("SYSTEM", 1.0, legs, system_sizes=[2, 3])
        # leg 2 lost -> the double(0,1) still wins; double(0,2), double(1,2)
        # and the treble all contain the lost leg and pay nothing.
        event = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "LOST"}],
        )
        self.assertAlmostEqual(event["payload"]["actual_return"], 1.0 * 2.0 * 2.0, places=9)

    def test_system_trixie_one_leg_void_reduces_combinations(self):
        # leg 2 voided: double(0,1) unaffected; double(0,2) collapses to a
        # single on leg 0; double(1,2) collapses to a single on leg 1; the
        # treble collapses to the double(0,1) again.
        legs = [_leg(0, 2.0), _leg(1, 3.0), _leg(2, 5.0)]
        tk_id = self._place("SYSTEM", 1.0, legs, system_sizes=[2, 3])
        event = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "VOID"}],
        )
        expected = (1.0 * 2.0 * 3.0) + (1.0 * 2.0) + (1.0 * 3.0) + (1.0 * 2.0 * 3.0)
        self.assertAlmostEqual(event["payload"]["actual_return"], expected, places=9)

    def test_system_lucky15_style_four_legs(self):
        # Lucky15 shape: singles+doubles+trebles+fourfold from 4 legs = 15 combos.
        legs = [_leg(0, 2.0), _leg(1, 2.0), _leg(2, 2.0), _leg(3, 2.0)]
        tk_id = self._place("SYSTEM", 1.0, legs, system_sizes=[1, 2, 3, 4])
        self.assertEqual(betting_ledger.combination_count_for("SYSTEM", 4, [1, 2, 3, 4]), 15)
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": i, "outcome": "WON"} for i in range(4)])
        # 4 singles@2 + 6 doubles@4 + 4 trebles@8 + 1 fourfold@16 = 8+24+32+16 = 80
        self.assertAlmostEqual(event["payload"]["actual_return"], 80.0, places=9)

    def test_settle_missing_leg_result_raises(self):
        tk_id = self._place("DOUBLE", 10.0, [_leg(0, 1.9), _leg(1, 2.0)])
        with self.assertRaises(ValueError):
            betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])

    def test_settle_unknown_ticket_raises(self):
        with self.assertRaises(KeyError):
            betting_ledger.settle_computed(self.path, "tk_doesnotexist0000", [])

    def test_void_ticket_refunds_full_stake_zero_profit(self):
        tk_id = self._place("DOUBLE", 10.0, [_leg(0, 1.9), _leg(1, 2.0)])
        event = betting_ledger.void_ticket(self.path, tk_id, reason="bookmaker cancelled")
        self.assertAlmostEqual(event["payload"]["actual_return"], 10.0, places=9)  # total_stake for 1 combo
        self.assertAlmostEqual(event["payload"]["profit_loss"], 0.0, places=9)
        self.assertEqual(event["event_type"], "VOIDED")

    def test_cash_out_uses_operator_reported_return_only(self):
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.cash_out(self.path, tk_id, actual_return=15.0)
        self.assertEqual(event["event_type"], "CASHED_OUT")
        self.assertAlmostEqual(event["payload"]["actual_return"], 15.0, places=9)
        self.assertAlmostEqual(event["payload"]["profit_loss"], 5.0, places=9)
        self.assertEqual(event["payload"]["settlement_method"], "MANUAL")

    def test_settlement_events_validate_against_schema(self):
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        errors = validation.validate_envelope(event, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])

    def test_forecast_scoring_and_ticket_profit_loss_are_independent_fields(self):
        # A ticket's settlement payload never contains a Brier/log-loss
        # field, and a forecast's SCORED payload never contains a stake/
        # profit field -- the two calculations are structurally separate.
        tk_id = self._place("SINGLE", 10.0, [_leg(0, 1.9)])
        event = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertNotIn("brier_score", event["payload"])
        self.assertNotIn("log_loss", event["payload"])


if __name__ == "__main__":
    unittest.main()
