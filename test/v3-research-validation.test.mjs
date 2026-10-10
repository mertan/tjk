import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { oddsAtCutoff, istanbulMs } from '../scripts/collect-odds.mjs';
import { calibrationStats, fitTemperature, marketGate, marketProbabilities, valueBets } from '../model-v3/market.mjs';
import { bootstrapRoiCI, summarizeWagers } from '../model-v3/evaluation.mjs';

const at = (time) => Date.parse('2026-03-14T' + time + ':00+03:00');
const history = () => ({
  labels: ['2026-03-14 13:40:00', '2026-03-14 13:52:00', '2026-03-14 13:57:00'],
  datasets: [1, 2, 3, 4].map((number) => ({ label: String(number), data: [number + 3, number + 2, number + 1] }))
});
const snapshot = (source) => oddsAtCutoff(source, at('14:00'), at('13:55'));

test('odds collector rejects reordered and duplicate timestamps instead of inventing an opening or cutoff quote', () => {
  const unordered = history();
  [unordered.labels[0], unordered.labels[1]] = [unordered.labels[1], unordered.labels[0]];
  const duplicate = history();
  duplicate.labels[1] = duplicate.labels[0];
  for (const source of [unordered, duplicate]) {
    const quote = snapshot(source);
    assert.equal(quote.historyValid, false);
    assert.deepEqual(quote.runners, {});
    assert.deepEqual(quote.reasonCodes, ['NON_MONOTONIC_HISTORY']);
  }
  const source = history();
  source.datasets[0].data = ['241,7', '-', '3.00'];
  const quote = snapshot(source);
  assert.equal(quote.historyValid, true);
  assert.equal(quote.runners[1].odds, 241.7);
  assert.equal(quote.runners[1].at, at('13:40'));
  assert.equal(quote.runners[1].final, 3, 'post-cutoff quote only appears in the separate final field');
  assert.ok(marketGate([1, 2, 3, 4], { cutoffMs: at('13:55'), ...quote }).reasons.includes('QUOTE_STALE'));
});

test('odds collector rejects duplicate identities, mismatched lengths and malformed values', () => {
  const cases = [
    [null, 'INVALID_HISTORY_SHAPE'],
    [{ labels: [], datasets: [] }, 'INVALID_HISTORY_SHAPE'],
    [{ ...history(), labels: [null, ...history().labels.slice(1)] }, 'INVALID_HISTORY_TIMESTAMP']
  ];
  const duplicate = history();
  duplicate.datasets.push({ label: '01', data: [4, 3, 2] });
  cases.push([duplicate, 'DUPLICATE_HISTORY_RUNNER']);
  for (const label of ['horse one', 0, 31, true]) {
    const source = history();
    source.datasets[0].label = label;
    cases.push([source, 'INVALID_HISTORY_RUNNER']);
  }
  const shortened = history();
  shortened.datasets[0].data.pop();
  cases.push([shortened, 'HISTORY_LENGTH_MISMATCH']);
  for (const value of [false, {}, [], 'bad', 'Infinity', -2, 900, 1]) {
    const source = history();
    source.datasets[0].data[1] = value;
    cases.push([source, 'INVALID_HISTORY_ODDS']);
  }
  for (const [source, reason] of cases) {
    const quote = snapshot(source);
    assert.equal(quote.historyValid, false);
    assert.deepEqual(quote.reasonCodes, [reason]);
    assert.deepEqual(quote.runners, {});
  }
  const quote = snapshot(history());
  quote.historyValid = false;
  assert.ok(marketGate([1, 2, 3, 4], { cutoffMs: at('13:55'), ...quote }).reasons.includes('QUOTE_HISTORY_INVALID'));
});

test('history timestamps validate calendar dates and cutoffs without rejecting later archive observations', () => {
  for (const label of [
    '2026-02-30 13:00:00', '2026-02-30T13:00:00+03:00',
    '2026-03-14 24:00:00', '2026-13-14 13:00:00', '2026-03-14 13:60:00',
    '2026-03-14 13:00:60', 123, null
  ]) assert.equal(istanbulMs(label), null);
  assert.equal(istanbulMs('2026-03-14T10:00:00Z'), at('13:00'));
  assert.equal(istanbulMs('2026-03-14 13:00:00'), at('13:00'));
  for (const [post, cutoff] of [[NaN, at('13:55')], [at('14:00'), Infinity], [at('13:50'), at('13:55')]]) {
    assert.deepEqual(oddsAtCutoff(history(), post, cutoff).reasonCodes, ['INVALID_HISTORY_CUTOFF']);
  }
  const source = history();
  source.labels.push('2026-03-14 14:05:00');
  source.datasets.forEach((set) => set.data.push(50));
  const quote = snapshot(source);
  assert.equal(quote.historyValid, true);
  assert.equal(quote.runners[1].odds, 3);
  assert.equal(quote.runners[1].final, 2);
});

