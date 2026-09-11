const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const ticketParser = require('../ticket_parser.js');
const { BASE_CONTEXT } = require('./helpers.js');

function docFromHtml(html) {
  return new JSDOM(html).window.document;
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

function capture(html, extraContext) {
  const doc = docFromHtml(`<div class="open-bets">${html}</div>`);
  return ticketParser.captureFromDocument(doc, { ...BASE_CONTEXT, ...extraContext });
}

test('single ticket: one leg fully parsed', () => {
  const { envelope } = capture(
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

test('fixture_id matches the fixture-capture extension\'s own scheme for the same event id', () => {
  const parser = require('../parser.js');
  const Bet9jaIds = require('../ids.js');
  const expected = Bet9jaIds.stableId('bxf', ['external', 'bet9ja-event-832455154']);
  const { envelope } = capture(
    ticket({ legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '832455154' })] })
  );
  assert.equal(envelope.tickets[0].legs[0].fixture_id, expected);
  assert.ok(parser.PARSER_VERSION); // sanity: both modules load independently, no shared state
});

test('accumulator ticket: multiple legs, distinct fixture ids, unambiguous SYSTEM/DOUBLE/TREBLE normalize', () => {
  const { envelope } = capture(
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

test('unrecognized ticket type (e.g. a straight all-up "Accumulator" category): raw text kept, taxonomy gap flagged, never guessed', () => {
  const { envelope } = capture(
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

test('system ticket: normalizes to SYSTEM, all legs captured', () => {
  const { envelope } = capture(
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

test('two tickets, each with its own leg scan: repeated fixture across separate tickets never merges or cross-contaminates', () => {
  const { envelope } = capture(
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

test('expanded vs collapsed view: both find the same tickets when both are present in the DOM', () => {
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
  const { envelope } = capture(html);
  assert.equal(envelope.coverage.tickets_seen, 2);
  assert.equal(envelope.coverage.tickets_parsed, 2);
  const ids = envelope.tickets.map((t) => t.bet9ja_ticket_id).sort();
  assert.deepEqual(ids, ['TCK-COLLAPSED', 'TCK-EXPANDED']);
});

test('fail closed: a leg with missing participants voids the WHOLE ticket, not just that leg', () => {
  const { envelope } = capture(
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

test('fail closed: unparseable odds on any leg voids the whole ticket', () => {
  const { envelope } = capture(
    ticket({
      legs: [leg({ home: 'Arsenal', away: 'Chelsea', eventId: '1', odds: 'n/a' })],
    })
  );
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'LEG_FAILED_TO_PARSE');
});

test('missing ticket id: never guessed a synthetic one, routed to unresolved_tickets', () => {
  const html = `
    <div class="ticket">
      <span class="ticket__status">OPEN</span>
      ${leg({ home: 'A', away: 'B', eventId: '1' })}
    </div>
  `;
  const { envelope } = capture(html);
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets.length, 1);
  assert.equal(envelope.unresolved_tickets[0].reason, 'MISSING_TICKET_ID');
});

test('live leg excludes the whole ticket (no live tickets, per scope boundary)', () => {
  const { envelope } = capture(
    ticket({
      legs: [leg({ home: 'A', away: 'B', eventId: '1' }), leg({ home: 'C', away: 'D', eventId: '2', statusHint: 'LIVE' })],
    })
  );
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.excluded_tickets.length, 1);
  assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG');
});

test('virtual and Zoom legs are excluded the same way as live', () => {
  for (const hint of ['VIRTUAL', 'ZOOM']) {
    const { envelope } = capture(ticket({ legs: [leg({ home: 'A', away: 'B', eventId: '1', statusHint: hint })] }));
    assert.equal(envelope.tickets.length, 0, hint);
    assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_EXCLUDED_LIVE_OR_VIRTUAL_OR_ZOOM_LEG', hint);
  }
});

test('settled/cashed-out tickets are excluded as out of scope, not treated as unresolved or errors', () => {
  for (const status of ['SETTLED', 'WON', 'LOST', 'CASHED_OUT']) {
    const { envelope } = capture(ticket({ status, legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }));
    assert.equal(envelope.tickets.length, 0, status);
    assert.equal(envelope.excluded_tickets[0].reason, 'TICKET_STATUS_OUT_OF_SCOPE', status);
    assert.notEqual(envelope.capture_status, 'CAPTURE_FAILED');
  }
});

test('no explicit placement timestamp: never guessed via Date.parse, reported unresolved with raw text preserved', () => {
  const html = `
    <div class="ticket" data-ticket-id="TCK-NOTIME">
      <span class="ticket__status">OPEN</span>
      <span class="ticket__placed-at">Today 14:32</span>
      ${leg({ home: 'A', away: 'B', eventId: '1' })}
    </div>
  `;
  const { envelope } = capture(html);
  const t = envelope.tickets[0];
  assert.equal(t.placed_at_utc, null);
  assert.equal(t.placed_at_resolution, 'UNRESOLVED_NO_EXPLICIT_TIMESTAMP');
  assert.equal(t.placed_at_raw, 'Today 14:32');
});

test('no source event id on a leg: falls back to a natural key, flagged as the weaker resolution path', () => {
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
  const { envelope } = capture(html);
  const l = envelope.tickets[0].legs[0];
  assert.equal(l.source_event_id, null);
  assert.equal(l.fixture_id_resolution, 'NATURAL_KEY_FALLBACK_NO_SOURCE_EVENT_ID');
  assert.match(l.fixture_id, /^bxf_[0-9a-f]{16}$/);
});

test('no tickets found at all: CAPTURE_FAILED, not a silent empty success', () => {
  const { envelope } = capture('');
  assert.equal(envelope.capture_status, 'CAPTURE_FAILED');
  assert.ok(envelope.capture_status_reasons.includes('NO_TICKETS_FOUND'));
  assert.equal(envelope.tickets.length, 0);
});

test('unrecognized ticket status is routed to unresolved_tickets, never silently included or dropped', () => {
  const { envelope } = capture(ticket({ status: 'WEIRD_STATUS', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }));
  assert.equal(envelope.tickets.length, 0);
  assert.equal(envelope.unresolved_tickets[0].reason, 'UNRECOGNIZED_TICKET_STATUS');
});

test('every capture always flags the placeholder-selector caveat, regardless of outcome', () => {
  const { envelope } = capture(ticket());
  assert.ok(envelope.capture_status_reasons.includes('TICKET_SELECTORS_UNVERIFIED_PLACEHOLDER'));
});

test('privacy: unresolved/excluded ticket records never carry more than the documented allowlisted fields', () => {
  const { envelope } = capture(
    ticket({ status: 'SETTLED', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] })
  );
  const excluded = envelope.excluded_tickets[0];
  assert.deepEqual(Object.keys(excluded).sort(), ['detail', 'reason', 'source_index', 'ticket_id_raw'].sort());
});

test('coverage invariant: tickets_seen = tickets_parsed + tickets_unresolved + tickets_expected_excluded', () => {
  const html =
    ticket({ ticketId: 'TCK-OK', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] }) + // parsed
    ticket({ ticketId: 'TCK-BAD', legs: [leg({ home: '', away: '', eventId: '2' })] }) + // unresolved
    ticket({ ticketId: 'TCK-SETTLED', status: 'SETTLED', legs: [leg({ home: 'C', away: 'D', eventId: '3' })] }); // expected-excluded
  const { envelope } = capture(html);
  const c = envelope.coverage;
  assert.equal(c.tickets_seen, 3);
  assert.equal(c.tickets_parsed, 1);
  assert.equal(c.tickets_unresolved, 1);
  assert.equal(c.tickets_expected_excluded, 1);
  assert.equal(c.tickets_seen, c.tickets_parsed + c.tickets_unresolved + c.tickets_expected_excluded);
});

test('ticket id stays stable regardless of an expanded/collapsed wrapper class around the same ticket markup', () => {
  const ticketHtml = ticket({ ticketId: 'TCK-STABLE', legs: [leg({ home: 'A', away: 'B', eventId: '1' })] });
  const collapsedDoc = docFromHtml(`<div class="open-bets"><div class="ticket-group collapsed">${ticketHtml}</div></div>`);
  const expandedDoc = docFromHtml(`<div class="open-bets"><div class="ticket-group expanded">${ticketHtml}</div></div>`);
  const collapsed = ticketParser.captureFromDocument(collapsedDoc, BASE_CONTEXT).envelope;
  const expanded = ticketParser.captureFromDocument(expandedDoc, BASE_CONTEXT).envelope;
  assert.equal(collapsed.tickets[0].bet9ja_ticket_id, 'TCK-STABLE');
  assert.equal(expanded.tickets[0].bet9ja_ticket_id, 'TCK-STABLE');
  assert.equal(collapsed.tickets[0].bet9ja_ticket_id, expanded.tickets[0].bet9ja_ticket_id);
});

test('total stake is preserved separately from each leg\'s own odds -- singles, accumulators, and system tickets alike', () => {
  const single = capture(
    ticket({ unitStake: '10.00', totalStake: '10.00', legs: [leg({ home: 'A', away: 'B', eventId: '1', odds: '1.95' })] })
  ).envelope.tickets[0];
  assert.equal(single.total_stake, 10);
  assert.equal(single.legs[0].odds, 1.95);
  assert.notEqual(single.total_stake, single.legs[0].odds);

  const acca = capture(
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
  ).envelope.tickets[0];
  assert.equal(acca.total_stake, 5);
  assert.deepEqual(acca.legs.map((l) => l.odds), [1.9, 2.1, 1.5]);

  const system = capture(
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
  ).envelope.tickets[0];
  assert.equal(system.unit_stake, 2);
  assert.equal(system.total_stake, 12, 'total_stake is a distinct field from unit_stake -- never derived from leg odds');
  assert.equal(system.legs.length, 4);
});

test('privacy: a ticket embedded inside unrelated page chrome (nav/balance/footer) never leaks that chrome', () => {
  const doc = docFromHtml(`
    <body>
      <nav class="site-nav">Account balance: $482.10 | Log out | Betslip (3)</nav>
      <div class="open-bets">
        ${ticket({ legs: [`<div class="ticket__leg"><span class="leg__home"></span><span class="leg__away"></span><span class="leg__odds">1.50</span></div>`] })}
      </div>
      <footer>Copyright Bet9ja. Your session token: abc123.</footer>
    </body>
  `);
  const { envelope } = ticketParser.captureFromDocument(doc, BASE_CONTEXT);
  const rawText = JSON.stringify(envelope.unresolved_tickets).toLowerCase();
  for (const banned of ['balance', 'account', 'betslip', 'cookie', 'token', 'password', 'login', 'session']) {
    assert.ok(!rawText.includes(banned), `unresolved_tickets unexpectedly contains "${banned}"`);
  }
});
