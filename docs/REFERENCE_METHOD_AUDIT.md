# Reference-Method Audit — Eight External Projects

**Status: output only. No adoption.** This document is a research artifact:
a build-versus-adopt matrix and a comparison-test plan covering eight named
external references, each scoped exactly as specified by the operator. It
changes no runtime code, no registry, no dependency, and no test file.
Nothing here authorizes vendoring, cloning, or depending on any of these
projects — see the hard constraints below.

**Hard constraints this document respects:**
- No third-party code enters this repository as a result of this audit — no
  copied functions, no vendored files, no new `pyproject.toml` dependency.
- `src/pcbf_calculator`, `src/pcbf_football`, `data_pipeline/`, every
  registry file, the promotion-threshold ledger, the model-admission
  registry, and `decision/trusted_provenance.py` are untouched.
- The comparison-test plan (Part 2) is a **plan only** — none of the listed
  tests are implemented in this PR.

**Method note on sourcing.** Every entry below was researched via each
repository's public GitHub README/landing page (and, where useful,
`pyproject.toml`) through this session's web-fetch tool — no external
repository's code was cloned into this sandbox. Every claim is either
attributed to what that fetch actually returned, or explicitly marked
**unverifiable** below where the fetched content did not answer the
question. Anything not explicitly sourced this way should be treated as
provisional and re-checked against the live repository before being acted
on.

---

## Part 1 — Build-versus-adopt matrix

### 1. Penaltyblog

| Field | Content |
|---|---|
| **Scope (as specified)** | Benchmark model candidates (Poisson, Bivariate Poisson, Dixon-Coles, Bayesian, Elo/Massey/Colley/Pi ratings) AND host-runtime compatibility. |
| **Key findings** | Repo (`martineastwood/penaltyblog`) organizes into `models`, `ratings`, `implied`, `metrics`, `scrapers`, `matchflow` modules. `models` implements Poisson, Bivariate Poisson, Dixon-Coles, and Bayesian (MCMC / hierarchical Bayesian) goal models — directly overlapping the Soccer 1X2 spec section 9 decision table's first candidate row. `ratings` implements Elo, Massey, Colley, and Pi ratings — the same four rating families section 9 lists as an alternative modelling family. `implied` does odds-to-probability conversion and margin/overround removal (de-vig) — comparable in *purpose* to `pricing/engine.py::analyze_market`, but its exact de-vig method(s) were not confirmed by the fetched README text (the fetch surfaced "margin/overround removal" generically, not a named method list); this repo's own method is the single, explicitly named `multiplicative_proportional` (`pricing/engine.py`), selected through a small registry (`_DE_VIG_METHODS`) that already anticipates adding e.g. Shin's method later — Penaltyblog is reported elsewhere (sports-pricing literature, not confirmed directly in the fetch) to offer more than one de-vig method including Shin's; **this specific claim is unverified against penaltyblog's actual source in this pass** and should be re-checked before citing it as fact. `metrics` includes "Ranked Probability Score" per the fetch — directly relevant to the Soccer 1X2 spec section 10's future calibration/scoring needs (this repo's spec discusses ECE, log loss, and Brier score, not RPS; RPS is a well-known soccer-specific alternative worth knowing exists in a benchmark library even though this repo hasn't adopted it). |
| **Build vs. adopt** | **Do not adopt as a runtime dependency. Track as a benchmark/reference-implementation candidate only**, used to sanity-check a future in-repo Dixon-Coles/Elo/Poisson implementation's numbers during Release B validation (section 6/14 of the Soccer 1X2 spec), never imported into `src/pcbf_calculator`. Reasoning: (a) it is a compiled package (see below) which the zero-runtime-dependency, host-neutral constraint in `docs/HOST_CONTRACT.md` and `docs/MULTI_SPORT_ARCHITECTURE.md` would need to specifically re-litigate before any adoption could even be considered; (b) this repo's own de-vig method is already implemented, tested, and documented, and section 9's recommended first candidate (Elo-based logistic) is deliberately simple enough not to need an external modelling library at all. |
| **License** | **MIT** (per fetched repo metadata). Compatible with this project in principle — MIT permits redistribution and use with attribution — but license compatibility is moot while adoption itself is not recommended (item (a) above is the real blocker). |
| **Open verification items** | **Critical, explicit flag per the task's instruction — do not assume it would pass:** `pyproject.toml` (fetched directly) declares `build-system` requiring `["setuptools", "wheel", "numpy", "Cython"]`, and its package data includes platform-specific compiled `.so`/`.dll`/`.dylib` extension binaries under `penaltyblog/models/` and `penaltyblog/metrics/`. **Penaltyblog is a compiled/Cython package.** Per this repo's own `docs/HOST_CONTRACT.md` and `docs/HOST_TEST_RESULTS_MATRIX.md`, this repo's own wheel had to independently pass offline install (`--no-index --no-deps`), SHA-256 verification, and byte-identical two-run tests on ChatGPT Project and Claude Cowork Project specifically because those hosts have constrained, session-scoped, sometimes non-air-gapped Python environments. A Cython extension built against a specific Python ABI/platform combination is a materially different compatibility question than this repo's own pure-Python wheel — **it must pass the exact same host-compatibility test pack (`HOST_TEST_CHECKLIST.md`) on both ChatGPT Project and Claude Cowork Project, independently, before it could ever be considered as a runtime dependency.** This has not been done, is not proposed to be done by this PR, and should not be assumed to succeed — compiled-extension wheels are exactly the kind of dependency most likely to fail an offline, non-air-gapped, session-scoped host environment (missing platform-specific wheel on PyPI for the host's exact Python build, no network access to fetch one, no local compiler to build from source). Additionally unverified in this pass: penaltyblog's exact de-vig method list (see Key findings), and its probability-output dict shape (not confirmed against actual API signatures, only the general module description). |

### 2. AlphaPy

| Field | Content |
|---|---|
| **Scope (as specified)** | Experiment-configuration pattern only — not the AutoML framework. |
| **Key findings** | The fetched README describes AlphaPy generically ("a machine learning framework... built with scikit-learn and pandas," supporting scikit-learn/Keras/XGBoost/LightGBM/CatBoost) but **did not surface AlphaPy's actual experiment/config file format** (it documents this via a separate `.yml`-based per-project config, per AlphaPy's known documentation pattern, but the specific field list was not returned by this fetch and is **unverifiable from this pass** — the README fetch explicitly noted the config schema was absent from the page and pointed to `alphapy.readthedocs.io` for it). What can be compared without that detail: the operator's proposed "Experiment manifest" pattern (`dataset_hash`, `feature_manifest_version`, `split_definition`, `model_family`, `hyperparameters`, `random_seed`, `calibration_method`, `code_commit`, `artifact_hash`, `metrics`) is oriented around **provenance and reproducibility auditing** (hashes of data/code/artifact, explicit calibration method, explicit metrics record) — a different emphasis than a typical AutoML config file, which is usually oriented around **pipeline construction** (which algorithms to try, which features to use, cross-validation folds) rather than after-the-fact hash-based provenance. Based on general knowledge of AlphaPy's documented config style (not independently re-verified here), it is expected to cover `model_family`/`hyperparameters`/`split_definition`-shaped fields well, but not to natively carry `dataset_hash`, `code_commit`, or `artifact_hash` — those are specifically this repo's own integrity-audit concerns, not typical AutoML-config concerns. |
| **Build vs. adopt** | **Do not adopt AlphaPy's config format or its parser.** This repo should define its own minimal experiment-manifest schema (the operator's proposed field list is already a reasonable, purpose-built shape) rather than borrowing AlphaPy's config structure — the config-format overlap that could plausibly be borrowed (`model_family`, `hyperparameters`, `split_definition`-shaped fields) is generic enough that it does not need a specific external example to design correctly, while the provenance/audit fields this repo actually needs (hashes, calibration method, commit) are not AlphaPy's design center at all. No dependency, no vendored parser. |
| **License/compatibility** | **Apache-2.0** (per fetched repo metadata) — compatible in principle, moot given no-adopt recommendation. |
| **Open verification items** | AlphaPy's actual config file schema (field names, nesting, YAML vs. other format) was not confirmed by this pass's fetch — if a future PR wants a closer structural comparison, someone should read `alphapy.readthedocs.io`'s configuration section directly rather than relying on this entry's inference. |

