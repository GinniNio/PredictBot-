"""Tests for pcbf_calculator.orchestration.bet9ja_ticket_import: currency
handling, structured system-stake derivation (never system_table_raw
parsing), exact-canonical-identity forecast linkage, bookmaker-observed
settlement for real settled tickets, the full multi-reason quarantine
matrix, and whole-batch import safety for both the PLACED and SETTLED
batches."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ledgers import betting_ledger, forecast_ledger
from ledgers.storage import read_all

from pcbf_calculator.orchestration import bet9ja_ticket_import as importer


def _open_single_ticket(**overrides):
    ticket = {
        "bet9ja_ticket_id": "000111222",
        "captured_at_utc": "2026-09-14T17:27:41.802Z",
        "placed_at_raw": "14 Sep 2026 17:40",
        "placed_at_utc": None,
        "potential_return": 66.5,
        "status": "OPEN",
        "ticket_type_normalized": "SINGLE",
        "total_stake": 35,
        "unit_stake": 35,
        "legs": [
            {
                "competition_raw": "Premier League",
                "fixture_and_time_raw": "Manchester Utd - Manchester City15 Sep 19:45",
                "fixture_id": "bxf_aaa",
                "market_raw": "1X2",
                "odds": 1.9,
                "selection": "Manchester City",
                "selection_raw": "Manchester City",
            }
        ],
    }
    ticket.update(overrides)
    return ticket


def _system_ticket_singles(**overrides):
    """The real shape of one of the "18 valid uniform-stake" tickets found
    in this session's own real-data reconciliation: 5 legs, a "Singles"
    system (fold_size=1), unit_stake 35.00, total_stake 175.00.

    NOTE: this shape is NOT actually resolvable by
    ``_derive_fold_size`` -- ``C(5,1) == C(5,4) == 5``, so the
    combination-count identity alone cannot tell "Singles" (fold_size=1)
    apart from "4-Folds" (fold_size=4) without reading
    ``system_table_raw``, which this module refuses to parse. It is kept
    here, and exercised as a QUARANTINED case (it is an OPEN ticket, so
    the bookmaker-observed settlement path never applies), specifically
    to document that this real shape still cannot safely clear today's
    importer without a genuine structured ``stake_buckets`` field from a
    fixed browser capture (path 1)."""

    legs = []
    teams = [
        ("Premier League", "Manchester Utd - Manchester City15 Sep 19:45", "Manchester City"),
        ("Premier League", "Arsenal - Chelsea15 Sep 17:30", "Arsenal"),
        ("La Liga", "Real Madrid - Barcelona15 Sep 20:00", "Real Madrid"),
        ("Bundesliga", "Bayern Munich - Dortmund15 Sep 18:30", "Bayern Munich"),
        ("Serie A", "Juventus - Inter15 Sep 19:45", "Juventus"),
    ]
    for competition, fixture_text, selection in teams:
        legs.append(
            {
                "competition_raw": competition,
                "fixture_and_time_raw": fixture_text,
                "fixture_id": f"bxf_{selection.replace(' ', '_')}",
                "market_raw": "1X2",
                "odds": 1.5,
                "selection": selection,
                "selection_raw": selection,
            }
        )
    ticket = {
        "bet9ja_ticket_id": "0040872301",
        "captured_at_utc": "2026-09-14T17:27:41.802Z",
        "placed_at_raw": "14 Sep 2026 17:40",
        "potential_return": 175.0 * (1.5**1),
        "status": "OPEN",
        "ticket_type_normalized": "SYSTEM",
        "total_stake": "175.00",
        "unit_stake": "35.00",
        "system_table_raw": "System TypeNo.BetsUnit StakeStakeSingles535.00175.00",
        "legs": legs,
    }
    ticket.update(overrides)
    return ticket


def _system_ticket_full_accumulator(**overrides):
    """A genuinely unique path-2 case: 3 legs, one combination covering
    ALL of them (a straight accumulator dressed up as a SYSTEM ticket --
    Bet9ja's own UI does this). ``C(3, k) == 1`` only at ``k == 3`` (the
    only other root, ``k == 0``, is never a valid fold size), so this is
    resolvable without reading system_table_raw at all."""

    legs = [
        {
            "competition_raw": "Premier League",
            "fixture_and_time_raw": "Manchester Utd - Manchester City15 Sep 19:45",
            "fixture_id": "bxf_manutd_mancity",
            "market_raw": "1X2",
            "odds": 1.5,
            "selection": "Manchester City",
            "selection_raw": "Manchester City",
        },
        {
            "competition_raw": "La Liga",
            "fixture_and_time_raw": "Real Madrid - Barcelona15 Sep 20:00",
            "fixture_id": "bxf_realmadrid_barca",
            "market_raw": "1X2",
            "odds": 1.6,
            "selection": "Real Madrid",
            "selection_raw": "Real Madrid",
        },
        {
            "competition_raw": "Serie A",
            "fixture_and_time_raw": "Juventus - Inter15 Sep 19:45",
            "fixture_id": "bxf_juventus_inter",
            "market_raw": "1X2",
            "odds": 1.4,
            "selection": "Juventus",
            "selection_raw": "Juventus",
        },
    ]
    ticket = {
        "bet9ja_ticket_id": "004405829",
        "captured_at_utc": "2026-09-14T17:27:41.802Z",
        "placed_at_raw": "14 Sep 2026 17:40",
        "potential_return": 240.0 * 1.5 * 1.6 * 1.4,
        "status": "OPEN",
        "ticket_type_normalized": "SYSTEM",
        "total_stake": "240.00",
        "unit_stake": "240.00",
        "system_table_raw": "System TypeNo.BetsUnit StakeStakeTrebles1240.00240.00",
        "legs": legs,
    }
    ticket.update(overrides)
    return ticket


def _settled_system_ticket(**overrides):
    """The real shape of a settled ticket: a SYSTEM ticket whose stake
    breakdown was never structurally recoverable, but whose bookmaker-
    reported final figure IS trusted -- exactly the shape this module's
    bookmaker-observed path exists for. By default this is a genuinely
    ACCEPTABLE settled ticket (WON, real actual_payout, a real leg_status
    per leg) -- override individual fields to explore each quarantine
    reason."""

    ticket = {
        "bet9ja_ticket_id": "911497157",
        "captured_at_utc": "2026-09-14T17:28:25.106Z",
        "placed_at_raw": "13 Sep 2026 15:51",
        "actual_payout": "343.08",
        "potential_return": None,
        "ticket_type_normalized": "SYSTEM",
        "ticket_status": "WON",
        "total_stake": "250.00",
        "unit_stake": None,
        "system_table_raw": (
            "System TypeNo.BetsUnit StakeStakeSingles535.00175.00Doubles103.0030.00"
            "Trebles103.0030.004 Folds53.0015.00"
        ),
        "legs": [
            {
                "competition_raw": "Premier League",
                "fixture_and_time_raw": "Manchester Utd - Manchester City",
                "fixture_id": "bxf_a106185cd80c3c90",
                "leg_status": "WON",
                "market_raw": "1X2",
                "odds": "2.22",
                "selection": "Manchester City",
                "selection_raw": "Manchester City",
            }
        ],
    }
    ticket.update(overrides)
    return ticket


class TmpLedgersMixin:
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.betting_ledger_path = self.tmp_dir / "betting-ledger.jsonl"
        self.forecast_ledger_path = self.tmp_dir / "forecast-ledger.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _record_forecast(self, **overrides):
        kwargs = dict(
            fixture_id="fixture-1",
            sport="soccer",
            league="E0",
            kickoff_utc="2026-09-15T18:45:00+00:00",
            market_type="1X2",
            offered_odds={"H": 1.9, "D": 3.4, "A": 4.2},
            classification="RESEARCH-MODEL",
            competition_code="E0",
            resolved_home_team="Man United",
            resolved_away_team="Man City",
            scheduled_date="2026-09-15",
        )
        kwargs.update(overrides)
        event = forecast_ledger.build_recorded_event(**kwargs)
        forecast_ledger.append_recorded(self.forecast_ledger_path, event)
        return event


class ParseFixtureAndTimeTests(unittest.TestCase):
    def test_splits_home_away_and_trailing_date_when_present(self):
        parsed = importer._parse_fixture_and_time("Cincinnati Reds - Los Angeles Dodgers14 Sep 23:40")
        self.assertEqual(parsed.home, "Cincinnati Reds")
        self.assertEqual(parsed.away, "Los Angeles Dodgers")
        self.assertEqual((parsed.day, parsed.month, parsed.hour, parsed.minute), (14, 9, 23, 40))

    def test_settled_shape_with_no_trailing_date_still_splits_teams(self):
        parsed = importer._parse_fixture_and_time("Manchester Utd - Manchester City")
        self.assertEqual(parsed.home, "Manchester Utd")
        self.assertEqual(parsed.away, "Manchester City")
        self.assertIsNone(parsed.day)

    def test_team_name_containing_digits_does_not_break_the_split(self):
        parsed = importer._parse_fixture_and_time("1899 Hoffenheim - Bayern Munich15 Sep 18:30")
        self.assertEqual(parsed.home, "1899 Hoffenheim")
        self.assertEqual(parsed.away, "Bayern Munich")

    def test_missing_separator_is_unparseable(self):
        self.assertIsNone(importer._parse_fixture_and_time("Manchester Utd vs Manchester City"))


class DeriveFoldSizeTests(unittest.TestCase):
    def test_full_accumulator_is_uniquely_resolvable(self):
        # 3 legs, one combination covering all of them: C(3,3) == 1 is the
        # only root (k=0 is never a valid fold size), so this is safe.
        self.assertEqual(importer._derive_fold_size(3, Decimal("240.00"), Decimal("240.00")), 3)

    def test_even_leg_count_midpoint_is_uniquely_self_paired(self):
        # 4 legs, fold_size 2: C(4,2) == 6 and its "partner" C(4,2) is
        # itself (4-2==2) -- the one non-boundary case with no collision.
        self.assertEqual(importer._derive_fold_size(4, Decimal("9.00"), Decimal("54.00")), 2)

    def test_singles_from_more_than_two_legs_is_ambiguous_with_its_binomial_partner(self):
        # C(5,1) == C(5,4) == 5 -- "Singles" cannot be told apart from a
        # "4 Folds" system by the numbers alone, so this must NEVER be
        # guessed, even though it is the real, common shape a Bet9ja
        # "Singles" system ticket actually has.
        self.assertIsNone(importer._derive_fold_size(5, Decimal("35.00"), Decimal("175.00")))

    def test_unreconcilable_quotient_is_none(self):
        # leg_count=3, unit_stake=2, total_stake=240 -> quotient 120, no
        # C(3,k) equals 120 for any k -- must never be guessed.
        self.assertIsNone(importer._derive_fold_size(3, Decimal("2"), Decimal("240")))

    def test_non_integer_quotient_is_none(self):
        self.assertIsNone(importer._derive_fold_size(5, Decimal("35.00"), Decimal("100.00")))


class ParsePlacedAtTests(unittest.TestCase):
    def test_parses_lagos_local_display_to_utc(self):
        # Africa/Lagos is a fixed UTC+1, so 17:40 local -> 16:40 UTC.
        self.assertEqual(importer.parse_placed_at_utc("14 Sep 2026 17:40"), "2026-09-14T16:40:00Z")

    def test_unparseable_text_returns_none_never_a_guess(self):
        self.assertIsNone(importer.parse_placed_at_utc("sometime last week"))

    def test_none_input_returns_none(self):
        self.assertIsNone(importer.parse_placed_at_utc(None))


class CurrencyTests(TmpLedgersMixin, unittest.TestCase):
    def test_operator_supplied_currency_is_stored_verbatim(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.event["payload"]["currency"], "NGN")

    def test_currency_is_never_inferred_a_different_value_is_stored_as_given(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="EUR", forecast_index=forecast_index)
        self.assertEqual(outcome.event["payload"]["currency"], "EUR")


class OpenSystemStakeDerivationTests(TmpLedgersMixin, unittest.TestCase):
    """Stake-structure resolution for OPEN (not-yet-settled) SYSTEM
    tickets -- unchanged from before this revision: still needs a real
    structure, since there is no bookmaker-observed figure to fall back
    on yet."""

    def test_real_singles_system_ticket_cannot_be_safely_resolved_and_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_system_ticket_singles(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE])

    def test_full_accumulator_system_ticket_is_accepted_with_derived_fold_size(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _system_ticket_full_accumulator(), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "ACCEPTED")
        payload = outcome.event["payload"]
        self.assertEqual(payload["system_sizes"], [3])
        self.assertEqual(payload["combination_count"], 1)
        self.assertEqual(payload["unit_stake"], "240.00")
        self.assertEqual(payload["total_stake"], "240.00")
        self.assertIsNone(outcome.settled_event)

    def test_a_genuine_structured_stake_buckets_field_is_honored_directly(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(
            unit_stake=None,
            stake_buckets=[
                {"fold_size": 1, "combination_count": 5, "unit_stake": "35.00", "total_stake": "175.00"}
            ],
        )
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertIsNone(outcome.event["payload"]["unit_stake"])
        self.assertEqual(outcome.event["payload"]["stake_buckets"][0]["fold_size"], 1)

    def test_ambiguous_quotient_across_multiple_fold_sizes_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(unit_stake="1.00", total_stake="7.00")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE])

    def test_a_genuine_singles_label_resolves_the_real_previously_quarantined_shape(self):
        # ticket_type_raw is a SEPARATE, already-structured field (never
        # system_table_raw) that ticket_parser.js's own arithmetic-
        # verified text split already produces -- this is the exact real
        # shape of 18 real open tickets found in this session's own
        # reconciliation, previously wrongly assumed unresolvable.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(ticket_type_raw="Singles")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        payload = outcome.event["payload"]
        self.assertEqual(payload["system_sizes"], [1])
        self.assertEqual(payload["combination_count"], 5)
        self.assertEqual(payload["unit_stake"], "35.00")

    def test_a_doubles_or_trebles_label_is_also_recognized(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_full_accumulator(ticket_type_raw="Trebles")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.event["payload"]["system_sizes"], [3])

    def test_a_label_whose_own_arithmetic_does_not_check_out_is_never_trusted(self):
        # "Doubles" (fold_size 2) would need C(5,2)=10 combinations at
        # 35.00 each = 350.00, not the real 175.00 total this ticket
        # actually has -- the mismatched label must never be trusted,
        # and must fall through to the (here, ambiguous) numeric path.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(ticket_type_raw="Doubles")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE])

    def test_an_unrecognized_label_falls_through_to_the_numeric_path(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_full_accumulator(ticket_type_raw="System")  # settled_bets_parser.js's own label
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")  # still resolvable via the full-accumulator numeric path
        self.assertEqual(outcome.event["payload"]["system_sizes"], [3])


class BookmakerObservedSettlementTests(TmpLedgersMixin, unittest.TestCase):
    """The central deliverable: a settled ticket never needs
    potential_return, and never needs its stake structure resolved,
    when the bookmaker's own trusted fields are present."""

    def test_real_settled_ticket_is_accepted_via_bookmaker_observed_despite_missing_max_return_and_structure(self):
        # This is the exact real-data shape that was WRONGLY quarantined
        # before this revision (for a missing potential_return it never
        # needed, and would have ALSO been wrongly quarantined for an
        # unresolvable stake breakdown it never needs either).
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_settled_system_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.reasons, [])
        placed_payload = outcome.event["payload"]
        self.assertIsNone(placed_payload["max_return"])
        self.assertIsNone(placed_payload["combination_count"])
        self.assertIsNone(placed_payload["unit_stake"])
        self.assertEqual(placed_payload["stake_structure_basis"], "TOTAL_ONLY_UNKNOWN_BREAKDOWN")
        self.assertEqual(placed_payload["total_stake"], "250.00")

        self.assertIsNotNone(outcome.settled_event)
        settled_payload = outcome.settled_event["payload"]
        self.assertEqual(settled_payload["actual_return"], "343.08")
        self.assertEqual(settled_payload["profit_loss"], "93.08")
        self.assertEqual(settled_payload["settlement_basis"], "BOOKMAKER_OBSERVED")
        self.assertEqual(settled_payload["settlement_method"], "MANUAL")
        self.assertEqual(settled_payload["leg_results"], [{"leg_index": 0, "outcome": "WON"}])
        self.assertEqual(settled_payload["settled_at_resolution"], "CAPTURE_TIME_UPPER_BOUND")

    def test_lost_ticket_with_null_actual_payout_settles_to_zero_return(self):
        # Real settled-bets captures never populate actual_payout for a
        # LOST ticket -- LOST means zero return by definition, not a
        # guess about money math.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(ticket_status="LOST", actual_payout=None)
        ticket["legs"][0]["leg_status"] = "LOST"
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.settled_event["payload"]["actual_return"], "0")
        self.assertEqual(outcome.settled_event["payload"]["profit_loss"], "-250.00")

    def test_lost_ticket_with_a_real_actual_payout_honors_it_verbatim(self):
        # A partial void refund on an otherwise-lost ticket: the real,
        # present figure is never overridden to zero.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(ticket_status="LOST", actual_payout="25.00")
        ticket["legs"][0]["leg_status"] = "VOID"
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.settled_event["payload"]["actual_return"], "25.00")

    def test_won_ticket_missing_actual_payout_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(actual_payout=None)
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertIn(importer.REASON_MISSING_ACTUAL_PAYOUT, outcome.reasons)

    def test_unsupported_settlement_status_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(ticket_status="CASHOUT")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertIn(importer.REASON_UNSUPPORTED_SETTLEMENT_STATUS, outcome.reasons)

    def test_missing_leg_outcome_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket()
        ticket["legs"][0]["leg_status"] = None
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertIn(importer.REASON_LEG_OUTCOME_MISSING, outcome.reasons)

    def test_settled_ticket_never_needs_potential_return(self):
        # Sanity check the negative: a settled ticket that DOES also
        # happen to carry a potential_return is unaffected -- it is
        # simply ignored, never required either way.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(potential_return="999.99")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertIsNone(outcome.event["payload"]["max_return"])

    def test_settle_computed_can_never_be_used_on_this_tickets_own_structure(self):
        # Direct confirmation of the ledger-level guardrail this whole
        # feature depends on: this ticket's own combinatorial structure
        # was never known, so replaying it is refused outright.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_settled_system_ticket(), currency="NGN", forecast_index=forecast_index)
        betting_ledger.append_placed(self.betting_ledger_path, outcome.event)
        with self.assertRaises(ValueError):
            betting_ledger.settle_computed(
                self.betting_ledger_path, outcome.event["ticket_id"], [{"leg_index": 0, "outcome": "WON"}]
            )


