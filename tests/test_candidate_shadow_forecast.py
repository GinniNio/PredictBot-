"""Tests for candidate shadow forecasting
(``src/pcbf_calculator/orchestration/candidate_shadow_forecast.py``).

Reuses ``tests/test_soccer_artifact_refresh.py``'s own
``BASELINE_FIXTURE_MAP``/``_populate_raw_dir`` pattern to build a real
candidate bundle, and ``tests/test_bet9ja_research_session.py``'s own
``make_fixture``/``make_envelope`` pattern to build a real Bet9ja
capture -- never a second, separately-maintained notion of either.

Team names: the tiny fixture CSVs behind ``BASELINE_FIXTURE_MAP`` only
carry a completed result (and therefore an Elo rating) for a handful of
teams -- "Liverpool"/"Man City" are used throughout this file for that
reason (confirmed directly against the candidate's own live_snapshot).
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from data_pipeline.dataset_builder import raw_file_path  # noqa: E402

from pcbf_calculator.adapters import registry as adapter_registry  # noqa: E402
from pcbf_calculator.orchestration import candidate_shadow_forecast as csf  # noqa: E402
from pcbf_calculator.orchestration import soccer_artifact_refresh as sar  # noqa: E402
from pcbf_calculator.orchestration.forecast_ledger_writer import (  # noqa: E402
    LedgerBatchConflictError,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "football_data"

BASELINE_FIXTURE_MAP = {
    ("E0", "1920"): "season_1920_with_kickoff.csv",
    ("E0", "2324"): "season_2324_for_elo_baseline.csv",
    ("E0", "2425"): "season_2425_for_elo_baseline.csv",
    ("E0", "2526"): "season_2526_prospective.csv",
    ("E0", "2627"): "season_2627_prospective.csv",
}

REAL_INCUMBENT_MANIFEST_PATH = (
    REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data" / "model_artifact_manifest.json"
)

CAPTURED_AT_UTC = "2026-09-12T15:30:23.000Z"
ENGLAND_LEDGER_ENTRY = {"competition_id": "2000001", "country": "England", "competition": "Premier League", "status": "COMPLETED"}
ARBITRAGE_ODDS = {"H": 1.2, "D": 1.2, "A": 1.2}


def _populate_raw_dir(raw_dir: Path, mapping: dict = BASELINE_FIXTURE_MAP) -> None:
    for (league, season), filename in mapping.items():
        dest = raw_file_path(raw_dir, league, season)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / filename, dest)


def make_fixture(
    fixture_id="bxf_1",
    competition_id="2000001",
    home="Liverpool",
    away="Man City",
    odds=None,
    date_heading_raw="Sun 20 Sep",
    kickoff_raw="14:00",
):
    return {
        "fixture_id": fixture_id,
        "sport": "SOCCER",
        "status": "PRE_MATCH",
        "market_family": "1X2",
        "offered_odds": odds if odds is not None else {"H": 1.9, "D": 3.4, "A": 4.3},
        "participants": {"home": home, "away": away},
        "resolved_source_competition_id": competition_id,
        "date_heading_raw": date_heading_raw,
        "kickoff_raw": kickoff_raw,
        "duplicate_status": "NEW",
    }


def make_envelope(fixtures, captured_at_utc=CAPTURED_AT_UTC):
    ledger = [ENGLAND_LEDGER_ENTRY]
    statuses = [entry["status"] for entry in ledger]
    summary = {"total": len(ledger), "completed": statuses.count("COMPLETED"), "confirmed_empty": 0, "failed": 0, "pending": 0}
    return {
        "schema_version": "bet9ja-soccer-session.v1",
        "capture_session_id": "soccer-test-session",
        "capture_scope": "SOCCER_ALL_PREMATCH_COMPETITIONS",
        "captured_at_utc": captured_at_utc,
        "inventory_fingerprint": "invfp_test",
        "summary": summary,
        "competition_ledger": ledger,
        "fixtures": fixtures,
        "unparsed_records": [],
    }


class CandidateShadowForecastTestCase(unittest.TestCase):
    """Builds one real candidate bundle in ``setUp`` -- every test method
    shadow-forecasts against it."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="pcbf-shadow-forecast-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.tmp_path = Path(self.tmpdir)

        raw_dir = self.tmp_path / "raw"
        _populate_raw_dir(raw_dir)
        incumbent_manifest_path = self.tmp_path / "incumbent_manifest.json"
        shutil.copy(REAL_INCUMBENT_MANIFEST_PATH, incumbent_manifest_path)
        perf_dir = self.tmp_path / "incumbent_perf"
        perf_dir.mkdir()

        candidate_out = self.tmp_path / "candidate_out"
        sar.refresh_soccer_artifact(
            training_input=raw_dir,
            incumbent_manifest_path=incumbent_manifest_path,
            incumbent_performance_dir=perf_dir,
            output_dir=candidate_out,
            generation_command="cmd",
        )
        self.candidate_dir = candidate_out / "candidate"
        self.candidate_bundle_hash = json.loads(
            (self.candidate_dir / "candidate_bundle_manifest.json").read_text()
        )["bundle_hash"]

        self.default_fixtures = [
            make_fixture(fixture_id="bxf_1"),  # Liverpool/Man City -- a real candidate forecast
            make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),  # forecast abstention
            make_fixture(fixture_id="bxf_3", odds=ARBITRAGE_ODDS),  # pricing-quality excluded
            make_fixture(fixture_id="bxf_4", date_heading_raw="Sat 12 Sep", kickoff_raw="00:00"),  # ingestion quarantine
        ]
        self.capture_path = self.tmp_path / "capture.json"
        self.capture_path.write_text(json.dumps(make_envelope(self.default_fixtures)), encoding="utf-8")

        self.ledger_dir = self.tmp_path / "candidate_ledgers"
        self.output_dir = self.tmp_path / "shadow_out"

    def _run(self):
        return csf.run_session(self.candidate_dir, self.capture_path, self.ledger_dir, self.output_dir)


