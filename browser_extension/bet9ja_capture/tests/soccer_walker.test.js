const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// --- Round 5 rewrite: batch competition selector on
// `/sportPage/1/competitions`, confirmed 2026-09-12 -- see
// soccer_walker.js's own header comment and
// SOCCER_ALL_COMPETITIONS_VALIDATION.md's Round 5 section. This harness
// models:
//   `.competitions` root -> `.accordion-item` per country -> expand ->
//   `.competitions__group-item` rows, each a `.sportpage__cb-input`
//   checkbox (readonly, matching the real markup) + its own
//   `<label for="{id}">` -> click the label -> checkbox toggles ->
//   "Show Leagues" (`.competitions__filter-btn.check-coupon`) renders
//   every currently-checked competition's configured fixture rows into
//   `.sports-table` WITHOUT changing the URL -> "Clear all"
//   (`.competitions__filter-btn.clear-all`) resets every selection.
// A configurable `maxSimultaneousSelectable` reverts a checkbox and
// shows a "Maximum selection limit reached!" notification once the
// count would be exceeded -- modeling Bet9ja's own unconfirmed limit,
// discovered operationally rather than hard-coded, per this project's
// "never invent a fixed batch size" rule.
//
// NOTE on fixture row ids: real evidence confirms row-level sport
// resolution needs an id-embedded "sport-N" segment ONLY on pages where
// the URL itself doesn't carry `/competition/{sport}/{country}/
// {competition}/` (see parser.js's own header comment). Since
// `/sportPage/1/competitions` never changes its URL, this suite's rows
// carry a "sport-1_" id segment as the best-supported hypothesis --
// NOT independently confirmed for this specific page's real markup. If
// a real run instead shows every fixture landing in
// `records_unresolved`/`UNSUPPORTED_SPORT`, that confirms the opposite
// and is the very next thing to fix in parser.js, not a walker bug.

function fixtureRow({ eventId, home, away, prices = ['1.95', '3.40', '4.20'] }) {
  const idBase = `sportpage_sport-1_soccer_event-${eventId}`;
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="${idBase}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-1">${prices[0]}</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-X">${prices[1]}</li><li class="sports-table__odds-item dib pt10" id="${idBase}_odds_market-1x2_sign-2">${prices[2]}</li></ul></div></div>`;
}

function competitionGroupItemHtml({ checkboxId, label }) {
  return `<div class="competitions__group-item"><input type="checkbox" id="${checkboxId}" class="sportpage__cb-input" readonly><label for="${checkboxId}" class="sportpage__cb-label"></label><span>${label}</span></div>`;
}

function countryAccordionHtml({ countrySlug, label, competitions }) {
  const rows = competitions.map((c) => competitionGroupItemHtml(c)).join('');
  return `
    <div class="accordion-item" data-country="${countrySlug}">
      <div class="accordion-toggle"><span class="accordion-text">${label}</span></div>
      <div class="accordion-content"><div class="accordion-inner">${rows}</div></div>
    </div>
  `;
}

/**
 * `countries` is `[{countrySlug, label, competitions: [{checkboxId, label, rows}]}]`.
 * `maxSimultaneousSelectable` models Bet9ja's own unconfirmed selection
 * limit -- `Infinity` (default) means "never observed in this test".
 */
