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

    // Scoped by id PREFIX, not by DOM containment -- each competition
    // link's own id already embeds its sport id/slug
    // (`competitionLinkIdPrefix`), which is what actually keeps a
    // sibling sport's links out, regardless of how the real page nests
    // its accordion sections (unconfirmed for these two sports -- see
    // header comment).
    const links = Array.from(documentLike.querySelectorAll('a.menu-list__link')).filter(
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
          href: el.getAttribute('href') || null,
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

  const api = {
    parseCatalogueSummary,
    parseCatalogueFromDocument,
    buildDiscoverySelectors,
    buildNaturalKey,
    slugify,
    COMPETITION_LINK_ID_PATTERN,
    SCHEMA_VERSION,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.Bet9jaAllSportsCatalogueParser = api;
  }
})(typeof window !== 'undefined' ? window : globalThis);
