# Multi-Sport Deterministic Calculation Platform — Release A

This document supersedes `docs/CALCULATOR_KICKOFF.md`'s placeholder scope.
`docs/CALCULATOR_KICKOFF.md` recorded the decision to scaffold
`src/pcbf_calculator/` with a typed `NOT_IMPLEMENTED` placeholder and no
design spec. This document is that design spec, and `src/pcbf_calculator/`
now contains the real Release A implementation described below — the
placeholder scaffold (`calculator.py`, its `NOT_IMPLEMENTED`-only CLI) has
been removed and replaced, not left alongside the new code.

It is the canonical reference for whoever picks up Release B/C next
(soccer, tennis, basketball forecasting adapters).

## Scope recap

Per `docs/DEVELOPER_HANDOFF.md` Agent D, this repository owns deterministic
calculation artifacts only. This platform extends that scope from
football-only to all 32 Bet9ja categories, while keeping every hard
constraint from `docs/HOST_CONTRACT.md`: host-neutral JSON-in/JSON-out CLI,
zero runtime dependencies, typed failure records, no LLM-fabricated
probabilities, no per-sport forecasting model in this PR.

`src/pcbf_football/` (the runtime probe) is untouched by this PR and keeps
working exactly as before.

## Three architectural layers

### Layer 1 — Universal market-pricing engine (`pcbf_calculator/pricing/`)

The only layer in Release A that does real, unambiguous math. Pure,
deterministic, dependency-free. One implementation handles two-way,
three-way, and general N-way markets — there is no per-arity code path.

**Opposing-price validation** (before any calculation runs):
- at least two outcomes present (`INCOMPLETE_OPPOSING_PRICES` otherwise)
- every price is a finite number, not a boolean, strictly greater than 1.0
  (`INVALID_PRICE_VALUE` otherwise, with the specific field named)

**Formulas** (decimal-odds convention; stake defaults to 1 unit):

| Quantity | Formula |
|---|---|
| Implied probability | `p_implied = 1 / price` |
| Bookmaker margin (overround) | `sum(p_implied) - 1` |
| Fair (de-vigged) probability | `p_fair = p_implied / sum(p_implied)` |
| Fair odds | `odds_fair = 1 / p_fair` |
| Point EV | `EV = stake * (p_fair * price - 1)` |
| Lower-bound EV | `EV_lower = stake * (p_fair_lower * price - 1)` |

Point EV derivation: `EV = p*payout - (1-p)*stake`, and a decimal price
already returns the stake on a win (`payout = stake * price`), so
`EV = p*stake*price - (1-p)*stake = stake*(p*price - 1)`.

**De-vigging method**: the **multiplicative/proportional** method is
implemented and is the universal Release A default for every category:
normalize implied probabilities to sum to 1. It is the simplest method that
guarantees a valid probability distribution and is the standard baseline in
the sports-pricing literature. It is selected through a small pluggable
registry (`_DE_VIG_METHODS` in `pricing/engine.py`) rather than hardcoded
inline, so a future, explicitly-approved Release B+ method (e.g. Shin's
method, which apportions margin by an assumed insider-trading share — a
reasonable alternative for very large outright fields) can be registered
and selected per-adapter without changing this engine's call sites. An
unrecognized `de_vig_method` fails closed with the typed
`UNSUPPORTED_DE_VIG_METHOD` code rather than silently falling back to the
default. Every calculation result records which method actually ran via
`de_vig_method`.

**Lower-bound EV — resolved decision: unavailable until an admitted adapter
supplies real uncertainty.** A previous version of this engine computed a
Wilson-score lower confidence bound by treating the de-vigged probability as
an observed proportion from an *invented* `effective_sample_size=200`
(`z=1.645`). That was a bug, not a conservative default: proportional de-vig
by construction makes point EV nearly identical across every outcome in a
market, and layering a confidence bound derived from a hardcoded constant on
top of it manufactured the *appearance* of differentiated signal between
outcomes where none of it was real — the differentiation came entirely from
the arbitrary constant, not from any actual uncertainty data. That code path
has been removed outright; it must not be reintroduced in any form (no
Wilson bound, or any other confidence bound, computed from an invented or
hardcoded sample size, anywhere in this codebase).

