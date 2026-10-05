/* Public source registry. Route patterns are intentionally narrow: a URL
 * must be confirmed by an observed public route before the walker follows it. */
(function (root) {
  const SOURCES = [
    {
      key: 'oddsportal', label: 'OddsPortal', hosts: ['oddsportal.com', 'www.oddsportal.com'],
      // Live fixture links use /<sport>/h2h/<participant>-<token>/
      // <participant>-<token>/; the final eight-character hash is optional.
      isEventUrl: (url) => /^\/[a-z-]+\/h2h\/[^/]+-[A-Za-z0-9]{8}\/[^/]+-[A-Za-z0-9]{8}(?:\/[^/]+)?\/?$/.test(url.pathname),
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
      discoveryStatus: 'READY',
    },
  ];

  // A host matches its listed name, any subdomain of it (m., www., en.),
  // a regional domain with the same name (oddsportal.ng, flashscore.co.uk),
  // or a numbered mirror the site redirects to (oddsportal1.com).
  function hostMatches(source, hostname) {
    const host = hostname.toLowerCase();
    return source.hosts.some((h) => {
      const base = h.replace(/^www\./, '');
      const name = base.split('.')[0];
      return host === base || host.endsWith('.' + base) || host.split('.').some((label) => new RegExp(`^${name}\\d*$`).test(label));
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
