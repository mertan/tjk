import test from 'node:test';
import { auditLiveV3, runAudit } from '../scripts/check-v3-live.mjs';
import assert from 'node:assert/strict';
import { buildExamples, FEATURE_NAMES } from '../model-v2/features.mjs';
import { buildExtras, EXTRA_NAMES } from '../model-v3/history-features.mjs';
import { marketProbabilities } from '../model-v3/market.mjs';
import { scores, softmax } from '../model-v2/model.mjs';
import { buildLiveV3Features } from '../model-v3/features.mjs';
import {
  ARTIFACT_SCHEMA_V3, FEATURE_NAMES_V3, historyDigest, predictLiveV3, validateArtifact
} from '../model-v3/live-inference.mjs';

// Synthetic fixtures establish transformations and gates, not trained-model quality.
const at = (time) => Date.parse('2026-10-10T' + time + ':00+03:00');
const runner = (number, position = null) => ({
  number, name: 'SYNTHETIC ' + number, key: 'SIRE' + number + '|DAM' + number + '|2022',
  age: 4, weight: 54 + number, extraWeight: number % 2, stall: number,
  jockey: 'JOCKEY ' + number, trainer: 'TRAINER ' + number,
  rating: 40 + number * 2, last6: ['K1Ç2K5', 'Ç1K5K3', 'K3K8Ç2', 'K2K1Ç4', 'K4Ç5K3'][number - 1],
  daysOff: 8 + number, bestTime: 81 + number, agf: number,
  scratched: false, position, finishTime: position ? 80 + position : null, closingOdds: null
});
const past = (date, distance, prize1) => ({
  date, venue: 'ANKARA', number: 1, time: '14:00', surface: 'Kum', distance, prize1,
  runners: [1, 2, 3, 4, 5].map((number) => runner(number, number))
});
function fixture() {
  const history = [past('2026-10-07', 1200, 100_000), past('2026-10-08', 1400, 120_000)];
  const race = {
    date: '2026-10-10', venue: 'ANKARA', number: 1, time: '14:00',
    distance: 1400, surface: 'Kum', prize1: 140_000,
    runners: [1, 2, 3, 4, 5].map((number) => ({ ...runner(number), scratched: number === 5 }))
  };
  const quote = {
    cutoffMs: at('13:55'), lastLabelAt: at('13:54'),
    runners: Object.fromEntries([1, 2, 3, 4, 5].map((number) => [number, { odds: 2 + number, at: at('13:54') }]))
  };
  const artifact = {
    schemaVersion: ARTIFACT_SCHEMA_V3, featureNames: [...FEATURE_NAMES_V3],
    weights: FEATURE_NAMES_V3.map((_, i) => (i - 16) / 100),
    formOrder: { newestLast: false },
    training: { from: '2026-10-07', through: '2026-10-07', races: 1 },
    calibration: { from: '2026-10-08', through: '2026-10-08', races: 30, temperature: 1.7, scoreTransform: 'multiply' },
    historySha256: historyDigest(history), quoteCutoffMinutes: 5
  };
  return { artifact, history, race, quote, now: at('13:56') };
}

test('live V3 has the exact 33 research columns and synthetic training/live parity', () => {
  const input = fixture();
  assert.deepEqual(FEATURE_NAMES_V3, [...FEATURE_NAMES, ...EXTRA_NAMES, 'logMarketProbT5']);
  assert.equal(FEATURE_NAMES_V3.length, 33);
  const dataset = [...input.history, input.race];
  const example = buildExamples(dataset, { newestLast: false }).find((item) => item.id === '2026-10-10|ANKARA|1');
  const extras = buildExtras(dataset).get(example.id);
  const q = marketProbabilities(example.numbers.map((number) => input.quote.runners[number].odds));
  const expected = example.X.map((row, i) => [...row, ...extras[i], Math.log(q[i])]);
  const actual = buildLiveV3Features({ ...input, newestLast: false });
  assert.deepEqual(actual.X, expected);
  assert.deepEqual(actual.numbers, [1, 2, 3, 4]);
  const result = predictLiveV3(input);
  assert.equal(result.status, 'OK');
  assert.deepEqual(result.runners.map((item) => item.rank), [1, 2, 3, 4]);
  const probabilities = softmax(scores(input.artifact.weights, expected).map((s) => s * 1.7));
  for (const item of result.runners) {
    assert.ok(Math.abs(item.probability - probabilities[example.numbers.indexOf(item.number)]) < 1e-12);
  }
  assert.ok(Math.abs(result.runners.reduce((sum, item) => sum + item.probability, 0) - 1) < 1e-12);
});

