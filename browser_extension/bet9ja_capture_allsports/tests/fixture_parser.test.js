const test = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const { parseFixturesFromDocument, resolveSportSlugFromUrl, parseThreeWayOdds } = require("../fixture_parser.js");

// Builds synthetic markup shaped exactly like the confirmed real
// structure (`prematch_event-<id>` row anchor, `.sports-table__home` /
// `.sports-table__away` / `.sports-table__time` descendants,
// `.sports-view__crumbs` breadcrumb) -- the same selectors already
// confirmed for Soccer in bet9ja_capture/parser.js, and reported
// identically by all four 2026-09-30 DOM contracts (Handball,
// Volleyball, Ice Hockey, Tennis).
function buildPage({ breadcrumb, rows, withDescendantOddsNoise }) {
  const rowsHtml = rows
    .map(([id, time, p1, p2]) => {
      const noise = withDescendantOddsNoise
        ? `<div id="${id}_odds_market-1x2_sign-1">1.50</div>`
        : "";
      return `
        <div id="${id}">
          <div class="sports-table__time">${time}</div>
          <div class="sports-table__home">${p1}</div>
          <div class="sports-table__away">${p2}</div>
          ${noise}
        </div>`;
    })
    .join("\n");

  return `<!DOCTYPE html><html><body>
    <div class="sports-view__crumbs">${breadcrumb}</div>
    <div class="sports-table">${rowsHtml}</div>
  </body></html>`;
}

// Real row data from bet9ja-handball-dom-contract-2026-09-30.json
// (captured_at_utc 2026-09-30T15:46:06.394Z, fnv1a_32 2aef6e79).
const HANDBALL_ROWS = [
  ["prematch_event-843197066", "18:00", "SC Magdeburg", "THW Kiel"],
  ["prematch_event-843406408", "18:00", "ThSV Eisenach", "TVB Stuttgart"],
  ["prematch_event-843406448", "18:00", "TBV Lemgo Lippe", "HC Hamburg"],
  ["prematch_event-843406347", "18:00", "HSG Wetzlar", "Rhein-Neckar Lowen"],
  ["prematch_event-843406336", "19:00", "HBW Balingen-Weilstetten", "Bergischer HC"],
  ["prematch_event-843406358", "17:00", "THW Kiel", "VfL Gummersbach"],
  ["prematch_event-843406344", "18:00", "SG Flensburg-Handewitt", "FRISCH AUF! Goppingen"],
  ["prematch_event-843406333", "19:00", "MT Melsungen", "TSV Hannover-Burgdorf"],
];

// Real row data from bet9ja-volleyball-dom-contract-2026-09-30.json
// (fnv1a_32 d8dbbeb7).
const VOLLEYBALL_ROWS = [
  ["prematch_event-834135118", "18:00", "Anioly Torun", "Kps Plock"],
  ["prematch_event-834135126", "16:30", "McKis Jaworzno", "KS Lechia Tomaszow Mazowiecki"],
];

// Real row data from bet9ja-hockey-dom-contract-2026-09-30.json
// (fnv1a_32 6e560fbe).
const ICE_HOCKEY_ROWS = [
  ["prematch_event-807237625", "00:30", "Philadelphia Flyers", "Pittsburgh Penguins"],
  ["prematch_event-841175751", "00:30", "Toronto Maple Leafs", "New York Islanders"],
  ["prematch_event-807237842", "03:00", "Colorado Avalanche", "Los Angeles Kings"],
  ["prematch_event-807239259", "00:00", "Columbus Blue Jackets", "Buffalo Sabres"],
  ["prematch_event-807239925", "00:00", "New Jersey Devils", "Philadelphia Flyers"],
  ["prematch_event-807240797", "00:00", "New York Rangers", "Tampa Bay Lightning"],
  ["prematch_event-807240118", "01:00", "Nashville Predators", "Minnesota Wild"],
  ["prematch_event-807236192", "02:00", "Calgary Flames", "Seattle Kraken"],
  ["prematch_event-807238929", "02:00", "Utah Mammoth", "Chicago Blackhawks"],
];

// Real row data from bet9ja-tennis-dom-contract-2026-09-30.json
// (fnv1a_32 4bbb397a) -- participant_model is "participant_1,
// participant_2" there, but rendered through the same
// .sports-table__home/__away pair as every other sport's home/away
// model; this module does not distinguish the two labels.
const TENNIS_ROWS = [
  ["prematch_event-843101650", "04:00", "Starodubtseva, Yuliia", "Charaeva, Alina"],
  ["prematch_event-843101808", "04:00", "Kenin, Sofia", "Krueger, Ashlyn"],
];

