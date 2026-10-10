import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { MARKET_LIMITS, marketGate } from '../model-v3/market.mjs';

const cutoff = Date.parse('2026-03-14T13:55:00+03:00');
const numbers = [1, 2, 3, 4];
const freshQuote = () => ({
  cutoffMs: cutoff,
  postMs: cutoff + 5 * 60_000,
  lastLabelAt: cutoff - 60_000,
  runners: Object.fromEntries(numbers.map((number) => [number, { odds: number + 1, at: cutoff - 60_000 }]))
});

test('V3 market limits cannot be absent, invalid, or loosen baseline safety gates', () => {
  for (const limits of [
    null, [], {}, { minField: 4 }, { maxQuoteAgeMs: 720_000 },
    { ...MARKET_LIMITS, minField: NaN }, { ...MARKET_LIMITS, minField: 4.5 },
    { ...MARKET_LIMITS, minField: '4' }, { ...MARKET_LIMITS, minField: 3 },
    { ...MARKET_LIMITS, minField: 31 }, { ...MARKET_LIMITS, maxQuoteAgeMs: 0 },
    { ...MARKET_LIMITS, maxQuoteAgeMs: -1 }, { ...MARKET_LIMITS, maxQuoteAgeMs: Infinity },
    { ...MARKET_LIMITS, maxQuoteAgeMs: 720_001 }, { ...MARKET_LIMITS, maxQuoteAgeMs: '720000' },
    { date: '2026-03-14', number: 1, distance: 1400, surface: 'Kum' }
  ]) {
    assert.deepEqual(marketGate(numbers, freshQuote(), limits), { ok: false, reasons: ['INVALID_MARKET_LIMITS'] });
  }
  assert.equal(marketGate(numbers, freshQuote()).ok, true);
  assert.deepEqual(marketGate(numbers.slice(0, 3), freshQuote()).reasons, ['FIELD_TOO_SMALL']);
  assert.deepEqual(marketGate(numbers, freshQuote(), { ...MARKET_LIMITS, minField: 5 }).reasons, ['FIELD_TOO_SMALL']);
  assert.equal(marketGate(numbers, freshQuote(), { ...MARKET_LIMITS, maxQuoteAgeMs: 60_000 }).ok, true);
  assert.ok(marketGate(numbers, freshQuote(), { ...MARKET_LIMITS, maxQuoteAgeMs: 59_999 }).reasons.includes('QUOTE_STALE'));
});

test('V3 requires a finite integer cutoff and a cutoff no later than post time', () => {
  for (const value of [undefined, null, NaN, Infinity, -Infinity, '123', 0, -1, cutoff + 0.5, 8.64e15 + 1]) {
    assert.ok(marketGate(numbers, { ...freshQuote(), cutoffMs: value }).reasons.includes('INVALID_CUTOFF'));
  }
  assert.ok(marketGate(numbers, { ...freshQuote(), postMs: cutoff - 1 }).reasons.includes('CUTOFF_AFTER_POST'));
  assert.ok(marketGate(numbers, { ...freshQuote(), postMs: null }).reasons.includes('INVALID_POST_TIME'));
  const withoutPost = freshQuote();
  delete withoutPost.postMs;
  assert.equal(marketGate(numbers, withoutPost).ok, true, 'legacy cutoff snapshots need not contain postMs');
});

test('V3 rejects future, stale and inconsistent source or runner timestamps', () => {
  assert.ok(marketGate(numbers, { ...freshQuote(), lastLabelAt: cutoff + 1 }).reasons.includes('SOURCE_TIMESTAMP_FUTURE'));
  for (const value of [null, NaN, Infinity, '2026-03-14', cutoff - MARKET_LIMITS.maxQuoteAgeMs - 1]) {
    assert.ok(marketGate(numbers, { ...freshQuote(), lastLabelAt: value }).reasons.includes('SOURCE_STALE'));
  }
  for (const value of [null, NaN, Infinity, '123', cutoff + 1, cutoff - MARKET_LIMITS.maxQuoteAgeMs - 1]) {
    const quote = freshQuote();
    quote.runners[1].at = value;
    assert.ok(marketGate(numbers, quote).reasons.includes('QUOTE_STALE'));
  }
  const inconsistent = freshQuote();
  inconsistent.runners[1].at += 1;
  assert.ok(marketGate(numbers, inconsistent).reasons.includes('SOURCE_QUOTE_TIMESTAMP_MISMATCH'));
  const boundary = freshQuote();
  boundary.lastLabelAt = cutoff - MARKET_LIMITS.maxQuoteAgeMs;
  for (const runner of Object.values(boundary.runners)) runner.at = boundary.lastLabelAt;
  assert.equal(marketGate(numbers, boundary).ok, true, 'exact twelve-minute age remains allowed');
  boundary.lastLabelAt -= 1;
  for (const runner of Object.values(boundary.runners)) runner.at = boundary.lastLabelAt;
  assert.deepEqual(marketGate(numbers, boundary).reasons, ['SOURCE_STALE', 'QUOTE_STALE']);
});

