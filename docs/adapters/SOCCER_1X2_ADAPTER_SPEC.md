# Soccer 1X2 Forecasting Adapter — Design Specification

**Status: design only.** This document specifies the first admitted
forecasting adapter (`SportAdapter` subclass, per
`docs/MULTI_SPORT_ARCHITECTURE.md` layer 2) for pre-match Soccer 1X2. It is
the reference pattern later adapters (tennis, basketball, etc.) will reuse —
get the shape right here, since it is a template, not a one-off.

**Nothing in this PR trains a model, fetches real data, or changes runtime
behavior.** The registry status change (`soccer`'s `adapter_status` moves to
`DESIGN_IN_PROGRESS`) is a documentation/tracking flip only; the adapter
dispatch framework does not branch on `adapter_status`, only on whether a
concrete class is registered in
`adapters/registry.py::_ADAPTER_IMPLEMENTATIONS` (still empty for `soccer`
after this PR). A companion contract-shape stub,
`SoccerOneXTwoAdapter` (`src/pcbf_calculator/adapters/soccer_1x2_stub.py`),
proves the `SportAdapter` interface is implementable against this spec
without doing any real forecasting — see its module docstring.

A structured companion manifest,
`docs/adapters/data/soccer_1x2_feature_manifest.yaml`, mirrors sections 2-3
below in a machine-checkable form (validated by
`tests/test_soccer_1x2_feature_manifest.py`).

---

## 1. Prediction target and outcome ordering

**Target**: the full-time 1X2 result of one pre-match soccer fixture —
which of three mutually exclusive, exhaustive outcomes occurs at the end of
90 minutes plus referee-allowed stoppage time, **before** extra time or
penalties (matching `market-types-registry.yaml`'s existing soccer
`settlement`: "Full-time regulation result unless the market states extra
time or penalties count").

**Outcome ids and ordering**: `pricing/engine.py::analyze_market` takes
outcomes as a `dict[str, float]` and preserves **caller insertion order**
in its output (`OutcomePricing` list built by iterating `validated.items()`
in the order the caller supplied); it does not impose a canonical outcome
naming scheme itself — `tests/test_cli_integration.py`'s
`THREE_WAY_REQUEST` uses `{"home": ..., "draw": ..., "away": ...}` as the
convention. This adapter must:

- use the canonical outcome ids `home_win`, `draw`, `away_win` internally
  and in its `ForecastResult.probabilities` dict (never the market's own
  labels, e.g. team names, so that a forecast dict is directly diffable
  against another fixture's without cross-referencing team identity);
- emit them from `forecast()` in that fixed order — `home_win`, `draw`,
  `away_win` — matching the `1x2` convention (`1` = home win, `X` = draw,
  `2` = away win) already used as the market code in
  `market-types-registry.yaml`;
- **never** invent its own outcome labels for the market-prices side of a
  CLI request — the caller's `market_prices` keys (e.g. `home`/`draw`/
  `away`, or literal team names) are matched to the adapter's
  `home_win`/`draw`/`away_win` via the `fixture` object's explicit
  `home_team`/`away_team` fields, not by string-matching market label text.
  A future CLI wiring layer (Release B implementation, not this PR) is
  responsible for that join; this spec only fixes the adapter's own
  internal vocabulary.

**Settlement**: an outcome is only ever labeled from the fixture's official
full-time result as published by the same structured results feed used for
training features (bucket 1, section 18). A postponed, abandoned (before
90 minutes are validly completed), or void fixture is **excluded from both
training and live inference** — it never gets a synthetic or imputed
result. This is a straightforward extension of the "no silently substituted
default" convention (`docs/MULTI_SPORT_ARCHITECTURE.md`, typed failure
catalogue) to dataset construction: an ambiguous settlement is dropped, not
guessed.

---

## 2. Required and optional features

The full list with definitions lives in
`docs/adapters/data/soccer_1x2_feature_manifest.yaml` (14 rows, each with
`id`, `required`, `definition`, `source`, `availability_relative_to_kickoff`,
and an optional `leakage_note`). Summary:

