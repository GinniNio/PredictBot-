const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const { parseFixturesFromDocument, resolveSportSlugFromUrl, parseThreeWayOdds } = require("../fixture_parser.js");

test("parseFixturesFromDocument excludes table nodes supplied by a multi-navigation walker", () => {
  const dom = new JSDOM(`<!DOCTYPE html><body>
    <div class="sports-table"><div class="table-f"><div id="prematch_event-1"><div class="sports-table__home">Old A</div><div class="sports-table__away">Old B</div></div></div></div>
    <div class="sports-table"><div class="table-f"><div id="prematch_event-2"><div class="sports-table__home">New A</div><div class="sports-table__away">New B</div></div></div></div>
  </body>`);
  const doc = dom.window.document;
  const oldTable = doc.querySelector(".sports-table");
  const result = parseFixturesFromDocument(doc, {
    href: "https://sports.bet9ja.com/competition/icehockey/russia/mhl/4-44083-4715477",
    excludeTableElements: [oldTable],
  });
  assert.equal(result.fixture_row_count, 1);
  assert.equal(result.fixtures[0].fixture_id, "2");
});

// v2 adds `markets` and `priced_market_count` per fixture; the legacy
// assertions below check only the v1 fields, the new fields are
// covered by their own tests at the end of this file.
function core(fixture) {
  const { markets, priced_market_count, kickoff_utc_derived, kickoff_utc_basis, ...rest } = fixture;
  return rest;
}

function loadFixture(name) {
  return fs.readFileSync(path.join(__dirname, "fixtures", name), "utf8");
}

// Real, byte-extracted fragments -- each file's own comment in
// tests/fixtures/ names the raw snapshot it was cut from via jsdom's own
// `outerHTML` (not hand-typed), covering: real breadcrumb text, the
// `.sports-head.table` date header, and one or more `.table-f` row
// wrappers with `.sports-table__time` / the `prematch_event-<id>`
// matchup cell / 2-way / handicap / 3-way odds as TRUE siblings --
// exactly the structure `resolveRowContainer` exists to handle.
const WNBA_POPULATED_HTML = loadFixture("wnba-populated-2026-09-30T16-23-42.html");
const WNBA_EMPTY_HTML = loadFixture("wnba-empty-2026-09-30T15-33-17.html");
const KHL_POPULATED_HTML = loadFixture("khl-populated-2026-09-30T16-26-27.html");

test("real WNBA fragment (2026-09-30T16:23:42Z): all 3 fixtures, correct date/time/participants/3way odds", () => {
  const dom = new JSDOM(WNBA_POPULATED_HTML);
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T16:23:42.845Z",
  });

  assert.equal(result.fixture_row_count, 3);
  assert.equal(result.breadcrumb_raw, "Basketball>USA>WNBA");
  assert.equal(result.sport_slug_from_url, "basketball");
  assert.equal(result.empty_state_status, "NOT_EMPTY");
  assert.deepEqual(result.duplicate_fixture_ids, []);

  assert.deepEqual(core(result.fixtures[0]), {
    fixture_id: "842933379",
    participant_1: "Washington Mystics",
    participant_2: "Atlanta Dream",
    kickoff_time_raw: "00:00",
    date_text_raw: "Thu 1 Oct",
    three_way_odds: { "1": "2.35", X: "13.00", "2": "1.73" },
    missing_participants: false,
  });
  assert.deepEqual(core(result.fixtures[1]), {
    fixture_id: "842949763",
    participant_1: "Dallas Wings",
    participant_2: "Golden State Valkyries",
    kickoff_time_raw: "02:00",
    date_text_raw: "Thu 1 Oct",
    three_way_odds: { "1": "2.60", X: "14.80", "2": "1.59" },
    missing_participants: false,
  });
  assert.deepEqual(core(result.fixtures[2]), {
    fixture_id: "843748380",
    participant_1: "Las Vegas Aces",
    participant_2: "Indiana Fever",
    kickoff_time_raw: "02:00",
    date_text_raw: "Fri 2 Oct",
    three_way_odds: { "1": "1.63", X: "14.30", "2": "2.50" },
    missing_participants: false,
  });
});

test("real WNBA fragment (2026-09-30T15:33:17Z): CONFIRMED_EMPTY via the exact real marker", () => {
  const dom = new JSDOM(WNBA_EMPTY_HTML);
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:33:17.696Z",
  });

  assert.equal(result.fixture_row_count, 0);
  assert.deepEqual(result.fixtures, []);
  assert.equal(result.empty_state_status, "CONFIRMED_EMPTY");
});

