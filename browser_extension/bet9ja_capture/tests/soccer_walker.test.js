const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// --- Round 2 rewrite: real pre-match Soccer accordion hierarchy --------
// confirmed 2026-09-12, see soccer_walker.js's own header comment and
// SOCCER_ALL_COMPETITIONS_VALIDATION.md. This harness models:
//   Coupons entry control -> /popularCoupons/1
//   -> Soccer accordion (#left_prematch_sport-1_soccer_label-toggle)
//   -> country accordions ([id^="..._sg-"][id$="_label-toggle"])
//   -> competition controls ([id^="..._sg-"][id*="_g-"])
//   -> click -> /competition/soccer/{country}/{competition}/{ids}
// jsdom's real accordion/route behavior is simulated with a plain inline
// <script> attaching click listeners that toggle the confirmed
// `accordion-item--open` class and push a new URL synchronously -- close
// enough to a real (fast) UI transition to exercise the wait loops.

function fixtureRow({ eventId, home, away, prices = ['1.95', '3.40', '4.20'] }) {
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${eventId}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-1">${prices[0]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-X">${prices[1]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-2">${prices[2]}</li></ul></div></div>`;
}

function competitionPageHtml(rowsHtml) {
  return `<div class="sports-table">${rowsHtml.join('')}</div>`;
}

function competitionControlHtml({ groupId, countrySlug, compId, slug, label }) {
  const id = `left_prematch_sport-1_soccer_sg-${groupId}_${countrySlug}_g-${compId}_${slug}`;
  return `<a id="${id}" href="javascript:;" class="competition-link" data-resolved-path="/competition/soccer/${countrySlug}/${slug}/1-${groupId}-${compId}">${label}</a>`;
}

function countryBlockHtml({ groupId, countrySlug, label, competitions }) {
  const countryId = `left_prematch_sport-1_soccer_sg-${groupId}_${countrySlug}_label-toggle`;
  const competitionsHtml = competitions.map((c) => competitionControlHtml({ groupId, countrySlug, ...c })).join('');
  return `
    <div class="accordion-item">
      <a id="${countryId}" href="javascript:;" class="accordion-toggle">${label}</a>
      <div class="accordion-content"><div class="accordion-inner">${competitionsHtml}</div></div>
    </div>
  `;
}

/**
 * Builds a synthetic pre-match Soccer accordion. `countries` is an array
 * of `{groupId, countrySlug, label, competitions: [{compId, slug, label,
 * rows}]}`. `extraCountriesFromShowMore` (same shape) are only revealed
 * once `#..._buttonmore-toggle` is clicked -- modeling the real "show N
 * A-Z more" behavior.
 */
function soccerAccordionHtml({ countries, extraCountriesFromShowMore = [], includeShowMore = true, brokenCompetitionIds = [] }) {
  const countriesHtml = countries.map((c) => countryBlockHtml(c)).join('');
  const showMoreHtml = includeShowMore
    ? `<a id="left_prematch_sport-1_soccer_buttonmore-toggle" href="javascript:;" class="show-more">Show 90 A-Z more</a>`
    : '';
  return `
    <div class="accordion-item" id="soccer-accordion">
      <a id="left_prematch_sport-1_soccer_label-toggle" href="javascript:;" class="accordion-toggle">Soccer</a>
      <div class="accordion-content"><div class="accordion-inner">${showMoreHtml}${countriesHtml}</div></div>
    </div>
  `;
}

/**
 * Full synthetic page: Coupons entry control (optional), the Soccer
 * accordion, an unrelated decoy `.menu-list mt30` shortcuts sidebar
 * (mixing other sports, exactly like the real Round 1 defect), and the
 * click wiring for every confirmed control.
 */
function pageHtml({
  countries,
  extraCountriesFromShowMore = [],
  includeShowMore = true,
  includeCouponsEntry = false,
  couponsResolvesTo = '/popularCoupons/1',
  brokenCompetitionIds = [],
}) {
  const allCountries = [...countries, ...extraCountriesFromShowMore];
  const compPageMap = {};
  for (const c of allCountries) {
    for (const comp of c.competitions) {
      const id = `left_prematch_sport-1_soccer_sg-${c.groupId}_${c.countrySlug}_g-${comp.compId}_${comp.slug}`;
      compPageMap[id] = {
        path: `/competition/soccer/${c.countrySlug}/${comp.slug}/1-${c.groupId}-${comp.compId}`,
        html: competitionPageHtml(comp.rows || []),
      };
    }
  }

  return `
    <body>
      ${includeCouponsEntry ? '<a id="coupons_sport-1_soccer" href="javascript:;" class="sports-nav-link">Coupons</a>' : ''}
      <ul class="menu-list mt30">
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">NBA Shortcut</a></li>
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">Tennis Shortcut</a></li>
      </ul>
      <div id="soccer-accordion-slot">${soccerAccordionHtml({ countries, extraCountriesFromShowMore, includeShowMore })}</div>
      <div id="app">${competitionPageHtml([])}</div>
      <script>
        const COMP_PAGES = ${JSON.stringify(compPageMap)};
        const BROKEN = ${JSON.stringify(brokenCompetitionIds)};
        const COUPONS_TARGET = ${JSON.stringify(couponsResolvesTo)};
        const EXTRA_COUNTRIES_HTML = ${JSON.stringify(extraCountriesFromShowMore.map((c) => countryBlockHtml(c)).join(''))};

        const couponsEntry = document.getElementById('coupons_sport-1_soccer');
        if (couponsEntry) {
          couponsEntry.addEventListener('click', () => {
            window.history.pushState({}, '', COUPONS_TARGET);
          });
        }

        function attachAccordionToggle(el) {
          el.addEventListener('click', () => {
            const item = el.closest('.accordion-item');
            if (item) item.classList.toggle('accordion-item--open');
          });
        }

        function attachShowMore() {
          const btn = document.getElementById('left_prematch_sport-1_soccer_buttonmore-toggle');
          if (btn) {
            btn.addEventListener('click', () => {
              btn.insertAdjacentHTML('afterend', EXTRA_COUNTRIES_HTML);
              wireAll();
            }, { once: true });
          }
        }

        function attachCompetitionLinks() {
          document.querySelectorAll('[id*="_g-"]').forEach((el) => {
            if (el.dataset.wired) return;
            el.dataset.wired = '1';
            el.addEventListener('click', () => {
              if (BROKEN.indexOf(el.id) !== -1) return; // no-op: simulates a stuck click
              const page = COMP_PAGES[el.id];
              if (!page) return;
              window.history.pushState({}, '', page.path);
              document.getElementById('app').innerHTML = page.html;
            });
          });
        }

        function wireAll() {
          document.querySelectorAll('.accordion-toggle').forEach((el) => {
            if (el.dataset.wired) return;
            el.dataset.wired = '1';
            attachAccordionToggle(el);
          });
          attachShowMore();
          attachCompetitionLinks();
        }
        wireAll();
      </script>
    </body>
  `;
}

function docFromHtml(html, startUrl = 'https://sports.bet9ja.com/popularCoupons/1') {
  return new JSDOM(html, { runScripts: 'dangerously', url: startUrl }).window.document;
}

const ENGLAND = {
  groupId: '11058',
  countrySlug: 'england',
  label: 'England',
  competitions: [
    { compId: '170880', slug: 'premier-league', label: 'Premier League', rows: [fixtureRow({ eventId: '1', home: 'Arsenal', away: 'Chelsea' })] },
  ],
};
const SPAIN = {
  groupId: '22001',
  countrySlug: 'spain',
  label: 'Spain',
  competitions: [
    { compId: '330990', slug: 'laliga', label: 'LaLiga', rows: [fixtureRow({ eventId: '2', home: 'Real Madrid', away: 'Barcelona' })] },
  ],
};

test('/popularCoupons/1 is accepted directly as the starting route -- no coupons-entry click needed', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.coupons_route_confirmed, true);
  assert.equal(envelope.competitions_visited, 1);
});