The correct behavior, and what Release A now does: a real Wilson-score
lower bound needs real calibrated uncertainty (a sample size, variance, or
whatever the applicable `uncertainty_method` actually requires) about the
fair-probability estimate, and no admitted forecasting adapter exists yet to
supply that (see layer 2 below — every category resolves to
`NoForecastAdapter` in Release A). So for every category, every outcome's
`lower_bound_ev` and `lower_bound_probability` are `null`, and
`lower_bound_ev_reason` carries the typed code `UNCERTAINTY_UNAVAILABLE`.
This is not a placeholder pending a decision — it is the honest, resolved
answer given the current state of the world.

The schema still leaves room for the real thing: `analyze_market` accepts an
optional `uncertainty` mapping (outcome name -> `{"sample_size": N, "z": Z}`)
that a future admitted adapter can populate with data it actually calibrated
(e.g. a real historical win/loss ledger). When that mapping supplies an
outcome's real sample size, the engine computes a genuine Wilson-score bound
from it, sets `uncertainty_method` (e.g. `"wilson_score"`), and clears
`lower_bound_ev_reason` to `null`. Release A's CLI never populates this
mapping, because no admitted adapter exists yet to calibrate it.

**Market-derived ranking**: see decision 5 below (`market_quality`) —
Release A ranks *markets*, by completeness/margin/evidence-quality signals,
never outcomes.

**Tests** (`tests/test_pricing_engine.py`): two-way, three-way, N-way (8
outcomes), incomplete prices, invalid price types (string/null/bool/NaN/
<=1.0), a zero-margin arbitrage-free edge case (fair probability equals
implied probability, EV ~= 0), and a heavily-vigged market (margin > 40%,
every outcome shows negative point EV against the offered price).

### Layer 2 — Sport/market adapter framework (`pcbf_calculator/adapters/`)

Framework only. `SportAdapter` (`base.py`) is the abstract interface a
future per-sport forecasting adapter must implement, declaring:
`sport_id`, `valid_markets`, `settlement_units`, `feature_requirements`,
`data_sources`, `uncertainty_method`, `model_version` — exactly the fields
required by the task spec, as an `AdapterInterfaceDeclaration` dataclass.

`registry.py` dispatches by category id read from the registries:
- **Category not present in any registry at all** →
  `UnsupportedCombinationError` (`UNSUPPORTED_SPORT_MARKET_COMBINATION`).
  This is a hard failure — there is no basis for producing any output.
- **Category present but no concrete adapter registered** (every category
  in Release A) → `NoForecastAdapter`, a stub that proves the dispatch
  framework end-to-end and always returns
  `ForecastResult(forecast_available=False, probabilities=None,
  no_forecast_reason=...)`. It never fabricates a probability, and it never
  reuses another sport's declaration or reason — `NoForecastAdapter` is
  purely id-driven with no soccer-specific (or any-sport-specific)
  assumptions baked in.

Release B/C+ populate `_ADAPTER_IMPLEMENTATIONS` (currently empty by
design) with real `SportAdapter` subclasses. No such subclass exists in
this PR.

**Tests** (`tests/test_adapters.py`): a registered category with no adapter
returns the no-forecast stub; the stub never returns a probability; an
unregistered id raises the typed error; two different sports never share a
declaration or forecast identity (no cross-sport leakage); every one of the
32 required categories resolves to *some* adapter (concrete or stub).

### Layer 3 — PCBF decision layer (`pcbf_calculator/decision/`)

A real rule-evaluation engine over explicit typed inputs supplied by the
caller (`evidence`, `freshness`, `liquidity`, `uncertainty`) — there is no
live evidence/liquidity data source wired up yet, so nothing here is a
hardcoded or fake classifier; every threshold comes from the request JSON.

**STOP rules**, evaluated in this fixed order (first match wins):

1. `STOP_STALE_DATA` — `freshness.data_age_seconds > freshness.max_age_seconds`
2. `STOP_INSUFFICIENT_EVIDENCE` — `evidence.sample_size < evidence.min_sample_size`
3. `STOP_INSUFFICIENT_LIQUIDITY` — `liquidity.available_stake < liquidity.min_required_stake`
4. `STOP_EXCESSIVE_UNCERTAINTY` — `uncertainty.width > uncertainty.max_width`
5. `STOP_NEGATIVE_EV` — the priced outcome's confidence-bounded EV is `<= 0`.
   Layer 1 only produces a real `lower_bound_ev` once an admitted adapter
   supplies calibrated uncertainty (Release A: never). When `lower_bound_ev`
   is `null`, this rule evaluates `point_ev` instead — still real,
   non-fabricated de-vigged market math, just without a confidence bound.
   This is safe only because passing this rule can never by itself
   authorize `CASH` (see the fixed policy below) — at most it lets a
   no-forecast category reach `PAPER`.

