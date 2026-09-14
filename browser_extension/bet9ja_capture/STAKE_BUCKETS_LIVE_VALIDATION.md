# Real-page selector validation — structured stake-bucket extraction

Point-in-time record of `stake_buckets.js`'s testing against the actual
Bet9ja "My Bets" system-ticket table (`.mybets__systable`), mirroring the
process `REAL_PAGE_VALIDATION.md`, `TICKET_REAL_PAGE_VALIDATION.md`,
`SOCCER_ALL_COMPETITIONS_VALIDATION.md`, and
`SETTLED_BETS_REAL_PAGE_VALIDATION.md` used for the other capture
surfaces. Updated per round; each round gets its own dated section below
rather than overwriting the last.

## Round 1 — 2026-09-14 (built from indirect evidence; NOT yet run against
a live, authenticated page)

**Status: no live browser session was available in this working session
to inspect `.mybets__systable`'s actual DOM markup. This round's evidence
is entirely indirect — real, already-captured JSON output from the
EXISTING `settled_bets_parser.js`/`ticket_parser.js` (this repository's
own captured ticket samples), plus this repository's own already-shipped
`system_table_raw` header text. It is NOT a substitute for live
inspection, and `parseStakeBuckets` must not be trusted as confirmed
until a real Round 2 (mirroring `TICKET_REAL_PAGE_VALIDATION.md`'s own
Round 2/5/6 progression) actually runs it against a real page.**

### What this round's evidence actually supports

- `.mybets__systable`'s CONTAINER selector is confirmed by live
  inspection already (`SETTLED_BETS_REAL_PAGE_VALIDATION.md` Round 1) —
  reused verbatim, not re-derived here.
- Real captured `system_table_raw` text (from this repository's own real
  ticket samples) is consistently exactly this shape: a fixed header
  ("`System TypeNo.BetsUnit StakeStake`") followed immediately by one
  concatenated group per system-type row (e.g.
  `"Singles535.00175.00Doubles103.0030.00Trebles103.0030.004 Folds53.0015.00"`).
  Four fields per row (System Type, No. Bets, Unit Stake, Stake) is
  therefore confirmed as the real COLUMN shape — this round's assumption
  that the underlying DOM table has exactly 4 cells per data row follows
  directly from that, not from live inspection of the markup itself.
- `ticket_parser.js`'s own pre-existing, independently-reviewed text
  parser (`findUnambiguousSystemSplit`) already extracts a genuine,
  separate `ticket_type_raw` field (e.g. `"Singles"`, `"Doubles"`,
  `"Trebles"`) plus `unit_stake`/`total_stake` for a ticket whose digit
  run has exactly one arithmetically-consistent split — confirmed present
  on 18 real open tickets in this repository's own real capture sample.
  This is evidence the SYSTEM TYPE LABELS this module also recognizes
  ("Singles"/"Doubles"/"Trebles", plus the unconfirmed "N Folds" pattern)
  are real, not invented.

### NOT yet confirmed (this is Round 2's job)

- Whether `.mybets__systable` is a real `<table>` element with `<tr>`/
  `<td>` rows and cells at all, as opposed to a div-based layout styled
  to look like a table (`text()`, the existing helper every parser in
  this directory already uses, works identically either way for the
  FLATTENED text these other parsers rely on — but `parseStakeBuckets`
  specifically needs real `querySelectorAll('tr')`/`'td, th'|`
  structure, which flattened-text evidence cannot confirm).
- Whether the table has a real header row at all (this module assumes
  row 0 is always a header and is never parsed for values — if the real
  table has no header row, every real data row would be silently
  dropped as "the header", and `parseStakeBuckets` would return `null`
  for every real ticket. Fail-closed, but worth confirming directly).
- Whether "N Folds" (4+ legs) is the REAL label text Bet9ja displays for
  a fold size beyond Trebles, as opposed to some other wording (e.g.
  "4-Fold", "Fourfold") this module's `parseFoldSize` would not
  recognize. Real captured `system_table_raw` text for a 4-leg-or-more
  system in this session's own samples DID show exactly `"4 Folds"`
  (see the concatenated example above), so this specific case has actual
  textual evidence — but not confirmation the DOM cell itself carries
  identical text (versus, say, extra markup `text()` already normalizes
  away safely, or a translation/locale variant).

### What happens if Round 2 finds this wrong

Nothing breaks silently. `parseStakeBuckets` is written so that ANY
mismatch between its assumption and the real page (a non-table element,
a missing header row, an unrecognized label, a row with the wrong cell
count, an internally-inconsistent row) returns `null` for the WHOLE
table — never a partial or wrong result. A `null` return means
`stake_buckets` stays absent from the captured JSON, which is exactly
today's existing behavior (before this module existed at all) — so
Round 2 finding this wrong is a no-op regression, not a correctness bug:
`ticket_type_raw`'s own pre-existing text-split path (already confirmed,
already shipping) and the ledger importer's from-totals derivation both
continue to work completely unaffected either way.

### How to run Round 2

1. Open an authenticated Bet9ja "My Bets" page with at least one real
   SYSTEM ticket (settled or open).
2. In DevTools, inspect `.mybets__systable`'s actual markup (`$0.outerHTML`
   after selecting it in the Elements panel, or
   `document.querySelector('.mybets__systable').outerHTML` in the
   Console).
3. Confirm: is it a `<table>`? Does row 0 read as a header (column
   labels, not values)? Does each data row have exactly 4 cells? Does
   the "System Type" cell's text match one of `"Singles"`/`"Doubles"`/
   `"Trebles"`/`"<N> Folds"` exactly?
4. Re-run `node --test tests/stake_buckets.test.js` after updating the
   fixture-building helper in that file (`tableFromRows`) to match the
   REAL markup shape found, if it differs from this round's assumption.
5. Record the outcome in a new "Round 2" section below, following this
   file's own format — confirmed items, remaining gaps, and whether
   `parseStakeBuckets` needed a code change to match reality.