### 3. georgedouzas/sports-betting

| Field | Content |
|---|---|
| **Scope (as specified)** | Loader and estimator interface boundaries only. |
| **Key findings** | Confirmed via fetch: the package's `DataLoader` is built from two independent, composable sources — a `stats` parameter (e.g. `FootballDataStats`) and an `odds` parameter (e.g. `FootballDataOdds`), plus a `param_grid` for filtering league/division/year. It exposes `extract_train_data()` (historical/backtesting) and `extract_fixtures_data()` (upcoming events) as two distinct extraction methods off the same loader. Estimators are wrapped through a `ClassifierBettor` class that encapsulates a scikit-learn pipeline (preprocessing + `MultiOutputClassifier`) plus betting-specific parameters (initial cash, stake size, markets), exposing `fit()`/`bet()`. |
| **Comparison against this repo** | This repo's `data_pipeline/download.py` + `schema_inspection.py` + `validation.py` already separate concerns similarly in spirit — `download.py` only fetches and hashes raw bytes, `schema_inspection.py` only reports what columns/bookmaker-odds a file actually contains, `validation.py` only validates rows/results — but the split is along a **pipeline-stage** axis (fetch -> inspect -> validate), not sports-betting's **data-provenance** axis (stats source vs. odds source as two independently swappable inputs to one loader). The two axes are not in conflict — this repo's pipeline currently only has one source (Football-Data, which happens to carry both results and odds columns in the same file) so the stats/odds split has not yet been forced to exist. The `SportAdapter` interface (`src/pcbf_calculator/adapters/base.py`) already declares a `data_sources: tuple[str, ...]` field on `AdapterInterfaceDeclaration`, which is the seam where a future adapter could plausibly need to distinguish "which source supplied the stats" from "which source supplied the odds" the way sports-betting's `DataLoader` does explicitly — today it is an undifferentiated tuple of source ids. sports-betting's `ClassifierBettor` wrapping pattern (scikit-learn pipeline + stake/cash parameters wrapped into one betting object) does not map cleanly onto this repo's layering: this repo keeps market pricing (layer 1), forecasting (layer 2, `SportAdapter`), and stake/decision policy (layer 3, `decision/engine.py`) as three separate objects/modules by explicit design (`docs/MULTI_SPORT_ARCHITECTURE.md`'s repeated "never merged into one score" language) — sports-betting's single wrapped-bettor object is the opposite design choice (forecasting model and stake logic fused into one class), which this repo has already deliberately rejected for good, documented reasons (keeping `forecast_quality` and `market_profitability` distinct, in particular). |
| **Build vs. adopt** | **Do not adopt sports-betting's `DataLoader`/`ClassifierBettor` classes or interface signatures.** Worth borrowing conceptually, not structurally: when a second odds/stats source is eventually added (Release B+), consider whether `AdapterInterfaceDeclaration.data_sources` should become a structured mapping (e.g. `{"stats": [...], "odds": [...]}`) rather than a flat tuple, purely as a low-risk future refinement — not required by this audit, not proposed as a change here. The estimator-wrapping pattern is explicitly incompatible with this repo's layer separation and should not be borrowed at all. |
| **License** | **MIT** (per fetched repo metadata). |
| **Open verification items** | None blocking — the interface details above were directly confirmed by the fetch. |

### 4. BeatTheBookie

| Field | Content |
|---|---|
| **Scope (as specified)** | Market baseline and evaluation methodology only. **Explicit constraint, honored here: none of BeatTheBookie's profitability claims are inherited or relied upon without independent reproduction on current, untouched data.** |
| **Key findings** | The fetched content (paper title "Beating the bookies with their own numbers," repo README) describes a methodology of comparing multiple bookmakers' odds/closing-odds series to find mispriced/divergent markets, i.e. a market-implied baseline built from the bookmakers' own numbers rather than an independent model. Its profitability claims are informal and self-described as weak in practice — the fetch surfaced the authors' own statement that "the effort of deploying such a strategy is completely worthless, considering the time spent... and the monetary reward," alongside an anecdotal claim about triggering account limits. These are **not** rigorous, reproducible backtest results by the fetched account, and per the operator's explicit instruction, none of it is treated as evidence of a real edge here. |
| **Comparison against this repo** | The Soccer 1X2 spec's section 8 baseline (market-implied baseline: always predict this platform's own de-vigged fair probability, `pricing/engine.py::analyze_market`'s `fair_probability`, as the candidate-model promotion bar) is a stricter, more precisely specified version of "compare against the market's own numbers" than BeatTheBookie's informal divergence-hunting: this repo's baseline is a single, deterministic, already-implemented function output, used as a strict must-beat bar (section 14's "beats-baseline-by-X" gate) rather than as a standalone trading signal. BeatTheBookie's approach (cross-bookmaker divergence as the *signal itself*, not just the *baseline to beat*) is a different methodology than anything in the Soccer 1X2 spec — this repo's baseline is single-market, single-source de-vig, not a cross-bookmaker consensus/divergence detector. |
| **Build vs. adopt** | **Do not adopt.** No code, no formula, and critically no profitability figure from BeatTheBookie should be cited as evidence this repo's approach (or any future adapter) will be profitable — that would need independent reproduction on current data per the operator's constraint, and this audit does not attempt that reproduction (out of scope). The one conceptually interesting idea worth *noting, not adopting*: a future data-quality signal could compare this repo's own captured `market_snapshot_odds_1x2` across multiple bookmaker columns (Football-Data already carries several) for divergence, as a data-quality check rather than a trading signal — this is speculative and not proposed as a concrete change here. |
| **License** | **GPL-3.0** (per fetched repo metadata) — this alone would make source-level adoption a copyleft concern even if adoption were otherwise being considered, which it is not. |
| **Open verification items** | The paper's exact statistical methodology (sample size, time window, statistical significance testing if any) was not retrievable in enough detail from this fetch to characterize further; this is not needed for the "do not adopt, do not inherit claims" recommendation above, but a future reader wanting the full methodology should read the actual paper, not this summary. |

### 5. ProphitBet-Soccer-Bets-Predictor

| Field | Content |
|---|---|
| **Scope (as specified)** | Append-only prediction storage and seasonal reporting only. |
| **Key findings (external)** | Per the fetch, ProphitBet's current release "tries to append the new metrics or predictions into the existing file" (CSV/Excel) rather than overwrite it, and includes "analytical evaluation metrics per season." This is directionally aligned with the operator's proposed append-only forecast-record pattern (never overwrite yesterday's predictions once results are known; each record keeps its original inputs/price snapshot/model version/hashes), though the fetch did not confirm ProphitBet records anything as rigorous as a price snapshot, model-version string, or content hash per row — its append behavior appears to be simple accumulation of prediction/metric rows, not a hash-audited ledger. |
| **Key findings (this repo, verified directly — important)** | **This repo already has a concrete, non-trivial entanglement with ProphitBet that the task asked to be checked, not assumed.** `git ls-files -s ProphitBet-Soccer-Bets-Predictor` shows a tracked gitlink (mode `160000`, commit `49fb86be553a62ed714acc1e2d99c73f6dcd27ea`) added in commit `8e72239` ("Add 5-league models and weekly picks pipeline"). **There is no `.gitmodules` file in this repository** — confirmed by `cat .gitmodules` returning nothing and no `.gitmodules` entry in `git ls-files`. This exactly matches the CI warning quoted in the task ("`fatal: No url found for submodule path 'ProphitBet-Soccer-Bets-Predictor' in .gitmodules`"): a gitlink was committed (Git records the *presence* of a nested repo and its exact commit SHA) but the accompanying `.gitmodules` file that would tell Git *where to fetch it from* was never added. `docs/handoffs/2026-04-28-predictbot-session-handoff.md` independently documents this exact situation in its own words: *"ProphitBet added as embedded git repo... It was committed as a gitlink (empty folder on GitHub), not as a submodule. The actual ProphitBet code only exists locally... if anyone else clones PredictBot they won't get ProphitBet automatically,"* and lists *"Fix ProphitBet as proper git submodule if others need to clone the repo"* as an open follow-up. Several top-level scripts (`backtest.py`, `train_leagues.py`, `weekly_picks.py`) and `README.md`/`SETUP_GUIDE.md` reference a local `ProphitBet-Soccer-Bets-Predictor/` directory by hardcoded Windows path (`C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\...`), confirming these scripts depend on a local, non-reproducible checkout that is not actually vendored in this repository's own git history — **it is not "already vendored in cleanly"; it is a broken gitlink with no fetch URL, and every script that assumes it exists will fail on a fresh clone.** This is a real, verified repository defect, separate from and outside the scope of this audit's own changes (this PR does not touch it, per the "no third-party code enters the repo" and "do not touch..." constraints — it is flagged here only because the task explicitly asked this audit to check and report, not fix). |
| **Build vs. adopt** | **Do not adopt ProphitBet's storage code or its GUI.** The append-only *pattern* the operator already proposed is more rigorous than what the fetch confirmed ProphitBet actually does (ProphitBet's append behavior is metrics/predictions accumulation; the operator's proposal specifically adds hashes, price snapshots and model versions per record, which is the harder, more valuable part) — there is nothing structurally novel in ProphitBet's approach worth borrowing beyond the general "append, don't overwrite" idea, which this repo's own operator has already independently arrived at with a stronger design. |
| **License** | **MIT** (per fetched repo metadata) — moot given no-adopt recommendation, but relevant context: MIT would not itself have blocked vendoring ProphitBet properly (as a real submodule with a `.gitmodules` entry, or a pinned wheel/zip), which makes the broken-gitlink situation purely an operational gap, not a licensing one. |
| **Open verification items** | Whether `ProphitBet-Soccer-Bets-Predictor`'s gitlink commit (`49fb86be553a62ed714acc1e2d99c73f6dcd27ea`) corresponds to any specific upstream ProphitBet release was not checked (out of scope — this audit does not fetch or reconcile that commit's contents); a future cleanup task (separate from this audit, since it touches repository top-level scripts and history, none of which this audit is permitted to change) should decide whether to (a) properly add `.gitmodules` and re-point the gitlink, (b) remove the gitlink entirely and document ProphitBet as an external manual-setup dependency (per `README.md`/`SETUP_GUIDE.md`'s own instructions to `git clone` it fresh), or (c) vendor a specific pinned snapshot — this audit takes no position on which, since it is out of scope. |

### 6. WagerBrain

| Field | Content |
|---|---|
| **Scope (as specified)** | Pricing math edge cases only. **The operator has already noted adopting the package would add duplication — this entry does not propose adoption; its entire value is the concrete test-gap comparison, expanded in Part 2.** |
| **Key findings** | Confirmed via fetch: WagerBrain documents odds conversion (American/Decimal/Fractional), implied-probability conversion (both directions), profit/payout calculation, EV, Kelly criterion, parlay-odds calculation, vig/spread calculation, and arbitrage-opportunity evaluation. Its documented **examples are all 2-way** (a tennis moneyline arbitrage example, a two-team vig example) — the fetch could not confirm from the README whether its vig/arbitrage functions generalize to N-way (N>3) markets at all, which is itself informative: this repo's own `pricing/engine.py::analyze_market` already handles two-way, three-way, and N-way markets through one unmodified code path (proven by `tests/test_pricing_engine.py::test_n_way_market`, an 8-outcome case) — on the specific "N-way with N>3" edge case named in the task, **this repo's own engine and test suite already exceed what WagerBrain's public documentation demonstrates**, not the other way around. The genuinely actionable gaps run the other direction: WagerBrain names **Kelly criterion** and **arbitrage-opportunity evaluation** as first-class documented features; this repo's pricing engine implements neither (there is no Kelly-stake-sizing function anywhere in `pricing/engine.py`, and no test asserts behavior on a genuine negative-margin/arbitrage-shaped market — confirmed by `grep`: the string `ANOMALOUS_NEGATIVE_MARGIN` appears in `pricing/engine.py`'s `_market_quality` function but is only ever asserted as *one of three possible values* in `tests/test_pricing_engine.py::test_market_quality_fields_present_and_computed` — no test actually feeds in a negative-margin market and asserts that value is the one produced). |
| **Comparison against this repo's existing test coverage** | See Part 2 below for the specific proposed tests. Summary: (1) no existing test exercises a genuine arbitrage-shaped market (`sum(1/price) < 1`, i.e. actual negative bookmaker margin) end-to-end and asserts `evidence_quality == "ANOMALOUS_NEGATIVE_MARGIN"`; (2) this repo has no Kelly-criterion function at all, so there is no test gap to close *for existing code* — Kelly is out of scope for `pricing/engine.py` unless a future PR adds it, but this is worth recording as a **known absent feature**, not a bug, since the spec never asked for Kelly staking (`decision/engine.py`'s stake tiering is a classification-driven policy, not a bankroll-optimal-fraction calculation); (3) WagerBrain's American/Fractional odds-conversion surface has no analogue in this repo at all — `pricing/engine.py` only accepts decimal odds and documents this convention explicitly; this is a deliberate, documented scope choice (decimal-odds convention throughout, per the module docstring), not a gap, and is noted here only so a future contributor does not mistake WagerBrain's broader input-format support for evidence this repo is missing something it actually chose not to build. |
| **Build vs. adopt** | **Do not adopt.** The operator's existing "would add duplication" judgment is correct and this audit does not challenge it — everything WagerBrain does that overlaps this repo's actual scope (de-vig, EV, N-way support) is already implemented and, for N-way specifically, already better-tested than WagerBrain's own public examples demonstrate. The one concrete, actionable output is the missing arbitrage-market test case (Part 2, test 6.1) — a small, in-repo addition, not an adoption. |
| **License** | **MIT** (per fetched repo metadata) — moot given no-adopt recommendation. |
| **Open verification items** | WagerBrain's actual source code (not just its README) was not fetched in this pass (per the task's instruction to research public README/documentation content only, not clone code) — if a future contributor wants to confirm definitively whether WagerBrain's vig/arbitrage functions support N>3-way markets in the actual implementation (rather than just absent from the documented examples), that would require reading its source directly, which this audit did not do. |

### 7. OddsHarvester

| Field | Content |
|---|---|
| **Scope (as specified)** | Sport/market taxonomy and snapshot schema only. **Explicit exclusions honored: this entry does not adopt any proxy/anti-blocking/scraping feature; does not treat OddsPortal as an approved data source (any actual use of OddsPortal would need its own separate access/terms review, out of scope here); and does not solve authenticated Bet9ja capture — OddsHarvester's design says nothing about Bet9ja at all.** |
| **Key findings** | Confirmed via fetch: OddsHarvester supports 11 sports with per-sport market-token taxonomies (e.g. football: `1x2`, `btts`, `double_chance`, `over/under`; tennis: `match_winner`, `total_sets_over/under`), and ~100+ league identifiers in a `region-competition` style string (e.g. `england-premier-league`). It distinguishes three capture modes explicitly: `upcoming` (future fixtures by date/league), `historic` (completed-season odds+results), and `live` (single-moment in-play snapshots carrying `live_period`, `live_score_home`, `live_score_away`). Every capture records a `scraped_at_utc` timestamp (and a `scraped_at` field on community-sourced records). **Withdrawn-market handling, confirmed directly and directly relevant to this repo's own gap (see below):** when a bookmaker stops offering a specific outcome, OddsPortal (the underlying source OddsHarvester scrapes) marks it with strikethrough formatting, and OddsHarvester captures this as a `blocked_outcomes` field listing which outcomes were struck through — present only when blocking actually occurs. Separately, a bookmaker simply not quoting a price at all (no market offered) renders as `"-"` and is explicitly **not** flagged as blocked — OddsHarvester deliberately distinguishes "withdrawn/blocked after being offered" from "never offered/empty." |
| **Comparison against this repo — the concrete finding the task asked for** | **This repo currently has no concept of a withdrawn/void market status anywhere in the pricing engine or the feature manifest — confirmed directly by grep.** Searching `src/pcbf_calculator` and `docs` for `withdrawn`/`void` (case-insensitive) returns matches only in `registries/yamlmini.py` (a YAML-parser detail, not a market-status concept) and the Soccer 1X2 spec's own settlement-integrity language, which uses the word "void" only in the sense of an entire *fixture* being void/abandoned pre-settlement (section 1: "A postponed, abandoned..., or void fixture is excluded from both training and live inference") — this is about excluding a whole match from the dataset, not about one *outcome within an active market* being withdrawn mid-market the way OddsHarvester's `blocked_outcomes` captures. `pricing/engine.py::analyze_market` has no input field, output field, or validation branch for "this specific outcome's price was withdrawn after being offered" as distinct from "this outcome was never in the `market_prices` dict at all" (which today just triggers `INCOMPLETE_OPPOSING_PRICES` if it drops the market below two outcomes, with no distinction between "never offered" and "offered then pulled"). `market_snapshot_odds_1x2`'s feature-manifest row (`docs/adapters/data/soccer_1x2_feature_manifest.yaml`) likewise carries `captured_at`/`scheduled_kickoff`/`hours_to_kickoff` but no withdrawn/blocked-status field. |
| **Where OddsHarvester's schema already matches this repo's direction** | The `upcoming`/`historic`/`live` capture-mode distinction maps closely onto this repo's own separation of concerns: `market_snapshot_odds_1x2` (a pre-kickoff snapshot at the model's decision horizon) is directly analogous to OddsHarvester's `upcoming` mode's odds capture, and `market_closing_odds_1x2_for_clv_analysis` (captured strictly after the forecast-generating snapshot, evaluation-only) is a narrower, single-purpose version of what OddsHarvester's `historic` mode would capture for a settled fixture. This repo has no `live`-mode analogue at all (pre-match only, per the Soccer 1X2 spec's section 1 scope), which is a deliberate scope boundary, not a gap. Per-snapshot UTC timestamping (`scraped_at_utc`) is exactly the discipline this repo's own `market_snapshot_odds_1x2.captured_at` and `market_closing_odds_1x2_for_clv_analysis` fields already require (section 2/5's decision-horizon-matching leakage control depends entirely on precise capture timestamps). |
| **Build vs. adopt** | **Do not adopt OddsHarvester's scraper, its proxy/anti-blocking code, or OddsPortal as a data source** (all explicitly excluded by the task's scope). **Do borrow the taxonomy idea, not the code**: a future revision of this repo's market-snapshot schema (a Release B+ decision, not proposed as a change in this PR) should consider adding an explicit withdrawn/blocked-outcome status field, distinguishable from "never offered," to `market_snapshot_odds_1x2` and to `pricing/engine.py`'s outcome validation — this is a real, currently-absent taxonomy detail worth having, flagged here as a finding, not implemented here. |
| **License** | **MIT** (per fetched repo metadata) — moot for the excluded scraper/proxy code; relevant only if a future PR wanted to borrow the taxonomy naming convention as inspiration (naming conventions are not copyrightable in a way license terms would block anyway, but noted for completeness). |
| **Open verification items** | This repo's own `market-types-registry.yaml` does not currently enumerate a canonical machine-readable market-code taxonomy as granular as OddsHarvester's (it lists market *categories* like `1x2`, `double_chance`, `over_under`, `btts`, `correct_score` per sport, which already loosely matches OddsHarvester's football taxonomy) — a side-by-side full taxonomy diff (every OddsHarvester market token against every `market-types-registry.yaml` `markets` entry) was not performed in this pass and would be a reasonable, small follow-up if a future PR wants to formalize the sport/market taxonomy further; not attempted here since it is not one of the eight required outputs. |

### 8. BuzzFeedNews tennis betting analysis

| Field | Content |
|---|---|
| **Scope (as specified)** | Odds-movement and anomaly-testing methodology only, for future tennis match-winner and general data-quality anomaly flags. **Explicitly separate from match-outcome forecasting — honored here: nothing in this entry proposes using this methodology to predict a match result, only to flag suspicious/anomalous data patterns.** |
| **Key findings** | Confirmed via fetch: the analysis used a **one-row-per-bookmaker-observation-per-match convention** across 26,000+ ATP/Grand Slam matches (2009-2015), with opening and closing odds collected separately from seven independent bookmakers via OddsPortal and converted to implied probability via `opponent_odds / (opponent_odds + player_odds)`. **Multi-bookmaker consensus / extreme-outlier removal**: opening odds implying a probability more than 10 percentage points away from the *median* of all bookmakers' opening-odds-implied probabilities for that match were excluded (~500 matches removed) before any anomaly analysis — a concrete, reusable outlier-rejection rule. **Cancellation/walkover exclusion**: cancelled and walkover matches were dropped entirely before analysis, matching this repo's own settlement-integrity convention (Soccer 1X2 spec section 1: postponed/abandoned/void fixtures excluded from both training and inference, never imputed) — the same "ambiguous settlement means excluded, not guessed" principle, independently arrived at by two different projects. **Simulation-based anomaly testing**: for each flagged player (39 players who lost 10+ matches with >=10-point odds movement), 1,000,000 simulation iterations estimated the probability of that many high-odds-movement losses occurring by chance given the opening-odds-implied win probabilities. **Multiple-testing correction**: a **Bonferroni correction** was applied across the 39-player test set specifically because testing many players individually inflates the chance of a false positive by chance alone — 4 players cleared a 95%-confidence threshold after correction. **Explicit caveat, directly quoted**: *"Betting patterns alone aren't proof of fixing"* — the authors state plainly that without independent corroborating evidence (account data, communications), a statistical anomaly cannot itself establish misconduct. |
| **Comparison against this repo** | This repo's `market_closing_odds_1x2_for_clv_analysis` field (Soccer 1X2 spec section 2, evaluation-only, never a model input, captured strictly after the forecast-generating snapshot) is analogous in *purpose* to BuzzFeed's opening/closing-odds-separately-stored convention — both keep an "early" and a "late" price as two distinct, never-conflated fields for exactly the reason each project needs them (this repo: closing-line-value analysis without training leakage; BuzzFeed: measuring how far the market moved between open and close as the anomaly signal itself). The Soccer 1X2 spec's leakage-control table (section 7) is entirely about *training-data* leakage (post-match stats, look-ahead bias, decision-horizon mismatch) — it has **no equivalent concept of a data-quality anomaly flag** for suspicious market behavior (e.g. one outcome's odds moving unusually far relative to bookmaker consensus, in a way that might indicate a data-capture error, a stale/erroneous odds feed, or — separately and far more speculatively — an actual market irregularity). This repo currently has no outlier-rejection rule at all for its own multi-bookmaker odds columns (Football-Data's files carry several bookmakers per fixture; `validation.py`'s docstring already notes it never treats Pinnacle as automatically authoritative over other bookmakers, but it does not implement a BuzzFeed-style median-consensus outlier filter). |
| **Build vs. adopt** | **Do not adopt any code** (none was proposed to be fetched or copied). **Flag a future, non-current item**: this repo could eventually add a data-quality anomaly flag — e.g. a per-fixture, per-bookmaker-column check that an odds value implies a probability more than N percentage points from the cross-bookmaker median at the same capture timestamp — as a **data-quality signal** on `market_snapshot_odds_1x2`/`market_closing_odds_1x2_for_clv_analysis` ingestion (a `data_pipeline/validation.py`-shaped check, structurally similar to its existing typed-rejection-report pattern), explicitly **not** as a match-outcome feature and explicitly **not** as a fraud-detection claim (per BuzzFeed's own caveat, quoted above, which this repo should carry forward verbatim if it ever builds this: an odds-movement anomaly flag is a data-quality/research signal, never proof of misconduct on its own). This is recorded here as a **future consideration**, not proposed as a change in this PR. |
| **License** | Not directly confirmed by this pass's fetch (the fetched content was the methodology writeup, not the repository's license file) — **unverifiable in this pass**; moot regardless since no code adoption is proposed, only a methodology idea. |
| **Open verification items** | The repository's actual license file was not checked (irrelevant to the no-adopt recommendation, but should be confirmed before anyone considers reusing its analysis code directly, which this audit does not recommend). The exact simulation methodology's statistical assumptions (what null model exactly was simulated under) were only summarized by the fetch, not independently re-derived — sufficient for this audit's "note the technique exists" purpose, not sufficient to reimplement without reading the original methodology write-up directly. |