test("parses all 8 real Handball fixture rows with correct ids/participants/times", () => {
  const dom = new JSDOM(buildPage({ breadcrumb: "Handball > Germany > Bundesliga", rows: HANDBALL_ROWS }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/handball/germany/bundesliga/6-45758-5804912",
    capturedAtUtc: "2026-09-30T15:46:06.394Z",
  });

  assert.equal(result.fixture_row_count, 8);
  assert.equal(result.breadcrumb_raw, "Handball > Germany > Bundesliga");
  assert.equal(result.sport_slug_from_url, "handball");
  assert.deepEqual(result.duplicate_fixture_ids, []);
  assert.deepEqual(result.fixtures[0], {
    fixture_id: "843197066",
    participant_1: "SC Magdeburg",
    participant_2: "THW Kiel",
    kickoff_time_raw: "18:00",
    date_text_raw: null,
    three_way_odds: null,
    missing_participants: false,
  });
});

test("parses both real Volleyball fixture rows", () => {
  const dom = new JSDOM(buildPage({ breadcrumb: "Volleyball > Poland > 1. Liga", rows: VOLLEYBALL_ROWS }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/volleyball/poland/1.liga/23-43872-4547150",
    capturedAtUtc: "2026-09-30T15:45:27.304Z",
  });

  assert.equal(result.fixture_row_count, 2);
  assert.equal(result.sport_slug_from_url, "volleyball");
  assert.equal(result.fixtures[1].fixture_id, "834135126");
  assert.equal(result.fixtures[1].participant_1, "McKis Jaworzno");
});

test("parses all 9 real Ice Hockey/NHL fixture rows, including a repeated participant name across rows", () => {
  const dom = new JSDOM(buildPage({ breadcrumb: "Ice Hockey > USA > NHL", rows: ICE_HOCKEY_ROWS }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/icehockey/usa/nhl/4-44080-4714779",
    capturedAtUtc: "2026-09-30T15:46:23.937Z",
  });

  assert.equal(result.fixture_row_count, 9);
  assert.equal(result.sport_slug_from_url, "icehockey");
  // Philadelphia Flyers appears in two distinct real fixtures
  // (807237625 and 807239925) -- confirming rows are keyed by fixture id,
  // not by participant name.
  const flyersFixtures = result.fixtures.filter(
    (f) => f.participant_1 === "Philadelphia Flyers" || f.participant_2 === "Philadelphia Flyers"
  );
  assert.equal(flyersFixtures.length, 2);
  assert.deepEqual(result.duplicate_fixture_ids, []);
});

test("parses Tennis fixture rows under the participant_1/participant_2 model via the same home/away selectors", () => {
  const dom = new JSDOM(
    buildPage({ breadcrumb: "Tennis > WTA > WTA Beijing, China Women Singles", rows: TENNIS_ROWS })
  );
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/tennis/wta/wtabeijingchinawomensingles/5-43765-5603249",
    capturedAtUtc: "2026-09-30T15:46:20.755Z",
  });

  assert.equal(result.fixture_row_count, 2);
  assert.equal(result.sport_slug_from_url, "tennis");
  assert.equal(result.fixtures[0].participant_1, "Starodubtseva, Yuliia");
  assert.equal(result.fixtures[0].participant_2, "Charaeva, Alina");
});

test("excludes descendant odds/dropdown ids that repeat the parent row id with a suffix", () => {
  const dom = new JSDOM(
    buildPage({ breadcrumb: "Handball > Germany > Bundesliga", rows: [HANDBALL_ROWS[0]], withDescendantOddsNoise: true })
  );
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/handball/germany/bundesliga/6-45758-5804912",
    capturedAtUtc: "2026-09-30T15:46:06.394Z",
  });

  // Without the exact-match filter this would count 2:
  // "prematch_event-843197066" and
  // "prematch_event-843197066_odds_market-1x2_sign-1" both match the
  // broader `[id^="prematch_event-"]` selector.
  assert.equal(result.fixture_row_count, 1);
  assert.equal(result.fixtures[0].fixture_id, "843197066");
});

test("reports a genuinely empty page honestly, without guessing an empty-state marker", () => {
  const dom = new JSDOM(buildPage({ breadcrumb: "Basketball > USA > WNBA", rows: [] }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:33:17.000Z",
  });

  assert.equal(result.fixture_row_count, 0);
  assert.deepEqual(result.fixtures, []);
});

test("resolveSportSlugFromUrl reads the sport slug from every confirmed competition URL shape", () => {
  assert.equal(
    resolveSportSlugFromUrl("https://sports.bet9ja.com/competition/tennis/wta/x/5-1-1"),
    "tennis"
  );
  assert.equal(
    resolveSportSlugFromUrl("https://sports.bet9ja.com/competition/icehockey/usa/nhl/4-1-1"),
    "icehockey"
  );
  assert.equal(resolveSportSlugFromUrl("https://sports.bet9ja.com/Sport/Default/All"), null);
});