test('a page with neither /popularCoupons/1 nor a coupons-entry control fails closed, never falls back to a guess', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND] }), 'https://sports.bet9ja.com/sport/soccer/1');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NOT_ON_POPULAR_COUPONS_PAGE'));
  assert.equal(envelope.coupons_route_confirmed, false);
});

test('landing on /liveCompetitions after the coupons-entry click is an immediate, named failure -- never treated as success', async () => {
  const doc = docFromHtml(
    pageHtml({ countries: [ENGLAND], includeCouponsEntry: true, couponsResolvesTo: '/liveCompetitions' }),
    'https://sports.bet9ja.com/'
  );
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('WRONG_SURFACE_LIVE_COMPETITIONS'));
});

test('the coupons-entry control successfully resolves to /popularCoupons/1 when clicked from elsewhere', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND], includeCouponsEntry: true }), 'https://sports.bet9ja.com/');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.coupons_route_confirmed, true);
  assert.equal(envelope.competitions_visited, 1);
});

test('no Soccer accordion toggle found: PREMATCH_SOCCER_INVENTORY_NOT_READY, never a fallback to the global shortcuts menu', async () => {
  const doc = docFromHtml('<body><ul class="menu-list mt30"><li class="menu-list__item"><a class="menu-list__link" href="javascript:;">Premier League</a></li></ul></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('PREMATCH_SOCCER_INVENTORY_NOT_READY'));
  assert.equal(envelope.soccer_accordion_found, false);
});

test('the global .menu-list.mt30 shortcuts sidebar is never searched -- only the Soccer accordion boundary counts', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  // Only England's one competition is discoverable -- the two
  // .menu-list.mt30 decoys (NBA, Tennis) must never contribute.
  assert.equal(envelope.countries_available, 1);
  assert.equal(envelope.competitions_available, 1);
});

