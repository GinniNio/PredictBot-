"""
PredictBot Weekly Picks
-----------------------
Fetches live upcoming fixtures from football-data.co.uk,
builds features using ProphitBet's own logic,
runs predictions, flags edges above threshold,
and logs picks to a running tracker CSV.

Usage:
    py -3.11 weekly_picks.py

Output:
    - Prints picks to terminal
    - Appends new picks to: picks_tracker.csv
"""

import sys
import os
import csv
import pickle
import numpy as np
import pandas as pd
import requests
from datetime import date, timedelta
from io import StringIO

# ── Config ────────────────────────────────────────────────────────────────────
BASE         = r"C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\ProphitBet-Soccer-Bets-Predictor"
TRACKER_PATH = r"C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\picks_tracker.csv"
EDGE_THRESH  = 0.10
STAKE        = 10

# football-data.co.uk division codes → maps to league data on disk
LEAGUES = {
    "E0": {
        "name":  "Premier League",
        "data":  BASE + r"\storage\leagues\Premier-League-England-01\data\dataset.csv",
        "model": BASE + r"\storage\leagues\Premier-League-England-01\models\Premier-League-England-01\classifier.pkl",
    },
    "SP1": {
        "name":  "La Liga",
        "data":  BASE + r"\storage\leagues\La-Liga-Spain-01\data\dataset.csv",
        "model": BASE + r"\storage\leagues\La-Liga-Spain-01\models\La-Liga-Spain-01\classifier.pkl",
    },
    "D1": {
        "name":  "Bundesliga",
        "data":  BASE + r"\storage\leagues\Bundesliga-1-Germany-01\data\dataset.csv",
        "model": BASE + r"\storage\leagues\Bundesliga-1-Germany-01\models\Bundesliga-1-Germany-01\classifier.pkl",
    },
    "I1": {
        "name":  "Serie A",
        "data":  BASE + r"\storage\leagues\Serie-A-Italy-01\data\dataset.csv",
        "model": BASE + r"\storage\leagues\Serie-A-Italy-01\models\Serie-A-Italy-01\classifier.pkl",
    },
    "F1": {
        "name":  "Ligue 1",
        "data":  BASE + r"\storage\leagues\Ligue-1-France-01\data\dataset.csv",
        "model": BASE + r"\storage\leagues\Ligue-1-France-01\models\Ligue-1-France-01\classifier.pkl",
    },
}

FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
NON_FEATURES = ['Date', 'Season', 'Home', 'Away', 'HG', 'AG', 'Result',
                'Result-U/O', 'HST', 'AST', 'HC', 'AC', 'Week']

sys.path.insert(0, BASE)
from src.preprocessing.utils.inputs import construct_inputs_by_teams

# ── Helpers ───────────────────────────────────────────────────────────────────
def fetch_fixtures():
    """Download upcoming fixtures from football-data.co.uk"""
    print("  Fetching live fixtures from football-data.co.uk...")
    try:
        r = requests.get(FIXTURES_URL, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(StringIO(r.text), encoding='utf-8-sig')
        # Strip BOM from column names if present
        df.columns = [c.lstrip('﻿').strip() for c in df.columns]
        # Rename team columns
        df = df.rename(columns={'HomeTeam': 'Home', 'AwayTeam': 'Away'})
        print(f"  Got {len(df)} upcoming fixtures across all leagues.")
        print(f"  Divisions: {sorted(df['Div'].unique().tolist())}")
        return df
    except Exception as e:
        print(f"  ERROR fetching fixtures: {e}")
        return None

def load_model(path):
    with open(path, 'rb') as f:
        return pickle.load(f)

def ensure_tracker():
    if not os.path.exists(TRACKER_PATH):
        with open(TRACKER_PATH, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Date', 'League', 'Home', 'Away',
                'Pick', 'Odds', 'Edge',
                'Prob_H', 'Prob_D', 'Prob_A',
                'Result', 'Correct', 'PnL', 'RunningPnL'
            ])

def already_logged(date_str, home, away):
    """Avoid duplicate picks in tracker"""
    if not os.path.exists(TRACKER_PATH):
        return False
    with open(TRACKER_PATH) as f:
        for row in csv.DictReader(f):
            if row['Date'] == date_str and row['Home'] == home and row['Away'] == away:
                return True
    return False

def running_pnl():
    if not os.path.exists(TRACKER_PATH):
        return 0
    total = 0
    with open(TRACKER_PATH) as f:
        for row in csv.DictReader(f):
            try:
                total += float(row['PnL'])
            except (ValueError, KeyError):
                pass
    return total