---

## Part 2 — Comparison-test plan (plan only — not implemented in this PR)

Per the operator's explicit instruction, the following are **specifications
for tests to add in a future PR**, not implementations. Each names the
target file, a test name, and exactly what it should assert.

### 6.1 — WagerBrain-motivated: genuine arbitrage (negative-margin) market

**File**: `tests/test_pricing_engine.py`
**Why**: WagerBrain names "arbitrage opportunity evaluation" as a first-class
feature. This repo's `pricing/engine.py::_market_quality` already has a
typed `ANOMALOUS_NEGATIVE_MARGIN` `evidence_quality` value for exactly this
situation, but no existing test ever constructs a market whose true
bookmaker margin is negative (`sum(1/price) < 1` — an actual, if
unrealistic, arbitrage-shaped price set) and asserts that code path fires.
The existing `test_market_quality_fields_present_and_computed` only asserts
`evidence_quality` is *one of* the three possible values on an ordinary
positive-margin market — it never exercises the negative-margin branch at
all.

**Proposed test**: `test_negative_margin_market_flagged_anomalous`
- Construct a market with `sum(1/price) < 1`, e.g.
  `{"home": 2.5, "away": 2.5}` gives `1/2.5 + 1/2.5 = 0.8`, margin `-0.2`
  (real arbitrage-shaped prices, unlikely in practice but a valid input the
  engine must handle correctly, not reject).