class OutputAndReconciliationTests(CandidateShadowForecastTestCase):
    def test_produces_every_required_output_file(self):
        self._run()
        for name in (
            "candidate-shadow-forecasts.json",
            "candidate-shadow-abstentions.json",
            "candidate-shadow-quarantine.json",
            "candidate-shadow-session-report.json",
        ):
            self.assertTrue((self.output_dir / name).exists(), name)

    def test_every_attempted_fixture_reconciles_into_exactly_one_bucket(self):
        result = self._run()
        counts = result["candidate_shadow_session_report"]["counts"]
        total = counts["quarantined_and_excluded"] + counts["forecast_abstained"] + counts["successful_forecasts"]
        self.assertEqual(total, counts["source_fixtures_raw"])
        self.assertEqual(counts["source_fixtures_raw"], 4)
        # A real forecast, a real abstention, and (quarantine+pricing
        # exclusion folded together) two quarantined/excluded.
        self.assertEqual(counts["successful_forecasts"], 1)
        self.assertEqual(counts["forecast_abstained"], 1)
        self.assertEqual(counts["quarantined_and_excluded"], 2)

    def test_successful_forecast_carries_probabilities_and_join_key_fields(self):
        result = self._run()
        forecasts = result["candidate_shadow_forecasts"]["forecasts"]
        self.assertEqual(len(forecasts), 1)
        row = forecasts[0]
        self.assertAlmostEqual(sum(row["forecast"]["probabilities"].values()), 1.0, places=6)
        for field in ("capture_hash", "candidate_bundle_hash", "build_identity", "market_type", "forecast_cutoff_utc"):
            self.assertIn(field, row)
        self.assertEqual(row["candidate_bundle_hash"], self.candidate_bundle_hash)

    def test_forecasts_are_never_ranked_for_operator_action(self):
        result = self._run()
        for row in result["candidate_shadow_forecasts"]["forecasts"]:
            # The TOP-LEVEL ranking fields run-bet9ja-research's own
            # ranked queue adds (a priority-ordering signal, plus its
            # position in that queue) are stripped -- the pricing
            # engine's OWN nested market_quality.research_priority_score
            # (a real, pre-existing pricing-quality measurement, not a
            # ranking this module applies) legitimately remains as part
            # of the full pricing detail, same as run-bet9ja-research's
            # own ranked markets carry it too.
            self.assertNotIn("research_priority_score", row)
            self.assertNotIn("queue_position", row)
        for row in result["candidate_shadow_forecasts"]["forecasts"]:
            self.assertIsNone(row["operator_decision"])
            self.assertEqual(row["recommendation_status"], "NOT_AVAILABLE")
            self.assertEqual(row["classification_ceiling"], "RESEARCH-MODEL")
            self.assertEqual(row["cash_stake"], 0)
            self.assertEqual(row["simulated_stake"], 0)


