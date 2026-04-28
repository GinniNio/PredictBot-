"""
PredictBot Backtest
-------------------
Runs the trained ProphitBet Random Forest model against the full 2024-25
Premier League season and calculates:
  - Match prediction accuracy
  - Edge detection performance (model prob vs bookmaker implied prob)
  - Simulated P&L if betting £10 on every edge > threshold

Run from your PredictBot folder:
    py -3.11 backtest.py
"""

import pickle
import sys
import numpy as np
import pandas as pd

# ── Paths ────────────────────────────────────────────────────────────────────
BASE = r"C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\ProphitBet-Soccer-Bets-Predictor"
DATA_PATH  = BASE + r"\storage\leagues\Premier-League-England-01\data\dataset.csv"
MODEL_PATH = BASE + r"\storage\leagues\Premier-League-England-01\models\Premier-League-England-01\classifier.pkl"

sys.path.insert(0, BASE)

# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading data...")
df = pd.read_csv(DATA_PATH)

# Keep only 2025 season (last full season)
season_df = df[df['Season'] == 2025].copy()
print(f"  {len(season_df)} matches found for 2025 season")

# Drop rows with missing odds or result
season_df = season_df.dropna(subset=['1', 'X', '2', 'Result'])
print(f"  {len(season_df)} matches with complete data")

# ── Load model ────────────────────────────────────────────────────────────────
print("Loading model...")
with open(MODEL_PATH, 'rb') as f:
    model = pickle.load(f)

# ── Prepare features (same columns ProphitBet drops) ─────────────────────────
NON_FEATURES = ['Date', 'Season', 'Home', 'Away', 'HG', 'AG', 'Result',
                'Result-U/O', 'HST', 'AST', 'HC', 'AC', 'Week']

X = season_df.drop(columns=[c for c in NON_FEATURES if c in season_df.columns], errors='ignore')
X = X.apply(pd.to_numeric, errors='coerce').fillna(0).to_numpy(dtype=np.float32)

# ── Predict ───────────────────────────────────────────────────────────────────
print("Running predictions...")
probs = model.predict_proba(X)          # shape: (n_matches, 3) → [Away, Draw, Home]
class_order = model.classes_            # e.g. ['A', 'D', 'H']

# Model encodes: 0=Home win, 1=Draw, 2=Away win
prob_H = probs[:, 0]
prob_D = probs[:, 1]
prob_A = probs[:, 2]

pred_numeric = np.argmax(probs, axis=1)
label_map    = {0: 'H', 1: 'D', 2: 'A'}
predicted    = np.array([label_map[p] for p in pred_numeric])
actual       = season_df['Result'].values

# ── Accuracy ──────────────────────────────────────────────────────────────────
correct = (predicted == actual).sum()
total   = len(actual)
accuracy = correct / total

print(f"\n{'='*55}")
print(f"  OVERALL ACCURACY: {correct}/{total} = {accuracy:.1%}")
print(f"{'='*55}")

# Per-outcome breakdown
for outcome, label in [('H','Home win'), ('D','Draw'), ('A','Away win')]:
    mask = actual == outcome
    if mask.sum() == 0:
        continue
    acc = (predicted[mask] == actual[mask]).sum() / mask.sum()
    print(f"  {label:<12}: {(predicted[mask]==actual[mask]).sum()}/{mask.sum()} = {acc:.1%}")

# ── Edge detection ────────────────────────────────────────────────────────────
odds_H = season_df['1'].astype(float).values
odds_D = season_df['X'].astype(float).values
odds_A = season_df['2'].astype(float).values

overround    = 1/odds_H + 1/odds_D + 1/odds_A
implied_H    = (1/odds_H) / overround
implied_D    = (1/odds_D) / overround
implied_A    = (1/odds_A) / overround

# Edge = model prob - bookmaker implied prob for the predicted outcome
edge = np.where(predicted == 'H', prob_H - implied_H,
       np.where(predicted == 'D', prob_D - implied_D,
                                   prob_A - implied_A))

# ── Simulate betting £10 on every edge above threshold ────────────────────────
STAKE       = 10
EDGE_THRESH = 0.05   # only bet when model sees >5% edge

print(f"\n{'='*55}")
print(f"  EDGE BETTING SIMULATION (threshold = {EDGE_THRESH:.0%}, £{STAKE}/bet)")
print(f"{'='*55}")

for thresh in [0.03, 0.05, 0.07, 0.10]:
    mask     = edge >= thresh
    n_bets   = mask.sum()
    if n_bets == 0:
        print(f"  >{thresh:.0%} edge: no bets triggered")
        continue

    wins     = (predicted[mask] == actual[mask]).sum()
    win_rate = wins / n_bets

    # Return on winning bets
    bet_odds = np.where(predicted[mask] == 'H', odds_H[mask],
               np.where(predicted[mask] == 'D', odds_D[mask],
                                                 odds_A[mask]))
    winnings  = np.where(predicted[mask] == actual[mask], (bet_odds - 1) * STAKE, -STAKE)
    total_pnl = winnings.sum()
    roi       = total_pnl / (n_bets * STAKE)

    print(f"  >{thresh:.0%} edge: {n_bets:>3} bets | {wins}/{n_bets} won ({win_rate:.1%}) | "
          f"P&L: £{total_pnl:+.0f} | ROI: {roi:+.1%}")

# ── Value bets detail ─────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  TOP 10 EDGE BETS (all season)")
print(f"{'='*55}")
print(f"  {'Date':<12} {'Match':<32} {'Pick':<5} {'Edge':>6}  {'Result':<6}")
print(f"  {'-'*65}")

top_idx = np.argsort(edge)[::-1][:10]
for i in top_idx:
    row    = season_df.iloc[i]
    match  = f"{row['Home']} v {row['Away']}"
    result = '✓' if predicted[i] == actual[i] else '✗'
    print(f"  {row['Date']:<12} {match:<32} {predicted[i]:<5} {edge[i]:>+.1%}  {actual[i]} {result}")

# ── Save full results to CSV ──────────────────────────────────────────────────
results_df = season_df[['Date', 'Home', 'Away', '1', 'X', '2', 'Result']].copy()
results_df['Predicted']   = predicted
results_df['Correct']     = predicted == actual
results_df['Prob_H']      = prob_H.round(3)
results_df['Prob_D']      = prob_D.round(3)
results_df['Prob_A']      = prob_A.round(3)
results_df['Implied_H']   = implied_H.round(3)
results_df['Implied_D']   = implied_D.round(3)
results_df['Implied_A']   = implied_A.round(3)
results_df['Edge']        = edge.round(3)
results_df['Value_Bet']   = edge >= EDGE_THRESH

out_path = r"C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\backtest_results_2025.csv"
results_df.to_csv(out_path, index=False)
print(f"\n  Full results saved to: backtest_results_2025.csv")
print(f"{'='*55}\n")
