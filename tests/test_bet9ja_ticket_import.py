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

    def test_no_raw_ticket_field_is_ever_read_for_currency(self):
        # A raw ticket carrying its OWN (bogus) currency-shaped field is
        # completely ignored -- currency is exclusively the operator's
        # own --currency argument, applied identically to every ticket.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _open_single_ticket(currency="USD", currency_raw="USD")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.event["payload"]["currency"], "NGN")

    def test_currency_is_identical_across_every_accepted_ticket_in_one_batch(self):
        # One run_import call takes exactly one --currency value -- it is
        # structurally impossible for two tickets in the same batch to
        # end up with different currencies, since no raw ticket field is
        # ever consulted for it (see the test above).
        report = importer.run_import(
            [_open_single_ticket(bet9ja_ticket_id="c1"), _open_single_ticket(bet9ja_ticket_id="c2")],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["accepted"], 2)
        currencies = {r["payload"]["currency"] for r in read_all(self.betting_ledger_path)}
        self.assertEqual(currencies, {"NGN"})


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

    def test_stake_buckets_disagreeing_with_the_tickets_own_total_stake_is_quarantined(self):
        # build_placed_event derives the PLACED total_stake from the
        # buckets' own sum -- a raw ticket whose separately-captured
        # top-level total_stake disagrees with that sum must never be
        # silently accepted with the buckets' figure quietly winning.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(
            unit_stake=None,
            total_stake="999999.00",  # wildly disagrees with the buckets below
            stake_buckets=[
                {"fold_size": 1, "combination_count": 5, "unit_stake": "35.00", "total_stake": "175.00"}
            ],
        )
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reasons, [importer.REASON_STAKE_BUCKETS_TOTAL_MISMATCH])

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

    def test_void_ticket_status_is_also_unsupported_and_quarantined(self):
        # A whole-ticket VOID (e.g. a postponed match voiding the whole
        # slip) is not one of the two real statuses this session's own
        # capture ever produces ({"WON", "LOST"}) -- never silently
        # treated as either.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(ticket_status="VOID")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertIn(importer.REASON_UNSUPPORTED_SETTLEMENT_STATUS, outcome.reasons)

    def test_partial_ticket_status_is_also_unsupported_and_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _settled_system_ticket(ticket_status="PARTIAL")
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertIn(importer.REASON_UNSUPPORTED_SETTLEMENT_STATUS, outcome.reasons)

    def test_mixed_leg_outcomes_never_recompute_the_trusted_payout(self):
        # A real, multi-leg SYSTEM ticket with a genuine MIX of WON/LOST/
        # VOID legs -- leg_results is recorded verbatim for audit, but
        # actual_return is the bookmaker's own figure, NEVER a naive
        # recombination of these leg outcomes. Deliberately choose an
        # actual_payout that a per-leg recompute (impossible here anyway,
        # since the fold-size structure is unknown) could never produce,
        # to prove nothing is silently re-derived from leg_results.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        legs = [
            {
                "competition_raw": "Premier League",
                "fixture_and_time_raw": f"Team{i}A - Team{i}B",
                "fixture_id": f"bxf_{i}",
                "leg_status": status,
                "market_raw": "1X2",
                "odds": "2.00",
                "selection": "H",
                "selection_raw": "H",
            }
            for i, status in enumerate(["WON", "LOST", "VOID", "WON"])
        ]
        ticket = _settled_system_ticket(actual_payout="12345.67", legs=legs)
        outcome = importer.evaluate_ticket(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        settled_payload = outcome.settled_event["payload"]
        self.assertEqual(settled_payload["actual_return"], "12345.67")
        self.assertEqual(settled_payload["settlement_basis"], "BOOKMAKER_OBSERVED")
        self.assertEqual(
            settled_payload["leg_results"],
            [
                {"leg_index": 0, "outcome": "WON"},
                {"leg_index": 1, "outcome": "LOST"},
                {"leg_index": 2, "outcome": "VOID"},
                {"leg_index": 3, "outcome": "WON"},
            ],
        )
        # The PLACED event's own structure is honestly unknown -- never
        # derived from these leg outcomes either.
        self.assertEqual(outcome.event["payload"]["stake_structure_basis"], "TOTAL_ONLY_UNKNOWN_BREAKDOWN")
        self.assertIsNone(outcome.event["payload"]["combination_count"])

    def test_profit_equals_trusted_payout_minus_trusted_total_stake_exactly_once(self):
        # End-to-end through run_import + the real ledger's own
        # current_state -- profit_loss is stored, never recomputed by
        # any downstream reader (ledgers/summary.py sums this SAME
        # stored field once per ticket_id; it never re-subtracts).
        report = importer.run_import(
            [_settled_system_ticket(actual_payout="343.08")],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["settled"]["appended"], 1)
        ticket_id = read_all(self.betting_ledger_path)[0]["ticket_id"]
        state = betting_ledger.current_state(self.betting_ledger_path, ticket_id)
        actual_return_d = Decimal(state["actual_return"])
        total_stake_d = Decimal(state["total_stake"])
        profit_loss_d = Decimal(state["profit_loss"])
        self.assertEqual(actual_return_d, Decimal("343.08"))
        self.assertEqual(total_stake_d, Decimal("250.00"))
        self.assertEqual(profit_loss_d, actual_return_d - total_stake_d)
        self.assertEqual(profit_loss_d, Decimal("93.08"))

    def test_null_max_return_does_not_affect_settlement_or_ledger_totals(self):
        # A settled ticket's max_return is always null (see the PLACED
        # payload assertion in the first test in this class) -- confirm
        # this null never leaks into, or is substituted for, any real
        # money figure: total_stake/actual_return/profit_loss are
        # unaffected by its presence or absence.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        with_null = _settled_system_ticket(actual_payout="343.08")
        without = _settled_system_ticket(actual_payout="343.08")
        del without["potential_return"]  # not even present, vs. explicitly null in the default fixture
        outcome_a = importer.evaluate_ticket(with_null, currency="NGN", forecast_index=forecast_index)
        outcome_b = importer.evaluate_ticket(without, currency="NGN", forecast_index=forecast_index)
        for outcome in (outcome_a, outcome_b):
            self.assertEqual(outcome.status, "ACCEPTED")
            self.assertIsNone(outcome.event["payload"]["max_return"])
            self.assertEqual(outcome.event["payload"]["total_stake"], "250.00")
            self.assertEqual(outcome.settled_event["payload"]["actual_return"], "343.08")
            self.assertEqual(outcome.settled_event["payload"]["profit_loss"], "93.08")

    def test_settle_computed_is_structurally_incapable_of_running_on_a_total_only_ticket(self):
        # Belt-and-braces confirmation (see also
        # test_settle_computed_can_never_be_used_on_this_tickets_own_structure
        # below): TOTAL_ONLY_UNKNOWN_BREAKDOWN's own combination_count/
        # system_sizes are null in the payload, so even if some future
        # caller mistakenly tried settle_computed against this ticket, it
        # has no combinatorial structure to iterate over at all.
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.evaluate_ticket(_settled_system_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertIsNone(outcome.event["payload"]["system_sizes"] if "system_sizes" in outcome.event["payload"] else None)
        self.assertNotIn("stake_buckets", outcome.event["payload"])

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
        # The ticket is already PLACED after the first run -- the second
        # import recognizes that and settles directly against it,
        # attempting NO new PLACED build/write at all (see
        # evaluate_ticket's own "already_placed" settle-only path), so
        # settled duplicate_skipped is where this no-op actually shows.
        self.assertEqual(report["placed"]["attempted"], 0)
        self.assertEqual(report["placed"]["appended"], 0)
        self.assertEqual(report["settled"]["appended"], 0)
        self.assertEqual(report["settled"]["duplicate_skipped"], 1)
        self.assertEqual(report["settled_against_pre_existing_placement"], 1)
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
        # The ticket is already PLACED, so this takes the settle-only
        # path (no new PLACED event attempted at all -- ticket_type/
        # total_stake/legs/currency all still agree with what's on
        # record, so the mismatch guard does not fire either); the
        # SETTLED batch itself is what correctly refuses the changed
        # financial figure under the same ticket_id.
        conflicting_settlement = _settled_system_ticket(actual_payout="1.00")
        report = importer.run_import(
            [conflicting_settlement],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["placed"]["attempted"], 0)
        self.assertEqual(report["settled"]["conflicted"], 1)
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


def _settled_version_of(open_ticket, *, actual_payout, ticket_status="WON", leg_status="WON"):
    """The SAME physical ticket (same bet9ja_ticket_id/legs/total_stake),
    captured again once it has settled -- exactly the shape
    settled_bets_parser.js produces for a ticket ticket_parser.js already
    captured while open: no unit_stake/potential_return, a real
    ticket_status/actual_payout, a leg_status per leg."""

    settled = dict(open_ticket)
    settled.update(
        {
            "actual_payout": actual_payout,
            "potential_return": None,
            "ticket_status": ticket_status,
            "unit_stake": None,
        }
    )
    settled["legs"] = [dict(leg, leg_status=leg_status) for leg in open_ticket["legs"]]
    return settled


class LifecycleTransitionTests(TmpLedgersMixin, unittest.TestCase):
    """A real ticket's own two-phase life: captured OPEN (with a real,
    resolvable stake structure), then captured again once SETTLED. The
    two captures share one ticket_id but have very different shapes --
    this must never be treated as a spurious PLACED conflict."""

    def _open_ticket(self):
        return _system_ticket_full_accumulator(bet9ja_ticket_id="LIFECYCLE-1")

    def test_open_then_settled_appends_a_valid_terminal_event_without_a_placed_conflict(self):
        open_ticket = self._open_ticket()
        report1 = importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report1["placed"]["appended"], 1)
        self.assertEqual(report1["accepted"], 1)

        settled_ticket = _settled_version_of(open_ticket, actual_payout="806.40")
        report2 = importer.run_import(
            [settled_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report2["accepted"], 1)
        self.assertEqual(report2["quarantined"], 0)
        # No new PLACED event was attempted at all -- and definitely not
        # a conflict merely because the ticket_id already existed.
        self.assertEqual(report2["placed"]["attempted"], 0)
        self.assertEqual(report2["placed"]["conflicted"], 0)
        self.assertEqual(report2["settled"]["appended"], 1)
        self.assertEqual(report2["settled_against_pre_existing_placement"], 1)

        records = read_all(self.betting_ledger_path)
        self.assertEqual(len(records), 2)
        self.assertEqual({r["event_type"] for r in records}, {"PLACED", "SETTLED"})
        settled_record = next(r for r in records if r["event_type"] == "SETTLED")
        self.assertEqual(settled_record["payload"]["actual_return"], "806.40")
        self.assertEqual(settled_record["payload"]["profit_loss"], "566.40")  # 806.40 - 240.00
        self.assertEqual(settled_record["payload"]["settlement_basis"], "BOOKMAKER_OBSERVED")
        # The original PLACED event's own real, resolved structure is
        # completely untouched by the later settlement.
        placed_record = next(r for r in records if r["event_type"] == "PLACED")
        self.assertEqual(placed_record["payload"]["system_sizes"], [3])
        self.assertEqual(placed_record["payload"]["unit_stake"], "240.00")

    def test_reimporting_the_original_open_capture_after_settlement_is_refused_not_silently_replayed(self):
        # Once a ticket_id has a real terminal event on the ledger, ANY
        # later capture of it that looks OPEN is refused outright --
        # unconditionally, even one whose content is byte-identical to
        # the original placement. This is a deliberately simple, strict
        # rule (never "safe because the content happens to still
        # match"): the ledger has no un-settle operation, so an
        # open-shaped capture of an already-settled ticket_id is always
        # treated as suspicious, never silently replayed.
        open_ticket = self._open_ticket()
        importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        importer.run_import(
            [_settled_version_of(open_ticket, actual_payout="806.40")], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_settlement = read_all(self.betting_ledger_path)

        report = importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(
            report["quarantine_reason_matrix"]["LIFECYCLE-1"], [importer.REASON_ALREADY_SETTLED_CANNOT_REVERT_TO_OPEN]
        )
        self.assertEqual(read_all(self.betting_ledger_path), records_after_settlement)

    def test_reimporting_the_identical_settled_state_is_a_no_op(self):
        open_ticket = self._open_ticket()
        importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        settled_ticket = _settled_version_of(open_ticket, actual_payout="806.40")
        importer.run_import(
            [settled_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_first_settlement = read_all(self.betting_ledger_path)

        report = importer.run_import(
            [settled_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["settled"]["duplicate_skipped"], 1)
        self.assertEqual(report["settled"]["conflicted"], 0)
        self.assertEqual(read_all(self.betting_ledger_path), records_after_first_settlement)

    def test_a_differently_shaped_open_recapture_of_an_already_settled_ticket_is_explicitly_refused(self):
        open_ticket = self._open_ticket()
        importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        importer.run_import(
            [_settled_version_of(open_ticket, actual_payout="806.40")], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_settlement = read_all(self.betting_ledger_path)

        # A DIFFERENT-looking OPEN capture of the same already-settled
        # ticket_id (e.g. a stale re-scrape with a changed max_return) --
        # this is exactly the case that would otherwise slip through the
        # "byte-identical no-op" path above and needs the explicit guard.
        reverted = dict(open_ticket)
        reverted["potential_return"] = 999.0
        report = importer.run_import(
            [reverted], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(
            report["quarantine_reason_matrix"]["LIFECYCLE-1"], [importer.REASON_ALREADY_SETTLED_CANNOT_REVERT_TO_OPEN]
        )
        self.assertEqual(read_all(self.betting_ledger_path), records_after_settlement)

    def test_a_settled_recapture_that_disagrees_with_its_own_placement_is_quarantined_not_silently_settled(self):
        open_ticket = self._open_ticket()
        importer.run_import(
            [open_ticket], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        records_after_placement = read_all(self.betting_ledger_path)

        mismatched_settlement = _settled_version_of(open_ticket, actual_payout="806.40")
        mismatched_settlement["total_stake"] = "999.00"  # disagrees with the real, recorded 240.00
        report = importer.run_import(
            [mismatched_settlement], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["quarantined"], 1)
        self.assertIn(importer.REASON_SETTLEMENT_TICKET_MISMATCH, report["quarantine_reason_matrix"]["LIFECYCLE-1"])
        self.assertEqual(read_all(self.betting_ledger_path), records_after_placement)

    def test_a_settled_only_ticket_never_seen_open_still_imports_normally(self):
        # The ordinary real-data case (113/158 real tickets): no prior
        # PLACED capture exists at all -- the settle-only path must never
        # apply here, and the usual bookmaker-observed build runs as
        # before.
        report = importer.run_import(
            [_settled_system_ticket()], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["placed"]["appended"], 1)
        self.assertEqual(report["settled"]["appended"], 1)
        self.assertEqual(report["settled_against_pre_existing_placement"], 0)


class AtomicityAndCompatibilityTests(TmpLedgersMixin, unittest.TestCase):
    def test_a_mixed_open_and_settled_batch_commits_the_whole_placed_phase_atomically(self):
        # An intra-batch PLACED conflict (two OPEN tickets sharing one
        # ticket_id with different stakes) sits alongside an otherwise
        # completely clean, unrelated, first-time SETTLED ticket in the
        # SAME batch -- the settled ticket's own PLACED+SETTLED events
        # must NOT slip through just because the conflict is elsewhere in
        # the same batch: the whole mixed-shape PLACED phase is one
        # atomic unit, not per-ticket.
        dup_a = _open_single_ticket(bet9ja_ticket_id="dup-1", total_stake=35, unit_stake=35)
        dup_b = _open_single_ticket(bet9ja_ticket_id="dup-1", total_stake=99, unit_stake=99)
        clean_settled = _settled_system_ticket(bet9ja_ticket_id="settled-clean-1", actual_payout="343.08")

        report = importer.run_import(
            [dup_a, dup_b, clean_settled], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertGreaterEqual(report["placed"]["conflicted"], 1)
        self.assertEqual(report["placed"]["appended"], 0)
        # Not even the clean settled ticket's PLACED event was written --
        # and by extension its SETTLED event was never even attempted.
        self.assertEqual(read_all(self.betting_ledger_path), [])
        self.assertEqual(report["settled"]["attempted"], 0)

    def test_a_mixed_batchs_placed_phase_and_settled_phase_are_each_atomic_but_sequential(self):
        # Documents this pipeline's ACTUAL, deliberate atomicity
        # boundary: PLACED and SETTLED are two SEPARATE atomic phases,
        # not one joint transaction (see run_import's own docstring) --
        # a same-batch SETTLED-phase conflict never undoes an
        # already-committed PLACED phase. Each phase is still
        # all-or-nothing on its own.
        clean_open = _open_single_ticket(bet9ja_ticket_id="phase-clean-1")
        settled_ok = _settled_system_ticket(bet9ja_ticket_id="phase-ok-1", actual_payout="343.08")
        settled_bad_status = _settled_system_ticket(bet9ja_ticket_id="phase-bad-1", ticket_status="CASHOUT")

        report = importer.run_import(
            [clean_open, settled_ok, settled_bad_status], currency="NGN",
            betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["quarantined"], 1)  # settled_bad_status only
        self.assertEqual(report["placed"]["appended"], 2)  # clean_open + settled_ok's own PLACED
        self.assertEqual(report["settled"]["appended"], 1)  # settled_ok only

    def test_a_rejected_batch_leaves_both_ledger_and_report_byte_for_byte_reproducible(self):
        first = betting_ledger.build_placed_event(
            ticket_type="SINGLE", max_return="999.00", currency="NGN",
            legs=[{"forecast_id": None, "fixture_id": "bxf_aaa", "market_type": "1X2", "selection": "X", "placed_odds": "9.00"}],
            unit_stake="35.00", external_ticket_ref="000111222",
        )
        betting_ledger.append_placed(self.betting_ledger_path, first)
        records_before = read_all(self.betting_ledger_path)

        run_kwargs = dict(
            currency="NGN", betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path
        )
        report_1 = importer.run_import([_open_single_ticket()], **run_kwargs)
        report_2 = importer.run_import([_open_single_ticket()], **run_kwargs)
        self.assertEqual(report_1, report_2)  # byte-for-byte (structurally) identical, not just "similar"
        self.assertEqual(read_all(self.betting_ledger_path), records_before)  # ledger untouched, both times

    def test_settle_computed_still_works_on_an_old_shape_row_predating_settlement_basis(self):
        # A ledger row settled BEFORE settlement_basis existed at all
        # (settle_computed's own default -- no settlement_basis key
        # present) must still re-settle as a safe no-op today: the new
        # optional field must never turn an old, valid row into a
        # spurious conflict.
        event = betting_ledger.build_placed_event(
            ticket_type="SINGLE", unit_stake="10.00", max_return="19.00", currency="NGN",
            legs=[_leg_for_compat_test(0, "1.9")], external_ticket_ref="old-shape-1",
        )
        betting_ledger.append_placed(self.betting_ledger_path, event)
        leg_results = [{"leg_index": 0, "outcome": "WON"}]
        first = betting_ledger.settle_computed(self.betting_ledger_path, event["ticket_id"], leg_results)
        self.assertEqual(first.status, "APPENDED")
        self.assertNotIn("settlement_basis", first.record["payload"])

        second = betting_ledger.settle_computed(self.betting_ledger_path, event["ticket_id"], leg_results)
        self.assertEqual(second.status, "DUPLICATE_SKIPPED")

    def test_concurrent_imports_of_the_identical_settled_ticket_never_duplicate(self):
        # A real, OS-level fcntl.flock lock (ledgers/locking.py) serializes
        # the preflight-then-commit sequence across concurrent writers --
        # fired here from two threads racing against the SAME ledger file
        # to give real evidence, not just a reading of the lock code.
        import concurrent.futures

        ticket = _settled_system_ticket(bet9ja_ticket_id="concurrent-1", actual_payout="343.08")
        run_kwargs = dict(
            currency="NGN", betting_ledger_path=self.betting_ledger_path, forecast_ledger_path=self.forecast_ledger_path
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(importer.run_import, [ticket], **run_kwargs) for _ in range(2)]
            reports = [f.result() for f in futures]

        records = read_all(self.betting_ledger_path)
        self.assertEqual(len(records), 2)  # exactly one PLACED + one SETTLED -- never duplicated
        self.assertEqual({r["event_type"] for r in records}, {"PLACED", "SETTLED"})
        total_placed_appended = sum(r["placed"]["appended"] for r in reports)
        total_settled_appended = sum(r["settled"]["appended"] for r in reports)
        self.assertEqual(total_placed_appended, 1)  # exactly one of the two runs actually appended it
        self.assertEqual(total_settled_appended, 1)


def _leg_for_compat_test(i, odds):
    return {
        "leg_index": i,
        "forecast_id": None,
        "fixture_id": f"fixture-{i}",
        "market_type": "1X2",
        "selection": "H",
        "placed_odds": odds,
    }


if __name__ == "__main__":
    unittest.main()
