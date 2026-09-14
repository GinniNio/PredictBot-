"""Tests for pcbf_calculator.orchestration.bet9ja_ticket_import: currency
handling, structured system-stake derivation (never system_table_raw
parsing), exact-canonical-identity forecast linkage, and whole-batch
import safety."""

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
    here, and exercised as a QUARANTINED case, specifically to document
    that this real, previously-assumed-"valid" shape still cannot safely
    clear today's importer without a genuine structured ``stake_buckets``
    field from a fixed browser capture (path 1)."""

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


def _settled_system_ticket_unresolvable(**overrides):
    """The real shape of a settled ticket lacking both a structured
    stake_buckets field and a usable scalar unit_stake -- must be
    quarantined, never guessed from system_table_raw."""

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


class BuildTicketEventCurrencyTests(TmpLedgersMixin, unittest.TestCase):
    def test_operator_supplied_currency_is_stored_verbatim(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(outcome.event["payload"]["currency"], "NGN")

    def test_currency_is_never_inferred_a_different_value_is_stored_as_given(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="EUR", forecast_index=forecast_index)
        self.assertEqual(outcome.event["payload"]["currency"], "EUR")


class SystemStakeDerivationImportTests(TmpLedgersMixin, unittest.TestCase):
    def test_real_singles_system_ticket_cannot_be_safely_resolved_and_is_quarantined(self):
        # See _system_ticket_singles's own docstring: C(5,1) == C(5,4),
        # so this real shape stays quarantined until a fixed capture
        # supplies a genuine stake_buckets field (path 1).
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_system_ticket_singles(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)

    def test_full_accumulator_system_ticket_is_accepted_with_derived_fold_size(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _system_ticket_full_accumulator(), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "ACCEPTED")
        payload = outcome.event["payload"]
        self.assertEqual(payload["system_sizes"], [3])
        self.assertEqual(payload["combination_count"], 1)
        self.assertEqual(payload["unit_stake"], "240.00")
        self.assertEqual(payload["total_stake"], "240.00")

    def test_settled_ticket_with_no_derivable_fold_size_is_quarantined(self):
        # The real settled-bets capture also lacks potential_return
        # (max_return) entirely -- add one here so this test isolates the
        # stake-breakdown check specifically, not the separate
        # missing-max-return check (covered on its own below).
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _settled_system_ticket_unresolvable(potential_return="343.08"), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)

    def test_real_settled_ticket_as_actually_captured_is_quarantined_for_missing_max_return(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _settled_system_ticket_unresolvable(), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_MISSING_MAX_RETURN)

    def test_a_genuine_structured_stake_buckets_field_is_honored_directly(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(
            unit_stake=None,
            stake_buckets=[
                {"fold_size": 1, "combination_count": 5, "unit_stake": "35.00", "total_stake": "175.00"}
            ],
        )
        outcome = importer.build_ticket_event(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertIsNone(outcome.event["payload"]["unit_stake"])
        self.assertEqual(outcome.event["payload"]["stake_buckets"][0]["fold_size"], 1)

    def test_ambiguous_quotient_across_multiple_fold_sizes_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _system_ticket_singles(unit_stake="1.00", total_stake="5.00")
        # 5 legs, quotient 5 -> only C(5,1)=5 matches (unambiguous), so
        # sanity-check the OPPOSITE: force an unreconcilable quotient by
        # using a stake that matches no C(5,k) at all.
        ticket["unit_stake"] = "1.00"
        ticket["total_stake"] = "7.00"
        outcome = importer.build_ticket_event(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE)


class QuarantineReasonTests(TmpLedgersMixin, unittest.TestCase):
    def test_missing_max_return_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _open_single_ticket(potential_return=None), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_MISSING_MAX_RETURN)

    def test_unparseable_placed_at_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _open_single_ticket(placed_at_raw="not a date"), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_PLACED_AT_UNPARSEABLE)

    def test_no_legs_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(legs=[]), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_NO_LEGS)

    def test_unsupported_ticket_type_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _open_single_ticket(ticket_type_normalized="ACCUMULATOR"), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_UNSUPPORTED_TICKET_TYPE)

    def test_invalid_total_stake_is_quarantined(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(
            _open_single_ticket(total_stake=0), currency="NGN", forecast_index=forecast_index
        )
        self.assertEqual(outcome.status, "QUARANTINED")
        self.assertEqual(outcome.reason, importer.REASON_INVALID_TOTAL_STAKE)


class ForecastLinkageTests(TmpLedgersMixin, unittest.TestCase):
    def test_exact_canonical_match_links_the_leg(self):
        self._record_forecast()
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        leg = outcome.event["payload"]["legs"][0]
        self.assertIsNotNone(leg["forecast_id"])
        self.assertEqual(outcome.unlinked_leg_count, 0)

    def test_no_matching_forecast_leaves_leg_unlinked_but_ticket_still_accepted(self):
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        self.assertEqual(outcome.status, "ACCEPTED")
        leg = outcome.event["payload"]["legs"][0]
        self.assertIsNone(leg["forecast_id"])
        self.assertEqual(outcome.unlinked_leg_count, 1)

    def test_uncovered_competition_leaves_leg_unlinked(self):
        self._record_forecast()
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        ticket = _open_single_ticket()
        ticket["legs"][0]["competition_raw"] = "EFL Cup"
        outcome = importer.build_ticket_event(ticket, currency="NGN", forecast_index=forecast_index)
        self.assertIsNone(outcome.event["payload"]["legs"][0]["forecast_id"])

    def test_ambiguous_multiple_candidates_leaves_leg_unlinked(self):
        self._record_forecast(fixture_id="fixture-1")
        self._record_forecast(fixture_id="fixture-2", model_version="v2")
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
        # Two RECORDED forecasts share the identical (competition, home,
        # away, market, scheduled_date) key -- never guessed between them.
        self.assertIsNone(outcome.event["payload"]["legs"][0]["forecast_id"])

    def test_never_a_substring_match_similar_but_distinct_team_name_stays_unlinked(self):
        self._record_forecast(resolved_home_team="Manchester United")  # not "Man United"
        forecast_index = importer.ForecastIndex.build(self.forecast_ledger_path)
        outcome = importer.build_ticket_event(_open_single_ticket(), currency="NGN", forecast_index=forecast_index)
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
        self.assertEqual(report["appended"], 0)
        self.assertEqual(read_all(self.betting_ledger_path), [])

    def test_a_clean_batch_writes_the_accepted_tickets(self):
        report = importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["appended"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)

    def test_reimporting_the_identical_batch_is_a_safe_no_op(self):
        importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        report = importer.run_import(
            [_open_single_ticket()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["appended"], 0)
        self.assertEqual(report["duplicate_skipped"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)

    def test_quarantined_tickets_never_block_accepted_siblings_in_the_same_batch(self):
        report = importer.run_import(
            [_open_single_ticket(), _settled_system_ticket_unresolvable()],
            currency="NGN",
            betting_ledger_path=self.betting_ledger_path,
            forecast_ledger_path=self.forecast_ledger_path,
        )
        self.assertEqual(report["accepted"], 1)
        self.assertEqual(report["quarantined"], 1)
        self.assertEqual(report["appended"], 1)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)

    def test_a_genuine_conflict_writes_nothing_at_all(self):
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
        self.assertEqual(report["conflicted"], 1)
        self.assertEqual(report["appended"], 0)
        self.assertEqual(len(read_all(self.betting_ledger_path)), 1)  # only the pre-seeded record


if __name__ == "__main__":
    unittest.main()
