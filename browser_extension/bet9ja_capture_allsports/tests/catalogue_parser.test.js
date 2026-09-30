const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const {
  parseCatalogueSummary,
  parseCatalogueFromDocument,
  parseGroupsFromDocument,
  needsMoreClick,
  buildDiscoverySelectors,
  buildNaturalKey,
  CONFIRMED_SPORTS,
} = require("../catalogue_parser.js");

// Real capture: bet9ja-american-football-all-competitions-2026-09-30.json
const AMERICAN_FOOTBALL_CATALOGUE = {
  capture_type: "left-menu sport catalogue",
  captured_at_utc: "2026-09-30T15:50:00Z",
  sport: "American Football",
  sport_id: 70,
  competition_count: 3,
  discovery_selector:
    "#left_prematch_sport-70_american_football_label-toggle; read a.menu-list__link beneath each country group",
  groups: {
    USA: ["NFL", "NCAA, Regular Season"],
    Canada: ["CFL"],
  },
};

// Real capture: bet9ja-ice-hockey-all-competitions-2026-09-30.json
const ICE_HOCKEY_CATALOGUE = {
  capture_type: "left-menu sport catalogue",
  captured_at_utc: "2026-09-30T15:50:00Z",
  sport: "Ice Hockey",
  sport_id: 4,
  competition_count: 34,
  discovery_selector:
    "#left_prematch_sport-4_ice_hockey_label-toggle; expand #left_prematch_sport-4_ice_hockey_buttonmore-toggle; read a.menu-list__link beneath each country group",
  groups: {
    USA: ["NHL", "AHL"],
    International: ["Alps Hockey League", "Asia League", "Champions Hockey League"],
    Canada: ["OHL", "Quebec Major Junior Hockey League", "WHL"],
    Russia: [],
    Sweden: ["SHL", "Allsvenskan", "HockeyEttan"],
    Norway: ["Eliteserien", "1st Division"],
    Germany: ["DEL", "DEL 2"],
    Austria: ["ICE Hockey League"],
    Belarus: ["Extraliga"],
    "Czech Republic": ["Extraliga", "1. Liga", "2. Liga", "University League"],
    Denmark: ["Superisligaen"],
    England: ["Elite League"],
    Finland: ["Liiga", "Mestis", "U20 SM Sarja"],
    France: ["Ligue Magnus"],
    Kazakhstan: ["Pro Hockey League"],
    Latvia: ["Latvian Hockey League"],
    Poland: ["Polska Hokej Liga"],
    Slovakia: ["Extraliga", "SHL"],
    Switzerland: ["National League", "Swiss League"],
  },
  notes: [
    "Russia was exposed as a country group but had no competition link in this capture.",
    "Same-named competitions require country-group context as part of the natural competition key.",
  ],
};

test("parses the real American Football catalogue: 3 competitions across 2 countries, count reconciled", () => {
  const result = parseCatalogueSummary(AMERICAN_FOOTBALL_CATALOGUE);

  assert.equal(result.sport, "American Football");
  assert.equal(result.sport_id, 70);
  assert.equal(result.countries_total, 2);
  assert.equal(result.competition_count_declared, 3);
  assert.equal(result.competition_count_computed, 3);
  assert.equal(result.count_reconciled, true);
  assert.deepEqual(result.countries_with_zero_competitions, []);
  assert.deepEqual(
    result.entries.map((e) => `${e.country_raw}/${e.competition_raw}`),
    ["USA/NFL", "USA/NCAA, Regular Season", "Canada/CFL"]
  );
});

test("parses the real Ice Hockey catalogue: 34 competitions reconciled, Russia kept with zero competitions", () => {
  const result = parseCatalogueSummary(ICE_HOCKEY_CATALOGUE);

  assert.equal(result.sport_id, 4);
  assert.equal(result.countries_total, 19);
  assert.equal(result.competition_count_declared, 34);
  assert.equal(result.competition_count_computed, 34);
  assert.equal(result.count_reconciled, true);
  assert.deepEqual(result.countries_with_zero_competitions, ["Russia"]);
});

test("gives 'Extraliga' in three different countries three distinct natural keys, not a collapsed duplicate", () => {
  const result = parseCatalogueSummary(ICE_HOCKEY_CATALOGUE);
  const extraligaEntries = result.entries.filter((e) => e.competition_raw === "Extraliga");

  assert.equal(extraligaEntries.length, 3);
  const countries = extraligaEntries.map((e) => e.country_raw).sort();
  assert.deepEqual(countries, ["Belarus", "Czech Republic", "Slovakia"]);

  const naturalKeys = new Set(extraligaEntries.map((e) => e.natural_key));
  assert.equal(naturalKeys.size, 3, "each country's Extraliga must have its own natural key");
});

