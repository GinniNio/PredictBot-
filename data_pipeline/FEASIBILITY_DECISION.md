# Football-Data feasibility decision

**Scope reminder**: this document, and the pipeline it describes, is a
**data-feasibility** exercise (`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`,
"Next sequence," step 2). It proves whether Football-Data CAN support the
Soccer 1X2 feature pipeline. It does not build, train, calibrate, or
evaluate a forecasting model, and it does not approve any promotion
threshold or admission-registry row.

**Two separate report files, two separate kinds of evidence — do not
conflate them:**

- **`data_pipeline/reports/feasibility_report.json`/`.md`** — the REAL
  source-attempt evidence. Its `source_attempts[]` table (built by
  `data_pipeline/report.py::build_source_attempts`) has exactly one row
  per (league, season) pair this manifest actually names — every real
  network attempt this run made, whatever the outcome, plus any season
  explicitly gapped as `SOURCE_NOT_LISTED` (see "Season manifest gap
  handling" below). This is the only file that speaks to real source
  compatibility or dataset usability, and even it, in this sandbox run,
  can only report that every real attempt was blocked (see below) — it
  never certifies usability it did not actually observe.
- **`data_pipeline/reports/fixture_validation_report.json`/`.md`** — the
  hand-crafted parser-behavior evidence, entirely separate. Its 6 entries
  are all identified as the neutral `TEST_FIXTURE` (never a real league
  name), built from small CSVs checked into
  `tests/fixtures/football_data/`, never from real downloaded content.
  **This file validates parser BEHAVIOR ONLY** — duplicate/conflict
  detection, date-format normalization, per-bookmaker odds tracking — and
  must NEVER be read as evidence about source compatibility or dataset
  usability for any real league or season, no matter how its `source_label`
  reads.

## Live-download attempt: what actually happened in this sandbox

A real download attempt was made against football-data.co.uk in this
environment (`python3 -m data_pipeline.download`, the actual manifest at
`data_pipeline/sources/football_data_sources.yaml`, 5 leagues x every
season 1993-94 through 2023-24, plus a circuit breaker — see below).
**Every attempt that actually reached the network failed at the network
layer, before any HTTP response was ever received.** The environment's
outbound proxy rejects the connection to `www.football-data.co.uk:443` at
the CONNECT/tunnel stage with an explicit policy denial (`Tunnel
connection failed: 403 Forbidden` — confirmed via the proxy's own
`/__agentproxy/status` endpoint, which logs `www.football-data.co.uk:443`
as a `connect_rejected` / "gateway answered 403 to CONNECT (policy denial
or upstream failure)"). This is **not** a Football-Data outage, a rate
limit, or a malformed request — the destination host itself is not on
this sandbox's outbound allowlist. This is stated here plainly, not
softened: **no real football-data.co.uk file was ever received in this
environment**, and no finding below should be read as validated against
live data unless its row is explicitly labeled `LIVE_SOURCE_VALIDATED`.

### Circuit breaker: this run does NOT retry all 155 files individually

The manifest names 155 (league, season) pairs (5 leagues x 31 confirmed
seasons). Earlier runs of this pipeline retried every single one
independently (3 attempts each) even after the very first file had
already proven the host is completely unreachable at the proxy level —
burning time re-confirming what one file already confirmed. `download.py`
now trips a circuit breaker (`HOST_BLOCK_FAILURE_THRESHOLD = 2`): once a
single host has produced 2 `CONNECTION_ERROR` failures in this run, every
remaining season for every league on that host is recorded
`NOT_ATTEMPTED_HOST_BLOCKED` — never even attempted — instead of being
retried. This is a deliberately small threshold: a single
`CONNECTION_ERROR` could in principle be a one-off blip, but two
independent files against the same host both failing at the connection
layer (never reaching an HTTP response) is already strong, cheap-to-obtain
evidence the host itself is unreachable from this environment.

This run's actual, honest numbers, recorded at `run_at_utc`
`data_pipeline/retrieval_log.json`'s top level (see its `summary` and
`circuit_breaker` keys):

