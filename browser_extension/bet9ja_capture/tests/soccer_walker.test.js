const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// --- Real menu structure confirmed 2026-09-11 (Round 1), see -------------
// SOCCER_ALL_COMPETITIONS_VALIDATION.md and soccer_walker.js's own header
// comment for exactly what is/isn't confirmed. This harness models a
// client-side single-page app close enough to exercise the pathname-based
// navigation wait, the URL-based identity resolution, and the
// return-to-inventory/rediscovery cycle: a competition page does NOT keep
// the inventory's `.menu-list.mt30` around at all (modeling the "menu may
// not survive navigation" uncertainty soccer_walker.js is written
// defensively against), so a real `history.back()` + rediscovery is
// exercised on every multi-competition test, not merely an in-place swap.

function fixtureRow({ eventId, home, away, prices = ['1.95', '3.40', '4.20'] }) {
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${eventId}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-1">${prices[0]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-X">${prices[1]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-2">${prices[2]}</li></ul></div></div>`;
}

function competitionPageHtml(rowsHtml) {
  return `<div class="sports-table">${rowsHtml.join('')}</div>`;
}

function menuItemHtml(label) {
  return `<li class="menu-list__item"><a class="menu-list__link" href="javascript:;" data-label="${label}">${label}</a></li>`;
}

function inventoryHtml(labels) {
  const items = labels.map((label) => menuItemHtml(label)).join('');
  // Initial content deliberately does NOT match any competition's own
  // fixtures -- the real page starts from Highlights/Upcoming, not from
  // any one competition's own page.
  const placeholder = competitionPageHtml([fixtureRow({ eventId: 'initial-highlights', home: 'Highlights Home', away: 'Highlights Away' })]);
  return `<ul class="menu-list mt30">${items}</ul><div id="content">${placeholder}</div>`;
}

/**
 * Builds a synthetic multi-competition Soccer inventory page. Competition
 * pages render WITHOUT the `.menu-list.mt30` container at all (see header
 * comment) -- `history.back()` + a `popstate` listener is the only way
 * back to a menu, exercising returnToInventory/rediscovery for real.
 *
 * `brokenIndexes`: labels whose click never navigates (simulates
 * CONTENT_DID_NOT_CHANGE). `dropLabelsAfterReturn`: labels removed from
 * the menu the FIRST time the inventory is re-rendered (simulates
 * LINK_NOT_FOUND_ON_REDISCOVERY). `breakReturnAfterFirst`: if true, the
 * popstate listener stops re-rendering the menu after the first return
 * (simulates COULD_NOT_RETURN_TO_INVENTORY).
 */
function multiCompetitionHtml({ competitions, brokenIndexes = [], dropLabelsAfterReturn = [], breakReturnAfterFirst = false }) {
  const labels = competitions.map((c) => c.label);
  const initialInventoryHtml = inventoryHtml(labels);
  const compData = JSON.stringify(
    competitions.map((c) => ({ label: c.label, url: c.url, html: competitionPageHtml(c.rows) }))
  );
  return `
    <body>
      <div id="app">${initialInventoryHtml}</div>
      <script>
        const COMPETITIONS = ${compData};
        const BROKEN = ${JSON.stringify(brokenIndexes)};
        const DROP_AFTER_RETURN = ${JSON.stringify(dropLabelsAfterReturn)};
        const BREAK_RETURN_AFTER_FIRST = ${JSON.stringify(breakReturnAfterFirst)};
        const app = document.getElementById('app');
        let returnCount = 0;
        function currentLabels() {
          if (returnCount >= 1) {
            return COMPETITIONS.map((c) => c.label).filter((l) => DROP_AFTER_RETURN.indexOf(l) === -1);
          }
          return COMPETITIONS.map((c) => c.label);
        }
        function renderInventory() {
          const labels = currentLabels();
          let html = '<ul class="menu-list mt30">';
          labels.forEach((label) => {
            html += '<li class="menu-list__item"><a class="menu-list__link" href="javascript:;" data-label="' + label + '">' + label + '</a></li>';
          });
          html += '</ul><div id="content"></div>';
          app.innerHTML = html;
          attachListeners();
        }
        function attachListeners() {
          Array.from(app.querySelectorAll('.menu-list__link')).forEach((link) => {
            link.addEventListener('click', () => {
              const label = link.getAttribute('data-label');
              const idx = COMPETITIONS.map((c) => c.label).indexOf(label);
              if (BROKEN.indexOf(idx) !== -1) return; // no-op: simulates a stuck click
              const comp = COMPETITIONS[idx];
              window.history.pushState({}, '', comp.url);
              app.innerHTML = '<div id="content">' + comp.html + '</div>';
            });
          });
        }
        window.addEventListener('popstate', () => {
          returnCount += 1;
          if (BREAK_RETURN_AFTER_FIRST && returnCount >= 1) {
            app.innerHTML = '<div id="content"></div>'; // no menu at all -- return never succeeds again
            return;
          }
          renderInventory();
        });
        attachListeners();
      </script>
    </body>
  `;
}

function docFromHtml(html, startUrl = 'https://sports.bet9ja.com/sport/soccer/1') {
  return new JSDOM(html, { runScripts: 'dangerously', url: startUrl }).window.document;
}

test('no .menu-list.mt30 container at all: honest CAPTURE_FAILED, container-missing reason', async () => {
  const doc = docFromHtml('<body><div id="content"></div></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SOCCER_MENU_CONTAINER_NOT_FOUND'));
  assert.equal(envelope.competitions_available, 0);
  assert.equal(envelope.scope, 'SOCCER_ALL_DISCOVERED_COMPETITIONS');
});

