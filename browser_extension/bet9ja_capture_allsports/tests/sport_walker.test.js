const test = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const { walkSport } = require("../sport_walker.js");

/**
 * Builds a fake Ice Hockey sidebar + simulates the site's own click
 * behavior via ONE delegated listener on `document` -- this survives
 * `body.innerHTML` replacement (simulating an in-place SPA navigation
 * per the operator's own rule 2), unlike per-element listeners which
 * would be destroyed along with the elements they were attached to.
 *
 * Scenario: sport starts collapsed (zero groups) with a "show more"
 * toggle; expanding reveals 2 groups (USA, Switzerland). USA has 1
 * competition (populated). Switzerland has 2: the real National League/
 * Swiss League ids from the confirmed live sidebar export, one
 * populated, one confirmed-empty. A third, deliberately mismatched
 * competition (`g-999_mismatch`) navigates to WRONG content, to exercise
 * INVALID_CONTENT_MISMATCH.
 */
function buildFakeIceHockeySite() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
  </body></html>`);
  const doc = dom.window.document;

  const SIDEBAR_EXPANDED = `
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_buttonmore-toggle">Show 1 more</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>
  `;
  const SIDEBAR_FULLY_EXPANDED = `
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_buttonmore-toggle">Show less</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-44092_switzerland_label-toggle">Switzerland</div>
  `;

  function usaCompetitionLinks() {
    return `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>`;
  }
  function switzerlandCompetitionLinks() {
    return (
      `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-4715478_national_league">National League</a>` +
      `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-5527435_swiss_league">Swiss League</a>` +
      `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-999_mismatch">Mismatch Comp</a>`
    );
  }

  function populatedCompetitionPage(breadcrumb) {
    return `
      <div class="sports-view__crumbs">${breadcrumb}</div>
      <div class="sports-table">
        <div class="table-f">
          <div class="sports-table__td sports-table__time txt-c"><span>18:00</span></div>
          <div class="sports-table__td sports-table__matchup pr10" id="prematch_event-1001">
            <div class="sports-table__home txt-cut">Team A</div>
            <div class="sports-table__away txt-cut">Team B</div>
          </div>
        </div>
      </div>
      ${SIDEBAR_FULLY_EXPANDED_RESET}
    `;
  }
  const SIDEBAR_FULLY_EXPANDED_RESET = `
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
  `;

  function confirmedEmptyCompetitionPage(breadcrumb) {
    return `
      <div class="sports-view__crumbs">${breadcrumb}</div>
      <div class="gen__holder"><div class="search-results"><div class="gen__txt">There are no markets available.</div></div></div>
      ${SIDEBAR_FULLY_EXPANDED_RESET}
    `;
  }

  doc.addEventListener("click", (event) => {
    const id = event.target.id || "";

    if (id === "left_prematch_sport-4_ice_hockey_label-toggle") {
      if (!doc.getElementById("left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle")) {
        doc.body.innerHTML = SIDEBAR_EXPANDED;
      }
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_buttonmore-toggle") {
      doc.body.innerHTML = SIDEBAR_FULLY_EXPANDED;
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle") {
      doc.body.innerHTML = SIDEBAR_FULLY_EXPANDED + usaCompetitionLinks();
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_label-toggle") {
      doc.body.innerHTML = SIDEBAR_FULLY_EXPANDED + switzerlandCompetitionLinks();
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl") {
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>USA>NHL");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-4715478_national_league") {
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>Switzerland>National League");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-5527435_swiss_league") {
      doc.body.innerHTML = confirmedEmptyCompetitionPage("Ice Hockey>Switzerland>Swiss League");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-999_mismatch") {
      // Deliberately navigates to the WRONG competition's content.
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>Switzerland>National League");
      return;
    }
  });

  return dom;
}

test("walkSport expands a collapsed sport, clicks 'show more' until its label stops containing 'more', and visits every competition in every group", async () => {
  const dom = buildFakeIceHockeySite();
  const summary = await walkSport(dom.window.document, {
    sportId: 4,
    sportSlug: "ice_hockey",
    now: () => "2026-09-30T18:00:00.000Z",
  });

  assert.equal(summary.groups_seen, 2);
  assert.equal(summary.competitions_seen, 4); // USA/NHL + Switzerland's 3
  assert.equal(summary.competitions_visited, 4);
  // The one deliberately-mismatched competition ("999") is the only
  // failure -- see the dedicated mismatch test below for its detail.
  assert.equal(summary.failures.length, 1);
  assert.equal(summary.failures[0].stage, "CONTENT_VALIDATION");
  assert.equal(summary.failures[0].competition_id, "999");

  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));
  assert.equal(byId["100"].parse_result, "populated");
  assert.equal(byId["100"].fixtures.length, 1);
  assert.equal(byId["100"].fixtures[0].participant_1, "Team A");

  assert.equal(byId["4715478"].parse_result, "populated");
  assert.equal(byId["5527435"].parse_result, "confirmed_empty");
  assert.deepEqual(byId["5527435"].fixtures, []);
});

