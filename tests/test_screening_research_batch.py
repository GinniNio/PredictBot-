"""Tests for the PCBF research-batch market-quality triage workflow
(``src/pcbf_calculator/screening/``).

This is a market-quality research-triage workflow, not a selection/
recommendation one: these tests exist specifically to prove that no output
this module produces ever reads as a forecast, an edge claim, or a
recommendation of any outcome — see ``research_batch.py``'s own module
docstring for the full framing. There is no "candidate" concept anywhere
in this workflow: a market either becomes a research queue item or an
excluded market.

Style matches this repo's existing convention: plain ``unittest.TestCase``,
no third-party test framework.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pcbf_calculator.screening.errors import SCREEN_MARKET_QUALITY_NOT_NORMAL
from pcbf_calculator.screening.research_batch import run_screen, screen_research_batch

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "bet9ja"

# Fields every research queue item must carry, fixed and non-configurable.
REQUIRED_STATUS_FIELDS = {
    "workflow_state": "RESEARCH_QUEUE",
    "classification_ceiling": "RESEARCH-MODEL",
    "forecast_probability_status": "NOT_COMPUTED",
    "edge_status": "NOT_COMPUTED",
    "recommendation_status": "NOT_AVAILABLE",
    "stake_status": "NOT_AVAILABLE",
    "cash_stake": 0,
    "simulated_stake": 0,
}

# Any of these keys, anywhere in a research queue item or excluded market
# record, would imply this workflow singled out one outcome as a pick/
# recommendation -- it must never appear.
BANNED_OUTCOME_SELECTION_KEYS = {"best_outcome", "best_outcome_point_ev", "selected_outcome", "pick", "recommendation"}


def make_fixture(
    *,
    fixture_id: str = "bxf_test0001",
    competition_id: str = "1000",
    home: str = "Home FC",
    away: str = "Away FC",
    kickoff_utc: str = "2026-09-14T18:00:00Z",
    home_price: float = 2.5,
    draw_price: float = 3.2,
    away_price: float = 3.0,
) -> dict:
    return {
        "source": "BET9JA",
        "source_capture_session_id": "soccer-2026-09-14T00-00-00Z",
        "source_fixture_id": fixture_id,
        "source_competition_id": competition_id,
        "sport": "SOCCER",
        "country": "Testland",
        "competition": "Test League",
        "home": home,
        "away": away,
        "kickoff_utc": kickoff_utc,
        "market": {"family": "1X2", "home": home_price, "draw": draw_price, "away": away_price},
        "classification_ceiling": "RESEARCH-MODEL",
    }


def make_batch(fixtures: list[dict]) -> dict:
    return {
        "schema_version": "pcbf-research-batch.v1",
        "source": "BET9JA",
        "source_capture_session_id": "soccer-2026-09-14T00-00-00Z",
        "source_captured_at_utc": "2026-09-14T00:05:00.000Z",
        "classification_ceiling": "RESEARCH-MODEL",
        "fixtures": fixtures,
    }


def _assert_no_selection_language(testcase: unittest.TestCase, record: dict) -> None:
    found = BANNED_OUTCOME_SELECTION_KEYS & set(record.keys())
    testcase.assertEqual(found, set(), f"record must not carry outcome-selection keys, found {found}")


def _assert_no_hda_outcome_selected(testcase: unittest.TestCase, record: dict) -> None:
    """No queue item may select H, D or A: the top-level record must not
    name a specific outcome anywhere outside the full, unranked
    ``pricing.outcomes`` list (which records every outcome equally, never
    singling one out)."""
    testcase.assertNotIn("outcome", record)
    testcase.assertIn("pricing", record)
    outcome_names = {o["outcome"] for o in record["pricing"]["outcomes"]}
    testcase.assertEqual(outcome_names, {"home", "draw", "away"})


class MarketQualityGateTests(unittest.TestCase):
    def test_arbitrage_shaped_market_is_excluded_not_queued(self):
        batch = make_batch(
            [make_fixture(home_price=1.5, draw_price=10.0, away_price=10.0)]
        )
        result = screen_research_batch(batch)
        self.assertEqual(result["research_queue_report"]["markets_queued"], 0)
        self.assertEqual(result["research_queue_report"]["markets_excluded"], 1)
        excluded = result["excluded_markets"]["excluded"]
        self.assertEqual(excluded[0]["reason"], SCREEN_MARKET_QUALITY_NOT_NORMAL)

    def test_high_margin_market_is_excluded_not_queued(self):
        batch = make_batch(
            [make_fixture(home_price=1.05, draw_price=1.05, away_price=1.05)]
        )
        result = screen_research_batch(batch)
        self.assertEqual(result["research_queue_report"]["markets_queued"], 0)
        self.assertEqual(result["research_queue_report"]["markets_excluded"], 1)
        self.assertEqual(
            result["excluded_markets"]["excluded"][0]["reason"],
            SCREEN_MARKET_QUALITY_NOT_NORMAL,
        )

    def test_normal_market_is_queued(self):
        batch = make_batch([make_fixture()])
        result = screen_research_batch(batch)
        self.assertEqual(result["research_queue_report"]["markets_queued"], 1)
        self.assertEqual(result["research_queue_report"]["markets_excluded"], 0)
        self.assertEqual(result["research_queue_report"]["reason_counts"], {})


class NoRecommendationLanguageTests(unittest.TestCase):
    """Proves this workflow never produces anything that reads as a pick,
    a selected outcome, or a betting recommendation."""

    def test_no_queue_item_contains_best_outcome(self):
        batch = make_batch([make_fixture()])
        result = screen_research_batch(batch)
        for record in result["research_queue"]["markets"]:
            self.assertNotIn("best_outcome", record)
            self.assertNotIn("best_outcome_point_ev", record)

    def test_no_queue_item_selects_h_d_or_a(self):
        batch = make_batch(
            [make_fixture(fixture_id=f"bxf_{i:04d}", competition_id=str(9000 + i)) for i in range(3)]
        )
        result = screen_research_batch(batch)
        for record in result["research_queue"]["markets"]:
            _assert_no_hda_outcome_selected(self, record)

    def test_outcome_ordering_cannot_affect_queue_ranking(self):
        # Same market shape (same three prices -> same margin -> same
        # research_priority_score) but the SHORTEST price sits on a
        # different outcome in each fixture -- "away" is shortest in A,
        # "home" is shortest in B. Queue order and every status field must
        # be entirely insensitive to which outcome carries which price.
        fixture_a = make_fixture(
            fixture_id="bxf_a", competition_id="6000", home_price=4.0, draw_price=3.5, away_price=2.0
        )
        fixture_b = make_fixture(
            fixture_id="bxf_b", competition_id="6001", home_price=2.0, draw_price=3.5, away_price=4.0
        )
        result = screen_research_batch(make_batch([fixture_a, fixture_b]))
        markets = {m["source_fixture_id"]: m for m in result["research_queue"]["markets"]}
        self.assertEqual(set(markets), {"bxf_a", "bxf_b"})
        for record in markets.values():
            _assert_no_selection_language(self, record)
            for field, expected in REQUIRED_STATUS_FIELDS.items():
                self.assertEqual(record[field], expected, field)
        self.assertAlmostEqual(
            markets["bxf_a"]["research_priority_score"],
            markets["bxf_b"]["research_priority_score"],
        )

    def test_no_output_contains_a_selected_outcome(self):
        batch = make_batch(
            [make_fixture(fixture_id="bxf_x", competition_id="7000"),
             make_fixture(fixture_id="bxf_y", competition_id="7001", home_price=1.05, draw_price=1.05, away_price=1.05)]
        )
        result = screen_research_batch(batch)
        for record in result["research_queue"]["markets"]:
            _assert_no_selection_language(self, record)
        for record in result["excluded_markets"]["excluded"]:
            self.assertEqual(BANNED_OUTCOME_SELECTION_KEYS & set(record.keys()), set())

    def test_every_item_carries_the_explicit_research_only_statuses(self):
        batch = make_batch(
            [make_fixture(fixture_id=f"bxf_{i:04d}", competition_id=str(8000 + i)) for i in range(4)]
        )
        result = screen_research_batch(batch)
        markets = result["research_queue"]["markets"]
        self.assertEqual(len(markets), 4)
        for record in markets:
            for field, expected in REQUIRED_STATUS_FIELDS.items():
                self.assertEqual(record[field], expected, field)
            self.assertIs(record["forecast"]["forecast_available"], False)


class QueueOrderingTests(unittest.TestCase):
    def test_ranking_changes_market_order_only_not_outcome_content(self):
        tight = make_fixture(
            fixture_id="bxf_tight", competition_id="2000", home_price=2.7, draw_price=3.4, away_price=2.8
        )
        wide = make_fixture(
            fixture_id="bxf_wide", competition_id="2001", home_price=2.0, draw_price=3.0, away_price=3.0
        )
        result = screen_research_batch(make_batch([wide, tight]))
        markets = result["research_queue"]["markets"]
        self.assertEqual(len(markets), 2)
        self.assertEqual(markets[0]["queue_position"], 1)
        self.assertEqual(markets[1]["queue_position"], 2)
        self.assertGreater(markets[0]["research_priority_score"], markets[1]["research_priority_score"])
        self.assertEqual(markets[0]["source_fixture_id"], "bxf_tight")
        self.assertEqual(markets[1]["source_fixture_id"], "bxf_wide")
        # "Ranked" reorders which MARKET comes first; every outcome's own
        # pricing content is untouched and none is singled out.
        for record in markets:
            _assert_no_hda_outcome_selected(self, record)

    def test_tie_break_is_deterministic_by_kickoff_competition_home_away_fixture(self):
        earlier = make_fixture(
            fixture_id="bxf_a", competition_id="3000", kickoff_utc="2026-09-14T12:00:00Z",
            home="Alpha", away="Beta",
        )
        later = make_fixture(
            fixture_id="bxf_b", competition_id="3000", kickoff_utc="2026-09-14T18:00:00Z",
            home="Gamma", away="Delta",
        )
        result = screen_research_batch(make_batch([later, earlier]))
        markets = result["research_queue"]["markets"]
        self.assertAlmostEqual(
            markets[0]["research_priority_score"], markets[1]["research_priority_score"]
        )
        self.assertEqual(markets[0]["source_fixture_id"], "bxf_a")
        self.assertEqual(markets[1]["source_fixture_id"], "bxf_b")


class ReconciliationTests(unittest.TestCase):
    def test_queued_plus_excluded_equals_source_fixtures(self):
        batch = make_batch(
            [
                make_fixture(fixture_id="bxf_ok", competition_id="4000"),
                make_fixture(fixture_id="bxf_bad", competition_id="4001", home_price=1.05, draw_price=1.05, away_price=1.05),
            ]
        )
        result = screen_research_batch(batch)
        report = result["research_queue_report"]
        self.assertEqual(report["source_fixtures"], 2)
        self.assertEqual(report["markets_queued"] + report["markets_excluded"], 2)
        self.assertTrue(report["reconciles"])

    def test_empty_batch_reconciles_cleanly(self):
        result = screen_research_batch(make_batch([]))
        report = result["research_queue_report"]
        self.assertEqual(report["source_fixtures"], 0)
        self.assertEqual(report["markets_queued"], 0)
        self.assertEqual(report["markets_excluded"], 0)
        self.assertTrue(report["reconciles"])


class IdempotencyTests(unittest.TestCase):
    def test_repeated_run_is_byte_identical(self):
        batch = make_batch(
            [make_fixture(fixture_id=f"bxf_{i:04d}", competition_id=str(5000 + i)) for i in range(5)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "pcbf-research-batch.json"
            input_path.write_text(json.dumps(batch), encoding="utf-8")
            out1, out2 = tmp_dir / "run1", tmp_dir / "run2"
            run_screen(input_path, out1)
            run_screen(input_path, out2)
            for filename in ("research-queue-report.json", "research-queue-ranked.json", "research-queue-excluded.json"):
                self.assertEqual((out1 / filename).read_bytes(), (out2 / filename).read_bytes())


class CliIntegrationTests(unittest.TestCase):
    def test_cli_and_filenames_use_research_queue_terminology(self):
        from pcbf_calculator.cli import main as cli_main

        batch = make_batch([make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "pcbf-research-batch.json"
            input_path.write_text(json.dumps(batch), encoding="utf-8")
            output_dir = tmp_dir / "out"
            exit_code = cli_main(["screen-research-batch", str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "research-queue-report.json").exists())
            self.assertTrue((output_dir / "research-queue-ranked.json").exists())
            self.assertTrue((output_dir / "research-queue-excluded.json").exists())
            # No "candidate"-named file must ever be produced.
            self.assertFalse((output_dir / "research-candidates-ranked.json").exists())
            self.assertFalse((output_dir / "research-candidates-rejected.json").exists())
            self.assertFalse((output_dir / "screening-report.json").exists())

    def test_host_contract_invocation_is_unaffected(self):
        # The documented host-contract invocation (python -m pcbf_calculator
        # INPUT [OUTPUT], no subcommand) must remain completely unchanged by
        # this new subcommand's addition.
        from pcbf_calculator.cli import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            request_path = tmp_dir / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "event_id": "demo-1",
                        "category": "soccer",
                        "market_prices": {"home": 2.1, "draw": 3.4, "away": 3.9},
                    }
                ),
                encoding="utf-8",
            )
            output_path = tmp_dir / "response.json"
            exit_code = cli_main([str(request_path), str(output_path)])
            self.assertEqual(exit_code, 0)
            response = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "OK")


# Real-data regression: the exact pcbf-research-batch.json produced by
# running `ingest-bet9ja` against the already-committed, real Round 14
# extract (tests/fixtures/bet9ja/bet9ja-soccer-all-round14-confirmed-clean.json,
# itself a trimmed real extract of an actual live Bet9ja capture -- see
# tests/test_bet9ja_ingestion.py). 3 real competitions (Nigerian
# Professional Football League, UEFA Nations League League A, a third real
# competition), 17 real fixtures, real H/D/A prices throughout -- nothing
# here is fabricated.
#
# The identical properties asserted here were also independently verified
# directly against the full real 798-fixture live capture used for PR #39's
# acceptance evidence (soccer-2026-09-13T14-10-35Z): running
# `ingest-bet9ja` then `screen-research-batch` against that live 2.4 MB
# export produced exactly 798 research queue items, 0 excluded markets,
# and (by construction, since every code path here is identical) 0
# recommendations of any kind -- see PR #40's body for the exact counts and
# SHA-256 hashes of that run. That raw live file is never committed, per
# this repo's evidence-only discipline of committing only compact,
# internally-reconciled real-data extracts; this 17-fixture extract
# exercises the exact same code path at real, if smaller, scale.
class Round14ScreeningRegressionTest(unittest.TestCase):
    def test_round14_research_batch_all_queued_zero_excluded_zero_recommendations(self):
        batch_path = FIXTURES_DIR / "pcbf-research-batch-round14-confirmed-clean.json"
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        result = screen_research_batch(batch)
        report = result["research_queue_report"]
        self.assertEqual(report["source_fixtures"], 17)
        self.assertEqual(report["markets_queued"], 17)
        self.assertEqual(report["markets_excluded"], 0)
        self.assertEqual(report["reason_counts"], {})

        markets = result["research_queue"]["markets"]
        positions = [m["queue_position"] for m in markets]
        self.assertEqual(positions, list(range(1, 18)))
        scores = [m["research_priority_score"] for m in markets]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for record in markets:
            _assert_no_selection_language(self, record)
            _assert_no_hda_outcome_selected(self, record)
            for field, expected in REQUIRED_STATUS_FIELDS.items():
                self.assertEqual(record[field], expected, field)

    def test_round14_research_batch_repeated_screening_is_byte_identical(self):
        batch_path = FIXTURES_DIR / "pcbf-research-batch-round14-confirmed-clean.json"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out1, out2 = tmp_dir / "run1", tmp_dir / "run2"
            run_screen(batch_path, out1)
            run_screen(batch_path, out2)
            for filename in ("research-queue-report.json", "research-queue-ranked.json", "research-queue-excluded.json"):
                self.assertEqual((out1 / filename).read_bytes(), (out2 / filename).read_bytes())


if __name__ == "__main__":
    unittest.main()
