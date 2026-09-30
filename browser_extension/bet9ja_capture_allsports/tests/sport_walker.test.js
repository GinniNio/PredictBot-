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

  // Real Bet9ja fixture ids are globally unique per event -- a fixed id
  // reused across unrelated competitions would (correctly) trip the
  // cross-competition contamination check in sport_walker.js, so every
  // caller passes its own.
  function populatedCompetitionPage(breadcrumb, fixtureId) {
    return `
      <div class="sports-view__crumbs">${breadcrumb}</div>
      <div class="sports-table">
        <div class="table-f">
          <div class="sports-table__td sports-table__time txt-c"><span>18:00</span></div>
          <div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${fixtureId}">
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
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>USA>NHL", "1001");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-4715478_national_league") {
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>Switzerland>National League", "2001");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-5527435_swiss_league") {
      doc.body.innerHTML = confirmedEmptyCompetitionPage("Ice Hockey>Switzerland>Swiss League");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-999_mismatch") {
      // Deliberately navigates to the WRONG competition's content.
      doc.body.innerHTML = populatedCompetitionPage("Ice Hockey>Switzerland>National League", "3001");
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
  assert.equal(summary.competitions_attempted, 4);
  assert.equal(summary.competitions_successful, 3);
  assert.equal(summary.competitions_failed, 1);
  // The one deliberately-mismatched competition ("999") is the only
  // failure -- see the dedicated mismatch test below for its detail.
  assert.equal(summary.failures.length, 1);
  assert.equal(summary.failures[0].stage, "CONTENT_VALIDATION");
  assert.equal(summary.failures[0].competition_id, "999");

  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));
  assert.equal(byId["100"].parse_result, "populated");
  assert.equal(byId["100"].fixtures.length, 1);
  assert.equal(byId["100"].fixtures[0].participant_1, "Team A");
  // Audit fields present on every result, success or failure.
  assert.equal(byId["100"].observed_breadcrumb_raw, "Ice Hockey>USA>NHL");
  assert.equal(byId["100"].source_url_after_click, "about:blank");

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
  assert.equal(summary.competitions_attempted, 0);
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
  assert.equal(summary.competitions_attempted, 1);
});

// ---------------------------------------------------------------------
// Regression coverage for the two real bugs found from a live
// 2026-09-30T17:00:14Z, 34-competition Ice Hockey walk (see
// sport_walker.js's own header comment, "TWO REAL BUGS..."). Unlike
// `buildFakeIceHockeySite` above (which wipes the whole sidebar/body on
// every navigation, incidentally hiding both bugs), this simulates what
// the real evidence showed: the sidebar and its group toggle PERSIST
// across navigation as a genuine accordion (a second click on an
// already-open group's toggle closes it), and a competition click
// APPENDS a new `.sports-table` block to a persistent content area
// rather than replacing it.
// ---------------------------------------------------------------------
function buildRealisticAccumulatingSite() {
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>
    <div id="sidebar-links"></div>
    <div class="sports-view__crumbs"></div>
    <div id="content"></div>
  </body></html>`);
  const doc = dom.window.document;
  let usaGroupOpen = false;

  // Fixture ids must be pure digits -- FIXTURE_ROW_ID_PATTERN in
  // fixture_parser.js is `/^prematch_event-(\d+)$/`, matching real
  // Bet9ja ids exactly.
  const FIXTURE_ID_BY_LABEL = { NHL: "100001", AHL: "200001" };

  function renderCompetitionPage(label) {
    doc.querySelector(".sports-view__crumbs").textContent = `Ice Hockey>USA>${label}`;
    const table = doc.createElement("div");
    table.className = "sports-table";
    table.innerHTML = `
      <div class="table-f">
        <div class="sports-table__td sports-table__time txt-c"><span>18:00</span></div>
        <div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${FIXTURE_ID_BY_LABEL[label]}">
          <div class="sports-table__home">${label} Home</div>
          <div class="sports-table__away">${label} Away</div>
        </div>
      </div>`;
    // APPENDS, never replaces or clears -- matching the real, confirmed
    // accumulation behavior (bug #1).
    doc.getElementById("content").appendChild(table);
  }

  doc.addEventListener("click", (event) => {
    const id = event.target.id || "";
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle") {
      // A REAL toggle: flips between showing and hiding the group's
      // competition links, exactly like the real accordion this bug was
      // found against.
      usaGroupOpen = !usaGroupOpen;
      doc.getElementById("sidebar-links").innerHTML = usaGroupOpen
        ? `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>
           <a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl">AHL</a>`
        : "";
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl") {
      renderCompetitionPage("NHL");
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl") {
      renderCompetitionPage("AHL");
      return;
    }
  });

  return dom;
}

test("does not toggle an already-open group closed while reopening it between competitions (real accordion, not a wipe-and-reset)", async () => {
  const dom = buildRealisticAccumulatingSite();
  const summary = await walkSport(dom.window.document, { sportId: 4, sportSlug: "ice_hockey", now: () => "2026-09-30T18:00:00Z" });

  // Before the fix, AHL would fail: the "reopen" step's unconditional
  // click on an already-open USA group toggled it CLOSED, so AHL's link
  // was gone by the time it was looked up.
  assert.equal(summary.competitions_attempted, 2);
  assert.deepEqual(
    summary.failures.filter((f) => f.stage === "MISSING_COMPETITION"),
    []
  );
  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));
  assert.equal(byId["100"].parse_result, "populated");
  assert.equal(byId["200"].parse_result, "populated");
});

