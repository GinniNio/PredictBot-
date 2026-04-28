# PredictBot Session Handoff — 2026-04-28

## What We Built

Starting from a list of 10 open-source football analytics repos, we built a complete football prediction and edge-detection pipeline from scratch. The system trains machine learning models on historical match data, compares model probabilities against bookmaker odds, and flags "value bets" where the model sees more probability than the market implies.

**GitHub repo:** https://github.com/GinniNio/PredictBot-

---

## Decisions Made

**Which repo to start with:** ProphitBet (kochlisGit) — chosen because it has a GUI, downloads data automatically, covers all major European leagues, and requires no custom code to get first predictions running. It became the backbone of the entire system.

**Edge threshold set to 10%:** Backtested the full 2024-25 Premier League season (338 matches). Results by threshold:
- >3% edge: -5.0% ROI
- >5% edge: -2.6% ROI
- >7% edge: +4.4% ROI
- >10% edge: +10.0% ROI (38 bets, £+38 on £380 staked)

10% was chosen as the floor. Higher threshold = fewer but more profitable bets.

**Random Forest as primary model:** ProphitBet trains 7 models. RF was chosen first (default, well-understood). XGBoost was also trained for comparison:
- RF eval accuracy: 52.8% (cross-val), 47.9% (2025 season backtest)
- XGBoost eval accuracy: 51.6%

**5 leagues:** Premier League, La Liga, Bundesliga, Serie A, Ligue 1. All trained with identical RF configs and the same 27 rolling stats features.

**Python 3.11 specifically:** User has Python 3.14.2 installed (default). 3.14 breaks scipy/sklearn. 3.11.9 was already installed and used throughout with `py -3.11`.

**Data source:** football-data.co.uk free tier — covers all 5 leagues, no API key required, includes historical odds.

---

## What's Working

- ProphitBet GUI launches and runs (`py -3.11 app.py` from inside the ProphitBet folder)
- 5 trained models saved to ProphitBet's storage system
- `weekly_picks.py` — fetches live fixtures from football-data.co.uk, runs predictions, logs picks above 10% edge to `picks_tracker.csv`
- `backtest.py` — runs model against any historical season, outputs accuracy + simulated P&L
- `train_leagues.py` — CLI script to download data and train models for all 4 non-PL leagues without the GUI
- Scheduled task set up to run `weekly_picks.py` every Friday at 9am automatically
- GitHub repo live and up to date

---

## Known Issues and Limitations

**Model never predicts draws.** The Random Forest gets 0/90 draws correct in the 2025 season backtest. It forces every draw into a home or away prediction. This is a class imbalance problem. Fix: retrain with stronger class weighting, or use a separate draw-detection model. Not addressed yet.

**ProphitBet Fixtures Dialog has an odds error.** When using the GUI Predict → Fixtures screen, it throws "Invalid Odds — found invalid odd values at row 0" for fixtures with missing bookmaker odds. This blocks predictions for all rows, not just the ones with missing data. The `weekly_picks.py` script bypasses this entirely by going directly to the model.

**football-data.co.uk fixtures file has a BOM character.** The column is `ï»¿Div` not `Div`. Fixed in `weekly_picks.py` with `encoding='utf-8-sig'` and column stripping.

**ProphitBet added as embedded git repo.** When we ran `git add .`, Git warned that ProphitBet-Soccer-Bets-Predictor is a nested git repo. It was committed as a gitlink (empty folder on GitHub), not as a submodule. The actual ProphitBet code only exists locally. This is fine for now — the scripts reference it by local path — but if anyone else clones PredictBot they won't get ProphitBet automatically.

**End of season timing.** Premier League ends May 24 2026. The fixtures file from football-data.co.uk only shows the most imminent matches (2-3 days out). Friday runs will catch that week's fixtures correctly. Bundesliga ends earlier (around May 17). All leagues restart August 2026.

---

## File Structure

```
C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot\
├── README.md                        — GitHub landing page with full stack description
├── SETUP_GUIDE.md                   — Step-by-step setup for beginners
├── weekly_picks.py                  — Main script: run every Friday
├── backtest.py                      — Backtest model against historical season
├── train_leagues.py                 — Download data + train models for all leagues
├── picks_tracker.csv                — Running log of picks + results (fill in after matches)
├── backtest_results_2025.csv        — Full 2024-25 PL season backtest output
├── docs/handoffs/                   — Session handoff documents
└── ProphitBet-Soccer-Bets-Predictor/    — Cloned ProphitBet repo (local only)
    └── storage/leagues/
        ├── Premier-League-England-01/
        ├── La-Liga-Spain-01/
        ├── Bundesliga-1-Germany-01/
        ├── Serie-A-Italy-01/
        └── Ligue-1-France-01/
```

---

## Model Accuracy Summary

| League | Eval Accuracy | F1 | Matches Trained On |
|---|---|---|---|
| Premier League | 52.8% (cross-val) | 0.390 | 6,650 |
| La Liga | 51.6% | 0.383 | 6,344 |
| Bundesliga | 50.9% | 0.372 | 5,120 |
| Serie A | 52.6% | 0.404 | 6,348 |
| Ligue 1 | 51.4% | 0.370 | 6,102 |

All models use: 100 trees, class_weight=balanced, calibrated probabilities, 27 rolling stats features (HW, AW, HL, AL, HGF, AGF, HAGF, HGA, AGA, HAGA, HGD, AGD, HAGD, HWGD, AWGD, HAWGD, HLGD, ALGD, HALGD, HW%, HL%, AW%, AL%, HSTF, ASTF, HCF, ACF), match_history_window=3, goal_diff_margin=2.

---

## Work That Remains

**Immediate (this season, before May 24):**
- Run `weekly_picks.py` each Friday and fill in results in `picks_tracker.csv` after the weekend
- After 4-5 weeks, review tracker to see if forward-looking accuracy matches backtest

**Phase 2 (before August new season):**
- Fix the draw prediction problem — retrain with stronger class weighting or add a secondary binary draw/no-draw classifier
- Add XGBoost as second model and only log picks where both RF and XGBoost agree (reduces bet count but raises confidence)
- Add MatchOutcomeAI for probability calibration (ratloop/MatchOutcomeAI)
- Add msoczi 354-feature pipeline as third model

**Phase 3 (once audience is established):**
- Set up Substack or Discord for sharing picks publicly
- Add football-analyze computer vision layer for proprietary tracking data
- Consider SoccerPredictor (LightGBM + Neural Net + FBRef scraper) as fourth model

**Infrastructure:**
- Fix ProphitBet as proper git submodule if others need to clone the repo
- Add `.gitignore` to exclude large model pickle files and CSV data from the repo

---

## How to Resume

1. Open Command Prompt
2. Activate the environment: nothing to activate, just use `py -3.11`
3. Navigate to project: `cd C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot`
4. Run picks: `py -3.11 weekly_picks.py`
5. Open ProphitBet GUI (for manual exploration): `cd ProphitBet-Soccer-Bets-Predictor && py -3.11 app.py`

The scheduled task runs automatically every Friday at 9am — no manual action needed for the weekly picks.