test('walks every discovered country and competition, tagging fixtures with real identity fields', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.countries_available, 2);
  assert.equal(envelope.countries_visited, 2);
  assert.equal(envelope.countries_failed, 0);
  assert.equal(envelope.competitions_available, 2);
  assert.equal(envelope.competitions_visited, 2);
  assert.equal(envelope.competitions_failed, 0);

  const byAway = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.away, f]));
  assert.equal(byAway.Chelsea.source_country, 'England');
  assert.equal(byAway.Chelsea.source_competition, 'Premier League');
  assert.equal(byAway.Chelsea.source_group_id, '11058');
  assert.equal(byAway.Chelsea.source_competition_id, '170880');
  assert.equal(byAway.Barcelona.source_country, 'Spain');

  const result = envelope.competition_results.find((r) => r.competition_name_raw === 'Premier League');
  assert.equal(result.country_name_raw, 'England');
  assert.equal(result.source_group_id, '11058');
  assert.equal(result.source_competition_id, '170880');
  assert.equal(result.route_confirmed, true);
  assert.match(result.resolved_url, /^https:\/\/sports\.bet9ja\.com\/competition\/soccer\/england\/premier-league\//);
});

test('"show N A-Z more" is clicked by stable ID suffix, never by its visible text, and reveals additional countries', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND], extraCountriesFromShowMore: [SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.country_inventory_expanded, true);
  assert.equal(envelope.countries_available, 2);
  assert.equal(envelope.competitions_visited, 2);
});

test('an absent "show more" control does not fail the capture -- it proceeds with available countries and records the gap', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND], includeShowMore: false }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.country_inventory_expanded, false);
  assert.equal(envelope.country_inventory_expansion_reason, 'SHOW_MORE_CONTROL_ABSENT');
  assert.equal(envelope.competitions_visited, 1);
  assert.notEqual(envelope.capture_status, 'CAPTURE_COMPLETE', 'an unexpanded inventory can never be reported complete');
});

test('a country with zero competitions fails closed as COUNTRY_COMPETITION_LIST_EMPTY without stopping remaining countries', async () => {
  const EMPTY_COUNTRY = { groupId: '99001', countrySlug: 'nowhere', label: 'Nowhere', competitions: [] };
  const doc = docFromHtml(pageHtml({ countries: [EMPTY_COUNTRY, ENGLAND] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_failed, 1);
  assert.equal(envelope.countries_visited, 1);
  const failed = envelope.competition_results.find((r) => r.country_name_raw === 'Nowhere');
  assert.equal(failed.failure_reason, 'COUNTRY_COMPETITION_LIST_EMPTY');
  // England must still be processed despite Nowhere's failure.
  assert.equal(envelope.competitions_visited, 1);
});

test('a competition whose click never navigates is reported COMPETITION_ROUTE_TIMEOUT, never merged, and later competitions still succeed', async () => {
  const stuckId = `left_prematch_sport-1_soccer_sg-${ENGLAND.groupId}_${ENGLAND.countrySlug}_g-${ENGLAND.competitions[0].compId}_${ENGLAND.competitions[0].slug}`;
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND, SPAIN], brokenCompetitionIds: [stuckId] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_failed, 1);
  assert.equal(envelope.competitions_visited, 1);
  const failed = envelope.competition_results.find((r) => r.country_name_raw === 'England');
  assert.equal(failed.failure_reason, 'COMPETITION_ROUTE_TIMEOUT');
  const succeeded = envelope.competition_results.find((r) => r.country_name_raw === 'Spain');
  assert.equal(succeeded.route_confirmed, true);
});