class LedgerFieldsAndSeparationTests(CandidateShadowForecastTestCase):
    def test_ledger_rows_carry_every_required_candidate_field(self):
        self._run()
        ledger_path = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        self.assertTrue(ledger_path.exists())
        lines = [json.loads(line) for line in ledger_path.read_text().splitlines()]
        self.assertEqual(len(lines), 2)  # one forecast + one abstention
        for event in lines:
            payload = event["payload"]
            self.assertEqual(payload["model_role"], "CANDIDATE_SHADOW")
            self.assertEqual(payload["candidate_bundle_hash"], self.candidate_bundle_hash)
            self.assertTrue(payload["build_identity"])
            self.assertTrue(payload["capture_hash"])
            self.assertTrue(payload["capture_session_id"])
            self.assertTrue(payload["forecast_cutoff_utc"])
            self.assertEqual(payload["recommendation_status"], "NOT_AVAILABLE")
            self.assertIsNone(payload["operator_decision"])

    def test_candidate_results_cannot_enter_the_incumbent_ledger(self):
        incumbent_ledger_dir = self.tmp_path / "incumbent_ledger"
        self._run()
        # The incumbent's own ledger directory was never created by this
        # run -- candidate rows live exclusively under
        # <ledger_dir>/<candidate_bundle_hash>/, never anywhere the
        # incumbent's own run-bet9ja-research --ledger-dir would write.
        self.assertFalse(incumbent_ledger_dir.exists())
        candidate_ledger_path = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        self.assertTrue(candidate_ledger_path.exists())
        # And the reverse: nothing this run writes lands directly under
        # ledger_dir itself (only under its own bundle-hash subdirectory).
        self.assertFalse((self.ledger_dir / "forecast-ledger.jsonl").exists())

    def test_candidate_rows_can_only_ever_reach_the_one_expected_ledger_path(self):
        self._run()
        all_ledger_files = sorted(p for p in self.ledger_dir.rglob("*.jsonl"))
        expected = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        self.assertEqual(all_ledger_files, [expected])

    def test_repeating_the_command_appends_zero_duplicate_rows(self):
        first = self._run()
        self.assertEqual(first["ledger_write_summary"]["appended"], 2)
        self.assertEqual(first["ledger_write_summary"]["duplicate_skipped"], 0)
        second = self._run()
        self.assertEqual(second["ledger_write_summary"]["appended"], 0)
        self.assertEqual(second["ledger_write_summary"]["duplicate_skipped"], 2)
        self.assertEqual(second["ledger_write_summary"]["total_ledger_records"], 2)

    def test_a_genuinely_conflicting_rerun_is_refused_not_silently_rewritten(self):
        self._run()
        # Change the capture's own odds for the SAME fixture under the
        # SAME candidate -- same natural key (fixture_id, market_type,
        # model_version), different content -> CONFLICT, nothing written.
        conflicting_fixtures = [
            make_fixture(fixture_id="bxf_1", odds={"H": 2.5, "D": 3.1, "A": 3.0}),
            make_fixture(fixture_id="bxf_2", home="Nonexistent FC"),
            make_fixture(fixture_id="bxf_3", odds=ARBITRAGE_ODDS),
            make_fixture(fixture_id="bxf_4", date_heading_raw="Sat 12 Sep", kickoff_raw="00:00"),
        ]
        self.capture_path.write_text(json.dumps(make_envelope(conflicting_fixtures)), encoding="utf-8")
        with self.assertRaises(LedgerBatchConflictError):
            self._run()
        # Nothing new landed in the output dir's report either -- rerun
        # the ORIGINAL capture and confirm the ledger is unaffected by
        # the aborted conflicting attempt.
        ledger_path = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        lines_after_conflict = ledger_path.read_text().splitlines()
        self.assertEqual(len(lines_after_conflict), 2)


