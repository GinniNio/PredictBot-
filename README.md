# PredictBot

A deterministic, host-neutral statistical calculation package for sports betting analysis: a universal market-pricing engine, a registered per-sport forecasting adapter, and a decision layer that gates real-money staking behind evidence.

Zero runtime dependencies. Pure Python stdlib throughout (`pyproject.toml`: `dependencies = []`).

---

## What PredictBot currently does

- **Prices any multi-outcome market** (two-way, three-way, or N-way) from bookmaker prices alone: de-vigs the market, returns fair probabilities/odds and per-outcome expected value, and flags the market's own pricing quality (complete/incomplete, margin tier, arbitrage-shaped).
- **Runs as a single, host-neutral JSON-in/JSON-out CLI** (`pcbf-calculator request.json`) — one JSON object in, one JSON object out, no network calls, deterministic byte-identical output for the same input (see `docs/HOST_CONTRACT.md`).
- **Dispatches to a per-sport forecasting adapter** when one exists, and reports honestly when none does or when it declines to forecast (`forecast_available: false`, a typed reason) rather than fabricating a probability. `category: "soccer"` has a real, registered adapter today (`soccer_1x2_elo_v1`, see below) — every other category still reports `forecast_available: false`.
- **Gates any real or simulated stake behind an explicit decision layer** (evidence, freshness, liquidity, uncertainty) — no market being priced, and no forecast existing, ever implies a stake is authorized.
- **Registered Soccer 1X2 forecasting adapter** (`src/pcbf_calculator/adapters/soccer_1x2_elo_v1/`): the same Elo + logistic-regression research baseline below, wired into `adapters/registry.py` with its real trained artifact, real evidence provenance, and typed fail-closed abstention for every unresolved identity, stale artifact, or already-started fixture. Still capped at `classification_ceiling: RESEARCH-MODEL` (see below) — a real forecast, never a betting decision.

## Verified host support

Tested against `docs/HOST_CONTRACT.md`'s contract across three project-style hosts (`docs/HOST_TEST_RESULTS_MATRIX.md`):

| Host | Python-wheel execution |
|---|---|
| ChatGPT Project | Supported |
| Claude Cowork Project | Supported |
| Gemini orchestration host | **Not supported** — the tested Gemini environment cannot execute Python or install a wheel at all; it can only orchestrate the JSON request/response contract around a host that can |

No Gemini-specific workaround exists or is planned as part of this repository's current scope (`docs/CALCULATOR_KICKOFF.md`).

## Data coverage

