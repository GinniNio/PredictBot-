const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const ticketParser = require('../ticket_parser.js');
const { BASE_CONTEXT } = require('./helpers.js');

function docFromHtml(html) {
  return new JSDOM(html, { runScripts: 'dangerously' }).window.document;
}

function leg({ home, away, market = '1X2', selection = 'H', odds = '1.95', eventId, statusHint = '' } = {}) {
  const idAttr = eventId ? ` id="prematch_event-${eventId}"` : '';
  return `
    <div class="ticket__leg">
      <span class="leg__home"${idAttr}>${home}</span>
      <span class="leg__away">${away}</span>
      <span class="leg__market">${market}</span>
      <span class="leg__selection">${selection}</span>
      <span class="leg__odds">${odds}</span>
      <span class="leg__status">${statusHint}</span>
    </div>
  `;
}

function ticket({
  ticketId = 'TCK-0001',
  status = 'OPEN',
  placedAtUtc = '2024-08-17T10:00:00Z',
  type = 'Single',
  unitStake = '10.00',
  totalStake = '10.00',
  potentialReturn = '19.50',
  legs = [leg()],
} = {}) {
  return `
    <div class="ticket" data-ticket-id="${ticketId}" data-placed-at-utc="${placedAtUtc}">
      <span class="ticket__status">${status}</span>
      <span class="ticket__type">${type}</span>
      <span class="ticket__unit-stake">${unitStake}</span>
      <span class="ticket__total-stake">${totalStake}</span>
      <span class="ticket__potential-return">${potentialReturn}</span>
      ${legs.join('\n')}
    </div>
  `;
}

async function capture(html, extraContext) {
  const doc = docFromHtml(`<div class="open-bets">${html}</div>`);
  return ticketParser.captureFromDocument(doc, { ...BASE_CONTEXT, ...extraContext });
}

test('single ticket: one leg fully parsed', async () => {
  const { envelope } = await capture(
    ticket({
      legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '832455154', odds: '1.95' })],
    })
  );
  assert.equal(envelope.schema_version, 'bet9ja-ticket-capture.v1');
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
  assert.equal(envelope.coverage.tickets_seen, 1);
  assert.equal(envelope.coverage.tickets_parsed, 1);
  assert.equal(envelope.coverage.tickets_unresolved, 0);
  assert.equal(envelope.coverage.tickets_expected_excluded, 0);
  assert.ok(envelope.capture_status_reasons.includes('TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER'));

  const t = envelope.tickets[0];
  assert.equal(t.bet9ja_ticket_id, 'TCK-0001');
  assert.equal(t.status, 'OPEN');
  assert.equal(t.ticket_type_normalized, 'SINGLE');
  assert.equal(t.ticket_type_taxonomy_gap, false);
  assert.equal(t.placed_at_utc, '2024-08-17T10:00:00.000Z');
  assert.equal(t.placed_at_resolution, 'EXPLICIT_UTC_ATTRIBUTE');
  assert.equal(t.unit_stake, 10);
  assert.equal(t.total_stake, 10);
  assert.equal(t.potential_return, 19.5);
  assert.equal(t.legs.length, 1);

  const l = t.legs[0];
  assert.equal(l.source_event_id, '832455154');
  assert.equal(l.fixture_id_resolution, 'EXTERNAL_EVENT_ID');
  assert.match(l.fixture_id, /^bxf_[0-9a-f]{16}$/);
  assert.equal(l.selection, 'H');
  assert.equal(l.odds, 1.95);
});

test('fixture_id matches the fixture-capture extension\'s own scheme for the same event id', async () => {
  const parser = require('../parser.js');
  const Bet9jaIds = require('../ids.js');
  const expected = Bet9jaIds.stableId('bxf', ['external', 'bet9ja-event-832455154']);
  const { envelope } = await capture(
    ticket({ legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '832455154' })] })
  );
  assert.equal(envelope.tickets[0].legs[0].fixture_id, expected);
  assert.ok(parser.PARSER_VERSION); // sanity: both modules load independently, no shared state
});

test('accumulator ticket: multiple legs, distinct fixture ids, unambiguous SYSTEM/DOUBLE/TREBLE normalize', async () => {
  const { envelope } = await capture(
    ticket({
      ticketId: 'TCK-ACCA-1',
      type: 'Treble',
      legs: [
        leg({ home: 'Arsenal', away: 'Chelsea', eventId: '1001' }),
        leg({ home: 'Liverpool', away: 'Everton', eventId: '1002' }),
        leg({ home: 'City', away: 'United', eventId: '1003' }),
      ],
    })
  );
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
  const t = envelope.tickets[0];
  assert.equal(t.ticket_type_normalized, 'TREBLE');
  assert.equal(t.legs.length, 3);
  const ids = new Set(t.legs.map((l) => l.fixture_id));
  assert.equal(ids.size, 3, 'each leg must get its own distinct fixture_id');
});

test('unrecognized ticket type (e.g. a straight all-up "Accumulator" category): raw text kept, taxonomy gap flagged, never guessed', async () => {
  const { envelope } = await capture(
    ticket({ ticketId: 'TCK-ACC-4', type: 'Accumulator', legs: [leg({ home: 'A', away: 'B', eventId: '1' }), leg({ home: 'C', away: 'D', eventId: '2' }), leg({ home: 'E', away: 'F', eventId: '3' }), leg({ home: 'G', away: 'H', eventId: '4' })] })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_type_raw, 'Accumulator');
  assert.equal(t.ticket_type_normalized, null);
  assert.equal(t.ticket_type_taxonomy_gap, true);
  // still fully captured -- the taxonomy gap is an importer-time problem,
  // not a reason to fail-close the whole ticket.
  assert.equal(envelope.capture_status, 'CAPTURE_OK');
});

test('system ticket: normalizes to SYSTEM, all legs captured', async () => {
  const { envelope } = await capture(
    ticket({
      ticketId: 'TCK-SYS-1',
      type: 'System',
      legs: [
        leg({ home: 'A', away: 'B', eventId: '11' }),
        leg({ home: 'C', away: 'D', eventId: '12' }),
        leg({ home: 'E', away: 'F', eventId: '13' }),
        leg({ home: 'G', away: 'H', eventId: '14' }),
      ],
    })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_type_normalized, 'SYSTEM');
  assert.equal(t.legs.length, 4);
});