test('.menu-list.mt30 present but empty: honest CAPTURE_FAILED, zero-discovered reason', async () => {
  const doc = docFromHtml('<body><ul class="menu-list mt30"></ul><div id="content"></div></body>');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_COMPETITIONS_DISCOVERED'));
});

test('walks every discovered competition via the confirmed .menu-list.mt30 structure, tags country/competition from the URL, and restores the menu afterward', async () => {
  const competitions = [
    { label: 'Premier League', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1001', home: 'Arsenal', away: 'Chelsea' })] },
    { label: 'LaLiga', url: '/competition/soccer/spain/laliga/2', rows: [fixtureRow({ eventId: '2001', home: 'Real Madrid', away: 'Barcelona' })] },
  ];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.competitions_available, 2);
  assert.equal(envelope.competitions_visited, 2);
  assert.equal(envelope.competitions_failed, 0);
  assert.equal(envelope.fixtures_parsed, 2);
  assert.equal(envelope.fixtures.length, 2);

  const byAway = Object.fromEntries(envelope.fixtures.map((f) => [f.participants.away, f]));
  assert.equal(byAway.Chelsea.source_country, 'england');
  assert.equal(byAway.Chelsea.source_competition, 'premier-league');
  assert.equal(byAway.Barcelona.source_country, 'spain');
  assert.equal(byAway.Barcelona.source_competition, 'laliga');

  assert.equal(envelope.competition_results.length, 2);
  for (const result of envelope.competition_results) {
    assert.equal(result.capture_status, 'COMPLETE');
    assert.equal(result.fixtures_parsed, 1);
    assert.equal(result.failure_reason, null);
  }

  // Confirms returnToInventory actually ran between competitions and
  // after the last one -- the menu is discoverable again at the end.
  assert.ok(doc.querySelector('.menu-list.mt30'));
});