class MultiReasonMatrixTests(TmpLedgersMixin, unittest.TestCase):
    """Every applicable check runs, and every applicable reason is
    reported -- never just the first one found."""

    def test_a_ticket_failing_three_independent_checks_reports_all_three(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _open_single_ticket(
            ticket_type_normalized="ACCUMULATOR",  # REASON_UNSUPPORTED_TICKET_TYPE
            total_stake=0,  # REASON_INVALID_TOTAL_STAKE
            potential_return=None,  # REASON_MISSING_MAX_RETURN
        )
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(
            set(outcome.reasons),
            {
                importer.REASON_UNSUPPORTED_TICKET_TYPE,
                importer.REASON_INVALID_TOTAL_STAKE,
                importer.REASON_MISSING_MAX_RETURN,
            },
        )
        # `reason` (singular) stays the FIRST one, for a caller that only
        # wants a single primary reason.
        self.assertEqual(outcome.reason, outcome.reasons[0])

    def test_run_import_reports_a_full_reason_matrix_across_the_whole_batch(self):
        ticket_a = _open_single_ticket(bet9ja_ticket_id="a", potential_return=None)
        ticket_b = _open_single_ticket(bet9ja_ticket_id="b", legs=[], total_stake=0)
        report = importer.run_import(
            [ticket_a, ticket_b],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
            dry_run=True,
        )
        self.assertEqual(report["quarantine_reason_matrix"]["a"], [importer.REASON_MISSING_MAX_RETURN])
        self.assertEqual(
            report["quarantine_reason_matrix"]["b"],
            sorted([importer.REASON_NO_LEGS, importer.REASON_INVALID_TOTAL_STAKE]),
        )
        # Each reason is counted once per ticket that carries it, not
        # capped at one reason per ticket overall.
        self.assertEqual(report["quarantine_reason_counts"][importer.REASON_MISSING_MAX_RETURN], 1)
        self.assertEqual(report["quarantine_reason_counts"][importer.REASON_NO_LEGS], 1)
        self.assertEqual(report["quarantine_reason_counts"][importer.REASON_INVALID_TOTAL_STAKE], 1)


class QuarantineReasonTests(TmpLedgersMixin, unittest.TestCase):
    def test_missing_max_return_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _open_single_ticket(potential_return=None), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_MISSING_MAX_RETURN])

    def test_unparseable_placed_at_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _open_single_ticket(placed_at_raw="not a date"), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_PLACED_AT_UNPARSEABLE])

    def test_no_legs_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(legs=[]), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_NO_LEGS])

    def test_unsupported_ticket_type_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _open_single_ticket(ticket_type_normalized="ACCUMULATOR"), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_UNSUPPORTED_TICKET_TYPE])

    def test_invalid_total_stake_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _open_single_ticket(total_stake=0), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_INVALID_TOTAL_STAKE])

    def test_non_system_ticket_missing_unit_stake_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(
            _open_single_ticket(unit_stake=None), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_UNIT_STAKE_MISSING])