def append_pick(row_data):
    with open(TRACKER_PATH, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row_data)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ensure_tracker()
    today = date.today()

    print(f"\n{'='*62}")
    print(f"  PREDICTBOT WEEKLY PICKS — {today}")
    print(f"  Edge threshold: {EDGE_THRESH:.0%} | Stake: £{STAKE}/bet")
    print(f"{'='*62}\n")

    # Fetch live fixtures
    fixtures_df = fetch_fixtures()
    if fixtures_df is None:
        print("  Could not fetch fixtures. Check internet connection.")
        return

    total_picks = 0

    for div_code, league in LEAGUES.items():
        league_name = league['name']
        print(f"  [{league_name}]")

        # Filter to this division
        league_fixtures = fixtures_df[fixtures_df['Div'] == div_code].copy()
        if league_fixtures.empty:
            print(f"    No upcoming fixtures found for {div_code}.\n")
            continue

        # Load historical data and model
        hist_df = pd.read_csv(league['data'])
        # Must be sorted descending for construct_inputs_by_teams
        hist_df = hist_df.sort_values('Date', ascending=False).reset_index(drop=True)
        model   = load_model(league['model'])

        # Map odds columns — football-data uses B365H/D/A or BbAvH/D/A
        odds_map = {}
        for h_col in ['B365H', 'BbAvH', 'AvgH', 'WHH']:
            if h_col in league_fixtures.columns:
                odds_map['1'] = h_col
                break
        for d_col in ['B365D', 'BbAvD', 'AvgD', 'WHD']:
            if d_col in league_fixtures.columns:
                odds_map['X'] = d_col
                break
        for a_col in ['B365A', 'BbAvA', 'AvgA', 'WHA']:
            if a_col in league_fixtures.columns:
                odds_map['2'] = a_col
                break

        if len(odds_map) < 3:
            print(f"    Could not find odds columns. Available: {list(league_fixtures.columns)}")
            continue

        picks_this_week = []
        known_teams = set(hist_df['Home'].unique()) | set(hist_df['Away'].unique())

        for _, fix in league_fixtures.iterrows():
            home = str(fix.get('Home', '')).strip()
            away = str(fix.get('Away', '')).strip()
            date_str = str(fix.get('Date', '')).strip()

            # Skip if teams not in our historical data
            if home not in known_teams or away not in known_teams:
                continue

            # Skip if already logged
            if already_logged(date_str, home, away):
                continue

            try:
                o1 = float(fix[odds_map['1']])
                ox = float(fix[odds_map['X']])
                o2 = float(fix[odds_map['2']])
            except (ValueError, TypeError, KeyError):
                continue

            if any(np.isnan([o1, ox, o2])) or any(v <= 1.0 for v in [o1, ox, o2]):
                continue

            # Build feature vector using ProphitBet's own method
            try:
                match_df = pd.DataFrame({
                    'Date': [date_str],
                    'Home': [home],
                    'Away': [away],
                    '1':   [o1],
                    'X':   [ox],
                    '2':   [o2]
                })
                match_df = construct_inputs_by_teams(df=hist_df, match_df=match_df)
            except Exception as e:
                continue

            # Predict
            feat_cols = [c for c in match_df.columns if c not in NON_FEATURES]
            x = match_df[feat_cols].apply(pd.to_numeric, errors='coerce').fillna(0).to_numpy(dtype=np.float32)

            try:
                probs = model.predict_proba(x)[0]
            except Exception:
                continue

            prob_h, prob_d, prob_a = probs[0], probs[1], probs[2]
            pred_idx  = int(np.argmax(probs))
            pred      = ['H', 'D', 'A'][pred_idx]
            pred_prob = probs[pred_idx]
            odds_list = [o1, ox, o2]
            implied   = [(1/o) / (1/o1+1/ox+1/o2) for o in odds_list]
            edge      = pred_prob - implied[pred_idx]
            bet_odds  = odds_list[pred_idx]

            if edge >= EDGE_THRESH:
                picks_this_week.append({
                    'date': date_str, 'home': home, 'away': away,
                    'pick': pred, 'odds': round(bet_odds, 2),
                    'edge': round(edge, 4),
                    'prob_h': round(prob_h, 3),
                    'prob_d': round(prob_d, 3),
                    'prob_a': round(prob_a, 3),
                })

        if not picks_this_week:
            print(f"    No picks above {EDGE_THRESH:.0%} edge this week.\n")
            continue

        picks_this_week.sort(key=lambda x: x['edge'], reverse=True)

        print(f"    {'Date':<12} {'Match':<32} {'Pick':<5} {'Odds':>6} {'Edge':>7}")
        print(f"    {'-'*62}")

        for p in picks_this_week:
            match = f"{p['home']} v {p['away']}"
            print(f"    {p['date']:<12} {match:<32} {p['pick']:<5} {p['odds']:>6.2f} {p['edge']:>+.1%}")
            append_pick([
                p['date'], league_name, p['home'], p['away'],
                p['pick'], p['odds'], p['edge'],
                p['prob_h'], p['prob_d'], p['prob_a'],
                '', '', '', ''
            ])
            total_picks += 1

        print()

    current_pnl = running_pnl()
    print(f"{'='*62}")
    print(f"  Picks logged this run : {total_picks}")
    print(f"  Running P&L to date   : £{current_pnl:+.0f}")
    print(f"  Tracker saved to      : picks_tracker.csv")
    print(f"{'='*62}")
    print()
    print("  Fill in Result/Correct/PnL columns after each match.")
    print()

if __name__ == '__main__':
    main()