**Classification ceiling, once every rule passes — fixed, non-configurable
policy, settled (not open for revision):**
- if `forecast.forecast_available` is `False` (true for every category in
  Release A, since no adapter exists yet), the ceiling is capped at
  `PAPER`, unconditionally. There is no decision-input field, STOP-rule
  combination, or evidence value that can move it. Rationale: market-implied
  probabilities can support research and price comparison, but they cannot
  prove a betting edge measured against the same market they were derived
  from — that would be circular. Only an independently admitted forecast
  (layer 2) can authorize `CASH`. This is enforced twice: once by the
  branch order in `decision/engine.py::evaluate`, and again by a defensive
  invariant assertion (`_assert_no_forecast_never_cash`) immediately before
  the result is returned, so there is no code path — today or added later
  by mistake — where a `forecast_available: false` category can reach
  `CASH`. `tests/test_decision_engine.py` proves this cannot be bypassed
  even when every STOP-rule input is made maximally generous.
- otherwise, if `evidence.sample_size < evidence.cash_min_sample_size` (a
  higher, optional bar than the STOP threshold; defaults to
  `min_sample_size` when omitted), the ceiling is also capped at `PAPER`.
- otherwise the ceiling is `CASH`.

**Forecast quality vs. market profitability stay separate, always.** The
decision result carries both `forecast_quality` (the layer 2
`ForecastResult`) and `market_profitability` (the selected outcome's layer 1
pricing dict) as two distinct sub-objects, never merged into one score —
this is asserted directly in `tests/test_decision_engine.py`.

**Tests** (`tests/test_decision_engine.py`): one test per STOP rule proving
it actually stops with the correct typed rejection, a fully-passing input
reaching `CASH` (with a forecast) and `PAPER` (without one, and when
under the CASH sample-size bar), STOP-rule precedence, and typed
`MISSING_DECISION_INPUT` errors for each missing/malformed required block.

## Registry schema design

Four YAML files, one row per category, joined on a shared `id`. Real files
live at `src/pcbf_calculator/registries/data/*.yaml` (so they ship inside
the wheel as package data); `registries/*.yaml` at the repository root are
symlinks to the same files and are the canonical place a human edits them —
this avoids a second copy that could drift, while still giving the
platform a discoverable top-level `registries/` directory as requested.

**Why a hand-rolled YAML subset instead of PyYAML**: the zero-runtime-
dependency constraint in `docs/HOST_CONTRACT.md` applies to this whole
platform, not just `pcbf_football`. Adding PyYAML would violate that
constraint and would need the same three-host dependency approval process
`docs/DEVELOPER_HANDOFF.md` requires before adding pandas/NumPy/etc. Instead
`pcbf_calculator/registries/yamlmini.py` implements a small, documented,
*restricted* YAML subset (flat block-mapping list items, flow-sequence
values, quoted/bare scalars) sufficient for every registry file in this
repository. It is explicitly not a general-purpose YAML parser.

**Field split across the four files** (sport-level vs. market-type vs.
adapter vs. data-source facts, per the task's instruction to split
sensibly rather than repeat everything everywhere):

- `sports-registry.yaml` — identity and taxonomy: `id`, `display_name`,
  `kind` (`sport` | `product_family`), `underlying` (the sport id a
  product/market family sits on top of, or `null`), `event_type`
  (`match` | `individual` | `multi_competitor` | `combo` | `futures`),
  `competitor` (`team` | `individual` | `mixed`).
- `market-types-registry.yaml` — market-type facts: `id`, `structures`
  (flow list of `two_way`/`three_way`/`n_way`), `markets` (flow list of
  typical market codes), `settlement` (free-text settlement requirement).
- `adapter-registry.yaml` — adapter facts: `id`, `adapter_status` (one of
  the five allowed statuses), `adapter_unsupported_reason`,
  `classification_ceiling` (the ceiling layer 3 may grant while this
  adapter status holds), `feature_requirements` (flow list, empty in
  Release A), `uncertainty_method` (`null` in Release A).
- `data-sources-registry.yaml` — data-source facts: `id`, `runtime_status`
  (one of the five allowed statuses — whether layer 1 pricing can run
  today), `runtime_unsupported_reason`, `data_sources` (flow list of
  required feed ids), and an optional `pricing_supported_requires` present
  only on rows held below `PRICING_SUPPORTED` pending a named engineering
  prerequisite (Release A: `specials_combo` only, naming
  `CORRELATION_AWARE_COMBO_PRICING_METHOD`).

