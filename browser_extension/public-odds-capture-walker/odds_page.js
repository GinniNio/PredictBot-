/* Pure page-side capture and discovery. It never requests an API, submits a
 * form, opens a login, or clicks a betting control. */
(function (root) {
  const SOURCE_API = typeof module !== 'undefined' && module.exports ? require('./source_registry.js') : root.PublicOddsSources;
  const SCHEMA_VERSION = 'public-odds-page-capture.v1';

  function cleanText(value) { return (value || '').replace(/\s+/g, ' ').trim(); }
  function pageContext(documentLike, href, title, capturedAtUtc) {
    const source = SOURCE_API.findSource(href);
    if (!source) return { ok: false, reason: 'UNSUPPORTED_SOURCE' };
    return {
      ok: true, source, source_key: source.key, source_label: source.label,
      source_url: href, page_title: title || '', captured_at_utc: capturedAtUtc,
    };
  }
  function captureCurrentPage(documentLike, context) {
    if (!documentLike || !documentLike.documentElement) throw new Error('captureCurrentPage requires a document.');
    const page = pageContext(documentLike, context.href, context.title, context.capturedAtUtc);
    if (!page.ok) return { schema_version: SCHEMA_VERSION, capture_status: 'CAPTURE_FAILED', failure_reason: page.reason };
    const html = documentLike.documentElement.outerHTML;
    return {
      schema_version: SCHEMA_VERSION,
      capture_status: 'CAPTURE_OK',
      source_key: page.source_key,
      source_label: page.source_label,
      source_url: page.source_url,
      page_title: page.page_title,
      captured_at_utc: page.captured_at_utc,
      html_length: html.length,
      html,
    };
  }
  function discoverEventLinks(documentLike, href, scope) {
    const source = SOURCE_API.findSource(href);
    if (!source) return { ok: false, reason: 'UNSUPPORTED_SOURCE', links: [] };
    const links = [];
    const seen = new Set();
    for (const anchor of Array.from(documentLike.querySelectorAll('a[href]'))) {
      let url;
      try { url = new URL(anchor.getAttribute('href'), href); } catch (_) { continue; }
      const labelRaw = cleanText(anchor.textContent);
      if (!SOURCE_API.hostMatches(source, url.hostname) || !source.isEventUrl(url)) continue;
      if (scope === 'sports' && source.isSportsEvent && !source.isSportsEvent(url, labelRaw)) continue;
      const absolute = url.href;
      if (seen.has(absolute)) continue;
      seen.add(absolute);
      links.push({ url: absolute, label_raw: labelRaw || null });
    }
    return {
      ok: true,
      source_key: source.key,
      discovery_status: source.discoveryStatus,
      links,
      rejected_link_count: Math.max(0, documentLike.querySelectorAll('a[href]').length - links.length),
    };
  }
  const api = { SCHEMA_VERSION, captureCurrentPage, discoverEventLinks };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PublicOddsPage = api;
})(typeof window !== 'undefined' ? window : globalThis);
