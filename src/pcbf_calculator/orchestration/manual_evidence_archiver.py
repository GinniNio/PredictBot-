"""Fetches and archives the real page a manual-results EVIDENCE-COLLECTION
source (``schemas/manual_results_evidence_input.v1.schema.json``) cites,
and appends one entry to the trusted archive manifest
(``schemas/manual_evidence_archive_manifest.v1.schema.json``) --
``convert-manual-results-evidence``'s own module docstring names this
module explicitly as the "SEPARATE, NOT-YET-BUILT evidence-preservation
tool" its own archive-manifest lookup depends on. This module is the one
place in this codebase that makes an outbound network request to fetch
someone else's page; every other command remains offline.

One command::

    python -m pcbf_calculator archive-manual-results-evidence \\
        manual-results-evidence.json \\
        --archive-dir ledger_data/manual_evidence \\
        --output-dir runs/archive-session-id \\
        [--manifest-path ledger_data/manual_evidence/archive-manifest.json]

**Every field this module writes into the manifest is tool-generated at
fetch time -- never copied from the untrusted evidence-collection file.**
``retrieved_at_utc``, ``http_status``, ``content_type``, ``byte_count``,
and ``sha256`` all come from the real HTTP response and the real archived
bytes on disk; the evidence-collection file's own ``evidence_sha256``/
``retrieved_at_utc`` are never read by this module at all (they exist only
so an LLM can note what IT saw -- this module independently re-fetches and
re-hashes the same page rather than trusting that note).

**SSRF-safe by construction.** Only an ``https`` URL is ever fetched.
Before connecting, and before following any redirect hop, every candidate
hostname is resolved and every resolved address is checked against
``ipaddress``'s own private/loopback/link-local/multicast/reserved/
unspecified classifications -- a destination that resolves to any of
those is refused, never connected to, whether it is the URL's own host or
a redirect target reached from it. A ``file:``/``ftp:``/other non-``https``
scheme, at either hop, is refused just as fast. Redirects are capped
(``MAX_REDIRECTS``); DNS-rebinding between the initial check and the
actual connection remains a residual, accepted risk of any userspace
same-process check (Python's ``http.client`` re-resolves internally) --
narrowed, not eliminated, by re-validating the specific hop that was
actually redirected to rather than trusting the chain's starting URL alone.

**Bounded fetch.** A fixed connect/read timeout, a fixed maximum download
size (the response is read in bounded chunks and abandoned the instant it
would exceed the cap -- never buffered unbounded first), and a fixed
allowed ``Content-Type`` set (``text/html``, ``application/xhtml+xml``,
``text/plain`` -- never a binary/media type) all apply to every fetch.

**Content is independently validated, never assumed from a 200 status
alone.** The archived bytes must actually contain, as plain visible text
(scripts/style/markup stripped): the claimed home team name, the claimed
away team name, the claimed scoreline, and at least one completed-match
marker (e.g. "full time", "final score") -- a page that loads but never
mentions the match at all, or reads like an in-progress/upcoming fixture,
is refused (``ARCHIVE_CONTENT_DOES_NOT_SUPPORT_RESULT``), not archived
"just in case". A page whose visible text is implausibly short after
stripping markup (the common signature of a JavaScript-only single-page
app that never server-rendered any content) is refused as
``ARCHIVE_JS_ONLY_OR_EMPTY`` before content validation even runs -- this
module never treats an empty shell as supporting evidence. When the cited
``fixture_id`` already has a RECORDED forecast in the (read-only) forecast
ledger, that forecast's own ``scheduled_date`` year is additionally
required to appear in the archived text; when it does not (fixture not in
the ledger, or the ledger has no full identity for it yet), this module
still requires team+score+completion evidence but cannot additionally
pin the date -- content validation is never skipped outright for that
reason.

**The LLM's own ``evidence_summary`` is never treated as substitute
evidence anywhere in this module.** It is not read at all -- only the
result row's own structured ``participants``/``home_score``/
``away_score`` fields (the row's OWN claim, still independently checked
against the real archived page) are used for content validation.

**Append-only, idempotent, immutable.** A ``requested_url`` already
present in the manifest is never re-fetched or re-archived (informational
skip, not a failure) -- the archiver is safe to re-run against the same
evidence-collection file day after day as new sources appear. Every
newly-archived entry is appended to the existing manifest's own
``entries`` list (loaded and re-validated first) and the WHOLE manifest is
rewritten atomically (temp file + rename) -- an existing entry already on
disk is never edited, reordered, or removed. Archived bytes are written
once, at a content-addressed path (``<sha256>.bin`` under
``--archive-dir``) that is never opened for writing again once it exists.

Explicit boundaries: never touches the forecast ledger beyond one
read-only ``fixture_id`` lookup used only to strengthen content
validation; never touches ``ingest-manual-results``, its ``--confirm``
gate, or the betting ledger at all; never invents, guesses, or backfills
an ``evidence_sha256``/``retrieved_at_utc`` for a source it did not
itself successfully fetch and archive.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ensure_ledgers_importable() -> None:
    """Same fallback every other orchestration module in this package
    uses -- see any of their own copies of this helper for the full
    rationale."""

    try:
        import ledgers  # noqa: F401

        return
    except ImportError:
        pass
    candidate = Path(__file__).resolve().parents[3]
    if (candidate / "ledgers" / "__init__.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


_ensure_ledgers_importable()

from ledgers.validation import _check_node  # noqa: E402

from .manual_results_evidence_converter import (  # noqa: E402
    _build_fixture_id_index,
    discover_input_files,
    load_archive_manifest,
    load_manual_evidence_archive_manifest_schema,
    load_manual_results_evidence_schema,
)

SCHEMA_VERSION_MANIFEST = "manual-evidence-archive-manifest.v1"
SCHEMA_VERSION_REPORT = "pcbf-manual-evidence-archive-report.v1"

REQUEST_TIMEOUT_SECONDS = 20
MAX_CONTENT_BYTES = 5_000_000
MAX_REDIRECTS = 5
CHUNK_SIZE = 8192
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "text/plain"})
MIN_VISIBLE_TEXT_CHARS = 200
COMPLETION_MARKERS = ("full time", "full-time", "finished", "final score", "match ended", "final whistle", " ft ")

REASON_INVALID_ENVELOPE = "ARCHIVE_INVALID_ENVELOPE"
REASON_NOT_VERIFIED = "ARCHIVE_NOT_VERIFIED"
REASON_ALREADY_ARCHIVED = "ARCHIVE_ALREADY_ARCHIVED"
REASON_UNSUPPORTED_SCHEME = "ARCHIVE_UNSUPPORTED_SCHEME"
REASON_INVALID_URL = "ARCHIVE_INVALID_URL"
REASON_BLOCKED_DESTINATION = "ARCHIVE_BLOCKED_DESTINATION"
REASON_REDIRECT_BLOCKED_DESTINATION = "ARCHIVE_REDIRECT_BLOCKED_DESTINATION"
REASON_TOO_MANY_REDIRECTS = "ARCHIVE_TOO_MANY_REDIRECTS"
REASON_FETCH_FAILED = "ARCHIVE_FETCH_FAILED"
REASON_CONTENT_TYPE_NOT_ALLOWED = "ARCHIVE_CONTENT_TYPE_NOT_ALLOWED"
REASON_CONTENT_TOO_LARGE = "ARCHIVE_CONTENT_TOO_LARGE"
REASON_JS_ONLY_OR_EMPTY = "ARCHIVE_JS_ONLY_OR_EMPTY"
REASON_CONTENT_DOES_NOT_SUPPORT_RESULT = "ARCHIVE_CONTENT_DOES_NOT_SUPPORT_RESULT"

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")


class _BlockedRedirectError(Exception):
    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        self.detail = detail
        super().__init__(f"redirect to {url!r} blocked: {detail}")


class _RedirectLimitExceeded(Exception):
    pass


@dataclass(frozen=True)
class FetchOutcome:
    """Either a successful fetch (``ok=True``, ``final_url``/``http_status``/
    ``content_type``/``body`` populated) or a typed failure (``ok=False``,
    ``reason``/``detail`` populated) -- never a mix of the two."""

    ok: bool
    reason: str | None = None
    detail: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    body: bytes | None = None


def _is_public_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_hostname_resolves_to_public_ip(hostname: str) -> tuple[bool, str | None]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed for {hostname!r}: {exc}"
    if not infos:
        return False, f"DNS resolution returned no addresses for {hostname!r}"
    for info in infos:
        ip_str = info[4][0].split("%")[0]
        if not _is_public_ip(ip_str):
            return False, f"{hostname!r} resolves to non-public address {ip_str}"
    return True, None


def validate_https_url(url: str) -> tuple[bool, str | None, str | None]:
    """Returns ``(ok, reason, detail)``. Blocks any non-``https`` scheme
    (``file:``/``ftp:``/plain ``http:`` included), any URL with no
    resolvable hostname, and any hostname that resolves to a private,
    loopback, link-local, multicast, reserved, or unspecified address --
    the exact same check applied to the URL's own host and to every
    redirect hop it leads to (see ``_SafeRedirectHandler`` below)."""

    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return False, REASON_INVALID_URL, f"unparseable URL: {exc}"
    if parsed.scheme != "https":
        return False, REASON_UNSUPPORTED_SCHEME, f"scheme {parsed.scheme!r} is not https"
    hostname = parsed.hostname
    if not hostname:
        return False, REASON_INVALID_URL, "URL has no hostname"
    ok, detail = _check_hostname_resolves_to_public_ip(hostname)
    if not ok:
        return False, REASON_BLOCKED_DESTINATION, detail
    return True, None, None


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validates -- via ``validate_https_url`` -- every redirect target
    BEFORE following it, and refuses past ``max_redirects`` hops. A fresh
    instance is built per fetch, so the hop counter never leaks between
    unrelated requests."""

    def __init__(self, max_redirects: int) -> None:
        self._max_redirects = max_redirects
        self._redirect_count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        self._redirect_count += 1
        if self._redirect_count > self._max_redirects:
            raise _RedirectLimitExceeded(f"exceeded {self._max_redirects} redirect(s)")
        ok, _reason, detail = validate_https_url(newurl)
        if not ok:
            raise _BlockedRedirectError(newurl, detail or "blocked destination")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_http_fetch(
    url: str,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_bytes: int = MAX_CONTENT_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> FetchOutcome:
    """The one real network call this module ever makes -- stdlib
    ``urllib.request`` only (matches this codebase's zero-new-dependency
    convention; see ``football_data_org_settlement.py``'s own identical
    choice), so the process's normal proxy configuration (``HTTPS_PROXY``
    etc, handled by urllib's own default ``ProxyHandler``) keeps working
    unchanged. Injectable (``http_fetch`` parameter throughout this
    module) so every other function here is testable with no real network
    access."""

    ok, reason, detail = validate_https_url(url)
    if not ok:
        return FetchOutcome(ok=False, reason=reason, detail=detail)

    handler = _SafeRedirectHandler(max_redirects)
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(url, headers={"User-Agent": "PredictBotManualEvidenceArchiver/1.0"})
    try:
        response = opener.open(request, timeout=timeout)
    except _BlockedRedirectError as exc:
        return FetchOutcome(ok=False, reason=REASON_REDIRECT_BLOCKED_DESTINATION, detail=exc.detail)
    except _RedirectLimitExceeded as exc:
        return FetchOutcome(ok=False, reason=REASON_TOO_MANY_REDIRECTS, detail=str(exc))
    except urllib.error.HTTPError as exc:
        return FetchOutcome(ok=False, reason=REASON_FETCH_FAILED, detail=f"HTTP {exc.code}: {exc.reason}")
    except urllib.error.URLError as exc:
        return FetchOutcome(ok=False, reason=REASON_FETCH_FAILED, detail=f"connection error: {exc.reason}")
    except TimeoutError as exc:
        return FetchOutcome(ok=False, reason=REASON_FETCH_FAILED, detail=f"timed out: {exc}")

    with response:
        final_url = response.geturl()
        http_status = getattr(response, "status", None) or response.getcode()
        content_type_header = response.headers.get("Content-Type", "")
        content_type = content_type_header.split(";")[0].strip().lower()
        if content_type not in ALLOWED_CONTENT_TYPES:
            return FetchOutcome(
                ok=False,
                reason=REASON_CONTENT_TYPE_NOT_ALLOWED,
                detail=f"content-type {content_type_header!r} is not in the allowed set {sorted(ALLOWED_CONTENT_TYPES)}",
                final_url=final_url,
                http_status=http_status,
                content_type=content_type_header,
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return FetchOutcome(
                    ok=False,
                    reason=REASON_CONTENT_TOO_LARGE,
                    detail=f"exceeded {max_bytes}-byte limit while reading the response body",
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type_header,
                )
            chunks.append(chunk)
        body = b"".join(chunks)

    return FetchOutcome(
        ok=True, final_url=final_url, http_status=http_status, content_type=content_type_header, body=body
    )


def extract_visible_text(html_bytes: bytes) -> str:
    """Strips ``<script>``/``<style>`` blocks and every remaining tag,
    collapses whitespace. A best-effort, dependency-free substitute for a
    real HTML parser -- good enough to tell "this page has real prose
    about a match" from "this page is an empty JS shell", which is all
    this module needs it for."""

    text = html_bytes.decode("utf-8", errors="replace")
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def content_supports_result(
    visible_text: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    scheduled_date: str | None,
) -> tuple[bool, str | None]:
    """Independently checks that the ARCHIVED page's own visible text
    actually supports the claimed result -- never assumed just because
    the fetch returned 200. Never reads ``evidence_summary`` at all."""

    lowered = visible_text.lower()
    if home_team.lower() not in lowered:
        return False, f"archived text does not mention home team {home_team!r}"
    if away_team.lower() not in lowered:
        return False, f"archived text does not mention away team {away_team!r}"
    scoreline_patterns = (f"{home_score}-{away_score}", f"{home_score} - {away_score}", f"{home_score}:{away_score}")
    if not any(pattern in visible_text for pattern in scoreline_patterns):
        return False, f"archived text does not contain the scoreline {home_score}-{away_score}"
    if scheduled_date:
        year = scheduled_date[:4]
        if year and year not in visible_text:
            return False, f"archived text does not mention the year {year} from scheduled_date {scheduled_date!r}"
    if not any(marker in lowered for marker in COMPLETION_MARKERS):
        return False, "archived text shows no completed-match evidence (no full-time/finished/final-score marker found)"
    return True, None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def archive_evidence_batch(
    json_paths: list[Path],
    archive_dir: Path,
    manifest: dict[str, Any],
    schema: dict[str, Any],
    ledger_path: Path | None,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    http_fetch: Callable[..., FetchOutcome] = default_http_fetch,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Reads every evidence-collection envelope in ``json_paths`` and
    attempts to archive each ``VERIFIED`` result's own sources. Returns
    ``(new_entries, rejected, counts)`` -- ``new_entries`` are manifest
    entries not yet written to disk (the caller appends and persists
    them); ``manifest`` itself is never mutated here."""

    fixture_index = _build_fixture_id_index(ledger_path) if ledger_path is not None else {}
    existing_urls = {entry["requested_url"] for entry in manifest.get("entries") or []}
    already_archived_this_run: set[str] = set()

    new_entries: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    source_rows_total = 0
    sources_attempted = 0
    sources_archived = 0
    sources_already_archived = 0

    for json_path in json_paths:
        envelope = json.loads(json_path.read_text(encoding="utf-8"))
        errors: list[str] = []
        _check_node(envelope, schema, "envelope", errors)
        if errors:
            source_rows_total += 1
            rejected.append(
                {
                    "source_file": str(json_path),
                    "source_row_index": None,
                    "reason": REASON_INVALID_ENVELOPE,
                    "detail": "; ".join(errors),
                }
            )
            continue

        for row_index, raw in enumerate(envelope.get("results") or []):
            source_rows_total += 1
            fixture_id = raw["fixture_id"]

            if raw["result_status"] != "VERIFIED":
                rejected.append(
                    {
                        "source_file": str(json_path),
                        "source_row_index": row_index,
                        "fixture_id": fixture_id,
                        "source_url": None,
                        "reason": REASON_NOT_VERIFIED,
                        "detail": raw.get("review_reason") or "result_status is not VERIFIED",
                    }
                )
                continue

            scheduled_date = None
            matches = fixture_index.get(fixture_id, [])
            if len(matches) == 1:
                scheduled_date = matches[0].get("scheduled_date")

            home_team = raw["participants"]["home"]
            away_team = raw["participants"]["away"]
            home_score = raw["home_score"]
            away_score = raw["away_score"]

            for source in raw["sources"]:
                base = {
                    "source_file": str(json_path),
                    "source_row_index": row_index,
                    "fixture_id": fixture_id,
                    "source_url": source["source_url"],
                }
                if source["source_url"] in existing_urls or source["source_url"] in already_archived_this_run:
                    rejected.append({**base, "reason": REASON_ALREADY_ARCHIVED, "detail": "already present in the archive manifest"})
                    continue

                sources_attempted += 1
                outcome = http_fetch(source["source_url"])
                if not outcome.ok:
                    rejected.append({**base, "reason": outcome.reason, "detail": outcome.detail})
                    continue

                assert outcome.body is not None and outcome.final_url is not None
                visible_text = extract_visible_text(outcome.body)
                if len(visible_text) < MIN_VISIBLE_TEXT_CHARS:
                    rejected.append(
                        {
                            **base,
                            "reason": REASON_JS_ONLY_OR_EMPTY,
                            "detail": (
                                f"only {len(visible_text)} visible character(s) after stripping markup "
                                f"(< {MIN_VISIBLE_TEXT_CHARS} minimum) -- likely an inaccessible or JavaScript-only page"
                            ),
                        }
                    )
                    continue

                supported, support_detail = content_supports_result(
                    visible_text, home_team, away_team, home_score, away_score, scheduled_date
                )
                if not supported:
                    rejected.append({**base, "reason": REASON_CONTENT_DOES_NOT_SUPPORT_RESULT, "detail": support_detail})
                    continue

                sha256 = hashlib.sha256(outcome.body).hexdigest()
                archive_path = archive_dir / f"{sha256}.bin"
                if not archive_path.exists():
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    archive_path.write_bytes(outcome.body)

                already_archived_this_run.add(source["source_url"])
                sources_archived += 1
                new_entries.append(
                    {
                        "requested_url": source["source_url"],
                        "final_url": outcome.final_url,
                        "retrieved_at_utc": now_fn().strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "http_status": outcome.http_status,
                        "content_type": outcome.content_type,
                        "byte_count": len(outcome.body),
                        "sha256": sha256,
                        "archive_path": archive_path.name,
                    }
                )

    sources_already_archived = sum(1 for r in rejected if r["reason"] == REASON_ALREADY_ARCHIVED)
    counts = {
        "source_rows_total": source_rows_total,
        "sources_attempted": sources_attempted,
        "sources_archived": sources_archived,
        "sources_already_archived": sources_already_archived,
        "sources_rejected": len(rejected) - sources_already_archived,
    }
    return new_entries, rejected, counts


def run_archive_session(
    input_path: Path,
    archive_dir: Path,
    output_dir: Path,
    manifest_path: Path | None = None,
    ledger_dir: Path | None = None,
    http_fetch: Callable[..., FetchOutcome] = default_http_fetch,
) -> dict[str, Any]:
    json_paths = discover_input_files(input_path)
    schema = load_manual_results_evidence_schema()
    manifest_schema = load_manual_evidence_archive_manifest_schema()

    resolved_manifest_path = manifest_path or (archive_dir / "archive-manifest.json")
    manifest = load_archive_manifest(resolved_manifest_path, manifest_schema)

    ledger_path = None
    if ledger_dir is not None:
        from ledgers import forecast_ledger

        candidate = ledger_dir / forecast_ledger.DEFAULT_FILENAME
        if candidate.exists():
            ledger_path = candidate

    new_entries, rejected, counts = archive_evidence_batch(
        json_paths, archive_dir, manifest, schema, ledger_path, http_fetch=http_fetch
    )

    updated_manifest = {
        "schema_version": SCHEMA_VERSION_MANIFEST,
        "entries": [*(manifest.get("entries") or []), *new_entries],
    }
    if new_entries:
        _write_json_atomic(resolved_manifest_path, updated_manifest)

    report = {
        "schema_version": SCHEMA_VERSION_REPORT,
        "source_files": [str(p) for p in json_paths],
        "archive_dir": str(archive_dir),
        "manifest_path": str(resolved_manifest_path),
        "manifest_entries_before": len(manifest.get("entries") or []),
        "manifest_entries_after": len(updated_manifest["entries"]),
        "note": (
            "Every manifest field is tool-generated at fetch time from a real HTTPS response and the "
            "real archived bytes on disk -- never copied from the untrusted evidence-collection file's "
            "own evidence_sha256/retrieved_at_utc, which this module never reads. Only https destinations "
            "that resolve to a public address are ever fetched, at every redirect hop. A source already "
            "present in the manifest is skipped, never re-fetched. convert-manual-results-evidence "
            "consumes this manifest by requested_url and independently recomputes each entry's sha256 "
            "from disk before trusting it."
        ),
        "counts": counts,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(output_dir / "archive-report.json", report)
    _write_json_atomic(output_dir / "archive-rejected.json", {"schema_version": "pcbf-manual-evidence-archive-rejected.v1", "rejected": rejected})

    return {"archive_report": report, "new_entries": new_entries, "rejected": rejected}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(
        prog="python -m pcbf_calculator archive-manual-results-evidence",
        description=__doc__,
    )
    parser.add_argument("input", type=Path, help="A manual-results-evidence-*.json file, or a directory of them")
    parser.add_argument(
        "--archive-dir", type=Path, required=True, help="Directory immutable archived bytes are written into"
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory to write the two report files into")
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=None,
        help="Trusted archive manifest path (default: <archive-dir>/archive-manifest.json)",
    )
    parser.add_argument(
        "--ledger-dir",
        type=Path,
        default=None,
        help="Optional forecast-ledger directory (read-only) -- when supplied, strengthens content "
        "validation with that fixture's own recorded scheduled_date year",
    )
    args = parser.parse_args(argv)

    result = run_archive_session(
        args.input,
        args.archive_dir,
        args.output_dir,
        manifest_path=args.manifest_path,
        ledger_dir=args.ledger_dir,
    )
    report = result["archive_report"]
    counts = report["counts"]
    print(
        f"{counts['source_rows_total']} result row(s), {counts['sources_attempted']} source(s) attempted: "
        f"{counts['sources_archived']} archived, {counts['sources_already_archived']} already archived, "
        f"{counts['sources_rejected']} rejected -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
