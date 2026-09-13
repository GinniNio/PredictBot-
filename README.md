# PredictBot

A deterministic, host-neutral statistical calculation package for sports betting analysis: a universal market-pricing engine, a sport-adapter framework, and a decision layer that gates real-money staking behind evidence — plus one closed research benchmark (Soccer 1X2) proving the pattern end to end on real data.

Zero runtime dependencies. Pure Python stdlib throughout (`pyproject.toml`: `dependencies = []`).

---

## What PredictBot currently does

- **Prices any multi-outcome market** (two-way, three-way, or N-way) from bookmaker prices alone: de-vigs the market, returns fair probabilities/odds and per-outcome expected value, and flags the market's own pricing quality (complete/incomplete, margin tier, arbitrage-shaped).
- **Runs as a single, host-neutral JSON-in/JSON-out CLI** (`pcbf-calculator request.json`) — one JSON object in, one JSON object out, no network calls, deterministic byte-identical output for the same input (see `docs/HOST_CONTRACT.md`).
- **Dispatches to a per-sport forecasting adapter** when one exists, and reports honestly when none does (`forecast_available: false`, a typed reason) rather than fabricating a probability.
- **Gates any real or simulated stake behind an explicit decision layer** (evidence, freshness, liquidity, uncertainty) — no market being priced, and no forecast existing, ever implies a stake is authorized.
- **Ships one closed research benchmark**: a Soccer 1X2 Elo + logistic-regression baseline (`research/soccer_1x2_elo_baseline/`), validated against real downloaded match data, kept structurally outside the production path described above.

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

## Frozen Soccer 1X2 baseline — closed research track

