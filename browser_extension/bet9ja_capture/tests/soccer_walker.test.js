const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// --- Round 5 rewrite (+ Round 7 correction): batch competition selector
// on `/sportPage/1/competitions`, confirmed 2026-09-12 -- see
// soccer_walker.js's own header comment and
// SOCCER_ALL_COMPETITIONS_VALIDATION.md's Round 5/7 sections. This
// harness models the CONFIRMED real hierarchy:
//   `.accordion.accordion-soccer` root -> a `.competitions` SIBLING
//   block (the Popular-competitions selection/results panel -- never a
//   country, and never the discovery root itself) alongside one
//   `.accordion-item` DIRECT CHILD per country -> expand -> its own
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
// "never invent a fixed batch size" rule. `countryRenderDelayMs` and
// `countriesNeverRender` model the confirmed real client-render delay
// (0 countries immediately after DOMContentLoaded, the full inventory
// only ~1.8s later) that PR #38's real captures both hit as
// `NO_COUNTRIES_DISCOVERED`.
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
      <div class="accordion-toggle"><div class="accordion-text">${label}</div></div>
      <div class="accordion-content"><div class="accordion-inner"><div class="competitions">${rows}</div></div></div>
    </div>
  `;
}

// The real Popular-competitions block: a `.competitions` SIBLING of the
// country `.accordion-item`s, not an accordion item itself and not their
// container -- discovery must never mistake it for a country.
const POPULAR_COMPETITIONS_DECOY_HTML = `
  <div class="competitions" data-popular="1">
    <div class="competitions__group-item"><input type="checkbox" id="9000001" class="sportpage__cb-input" readonly><label for="9000001"></label><span>Some Popular League</span></div>
  </div>
`;

// A DIRECT-CHILD `.accordion-item` with no `.accordion-toggle
// .accordion-text` of its own -- proves discovery filters on that, not
// merely on class name + direct-child scoping.
const MALFORMED_ACCORDION_ITEM_DECOY_HTML = `
  <div class="accordion-item" data-decoy="1"><span>not a real country -- no accordion-toggle/accordion-text</span></div>
`;

/**
 * `countries` is `[{countrySlug, label, competitions: [{checkboxId, label, rows}]}]`.
 * `maxSimultaneousSelectable` models Bet9ja's own unconfirmed selection
 * limit -- `Infinity` (default) means "never observed in this test".
 * `countryRenderDelayMs` (default 0) delays the country `.accordion-item`s
 * being inserted into `.accordion.accordion-soccer`, modeling the
 * confirmed real client-render delay; `countriesNeverRender` means they
 * never appear at all.
 */