- Assert `result["bookmaker_margin"] < 0`.
- Assert `result["market_quality"]["evidence_quality"] ==
  "ANOMALOUS_NEGATIVE_MARGIN"`.
- Assert fair probabilities still sum to 1.0 and each still lies in
  `[0, 1]` (the de-vig math itself does not break on a negative-margin
  input — only the market-quality label changes).
- Assert every outcome's `point_ev` is now **positive** (a negative-margin
  market implies positive expected value against every outcome at the
  offered prices under proportional de-vig — the mathematically correct,
  if unusual, consequence of a true arbitrage-shaped market) — this is the
  concrete behavioral claim WagerBrain's arbitrage-evaluation feature is
  meant to detect, expressed here as an assertion on this repo's own
  already-implemented math rather than as new code.

### 6.2 — WagerBrain-motivated: near-zero-but-not-exactly-zero margin boundary

**File**: `tests/test_pricing_engine.py`
**Why**: the existing `test_zero_margin_arbitrage_free_edge_case` only
covers the exact-zero-margin case (`2.0`/`2.0`). WagerBrain's vig
calculator is presented as a general-purpose function expected to handle
the full range from "no vig" to "heavy vig" smoothly; this repo's own two
existing tests (`test_zero_margin_arbitrage_free_edge_case`,
`test_heavily_vigged_market`) cover the two extremes but nothing in
between, and nothing confirms `evidence_quality` transitions correctly
right at its `NORMAL` / `LOW_EVIDENCE_HIGH_MARGIN` boundary (`margin > 0.5`
in `_market_quality`).