test("real Ice Hockey/KHL fragment (2026-09-30T16:26:27Z, a different sport from WNBA): all 4 fixtures, no 3way market", () => {
  const dom = new JSDOM(KHL_POPULATED_HTML);
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/icehockey/russia/khl/4-44083-4714776",
    capturedAtUtc: "2026-09-30T16:26:27.020Z",
  });

  assert.equal(result.fixture_row_count, 4);
  assert.equal(result.breadcrumb_raw, "Ice Hockey>Russia>KHL");
  assert.equal(result.sport_slug_from_url, "icehockey");
  assert.deepEqual(result.duplicate_fixture_ids, []);

  assert.deepEqual(core(result.fixtures[0]), {
    fixture_id: "842493730",
    participant_1: "HC Sochi",
    participant_2: "Salavat Yulaev UFA",
    kickoff_time_raw: "17:30",
    date_text_raw: "Wed 30 Sep",
    three_way_odds: null,
    missing_participants: false,
  });
  // Every KHL fixture in this fragment shares the same date group and
  // has no 3way market (confirmed real: Ice Hockey doesn't carry it).
  for (const fixture of result.fixtures) {
    assert.equal(fixture.date_text_raw, "Wed 30 Sep");
    assert.equal(fixture.three_way_odds, null);
  }
});

test("parseThreeWayOdds returns null when a fixture element has no .table-f ancestor at all (fail-closed, no fallback)", () => {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body><div id="prematch_event-1"><div class="sports-table__home">A</div></div></body></html>`
  );
  const fixtureEl = dom.window.document.querySelector("#prematch_event-1");
  assert.equal(parseThreeWayOdds(fixtureEl, "1"), null);
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

// ---------------------------------------------------------------------
// The four sports below have no raw HTML at all -- only a "DOM
// extraction contract" (a structured live-inspection summary, not raw
// bytes; see fixture_parser.js's own header comment on this evidence
// tier). `buildContractPage` renders their real row data through the
// confirmed REAL sibling structure (`.table-f` wrapping
// `.sports-table__time` and the `prematch_event-<id>` matchup cell as
// siblings) rather than a flat/incorrect nesting, since that structure
// is independently byte-confirmed by the WNBA/KHL fragments above --
// only the row DATA here (ids/times/names) comes from the contract tier.
// ---------------------------------------------------------------------
function buildContractPage({ breadcrumb, rows, withDescendantOddsNoise }) {
  const rowsHtml = rows
    .map(([id, time, p1, p2]) => {
      const noise = withDescendantOddsNoise
        ? `<li id="${id}_event-${id.replace('prematch_event-', '')}_odds_market-1x2_sign-1">1.50</li>`
        : "";
      return `
        <div class="table-f">
          <div class="sports-table__td sports-table__time txt-c"><span>${time}</span></div>
          <div class="sports-table__td sports-table__matchup pr10" id="${id}">
            <div class="sports-table__home txt-cut">${p1}</div>
            <div class="sports-table__away txt-cut">${p2}</div>
          </div>
          <div class="sports-table__td sports-table__odds txt-c"><ul>${noise}</ul></div>
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
const ICE_HOCKEY_NHL_ROWS = [
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
  const dom = new JSDOM(buildContractPage({ breadcrumb: "Handball > Germany > Bundesliga", rows: HANDBALL_ROWS }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/handball/germany/bundesliga/6-45758-5804912",
    capturedAtUtc: "2026-09-30T15:46:06.394Z",
  });

  assert.equal(result.fixture_row_count, 8);
  assert.equal(result.breadcrumb_raw, "Handball > Germany > Bundesliga");
  assert.equal(result.sport_slug_from_url, "handball");
  assert.deepEqual(result.duplicate_fixture_ids, []);
  assert.deepEqual(core(result.fixtures[0]), {
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
  const dom = new JSDOM(buildContractPage({ breadcrumb: "Volleyball > Poland > 1. Liga", rows: VOLLEYBALL_ROWS }));
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
  const dom = new JSDOM(buildContractPage({ breadcrumb: "Ice Hockey > USA > NHL", rows: ICE_HOCKEY_NHL_ROWS }));
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
    buildContractPage({ breadcrumb: "Tennis > WTA > WTA Beijing, China Women Singles", rows: TENNIS_ROWS })
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
    buildContractPage({ breadcrumb: "Handball > Germany > Bundesliga", rows: [HANDBALL_ROWS[0]], withDescendantOddsNoise: true })
  );
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/handball/germany/bundesliga/6-45758-5804912",
    capturedAtUtc: "2026-09-30T15:46:06.394Z",
  });

  // Without the exact-match filter this would count 2:
  // "prematch_event-843197066" and
  // "prematch_event-843197066_event-843197066_odds_market-1x2_sign-1"
  // both match the broader `[id^="prematch_event-"]` selector.
  assert.equal(result.fixture_row_count, 1);
  assert.equal(result.fixtures[0].fixture_id, "843197066");
});