`research/soccer_1x2_elo_baseline/`: a pre-match Elo rating engine feeding a pure-Python multinomial logistic regression, temperature-calibrated, evaluated against real downloaded football-data.co.uk content. Real-data results (workflow run [34590227850](https://github.com/GinniNio/PredictBot-/actions/runs/34590227850), `evidence_class: LIVE_SOURCE_VALIDATED`, all four frozen-split hashes `CONFIRMED`):

| | Locked test (2024-25) | Out-of-time holdout (2025-26) |
|---|---|---|
| Model — Brier score | 0.5896 | 0.5930 |
| Naive league-frequency baseline | 0.6526 | 0.6479 |
| De-vigged opening bookmaker odds | 0.5734 | 0.5827 |

**Beats the naive baseline. Trails de-vigged opening odds.** The model carries real information but less than the market's own aggregated information — exactly the outcome that sets the minimum bar every future soccer model must clear. This track is closed:

- Deterministic (`run_twice_determinism_check`: byte-identical model artifacts across independent runs).
- Frozen (four model-development inputs individually hashed and pinned in `expected_hashes.json`; a later run whose real data drifts fails loudly, never silently).
- Real-data validated (`evidence_class: LIVE_SOURCE_VALIDATED`, confirmed by SHA-256 match against a genuine football-data.co.uk download — never inferred from row counts or fixture content alone).
- Unregistered: no model-admission-registry row, no adapter dispatch-table wiring, no promotion-threshold change.

## Status: RESEARCH-MODEL

Every category in the sports/adapter registry ships at `classification_ceiling: RESEARCH-MODEL` (`src/pcbf_calculator/registries/data/adapter-registry.yaml`). No adapter is registered in `adapters/registry.py::_ADAPTER_IMPLEMENTATIONS`; no row exists in the model-admission registry; every soccer promotion threshold is `PROPOSED_OPERATOR_DECISION` or `DISABLED`. `PAPER` and `CASH` are unreachable in this codebase as shipped — the decision layer (`src/pcbf_calculator/decision/`) exists and is tested, but nothing currently feeds it a registered forecast to act on.

## What remains unbuilt

- **A real forecasting adapter *registered against `adapters/registry.py`'s layer-2 dispatch***, per the Soccer 1X2 spec (`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`) — that registry is still empty, so `category: "soccer"` requests through the main `pcbf-calculator` CLI still report `forecast.forecast_available: false` exactly as before. What *does* now exist is a separate, standalone research pipeline — `python -m pcbf_calculator forecast-soccer-1x2`/`evaluate-soccer-1x2` (below) — that produces independent H/D/A research probabilities from caller-supplied historical results, compared against Bet9ja market prices, entirely outside that dispatch framework. It is deliberately not wired into it: its own evaluation evidence (Brier/log-loss/calibration/coverage from real walk-forward testing) has to exist and be reviewed first, per its own module docstrings.
- Automatic linking from a Bet9ja capture into the *forecast ledger* specifically: `python -m pcbf_calculator ingest-bet9ja` (below) validates an assembled Bet9ja capture and produces a deterministic PCBF research batch, and `python -m pcbf_calculator screen-research-batch` (below) prices it and triages it into a research queue by pricing quality, but neither writes to `ledgers/` — `python -m ledgers.cli record-forecast`/`place-ticket` are still separate, manual steps a human takes from a research queue item (see `docs/LEDGER_DAILY_WORKFLOW.md`). Bet9ja ticket/settlement capture is unaffected by either bridge.
- Capture tooling for any live-odds source other than Bet9ja pre-match Soccer 1X2, and for any Bet9ja market other than 1X2 (both are preserved in the capture's `unparsed_records` for a later adapter, never silently dropped).
- Hosting, an API surface, or a UI — this is a local CLI/library today.
- Any second sport's adapter (the framework is designed for one; only soccer has a design spec).

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

Prints one JSON object: de-vigged fair probabilities/odds and per-outcome EV under `pricing`, `forecast.forecast_available: false` (no adapter registered yet), `decision: null` (no decision input supplied), and `classification_ceiling: "RESEARCH-MODEL"`.

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

## Soccer 1X2 Poisson research forecast (independent H/D/A, market comparison)

Turns a research queue plus a **caller-supplied** historical-match file
(no network retrieval anywhere in this pipeline) into independent H/D/A
probabilities from a transparent Poisson model, compared against the
queue's own market prices — the first PCBF component that produces a real
forecast, not just pricing/triage. Still fully research-only: every
output row is fixed at `workflow_state: "FORECAST_RESEARCH"`,
`classification_ceiling: "RESEARCH-MODEL"`, `recommendation_status:
"NOT_AVAILABLE"`, `stake_status: "NOT_AVAILABLE"`, `cash_stake: 0`,
`simulated_stake: 0` — no code path in this module can set any of them to
anything else.

```bash
python -m pcbf_calculator forecast-soccer-1x2 \
  research-queue-ranked.json \
  --history soccer-history.json \
  --config config/soccer_1x2_poisson_v1.yaml \
  --output-dir runs/forecast
```

Model odds never reach the forecasting math: team/league attack-defence
rates and the independent-Poisson score matrix are fit *only* from the
supplied history; offered prices are read strictly afterward, only for
the reported `model_point_ev`/`probability_difference` comparison
against the queue's own `pricing.outcomes`. Team/competition names
resolve by exact match (after case/whitespace/safe-punctuation
normalization) or an explicit checked-in alias
(`config/soccer_1x2_team_aliases.json`) — never fuzzy matching. Every
fixture this module cannot honestly forecast — an unresolved team,
already started, insufficient evidence for its config's minimum
match thresholds, and more — abstains with a typed reason
(`src/pcbf_calculator/forecasting/errors.py`) rather than guessing;
every input market ends up in exactly one bucket, forecast or typed
abstention.

Walk-forward evaluation (train only on matches strictly before each
held-out match's own kickoff) reports coverage, multiclass Brier score,
log loss, and calibration bands against two baselines (uniform
1/3-1/3-1/3 and each competition's own walk-forward outcome frequency) —
evidence for a later promotion decision, not a promotion decision
itself; no `PAPER`-admission threshold is defined or checked here:

```bash
python -m pcbf_calculator evaluate-soccer-1x2 \
  --history soccer-history.json \
  --config config/soccer_1x2_poisson_v1.yaml \
  --output-dir runs/evaluation
```

See `src/pcbf_calculator/forecasting/soccer_1x2.py`/`evaluate.py`'s own
module docstrings for the full evidence contract, gates, and
out-of-scope list (no automated historical-data retrieval, no ML
frameworks, no staking, no `PAPER`/`CASH` promotion, no other sports).

Run the test suite (zero dependencies, stdlib `unittest`):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## License

MIT
