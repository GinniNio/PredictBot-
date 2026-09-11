const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const settledParser = require('../settled_bets_parser.js');

function docFromHtml(html) {
  return new JSDOM(html, { runScripts: 'dangerously' }).window.document;
}

const SETTLED_CONTEXT = {
  sourceUrl: 'https://sports.bet9ja.com/myBets/',
  pageTitle: 'Bet9ja - My Bets',
  capturedAtUtc: '2024-08-17T12:00:00.000Z',
};

// --- Real selectors confirmed 2026-09-11, see -----------------------------
// SETTLED_BETS_REAL_PAGE_VALIDATION.md Round 1 and settled_bets_parser.js's
// own "REAL-DOM PROFILE" header comment for exactly what is/isn't confirmed.
//
// jsdom's real accordion/tab click behavior is simulated with a plain
// inline <script> attaching click listeners that toggle the confirmed
// state classes synchronously -- close enough to a real (fast) UI
// transition to exercise the wait loops without needing real timers
// beyond one polling interval.

function settledLeg({
  selection = 'Home Win',
  odds = '1.95',
  market = '1X2',
  score = '2:1',
  fixtureAndTime = 'Arsenal - Chelsea, 20 Sep 15:00',
  outcome = 'Won',
  competition = 'England - Premier League',
} = {}) {
  return `
    <div class="mybets-item">
      <div class="mybets-item__row">
        <span class="mybets-bet">${selection}</span>
        <span class="mybets-odd">${odds}</span>
      </div>
      <div class="mybets-item__row">${market}</div>
      <div class="mybets-item__row">
        <span class="mybets-score">${score}</span>
        <span class="mybets-game">${fixtureAndTime}</span>
        <span class="mybets__info">${outcome}</span>
      </div>
      <div class="mybets-item__row"><span class="mybets-game">${competition}</span></div>
    </div>
  `;
}

function settledTicket({
  ticketId = '9001234567',
  placedAtRaw = 'Today 14:32',
  type = 'System',
  legs = [settledLeg()],
  systemTable = '',
  stakeItem = 'Stake: 10.00',
  resultItem = 'Lost',
  open = false,
} = {}) {
  return `
    <div class="accordion-item${open ? ' accordion-item--open' : ''}">
      <div class="accordion-toggle">Toggle</div>
      <div class="accordion-text">${type}</div>
      <div class="mybets-holder">
        <span class="mybets-date">${placedAtRaw}</span>
        <span class="mybets-holder__info-item">${stakeItem}</span>
        <span class="mybets-holder__info-item">${resultItem}</span>
      </div>
      <div class="mybets-head__item">Ticket ID: ${ticketId}</div>
      ${systemTable ? `<div class="mybets__systable">${systemTable}</div>` : ''}
      ${legs.join('\n')}
    </div>
  `;
}

function settledCaptureHtml(ticketsHtml, { settledTabCurrent = false } = {}) {
  return `
    <body>
      <div class="account-info">Balance: 482.10 | Log out</div>
      <div class="mybets">
        <div class="mybets__bets-item${settledTabCurrent ? '' : ' mybets__bets-item--current'}">Open Bets</div>
        <div class="mybets__bets-item${settledTabCurrent ? ' mybets__bets-item--current' : ''}" id="settled-tab">Settled Bets</div>
        <div id="ticket-list">${ticketsHtml}</div>
      </div>
      <script>
        function attachAccordionListeners() {
          document.querySelectorAll('.accordion-item').forEach((item) => {
            const toggle = item.querySelector('.accordion-toggle');
            if (toggle) toggle.addEventListener('click', () => item.classList.toggle('accordion-item--open'));
          });
        }
        attachAccordionListeners();
        document.getElementById('settled-tab').addEventListener('click', function () {
          document.querySelectorAll('.mybets__bets-item').forEach((x) => x.classList.remove('mybets__bets-item--current'));
          this.classList.add('mybets__bets-item--current');
        });
      </script>
    </body>
  `;
}