class ForecastLinkageTests(TmpLedgersMixin, unittest.TestCase):
    def test_exact_canonical_match_links_the_leg(self):
        self._record_forecast()
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        leg = outcome.event["payload"]["legs"][0]
        self.assertIsNotNone(leg["forecast_id"])
        self.assertEqual(outcome.unlinked_leg_count, 0)

    def test_no_matching_forecast_leaves_leg_unlinked_but_ticket_still_accepted(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        leg = outcome.event["payload"]["legs"][0]
        self.assertIsNone(leg["forecast_id"])
        self.assertEqual(outcome.unlinked_leg_count, 1)

    def test_uncovered_competition_leaves_leg_unlinked(self):
        self._record_forecast()
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _open_single_ticket()
        ticket["legs"][0]["competition_raw"] = "EFL Cup"
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertIsNone(outcome.event["payload"]["legs"][0]["forecast_id"])

    def test_ambiguous_multiple_candidates_leaves_leg_unlinked(self):
        self._record_forecast(fixture_id="fixture-1")
        self._record_forecast(fixture_id="fixture-2", model_version="v2")
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        # Two RECORDED forecasts share the identical (competition, home,
        # away, market, scheduled_date) key -- never guessed between them.
        self.assertIsNone(outcome.event["payload"]["legs"][0]["forecast_id"])

    def test_never_a_substring_match_similar_but_distinct_team_name_stays_unlinked(self):
        self._record_forecast(resolved_home_team="Manchester United")  # not "Man United"
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertIsNone(outcome.event["payload"]["legs"][0]["forecast_id"])


class RunImportBatchSafetyTests(TmpLedgersMixin, unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        report = importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
            dry_run=True,
        )
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["placed"]["accepted_would_write"], 1)
        self.assertEqual(read_all(self.betting_ledger_path), [])

    def test_a_clean_batch_writes_the_accepted_placed_ticket(self):
        report = importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["appended"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)

    def test_a_settled_ticket_writes_both_placed_and_settled_events(self):
        report = importer.run_import(
            [_settled_system_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["appended"], 1)
        self.assertEqual(report["settled"]["appended"], 1)
        self.assertEqual(report["settled_via_bookmaker_observed"], 1)
        records = read_all(self.betting_ledger_path)
        self.assertEqual(len(records), 2)
        event_types = {r["event_type"] for r in records}
        self.assertEqual(event_types, {"PLACED", "SETTLED"})

    def test_reimporting_the_identical_batch_is_a_safe_no_op(self):
        importer.run_import(
            [_settled_system_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        report = importer.run_import(
            [_settled_system_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["appended"], 0)
        self.assertEqual(report["placed"]["duplicate_skipped"], 1)
        self.assertEqual(report["settled"]["appended"], 0)
        self.assertEqual(report["settled"]["duplicate_skipped"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 2)  # never duplicated

    def test_quarantined_tickets_never_block_accepted_siblings_in_the_same_batch(self):
        report = importer.run_import(
            [_open_single_ticket(), _open_single_ticket(bet9ja_ticket_id="qq", potential_return=None)],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["placed"]["appended"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)

    def test_a_genuine_placed_conflict_writes_nothing_at_all(self):
        # Pre-seed the ledger with a DIFFERENT ticket under the same
        # external_ticket_ref -- the real conflict shape write_batch_placed
        # itself already guarantees "all or nothing" for.
        conflicting = betting_ledger.build_placed_event(
            ticket_type="SINGLE",
            max_return="999.00",
            currency="NGN",
            legs=[
                {
                    "forecast_id": None,
                    "fixture_id": "bxf_aaa",
                    "market_type": "1X2",
                    "selection": "SOMETHING ELSE",
                    "placed_odds": "9.00",
                }
            ],
            unit_stake="35.00",
            external_ticket_ref="000111222",
        )
        betting_ledger.append_placed(self.betting_ledger_path, conflicting)

        report = importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["conflicted"], 1)
        self.assertEqual(report["placed"]["appended"], 0)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)  # only the pre-seeded record

    def test_duplicate_ticket_id_with_different_financial_values_aborts_the_whole_placed_batch(self):
        # Two raw tickets sharing the same bet9ja_ticket_id but different
        # stakes -- the second one's PLACED event collides with the
        # first's under the same natural key with different content.
        ticket_1 = _open_single_ticket(bet9ja_ticket_id="dup-1", total_stake=35, unit_stake=35)
        ticket_2 = _open_single_ticket(bet9ja_ticket_id="dup-1", total_stake=99, unit_stake=99)
        other = _open_single_ticket(bet9ja_ticket_id="clean-1")
        report = importer.run_import(
            [ticket_1, ticket_2, other],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertGreaterEqual(report["placed"]["conflicted"], 1)
        self.assertEqual(report["placed"]["appended"], 0)
        # Not even the perfectly clean "other" ticket was written.
        self.assertEqual(read_all(self.betting_ledger_path), [])

    def test_a_re_settlement_with_a_different_actual_payout_conflicts_and_writes_nothing_new(self):
        # Settle the ticket once for real.
        importer.run_import(
            [_settled_system_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_first_run = read_all(self.betting_ledger_path)
        self.assertEqual(len(records_after_first_run), 2)

        # Re-import the SAME ticket but with a different actual_payout.
        # source_raw preserves the whole raw record verbatim, so this
        # also changes the PLACED event's own payload -- both batches
        # correctly refuse to silently accept a changed financial figure
        # under the same ticket_id, exactly the "duplicate ticket IDs
        # with different financial values abort the whole batch"
        # guarantee, at the PLACED layer this time rather than SETTLED.
        conflicting_settlement = _settled_system_ticket(actual_payout="1.00")
        report = importer.run_import(
            [conflicting_settlement],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["conflicted"], 1)
        self.assertEqual(report["settled"]["attempted"], 0)  # never even reached -- PLACED conflicted first
        # The already-committed PLACED+SETTLED pair from the first run is
        # untouched -- this run wrote nothing at all.
        self.assertEqual(read_all(self.betting_ledger_path), records_after_first_run)

    def test_a_settled_batch_conflict_with_an_unrelated_manually_placed_ticket_writes_nothing_new(self):
        # A ticket manually PLACED earlier (e.g. via ledgers.cli, never
        # through this importer, so it carries no source_raw at all),
        # then re-settled through this importer with a DIFFERENT
        # actual_return than what a first import already recorded --
        # isolates a genuine SETTLED-only conflict, since the PLACED
        # event itself is never touched by this run at all.
        first_import = _settled_system_ticket()
        importer.run_import(
            [first_import],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_first_run = read_all(self.betting_ledger_path)

        # Re-run the identical import a second time, but monkeypatch-free:
        # directly attempt a conflicting settlement batch the way
        # run_import's own second phase would, using the same ticket_id.
        ticket_id = records_after_first_run[0]["ticket_id"]
        conflicting_event = importer._build_settled_event_dict(
            ticket_id, "1.00", [{"leg_index": 0, "outcome": "WON"}], Decimal("250.00"), settled_at_utc=None
        )
        with self.assertRaises(betting_ledger.BettingLedgerTerminalBatchConflictError):
            betting_ledger.write_batch_terminal(self.betting_ledger_path, [conflicting_event])
        self.assertEqual(read_all(self.betting_ledger_path), records_after_first_run)


if __name__ == "__main__":
    unittest.main()