test('works from the second supported starting route (/sportPage/1/coupons), not just /sport/soccer/1', async () => {
  const competitions = [{ label: 'Serie A', url: '/competition/soccer/italy/serie-a/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] }];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }), 'https://sports.bet9ja.com/sportPage/1/coupons');
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_visited, 1);
  assert.equal(envelope.fixtures_parsed, 1);
});

test('deduplicates a fixture that legitimately appears under more than one competition', async () => {
  const sharedRow = fixtureRow({ eventId: '5555', home: 'Ajax', away: 'PSV' });
  const competitions = [
    { label: 'Eredivisie', url: '/competition/soccer/netherlands/eredivisie/1', rows: [sharedRow, fixtureRow({ eventId: '6001', home: 'Feyenoord', away: 'Utrecht' })] },
    { label: 'KNVB Cup', url: '/competition/soccer/netherlands/knvb-cup/1', rows: [sharedRow, fixtureRow({ eventId: '6002', home: 'AZ', away: 'Twente' })] },
  ];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.fixtures_seen, 4);
  assert.equal(envelope.fixtures_parsed, 3, 'the shared fixture is only counted once');
  assert.equal(envelope.duplicates_skipped, 1);
  assert.equal(envelope.fixtures.length, 3);
});

test('two menu labels resolving to the same destination URL: the second is skipped as a duplicate destination, never re-captured', async () => {
  const sameUrl = '/competition/soccer/england/premier-league/1';
  const competitions = [
    { label: 'Premier League', url: sameUrl, rows: [fixtureRow({ eventId: '1', home: 'Arsenal', away: 'Chelsea' })] },
    { label: 'PL (Highlights alias)', url: sameUrl, rows: [fixtureRow({ eventId: '1', home: 'Arsenal', away: 'Chelsea' })] },
  ];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.competitions_visited, 1);
  assert.equal(envelope.fixtures_parsed, 1);
  const statuses = envelope.competition_results.map((r) => r.capture_status);
  assert.deepEqual(statuses, ['COMPLETE', 'SKIPPED_DUPLICATE_DESTINATION']);
});