test('two tickets, each with its own leg scan: repeated fixture across separate tickets never merges or cross-contaminates', async () => {
  const { envelope } = await capture(
    ticket({ ticketId: 'TCK-A', legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '5001', odds: '1.90' })] }) +
      ticket({ ticketId: 'TCK-B', legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '5001', odds: '1.85' })] })
  );
  assert.equal(envelope.coverage.tickets_seen, 2);
  assert.equal(envelope.coverage.tickets_parsed, 2);
  const [a, b] = envelope.tickets;
  assert.notEqual(a.bet9ja_ticket_id, b.bet9ja_ticket_id);
  // Same fixture (same source_event_id) legitimately appears in both
  // tickets -- same fixture_id is correct here, not a bug -- but each
  // ticket's own leg (with its own odds) must stay scoped to its own
  // ticket, never merged into the other ticket's legs array.
  assert.equal(a.legs[0].fixture_id, b.legs[0].fixture_id);
  assert.equal(a.legs.length, 1);
  assert.equal(b.legs.length, 1);
  assert.equal(a.legs[0].odds, 1.9);
  assert.equal(b.legs[0].odds, 1.85);
});

test('expanded vs collapsed view: both find the same tickets when both are present in the DOM', async () => {
  // "Collapsed" in this DOM-in/envelope-out design just means: whatever is
  // actually rendered in the DOM at capture time is what gets captured --
  // there is no confirmed real-page evidence yet for a genuinely
  // lazy-loaded/paginated open-bets list, so this only demonstrates the
  // parser doesn't care about a wrapping collapse/expand toggle class.
  const html = `
    <div class="ticket-group collapsed">
      ${ticket({ ticketId: 'TCK-COLLAPSED', legs: [leg({ home: 'A', away: 'B', eventId: '9001' })] })}
    </div>
    <div class="ticket-group expanded">
      ${ticket({ ticketId: 'TCK-EXPANDED', legs: [leg({ home: 'C', away: 'D', eventId: '9002' })] })}
    </div>
  `;
  const { envelope } = await capture(html);
  assert.equal(envelope.coverage.tickets_seen, 2);
  assert.equal(envelope.coverage.tickets_parsed, 2);
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['TCK-COLLAPSED', 'TCK-EXPANDED']);
});

test('fail closed: a leg with missing participants voids the WHOLE ticket, not just that leg', async () => {
  const { envelope } = await capture(
    ticket({
      ticketId: 'TCK-BAD-LEG',
      legs: [
        leg({ home: 'Arsenal', away: 'Chelsea', eventId: '1' }),
        `<div class="ticket__leg"><span class="leg__home"></span><span class="leg__away"></span><span class="leg__odds">1.50</span></div>`,
      ],
    })
  );
  assert.equal(envelope.coverage.tickets_parsed, 0);
  assert.equal(envelope.coverage.tickets_unresolved, 1);
  assert.equal(envelope.tickets.length, 0, 'the well-formed first leg must NOT be admitted on its own');
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
  assert.match(envelope.unresolved_tickets[0].detail, /leg\[1\]/);
});

test('fail closed: unparseable odds on any leg voids the whole ticket', async () => {
  const { envelope } = await capture(
    ticket({
      legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '1', odds: 'n/a' })],
    })
  );
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
});

test('missing ticket id: never guessed a synthetic one, routed to unresolved_tickets', async () => {
  const html = `
    <div class="ticket">
      <span class="ticket__status">OPEN</span>
      ${leg({ home: 'A', away: 'B', eventId: '1' })}
    </div>
  `;
  const { envelope } = await capture(html);
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets.length, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'MISSING_TICKET_ID');
});

test('live leg excludes the whole ticket (no live tickets, per scope boundary)', async () => {
  const { envelope } = await capture(
    ticket({
      legs: [leg({ home: 'A', away: 'B', eventId: '1' }), leg({ home: 'C', away: 'D', eventId: '2', statusHint: 'LIVE' })],
    })
  );
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.excluded_tickets.length, 1);
  assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG');
});

test('virtual and Zoom legs are excluded the same way as live', async () => {
  for (const hint of ['VIRTUAL', 'ZOOM']) {
    const { envelope } = await capture(ticket({ legs: [leg({ home: 'A', away: 'B', eventId: '1', statusHint: hint })] }));
    assert.equal(envelope.tickets.length, 0, hint);
    assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG', hint);
  }
});

test('settled/cashed-out tickets are excluded as out of scope, not treated as unresolved or errors', async () => {
  for (const status of ['SETTLED', 'WON', 'LOST', 'CASHED_OUT']) {
    const { envelope } = await capture(ticket({ status, legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }));
    assert.equal(envelope.tickets.length, 0, status);
    assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_STATUS_OUT_OF_SCOPE', status);
    assert.notEqual(envelope.capture_status, 'CAPTURE_FAILED');
  }
});

test('no explicit placement timestamp: never guessed via Date.parse, reported unresolved with raw text preserved', async () => {
  const html = `
    <div class="ticket" data-ticket-id="TCK-NOTIME">
      <span class="ticket__status">OPEN</span>
      <span class="ticket__placed-at">Today 14:32</span>
      ${leg({ home: 'A', away: 'B', eventId: '1' })}
    </div>
  `;
  const { envelope } = await capture(html);
  const t = envelope.tickets[0];
  assert.equal(t.placed_at_utc, null);
  assert.equal(t.placed_at_resolution, 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP');
  assert.equal(t.placed_at_raw, 'Today 14:32');
});

test('no source event id on a leg: falls back to a natural key, flagged as the weaker resolution path', async () => {
  const html = `
    <div class="ticket" data-ticket-id="TCK-NOEVENTID">
      <span class="ticket__status">OPEN</span>
      <div class="ticket__leg">
        <span class="leg__home">Arsenal</span>
        <span class="leg__away">Chelsea</span>
        <span class="leg__market">1X2</span>
        <span class="leg__selection">H</span>
        <span class="leg__odds">1.95</span>
      </div>
    </div>
  `;
  const { envelope } = await capture(html);
  const l = envelope.tickets[0].legs[0];
  assert.equal(l.source_event_id, null);
  assert.equal(l.fixture_id_resolution, 'NATURAL_KEY_FALLBACK_NO_SOURCE_EVENT_ID');
  assert.match(l.fixture_id, /^bxf_[0-9a-f]{16}$/);
});

test('no tickets found at all: CAPTURE_FAILED, not a silent empty success', async () => {
  const { envelope } = await capture('');
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_TICKETS_FOUND'));
  assert.equal(envelope.tickets.length, 0);
});

test('unrecognized ticket status is routed to unresolved_tickets, never silently included or dropped', async () => {
  const { envelope } = await capture(ticket({ status: 'WEIRD_STATUS', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }));
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'UNRECOGNIZED_TICKET_STATUS');
});