- **155 (league, season) pairs planned** (the full manifest).
- **2 actually attempted over the network** (`E0/9394`, `E0/9495`), both
  failing after their full 3 retries each with `error_type:
  CONNECTION_ERROR` / `"Tunnel connection failed: 403 Forbidden"`.
- **153 skipped** (`NOT_ATTEMPTED_HOST_BLOCKED`) once the circuit breaker
  tripped on host `www.football-data.co.uk` after the 2nd consecutive
  `CONNECTION_ERROR` (`retrieval_log.json`'s `circuit_breaker.events[]`).
- **0 succeeded.**

The evidence trail keeps these honestly distinct — `error_type`
`CONNECTION_ERROR` means "we tried this specific file and it failed";
`NOT_ATTEMPTED_HOST_BLOCKED` means "we did not even try this one because
we'd already confirmed the host is blocked." Both still carry
`http_status: null`/`final_url: null`/`content_length: null` (the former
because no HTTP response was ever received; the latter because no request
was ever sent), never a fabricated placeholder. Representative entries:

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

```json
{
  "league_code": "E0",
  "league_name": "English Premier League",
  "season_code": "9596",
  "requested_url": "https://www.football-data.co.uk/mmz4281/9596/E0.csv",
  "final_url": null,
  "http_status": null,
  "content_length": null,
  "error_type": "NOT_ATTEMPTED_HOST_BLOCKED",
  "error_detail": "Not attempted: host 'www.football-data.co.uk' was already confirmed blocked earlier in this run after 2 consecutive CONNECTION_ERROR failures against it (circuit breaker).",
  "attempts_made": 0
}
```

## Season manifest gap handling (2024-25 / 2025-26)

`football_data_sources.yaml`'s `latest_completed_season` is `"2324"`
(2023-24) for every league — deliberately NOT advanced to reflect "today."
As of this document's last edit, "today" is 2026-09-10, and by the
conventional August-to-May European domestic-season calendar both the
2024-25 season (`"2425"`) and the 2025-26 season (`"2526"`) have already
reached their natural completion point (a 2026-27 season would be the one
currently in progress). Rather than silently stopping the season list at
`2324` with no explanation, or silently advancing `latest_completed_season`
on an assumption this sandbox cannot verify (no live download access means
there is no way to confirm football-data.co.uk's URL pattern still
resolves to a real file for these two seasons — the scheme has been
stable historically, but "historically stable" is not "confirmed live"),
each league's manifest row now carries an explicit
`unconfirmed_seasons: ["2425", "2526"]` field. `data_pipeline/report.py`
surfaces every one of these 10 rows (5 leagues x 2 seasons) in the
source-attempt table with `source_label`/`download_status:
SOURCE_NOT_LISTED` — a season that exists in principle but for which this
manifest lists no confirmed source URL yet. This is a visible, explicit
gap a reader can see and act on, never a silent truncation. Once a
live-download-capable environment actually confirms these seasons' files
resolve and validate, they should be promoted into
`latest_completed_season` and dropped from `unconfirmed_seasons`.

## Required labeling: LIVE_SOURCE_VALIDATED vs. FIXTURE_ONLY_VALIDATED vs. SOURCE_NOT_USABLE (vs. SOURCE_NOT_LISTED)

Every row in `data_pipeline/reports/feasibility_report.json`'s
`source_attempts[]` table (one row per real (league, season) pair this
manifest names, per the section above) carries exactly one of these
labels, as a structured field (`source_label`), never only as prose:

- **`LIVE_SOURCE_VALIDATED`** — this league-season's file was actually
  downloaded from football-data.co.uk in this run, inspected/validated
  against that real, live-fetched file, and that real content passed
  validation well enough to be usable.
- **`FIXTURE_ONLY_VALIDATED`** — no real Football-Data content was ever
  obtained for this league-season in this run — the live download attempt
  either failed outright (`download_status: CONNECTION_ERROR`/etc.) or
  was skipped by the circuit breaker (`download_status:
  NOT_ATTEMPTED_HOST_BLOCKED`) after a host-level denial was confirmed
  elsewhere in the run (as it was, universally, in this sandbox — see
  above). Despite the name, this label is never populated from the actual
  hand-crafted fixtures' numbers for a real row — `total_rows`/
  `usable_fixtures` stay `null` here; the fixtures themselves live only in
  `fixture_validation_report.json`, entirely separately (see top of this
  document). This is also the label for "we couldn't test it" in every
  such case — it is never replaced by `SOURCE_NOT_USABLE` just because a
  download failed or was skipped.
- **`SOURCE_NOT_USABLE`** — a live download DID complete with real
  Football-Data content for this league-season, but that real content
  fails validation badly enough to be unusable (missing required
  identity/result columns entirely, zero rows, or every row rejected by
  `validation.py`). This is a genuine negative finding about the source
  itself (see `data_pipeline/report.py::classify_live_download`), only
  ever assigned from real observed evidence of a real downloaded file —
  never as a stand-in for "couldn't test it."
- **`SOURCE_NOT_LISTED`** — this season is not part of this manifest's
  confirmed URL range at all (see "Season manifest gap handling" above) —
  it was never attempted, by design, because no confirmed source URL is
  listed for it yet. Distinct from `FIXTURE_ONLY_VALIDATED`, which always
  means an attempt was made or deliberately skipped mid-run; this label
  means the manifest itself does not yet claim to know a URL for this
  season.

**Given the network block above, every one of this run's 155 real
attempted-range rows is `FIXTURE_ONLY_VALIDATED`** (2 actually attempted
and failed, 153 skipped by the circuit breaker — see above), and the 10
gap rows (5 leagues x `2425`/`2526`) are `SOURCE_NOT_LISTED`. Zero rows
are `LIVE_SOURCE_VALIDATED` and zero rows are `SOURCE_NOT_USABLE` in this
sandbox run — `SOURCE_NOT_USABLE` can only ever be assigned when a real
download actually completes, which never happened here. See
`data_pipeline/reports/feasibility_report.json`'s `source_attempts_summary`
for the exact counts. All four labels are fully defined, validated, and
tested in this PR (`tests/test_report.py`) so a future run against real
downloaded content — whether it turns out usable or not — has somewhere
correct to land.

## What the fixture-validation report actually proves, and what it does NOT prove

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
   assumed; the circuit breaker means only 2 of the 155 pairs were even
   attempted over the network before that block was confirmed and the
   remaining 153 were honestly recorded as not attempted).
