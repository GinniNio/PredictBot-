"""Tests for ledgers/betting_ledger.py: Decimal-safe money, ticket
placement (idempotent re-import), settlement math for singles, doubles,
trebles, system bets, voids, and cashout, the one-time settlement
lifecycle (append-only, payload-aware, never rewriting PLACED), and the
STOP-to-ticket protection -- ticket profit/loss kept separate from
forecast scoring throughout (no test here ever touches Brier score or
log loss)."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import betting_ledger, forecast_ledger, money, validation
from ledgers.storage import APPENDED, CONFLICT, DUPLICATE_SKIPPED, NOT_YET_PLACED, read_all


def _leg(i, odds, forecast_id=None):
    return {
        "leg_index": i,
        "forecast_id": forecast_id or f"fc_leg{i}",
        "fixture_id": f"fixture-{i}",
        "market_type": "1X2",
        "selection": "H",
        "placed_odds": odds,
    }


class MoneyTypeSafetyTests(unittest.TestCase):
    def test_float_unit_stake_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            betting_ledger.build_placed_event(
                ticket_type="SINGLE", unit_stake=10.0, max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
            )

    def test_float_max_return_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            betting_ledger.build_placed_event(
                ticket_type="SINGLE", unit_stake="10.00", max_return=19.0, currency="NGN", legs=[_leg(0, "1.9")]
            )

    def test_float_placed_odds_is_rejected(self):
        with self.assertRaises(money.MoneyValueError):
            betting_ledger.build_placed_event(
                ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, 1.9)]
            )

    def test_float_actual_return_for_cash_out_is_rejected(self):
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "betting-ledger.jsonl"
            event = betting_ledger.build_placed_event(
                ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
            )
            betting_ledger.append_placed(path, event)
            with self.assertRaises(money.MoneyValueError):
                betting_ledger.cash_out(path, event["ticket_id"], 15.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_decimal_string_int_and_decimal_all_accepted(self):
        for stake in (Decimal("10.00"), 10, "10.00", "10"):
            event = betting_ledger.build_placed_event(
                ticket_type="SINGLE", unit_stake=stake, max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
            )
            self.assertEqual(Decimal(event["payload"]["unit_stake"]), Decimal("10"))

    def test_money_fields_are_json_strings_never_numbers(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
        )
        self.assertIsInstance(event["payload"]["unit_stake"], str)
        self.assertIsInstance(event["payload"]["total_stake"], str)
        self.assertIsInstance(event["payload"]["max_return"], str)
        self.assertIsInstance(event["payload"]["legs"][0]["placed_odds"], str)

    def test_decimal_precision_never_lost_across_system_combinations(self):
        # 0.1 + 0.2 != 0.3 in binary float -- this proves the same style
        # of computation stays exact in this module across many
        # combinations, not just a single addition.
        tmp = tempfile.mkdtemp()
        try:
            path = Path(tmp) / "betting-ledger.jsonl"
            legs = [_leg(0, "1.1"), _leg(1, "1.2"), _leg(2, "1.3")]
            event = betting_ledger.build_placed_event(
                ticket_type="SYSTEM", unit_stake="0.1", max_return="9999", currency="NGN", legs=legs, system_sizes=[2, 3]
            )
            betting_ledger.append_placed(path, event)
            result = betting_ledger.settle_computed(
                path, event["ticket_id"], [{"leg_index": i, "outcome": "WON"} for i in range(3)]
            )
            actual = Decimal(result.record["payload"]["actual_return"])
            # 3 doubles (0.1*1.1*1.2 + 0.1*1.1*1.3 + 0.1*1.2*1.3) + 1 treble (0.1*1.1*1.2*1.3)
            expected = (
                Decimal("0.1") * Decimal("1.1") * Decimal("1.2")
                + Decimal("0.1") * Decimal("1.1") * Decimal("1.3")
                + Decimal("0.1") * Decimal("1.2") * Decimal("1.3")
                + Decimal("0.1") * Decimal("1.1") * Decimal("1.2") * Decimal("1.3")
            )
            self.assertEqual(actual, expected)  # EXACT equality -- no float tolerance needed
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BettingLedgerPlacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_placed_event_validates_against_schema(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
        )
        errors = validation.validate_envelope(event, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])

    def test_total_stake_never_duplicated_across_system_legs(self):
        # A Trixie (system_sizes [2,3] from 3 legs) has 4 combinations --
        # total_stake must be unit_stake * 4, recorded ONCE on the ticket,
        # never once per combination as separate stake fields.
        legs = [_leg(0, "1.9"), _leg(1, "2.1"), _leg(2, "1.8")]
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", unit_stake="2.00", max_return="100.00", currency="NGN", legs=legs, system_sizes=[2, 3]
        )
        self.assertEqual(event["payload"]["combination_count"], 4)
        self.assertEqual(Decimal(event["payload"]["total_stake"]), Decimal("8.00"))
        self.assertEqual(len(event["payload"]["legs"]), 3)  # legs list itself is never duplicated per combination

    def test_idempotent_reimport_of_identical_ticket_is_a_safe_no_op(self):
        def _build():
            return betting_ledger.build_placed_event(
                ticket_type="SINGLE",
                unit_stake="10.00",
                max_return="19.00",
                currency="NGN",
                legs=[_leg(0, "1.9")],
                placed_at_utc="2024-01-01T00:00:00+00:00",
                external_ticket_ref="slip-1",
            )

        betting_ledger.append_placed(self.path, _build())
        result = betting_ledger.append_placed(self.path, _build())
        self.assertEqual(result.status, DUPLICATE_SKIPPED)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_conflicting_reimport_under_same_external_ref_is_refused(self):
        event1 = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")], external_ticket_ref="slip-2"
        )
        betting_ledger.append_placed(self.path, event1)
        event2 = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="20.00", max_return="38.00", currency="NGN", legs=[_leg(0, "1.9")], external_ticket_ref="slip-2"
        )
        result = betting_ledger.append_placed(self.path, event2)
        self.assertEqual(result.status, CONFLICT)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_mismatched_leg_count_for_ticket_type_is_rejected(self):
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="DOUBLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
            )


class StopLinkedLegTests(unittest.TestCase):
    """Contract: place-ticket must reject any leg linked to a STOP
    forecast, even if the caller supplies a favourable classification or
    operator decision -- the check reads ONLY stop_reason."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.betting_path = Path(self.tmp) / "betting-ledger.jsonl"
        self.forecast_path = Path(self.tmp) / "forecast-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record_forecast(self, fixture_id, stop_reason=None, operator_decision=None, classification="RESEARCH-MODEL"):
        kwargs = dict(
            fixture_id=fixture_id,
            sport="soccer",
            league="E0",
            kickoff_utc="2024-01-01T14:00:00+00:00",
            market_type="1X2",
            offered_odds={"H": 1.9, "D": 3.4, "A": 4.2},
            classification=classification,
            stop_reason=stop_reason,
            operator_decision=operator_decision,
        )
        if stop_reason is None:
            kwargs.update(model_probabilities={"H": 0.5, "D": 0.3, "A": 0.2}, model_version="v1", artifact_hash="h", output_hash="h")
        event = forecast_ledger.build_recorded_event(**kwargs)
        forecast_ledger.append_recorded(self.forecast_path, event)
        return event["forecast_id"]

    def test_ticket_with_stop_linked_leg_is_rejected(self):
        fc_id = self._record_forecast("f-stop", stop_reason="JURISDICTIONAL_STOP")
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9", forecast_id=fc_id)]
        )
        with self.assertRaises(betting_ledger.StopLinkedLegError):
            betting_ledger.place_ticket_checked(self.betting_path, self.forecast_path, event)
        self.assertEqual(read_all(self.betting_path), [])  # nothing appended

    def test_favourable_operator_decision_or_classification_cannot_bypass_stop(self):
        # Even if operator_decision reads like an override and
        # classification is the normal RESEARCH-MODEL value, the STOP
        # check fires purely on stop_reason being non-null.
        fc_id = self._record_forecast(
            "f-stop-override",
            stop_reason="JURISDICTIONAL_STOP",
            operator_decision="OVERRIDE_APPROVED",
            classification="RESEARCH-MODEL",
        )
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9", forecast_id=fc_id)]
        )
        with self.assertRaises(betting_ledger.StopLinkedLegError):
            betting_ledger.place_ticket_checked(self.betting_path, self.forecast_path, event)

    def test_ticket_with_no_stop_linked_legs_is_accepted(self):
        fc_id = self._record_forecast("f-clean")
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9", forecast_id=fc_id)]
        )
        result = betting_ledger.place_ticket_checked(self.betting_path, self.forecast_path, event)
        self.assertEqual(result.status, APPENDED)

    def test_multi_leg_ticket_blocked_if_any_single_leg_is_stopped(self):
        fc_ok = self._record_forecast("f-ok")
        fc_stop = self._record_forecast("f-blocked", stop_reason="MARKET_TOO_THIN")
        event = betting_ledger.build_placed_event(
            ticket_type="DOUBLE",
            unit_stake="10.00",
            max_return="100.00",
            currency="NGN",
            legs=[_leg(0, "1.9", forecast_id=fc_ok), _leg(1, "2.0", forecast_id=fc_stop)],
        )
        with self.assertRaises(betting_ledger.StopLinkedLegError):
            betting_ledger.place_ticket_checked(self.betting_path, self.forecast_path, event)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _place(self, ticket_type, unit_stake, legs, system_sizes=None):
        event = betting_ledger.build_placed_event(
            ticket_type=ticket_type, unit_stake=unit_stake, max_return="9999.00", currency="NGN", legs=legs, system_sizes=system_sizes
        )
        betting_ledger.append_placed(self.path, event)
        return event["ticket_id"]

    def test_single_won(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("19.00"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("9.00"))

    def test_single_lost(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "LOST"}])
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("0"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("-10.00"))

    def test_single_void_refunds_stake(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "VOID"}])
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("10.00"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("0"))

    def test_double_both_won(self):
        tk_id = self._place("DOUBLE", "10.00", [_leg(0, "1.9"), _leg(1, "2.0")])
        result = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}]
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("10.00") * Decimal("1.9") * Decimal("2.0"))

    def test_double_one_leg_lost_loses_whole_combination(self):
        tk_id = self._place("DOUBLE", "10.00", [_leg(0, "1.9"), _leg(1, "2.0")])
        result = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "LOST"}]
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("0"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("-10.00"))

    def test_double_one_leg_void_collapses_to_single_on_remaining_leg(self):
        tk_id = self._place("DOUBLE", "10.00", [_leg(0, "1.9"), _leg(1, "2.0")])
        result = betting_ledger.settle_computed(
            self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "VOID"}]
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("10.00") * Decimal("1.9"))

    def test_treble_all_won(self):
        tk_id = self._place("TREBLE", "5.00", [_leg(0, "1.5"), _leg(1, "1.8"), _leg(2, "2.2")])
        result = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "WON"}],
        )
        expected = Decimal("5.00") * Decimal("1.5") * Decimal("1.8") * Decimal("2.2")
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), expected)

    def test_treble_one_lost_loses_everything(self):
        tk_id = self._place("TREBLE", "5.00", [_leg(0, "1.5"), _leg(1, "1.8"), _leg(2, "2.2")])
        result = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "LOST"}, {"leg_index": 2, "outcome": "WON"}],
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("0"))

    def test_system_trixie_all_three_won_pays_all_four_combinations(self):
        legs = [_leg(0, "2.0"), _leg(1, "2.0"), _leg(2, "2.0")]
        tk_id = self._place("SYSTEM", "1.00", legs, system_sizes=[2, 3])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": i, "outcome": "WON"} for i in range(3)])
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("20.00"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("20.00") - Decimal("4.00"))

    def test_system_trixie_one_leg_lost_only_the_untouched_double_survives(self):
        legs = [_leg(0, "2.0"), _leg(1, "2.0"), _leg(2, "2.0")]
        tk_id = self._place("SYSTEM", "1.00", legs, system_sizes=[2, 3])
        result = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "LOST"}],
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("1.00") * Decimal("2.0") * Decimal("2.0"))

    def test_system_trixie_one_leg_void_reduces_combinations(self):
        legs = [_leg(0, "2.0"), _leg(1, "3.0"), _leg(2, "5.0")]
        tk_id = self._place("SYSTEM", "1.00", legs, system_sizes=[2, 3])
        result = betting_ledger.settle_computed(
            self.path,
            tk_id,
            [{"leg_index": 0, "outcome": "WON"}, {"leg_index": 1, "outcome": "WON"}, {"leg_index": 2, "outcome": "VOID"}],
        )
        expected = (
            Decimal("1.00") * Decimal("2.0") * Decimal("3.0")
            + Decimal("1.00") * Decimal("2.0")
            + Decimal("1.00") * Decimal("3.0")
            + Decimal("1.00") * Decimal("2.0") * Decimal("3.0")
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), expected)

    def test_system_lucky15_style_four_legs(self):
        legs = [_leg(0, "2.0"), _leg(1, "2.0"), _leg(2, "2.0"), _leg(3, "2.0")]
        tk_id = self._place("SYSTEM", "1.00", legs, system_sizes=[1, 2, 3, 4])
        self.assertEqual(betting_ledger.combination_count_for("SYSTEM", 4, [1, 2, 3, 4]), 15)
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": i, "outcome": "WON"} for i in range(4)])
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("80.00"))

    def test_settle_missing_leg_result_raises(self):
        tk_id = self._place("DOUBLE", "10.00", [_leg(0, "1.9"), _leg(1, "2.0")])
        with self.assertRaises(ValueError):
            betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])

    def test_settle_unknown_ticket_returns_not_yet_placed(self):
        result = betting_ledger.settle_computed(self.path, "tk_doesnotexist0000", [])
        self.assertEqual(result.status, NOT_YET_PLACED)

    def test_void_unknown_ticket_returns_not_yet_placed(self):
        result = betting_ledger.void_ticket(self.path, "tk_doesnotexist0000")
        self.assertEqual(result.status, NOT_YET_PLACED)

    def test_cash_out_unknown_ticket_returns_not_yet_placed(self):
        result = betting_ledger.cash_out(self.path, "tk_doesnotexist0000", "10.00")
        self.assertEqual(result.status, NOT_YET_PLACED)

    def test_void_ticket_refunds_full_stake_zero_profit(self):
        tk_id = self._place("DOUBLE", "10.00", [_leg(0, "1.9"), _leg(1, "2.0")])
        result = betting_ledger.void_ticket(self.path, tk_id, reason="bookmaker cancelled")
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("10.00"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("0"))
        self.assertEqual(result.record["event_type"], "VOIDED")

    def test_cash_out_uses_operator_reported_return_only(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.cash_out(self.path, tk_id, "15.00")
        self.assertEqual(result.record["event_type"], "CASHED_OUT")
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("15.00"))
        self.assertEqual(Decimal(result.record["payload"]["profit_loss"]), Decimal("5.00"))
        self.assertEqual(result.record["payload"]["settlement_method"], "MANUAL")

    def test_settlement_events_validate_against_schema(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        errors = validation.validate_envelope(result.record, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])

    def test_forecast_scoring_and_ticket_profit_loss_are_independent_fields(self):
        tk_id = self._place("SINGLE", "10.00", [_leg(0, "1.9")])
        result = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertNotIn("brier_score", result.record["payload"])
        self.assertNotIn("log_loss", result.record["payload"])