test('every capture always flags the placeholder-selector caveat, regardless of outcome', async () => {
  const { envelope } = await capture(ticket());
  assert.ok(envelope.capture_status_reasons.includes('TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER'));
});

test('privacy: unresolved/excluded ticket records never carry more than the documented allowlisted fields', async () => {
  const { envelope } = await capture(
    ticket({ status: 'SETTLED', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] })
  );
  const excluded = envelope.excluded_tickets[0];
  assert.deepEqual(Object.keys(excluded).sort(), ['detail', 'reason', 'source_index', 'ticket_id_raw'].sort());
});

test('coverage invariant: tickets_seen = tickets_parsed + tickets_unresolved + tickets_expected_excluded', async () => {
  const html =
    ticket({ ticketId: 'TCK-OK', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }) + // parsed
    ticket({ ticketId: 'TCK-BAD', legs: [leg({ home: '', away: '', eventId: '2' })] }) + // unresolved
    ticket({ ticketId: 'TCK-SETTLED', status: 'SETTLED', legs: [leg({ home: 'C', away: 'D', eventId: '3' })] }); // expected-excluded
  const { envelope } = await capture(html);
  const c = envelope.coverage;
  assert.equal(c.tickets_seen, 3);
  assert.equal(c.tickets_parsed, 1);
  assert.equal(c.tickets_unresolved, 1);
  assert.equal(c.tickets_expected_excluded, 1);
  assert.equal(c.tickets_seen, c.tickets_parsed + c.tickets_unresolved + c.tickets_expected_excluded);
});

test('ticket id stays stable regardless of an expanded/collapsed wrapper class around the same ticket markup', async () => {
  const ticketHtml = ticket({ ticketId: 'TCK-STABLE', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] });
  const collapsedDoc = docFromHtml(`<div class="open-bets"><div class="ticket-group collapsed">${ticketHtml}</div></div>`);
  const expandedDoc = docFromHtml(`<div class="open-bets"><div class="ticket-group expanded">${ticketHtml}</div></div>`);
  const collapsed = (await ticketParser.captureFromDocument(collapsedDoc, BASE_CONTEXT)).envelope;
  const expanded = (await ticketParser.captureFromDocument(expandedDoc, BASE_CONTEXT)).envelope;
  assert.equal(collapsed.tickets[0].bet9ja_ticket_id, 'TCK-STABLE');
  assert.equal(expanded.tickets[0].bet9ja_ticket_id, 'TCK-STABLE');
  assert.equal(collapsed.tickets[0].bet9ja_ticket_id, expanded.tickets[0].bet9ja_ticket_id);
});

test('total stake is preserved separately from each leg\'s own odds -- singles, accumulators, and system tickets alike', async () => {
  const single = (await capture(
    ticket({ unitStake: '10.00', totalStake: '10.00', legs: [leg({ home: 'A', away: 'B', eventId: '1', odds: '1.95' })] })
  )).envelope.tickets[0];
  assert.equal(single.total_stake, 10);
  assert.equal(single.legs[0].odds, 1.95);
  assert.notEqual(single.total_stake, single.legs[0].odds);

  const acca = (await capture(
    ticket({
      type: 'Treble',
      unitStake: '5.00',
      totalStake: '5.00',
      legs: [
        leg({ home: 'A', away: 'B', eventId: '1', odds: '1.90' }),
        leg({ home: 'C', away: 'D', eventId: '2', odds: '2.10' }),
        leg({ home: 'E', away: 'F', eventId: '3', odds: '1.50' }),
      ],
    })
  )).envelope.tickets[0];
  assert.equal(acca.total_stake, 5);
  assert.deepEqual(acca.legs.map((l) => l.odds), [1.9, 2.1, 1.5]);

  const system = (await capture(
    ticket({
      type: 'System',
      unitStake: '2.00',
      totalStake: '12.00',
      legs: [
        leg({ home: 'A', away: 'B', eventId: '1', odds: '1.80' }),
        leg({ home: 'C', away: 'D', eventId: '2', odds: '1.70' }),
        leg({ home: 'E', away: 'F', eventId: '3', odds: '1.60' }),
        leg({ home: 'G', away: 'H', eventId: '4', odds: '1.50' }),
      ],
    })
  )).envelope.tickets[0];
  assert.equal(system.unit_stake, 2);
  assert.equal(system.total_stake, 12, 'total_stake is a distinct field from unit_stake -- never derived from leg odds');
  assert.equal(system.legs.length, 4);
});

test('privacy: a ticket embedded inside unrelated page chrome (nav/balance/footer) never leaks that chrome', async () => {
  const doc = docFromHtml(`
    <body>
      <nav class="site-nav">Account balance: $482.10 | Log out | Betslip (3)</nav>
      <div class="open-bets">
        ${ticket({ legs: [`<div class="ticket__leg"><span class="leg__home"></span><span class="leg__away"></span><span class="leg__odds">1.50</span></div>`] })}
      </div>
      <footer>Copyright Bet9ja. Your session token: abc123.</footer>
    </body>
  `);
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  const rawText = JSON.stringify(envelope.unresolved_tickets).toLowerCase();
  for (const banned of ['balance', 'account', 'betslip', 'cookie', 'token', 'password', 'login', 'session']) {
    assert.ok(!rawText.includes(banned), `unresolved_tickets unexpectedly contains "${banned}"`);
  }
});

// --- MYBETS profile: real selectors confirmed 2026-09-11, see -----------
// TICKET_REAL_PAGE_VALIDATION.md Round 2 and ticket_parser.js's own
// "MYBETS PROFILE" header comment for exactly what is/isn't confirmed.
//
// jsdom's real accordion click/class-toggle behavior is simulated with a
// plain inline <script> attaching a click listener that toggles the
// `.accordion-item--open` class synchronously -- close enough to a real
// (fast) UI transition to exercise ensureTicketExpanded's wait loop
// without needing real timers beyond one polling interval.