Every non-`sports-registry.yaml` row's `id` must resolve to a
`sports-registry.yaml` row (the cross-reference check); every
`sports-registry.yaml` row's `underlying` (when not `null`) must itself
resolve to a `kind: sport` row.

**Allowed status enum** (used verbatim for both `runtime_status` and
`adapter_status` — one shared five-value enum, not two different ones):
`PRICING_SUPPORTED`, `FORECAST_ADAPTER_AVAILABLE`, `RESEARCH_ONLY`,
`UNSUPPORTED_INPUT`, `NOT_IMPLEMENTED`.

## Registry contents — Release A statuses

All 32 required categories are present in every registry file
(`tests/test_registries.py::test_all_32_categories_are_present`). Release A
statuses:

- **31 categories**: `runtime_status = PRICING_SUPPORTED`,
  `adapter_status = NOT_IMPLEMENTED`, `classification_ceiling = PAPER`.
- **`specials_combo`**: `runtime_status = RESEARCH_ONLY`,
  `classification_ceiling = RESEARCH-MODEL`, and this stays fixed until a
  named engineering prerequisite is met — it is not a placeholder pending a
  product decision. Rationale: the universal N-way de-vig math assumes one
  mutually-exclusive, exhaustive market. A combo/parlay is a product of
  correlated legs, potentially spanning multiple sports and market types at
  once; treating its combined price as a single N-way market and de-vigging
  it the same way would silently misstate the fair probability (it ignores
  leg correlation entirely). `specials_combo` may only move to
  `PRICING_SUPPORTED` once a **correlation-aware** calculation method exists
  and is implemented — the same N-way math this engine already runs for
  every other category is explicitly not sufficient and must never be
  applied to this category as-is. This requirement is recorded mechanically,
  not just here: `data-sources-registry.yaml`'s `specials_combo` row carries
  `pricing_supported_requires: CORRELATION_AWARE_COMBO_PRICING_METHOD`
  alongside its existing `runtime_unsupported_reason:
  COMBO_LEG_CORRELATION_NOT_MODELED`, and
  `tests/test_registries.py::test_specials_combo_names_its_correlation_aware_pricing_prerequisite`
  asserts both are present. Until then, the platform fail-closes this
  category at the CLI level (`UNSUPPORTED_INPUT`) rather than emit a pricing
  result that looks legitimate but is not.
- **`outrights`**: deliberately kept at `PRICING_SUPPORTED` /
  `PAPER` — unlike a combo, an outright market ("who wins the tournament")
  is a single mutually-exclusive, exhaustive N-way market over the entire
  field, which is exactly what the layer 1 engine already handles. No
  special-casing needed beyond noting its long-horizon settlement in
  `market-types-registry.yaml`.

## Typed failure catalogue (`pcbf_calculator/errors.py`)

| Code | Layer | Meaning |
|---|---|---|
| `INCOMPLETE_OPPOSING_PRICES` | 1 | Fewer than two outcomes, or no price map at all. |
| `INVALID_PRICE_VALUE` | 1 | A price was non-numeric, boolean, non-finite, or `<= 1.0`. |
| `UNSUPPORTED_DE_VIG_METHOD` | 1 | Requested `de_vig_method` is not one of the approved, registered methods. |
| `UNCERTAINTY_UNAVAILABLE` | 1 | Per-outcome reason (not a CLI failure): no admitted adapter has supplied real calibrated uncertainty, so `lower_bound_ev`/`lower_bound_probability` are `null`. True for every category in Release A. |
| `INVALID_REQUEST` | CLI | A required top-level field (`event_id`, `category`, `stake`, `selected_outcome`) was missing or malformed. |
| `UNSUPPORTED_INPUT` | CLI/registries | Category resolves but its `runtime_status` is not `PRICING_SUPPORTED` (Release A: `specials_combo`). |
| `UNSUPPORTED_SPORT_MARKET_COMBINATION` | 2 | Category id is not present in any registry at all. |
| `NOT_IMPLEMENTED` | 2 | Registered category has no forecasting adapter (soft — pricing still runs; surfaced via `forecast.no_forecast_reason`, not a CLI-level failure). |
| `MISSING_DECISION_INPUT` | 3 | A required decision-input block/field was missing or the wrong type. |
| `STOP_STALE_DATA` / `STOP_INSUFFICIENT_EVIDENCE` / `STOP_INSUFFICIENT_LIQUIDITY` / `STOP_EXCESSIVE_UNCERTAINTY` / `STOP_NEGATIVE_EV` | 3 | The named STOP rule fired; classification is `REJECTED`. |

