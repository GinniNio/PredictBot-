const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const soccerWalker = require('../soccer_walker.js');
const { BASE_CONTEXT } = require('./helpers.js');

// The production default (SOCCER_MENU_SELECTORS.soccerMenuContainer:
// null) always reports zero competitions -- see soccer_walker.js's own
// header comment. These tests configure a container selector before
// each run to exercise the real navigation/aggregation logic ahead of
// that selector being confirmed against the live page, then restore the
// production default afterward so no test leaks state into another.
function withConfiguredContainer(containerSelector, fn) {
  const original = soccerWalker.SOCCER_MENU_SELECTORS.soccerMenuContainer;
  soccerWalker.SOCCER_MENU_SELECTORS.soccerMenuContainer = containerSelector;
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      soccerWalker.SOCCER_MENU_SELECTORS.soccerMenuContainer = original;
    });
}

function fixtureRow({ eventId, home, away, prices = ['1.95', '3.40', '4.20'] }) {
  return `<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${eventId}"><div class="sports-table__home txt-cut">${home}</div><div class="sports-table__away txt-cut">${away}</div></div><div class="sports-table__td sports-table__odds txt-c"><ul class="sports-table__odds-list f0"><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-1">${prices[0]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-X">${prices[1]}</li><li class="sports-table__odds-item dib pt10" id="prematch_event-${eventId}_odds_market-1x2_sign-2">${prices[2]}</li></ul></div></div>`;
}

function competitionPageHtml(rowsHtml) {
  return `<div class="sports-table">${rowsHtml.join('')}</div>`;
}

/**
 * Builds a synthetic multi-competition Soccer page: a menu of competition
 * links inside `containerSelector`, and a content area whose innerHTML
 * (and URL, via history.pushState) changes on each link's click --
 * modeling a client-side single-page app close enough to exercise the
 * content-change wait and the URL-based identity resolution, without
 * asserting anything about the STILL-unconfirmed menu-scoping selector
 * itself.
 */
function multiCompetitionHtml({ competitions, containerClass = 'soccer-menu' }) {
  const links = competitions
    .map((c, i) => `<a class="menu-list__link" href="javascript:;" data-index="${i}">${c.label}</a>`)
    .join('');
  // Initial content deliberately does NOT match any competition's own
  // fixtures (a generic "Highlights" placeholder, distinct event id) --
  // on the real page, the walker starts from the Highlights/Upcoming
  // view, not from any one competition's own page, so clicking even the
  // FIRST discovered competition link is expected to change what's on
  // screen. Seeding the initial content to equal competition[0]'s own
  // fixtures would make clicking link 0 a false no-op in this harness.
  const initialHtml = competitionPageHtml([fixtureRow({ eventId: 'initial-highlights', home: 'Highlights Home', away: 'Highlights Away' })]);
  return `
    <body>
      <div class="${containerClass}">${links}</div>
      <div id="content">${initialHtml}</div>
      <script>
        const PAGES = ${JSON.stringify(competitions.map((c) => ({ url: c.url, html: competitionPageHtml(c.rows) })))};
        const content = document.getElementById('content');
        document.querySelectorAll('.menu-list__link').forEach((link) => {
          link.addEventListener('click', () => {
            const i = parseInt(link.getAttribute('data-index'), 10);
            content.innerHTML = PAGES[i].html;
            window.history.pushState(null, '', PAGES[i].url);
          });
        });
      </script>
    </body>
  `;
}

function docFromHtml(html) {
  return new JSDOM(html, { runScripts: 'dangerously', url: 'https://sports.bet9ja.com/sport/soccer/1' }).window.document;
}

test('production default (no configured container): reports zero competitions, honest CAPTURE_FAILED', async () => {
  const doc = docFromHtml(multiCompetitionHtml({ competitions: [{ label: 'PL', url: '/competition/soccer/england/premier-league/1', rows: [] }] }));
  const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SOCCER_MENU_SELECTORS_UNVERIFIED'));
  assert.ok(envelope.capture_status_reasons.includes('NO_COMPETITIONS_DISCOVERED'));
  assert.equal(envelope.competitions_available, 0);
  assert.equal(envelope.scope, 'SOCCER_ALL_DISCOVERED_COMPETITIONS');
});

