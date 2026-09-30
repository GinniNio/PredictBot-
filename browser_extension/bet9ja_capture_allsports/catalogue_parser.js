/**
 * Bet9ja all-sports competition-catalogue parser -- stage 2a.
 *
 * CORRECTION (2026-09-30): a single competition page's fixtures
 * (e.g. NFL, NHL) is NOT the sport's crawl scope -- it is one leaf of it.
 * The real scope is the left-menu sport catalogue: every country/group
 * exposed under a sport's own accordion, and every competition link
 * beneath each group. `fixture_parser.js` (stage 2) still parses one
 * competition page's own fixtures once visited; this module is the
 * layer above it that enumerates WHICH competition pages exist to visit,
 * from the sport-level catalogue.
 *
 * Two real "left-menu sport catalogue" captures back this
 * (2026-09-30T15:50:00Z): American Football (sport_id 70, 3
 * competitions: USA/NFL, USA/"NCAA, Regular Season", Canada/CFL) and Ice
 * Hockey (sport_id 4, 34 competitions across 19 country groups,
 * including Russia exposed as a country group with ZERO competition
 * links, and repeated competition names -- "Extraliga" under Czech
 * Republic, Belarus, AND Slovakia -- that the ice-hockey capture's own
 * notes explicitly call out as requiring country-group context in the
 * natural key). `parseCatalogueSummary` below is built and tested
 * directly against both of those real captures' own JSON, unchanged.
 *
 * DISCOVERY SELECTOR (as reported by both captures, generalized here):
 *   `#left_prematch_sport-{sportId}_{sportSlug}_label-toggle` opens the
 *   sport's own accordion; competition links are read from
 *   `a.menu-list__link` beneath each country group. Ice Hockey's capture
 *   additionally reports a `#left_prematch_sport-4_ice_hockey_
 *   buttonmore-toggle` needed to reveal competitions beyond a default
 *   visible set -- `buildDiscoverySelectors` includes this as an
 *   OPTIONAL selector, since American Football's own capture reported no
 *   such toggle (3 competitions apparently fit without one).
 *
 * WHAT THIS MODULE DOES NOT DO YET: neither catalogue capture includes
 * each competition link's own `href` or stable id -- only its visible
 * country/competition text. `parseCatalogueFromDocument` (the DOM-level
 * enumerator, for when a real page is captured rather than summarized)
 * reads real hrefs/ids using the `left_prematch_sport-<id>_<slug>_sg-
 * <group-id>_<group>_g-<competition-id>_<competition>` id pattern this
 * project already confirmed generically on an earlier real capture of
 * this same sidebar -- but that pattern has not been independently
 * byte-verified specifically against American Football's or Ice
 * Hockey's own raw markup, only against the summary-level catalogue
 * captures above. Treat `parseCatalogueFromDocument` as evidenced by
 * analogy, not independently confirmed for these two sports, until a raw
 * snapshot of one of their sidebars is supplied. Live navigation
 * (actually expanding the accordion, clicking "show more", following
 * each competition link) is NOT built here either -- this session still
 * has no live browser access to test it against, the same limitation
 * already documented for the existing Soccer walker and this
 * extension's own stage-1 README.
 *
 * UPDATE (2026-09-30T17:03Z): a live DOM inspection resolved several of
 * the above gaps. Evidence tier: this is a hand-distilled inspection
 * report (`tests/fixtures/selected-sports-expanded-sidebar-2026-09-30.html`)
 * -- clean, minimal, annotated markup written to communicate specific
 * id strings, not a raw `outerHTML` extension snapshot of the real,
 * noisy Bet9ja page. It carries the same evidence tier as this
 * project's earlier "DOM extraction contract" JSON captures: the ids and
 * structural relationships quoted in it are treated as real, but its own
 * incidental markup (e.g. its `class="id"` styling hook) is NOT Bet9ja's
 * actual class name and is never used as a selector. Confirmed by it:
 *
 * - **Full sport-level catalogue**: all 34 top-level sport roots
 *   (`left_prematch_sport-<id>_<slug>_label-toggle`), including Bandy
 *   (15) and Field Hockey (321), which had no fixture-content evidence
 *   until now either -- see `CONFIRMED_SPORTS` below. Nested country/
 *   group nodes share the same id prefix as their sport root and must
 *   never be mistaken for one (this module already avoids that by
 *   requiring an EXACT `#id` match for the sport toggle, not a prefix
 *   match).
 * - **Real group-level ids** for Ice Hockey (all 19 country groups, now
 *   with `sg-<id>` values -- exactly reconciling the earlier summary
 *   catalogue's 19-country count), Tennis (8 groups), Volleyball (5),
 *   Handball (10 visible + a "Show 4 A-Z more" toggle), American
 *   Football (2) -- see `parseGroupsFromDocument`.
 * - **Real competition-link markup**, confirmed after clicking
 *   Switzerland under Ice Hockey: `<a href="javascript:;"
 *   id="left_prematch_sport-4_ice_hockey_sg-44092_switzerland_g-
 *   4715478_national_league">`. Two corrections this forces: (1) the
 *   link's own `href` is the literal string `javascript:;`, never a real
 *   URL -- a walker must `.click()` the element itself, it cannot follow
 *   `href`; (2) the confirmed selector for reading competition links is
 *   `a[id*="_g-"]`, not a `.menu-list__link` class match (this module's
 *   own earlier, unconfirmed guess).
 * - **The "show more" toggle's own click contract**: click it only while
 *   its visible label CONTAINS "more" (case-insensitively) -- Ice
 *   Hockey's toggle reads "Show less" once already expanded (stop
 *   clicking), Handball's reads "Show 4 A-Z more" (still needs a click).
 *   See `needsMoreClick`.
 */
