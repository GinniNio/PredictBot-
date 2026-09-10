# Football-Data feasibility decision

**Scope reminder**: this document, and the pipeline it describes, is a
**data-feasibility** exercise (`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`,
"Next sequence," step 2). It proves whether Football-Data CAN support the
Soccer 1X2 feature pipeline. It does not build, train, calibrate, or
evaluate a forecasting model, and it does not approve any promotion
threshold or admission-registry row.

## Live-download attempt: what actually happened in this sandbox

A real download attempt was made against football-data.co.uk in this
environment (`python3 -m data_pipeline.download`, the actual manifest at
`data_pipeline/sources/football_data_sources.yaml`, 5 leagues x every
season 1993-94 through 2023-24). **Every single attempt failed at the
network layer, before any HTTP response was ever received.** The
environment's outbound proxy rejects the connection to
`www.football-data.co.uk:443` at the CONNECT/tunnel stage with an explicit
policy denial (`Tunnel connection failed: 403 Forbidden` — confirmed via
the proxy's own `/__agentproxy/status` endpoint, which logs
`www.football-data.co.uk:443` as a `connect_rejected` /
"gateway answered 403 to CONNECT (policy denial or upstream failure)").
This is **not** a Football-Data outage, a rate limit, or a malformed
request — the destination host itself is not on this sandbox's outbound
allowlist. This is stated here plainly, not softened: **no real
football-data.co.uk file was ever received in this environment**, and no
finding below should be read as validated against live data unless its
row is explicitly labeled `LIVE_SOURCE_VALIDATED`.

This attempt actually completed (it was not left hanging or abandoned):
**155/155 files attempted (5 leagues x 31 seasons, 1993-94 through
2023-24), 0 succeeded, 155 failed**, run at `2026-09-10T17:29:32+00:00`
(see `data_pipeline/retrieval_log.json`'s `run_at_utc`/`summary`). Every
one of the 155 failures recorded `error_type: CONNECTION_ERROR` with
detail `"Tunnel connection failed: 403 Forbidden"`, after the full 3
retry attempts with backoff each. See
`data_pipeline/retrieval_log.json`'s `failed_attempts[]` for the full,
per-file record — every attempt recorded `http_status: null` (no HTTP
response was ever received to have a status at all), `final_url: null`,
and `content_length: null`, honestly, rather than a fabricated
placeholder. One representative entry:

```json
{
  "league_code": "E0",
  "league_name": "English Premier League",
  "season_code": "9394",
  "requested_url": "https://www.football-data.co.uk/mmz4281/9394/E0.csv",
  "final_url": null,
  "http_status": null,
  "content_length": null,
  "error_type": "CONNECTION_ERROR",
  "error_detail": "Tunnel connection failed: 403 Forbidden",
  "attempts_made": 3
}
```

## Required labeling: LIVE_SOURCE_VALIDATED vs. FIXTURE_ONLY_VALIDATED vs. SOURCE_NOT_USABLE

Every league-season entry in `data_pipeline/reports/feasibility_report.{md,json}`
carries exactly one of these three labels, as a structured field
(`source_label`, validated by `data_pipeline/report.py::_validate_label`
against `VALID_SOURCE_LABELS`), never only as prose:

- **`LIVE_SOURCE_VALIDATED`** — this league-season's file was actually
  downloaded from football-data.co.uk in this run, inspected/validated
  against that real, live-fetched file, and that real content passed
  validation well enough to be usable.
- **`FIXTURE_ONLY_VALIDATED`** — this league-season was only exercised
  against a small, hand-crafted test fixture (`tests/fixtures/football_data/`),
  never a real downloaded file — either because no live download was
  attempted for it, or because the live download attempt failed/was
  blocked (as it was, universally, in this sandbox — see above). This is
  also the label for "we couldn't test it" in every case, including a
  failed/blocked download — it is never replaced by `SOURCE_NOT_USABLE`
  just because a download failed.
- **`SOURCE_NOT_USABLE`** — a live download DID complete with real
  Football-Data content for this league-season, but that real content
  fails validation badly enough to be unusable (missing required
  identity/result columns entirely, zero rows, or every row rejected by
  `validation.py`). This is a genuine negative finding about the source
  itself (see `data_pipeline/report.py::classify_live_download`), only
  ever assigned from real observed evidence of a real downloaded file —
  never as a stand-in for "couldn't test it."

**Given the network block above, every row in this run's feasibility
report is `FIXTURE_ONLY_VALIDATED`.** Zero rows are
`LIVE_SOURCE_VALIDATED` and zero rows are `SOURCE_NOT_USABLE` in this
sandbox run — `SOURCE_NOT_USABLE` can only ever be assigned when a real
download actually completes, which never happened here. See
`data_pipeline/reports/feasibility_report.md`'s summary table for the
per-file breakdown (all six hand-crafted fixtures, all labeled
`FIXTURE_ONLY_VALIDATED`, standing in for one league as a parser-logic
proof only — not as a stand-in for any real league/season's actual data).
All three labels are fully defined, validated, and tested in this PR
(`tests/test_report.py`) so a future run against real downloaded content —
whether it turns out usable or not — has somewhere correct to land.

## What the fixture-only run actually proves, and what it does NOT prove

**Proves** (real, verified in this run):
- The schema-inspection module correctly distinguishes present vs. absent
  core columns and per-bookmaker odds column sets (opening AND closing
  tracked separately), across a modern-style header and a pre-2000s-style
  header with far fewer columns — i.e., it does not assume one fixed
  schema.
- The validation module correctly assigns TYPED rejection reasons for:
  duplicate fixtures, conflicting fixtures (same teams+date, different
  score), missing team identity, missing odds, incomplete three-way
  prices, and two genuinely different date formats (`DD/MM/YY` vs.
  `DD/MM/YYYY`).
- The column-drift check correctly reports added/dropped columns between
  a modern-style and an older-style header.
- Every odds column, in every fixture tested, is correctly flagged
  `capture_timestamp_known: false` — the pipeline never claims a
  collection timestamp Football-Data does not provide.
- Pinnacle columns are tracked as their own distinct, separately-labeled
  field throughout (schema inspection, validation, and the normalized
  schema proposal) — never merged with or defaulted-to over other
  bookmakers.

**Does NOT prove** (genuinely unverified in this sandbox):
- **Source compatibility**: whether football-data.co.uk's real CSV files,
  for any of the five approved leagues, in any real season, actually parse
  cleanly against this pipeline's column-name assumptions
  (`schema_inspection.CORE_COLUMNS`, `KNOWN_BOOKMAKER_PREFIXES`). The
  hand-crafted fixtures were built FROM this pipeline author's knowledge
  of Football-Data's documented column conventions, not from a real
  downloaded file — a real file could differ in ways these fixtures do
  not anticipate.
- **Dataset usability**: row counts, real missingness rates, real
  bookmaker coverage per league/season, and real chronological
  seasons-available counts for Premier League, Bundesliga, La Liga, Serie
  A, or Ligue 1. None of this task's numeric coverage claims (section 5's
  3-season/3,000-fixture floor) can be checked against real data in this
  run.
- Whether Football-Data's own date-format drift (flagged as a real risk
  in the spec) actually occurs at the specific season boundaries this
  pipeline would need to cross for these five leagues, in practice.

