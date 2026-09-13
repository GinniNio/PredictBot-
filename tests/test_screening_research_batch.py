"""Tests for the PCBF research-batch screening/ranking workflow
(``src/pcbf_calculator/screening/``).

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


class MarketQualityGateTests(unittest.TestCase):
    def test_arbitrage_shaped_market_is_rejected_not_ranked(self):
        # 1/1.5 + 1/6 + 1/6 = 1.0000 -- push it slightly under 1 for a real
        # negative margin (arbitrage-shaped, evidence_quality ANOMALOUS_NEGATIVE_MARGIN).
        batch = make_batch(
            [make_fixture(home_price=1.5, draw_price=10.0, away_price=10.0)]
        )
        result = screen_research_batch(batch)
        self.assertEqual(result["screening_report"]["candidates_ranked"], 0)
        self.assertEqual(result["screening_report"]["candidates_rejected"], 1)
        rejected = result["candidates_rejected"]["rejected"]
        self.assertEqual(rejected[0]["reason"], SCREEN_MARKET_QUALITY_NOT_NORMAL)

    def test_high_margin_market_is_rejected_not_ranked(self):
        # Very long, uncompetitive prices push margin well over 50%.
        batch = make_batch(
            [make_fixture(home_price=1.05, draw_price=1.05, away_price=1.05)]
        )
        result = screen_research_batch(batch)
        self.assertEqual(result["screening_report"]["candidates_ranked"], 0)
        self.assertEqual(result["screening_report"]["candidates_rejected"], 1)
        self.assertEqual(
            result["candidates_rejected"]["rejected"][0]["reason"],
            SCREEN_MARKET_QUALITY_NOT_NORMAL,
        )

    def test_normal_market_is_ranked(self):
        batch = make_batch([make_fixture()])
        result = screen_research_batch(batch)
        self.assertEqual(result["screening_report"]["candidates_ranked"], 1)
        self.assertEqual(result["screening_report"]["candidates_rejected"], 0)
        self.assertEqual(result["screening_report"]["reason_counts"], {})


class ClassificationCeilingNeverPromotedTests(unittest.TestCase):
    def test_every_ranked_candidate_stays_research_model_with_zero_stakes(self):
        batch = make_batch([make_fixture(), make_fixture(fixture_id="bxf_test0002", competition_id="1001")])
        result = screen_research_batch(batch)
        candidates = result["candidates_ranked"]["candidates"]
        self.assertEqual(len(candidates), 2)
        for candidate in candidates:
            self.assertEqual(candidate["classification_ceiling"], "RESEARCH-MODEL")
            self.assertEqual(candidate["cash_stake"], 0)
            self.assertEqual(candidate["simulated_stake"], 0)

    def test_forecast_is_never_available_no_adapter_registered(self):
        batch = make_batch([make_fixture()])
        result = screen_research_batch(batch)
        candidate = result["candidates_ranked"]["candidates"][0]
        self.assertIs(candidate["forecast"]["forecast_available"], False)


class RankingOrderTests(unittest.TestCase):
    def test_ranked_by_research_priority_score_descending_tighter_market_first(self):
        # Fixture A: tight, low-margin market (higher research_priority_score).
        # Fixture B: wider, higher-margin market (lower score) but still NORMAL.
        tight = make_fixture(
            fixture_id="bxf_tight", competition_id="2000", home_price=2.7, draw_price=3.4, away_price=2.8
        )
        wide = make_fixture(
            fixture_id="bxf_wide", competition_id="2001", home_price=2.0, draw_price=3.0, away_price=3.0
        )
        batch = make_batch([wide, tight])  # deliberately inserted out of expected rank order
        result = screen_research_batch(batch)
        candidates = result["candidates_ranked"]["candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["rank"], 1)
        self.assertEqual(candidates[1]["rank"], 2)
        self.assertGreater(candidates[0]["research_priority_score"], candidates[1]["research_priority_score"])
        self.assertEqual(candidates[0]["source_fixture_id"], "bxf_tight")
        self.assertEqual(candidates[1]["source_fixture_id"], "bxf_wide")

    def test_tie_break_is_deterministic_by_kickoff_competition_home_away_fixture(self):
        # Identical prices -> identical research_priority_score -> tie-break must decide order.
        earlier = make_fixture(
            fixture_id="bxf_a", competition_id="3000", kickoff_utc="2026-09-14T12:00:00Z",
            home="Alpha", away="Beta",
        )
        later = make_fixture(
            fixture_id="bxf_b", competition_id="3000", kickoff_utc="2026-09-14T18:00:00Z",
            home="Gamma", away="Delta",
        )
        batch = make_batch([later, earlier])
        result = screen_research_batch(batch)
        candidates = result["candidates_ranked"]["candidates"]
        self.assertAlmostEqual(
            candidates[0]["research_priority_score"], candidates[1]["research_priority_score"]
        )
        self.assertEqual(candidates[0]["source_fixture_id"], "bxf_a")
        self.assertEqual(candidates[1]["source_fixture_id"], "bxf_b")


class ReconciliationTests(unittest.TestCase):
    def test_ranked_plus_rejected_equals_source_fixtures(self):
        batch = make_batch(
            [
                make_fixture(fixture_id="bxf_ok", competition_id="4000"),
                make_fixture(fixture_id="bxf_bad", competition_id="4001", home_price=1.05, draw_price=1.05, away_price=1.05),
            ]
        )
        result = screen_research_batch(batch)
        report = result["screening_report"]
        self.assertEqual(report["source_fixtures"], 2)
        self.assertEqual(report["candidates_ranked"] + report["candidates_rejected"], 2)
        self.assertTrue(report["reconciles"])

    def test_empty_batch_reconciles_cleanly(self):
        result = screen_research_batch(make_batch([]))
        report = result["screening_report"]
        self.assertEqual(report["source_fixtures"], 0)
        self.assertEqual(report["candidates_ranked"], 0)
        self.assertEqual(report["candidates_rejected"], 0)
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
            for filename in ("screening-report.json", "research-candidates-ranked.json", "research-candidates-rejected.json"):
                self.assertEqual((out1 / filename).read_bytes(), (out2 / filename).read_bytes())


class CliIntegrationTests(unittest.TestCase):
    def test_cli_writes_three_files_and_returns_zero(self):
        from pcbf_calculator.cli import main as cli_main

        batch = make_batch([make_fixture()])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "pcbf-research-batch.json"
            input_path.write_text(json.dumps(batch), encoding="utf-8")
            output_dir = tmp_dir / "out"
            exit_code = cli_main(["screen-research-batch", str(input_path), "--output-dir", str(output_dir)])
            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / "screening-report.json").exists())
            self.assertTrue((output_dir / "research-candidates-ranked.json").exists())
            self.assertTrue((output_dir / "research-candidates-rejected.json").exists())

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
class Round14ScreeningRegressionTest(unittest.TestCase):
    def test_round14_research_batch_all_admitted_and_ranked(self):
        batch_path = FIXTURES_DIR / "pcbf-research-batch-round14-confirmed-clean.json"
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        result = screen_research_batch(batch)
        report = result["screening_report"]
        self.assertEqual(report["source_fixtures"], 17)
        self.assertEqual(report["candidates_ranked"], 17)
        self.assertEqual(report["candidates_rejected"], 0)
        self.assertEqual(report["reason_counts"], {})

        candidates = result["candidates_ranked"]["candidates"]
        ranks = [c["rank"] for c in candidates]
        self.assertEqual(ranks, list(range(1, 18)))
        # Ranking is sorted descending by research_priority_score.
        scores = [c["research_priority_score"] for c in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for candidate in candidates:
            self.assertEqual(candidate["classification_ceiling"], "RESEARCH-MODEL")
            self.assertEqual(candidate["cash_stake"], 0)
            self.assertEqual(candidate["simulated_stake"], 0)

    def test_round14_research_batch_repeated_screening_is_byte_identical(self):
        batch_path = FIXTURES_DIR / "pcbf-research-batch-round14-confirmed-clean.json"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out1, out2 = tmp_dir / "run1", tmp_dir / "run2"
            run_screen(batch_path, out1)
            run_screen(batch_path, out2)
            for filename in ("screening-report.json", "research-candidates-ranked.json", "research-candidates-rejected.json"):
                self.assertEqual((out1 / filename).read_bytes(), (out2 / filename).read_bytes())


if __name__ == "__main__":
    unittest.main()