(function (root) {
  const SCHEMA_VERSION = 'bet9ja-allsports-competition-catalogue.v1';

  function slugify(value) {
    return (value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '');
  }

  function buildNaturalKey(sportSlug, countryRaw, competitionRaw) {
    return `${sportSlug}::${slugify(countryRaw)}::${slugify(competitionRaw)}`;
  }

  /**
   * Normalizes the "left-menu sport catalogue" JSON shape (as captured
   * by the operator's own catalogue tool) into a flat list of
   * competition entries, one per (country, competition) pair -- a
   * country group with zero competitions (e.g. Ice Hockey's Russia) is
   * still recorded, with an empty competitions array, never dropped.
   *
   * Reconciles the catalogue's own declared `competition_count` against
   * the actual sum of per-country competition lists -- both real
   * captures reconcile exactly (3 for American Football, 34 for Ice
   * Hockey), so a future catalogue that DOESN'T reconcile is flagged
   * (`count_reconciled: false`) rather than silently trusted.
   *
   * Duplicate competition names across different countries (confirmed
   * real: "Extraliga" in Czech Republic/Belarus/Slovakia) are expected
   * and produce distinct entries/natural keys, one per country -- this
   * function never collapses them.
   */
  function parseCatalogueSummary(catalogue) {
    if (!catalogue || typeof catalogue.groups !== 'object' || catalogue.groups === null) {
      throw new Error('parseCatalogueSummary requires a catalogue with a `groups` object.');
    }
    const sportSlug = slugify(catalogue.sport);
    const entries = [];
    const countriesWithZeroCompetitions = [];

    for (const [countryRaw, competitions] of Object.entries(catalogue.groups)) {
      const list = Array.isArray(competitions) ? competitions : [];
      if (list.length === 0) {
        countriesWithZeroCompetitions.push(countryRaw);
      }
      for (const competitionRaw of list) {
        entries.push({
          sport: catalogue.sport || null,
          sport_id: catalogue.sport_id != null ? catalogue.sport_id : null,
          country_raw: countryRaw,
          competition_raw: competitionRaw,
          natural_key: buildNaturalKey(sportSlug, countryRaw, competitionRaw),
        });
      }
    }

    const naturalKeyCounts = new Map();
    for (const entry of entries) {
      naturalKeyCounts.set(entry.natural_key, (naturalKeyCounts.get(entry.natural_key) || 0) + 1);
    }
    const duplicateNaturalKeys = Array.from(naturalKeyCounts.entries())
      .filter(([, count]) => count > 1)
      .map(([key]) => key);

    const competitionCountDeclared = catalogue.competition_count != null ? catalogue.competition_count : null;
    const competitionCountComputed = entries.length;

    return {
      schema_version: SCHEMA_VERSION,
      sport: catalogue.sport || null,
      sport_id: catalogue.sport_id != null ? catalogue.sport_id : null,
      sport_slug: sportSlug,
      captured_at_utc: catalogue.captured_at_utc || null,
      countries_total: Object.keys(catalogue.groups).length,
      countries_with_zero_competitions: countriesWithZeroCompetitions,
      competition_count_declared: competitionCountDeclared,
      competition_count_computed: competitionCountComputed,
      count_reconciled: competitionCountDeclared === null ? null : competitionCountDeclared === competitionCountComputed,
      duplicate_natural_keys: duplicateNaturalKeys,
      entries,
    };
  }

  /**
   * Builds the discovery selectors a live walker would need for a given
   * sport -- see header comment for the evidence and its limits. The
   * "show more" toggle is always offered as OPTIONAL (a caller checks
   * for its presence before clicking; American Football's own capture
   * reported none), never assumed required.
   */
  function buildDiscoverySelectors(sportId, sportSlug) {
    const base = `left_prematch_sport-${sportId}_${sportSlug}`;
    return {
      sportToggle: `#${base}_label-toggle`,
      showMoreToggleOptional: `#${base}_buttonmore-toggle`,
      competitionLinkIdPrefix: `${base}_sg-`,
    };
  }

  // Matches `left_prematch_sport-<id>_<slug>_sg-<groupId>_<group>_g-
  // <competitionId>_<competition>` -- see header comment for this
  // pattern's evidence tier (confirmed generically elsewhere on this
  // sidebar, not independently byte-verified for American Football or
  // Ice Hockey specifically).
  const COMPETITION_LINK_ID_PATTERN = /^left_prematch_sport-(\d+)_([a-z0-9_]+)_sg-(\d+)_([a-z0-9_]+)_g-(\d+)_([a-z0-9_]+)$/;

  /**
   * DOM-level enumerator for when a real sidebar page (not just a
   * summary contract) is captured. Scopes to the sport's own toggle
   * element and reads every descendant `a.menu-list__link` whose `id`
   * matches the confirmed competition-link pattern, so links belonging
   * to a DIFFERENT sport's accordion are never picked up by a loose
   * document-wide query.
   */
  function parseCatalogueFromDocument(documentLike, { sportId, sportSlug }) {
    if (!documentLike || typeof documentLike.querySelector !== 'function') {
      throw new Error('parseCatalogueFromDocument requires a Document-like object.');
    }
    const selectors = buildDiscoverySelectors(sportId, sportSlug);
    const toggle = documentLike.querySelector(selectors.sportToggle);
    if (!toggle) {
      return { ok: false, reason: 'SPORT_TOGGLE_NOT_FOUND', selectors, entries: [] };
    }
    const showMoreToggle = documentLike.querySelector(selectors.showMoreToggleOptional);

    // Confirmed selector (2026-09-30 live inspection): `a[id*="_g-"]`,
    // scoped by id PREFIX rather than DOM containment -- each
    // competition link's own id already embeds its sport id/slug
    // (`competitionLinkIdPrefix`), which is what actually keeps a
    // sibling sport's links out, regardless of how the real page nests
    // its accordion sections.
    const links = Array.from(documentLike.querySelectorAll('a[id*="_g-"]')).filter(
      (el) => el.id && el.id.startsWith(selectors.competitionLinkIdPrefix)
    );

    const entries = links
      .map((el) => {
        const match = el.id.match(COMPETITION_LINK_ID_PATTERN);
        if (!match) return null;
        const [, matchedSportId, matchedSportSlug, groupId, groupSlug, competitionId, competitionSlug] = match;
        return {
          sport_id: Number(matchedSportId),
          sport_slug: matchedSportSlug,
          group_id: groupId,
          group_slug: groupSlug,
          competition_id: competitionId,
          competition_slug: competitionSlug,
          // Confirmed real value: the literal string "javascript:;" --
          // never a real URL. A walker must call `.click()` on the
          // element itself; `href` cannot be followed/fetched directly.
          href: el.getAttribute('href') || null,
          requires_click_navigation: true,
          label_raw: (el.textContent || '').replace(/\s+/g, ' ').trim(),
          natural_key: buildNaturalKey(matchedSportSlug, groupSlug, competitionSlug),
        };
      })
      .filter(Boolean);

    return {
      ok: true,
      reason: null,
      selectors,
      show_more_toggle_present: !!showMoreToggle,
      entries,
    };
  }

  // Group toggle id pattern: `left_prematch_sport-<id>_<slug>_sg-
  // <groupId>_<group>_label-toggle` -- confirmed real for Ice Hockey (19
  // groups), Tennis (8), Volleyball (5), Handball (10 + show-more),
  // American Football (2). Distinguished from a competition link by its
  // `_label-toggle` suffix (a competition link ends in its own
  // competition slug instead).
  const GROUP_TOGGLE_ID_PATTERN = /^left_prematch_sport-(\d+)_([a-z0-9_]+)_sg-(\d+)_([a-z0-9_]+)_label-toggle$/;

  /**
   * Enumerates a sport's own country/group toggles (the level ABOVE
   * competition links) -- the step a walker clicks before competition
   * links for that group become visible/queryable.
   */
  function parseGroupsFromDocument(documentLike, { sportId, sportSlug }) {
    if (!documentLike || typeof documentLike.querySelectorAll !== 'function') {
      throw new Error('parseGroupsFromDocument requires a Document-like object.');
    }
    const prefix = `left_prematch_sport-${sportId}_${sportSlug}_sg-`;
    const groups = Array.from(documentLike.querySelectorAll('[id^="left_prematch_sport-"]'))
      .filter((el) => el.id.startsWith(prefix) && el.id.endsWith('_label-toggle'))
      .map((el) => {
        const match = el.id.match(GROUP_TOGGLE_ID_PATTERN);
        if (!match) return null;
        const [, matchedSportId, matchedSportSlug, groupId, groupSlug] = match;
        return {
          sport_id: Number(matchedSportId),
          sport_slug: matchedSportSlug,
          group_id: groupId,
          group_slug: groupSlug,
          label_raw: (el.textContent || '').replace(/\s+/g, ' ').trim(),
        };
      })
      .filter(Boolean);
    return groups;
  }

  // Confirmed real (2026-09-30 live inspection): click the "show more"
  // toggle only while its OWN visible label still contains "more"
  // (case-insensitive) -- e.g. Handball's "Show 4 A-Z more" needs a
  // click, Ice Hockey's "Show less" (already expanded) does not. Never
  // click unconditionally on the toggle's mere presence.
  function needsMoreClick(labelRaw) {
    return /more/i.test((labelRaw || '').trim());
  }

  // Confirmed real top-level sport catalogue (2026-09-30 live
  // inspection of the rendered sidebar) -- 34 sports, each with its own
  // `left_prematch_sport-<id>_<slug>_label-toggle` root id. Includes
  // Bandy (15) and Field Hockey (321), previously known only from a
  // plain-text sidebar paste with no id evidence at all.
  const CONFIRMED_SPORTS = [
    ['left_prematch_sport-1_soccer_label-toggle', 'Soccer'],
    ['left_prematch_sport-2000001_specials_soccer_label-toggle', 'Specials Soccer'],
    ['left_prematch_sport-305_specials_combo_label-toggle', 'Specials Combo'],
    ['left_prematch_sport-101_zoom_soccer_label-toggle', 'Zoom Soccer'],
    ['left_prematch_sport-303_players_zoom_soccer_label-toggle', 'Players Zoom Soccer'],
    ['left_prematch_sport-5_tennis_label-toggle', 'Tennis'],
    ['left_prematch_sport-102_zoom_tennis_label-toggle', 'Zoom Tennis'],
    ['left_prematch_sport-2_basketball_label-toggle', 'Basketball'],
    ['left_prematch_sport-2000002_specials_basketball_label-toggle', 'Specials Basketball'],
    ['left_prematch_sport-23_volleyball_label-toggle', 'Volleyball'],
    ['left_prematch_sport-70_american_football_label-toggle', 'American Football'],
    ['left_prematch_sport-304_players_am__football_label-toggle', 'Players Am. Football'],
    ['left_prematch_sport-3_baseball_label-toggle', 'Baseball'],
    ['left_prematch_sport-6_handball_label-toggle', 'Handball'],
    ['left_prematch_sport-12_rugby_label-toggle', 'Rugby'],
    ['left_prematch_sport-11_motor_sports_label-toggle', 'Motor Sports'],
    ['left_prematch_sport-4_ice_hockey_label-toggle', 'Ice Hockey'],
    ['left_prematch_sport-541_alpine_label-toggle', 'Alpine'],
    ['left_prematch_sport-561_biathlon_label-toggle', 'Biathlon'],
    ['left_prematch_sport-601_cross-country_label-toggle', 'Cross-Country'],
    ['left_prematch_sport-13_aussie_rules_label-toggle', 'Aussie Rules'],
    ['left_prematch_sport-179_badminton_label-toggle', 'Badminton'],
    ['left_prematch_sport-15_bandy_label-toggle', 'Bandy'],
    ['left_prematch_sport-261_boxing_label-toggle', 'Boxing'],
    ['left_prematch_sport-181_cricket_label-toggle', 'Cricket'],
    ['left_prematch_sport-22_darts_label-toggle', 'Darts'],
    ['left_prematch_sport-321_field_hockey_label-toggle', 'Field Hockey'],
    ['left_prematch_sport-178_floorball_label-toggle', 'Floorball'],
    ['left_prematch_sport-180_futsal_label-toggle', 'Futsal'],
    ['left_prematch_sport-341_mma_label-toggle', 'MMA'],
    ['left_prematch_sport-19_snooker_label-toggle', 'Snooker'],
    ['left_prematch_sport-346_squash_label-toggle', 'Squash'],
    ['left_prematch_sport-20_table_tennis_label-toggle', 'Table Tennis'],
    ['left_prematch_sport-182_waterpolo_label-toggle', 'Waterpolo'],
  ].map(([id, label]) => {
    const match = id.match(/^left_prematch_sport-(\d+)_([a-z0-9_-]+)_label-toggle$/);
    return {
      sport_id: Number(match[1]),
      sport_slug: match[2],
      label,
      toggle_id: id,
    };
  });

  const api = {
    parseCatalogueSummary,
    parseCatalogueFromDocument,
    parseGroupsFromDocument,
    needsMoreClick,
    buildDiscoverySelectors,
    buildNaturalKey,
    slugify,
    CONFIRMED_SPORTS,
    COMPETITION_LINK_ID_PATTERN,
    GROUP_TOGGLE_ID_PATTERN,
    SCHEMA_VERSION,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaAllSportsCatalogueParser = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