test("walkSport flags a competition whose resulting breadcrumb disagrees with the one requested, discards its fixtures, and records it as a failure", async () => {
  const dom = buildFakeIceHockeySite();
  const summary = await walkSport(dom.window.document, {
    sportId: 4,
    sportSlug: "ice_hockey",
    now: () => "2026-09-30T18:00:00.000Z",
  });

  const mismatch = summary.results.find((r) => r.competition_id === "999");
  assert.equal(mismatch.parse_result, "invalid_content_mismatch");
  assert.deepEqual(mismatch.fixtures, []);
  assert.equal(mismatch.failure_reason, "BREADCRUMB_DID_NOT_MATCH_REQUESTED_COMPETITION");

  const failureRecord = summary.failures.find((f) => f.competition_id === "999");
  assert.equal(failureRecord.stage, "CONTENT_VALIDATION");
  assert.equal(failureRecord.reason, "BREADCRUMB_DID_NOT_MATCH_REQUESTED_COMPETITION");
});

test("walkSport records a MISSING_COMPETITION failure when a competition's link fails to come back after reopening the sport/group", async () => {
  // USA has 2 competitions (NHL, AHL). After visiting NHL, the walker
  // reopens the sport and re-clicks the USA group toggle before trying
  // AHL -- this fake site deliberately only restores NHL's link on that
  // reopen, simulating a real-world inconsistency where a competition
  // link doesn't reliably come back. AHL's own link is then missing at
  // the exact moment walkSport tries to click it.
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
  </body></html>`);
  const doc = dom.window.document;
  let usaGroupOpenedCount = 0;

  const SIDEBAR = `
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>
  `;

  doc.addEventListener("click", (event) => {
    const id = event.target.id || "";
    if (id === "left_prematch_sport-4_ice_hockey_label-toggle") {
      if (!doc.getElementById("left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle")) {
        doc.body.innerHTML = SIDEBAR;
      }
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle") {
      usaGroupOpenedCount += 1;
      const links =
        usaGroupOpenedCount === 1
          ? `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>
             <a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl">AHL</a>`
          : `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>`; // AHL missing on reopen
      doc.body.innerHTML = SIDEBAR + links;
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl") {
      doc.body.innerHTML = `
        <div class="sports-view__crumbs">Ice Hockey>USA>NHL</div>
        <div class="sports-table"><div class="table-f">
          <div class="sports-table__td sports-table__matchup pr10" id="prematch_event-1"><div class="sports-table__home">A</div><div class="sports-table__away">B</div></div>
        </div></div>
        <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
      `;
    }
  });

  const summary = await walkSport(doc, { sportId: 4, sportSlug: "ice_hockey" });

  assert.equal(summary.competitions_seen, 2);
  const failureRecord = summary.failures.find((f) => f.stage === "MISSING_COMPETITION");
  assert.ok(failureRecord, "expected a MISSING_COMPETITION failure record");
  assert.equal(failureRecord.competition_id, "200");
  assert.equal(failureRecord.reason, "COMPETITION_LINK_NOT_FOUND_AT_CLICK_TIME");
});

test("walkSport records a typed failure and returns an honest empty summary when the sport root itself is missing", async () => {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
  const summary = await walkSport(dom.window.document, { sportId: 4, sportSlug: "ice_hockey" });

  assert.equal(summary.groups_seen, 0);
  assert.equal(summary.competitions_visited, 0);
  assert.deepEqual(summary.results, []);
  assert.equal(summary.failures.length, 1);
  assert.equal(summary.failures[0].reason, "SPORT_TOGGLE_NOT_FOUND");
});

test("walkSport respects maxGroups and maxCompetitionsPerGroup caps", async () => {
  const dom = buildFakeIceHockeySite();
  const summary = await walkSport(dom.window.document, {
    sportId: 4,
    sportSlug: "ice_hockey",
    now: () => "2026-09-30T18:00:00.000Z",
    maxGroups: 1,
    maxCompetitionsPerGroup: 1,
  });

  assert.equal(summary.groups_seen, 1);
  assert.equal(summary.competitions_visited, 1);
});
