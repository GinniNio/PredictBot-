"""Download command for the Soccer 1X2 data-feasibility pipeline.

Reads `data_pipeline/sources/football_data_sources.yaml`, attempts to
download each league/season CSV from football-data.co.uk, and records
per-file provenance for every attempt (success or failure):

- SHA-256 of the response body, computed BEFORE any parsing/inspection
  touches the bytes, so it always reflects exactly what was received over
  the wire, never a post-processed version.
- UTC retrieval timestamp (ISO 8601).
- The exact source URL requested, AND the final URL after any redirects
  (football-data.co.uk or a CDN in front of it may redirect; these can
  differ).
- HTTP status code, when a response was received at all (even an error
  page still carries one). For a connection-level failure with no HTTP
  response — no server reachable, TLS/proxy rejection, DNS failure — this
  is recorded explicitly as `None` with `error_type: CONNECTION_ERROR`,
  never a fabricated status code.
- Content length in bytes of the response body actually received.

Stdlib only (urllib, hashlib, json) — no new runtime dependency, matching
this repo's zero-dependency convention.

Raw downloaded files are written OUTSIDE version control
(`data_pipeline/raw/`, excluded via `.gitignore`) — this module never
writes Football-Data's own CSV content anywhere git tracks. Only the
retrieval log (`data_pipeline/retrieval_log.json`), which stores metadata
(hash/timestamp/URL/status/length) and never the file content itself, is
safe to commit.

Typed, distinct failure reporting per file — a timeout, a generic HTTP
error, a 404-specifically (`HTTP_NOT_FOUND` — the source genuinely has no
file at this URL, distinct from every other HTTP-level rejection), and a
connection error are reported as different `error_type` values, never
collapsed into one generic "download failed" string, so a feasibility
report reader can tell "the site confirmed this specific season doesn't
exist" from "the site rejected this specific season for some other
reason" from "the network path to the site was blocked entirely."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_pipeline.manifest_yaml import load_yaml_categories_file  # noqa: E402

DEFAULT_MANIFEST_PATH = REPO_ROOT / "data_pipeline" / "sources" / "football_data_sources.yaml"
DEFAULT_RAW_DIR = REPO_ROOT / "data_pipeline" / "raw"
DEFAULT_RETRIEVAL_LOG_PATH = REPO_ROOT / "data_pipeline" / "retrieval_log.json"

MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 20
BACKOFF_SECONDS = (1, 2, 4)  # sleep before attempt 2, attempt 3 (len == MAX_ATTEMPTS - 1)

# Distinct, typed failure reasons — never one generic "download failed".
ERROR_TIMEOUT = "TIMEOUT"
ERROR_HTTP = "HTTP_ERROR"
# A real HTTP response was received AND it was specifically a 404 Not
# Found — kept distinct from the generic ERROR_HTTP (e.g. a 500, 403, or
# any other HTTP-level rejection). A 404 on this source's predictable
# `mmz4281/<season>/<league>.csv` URL scheme is the one HTTP outcome that
# means "the source has confirmed, by actually answering the request,
# that no file exists at this URL" — never conflated with a generic HTTP
# error, a timeout, or a connection-level failure (see report.py's
# SOURCE_NOT_LISTED, which this maps to for a season that was actually
# attempted and came back genuinely absent).
ERROR_NOT_FOUND = "HTTP_NOT_FOUND"
ERROR_CONNECTION = "CONNECTION_ERROR"
ERROR_UNKNOWN = "UNKNOWN_ERROR"
# Not a download failure at all — this file was never even attempted
# because the circuit breaker (see `run()`) had already confirmed, from
# earlier files against the SAME host in this same run, that the host is
# blocked at the proxy/network level. Kept distinct from every real
# error_type above so the evidence trail can always tell "we tried this
# specific file and it failed" from "we didn't even try this one".
ERROR_NOT_ATTEMPTED_HOST_BLOCKED = "NOT_ATTEMPTED_HOST_BLOCKED"

# Circuit breaker: once a single host has produced this many CONNECTION_ERROR
# failures in this run (a policy-level/proxy-level denial, not a
# per-file/transient issue — see ERROR_CONNECTION's docstring above), every
# remaining season for every league on that host is recorded as
# NOT_ATTEMPTED_HOST_BLOCKED instead of being individually retried 3x each.
# 2 is deliberately small: a single CONNECTION_ERROR could in principle be a
# one-off blip, but two independent files against the same host both failing
# at the connection layer (never reaching an HTTP response) is already
# strong, cheap-to-obtain evidence that the host itself is unreachable from
# this environment — continuing to retry all 155 files 3x each would only
# burn time confirming what's already confirmed.
HOST_BLOCK_FAILURE_THRESHOLD = 2


@dataclass
class DownloadOutcome:
    league_code: str
    league_name: str
    season_code: str
    requested_url: str
    success: bool
    attempts_made: int
    # Per-attempt HTTP-level metadata, recorded whenever an HTTP response
    # was received at all — for success this describes the winning
    # attempt; for a failure with an HTTP response (e.g. 404) this
    # describes that final rejected attempt. Left `None` when no HTTP
    # response was ever received (a pure connection-level failure).
    http_status: int | None = None
    final_url: str | None = None
    content_length: int | None = None
    # Populated only when success is True.
    sha256: str | None = None
    retrieved_at_utc: str | None = None
    raw_path: str | None = None
    # Populated only when success is False.
    error_type: str | None = None
    error_detail: str | None = None


def season_codes(first_season: str, latest_completed_season: str) -> list[str]:
    """Enumerate Football-Data 4-digit season codes from `first_season`
    through `latest_completed_season` inclusive, e.g. "9394" -> "9495" ->
    ... -> "2324". Football-Data's own season-code convention: the first
    two digits are the two-digit year the season starts in, the last two
    are the two-digit year it ends in (so "9900" is the 1999-2000 season,
    not an error)."""

    def start_year(code: str) -> int:
        two_digit = int(code[:2])
        # Football-Data's main-league archives only go back to the 1990s,
        # so 2-digit years >= 50 are 1900s, else 2000s.
        return 1900 + two_digit if two_digit >= 50 else 2000 + two_digit

    first_year = start_year(first_season)
    last_year = start_year(latest_completed_season)
    if last_year < first_year:
        raise ValueError(
            f"latest_completed_season {latest_completed_season!r} is earlier than "
            f"first_season {first_season!r}"
        )
    codes = []
    for year in range(first_year, last_year + 1):
        codes.append(f"{year % 100:02d}{(year + 1) % 100:02d}")
    return codes


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> list[dict[str, Any]]:
    return load_yaml_categories_file(str(manifest_path))


def download_one(
    league_code: str,
    league_name: str,
    season_code: str,
    url: str,
    raw_dir: Path,
    max_attempts: int = MAX_ATTEMPTS,
    timeout: int = TIMEOUT_SECONDS,
    backoff: tuple[int, ...] = BACKOFF_SECONDS,
    sleep_fn=time.sleep,
) -> DownloadOutcome:
    """Download one league/season CSV with retry-with-backoff and typed,
    distinct failure classification. Never raises — every outcome (success
    or a specific failure type) is returned as a `DownloadOutcome`, always
    carrying whatever HTTP-level metadata (status/final URL/content
    length) was actually observable for that attempt."""

    last_error_type: str | None = None
    last_error_detail: str | None = None
    last_http_status: int | None = None
    last_final_url: str | None = None
    last_content_length: int | None = None

    for attempt in range(1, max_attempts + 1):
        request = urllib.request.Request(url, headers={"User-Agent": "PredictBot-data-feasibility/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
                http_status = getattr(response, "status", None) or response.getcode()
                final_url = response.geturl()
        except urllib.error.HTTPError as exc:
            # A real HTTP response was received (e.g. a 404 error page) —
            # record its status/final-url/content-length even though the
            # request itself is a failure. A 404 specifically is typed
            # distinctly (ERROR_NOT_FOUND) from every other HTTP-level
            # rejection (ERROR_HTTP) — see ERROR_NOT_FOUND's docstring
            # above.
            last_error_type = ERROR_NOT_FOUND if exc.code == 404 else ERROR_HTTP
            last_error_detail = f"HTTP {exc.code}: {exc.reason}"
            last_http_status = exc.code
            last_final_url = exc.geturl() if hasattr(exc, "geturl") else url
            try:
                body = exc.read()
                last_content_length = len(body)
            except Exception:  # noqa: BLE001 - best-effort only
                last_content_length = None
        except (socket.timeout, TimeoutError) as exc:
            last_error_type = ERROR_TIMEOUT
            last_error_detail = str(exc) or "socket timed out"
            last_http_status = None
            last_final_url = None
            last_content_length = None
        except urllib.error.URLError as exc:
            # Covers DNS failures, connection refusals, and this sandbox's
            # own proxy-policy rejections (e.g. "Tunnel connection failed:
            # 403 Forbidden") — no HTTP response was ever received, so
            # http_status/final_url/content_length are genuinely unknown
            # (recorded as None, never a fabricated value).
            last_error_type = ERROR_CONNECTION
            last_error_detail = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            last_http_status = None
            last_final_url = None
            last_content_length = None
        except Exception as exc:  # noqa: BLE001 - deliberately broad, typed as UNKNOWN
            last_error_type = ERROR_UNKNOWN
            last_error_detail = f"{type(exc).__name__}: {exc}"
            last_http_status = None
            last_final_url = None
            last_content_length = None
        else:
            # SHA-256 computed here, immediately on the raw bytes exactly
            # as received — before anything (including this function's own
            # write-to-disk step) does any further processing.
            sha256 = hashlib.sha256(content).hexdigest()
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = raw_dir / league_code / f"{league_code}_{season_code}.csv"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(content)
            return DownloadOutcome(
                league_code=league_code,
                league_name=league_name,
                season_code=season_code,
                requested_url=url,
                success=True,
                attempts_made=attempt,
                http_status=http_status,
                final_url=final_url,
                content_length=len(content),
                sha256=sha256,
                retrieved_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                raw_path=str(raw_path.relative_to(REPO_ROOT)),
            )

        if attempt < max_attempts:
            sleep_fn(backoff[min(attempt - 1, len(backoff) - 1)])

    return DownloadOutcome(
        league_code=league_code,
        league_name=league_name,
        season_code=season_code,
        requested_url=url,
        success=False,
        attempts_made=max_attempts,
        http_status=last_http_status,
        final_url=last_final_url,
        content_length=last_content_length,
        error_type=last_error_type,
        error_detail=last_error_detail,
    )


def run(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    retrieval_log_path: Path = DEFAULT_RETRIEVAL_LOG_PATH,
    host_block_failure_threshold: int = HOST_BLOCK_FAILURE_THRESHOLD,
    sleep_fn=time.sleep,
) -> dict[str, Any]:
    manifest_rows = load_manifest(manifest_path)
    successes: list[DownloadOutcome] = []
    failures: list[DownloadOutcome] = []

    # Circuit breaker state, per host — see HOST_BLOCK_FAILURE_THRESHOLD.
    connection_error_counts: dict[str, int] = {}
    blocked_hosts: set[str] = set()
    circuit_breaker_events: list[dict[str, Any]] = []

    for row in manifest_rows:
        # Attempt every season the manifest names for this league — the
        # confirmed [first_season, latest_completed_season] range AND the
        # `unconfirmed_seasons` gap seasons (e.g. 2024-25/2025-26) — through
        # the exact same download/retry/circuit-breaker logic below. The
        # manifest's own url_pattern is the same for both; the only
        # difference between a confirmed and an unconfirmed season is that
        # this manifest has not yet had a live-download-capable environment
        # confirm the unconfirmed one resolves to a real file. A season
        # this run cannot actually reach the file for (e.g. a genuine 404)
        # is reported with its own typed outcome (see ERROR_NOT_FOUND /
        # report.py's SOURCE_NOT_LISTED) rather than being silently skipped
        # up front — see data_pipeline/sources/football_data_sources.yaml's
        # module docstring.
        confirmed_codes = season_codes(str(row["first_season"]), str(row["latest_completed_season"]))
        unconfirmed_codes = [str(c) for c in (row.get("unconfirmed_seasons") or [])]
        for code in confirmed_codes + unconfirmed_codes:
            url = row["url_pattern"].format(season_code=code)
            host = urllib.parse.urlparse(url).netloc

            if host in blocked_hosts:
                # Do not even attempt this one — a confirmed host-level
                # denial was already observed against this same host
                # earlier in this run (see circuit_breaker_events below).
                failures.append(
                    DownloadOutcome(
                        league_code=row["league_code"],
                        league_name=row["league_name"],
                        season_code=code,
                        requested_url=url,
                        success=False,
                        attempts_made=0,
                        error_type=ERROR_NOT_ATTEMPTED_HOST_BLOCKED,
                        error_detail=(
                            f"Not attempted: host {host!r} was already confirmed blocked "
                            f"earlier in this run after {host_block_failure_threshold} "
                            "consecutive CONNECTION_ERROR failures against it (circuit breaker)."
                        ),
                    )
                )
                continue

            outcome = download_one(
                league_code=row["league_code"],
                league_name=row["league_name"],
                season_code=code,
                url=url,
                raw_dir=raw_dir,
                sleep_fn=sleep_fn,
            )
            (successes if outcome.success else failures).append(outcome)

            if outcome.success:
                connection_error_counts[host] = 0
            elif outcome.error_type == ERROR_CONNECTION:
                connection_error_counts[host] = connection_error_counts.get(host, 0) + 1
                if connection_error_counts[host] >= host_block_failure_threshold:
                    blocked_hosts.add(host)
                    circuit_breaker_events.append(
                        {
                            "host": host,
                            "tripped_after_league_season": f"{row['league_code']}/{code}",
                            "consecutive_connection_errors": connection_error_counts[host],
                        }
                    )

    result = {
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "manifest_path": (
            str(manifest_path.relative_to(REPO_ROOT))
            if manifest_path.is_relative_to(REPO_ROOT)
            else str(manifest_path)
        ),
        "circuit_breaker": {
            "failure_threshold": host_block_failure_threshold,
            "tripped_hosts": sorted(blocked_hosts),
            "events": circuit_breaker_events,
            "not_attempted_host_blocked_count": sum(
                1 for o in failures if o.error_type == ERROR_NOT_ATTEMPTED_HOST_BLOCKED
            ),
        },
        "downloads": [
            {
                "league_code": o.league_code,
                "league_name": o.league_name,
                "season_code": o.season_code,
                "requested_url": o.requested_url,
                "final_url": o.final_url,
                "http_status": o.http_status,
                "content_length": o.content_length,
                "sha256": o.sha256,
                "retrieved_at_utc": o.retrieved_at_utc,
                "raw_path": o.raw_path,
                "attempts_made": o.attempts_made,
            }
            for o in successes
        ],
        "failed_attempts": [
            {
                "league_code": o.league_code,
                "league_name": o.league_name,
                "season_code": o.season_code,
                "requested_url": o.requested_url,
                "final_url": o.final_url,
                "http_status": o.http_status,
                "content_length": o.content_length,
                "error_type": o.error_type,
                "error_detail": o.error_detail,
                "attempts_made": o.attempts_made,
            }
            for o in failures
        ],
        "summary": {
            # Every (league, season) pair the manifest's confirmed range
            # covers, whether it was actually attempted over the network or
            # skipped by the circuit breaker — this always equals
            # successes + failures, and failures includes both real
            # per-file failures AND NOT_ATTEMPTED_HOST_BLOCKED skips.
            "total_manifest_entries": len(successes) + len(failures),
            "succeeded": len(successes),
            "failed": len(failures),
            # Subset of the above that actually made a network attempt
            # (download_one was called at least once) — excludes rows the
            # circuit breaker skipped entirely.
            "network_attempts_made": sum(1 for o in successes) + sum(
                1 for o in failures if o.error_type != ERROR_NOT_ATTEMPTED_HOST_BLOCKED
            ),
            "skipped_host_blocked": sum(
                1 for o in failures if o.error_type == ERROR_NOT_ATTEMPTED_HOST_BLOCKED
            ),
        },
    }

    retrieval_log_path.parent.mkdir(parents=True, exist_ok=True)
    retrieval_log_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--retrieval-log", type=Path, default=DEFAULT_RETRIEVAL_LOG_PATH)
    args = parser.parse_args()

    result = run(manifest_path=args.manifest, raw_dir=args.raw_dir, retrieval_log_path=args.retrieval_log)
    summary = result["summary"]
    print(
        f"{summary['total_manifest_entries']} league-season files planned: "
        f"{summary['network_attempts_made']} actually attempted over the network "
        f"({summary['succeeded']} succeeded, "
        f"{summary['network_attempts_made'] - summary['succeeded']} failed), "
        f"{summary['skipped_host_blocked']} skipped by the circuit breaker "
        "(NOT_ATTEMPTED_HOST_BLOCKED). "
        f"Retrieval log written to {args.retrieval_log}"
    )
    if result["circuit_breaker"]["tripped_hosts"]:
        print(
            "Circuit breaker tripped for host(s): "
            f"{', '.join(result['circuit_breaker']['tripped_hosts'])} — "
            "see retrieval_log.json's circuit_breaker[] for details."
        )
    if summary["failed"] and not summary["succeeded"]:
        print("ALL downloads failed/skipped — see failed_attempts[].error_type/error_detail in the retrieval log.")


if __name__ == "__main__":
    main()
