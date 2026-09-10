# Proposed normalized dataset schema (Football-Data -> Soccer 1X2 feature manifest)

**Status: proposal/design only.** This document specifies the TARGET shape
a future transform pipeline would need to map Football-Data's raw CSV
columns into, to feed
`docs/adapters/data/soccer_1x2_feature_manifest.yaml`. It does **not**
implement that transform — no ingestion/normalization code exists in this
PR (see `docs/adapters/SOCCER_1X2_ADAPTER_SPEC.md`, "Next sequence," step
2's own scope boundary: this feasibility PR proves the raw data can
support the pipeline, a later PR builds the actual transform).

## Design principles carried over from the spec (non-negotiable)

1. **Opening, pre-closing, and closing odds are distinct, separately
   tracked fields — never collapsed or averaged.** A single bookmaker's
   opening line and its closing line answer different questions and map
   to different manifest rows (`market_snapshot_odds_1x2` vs.
   `market_closing_odds_1x2_for_clv_analysis`); an aggregate column
   (`Avg*`/`Max*`) is a third, distinct thing again (a market summary
   statistic, not any one bookmaker's own price).
2. **Bookmaker/provider identity travels with every price, always.** No
   normalized row may contain an odds value without a
   `bookmaker` field naming exactly which column it came from.
3. **Pinnacle is tracked as its own distinct field, never auto-
   authoritative.** Football-Data's own documentation warns recent
   Pinnacle data can be stale (spec section 18, bucket 1). The normalized
   schema below carries an explicit `is_pinnacle: bool` flag per odds row
   and never designates Pinnacle as "the" price when multiple bookmakers
   disagree.
4. **No column may claim a `captured_at` timestamp Football-Data does not
   actually provide.** Football-Data's odds columns carry only an
   "opening" vs. "closing" (or aggregate) LABEL — never a true
   collection timestamp. The normalized schema's `price_label` field
   (below) preserves that label honestly; it is explicitly NOT a
   substitute for `market_snapshot_odds_1x2`'s mandatory `captured_at`
   (manifest row) — see "Known gap" below.

## Proposed normalized tables

### `fixtures`

One row per completed, non-void match.

| field | type | source (raw Football-Data column) | notes |
|---|---|---|---|
| `fixture_id` | string | derived: `f"{league_code}:{date_iso}:{home_team}:{away_team}"` | stable synthetic key; Football-Data has no native fixture id |
| `league_id` | string | `Div` (mapped to this repo's own league_code, e.g. `E0`) | retained per spec section 5's multi-league caution |
| `season_label` | string | derived from the source filename/season code | e.g. `"2023-24"` |
| `kickoff_date` | date (ISO 8601) | `Date`, normalized via the format actually detected per file (see `validation.py::parse_date` — do not assume one format) | |
| `kickoff_time_local` | time or null | `Time` (absent in older seasons — null, never defaulted to a guessed time) | |
| `home_team` | string (canonicalized) | `HomeTeam` | canonicalization (e.g. "Nott'm Forest" -> a stable team id) is a normalization-pipeline concern, not decided here |
| `away_team` | string (canonicalized) | `AwayTeam` | |
| `full_time_home_goals` | integer | `FTHG` | |
| `full_time_away_goals` | integer | `FTAG` | |
| `full_time_result` | enum {H, D, A} | `FTR` | matches spec section 1's settlement rule: only used when the match completed normally |
| `half_time_home_goals` | integer or null | `HTHG` | optional context, not a required feature |
| `half_time_away_goals` | integer or null | `HTAG` | |
| `data_quality_flags` | list[string] | derived from `validation.py`'s typed `RejectionReason` codes | a fixture failing any check is excluded from the modeling-ready view, never silently repaired |

### `odds_prices` (one row per bookmaker x variant x fixture — NEVER collapsed)

| field | type | source | notes |
|---|---|---|---|
| `fixture_id` | string | FK to `fixtures` | |
| `bookmaker` | string | derived from column prefix (e.g. `B365` -> `"Bet365"`, `PS`/`P` -> `"Pinnacle"`) | see `schema_inspection.KNOWN_BOOKMAKER_PREFIXES` |
| `is_pinnacle` | boolean | derived | drives the "never auto-authoritative" rule above |
| `price_label` | enum {opening_or_only, closing, market_aggregate_avg, market_aggregate_max} | derived from column suffix (`H/D/A` vs `CH/CD/CA`) or aggregate prefix (`Avg*`/`Max*`) | honestly named — NOT a timestamp |
| `home_odds` | float or null | `{prefix}H` / `{prefix}CH` | decimal odds |
| `draw_odds` | float or null | `{prefix}D` / `{prefix}CD` | |
| `away_odds` | float or null | `{prefix}A` / `{prefix}CA` | |
| `all_three_priced` | boolean | derived | flags `INCOMPLETE_THREE_WAY_PRICE` per this row |
| `capture_timestamp_known` | boolean, always `false` | constant | Football-Data never provides this; this field exists so downstream code cannot silently forget the caveat |

### Known, explicit gap: `market_snapshot_odds_1x2`'s mandatory `captured_at`

The feature manifest's `market_snapshot_odds_1x2` row (required feature)
mandates a real `captured_at` timestamp and a derived `hours_to_kickoff`
(Correction 1's decision-horizon-matching control, spec sections 2-3, 5-7).
**Football-Data's `odds_prices` table above cannot supply this.** Its
`price_label` field only ever says "opening" or "closing" as the source
file's own static label — never an actual point-in-time capture. This is
not a bug in the proposed schema; it is the honest limit of what this
input source provides (see `data_pipeline/FEASIBILITY_DECISION.md`'s
"still needs another source" list). A real `market_snapshot_odds_1x2`
feature requires a genuinely timestamped live-odds feed captured at the
model's actual decision horizon — a separate acquisition problem this
feasibility PR does not solve, and the normalized schema must never
pretend `odds_prices.price_label == "opening_or_only"` satisfies that
manifest row's `captured_at` requirement.

### `team_form_inputs` (derived, not raw — named here only to show the join)

Rolling-window and Elo features (`*_rolling_goals_*_last_10`,
`*_elo_pre_match`) are computed FROM the `fixtures` table's chronological
sequence per team — they are not columns Football-Data itself provides.
This normalized schema stops at `fixtures`/`odds_prices`; the actual
rolling/Elo computation (with its own leakage-control unit tests per spec
section 7) is implementation-PR scope, not this feasibility PR's.

## Mapping to the feature manifest — what's covered vs. still open

| Manifest feature id | Covered by this normalized schema? | Notes |
|---|---|---|
| `home_team_elo_pre_match` / `away_team_elo_pre_match` | Computable FROM `fixtures`, not a raw column | needs the (not-yet-built) Elo computation |
| `home_team_rolling_goals_for_last_10` (and the other 3 rolling-goals ids) | Computable FROM `fixtures` | needs the (not-yet-built) rolling-window computation |
| `market_snapshot_odds_1x2` | **Not fully covered** — see "Known gap" above | needs a real timestamped decision-time odds feed; Football-Data alone cannot supply `captured_at` |
| `days_since_last_match_home` / `_away` | Computable FROM `fixtures` | straightforward date arithmetic once fixtures are sorted per team |
| `head_to_head_last_5_result_distribution` (optional) | Computable FROM `fixtures` | |
| `confirmed_starting_lineup_strength_delta` (optional) | **Not covered at all** | deferred per spec's resolved operator decision; no Football-Data column for this |
| `weather_conditions_pre_match` (optional) | **Not covered at all** | excluded per spec's resolved operator decision |
| `market_closing_odds_1x2_for_clv_analysis` (optional, eval-only) | Partially covered — `odds_prices` rows with `price_label == "closing"` | still lacks a true capture timestamp; usable for CLV only in the loose sense the spec already scopes it (comparing decision-time vs. closing LINES, not comparing exact capture instants) |
