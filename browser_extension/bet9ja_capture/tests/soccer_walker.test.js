const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// --- Round 2 rewrite (+ post-review correction): real pre-match Soccer -
// accordion hierarchy confirmed 2026-09-12, see soccer_walker.js's own
// header comment and SOCCER_ALL_COMPETITIONS_VALIDATION.md. This harness
// models:
//   Soccer accordion (#left_prematch_sport-1_soccer_label-toggle)
//   -> country accordions ([id^="..._sg-"][id$="_label-toggle"])
//   -> competition controls ([id^="..._sg-"][id*="_g-"])
//   -> click -> /competition/soccer/{country}/{competition}/{ids},
//      REPLACING the entire accordion out of the DOM (modeling the real
//      uncertainty over whether a competition page keeps the inventory
//      around -- this harness assumes the worst case, since that is
//      exactly the case Round 2's first cut silently broke on: selecting
//      a second competition in the same country without ever returning
//      to Coupons in between). `history.back()` (via a `popstate`
//      listener) is the only way the accordion reappears -- exercising
//      `returnToCouponsAndReopen` for real, not an in-place swap.
// The Coupons ENTRY control is intentionally not modeled here -- that
// responsibility now belongs to popup.js's own `ensureOnPopularCouponsRoute`
// (a real `chrome.tabs.update` navigation), not this content-script
// module, which only ever operates on a `Document` already on the
// confirmed route.