// Real WNBA rows/odds from bet9ja-stage2-resolution-evidence-2026-09-30.json
// (captured_at_utc 2026-09-30T17:03:00Z), including the confirmed "3way"
// market's exact odds-id suffix mapping (1B/XB/2B -> 1/X/2).
const WNBA_ROWS_WITH_THREE_WAY = [
  {
    id: "842933379",
    time: "00:00",
    p1: "Washington Mystics",
    p2: "Atlanta Dream",
    threeWay: { "1B": "2.35", XB: "13.00", "2B": "1.73" },
  },
  {
    id: "842949763",
    time: "02:00",
    p1: "Dallas Wings",
    p2: "Golden State Valkyries",
    threeWay: { "1B": "2.60", XB: "14.80", "2B": "1.59" },
  },
  {
    id: "843748380",
    time: "02:00",
    p1: "Las Vegas Aces",
    p2: "Indiana Fever",
    threeWay: { "1B": "1.63", XB: "14.30", "2B": "2.50" },
  },
];

function buildWnbaDateGroupedPage() {
  const rowsHtml = WNBA_ROWS_WITH_THREE_WAY.map(({ id, time, p1, p2, threeWay }) => {
    const oddsHtml = Object.entries(threeWay)
      .map(([sign, value]) => `<div id="prematch_event-${id}_event-${id}_odds_market-3way_sign-${sign}">${value}</div>`)
      .join("\n");
    return `
      <div id="prematch_event-${id}">
        <div class="sports-table__time">${time}</div>
        <div class="sports-table__home">${p1}</div>
        <div class="sports-table__away">${p2}</div>
        ${oddsHtml}
      </div>`;
  }).join("\n");

  return `<!DOCTYPE html><html><body>
    <div class="sports-view__crumbs">Basketball > USA > WNBA</div>
    <div class="sports-head table"><div class="sports-head__date"><span>Thu 1 Oct</span></div></div>
    <div class="sports-table">${rowsHtml}</div>
  </body></html>`;
}

test("attributes each row's date from its .sports-table's own preceding .sports-head.table sibling", () => {
  const dom = new JSDOM(buildWnbaDateGroupedPage());
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T17:03:00Z",
  });

  assert.equal(result.fixture_row_count, 3);
  for (const fixture of result.fixtures) {
    assert.equal(fixture.date_text_raw, "Thu 1 Oct");
  }
});

test("extracts the confirmed WNBA 3way market with real odds, mapping 1B/XB/2B to 1/X/2", () => {
  const dom = new JSDOM(buildWnbaDateGroupedPage());
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T17:03:00Z",
  });

  assert.deepEqual(result.fixtures[0].three_way_odds, { "1": "2.35", X: "13.00", "2": "1.73" });
  assert.deepEqual(result.fixtures[1].three_way_odds, { "1": "2.60", X: "14.80", "2": "1.59" });
  assert.deepEqual(result.fixtures[2].three_way_odds, { "1": "1.63", X: "14.30", "2": "2.50" });
});

test("parseThreeWayOdds returns null when a row has no 3way market", () => {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body><div id="prematch_event-1"><div class="sports-table__home">A</div></div></body></html>`
  );
  const row = dom.window.document.querySelector("#prematch_event-1");
  assert.equal(parseThreeWayOdds(row, "1"), null);
});

test("empty_state_status is CONFIRMED_EMPTY only with zero rows AND the exact confirmed marker text", () => {
  const confirmedEmptyDom = new JSDOM(`<!DOCTYPE html><html><body>
    <div class="sports-view__crumbs">Basketball > USA > WNBA</div>
    <div class="gen__holder"><div class="search-results"><div class="gen__txt">There are no markets available.</div></div></div>
  </body></html>`);
  const confirmedEmpty = parseFixturesFromDocument(confirmedEmptyDom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:33:17.696Z",
  });
  assert.equal(confirmedEmpty.fixture_row_count, 0);
  assert.equal(confirmedEmpty.empty_state_status, "CONFIRMED_EMPTY");

  const unknownEmptyDom = new JSDOM(`<!DOCTYPE html><html><body>
    <div class="sports-view__crumbs">Basketball > USA > WNBA</div>
  </body></html>`);
  const unknownEmpty = parseFixturesFromDocument(unknownEmptyDom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:33:17.696Z",
  });
  assert.equal(unknownEmpty.empty_state_status, "UNKNOWN_EMPTY");

  const notEmptyDom = new JSDOM(buildWnbaDateGroupedPage());
  const notEmpty = parseFixturesFromDocument(notEmptyDom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:31:43.000Z",
  });
  assert.equal(notEmpty.empty_state_status, "NOT_EMPTY");
});

test("falls back to .table-f > :first-child for kickoff time when .sports-table__time is absent", () => {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div class="sports-table">
      <div id="prematch_event-1">
        <div class="table-f"><span>19:00</span></div>
        <div class="sports-table__home">A</div>
        <div class="sports-table__away">B</div>
      </div>
    </div>
  </body></html>`);
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/handball/germany/bundesliga/6-1-1",
    capturedAtUtc: "2026-09-30T17:03:00Z",
  });
  assert.equal(result.fixtures[0].kickoff_time_raw, "19:00");
});