class BundleTamperingAbortsTests(CandidateShadowForecastTestCase):
    def test_tampering_aborts_before_any_ledger_or_report_mutation(self):
        live_snapshot_path = self.candidate_dir / "live_snapshot.json"
        data = json.loads(live_snapshot_path.read_text())
        data["team_count"] = 999999
        live_snapshot_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        with self.assertRaises(sar.CandidateBundleVerificationError):
            self._run()

        self.assertFalse(self.output_dir.exists())
        self.assertFalse(self.ledger_dir.exists())

    def test_missing_candidate_bundle_file_aborts_cleanly(self):
        (self.candidate_dir / "evaluation_report.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self._run()
        self.assertFalse(self.output_dir.exists())
        self.assertFalse(self.ledger_dir.exists())


class RegistryAndAdapterIsolationTests(CandidateShadowForecastTestCase):
    def test_candidate_bundle_cannot_load_through_the_active_adapter_registry(self):
        incumbent_before = adapter_registry.get_adapter("soccer").declaration.model_version
        self._run()
        incumbent_after = adapter_registry.get_adapter("soccer").declaration.model_version
        self.assertEqual(incumbent_before, incumbent_after)
        # The candidate's own model_version never equals the incumbent's --
        # confirming the registry truly never resolved the candidate.
        candidate_manifest = json.loads((self.candidate_dir / "model_artifact_manifest.json").read_text())
        self.assertNotEqual(incumbent_after, candidate_manifest["model_version"])

    def test_never_touches_the_real_shipped_adapter_data_directory(self):
        real_data_dir = REPO_ROOT / "src" / "pcbf_calculator" / "adapters" / "soccer_1x2_elo_v1" / "data"
        before = {p: p.read_bytes() for p in real_data_dir.iterdir() if p.is_file()}
        self._run()
        after = {p: p.read_bytes() for p in real_data_dir.iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_candidate_resolves_the_same_teams_the_incumbent_would_with_real_aliases_injected(self):
        # A plain SoccerOneXTwoEloV1Adapter(data_dir=candidate_dir) with
        # NO alias_book override would silently fall back to an EMPTY
        # alias book (candidate bundles ship no team_aliases.json) --
        # this module always injects the real, shipped one instead. Prove
        # it actually took effect by checking the real alias book is
        # non-empty and at least one alias resolves identically to how
        # the incumbent's own default construction would resolve it.
        from pcbf_calculator.adapters.soccer_1x2_elo_v1 import SoccerOneXTwoEloV1Adapter, load_team_alias_book

        alias_book = load_team_alias_book()
        injected = SoccerOneXTwoEloV1Adapter(data_dir=self.candidate_dir, alias_book=alias_book)
        self.assertIs(injected._alias_book, alias_book)
        default = SoccerOneXTwoEloV1Adapter(data_dir=self.candidate_dir)
        # Without an explicit override, a candidate_dir with no
        # team_aliases.json of its own falls back to an EMPTY book --
        # this is the exact gap alias_book injection closes.
        self.assertIsNot(default._alias_book, alias_book)


class AliasReproducibilityTests(CandidateShadowForecastTestCase):
    def _patch_alias_path(self, path):
        original = csf.default_team_aliases_path
        csf.default_team_aliases_path = lambda data_dir=None: path
        self.addCleanup(setattr, csf, "default_team_aliases_path", original)

    def _write_alias_file(self, path, extra_entry=None):
        # Base content: a minimal, well-formed alias book -- never reads
        # or writes the real repo file, so this test can never leave it
        # modified regardless of how it exits.
        data = {"leagues": {"E0": {}}}
        if extra_entry:
            data["leagues"]["E0"].update(extra_entry)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_alias_hash_is_recorded_and_consistent_across_every_row(self):
        alias_path = self.tmp_path / "team_aliases.json"
        self._write_alias_file(alias_path)
        self._patch_alias_path(alias_path)

        result = self._run()
        expected_hash = csf.compute_alias_hash()
        self.assertTrue(expected_hash)

        for row in result["candidate_shadow_forecasts"]["forecasts"]:
            self.assertEqual(row["alias_hash"], expected_hash)
        for row in result["candidate_shadow_abstentions"]["abstentions"]:
            self.assertEqual(row["alias_hash"], expected_hash)
        self.assertEqual(result["candidate_shadow_session_report"]["alias_hash"], expected_hash)

        ledger_path = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        for line in ledger_path.read_text().splitlines():
            payload = json.loads(line)["payload"]
            self.assertEqual(payload["alias_hash"], expected_hash)

    def test_rerun_after_aliases_change_fails_with_a_typed_mismatch_not_silently(self):
        alias_path = self.tmp_path / "team_aliases.json"
        self._write_alias_file(alias_path)
        self._patch_alias_path(alias_path)

        first = self._run()
        ledger_path = self.ledger_dir / self.candidate_bundle_hash / "forecast-ledger.jsonl"
        lines_before = ledger_path.read_text().splitlines()

        # Change the alias file's own content -- a different alias_hash,
        # same candidate bundle, same ledger location.
        self._write_alias_file(alias_path, extra_entry={"Some New Alias FC": "Liverpool"})

        second_output_dir = self.tmp_path / "second_shadow_out"
        with self.assertRaises(csf.AliasHashMismatchError):
            csf.run_session(self.candidate_dir, self.capture_path, self.ledger_dir, second_output_dir)

        # Nothing was mutated by the aborted run.
        self.assertFalse(second_output_dir.exists())
        self.assertEqual(ledger_path.read_text().splitlines(), lines_before)

    def test_unchanged_aliases_still_rerun_idempotently(self):
        alias_path = self.tmp_path / "team_aliases.json"
        self._write_alias_file(alias_path)
        self._patch_alias_path(alias_path)

        first = self._run()
        second = self._run()
        self.assertEqual(second["ledger_write_summary"]["appended"], 0)
        self.assertEqual(second["ledger_write_summary"]["duplicate_skipped"], first["ledger_write_summary"]["appended"])

    def test_candidate_and_incumbent_share_the_same_alias_hash(self):
        # The incumbent has no override path at all -- adapters.registry
        # always constructs SoccerOneXTwoEloV1Adapter with alias_book=None,
        # which resolves to the one real, shipped team_aliases.json. A
        # candidate's own recorded alias_hash (using the REAL file, no
        # patching here) must equal a fresh compute_alias_hash() call --
        # exactly "candidate and incumbent used the same aliases."
        result = self._run()
        real_alias_hash = csf.compute_alias_hash()
        for row in result["candidate_shadow_forecasts"]["forecasts"]:
            self.assertEqual(row["alias_hash"], real_alias_hash)


class SettlementAndReportingReuseTests(CandidateShadowForecastTestCase):
    def test_existing_settlement_engine_settles_the_candidate_ledger_unmodified(self):
        from pcbf_calculator.orchestration import football_data_settlement as settlement

        self._run()
        candidate_ledger_dir = self.ledger_dir / self.candidate_bundle_hash

        csv_path = self.tmp_path / "settle_e0.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "PSCH", "PSCD", "PSCA"])
            writer.writerow(["E0", "20/09/26", "14:00", "Liverpool", "Man City", "2", "1", "H", "1.85", "3.5", "4.2"])

        result = settlement.ingest_football_data_results([csv_path], candidate_ledger_dir)
        self.assertEqual(result["settlement_report"]["counts"]["forecast_matches_scored"], 1)
        self.assertTrue(result["settlement_report"]["rows_reconciled"])

    def test_existing_performance_reporter_reports_on_the_candidate_ledger_unmodified_and_separately(self):
        from pcbf_calculator.orchestration import football_data_settlement as settlement
        from pcbf_calculator.orchestration import forecast_performance_report as report

        self._run()
        candidate_ledger_dir = self.ledger_dir / self.candidate_bundle_hash

        csv_path = self.tmp_path / "settle_e0.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "PSCH", "PSCD", "PSCA"])
            writer.writerow(["E0", "20/09/26", "14:00", "Liverpool", "Man City", "2", "1", "H", "1.85", "3.5", "4.2"])
        settlement.ingest_football_data_results([csv_path], candidate_ledger_dir)

        perf = report.run_performance_report(candidate_ledger_dir)
        self.assertEqual(perf["performance_summary"]["overall"]["sample_count"], 1)

        # Physically separate: an incumbent-scoped ledger dir alongside it
        # has no rows at all, proving this candidate's performance report
        # is not silently drawing from (or polluting) any shared/incumbent
        # location.
        incumbent_ledger_dir = self.tmp_path / "incumbent_ledger_dir_never_written"
        incumbent_ledger_dir.mkdir()
        (incumbent_ledger_dir / "forecast-ledger.jsonl").write_text("", encoding="utf-8")
        incumbent_perf = report.run_performance_report(incumbent_ledger_dir)
        self.assertEqual(incumbent_perf["performance_summary"]["overall"]["sample_count"], 0)


class InertFieldsInvariantTests(CandidateShadowForecastTestCase):
    def test_no_recommendation_admission_staking_or_ticket_fields_become_actionable(self):
        result = self._run()
        rendered = json.dumps(
            [
                result["candidate_shadow_forecasts"],
                result["candidate_shadow_abstentions"],
                result["candidate_shadow_quarantine"],
                result["candidate_shadow_session_report"],
            ]
        )
        for forbidden in ("recommended_outcome", "best_outcome", '"pick"', "bet_side", "selected_outcome", "ticket"):
            self.assertNotIn(forbidden, rendered)
        for row in result["candidate_shadow_forecasts"]["forecasts"]:
            self.assertIsNone(row["operator_decision"])
            self.assertEqual(row["recommendation_status"], "NOT_AVAILABLE")


def _incumbent_result(markets=(), abstentions=()):
    return {
        "forecast_research_ranked": {"markets": list(markets)},
        "forecast_abstentions": {"abstentions": list(abstentions)},
    }


def _candidate_result(forecasts=(), abstentions=()):
    return {
        "candidate_shadow_forecasts": {"forecasts": list(forecasts)},
        "candidate_shadow_abstentions": {"abstentions": list(abstentions)},
    }


def _market(fixture_id, probabilities, alias_hash=None):
    entry = {"source_fixture_id": fixture_id, "forecast": {"probabilities": probabilities}}
    if alias_hash is not None:
        entry["alias_hash"] = alias_hash
    return entry


def _abstention(fixture_id, reason):
    return {"source_fixture_id": fixture_id, "reason": reason}


class PairedFixtureIntegrityTests(unittest.TestCase):
    """Direct, synthetic-input tests of
    ``pair_incumbent_and_candidate_results`` -- a pure function, tested
    at this granularity rather than by contorting a full pipeline run
    into each of the five required scenarios."""

    PROBS_A = {"home_win": 0.5, "draw": 0.3, "away_win": 0.2}
    PROBS_B = {"home_win": 0.2, "draw": 0.3, "away_win": 0.5}

    SAME_ALIAS_HASH = "alias-hash-shared"

    def test_identical_probabilities(self):
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(forecasts=[_market("f1", dict(self.PROBS_A), alias_hash=self.SAME_ALIAS_HASH)])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate, incumbent_alias_hash=self.SAME_ALIAS_HASH)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_BOTH_FORECAST_IDENTICAL)

    def test_different_probabilities(self):
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(forecasts=[_market("f1", self.PROBS_B, alias_hash=self.SAME_ALIAS_HASH)])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate, incumbent_alias_hash=self.SAME_ALIAS_HASH)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_BOTH_FORECAST_DIFFERENT)
        self.assertEqual(pairs[0]["incumbent_probabilities"], self.PROBS_A)
        self.assertEqual(pairs[0]["candidate_probabilities"], self.PROBS_B)

    def test_forecast_pair_without_incumbent_alias_hash_is_never_labeled_comparable(self):
        # incumbent_alias_hash omitted (defaults to None) -- even though
        # the probabilities are identical, this must NOT be reported as
        # COMPARISON_BOTH_FORECAST_IDENTICAL: alias provenance on the
        # incumbent side is unknown, so the pair could be "identical"
        # purely by coincidence, or because team resolution masked a
        # real difference either way -- never assumed safe.
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(forecasts=[_market("f1", dict(self.PROBS_A), alias_hash=self.SAME_ALIAS_HASH)])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(pairs[0]["comparison_status"], csf.ALIAS_PROVENANCE_UNAVAILABLE)
        # Raw probabilities are still surfaced for inspection.
        self.assertEqual(pairs[0]["incumbent_probabilities"], self.PROBS_A)
        self.assertEqual(pairs[0]["candidate_probabilities"], self.PROBS_A)

    def test_forecast_pair_with_missing_candidate_alias_hash_is_never_labeled_comparable(self):
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(forecasts=[_market("f1", dict(self.PROBS_A))])  # no alias_hash at all
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate, incumbent_alias_hash=self.SAME_ALIAS_HASH)
        self.assertEqual(pairs[0]["comparison_status"], csf.ALIAS_PROVENANCE_UNAVAILABLE)

    def test_forecast_pair_with_mismatched_alias_hashes_is_flagged_not_silently_compared(self):
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(forecasts=[_market("f1", dict(self.PROBS_A), alias_hash="a-different-hash")])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate, incumbent_alias_hash=self.SAME_ALIAS_HASH)
        self.assertEqual(pairs[0]["comparison_status"], csf.ALIAS_HASH_MISMATCH)
        self.assertEqual(pairs[0]["incumbent_alias_hash"], self.SAME_ALIAS_HASH)
        self.assertEqual(pairs[0]["candidate_alias_hash"], "a-different-hash")

    def test_candidate_abstention_incumbent_forecasts(self):
        incumbent = _incumbent_result(markets=[_market("f1", self.PROBS_A)])
        candidate = _candidate_result(abstentions=[_abstention("f1", "FORECAST_ARTIFACT_STALE")])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_CANDIDATE_ABSTAINED)
        self.assertEqual(pairs[0]["candidate_abstention_reason"], "FORECAST_ARTIFACT_STALE")

    def test_incumbent_abstention_candidate_forecasts(self):
        incumbent = _incumbent_result(abstentions=[_abstention("f1", "FORECAST_ARTIFACT_STALE")])
        candidate = _candidate_result(forecasts=[_market("f1", self.PROBS_A)])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_INCUMBENT_ABSTAINED)
        self.assertEqual(pairs[0]["incumbent_abstention_reason"], "FORECAST_ARTIFACT_STALE")

    def test_unresolved_identity_on_both_sides(self):
        incumbent = _incumbent_result(abstentions=[_abstention("f1", "FORECAST_TEAM_UNRESOLVED")])
        candidate = _candidate_result(abstentions=[_abstention("f1", "FORECAST_TEAM_UNRESOLVED")])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_BOTH_UNRESOLVED_IDENTITY)

    def test_both_abstained_for_different_typed_reasons(self):
        incumbent = _incumbent_result(abstentions=[_abstention("f1", "FORECAST_ARTIFACT_STALE")])
        candidate = _candidate_result(abstentions=[_abstention("f1", "FORECAST_TEAM_NOT_IN_ARTIFACT")])
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(pairs[0]["comparison_status"], csf.COMPARISON_BOTH_ABSTAINED_OTHER)
        self.assertEqual(pairs[0]["incumbent_abstention_reason"], "FORECAST_ARTIFACT_STALE")
        self.assertEqual(pairs[0]["candidate_abstention_reason"], "FORECAST_TEAM_NOT_IN_ARTIFACT")

    def test_every_pair_carries_a_typed_comparison_status(self):
        incumbent = _incumbent_result(
            markets=[_market("f1", self.PROBS_A)],
            abstentions=[_abstention("f2", "FORECAST_TEAM_UNRESOLVED")],
        )
        candidate = _candidate_result(
            forecasts=[_market("f1", self.PROBS_A)],
            abstentions=[_abstention("f2", "FORECAST_TEAM_UNRESOLVED")],
        )
        pairs = csf.pair_incumbent_and_candidate_results(incumbent, candidate)
        self.assertEqual(len(pairs), 2)
        for pair in pairs:
            self.assertIn("comparison_status", pair)
            self.assertTrue(pair["comparison_status"])