These are new codes for this domain, following the same shape as the
existing `HOST_RUNTIME_UNAVAILABLE` / `M02_TEST_FAIL` convention: a
`{"code", "field", "reason"}` object, never a bare exception, never a
silently substituted default.

## CLI contract (`python -m pcbf_calculator INPUT [OUTPUT]`)

Same host-neutral JSON-in/JSON-out shape as `pcbf_football`. See the
docstring at the top of `src/pcbf_calculator/cli.py` for the full input/
output schema. Key guarantee: `pricing`, `forecast`, and `decision` are
always three distinct sub-objects (each `null` when not computed) —
market-derived numbers can never be mistaken for a forecast, and forecast
quality can never be mistaken for ticket profitability.

## Release sequence

- **Release A (this PR)**: universal validation, normalization, and
  market-pricing calculations across all 32 categories; the four
  registries; the adapter framework (interface + registry-driven dispatch);
  the decision-layer rule engine. No per-sport forecasting logic.
- **Release B (future)**: soccer 1X2 forecasting adapter — the first
  concrete `SportAdapter` subclass, registered in
  `adapters/registry.py::_ADAPTER_IMPLEMENTATIONS`, with `soccer`'s
  `adapter-registry.yaml` row moving to `FORECAST_ADAPTER_AVAILABLE`.
- **Release C (future)**: tennis match-winner and basketball moneyline
  adapters, same pattern.
- **Later releases (future)**: adapter priority is decided by actual
  candidate volume and ledger evidence, not by this document — this
  platform only records the sequence already agreed with the task, it does
  not re-prioritize it.

## Settled decisions (corrected in the integrity-fix PR)

A previous version of this document listed several of these as "open
questions... a human may want to revisit." Decisions 1-4 below are now
resolved policy, not placeholders — this section records what was decided
and why, for future contributors, rather than flagging them as pending.

1. **De-vig method**: multiplicative/proportional is the universal Release A
   default over Shin's method, and is implemented through a pluggable
   registry (`_DE_VIG_METHODS`) rather than hardcoded — a future,
   explicitly-approved Release B+ method can be added without changing
   engine call sites. No change to the core de-vig math itself. See layer 1
   above.
2. **Lower-bound EV**: resolved as `null` with typed reason
   `UNCERTAINTY_UNAVAILABLE` for every category in Release A, replacing the
   removed Wilson-score-from-invented-`effective_sample_size=200` bug (see
   layer 1 above for the full rationale). This is not conservative-but-
   arbitrary the way the old constant was — it is the honest answer given
   that no admitted adapter supplies real uncertainty data yet, and the
   schema (the optional `uncertainty` parameter to `analyze_market`) is
   ready to carry real calibrated data the moment one does.
3. **No-forecast implies `PAPER` ceiling, never `CASH`** — settled, fixed,
   non-configurable policy (see layer 3 above). Market-implied probabilities
   can support research and price comparison, but cannot prove a betting
   edge measured against the same market they were derived from; that would
   be circular. There is no code path, decision-input value, or STOP-rule
   combination that can move a no-forecast category to `CASH`, and this is
   asserted defensively at runtime in addition to being tested.
4. **`specials_combo` marked `RESEARCH_ONLY` rather than
   `PRICING_SUPPORTED`** — settled: it stays `RESEARCH_ONLY` until a
   correlation-aware combo pricing method is designed and implemented; the
   registry names this prerequisite mechanically
   (`pricing_supported_requires: CORRELATION_AWARE_COMBO_PRICING_METHOD`)
   rather than leaving it as tribal knowledge. See the layer-1 registry
   section above.
5. **Ranking scope**: Release A may rank *markets* (by completeness, margin,
   evidence quality — the `market_quality` object every `analyze_market`
   result carries) but must never rank *outcomes* within a market as
   implied betting recommendations without an independent admitted
   forecast. The previous `ranking_by_point_ev` field (a descending-point-EV
   ordering of outcomes) has been removed for exactly that reason: point EV
   under proportional de-vig alone does not represent a validated edge, and
   presenting it as an ordered "best bet" list implied one. See layer 1
   above.
6. **Category id taxonomy** (e.g. `cross_country`, `alpine` as separate
   sport ids rather than sub-disciplines of one "skiing" sport) follows the
   Bet9ja category list literally as given in the task rather than
   imposing an additional taxonomy layer — still an open, low-risk
   judgement call, unaffected by this PR.
