"""Tests for the manual-results evidence-preservation archiver
(``src/pcbf_calculator/orchestration/manual_evidence_archiver.py``).

No real network access anywhere here: every fetch goes through an
injected ``http_fetch`` stub (mirrors ``football_data_org_settlement``'s
own ``http_get`` injection pattern). SSRF/URL-validation logic
(``validate_https_url``) IS exercised for real against real hostnames
(``localhost``, a literal loopback IP, a real public one) since it never
performs a fetch itself -- only a DNS lookup / literal parse.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledgers import forecast_ledger  # noqa: E402

from pcbf_calculator.orchestration import manual_evidence_archiver as archiver  # noqa: E402


def _record_forecast(ledger_path: Path, fixture_id: str = "bxf_evidence_1", scheduled_date: str = "2026-09-20"):
    event = forecast_ledger.build_recorded_event(
        fixture_id=fixture_id,
        sport="SOCCER",
        league="Premier League",
        kickoff_utc=f"{scheduled_date}T14:00:00Z",
        market_type="1X2",
        offered_odds={"H": 1.9, "D": 3.4, "A": 4.3},
        classification="RESEARCH-MODEL",
        model_probabilities={"H": 0.6, "D": 0.25, "A": 0.15},
        model_version="soccer_1x2_elo_v1-test",
        artifact_hash="sha256:test",
        output_hash="sha256:test-output",
        selection_status="considered",
        stop_reason=None,
        competition_code="E0",
        resolved_home_team="Arsenal",
        resolved_away_team="Chelsea",
        scheduled_date=scheduled_date,
    )
    forecast_ledger.append_recorded(ledger_path, event)


def _envelope(fixture_id: str, source_url: str, result_status: str = "VERIFIED", home_score: int = 3, away_score: int = 1):
    return {
        "schema_version": "predictbot.manual-results-evidence.v1",
        "results": [
            {
                "fixture_id": fixture_id,
                "competition": "Premier League",
                "participants": {"home": "Arsenal", "away": "Chelsea"},
                "match_status": "FINISHED",
                "home_score": home_score,
                "away_score": away_score,
                "result_status": result_status,
                "verification_method": "manual_search",
                "review_reason": None,
                "sources": [
                    {
                        "source_name": "BBC Sport",
                        "source_type": "MAJOR_PUBLICATION",
                        "source_url": source_url,
                        "retrieved_at_utc": "2026-09-20T18:00:00Z",
                        "evidence_summary": "Arsenal beat Chelsea 3-1.",
                        "evidence_sha256": None,
                    }
                ],
            }
        ],
    }


def _stub_fetch(outcome: archiver.FetchOutcome):
    def _fetch(url, *args, **kwargs):
        return outcome

    return _fetch


_GOOD_PAGE = (
    "<html><body><h1>Match report</h1><p>Arsenal secured a commanding win over Chelsea in front of "
    "a packed house in 2026. The final score at full time was 3-1, sealing three points. "
    "It was a dominant display from start to finish, with the match ended after a tense final whistle."
    "</p></body></html>"
).encode("utf-8")


class ValidateHttpsUrlTests(unittest.TestCase):
    def test_plain_http_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("http://example.com/report")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_UNSUPPORTED_SCHEME)

    def test_file_scheme_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("file:///etc/passwd")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_UNSUPPORTED_SCHEME)

    def test_loopback_literal_ip_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("https://127.0.0.1/report")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_BLOCKED_DESTINATION)

    def test_localhost_hostname_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("https://localhost/report")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_BLOCKED_DESTINATION)

    def test_link_local_ip_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("https://169.254.169.254/latest/meta-data")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_BLOCKED_DESTINATION)

    def test_private_rfc1918_ip_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("https://10.0.0.5/report")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_BLOCKED_DESTINATION)

    def test_url_with_no_hostname_is_rejected(self):
        ok, reason, _detail = archiver.validate_https_url("https:///report")
        self.assertFalse(ok)
        self.assertEqual(reason, archiver.REASON_INVALID_URL)

    def test_a_public_ip_literal_is_accepted(self):
        # A literal IP resolves with no real DNS query (getaddrinfo
        # short-circuits for a literal address) -- keeps this test
        # network-independent, unlike resolving a real hostname would be.
        ok, reason, detail = archiver.validate_https_url("https://8.8.8.8/report")
        self.assertTrue(ok, detail)
        self.assertIsNone(reason)


class ContentSupportsResultTests(unittest.TestCase):
    def test_page_mentioning_both_teams_score_and_completion_marker_is_supported(self):
        text = archiver.extract_visible_text(_GOOD_PAGE)
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, "2026-09-20")
        self.assertTrue(ok, detail)

    def test_page_missing_the_away_team_is_rejected(self):
        text = "Arsenal won 3-1 at full time in a dominant match ended performance."
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, "2026-09-20")
        self.assertFalse(ok)
        self.assertIn("Chelsea", detail)

    def test_page_with_wrong_scoreline_is_rejected(self):
        text = "Arsenal 2-1 Chelsea. Full time report: a narrow win, match ended."
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, "2026-09-20")
        self.assertFalse(ok)
        self.assertIn("3-1", detail)

    def test_page_with_no_completion_marker_reads_as_upcoming_and_is_rejected(self):
        text = "Arsenal vs Chelsea kicks off this weekend in 2026, expected scoreline pundits favour is 3-1."
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, "2026-09-20")
        self.assertFalse(ok)
        self.assertIn("completed-match", detail)

    def test_wrong_year_is_rejected_when_a_scheduled_date_is_supplied(self):
        text = "Arsenal 3-1 Chelsea, full time, back in 2019, match ended long ago."
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, "2026-09-20")
        self.assertFalse(ok)
        self.assertIn("2026", detail)

    def test_no_scheduled_date_skips_the_year_check_but_still_requires_the_rest(self):
        text = "Arsenal 3-1 Chelsea, full time, match ended."
        ok, detail = archiver.content_supports_result(text, "Arsenal", "Chelsea", 3, 1, None)
        self.assertTrue(ok, detail)


class ExtractVisibleTextTests(unittest.TestCase):
    def test_scripts_and_styles_are_stripped(self):
        html = b"<html><head><style>.a{color:red}</style></head><body><script>evil()</script>Hello world</body></html>"
        text = archiver.extract_visible_text(html)
        self.assertNotIn("evil", text)
        self.assertNotIn("color", text)
        self.assertIn("Hello world", text)


class ArchiveEvidenceBatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.input_dir = self.tmp_path / "input"
        self.input_dir.mkdir()
        self.archive_dir = self.tmp_path / "manual_evidence"
        self.ledger_dir = self.tmp_path / "ledger"
        self.ledger_dir.mkdir()
        self.ledger_path = self.ledger_dir / forecast_ledger.DEFAULT_FILENAME
        _record_forecast(self.ledger_path)
        self.schema = archiver.load_manual_results_evidence_schema()
        self.empty_manifest = {"schema_version": archiver.SCHEMA_VERSION_MANIFEST, "entries": []}

    def tearDown(self):
        self._tmp.cleanup()

    def _write_envelope(self, name: str, envelope: dict) -> Path:
        path = self.input_dir / name
        path.write_text(json.dumps(envelope), encoding="utf-8")
        return path

    def test_a_successfully_fetched_and_validated_source_is_archived(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report"))
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        new_entries, rejected, counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome)
        )
        self.assertEqual(rejected, [])
        self.assertEqual(len(new_entries), 1)
        entry = new_entries[0]
        self.assertEqual(entry["requested_url"], "https://news.example.com/report")
        self.assertEqual(entry["final_url"], "https://news.example.com/report")
        self.assertEqual(entry["http_status"], 200)
        self.assertEqual(counts["sources_archived"], 1)

        archived_path = self.archive_dir / entry["archive_path"]
        self.assertTrue(archived_path.is_file())
        self.assertEqual(archived_path.read_bytes(), _GOOD_PAGE)
        import hashlib

        self.assertEqual(entry["sha256"], hashlib.sha256(_GOOD_PAGE).hexdigest())

    def test_a_source_url_already_in_the_manifest_is_skipped_never_refetched(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report"))
        manifest = {
            "schema_version": archiver.SCHEMA_VERSION_MANIFEST,
            "entries": [
                {
                    "requested_url": "https://news.example.com/report",
                    "final_url": "https://news.example.com/report",
                    "retrieved_at_utc": "2026-09-19T00:00:00Z",
                    "http_status": 200,
                    "content_type": "text/html",
                    "byte_count": 10,
                    "sha256": "0" * 64,
                    "archive_path": "already-there.bin",
                }
            ],
        }

        calls = []

        def _fetch_that_must_not_be_called(url, *a, **k):
            calls.append(url)
            raise AssertionError("should never fetch an already-archived URL")

        new_entries, rejected, counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, manifest, self.schema, self.ledger_path, http_fetch=_fetch_that_must_not_be_called
        )
        self.assertEqual(calls, [])
        self.assertEqual(new_entries, [])
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], archiver.REASON_ALREADY_ARCHIVED)
        self.assertEqual(counts["sources_already_archived"], 1)

    def test_a_fetch_failure_is_reported_with_its_typed_reason_never_archived(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/gone"))
        outcome = archiver.FetchOutcome(ok=False, reason=archiver.REASON_FETCH_FAILED, detail="HTTP 404: Not Found")
        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome)
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_FETCH_FAILED)
        self.assertFalse(self.archive_dir.exists())

    def test_a_blocked_ssrf_destination_never_reaches_http_fetch_at_all(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://127.0.0.1/report"))

        def _fetch_that_must_not_be_called(url, *a, **k):
            raise AssertionError("archive_evidence_batch should not call http_fetch for a pre-blocked URL")

        # default_http_fetch itself blocks -- here we exercise the real
        # default, not a stub, to prove validate_https_url runs before any
        # socket is ever opened.
        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=archiver.default_http_fetch
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_BLOCKED_DESTINATION)

    def test_a_javascript_only_near_empty_page_is_rejected_before_content_validation(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/spa"))
        tiny_body = b"<html><body><div id='root'></div><script>renderApp()</script></body></html>"
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/spa", http_status=200, content_type="text/html", body=tiny_body
        )
        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome)
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_JS_ONLY_OR_EMPTY)

    def test_a_page_that_does_not_actually_support_the_claimed_result_is_rejected(self):
        envelope = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/unrelated"))
        unrelated_body = (
            b"<html><body>" + b"Totally unrelated football news about a different match entirely. " * 10 + b"</body></html>"
        )
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/unrelated", http_status=200, content_type="text/html", body=unrelated_body
        )
        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome)
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_CONTENT_DOES_NOT_SUPPORT_RESULT)

    def test_a_needs_review_row_is_rejected_as_not_verified_never_fetched(self):
        envelope = self._write_envelope(
            "e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report", result_status="NEEDS_REVIEW")
        )

        def _fetch_that_must_not_be_called(url, *a, **k):
            raise AssertionError("a NEEDS_REVIEW row's sources must never be fetched")

        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [envelope], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_fetch_that_must_not_be_called
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_NOT_VERIFIED)

    def test_an_invalid_envelope_is_rejected_as_a_whole_never_partially_processed(self):
        path = self.input_dir / "bad.json"
        path.write_text(json.dumps({"schema_version": "predictbot.manual-results-evidence.v1"}), encoding="utf-8")

        new_entries, rejected, _counts = archiver.archive_evidence_batch(
            [path], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=archiver.default_http_fetch
        )
        self.assertEqual(new_entries, [])
        self.assertEqual(rejected[0]["reason"], archiver.REASON_INVALID_ENVELOPE)

    def test_content_addressed_archive_path_is_never_rewritten_once_it_exists(self):
        envelope1 = self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report-a"))
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report-a", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        new_entries_1, _rejected, _counts = archiver.archive_evidence_batch(
            [envelope1], self.archive_dir, self.empty_manifest, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome)
        )
        archived_path = self.archive_dir / new_entries_1[0]["archive_path"]
        original_mtime = archived_path.stat().st_mtime_ns

        # A second, unrelated source citing byte-identical content (a
        # realistic case: two outlets syndicating the same wire copy)
        # must not rewrite the same content-addressed file.
        manifest_with_first = {"schema_version": archiver.SCHEMA_VERSION_MANIFEST, "entries": new_entries_1}
        envelope2 = self._write_envelope("e2.json", _envelope("bxf_evidence_1", "https://news.example.com/report-b"))
        outcome2 = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report-b", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        archiver.archive_evidence_batch(
            [envelope2], self.archive_dir, manifest_with_first, self.schema, self.ledger_path, http_fetch=_stub_fetch(outcome2)
        )
        self.assertEqual(archived_path.stat().st_mtime_ns, original_mtime)


class RunArchiveSessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.input_dir = self.tmp_path / "input"
        self.input_dir.mkdir()
        self.archive_dir = self.tmp_path / "manual_evidence"
        self.output_dir = self.tmp_path / "output"
        self.ledger_dir = self.tmp_path / "ledger"
        self.ledger_dir.mkdir()
        _record_forecast(self.ledger_dir / forecast_ledger.DEFAULT_FILENAME)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_envelope(self, name: str, envelope: dict) -> Path:
        path = self.input_dir / name
        path.write_text(json.dumps(envelope), encoding="utf-8")
        return path

    def test_end_to_end_run_writes_manifest_and_report_files(self):
        self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report"))
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        result = archiver.run_archive_session(
            self.input_dir, self.archive_dir, self.output_dir, ledger_dir=self.ledger_dir, http_fetch=_stub_fetch(outcome)
        )
        self.assertEqual(result["archive_report"]["counts"]["sources_archived"], 1)

        manifest_path = self.archive_dir / "archive-manifest.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(len(manifest["entries"]), 1)
        self.assertEqual(manifest["schema_version"], "manual-evidence-archive-manifest.v1")

        self.assertTrue((self.output_dir / "archive-report.json").is_file())
        self.assertTrue((self.output_dir / "archive-rejected.json").is_file())

    def test_manifest_is_not_rewritten_when_nothing_new_was_archived(self):
        self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/gone"))
        outcome = archiver.FetchOutcome(ok=False, reason=archiver.REASON_FETCH_FAILED, detail="HTTP 404: Not Found")
        archiver.run_archive_session(
            self.input_dir, self.archive_dir, self.output_dir, ledger_dir=self.ledger_dir, http_fetch=_stub_fetch(outcome)
        )
        self.assertFalse((self.archive_dir / "archive-manifest.json").exists())

    def test_a_second_run_against_an_already_populated_manifest_appends_never_overwrites(self):
        self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report-a"))
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report-a", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        archiver.run_archive_session(
            self.input_dir, self.archive_dir, self.output_dir, ledger_dir=self.ledger_dir, http_fetch=_stub_fetch(outcome)
        )

        input_dir_2 = self.tmp_path / "input2"
        input_dir_2.mkdir()
        path2 = input_dir_2 / "e2.json"
        path2.write_text(json.dumps(_envelope("bxf_evidence_1", "https://news.example.com/report-b")), encoding="utf-8")
        outcome2 = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report-b", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        archiver.run_archive_session(
            input_dir_2, self.archive_dir, self.output_dir, ledger_dir=self.ledger_dir, http_fetch=_stub_fetch(outcome2)
        )

        manifest = json.loads((self.archive_dir / "archive-manifest.json").read_text())
        self.assertEqual(len(manifest["entries"]), 2)
        urls = {e["requested_url"] for e in manifest["entries"]}
        self.assertEqual(urls, {"https://news.example.com/report-a", "https://news.example.com/report-b"})

    def test_converter_accepts_this_archiver_own_output_end_to_end(self):
        """The whole point: convert-manual-results-evidence must accept a
        manifest entry this module itself produced, recomputing the SAME
        sha256 from the SAME archived bytes on disk."""

        from pcbf_calculator.orchestration import manual_results_evidence_converter as conv

        self._write_envelope("e1.json", _envelope("bxf_evidence_1", "https://news.example.com/report"))
        outcome = archiver.FetchOutcome(
            ok=True, final_url="https://news.example.com/report", http_status=200, content_type="text/html", body=_GOOD_PAGE
        )
        archiver.run_archive_session(
            self.input_dir, self.archive_dir, self.output_dir, ledger_dir=self.ledger_dir, http_fetch=_stub_fetch(outcome)
        )

        conversion_output_dir = self.tmp_path / "conversion-output"
        conv_result = conv.run_conversion_session(
            self.input_dir,
            self.ledger_dir,
            conversion_output_dir,
            archive_manifest_path=self.archive_dir / "archive-manifest.json",
        )
        self.assertEqual(conv_result["conversion_report"]["counts"]["converted"], 1)
        self.assertEqual(conv_result["rejected"], [])


if __name__ == "__main__":
    unittest.main()
