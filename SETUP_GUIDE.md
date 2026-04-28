# PredictBot — Setup Guide

## The Stack (What You're Building)

```
football-data.co.uk (free data)
        ↓
ProphitBet — GUI predictions, 7 ML models, Excel export       ← START HERE
        ↓
msoczi pipeline — 354 engineered features + XGBoost           ← Week 2
        ↓
MatchOutcomeAI — calibrated probabilities                      ← Week 3
        ↓
Footy4.0 — edge detection vs bookmaker odds                    ← Week 3
```

---

## Week 1 — Get ProphitBet Running

### Step 1: Install Python

Download Python **3.11** from: https://www.python.org/downloads/

> **Important during install:** Check the box that says **"Add Python to PATH"**

Verify it worked — open Command Prompt (search "cmd") and type:
```
python --version
```
Should print `Python 3.11.x`

---

### Step 2: Install Git

Download from: https://git-scm.com/download/win

Install with all defaults. No changes needed.

---

### Step 3: Clone and Set Up ProphitBet

Open Command Prompt, paste these commands one at a time:

```bash
cd C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot

git clone https://github.com/kochlisGit/ProphitBet-Soccer-Bets-Predictor

cd ProphitBet-Soccer-Bets-Predictor

pip install -r requirements.txt
```

> This will take 2-5 minutes. Normal.

---

### Step 4: Launch the App

```bash
python main.py
```

A GUI window will open. From here you can:

1. **Select a league** — Premier League, La Liga, Bundesliga, Serie A, Ligue 1 all available
2. **Download fixtures + historical data** — one click, pulls from football-data.co.uk automatically
3. **Train models** — select which of the 7 algorithms to use (Random Forest + XGBoost recommended to start)
4. **Predict upcoming fixtures** — generates win/draw/loss probabilities
5. **Export to Excel** — saves predictions for all upcoming games

---

### Step 5: What the Predictions Mean

ProphitBet outputs:
- **Home Win / Draw / Away Win probabilities** (e.g. 62% / 22% / 16%)
- **Confidence score** per prediction

At this stage, write down (or export) any prediction where one outcome is above 60% confidence. That's your starting dataset for edge detection in Week 3.

---

### Troubleshooting

| Problem | Fix |
|---|---|
| `pip` not found | Close cmd, reopen, try `python -m pip install -r requirements.txt` |
| Missing module error | Run `pip install [module-name]` for whatever's missing |
| App won't open | Make sure you're inside the `ProphitBet-Soccer-Bets-Predictor` folder when running `python main.py` |

---

## Week 2 — Add the 354-Feature Pipeline (msoczi)

Once ProphitBet is running, add a second, more powerful model.

```bash
cd C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot

git clone https://github.com/msoczi/football_predictions

cd football_predictions

pip install -r requirements.txt
```

This repo uses XGBoost with 354 hand-crafted features. Same data source (football-data.co.uk), so no new accounts needed.

Run the notebook:
```bash
jupyter notebook
```

Open `football_predictions.ipynb` — follow the cells top to bottom.

**Why add this?** When ProphitBet AND msoczi agree on an outcome, your confidence is much higher. Model agreement = stronger signal.

---

## Week 3 — Edge Detection vs Bookmaker Odds

```bash
cd C:\Users\HUAWAI\Documents\Claude\Projects\PredictBot

git clone https://github.com/silasnevstad/Footy4.0
```

This compares your model probabilities against Vegas/bookmaker implied probabilities.

**The logic:**
- Your model says Team A wins: 65% probability
- Bookmaker implied probability (from odds): 52%
- Edge = 65% - 52% = **+13% edge** → this is a value bet

You don't need to actually bet. The edge percentage is what you publish/share.

---

## Leagues Covered (Free Tier, football-data.co.uk)

| League | Coverage |
|---|---|
| Premier League | ✅ Full history |
| La Liga | ✅ Full history |
| Bundesliga | ✅ Full history |
| Serie A | ✅ Full history |
| Ligue 1 | ✅ Full history |
| Championship, Eredivisie, Liga NOS | ✅ Also included |

---

## What to Track From Day 1

Keep a simple spreadsheet with:
- Date of prediction
- Match
- Your model's probability (home/draw/away)
- Bookmaker odds at time of prediction
- Actual result

After 50+ predictions you'll have real accuracy data to share publicly. This is your proof of concept.

---

## The Eventual Stack (Full Vision)

| Repo | What It Adds |
|---|---|
| ProphitBet | GUI predictions, 7 models |
| msoczi | 354-feature XGBoost |
| SoccerPredictor | LightGBM + Neural Net ensemble, FBRef data |
| MatchOutcomeAI | Calibrated probabilities |
| soccer_xg | Shot quality model (xG) |
| Footy4.0 | Edge detection vs bookmaker |
| football-analyze | Computer vision tracking from TV feeds |

You don't need all of these on day 1. Get ProphitBet working. Everything else layers on top.
