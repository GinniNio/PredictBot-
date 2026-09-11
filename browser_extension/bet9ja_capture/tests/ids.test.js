const { test } = require('node:test');
const assert = require('node:assert/strict');
const ids = require('../ids.js');

test('same natural key always produces the same stable id', () => {
  const a = ids.stableId('bxf', ['SOCCER', 'England', 'Premier League', 'arsenal', 'chelsea']);
  const b = ids.stableId('bxf', ['SOCCER', 'England', 'Premier League', 'arsenal', 'chelsea']);
  assert.equal(a, b);
  assert.match(a, /^bxf_[0-9a-f]{16}$/);
});

test('a different natural key produces a different id', () => {
  const a = ids.stableId('bxf', ['SOCCER', 'England', 'Premier League', 'arsenal', 'chelsea']);
  const b = ids.stableId('bxf', ['SOCCER', 'England', 'Premier League', 'arsenal', 'everton']);
  assert.notEqual(a, b);
});

test('canonicalKey length-prefixes parts so concatenation cannot collide', () => {
  const a = ids.canonicalKey(['ab', 'c']);
  const b = ids.canonicalKey(['a', 'bc']);
  assert.notEqual(a, b);
});

test('captureId is unique across calls even for the same timestamp', () => {
  const a = ids.captureId('2024-08-17T12:00:00.000Z');
  const b = ids.captureId('2024-08-17T12:00:00.000Z');
  assert.notEqual(a, b);
  assert.match(a, /^cap_\d+_[0-9a-f]{8}$/);
});