async function captureSettled(ticketsHtml, { settledTabCurrent = true, extraContext } = {}) {
  const doc = docFromHtml(settledCaptureHtml(ticketsHtml, { settledTabCurrent }));
  return settledParser.captureFromDocument(doc, { ...SETTLED_CONTEXT, ...extraContext });
}

test('a source URL not on /myBets/ is refused without touching the DOM', async () => {
  const doc = docFromHtml(settledCaptureHtml(settledTicket()));
  const { envelope } = await settledParser.captureFromDocument(doc, {
    ...SETTLED_CONTEXT,
    sourceUrl: 'https://sports.bet9ja.com/sport/soccer/1',
  });
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NOT_ON_MYBETS_PAGE'));
});

test('activates the Settled Bets tab by clicking the confirmed control when not already current', async () => {
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Won 19.50' }), { settledTabCurrent: false });
  assert.notEqual(envelope.capture_status, 'CAPTURE_FAILED');
  assert.equal(envelope.coverage.tickets_parsed, 1);
});

test('a page with no "Settled Bets" tab control at all fails closed, never guesses a click target', async () => {
  const doc = docFromHtml(`
    <body>
      <div class="mybets"><div id="ticket-list">${settledTicket()}</div></div>
    </body>
  `);
  const { envelope } = await settledParser.captureFromDocument(doc, SETTLED_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('SETTLED_TAB_CONTROL_NOT_FOUND'));
});

test('a lost ticket: ticket_status LOST, no manufactured payout', async () => {
  const { envelope } = await captureSettled(
    settledTicket({
      ticketId: '9111111111',
      resultItem: 'Lost',
      legs: [settledLeg({ outcome: 'Lost', score: '0:2' }), settledLeg({ outcome: 'Won', score: '1:0' })],
    })
  );
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL'); // permanent cap: system_settlement breakdown unconfirmed
  const t = envelope.tickets[0];
  assert.equal(t.ticket_status, 'LOST');
  assert.equal(t.actual_payout, null);
  assert.equal(t.payout_resolution, 'NO_EXPLICIT_PAYOUT_DISPLAYED');
  assert.equal(t.total_stake, '10.00');
  assert.equal(t.legs[0].leg_status, 'LOST');
  assert.equal(t.legs[1].leg_status, 'WON');
});

test('a won ticket: ticket_status WON, actual_payout parsed as a decimal string', async () => {
  const { envelope } = await captureSettled(
    settledTicket({ ticketId: '9222222222', resultItem: 'Won 123.45', legs: [settledLeg({ outcome: 'Won' })] })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_status, 'WON');
  assert.equal(t.actual_payout, '123.45');
  assert.equal(t.payout_resolution, 'EXPLICIT_BOOKMAKER_AMOUNT');
  assert.equal(typeof t.actual_payout, 'string');
  assert.equal(typeof t.legs[0].odds, 'string');
});

test('a system ticket may contain both Won and Lost legs while producing one ticket result -- never inferred from "all legs won"', async () => {
  const { envelope } = await captureSettled(
    settledTicket({
      ticketId: '9333333333',
      type: 'System',
      systemTable: 'System TypeNo.BetsUnit StakeStake4 Folds535.00175.00',
      resultItem: 'Won 220.00',
      legs: [
        settledLeg({ outcome: 'Won' }),
        settledLeg({ outcome: 'Lost' }),
        settledLeg({ outcome: 'Won' }),
        settledLeg({ outcome: 'Lost' }),
      ],
    })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_status, 'WON');
  assert.equal(t.ticket_type_normalized, 'SYSTEM');
  assert.deepEqual(
    t.legs.map((l) => l.leg_status),
    ['WON', 'LOST', 'WON', 'LOST']
  );
  // Never inferred/collapsed from leg outcomes -- combination counts stay
  // null until a real breakdown selector is confirmed.
  assert.equal(t.system_settlement.combinations_total, null);
  assert.equal(t.system_settlement.combinations_won, null);
});

test('an unrecognized ticket summary result text resolves the whole ticket to unresolved, never guessed', async () => {
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Refunded' }));
  assert.equal(envelope.coverage.tickets_parsed, 0);
  assert.equal(envelope.coverage.tickets_unresolved, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'MISSING_OR_UNRECOGNIZED_SETTLEMENT_STATUS');
});