test('walks every discovered competition, captures its fixtures, and records country/competition from the URL', async () => {
  const competitions = [
    {
      label: 'Premier League',
      url: '/competition/soccer/england/premier-league/1',
      rows: [fixtureRow({ eventId: '1001', home: 'Arsenal', away: 'Chelsea' })],
    },
    {
      label: 'LaLiga',
      url: '/competition/soccer/spain/laliga/2',
      rows: [fixtureRow({ eventId: '2001', home: 'Real Madrid', away: 'Barcelona' })],
    },
  ];
  await withConfiguredContainer('.soccer-menu', async () => {
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
  });
});

test('deduplicates a fixture that legitimately appears under more than one competition menu entry', async () => {
  // Each competition also carries its own exclusive fixture so the two
  // pages' overall content genuinely differs -- the shared fixture alone
  // still must be deduplicated by fixture_id, but the pages themselves
  // must not look byte-identical (that would be indistinguishable from
  // the "content did not change" case this parser correctly treats as a
  // real failure -- see the FAILED test below).
  // Both use a /competition/ URL -- sport resolution for the
  // BET9JA_DESKTOP fallback profile requires either a "sport-N" id
  // segment (the Highlights-page convention) or a matched competition
  // URL (see parser.js); this test is about dedup behavior, not that
  // distinction, so both competitions use the confirmed competition-page
  // shape.
  const sharedRow = fixtureRow({ eventId: '5555', home: 'Ajax', away: 'PSV' });
  const competitions = [
    {
      label: 'Eredivisie',
      url: '/competition/soccer/netherlands/eredivisie/1',
      rows: [sharedRow, fixtureRow({ eventId: '6001', home: 'Feyenoord', away: 'Utrecht' })],
    },
    {
      label: 'KNVB Cup',
      url: '/competition/soccer/netherlands/knvb-cup/1',
      rows: [sharedRow, fixtureRow({ eventId: '6002', home: 'AZ', away: 'Twente' })],
    },
  ];
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(multiCompetitionHtml({ competitions }));
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

    assert.equal(envelope.fixtures_seen, 4, 'both competitions genuinely rendered their own two fixtures');
    assert.equal(envelope.fixtures_parsed, 3, 'the shared fixture is only counted once');
    assert.equal(envelope.duplicates_skipped, 1);
    assert.equal(envelope.fixtures.length, 3);
  });
});

test('a competition whose content never changes after the click is reported FAILED, never merged with the previous one', async () => {
  const competitions = [
    { label: 'Premier League', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] },
    { label: 'Stuck', url: '/competition/soccer/x/stuck/1', rows: [] }, // no click listener attached for this one below
  ];
  const html = `
    <body>
      <div class="soccer-menu">
        <a class="menu-list__link" href="javascript:;" data-index="0">Premier League</a>
        <a class="menu-list__link" href="javascript:;" data-index="1">Stuck</a>
      </div>
      <div id="content">${competitionPageHtml(competitions[0].rows)}</div>
      <script>
        const content = document.getElementById('content');
        // Only the FIRST link's click actually changes anything -- the
        // second is wired to a no-op, simulating a click that fails to
        // update the page.
        document.querySelectorAll('.menu-list__link')[0].addEventListener('click', () => {
          content.innerHTML = ${JSON.stringify(competitionPageHtml(competitions[0].rows))};
        });
      </script>
    </body>
  `;
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(html);
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);

    assert.equal(envelope.competitions_available, 2);
    // First link: content doesn't change from its own initial state on
    // click (no-op relative to itself) -- both links in this test in
    // fact fail to produce a NEW fingerprint since the first click
    // re-renders identical content and the second never renders at all.
    assert.equal(envelope.competitions_failed, 2);
    assert.ok(envelope.capture_status_reasons.includes('SOME_COMPETITIONS_FAILED'));
    for (const result of envelope.competition_results) {
      assert.equal(result.capture_status, 'FAILED');
      assert.equal(result.failure_reason, 'CONTENT_DID_NOT_CHANGE');
    }
  });
});