function fixtureRow({ eventId, home, away, prices = ['1.95', '3.40', '4.20'] }) {
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${eventId}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-1">${prices[0]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-X">${prices[1]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-2">${prices[2]}</li></ul></div></div>`;
}

function competitionPageHtml(rowsHtml) {
  return `<div class="sports-table">${rowsHtml.join('')}</div>`;
}

function competitionControlHtml({ groupId, countrySlug, compId, slug, label }) {
  const id = `left_prematch_sport-1_soccer_sg-${groupId}_${countrySlug}_g-${compId}_${slug}`;
  return `<a id="${id}" href="javascript:;" class="competition-link">${label}</a>`;
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
function soccerAccordionHtml({ countries, includeShowMore = true }) {
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
 * Full synthetic page. Selecting a competition REPLACES the whole
 * `#app` root (which holds the Soccer accordion) with just the
 * competition page -- the accordion is genuinely gone from the DOM until
 * `history.back()` (wired via a real `popstate` listener) restores it.
 */
function pageHtml({ countries, extraCountriesFromShowMore = [], includeShowMore = true, brokenCompetitionIds = [] }) {
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
      <ul class="menu-list mt30">
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">NBA Shortcut</a></li>
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">Tennis Shortcut</a></li>
      </ul>
      <div id="app">${soccerAccordionHtml({ countries, includeShowMore })}</div>
      <script>
        const COMP_PAGES = ${JSON.stringify(compPageMap)};
        const BROKEN = ${JSON.stringify(brokenCompetitionIds)};
        const EXTRA_COUNTRIES_HTML = ${JSON.stringify(extraCountriesFromShowMore.map((c) => countryBlockHtml(c)).join(''))};
        let INVENTORY_HTML = document.getElementById('app').innerHTML;
        const app = document.getElementById('app');

        function attachAccordionToggle(el) {
          el.addEventListener('click', () => {
            const item = el.closest('.accordion-item');
            if (item) item.classList.toggle('accordion-item--open');
          });
        }

        function attachShowMore() {
          const btn = document.getElementById('left_prematch_sport-1_soccer_buttonmore-toggle');
          if (btn && !btn.dataset.wired) {
            btn.dataset.wired = '1';
            btn.addEventListener('click', () => {
              btn.insertAdjacentHTML('afterend', EXTRA_COUNTRIES_HTML);
              // The expanded set is now "current" -- a later return-from-
              // competition restore must bring back the EXPANDED
              // inventory, not the pre-expansion snapshot.
              INVENTORY_HTML = app.innerHTML;
              wireAll();
            });
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
              app.innerHTML = page.html; // the accordion is now genuinely gone
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

        window.addEventListener('popstate', () => {
          app.innerHTML = INVENTORY_HTML;
          document.querySelectorAll('.accordion-toggle, [id*="_g-"], [id$="_buttonmore-toggle"]').forEach((el) => delete el.dataset.wired);
          wireAll();
        });
      </script>
    </body>
  `;
}

function docFromHtml(html, startUrl = 'https://sports.bet9ja.com/popularCoupons/1') {
  return new JSDOM(html, { runScripts: 'dangerously', url: startUrl }).window.document;
}

const ENGLAND_TWO_COMPS = {
  groupId: '11058',
  countrySlug: 'england',
  label: 'England',
  competitions: [
    { compId: '170880', slug: 'premier-league', label: 'Premier League', rows: [fixtureRow({ eventId: '1', home: 'Arsenal', away: 'Chelsea' })] },
    { compId: '170881', slug: 'championship', label: 'Championship', rows: [fixtureRow({ eventId: '11', home: 'Leeds', away: 'Norwich' })] },
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

test('/popularCoupons/1 is accepted as the starting route', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.coupons_route_confirmed, true);
});

test('a document not on /popularCoupons/1 fails closed immediately -- this module never clicks anything to try to get there', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN] }), 'https://sports.bet9ja.com/sport/soccer/1');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NOT_ON_POPULAR_COUPONS_PAGE'));
  assert.equal(envelope.coupons_route_confirmed, false);
});

test('a document already on /liveCompetitions fails closed immediately with a named reason -- never treated as usable', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN] }), 'https://sports.bet9ja.com/liveCompetitions');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('WRONG_SURFACE_LIVE_COMPETITIONS'));
});

test('no Soccer accordion toggle found: PREMATCH_SOCCER_INVENTORY_NOT_READY, never a fallback to the global shortcuts menu', async () => {
  const doc = docFromHtml('<body><ul class="menu-list mt30"><li class="menu-list__item"><a class="menu-list__link" href="javascript:;">Premier League</a></li></ul></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('PREMATCH_SOCCER_INVENTORY_NOT_READY'));
  assert.equal(envelope.soccer_accordion_found, false);
});

test('the global .menu-list.mt30 shortcuts sidebar is never searched -- only the Soccer accordion boundary counts', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_available, 1);
  assert.equal(envelope.competitions_available, 1);
});

test('a country with TWO competitions: both are visited -- the fix for Round 2\'s own bug (selecting every competition back-to-back with no return in between)', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND_TWO_COMPS] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_available, 2);
  assert.equal(envelope.competitions_visited, 2);
  assert.equal(envelope.competitions_failed, 0);
  const byAway = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.away, f]));
  assert.ok(byAway.Chelsea, 'first competition in the country must be captured');
  assert.ok(byAway.Norwich, 'second competition in the SAME country must also be captured -- this is exactly what Round 2\'s bug missed');
});

test('walks every discovered country and competition, tagging fixtures with real identity fields', async () => {
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND_TWO_COMPS, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.countries_available, 2);
  assert.equal(envelope.countries_visited, 2);
  assert.equal(envelope.countries_failed, 0);
  assert.equal(envelope.competitions_available, 3);
  assert.equal(envelope.competitions_visited, 3);

  const byAway = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.away, f]));
  assert.equal(byAway.Chelsea.source_country, 'England');
  assert.equal(byAway.Chelsea.source_competition, 'Premier League');
  assert.equal(byAway.Chelsea.source_group_id, '11058');
  assert.equal(byAway.Chelsea.source_competition_id, '170880');
  assert.equal(byAway.Barcelona.source_country, 'Spain');

  const result = envelope.competition_results.find((r) => r.competition_name_raw === 'Premier League');
  assert.equal(result.country_name_raw, 'England');
  assert.equal(result.route_confirmed, true);
  assert.equal(result.records_seen, result.records_parsed + result.records_unresolved + result.records_expected_unsupported + result.duplicate_fixtures_skipped);
});

test('"show N A-Z more" is clicked by stable ID suffix, never by its visible text, and reveals additional countries (growth observed)', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN], extraCountriesFromShowMore: [ENGLAND_TWO_COMPS] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.country_inventory_expanded, true);
  assert.equal(envelope.country_inventory_growth_observed, true);
  assert.equal(envelope.countries_available, 2);
  assert.equal(envelope.competitions_visited, 3);
});

test('an absent "show more" control does not fail the capture -- it proceeds with available countries and records the gap', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN], includeShowMore: false }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.country_inventory_expanded, false);
  assert.equal(envelope.country_inventory_expansion_reason, 'SHOW_MORE_CONTROL_ABSENT');
  assert.notEqual(envelope.capture_status, 'CAPTURE_COMPLETE', 'an unexpanded inventory can never be reported complete');
});

test('a "show more" control that is clicked but reveals nothing new is recorded honestly, not silently treated as a full success', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN], includeShowMore: true, extraCountriesFromShowMore: [] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.country_inventory_expanded, true);
  assert.equal(envelope.country_inventory_growth_observed, false);
  assert.ok(envelope.capture_status_reasons.includes('COUNTRY_INVENTORY_EXPANSION_GROWTH_NOT_OBSERVED'));
  assert.notEqual(envelope.capture_status, 'CAPTURE_COMPLETE');
});

test('a country with zero competitions fails closed as COUNTRY_COMPETITION_LIST_EMPTY without stopping remaining countries', async () => {
  const EMPTY_COUNTRY = { groupId: '99001', countrySlug: 'nowhere', label: 'Nowhere', competitions: [] };
  const doc = docFromHtml(pageHtml({ countries: [EMPTY_COUNTRY, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_failed, 1);
  assert.equal(envelope.countries_visited, 1);
  const failed = envelope.competition_results.find((r) => r.country_name_raw === 'Nowhere');
  assert.equal(failed.failure_reason, 'COUNTRY_COMPETITION_LIST_EMPTY');
  assert.equal(envelope.competitions_visited, 1);
});

test('a competition whose click never navigates is reported COMPETITION_ROUTE_TIMEOUT, never merged, and later competitions still succeed', async () => {
  const stuckId = 'left_prematch_sport-1_soccer_sg-11058_england_g-170880_premier-league';
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND_TWO_COMPS, SPAIN], brokenCompetitionIds: [stuckId] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_failed, 1);
  const failed = envelope.competition_results.find((r) => r.competition_control_id === stuckId);
  assert.equal(failed.failure_reason, 'COMPETITION_ROUTE_TIMEOUT');
  // The SECOND competition in England, and Spain's, must still succeed.
  const succeeded = envelope.competition_results.filter((r) => r.route_confirmed === true);
  assert.equal(succeeded.length, 2);
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

test('a fixture legitimately reachable from more than one competition is deduplicated by fixture_id, and the skip is recorded on that specific competition\'s own record', async () => {
  const shared = fixtureRow({ eventId: '5555', home: 'Ajax', away: 'PSV' });
  const NETHERLANDS = {
    groupId: '44001',
    countrySlug: 'netherlands',
    label: 'Netherlands',
    competitions: [
      { compId: '500001', slug: 'eredivisie', label: 'Eredivisie', rows: [shared] },
      { compId: '500002', slug: 'knvb-cup', label: 'KNVB Cup', rows: [shared] },
    ],
  };
  const doc = docFromHtml(pageHtml({ countries: [NETHERLANDS] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.duplicates_skipped, 1);
  assert.equal(envelope.fixtures.length, 1);
  const cupResult = envelope.competition_results.find((r) => r.competition_name_raw === 'KNVB Cup');
  assert.equal(cupResult.duplicate_fixtures_skipped, 1);
  assert.equal(cupResult.records_parsed, 0);
});

test('a full run across multiple countries and competitions with the full inventory expanded and growth observed reports CAPTURE_COMPLETE', async () => {
  const doc = docFromHtml(pageHtml({ countries: [SPAIN], extraCountriesFromShowMore: [ENGLAND_TWO_COMPS] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.countries_failed, 0);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.capture_status, 'CAPTURE_COMPLETE');
  assert.ok(!envelope.capture_status_reasons.includes('COUNTRY_ACCOUNTING_INVARIANT_VIOLATED'));
  assert.ok(!envelope.capture_status_reasons.includes('COMPETITION_ACCOUNTING_INVARIANT_VIOLATED'));
});

test('reconciliation: countries_available accounts for every country (visited + failed + skipped by cap/early-stop)', async () => {
  const EMPTY_COUNTRY = { groupId: '99001', countrySlug: 'nowhere', label: 'Nowhere', competitions: [] };
  const doc = docFromHtml(pageHtml({ countries: [EMPTY_COUNTRY, ENGLAND_TWO_COMPS, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(
    envelope.countries_available,
    envelope.countries_visited + envelope.countries_failed + envelope.countries_skipped_by_safety_cap + envelope.countries_skipped_by_early_stop
  );
  assert.equal(
    envelope.competitions_available,
    envelope.competitions_visited +
      envelope.competitions_empty +
      envelope.competitions_failed +
      envelope.competitions_skipped_by_safety_cap +
      envelope.competitions_skipped_by_early_stop
  );
});

test('cancellation between competitions is a safe stop with resume_metadata, never a failure', async () => {
  let calls = 0;
  const doc = docFromHtml(pageHtml({ countries: [ENGLAND_TWO_COMPS, SPAIN] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, {
    ...BASE_CONTEXT,
    shouldCancel: () => {
      calls += 1;
      // shouldCancel is checked once per country and once per
      // competition; let the first two checks (country, then the first
      // competition) through, cancel from the third check onward.
      return calls > 2;
    },
  });
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.some((r) => r.startsWith('STOPPED_EARLY_USER_CANCELLED')));
  assert.ok(envelope.resume_metadata.can_resume);
  assert.equal(envelope.competitions_visited, 1);
});

test('a return-to-Coupons that never confirms the route is a safe stop, not a crash, and preserves everything already captured', async () => {
  // A competition page that, once entered, can never be left (popstate
  // does nothing) -- models a broken/unconfirmed return path for real.
  const html = `
    <body>
      <div id="app">${soccerAccordionHtml({ countries: [ENGLAND_TWO_COMPS] })}</div>
      <script>
        const app = document.getElementById('app');
        function wire() {
          document.querySelectorAll('.accordion-toggle').forEach((el) => {
            if (el.dataset.wired) return;
            el.dataset.wired = '1';
            el.addEventListener('click', () => {
              const item = el.closest('.accordion-item');
              if (item) item.classList.toggle('accordion-item--open');
            });
          });
          document.querySelectorAll('[id*="_g-"]').forEach((el) => {
            if (el.dataset.wired) return;
            el.dataset.wired = '1';
            el.addEventListener('click', () => {
              window.history.pushState({}, '', '/competition/soccer/england/premier-league/1-11058-170880');
              app.innerHTML = ${JSON.stringify(competitionPageHtml([fixtureRow({ eventId: '1', home: 'Arsenal', away: 'Chelsea' })]))}; // never restored, even on popstate
            });
          });
        }
        wire();
      </script>
    </body>
  `;
  const doc = docFromHtml(html);
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_visited, 1);
  // The pathname itself resets via the browser's own history.back()
  // mechanics even with no popstate handler, so the exact safe-stop
  // reason surfaced here is whichever verification step first notices
  // the DOM was never actually restored -- either is a correct outcome.
  assert.ok(
    envelope.capture_status_reasons.some(
      (r) => r === 'STOPPED_EARLY_COULD_NOT_RETURN_TO_COUPONS' || r === 'STOPPED_EARLY_COULD_NOT_REOPEN_SOCCER_ACCORDION'
    )
  );
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.resume_metadata.can_resume);
});

test('safety: soccer_walker.js only ever clicks a confirmed control, never assigns a javascript: href, never clicks a Coupons-entry control, and neutralizes the default action before every click', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'soccer_walker.js'), 'utf-8');
  const codeOnly = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

  const clickCalls = codeOnly.match(/\w+\.click\(\)/g) || [];
  assert.deepEqual(clickCalls, ['el.click()'], 'the only raw .click() call must be inside clickSafely()');

  assert.ok(!/location\s*\.\s*href\s*=/.test(codeOnly), 'must never assign location.href directly');
  assert.ok(!/window\.open\(/.test(codeOnly), 'must never call window.open()');
  assert.ok(!codeOnly.includes('.menu-list'), 'must never reference the global shortcuts menu');
  assert.ok(!codeOnly.toLowerCase().includes('cashout'), 'must never reference a cashout control');
  assert.ok(!codeOnly.includes('couponsEntryControl') && !codeOnly.includes('coupons_sport-1_soccer'), 'must never click the Coupons entry control -- that navigation belongs to popup.js');
});
