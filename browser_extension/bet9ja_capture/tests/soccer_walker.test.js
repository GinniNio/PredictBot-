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
  // ROUND 11: the FIRST Show Leagues click ever made while any of these
  // ids is the (sole) selected competition delays its own content update
  // until past SHOW_LEAGUES_CONTENT_TIMEOUT_MS -- modeling a real
  // competition that is merely slow once (e.g. Botswana's own real
  // capture), not permanently broken. A later click (soccer_walker.js's
  // own clear+reselect+retry) updates immediately, same as any other
  // competition.
  slowFirstShowLeaguesForIds = [],
  // ROUND 11: these ids render NEITHER a heading NOR a `.sports-table`
  // at all when selected -- modeling the real Turkey "2. Lig"/"3. Lig"
  // evidence (zero tables, zero rows), distinct from a competition whose
  // table renders but is empty.
  omitTableForIds = [],
  // ROUND 11: Show Leagues clicks made while ONLY these ids are selected
  // always leave a persistent, never-clearing loading indicator (both
  // the original attempt AND the retry) -- unlike page-wide
  // `contentNeverUpdates`, this is scoped so a DIFFERENT, later batch in
  // the same run can still succeed normally, proving one permanently
  // stuck competition never stops the whole run.
  neverUpdatesForIds = [],
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
        const SLOW_FIRST_SHOW_LEAGUES_FOR_IDS = ${JSON.stringify(slowFirstShowLeaguesForIds)};
        const OMIT_TABLE_FOR_IDS = ${JSON.stringify(omitTableForIds)};
        const NEVER_UPDATES_FOR_IDS = ${JSON.stringify(neverUpdatesForIds)};
        const alreadySlowedIds = new Set();
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
            if (checked.some((cb) => NEVER_UPDATES_FOR_IDS.indexOf(cb.id) !== -1)) {
              results.innerHTML = '<div class="loading-spinner">Loading...</div>';
              return;
            }
            const isFirstSlowClick = checked.some(
              (cb) => SLOW_FIRST_SHOW_LEAGUES_FOR_IDS.indexOf(cb.id) !== -1 && !alreadySlowedIds.has(cb.id)
            );
            function renderNow() {
              // Real evidence: the rendered content includes the
              // competition heading IMMEDIATELY BEFORE its own fixture
              // table -- one .sports-table PER selected competition, each
              // preceded by a "Soccer > Country > Competition" breadcrumb
              // heading (never one combined table). A competition in
              // OMIT_TABLE_FOR_IDS renders NEITHER at all (real evidence:
              // Turkey's "2. Lig"/"3. Lig", zero tables, zero rows).
              const html = checked
                .map((cb) => {
                  if (OMIT_TABLE_FOR_IDS.indexOf(cb.id) !== -1) return '';
                  // A genuinely UNRELATED name -- not a suffixed variant
                  // of the real one -- so neither an exact nor a
                  // substring match against any candidate's own name can
                  // succeed.
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
            }
            if (isFirstSlowClick) {
              checked.forEach((cb) => alreadySlowedIds.add(cb.id));
              // Never resolves within SHOW_LEAGUES_CONTENT_TIMEOUT_MS on
              // THIS click -- modeling a competition that is merely slow
              // once, not permanently broken. Leaves a loading indicator
              // up in the meantime, same honest "still loading" signal
              // CONTENT_NEVER_UPDATES uses.
              results.innerHTML = '<div class="loading-spinner">Loading...</div>';
              setTimeout(renderNow, 10000);
              return;
            }
            renderNow();
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

test('ROUND 10 (real-capture regression): a page with no rendered Soccer heading or breadcrumb at all still succeeds -- the gate no longer requires either', async () => {
  // This is the exact real-capture scenario (13:05:54 diagnostic
  // capture): content readiness passed, but neither heading this gate
  // used to require ever rendered. Rounds 6/8 would have failed this
  // closed with SPORT_CONTEXT_CONFLICT; the corrected gate (route +
  // capture_scope + selected_competition_ids, none of which depend on a
  // rendered heading) must let every batch reach real row parsing.
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND], omitHeadings: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.ok(!envelope.capture_status_reasons.includes('STOPPED_EARLY_SPORT_CONTEXT_CONFLICT'));
  assert.equal(envelope.competitions_captured, 3);
  for (const id of ['1209691', '2000001', '2000002']) {
    const result = envelope.competition_results.find((r) => r.source_competition_id === id);
    assert.equal(result.outcome, 'CAPTURED_IN_BATCH');
  }
  assert.equal(envelope.fixtures.length, 3);
  for (const fixture of envelope.fixtures) {
    assert.equal(fixture.sport, 'SOCCER');
  }
});

test('ROUND 10: a single-competition batch is attributed to its one selected competition unconditionally, even when its rendered heading names something else entirely', () => {
  // MAX_COMPETITIONS_PER_BATCH = 1 means every batch soccer_walker.js
  // actually builds has exactly one selected competition -- there is no
  // real ambiguity left for a heading to resolve, so
  // makeTableCompetitionResolver bypasses heading matching entirely for
  // this case (see its own comment). A heading naming an unrelated
  // competition (as this test's fixture renders) must NOT cause
  // COMPETITION_ATTRIBUTION_UNRESOLVED the way it would have under the
  // pre-Round-10 heading-matching resolver.
  const currentBatch = [{ checkboxId: '1209691', competitionNameRaw: 'Professional Football League', countryNameRaw: 'Nigeria' }];
  const resolver = soccerWalker.makeTableCompetitionResolver(currentBatch);
  const fakeTableWithWrongHeading = { previousElementSibling: { textContent: 'Soccer > Somewhere Else > Totally Unrelated League' } };
  const result = resolver(fakeTableWithWrongHeading);
  assert.equal(result.resolved, true);
  assert.equal(result.sourceCompetitionId, '1209691');
  assert.equal(result.competitionNameRaw, 'Professional Football League');
  assert.equal(result.countryNameRaw, 'Nigeria');
});

test('ROUND 10: the heading-matching resolver logic is retained, dormant, for a hypothetical future multi-competition batch', () => {
  // Exercises the fallback branch directly (currentBatch.length > 1) --
  // MAX_COMPETITIONS_PER_BATCH keeps this unreachable in production
  // today, but the logic itself must still work exactly as it did before
  // Round 10, unchanged, so it's ready if that cap is ever raised with
  // its own real evidence.
  const currentBatch = [
    { checkboxId: '1209691', competitionNameRaw: 'Professional Football League', countryNameRaw: 'Nigeria' },
    { checkboxId: '2000001', competitionNameRaw: 'Premier League', countryNameRaw: 'England' },
  ];
  const resolver = soccerWalker.makeTableCompetitionResolver(currentBatch);
  const nigeriaTable = { previousElementSibling: { textContent: 'Soccer > Nigeria > Professional Football League' } };
  const unrelatedTable = { previousElementSibling: { textContent: 'Soccer > Somewhere Else > Totally Unrelated League' } };
  assert.equal(resolver(nigeriaTable).sourceCompetitionId, '1209691');
  assert.equal(resolver(unrelatedTable).resolved, false);
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

test('ROUND 11: Show Leagues never updating the fixture output is retried once, then a safe BATCH_FAILED, not a crash and not a whole-run stop', async () => {
  // contentNeverUpdates means BOTH the original attempt and the retry
  // time out -- a persistent, never-clearing loading indicator, matching
  // the real screenshot evidence this check was built from. With only
  // one competition in the run, once it fails there is nothing left to
  // attempt, so the run ends normally (no earlyStopReason) rather than
  // stopping "early".
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA], contentNeverUpdates: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.ok(!envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_')));
  assert.equal(envelope.competitions_failed, 1);
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  assert.equal(nigeria.outcome, 'BATCH_FAILED');
  assert.equal(nigeria.failure_reason, 'SHOW_LEAGUES_CONTENT_TIMEOUT');
  // Zero usable output resulted (no fixtures, no unparsed records, no
  // confirmed-empty competition either) -- CAPTURE_FAILED is still the
  // correct, honest status here, same as every other zero-output gate.
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  // Nothing left to resume TO (the only competition already failed and
  // there is no early stop) -- can_resume is honestly false here.
  assert.equal(envelope.resume_metadata.can_resume, false);
  const timedOutBatch = envelope.batch_results.find((b) => b.failure_reason === 'SHOW_LEAGUES_CONTENT_TIMEOUT');
  assert.ok(timedOutBatch.content_readiness_diagnostics, 'a content timeout must carry readiness diagnostics');
  assert.ok(timedOutBatch.content_readiness_diagnostics.loading_indicators_remaining >= 1);
  assert.equal(timedOutBatch.retried, true);
});

test('ROUND 11 (real-capture regression): a single competition that times out on every attempt does not stop the run -- every other competition is still attempted, classified, and captured', async () => {
  // This is the exact real-capture defect (13:30:07 capture): Botswana >
  // Premier League timed out once and stopped the ENTIRE run, leaving
  // 213 other competitions NOT_ATTEMPTED_AFTER_EARLY_STOP. England's two
  // competitions here model those "left behind" competitions -- they
  // must now be reached and captured normally even though Nigeria (the
  // first batch) is permanently stuck (times out on both the original
  // attempt and the retry).
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND], neverUpdatesForIds: ['1209691'] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.ok(!envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_')));
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  assert.equal(nigeria.outcome, 'BATCH_FAILED');
  assert.equal(nigeria.failure_reason, 'SHOW_LEAGUES_CONTENT_TIMEOUT');
  for (const id of ['2000001', '2000002']) {
    const result = envelope.competition_results.find((r) => r.source_competition_id === id);
    assert.equal(result.outcome, 'CAPTURED_IN_BATCH');
  }
  assert.equal(envelope.competitions_captured, 2);
  assert.equal(envelope.competitions_failed, 1);
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  const nigeriaBatch = envelope.batch_results.find((b) => b.competition_ids.includes('1209691'));
  assert.equal(nigeriaBatch.retried, true);
});

test('ROUND 11: a competition slow only ONCE recovers on the retry and is captured normally, never reported as failed', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA], slowFirstShowLeaguesForIds: ['1209691'] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.ok(!envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_')));
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  assert.equal(nigeria.outcome, 'CAPTURED_IN_BATCH');
  assert.equal(envelope.competitions_captured, 1);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.fixtures.length, 1);
  const nigeriaBatch = envelope.batch_results.find((b) => b.competition_ids.includes('1209691'));
  assert.equal(nigeriaBatch.ok, true);
  assert.equal(nigeriaBatch.retried, true);
});

test('ROUND 11 (real-capture regression): a competition with zero rendered tables and zero rows is COMPETITION_CONTENT_UNRESOLVED, not COMPETITION_ATTRIBUTION_UNRESOLVED', async () => {
  // Real evidence: the 13:30:07 capture's Turkey "2. Lig"/"3. Lig"
  // competitions rendered no .sports-table at all. For a single-
  // competition batch (the only case reachable today), an EMPTY
  // table_attribution_summary means attribution logic was never even
  // exercised -- COMPETITION_ATTRIBUTION_UNRESOLVED would wrongly claim
  // it ran and failed to resolve a table that in fact never rendered.
  const TURKEY = {
    countrySlug: 'turkey',
    label: 'Turkey',
    competitions: [{ checkboxId: '5000001', label: '2. Lig', rows: [] }],
  };
  const doc = docFromHtml(pageHtml({ countries: [TURKEY], omitTableForIds: ['5000001'] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  const turkey = envelope.competition_results.find((r) => r.source_competition_id === '5000001');
  assert.equal(turkey.outcome, 'COMPETITION_CONTENT_UNRESOLVED');
  assert.equal(turkey.failure_reason, 'COMPETITION_CONTENT_UNRESOLVED');
  assert.ok(envelope.capture_status_reasons.includes('SOME_COMPETITIONS_CONTENT_UNRESOLVED'));
  assert.ok(!envelope.capture_status_reasons.includes('SOME_COMPETITIONS_ATTRIBUTION_UNRESOLVED'));
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

// --- Round 12: checkpointed capture sessions (soccer_session.js) support:
// context.competitionIdFilter (walk only a caller-specified subset of the
// freshly discovered inventory) and context.onBatchComplete (a per-batch
// progress hook so a session can be persisted after EVERY batch, not only
// at the end) -------------------------------------------------------------

test('competitionIdFilter: only the filtered-in competitions are walked; every filtered-out one gets an honest SKIPPED_BY_RESUME_FILTER row, never silently absent', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    // Only Nigeria and Championship -- Premier League (2000001) is
    // deliberately left out, modeling a "Resume capture" run that skips
    // an already-COMPLETED competition from a prior session.
    competitionIdFilter: ['1209691', '2000002'],
  });
  const nigeria = envelope.competition_results.find((r) => r.source_competition_id === '1209691');
  const premierLeague = envelope.competition_results.find((r) => r.source_competition_id === '2000001');
  const championship = envelope.competition_results.find((r) => r.source_competition_id === '2000002');
  assert.equal(nigeria.outcome, 'CAPTURED_IN_BATCH');
  assert.equal(championship.outcome, 'CAPTURED_IN_BATCH');
  assert.equal(premierLeague.outcome, 'SKIPPED_BY_RESUME_FILTER');
  assert.equal(premierLeague.batch_index, null);
  assert.equal(envelope.competitions_skipped_by_resume_filter, 1);
  assert.ok(envelope.capture_status_reasons.includes('COMPETITIONS_SKIPPED_BY_RESUME_FILTER'));
  assert.ok(!envelope.capture_status_reasons.includes('COMPETITION_ACCOUNTING_INVARIANT_VIOLATED'));
  assert.equal(
    envelope.competitions_available,
    envelope.competitions_captured +
      envelope.competitions_empty +
      envelope.competitions_failed +
      envelope.competitions_skipped_by_safety_cap +
      envelope.competitions_skipped_by_early_stop +
      envelope.competitions_skipped_by_resume_filter
  );
  // A filtered run is, by design, only ever attempting a deliberate
  // subset -- never CAPTURE_COMPLETE, which claims the whole inventory
  // succeeded.
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
});

test('competitionIdFilter accepts a Set as well as an array', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    competitionIdFilter: new Set(['1209691']),
  });
  assert.equal(envelope.competitions_skipped_by_resume_filter, 0);
  assert.equal(envelope.competition_results[0].outcome, 'CAPTURED_IN_BATCH');
});