test('an unrecognized leg outcome resolves that leg (and the whole ticket, per fail-closed leg voiding) -- wait, leg_status UNRESOLVED is still a valid parse', async () => {
  const { envelope } = await captureSettled(
    settledTicket({ resultItem: 'Lost', legs: [settledLeg({ outcome: 'Push' })] })
  );
  // An unrecognized *leg* outcome does not void the ticket -- only a
  // structurally unsafe leg does. leg_status simply reports UNRESOLVED.
  assert.equal(envelope.coverage.tickets_parsed, 1);
  assert.equal(envelope.tickets[0].legs[0].leg_status, 'UNRESOLVED');
  assert.equal(envelope.tickets[0].legs[0].settlement_resolution, 'UNRECOGNIZED_STATUS_TEXT');
});

test('a leg with 3 rows (competition omitted) is accepted, competition_raw null', async () => {
  const threeRowLeg = `
    <div class="mybets-item">
      <div class="mybets-item__row"><span class="mybets-bet">X</span><span class="mybets-odd">1.50</span></div>
      <div class="mybets-item__row">1X2</div>
      <div class="mybets-item__row">
        <span class="mybets-score">1:1</span>
        <span class="mybets-game">Team A - Team B</span>
        <span class="mybets__info">Won</span>
      </div>
    </div>
  `;
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Won 5.00', legs: [threeRowLeg] }));
  assert.equal(envelope.coverage.tickets_parsed, 1);
  const leg = envelope.tickets[0].legs[0];
  assert.equal(leg.competition_raw, null);
  assert.equal(leg.competition_resolution, 'COMPETITION_UNAVAILABLE');
  assert.equal(leg.result_raw, '1:1');
});

test('a structural .mybets-item with zero rows is excluded at candidacy, never a fail-closed leg', async () => {
  const zeroRowStructural = '<div class="mybets-item"></div>';
  const { envelope } = await captureSettled(
    settledTicket({ resultItem: 'Won 5.00', legs: [settledLeg(), zeroRowStructural] })
  );
  assert.equal(envelope.coverage.tickets_parsed, 1);
  assert.equal(envelope.tickets[0].legs.length, 1);
  assert.equal(envelope.coverage.legs_seen, 1);
});