function pageHtml({ countries, maxSimultaneousSelectable = Infinity, showLeaguesMissing = false, clearAllMissing = false, contentNeverUpdates = false }) {
  const countriesHtml = countries.map((c) => countryAccordionHtml(c)).join('');
  const rowsById = {};
  for (const c of countries) {
    for (const comp of c.competitions) {
      rowsById[comp.checkboxId] = comp.rows || [];
    }
  }
  const showLeaguesBtn = showLeaguesMissing
    ? ''
    : `<button type="button" class="competitions__filter-btn check-coupon">Show Leagues</button>`;
  const clearAllBtn = clearAllMissing
    ? ''
    : `<button type="button" class="competitions__filter-btn clear-all">Clear all</button>`;

  return `
    <body>
      <div class="competitions">
        ${countriesHtml}
        ${clearAllBtn}
        ${showLeaguesBtn}
      </div>
      <div id="results"></div>
      <div id="limit-notice" hidden></div>
      <script>
        const ROWS_BY_ID = ${JSON.stringify(rowsById)};
        const MAX_SELECTABLE = ${JSON.stringify(maxSimultaneousSelectable === Infinity ? null : maxSimultaneousSelectable)};
        const CONTENT_NEVER_UPDATES = ${JSON.stringify(contentNeverUpdates)};
        const results = document.getElementById('results');
        const limitNotice = document.getElementById('limit-notice');

        document.querySelectorAll('.accordion-toggle').forEach((el) => {
          el.addEventListener('click', () => {
            el.closest('.accordion-item').classList.toggle('accordion-item--open');
          });
        });

        function selectedCount() {
          return document.querySelectorAll('.sportpage__cb-input:checked').length;
        }

        document.querySelectorAll('.sportpage__cb-input').forEach((cb) => {
          cb.addEventListener('change', () => {
            if (cb.checked && MAX_SELECTABLE !== null && selectedCount() > MAX_SELECTABLE) {
              cb.checked = false;
              limitNotice.hidden = false;
              limitNotice.textContent = 'Maximum selection limit reached!';
            }
          });
        });

        const showLeaguesBtn = document.querySelector('.competitions__filter-btn.check-coupon');
        if (showLeaguesBtn) {
          showLeaguesBtn.addEventListener('click', () => {
            if (CONTENT_NEVER_UPDATES) return;
            const checked = Array.from(document.querySelectorAll('.sportpage__cb-input:checked'));
            const rowsHtml = checked.map((cb) => (ROWS_BY_ID[cb.id] || []).join('')).join('');
            results.innerHTML = '<div class="sports-table">' + rowsHtml + '</div>';
          });
        }

        const clearAllBtn = document.querySelector('.competitions__filter-btn.clear-all');
        if (clearAllBtn) {
          clearAllBtn.addEventListener('click', () => {
            document.querySelectorAll('.sportpage__cb-input:checked').forEach((cb) => {
              cb.checked = false;
            });
            limitNotice.hidden = true;
          });
        }
      </script>
    </body>
  `;
}

function docFromHtml(html, startUrl = 'https://sports.bet9ja.com/sportPage/1/competitions') {
  return new JSDOM(html, { runScripts: 'dangerously', url: startUrl }).window.document;
}

const NIGERIA = {
  countrySlug: 'nigeria',
  label: 'Nigeria',
  competitions: [
    { checkboxId: '1209691', label: 'Professional Football League', rows: [fixtureRow({ eventId: '1', home: 'Enyimba', away: 'Rivers United' })] },
  ],
};

const ENGLAND = {
  countrySlug: 'england',
  label: 'England',
  competitions: [
    { checkboxId: '2000001', label: 'Premier League', rows: [fixtureRow({ eventId: '2', home: 'Arsenal', away: 'Chelsea' })] },
    { checkboxId: '2000002', label: 'Championship', rows: [fixtureRow({ eventId: '3', home: 'Leeds', away: 'Norwich' })] },
  ],
};

const SPAIN = {
  countrySlug: 'spain',
  label: 'Spain',
  competitions: [
    { checkboxId: '3000001', label: 'LaLiga', rows: [fixtureRow({ eventId: '4', home: 'Real Madrid', away: 'Barcelona' })] },
  ],
};

test('not on /sportPage/1/competitions fails closed, no clicks attempted', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA] }), 'https://sports.bet9ja.com/sport/soccer/1');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.deepEqual(envelope.capture_status_reasons, ['NOT_ON_COMPETITIONS_PAGE']);
  assert.equal(envelope.competitions_route_confirmed, false);
  assert.equal(envelope.fixtures.length, 0);
});

test('the correct route with no countries in .competitions fails closed', async () => {
  const doc = docFromHtml('<body><div class="competitions"></div></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_COUNTRIES_DISCOVERED'));
  assert.equal(envelope.competitions_route_confirmed, true);
});

test('a single country, single competition, full batch capture reports CAPTURE_COMPLETE', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.countries_available, 1);
  assert.equal(envelope.countries_visited, 1);
  assert.equal(envelope.competitions_available, 1);
  assert.equal(envelope.competitions_captured, 1);
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.fixtures[0].source_batch_index, 0);
  assert.deepEqual(envelope.fixtures[0].source_competition_ids_in_batch, ['1209691']);
  assert.equal(envelope.batch_results.length, 1);
  assert.equal(envelope.competition_results.length, 1);
  assert.equal(envelope.competition_results[0].outcome, 'CAPTURED_IN_BATCH');
  assert.equal(envelope.competition_results[0].source_competition_id, '1209691');
});

