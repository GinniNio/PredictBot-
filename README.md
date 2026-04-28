# PredictBot ⚽

An open-source football analytics pipeline that stacks 10 free repos into a single prediction and edge-detection system. No paid subscriptions. No proprietary data. Covers all major European leagues.

> Built as an alternative to €30-50/month prediction platforms and the kind of analytics infrastructure that costs clubs £100k/year.

---

## What It Does

- **Predicts match outcomes** across Premier League, La Liga, Bundesliga, Serie A, and Ligue 1
- **Runs multiple models** and flags when they agree (higher confidence)
- **Detects value bets** by comparing model probability vs bookmaker implied probability
- **Explains every prediction** with SHAP feature importance
- **Tracks accuracy** over time with a simple export to Excel

---

## The Stack

| Repo | Role | Replaces |
|---|---|---|
| [ProphitBet](https://github.com/kochlisGit/ProphitBet-Soccer-Bets-Predictor) | GUI app — 7 ML models, auto data download, Excel export | Paid prediction platforms ($30/mo) |
| [msoczi/football_predictions](https://github.com/msoczi/football_predictions) | XGBoost with 354 hand-crafted features | Manual feature engineering |
| [SoccerPredictor](https://github.com/ronyka77/SoccerPredictor_byRichardSzita) | LightGBM + XGBoost + NN + Random Forest ensemble, FBRef scraper | Entire quant sports desk |
| [MatchOutcomeAI](https://github.com/ratloop/MatchOutcomeAI) | Calibrated probabilities that match real bookmaker confidence | Bookmaker calibration tools |
| [Footy4.0](https://github.com/silasnevstad/Footy4.0) | Edge detection: model probability vs Vegas implied probability | Value bet scanners ($50/mo) |
| [soccer_xg (KU Leuven)](https://github.com/ML-KULeuven/soccer_xg) | Academic-grade xG model (LogReg + XGBoost) | StatsBomb xG subscription |
| [Football-xG-Predictor](https://github.com/bsobkowicz1096/Football-xG-Predictor) | SHAP explanations on every xG prediction | xG analytics dashboards |
| [FootballMatchPredictionPoisson](https://github.com/Amar0302/FootballMatchPredictionPoisson) | Poisson goal simulation — beats ML on draw prediction | Basic prediction models |
| [Predict_EPL_Matches](https://github.com/douglaspsteen/Predict_EPL_Matches) | XGBoost + AdaBoost + SVM on EPL data | Premier League prediction services |
| [football-analyze](https://github.com/zostaff/football-analyze) | YOLO tracking from any broadcast — speed, distance, possession, no sensors | Hawkeye / Second Spectrum |

---

## Pipeline Architecture

```
football-data.co.uk (free)  +  FBRef (scraped automatically)
                ↓
        Feature Engineering
        msoczi: 354 features
                ↓
         Prediction Layer
   ┌─────────────────────────┐
   │  ProphitBet (7 models)  │
   │  SoccerPredictor        │  → ensemble vote
   │  Poisson simulation     │
   └─────────────────────────┘
                ↓
        Probability Calibration
           MatchOutcomeAI
                ↓
          Edge Detection
   model probability vs bookmaker implied
              Footy4.0
                ↓
     Output: value bets + confidence
```

---

## Quick Start

### Requirements
- Python 3.11 — [download](https://www.python.org/downloads/)
- Git — [download](https://git-scm.com/download/win)

### Step 1 — Clone this repo

```bash
git clone https://github.com/GinniNio/PredictBot-
cd PredictBot-
```

### Step 2 — Get ProphitBet running (GUI, no code required)

```bash
git clone https://github.com/kochlisGit/ProphitBet-Soccer-Bets-Predictor
cd ProphitBet-Soccer-Bets-Predictor
pip install -r requirements.txt
python main.py
```

A GUI window opens. Select a league → download data → train models → predict upcoming fixtures → export to Excel.

### Step 3 — Add the 354-feature XGBoost model

```bash
git clone https://github.com/msoczi/football_predictions
cd football_predictions
pip install -r requirements.txt
jupyter notebook
```

Open `football_predictions.ipynb` and run all cells.

---

## Data Sources (All Free)

| Source | Used By | Cost |
|---|---|---|
| football-data.co.uk | ProphitBet, msoczi, Footy4.0 | Free (Tier 1) |
| FBRef | SoccerPredictor (auto-scraped) | Free |
| StatsBomb Open Data | soccer_xg, Football-xG-Predictor | Free |

---

## Leagues Covered

Premier League · La Liga · Bundesliga · Serie A · Ligue 1 · Championship · Eredivisie · Liga Portugal

---

## Accuracy — Being Honest

Match outcome prediction in football hits around **54-58% accuracy** on win/loss/draw. That's the ceiling for the sport, not a limitation of these models. Football is high variance.

The value is not in prediction accuracy — it's in **edge percentage**: when your calibrated model probability is consistently higher than the bookmaker's implied probability, that's exploitable over a large sample. This is what Footy4.0 measures.

---

## Roadmap

- [x] Setup guide and repo structure
- [ ] ProphitBet integration (Week 1)
- [ ] msoczi 354-feature pipeline (Week 2)
- [ ] MatchOutcomeAI calibration (Week 3)
- [ ] Footy4.0 edge detection (Week 3)
- [ ] SoccerPredictor ensemble (Week 4)
- [ ] Unified prediction output (single CSV across all models)
- [ ] Weekly edge report (newsletter / Discord bot)
- [ ] football-analyze computer vision layer (Phase 3)

---

## Cost

$0 to build and run locally. ~$5-10/month if you host on a VPS.

---

## Credits

All prediction and analytics work is built on top of the original open-source repos listed above. This project integrates and extends them — all original authors retain credit for their work.

---

## License

MIT