function mybetsLeg({
  selection = 'Home Win',
  odds = '1.95',
  market = '1X2',
  fixtureAndTime = 'Arsenal - Chelsea, 20 Sep 15:00',
  competition = 'England - Premier League',
  eventId,
} = {}) {
  const idAttr = eventId ? ` id="prematch_event-${eventId}"` : '';
  return `
    <div class="mybets-item">
      <div class="mybets-item__row">
        <span class="mybets-bet"${idAttr}>${selection}</span>
        <span class="mybets-odd">${odds}</span>
      </div>
      <div class="mybets-item__row">${market}</div>
      <div class="mybets-item__row">${fixtureAndTime}</div>
      <div class="mybets-item__row">${competition}</div>
    </div>
  `;
}

function mybetsTicket({
  ticketId = '9001234567',
  placedAtRaw = 'Today 14:32',
  legs = [mybetsLeg()],
  systemTable = '',
  infoItems = ['Stake: 10.00', 'Max Return: 19.50'],
  open = false,
} = {}) {
  return `
    <div class="accordion-item${open ? ' accordion-item--open' : ''}">
      <div class="accordion-toggle">Toggle</div>
      <div class="mybets-holder">
        <span class="mybets-date">${placedAtRaw}</span>
        ${infoItems.map((t) => `<span class="mybets-holder__info-item">${t}</span>`).join('')}
      </div>
      <div class="mybets-head__item">Ticket ID: ${ticketId}</div>
      ${systemTable ? `<div class="mybets__systable">${systemTable}</div>` : ''}
      ${legs.join('\n')}
    </div>
  `;
}

function mybetsCaptureHtml(ticketsHtml) {
  return `
    <body>
      <div class="account-info">Balance: 482.10 | Log out</div>
      <div class="mybets">${ticketsHtml}</div>
      <script>
        document.querySelectorAll('.accordion-item').forEach((item) => {
          const toggle = item.querySelector('.accordion-toggle');
          if (toggle) {
            toggle.addEventListener('click', () => {
              item.classList.toggle('accordion-item--open');
            });
          }
        });
      </script>
    </body>
  `;
}

async function captureMybets(ticketsHtml, extraContext) {
  const doc = docFromHtml(mybetsCaptureHtml(ticketsHtml));
  return ticketParser.captureFromDocument(doc, { ...BASE_CONTEXT, ...extraContext });
}

