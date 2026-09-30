/**
 * Pure DOM-snapshot builder -- no chrome.* API usage, so it runs
 * identically in the popup/content-script context and in the Node test
 * suite (jsdom). Never parses fixtures, odds, or any sport-specific
 * structure: this stage exists ONLY to produce real, unparsed evidence
 * (see this directory's own README.md "Why a snapshot tool first"). The
 * actual per-sport fixture parser is built in a later stage, against the
 * real snapshots this produces -- never against guessed selectors, the
 * same discipline the existing browser_extension/bet9ja_capture/ tooling
 * already documents for itself.
 *
 * `buildSnapshot` takes a `documentLike` (the real `document` in the
 * extension, or a jsdom `document` in tests) plus caller-supplied context
 * (`href`, `title`, `capturedAtUtc`) and returns one plain object -- never
 * mutates its input, never touches storage/network itself.
 */

const SCHEMA_VERSION = "bet9ja-raw-page-snapshot.v1";

function buildSnapshot(documentLike, { href, title, capturedAtUtc }) {
  if (!documentLike || !documentLike.documentElement) {
    throw new Error("buildSnapshot requires a document with a documentElement.");
  }
  const html = documentLike.documentElement.outerHTML;
  return {
    schema_version: SCHEMA_VERSION,
    purpose:
      "Raw, unparsed evidence for building a separate all-sports/all-fixtures " +
      "Bet9ja capture tool. Never fixture data itself -- see this repo's " +
      "browser_extension/bet9ja_capture_allsports/README.md.",
    captured_at_utc: capturedAtUtc,
    source_url: href,
    page_title: title,
    html_length: html.length,
    html,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { buildSnapshot, SCHEMA_VERSION };
}
