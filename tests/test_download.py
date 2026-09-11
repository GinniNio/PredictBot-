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
    ERROR_NOT_ATTEMPTED_HOST_BLOCKED,
    ERROR_NOT_FOUND,
    ERROR_TIMEOUT,
    download_one,
    load_manifest,
    run,
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
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 500, "Internal Server Error", {}, None)):
            outcome = download_one(
                "E0", "Premier League", "9091", "https://example.invalid/9091/E0.csv",
                Path("/tmp/does-not-matter"), max_attempts=3, sleep_fn=lambda s: sleeps.append(s),
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_type, ERROR_HTTP)
        self.assertIn("500", outcome.error_detail)
        self.assertEqual(outcome.attempts_made, 3)
        self.assertEqual(len(sleeps), 2)  # backoff happens between attempts 1-2 and 2-3, not after the last

    def test_404_is_typed_distinctly_as_not_found_never_generic_http_error(self):
        # A confirmed-absent file (this task's genuinely-absent case) must
        # never be conflated with a generic HTTP-level rejection.
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "Not Found", {}, None)):
            outcome = download_one(
                "E0", "Premier League", "2425", "https://example.invalid/2425/E0.csv",
                Path("/tmp/does-not-matter"), max_attempts=3, sleep_fn=lambda s: None,
            )
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.error_type, ERROR_NOT_FOUND)
        self.assertNotEqual(outcome.error_type, ERROR_HTTP)
        self.assertIn("404", outcome.error_detail)

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


class CircuitBreakerTests(unittest.TestCase):
    """Proves the circuit breaker trips well before all files in a manifest
    are individually retried 3x each — no real network call is made; every
    urlopen call is mocked to simulate a confirmed host-level denial
    (CONNECTION_ERROR) exactly as observed in this sandbox."""

    def _write_manifest(self, path: Path, num_leagues: int, num_seasons: int) -> None:
        lines = ["categories:"]
        for i in range(num_leagues):
            lines.append(f'  - league_code: "L{i}"')
            lines.append(f'    league_name: "League {i}"')
            lines.append('    url_pattern: "https://blocked.invalid/{season_code}/X.csv"')
            lines.append('    file_type: "CSV"')
            lines.append('    first_season: "9394"')
            last_year = 93 + num_seasons - 1
            lines.append(f'    latest_completed_season: "{last_year % 100:02d}{(last_year + 1) % 100:02d}"')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_circuit_breaker_trips_and_skips_the_remaining_files(self):
        import shutil
        import tempfile

        work_dir = Path(tempfile.mkdtemp(dir=str(REPO_ROOT)))
        try:
            manifest_path = work_dir / "manifest.yaml"
            # 3 leagues x 5 seasons = 15 total planned files, all against
            # the SAME host — a confirmed host-level denial on the first
            # two files must stop the other 13 from ever being attempted.
            self._write_manifest(manifest_path, num_leagues=3, num_seasons=5)
            raw_dir = work_dir / "raw"
            retrieval_log_path = work_dir / "retrieval_log.json"

            call_count = {"n": 0}

            def fake_urlopen(*args, **kwargs):
                call_count["n"] += 1
                raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = run(
                    manifest_path=manifest_path,
                    raw_dir=raw_dir,
                    retrieval_log_path=retrieval_log_path,
                    sleep_fn=lambda s: None,
                )

            summary = result["summary"]
            self.assertEqual(summary["total_manifest_entries"], 15)
            self.assertEqual(summary["succeeded"], 0)
            # Only the first 2 files (threshold=2) are actually attempted
            # over the network, 3 tries each -> 6 urlopen calls total, NOT
            # 15 files x 3 = 45.
            self.assertEqual(call_count["n"], 6)
            self.assertEqual(summary["network_attempts_made"], 2)
            self.assertEqual(summary["skipped_host_blocked"], 13)

            skipped = [f for f in result["failed_attempts"] if f["error_type"] == ERROR_NOT_ATTEMPTED_HOST_BLOCKED]
            self.assertEqual(len(skipped), 13)
            for f in skipped:
                self.assertEqual(f["attempts_made"], 0)

            attempted_and_failed = [f for f in result["failed_attempts"] if f["error_type"] == ERROR_CONNECTION]
            self.assertEqual(len(attempted_and_failed), 2)

            self.assertEqual(result["circuit_breaker"]["tripped_hosts"], ["blocked.invalid"])
            self.assertEqual(len(result["circuit_breaker"]["events"]), 1)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


