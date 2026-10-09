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
    // OddsPortal: the app reads only the page's visible text and title, so
    // scripts, styles and icons (most of the page's size) are left out.
    const lean = !useExcerpt && page.source_key === 'oddsportal';
    const html = useExcerpt ? excerpt(full, page.source.excerptPattern)
      : lean ? full.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>|<svg[\s\S]*?<\/svg>/gi, '') : full;
    return {
      schema_version: SCHEMA_VERSION,
      capture_status: 'CAPTURE_OK',
      source_key: page.source_key,
      source_label: page.source_label,
      source_url: page.source_url,
      page_title: page.page_title,
      captured_at_utc: page.captured_at_utc,
      html_length: full.length,
      html_mode: useExcerpt ? 'EXCERPT_' + page.source.excerptPattern : lean ? 'NO_SCRIPTS_STYLES_SVG' : 'FULL',
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

  // ---------------------------------------------------------- walker helpers
  // One key per match page: OddsPortal adds market tabs to the hash
  // (#abc:home-away;2), which are the same match.
  function eventKey(urlString) {
    try { const u = new URL(urlString); return u.origin + u.pathname.replace(/\/+$/, ''); } catch (_) { return urlString; }
  }

  // The sport segment of an event or listing URL, where the source has one.
  function sportOf(urlString, sourceKey) {
    let u;
    try { u = new URL(urlString); } catch (_) { return null; }
    const parts = u.pathname.split('/').filter(Boolean);
    if (sourceKey === 'oddsportal' || sourceKey === 'betexplorer') return parts[0] || null;
    if (sourceKey === 'flashscore') return parts[0] === 'match' ? parts[1] || null : parts[0] || null;
    return null;
  }

  const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

  // Is the page ready to capture? READY (odds rendered), WAITING (keep
  // polling), STARTED (kickoff has passed: not a pre-match price).
  // nowMs and the page's kickoff are both in the browser's local time.
  function readiness(documentLike, sourceKey, nowMs) {
    const title = documentLike.title || '';
    const text = (documentLike.body && documentLike.body.textContent) || '';
    if (sourceKey === 'oddsportal') {
      if (title.includes('[[')) return { state: 'WAITING', detail: 'page template not rendered yet' };
      const k = text.match(/(\d{1,2}) ([A-Za-z]{3}) (\d{4}),\s*(\d{1,2}):(\d{2})/);
      if (k && MONTHS[k[2].toLowerCase()] !== undefined) {
        const kickoff = new Date(+k[3], MONTHS[k[2].toLowerCase()], +k[1], +k[4], +k[5]).getTime();
        if (kickoff <= nowMs) return { state: 'STARTED', detail: `kickoff ${k[0]} has passed` };
      }
      const head = text.search(/Bookmakers\s*1\s*(?:X\s*)?2\s*Payout/);
      if (head >= 0 && /\d+\.\d+\s*\d+\.\d+[\s\S]{0,40}?\d{2,3}(?:\.\d+)?%/.test(text.slice(head))) {
        return { state: 'READY', detail: 'bookmaker table rendered' };
      }
      return { state: 'WAITING', detail: 'bookmaker table not rendered yet' };
    }
    if (sourceKey === 'polymarket') {
      const html = documentLike.documentElement ? documentLike.documentElement.outerHTML : '';
      return html.includes('outcomePrices') ? { state: 'READY', detail: 'market data present' }
        : { state: 'WAITING', detail: 'market data not loaded yet' };
    }
    return { state: 'READY', detail: 'no readiness check for this source' };
  }

  // Words shared by many unrelated teams, or club-type prefixes.
  const NOISE = new Set(['fc', 'cf', 'sc', 'ac', 'afc', 'club', 'de', 'the', 'fk', 'sk', 'bk', 'cd', 'ca', 'sv', 'bc', 'cb',
    'kk', 'kc', 'hc', 'hk', 'vc', 'basket', 'baskets', 'basketball', 'volley', 'women', 'united', 'city', 'town', 'real',
    'sporting', 'athletic', 'atletico', 'saint', 'san', 'santa', 'inter', 'dynamo', 'dinamo', 'olympic', 'racing',
    'and', 'del', 'los', 'las', 'les', 'ss', 'us', 'as', 'cs', 'ud', 'cr', 'sd', 'ec', 'pr', 'sp']);

  function tokens(text) {
    return (text || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .split(/[^a-z]+/).filter((t) => t.length >= 3 && !NOISE.has(t));
  }

  // A Bet9ja name's identifying words: the surname for 'Surname, Given',
  // otherwise its distinctive words.
  function nameTokens(name) {
    return name.includes(',') ? tokens(name.split(',')[0]) : tokens(name);
  }

  // Does an event link look like one of the app's Bet9ja fixtures? Both
  // sides must share a word with the link's URL slug or label, in the same
  // sport. The app does the exact matching later; this only decides which
  // pages are worth visiting.
  function matchesTarget(link, targets, sourceKey) {
    const seen = new Set([...tokens(decodeURIComponent(new URL(link.url).pathname)), ...tokens(link.label_raw)]);
    const sport = sportOf(link.url, sourceKey);
    return targets.some((t) => (!sport || !t.sport || t.sport === sport)
      && nameTokens(t.home).some((w) => seen.has(w)) && nameTokens(t.away).some((w) => seen.has(w)));
  }

  // Results mode: a finished OddsPortal match shows 'Final result 5:4 OT
  // (0:2, 3:2, 1:0, 1:0)' under the teams. FINAL with that text, NOT_FINAL
  // when the header says the match was postponed, cancelled or similar,
  // otherwise WAITING (not rendered yet, or not finished). The app reads
  // the score; the walker only copies the text.
  const KICKOFF_TEXT = /(\d{1,2}) ([A-Za-z]{3}) (\d{4}),\s*(\d{1,2}):(\d{2})/;
  const NOT_PLAYED = /(?<![A-Za-z])(Postponed|Cancell?ed|Abandoned|Interrupted|Awarded|Walkover)(?![A-Za-z])/i;
  function resultState(documentLike) {
    const body = documentLike.body;
    const text = cleanText((body && (body.innerText || body.textContent)) || '');
    const kick = text.match(KICKOFF_TEXT);
    const kickoffRaw = kick ? kick[0] : null;
    const final = text.match(/Final\s+result\s*\d+\s*:\s*\d+[^()]{0,25}(?:\([^)]*\))?[^()\d]{0,12}/i);
    if (final) return { state: 'FINAL', final_result_raw: final[0].trim(), kickoff_raw: kickoffRaw };
    const header = kick ? text.slice(Math.max(0, kick.index - 200), kick.index + 200) : '';
    const stop = header.match(NOT_PLAYED);
    if (stop) return { state: 'NOT_FINAL', detail: stop[0], kickoff_raw: kickoffRaw };
    return { state: 'WAITING', detail: kick ? 'no final result on the page yet' : 'page not rendered yet',
      kickoff_raw: kickoffRaw };
  }

  // Search mode: OddsPortal's search page shows 'Next Matches (N)' once it
  // has answered. EMPTY when N is 0, READY when N > 0 and the match links have
  // rendered, otherwise WAITING (still loading: grey placeholders).
  function searchState(documentLike) {
    const text = cleanText((documentLike.body && (documentLike.body.innerText || documentLike.body.textContent)) || '');
    const m = text.match(/Next Matches\s*\((\d+)\)/i);
    const links = documentLike.querySelectorAll('a[href*="/h2h/"]').length;
    if (m && +m[1] === 0) return { state: 'EMPTY', count: 0 };
    if (m && links) return { state: 'READY', count: +m[1] };
    return { state: 'WAITING', count: m ? +m[1] : null };
  }

  const api = { SCHEMA_VERSION, searchState, captureCurrentPage, discoverEventLinks, discoverListingLinks, withinDays, excerpt,
    eventKey, sportOf, readiness, matchesTarget, nameTokens, resultState };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PublicOddsPage = api;
})(typeof window !== 'undefined' ? window : globalThis);
