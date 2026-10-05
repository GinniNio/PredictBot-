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
  // Keep only windows of the page around each occurrence of `pattern`.
  function excerpt(html, pattern, radius = 6000) {
    const parts = [];
    let from = 0;
    let lastEnd = -1;
    for (;;) {
      const i = html.indexOf(pattern, from);
      if (i < 0) break;
      const start = Math.max(0, i - radius, lastEnd);
      const end = Math.min(html.length, i + radius);
      if (end > start) parts.push(html.slice(start, end));
      lastEnd = end;
      from = i + pattern.length;
    }
    return parts.join('\n<!-- excerpt -->\n');
  }

  function captureCurrentPage(documentLike, context, mode) {
    if (!documentLike || !documentLike.documentElement) throw new Error('captureCurrentPage requires a document.');
    const page = pageContext(documentLike, context.href, context.title, context.capturedAtUtc);
    if (!page.ok) return { schema_version: SCHEMA_VERSION, capture_status: 'CAPTURE_FAILED', failure_reason: page.reason };
    const full = documentLike.documentElement.outerHTML;
    const useExcerpt = mode === 'excerpt' && page.source.excerptPattern && full.includes(page.source.excerptPattern);
    const html = useExcerpt ? excerpt(full, page.source.excerptPattern) : full;
    return {
      schema_version: SCHEMA_VERSION,
      capture_status: 'CAPTURE_OK',
      source_key: page.source_key,
      source_label: page.source_label,
      source_url: page.source_url,
      page_title: page.page_title,
      captured_at_utc: page.captured_at_utc,
      html_length: full.length,
      html_mode: useExcerpt ? 'EXCERPT_' + page.source.excerptPattern : 'FULL',
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
  // Event slugs carry their date (…-2026-10-05). Keep events from yesterday
  // to `daysAhead` days ahead (UTC); undated slugs are kept.
  function withinDays(url, todayIso, daysAhead) {
    const m = url.match(/(\d{4})-(\d{2})-(\d{2})(?:-\d+)?\/?$/);
    if (!m) return true;
    const d = Date.UTC(+m[1], +m[2] - 1, +m[3]);
    const t = Date.parse(todayIso.slice(0, 10) + 'T00:00:00Z');
    return d >= t - 86400000 && d <= t + daysAhead * 86400000;
  }

  function discoverListingLinks(documentLike, href) {
    const source = SOURCE_API.findSource(href);
    if (!source || !source.isListingUrl) return { ok: true, links: [] };
    const links = [];
    const seen = new Set();
    for (const anchor of Array.from(documentLike.querySelectorAll('a[href]'))) {
      let url;
      try { url = new URL(anchor.getAttribute('href'), href); } catch (_) { continue; }
      if (!SOURCE_API.hostMatches(source, url.hostname) || !source.isListingUrl(url)) continue;
      url.search = ''; url.hash = '';
      if (seen.has(url.href)) continue;
      seen.add(url.href);
      links.push({ url: url.href, label_raw: cleanText(anchor.textContent) || null });
    }
    return { ok: true, links };
  }

  const api = { SCHEMA_VERSION, captureCurrentPage, discoverEventLinks, discoverListingLinks, withinDays, excerpt };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PublicOddsPage = api;
})(typeof window !== 'undefined' ? window : globalThis);