test('multiple countries and competitions with no selection limit all land in ONE batch', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.countries_available, 3);
  assert.equal(envelope.countries_visited, 3);
  assert.equal(envelope.competitions_available, 4);
  assert.equal(envelope.competitions_captured, 4);
  assert.equal(envelope.fixtures.length, 4);
  assert.equal(envelope.batch_results.length, 1);
  assert.equal(envelope.batch_results[0].competition_ids.length, 4);
});

test('a selection limit reached mid-run splits the run into two batches, both captured, Clear all runs between them', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN], maxSimultaneousSelectable: 2 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.competitions_available, 4);
  assert.equal(envelope.competitions_captured, 4);
  assert.equal(envelope.fixtures.length, 4);
  assert.equal(envelope.batch_results.length, 2);
  assert.equal(envelope.batch_results[0].competition_ids.length, 2);
  assert.equal(envelope.batch_results[1].competition_ids.length, 2);
  // Every fixture in batch 2 must be tagged with batch 2's own
  // competitions, never batch 1's leftover selection -- proof Clear all
  // actually ran and was verified between batches.
  const batch2Fixtures = envelope.fixtures.filter((f) => f.source_batch_index === 1);
  assert.equal(batch2Fixtures.length, 2);
  for (const f of batch2Fixtures) {
    assert.deepEqual(f.source_competition_ids_in_batch, envelope.batch_results[1].competition_ids);
  }
});

test('a competition with a missing label is SELECTION_FAILED and does not stop the run', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  // Remove Premier League's own label, simulating a real, unexplained
  // per-competition selection failure distinct from a limit.
  doc.querySelector('label[for="2000001"]').remove();
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_failed, 1);
  assert.equal(envelope.competitions_captured, 2); // Nigeria + Championship
  const failed = envelope.competition_results.find((r) => r.source_competition_id === '2000001');
  assert.equal(failed.outcome, 'SELECTION_FAILED');
  assert.equal(failed.failure_reason, 'LABEL_NOT_FOUND');
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('SOME_COMPETITIONS_FAILED'));
});

test('a country whose accordion never opens is countries_failed but does not stop other countries', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  // Break Nigeria's own toggle so its accordion-item--open class never
  // gets applied, without touching England's.
  const nigeriaToggle = doc.querySelector('[data-country="nigeria"] .accordion-toggle');
  const clone = nigeriaToggle.cloneNode(true);
  nigeriaToggle.replaceWith(clone); // strips the wired click listener
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_failed, 1);
  assert.equal(envelope.countries_visited, 1);
  assert.equal(envelope.competitions_available, 2); // only England's two discovered
  assert.equal(envelope.competitions_captured, 2);
  assert.ok(envelope.capture_status_reasons.includes('SOME_COUNTRIES_FAILED'));
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
});

test('Show Leagues never updating the fixture output is a safe stop, not a crash', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA], contentNeverUpdates: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.ok(envelope.capture_status_reasons.some((r) => r === 'STOPPED_EARLY_SHOW_LEAGUES_CONTENT_TIMEOUT'));
  // Zero usable output resulted (no fixtures, no unparsed records, no
  // confirmed-empty competition either) -- CAPTURE_FAILED is the correct,
  // honest status here, same as every other zero-output gate in this
  // module, even though there IS also a named early-stop reason.
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.resume_metadata.can_resume);
});

test('a missing Show Leagues button fails the batch safely instead of throwing', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA], showLeaguesMissing: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_failed, 1);
  const result = envelope.competition_results[0];
  assert.equal(result.outcome, 'BATCH_FAILED');
  assert.equal(result.failure_reason, 'SHOW_LEAGUES_BUTTON_NOT_FOUND');
});