test("reports a genuinely empty page honestly when no marker is present (UNKNOWN_EMPTY, not guessed CONFIRMED_EMPTY)", () => {
  const dom = new JSDOM(buildContractPage({ breadcrumb: "Basketball > USA > WNBA", rows: [] }));
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871",
    capturedAtUtc: "2026-09-30T15:33:17.000Z",
  });

  assert.equal(result.fixture_row_count, 0);
  assert.deepEqual(result.fixtures, []);
  assert.equal(result.empty_state_status, "UNKNOWN_EMPTY");
});

// ---------------------------------------------------------------------
// v2 generic market extraction -- real byte fragments only.
// ---------------------------------------------------------------------
const { parseAllMarkets, summarizeOddsCoverage } = require("../fixture_parser.js");

test("v2: KHL (no 3way market) now yields match_winner 1/X/2, draw_no_bet and handicap_rt with line", () => {
  const dom = new JSDOM(KHL_POPULATED_HTML);
  const result = parseFixturesFromDocument(dom.window.document, {
    href: "https://sports.bet9ja.com/competition/icehockey/russia/khl/4-44083-4714776",
  });
  const f = result.fixtures.find((x) => x.fixture_id === "842493730");
  assert.equal(f.three_way_odds, null, "legacy field still null -- the original bug");
  const keys = f.markets.map((m) => m.market_key).sort();
  assert.deepEqual(keys, ["draw_no_bet", "handicap_rt", "match_winner"]);
  const mw = f.markets.find((m) => m.market_key === "match_winner");
  assert.deepEqual(mw.selections.map((s) => [s.sign_raw, s.odds]), [["1", 4.45], ["X", 4.05], ["2", 1.56]]);
  assert.equal(mw.fully_priced, true);
  const hcp = f.markets.find((m) => m.market_key === "handicap_rt");
  assert.equal(hcp.line_raw, "+1.5");
  assert.deepEqual(hcp.selections.map((s) => s.odds), [1.75, 1.96]);
  assert.equal(result.odds_coverage.status, "ODDS_COMPLETE");
  assert.equal(result.odds_coverage.fixtures_with_priced_market, result.fixture_row_count);
});

test("v2: WNBA captures 2_way and handicap alongside 3way", () => {
  const dom = new JSDOM(WNBA_POPULATED_HTML);
  const result = parseFixturesFromDocument(dom.window.document, { href: "https://sports.bet9ja.com/competition/basketball/usa/wnba/2-43460-4759871" });
  const f = result.fixtures[0];
  const keys = f.markets.map((m) => m.market_key).sort();
  assert.deepEqual(keys, ["2_way", "3way", "handicap"]);
  const two = f.markets.find((m) => m.market_key === "2_way");
  assert.deepEqual(two.selections.map((s) => s.odds), [2.2, 1.65]);
  assert.equal(f.markets.find((m) => m.market_key === "handicap").line_raw, "+2.5");
});

test("v2: a 2-way sport (snooker-shaped row) is priced without any 3way market", () => {
  const dom = new JSDOM(`<!DOCTYPE html><body><div class="sports-table"><div class="table-f">
    <div class="sports-table__td sports-table__time"><span>07:00</span></div>
    <div class="sports-table__td sports-table__matchup" id="prematch_event-9">
      <div class="sports-table__home">Sijun, Yuan</div><div class="sports-table__away">Robertson, Jimmy</div></div>
    <div class="sports-table__td sports-table__odds"><ul>
      <li id="prematch_event-9_event-9_odds_market-match_winner_sign-1">1.85</li>
      <li id="prematch_event-9_event-9_odds_market-match_winner_sign-2">1.95</li></ul></div>
  </div></div></body>`);
  const result = parseFixturesFromDocument(dom.window.document, { href: "https://sports.bet9ja.com/competition/snooker/international/x/19-1-1" });
  assert.equal(result.fixtures[0].three_way_odds, null);
  assert.equal(result.fixtures[0].priced_market_count, 1);
  assert.equal(result.odds_coverage.status, "ODDS_COMPLETE");
});