test("flags a catalogue whose declared count disagrees with its own group lists", () => {
  const bad = { ...AMERICAN_FOOTBALL_CATALOGUE, competition_count: 99 };
  const result = parseCatalogueSummary(bad);
  assert.equal(result.count_reconciled, false);
  assert.equal(result.competition_count_declared, 99);
  assert.equal(result.competition_count_computed, 3);
});

test("buildDiscoverySelectors matches both real captures' own reported selectors", () => {
  const afSelectors = buildDiscoverySelectors(70, "american_football");
  assert.equal(afSelectors.sportToggle, "#left_prematch_sport-70_american_football_label-toggle");

  const ihSelectors = buildDiscoverySelectors(4, "ice_hockey");
  assert.equal(ihSelectors.sportToggle, "#left_prematch_sport-4_ice_hockey_label-toggle");
  assert.equal(ihSelectors.showMoreToggleOptional, "#left_prematch_sport-4_ice_hockey_buttonmore-toggle");
});

test("buildNaturalKey is stable across raw-text variants of the same country/competition", () => {
  const a = buildNaturalKey("ice_hockey", "Czech Republic", "Extraliga");
  const b = buildNaturalKey("ice_hockey", "czech-republic", "extraliga");
  assert.equal(a, b);
});

// Synthetic markup shaped like the confirmed sidebar id pattern
// (left_prematch_sport-<id>_<slug>_sg-<group>_g-<competition>) --
// see catalogue_parser.js's own header comment on this pattern's
// evidence tier: confirmed generically elsewhere on this sidebar, not
// independently byte-verified for these two sports.
function buildCataloguePage({ sportId, sportSlug, includeShowMore, links }) {
  const linksHtml = links
    .map(
      ({ groupId, groupSlug, competitionId, competitionSlug, label, href }) => `
      <a class="menu-list__link"
         id="left_prematch_sport-${sportId}_${sportSlug}_sg-${groupId}_${groupSlug}_g-${competitionId}_${competitionSlug}"
         href="${href}">${label}</a>`
    )
    .join("\n");

  return `<!DOCTYPE html><html><body>
    <div id="left_prematch_sport-${sportId}_${sportSlug}">
      <a id="left_prematch_sport-${sportId}_${sportSlug}_label-toggle" href="#">${sportSlug}</a>
      ${includeShowMore ? `<a id="left_prematch_sport-${sportId}_${sportSlug}_buttonmore-toggle" href="#">Show more</a>` : ""}
      ${linksHtml}
    </div>
    <div id="left_prematch_sport-999_soccer">
      <a class="menu-list__link" id="left_prematch_sport-999_soccer_sg-1_italy_g-1_serie_a" href="/competition/soccer/italy/serie-a/1">Serie A</a>
    </div>
  </body></html>`;
}

test("parseCatalogueFromDocument enumerates only the target sport's competition links, ignoring a sibling sport's accordion", () => {
  const dom = new JSDOM(
    buildCataloguePage({
      sportId: 70,
      sportSlug: "american_football",
      includeShowMore: false,
      links: [
        { groupId: 1, groupSlug: "usa", competitionId: 1, competitionSlug: "nfl", label: "NFL", href: "/competition/americanfootball/usa/nfl/1" },
        { groupId: 1, groupSlug: "usa", competitionId: 2, competitionSlug: "ncaa", label: "NCAA, Regular Season", href: "/competition/americanfootball/usa/ncaa/2" },
        { groupId: 2, groupSlug: "canada", competitionId: 3, competitionSlug: "cfl", label: "CFL", href: "/competition/americanfootball/canada/cfl/3" },
      ],
    })
  );

  const result = parseCatalogueFromDocument(dom.window.document, { sportId: 70, sportSlug: "american_football" });

  assert.equal(result.ok, true);
  assert.equal(result.show_more_toggle_present, false);
  assert.equal(result.entries.length, 3);
  assert.equal(result.entries[0].href, "/competition/americanfootball/usa/nfl/1");
  assert.equal(result.entries[0].competition_slug, "nfl");
  // The sibling Soccer accordion's link must never leak in.
  assert.ok(!result.entries.some((e) => e.sport_slug === "soccer"));
});