**Proposed test**: `test_market_quality_boundary_at_fifty_percent_margin`
- Construct one market whose margin is just below `0.5` and one just above
  it (e.g. by choosing prices that make `sum(1/price) - 1` land at
  `0.49` and `0.51`).
- Assert the first has `evidence_quality == "NORMAL"` and the second has
  `evidence_quality == "LOW_EVIDENCE_HIGH_MARGIN"` — pinning the exact
  boundary behavior (`margin > 0.5`, not `>=`) so a future refactor of
  `_market_quality` cannot silently shift it without a test failing.

### 7.1 — OddsHarvester-motivated: withdrawn/blocked outcome status (future feature, plan only)

**File**: a new test module, e.g. `tests/test_pricing_engine.py` (if the
withdrawn-status concept is added directly to `analyze_market`'s input
validation) or a new adapter/feature-manifest test file (if it is instead
added at the feature-manifest/ingestion layer) — **which file is correct
depends on where a future PR actually implements the concept, which this
audit does not decide.**
**Why**: OddsHarvester's `blocked_outcomes` field distinguishes "this
outcome was offered, then withdrawn" from "this outcome was never offered
at all." This repo's pricing engine currently only has one failure mode for
a missing outcome (`INCOMPLETE_OPPOSING_PRICES` if it drops the market
below two priced outcomes) with no way to represent "three outcomes exist
in this market but one was withdrawn mid-market," which is a real market
state a live odds feed can produce.

