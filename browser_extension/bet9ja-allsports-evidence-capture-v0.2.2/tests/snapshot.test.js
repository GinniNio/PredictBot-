const test = require("node:test");
const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");
const { buildSnapshot, SCHEMA_VERSION } = require("../snapshot.js");

test("buildSnapshot captures the real document's outerHTML verbatim", () => {
  const dom = new JSDOM("<!DOCTYPE html><html><body><div id='x'>hi</div></body></html>");
  const snapshot = buildSnapshot(dom.window.document, {
    href: "https://sports.bet9ja.com/Sport/Default/All",
    title: "Bet9ja",
    capturedAtUtc: "2026-09-30T00:00:00.000Z",
  });

  assert.equal(snapshot.schema_version, SCHEMA_VERSION);
  assert.equal(snapshot.source_url, "https://sports.bet9ja.com/Sport/Default/All");
  assert.equal(snapshot.page_title, "Bet9ja");
  assert.equal(snapshot.captured_at_utc, "2026-09-30T00:00:00.000Z");
  assert.match(snapshot.html, /<div id="x">hi<\/div>/);
  assert.equal(snapshot.html_length, snapshot.html.length);
});

test("buildSnapshot never parses or extracts fixture data -- it is a verbatim HTML string", () => {
  const dom = new JSDOM(
    "<!DOCTYPE html><html><body><div class='fixture'>Team A - Team B 1.50 3.20 2.10</div></body></html>"
  );
  const snapshot = buildSnapshot(dom.window.document, {
    href: "https://sports.bet9ja.com/Sport/Default/All",
    title: "Bet9ja",
    capturedAtUtc: "2026-09-30T00:00:00.000Z",
  });

  assert.equal(typeof snapshot.html, "string");
  assert.ok(!("fixtures" in snapshot));
  assert.ok(!("odds" in snapshot));
});

test("buildSnapshot rejects a document with no documentElement", () => {
  assert.throws(() => buildSnapshot({}, { href: "x", title: "x", capturedAtUtc: "x" }), /documentElement/);
});
