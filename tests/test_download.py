"""Tests for data_pipeline/download.py's manifest loading, season-code
enumeration, and retry/backoff/typed-failure-classification logic.

No real network calls are made here — `download_one`'s retry/backoff and
typed-error-classification behavior is tested by monkeypatching
`urllib.request.urlopen` to raise controlled, specific exceptions, so this
test suite runs deterministically offline. The actual live-network attempt
against football-data.co.uk (and its outcome in this sandbox) is a
separate, already-executed run recorded in `data_pipeline/retrieval_log.json`
and discussed in `data_pipeline/FEASIBILITY_DECISION.md` — it is not
re-run as part of the automated test suite.
"""

import socket
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.download import (
    DEFAULT_MANIFEST_PATH,
    ERROR_CONNECTION,
    ERROR_HTTP,
    ERROR_TIMEOUT,
    download_one,
    load_manifest,
    season_codes,
)


class SeasonCodesTests(unittest.TestCase):
    def test_single_season(self):
        self.assertEqual(season_codes("2324", "2324"), ["2324"])

    def test_crosses_the_1999_2000_boundary(self):
        codes = season_codes("9899", "0001")
        self.assertEqual(codes, ["9899", "9900", "0001"])

    def test_multi_season_range(self):
        codes = season_codes("9394", "9697")
        self.assertEqual(codes, ["9394", "9495", "9596", "9697"])

    def test_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            season_codes("2324", "9394")


class ManifestLoadingTests(unittest.TestCase):
    def test_manifest_has_five_approved_leagues(self):
        rows = load_manifest(DEFAULT_MANIFEST_PATH)
        codes = {row["league_code"] for row in rows}
        self.assertEqual(codes, {"E0", "D1", "SP1", "I1", "F1"})

    def test_every_row_declares_required_fields(self):
        rows = load_manifest(DEFAULT_MANIFEST_PATH)
        for row in rows:
            for field in ("league_code", "league_name", "url_pattern", "file_type", "first_season", "latest_completed_season"):
                self.assertIn(field, row)


class DownloadOneRetryAndTypedFailureTests(unittest.TestCase):
    def test_http_error_is_typed_and_not_retried_into_success(self):
        sleeps = []
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None)):
            outcome = download_one(
                "E0", "Premier League", "9091", "https://example.invalid/9091/E0.csv",
                Path("/tmp/does-not-matter"), max_attempts=3, sleep_fn=lambda s: sleeps.append(s),
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_type, ERROR_HTTP)
        self.assertIn("404", outcome.error_detail)
        self.assertEqual(outcome.attempts_made, 3)
        self.assertEqual(len(sleeps), 2)  # backoff happens between attempts 1-2 and 2-3, not after the last

    def test_timeout_is_typed_distinctly_from_http_error(self):
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timed out")):
            outcome = download_one(
                "E0", "Premier League", "9091", "https://example.invalid/9091/E0.csv",
                Path("/tmp/does-not-matter"), max_attempts=2, sleep_fn=lambda s: None,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_type, ERROR_TIMEOUT)

    def test_connection_error_is_typed_distinctly(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Tunnel connection failed: 403 Forbidden")):
            outcome = download_one(
                "E0", "Premier League", "9091", "https://example.invalid/9091/E0.csv",
                Path("/tmp/does-not-matter"), max_attempts=1, sleep_fn=lambda s: None,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_type, ERROR_CONNECTION)
        self.assertIn("403", outcome.error_detail)
        self.assertIsNone(outcome.http_status)
        self.assertIsNone(outcome.final_url)
        self.assertIsNone(outcome.content_length)

    def test_success_records_status_final_url_length_and_sha256(self, ):
        import io

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"Date,HomeTeam\n01/01/2020,X\n"

            def geturl(self):
                return "https://example.invalid/final/9091/E0.csv"

            def getcode(self):
                return 200

        import shutil
        import tempfile

        raw_dir = Path(tempfile.mkdtemp(dir=str(REPO_ROOT)))
        try:
            with patch("urllib.request.urlopen", return_value=FakeResponse()):
                outcome = download_one(
                    "E0", "Premier League", "9091", "https://example.invalid/9091/E0.csv",
                    raw_dir, max_attempts=3, sleep_fn=lambda s: None,
                )
        finally:
            shutil.rmtree(raw_dir, ignore_errors=True)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.http_status, 200)
        self.assertEqual(outcome.final_url, "https://example.invalid/final/9091/E0.csv")
        self.assertEqual(outcome.content_length, len(b"Date,HomeTeam\n01/01/2020,X\n"))
        self.assertIsNotNone(outcome.sha256)
        self.assertIsNotNone(outcome.retrieved_at_utc)


if __name__ == "__main__":
    unittest.main()