test('scratched quotes and AGF never change live V3 active predictions', () => {
  const base = fixture();
  const expected = predictLiveV3(base).runners;
  base.quote.runners[5].odds = 1000;
  base.race.runners.forEach((item) => { item.agf = 100 - item.number * 10; });
  assert.deepEqual(predictLiveV3(base).runners, expected);
  assert.ok(expected.every((item) => item.number !== 5));
});

test('missing artifact and legacy weights remain PAS with no predictions', () => {
  for (const artifact of [null, {}, { weights: Array(33).fill(0) }]) {
    const result = predictLiveV3({ ...fixture(), artifact });
    assert.equal(result.status, 'PAS');
    assert.deepEqual(result.runners, []);
    assert.ok(result.reasonCodes.length);
  }
});

test('manifest rejects malformed weights, calibration, feature order and unfrozen token order', () => {
  const cases = [
    [(a) => a.weights.pop(), 'V3_WEIGHTS_INVALID'],
    [(a) => { a.weights[0] = NaN; }, 'V3_WEIGHTS_INVALID'],
    [(a) => { a.weights[0] = Infinity; }, 'V3_WEIGHTS_INVALID'],
    [(a) => { a.weights[0] = '0.1'; }, 'V3_WEIGHTS_INVALID'],
    [(a) => { [a.featureNames[0], a.featureNames[1]] = [a.featureNames[1], a.featureNames[0]]; }, 'V3_FEATURE_SCHEMA_MISMATCH'],
    [(a) => { a.calibration.temperature = 0; }, 'V3_CALIBRATION_INVALID'],
    [(a) => { a.calibration.temperature = NaN; }, 'V3_CALIBRATION_INVALID'],
    [(a) => { a.calibration.scoreTransform = 'divide'; }, 'V3_CALIBRATION_INVALID'],
    [(a) => { a.calibration.races = 0; }, 'V3_CALIBRATION_INVALID'],
    [(a) => { a.training.through = a.calibration.from; }, 'V3_CALIBRATION_INVALID'],
    [(a) => { a.formOrder = {}; }, 'V3_FORM_ORDER_MISSING']
  ];
  for (const [change, reason] of cases) {
    const input = fixture();
    change(input.artifact);
    assert.equal(validateArtifact(input.artifact).ok, false);
    const result = predictLiveV3(input);
    assert.ok(result.reasonCodes.includes(reason), reason);
    assert.deepEqual(result.runners, []);
  }
});

test('history identity, digest and date gates reject fabricated or future context', () => {
  const input = fixture();
  input.history[0].runners[0].finishTime += 1;
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_HISTORY_DIGEST_MISMATCH'));
  input.history[0].date = input.race.date;
  input.artifact.historySha256 = historyDigest(input.history);
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_HISTORY_LOOKAHEAD'));
  input.history[0].date = '2026-10-11';
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_HISTORY_LOOKAHEAD'));
  input.history[0].runners[0].key = '||?';
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_HISTORY_INVALID'));
  assert.ok(predictLiveV3({ ...fixture(), history: [] }).reasonCodes.includes('V3_HISTORY_MISSING'));
});