Five European leagues via [football-data.co.uk](https://www.football-data.co.uk/) (free, no API key): Premier League (E0), Bundesliga (D1), La Liga (SP1), Serie A (I1), Ligue 1 (F1). The feasibility/download pipeline (`data_pipeline/`) covers 165+ (league, season) files back to the 1990s, with per-file provenance (SHA-256, retrieval timestamp, source URL) recorded in `data_pipeline/retrieval_log.json` — never fabricated, never silently substituted when a file is missing.

## Universal pricing capabilities

`src/pcbf_calculator/pricing/engine.py::analyze_market` — the one pricing implementation every sport and every adapter reuses:

- De-vigs any complete N-way market (proportional method) into fair probabilities/odds.
- Per-outcome expected value at a given stake; an optional calibrated lower-confidence-bound EV when an adapter supplies real sample-size/uncertainty data (never fabricated when it doesn't).
- Market-quality signals: `NORMAL` / `LOW_EVIDENCE_HIGH_MARGIN` (margin > 0.5) / `ANOMALOUS_NEGATIVE_MARGIN` (implied probabilities sum to under 1.0 — an arbitrage-shaped market) — a signal about the *market's* pricing, never a per-outcome betting recommendation.

## Registered Soccer 1X2 Elo v1 adapter

`src/pcbf_calculator/adapters/soccer_1x2_elo_v1/`, registered in `adapters/registry.py::_ADAPTER_IMPLEMENTATIONS["soccer"]`: a pre-match Elo rating engine feeding a pure-Python multinomial logistic regression, temperature-calibrated, trained and evaluated against real downloaded football-data.co.uk content (5 leagues: Premier League/E0, Bundesliga/D1, La Liga/SP1, Serie A/I1, Ligue 1/F1). Real-data results (workflow run [34590227850](https://github.com/GinniNio/PredictBot-/actions/runs/34590227850), `evidence_class: LIVE_SOURCE_VALIDATED`, all four frozen-split hashes `CONFIRMED`):

| | Locked test (2024-25) | Out-of-time holdout (2025-26) |
|---|---|---|
| Model — Brier score | 0.5896 | 0.5930 |
| Naive league-frequency baseline | 0.6526 | 0.6479 |
| De-vigged opening bookmaker odds | 0.5734 | 0.5827 |

**Beats the naive baseline. Trails de-vigged opening odds.** The model carries real information but less than the market's own aggregated information. The adapter is:

- Deterministic (`run_twice_determinism_check`: byte-identical model artifacts across independent runs; every CLI/pipeline command built on it is likewise proven byte-identical on a repeated run).
- Frozen/provenance-pinned (`data/model_artifact_manifest.json` records the exact workflow run, commit, and hash-provenance status this artifact was built from; `build_manifest.py` refuses to build a manifest unless `evidence_class: LIVE_SOURCE_VALIDATED` and every frozen-split hash is `CONFIRMED`).
- Real-data validated (confirmed by SHA-256 match against a genuine football-data.co.uk download — never inferred from row counts or fixture content alone).
- Fail-closed by identity, not fuzzy-matched: an unresolved competition or team name, a stale artifact, or an already-started fixture abstains with a typed `FORECAST_*` reason rather than guessing (`adapters/soccer_1x2_elo_v1/errors.py`).
- Still capped at `classification_ceiling: RESEARCH-MODEL` (see below) — registering the adapter changes nothing about staking authorization.

## Status: RESEARCH-MODEL

Every category in the sports/adapter registry ships at `classification_ceiling: RESEARCH-MODEL` (`src/pcbf_calculator/registries/data/adapter-registry.yaml`). `category: "soccer"` has one registered adapter (`soccer_1x2_elo_v1`, above); no other category does. No row exists in the model-admission registry (`registries/data/model-admission-registry.yaml` — zero rows, by design); every soccer promotion threshold is `PROPOSED_OPERATOR_DECISION` or `DISABLED`. `PAPER` and `CASH` are unreachable in this codebase as shipped — the decision layer (`src/pcbf_calculator/decision/`) exists and is tested, but a registered forecast existing is not itself an admission: nothing currently feeds the decision layer a `decision_input` to act on, and no model-admission row exists to authorize one even if it did.

## What remains unbuilt

- Ticket placement from a ranked research market: `run-bet9ja-research --ledger-dir` (below) idempotently records every ranked market and forecast abstention into the forecast ledger, but `python -m ledgers.cli place-ticket` is still a separate, manual step a human takes from a recorded forecast (see `docs/LEDGER_DAILY_WORKFLOW.md`). `ingest-football-data-results` (below) automates forecast-ledger settlement/scoring from football-data.co.uk files; the human decision of *which* market to bet, and the placement of the bet itself with Bet9ja, remain entirely manual. `import-bet9ja-tickets` (below) records the resulting real tickets into the betting ledger after the fact -- 131 of this session's own 158 captured tickets import cleanly today; the remaining 27 (unlabeled "N Folds" open SYSTEM tickets) need the capture-side `stake_buckets` fix described there to close, since a real, live-confirmed DOM structure for that field has not yet been verified.
- Capture tooling for any live-odds source other than Bet9ja pre-match Soccer 1X2, and for any Bet9ja market other than 1X2 (both are preserved in the capture's `unparsed_records` for a later adapter, never silently dropped).
- Hosting, an API surface, or a UI — this is a local CLI/library today.
- Any second sport's adapter (the framework is designed for one; only soccer has a design spec).
- A country-aware competition identity check: `soccer_1x2_elo_v1`'s competition resolution currently matches on name text alone (e.g. "Premier League"), not name **and** country — a different country's league sharing one of the 5 covered leagues' name fails closed today only because team names don't happen to collide, not because identity is actually checked. Flagged as a known gap, not yet fixed.

## Local run example

```bash
git clone https://github.com/GinniNio/PredictBot-
cd PredictBot-
pip install -e .

cat > request.json <<'JSON'
{
  "event_id": "demo-1",
  "category": "soccer",
  "market_prices": {"home": 2.1, "draw": 3.4, "away": 3.9}
}
JSON

pcbf-calculator request.json
```

Prints one JSON object: de-vigged fair probabilities/odds and per-outcome EV under `pricing`, `forecast.forecast_available: false` (`FORECAST_INPUT_INCOMPLETE` — no `fixture` block was supplied, so the registered soccer adapter has nothing to identify a match by; see below for a request that does), `decision: null` (no decision input supplied), and `classification_ceiling: "RESEARCH-MODEL"`.

## One-command Bet9ja forecast research pipeline

Turns one Bet9ja capture into a deterministic, model-enriched research queue in a single command — ingestion, the existing market-quality gate, and the registered soccer forecast adapter, end to end:

```bash
python -m pcbf_calculator run-bet9ja-research \
  bet9ja-soccer-all-soccer-2026-09-12T15-30-23Z.json \
  --output-dir runs/session-id
```

Reuses — never reimplements — `ingest-bet9ja`'s ingestion, `screen-research-batch`'s own market-quality gate, and `pcbf-calculator`'s own pricing/forecasting (`src/pcbf_calculator/orchestration/bet9ja_research_session.py`). Every fixture in the raw capture ends up in exactly one of four typed buckets, each written to its own file:

| File | Contents |
|---|---|
| `research-session-report.json` | Canonical reconciled totals and typed reason counts |
| `forecast-research-ranked.json` | Every fixture with a real forecast — full H/D/A model probabilities, market probabilities, per-outcome differences, model point EV, artifact provenance, deterministic calculation hash |
| `forecast-abstentions.json` | Every typed adapter abstention (unresolved team/competition, stale artifact, already started, …) |
| `ingestion-and-screening-exclusions.json` | Every ingestion quarantine and pricing-quality exclusion |

`forecast-research-ranked.json` is ranked by `research_priority_score` — the pricing engine's own already-documented market-quality prioritization signal, the exact same score `screen-research-batch` orders its own queue by — **never** by model probability, model-vs-market divergence, or expected value. Every ranked market carries the fixed, non-configurable block `classification_ceiling: "RESEARCH-MODEL"`, `cash_stake: 0`, `simulated_stake: 0`, `recommendation_status: "NOT_AVAILABLE"`, `operator_decision: null` — this is a research shortlist for a human-controlled decision process to act on next, never a betting slip, and no outcome is ever singled out, named "best," or reduced to a stake.

The lower-level `ingest-bet9ja` and `screen-research-batch` commands (below) remain available unchanged, for debugging or for a workflow that only needs one stage at a time; `run-bet9ja-research` is additive, not a replacement.

### Optional: write straight into the forecast ledger (`--ledger-dir`)

```bash
python -m pcbf_calculator run-bet9ja-research \
  bet9ja-soccer-all-soccer-2026-09-12T15-30-23Z.json \
  --output-dir runs/session-id \
  --ledger-dir ledger_data
```

Omitting `--ledger-dir` leaves everything above byte-for-byte unchanged — no ledger is touched. Supplying it also idempotently writes one `RECORDED` event per ranked market and per typed forecast abstention into `ledger_data/forecast-ledger.jsonl` (`src/pcbf_calculator/orchestration/forecast_ledger_writer.py`), reusing the ledger's own existing natural key and duplicate/conflict rules (`ledgers/forecast_ledger.py`/`ledgers/storage.py`) — never reimplemented. The whole batch is preflighted before a single byte is written: if any record would conflict with existing ledger content under an unchanged natural key, **nothing is written at all** — neither the ledger nor that call's own four research output files above — and the command exits non-zero. A ranked market's event carries its full H/D/A model probabilities and provenance; a forecast abstention's carries `model_probabilities: null` and its exact typed `stop_reason`, with `model_version`/`artifact_hash` still recorded since the adapter always loads its real artifact even when it declines to forecast. Every event fixes `selection: null`, `selection_status: "considered"`, `operator_decision: null`, `classification: "RESEARCH-MODEL"` — this never places a ticket, sizes a stake, or touches `ledgers/betting_ledger.py` at all. Every ranked market's and forecast abstention's `RECORDED` event also carries the adapter's own resolved settlement identity (`competition_code`, `resolved_home_team`, `resolved_away_team`, `scheduled_date`) whenever the adapter resolved one — the join key the results-settlement command below matches against.

## Results and closing-odds settlement (`ingest-football-data-results`)

Closes `capture → forecast → record → settle → score`: matches already-recorded forecasts back against real match results and closing odds, and appends `SCORED` events (Brier score, log loss, opening-vs-closing market comparison) to the forecast ledger.

```bash
python -m pcbf_calculator ingest-football-data-results \
  data_pipeline/downloads/ \
  --ledger-dir ledger_data \
  --output-dir runs/settlement-session-id
```

`data_pipeline/downloads/` may be a single football-data.co.uk CSV file or a directory of them. **football-data.co.uk, not Bet9ja's own settled-bets capture, is this pipeline's settlement source**: Bet9ja's settled-bets capture (`browser_extension/bet9ja_capture/settled_bets_parser.js`) remains the source for tickets actually placed, but it can never settle every research forecast — a forecast that was never placed as a ticket (the normal case for most of `run-bet9ja-research`'s own ranked queue) never appears in betting history at all. football-data.co.uk covers the same five leagues `soccer_1x2_elo_v1` forecasts and supplies both results and, for many rows, real closing-price columns, independent of whether any given forecast was ever bet on. Bet9ja ticket settlement stays a separate, later bridge using the existing settled-bets capture and `ledgers/betting_ledger.py` — this command never touches that ledger at all.

**Matching key — never team names alone**: `(competition_code, resolved_home_team, resolved_away_team, scheduled_date, market_type)`. Both sides of the match are canonicalized through the identical `soccer_1x2_elo_v1` identity resolution (`adapters/soccer_1x2_elo_v1/identity.py`) — a football-data.co.uk row or a recorded forecast that cannot be canonicalized this way is never guessed into a match; it is reported with a typed reason instead. **Known, documented gap**: `scheduled_date` is a UTC calendar date on the forecast side but football-data.co.uk's own local calendar date on the settlement side — these disagree only for a fixture whose kickoff itself falls within the narrow window around local midnight that crosses into a different UTC day (never observed in this module's own 792-real-fixture verification below); see `orchestration/football_data_settlement.py`'s own module docstring.

**Closing-odds rules**: only a column set genuinely labeled "closing" (never opening/pre-closing) is ever used, and only when all three (H/D/A) prices are present and valid for that row; when a file supplies more than one closing source, Pinnacle is preferred (a policy choice, never asserted as ground truth), and `closing_odds_source` always names exactly which columns were used. A forecast is still scored when no closing-odds source is usable — `closing_odds` stays `null`, never fabricated or backfilled from opening odds. No network access, no retrieval, no invented prices.

**Atomicity and idempotency**: the whole batch is preflighted against the ledger's current on-disk content before a single `SCORED` event is written — one conflicting previously-settled result (a forecast already scored with a *different* actual result or closing odds) blocks the entire batch, writing nothing, using the same OS-level exclusive lock (`ledgers/locking.py`, shared with `--ledger-dir` above) for the whole preflight-then-commit sequence. Re-running against the same files is a safe, idempotent no-op.

Always produces four output files: `settlement-report.json` (counts and row-accounting reconciliation), `settled-forecasts.json` (every forecast scored this run), `unmatched-results.json` (every source row or matched forecast that could not be scored, with a typed reason), and `settlement-conflicts.json` (populated, and the command exits non-zero, only when the batch was aborted).

Explicit boundaries: forecast ledger only — no betting-ledger write, no Bet9ja capture/scraping change, no automated model retraining, no calibration adjustment, no `PAPER`/`CASH` promotion, no outcome or stake decision of any kind.

## Prospective performance and calibration reporting (`report-forecast-performance`)

Closes the loop: `capture → forecast → record → settle → score → report`. Reads only `SCORED` forecast-ledger events and produces deterministic, byte-identical (for unchanged ledger content) JSON reports — no ledger write of any kind.

```bash
python -m pcbf_calculator report-forecast-performance \
  --ledger-dir ledger_data \
  --output-dir runs/performance-report
```

Always produces four files:

| File | Contents |
|---|---|
| `performance-summary.json` | Overall multiclass Brier score, log loss, accuracy, and outcome distribution; the same broken down by competition, model artifact hash, and forecast month (the month a forecast was recorded, never the fixture's kickoff month) |
| `calibration-report.json` | Per-outcome (home/draw/away) reliability-diagram calibration against fixed, documented bins (10 equal-width bins over `[0, 1]` — never data-driven) |
| `closing-line-report.json` | The model's own Brier/log-loss/accuracy vs. the closing market's de-vigged probability, on the subset of scored forecasts with complete closing odds — kept in its own `model`/`closing_market` blocks, never blended with `performance-summary.json`'s own model-quality metrics |
| `excluded-records.json` | Every `SCORED` forecast rejected from every metric above, with a typed reason (below) |

**Small samples are never silently treated as evidence**: every count, at every level (overall, per breakdown bucket, per calibration bin), carries its own `small_sample` flag (`sample_count < 50` — the same "full correction" threshold this project's own Kasiro Brain history already documents for `pcbf_ml`'s personal calibration layer, not a freshly invented number). A handful of real scored forecasts is never presented, anywhere in these reports, as production-ready evidence.

**Rejected, not silently included**: a `SCORED` forecast is excluded when its `model_probabilities` are malformed (missing an outcome, non-numeric, negative, or not summing to 1), when the ledger's own stored Brier score/log loss disagrees with an independent recomputation from that same forecast's probabilities and result (an internal-consistency check — never trusted blindly), when its `market_type` isn't `"1X2"`, when two forecasts share the same real-world fixture but report *different* actual results (the same match cannot have two results — both are excluded, never one arbitrarily kept), or when a single forecast somehow carries more than one `SCORED` event on disk (never producible by normal ledger writes — a defensive check, since blindly merging two disagreeing scores would otherwise silently prefer whichever is last in file order).

**Explicit reconciliation invariant**: every eligible scored record (one per forecast_id carrying a `SCORED` event) appears in exactly one of the included set or `excluded-records.json` — never both, never neither. Enforced directly (raises if violated) and independently re-reported as `performance-summary.json`'s own `reconciles` boolean, so a caller reading that one file can confirm the totals without re-deriving anything.

Explicit boundaries: reports only. Never writes to any ledger, never touches the model-admission registry, `classification_ceiling`, promotion/threshold state, staking, ticket construction, or any `operator_decision`.

## Artifact refresh lifecycle (`refresh-soccer-artifact`) — CANDIDATE creation, never promotion

Builds a new, immutable **candidate** artifact bundle from a training run and compares it against the currently shipped (incumbent) artifact — but never registers, classifies, activates, or promotes anything. The currently shipped adapter artifact under `pcbf_calculator/adapters/soccer_1x2_elo_v1/data/` is a read-only input here, never touched.

```bash
python -m pcbf_calculator refresh-soccer-artifact \
  --training-input <path to raw football-data.co.uk files> \
  --incumbent-manifest <path to the incumbent's model_artifact_manifest.json> \
  --incumbent-performance-dir <path to the incumbent's report-forecast-performance output> \
  --output-dir <path>
```

Runs the existing training/evaluation code (`research/soccer_1x2_elo_baseline/`) **twice** against the same input and refuses to proceed if the two runs disagree on anything deterministic — internal consistency is checked, not assumed. Produces:

| File | Contents |
|---|---|
| `candidate/model_artifact.json`, `candidate/live_snapshot.json`, `candidate/evaluation_report.json` | The three real outputs of that same training/evaluation execution |
| `candidate/model_artifact_manifest.json` | Built from those exact bytes, verifying evidence class, hash provenance, and code hash — a candidate never human-reviewed yet is not blocked (unlike installing an artifact as the *shipped* adapter), but a genuine hash MISMATCH against previously pinned data, or a zero-usable-row source, still aborts |
| `candidate/candidate_bundle_manifest.json` | An immutable bundle keyed by `bundle_hash` — two runs against byte-identical `--training-input` content produce a byte-identical `bundle_hash` (and this whole file); `verify_candidate_bundle` detects a file swapped in from a different execution |
| `artifact-comparison.json` | Candidate backtest vs. incumbent backtest (apples-to-apples, both frozen held-out evaluations) in one block; the incumbent's real prospective forecast history in a **separate** block; the candidate's own prospective metrics are always `null` |
| `promotion-review.json` | Human-readable; `recommendation` is always `HOLD_FOR_PROSPECTIVE_EVIDENCE` |

**`build_identity` — a real execution-level binding, not just a code-version check.** `model_artifact.json`/`live_snapshot.json`/`evaluation_report.json`/`model_artifact_manifest.json`/`candidate_bundle_manifest.json` all carry one `build_identity` value, generated once at command start from pure content hashes of the training code, the dataset contract configuration, and every byte of the actual training input — never from wall-clock time, a process id, or randomness, so it stays identical across two separate runs (even on different machines) given identical inputs. This is deliberately stronger than comparing a training-code hash alone: two separate executions of *identical* code can still disagree in their actual data, configuration, or generated output, so matching code alone never proved "the same execution." `verify_candidate_bundle` rejects a bundle where these files' own `build_identity` values are missing or disagree — files from two different runs cannot be mixed into one bundle even when both runs used the exact same training code.

**Never compares the incumbent's real prospective record against the candidate's backtest as if they were the same kind of evidence.** A freshly trained candidate has a real backtest the moment it's built, but zero prospective forecasts — it has never forecast a real fixture. The incumbent's own prospective metrics (from `report-forecast-performance`) are reported alongside the comparison purely for context, in their own clearly labeled block, never blended into the backtest-vs-backtest numbers.

**Atomic, immutable, non-mutating**: every step runs in a private staging directory; `--output-dir` is created only after every verification has succeeded (a failure never leaves a partial candidate); `--output-dir` must not already exist (never silently overwritten); no model-admission-registry row, `classification_ceiling` change, promotion-threshold change, staking, ticket-construction, or active-artifact mutation of any kind.

No scheduled/automatic retraining and no automatic promotion in this command — a human invokes it explicitly, and turning a candidate into anything more than "created and compared" is future, separate, human-controlled work. The next step, below, is candidate **shadow forecasting**, so a candidate can accumulate its own real prospective history over the same fixtures the incumbent forecasts.

## Candidate shadow forecasting (`shadow-forecast-candidate`)

Gives a verified candidate bundle its own real prospective forecast history, over the SAME fixtures the incumbent forecasts, without ever touching the incumbent's own ledger, the active adapter registry, or anything in the decision/staking/ticket layers:

```bash
python -m pcbf_calculator shadow-forecast-candidate \
  --candidate-bundle <path to refresh-soccer-artifact's own candidate/ directory> \
  --capture <bet9ja-capture.json> \
  --ledger-dir <candidate-ledgers root> \
  --output-dir <path>
```

Independently re-verifies the candidate bundle (`verify_candidate_bundle`) before loading a single byte of it — a tampered or internally inconsistent bundle aborts before any ledger or output file is touched. Reuses, never reimplements: `ingestion/bet9ja.py`'s own ingestion, `screening/research_batch.py`'s own market-quality gate, and `bet9ja_research_session.run_bet9ja_research_session` itself (via its own new `forecast_override` hook) — the identical eligible-fixture universe and cutoff semantics (`forecast_cutoff_utc = source_captured_at_utc`, never wall-clock) `run-bet9ja-research` uses, just forecasted by the candidate's own adapter instance (`SoccerOneXTwoEloV1Adapter(data_dir=<candidate bundle>, alias_book=<the real, shipped team_aliases.json>)`) instead of the incumbent's — never through `adapters.registry`, which continues to resolve only the incumbent, completely unaffected.

Produces:

| File | Contents |
|---|---|
| `candidate-shadow-forecasts.json` | Every successful candidate forecast — sorted by a fixed, priority-free tie-break (kickoff, competition, teams, fixture id), **never** by research-priority score or any signal implying operator priority. `operator_decision` is always `null`, `recommendation_status` is always `"NOT_AVAILABLE"` |
| `candidate-shadow-abstentions.json` | Every typed forecast abstention (the adapter's own `FORECAST_*` codes) |
| `candidate-shadow-quarantine.json` | Ingestion quarantine and pricing-quality exclusions folded together — computed independently of which model would go on to forecast, so structurally identical to what `run-bet9ja-research` would produce against the identical capture |
| `candidate-shadow-session-report.json` | Reconciled counts and reason codes |

**Physically separate candidate ledger.** Every forecast/abstention is also written as one `RECORDED` event (reusing `ledgers/forecast_ledger.py`'s own event construction/idempotent-append primitives, via `forecast_ledger_writer.write_batch`, itself unmodified) to `<ledger-dir>/<candidate_bundle_hash>/forecast-ledger.jsonl` — a directory keyed by this candidate's own `bundle_hash`, never the incumbent's own ledger location. Every row carries `model_role: "CANDIDATE_SHADOW"`, `candidate_bundle_hash`, `build_identity`, `capture_hash` (a content hash of the exact capture file used), `capture_session_id`, `forecast_cutoff_utc`, `alias_hash`, and `recommendation_status: "NOT_AVAILABLE"` — a candidate's `model_version` is always distinct from the incumbent's, so its forecast ids never collide with the incumbent's own rows for the identical fixture, even in one shared ledger file, though this command never writes to one.

**The injected team-alias book is never an unrecorded dependency.** `alias_hash` — the SHA-256 of the exact `team_aliases.json` bytes used to resolve every fixture — is recorded on every row (output files and ledger alike). Because the incumbent has no override path at all, its own "alias hash" is always this same, single, re-derivable value, so confirming a candidate's recorded `alias_hash` against a fresh computation is exactly "candidate and incumbent used the same aliases." Rerunning the same candidate against the same `--ledger-dir` location after `team_aliases.json` has changed fails with a typed `AliasHashMismatchError` before anything is written, rather than silently mixing rows resolved under two different alias books in one ledger.

The ledger *filename* is deliberately the same one the incumbent's own ledger uses (`forecast-ledger.jsonl`) — not a differently-spelled one — because `ingest-football-data-results` and `report-forecast-performance` both already hardcode that exact filename internally. Pointing either one's own `--ledger-dir` at `<ledger-dir>/<candidate_bundle_hash>/` settles or reports on this candidate's own ledger through the existing, real settlement/reporting engines, with zero code changes to either, while remaining a physically separate file from the incumbent's own ledger throughout.

**Stable join key for a future incumbent-vs-candidate comparison** (every field is already on every row this command writes): `capture_hash + settlement_identity + forecast_cutoff_utc + market_type` — never `forecast_id`, which differs by construction between a candidate and the incumbent.

**Idempotent, atomic.** The ledger write is preflighted as one whole batch, under an OS-level lock, before any output file is written — a conflicting batch leaves both completely unwritten. Re-running the identical candidate-bundle-plus-capture combination appends zero duplicate rows.

Explicitly out of scope: promotion, admission-registry rows, ranking for operator action, staking, ticket construction, settlement/scoring (run the existing settlement engine against this command's own ledger location separately, as described above), new team aliases, scheduled/automatic shadow-forecast runs.

## Bet9ja real-ticket import (`import-bet9ja-tickets`)

Records REAL Bet9ja settled/open ticket captures
(`browser_extension/bet9ja_capture/settled_bets_parser.js` /
`ticket_parser.js`'s own output) into `ledgers/betting_ledger.py` as
`PLACED` events, and (for a settled ticket) a paired `SETTLED` event.
This is a distinct pipeline from every command above: those record and
score *forecasts* (what the model would have bet); this one records
*actual wagers*, whatever they were, and their real, already-known
outcomes.

```bash
python -m pcbf_calculator import-bet9ja-tickets \
  bet9ja-settled-bets.json bet9ja-open-bets.json \
  --currency NGN \
  --betting-ledger-dir ledger_data \
  --forecast-ledger-dir ledger_data \
  --output-dir runs/ticket-import-session-id \
  [--dry-run]
```

- **Currency is mandatory and never inferred.** `--currency` is required
  on every run and stored verbatim on every ticket produced — never
  guessed from odds formatting, stake size, or the bookmaker's name.
- **Every applicable quarantine reason is reported, not just the first.**
  `evaluate_ticket` runs every independent check regardless of earlier
  failures; the report's `quarantine_reason_matrix` records every
  ticket's full reason list, and `quarantine_reason_counts` counts a
  ticket once per reason it carries (so counts can exceed the number of
  quarantined tickets).
- **A settled ticket uses the bookmaker's own reported figure, never a
  recomputed one, and never needs `potential_return` or a resolved stake
  structure at all.** When trusted fields are present (ticket ID, total
  stake, `actual_payout` -- or, for a `LOST` ticket, its absence itself
  meaning zero return -- `ticket_status`, currency, selections, source
  hash), the ticket is recorded with `settlement_basis:
  "BOOKMAKER_OBSERVED"` (`ledgers.betting_ledger.settle_bookmaker_observed`):
  `actual_return` is the bookmaker's own figure verbatim, even when a
  stake structure happens to be resolvable, since a real bookmaker figure
  already reflects voids/promotions/rounding a pure combinatorial replay
  cannot see. Such a SYSTEM ticket is placed with
  `stake_structure_known=False` (`stake_structure_basis:
  "TOTAL_ONLY_UNKNOWN_BREAKDOWN"`, `combination_count`/`unit_stake: null`)
  -- it can only ever be settled this way, never via `settle_computed`.
  `settle_computed`'s own `settlement_basis: "COMPUTED_FROM_SELECTIONS"`
  tag remains available for a caller with a real, resolvable structure
  but no bookmaker-observed figure to trust instead.
- **No `system_table_raw` parsing, ever, for an OPEN ticket's stake
  structure.** Three paths, in order: (1) a genuine, already-structured
  `stake_buckets` field (from the browser capture -- see below); (2)
  `ticket_type_raw` ("Singles"/"Doubles"/"Trebles" -- a SEPARATE,
  already-structured field `ticket_parser.js`'s own pre-existing,
  arithmetic-verified text split already produces, independently
  re-verified here against the ticket's own stake/leg-count arithmetic
  before ever being trusted); (3) the binomial identity
  `C(leg_count, k) == total_stake / unit_stake`, which (because
  `C(n,k) == C(n,n-k)`) only ever has a unique solution for a full-legs
  accumulator or an even leg count's exact midpoint. Any OPEN SYSTEM
  ticket satisfying none of the three is quarantined
  `SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE`, never guessed at from the raw
  table text.
- **Exact canonical forecast linkage, never substring matching.** Each
  leg is linked to a forecast only via the identical
  `identity.resolve_competition`/`resolve_team` resolution the adapter
  and `ingest-football-data-results` already use, matched against the
  forecast ledger's own recorded `(competition_code, resolved_home_team,
  resolved_away_team, market_type, scheduled_date)`. A leg that cannot
  resolve this way keeps `forecast_id: null` -- unlinked, never a reason
  to refuse recording the wager itself.
- **Whole-batch import safety, PLACED and SETTLED separately.** Every
  ticket is normalized, evaluated, and linked entirely in memory before a
  byte is written. The accepted PLACED events commit as one atomic batch
  (`betting_ledger.write_batch_placed`); the accepted SETTLED events then
  commit as a second atomic batch (`betting_ledger.write_batch_terminal`)
  -- nothing is written to either if that batch's own preflight finds a
  conflict, or if `--dry-run`. A duplicate ticket ID with different
  financial values under either batch aborts that whole batch, never a
  silent partial write; identical reruns append zero new events either
  way. Every original Bet9ja field is preserved verbatim (`source_raw`)
  alongside a content hash (`source_raw_hash`), quarantined tickets
  included.

**Real-data status (this session's own two capture files, 158 tickets
total):** **131 of 158 now import cleanly** -- all 113 real settled
tickets (bookmaker-observed settlement, needing neither `potential_return`
nor a resolved stake structure) and 18 of 45 real open SYSTEM tickets (a
genuine `ticket_type_raw` "Singles"/"Doubles"/"Trebles" label, path 2
above). The remaining 27 open tickets are unlabeled "N Folds" systems the
binomial-identity path alone cannot disambiguate, and stay quarantined
`SYSTEM_STAKE_BREAKDOWN_UNPARSEABLE` until the browser capture below
supplies a real, structured `stake_buckets` field for them.

**Capture-side fix.** `browser_extension/bet9ja_capture/stake_buckets.js`
walks `.mybets__systable`'s real DOM rows/cells (never the flattened
text) to emit a structured `stake_buckets` array directly, wired into
both `settled_bets_parser.js` and `ticket_parser.js`. It is defensive by
construction: any row it cannot cleanly resolve to exactly 4 cells, an
unrecognized System Type label, a duplicate fold size, or a "No. Bets"/
total that disagrees with the canonical combination count, discards the
WHOLE table rather than a partial or guessed one. See
`STAKE_BUCKETS_LIVE_VALIDATION.md` for exactly what is, and is not yet,
confirmed against a real, live Bet9ja page -- this working session had no
authenticated browser access to confirm `.mybets__systable`'s real
row/cell markup, so this module's own row/cell assumption (standard
`<table>`/`<tr>`/`<td>` structure) is built from indirect evidence
(this repository's own already-captured `system_table_raw` text shape)
and unit-tested against synthetic markup, not yet run end-to-end against
a live page.

## Bet9ja capture ingestion (research batch preparation)

Validates one assembled Bet9ja Soccer capture export (from
`browser_extension/bet9ja_capture/`'s "Download current results" button)
and produces a deterministic PCBF research batch — pure data plumbing,
no forecasting, no staking, no ledger writes:

```bash
python -m pcbf_calculator ingest-bet9ja \
  bet9ja-soccer-all-soccer-2026-09-12T15-30-23Z.json \
  --output-dir runs/bet9ja-2026-09-12
```

Rejects the whole import (nothing written) on any internal contradiction
— a ledger/fixture-count mismatch, a duplicate id, a confirmed-empty
competition with fixtures attached — rather than silently repairing
source data. Admits only complete, unambiguous pre-match Soccer 1X2
fixtures; every other record is retained, never dropped, in a typed
quarantine file alongside a reconciliation report. Kickoff times are
resolved deterministically from the page's own Africa/Lagos display text
(never guessed when the weekday/date evidence conflicts). Every admitted
fixture is tagged `classification_ceiling: "RESEARCH-MODEL"` —
unconditionally, since this bridge has no mechanism to authorize
anything else. See `src/pcbf_calculator/ingestion/bet9ja.py`'s own module
docstring for the full in/out-of-scope list.

## PCBF research-batch market-quality triage

Consumes the `pcbf-research-batch.json` the command above produces, prices
every fixture through this platform's own host-contract pipeline
(`category: "soccer"`, no new pricing math), and produces a deterministic
**research queue** — the first functional step beyond file preparation.
This is market-quality research triage, never selection or ranking of
betting outcomes: it answers "which markets have clean, complete pricing
data worth research attention next," never "which outcome is likely to
win" or "which market has an edge."

```bash
python -m pcbf_calculator screen-research-batch \
  runs/bet9ja-2026-09-12/pcbf-research-batch.json \
  --output-dir runs/bet9ja-2026-09-12/screening
```

Writes `research-queue-report.json`, `research-queue-ranked.json` (the
queue itself, ordered by pricing-quality priority) and
`research-queue-excluded.json` (every excluded market with its typed
reason). Every fixture is priced with no `decision_input` supplied (a
Bet9ja capture carries no real evidence/liquidity/uncertainty data to
honestly fill that in with), so layer 3 (the `PAPER`/`CASH` decision
engine) never runs — every research queue item's `classification_ceiling`
stays exactly `RESEARCH-MODEL` with `cash_stake: 0`/`simulated_stake: 0`,
structurally, not by convention, and each item also explicitly carries
`workflow_state: "RESEARCH_QUEUE"`, `forecast_probability_status:
"NOT_COMPUTED"`, `edge_status: "NOT_COMPUTED"`, `recommendation_status:
"NOT_AVAILABLE"` and `stake_status: "NOT_AVAILABLE"` — fixed values with
no code path that can set them to anything else. A market whose own
pricing-quality signal is not `NORMAL` (arbitrage-shaped or
high-margin/low-evidence) is excluded with a typed reason rather than
queued.

Queue order is the pricing engine's own `research_priority_score`,
orders **markets**, never outcomes — deliberately not expected value:
under proportional de-vigging, EV reduces algebraically to the same value
for every outcome in a market and is non-positive whenever margin is
nonnegative, so it is not a usable signal (see
`src/pcbf_calculator/screening/research_batch.py`'s own module docstring
for the full reasoning and in/out-of-scope list). A tighter bookmaker
margin means cleaner, more complete pricing data — not a better bet. No
outcome is ever singled out, named "best," or selected anywhere in the
output; the full per-outcome pricing detail is kept for audit, clearly
labeled as market-implied de-vigged values, never a forecast. No
probability generation, no staking, no `PAPER`/`CASH` promotion, no
recommendation of any kind.

Run the test suite (zero dependencies, stdlib `unittest`):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## License

MIT