test("parseCatalogueFromDocument reports the optional show-more toggle when present", () => {
  const dom = new JSDOM(
    buildCataloguePage({
      sportId: 4,
      sportSlug: "ice_hockey",
      includeShowMore: true,
      links: [
        { groupId: 1, groupSlug: "usa", competitionId: 1, competitionSlug: "nhl", label: "NHL", href: "/competition/icehockey/usa/nhl/1" },
      ],
    })
  );

  const result = parseCatalogueFromDocument(dom.window.document, { sportId: 4, sportSlug: "ice_hockey" });
  assert.equal(result.show_more_toggle_present, true);
});

test("parseCatalogueFromDocument reports a typed failure when the sport's own toggle is missing", () => {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");
  const result = parseCatalogueFromDocument(dom.window.document, { sportId: 4, sportSlug: "ice_hockey" });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "SPORT_TOGGLE_NOT_FOUND");
});

// Real live-DOM export: bet9ja-selected-sports-expanded-sidebar-2026-09-30.html
// (2026-09-30T17:03Z). See catalogue_parser.js's own header comment for
// this evidence's tier -- ids/structure treated as real, incidental
// styling (e.g. class="id") is not.
const LIVE_SIDEBAR_HTML = fs.readFileSync(
  path.join(__dirname, "fixtures", "selected-sports-expanded-sidebar-2026-09-30.html"),
  "utf8"
);

test("parseGroupsFromDocument finds all 19 real Ice Hockey country groups, excluding the show-more toggle", () => {
  const dom = new JSDOM(LIVE_SIDEBAR_HTML);
  const groups = parseGroupsFromDocument(dom.window.document, { sportId: 4, sportSlug: "ice_hockey" });

  assert.equal(groups.length, 19);
  assert.ok(groups.some((g) => g.label_raw === "Switzerland" && g.group_id === "44092"));
  assert.ok(groups.every((g) => g.sport_id === 4 && g.sport_slug === "ice_hockey"));
});

test("parseGroupsFromDocument finds the real group counts for Tennis, Volleyball, Handball, American Football", () => {
  const dom = new JSDOM(LIVE_SIDEBAR_HTML);
  assert.equal(parseGroupsFromDocument(dom.window.document, { sportId: 5, sportSlug: "tennis" }).length, 8);
  assert.equal(parseGroupsFromDocument(dom.window.document, { sportId: 23, sportSlug: "volleyball" }).length, 5);
  assert.equal(parseGroupsFromDocument(dom.window.document, { sportId: 6, sportSlug: "handball" }).length, 10);
  assert.equal(
    parseGroupsFromDocument(dom.window.document, { sportId: 70, sportSlug: "american_football" }).length,
    2
  );
});

test("parseCatalogueFromDocument reads the real Switzerland/Ice Hockey competition links, flagging click-required javascript:; hrefs", () => {
  const dom = new JSDOM(LIVE_SIDEBAR_HTML);
  const result = parseCatalogueFromDocument(dom.window.document, { sportId: 4, sportSlug: "ice_hockey" });

  assert.equal(result.ok, true);
  assert.equal(result.entries.length, 2);
  const [nationalLeague, swissLeague] = result.entries;
  assert.equal(nationalLeague.competition_id, "4715478");
  assert.equal(nationalLeague.competition_slug, "national_league");
  assert.equal(nationalLeague.group_slug, "switzerland");
  assert.equal(nationalLeague.href, "javascript:;");
  assert.equal(nationalLeague.requires_click_navigation, true);
  assert.equal(swissLeague.competition_id, "5527435");
});

test("needsMoreClick matches the real Handball 'more' label but not Ice Hockey's already-expanded 'Show less'", () => {
  assert.equal(needsMoreClick("Show 4 A-Z more"), true);
  assert.equal(needsMoreClick("Show less"), false);
  assert.equal(needsMoreClick(""), false);
});

test("CONFIRMED_SPORTS lists all 34 real sport roots, including Bandy and Field Hockey", () => {
  assert.equal(CONFIRMED_SPORTS.length, 34);
  const bandy = CONFIRMED_SPORTS.find((s) => s.label === "Bandy");
  assert.equal(bandy.sport_id, 15);
  assert.equal(bandy.sport_slug, "bandy");

  const fieldHockey = CONFIRMED_SPORTS.find((s) => s.label === "Field Hockey");
  assert.equal(fieldHockey.sport_id, 321);

  const iceHockey = CONFIRMED_SPORTS.find((s) => s.label === "Ice Hockey");
  assert.equal(iceHockey.sport_id, 4);
  assert.equal(iceHockey.toggle_id, "left_prematch_sport-4_ice_hockey_label-toggle");
});