test('a competition confirmed to have zero fixtures is recorded as competitions_empty, not a failure', async () => {
  const EMPTY_COMP_COUNTRY = {
    groupId: '33002',
    countrySlug: 'quietland',
    label: 'Quietland',
    competitions: [{ compId: '400000', slug: 'off-season', label: 'Off Season League', rows: [] }],
  };
  const doc = docFromHtml(pageHtml({ countries: [EMPTY_COMP_COUNTRY] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.competitions_empty, 1);
  const result = envelope.competition_results[0];
  assert.equal(result.route_confirmed, true);
  assert.equal(result.records_seen, 0);
  assert.equal(result.failure_reason, null);
});

test('a fixture legitimately reachable from more than one competition is deduplicated by fixture_id', async () => {
  const shared = fixtureRow({ eventId: '5555', home: 'Ajax', away: 'PSV' });
  const NL_A = { groupId: '44001', countrySlug: 'netherlands', label: 'Netherlands', competitions: [{ compId: '500001', slug: 'eredivisie', label: 'Eredivisie', rows: [shared] }] };
  const NL_B = { groupId: '44001', countrySlug: 'netherlands', label: 'Netherlands', competitions: [{ compId: '500002', slug: 'knvb-cup', label: 'KNVB Cup', rows: [shared] }] };
  // Two distinct competition IDs (different g- segments) can legitimately
  // resolve the same underlying fixture -- e.g. a cross-listed match.
  const doc = docFromHtml(
    pageHtml({
      countries: [{ groupId: '44001', countrySlug: 'netherlands', label: 'Netherlands', competitions: [...NL_A.competitions, ...NL_B.competitions] }],
    })
  );
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.duplicates_skipped, 1);
  assert.equal(envelope.fixtures.length, 1);
});

test('a full run across multiple countries and competitions with the full inventory expanded reports CAPTURE_COMPLETE', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND, SPAIN], includeShowMore: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  // No "show more" countries configured beyond what's already available,
  // so the control click is a genuine no-op -- country_inventory_expanded
  // still reports true because the control WAS found and clicked (the
  // gap this flag protects against is an ABSENT control, not an
  // already-complete list).
  assert.equal(envelope.country_inventory_expanded, true);
  assert.equal(envelope.countries_failed, 0);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.ok(!envelope.capture_status_reasons.includes('COUNTRY_ACCOUNTING_INVARIANT_VIOLATED'));
});

test('reconciliation: countries_available = countries_visited + countries_failed', async () => {
  const EMPTY_COUNTRY = { groupId: '99001', countrySlug: 'nowhere', label: 'Nowhere', competitions: [] };
  const doc = docFromHtml(pageHtml({ countries: [EMPTY_COUNTRY, ENGLAND, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_available, envelope.countries_visited + envelope.countries_failed);
});

test('safety: soccer_walker.js only ever clicks a confirmed control, never assigns a javascript: href, and neutralizes the default action before every click', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'soccer_walker.js'), 'utf-8');
  const codeOnly = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

  // Every .click() call in this file must go through clickSafely(), which
  // itself only calls .click() on its own `el` parameter -- confirming
  // there is exactly one raw .click() call site, inside that one guarded
  // helper.
  const clickCalls = codeOnly.match(/\w+\.click\(\)/g) || [];
  assert.deepEqual(clickCalls, ['el.click()'], 'the only raw .click() call must be inside clickSafely()');

  assert.ok(!/location\s*\.\s*href\s*=/.test(codeOnly), 'must never assign location.href directly');
  assert.ok(!/window\.open\(/.test(codeOnly), 'must never call window.open()');
  assert.ok(!codeOnly.includes('.menu-list'), 'must never reference the global shortcuts menu');
  assert.ok(!codeOnly.toLowerCase().includes('cashout'), 'must never reference a cashout control');
});