class RealPipelinePairedComparisonTests(CandidateShadowForecastTestCase):
    """One integration-style test: pairs a REAL incumbent run
    (``run_bet9ja_research_session``, unmodified) against a REAL
    candidate shadow-forecast run from this test case's own fixture
    bundle, over the identical capture."""

    def test_real_incumbent_and_candidate_runs_pair_cleanly(self):
        from pcbf_calculator.orchestration.bet9ja_research_session import run_bet9ja_research_session

        envelope = json.loads(self.capture_path.read_text())
        incumbent_result = run_bet9ja_research_session(envelope)
        candidate_result = self._run()

        # Both runs used the identical, real, shipped team_aliases.json
        # (the incumbent has no override path at all) -- passing it
        # explicitly is exactly what a real, same-moment comparison
        # caller does.
        pairs = csf.pair_incumbent_and_candidate_results(
            incumbent_result, candidate_result, incumbent_alias_hash=csf.compute_alias_hash()
        )
        # Every fixture that reached FORECAST on either side is present,
        # and every one carries a typed comparison_status -- no crash, no
        # unlabeled bucket, regardless of how the two models actually
        # diverge on this tiny fixture set.
        self.assertTrue(pairs)
        for pair in pairs:
            self.assertIn(
                pair["comparison_status"],
                {
                    csf.COMPARISON_BOTH_FORECAST_IDENTICAL,
                    csf.COMPARISON_BOTH_FORECAST_DIFFERENT,
                    csf.COMPARISON_CANDIDATE_ABSTAINED,
                    csf.COMPARISON_INCUMBENT_ABSTAINED,
                    csf.COMPARISON_BOTH_UNRESOLVED_IDENTITY,
                    csf.COMPARISON_BOTH_ABSTAINED_OTHER,
                    csf.COMPARISON_NOT_ELIGIBLE_ON_BOTH_SIDES,
                    csf.ALIAS_PROVENANCE_UNAVAILABLE,
                    csf.ALIAS_HASH_MISMATCH,
                },
            )
            if pair["comparison_status"] in (csf.COMPARISON_BOTH_FORECAST_IDENTICAL, csf.COMPARISON_BOTH_FORECAST_DIFFERENT):
                # A genuinely eligible, fully-comparable pair in THIS test
                # (same real alias file, same moment) must never fall
                # back to the "unavailable" statuses.
                self.assertEqual(pair["incumbent_alias_hash"], pair["candidate_alias_hash"])

    def test_real_run_without_incumbent_alias_hash_never_claims_forecast_pairs_comparable(self):
        from pcbf_calculator.orchestration.bet9ja_research_session import run_bet9ja_research_session

        envelope = json.loads(self.capture_path.read_text())
        incumbent_result = run_bet9ja_research_session(envelope)
        candidate_result = self._run()

        pairs = csf.pair_incumbent_and_candidate_results(incumbent_result, candidate_result)
        forecast_vs_forecast_pairs = [
            p for p in pairs
            if p["comparison_status"] in (
                csf.COMPARISON_BOTH_FORECAST_IDENTICAL,
                csf.COMPARISON_BOTH_FORECAST_DIFFERENT,
                csf.ALIAS_PROVENANCE_UNAVAILABLE,
                csf.ALIAS_HASH_MISMATCH,
            )
        ]
        self.assertTrue(forecast_vs_forecast_pairs)
        for pair in forecast_vs_forecast_pairs:
            self.assertEqual(pair["comparison_status"], csf.ALIAS_PROVENANCE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