**Correction (post-original-spec)**: the original spec's required feature
list included `market_closing_odds_1x2` (the closing market line) as a
pre-match model input. That is not actually observable at a real pre-match
decision horizon — a forecast produced hours before kickoff cannot see a
price that does not exist until immediately before kickoff. It has been
replaced by `market_snapshot_odds_1x2` (captured at whatever moment the
forecast is actually generated) below, plus a new optional,
post-forecast-generation-only evaluation field,
`market_closing_odds_1x2_for_clv_analysis`, for closing-line-value analysis
only (see section 7's dedicated leakage-control row on this).

**Required** (9 features — a fixture missing any one of these fails closed,
section 4):

| id | Measures |
|---|---|
| `home_team_elo_pre_match` | Home team's Elo-style rating from matches strictly before kickoff. |
| `away_team_elo_pre_match` | Away team's Elo-style rating, same computation. |
| `home_team_rolling_goals_for_last_10` | Home team's mean goals scored over its last 10 completed matches. |
| `home_team_rolling_goals_against_last_10` | Home team's mean goals conceded over its last 10 completed matches. |
| `away_team_rolling_goals_for_last_10` | Away team's mean goals scored over its last 10 completed matches. |
| `away_team_rolling_goals_against_last_10` | Away team's mean goals conceded over its last 10 completed matches. |
| `market_snapshot_odds_1x2` | This repo's own de-vigged fair 1X2 probabilities from the market's odds at the model's actual decision time (never the closing line), used as a model **input** (a strong prior), never as the training **label**. Carries mandatory `captured_at`/`scheduled_kickoff` timestamps and a derived `hours_to_kickoff`. |
| `days_since_last_match_home` | Days since the home team's previous completed match (fatigue/rotation proxy). |
| `days_since_last_match_away` | Days since the away team's previous completed match. |

**Optional** (features that improve the model if available, but whose
absence must never block a forecast on their own — only a missing
*required* feature is a hard fail):

| id | Measures | Caveat |
|---|---|---|
| `head_to_head_last_5_result_distribution` | Outcome distribution of the last 5 meetings between these two teams. | Small-sample (n<5 for most pairs); document as high-variance. |
| `confirmed_starting_lineup_strength_delta` | Lineup-strength difference between confirmed starting XIs. | Only available T-45m or later, and only once lineups are officially confirmed; missing for many fixtures. **Operator decision (resolved)**: this pipeline is deferred, not built, in the first model version — see "Unresolved / operator decisions" below. |
| `weather_conditions_pre_match` | Forecast (not observed) conditions at kickoff. | No wired source today; see bucket 3, section 19. **Operator decision (resolved)**: excluded from the first model — see "Unresolved / operator decisions" below. |
| `market_closing_odds_1x2_for_clv_analysis` | The closing market line's de-vigged fair 1X2 probabilities, captured strictly after `market_snapshot_odds_1x2` and the forecast it fed. | **Evaluation-only, post-forecast-generation.** Used only to compute closing-line value (CLV); must never be fed into the model as an input feature (section 7). |

**Explicitly rejected candidate** (documented, not implemented): a fixture's
own `post_match_actual_goals_scored` is the target variable's own outcome,
not a usable pre-match feature — see the leakage discussion in section 7.
Kept in the manifest as a labeled rejection so a future contributor does not
propose it again without reading why.

---

## 3. Source and availability timestamp for every feature

See the manifest's `source` and `availability_relative_to_kickoff` fields
per feature (section 2 table links each id to its row). The two
availability-related problems called out explicitly:

- **`confirmed_starting_lineup_strength_delta`** is only ever known once
  official lineups are published, typically 45-75 minutes before kickoff,
  and for many leagues/fixtures it is never published on a schedule a
  machine-readable pipeline can rely on at all. It cannot be a required
  feature for exactly this reason — treating it as required would silently
  restrict the adapter to only the subset of fixtures where confirmed
  lineups happen to be captured in time, which is a selection bias, not a
  neutral missing-data policy.
- **`post_match_actual_goals_scored`** (rejected, section 2) is available
  only *after* the fixture it would describe has been played. A candidate
  feature that is only known post-match is definitionally useless for
  pre-match prediction of that same fixture — it is called out here, not
  quietly omitted, because it is the single most common way a soccer
  prediction pipeline accidentally leaks its own label (see section 7).

**Feature-freeze time**: every required feature must be computed as of a
single declared cutoff — `T-1h` (one hour before kickoff) is this spec's
proposed default for both training and live inference — with
`market_snapshot_odds_1x2` as the one deliberate exception: its value is
fixed at whatever moment the forecast is actually generated (its own
`captured_at`), which may be earlier than `T-1h` for a production model
running at a longer decision horizon (e.g. `T-6h`); it is never the closing
line (`T-0`). Every adapter that follows this template must declare its
own single freeze time this explicitly rather than leaving it implicit
per-feature. **Classification note (Correction 3)**: `T-1h` as the exact
freeze-time value is a feature-definition constant the implementation PR
will select/tune during validation (see the "feature-definition constants"
note in section 5), not a policy gate requiring pre-approval — unlike the
freeze-time *convention itself* (one single declared cutoff for all
required features, `market_snapshot_odds_1x2` excepted), which is settled
design intent, not a number pending review.

---

## 4. Missing-data and stale-data failures

Fail closed, never impute silently — matching this repo's existing typed
failure convention (`errors.py`: a `{"code", "field", "reason"}` object,
never a bare exception, never a silently substituted default). Two new
typed codes are specified for the future Release B implementation (not
added to `errors.py` in this PR, since no code raises them yet — the stub
adapter fails closed unconditionally instead, see its docstring):

| Proposed code | Layer | Meaning |
|---|---|---|
| `REQUIRED_FEATURE_MISSING` | 2 (soccer 1X2 adapter) | A required feature (section 2) is absent, `null`, or not the declared type for this fixture. The adapter returns `ForecastResult(forecast_available=False, ...)` with this reason — it never substitutes a league-average, a zero, or any other imputed value for a required feature. |
| `FEATURE_DATA_STALE` | 2 (soccer 1X2 adapter) | A required feature's own source timestamp is older than that feature's declared freeze-time tolerance *(number ledgered, proposed/unapproved — see below)*: e.g. an Elo rating computed from a match-results feed that has not been refreshed in more than 48 hours ahead of a same-day kickoff. Distinct from `STOP_STALE_DATA` (layer 3, which governs the caller-supplied `freshness` block for the whole decision, not a single adapter feature) — this is a layer-2, adapter-internal staleness check that runs *before* the adapter attempts to produce a forecast at all. |

**Numeric-threshold classification (Correction 3)**: the "48 hours" figure
above is a policy-affecting gate-style number (it decides whether a
feature fails closed) — it is recorded as `feature_staleness_tolerance_hours`
in `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml` with
`status: PROPOSED_OPERATOR_DECISION` / `enforcement: DISABLED`, same as
every other candidate threshold in this spec. `FEATURE_DATA_STALE` itself
is not wired into `errors.py` in this PR (see above) — this number becomes
enforceable only once both that implementation lands and this ledger row
is explicitly approved.

Both failures are **soft** at the CLI level, exactly like today's
`NOT_IMPLEMENTED`: pricing (layer 1) still runs and is returned, and the
decision layer (layer 3) still caps the ceiling at `RESEARCH-MODEL` via
`forecast.forecast_available: false` — a missing-feature failure inside the
adapter must never become a CLI-level `FAILED` status, because the market
pricing analysis is still valid and useful even when the forecast is not.
This mirrors `NOT_IMPLEMENTED`'s existing "soft" classification in
`errors.py` exactly. (Platform-wide policy: the no-forecast ceiling is
`RESEARCH-MODEL`, not `PAPER` — see
`docs/MULTI_SPORT_ARCHITECTURE.md` decision 3.)

**Partial-optional-feature policy**: an optional feature being missing
degrades gracefully — implementation detail left to Release B (e.g. a
model trained to accept a missing-indicator flag for optional features
rather than a hard fail), but it must never silently substitute a
plausible-looking default either; whatever substitution scheme Release B
picks must be declared in its own model card, not invented ad hoc at
inference time.

---

## 5. Training dataset requirements

Minimum viable dataset for this to be defensible at all:

- **Coverage** *(numbers ledgered — see below)*: at least 3 full completed
  seasons of one or more leagues covered by the mandatory machine-readable
  input (bucket 1, section 18), with every required feature (section 2)
  computable for at least 95% of fixtures in that coverage window (the
  remaining <=5% may be dropped, not imputed, per section 4).
  **Operator decision (resolved)**: the historical period is not a fixed
  season count decided up front — acquire every consistently formatted
  completed season available for the five approved leagues (see
  "Unresolved / operator decisions" below), then determine the usable
  period after validation. The "3 seasons" figure below is therefore a
  **floor/target to validate against**, not a hard acquisition ceiling or a
  stopping point.
- **Size** *(number ledgered — see below)*: at least 3,000 completed,
  non-void fixtures after the drop rule above. This is a floor, not a
  target — a single mid-size European league plays roughly 380 top-flight
  fixtures/season, so 3,000 fixtures implies either several seasons of one
  league or several leagues combined; either is acceptable as long as the
  chronological-split rule (section 6) is applied per-league-or-globally
  consistently, never mixed ad hoc.
- **Decision-horizon matching for `market_snapshot_odds_1x2` (Correction 1,
  a real, specific leakage vector)**: training observations must be matched
  to comparable decision horizons — if the production model will generate
  forecasts at, say, `T-6h`, training data must use odds snapshots captured
  at a comparable `hours_to_kickoff`, never closing-line snapshots, or the
  model is trained on information it will not have at inference time. This
  is a silent horizon mismatch, not merely a stylistic inconsistency — see
  the dedicated leakage-control row in section 7 and the mandatory
  `captured_at`/`scheduled_kickoff`/`hours_to_kickoff` fields on
  `market_snapshot_odds_1x2` in the feature manifest.
- **Class balance check, not requirement**: soccer 1X2 outcomes are not
  balanced (home win is typically the modal outcome around 45%, draw the
  least common around 25%). The dataset must **report** its class
  distribution, but must not be artificially rebalanced (oversampling/
  undersampling) in a way that would distort the true base rates a
  probability forecast is supposed to reflect — a rebalanced training set
  produces a mis-calibrated model that then requires an extra, easy-to-get-
  wrong recalibration step to correct back to true base rates.
- **Multi-league caution**: if fixtures are pooled across leagues to reach
  the 3,000-fixture floor, `league_id` (or an equivalent identifier) must
  be retained as a feature or stratification key — league-level differences
  in average goals/home-advantage are real signal, not noise, and pooling
  them away would blur the model's calibration across leagues with
  genuinely different base rates.

**Numeric-threshold classification (Correction 3)**: "3 seasons," "3,000
fixtures," and "95% feature coverage" above are all **policy-affecting,
gate-style numbers** (they gate whether a dataset is even defensible enough
to proceed to modeling) — each has a corresponding row
(`dataset_minimum_seasons`, `dataset_minimum_fixture_count`,
`dataset_minimum_feature_coverage_rate`) in
`docs/adapters/data/soccer_1x2_promotion_thresholds.yaml` with
`status: PROPOSED_OPERATOR_DECISION` / `enforcement: DISABLED`, distinct
from the already-ledgered 98% *prospective* (live, shadow-mode)
feature-availability rate (section 15) — these are two different numbers
about two different pipelines (offline training-data coverage vs. live
shadow-mode availability), never to be conflated or double-ledgered under
one id. By contrast, the "last 10 matches" rolling-window size (section 2's
feature definitions) is classified as a **feature-definition constant to be
selected during validation**, not a policy gate: it is a modeling
hyperparameter (window length trades off recency-sensitivity against
sample noise) the implementation PR will legitimately tune empirically
against validation data, not a go/no-go threshold an operator needs to
pre-approve before any dataset work can start — changing it later does not
retroactively invalidate a decision the way changing "3,000 fixtures" would.

---

## 6. Temporal train/validation/test splits

**Chronological only — never randomly shuffled.** A time-ordered sports
dataset shuffled randomly leaks future information into training (a match
from November training on a rolling-average feature that itself was
computed using data from a December match the model is later evaluated
against, if the split does not respect chronology at the fixture level).

Exact methodology:

1. Sort the full deduplicated fixture set by kickoff datetime, ascending.
2. Split by **date boundary**, not by row count or shuffle: e.g. train on
   seasons 1-N-2, validate on season N-1, test on season N (a strict
   walk-forward split by season is the simplest version that avoids
   subtler within-season leakage; an implementation may use rolling-window
   walk-forward folds instead, but every fold's validation/test window must
   be entirely later in time than every fixture used to train that fold).
3. **No fixture's own features may be computed using any data dated at or
   after that fixture's own kickoff** (this is the leakage control from
   section 7, restated as a split-time invariant, not just a training-time
   one — the same check applies when features are recomputed for the
   validation/test folds).