**Proposed test(s)** (once the feature exists — not written against
today's code, since today's code has no such field to test):
- `test_withdrawn_outcome_excluded_from_pricing_but_recorded` — a market
  input carrying an explicit withdrawn/blocked marker for one outcome
  should exclude that outcome from the de-vig calculation (repricing the
  remaining outcomes as a smaller N-way market) while still recording that
  the outcome existed and was withdrawn, distinct from it simply never
  having appeared in the input at all.
- `test_never_offered_outcome_distinct_from_withdrawn_outcome` — asserts
  the two states produce distinguishable output (e.g. a
  `withdrawn_outcomes` list vs. an outcome simply absent from
  `market_prices` and `outcomes` both), matching OddsHarvester's
  `blocked_outcomes` vs. `"-"` distinction.

This is recorded as a **plan for a future feature**, not a gap in existing
code the way 6.1/6.2 are — there is no existing withdrawn-outcome concept
to test today, so this entry exists to make the eventual test plan already
legible once/if the feature is built, per the task's request to note this
comparison explicitly.

### 8.1 — BuzzFeed-motivated: cross-bookmaker odds-consensus outlier flag (future feature, plan only)

**File**: `tests/test_validation.py` (new, or extend an existing
`data_pipeline` validation test file — the current `data_pipeline/`
directory has `validation.py` but this audit did not locate a matching
`tests/test_validation.py`; confirm the actual existing test file name
before adding, since this audit did not exhaustively enumerate every
`data_pipeline`-adjacent test file).
**Why**: BuzzFeed's median-consensus outlier rule (exclude/flag any single
bookmaker's odds implying a probability more than 10 percentage points from
the cross-bookmaker median) is a directly reusable data-quality check this
repo's own multi-bookmaker Football-Data ingestion does not yet implement.

