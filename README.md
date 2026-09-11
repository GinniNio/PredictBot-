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

- A real forecasting adapter registered against the Soccer 1X2 spec (`docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`) — the research baseline above is a benchmark, not an admitted adapter.
- Automatic linking from a Bet9ja capture into the forecast ledger, and Bet9ja ticket/settlement capture (`browser_extension/bet9ja_capture/` produces normalized fixture JSON only; `python -m ledgers.cli record-forecast`/`place-ticket` are still separate, manual steps — see `docs/LEDGER_DAILY_WORKFLOW.md`).
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

Run the test suite (zero dependencies, stdlib `unittest`):

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

---

## License

MIT