test('upstream freshness and source mismatches always close V3 to PAS', () => {
  const result = predictLiveV3({ ...fixture(), reasonCodes: ['SOURCE_FRESHNESS_UNVERIFIED', 'SOURCE_MISMATCH'] });
  assert.equal(result.status, 'PAS');
  assert.deepEqual(result.runners, []);
  assert.deepEqual(result.reasonCodes, ['SOURCE_FRESHNESS_UNVERIFIED', 'SOURCE_MISMATCH']);
});

test('only the scheduled T-5 snapshot before the start can produce V3 predictions', () => {
  const input = fixture();
  assert.ok(predictLiveV3({ ...input, now: at('13:54') }).reasonCodes.includes('V3_CUTOFF_NOT_REACHED'));
  assert.ok(predictLiveV3({ ...input, now: at('14:00') }).reasonCodes.includes('V3_RACE_STARTED'));
  input.quote.cutoffMs += 60_000;
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_QUOTE_CUTOFF_MISMATCH'));
});

test('missing, invalid, stale, future, wrong-day and extra-runner quotes remain PAS', () => {
  const changes = [
    (q) => { delete q.runners[1]; },
    (q) => { q.runners[1].odds = 0; },
    (q) => { q.runners[1].odds = -3; },
    (q) => { q.runners[1].odds = Infinity; },
    (q) => { q.runners[1].at = at('13:56'); },
    (q) => { q.runners[1].at = at('13:40'); },
    (q) => { q.runners[1].at -= 86_400_000; },
    (q) => { q.lastLabelAt = at('13:56'); },
    (q) => { q.runners[6] = { odds: 10, at: at('13:54') }; }
  ];
  for (const change of changes) {
    const input = fixture();
    change(input.quote);
    const result = predictLiveV3(input);
    assert.equal(result.status, 'PAS');
    assert.deepEqual(result.runners, []);
  }
});

test('scratch changes recompute active-field features; small fields are PAS', () => {
  const input = fixture();
  input.race.runners[3].scratched = true;
  const result = predictLiveV3(input);
  assert.equal(result.status, 'PAS');
  assert.ok(result.reasonCodes.includes('FIELD_TOO_SMALL'));
});

test('manifest windows cannot reach the target day', () => {
  const input = fixture();
  input.artifact.calibration.through = input.race.date;
  assert.ok(predictLiveV3(input).reasonCodes.includes('V3_ARTIFACT_LOOKAHEAD'));
});

test('missing class/prize is an explicit trained missing feature, not fabricated data', () => {
  const input = fixture();
  input.race.prize1 = null;
  assert.equal(predictLiveV3(input).status, 'OK');
});

test('installation audit reports integrity separately from unverified training and backtests', () => {
  const input = fixture();
  const result = auditLiveV3(input.artifact, input.history);
  assert.equal(result.status, 'VALID_MANIFEST');
  assert.equal(result.weightsCount, 33);
  assert.equal(result.weightsFinite, true);
  assert.equal(result.featureOrderMatches, true);
  assert.equal(result.history.digestMatches, true);
  assert.equal(result.trainingReproduced, false);
  assert.equal(result.backtestVerified, false);
  assert.equal(Object.hasOwn(result, 'weights'), false);
  input.artifact.historySha256 = 'a'.repeat(64);
  assert.equal(auditLiveV3(input.artifact, input.history).status, 'PAS');
});

test('installation audit handles missing files and invalid CLI options without throwing', async () => {
  const invalid = await runAudit(['--unknown']);
  assert.equal(invalid.status, 'PAS');
  assert.ok(invalid.reasonCodes.includes('INVALID_ARGUMENTS'));
  const missing = await runAudit(['--model', 'test/__missing_v3_artifact__.json', '--history', 'test/__missing_v3_history__.json']);
  assert.equal(missing.status, 'PAS');
  assert.ok(missing.reasonCodes.includes('V3_ARTIFACT_FILE_UNAVAILABLE'));
  assert.ok(missing.reasonCodes.includes('V3_HISTORY_FILE_UNAVAILABLE'));
  assert.equal(missing.weightsCount, null);
  assert.equal(missing.backtestVerified, false);
});