test('a competition whose click never navigates is reported FAILED/CONTENT_DID_NOT_CHANGE, never merged, and later competitions still succeed', async () => {
  const competitions = [
    { label: 'Stuck', url: '/competition/soccer/x/stuck/1', rows: [] },
    { label: 'Premier League', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] },
  ];
  const doc = docFromHtml(multiCompetitionHtml({ competitions, brokenIndexes: [0] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.competitions_available, 2);
  assert.equal(envelope.competitions_failed, 1);
  assert.equal(envelope.competitions_visited, 1);
  assert.ok(envelope.capture_status_reasons.includes('SOME_COMPETITIONS_FAILED'));
  assert.equal(envelope.competition_results[0].capture_status, 'FAILED');
  assert.equal(envelope.competition_results[0].failure_reason, 'CONTENT_DID_NOT_CHANGE');
  assert.equal(envelope.competition_results[1].capture_status, 'COMPLETE');
  assert.equal(envelope.fixtures_parsed, 1);
});

test('a label that no longer resolves on rediscovery is reported FAILED/LINK_NOT_FOUND_ON_REDISCOVERY, never crashing the walk', async () => {
  const competitions = [
    { label: 'Premier League', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] },
    { label: 'LaLiga', url: '/competition/soccer/spain/laliga/2', rows: [fixtureRow({ eventId: '2', home: 'C', away: 'D' })] },
  ];
  // After the first return-to-inventory, "LaLiga" is no longer in the
  // re-rendered menu (simulating the live menu genuinely changing
  // mid-walk).
  const doc = docFromHtml(multiCompetitionHtml({ competitions, dropLabelsAfterReturn: ['LaLiga'] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.competitions_visited, 1);
  assert.equal(envelope.competitions_failed, 1);
  const byReason = envelope.competition_results.find((r) => r.failure_reason === 'LINK_NOT_FOUND_ON_REDISCOVERY');
  assert.ok(byReason, 'expected one LINK_NOT_FOUND_ON_REDISCOVERY result');
});

test('a return-to-inventory that never restores the menu is a safe stop, not a crash: already-captured competitions are preserved', async () => {
  const competitions = [
    { label: 'Premier League', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] },
    { label: 'LaLiga', url: '/competition/soccer/spain/laliga/2', rows: [fixtureRow({ eventId: '2', home: 'C', away: 'D' })] },
  ];
  const doc = docFromHtml(multiCompetitionHtml({ competitions, breakReturnAfterFirst: true }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

  assert.equal(envelope.competitions_visited, 1);
  assert.equal(envelope.fixtures_parsed, 1);
  assert.ok(envelope.capture_status_reasons.includes('COULD_NOT_RETURN_TO_INVENTORY'));
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL', 'a partial, honestly-stopped walk is not a failure');
});

test('unresolved fixtures are still preserved with source_country/source_competition tagging', async () => {
  const brokenRow =
    '<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-9"><div class="sports-table__home txt-cut">OnlyHome</div></div></div>';
  const competitions = [{ label: 'Serie A', url: '/competition/soccer/italy/serie-a/1', rows: [brokenRow] }];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.fixtures_unresolved, 1);
  assert.equal(envelope.unparsed_records.length, 1);
  assert.equal(envelope.unparsed_records[0].source_country, 'italy');
  assert.equal(envelope.unparsed_records[0].source_competition, 'serie-a');
});

test('never CAPTURE_OK -- one real successful end-to-end capture is still required before the version/status cap lifts', async () => {
  const competitions = [{ label: 'PL', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] }];
  const doc = docFromHtml(multiCompetitionHtml({ competitions }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('MENU_SELECTORS_CONFIRMED_VIA_INSPECTION_PENDING_REAL_CAPTURE'));
  assert.equal(soccerWalker.PARSER_VERSION, 'bet9ja-soccer-walker@0.1.0-unverified-menu-selectors');
});

test('decorative/icon-only .menu-list__link elements (no visible text) are excluded from discovery', async () => {
  const html = `
    <body>
      <ul class="menu-list mt30">
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;"></a></li>
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;" data-label="Premier League">Premier League</a></li>
      </ul>
      <div id="content"></div>
      <script>
        document.querySelector('a[data-label="Premier League"]').addEventListener('click', () => {
          document.getElementById('content').innerHTML = ${JSON.stringify(competitionPageHtml([fixtureRow({ eventId: '1', home: 'A', away: 'B' })]))};
          window.history.pushState(null, '', '/competition/soccer/england/premier-league/1');
        });
      </script>
    </body>
  `;
  const doc = docFromHtml(html);
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_available, 1, 'the empty-text link must not count as a discovered competition');
});

test('a real (non-javascript:;) href on a .menu-list__link is excluded -- only confirmed client-side switches are candidates', async () => {
  const html = `
    <body>
      <ul class="menu-list mt30">
        <li class="menu-list__item"><a class="menu-list__link" href="/some/real/page">Real Link</a></li>
      </ul>
      <div id="content"></div>
    </body>
  `;
  const doc = docFromHtml(html);
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_available, 0);
});

test('a link outside .menu-list.mt30 (e.g. another sport\'s picker sharing the same class) is never discovered', async () => {
  const html = `
    <body>
      <ul class="menu-list mt30">
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">Premier League</a></li>
      </ul>
      <ul class="menu-list basketball-menu">
        <li class="menu-list__item"><a class="menu-list__link" href="javascript:;">NBA</a></li>
      </ul>
      <div id="content"></div>
    </body>
  `;
  const doc = docFromHtml(html);
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.competitions_available, 1);
});

test('safety: soccer_walker.js only ever calls .click() on a discovered competition-menu link', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'soccer_walker.js'), 'utf-8');
  const codeOnly = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  const clickCalls = codeOnly.match(/\w+\.click\(\)/g) || [];
  assert.ok(clickCalls.length > 0, 'expected at least one .click() call');
  const ALLOWED_CLICK_CALLS = new Set(['linkEl.click()']);
  for (const call of clickCalls) {
    assert.ok(ALLOWED_CLICK_CALLS.has(call), `unexpected click target: ${call}`);
  }
  assert.ok(!/\.querySelector\([^)]*(back|prev|breadcrumb)[^)]*\)\s*\.click/i.test(codeOnly), 'must never click a guessed back/breadcrumb control');
  assert.ok(codeOnly.includes('history.back()'), 'must return to the inventory page via history.back(), not a guessed click target');
});