test('onBatchComplete fires once per genuinely attempted competition (and once for the up-front filtered-out set), each call carrying only that call\'s own delta, never the whole running total', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  const calls = [];
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    onBatchComplete: (delta) => calls.push(delta),
  });
  // One call per batch (3 competitions, MAX_COMPETITIONS_PER_BATCH === 1
  // means 3 batches) -- never one giant call with everything at the end.
  assert.equal(calls.length, 3);
  const allDeltaIds = calls.flatMap((c) => c.competitionResults.map((r) => r.source_competition_id));
  assert.deepEqual(allDeltaIds.sort(), ['1209691', '2000001', '2000002'].sort());
  // Every delta's own fixtures are a SUBSET of the final envelope's own
  // fixtures (by fixture_id) -- proof this is a true per-batch delta, not
  // an accumulating snapshot.
  const finalFixtureIds = new Set(envelope.fixtures.map((f) => f.fixture_id));
  for (const call of calls) {
    for (const fixture of call.fixtures) {
      assert.ok(finalFixtureIds.has(fixture.fixture_id));
    }
  }
});

test('onBatchComplete also fires for a filtered-out competition (SKIPPED_BY_RESUME_FILTER), so a session can record it without waiting for the whole run to finish', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  const calls = [];
  await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    competitionIdFilter: ['1209691'],
    onBatchComplete: (delta) => calls.push(delta),
  });
  const skippedCall = calls.find((c) => c.competitionResults.some((r) => r.outcome === 'SKIPPED_BY_RESUME_FILTER'));
  assert.ok(skippedCall, 'expected one onBatchComplete call covering the filtered-out competitions');
  assert.equal(skippedCall.competitionResults.length, 2); // England's two competitions
});

test('a throwing onBatchComplete never breaks the underlying capture', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    onBatchComplete: () => {
      throw new Error('boom');
    },
  });
  assert.equal(envelope.competitions_captured, 1);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
});

test('discoveryOnly: runs discovery but walks nothing -- no competition is ever selected, no Show Leagues click happens, and every discovered competition is returned', async () => {
  const doc = docFromHtml(pageHtml({ countries: [NIGERIA, ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    discoveryOnly: true,
  });
  assert.equal(envelope.competitions_available, 3);
  assert.deepEqual(
    envelope.discovered_competitions.map((c) => c.competition_id).sort(),
    ['1209691', '2000001', '2000002'].sort()
  );
  const nigeriaEntry = envelope.discovered_competitions.find((c) => c.competition_id === '1209691');
  assert.equal(nigeriaEntry.country, 'Nigeria');
  assert.equal(nigeriaEntry.competition, 'Professional Football League');
  // Nothing was ever walked -- no batches, no fixtures, no checkbox ever
  // left checked (still exactly as discovery found it).
  assert.equal(envelope.batch_results.length, 0);
  assert.equal(envelope.fixtures.length, 0);
  assert.equal(doc.querySelectorAll('.sportpage__cb-input:checked').length, 0);
});