test('V3 requires distinct, valid active runner numbers and actual quote objects', () => {
  for (const value of [null, {}, '1234']) {
    assert.deepEqual(marketGate(value, freshQuote()).reasons, ['INVALID_RUNNER_SET']);
  }
  for (const value of [0, -1, 31, 1.5, NaN, Infinity, '1']) {
    assert.ok(marketGate([value, 2, 3, 4], freshQuote()).reasons.includes('INVALID_RUNNER_NUMBER'));
  }
  assert.deepEqual(marketGate([1, 2, 3, 3], freshQuote()).reasons, ['DUPLICATE_RUNNER_NUMBER']);
  assert.deepEqual(marketGate(numbers, null).reasons, ['ODDS_HISTORY_MISSING']);
  for (const quote of [[], true, 'history']) {
    assert.deepEqual(marketGate(numbers, quote).reasons, ['INVALID_ODDS_HISTORY']);
  }
  const quote = freshQuote();
  delete quote.runners[4];
  assert.deepEqual(marketGate(numbers, quote).reasons, ['QUOTE_MISSING']);
  quote.runners = Object.create(freshQuote().runners);
  assert.deepEqual(marketGate(numbers, quote).reasons, ['QUOTE_MISSING'], 'inherited quote properties are not evidence');
});

test('V3 rejects zero, negative, placeholder and non-numeric quotes', () => {
  for (const odds of [0, -2, 1, 900, 999, NaN, Infinity, '2.5', true, {}, []]) {
    const quote = freshQuote();
    quote.runners[1].odds = odds;
    assert.deepEqual(marketGate(numbers, quote).reasons, ['QUOTE_INVALID']);
  }
  for (const odds of [null, undefined]) {
    const quote = freshQuote();
    quote.runners[1].odds = odds;
    assert.deepEqual(marketGate(numbers, quote).reasons, ['QUOTE_MISSING']);
  }
  for (const odds of [1.01, 241.7, 899.99]) {
    const quote = freshQuote();
    quote.runners[1].odds = odds;
    assert.equal(marketGate(numbers, quote).ok, true, '241.7 alone does not establish corrupted source odds');
  }
});

test('walk-forward evaluation retains default freshness limits instead of passing race metadata as limits', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tjk-v3-market-gates-'));
  try {
    const races = ['2026-01-14', '2026-02-14', '2026-03-14'].map((date) => ({
      date, venue: 'ANKARA', number: 1, time: '14:00', distance: 1400, surface: 'Kum', prize1: 100,
      runners: numbers.map((number) => ({
        number, key: 'H' + number, name: 'AT ' + number, scratched: false,
        rating: 80 - number, weight: 55 + number, extraWeight: 0, stall: number, age: 4,
        last6: 'K1K2K3', daysOff: 15, bestTime: 85, jockey: 'J' + number, trainer: 'T' + number,
        position: number, finishTime: 85 + number, agf: 25, closingOdds: number + 1
      }))
    }));
    const quotes = {};
    for (const race of races) {
      const at = Date.parse(race.date + 'T13:55:00+03:00');
      quotes[race.date + '|ANKARA|1'] = {
        cutoffMs: at, postMs: at + 5 * 60_000, lastLabelAt: at - 13 * 60_000,
        runners: Object.fromEntries(numbers.map((number) => [number, { odds: number + 1, at: at - 13 * 60_000 }]))
      };
    }
    const data = join(directory, 'races.json');
    const odds = join(directory, 'odds.json');
    await writeFile(data, JSON.stringify({ races }));
    await writeFile(odds, JSON.stringify({ races: quotes }));
    const { stdout } = await promisify(execFile)(process.execPath, [
      fileURLToPath(new URL('../scripts/evaluate-v3.mjs', import.meta.url)),
      '--data', data, '--odds', odds, '--cache', directory, '--months', '2026-03'
    ], { cwd: directory, timeout: 30_000, maxBuffer: 1024 * 1024 });
    const report = JSON.parse(stdout);
    assert.equal(report.overall.races, 1);
    assert.equal(report.overall.v3.coverage.predicted, 0);
    assert.equal(report.overall.calibration.races, 0);
    assert.equal(report.marketPASReasons.SOURCE_STALE, 1);
    assert.equal(report.marketPASReasons.QUOTE_STALE, 1);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