2. Therefore **zero league-seasons carry the `LIVE_SOURCE_VALIDATED` or
   `SOURCE_NOT_USABLE` labels** (both require a real download to have
   actually completed) — every one of the 155 real attempted-range rows
   in this run's source-attempt table is `FIXTURE_ONLY_VALIDATED`, and the
   10 explicit gap rows (2024-25/2025-26, all five leagues) are
   `SOURCE_NOT_LISTED`.
3. **Source compatibility and dataset usability for Premier League,
   Bundesliga, La Liga, Serie A, and Ligue 1 remain UNVERIFIED**, not
   "probably fine" — this is a genuine open question this PR could not
   close in this environment, not a soft caveat on an otherwise-positive
   result. The separate `fixture_validation_report.json`'s parser-behavior
   passes do not change this — they were never evidence about a real
   league or season to begin with.

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

Re-run `python3 -m data_pipeline.download` (unmodified — the manifest,
retry/backoff logic, and circuit breaker are already correct and ready)
from an environment with actual outbound access to `football-data.co.uk`,
then re-run `python3 -m data_pipeline.report` against the resulting
`data_pipeline/retrieval_log.json` to produce a feasibility report with
real `LIVE_SOURCE_VALIDATED` rows. Only then can this task's numeric
coverage questions (seasons available, row counts, real missingness, real
bookmaker coverage per league/season) be answered for real. Once a real
run confirms the 2024-25/2025-26 URL pattern, also promote those two
seasons from `unconfirmed_seasons` into `latest_completed_season` in
`data_pipeline/sources/football_data_sources.yaml` (see "Season manifest
gap handling" above).