test("scopes each competition's fixtures to only its own newly-added .sports-table, ignoring earlier competitions' tables left in the DOM", async () => {
  const dom = buildRealisticAccumulatingSite();
  const summary = await walkSport(dom.window.document, { sportId: 4, sportSlug: "ice_hockey", now: () => "2026-09-30T18:00:00Z" });

  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));
  // NHL's own table.
  assert.equal(byId["100"].fixtures.length, 1);
  assert.equal(byId["100"].fixtures[0].participant_1, "NHL Home");
  // AHL is visited AFTER NHL, with NHL's own .sports-table still sitting
  // in the DOM (never removed) -- before the fix this would report 2
  // fixtures (NHL's carried over plus AHL's own).
  assert.equal(byId["200"].fixtures.length, 1);
  assert.equal(byId["200"].fixtures[0].participant_1, "AHL Home");
});

test("rediscovers a group's competition links fresh on every iteration, picking up one that only appears after the first visit", async () => {
  // USA starts with only NHL. After NHL is visited and the group is
  // reopened, a SECOND competition (AHL) appears for the first time --
  // simulating a genuinely changing link list, not just a toggle. A
  // walker holding onto its first (frozen) discovery would never see
  // AHL at all.
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
  </body></html>`);
  const doc = dom.window.document;
  let usaOpenCount = 0;

  const SIDEBAR = `<div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>`;

  doc.addEventListener("click", (event) => {
    const id = event.target.id || "";
    if (id === "left_prematch_sport-4_ice_hockey_label-toggle") {
      if (!doc.getElementById("left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle")) {
        doc.body.innerHTML = SIDEBAR;
      }
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle") {
      usaOpenCount += 1;
      const links =
        usaOpenCount === 1
          ? `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>`
          : `<a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>
             <a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl">AHL</a>`;
      doc.body.innerHTML = SIDEBAR + links;
      return;
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl" || id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl") {
      doc.body.innerHTML = `
        <div class="sports-view__crumbs">Ice Hockey>USA>${id.endsWith("nhl") ? "NHL" : "AHL"}</div>
        <div class="sports-table"><div class="table-f">
          <div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${id.endsWith("nhl") ? "1" : "2"}"><div class="sports-table__home">A</div><div class="sports-table__away">B</div></div>
        </div></div>
        <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
      `;
    }
  });

  const summary = await walkSport(doc, { sportId: 4, sportSlug: "ice_hockey" });

  assert.equal(summary.competitions_seen, 2);
  assert.equal(summary.competitions_attempted, 2);
  assert.equal(summary.competitions_successful, 2);
  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));
  assert.equal(byId["100"].parse_result, "populated");
  assert.equal(byId["200"].parse_result, "populated");
});

test("rejects a competition whose newly-scoped fixture id was already attributed to an earlier competition in the same walk", async () => {
  // Table-level contamination check (see visitCompetition's own comment
  // on why fixture-id ownership stands in for a heading check with no
  // confirmed selector). Simulates the table-diffing fix somehow still
  // letting through a table carrying a fixture id already owned by an
  // earlier competition -- this must never be ingested even so.
  const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-4_ice_hockey_label-toggle">Ice Hockey</div>
    <div id="left_prematch_sport-4_ice_hockey_sg-1_usa_label-toggle">USA</div>
    <div id="links">
      <a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl">NHL</a>
      <a href="javascript:;" id="left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl">AHL</a>
    </div>
    <div class="sports-view__crumbs"></div>
    <div id="content"></div>
  </body></html>`);
  const doc = dom.window.document;

  function renderPage(label, fixtureId) {
    doc.querySelector(".sports-view__crumbs").textContent = `Ice Hockey>USA>${label}`;
    const table = doc.createElement("div");
    table.className = "sports-table";
    table.innerHTML = `<div class="table-f"><div class="sports-table__td sports-table__matchup pr10" id="prematch_event-${fixtureId}"><div class="sports-table__home">A</div><div class="sports-table__away">B</div></div></div>`;
    doc.getElementById("content").appendChild(table);
  }

  doc.addEventListener("click", (event) => {
    const id = event.target.id || "";
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-100_nhl") {
      renderPage("NHL", "999"); // owns fixture 999
    }
    if (id === "left_prematch_sport-4_ice_hockey_sg-1_usa_g-200_ahl") {
      // AHL's own click deliberately re-renders the SAME fixture id
      // (999) that NHL already owns -- simulating stale content leaking
      // through despite table-diffing.
      renderPage("AHL", "999");
    }
  });

  const summary = await walkSport(doc, { sportId: 4, sportSlug: "ice_hockey" });
  const byId = Object.fromEntries(summary.results.map((r) => [r.competition_id, r]));

  assert.equal(byId["100"].parse_result, "populated");
  assert.equal(byId["200"].parse_result, "invalid_content_mismatch");
  assert.equal(byId["200"].failure_reason, "STALE_FIXTURE_ID_REUSED_FROM_EARLIER_COMPETITION");
  assert.deepEqual(byId["200"].fixtures, []);
});
