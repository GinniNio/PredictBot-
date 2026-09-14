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

- Ticket placement from a ranked research market: `run-bet9ja-research --ledger-dir` (below) idempotently records every ranked market and forecast abstention into the forecast ledger, but `python -m ledgers.cli place-ticket` is still a separate, manual step a human takes from a recorded forecast (see `docs/LEDGER_DAILY_WORKFLOW.md`). `ingest-football-data-results` (below) automates forecast-ledger settlement/scoring from football-data.co.uk files; Bet9ja ticket placement and settlement (the betting ledger) remain entirely manual and unaffected.
- Prospective performance reporting (coverage, abstention reasons, Brier/log-loss by competition, calibration by probability band, closing-line comparison over time) built from the now-real `SCORED` event history — not yet built.
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

**Matching key — never team names alone**: `(competition_code, resolved_home_team, resolved_away_team, scheduled_date, market_type)`. Both sides of the match are canonicalized through the identical `soccer_1x2_elo_v1` identity resolution (`adapters/soccer_1x2_elo_v1/identity.py`) — a football-data.co.uk row or a recorded forecast that cannot be canonicalized this way is never guessed into a match; it is reported with a typed reason instead.

**Closing-odds rules**: only a column set genuinely labeled "closing" (never opening/pre-closing) is ever used, and only when all three (H/D/A) prices are present and valid for that row; when a file supplies more than one closing source, Pinnacle is preferred (a policy choice, never asserted as ground truth), and `closing_odds_source` always names exactly which columns were used. A forecast is still scored when no closing-odds source is usable — `closing_odds` stays `null`, never fabricated or backfilled from opening odds. No network access, no retrieval, no invented prices.

**Atomicity and idempotency**: the whole batch is preflighted against the ledger's current on-disk content before a single `SCORED` event is written — one conflicting previously-settled result (a forecast already scored with a *different* actual result or closing odds) blocks the entire batch, writing nothing, using the same OS-level exclusive lock (`ledgers/locking.py`, shared with `--ledger-dir` above) for the whole preflight-then-commit sequence. Re-running against the same files is a safe, idempotent no-op.

Always produces four output files: `settlement-report.json` (counts and row-accounting reconciliation), `settled-forecasts.json` (every forecast scored this run), `unmatched-results.json` (every source row or matched forecast that could not be scored, with a typed reason), and `settlement-conflicts.json` (populated, and the command exits non-zero, only when the batch was aborted).

Explicit boundaries: forecast ledger only — no betting-ledger write, no Bet9ja capture/scraping change, no automated model retraining, no calibration adjustment, no `PAPER`/`CASH` promotion, no outcome or stake decision of any kind.

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
