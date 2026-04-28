# PredictBot - League Trainer
# Downloads data and trains a Random Forest model for each target league.
# No GUI required.
#
# Usage:
#   cd C:/Users/HUAWAI/Documents/Claude/Projects/PredictBot
#   py -3.11 train_leagues.py

import sys
import os
import time

# Must run from inside ProphitBet folder
PROPHITBET_DIR = os.path.dirname(os.path.abspath(__file__))
PROPHITBET_DIR = os.path.join(PROPHITBET_DIR, 'ProphitBet-Soccer-Bets-Predictor')
os.chdir(PROPHITBET_DIR)
sys.path.insert(0, PROPHITBET_DIR)

from src.database.league import LeagueDatabase
from src.database.model import ModelDatabase
from src.network.leagues.league import League
from src.models.classifiers.randomforest import RandomForest
from src.preprocessing.utils.target import TargetType

# ── Leagues to train ──────────────────────────────────────────────────────────
# Each entry: (country, name, start_year, category, url, fixture, league_id)
# Same stats config used by the Premier League model
STATS_COLUMNS = [
    'HW','AW','HL','AL','HGF','AGF','HAGF','HGA','AGA','HAGA',
    'HGD','AGD','HAGD','HWGD','AWGD','HAWGD','HLGD','ALGD','HALGD',
    'HW%','HL%','AW%','AL%','HSTF','ASTF','HCF','ACF'
]
MATCH_HISTORY_WINDOW = 3
GOAL_DIFF_MARGIN     = 2

TARGET_LEAGUES = [
    {
        'country':             'Spain',
        'name':                'La-Liga',
        'start_year':          2005,
        'category':            'main',
        'url':                 'https://www.football-data.co.uk/mmz4281/{}/SP1.csv',
        'fixture':             'https://footystats.org/spain/la-liga/fixtures',
        'league_id':           'La-Liga-Spain-01',
        'stats_columns':       STATS_COLUMNS,
        'match_history_window': MATCH_HISTORY_WINDOW,
        'goal_diff_margin':    GOAL_DIFF_MARGIN,
    },
    {
        'country':             'Germany',
        'name':                'Bundesliga-1',
        'start_year':          2005,
        'category':            'main',
        'url':                 'https://www.football-data.co.uk/mmz4281/{}/D1.csv',
        'fixture':             'https://footystats.org/germany/bundesliga/fixtures',
        'league_id':           'Bundesliga-1-Germany-01',
        'stats_columns':       STATS_COLUMNS,
        'match_history_window': MATCH_HISTORY_WINDOW,
        'goal_diff_margin':    GOAL_DIFF_MARGIN,
    },
    {
        'country':             'Italy',
        'name':                'Serie-A',
        'start_year':          2005,
        'category':            'main',
        'url':                 'https://www.football-data.co.uk/mmz4281/{}/I1.csv',
        'fixture':             'https://footystats.org/italy/serie-a/fixtures',
        'league_id':           'Serie-A-Italy-01',
        'stats_columns':       STATS_COLUMNS,
        'match_history_window': MATCH_HISTORY_WINDOW,
        'goal_diff_margin':    GOAL_DIFF_MARGIN,
    },
    {
        'country':             'France',
        'name':                'Ligue-1',
        'start_year':          2005,
        'category':            'main',
        'url':                 'https://www.football-data.co.uk/mmz4281/{}/F1.csv',
        'fixture':             'https://footystats.org/france/ligue-1/fixtures',
        'league_id':           'Ligue-1-France-01',
        'stats_columns':       STATS_COLUMNS,
        'match_history_window': MATCH_HISTORY_WINDOW,
        'goal_diff_margin':    GOAL_DIFF_MARGIN,
    },
]

EVAL_RATIO = 0.20   # 20% of data used for evaluation

# ── Main ──────────────────────────────────────────────────────────────────────
def train_league(league_cfg):
    league_id = league_cfg['league_id']
    print(f"\n{'='*60}")
    print(f"  {league_id}")
    print(f"{'='*60}")

    league = League(**{k: v for k, v in league_cfg.items() if k != 'league_id'},
                    league_id=league_id)

    league_db = LeagueDatabase()

    # ── Download data ─────────────────────────────────────────────────────────
    if league_db.league_exists(league_id):
        print("  Data already exists — updating...")
        df = league_db.update_league(league_id=league_id)
    else:
        print("  Downloading data (this takes 1-2 minutes)...")
        df = league_db.create_league(league=league)

    if df is None:
        print("  ERROR: Download failed. Check internet connection.")
        return False

    df = df.dropna(subset=['Result', '1', 'X', '2'])
    print(f"  Downloaded {len(df)} matches.")

    # ── Train / Eval split ────────────────────────────────────────────────────
    n_eval  = int(len(df) * EVAL_RATIO)
    # df is sorted descending (newest first), eval = most recent matches
    eval_df  = df.iloc[:n_eval].copy()
    train_df = df.iloc[n_eval:].copy()
    print(f"  Train: {len(train_df)} | Eval: {len(eval_df)}")

    # ── Build and train model ─────────────────────────────────────────────────
    model_id = league_id  # same as league id for simplicity

    model = RandomForest(
        league_id=league_id,
        model_id=model_id,
        target_type=TargetType.RESULT,
        calibrate_probabilities=True,
        class_weight=True,
        n_estimators=100,
    )

    print("  Training Random Forest...")
    t0 = time.time()
    results_df = model.fit(train_df=train_df, eval_df=eval_df)
    elapsed = time.time() - t0

    # ── Print accuracy ────────────────────────────────────────────────────────
    eval_row = results_df[results_df['data'] == 'eval'].iloc[0]
    train_row = results_df[results_df['data'] == 'train'].iloc[0]
    print(f"  Done in {elapsed:.0f}s")
    print(f"  Train accuracy : {train_row['Accuracy']:.1%}")
    print(f"  Eval accuracy  : {eval_row['Accuracy']:.1%}")
    print(f"  Eval F1        : {eval_row['F1']:.3f}")

    # ── Save model ────────────────────────────────────────────────────────────
    model_db = ModelDatabase(league_id=league_id)
    model_config = {
        'cls':                   RandomForest,
        'league_id':             league_id,
        'model_id':              model_id,
        'target_type':           TargetType.RESULT,
        'calibrate_probabilities': True,
        'class_weight':          True,
        'n_estimators':          100,
    }
    model_db.save_model(model=model, model_config=model_config)
    print(f"  Model saved: {model_id}")
    return True


def main():
    print("\nPredictBot — League Trainer")
    print(f"Training {len(TARGET_LEAGUES)} leagues...\n")

    results = {}
    for cfg in TARGET_LEAGUES:
        ok = train_league(cfg)
        results[cfg['league_id']] = 'OK' if ok else 'FAILED'

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for league_id, status in results.items():
        print(f"  {league_id:<35} {status}")

    print(f"\n  All done. Update weekly_picks.py LEAGUES dict with these IDs.")
    print()


if __name__ == '__main__':
    main()
