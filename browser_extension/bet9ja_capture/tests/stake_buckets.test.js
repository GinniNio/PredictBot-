const { test } = require('node:test');
const assert = require('node:assert/strict');
const { JSDOM } = require('jsdom');
const { parseStakeBuckets, parseFoldSize, nChooseK } = require('../stake_buckets.js');

function tableFromRows(rows) {
  const html = `<table class="mybets__systable"><tr><th>System Type</th><th>No.Bets</th><th>Unit Stake</th><th>Stake</th></tr>${rows
    .map((r) => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td></tr>`)
    .join('')}</table>`;
  return new JSDOM(html).window.document.querySelector('.mybets__systable');
}

test('nChooseK matches the canonical combinatorial identity', () => {
  assert.equal(nChooseK(5, 1), 5);
  assert.equal(nChooseK(5, 4), 5);
  assert.equal(nChooseK(4, 2), 6);
  assert.equal(nChooseK(3, 3), 1);
  assert.equal(nChooseK(3, 0), 1);
});

test('parseFoldSize recognizes the three named sizes and "N Folds"', () => {
  assert.equal(parseFoldSize('Singles'), 1);
  assert.equal(parseFoldSize('Doubles'), 2);
  assert.equal(parseFoldSize('Trebles'), 3);
  assert.equal(parseFoldSize('4 Folds'), 4);
  assert.equal(parseFoldSize('7 Fold'), 7);
  assert.equal(parseFoldSize('nonsense'), null);
});

test('a real 4-row real-shaped table (the settled ticket from this session) parses cleanly', () => {
  // The real settled-bets sample this session's own reconciliation used:
  // 5 legs, Singles/Doubles/Trebles/4 Folds all present.
  const el = tableFromRows([
    ['Singles', '5', '35.00', '175.00'],
    ['Doubles', '10', '3.00', '30.00'],
    ['Trebles', '10', '3.00', '30.00'],
    ['4 Folds', '5', '3.00', '15.00'],
  ]);
  const buckets = parseStakeBuckets(el, 5);
  assert.deepEqual(buckets, [
    { fold_size: 1, combination_count: 5, unit_stake: '35.00', total_stake: '175.00' },
    { fold_size: 2, combination_count: 10, unit_stake: '3.00', total_stake: '30.00' },
    { fold_size: 3, combination_count: 10, unit_stake: '3.00', total_stake: '30.00' },
    { fold_size: 4, combination_count: 5, unit_stake: '3.00', total_stake: '15.00' },
  ]);
});

test('a single-bucket full-accumulator table parses cleanly', () => {
  const el = tableFromRows([['Trebles', '1', '240.00', '240.00']]);
  assert.deepEqual(parseStakeBuckets(el, 3), [
    { fold_size: 3, combination_count: 1, unit_stake: '240.00', total_stake: '240.00' },
  ]);
});

test('a wrong combination_count for the declared fold size is refused entirely', () => {
  // 5 legs, "Singles" (fold_size 1) should be C(5,1)=5, not 3.
  const el = tableFromRows([['Singles', '3', '35.00', '105.00']]);
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('an internally inconsistent row (unit * count != total) is refused entirely', () => {
  const el = tableFromRows([['Singles', '5', '35.00', '999.00']]);
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('a duplicate fold size across two rows is refused entirely', () => {
  const el = tableFromRows([
    ['Singles', '5', '35.00', '175.00'],
    ['Singles', '5', '35.00', '175.00'],
  ]);
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('an unrecognized System Type label is refused entirely, never one bad row silently dropped', () => {
  const el = tableFromRows([
    ['Singles', '5', '35.00', '175.00'],
    ['Some New Bet Type', '10', '3.00', '30.00'],
  ]);
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('a row with the wrong number of cells is refused entirely', () => {
  const html =
    '<table class="mybets__systable"><tr><th>a</th></tr><tr><td>Singles</td><td>5</td><td>35.00</td></tr></table>';
  const el = new JSDOM(html).window.document.querySelector('.mybets__systable');
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('an empty table (header only) is refused entirely', () => {
  const el = tableFromRows([]);
  assert.equal(parseStakeBuckets(el, 5), null);
});

test('a null systemTableEl (a non-SYSTEM ticket) returns null, never throws', () => {
  assert.equal(parseStakeBuckets(null, 3), null);
});

test('a fold size larger than the ticket own leg count is refused entirely', () => {
  const el = tableFromRows([['5 Folds', '1', '10.00', '10.00']]);
  assert.equal(parseStakeBuckets(el, 3), null);
});

test('thousands-separated and whitespace-padded cell text is still accepted', () => {
  const el = tableFromRows([[' Singles ', ' 5 ', '1,035.00', '5,175.00']]);
  const buckets = parseStakeBuckets(el, 5);
  assert.deepEqual(buckets, [
    { fold_size: 1, combination_count: 5, unit_stake: '1035.00', total_stake: '5175.00' },
  ]);
});
