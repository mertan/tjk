import test from 'node:test';
import assert from 'node:assert/strict';
import { oddsAtCutoff } from '../scripts/collect-odds.mjs';
import { calibrationStats, fitTemperature, marketGate, marketProbabilities, valueBets } from '../model-v3/market.mjs';
import { buildExtras, EXTRA_NAMES } from '../model-v3/history-features.mjs';

const at = (hhmm) => Date.parse(`2026-03-14T${hhmm}:00+03:00`);

test('odds at cutoff never use quotes stamped after post-5min', () => {
  const history = { labels: ['2026-03-14 13:40:00', '2026-03-14 13:52:00', '2026-03-14 13:57:00'], datasets: [{ label: '1', data: ['5.00', '4.00', '2.00'] }, { label: '2', data: ['2.00', '-', '6.00'] }] };
  const result = oddsAtCutoff(history, at('14:00'), at('13:55'));
  assert.deepEqual(result.runners['1'], { odds: 4, at: at('13:52'), final: 2 });
  assert.equal(result.runners['2'].odds, 2, 'invalid point skipped, earlier valid quote kept');
  assert.equal(result.lastLabelAt, at('13:52'));
});

test('market gate PASes missing, stale and small-field races', () => {
  const quote = { cutoffMs: at('13:55'), lastLabelAt: at('13:52'), runners: { 1: { odds: 2, at: at('13:52') }, 2: { odds: 3, at: at('13:52') }, 3: { odds: 5, at: at('13:52') }, 4: { odds: 9, at: at('13:30') } } };
  assert.deepEqual(marketGate([1, 2, 3, 4], quote).reasons, ['QUOTE_STALE']);
  assert.deepEqual(marketGate([1, 2, 3, 5], quote).reasons, ['QUOTE_MISSING']);
  assert.deepEqual(marketGate([1, 2, 3], quote).reasons, ['FIELD_TOO_SMALL']);
  assert.ok(marketGate([1, 2, 3, 4], null).reasons.includes('ODDS_HISTORY_MISSING'));
  quote.runners[4].at = at('13:50');
  assert.equal(marketGate([1, 2, 3, 4], quote).ok, true);
  quote.lastLabelAt = at('13:40');
  assert.deepEqual(marketGate([1, 2, 3, 4], quote).reasons, ['SOURCE_STALE']);
});

test('market probabilities remove the overround', () => {
  const q = marketProbabilities([2, 2.5, 5]);
  assert.ok(Math.abs(q.reduce((a, b) => a + b, 0) - 1) < 1e-12);
  assert.ok(q[0] > q[1] && q[1] > q[2]);
});

test('value bets settle at the official dividend; T-5 quote return is reported separately', () => {
  const bets = valueBets({ p: [0.6, 0.45, 0.28], quoteOdds: [2.4, 2.5, 4], dividends: [2.1, 2.9, 6], positions: [2, 1, 3] });
  assert.deepEqual(bets['0.05'], { stake: 3, ret: 2.9, retAtQuote: 2.5, wins: 1, missingDividend: 0 });
  assert.equal(bets['0.20'].stake, 1, 'only 0.6*2.4=1.44 clears a 20% edge');
});

test('temperature calibration sharpens under-confident scores; ECE is low when calibrated', () => {
  const items = Array.from({ length: 400 }, (_, k) => ({ s: [0.5, 0, 0], win: k % 10 < 8 ? 0 : 1 + (k % 2) }));
  assert.ok(fitTemperature(items) > 2);
  const stats = calibrationStats(Array.from({ length: 100 }, (_, k) => ({ p: [0.5, 0.5], win: k % 2 })));
  assert.equal(stats.ece, 0);
  assert.ok(Math.abs(stats.logLoss - Math.log(2)) < 1e-4);
});

test('V3 history extras use only earlier days', () => {
  const runner = (n, position, finishTime) => ({ number: n, key: `H${n}`, scratched: false, position, finishTime });
  const race = (date, runners, prize1 = 100) => ({ date, venue: 'ADANA', number: 1, distance: 1400, surface: 'Kum', prize1, runners });
  const base = [race('2026-01-01', [runner(1, 1, 85), runner(2, 2, 86)]), race('2026-01-02', [runner(1, 1, 85), runner(2, 2, 90)])];
  const altered = [race('2026-01-01', [runner(1, 1, 85), runner(2, 2, 86)]), race('2026-01-02', [runner(1, 2, 99), runner(2, 1, 80)]), race('2026-01-03', [runner(1, 1, 80), runner(2, 2, 99)])];
  assert.deepEqual(buildExtras(altered).get('2026-01-02|ADANA|1'), buildExtras(base).get('2026-01-02|ADANA|1'));
  const day1 = buildExtras(base).get('2026-01-01|ADANA|1');
  assert.equal(day1[0][EXTRA_NAMES.indexOf('speedRelMissing')], 1, 'no prior runs => missing flag');
});