test('unresolved fixtures are still preserved with source_country/source_competition tagging', async () => {
  // A row with no away team -- MISSING_PARTICIPANTS, an unparsed record.
  const brokenRow =
    '<div class="table-f"><div class="sports-table__td sports-table__time txt-c"><span>19:00</span></div><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-9"><div class="sports-table__home txt-cut">OnlyHome</div></div></div>';
  const competitions = [{ label: 'Serie A', url: '/competition/soccer/italy/serie-a/1', rows: [brokenRow] }];
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(multiCompetitionHtml({ competitions }));
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
    assert.equal(envelope.fixtures_unresolved, 1);
    assert.equal(envelope.unparsed_records.length, 1);
    assert.equal(envelope.unparsed_records[0].source_country, 'italy');
    assert.equal(envelope.unparsed_records[0].source_competition, 'serie-a');
  });
});

test('never CAPTURE_OK -- the navigation/aggregation flow itself is unconfirmed against the real page', async () => {
  const competitions = [{ label: 'PL', url: '/competition/soccer/england/premier-league/1', rows: [fixtureRow({ eventId: '1', home: 'A', away: 'B' })] }];
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(multiCompetitionHtml({ competitions }));
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
    assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
    assert.ok(envelope.capture_status_reasons.includes('SOCCER_MENU_SELECTORS_UNVERIFIED'));
  });
});

test('decorative/icon-only .menu-list__link elements (no visible text) are excluded from discovery', async () => {
  const html = `
    <body>
      <div class="soccer-menu">
        <a class="menu-list__link" href="javascript:;"></a>
        <a class="menu-list__link" href="javascript:;" data-index="0">Premier League</a>
      </div>
      <div id="content">${competitionPageHtml([fixtureRow({ eventId: '1', home: 'A', away: 'B' })])}</div>
      <script>
        document.querySelectorAll('.menu-list__link')[1].addEventListener('click', () => {
          document.getElementById('content').innerHTML = ${JSON.stringify(
            competitionPageHtml([fixtureRow({ eventId: '2', home: 'C', away: 'D' })])
          )};
          window.history.pushState(null, '', '/competition/soccer/england/premier-league/1');
        });
      </script>
    </body>
  `;
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(html);
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
    assert.equal(envelope.competitions_available, 1, 'the empty-text link must not count as a discovered competition');
  });
});

test('a real (non-javascript:;) href on a .menu-list__link is excluded -- only confirmed client-side switches are candidates', async () => {
  const html = `
    <body>
      <div class="soccer-menu">
        <a class="menu-list__link" href="/some/real/page">Real Link</a>
      </div>
      <div id="content"></div>
    </body>
  `;
  await withConfiguredContainer('.soccer-menu', async () => {
    const doc = docFromHtml(html);
    const { envelope } = await soccerWalker.captureAllSoccerCompetitions(doc, BASE_CONTEXT);
    assert.equal(envelope.competitions_available, 0);
  });
});

test('safety: soccer_walker.js only ever calls .click() on a discovered competition-menu link', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'soccer_walker.js'), 'utf-8');
  const codeOnly = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  const clickCalls = codeOnly.match(/\w+\.click\(\)/g) || [];
  assert.ok(clickCalls.length > 0, 'expected at least one .click() call');
  const ALLOWED_CLICK_CALLS = new Set(['linkEl.click()', 'restoreTarget.click()']);
  for (const call of clickCalls) {
    assert.ok(ALLOWED_CLICK_CALLS.has(call), `unexpected click target: ${call}`);
  }
});