**Proposed test(s)** (once the feature exists):
- `test_bookmaker_odds_outlier_flagged_against_median_consensus` — given a
  fixture's row with several bookmaker odds columns where one bookmaker's
  implied probability differs from the cross-bookmaker median by more than
  a declared threshold, assert the validation layer flags that specific
  bookmaker column (a typed reason code, per this repo's existing
  typed-rejection-report convention in `validation.py`) without rejecting
  the whole fixture — an outlier bookmaker column is a data-quality flag on
  that column, not proof the fixture itself is invalid.
- Any such threshold, if implemented, should be treated as a
  policy-affecting numeric constant subject to this repo's existing
  ledgering convention (`docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`'s
  `PROPOSED_OPERATOR_DECISION` / `DISABLED` pattern) rather than hardcoded
  and silently enforced — consistent with how every other numeric gate in
  the Soccer 1X2 spec is handled.

This, like 7.1, is a plan for a future feature, explicitly separate from
match-outcome forecasting (per the task's scope note for this reference)
and explicitly not a fraud-detection claim — any future implementation
should carry forward BuzzFeed's own caveat ("betting patterns alone aren't
proof of fixing") in its own documentation if it is ever built.

### Other references checked for test gaps, with no actionable gap found

- **Penaltyblog**: no concrete, actionable pricing-engine test gap — its
  overlap with this repo is at the *modelling* layer (Release B+ candidate
  models), not the pricing-math layer `tests/test_pricing_engine.py`
  covers; any future test gap here would be about Release B's forecasting
  adapter tests, not this PR's pricing-engine test plan.
- **AlphaPy**: no test gap — scope was a documentation/config-schema
  comparison, not a code-behavior comparison.
- **sports-betting**: no test gap in `pricing/engine.py` specifically; the
  one structural note (item 3's `data_sources` field shape) is a possible
  future adapter-interface refinement, not a testable gap in current code.
- **BeatTheBookie**: no test gap — its methodology is informal and
  explicitly not being reproduced or relied upon; there is nothing concrete
  enough to turn into a test assertion without independently reproducing
  claims this audit was told not to inherit.
- **ProphitBet**: no pricing-engine test gap; its comparison surfaced a
  real repository defect (the broken gitlink, Part 1 item 5) rather than a
  test-coverage gap in this repo's own code.

---

## Summary table

| # | Reference | Recommendation | License | Compiled/host-risk flag |
|---|---|---|---|---|
| 1 | Penaltyblog | Reference/benchmark only, never a dependency | MIT | **Yes — confirmed Cython/`.so` extension; host-compatibility unverified, do not assume pass** |
| 2 | AlphaPy | No adoption; define own minimal manifest schema | Apache-2.0 | N/A |
| 3 | sports-betting | No adoption; note possible future `data_sources` shape refinement | MIT | N/A |
| 4 | BeatTheBookie | No adoption; do not inherit profitability claims | GPL-3.0 | N/A |
| 5 | ProphitBet | No adoption; **separately, a real broken-gitlink defect found and reported** | MIT | N/A |
| 6 | WagerBrain | No adoption; concrete pricing-engine test gaps identified (6.1, 6.2) | MIT | N/A |
| 7 | OddsHarvester | No adoption of scraper; taxonomy idea (withdrawn-outcome status) flagged as a real, currently-absent gap | MIT | N/A |
| 8 | BuzzFeed tennis analysis | No code adoption; methodology (outlier-consensus flag) flagged as a future, non-forecasting data-quality idea | Unverified | N/A |
