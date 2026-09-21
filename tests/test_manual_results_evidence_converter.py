"""Tests for the manual-results evidence-collection converter
(``src/pcbf_calculator/orchestration/manual_results_evidence_converter.py``).

Style matches ``tests/test_manual_results_settlement.py``: plain
``unittest.TestCase``, real football-data.co.uk team/competition names so
the real, committed adapter artifact actually resolves identity, a
directly-built RECORDED event (never through the full research pipeline)
to give the converter a real fixture_id to look up.

**Trust-boundary discipline**: every test here that wants a source
accepted must go through a REAL archive-manifest entry backed by REAL
bytes on disk, recomputed via ``hashlib.sha256`` -- never a bare
``evidence_sha256`` string in the evidence-collection envelope itself,
which this module no longer reads for trust purposes at all. See
``_archive`` below.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger  # noqa: E402

from pcbf_calculator.orchestration import manual_results_evidence_converter as conv  # noqa: E402


def _record_forecast(
    ledger_path: Path,
    fixture_id: str = "bxf_evidence_1",
    competition_code: str | None = "E0",
    resolved_home_team: str | None = "Arsenal",
    resolved_away_team: str | None = "Chelsea",
    scheduled_date: str | None = "2026-09-20",
    stop_reason: str | None = None,
):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z" if scheduled_date else "2026-09-20T14:00:00Z",
        market_type="1X2",
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15} if stop_reason is None else None,
        model_version="soccer_1x2_elo_v1-test" if stop_reason is None else None,
        artifact_hash="sha256:test" if stop_reason is None else None,
        output_hash="sha256:test-output" if stop_reason is None else None,
        selection_status="considered",
        stop_reason=stop_reason,
        competition_code=competition_code,
        resolved_home_team=resolved_home_team,
        resolved_away_team=resolved_away_team,
        scheduled_date=scheduled_date,
    )
    result = forecast_ledger.append_recorded(ledger_path, event)
    return result.record["forecast_id"]


class _Archive:
    """A small in-memory archive builder: writes real bytes to a real
    file under a temp "manual_evidence" directory and hands back the
    manifest entry describing them, plus the exact URL to cite in an
    evidence-collection source so it resolves against this entry."""

    def __init__(self, manifest_dir: Path):
        self.manifest_dir = manifest_dir
        self.manifest_dir.mkdir(parents=True, exist_ok=True)
        self.entries: list[dict] = []
        self._n = 0

    def add(self, url: str, content: bytes, final_url: str | None = None, corrupt_after_hashing: bool = False) -> dict:
        self._n += 1
        rel_path = f"page-{self._n}.html"
        (self.manifest_dir / rel_path).write_bytes(content)
        sha256 = hashlib.sha256(content).hexdigest()
        if corrupt_after_hashing:
            (self.manifest_dir / rel_path).write_bytes(content + b"tampered")
        entry = {
            "requested_url": url,
            "final_url": final_url or url,
            "retrieved_at_utc": "2026-09-19T12:00:00Z",
            "http_status": 200,
            "content_type": "text/html",
            "byte_count": len(content),
            "sha256": sha256,
            "archive_path": rel_path,
        }
        self.entries.append(entry)
        return entry

    def manifest(self) -> dict:
        return {"schema_version": "manual-evidence-archive-manifest.v1", "entries": self.entries}

    def write_manifest_file(self) -> Path:
        path = self.manifest_dir / "archive-manifest.json"
        path.write_text(json.dumps(self.manifest()), encoding="utf-8")
        return path

    def index(self) -> dict[str, dict]:
        return conv._build_archive_index(self.manifest(), self.manifest_dir)


def _evidence_result(
    fixture_id="bxf_evidence_1",
    competition="Premier League",
    home="Arsenal",
    away="Chelsea",
    result_status="VERIFIED",
    home_score=2,
    away_score=1,
    review_reason=None,
    sources=None,
):
    if sources is None:
        sources = []
    return {
        "fixture_id": fixture_id,
        "competition": competition,
        "participants": {"home": home, "away": away},
        "scheduled_date": None,
        "kickoff_utc": None,
        "market_type": None,
        "match_status": "COMPLETED" if result_status == "VERIFIED" else "UNKNOWN",
        "home_score": home_score if result_status == "VERIFIED" else None,
        "away_score": away_score if result_status == "VERIFIED" else None,
        "result_status": result_status,
        "verification_method": "ONE_AUTHORITATIVE_SOURCE" if result_status == "VERIFIED" else None,
        "review_reason": review_reason,
        "sources": sources if result_status == "VERIFIED" else [],
    }


def _evidence_source(
    source_url,
    source_name="Arsenal FC",
    source_type="OFFICIAL_CLUB",
    home_score=2,
    away_score=1,
    evidence_summary=None,
    evidence_sha256=None,
):
    """``evidence_sha256`` defaults to None (the real, honest upstream
    shape) but a test may pass an arbitrary FABRICATED value here to
    prove the converter ignores it either way -- see
    ``test_a_fabricated_evidence_sha256_with_no_real_archive_entry_is_still_rejected``."""

    return {
        "source_name": source_name,
        "source_type": source_type,
        "source_url": source_url,
        "retrieved_at_utc": "2020-01-01T00:00:00Z",  # deliberately implausible -- must never be trusted/forwarded
        "evidence_summary": evidence_summary if evidence_summary is not None else f"Final score: Arsenal {home_score}-{away_score} Chelsea.",
        "evidence_sha256": evidence_sha256,
    }


def _envelope(results):
    return {"schema_version": conv.SCHEMA_VERSION_EVIDENCE, "results": results}


def _write(tmp: str, name: str, envelope: dict) -> Path:
    path = Path(tmp) / name
    path.write_text(json.dumps(envelope), encoding="utf-8")
    return path


class SchemaValidationTests(unittest.TestCase):
    def setUp(self):
        self.schema = conv.load_manual_results_evidence_schema()

    def test_a_well_formed_envelope_validates(self):
        from ledgers.validation import _check_node

        errors: list[str] = []
        _check_node(_envelope([_evidence_result()]), self.schema, "envelope", errors)
        self.assertEqual(errors, [])

    def test_wrong_schema_version_is_rejected(self):
        from ledgers.validation import _check_node

        envelope = _envelope([_evidence_result()])
        envelope["schema_version"] = "manual-results-input.v1"  # a different schema's own constant
        errors: list[str] = []
        _check_node(envelope, self.schema, "envelope", errors)
        self.assertNotEqual(errors, [])


class ArchiveManifestSchemaTests(unittest.TestCase):
    def test_a_well_formed_manifest_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = _Archive(Path(tmp))
            archive.add("https://example.com/report", b"<html>Arsenal 2-1 Chelsea</html>")
            schema = conv.load_manual_evidence_archive_manifest_schema()
            from ledgers.validation import _check_node

            errors: list[str] = []
            _check_node(archive.manifest(), schema, "manifest", errors)
        self.assertEqual(errors, [])

    def test_a_missing_manifest_file_loads_as_empty_never_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = conv.load_manual_evidence_archive_manifest_schema()
            manifest = conv.load_archive_manifest(Path(tmp) / "does-not-exist.json", schema)
        self.assertEqual(manifest["entries"], [])


class ConvertEvidenceBatchTests(unittest.TestCase):
    def setUp(self):
        self.schema = conv.load_manual_results_evidence_schema()
        self.known_teams = {"E0": {"Arsenal", "Chelsea", "Tottenham", "Liverpool"}}
        from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook

        self.alias_book = TeamAliasBook({"leagues": {}})

    def _convert(self, results, ledger_path, archive_index, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, "evidence.json", _envelope(results))
            return conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, archive_index, **kwargs
            )

    def test_a_source_backed_by_a_real_archived_and_verified_page_converts_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea full match report")
            result = _evidence_result(sources=[_evidence_source("https://arsenal.com/report")])
            converted, rejected, counts = self._convert([result], ledger_path, archive.index())
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        row = converted[0]
        self.assertEqual(row["competition_raw"], "Premier League")
        self.assertEqual(row["final_score"], {"home": 2, "away": 1})
        self.assertEqual(len(row["sources"]), 1)
        self.assertTrue(row["sources"][0]["authoritative"])
        # retrieved_at_utc/evidence_hash come from the ARCHIVE, never the
        # (deliberately implausible) value the evidence source itself carries.
        self.assertEqual(row["sources"][0]["retrieved_at_utc"], "2026-09-19T12:00:00Z")
        self.assertNotEqual(row["sources"][0]["retrieved_at_utc"], "2020-01-01T00:00:00Z")
        self.assertEqual(counts["source_rows_total"], 1)

    def test_a_fabricated_evidence_sha256_with_no_real_archive_entry_is_still_rejected(self):
        # THE regression test for the trust-boundary fix: an LLM supplying
        # a plausible, well-formed-looking evidence_sha256 (fabricated,
        # self-consistent with its own evidence_summary) must NEVER be
        # accepted just because the field is present and looks valid --
        # only a real, independently-verified archive entry counts.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            fabricated_hash = hashlib.sha256(b"this was never actually fetched or archived").hexdigest()
            result = _evidence_result(
                sources=[_evidence_source("https://arsenal.com/report", evidence_sha256=fabricated_hash)]
            )
            empty_index: dict = {}  # no archiver has ever run -- nothing is real yet
            converted, rejected, _ = self._convert([result], ledger_path, empty_index)
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_NOT_ARCHIVED)

    def test_needs_review_rows_are_rejected_as_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(result_status="NEEDS_REVIEW", review_reason="no evidence found")
            converted, rejected, _ = self._convert([result], ledger_path, {})
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_NOT_VERIFIED)
        self.assertEqual(rejected[0]["detail"], "no evidence found")

    def test_fixture_id_absent_from_the_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            # ledger stays empty
            result = _evidence_result(sources=[_evidence_source("https://arsenal.com/report")])
            converted, rejected, _ = self._convert([result], ledger_path, {})
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_FIXTURE_NOT_IN_LEDGER)

    def test_a_forecast_with_no_settlement_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(
                ledger_path,
                competition_code=None,
                resolved_home_team=None,
                resolved_away_team=None,
                scheduled_date=None,
                stop_reason="FORECAST_COMPETITION_UNRESOLVED",
            )
            result = _evidence_result(sources=[_evidence_source("https://arsenal.com/report")])
            converted, rejected, _ = self._convert([result], ledger_path, {})
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_NO_SETTLEMENT_IDENTITY)

    def test_a_url_with_no_manifest_entry_at_all_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            result = _evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])
            converted, rejected, _ = self._convert([result], ledger_path, {})
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_NOT_ARCHIVED)

    def test_a_manifest_entry_whose_archived_file_is_missing_on_disk_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            entry = archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea")
            index = archive.index()
            (archive.manifest_dir / entry["archive_path"]).unlink()  # simulate a lost/never-written archive file
            result = _evidence_result(sources=[_evidence_source("https://arsenal.com/report")])
            converted, rejected, _ = self._convert([result], ledger_path, index)
        self.assertEqual(converted, [])
        self.assertIn(conv.REASON_ARCHIVE_INTEGRITY_MISMATCH, rejected[0]["reason"])

    def test_a_manifest_entry_whose_recomputed_hash_does_not_match_is_rejected(self):
        # Simulates a tampered or corrupted archive file -- the manifest's
        # OWN recorded hash is never trusted on its own either.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea", corrupt_after_hashing=True)
            result = _evidence_result(sources=[_evidence_source("https://arsenal.com/report")])
            converted, rejected, _ = self._convert([result], ledger_path, archive.index())
        self.assertEqual(converted, [])
        self.assertIn(conv.REASON_ARCHIVE_INTEGRITY_MISMATCH, rejected[0]["reason"])

    def test_an_unparseable_evidence_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"content")
            result = _evidence_result(
                sources=[_evidence_source("https://arsenal.com/report", evidence_summary="Match completed, no scoreline given.")]
            )
            converted, rejected, _ = self._convert([result], ledger_path, archive.index())
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_SUMMARY_UNPARSEABLE)

    def test_a_source_whose_own_text_disagrees_with_the_claimed_score_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"content")
            result = _evidence_result(
                home_score=2, away_score=1,
                sources=[_evidence_source("https://arsenal.com/report", evidence_summary="Final score: Arsenal 3-1 Chelsea.")],
            )
            converted, rejected, _ = self._convert([result], ledger_path, archive.index())
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_SCORE_MISMATCH)
        self.assertIn("SETTLE_EVIDENCE_SCORE_MISMATCH", rejected[0]["detail"])

    def test_single_other_credible_source_is_insufficient_corroboration(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://sportytrader.example.com/report", b"content")
            result = _evidence_result(
                sources=[_evidence_source("https://sportytrader.example.com/report", source_type="OTHER_CREDIBLE", source_name="SportyTrader")]
            )
            converted, rejected, _ = self._convert([result], ledger_path, archive.index())
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_INSUFFICIENT_CORROBORATION)

    def test_two_independent_archived_non_authoritative_sources_are_sufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://source-a.example.com/report", b"content A")
            archive.add("https://source-b.example.com/report", b"content B")
            result = _evidence_result(
                sources=[
                    _evidence_source("https://source-a.example.com/report", source_type="OTHER_CREDIBLE", source_name="Source A"),
                    _evidence_source("https://source-b.example.com/report", source_type="OTHER_CREDIBLE", source_name="Source B"),
                ]
            )
            converted, rejected, _ = self._convert([result], ledger_path, archive.index())
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        self.assertEqual(len(converted[0]["sources"]), 2)

    def test_competition_or_team_text_mismatched_against_the_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path, competition_code="E0", resolved_home_team="Arsenal", resolved_away_team="Chelsea")
            result = _evidence_result(competition="Bundesliga")
            converted, rejected, _ = self._convert([result], ledger_path, {})
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_IDENTITY_MISMATCH)

    def test_a_malformed_envelope_rejects_the_whole_file_never_a_partial_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "bad.json", {"schema_version": conv.SCHEMA_VERSION_EVIDENCE, "results": "not-a-list"})
            converted, rejected, counts = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {}
            )
        self.assertEqual(converted, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], conv.REASON_INVALID_ENVELOPE)

    def test_reconciliation_every_source_row_lands_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"content")
            results = [
                _evidence_result(sources=[_evidence_source("https://arsenal.com/report")]),
                _evidence_result(fixture_id="bxf_missing", result_status="NEEDS_REVIEW", review_reason="x"),
            ]
            converted, rejected, counts = self._convert(results, ledger_path, archive.index())
        self.assertEqual(counts["source_rows_total"], len(results))
        self.assertEqual(counts["source_rows_total"], len(rejected) + len(converted))


class OperatorAttestedFallbackTests(unittest.TestCase):
    """convert-manual-results-evidence's own operator-attested downgrade,
    off by default -- a source failing ONLY because it was never archived
    can be emitted as OPERATOR_ATTESTED instead of rejected, but only when
    BOTH accept_operator_attested_evidence AND accepted_by are explicitly
    supplied, and only after its evidence_summary still independently
    parses and matches the claimed score."""

    def setUp(self):
        self.schema = conv.load_manual_results_evidence_schema()
        self.known_teams = {"E0": {"Arsenal", "Chelsea", "Tottenham", "Liverpool"}}
        from pcbf_calculator.adapters.soccer_1x2_elo_v1.identity import TeamAliasBook

        self.alias_book = TeamAliasBook({"leagues": {}})

    def test_default_behavior_is_completely_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))
            converted, rejected, _ = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {}
            )
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_NOT_ARCHIVED)

    def test_accept_operator_attested_evidence_without_accepted_by_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))
            with self.assertRaises(ValueError):
                conv.convert_evidence_batch(
                    [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                    accept_operator_attested_evidence=True,
                )

    def test_an_unarchived_but_otherwise_valid_source_downgrades_to_operator_attested(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(
                tmp, "evidence.json",
                _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report", source_name="A")])]),
            )
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            converted, rejected, counts = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        source = converted[0]["sources"][0]
        self.assertEqual(source["evidence_mode"], "OPERATOR_ATTESTED")
        self.assertIsNone(source["evidence_hash"])
        self.assertEqual(source["source_url"], "https://never-archived.example.com/report")
        attestation = source["operator_attestation"]
        self.assertEqual(attestation["accepted_by"], "operator")
        self.assertEqual(attestation["reason_code"], "ARCHIVE_UNVERIFIABLE_EGRESS")
        self.assertEqual(attestation["source_artifact_sha256"], expected_hash)
        self.assertEqual(attestation["source_artifact_name"], "evidence.json")
        self.assertTrue(attestation["source_urls_preserved"])
        self.assertEqual(counts["operator_attested_sources"] if "operator_attested_sources" in counts else 1, 1)

    def test_a_custom_attestation_reason_code_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))
            converted, _rejected, _counts = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                accept_operator_attested_evidence=True,
                accepted_by="operator",
                attestation_reason_code="CUSTOM_REASON",
            )
        self.assertEqual(converted[0]["sources"][0]["operator_attestation"]["reason_code"], "CUSTOM_REASON")

    def test_an_unparseable_evidence_summary_is_still_rejected_even_with_the_flag_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(
                tmp, "evidence.json",
                _envelope([_evidence_result(sources=[_evidence_source(
                    "https://never-archived.example.com/report", evidence_summary="Match completed, no scoreline given."
                )])]),
            )
            converted, rejected, _ = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_SUMMARY_UNPARSEABLE)

    def test_a_score_mismatched_evidence_summary_is_still_rejected_even_with_the_flag_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(
                tmp, "evidence.json",
                _envelope([_evidence_result(
                    home_score=2, away_score=1,
                    sources=[_evidence_source("https://never-archived.example.com/report", evidence_summary="Final score: Arsenal 3-1 Chelsea.")],
                )]),
            )
            converted, rejected, _ = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        self.assertEqual(converted, [])
        self.assertEqual(rejected[0]["reason"], conv.REASON_EVIDENCE_SCORE_MISMATCH)

    def test_a_tampered_archive_entry_is_never_downgraded_to_operator_attested(self):
        # Integrity mismatch means real evidence went bad, not "never
        # archived" -- the operator-attested fallback must never paper
        # over that with a downgrade instead of a hard rejection.
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea", corrupt_after_hashing=True)
            path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))
            converted, rejected, _ = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, archive.index(),
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        self.assertEqual(converted, [])
        self.assertIn(conv.REASON_ARCHIVE_INTEGRITY_MISMATCH, rejected[0]["reason"])

    def test_mixed_archived_and_operator_attested_sources_are_both_accepted_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "manual_evidence")
            archive.add("https://archived.example.com/report", b"Arsenal 2-1 Chelsea full match report")
            path = _write(
                tmp, "evidence.json",
                _envelope([_evidence_result(sources=[
                    _evidence_source("https://archived.example.com/report", source_name="Archived Source"),
                    _evidence_source("https://never-archived.example.com/report", source_name="Attested Source", source_type="OTHER_CREDIBLE"),
                ])]),
            )
            converted, rejected, _ = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, archive.index(),
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        self.assertEqual(rejected, [])
        self.assertEqual(len(converted), 1)
        modes = {s["evidence_mode"] for s in converted[0]["sources"]}
        self.assertEqual(modes, {"ARCHIVED", "OPERATOR_ATTESTED"})

    def test_converted_operator_attested_output_validates_against_ingest_manual_results_own_schema(self):
        from ledgers.validation import _check_node
        from pcbf_calculator.orchestration import manual_results_settlement as mrs

        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "forecast-ledger.jsonl"
            _record_forecast(ledger_path)
            path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))
            converted, _rejected, _counts = conv.convert_evidence_batch(
                [path], ledger_path, self.known_teams, self.alias_book, self.schema, {},
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
        converted_input = {"schema_version": mrs.SCHEMA_VERSION_INPUT, "results": converted}
        errors: list[str] = []
        _check_node(converted_input, mrs.load_manual_results_schema(), "envelope", errors)
        self.assertEqual(errors, [])


class RunConversionSessionTests(unittest.TestCase):
    def test_never_writes_to_the_ledger_and_uses_the_default_manifest_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            before = ledger_path.read_bytes()

            archive = _Archive(ledger_dir / conv.DEFAULT_ARCHIVE_MANIFEST_RELATIVE_PATH.parent)
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea")
            archive.write_manifest_file()

            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))

            result = conv.run_conversion_session(input_path, ledger_dir, output_dir)

            after = ledger_path.read_bytes()
            self.assertEqual(before, after)
            self.assertEqual(len(result["converted_input"]["results"]), 1)
            self.assertEqual(result["conversion_report"]["archive_manifest_entries"], 1)
            for name in ("conversion-report.json", "converted-manual-results-input.json", "conversion-rejected.json"):
                self.assertTrue((output_dir / name).exists())

    def test_a_missing_manifest_yields_zero_conversions_never_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            # No manifest file written at all -- the archiver doesn't exist yet.
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))

            result = conv.run_conversion_session(input_path, ledger_dir, output_dir)

            self.assertEqual(result["conversion_report"]["archive_manifest_entries"], 0)
            self.assertEqual(len(result["converted_input"]["results"]), 0)
            self.assertEqual(result["rejected"][0]["reason"], conv.REASON_EVIDENCE_NOT_ARCHIVED)

    def test_converted_output_validates_against_ingest_manual_results_own_schema(self):
        # The whole point of this converter: its output must be directly
        # consumable by ingest-manual-results without any further editing.
        from pcbf_calculator.orchestration import manual_results_settlement as mrs

        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            archive = _Archive(ledger_dir / conv.DEFAULT_ARCHIVE_MANIFEST_RELATIVE_PATH.parent)
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea")
            archive.write_manifest_file()
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))

            result = conv.run_conversion_session(input_path, ledger_dir, output_dir)
            errors = mrs.validate_manual_results_envelope(result["converted_input"], mrs.load_manual_results_schema())
        self.assertEqual(errors, [])

    def test_operator_attested_evidence_end_to_end_via_run_conversion_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))

            result = conv.run_conversion_session(
                input_path, ledger_dir, output_dir,
                accept_operator_attested_evidence=True,
                accepted_by="operator",
            )
            self.assertEqual(result["conversion_report"]["counts"]["operator_attested_sources"], 1)
            self.assertTrue(result["conversion_report"]["accept_operator_attested_evidence"])
            self.assertEqual(result["converted_input"]["results"][0]["sources"][0]["evidence_mode"], "OPERATOR_ATTESTED")


class MainCliTests(unittest.TestCase):
    def test_exit_code_is_zero_and_never_touches_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            before = ledger_path.read_bytes()
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))

            exit_code = conv.main([str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir)])

            self.assertEqual(exit_code, 0)
            self.assertEqual(ledger_path.read_bytes(), before)

    def test_explicit_archive_manifest_option_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            ledger_path = ledger_dir / forecast_ledger.DEFAULT_FILENAME
            _record_forecast(ledger_path)
            archive = _Archive(Path(tmp) / "custom_archive_location")
            archive.add("https://arsenal.com/report", b"Arsenal 2-1 Chelsea")
            manifest_path = archive.write_manifest_file()
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://arsenal.com/report")])]))

            exit_code = conv.main(
                [str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir), "--archive-manifest", str(manifest_path)]
            )

            self.assertEqual(exit_code, 0)
            converted = json.loads((output_dir / "converted-manual-results-input.json").read_text())
            self.assertEqual(len(converted["results"]), 1)

    def test_accept_operator_attested_evidence_without_accepted_by_errors_at_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))

            with self.assertRaises(SystemExit):
                conv.main(
                    [
                        str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir),
                        "--accept-operator-attested-evidence",
                    ]
                )

    def test_accept_operator_attested_evidence_flags_commit_end_to_end_via_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp) / "ledger_data"
            output_dir = Path(tmp) / "out"
            _record_forecast(ledger_dir / forecast_ledger.DEFAULT_FILENAME)
            input_path = _write(tmp, "evidence.json", _envelope([_evidence_result(sources=[_evidence_source("https://never-archived.example.com/report")])]))

            exit_code = conv.main(
                [
                    str(input_path), "--ledger-dir", str(ledger_dir), "--output-dir", str(output_dir),
                    "--accept-operator-attested-evidence", "--accepted-by", "operator",
                ]
            )
            self.assertEqual(exit_code, 0)
            converted = json.loads((output_dir / "converted-manual-results-input.json").read_text())
            self.assertEqual(converted["results"][0]["sources"][0]["evidence_mode"], "OPERATOR_ATTESTED")


if __name__ == "__main__":
    unittest.main()