test('a leg with an unexpected row count (e.g. 2) voids the whole ticket', async () => {
  const malformedLeg = `<div class="mybets-item"><div class="mybets-item__row">a</div><div class="mybets-item__row">b</div></div>`;
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Won 5.00', legs: [malformedLeg] }));
  assert.equal(envelope.coverage.tickets_parsed, 0);
  assert.equal(envelope.coverage.tickets_unresolved, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
});

test('a missing ticket id fails closed', async () => {
  const noIdTicket = `<div class="accordion-item"><div class="accordion-toggle">Toggle</div><div class="mybets-head__item"></div><div class="mybets-holder"><span class="mybets-holder__info-item">Stake: 5.00</span><span class="mybets-holder__info-item">Lost</span></div>${settledLeg()}</div>`;
  const { envelope } = await captureSettled(noIdTicket);
  assert.equal(envelope.coverage.tickets_unresolved, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'MISSING_TICKET_ID');
});

test('a ticket expansion that never confirms (toggle exists but never adds the open class) times out unresolved', async () => {
  const brokenTicket = `
    <div class="accordion-item">
      <div class="accordion-toggle">Toggle</div>
      <div class="mybets-holder"><span class="mybets-holder__info-item">Stake: 5.00</span><span class="mybets-holder__info-item">Lost</span></div>
      <div class="mybets-head__item">Ticket ID: 700000001</div>
      ${settledLeg()}
    </div>
  `;
  const doc = docFromHtml(`
    <body>
      <div class="mybets">
        <div class="mybets__bets-item mybets__bets-item--current">Settled Bets</div>
        <div id="ticket-list">${brokenTicket}</div>
      </div>
    </body>
  `); // no click listener attached -- .accordion-toggle click is a no-op
  const { envelope } = await settledParser.captureFromDocument(doc, SETTLED_CONTEXT);
  assert.equal(envelope.coverage.tickets_unresolved, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'TICKET_EXPANSION_TIMEOUT');
});

test('two tickets on one page, each independently resolved -- no cross-ticket contamination', async () => {
  const { envelope } = await captureSettled(
    settledTicket({ ticketId: '9444444441', resultItem: 'Lost' }) +
      settledTicket({ ticketId: '9444444442', resultItem: 'Won 40.00' })
  );
  assert.equal(envelope.coverage.tickets_parsed, 2);
  const byId = Object.fromEntries(envelope.tickets.map((t) => [t.bet9ja_ticket_id, t]));
  assert.equal(byId['9444444441'].ticket_status, 'LOST');
  assert.equal(byId['9444444442'].ticket_status, 'WON');
});

test('a ticket already open before capture is left open afterward (collapseIfNeeded restores only what this capture opened)', async () => {
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Lost', open: true }));
  assert.equal(envelope.coverage.tickets_parsed, 1);
});

test('row accounting invariant: tickets_seen = parsed + unresolved + expected_excluded', async () => {
  const { envelope } = await captureSettled(
    settledTicket({ ticketId: 'A', resultItem: 'Won 1.00' }) + settledTicket({ ticketId: 'B', resultItem: 'Unknown Text' })
  );
  assert.equal(
    envelope.coverage.tickets_seen,
    envelope.coverage.tickets_parsed + envelope.coverage.tickets_unresolved + envelope.coverage.tickets_expected_excluded
  );
});

test('capture_status is permanently capped at CAPTURE_PARTIAL for this profile version (unconfirmed system-settlement breakdown)', async () => {
  const { envelope } = await captureSettled(settledTicket({ resultItem: 'Won 1.00' }));
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('SYSTEM_SETTLEMENT_BREAKDOWN_SELECTOR_UNVERIFIED'));
});

test('safety: the only elements this module ever calls .click() on are the confirmed Settled Bets tab, accordion toggle, and a verified numbered pagination item', () => {
  const source = fs.readFileSync(path.join(__dirname, '../settled_bets_parser.js'), 'utf-8');
  const clickCalls = [...source.matchAll(/(\w+)\.click\(\)/g)].map((m) => m[1]);
  assert.ok(clickCalls.length > 0, 'expected at least one .click() call');
  const allowed = new Set(['tabEl', 'toggle', 'nextItem', 'firstItem']);
  for (const target of clickCalls) {
    assert.ok(allowed.has(target), `unexpected click target "${target}" -- only ${[...allowed].join(', ')} are allowed`);
  }
  assert.ok(!source.toLowerCase().includes('reload selections'));
});

// --- Pagination ------------------------------------------------------------

function settledPaginationHtml({ pageTicketsHtmlList, startPage = 1 }) {
  const totalPages = pageTicketsHtmlList.length;
  const items = [];
  items.push('<span class="pg-pagination__item first">First</span>');
  items.push('<span class="pg-pagination__item prev">Prev</span>');
  for (let p = 1; p <= totalPages; p += 1) {
    items.push(
      `<span class="pg-pagination__item${p === startPage ? ' pg-pagination__item--current' : ''}" data-page="${p}">${p}</span>`
    );
  }
  items.push('<span class="pg-pagination__item next">Next</span>');
  items.push('<span class="pg-pagination__item last">Last</span>');

  return `
    <body>
      <div class="mybets">
        <div class="mybets__bets-item mybets__bets-item--current">Settled Bets</div>
        <div id="ticket-list">${pageTicketsHtmlList[startPage - 1]}</div>
        <div class="pg-pagination">${items.join('')}</div>
      </div>
      <script>
        const PAGES = ${JSON.stringify(pageTicketsHtmlList)};
        const list = document.getElementById('ticket-list');
        const pagination = document.querySelector('.pg-pagination');
        function attachAccordionListeners() {
          list.querySelectorAll('.accordion-item').forEach((item) => {
            const toggle = item.querySelector('.accordion-toggle');
            if (toggle) toggle.addEventListener('click', () => item.classList.toggle('accordion-item--open'));
          });
        }
        attachAccordionListeners();
        pagination.querySelectorAll('.pg-pagination__item').forEach((el) => {
          const p = el.getAttribute('data-page');
          if (p) {
            el.addEventListener('click', () => {
              const pageNum = parseInt(p, 10);
              list.innerHTML = PAGES[pageNum - 1];
              pagination.querySelectorAll('.pg-pagination__item').forEach((x) => x.classList.remove('pg-pagination__item--current'));
              el.classList.add('pg-pagination__item--current');
              attachAccordionListeners();
            });
          }
        });
      </script>
    </body>
  `;
}