// --- Pagination: real selectors confirmed Round 3, see -----------------
// TICKET_REAL_PAGE_VALIDATION.md and ticket_parser.js's own PAGINATION
// header comment. This harness simulates client-side pagination (the URL
// never changes) by swapping a ticket-list container's innerHTML on a
// numbered-page click and moving the `--current` marker -- close enough
// to the real client-side transition to exercise the wait-for-advance
// loop without needing real network/navigation.
function mybetsPaginationHtml({ pageTicketsHtmlList, startPage = 1, brokenPageNumber = null }) {
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
      <div class="account-info">Balance: 482.10 | Log out</div>
      <div class="mybets">
        <div id="ticket-list">${pageTicketsHtmlList[startPage - 1]}</div>
        <div class="pg-pagination">${items.join('')}</div>
      </div>
      <script>
        const PAGES = ${JSON.stringify(pageTicketsHtmlList)};
        const BROKEN_PAGE = ${brokenPageNumber === null ? 'null' : brokenPageNumber};
        window.__clickCounts = { first: 0, prev: 0, next: 0, last: 0 };
        const list = document.getElementById('ticket-list');
        const pagination = document.querySelector('.pg-pagination');

        function attachAccordionListeners() {
          list.querySelectorAll('.accordion-item').forEach((item) => {
            const toggle = item.querySelector('.accordion-toggle');
            if (toggle) {
              toggle.addEventListener('click', () => {
                item.classList.toggle('accordion-item--open');
              });
            }
          });
        }
        attachAccordionListeners();

        pagination.querySelectorAll('.pg-pagination__item').forEach((el) => {
          const p = el.getAttribute('data-page');
          if (p) {
            el.addEventListener('click', () => {
              const pageNum = parseInt(p, 10);
              list.innerHTML = PAGES[pageNum - 1];
              // Simulate a broken transition: content swaps, but the
              // --current marker never moves -- the exact failure mode
              // PAGE_TRANSITION_TIMEOUT exists to catch.
              if (pageNum !== BROKEN_PAGE) {
                pagination.querySelectorAll('.pg-pagination__item').forEach((x) => x.classList.remove('pg-pagination__item--current'));
                el.classList.add('pg-pagination__item--current');
              }
              attachAccordionListeners();
            });
          } else if (el.classList.contains('first')) {
            el.addEventListener('click', () => { window.__clickCounts.first += 1; });
          } else if (el.classList.contains('prev')) {
            el.addEventListener('click', () => { window.__clickCounts.prev += 1; });
          } else if (el.classList.contains('next')) {
            el.addEventListener('click', () => { window.__clickCounts.next += 1; });
          } else if (el.classList.contains('last')) {
            el.addEventListener('click', () => { window.__clickCounts.last += 1; });
          }
        });
      </script>
    </body>
  `;
}

test('mybets pagination: walks every numbered page, merges tickets, stops at the highest page', async () => {
  const pages = [1, 2, 3].map((p) =>
    mybetsTicket({ ticketId: `9000000${p}`, legs: [mybetsLeg({ selection: `Page ${p}`, eventId: `${p}00` })] })
  );
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.equal(envelope.coverage.pages_available, 3);
  assert.equal(envelope.coverage.pages_visited, 3);
  assert.equal(envelope.coverage.tickets_parsed, 3);
  assert.ok(envelope.capture_status_reasons.includes('PAGINATION_STOPPED_NO_FURTHER_NUMBERED_PAGE'));
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['90000001', '90000002', '90000003']);
});

test('mybets pagination: never clicks first/prev/next/last -- only verified numbered items', async () => {
  const pages = [1, 2, 3].map((p) => mybetsTicket({ ticketId: `NUM${p}`, legs: [mybetsLeg({ eventId: `${p}0` })] }));
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  // Spread into a plain object of the current realm first -- __clickCounts
  // was created inside jsdom's own sandboxed global, whose Object is a
  // distinct constructor from this test file's, which trips assert's
  // cross-realm identity check even though the values are equal.
  const counts = { ...doc.defaultView.__clickCounts };
  assert.deepEqual(counts, { first: 0, prev: 0, next: 0, last: 0 });
});

test('mybets pagination: restores the browser to page 1 after a multi-page walk', async () => {
  const pages = [1, 2, 3].map((p) => mybetsTicket({ ticketId: `RST${p}`, legs: [mybetsLeg({ eventId: `${p}1` })] }));
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  const currentEl = doc.querySelector('.pg-pagination__item--current');
  assert.equal(currentEl && currentEl.textContent.trim(), '1');
  assert.match(doc.getElementById('ticket-list').innerHTML, /RST1/);
});

test('mybets pagination: a ticket reappearing across pages is deduplicated by ticket id, not double-counted', async () => {
  const shared = mybetsTicket({ ticketId: '8000000000', legs: [mybetsLeg({ eventId: '500' })] });
  const page1 = shared + mybetsTicket({ ticketId: '8000000011', legs: [mybetsLeg({ eventId: '501' })] });
  const page2 = shared + mybetsTicket({ ticketId: '8000000012', legs: [mybetsLeg({ eventId: '502' })] });
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: [page1, page2] }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.equal(envelope.coverage.tickets_seen, 4);
  assert.equal(envelope.coverage.tickets_parsed, 3);
  assert.equal(envelope.coverage.duplicate_tickets_skipped, 1);
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['8000000000', '8000000011', '8000000012']);
});

test('mybets pagination: stops when a page\'s content exactly repeats a previous page (transition looked successful but content did not change)', async () => {
  const stuckTicket = mybetsTicket({ ticketId: 'STUCK-1', legs: [mybetsLeg({ eventId: '600' })] });
  // Three "pages" configured, but page 2 and page 3 both serve the exact
  // same ticket-id set as page 1 -- simulating a page that never actually
  // advances its content despite the --current marker moving.
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: [stuckTicket, stuckTicket, stuckTicket] }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.ok(envelope.capture_status_reasons.includes('PAGINATION_STOPPED_PAGE_CONTENT_REPEATED'));
  assert.equal(envelope.coverage.pages_visited, 2, 'must stop as soon as repeated content is detected, not keep going');
  assert.equal(envelope.coverage.tickets_parsed, 1, 'the repeated ticket must still only be counted once');
});

test('mybets pagination: a page transition that never moves the --current marker is a timeout, not a silent stop', async () => {
  const pages = [1, 2].map((p) => mybetsTicket({ ticketId: `TO${p}`, legs: [mybetsLeg({ eventId: `${p}9` })] }));
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages, brokenPageNumber: 2 }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.ok(envelope.capture_status_reasons.includes('PAGINATION_STOPPED_PAGE_TRANSITION_TIMEOUT'));
  assert.equal(envelope.coverage.pages_visited, 1, 'page 2 never confirmed, so only page 1 counts as visited');
  assert.equal(envelope.coverage.tickets_parsed, 1);
});

test('mybets pagination: coverage always exposes pages_available, pages_visited, and duplicate_tickets_skipped', async () => {
  const pages = [1, 2].map((p) => mybetsTicket({ ticketId: `COV${p}`, legs: [mybetsLeg({ eventId: `${p}8` })] }));
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.coverage.pages_available, 2);
  assert.equal(envelope.coverage.pages_visited, 2);
  assert.equal(envelope.coverage.duplicate_tickets_skipped, 0);
  assert.equal(envelope.coverage.tickets_seen, envelope.coverage.tickets_parsed);
});

test('mybets pagination: 16 pages of 5 tickets each accumulate to 80 ticket containers before deduplication (Round 4 regression)', async () => {
  const pages = [];
  for (let p = 1; p <= 16; p += 1) {
    const legs = [1, 2, 3, 4, 5].map((n) =>
      mybetsTicket({ ticketId: `${7000000000 + p * 10 + n}`, legs: [mybetsLeg({ eventId: `${p}${n}` })] })
    );
    pages.push(legs.join('\n'));
  }
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.equal(envelope.coverage.pages_available, 16);
  assert.equal(envelope.coverage.pages_visited, 16);
  assert.equal(envelope.coverage.tickets_seen, 80, '16 pages x 5 tickets must all be counted, not just the first page');
  assert.equal(envelope.coverage.tickets_parsed, 80);
  assert.equal(envelope.coverage.duplicate_tickets_skipped, 0);
  assert.equal(
    envelope.coverage.tickets_seen,
    envelope.coverage.tickets_parsed + envelope.coverage.tickets_unresolved + envelope.coverage.tickets_expected_excluded
  );
});

test('mybets pagination: page_results records per-page evidence for every visited page', async () => {
  const pages = [1, 2, 3].map((p) =>
    mybetsTicket({ ticketId: `${6000000000 + p}`, legs: [mybetsLeg({ eventId: `${p}00` })] })
  );
  const doc = docFromHtml(mybetsPaginationHtml({ pageTicketsHtmlList: pages }));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.equal(envelope.page_results.length, 3);
  const pageNumbers = envelope.page_results.map((p) => p.page_number);
  assert.deepEqual(pageNumbers, [1, 2, 3]);
  for (const page of envelope.page_results) {
    assert.equal(page.ticket_containers_seen, 1);
    assert.equal(page.tickets_parsed, 1);
    assert.equal(page.tickets_unresolved, 0);
    assert.equal(page.tickets_expected_excluded, 0);
    assert.equal(typeof page.page_fingerprint, 'string');
  }
  // Each page's fingerprint must be distinct -- otherwise this looks
  // exactly like the PAGE_CONTENT_REPEATED case this parser guards
  // against.
  const fingerprints = new Set(envelope.page_results.map((p) => p.page_fingerprint));
  assert.equal(fingerprints.size, 3);
});

test('mybets pagination: a page whose ticket list finishes rendering shortly AFTER --current advances is still parsed, not read empty (Round 4 fix)', async () => {
  // Simulates the exact real-page defect: the pagination click handler
  // moves --current immediately, but the new page's own ticket markup is
  // appended a beat later (a real network/render delay). Without the
  // post-transition content wait, page 2 would be parsed as empty.
  const page1 = mybetsTicket({ ticketId: '5000000001', legs: [mybetsLeg({ eventId: '1' })] });
  const page2 = mybetsTicket({ ticketId: '5000000002', legs: [mybetsLeg({ eventId: '2' })] });
  const doc = docFromHtml(`
    <body>
      <div class="mybets">
        <div id="ticket-list">${page1}</div>
        <div class="pg-pagination">
          <span class="pg-pagination__item pg-pagination__item--current" data-page="1">1</span>
          <span class="pg-pagination__item" data-page="2">2</span>
        </div>
      </div>
      <script>
        const list = document.getElementById('ticket-list');
        const pagination = document.querySelector('.pg-pagination');
        function attachAccordionListeners() {
          list.querySelectorAll('.accordion-item').forEach((item) => {
            const toggle = item.querySelector('.accordion-toggle');
            if (toggle) toggle.addEventListener('click', () => item.classList.toggle('accordion-item--open'));
          });
        }
        attachAccordionListeners();
        pagination.querySelector('[data-page="2"]').addEventListener('click', function () {
          pagination.querySelectorAll('.pg-pagination__item').forEach((x) => x.classList.remove('pg-pagination__item--current'));
          this.classList.add('pg-pagination__item--current');
          // --current moves synchronously; the ticket list itself only
          // updates after a short delay, exactly mirroring the real page.
          list.innerHTML = '';
          setTimeout(() => {
            list.innerHTML = ${JSON.stringify(page2)};
            attachAccordionListeners();
          }, 150);
        });
      </script>
    </body>
  `);
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);

  assert.equal(envelope.coverage.pages_visited, 2);
  assert.equal(envelope.coverage.tickets_seen, 2, 'page 2 must not be read empty just because --current advanced first');
  assert.equal(envelope.coverage.tickets_parsed, 2);
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['5000000001', '5000000002']);
});

test('mybets: a collapsed ticket is expanded, parsed, and collapsed again', async () => {
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: '9001234567' }));
  assert.equal(envelope.coverage.tickets_seen, 1);
  assert.equal(envelope.coverage.tickets_parsed, 1);
  assert.ok(envelope.capture_status_reasons.includes('MYBETS_SELECTOR_PROFILE_ACTIVE'));

  const t = envelope.tickets[0];
  assert.equal(t.bet9ja_ticket_id, '9001234567');
  assert.equal(t.status, 'OPEN');
  assert.equal(t.status_resolution, 'INFERRED_FROM_OPEN_BETS_PAGE_NO_EXPLICIT_STATUS_MARKUP_CONFIRMED');
  assert.equal(t.placed_at_raw, 'Today 14:32');
  assert.equal(t.placed_at_utc, null, 'no confirmed UTC timestamp markup -- never guessed');
});

test('mybets: an already-expanded ticket is left expanded afterward (never force-collapsed)', async () => {
  const doc = docFromHtml(mybetsCaptureHtml(mybetsTicket({ ticketId: '111', open: true })));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.tickets.length, 1);
  const ticketEl = doc.querySelector('.accordion-item');
  assert.ok(ticketEl.classList.contains('accordion-item--open'), 'a ticket already open before capture must remain open after');
});

test('mybets: a collapsed ticket is restored to collapsed after capture', async () => {
  const doc = docFromHtml(mybetsCaptureHtml(mybetsTicket({ ticketId: '222', open: false })));
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.tickets.length, 1);
  const ticketEl = doc.querySelector('.accordion-item');
  assert.ok(!ticketEl.classList.contains('accordion-item--open'), 'a ticket this capture opened must be restored to collapsed');
});

test('mybets: five tickets on one page -- each expanded, parsed, and collapsed in its own turn, never cross-contaminated', async () => {
  const html = [1, 2, 3, 4, 5]
    .map((n) =>
      mybetsTicket({
        ticketId: `90012345${n}`,
        legs: [mybetsLeg({ selection: `Selection ${n}`, odds: `${1 + n / 10}`, eventId: `${n}` })],
      })
    )
    .join('\n');
  const { envelope } = await captureMybets(html);
  assert.equal(envelope.coverage.tickets_seen, 5);
  assert.equal(envelope.coverage.tickets_parsed, 5);
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['900123451', '900123452', '900123453', '900123454', '900123455']);
  const oddsByTicket = Object.fromEntries(envelope.tickets.map((t) => [t.bet9ja_ticket_id, t.legs[0].odds]));
  assert.equal(oddsByTicket['900123451'], 1.1);
  assert.equal(oddsByTicket['900123455'], 1.5);
});

test('mybets: a system ticket\'s 6 legs (3 rows of 2) are all captured, its system table marks ticket_type_normalized SYSTEM, and confirmed stake columns are mapped (Round 5)', async () => {
  const legs = [1, 2, 3, 4, 5, 6].map((n) => mybetsLeg({ selection: `Sel ${n}`, eventId: `${n}` }));
  const { envelope } = await captureMybets(
    mybetsTicket({
      ticketId: 'SYS-1',
      legs,
      // Real confirmed shape (a real Round 5 ticket, "Trebles" type: a
      // letters-only System Type, unambiguously splittable): fixed
      // header run immediately followed by the four values concatenated
      // with no separators. 56 x 3.00 = 168.00.
      systemTable: 'System TypeNo.BetsUnit StakeStakeTrebles563.00168.00',
      infoItems: ['Stake: 168.00', 'Max Win: 1,308.10'],
    })
  );
  const t = envelope.tickets[0];
  assert.equal(t.legs.length, 6);
  assert.equal(t.ticket_type_normalized, 'SYSTEM');
  assert.equal(t.ticket_type_raw, 'Trebles');
  assert.match(t.system_table_raw, /System Type/);
  assert.equal(t.unit_stake, 3);
  assert.equal(t.total_stake, 168);
  assert.equal(t.potential_return, 1308.1, 'comma thousands separator must not corrupt the parsed amount');
});

test('mybets: a System Type starting with a digit ("N Folds") is left unparsed rather than guessed -- total_stake/potential_return are unaffected (Round 5)', async () => {
  // Real Round 5 example: the digit-prefixed type text makes the
  // No.Bets/Unit Stake split genuinely ambiguous from text alone (see
  // parseSystemTableRaw's own comment) -- unlike the letters-only cases,
  // this is never guessed at.
  const { envelope } = await captureMybets(
    mybetsTicket({
      systemTable: 'System TypeNo.BetsUnit StakeStake6 Folds283.0084.00',
      infoItems: ['Stake: 84.00', 'Max Win: 1,308.10'],
    })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_type_normalized, 'SYSTEM', 'presence of a system table alone still confirms SYSTEM');
  assert.equal(t.ticket_type_raw, null, 'the digit-prefixed type text is not confidently splittable');
  assert.equal(t.unit_stake, null);
  // Unaffected -- these always come from the info items, never the table.
  assert.equal(t.total_stake, 84);
  assert.equal(t.potential_return, 1308.1);
});

test('mybets: a multi-row full-cover system table (two type+value groups concatenated) is left unparsed rather than guessed (Round 5)', async () => {
  // Real Round 5 example: "Doubles...Trebles..." -- two complete
  // type+value groups in one string, with no reliable way to tell where
  // one ends and the next begins from text alone.
  const { envelope } = await captureMybets(
    mybetsTicket({
      systemTable: 'System TypeNo.BetsUnit StakeStakeDoubles156.0090.00Trebles202.0040.00',
      infoItems: ['Stake: 130.00', 'Max Win: 720.46'],
    })
  );
  const t = envelope.tickets[0];
  assert.equal(t.ticket_type_raw, null);
  assert.equal(t.unit_stake, null);
  assert.equal(t.total_stake, 130);
  assert.equal(t.potential_return, 720.46);
});

test('mybets: fail closed -- a leg with an unexpected row count voids the whole ticket', async () => {
  const malformedLeg = `<div class="mybets-item"><div class="mybets-item__row"><span class="mybets-bet">X</span><span class="mybets-odd">1.50</span></div></div>`;
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: 'BAD-ROWS', legs: [malformedLeg] }));
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
  assert.match(envelope.unresolved_tickets[0].detail, /LEG_UNEXPECTED_ROW_COUNT/);
});

test('mybets: a 3-row leg (competition omitted) is ACCEPTED, not a failure -- Round 5 real-capture correction', async () => {
  const threeRowLeg = `
    <div class="mybets-item">
      <div class="mybets-item__row"><span class="mybets-bet">Team A</span><span class="mybets-odd">1.50</span></div>
      <div class="mybets-item__row">1X2</div>
      <div class="mybets-item__row">Team A - Team B11 Sep 19:00</div>
    </div>
  `;
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: 'THREE-ROWS', legs: [threeRowLeg] }));
  assert.equal(envelope.tickets.length, 1);
  const leg = envelope.tickets[0].legs[0];
  assert.equal(leg.competition_raw, null);
  assert.equal(leg.competition_resolution, 'COMPETITION_UNAVAILABLE');
  assert.equal(leg.selection, 'Team A');
  assert.equal(leg.selection_raw, 'Team A');
});

test('mybets: a 2-row leg is still an unexpected row count, and preserves each found row\'s own text for audit', async () => {
  const twoRowLeg = `
    <div class="mybets-item">
      <div class="mybets-item__row"><span class="mybets-bet">X</span><span class="mybets-odd">1.50</span></div>
      <div class="mybets-item__row">1X2</div>
    </div>
  `;
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: 'TWO-ROWS', legs: [twoRowLeg] }));
  const legRaw = envelope.unresolved_tickets[0].raw.leg_raw;
  assert.equal(legRaw.row_count, 2);
  assert.deepEqual(legRaw.row_texts, ['X1.50', '1X2']);
  assert.equal(legRaw.leg_element_text, null, 'leg_element_text is only populated for the 0-row case');
});

test('mybets: a leg with zero .mybets-item__row children is excluded at candidacy, never treated as a fail-closed leg', async () => {
  const zeroRowLeg = `<div class="mybets-item">Cashout available</div>`;
  const { envelope } = await captureMybets(
    mybetsTicket({ ticketId: 'HAS-STRUCTURAL', legs: [mybetsLeg({ eventId: '1' }), zeroRowLeg] })
  );
  // The structural 0-row element is silently excluded -- the ticket still
  // parses cleanly from its one genuine leg, never voided because of it.
  assert.equal(envelope.tickets.length, 1);
  assert.equal(envelope.tickets[0].legs.length, 1);
});

test('mybets: a ticket whose ONLY .mybets-item is a 0-row structural element reports NO_LEGS_FOUND, never invents a leg', async () => {
  const zeroRowLeg = `<div class="mybets-item">Cashout available</div>`;
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: 'ONLY-STRUCTURAL', legs: [zeroRowLeg] }));
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'NO_LEGS_FOUND');
});

test('mybets: more than 4 rows is also an unexpected row count, never silently truncated to the first 4', async () => {
  const fiveRowLeg = `
    <div class="mybets-item">
      <div class="mybets-item__row"><span class="mybets-bet">X</span><span class="mybets-odd">1.50</span></div>
      <div class="mybets-item__row">1X2</div>
      <div class="mybets-item__row">Team A - Team B</div>
      <div class="mybets-item__row">England - Premier League</div>
      <div class="mybets-item__row">Extra row</div>
    </div>
  `;
  const { envelope } = await captureMybets(mybetsTicket({ ticketId: 'FIVE-ROWS', legs: [fiveRowLeg] }));
  assert.equal(envelope.tickets.length, 0);
  assert.match(envelope.unresolved_tickets[0].detail, /found 5/);
});

test('mybets: fail closed -- unparseable odds on any leg voids the whole ticket', async () => {
  const { envelope } = await captureMybets(mybetsTicket({ legs: [mybetsLeg({ odds: 'n/a' })] }));
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
});

test('mybets: no .mybets-head__item ever appears -- reported as an expansion timeout, never reaches ticket-id parsing at all', async () => {
  // Round 4 correction: ensureTicketExpanded now requires the head item
  // to exist before considering a ticket ready -- a ticket that opens
  // but never gets one times out here, rather than falling through to
  // processMybetsTicket's own (still-reachable, see the next test)
  // MISSING_TICKET_ID path.
  const html = `
    <div class="accordion-item">
      <div class="accordion-toggle">Toggle</div>
      <div class="mybets-holder"><span class="mybets-date">Today 14:32</span></div>
      ${mybetsLeg()}
    </div>
  `;
  const { envelope } = await captureMybets(html);
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'TICKET_EXPANSION_TIMEOUT');
});

test('mybets: a .mybets-head__item exists but its text yields no id -- still MISSING_TICKET_ID, never guessed', async () => {
  const html = `
    <div class="accordion-item">
      <div class="accordion-toggle">Toggle</div>
      <div class="mybets-holder"><span class="mybets-date">Today 14:32</span></div>
      <div class="mybets-head__item"></div>
      ${mybetsLeg()}
    </div>
  `;
  const { envelope } = await captureMybets(html);
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'MISSING_TICKET_ID');
});

test('mybets: a toggle that never adds the open class is reported as an expansion timeout, never treated as empty', async () => {
  const html = `
    <div class="mybets">
      <div class="accordion-item">
        <div class="accordion-toggle">Toggle</div>
        <div class="mybets-head__item">Ticket ID: 555</div>
        ${mybetsLeg()}
      </div>
    </div>
  `;
  // No click listener attached in this doc -- the toggle click is a no-op,
  // so the open class never appears.
  const doc = docFromHtml(`<body>${html}</body>`);
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'TICKET_EXPANSION_TIMEOUT');
});

test('mybets: selection always mirrors the trimmed selection_raw verbatim -- named selections are never discarded, numeric ones pass through unchanged (Round 5)', async () => {
  const named = await captureMybets(mybetsTicket({ legs: [mybetsLeg({ selection: 'Real Madrid' })] }));
  assert.equal(named.envelope.tickets[0].legs[0].selection, 'Real Madrid');

  const numeric = await captureMybets(mybetsTicket({ legs: [mybetsLeg({ selection: '1' })] }));
  assert.equal(numeric.envelope.tickets[0].legs[0].selection, '1');

  const handicap = await captureMybets(mybetsTicket({ legs: [mybetsLeg({ selection: 'Czechia (Home -1.5)' })] }));
  assert.equal(handicap.envelope.tickets[0].legs[0].selection, 'Czechia (Home -1.5)');
});

test('mybets: source_event_id and fixture_id are exposed per leg, matching the fixture-capture extension\'s own scheme', async () => {
  const Bet9jaIds = require('../ids.js');
  const expected = Bet9jaIds.stableId('bxf', ['external', 'bet9ja-event-999']);
  const { envelope } = await captureMybets(mybetsTicket({ legs: [mybetsLeg({ eventId: '999' })] }));
  const l = envelope.tickets[0].legs[0];
  assert.equal(l.source_event_id, '999');
  assert.equal(l.fixture_id, expected);
  assert.equal(l.fixture_id_resolution, 'EXTERNAL_EVENT_ID');
});

test('mybets: no source_event_id falls back to the natural-key resolution, flagged as such', async () => {
  const { envelope } = await captureMybets(mybetsTicket({ legs: [mybetsLeg()] }));
  const l = envelope.tickets[0].legs[0];
  assert.equal(l.source_event_id, null);
  assert.equal(l.fixture_id_resolution, 'NATURAL_KEY_FALLBACK_NO_SOURCE_EVENT_ID');
  assert.match(l.fixture_id, /^bxf_[0-9a-f]{16}$/);
});

test('mybets: coverage invariant holds (tickets_seen = tickets_parsed + tickets_unresolved + tickets_expected_excluded)', async () => {
  const html =
    mybetsTicket({ ticketId: 'OK-1' }) +
    `<div class="accordion-item"><div class="accordion-toggle">Toggle</div>${mybetsLeg()}</div>`; // no ticket id
  const { envelope } = await captureMybets(html);
  const c = envelope.coverage;
  assert.equal(c.tickets_seen, 2);
  assert.equal(c.tickets_parsed, 1);
  assert.equal(c.tickets_unresolved, 1);
  assert.equal(c.tickets_seen, c.tickets_parsed + c.tickets_unresolved + c.tickets_expected_excluded);
});

test('mybets: never CAPTURE_OK -- unconfirmed live/Virtual/Zoom detection and stake mapping cap it at CAPTURE_PARTIAL', async () => {
  const { envelope } = await captureMybets(mybetsTicket());
  assert.equal(envelope.capture_status, 'CAPTURE_PARTIAL');
  assert.ok(envelope.capture_status_reasons.includes('LIVE_VIRTUAL_ZOOM_DETECTION_UNCONFIRMED_FOR_MYBETS_PROFILE'));
  assert.ok(envelope.capture_status_reasons.includes('STAKE_RETURN_FIELD_MAPPING_CONFIRMED_ONLY_FOR_SYSTEM_TICKETS'));
});

test('mybets: no pagination container -- single page mode, coverage names pages_available=1/pages_visited=1', async () => {
  const { envelope } = await captureMybets(mybetsTicket());
  assert.equal(envelope.coverage.pages_available, 1);
  assert.equal(envelope.coverage.pages_visited, 1);
  assert.equal(envelope.coverage.pagination_automated, true);
  assert.ok(envelope.capture_status_reasons.includes('PAGINATION_STOPPED_NO_PAGINATION_CONTROL_FOUND'));
});

test('mybets: no tickets found under .mybets is still CAPTURE_FAILED, not a silent empty success', async () => {
  const doc = docFromHtml('<body><div class="mybets"></div></body>');
  const { envelope } = await ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_TICKETS_FOUND'));
});

test('mybets: privacy -- account info rendered outside .mybets never leaks into any ticket or leg raw field', async () => {
  const { envelope } = await captureMybets(mybetsTicket());
  const dump = JSON.stringify(envelope).toLowerCase();
  for (const banned of ['balance', '482.10', 'log out']) {
    assert.ok(!dump.includes(banned), `capture envelope unexpectedly contains "${banned}"`);
  }
});

test('safety: ticket_parser.js only ever calls .click() on the confirmed accordion toggle or a verified numbered pagination item, never a cashout/reload/betting control', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.join(__dirname, '..', 'ticket_parser.js'), 'utf-8');
  // Strip comments first -- "cashout" and "reload" are deliberately named
  // in this file's own documentation (the exclusion-zone comments), which
  // is the opposite of a violation; only EXECUTABLE code is checked here.
  const codeOnly = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');

  // Every allowed click target's variable name is itself only ever
  // assigned from a `.accordion-toggle` query or filtered through
  // isVerifiedNumberedPaginationItem -- see ensureTicketExpanded,
  // collapseIfNeeded, and paginateAndCaptureAllPages. This test only
  // checks the click call sites themselves (never/first/prev/last/
  // cashout/reload must never appear as a click target); it does not
  // re-verify the upstream guard logic, which the "mybets" behavioral
  // tests above exercise instead.
  const ALLOWED_CLICK_CALLS = new Set(['toggle.click()', 'nextItem.click()', 'firstItem.click()']);
  const clickCalls = codeOnly.match(/\w+\.click\(\)/g) || [];
  assert.ok(clickCalls.length > 0, 'expected at least one .click() call');
  for (const call of clickCalls) {
    assert.ok(ALLOWED_CLICK_CALLS.has(call), `unexpected click target: ${call}`);
  }
  assert.ok(!/cashout/i.test(codeOnly), 'ticket_parser.js must never reference cashout by name in executable code');
  assert.ok(!/reload/i.test(codeOnly), 'ticket_parser.js must never reference "reload" by name in executable code');
  assert.ok(!codeOnly.includes('MYBETS_SELECTORS.cashoutHolder'), 'cashoutHolder must never be defined as a queryable selector');
});
