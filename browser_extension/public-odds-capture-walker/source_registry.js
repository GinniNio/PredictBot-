/* Public source registry. Route patterns are intentionally narrow: a URL
 * must be confirmed by an observed public route before the walker follows it. */
(function (root) {
  const SOURCES = [
    {
      key: 'oddsportal', label: 'OddsPortal', hosts: ['oddsportal.com', 'www.oddsportal.com'],
      // Live fixture links use /<sport>/h2h/<participant>-<token>/
      // <participant>-<token>/; the final eight-character hash is optional.
      // In-play tabs (/inplay-odds/) are not pre-match prices and are skipped.
      isEventUrl: (url) => /^\/[a-z-]+\/h2h\/[^/]+-[A-Za-z0-9]{8}\/[^/]+-[A-Za-z0-9]{8}(?:\/[^/]+)?\/?$/.test(url.pathname)
        && !url.pathname.includes('/inplay-odds'),
      settleMs: 5000,   // [UNVERIFIED] wait for the bookmaker table to render
      discoveryStatus: 'READY',
    },
    {
      key: 'oddschecker', label: 'Oddschecker', hosts: ['oddschecker.com', 'www.oddschecker.com'],
      // Captured 2026-10-05 from the US homepage: event links use
      // /us/<sport>/<competition>/<event-slug>. Listing pages stop one
      // path segment earlier, so they are not admitted.
      isEventUrl: (url) => /^\/us\/(?:football|basketball|baseball|hockey|soccer|tennis|golf)\/[^/]+\/[^/]+\/?$/.test(url.pathname),
      discoveryStatus: 'READY',
    },
    {
      key: 'flashscore', label: 'Flashscore',
      // flashscore.info serves the same site; its event routes are assumed
      // to match .com until a real .info capture confirms them.
      hosts: ['flashscore.com', 'www.flashscore.com', 'flashscore.info', 'www.flashscore.info'],
      // Live fixture links have two participant slugs, each ending in an
      // eight-character opaque token.
      isEventUrl: (url) => /^\/match\/[a-z-]+\/[^/]+-[A-Za-z0-9]{8}\/[^/]+-[A-Za-z0-9]{8}\/?$/.test(url.pathname),
      discoveryStatus: 'READY',
    },
    {
      key: 'betexplorer', label: 'BetExplorer', hosts: ['betexplorer.com', 'www.betexplorer.com'],
      // Captured 2026-10-05 from the public homepage: match links end
      // in an opaque eight-character event token after sport/country/
      // competition/match-slug.
      isEventUrl: (url) => /^\/(?:football|basketball|tennis|ice-hockey|baseball|handball|volleyball)\/[^/]+\/[^/]+\/[^/]+\/[A-Za-z0-9]{8}\/?$/.test(url.pathname),
      discoveryStatus: 'READY',
    },
    {
      key: 'betfair_exchange', label: 'Betfair Exchange', hosts: ['betfair.com', 'www.betfair.com'],
      // The live public page was unavailable during the 2026-10-05 audit.
      // Do not auto-follow an unverified route shape.
      isEventUrl: () => false,
      discoveryStatus: 'EVIDENCE_REQUIRED',
    },
    {
      key: 'polymarket', label: 'Polymarket', hosts: ['polymarket.com', 'www.polymarket.com'],
      // Sports cards use /sports/<league>/<event>. /event/<slug> remains
      // admissible only in explicit all-markets runs.
      isEventUrl: (url) => /^\/sports\/[a-z0-9-]+\/(?!games\/?$|props\/?$)[a-z0-9-]+\/?$/.test(url.pathname) || /^\/(?:[a-z]{2}\/)?event\/[^/]+/.test(url.pathname),
      isSportsEvent: (url) => /^\/sports\/[a-z0-9-]+\/(?!games\/?$|props\/?$)[a-z0-9-]+\/?$/.test(url.pathname),
      // Catalogue stage: the rendered Sports navigation links each sport's
      // match listing as /sports/<league>/games (26 seen in the 2026-10-05
      // /sports/live capture). /props, /live and /futures are not listings
      // of head-to-head matches and are never visited.
      isListingUrl: (url) => /^\/sports\/[a-z0-9-]+\/games\/?$/.test(url.pathname),
      // Event pages are large (~800 KB); only the embedded market JSON is
      // kept, so a 100-event walk stays small enough to save.
      excerptPattern: 'outcomePrices',
      discoveryStatus: 'READY',
    },
  ];

  // A host matches its listed name, any subdomain of it (m., www., en.),
  // or a regional domain with the same name (oddsportal.ng, flashscore.co.uk).
  // Numbered look-alikes (oddsportal1.com) are NOT accepted: a 2026-10-05 DOM
  // audit found a different site structure, so it is not a verified mirror.
  // Explicit allowlist: a listed host or its subdomain (www., m.). A brand
  // name elsewhere in the hostname (oddsportal.evil.example, oddsportal1.com,
  // unlisted regional domains) is never trusted; register a host only after a
  // real capture from it has been checked.
  function hostMatches(source, hostname) {
    const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
    return source.hosts.some((h) => {
      const base = h.replace(/^www\./, '');
      return host === base || host.endsWith('.' + base);
    });
  }

  function findSource(urlLike) {
    let url;
    try { url = new URL(urlLike); } catch (_) { return null; }
    return SOURCES.find((source) => hostMatches(source, url.hostname)) || null;
  }

  const api = { SOURCES, findSource, hostMatches };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PublicOddsSources = api;
})(typeof window !== 'undefined' ? window : globalThis);