test('calibration helpers return unavailable for empty or malformed evidence', () => {
  for (const items of [null, [], new Array(1), [{}], [{ s: new Array(2), win: 0 }], [{ s: [], win: 0 }], [{ s: [0, 1], win: -1 }],
    [{ s: [0, 1], win: 2 }], [{ s: [0, 1], win: 0.5 }], [{ s: [0, NaN], win: 0 }],
    [{ s: [0, Infinity], win: 0 }], [{ s: [0, '1'], win: 0 }]]) {
    assert.equal(fitTemperature(items), null);
  }
  assert.ok(Number.isFinite(fitTemperature([{ s: [1e308, -1e308], win: 0 }])));
  for (const items of [null, [], new Array(1), [{}], [{ p: Object.assign(new Array(2), { 0: 1 }), win: 0 }], [{ p: [0.2, 0.3], win: 0 }],
    [{ p: [0.5, 0.5], win: 2 }], [{ p: [0.5, 0.5], win: -1 }],
    [{ p: [0.5, 0.5], win: 0.5 }], [{ p: [-0.5, 1.5], win: 0 }],
    [{ p: [0.5, NaN], win: 0 }], [{ p: ['0.5', 0.5], win: 0 }]]) {
    assert.equal(calibrationStats(items), null);
  }
  for (const bins of [0, -1, 1.5, Infinity, 101]) {
    assert.equal(calibrationStats([{ p: [0.5, 0.5], win: 0 }], bins), null);
  }
  assert.equal(calibrationStats([{ p: [0.5, 0.5], win: 0 }]).logLoss, 0.6931);
  for (const odds of [[], [2], Object.assign(new Array(2), { 0: 2 }), [0, 2], [2, NaN], [2, -1], [2, '3']]) {
    assert.equal(marketProbabilities(odds), null);
  }
});

test('ROI bootstrap uses aggregate return divided by aggregate stake for each race-cluster sample', () => {
  const records = Array.from({ length: 30 }, (_, index) =>
    index % 2 ? { stake: 9, ret: 0, wins: 0 } : { stake: 1, ret: 2, wins: 1 });
  const ci = bootstrapRoiCI(records);
  assert.ok(ci[0] > -1 && ci[1] < -0.3, 'a mean of per-race ratios would instead center on zero');
  const report = summarizeWagers(records);
  assert.equal(report.roiDividend, -80);
  assert.deepEqual(report.roiDividendCI95, ci.map((value) => Math.round(value * 1000) / 10));
  assert.deepEqual(bootstrapRoiCI(records), ci, 'fixed seed remains reproducible');
  const withZeros = [...records, ...Array.from({ length: 20 }, () => ({ stake: 0, ret: 0, wins: 0 }))];
  assert.equal(summarizeWagers(withZeros).roiDividend, -80);
  assert.ok(bootstrapRoiCI(withZeros)[1] < -0.3);
  assert.equal(bootstrapRoiCI(records.slice(0, 29)), null);
  assert.equal(bootstrapRoiCI([{ stake: 1, ret: NaN }]), null);
  assert.equal(bootstrapRoiCI(new Array(30)), null);
});

test('missing winning dividends suppress official ROI and CI, while retaining labelled quote counterfactual', () => {
  for (const dividend of [null, undefined, -1, Infinity]) {
    const wagers = valueBets({ p: [0.8, 0.2], quoteOdds: [2, 3], dividends: [dividend, 3], positions: [1, 2] });
    const records = Array.from({ length: 30 }, () => wagers['0.05']);
    const report = summarizeWagers(records);
    assert.equal(report.status, 'DIVIDEND_MISSING');
    assert.equal(report.missingDividend, 30);
    assert.equal(report.roiDividend, null);
    assert.equal(report.roiDividendCI95, null);
    assert.equal(report.roiDividendHaircut5, null);
    assert.equal(report.roiAtT5QuoteOptimistic, 100);
  }
});

const fixtureRaces = () => ['2026-01-14', '2026-02-14', '2026-03-14'].map((date) => ({
  date, venue: 'ANKARA', number: 1, time: '14:00', distance: 1400, surface: 'Kum', prize1: 100,
  runners: [1, 2, 3, 4].map((number) => ({
    number, key: 'H' + number, name: 'AT ' + number, scratched: false,
    rating: 80 - number, weight: 55 + number, extraWeight: 0, stall: number, age: 4,
    last6: 'K1K2K3', daysOff: 15, bestTime: 85, jockey: 'J' + number, trainer: 'T' + number,
    position: number, finishTime: 85 + number, agf: 25, closingOdds: number + 1
  }))
}));