test('pagination: walks every numbered page, merges tickets, never clicks first/prev/next/last', async () => {
  const doc = docFromHtml(
    settledPaginationHtml({
      pageTicketsHtmlList: [
        settledTicket({ ticketId: 'P1', resultItem: 'Won 1.00' }),
        settledTicket({ ticketId: 'P2', resultItem: 'Lost' }),
        settledTicket({ ticketId: 'P3', resultItem: 'Won 3.00' }),
      ],
    })
  );
  const { envelope } = await settledParser.captureFromDocument(doc, SETTLED_CONTEXT);
  assert.equal(envelope.coverage.pages_available, 3);
  assert.equal(envelope.coverage.pages_visited, 3);
  assert.equal(envelope.coverage.tickets_parsed, 3);
  assert.equal(envelope.page_results.length, 3);
});

test('pagination: onProgress callback fires once per page with running counts', async () => {
  const doc = docFromHtml(
    settledPaginationHtml({
      pageTicketsHtmlList: [
        settledTicket({ ticketId: 'Q1', resultItem: 'Won 1.00' }),
        settledTicket({ ticketId: 'Q2', resultItem: 'Lost' }),
      ],
    })
  );
  const progressCalls = [];
  const { envelope } = await settledParser.captureFromDocument(doc, {
    ...SETTLED_CONTEXT,
    onProgress: (info) => progressCalls.push(info),
  });
  assert.equal(progressCalls.length, 2);
  assert.equal(progressCalls[1].pagesVisited, 2);
  assert.equal(envelope.coverage.tickets_parsed, 2);
});

test('pagination: shouldCancel stops the walk early and produces a CAPTURE_PARTIAL envelope with resume_metadata', async () => {
  const doc = docFromHtml(
    settledPaginationHtml({
      pageTicketsHtmlList: [
        settledTicket({ ticketId: 'R1', resultItem: 'Won 1.00' }),
        settledTicket({ ticketId: 'R2', resultItem: 'Lost' }),
        settledTicket({ ticketId: 'R3', resultItem: 'Won 3.00' }),
      ],
    })
  );
  const { envelope } = await settledParser.captureFromDocument(doc, {
    ...SETTLED_CONTEXT,
    shouldCancel: () => true, // cancel before advancing past the first (already-processed) page
  });
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('CANCELLED_BEFORE_ALL_PAGES_VISITED'));
  assert.equal(envelope.coverage.tickets_parsed, 1);
  assert.ok(envelope.resume_metadata.can_resume);
  assert.equal(envelope.resume_metadata.last_page_completed, 1);
  assert.match(envelope.resume_metadata.resume_hint, /page 2/);
});

test('a full, uninterrupted multi-page walk that reaches the last known page reports resume_metadata.can_resume = false', async () => {
  const doc = docFromHtml(
    settledPaginationHtml({
      pageTicketsHtmlList: [settledTicket({ ticketId: 'S1', resultItem: 'Won 1.00' }), settledTicket({ ticketId: 'S2', resultItem: 'Lost' })],
    })
  );
  const { envelope } = await settledParser.captureFromDocument(doc, SETTLED_CONTEXT);
  assert.equal(envelope.resume_metadata.can_resume, false);
  assert.equal(envelope.resume_metadata.resume_hint, null);
});