function pageHtml({
  countries,
  maxSimultaneousSelectable = Infinity,
  showLeaguesMissing = false,
  clearAllMissing = false,
  contentNeverUpdates = false,
  omitHeadings = false,
  countryRenderDelayMs = 0,
  countriesNeverRender = false,
  mismatchAttributionForIds = [],
}) {
  const countriesHtml = countries.map((c) => countryAccordionHtml(c)).join('');
  const rowsById = {};
  const labelsById = {};
  const countryLabelsById = {};
  for (const c of countries) {
    for (const comp of c.competitions) {
      rowsById[comp.checkboxId] = comp.rows || [];
      labelsById[comp.checkboxId] = comp.label;
      countryLabelsById[comp.checkboxId] = c.label;
    }
  }
  const showLeaguesBtn = showLeaguesMissing
    ? ''
    : `<button type="button" class="competitions__filter-btn check-coupon">Show Leagues</button>`;
  const clearAllBtn = clearAllMissing
    ? ''
    : `<button type="button" class="competitions__filter-btn clear-all">Clear all</button>`;

  const immediateCountriesHtml = countryRenderDelayMs > 0 || countriesNeverRender ? '' : countriesHtml;

  return `
    <head><title>Soccer - Competitions</title></head>
    <body>
      <div class="accordion accordion-soccer" id="soccer-root">
        ${POPULAR_COMPETITIONS_DECOY_HTML}
        ${MALFORMED_ACCORDION_ITEM_DECOY_HTML}
        ${immediateCountriesHtml}
        ${clearAllBtn}
        ${showLeaguesBtn}
      </div>
      <div id="results"></div>
      <div id="limit-notice" hidden></div>
      <script>
        const ROWS_BY_ID = ${JSON.stringify(rowsById)};
        const LABELS_BY_ID = ${JSON.stringify(labelsById)};
        const COUNTRY_LABELS_BY_ID = ${JSON.stringify(countryLabelsById)};
        const MAX_SELECTABLE = ${JSON.stringify(maxSimultaneousSelectable === Infinity ? null : maxSimultaneousSelectable)};
        const CONTENT_NEVER_UPDATES = ${JSON.stringify(contentNeverUpdates)};
        const OMIT_HEADINGS = ${JSON.stringify(omitHeadings)};
        const MISMATCH_ATTRIBUTION_FOR_IDS = ${JSON.stringify(mismatchAttributionForIds)};
        const results = document.getElementById('results');
        const limitNotice = document.getElementById('limit-notice');
        const soccerRoot = document.getElementById('soccer-root');

        function wireCountryToggles() {
          document.querySelectorAll('.accordion-toggle').forEach((el) => {
            if (el.dataset.wired) return;
            el.dataset.wired = '1';
            el.addEventListener('click', () => {
              el.closest('.accordion-item').classList.toggle('accordion-item--open');
            });
          });
        }
        wireCountryToggles();

        ${
          countryRenderDelayMs > 0
            ? `setTimeout(() => {
                soccerRoot.insertAdjacentHTML('beforeend', ${JSON.stringify(countriesHtml)});
                wireCountryToggles();
              }, ${countryRenderDelayMs});`
            : ''
        }

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
            if (CONTENT_NEVER_UPDATES) {
              // Realistic "stuck" behavior: a persistent, never-clearing
              // loading indicator -- matching the real screenshot's own
              // evidence (Bet9ja still rendering when the extension gave
              // up), never silent no-op inaction.
              results.innerHTML = '<div class="loading-spinner">Loading...</div>';
              return;
            }
            const checked = Array.from(document.querySelectorAll('.sportpage__cb-input:checked'));
            // Real evidence: the rendered content includes the
            // competition heading IMMEDIATELY BEFORE its own fixture
            // table -- one .sports-table PER selected competition, each
            // preceded by a "Soccer > Country > Competition" breadcrumb
            // heading (never one combined table).
            const html = checked
              .map((cb) => {
                // A genuinely UNRELATED name -- not a suffixed variant of
                // the real one -- so neither an exact nor a substring
                // match against any candidate's own name can succeed.
                const competitionLabel = MISMATCH_ATTRIBUTION_FOR_IDS.indexOf(cb.id) !== -1
                  ? 'Some Unrelated League Entirely'
                  : (LABELS_BY_ID[cb.id] || '');
                const breadcrumb = 'Soccer > ' + (COUNTRY_LABELS_BY_ID[cb.id] || '') + ' > ' + competitionLabel;
                const heading = OMIT_HEADINGS ? '' : '<div class="heading">' + breadcrumb + '</div>';
                const rowsHtml = (ROWS_BY_ID[cb.id] || []).join('');
                return heading + '<div class="sports-table">' + rowsHtml + '</div>';
              })
              .join('');
            results.innerHTML = html;
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

test('the .accordion.accordion-soccer root never appearing at all is a typed timeout, never NO_COUNTRIES_DISCOVERED', async () => {
  const doc = docFromHtml('<body><div class="competitions"></div></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  // The outer Popular .competitions block existing (but NOT wrapped in
  // .accordion.accordion-soccer) must never be mistaken for the root --
  // this is the exact real-capture defect PR #38's two uploaded captures
  // both hit (NO_COUNTRIES_DISCOVERED despite reaching the right route).
  assert.deepEqual(envelope.capture_status_reasons, ['SOCCER_COMPETITIONS_ROOT_TIMEOUT']);
  assert.equal(envelope.competitions_route_confirmed, true);
});

test('a root that exists but never gains a single country is SOCCER_COUNTRY_INVENTORY_TIMEOUT, not NO_COUNTRIES_DISCOVERED', async () => {
  const doc = docFromHtml('<head><title>Soccer - Competitions</title></head><body><div class="accordion accordion-soccer"></div></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.deepEqual(envelope.capture_status_reasons, ['SOCCER_COUNTRY_INVENTORY_TIMEOUT']);
});

test('an initially empty country inventory that renders after a delay is awaited successfully, never failed early', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA], countryRenderDelayMs: 300 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.countries_available, 1);
  assert.equal(envelope.fixtures.length, 1);
});

test('a country count that keeps changing without ever settling is SOCCER_COUNTRY_INVENTORY_UNSTABLE, never treated as ready', async () => {
  const html = `
    <head><title>Soccer - Competitions</title></head>
    <body>
      <div class="accordion accordion-soccer" id="soccer-root"></div>
      <script>
        const root = document.getElementById('soccer-root');
        let i = 0;
        function addOne() {
          i += 1;
          if (i > 250) return; // safely outlasts the 10s production timeout, then stops scheduling
          const div = document.createElement('div');
          div.className = 'accordion-item';
          div.innerHTML = '<div class="accordion-toggle"><div class="accordion-text">Country ' + i + '</div></div>';
          root.appendChild(div);
          setTimeout(addOne, 50);
        }
        addOne();
      </script>
    </body>
  `;
  const doc = docFromHtml(html);
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.deepEqual(envelope.capture_status_reasons, ['SOCCER_COUNTRY_INVENTORY_UNSTABLE']);
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
  // Confirms the real single-league evidence: the heading-based resolver
  // actually mapped this fixture to its own competition, not merely the
  // (identical, for a batch of one) whole-batch fallback list.
  assert.equal(envelope.fixtures[0].resolved_source_competition_id, '1209691');
  assert.equal(envelope.batch_results.length, 1);
  assert.equal(envelope.competition_results.length, 1);
  assert.equal(envelope.competition_results[0].outcome, 'CAPTURED_IN_BATCH');
  assert.equal(envelope.competition_results[0].source_competition_id, '1209691');
  assert.equal(envelope.batch_results[0].sport_context_diagnostics, undefined, 'a successful batch never carries sport_context_diagnostics');
  assert.ok(envelope.batch_results[0].content_readiness_diagnostics, 'every batch carries content_readiness_diagnostics');
  assert.ok(envelope.batch_results[0].content_readiness_diagnostics.matchup_rows_seen >= 1);
  // Requirements 1, 2, 3 from the real-structure regression fixture: the
  // outer Popular .competitions block (and its own decoy checkbox) is
  // never treated as a country or a competition; Nigeria IS discovered;
  // its own competition 1209691 is discovered after expanding it.
  assert.equal(envelope.countries_available, 1, 'the Popular .competitions sibling must never be counted as a country');
  assert.ok(
    !envelope.competition_results.some((r) => r.source_competition_id === '9000001'),
    'the Popular block\'s own decoy checkbox must never be discovered as a real competition'
  );
});

test('country discovery uses DIRECT children only -- a nested .accordion-item inside another country\'s own content is never double-counted as a top-level country', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  // Simulate a nested accordion item living INSIDE Nigeria's own expanded
  // content (there is no confirmed limit on accordion nesting depth) --
  // it carries its own accordion-toggle/accordion-text, so a naive
  // full-descendant query would misclassify it as a THIRD top-level
  // country.
  const nigeriaInner = doc.querySelector('[data-country="nigeria"] .accordion-inner');
  nigeriaInner.insertAdjacentHTML(
    'beforeend',
    '<div class="accordion-item"><div class="accordion-toggle"><div class="accordion-text">Nested Decoy</div></div></div>'
  );
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_available, 2, 'the nested accordion-item must never be counted as a third top-level country');
});

test('ROUND 8 regression: every competition is selected and shown SEQUENTIALLY, one per batch -- never all selected together into one render', async () => {
  // Models the real defect at a smaller, fast-to-test scale: a real run
  // selected all 374 discovered competitions into ONE batch before ever
  // clicking Show Leagues, and Bet9ja was still rendering when the
  // capture gave up. "Capture everything in one go" must mean one user
  // click automating many small batches, never one enormous render.
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.countries_available, 3);
  assert.equal(envelope.countries_visited, 3);
  assert.equal(envelope.competitions_available, 4);
  assert.equal(envelope.competitions_captured, 4);
  assert.equal(envelope.fixtures.length, 4);
  // Four competitions -> four SEPARATE batches, each holding exactly one.
  assert.equal(envelope.batch_results.length, 4);
  for (const batch of envelope.batch_results) {
    assert.equal(batch.competition_ids.length, 1, 'no batch may ever hold more than one competition (Round 8 fix)');
  }
});

test('Clear all is verified between EVERY batch, not just when a real Bet9ja selection limit is hit', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN], maxSimultaneousSelectable: 2 }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.competitions_available, 4);
  assert.equal(envelope.competitions_captured, 4);
  assert.equal(envelope.fixtures.length, 4);
  assert.equal(envelope.batch_results.length, 4);
  // Every fixture is precisely attributed to its OWN single competition
  // (via the resolved per-table heading) -- proof Clear all actually ran
  // and was verified before the next batch's selection began.
  for (const f of envelope.fixtures) {
    assert.equal(f.source_competition_ids_in_batch.length, 1);
    assert.equal(f.resolved_source_competition_id, f.source_competition_ids_in_batch[0]);
  }
  assert.ok(!envelope.capture_status_reasons.includes('SOME_COMPETITIONS_ATTRIBUTION_UNRESOLVED'));
});

test('multi-competition batch: each fixture is attributed to its OWN competition via the nearest heading, never to every competition in the batch', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.equal(envelope.fixtures.length, 3);
  const byHome = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.home, f]));
  assert.equal(byHome.Enyimba.resolved_source_competition_id, '1209691');
  assert.equal(byHome.Enyimba.competition, 'Professional Football League');
  assert.equal(byHome.Enyimba.region, 'Nigeria');
  assert.equal(byHome.Arsenal.resolved_source_competition_id, '2000001');
  assert.equal(byHome.Arsenal.competition, 'Premier League');
  assert.equal(byHome.Leeds.resolved_source_competition_id, '2000002');
  assert.equal(byHome.Leeds.competition, 'Championship');
  // Every competition classified individually, never lumped together.
  for (const id of ['1209691', '2000001', '2000002']) {
    const result = envelope.competition_results.find((r) => r.source_competition_id === id);
    assert.equal(result.outcome, 'CAPTURED_IN_BATCH');
  }
});

test('a page with no rendered Soccer breadcrumb heading at all fails the whole run closed with SPORT_CONTEXT_CONFLICT (the page-level gate), never a per-competition attribution failure', async () => {
  // Omitting every heading removes parser.js's OWN page-level
  // confirmation signal (a rendered heading beginning with "Soccer >"),
  // which is a stricter, earlier gate than per-table attribution --
  // this must fail the run at that gate, not be mistaken for a
  // per-competition COMPETITION_ATTRIBUTION_UNRESOLVED case.
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND], omitHeadings: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(envelope.competitions_captured, 0);
  assert.ok(envelope.capture_status_reasons.includes('STOPPED_EARLY_SPORT_CONTEXT_CONFLICT'));
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  assert.equal(nigeria.outcome, 'BATCH_FAILED');
  assert.equal(nigeria.failure_reason, 'SPORT_CONTEXT_CONFLICT');
  // England's competitions were never even attempted once the run
  // stopped closed -- honestly labeled, never silently absent and never
  // counted as "failed".
  for (const id of ['2000001', '2000002']) {
    const result = envelope.competition_results.find((r) => r.source_competition_id === id);
    assert.equal(result.outcome, 'NOT_ATTEMPTED_AFTER_EARLY_STOP');
  }
  // Every SPORT_CONTEXT_CONFLICT batch must carry both diagnostics
  // objects, so a real conflict is never diagnosed by guessing blind.
  const failedBatch = envelope.batch_results.find((b) => b.failure_reason === 'SPORT_CONTEXT_CONFLICT');
  assert.ok(failedBatch, 'expected one SPORT_CONTEXT_CONFLICT batch result');
  assert.ok(failedBatch.sport_context_diagnostics, 'batch must carry sport_context_diagnostics');
  assert.equal(failedBatch.sport_context_diagnostics.failed_check, 'BREADCRUMB_NOT_FOUND');
  assert.ok(failedBatch.content_readiness_diagnostics, 'batch must carry content_readiness_diagnostics');
  assert.equal(typeof failedBatch.content_readiness_diagnostics.stable_poll_count, 'number');
});

test('one competition whose own heading cannot be uniquely attributed is COMPETITION_ATTRIBUTION_UNRESOLVED, and does NOT invalidate a correctly attributed competition processed in a different batch', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND], mismatchAttributionForIds: ['2000001'] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  // Nigeria and Championship both resolve correctly and are captured.
  assert.equal(envelope.competitions_captured, 2);
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  const championship = envelope.competition_results.find((r) => r.source_competition_id === '2000002');
  assert.equal(nigeria.outcome, 'CAPTURED_IN_BATCH');
  assert.equal(championship.outcome, 'CAPTURED_IN_BATCH');
  // Premier League's own heading was rendered with an unrelated name --
  // its table cannot be uniquely mapped, so it alone is unresolved.
  const premierLeague = envelope.competition_results.find((r) => r.source_competition_id === '2000001');
  assert.equal(premierLeague.outcome, 'COMPETITION_ATTRIBUTION_UNRESOLVED');
  assert.ok(!envelope.fixtures.some((f) => f.resolved_source_competition_id === '2000001'));
  assert.ok(envelope.capture_status_reasons.includes('SOME_COMPETITIONS_ATTRIBUTION_UNRESOLVED'));
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
  const timedOutBatch = envelope.batch_results.find((b) => b.failure_reason === 'SHOW_LEAGUES_CONTENT_TIMEOUT');
  assert.ok(timedOutBatch.content_readiness_diagnostics, 'a content timeout must carry readiness diagnostics');
  assert.ok(timedOutBatch.content_readiness_diagnostics.loading_indicators_remaining >= 1);
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
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND, SPAIN] }));
  // Robust against exact shouldCancel() call-count bookkeeping (which
  // changed with Round 8's multi-pass inventory stabilization and
  // one-competition-per-batch cap): cancel once the first batch's own
  // Show Leagues button has actually been clicked, not by counting
  // predicate calls.
  let showLeaguesClicks = 0;
  doc.querySelector('.competitions__filter-btn.check-coupon').addEventListener('click', () => {
    showLeaguesClicks += 1;
  });
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    shouldCancel: () => showLeaguesClicks >= 1,
  });
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_USER_CANCELLED')));
  assert.ok(envelope.resume_metadata.can_resume);
  // The one competition whose batch already rendered before cancellation
  // is genuinely completed; the rest are honestly NOT_ATTEMPTED, never
  // silently absent and never counted as failed.
  assert.equal(envelope.competitions_captured, 1);
  assert.equal(envelope.competitions_failed, 0);
  assert.ok(envelope.competitions_skipped_by_early_stop >= 1);
  assert.equal(envelope.resume_metadata.last_completed_competition_id, '1209691');
  const notAttempted = envelope.competition_results.filter((r) => r.outcome === 'NOT_ATTEMPTED_AFTER_EARLY_STOP');
  assert.equal(notAttempted.length, envelope.competitions_skipped_by_early_stop);
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