test('a Clear all that never resets selections is a safe stop after the batch it already captured', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND], clearAllMissing: true, maxSimultaneousSelectable: 1 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  // The first batch (Nigeria's one competition) is fully captured before
  // Clear all is ever needed to be relied on for the next batch.
  assert.equal(envelope.competitions_captured, 1);
  assert.ok(envelope.capture_status_reasons.some((r) => r === 'STOPPED_EARLY_CLEAR_ALL_BUTTON_NOT_FOUND'));
  assert.ok(envelope.resume_metadata.can_resume);
});

test('cancellation before the second batch is a safe stop with resume_metadata, never a failure', async () => {
  let calls = 0;
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN], maxSimultaneousSelectable: 2 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    shouldCancel: () => {
      calls += 1;
      // shouldCancel is checked once per country during discovery (3),
      // once per batch attempt, and once per competition selection
      // attempt within a batch. Let discovery (3) and the first batch's
      // own selections (Nigeria, England PL, then the England
      // Championship attempt that hits the limit and finalizes the
      // batch: 4 more) fully complete, cancel once the second batch is
      // about to start.
      return calls > 7;
    },
  });
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_USER_CANCELLED')));
  assert.ok(envelope.resume_metadata.can_resume);
  assert.ok(envelope.competitions_captured >= 1);
  assert.ok(envelope.competitions_skipped_by_early_stop >= 1);
});

test('reconciliation: competitions_available accounts for every competition (captured + empty + failed + skipped by cap/early-stop)', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN], maxSimultaneousSelectable: 2 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(
    envelope.competitions_available,
    envelope.competitions_captured +
      envelope.competitions_empty +
      envelope.competitions_failed +
      envelope.competitions_skipped_by_safety_cap +
      envelope.competitions_skipped_by_early_stop
  );
  assert.ok(!envelope.capture_status_reasons.includes('COMPETITION_ACCOUNTING_INVARIANT_VIOLATED'));
});

test('a competition with zero configured fixture rows is a confirmed BATCH_EMPTY outcome, not a failure', async () => {
  const emptyComp = { countrySlug: 'nowhere', label: 'Nowhere', competitions: [{ checkboxId: '9999', label: 'Off Season League', rows: [] }] };
  const doc = docFromHtml(pageHtml({ countries: [emptyComp] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_empty, 1);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.competition_results[0].outcome, 'BATCH_EMPTY');
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
});

test('cross-batch duplicate fixtures are deduplicated by fixture_id and counted, never double-counted', async () => {
  const duplicateRow = fixtureRow({ eventId: '777', home: 'Same Team A', away: 'Same Team B' });
  const countryA = { countrySlug: 'aland', label: 'Aland', competitions: [{ checkboxId: '111', label: 'League A', rows: [duplicateRow] }] };
  const countryB = { countrySlug: 'bland', label: 'Bland', competitions: [{ checkboxId: '222', label: 'League B', rows: [duplicateRow] }] };
  const doc = docFromHtml(pageHtml({ countries: [countryA, countryB], maxSimultaneousSelectable: 1 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.batch_results.length, 2);
  assert.equal(envelope.fixtures.length, 1);
  assert.equal(envelope.duplicates_skipped, 1);
  assert.equal(envelope.batch_results[1].duplicate_fixtures_skipped, 1);
});

test('safety: soccer_walker.js only ever clicks a country accordion toggle, a competition label, Show Leagues, or Clear all -- never a checkbox directly, never a raw href navigation', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'soccer_walker.js'), 'utf-8');
  // Strip block and line comments before checking actual code -- the
  // header comment legitimately narrates the RETIRED Round 1-4 approach
  // (history.back(), javascript:;, /popularCoupons/1) for context, which
  // must not be mistaken for code this round still executes.
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  const clickCallSites = code.match(/\w+(?:\.\w+)*\s*\.click\(\)/g) || [];
  assert.ok(clickCallSites.length > 0, 'expected at least one .click() call site');
  for (const call of clickCallSites) {
    assert.ok(
      /^(toggle|label|button)\.click\(\)$/.test(call),
      `unexpected click call site: ${call}`
    );
  }
  assert.ok(!code.includes('location.href ='));
  assert.ok(!code.includes('window.open('));
  assert.ok(!code.includes('history.back'));
  assert.ok(!code.includes('popularCoupons'));
  assert.ok(!code.includes('javascript:'));
  assert.ok(!code.includes('checkbox.click'));
});
