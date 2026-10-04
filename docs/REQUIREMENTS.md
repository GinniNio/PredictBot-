# PredictBot requirements (agreed 2026-10-04)

PredictBot should make the operator's existing betting workflow faster, keep
a reliable record, and use that record to evaluate and improve predictions.
Routine use should need no manual data cleanup, ledger maintenance or model
administration.

Calculations and storage are local. Export to an AI chat is deliberate, and
sync of the data folder (OneDrive / Google Drive) is optional.

## Daily workflow

| Step | Operator | App |
|---|---|---|
| 1. Capture | Extract all-sports fixtures and odds from Bet9ja. | Read the captures, remove duplicate coverage, report missing or unreadable information. |
| 2. Prepare research | Open the app, export the research pack. | Calculate market probabilities, run supported models, include relevant history. |
| 3. Research | Give the pack to Claude, ChatGPT or Gemini for online research and recommendations. | Supply clear instructions and a consistent return format. |
| 4. Save recommendations | Paste or import the chat's response. | Match picks to captured fixtures and markets, keep sources and reasoning, save predictions before results occur. |
| 5. Bet | Choose and place bets on Bet9ja. | Keep recommendations separate from actual wagers. |
| 6. Capture history | Extract open and settled bets as usual. | Import stakes, accepted odds, returns and outcomes; match them to saved recommendations where possible. |
| 7. Improve | Read a short performance summary. | Evaluate predictions and betting results, identify supported patterns, test proposed model changes against later outcomes. |

## First release must

- Accept the all-sports capture files the operator already produces, with clear coverage reporting.
- Run supported calculations automatically. Keep bookmaker-derived probabilities, model predictions and AI research judgments distinct.
- Produce one research pack usable across Claude, ChatGPT and Gemini: fixtures, odds, model outputs where available, a compact relevant history summary.
- Keep online research in the workflow: ask for dated sources, uncertainty, and explicit reasons for selections or abstentions.
- Import recommendations without transcribing each pick.
- Keep the authoritative cumulative forecast and betting history.
- Import new captures automatically without duplicating records.
- Show actual profit and loss separately from prediction accuracy.
- Show unresolved matches or conflicts without guessing or overwriting history.
- Keep sports the model does not cover available for research, with model coverage clearly marked.

## Model improvement must

- Use historical betting data only where the required information exists.
- Record future predictions, probabilities, odds, model versions and outcomes consistently.
- Evaluate saved recommendations beyond the bets placed, when reliable results are available.
- Compare performance with a bookmaker-probability baseline.
- Test changes on subsequent data before treating them as improvements.
- Report insufficient evidence instead of producing an adjusted probability.

## Later decisions (not first release)

Ticket analysis is a supporting feature; when shown, it states its
assumptions. Automatic Kelly stakes, paid odds feeds and arbitrage are later
decisions.

## Success

Fewer routine steps, reliable all-sports capture handling, model-plus-research
capability kept, and measurable evidence on whether predictions improve.

## Order of work

1. Trace one representative all-sports capture and one current chat output
   through this workflow to fix the real input, model execution, research
   and return formats.
2. Inventory surviving history (capture files, daily reports, any PCBF
   ledger CSV) and migrate it.
3. Build the first release against the traced formats.
4. Only after a working replacement and verified history migration: freeze
   the old pipeline, remove legacy files, rewrite AGENTS.md.

## Open inputs needed from the operator

- A recent all-sports capture as actually produced. The repo's
  `browser_extension/bet9ja_capture_allsports/` saves raw page HTML only;
  the PCBF Mini prompt expects per-sport JSON with `markets`, `state` and
  `kickoff_utc_derived`, a format not present anywhere in this repo.
- One example of a chat's current output (picks, sources, reasoning).
- The prompt(s) currently used in the chats (PCBF Mini v1.3 received
  2026-10-04) and `football_score_v1.py` plus league params, if still used.
- Any surviving ledger or daily-report files.