class UnconfirmedSeasonsAreAttemptedTests(unittest.TestCase):
    """Proves `run()` attempts a manifest row's `unconfirmed_seasons`
    through the identical download/retry logic as the confirmed
    [first_season, latest_completed_season] range — never silently
    skipped/left unattempted — and that a genuinely-absent (404) season is
    reported distinctly from a network-level circuit-breaker skip."""

    def _write_manifest(self, path: Path) -> None:
        path.write_text(
            "\n".join(
                [
                    "categories:",
                    '  - league_code: "E0"',
                    '    league_name: "Premier League"',
                    '    url_pattern: "https://example.invalid/{season_code}/E0.csv"',
                    '    file_type: "CSV"',
                    '    first_season: "2223"',
                    '    latest_completed_season: "2324"',
                    '    unconfirmed_seasons: ["2425", "2526"]',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    def test_unconfirmed_seasons_are_actually_requested_over_the_network(self):
        import shutil
        import tempfile

        work_dir = Path(tempfile.mkdtemp(dir=str(REPO_ROOT)))
        try:
            manifest_path = work_dir / "manifest.yaml"
            self._write_manifest(manifest_path)
            requested_urls = []

            def fake_urlopen(request, *args, **kwargs):
                requested_urls.append(request.full_url)
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = run(
                    manifest_path=manifest_path,
                    raw_dir=work_dir / "raw",
                    retrieval_log_path=work_dir / "retrieval_log.json",
                    sleep_fn=lambda s: None,
                )

            # 2 confirmed-range seasons (2223, 2324) + 2 unconfirmed
            # (2425, 2526) = 4 total, ALL actually requested — never
            # skipped up front just because they're "unconfirmed".
            self.assertEqual(result["summary"]["total_manifest_entries"], 4)
            self.assertEqual(result["summary"]["network_attempts_made"], 4)
            requested_season_codes = {u.split("/")[-2] for u in requested_urls}
            self.assertEqual(requested_season_codes, {"2223", "2324", "2425", "2526"})

            for outcome in result["failed_attempts"]:
                self.assertEqual(outcome["error_type"], ERROR_NOT_FOUND)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_confirmed_absent_404_is_distinct_from_circuit_broken_skip(self):
        import shutil
        import tempfile

        work_dir = Path(tempfile.mkdtemp(dir=str(REPO_ROOT)))
        try:
            manifest_path = work_dir / "manifest.yaml"
            self._write_manifest(manifest_path)

            def fake_urlopen(request, *args, **kwargs):
                # Every attempt in this manifest 404s (never a connection
                # error), so the circuit breaker must never trip here —
                # HTTP_NOT_FOUND is a real HTTP response, not a
                # connection-level failure.
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                result = run(
                    manifest_path=manifest_path,
                    raw_dir=work_dir / "raw",
                    retrieval_log_path=work_dir / "retrieval_log.json",
                    sleep_fn=lambda s: None,
                )

            self.assertEqual(result["summary"]["skipped_host_blocked"], 0)
            self.assertEqual(result["circuit_breaker"]["tripped_hosts"], [])
            not_found = [f for f in result["failed_attempts"] if f["error_type"] == ERROR_NOT_FOUND]
            host_blocked = [f for f in result["failed_attempts"] if f["error_type"] == ERROR_NOT_ATTEMPTED_HOST_BLOCKED]
            self.assertEqual(len(not_found), 4)
            self.assertEqual(len(host_blocked), 0)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