async function evaluateFixture({ missingQuoteDate = null, omitDate = null, missingDividend = false,
  testCutoffShiftMs = 0, testPostShiftMs = 0 } = {}) {
  const directory = await mkdtemp(join(tmpdir(), 'tjk-v3-evaluation-'));
  try {
    const races = fixtureRaces().filter((race) => race.date !== omitDate);
    if (missingDividend) races.find((race) => race.date === '2026-03-14').runners[0].closingOdds = null;
    const quotes = {};
    for (const race of races) {
      const time = Date.parse(race.date + 'T13:55:00+03:00');
      quotes[race.date + '|ANKARA|1'] = race.date === missingQuoteDate ? null : {
        cutoffMs: time + (race.date === '2026-03-14' ? testCutoffShiftMs : 0),
        postMs: time + 5 * 60_000 + (race.date === '2026-03-14' ? testPostShiftMs : 0), lastLabelAt: time - 60_000,
        runners: Object.fromEntries([1, 2, 3, 4].map((number) => [number, { odds: number + 1, at: time - 60_000 }]))
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
    return JSON.parse(stdout);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test('evaluation never substitutes an untrained model or temperature one for empty V3 windows', async () => {
  for (const [missingQuoteDate, reason] of [
    ['2026-01-14', 'V3_TRAINING_UNAVAILABLE'], ['2026-02-14', 'V3_CALIBRATION_UNAVAILABLE']
  ]) {
    const report = await evaluateFixture({ missingQuoteDate });
    assert.equal(report.overall.v3.coverage.predicted, 0);
    assert.equal(report.overall.v2.coverage.predicted, 1);
    assert.equal(report.overall.fav.coverage.predicted, 1);
    assert.equal(report.folds[0].tempV3, null);
    assert.equal(report.folds[0].v3Status, 'PAS');
    assert.ok(report.folds[0].v3Reasons.includes(reason));
    assert.equal(report.v3PASReasons[reason], 1);
    assert.equal(report.overall.roi['edge>=0.05'].bets, 0);
  }
  for (const omitDate of ['2026-01-14', '2026-02-14']) {
    const report = await evaluateFixture({ omitDate });
    assert.equal(report.overall.races, 1, 'the unavailable fold remains visible');
    assert.equal(report.overall.v2.coverage.predicted, 0);
    assert.equal(report.overall.v3.coverage.predicted, 0);
    assert.equal(report.folds[0].tempV2, null);
    assert.equal(report.folds[0].tempV3, null);
  }
  const valid = await evaluateFixture();
  assert.equal(valid.overall.v3.coverage.predicted, 1);
  assert.ok(Number.isFinite(valid.folds[0].tempV3));
});

test('evaluation reports missing favourite dividend instead of booking the win as a loss', async () => {
  const report = await evaluateFixture({ missingDividend: true });
  assert.equal(report.overall.roi.marketFavouriteFlat.status, 'DIVIDEND_MISSING');
  assert.equal(report.overall.roi.marketFavouriteFlat.missingDividend, 1);
  assert.equal(report.overall.roi.marketFavouriteFlat.roiDividend, null);
});

test('evaluation binds every quote cutoff and post time to its official race and labels archive provenance unverified', async () => {
  for (const [options, reason] of [
    [{ testCutoffShiftMs: -5 * 60_000 }, 'CUTOFF_RACE_MISMATCH'],
    [{ testCutoffShiftMs: 60_000 }, 'CUTOFF_RACE_MISMATCH'],
    [{ testPostShiftMs: 60_000 }, 'POST_RACE_MISMATCH']
  ]) {
    const report = await evaluateFixture(options);
    assert.equal(report.overall.v3.coverage.predicted, 0);
    assert.equal(report.overall.fav.coverage.predicted, 0);
    assert.equal(report.marketPASReasons[reason], 1);
  }
  const report = await evaluateFixture();
  assert.equal(report.backtestVerified, false);
  assert.equal(report.reportKind, 'UNVERIFIED_ARCHIVAL_RESEARCH');
  assert.equal(report.method.inputProvenance, 'archival_unverified');
  assert.ok(report.verification.reasonCodes.includes('PRE_CUTOFF_INPUT_SNAPSHOT_UNVERIFIED'));
});