test("v2: locked/blank prices are unpriced, never numbers; coverage reports ODDS_MISSING", () => {
  const dom = new JSDOM(`<!DOCTYPE html><body><div class="sports-table"><div class="table-f">
    <div class="sports-table__td sports-table__matchup" id="prematch_event-5">
      <div class="sports-table__home">A</div><div class="sports-table__away">B</div></div>
    <div class="sports-table__td"><ul>
      <li class="locked" id="prematch_event-5_event-5_odds_market-match_winner_sign-1"></li>
      <li id="prematch_event-5_event-5_odds_market-match_winner_sign-2">-</li></ul></div>
  </div></div></body>`);
  const result = parseFixturesFromDocument(dom.window.document, { href: "" });
  const sel = result.fixtures[0].markets[0].selections;
  assert.deepEqual(sel.map((s) => [s.odds, s.state]), [[null, "locked"], [null, "unpriced"]]);
  assert.equal(sel[0].class_raw, "locked");
  assert.equal(result.odds_coverage.status, "ODDS_MISSING");
});

test("v2: summarizeOddsCoverage statuses", () => {
  assert.equal(summarizeOddsCoverage([]).status, "NO_FIXTURES");
  assert.equal(summarizeOddsCoverage([{ priced_market_count: 1 }, { priced_market_count: 0 }]).status, "ODDS_PARTIAL");
});

const { deriveKickoffUtc } = require("../fixture_parser.js");

test("v2.2: real locked class from 2026-10-03 Boxing capture -> state locked, market not priced", () => {
  const dom = new JSDOM(`<!DOCTYPE html><body><div class="sports-table"><div class="table-f">
    <div class="sports-table__td sports-table__matchup" id="prematch_event-7"><div class="sports-table__home">A</div><div class="sports-table__away">B</div></div>
    <div class="sports-table__td"><ul>
      <li class="sports-table__odds-item dib pt10 locked" id="prematch_event-7_event-7_odds_market-dnb_sign-1"></li>
      <li class="sports-table__odds-item dib pt10 locked" id="prematch_event-7_event-7_odds_market-dnb_sign-2"></li></ul></div>
  </div></div></body>`);
  const r = parseFixturesFromDocument(dom.window.document, { href: "" });
  assert.deepEqual(r.fixtures[0].markets[0].selections.map((s) => s.state), ["locked", "locked"]);
  assert.equal(r.fixtures[0].priced_market_count, 0);
});

test("v2.2: one-selection Specials Combo market (real shape) counts as priced but not complete", () => {
  const dom = new JSDOM(`<!DOCTYPE html><body><div class="sports-table"><div class="table-f">
    <div class="sports-table__td sports-table__matchup" id="prematch_event-844410882"><div class="sports-table__home">England Score Both Halves, England First Team to Score</div></div>
    <div class="sports-table__td"><ul><li class="sports-table__odds-item dib pt10" id="prematch_event-844410882_event-844410882_odds_market-to_happen_sign-Y">3.25</li></ul></div>
  </div></div></body>`);
  const r = parseFixturesFromDocument(dom.window.document, { href: "" });
  const m = r.fixtures[0].markets[0];
  assert.equal(m.fully_priced, true);
  assert.equal(m.complete_market, false);
  assert.equal(r.odds_coverage.status, "ODDS_COMPLETE");
});

test("v2.2: kickoff UTC derived at UTC+1 (Charlotte v Memphis listed 16:00 = 15:00Z official)", () => {
  assert.equal(deriveKickoffUtc("Sat 3 Oct", "16:00", "2026-10-03T07:05:00.000Z"), "2026-10-03T15:00:00.000Z");
  assert.equal(deriveKickoffUtc("Thu 1 Oct", "00:00", "2026-09-30T16:23:42.845Z"), "2026-09-30T23:00:00.000Z");
  assert.equal(deriveKickoffUtc("Fri 2 Jan", "12:00", "2026-12-30T10:00:00Z"), "2027-01-02T11:00:00.000Z");
  assert.equal(deriveKickoffUtc(null, "16:00", "2026-10-03T07:05:00Z"), null);
});