## Final feasibility decision (required conclusion)

**Football-Data's DOCUMENTED column conventions and this pipeline's
parsing/validation logic are structurally compatible with the Soccer 1X2
feature manifest's ingestion needs, in principle** — schema inspection,
duplicate/conflict detection, date-format normalization, and per-bookmaker
odds tracking (with the Pinnacle caveat honored) all work correctly
against fixtures that mirror the source's known layout.

**However, this PR CANNOT and does NOT certify that Football-Data actually
supports the pipeline in practice, for any specific league or season**,
because:

1. **No real football-data.co.uk file was ever downloaded in this
   sandbox** (outbound access to the host is blocked by this
   environment's proxy policy, confirmed and documented above, not merely
   assumed).
2. Therefore **zero league-seasons carry the `LIVE_SOURCE_VALIDATED` or
   `SOURCE_NOT_USABLE` labels** (both require a real download to have
   actually completed) — every row in this run's feasibility report is
   `FIXTURE_ONLY_VALIDATED`.
3. **Source compatibility and dataset usability for Premier League,
   Bundesliga, La Liga, Serie A, and Ligue 1 remain UNVERIFIED**, not
   "probably fine" — this is a genuine open question this PR could not
   close in this environment, not a soft caveat on an otherwise-positive
   result.

**Which seasons are usable, per league**: **cannot be stated** — no real
season file was inspected. `data_pipeline/sources/football_data_sources.yaml`
proposes attempting every season 1993-94 through 2023-24 for all five
leagues once a live-download-capable environment is available; which of
those are actually "consistently formatted" (the resolved operator
decision's own qualifier) can only be determined once real files are
fetched and run through this same `schema_inspection.py`/`validation.py`
pipeline.

**Which required inputs (feature manifest) still need another source, even
once real Football-Data files are available** (this finding does NOT
depend on the live-download block, and holds regardless — see
`data_pipeline/NORMALIZED_SCHEMA.md`'s "Known gap" section):
- **`market_snapshot_odds_1x2`**: Football-Data's odds columns carry only
  a static "opening"/"closing" LABEL, never an actual `captured_at`
  timestamp — this manifest row's mandatory `captured_at`/
  `hours_to_kickoff` fields cannot be populated from Football-Data alone.
  A genuinely timestamped, decision-time-captured live-odds feed is a
  separate, still-open acquisition problem.
- **`confirmed_starting_lineup_strength_delta`** (optional): no
  Football-Data column exists for this; already deferred per the spec's
  resolved operator decision.
- **`weather_conditions_pre_match`** (optional): no Football-Data column
  exists for this; already excluded per the spec's resolved operator
  decision.

## Required next step before this task's "step 3" (freeze the usable
dataset contract) can happen

Re-run `python3 -m data_pipeline.download` (unmodified — the manifest and
retry/backoff logic are already correct and ready) from an environment
with actual outbound access to `football-data.co.uk`, then re-run
`python3 -m data_pipeline.report` against the resulting
`data_pipeline/retrieval_log.json` to produce a feasibility report with
real `LIVE_SOURCE_VALIDATED` rows. Only then can this task's numeric
coverage questions (seasons available, row counts, real missingness,
real bookmaker coverage per league/season) be answered for real.
