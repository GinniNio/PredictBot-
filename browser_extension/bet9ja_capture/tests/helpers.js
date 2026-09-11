const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

function loadFixtureDocument(name) {
  const html = fs.readFileSync(path.join(__dirname, 'fixtures', name), 'utf-8');
  const dom = new JSDOM(html);
  return dom.window.document;
}

const BASE_CONTEXT = {
  sourceUrl: 'https://www.bet9ja.com/sport/prematch',
  pageTitle: 'Bet9ja - Prematch',
  capturedAtUtc: '2024-08-17T12:00:00.000Z',
};

module.exports = { loadFixtureDocument, BASE_CONTEXT };
