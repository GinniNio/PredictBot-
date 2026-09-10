# Football-Data feasibility report

Every row below is labeled `LIVE_SOURCE_VALIDATED` (a real football-data.co.uk download, run through this pipeline in this environment) or `FIXTURE_ONLY_VALIDATED` (only a hand-crafted test fixture was exercised, or the real download failed/was blocked — this label never implies real-world source compatibility or dataset usability for that league-season). See `data_pipeline/FEASIBILITY_DECISION.md`.

LIVE_SOURCE_VALIDATED rows: 0
FIXTURE_ONLY_VALIDATED rows: 6

## League x season x label summary

| League | Season | Label | Total rows | Usable | Rejected |
|---|---|---|---:|---:|---:|
| English Premier League (fixture proxy) (E0) | fixture:clean_modern_season | FIXTURE_ONLY_VALIDATED | 5 | 5 | 0 |
| English Premier League (fixture proxy) (E0) | fixture:duplicate_fixture | FIXTURE_ONLY_VALIDATED | 4 | 2 | 2 |
| English Premier League (fixture proxy) (E0) | fixture:missing_odds | FIXTURE_ONLY_VALIDATED | 3 | 2 | 1 |
| English Premier League (fixture proxy) (E0) | fixture:incomplete_three_way | FIXTURE_ONLY_VALIDATED | 3 | 1 | 2 |
| English Premier League (fixture proxy) (E0) | fixture:old_date_format_season | FIXTURE_ONLY_VALIDATED | 3 | 3 | 0 |
| English Premier League (fixture proxy) (E0) | fixture:column_drift_2000s_season | FIXTURE_ONLY_VALIDATED | 3 | 3 | 0 |

## Per-file detail

### English Premier League (fixture proxy) (E0) — fixture:clean_modern_season — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/clean_modern_season.csv`
- total_rows: 5, usable: 5, rejected: 0
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 0, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 0, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 0, 'INCOMPLETE_THREE_WAY_PRICE': 0}`
- date_formats_observed: `{'%d/%m/%Y': 5}`
- core_columns_absent: `[]`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):
  - Bet365 (B365, opening_or_only): all_three_present=True, capture_timestamp_known=False
  - Pinnacle (PS, opening_or_only, PINNACLE — treat cautiously, never auto-authoritative): all_three_present=True, capture_timestamp_known=False
  - Pinnacle (PS, closing, PINNACLE — treat cautiously, never auto-authoritative): all_three_present=True, capture_timestamp_known=False

### English Premier League (fixture proxy) (E0) — fixture:duplicate_fixture — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/duplicate_fixture.csv`
- total_rows: 4, usable: 2, rejected: 2
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 2, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 1, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 0, 'INCOMPLETE_THREE_WAY_PRICE': 0}`
- date_formats_observed: `{'%d/%m/%Y': 4}`
- core_columns_absent: `[]`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):
  - Bet365 (B365, opening_or_only): all_three_present=True, capture_timestamp_known=False

### English Premier League (fixture proxy) (E0) — fixture:missing_odds — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/missing_odds.csv`
- total_rows: 3, usable: 2, rejected: 1
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 0, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 0, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 1, 'INCOMPLETE_THREE_WAY_PRICE': 0}`
- date_formats_observed: `{'%d/%m/%Y': 3}`
- core_columns_absent: `[]`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):
  - Bet365 (B365, opening_or_only): all_three_present=True, capture_timestamp_known=False

### English Premier League (fixture proxy) (E0) — fixture:incomplete_three_way — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/incomplete_three_way.csv`
- total_rows: 3, usable: 1, rejected: 2
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 0, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 0, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 0, 'INCOMPLETE_THREE_WAY_PRICE': 2}`
- date_formats_observed: `{'%d/%m/%Y': 3}`
- core_columns_absent: `[]`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):
  - Bet365 (B365, opening_or_only): all_three_present=True, capture_timestamp_known=False

### English Premier League (fixture proxy) (E0) — fixture:old_date_format_season — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/old_date_format_season.csv`
- total_rows: 3, usable: 3, rejected: 0
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 0, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 0, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 0, 'INCOMPLETE_THREE_WAY_PRICE': 0}`
- date_formats_observed: `{'%d/%m/%y': 3}`
- core_columns_absent: `['kickoff_time', 'half_time_home_goals', 'half_time_away_goals', 'half_time_result']`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):

### English Premier League (fixture proxy) (E0) — fixture:column_drift_2000s_season — `FIXTURE_ONLY_VALIDATED`
- file: `/home/user/PredictBot-/tests/fixtures/football_data/column_drift_2000s_season.csv`
- total_rows: 3, usable: 3, rejected: 0
- rejection_reason_counts: `{'DUPLICATE_FIXTURE': 0, 'MISSING_TEAM_IDENTITY': 0, 'MISSING_RESULT': 0, 'INVALID_RESULT_LABEL': 0, 'IMPOSSIBLE_SCORE': 0, 'CONFLICTING_FIXTURE': 0, 'UNPARSEABLE_DATE': 0, 'MISSING_OR_INVALID_ODDS': 0, 'INCOMPLETE_THREE_WAY_PRICE': 0}`
- date_formats_observed: `{'%d/%m/%Y': 3}`
- core_columns_absent: `['kickoff_time']`
- odds columns found (bookmaker, variant, all-3-present, capture_timestamp_known):
  - Bet365 (B365, opening_or_only): all_three_present=True, capture_timestamp_known=False
  - Gamebookers (GB, opening_or_only): all_three_present=True, capture_timestamp_known=False
  - Stan James (SJ, opening_or_only): all_three_present=True, capture_timestamp_known=False

## Column drift between seasons
- E0: fixture:clean_modern_season -> fixture:duplicate_fixture: dropped=['PSA', 'PSCA', 'PSCD', 'PSCH', 'PSD', 'PSH'], added=[]
- E0: fixture:incomplete_three_way -> fixture:old_date_format_season: dropped=['B365A', 'B365D', 'B365H', 'HTAG', 'HTHG', 'HTR', 'Time'], added=[]
- E0: fixture:old_date_format_season -> fixture:column_drift_2000s_season: dropped=[], added=['B365A', 'B365D', 'B365H', 'GBA', 'GBD', 'GBH', 'HTAG', 'HTHG', 'HTR', 'SJA', 'SJD', 'SJH']