4. The test set is used exactly once, at the end, to report the acceptance-
   gate metrics (section 14). Re-running the test set repeatedly while
   tuning hyperparameters turns it into a second validation set and
   invalidates the gate — if that happens, a fresh, later, previously
   unseen date range must be carved out as the real test set before
   promotion.
5. **Decision-horizon matching applies to the split too** (Correction 1):
   `market_snapshot_odds_1x2` must be re-derived per fold at the production
   model's declared decision horizon, not backfilled from whatever
   closing-line data happens to be sitting in a historical table — a
   validation/test fold built from closing odds while the production model
   will run at `T-6h` would silently overstate how well-calibrated the
   model actually is at real inference time.
6. **Statistical approval process (operator decision, resolved)**: select
   thresholds (section 14's backtest gates, section 15's prospective gates)
   using training/validation evidence only, and **freeze** them (flip the
   relevant `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml` row's
   `status` to `APPROVED`) *before* opening the held-out test set. The test
   set must never be touched, peeked at, or iterated against while
   thresholds are still being chosen — it opens **exactly once**, after
   freezing, to produce the actual go/no-go result (see section 14's
   restatement of this same rule as a backtest-acceptance requirement).

---

## 7. Leakage controls

| Leakage vector | Concrete control |
|---|---|
| Using post-match stats as pre-match features (e.g. actual goals scored, final possession%, post-match xG for *this* fixture) | Every feature's `availability_relative_to_kickoff` (manifest, section 3) must name a timestamp strictly before kickoff; a feature whose only source is the match report of the fixture being predicted is rejected outright (see `post_match_actual_goals_scored` in the manifest) — enforced by a manifest-shape test (`tests/test_soccer_1x2_feature_manifest.py`) that every *required* feature's availability string does not contain `POST_MATCH`. |
| Using data not actually available at the declared availability timestamp | Feature computation at training time must replay history strictly as of each fixture's own feature-freeze time (`T-1h`, section 3) — not "as of today, filtered by date" over a table that has since been corrected/backfilled with information not available at the time (e.g. a match result correction issued days later). Training pipelines must snapshot or timestamp their source tables, not just filter live tables by date. |
| Team/player name leakage into unintended features | The model must never take a raw team-name string as a feature (only derived numeric features like Elo/rolling stats); this prevents the model from memorizing "Team X always wins" as a lookup table disconnected from the actual football signal, which would not generalize to promoted/relegated teams or name changes and would silently break down exactly when it matters most (a team's first match in a new context). |
| Look-ahead bias in rolling averages | Every rolling-window feature (`*_rolling_goals_*_last_10`) is defined over the team's last 10 **completed matches strictly before** this fixture (manifest `definition` field states this explicitly per row) — an off-by-one window boundary that includes the current fixture, or a window computed from a table that has not excluded not-yet-played fixtures, is the single most common rolling-feature leak and must be unit-tested at implementation time against a known fixture list. |
| Market-derived leakage (odds circularity) | `market_snapshot_odds_1x2` is explicitly a **model input**, never the training **label** — the label is always the actual full-time result (section 1). Using the market's own de-vigged probability as a stand-in target (e.g. training the model to reproduce the market rather than the real outcome) would make "independent EV" (section 13) meaningless by construction, since the forecast would just be a noisy copy of the market it is supposed to be compared against. |
| **Decision-horizon mismatch (Correction 1 — a real, specific leakage vector, not merely hypothetical)** | Training on `market_snapshot_odds_1x2` snapshots captured at the closing line while the production model actually generates forecasts at an earlier decision horizon (e.g. `T-6h`) is a **silent horizon mismatch**: the model is trained on market information (the fully-formed closing price) it will never have at real inference time, which inflates apparent backtest performance in a way that does not survive contact with production. Control: every training observation's `market_snapshot_odds_1x2.captured_at` must correspond to a `hours_to_kickoff` comparable to the production model's declared decision horizon — never backfilled from the closing line "because that's what was in the historical table." `market_closing_odds_1x2_for_clv_analysis` exists specifically so a *different*, explicitly post-forecast-generation field carries the closing line for evaluation — it must never be substituted into `market_snapshot_odds_1x2`'s slot at training time. |
| Season-boundary contamination in chronological splits | The split (section 6) must fall exactly at a season boundary or an explicit date cutover with no fixture appearing in both a training and a validation/test fold, even when leagues have different season calendars (e.g. a winter-break league vs. a summer-break league) — a naive global date cutoff can otherwise put one league's season 10 fully in training while another's season 10 straddles the boundary. |
| Future roster/transfer knowledge leaking into historical Elo/form features | Elo and rolling-form features must be computed using only the match results available up to the feature-freeze time — never adjusted retroactively using knowledge of a subsequent transfer, injury, or squad change that had not happened yet as of that fixture's kickoff. |

---

## 8. Baseline models

At least one trivial baseline any real candidate model must beat to justify
existing:

1. **Market-implied baseline (mandatory)**: always predict this platform's
   own de-vigged fair probability (`pricing/engine.py::analyze_market`'s
   `fair_probability`, computed from the closing market price) as the
   forecast, verbatim. This is the honest floor — a soccer 1X2 candidate
   model that cannot beat the market it is trying to independently price
   against has no basis for existing as a separate forecasting adapter (its
   entire purpose per `docs/MULTI_SPORT_ARCHITECTURE.md` decision 3 is to
   supply an *independent* signal the market-only pricing engine cannot).
2. **Static base-rate baseline (secondary, sanity check only)**: always
   predict the training set's overall {home_win, draw, away_win} frequency,
   ignoring the specific fixture entirely. A real candidate beating this
   trivially (any model with team-level information should) is a pipeline
   sanity check, not evidence of real skill — it exists to catch bugs (e.g.
   a broken feature pipeline silently degenerating into "always predict the
   average"), not to set the promotion bar.
3. **Simple Poisson baseline (secondary, a real football baseline)**: an
   independent-Poisson goals model using only `*_rolling_goals_*_last_10`
   (no Elo, no market input) as a second, simpler-than-final-candidate
   reference point — useful for isolating how much of a candidate's edge
   (if any) comes from the market-input feature versus genuine goal-scoring
   modeling.

The **market-implied baseline is the actual promotion bar** (section 14);
the other two are diagnostic.

---

## 9. Candidate modelling methods

| Method | Sketch |
|---|---|
| Independent Poisson / Dixon-Coles goals model | Model home and away goals as (correlated, in Dixon-Coles) Poisson processes parameterized by team attack/defense strengths; derive 1X2 probabilities from the resulting scoreline distribution. |
| Elo-based logistic mapping | Convert the Elo rating difference (plus home advantage) directly into a 1X2 probability via a fitted logistic/ordinal link, without modeling goals explicitly. |
| Gradient-boosted trees on engineered features | Feed the full feature manifest (section 2) into a GBT classifier (3-class, or two binary one-vs-rest models plus a consistency constraint) trained to predict 1X2 directly. |
| Ordinal logistic regression | Treat {away_win, draw, home_win} as an ordinal target (draw genuinely sits "between" the two win outcomes in goal-difference terms) and fit a single ordinal link function. |

### Decision table

| Dimension | Poisson / Dixon-Coles | Elo-based logistic | Gradient-boosted trees | Ordinal logistic regression |
|---|---|---|---|---|
| Data requirements | Low — goals-for/against history only | Very low — Elo history only | High — full feature manifest, more fixtures needed to avoid overfitting | Moderate — full feature manifest but far fewer parameters than GBT |
| Interpretability | High — attack/defense strengths are directly meaningful | High — single rating difference | Low — feature-importance only, no closed form | High — coefficients are directly interpretable |
| Calibration difficulty | Low-moderate — Poisson probabilities are usually well-calibrated out of the box, Dixon-Coles low-score correction adds complexity | Low — logistic link is close to calibrated by construction, still needs the section-10 check | High — tree ensembles are frequently overconfident and need explicit recalibration | Low-moderate — similar to Elo-based |
| Implementation complexity | Moderate (Dixon-Coles' low-score correlation term is fiddly to fit correctly) | Low | Moderate-high (feature pipeline, encoding, tuning, recalibration layer) | Low-moderate |
| Expected performance ceiling | Moderate — well-established, competitive baseline-beating performance in the literature | Moderate — simple but a known reasonable Elo-diff-to-probability mapping | Highest ceiling in principle, if enough clean data and leakage-free features exist | Moderate — similar ceiling to Elo-based, slightly more flexible functional form |
| Maintenance burden | Low — few parameters, stable to refit | Very low | High — needs the most ongoing monitoring for drift/overfitting (section 17) | Low |

### Recommendation for the first implementation PR

**Recommended: the Elo-based logistic mapping**, not the GBT or Dixon-Coles
model, for the *first* implementation PR specifically (not necessarily the
long-run final choice).

Reasoning:

- It needs the smallest, most defensible dataset (section 5's 3,000-fixture
  floor is comfortably enough for an Elo-diff logistic fit; it would be a
  thin dataset for a GBT with 9+ features).
- It is the easiest of the four to get genuinely calibration-correct on the
  first attempt (section 10), which matters more than raw performance
  ceiling for admitting the *first* adapter into this platform's
  fail-closed, no-fabrication culture — a well-calibrated simple model beats
  a powerful mis-calibrated one for every downstream use in this platform
  (fair odds, independent EV, `lower_bound_ev`).
- Its interpretability makes the manual CASH sign-off review (section 16)
  tractable for a human operator who is not a machine-learning specialist —
  "the model output a probability from one input variable, a rating
  difference, via a fitted logistic curve" is auditable in a way a GBT's
  decision surface is not.
- It establishes the full adapter plumbing (dispatch, feature pipeline,
  calibration, versioning, backtest gates, monitoring) end-to-end on the
  simplest possible model, so the *next* adapter (or a v2 of this one using
  GBT or Dixon-Coles) reuses a proven pipeline rather than debugging the
  pipeline and the model simultaneously.

The Poisson/Dixon-Coles model is the natural **second** iteration (it adds
real goal-scoring structure once the pipeline is proven), and GBT is the
natural **third** iteration once enough clean, leakage-audited historical
data has accumulated to justify its data appetite.

---

## 10. Calibration and uncertainty methodology

**Calibration**: after fitting, the raw model output must be checked for
calibration on the held-out validation fold (never the test fold) using a
reliability diagram / calibration curve, binned by predicted probability
decile. If the empirical outcome frequency within a bin deviates from the
bin's mean predicted probability by more than the tolerance in section 14,
apply **Platt scaling** (a single logistic recalibration layer — matches
this adapter's Elo-based logistic model naturally) or **isotonic
regression** (more flexible, appropriate if Platt scaling does not close
the gap) fit on the validation fold, then re-check calibration on the
(still-untouched) test fold once, at the end.

**Real calibrated uncertainty for `lower_bound_ev`**: this is the concrete
mechanism that finally supplies the `uncertainty` mapping
`pricing/engine.py::analyze_market` has been ready to accept since the
Release A integrity-fix PR (`{"sample_size": N, "z": Z}` per outcome).
Concretely, for this adapter:

- `sample_size` = the number of held-out (validation + test fold) fixtures
  used to estimate this model version's calibration for the relevant
  outcome bucket — a real count of backtested fixtures, never an invented
  constant. This directly fixes the exact bug the integrity-fix PR removed
  (a hardcoded `effective_sample_size=200`): the number now comes from
  actual backtest evidence, sport- and model-version-specific.
- The Wilson-score bound itself is computed from this real `sample_size`
  and the model's calibrated probability for the fixture's predicted
  outcome, using the same `_wilson_lower_bound` function already in
  `pricing/engine.py` (no new statistics code needed — only a real input to
  the existing, correct formula).
- `z` follows the platform's existing default (`1.645`, a one-sided 95%
  bound) unless a specific risk posture calls for a different one; any
  deviation from the default must be declared in the model version's model
  card, not silently varied per-call.
- The `uncertainty_method` string recorded is `"wilson_score_backtest_n"`
  (distinct from a generic `"wilson_score"`) so the pricing output makes it
  traceable that the sample size behind the bound came from this specific
  adapter's backtest ledger, not some other calibration source a future
  adapter might use differently.

This is the one piece of section 10 that is genuinely *new* relative to
Release A's math (which only ever received `uncertainty=None`) — everything
else in layer 1 stays unchanged; this adapter simply becomes the first real
supplier of the `uncertainty` parameter `analyze_market` already accepts.

---

## 11. Model-version and calculation hashes

- Every trained model artifact gets an explicit `model_version` string,
  matching the existing `AdapterInterfaceDeclaration.model_version` /
  `ForecastResult.model_version` fields already defined in `adapters/
  base.py`. Format: `soccer_1x2_v<major>.<minor>.<patch>_<training-cutoff-
  date>` (e.g. `soccer_1x2_v1.0.0_2027-06-30`) — the embedded cutoff date
  makes the training data window legible from the version string alone,
  without needing a lookup.
- `model_version` is included in the `fixture`/adapter output the CLI's
  `_canonical_bytes`/`_sha256` hashing already covers (`cli.py`), so a
  version bump automatically changes `calculation_hash` for the same
  `input_hash` — this is the correct behavior: the same market prices
  priced under two different model versions must **not** collide on
  `calculation_hash`, because the forecast content genuinely differs. No
  change to `cli.py`'s hashing logic itself is required; `model_version`
  simply needs to appear inside `forecast_result` (it already does, per
  `ForecastResult.to_dict()`) so it naturally flows into the payload that
  gets hashed.
- `input_hash` stays exactly as defined today (a hash of the raw request
  JSON) — it must **not** incorporate `model_version`, since the same input
  fixture legitimately gets a different `calculation_hash` under a
  different model version while remaining "the same input" in every sense
  the host contract cares about (byte-identical rerun, hash verification
  per `docs/HOST_CONTRACT.md`).

---

## 12. Deterministic rerun requirements

Same input + same `model_version` must produce byte-identical output,
matching the existing runtime-probe determinism requirement
(`docs/HOST_CONTRACT.md`'s "run the same input twice and compare the output
byte for byte"). Concretely for this adapter:

- The trained model artifact for a given `model_version` is immutable once
  published — no online learning, no silent hot-reload of weights under the
  same version string. A retrained model, even with identical
  hyperparameters and data, gets a new `model_version` if there is any
  chance floating-point nondeterminism in training changed the weights
  (e.g. GPU nondeterminism does not apply here since Elo/logistic fitting
  is CPU/deterministic-solver based, but any future GBT iteration must
  pin a deterministic seed and document it in the model card).
- Feature computation at inference time must be a pure function of the
  fixture's declared feature-freeze-time inputs — no wall-clock reads, no
  "as of right now" queries inside the adapter itself. The CLI's `fixture`
  input carries whatever data snapshot the caller captured; the adapter
  computes from that snapshot only, never re-fetches anything.
- `forecast()` itself must remain a pure function of `(fixture, loaded
  model artifact)` — no randomness anywhere in inference (a logistic/Elo
  or Poisson-model forward pass has none by construction; if a future GBT
  iteration uses any randomized inference-time behavior, it must be
  disabled/pinned).

---

## 13. Fair odds, independent EV and lower-bound EV

- **Fair odds from the adapter's own forecast**: once `forecast_available`
  is `true`, the adapter's `probabilities` dict (section 1's canonical
  `home_win`/`draw`/`away_win`) is the input to a **second**,
  adapter-sourced fair-odds calculation — `fair_odds_forecast = 1 /
  p_forecast` — kept entirely separate from `pricing/engine.py`'s
  market-derived `fair_odds` (which is always `1 / p_fair_market`, the
  de-vigged market probability). These two fair-odds numbers must never be
  merged into one field; they answer different questions (what does the
  *market* think is fair vs. what does *our model* think is fair).
- **Independent EV**: `EV_independent = stake * (p_forecast * price_offered
  - 1)`, using the *offered* market price exactly as layer 1's `point_ev`
  formula does, but substituting the adapter's forecast probability for the
  market's de-vigged probability. This is a distinct field, e.g.
  `independent_ev`, returned alongside (never overwriting) layer 1's
  existing `point_ev`. The two must be presented as clearly labeled,
  separately-named fields in whatever future CLI output schema wires this
  adapter in — reusing the `point_ev` key for a forecast-derived number
  would silently conflate "what the market's own de-vig implies" with
  "what our independent model implies," exactly the conflation
  `docs/MULTI_SPORT_ARCHITECTURE.md` layer-3 policy is built to prevent one
  layer up.
- **`lower_bound_ev` becomes real**: per section 10, `lower_bound_ev` is
  computed by `pricing/engine.py::analyze_market`'s existing (unmodified)
  Wilson-score code path, fed this adapter's real backtest `sample_size`
  and its **forecast** probability (not the market's de-vigged one) as
  `p_hat`. This is the first time in the platform's life that
  `lower_bound_ev` is non-null and non-fabricated — every other category
  remains `UNCERTAINTY_UNAVAILABLE` until it gets its own admitted adapter.
- **Keeping Release A's market-only pricing output distinguishable**: the
  CLI's existing `pricing` sub-object (layer 1, market-only) is completely
  unaffected by this adapter's existence — it keeps producing
  `lower_bound_ev: null` / `UNCERTAINTY_UNAVAILABLE` for every category that
  has no admitted adapter, exactly as today. Only when `forecast` (layer 2)
  supplies real `uncertainty` data for *this specific* fixture's selected
  outcome does the pricing engine's optional `uncertainty` parameter get
  populated for that call — this is opt-in per call, not a global engine
  behavior change, and `pricing` and `forecast` remain two distinct
  sub-objects in the output exactly as `cli.py`'s docstring already
  guarantees today.

---

## 14. Backtest acceptance gates

> **Status: proposed, not approved.** Every numeric threshold in this
> section is a candidate figure only — see
> `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`, where each one
> is recorded with `status: PROPOSED_OPERATOR_DECISION` and
> `enforcement: DISABLED`, and `tests/test_soccer_1x2_promotion_thresholds.py`,
> which proves both that no threshold has silently become approved and that
> no runtime code reads or enforces any of them today. A concrete number
> satisfies "this spec has a measurable gate" as a documentation-completeness
> check; it does **not** make that number statistically sound or authorized
> PCBF policy. These numbers require statistical review and explicit
> operator approval (flipping the relevant row's `status` to `APPROVED`)
> before any future implementation PR may wire them into an actual gate
> check. Until that happens, `soccer` stays capped at `RESEARCH-MODEL`
> regardless of what any backtest run against these candidate numbers would
> show — the gate literally does not exist yet, only its proposed shape does.

Candidate, checkable thresholds a model version would need to clear on the
held-out test fold (section 6) before promotion to PAPER eligibility (i.e.
before `adapter_status` may move to `FORECAST_ADAPTER_AVAILABLE` and the
adapter gets registered in `_ADAPTER_IMPLEMENTATIONS`) — pending the
approval described above:

1. **Minimum sample size** *(proposed, unapproved)*: at least 500 fixtures
   in the test fold alone (a subset of the 3,000-fixture dataset floor from
   section 5).
2. **Date-range coverage** *(proposed, unapproved)*: the test fold must
   span at least one full season (not a scattered sample of matchdays
   across seasons), so the gate reflects performance over a complete
   competitive cycle including its own seasonal home-advantage and form
   drift.
3. **Calibration error bound** *(proposed, unapproved)*: expected
   calibration error (ECE), computed over the same probability-decile bins
   used in section 10, would need to be <= 0.05 (5 percentage points) on
   the test fold, post-recalibration.
4. **Beats-baseline-by-X** *(proposed, unapproved)*: the model's log loss
   on the test fold would need to be strictly lower than the
   market-implied baseline's (section 8) log loss by at least 1%, **and**
   the model's Brier score would likewise need to be lower by at least 1%.
   Both metrics agreeing in direction is a reasonable design intent, but
   the specific "1%" figure is exactly the kind of number that needs
   statistical review, not just documentation.
5. **No leakage-control failures** *(this one is not a numeric threshold —
   it is a structural requirement, not pending statistical approval)*:
   every check in section 7's table must have a passing automated test in
   the (future) implementation PR's test suite — a backtest run on a
   codebase with a known-failing leakage check cannot be used to clear
   this gate even if its numbers look good, since the numbers themselves
   would be untrustworthy.

Any model version failing any one of these gates — or, today, simply
because none of them has been approved yet — stays capped at
`RESEARCH-MODEL` classification ceiling (never `PAPER`, and certainly never
`CASH`) — this mirrors the existing `specials_combo` pattern of a named,
mechanical prerequisite blocking promotion (`pricing_supported_requires` in
`data-sources-registry.yaml`) rather than a subjective judgment call.

**How this connects to the decision engine's gate-approval mechanism**:
`decision/engine.py` never infers "gates cleared" from `forecast_available`
alone — it looks up the adapter's exact `(adapter_id, model_version,
model_artifact_hash)` triple in
`registries/data/model-admission-registry.yaml` (see
`docs/MULTI_SPORT_ARCHITECTURE.md`, "Model-admission registry"). Once (and
only once) this section's thresholds are statistically reviewed and their
ledger rows (`docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`)
are flipped `APPROVED`, and a real backtest report for a specific trained
`soccer_1x2_v<...>` model version actually clears every gate above, the
mechanical, auditable follow-through is: add a row to
`model-admission-registry.yaml` for that exact model version and its
artifact hash, with `backtest_gates_status: APPROVED` — that row, not this
section's prose, is what the decision engine actually checks. A backtest
report alone, however clean, changes nothing until that row exists.

---

## 15. Prospective PAPER admission gates

> **Status: proposed, not approved.** As with section 14, the specific
> numeric thresholds below (150 fixtures, 98% availability, **and the
> 4-week minimum window itself — Correction 3 supersedes an earlier draft
> that called the 4-week figure "design intent, not pending numeric
> approval"; it is ledgered like every other number here**) are unapproved
> candidates — see `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`
> and `tests/test_soccer_1x2_promotion_thresholds.py`. Only the general
> *shape* of "backtest, then live shadow-mode, then promote" is design
> intent, not pending numeric approval — every concrete number within that
> shape is.

Backtesting alone (section 14) proves a model *could* have worked
historically; it does not prove the live data pipeline actually delivers
the same features, on time, with the same freshness, going forward. Before
a model version that has cleared section 14 is registered as a live
`FORECAST_ADAPTER_AVAILABLE` adapter producing real `PAPER`-level output
(reachable, per `docs/MULTI_SPORT_ARCHITECTURE.md` decision 3, only once
this model version's row in `model-admission-registry.yaml` also has
`backtest_gates_status: APPROVED`):

- **Minimum live monitoring window** *(proposed, unapproved — ledgered as
  `prospective_minimum_shadow_period_weeks`)*: at least 4 consecutive weeks
  of live (not backtested) fixtures, run in shadow mode — the adapter
  computes and logs its forecast for every eligible fixture, but its output
  is not yet surfaced through the CLI's `forecast` field (a feature flag or
  a not-yet-registered `_ADAPTER_IMPLEMENTATIONS` entry keeps it inert to
  callers during this window, matching this PR's stub pattern).
- **Minimum live fixture count** *(proposed, unapproved)*: at least 150
  fixtures observed in shadow mode, across at least 2 different weeks of
  fixtures (not one congested midweek round), to avoid promoting on a
  lucky short streak.
- **Live feature-availability rate** *(proposed, unapproved)*: required
  features (section 2) would need to be successfully computed, on time
  (before the declared feature-freeze cutoff, section 3), for at least 98%
  of eligible fixtures during the shadow window — a live pipeline that
  frequently misses its own freeze deadline is not ready even if its
  historical backtest was clean, since that is exactly the operational gap
  backtesting cannot catch.
- **Live calibration re-check**: the same (also proposed, unapproved) ECE
  bound as section 14, recomputed on the shadow-mode fixtures only — this
  is the first check entirely on data the model version never saw during
  development, and is the closest thing this process has to a true
  out-of-sample guarantee.
- Only once all four conditions hold, **and** every threshold they depend
  on has been explicitly approved per the status ledger above, does the
  adapter's dispatch entry get
  added to `_ADAPTER_IMPLEMENTATIONS` and `soccer`'s `adapter_status` move
  to `FORECAST_ADAPTER_AVAILABLE` in `adapter-registry.yaml`, **and** a row
  is added to `model-admission-registry.yaml` for this exact model version
  and artifact hash with `backtest_gates_status: APPROVED` (and
  `prospective_status: APPROVED` once this section's own conditions are
  met) does `classification_ceiling` move from `RESEARCH-MODEL`
  (unconditional cap while no forecast exists, or while no admission-registry
  row approves this model version, per
  `docs/MULTI_SPORT_ARCHITECTURE.md` decision 3) to whatever the
  decision-layer rules (section 16) then allow.

---

## 16. Manual CASH-admission requirements

`CASH` is **already structurally unreachable** without both an admitted
forecast **and** an `APPROVED` row in `model-admission-registry.yaml` for
that exact model version and artifact hash — enforced in code today
(`decision/engine.py::evaluate`'s branch order, the model-admission
registry lookup, plus the defensive `_assert_no_forecast_never_cash` and
`_assert_cash_stake_only_for_cash` invariants) and proven by
`tests/test_decision_engine.py` and
`tests/test_platform_classification_invariant.py`. Clearing sections 14-15
and adding the corresponding `backtest_gates_status`/`prospective_status:
APPROVED` rows only makes `PAPER` reachable for `soccer`; it does **not**
by itself authorize `CASH` — the decision engine's
`evidence.cash_min_sample_size` gate (a higher, still-explicit bar than the
STOP-rule threshold) still applies, **and** the model-admission registry's
`cash_admission_status` for that row must also be `APPROVED` (a mechanical
prerequisite only — never a substitute for the human sign-off below), and
a human sign-off is required on top of both automated gates before this
specific model version is treated as CASH-eligible in practice. Before a
human operator manually admits a model version to CASH-eligible status,
they must review and sign off on, in writing (e.g. a short model card /
promotion memo committed alongside the registry change), **and only then**
set `cash_admission_status: APPROVED` on that model version's
`model-admission-registry.yaml` row:

1. The full backtest report from section 14 (sample sizes, calibration
   plots, log loss/Brier comparisons against the market-implied baseline),
   not just a pass/fail summary.
2. The shadow-mode monitoring report from section 15, including the actual
   live feature-availability rate achieved (not just "it passed 98%") and
   any fixtures excluded from the shadow window and why.
3. A live PnL simulation (paper-traded, not real stakes) over the shadow
   window, using the model's `independent_ev` (section 13) against actual
   offered prices at the time, to confirm the backtested edge survived
   contact with live, no-longer-historical odds movement (closing-line
   value can differ meaningfully from a backtest's assumed price).
4. An explicit statement of what `evidence.cash_min_sample_size` and
   `uncertainty.max_width` values are being set for this model version in
   the decision-layer configuration, and why — these remain caller-
   supplied decision inputs per `decision/engine.py`'s existing design
   (nothing here proposes hardcoding a threshold into the engine itself).
5. Sign-off must name a specific human (not "the team" or "the model")
   and a specific date, recorded in the model card — this is a deliberate,
   auditable human decision point, not a rubber stamp automatically
   following the section 14/15 gates.

No amount of backtest or shadow-mode evidence substitutes for this manual
step; sections 14-15 are necessary, not sufficient, for CASH.

---

## 17. Monitoring, drift and rollback rules

> **Status: proposed, not approved (Correction 3).** Every numeric window,
> cadence, and threshold in this section is a candidate figure only,
> ledgered in `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`
> exactly like sections 4/5/14/15's numbers, with
> `status: PROPOSED_OPERATOR_DECISION` / `enforcement: DISABLED`. An
> earlier draft of this section stated these numbers inline without
> ledgering them — that gap is fixed here.

**Ongoing production monitoring** (once `FORECAST_ADAPTER_AVAILABLE`):

- **Calibration drift** *(ledgered as `monitoring_calibration_window_and_cadence`)*:
  recompute the section-10 reliability diagram on a trailing rolling window
  (e.g. the last 200 settled fixtures) on a regular cadence (e.g. weekly).
  Track ECE over time, not just a point-in-time value.
- **Feature-availability drift**: track the live feature-availability rate
  (section 15's metric) continuously, not just during the admission
  window — a data source that silently changes its schema or publishing
  schedule months after admission is exactly the kind of regression this
  must catch.
- **Performance degradation**: track rolling log loss / Brier score against
  the market-implied baseline (section 8) on settled fixtures — the
  baseline comparison must be recomputed on live data too, not assumed to
  hold forever from the original backtest.

**Rollback triggers** (any one is sufficient to trigger immediate
demotion, not just a review):

1. *(ledgered as `rollback_calibration_error_bound`)* Rolling ECE exceeds
   0.08 (a materially looser bound than the 0.05 admission gate, to avoid
   rolling back on ordinary short-window noise) over a trailing window of
   at least 100 settled fixtures.
2. *(ledgered as `rollback_feature_availability_threshold`)* Live
   feature-availability rate drops below 90% (below the 98% admission bar,
   again to avoid over-reacting to a single bad day) over a trailing week.
3. *(ledgered as `rollback_baseline_comparison_window_fixtures` — a
   distinct number from `prospective_minimum_live_fixture_count`'s "150
   live shadow-mode fixtures" in section 15, despite sharing the same
   count; see that ledger row's rationale for why they are tracked
   separately)* Rolling log loss becomes worse than the market-implied
   baseline's over a trailing window of at least 150 settled fixtures — the
   entire justification for this adapter existing (section 8) has stopped
   holding, live.
4. Any confirmed leakage-control failure discovered post-admission (e.g. a
   pipeline bug that let a post-match value into a feature) — this is an
   automatic, immediate rollback regardless of how the performance metrics
   otherwise look, because those metrics are now known to be untrustworthy.
   (Not a numeric threshold — a structural requirement, like section 14
   item 5.)

**Rollback procedure**: revert `adapter_status` to `NOT_IMPLEMENTED` (or, if
a previously-admitted earlier model version is still known-good, revert to
that version rather than to no forecast at all), remove/point back the
`_ADAPTER_IMPLEMENTATIONS["soccer"]` entry accordingly, **and** set the
rolled-back model version's `model-admission-registry.yaml` row's
`backtest_gates_status`/`cash_admission_status` to `REJECTED` (never simply
delete the row — the rejection itself is part of the audit trail); this is
the same registry-and-dispatch mechanism used to admit the adapter in the
first place (section 15), run in reverse, so no new revert-specific
machinery is needed. `classification_ceiling` automatically returns to the
`forecast_available: false` (or "model version no longer has an `APPROVED`
admission row") -> `RESEARCH-MODEL` cap the moment the dispatch reverts and
the row is updated, via the same unconditional layer-3 policy already in
place today — no separate manual decision-layer change is required to make
a rollback safe.

---

## 18. Data source honesty — three explicit buckets

No paid API is assumed anywhere in this spec.

### Mandatory machine-readable inputs

Things that must come from a structured, reliable, non-manual source to be
viable at all — without these, this adapter cannot exist regardless of
modelling sophistication:

- **Historical match results feed** — **operator decision (resolved)**:
  **Football-Data CSV files** (https://football-data.co.uk/data.php) is
  the initial historical source. Date, kickoff time, home/away team
  identity, full-time score, competition/season identity, for every
  fixture in the training coverage window (section 5). Must be structured
  (not free-text scraped match reports) and dated accurately enough to
  support the chronological split (section 6) and feature-freeze
  discipline (section 7). **Caveat the feasibility PR must check
  explicitly** (see "Next sequence" below): Football-Data's own historical
  date-format conventions have not always been consistent across seasons —
  do not assume the date column parses cleanly for every season/league
  file without validation.
- **Fixture list feed** (future kickoffs, team identities, scheduled
  kickoff time) for live inference — this is a strict subset of what the
  historical results feed already needs, just for not-yet-played
  fixtures.
- **Market price feed, captured at the model's actual decision time (not
  closing time)**: this platform already requires `market_prices` as CLI
  input for layer 1 pricing regardless of this adapter's existence, so
  `market_snapshot_odds_1x2` (section 2) rides on infrastructure this repo
  already assumes — no new acquisition problem beyond capturing prices at
  the declared decision horizon rather than at closing (see Correction 1's
  discussion in sections 2-3 and 5-7 for why closing-time capture is
  specifically wrong here).
  **Sourcing caveat directly affecting this feature**: Football-Data's own
  site documentation warns that recent Pinnacle odds data in its files can
  be stale. The ingestion pipeline must **preserve bookmaker/source
  identity per odds column** and must **not** treat Pinnacle as
  automatically authoritative over other bookmakers in the file when
  constructing `market_snapshot_odds_1x2` — a naive "always use the
  Pinnacle column" rule would silently import stale prices exactly when
  Pinnacle's own data lags. This is recorded on the feature's manifest row
  too (`docs/adapters/data/soccer_1x2_feature_manifest.yaml`).

### Inputs that can be collected through browser research

Things a human or a browser-automation-assisted operator **could** gather
manually/semi-manually — named here, **not built** in this PR:

- **Confirmed starting lineups** (`confirmed_starting_lineup_strength_delta`,
  section 2): published on club/league websites and sports-media sites
  45-75 minutes before kickoff; a human operator or a future
  browser-automation-assisted pipeline could capture these per fixture, but
  at Bet9ja's full soccer-category volume this is a meaningfully manual,
  ongoing operational burden, not a one-time setup cost — it recurs every
  single fixture.
- **Injury/suspension news** (not currently in the feature manifest as a
  standalone feature, but a natural input to a future lineup-strength
  computation): scattered across club statements, journalist reporting,
  and injury-tracking sites; no single structured feed was identified
  during this design pass.

### Inputs unavailable reliably enough for admission

Considered and **ruled out for now** — no trustworthy free/manual source
exists at the volume/freshness this adapter needs:

- **Weather forecasts keyed to venue and kickoff time**
  (`weather_conditions_pre_match`, section 2): free weather APIs exist, but
  reliably joining "this specific stadium, this specific kickoff hour" at
  the volume of fixtures this platform covers (32 categories, many
  leagues) without a paid/rate-limited service was not something this
  design pass could responsibly assume; kept optional in the manifest and
  ruled out of the *required* set rather than blocking on it.
- **Referee identity/tendency features**: referee assignment is often
  announced very close to kickoff (sometimes same-day) and structured
  historical referee-tendency data (cards/penalties awarded per referee) is
  not reliably available as a free machine-readable feed at the coverage
  this adapter needs; ruled out entirely for the first implementation
  rather than added as an optional feature with an unreliable source.
- **Real-time betting-market liquidity/volume signals** (distinct from the
  closing price itself, which is a mandatory input): volume/liquidity data
  behind most odds feeds is either paid or not exposed at all by free
  sources; ruled out for this adapter, though `docs/MULTI_SPORT_ARCHITECTURE.md`'s
  existing `liquidity` decision-input block already covers the platform's
  actual liquidity-gating need without requiring this adapter to supply it.

---

## Unresolved / operator decisions

This section is a historical record of what was originally asked, and what
the operator (Kaye) has since decided. Every item below was originally a
judgment call this design spec deliberately left open; this correction PR
records eight of them as **RESOLVED/APPROVED**. Nothing here is deleted —
the original question stays visible alongside its resolution.

1. **Exact data source selection** for the mandatory machine-readable
   match-results/fixtures feed (section 18, bucket 1) — which specific
   provider, at what cost (if any) and update cadence, was not chosen at
   spec-writing time.
   **RESOLVED/APPROVED**: **Football-Data CSV files**
   (https://football-data.co.uk/data.php). See section 18, bucket 1 for the
   full resolution, including the explicit caveat that Football-Data's own
   site documentation warns recent Pinnacle odds data can be stale — the
   ingestion pipeline must preserve bookmaker/source identity per odds
   column and must not treat Pinnacle as automatically authoritative over
   other bookmakers, a caveat that also directly affects
   `market_snapshot_odds_1x2`'s sourcing (see that feature's manifest row
   and section 18).
2. **Whether to build the browser-research/lineup-capture pipeline at all**
   (section 18, bucket 2) — the spec treats confirmed-lineup strength as
   optional precisely so the first implementation can ship without this
   decision being made.
   **RESOLVED/APPROVED**: **deferred** — not built in the first model
   version. `confirmed_starting_lineup_strength_delta` in the feature
   manifest stays optional/unimplemented (see section 2's table, updated
   with this resolution noted inline).
3. **Budget/time allocated to backtesting** (sections 5-6, 14) — the
   3,000-fixture / 3-season floor is a defensibility minimum, not a
   recommendation to stop there; how much historical data to actually
   acquire and clean was left as a cost/time tradeoff.
   **RESOLVED/APPROVED**: **acquire every consistently formatted completed
   season available for the five approved leagues (decision 4 below);
   determine the usable period after validation** — not a fixed season
   count decided up front. The "3 seasons" / "3,000 fixtures" numbers from
   section 5 (now ledgered — see Correction 3 in this PR's change history)
   are a **floor/target to validate against**, not a hard acquisition
   target, given this decision.
4. **League/competition scope for the first model version** — one league,
   several, or a global pool (section 5's multi-league caution) was left
   open.
   **RESOLVED/APPROVED**: **Premier League, Bundesliga, La Liga, Serie A,
   Ligue 1** (five leagues, pooled per section 5's multi-league caution —
   `league_id` must be retained as a feature/stratification key).
5. **Who the "specific human" in section 16's sign-off is**, and what
   internal review process (if any) sits around that sign-off.
   **RESOLVED/APPROVED**: **Kaye** is the named CASH signatory (section 16).
6. **Statistical review and approval of the numeric backtest/prospective/
   dataset/monitoring thresholds** proposed across sections 4, 5, 14, 15,
   and 17 (originally: 500-fixture minimum, one full season, ECE <= 0.05,
   >= 1% log loss/Brier improvement, 150 live fixtures, 98% live
   feature-availability rate; expanded by this PR's Correction 3 to also
   cover the dataset-size/coverage numbers, the feature-staleness
   tolerance, the shadow-period length, and the section-17 monitoring/
   rollback numbers — see `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml`
   for the full, current list of ids).
   **RESOLVED/APPROVED — as still pending, not as an oversight**: every
   threshold **remains proposed and disabled**
   (`status: PROPOSED_OPERATOR_DECISION`, `enforcement: DISABLED`). This is
   an explicit, deliberate operator decision to keep them unapproved for
   now, not something left unresolved by accident — none of them authorizes
   anything until an operator, after statistical review, explicitly changes
   a given row's status to `APPROVED`, following the process in decision 8
   below.
7. **Weather and referee features** (section 2's optional-feature table;
   section 18, bucket 3) — originally documented only as "no wired
   source"/"ruled out for now," a data-availability gap rather than a
   product decision.
   **RESOLVED/APPROVED**: **excluded from the first model** — this is now
   an explicit decision, not just a data-availability gap. The feature
   manifest's `weather_conditions_pre_match` row and any referee-tendency
   mention stay documented-but-rejected, per bucket 3 in section 18.
8. **Statistical approval process** for how thresholds get approved at all
   — not addressed as a process in the original spec.
   **RESOLVED/APPROVED**: select thresholds using training/validation
   evidence only, and **freeze** them (flip the relevant
   `soccer_1x2_promotion_thresholds.yaml` row to `APPROVED`) *before*
   opening the held-out test set; the test set is opened **exactly once**,
   after freezing, to produce the actual go/no-go result. This is now
   explicit process guidance in section 14 (backtest acceptance gates) and
   section 6 (temporal splits) — the test set must never be touched,
   peeked at, or iterated against while thresholds are still being chosen.

---

## Next sequence (approved scope ordering)

This section records the operator-approved sequence of PRs from here, so it
is not lost. It supersedes the previous "Recommended scope for the next
(implementation) PR" section below (kept, updated in place, as step 4-6 of
this sequence) — the key structural change this correction PR makes is
inserting an explicit **data-feasibility PR** before any modeling work, and
being explicit that "prove the data can support the model" and "build the
model" are two different PRs' jobs.

1. **This correction PR (current)** — the three corrections, the eight
   recorded operator decisions, and this next-sequence section. No new
   ingestion code, no downloads, no model training.
2. **A data-feasibility PR (next, NOT this PR)** — its job is **"prove the
   data can support the model,"** explicitly **not** "build the model": no
   modeling code, no candidate baselines, no promotion-gate work. Download
   and validate the five approved leagues (decision 4) from Football-Data,
   reporting:
   - seasons available per league,
   - row counts,
   - missing fields,
   - odds-column availability, **and which bookmakers are present per
     file**, given the Pinnacle-staleness caveat (section 18, bucket 1) —
     this feasibility report is exactly where that caveat gets checked
     against real files for the first time,
   - duplicate detection,
   - timestamp/date-format limitations — **Football-Data's date format has
     historically been inconsistent across seasons; this must be checked
     explicitly, never assumed clean.**
   This correction PR does **not** perform this download/validation itself
   — it is explicitly out of scope here.
3. **Freeze the usable dataset contract** (post-feasibility) — using the
   feasibility PR's findings against the `dataset_minimum_seasons` /
   `dataset_minimum_fixture_count` / `dataset_minimum_feature_coverage_rate`
   floor/target numbers (Correction 3's ledger rows), determine and commit
   the actual usable historical period and league set, per operator
   decision 3 above ("determine the usable period after validation").
4. **Build baselines and derive candidate thresholds** from
   training/validation data only (implements section 8's baselines and
   feeds real numbers into a candidate review of the section 14/15/17
   ledger rows) — the held-out test set stays unopened throughout this
   step, per operator decision 8's process.
5. **Freeze approved thresholds** — flip the relevant
   `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml` rows' `status`
   to `APPROVED` (and `enforcement` to `ACTIVE` once a gate-check actually
   consults them), per operator decision 8's process, *before* step 6.
6. **Open the untouched held-out test set exactly once** to produce the
   actual go/no-go backtest result (section 14) — never before step 5,
   never repeated afterward per section 6 item 4's re-use-invalidates-the-
   gate rule.

Steps 4-6 above implement (with updated ledger/status references) what an
earlier draft of this spec called the "Recommended scope for the next
(implementation) PR":

1. Pick one mandatory data source (operator decision 1, now resolved:
   Football-Data) and build the ingestion/normalization pipeline for the
   historical match-results feed only — no live fixture feed yet. (This is
   step 2's feasibility PR plus the corresponding production ingestion
   work.)
2. Implement feature computation for the 9 required features (section 2)
   with the leakage controls from section 7 enforced by unit tests against
   a small known fixture list (the rolling-window off-by-one case
   specifically, and the decision-horizon-matching check from Correction 1
   for `market_snapshot_odds_1x2`).
3. Implement the chronological split (section 6) and the market-implied +
   static-base-rate baselines (section 8) — get the promotion-bar
   comparison numbers working before touching a real candidate model.
4. Implement the Elo-based logistic candidate (section 9's recommendation)
   and the Platt-scaling calibration step (section 10).
5. Run the backtest and report the numbers honestly against section 14's
   *proposed* thresholds — this PR's job is to produce that report, not to
   guarantee it clears anything, and not to treat those proposed numbers
   as approved. Before that implementation PR is opened, every threshold in
   `docs/adapters/data/soccer_1x2_promotion_thresholds.yaml` relevant to the
   gates being cleared must have gone through statistical review and been
   explicitly marked `APPROVED` by the operator (step 5 above) — running a
   real backtest against still-`PROPOSED_OPERATOR_DECISION` numbers
   produces a report with nothing to honestly compare against.
   `adapter_status` stays `DESIGN_IN_PROGRESS` (or moves to a new, still
   pre-live status if one is added) until the now-approved gates in
   sections 14-16 are actually cleared with a human sign-off — registering
   the adapter in `_ADAPTER_IMPLEMENTATIONS`, flipping to
   `FORECAST_ADAPTER_AVAILABLE`, **and adding the corresponding row to
   `model-admission-registry.yaml`** are explicitly **out of scope** for
   that PR unless the backtest report clears every approved section 14
   gate.

Shadow-mode monitoring (section 15) and live promotion are their own,
later PR(s), gated on the implementation PR's backtest report actually
passing.