class TicketLifecycleTests(unittest.TestCase):
    """Contract: settlement is append-only and never rewrites PLACED;
    idempotency is payload-aware; conflicting terminal events (including
    a different TYPE of terminal event) are refused; re-importing a
    settled ticket never duplicates its return or profit."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _place(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[_leg(0, "1.9")]
        )
        betting_ledger.append_placed(self.path, event)
        return event["ticket_id"], event

    def test_placed_record_is_never_rewritten_by_settlement(self):
        tk_id, placed_event = self._place()
        before = read_all(self.path)[0]
        betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        after_records = read_all(self.path)
        self.assertEqual(after_records[0], before)  # the PLACED line is byte-identical, never touched
        self.assertEqual(len(after_records), 2)  # PLACED + SETTLED, strictly appended

    def test_reimporting_identical_settlement_is_a_safe_no_op_no_duplicated_return(self):
        tk_id, _ = self._place()
        first = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertEqual(first.status, APPENDED)
        second = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        self.assertEqual(second.status, DUPLICATE_SKIPPED)

        records = read_all(self.path)
        settled = [r for r in records if r["event_type"] == "SETTLED"]
        self.assertEqual(len(settled), 1)  # never duplicated
        # current_state's actual_return/profit_loss reflect exactly one
        # settlement's numbers, not a sum of two.
        state = betting_ledger.current_state(self.path, tk_id)
        self.assertEqual(Decimal(state["actual_return"]), Decimal("19.00"))

    def test_reimporting_with_different_leg_results_is_a_conflict(self):
        tk_id, _ = self._place()
        betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        conflict = betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "LOST"}])
        self.assertEqual(conflict.status, CONFLICT)
        records = read_all(self.path)
        settled = [r for r in records if r["event_type"] == "SETTLED"]
        self.assertEqual(len(settled), 1)
        self.assertEqual(settled[0]["payload"]["leg_results"][0]["outcome"], "WON")  # original untouched

    def test_cash_out_after_settled_is_a_conflict_not_a_second_terminal_state(self):
        tk_id, _ = self._place()
        betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        result = betting_ledger.cash_out(self.path, tk_id, "12.00")
        self.assertEqual(result.status, CONFLICT)
        records = read_all(self.path)
        terminal = [r for r in records if r["event_type"] in betting_ledger.TERMINAL_EVENTS]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["event_type"], "SETTLED")

    def test_void_after_cash_out_is_a_conflict(self):
        tk_id, _ = self._place()
        betting_ledger.cash_out(self.path, tk_id, "12.00")
        result = betting_ledger.void_ticket(self.path, tk_id)
        self.assertEqual(result.status, CONFLICT)

    def test_settle_before_placement_returns_not_yet_placed_never_fabricates_a_ticket(self):
        result = betting_ledger.settle_computed(self.path, "tk_nonexistent00000", [{"leg_index": 0, "outcome": "WON"}])
        self.assertEqual(result.status, NOT_YET_PLACED)
        self.assertEqual(read_all(self.path), [])

    def test_exactly_one_terminal_event_ever_recorded_per_ticket(self):
        tk_id, _ = self._place()
        betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])
        betting_ledger.settle_computed(self.path, tk_id, [{"leg_index": 0, "outcome": "WON"}])  # no-op
        betting_ledger.void_ticket(self.path, tk_id)  # conflict, refused
        betting_ledger.cash_out(self.path, tk_id, "1.00")  # conflict, refused
        records = read_all(self.path)
        terminal = [r for r in records if r["event_type"] in betting_ledger.TERMINAL_EVENTS]
        self.assertEqual(len(terminal), 1)


def _real_system_stake_buckets():
    """The real shape from a genuine Bet9ja 5-leg system ticket (Singles
    35.00, Doubles/Trebles/4-Folds at 3.00 each) -- total_stake 250.00.
    Never derived from system_table_raw text; hand-built here as
    already-structured data, exactly what a real stake_buckets field
    (once the capture emits one) would look like."""
    return [
        {"fold_size": 1, "combination_count": 5, "unit_stake": "35.00", "total_stake": "175.00"},
        {"fold_size": 2, "combination_count": 10, "unit_stake": "3.00", "total_stake": "30.00"},
        {"fold_size": 3, "combination_count": 10, "unit_stake": "3.00", "total_stake": "30.00"},
        {"fold_size": 4, "combination_count": 5, "unit_stake": "3.00", "total_stake": "15.00"},
    ]


class StakeBucketsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"
        self.legs = [_leg(i, "2.00") for i in range(5)]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_builds_correct_total_stake_and_null_unit_stake(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs,
            stake_buckets=_real_system_stake_buckets(),
        )
        self.assertIsNone(event["payload"]["unit_stake"])
        self.assertEqual(Decimal(event["payload"]["total_stake"]), Decimal("250.00"))
        self.assertEqual(event["payload"]["system_sizes"], [1, 2, 3, 4])
        self.assertEqual(len(event["payload"]["stake_buckets"]), 4)

    def test_validates_against_schema(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs,
            stake_buckets=_real_system_stake_buckets(),
        )
        errors = validation.validate_envelope(event, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])

    def test_omitting_stake_buckets_leaves_payload_byte_for_byte_as_before(self):
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", unit_stake="5.00", max_return="9999", currency="NGN", legs=self.legs,
            system_sizes=[1, 2],
        )
        self.assertNotIn("stake_buckets", event["payload"])
        self.assertIsInstance(event["payload"]["unit_stake"], str)

    def test_supplying_both_unit_stake_and_stake_buckets_is_rejected(self):
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", unit_stake="5.00", max_return="9999", currency="NGN", legs=self.legs,
                stake_buckets=_real_system_stake_buckets(),
            )

    def test_supplying_neither_unit_stake_nor_stake_buckets_is_rejected(self):
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs, system_sizes=[1, 2],
            )

    def test_rejects_bucket_combination_count_mismatch(self):
        buckets = _real_system_stake_buckets()
        buckets[0]["combination_count"] = 999
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs, stake_buckets=buckets,
            )

    def test_rejects_bucket_total_stake_mismatch(self):
        buckets = _real_system_stake_buckets()
        buckets[0]["total_stake"] = "999.00"
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs, stake_buckets=buckets,
            )

    def test_rejects_duplicate_fold_size(self):
        buckets = _real_system_stake_buckets()
        buckets.append(dict(buckets[0]))
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs, stake_buckets=buckets,
            )

    def test_rejects_fold_size_larger_than_leg_count(self):
        buckets = [{"fold_size": 6, "combination_count": 1, "unit_stake": "1.00", "total_stake": "1.00"}]
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=self.legs, stake_buckets=buckets,
            )

    def test_rejects_stake_buckets_on_a_non_system_ticket_type(self):
        with self.assertRaises(ValueError):
            betting_ledger.build_placed_event(
                ticket_type="SINGLE", max_return="9999", currency="NGN", legs=[self.legs[0]],
                stake_buckets=[{"fold_size": 1, "combination_count": 1, "unit_stake": "1.00", "total_stake": "1.00"}],
            )

    def test_settlement_scores_each_bucket_at_its_own_stake(self):
        legs = [_leg(i, "2.00") for i in range(5)]  # every leg odds 2.00 for easy arithmetic
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=legs,
            stake_buckets=_real_system_stake_buckets(),
        )
        betting_ledger.append_placed(self.path, event)
        # Every leg WON -> every combination wins. Singles: 5 combos * 35.00 * 2.00 = 350.00.
        # Doubles: 10 combos * 3.00 * 2.00^2 = 120.00. Trebles: 10 * 3.00 * 2.00^3 = 240.00.
        # 4-folds: 5 * 3.00 * 2.00^4 = 240.00. Total = 350+120+240+240 = 950.00.
        result = betting_ledger.settle_computed(
            self.path, event["ticket_id"], [{"leg_index": i, "outcome": "WON"} for i in range(5)]
        )
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("950.00"))
        # A naive single-uniform-stake computation (using e.g. the smallest
        # bucket's own 3.00 stake across every combination) would give a
        # very different, WRONG total -- proving buckets are genuinely
        # scored separately, not collapsed into one stake.
        self.assertNotEqual(Decimal(result.record["payload"]["actual_return"]), Decimal("3.00") * (5 + 10 + 10 + 5))

    def test_settlement_with_a_lost_leg_only_pays_combinations_not_containing_it(self):
        legs = [_leg(i, "2.00") for i in range(5)]
        event = betting_ledger.build_placed_event(
            ticket_type="SYSTEM", max_return="9999", currency="NGN", legs=legs,
            stake_buckets=_real_system_stake_buckets(),
        )
        betting_ledger.append_placed(self.path, event)
        outcomes = [{"leg_index": i, "outcome": "WON" if i != 0 else "LOST"} for i in range(5)]
        result = betting_ledger.settle_computed(self.path, event["ticket_id"], outcomes)
        # Leg 0 LOST: its own single (35.00) loses; every combo containing
        # leg 0 across every fold size loses too. Only combos entirely
        # among legs {1,2,3,4} survive: Singles legs 1-4 (4*35.00*2=280.00),
        # Doubles among {1,2,3,4} (C(4,2)=6 combos *3.00*4=72.00),
        # Trebles among {1,2,3,4} (C(4,3)=4 combos *3.00*8=96.00),
        # the one 4-fold among {1,2,3,4} (1 combo *3.00*16=48.00).
        expected = Decimal("280.00") + Decimal("72.00") + Decimal("96.00") + Decimal("48.00")
        self.assertEqual(Decimal(result.record["payload"]["actual_return"]), expected)


class NullForecastIdLegTests(unittest.TestCase):
    def test_an_unlinked_leg_with_null_forecast_id_is_accepted_and_validates(self):
        leg = _leg(0, "1.9")
        leg["forecast_id"] = None
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN", legs=[leg]
        )
        self.assertIsNone(event["payload"]["legs"][0]["forecast_id"])
        errors = validation.validate_envelope(event, validation.load_schema("betting_ledger.v1"))
        self.assertEqual(errors, [])
        # An unlinked leg is never STOP-checked -- check_legs_not_stopped
        # skips it outright (nothing to look up).
        tmp = tempfile.mkdtemp()
        try:
            forecast_path = Path(tmp) / "forecast-ledger.jsonl"
            betting_ledger.check_legs_not_stopped(forecast_path, event["payload"]["legs"])  # must not raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class WriteBatchPlacedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "betting-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _event(self, ref, stake="10.00"):
        return betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake=stake, max_return="19.00", currency="NGN",
            legs=[_leg(0, "1.9")], external_ticket_ref=ref,
        )

    def test_a_clean_batch_appends_every_event(self):
        events = [self._event("ref-1"), self._event("ref-2"), self._event("ref-3")]
        summary = betting_ledger.write_batch_placed(self.path, events)
        self.assertEqual(summary["attempted"], 3)
        self.assertEqual(summary["appended"], 3)
        self.assertEqual(summary["duplicate_skipped"], 0)
        self.assertEqual(summary["conflicted"], 0)
        self.assertEqual(summary["total_ledger_records"], 3)

    def test_repeating_the_identical_batch_is_a_safe_no_op(self):
        events = [self._event("ref-1"), self._event("ref-2")]
        betting_ledger.write_batch_placed(self.path, events)
        summary = betting_ledger.write_batch_placed(self.path, events)
        self.assertEqual(summary["appended"], 0)
        self.assertEqual(summary["duplicate_skipped"], 2)
        self.assertEqual(summary["total_ledger_records"], 2)

    def test_a_conflicting_batch_writes_nothing_at_all(self):
        first = [self._event("ref-1", stake="10.00")]
        betting_ledger.write_batch_placed(self.path, first)
        records_before = read_all(self.path)

        conflicting = [self._event("ref-1", stake="99.00"), self._event("ref-2", stake="10.00")]
        with self.assertRaises(betting_ledger.BettingLedgerBatchConflictError):
            betting_ledger.write_batch_placed(self.path, conflicting)
        # Neither the conflicting ref-1 NOR the perfectly-clean ref-2 was
        # written -- one bad item in the batch blocks the whole batch.
        self.assertEqual(read_all(self.path), records_before)
        self.assertEqual(len(read_all(self.path)), 1)

    def test_an_intra_batch_duplicate_is_recognized_against_the_first_occurrence(self):
        event = self._event("ref-1")
        summary = betting_ledger.write_batch_placed(self.path, [event, event])
        self.assertEqual(summary["appended"], 1)
        self.assertEqual(summary["duplicate_skipped"], 1)
        self.assertEqual(summary["total_ledger_records"], 1)


if __name__ == "__main__":
    unittest.main()
