import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runPreparation } from '../scripts/prepare-v3-live.mjs';
import { runAudit } from '../scripts/check-v3-live.mjs';
import { auditBundle } from '../model-v3/training-bundle.mjs';
import { buildExamples, inferFormOrder } from '../model-v2/features.mjs';
import { scores, train } from '../model-v2/model.mjs';
import { buildExtras } from '../model-v3/history-features.mjs';
import { buildLiveV3Features, FEATURE_NAMES_V3 } from '../model-v3/features.mjs';
import { fitTemperature, marketProbabilities } from '../model-v3/market.mjs';
import { historyDigest, validateArtifact } from '../model-v3/live-inference.mjs';
import { jsonDigest, prepareTraining, verifyTraining } from '../model-v3/training-evidence.mjs';

// Synthetic results prove reproducibility and temporal isolation, not model quality.
const NOW = Date.parse('2026-03-02T12:00:00+03:00');
const PARAMS = { l2: 30, epochs: 8, lr: 0.05 };
const id = (race) => [race.date, race.venue, race.number].join('|');
const copy = (value) => structuredClone(value);
function raceOn(date, number) {
  return {
    date, venue: 'ANKARA', number, time: String(12 + number).padStart(2, '0') + ':00',
    distance: number % 2 ? 1200 : 1400, surface: 'Kum', prize1: 100_000 + number * 1000,
    runners: [1, 2, 3, 4].map((n) => ({
      number: n, name: 'SYNTHETIC ' + number + '-' + n,
      key: 'SIRE' + number + '-' + n + '|DAM' + number + '-' + n + '|2022',
      age: 4, weight: 54 + n, extraWeight: n % 2, stall: n,
      jockey: 'JOCKEY ' + n, trainer: 'TRAINER ' + n,
      rating: 60 - 2 * n, last6: 'K8K' + n, daysOff: 10 + n,
      bestTime: 80 + n, agf: n, scratched: false,
      position: n, finishTime: 80 + n, closingOdds: null
    }))
  };
}
function quoteFor(race) {
  const postMs = Date.parse(race.date + 'T' + race.time + ':00+03:00');
  const cutoffMs = postMs - 5 * 60_000;
  return {
    postMs, cutoffMs, lastLabelAt: cutoffMs - 60_000, historyValid: true,
    runners: Object.fromEntries(race.runners.map((runner) => [
      runner.number, { odds: 2 + runner.number, at: cutoffMs - 60_000 }
    ]))
  };
}
function fixture() {
  const dates = [
    '2026-01-28', '2026-01-29', '2026-01-30', '2026-01-31',
    ...Array.from({ length: 8 }, (_, i) => '2026-02-' + String(i + 1).padStart(2, '0'))
  ];
  const history = dates.flatMap((date) => [1, 2, 3, 4].map((n) => raceOn(date, n)));
  return {
    history, odds: Object.fromEntries(history.map((race) => [id(race), quoteFor(race)])),
    asOf: '2026-03-01', calibrationFrom: '2026-02-01', now: NOW, params: { ...PARAMS }
  };
}
function trained(input = fixture()) {
  const result = prepareTraining(input);
  assert.equal(result.status, 'TRAINED_CANDIDATE', JSON.stringify(result.reasonCodes));
  return result;
}
function verify(result) {
  return verifyTraining({ ...result, now: NOW });
}
function researchRows(history, odds, newestLast) {
  const extras = buildExtras(history);
  return buildExamples(history, { newestLast }).map((example) => {
    const quote = odds.races?.[example.id] ?? odds[example.id];
    const q = marketProbabilities(example.numbers.map((number) => quote.runners[number].odds));
    return {
      ...example,
      X: example.X.map((row, i) => [...row, ...extras.get(example.id)[i], Math.log(q[i])])
    };
  });
}

test('evidence reproduces 33 training columns, frozen form order, weights and held-out temperature', () => {
  const input = fixture();
  const result = trained(input);
  assert.deepEqual(result.artifact.featureNames, FEATURE_NAMES_V3);
  assert.equal(result.artifact.weights.length, 33);
  assert.ok(result.artifact.weights.every(Number.isFinite));
  assert.equal(validateArtifact(result.artifact).ok, true);
  const frozenOrder = inferFormOrder(input.history.filter((race) => race.date < input.calibrationFrom));
  assert.equal(frozenOrder.newestLast, true);
  assert.equal(result.artifact.formOrder.newestLast, frozenOrder.newestLast);
  const rows = researchRows(result.history, result.odds, frozenOrder.newestLast);
  assert.ok(rows.every((row) => row.X.every((x) => x.length === 33 && x.every(Number.isFinite))));
  const training = rows.filter((row) => row.date < input.calibrationFrom);
  const calibration = rows.filter((row) => row.date >= input.calibrationFrom);
  assert.equal(training.length, 16);
  assert.equal(calibration.length, 32);
  assert.deepEqual(result.artifact.weights, train(training, PARAMS));
  assert.equal(result.artifact.training.races, 16);
  assert.equal(result.artifact.calibration.races, 32);
  assert.ok(result.artifact.training.through < result.artifact.calibration.from);
  const expectedTemperature = fitTemperature(calibration.map((row) => ({
    s: scores(result.artifact.weights, row.X), win: row.positions.indexOf(1)
  })));
  assert.equal(result.artifact.calibration.temperature, expectedTemperature);
  assert.equal(result.artifact.calibration.scoreTransform, 'multiply');
  const proofRows = (items) => items.map(({ id, numbers, X, positions }) => ({ id, numbers, X, positions }));
  assert.equal(result.evidence.training.examplesSha256, jsonDigest(proofRows(training)));
  assert.equal(result.evidence.calibration.examplesSha256, jsonDigest(proofRows(calibration)));
  const audit = verify(result);
  assert.equal(audit.status, 'VERIFIED_TRAINING', JSON.stringify(audit.reasonCodes));
  assert.equal(audit.trainingReproduced, true);
  assert.equal(audit.calibrationReproduced, true);
  assert.equal(audit.backtestVerified, false, 'calibration metrics are not an independent backtest');
});

test('training matrices match live inference with current outcomes withheld', () => {
  const input = fixture();
  const result = trained(input);
  const rows = researchRows(result.history, result.odds, result.artifact.formOrder.newestLast);
  const target = copy(input.history.find((race) => race.date === '2026-02-03' && race.number === 2));
  const expected = rows.find((row) => row.id === id(target));
  target.runners.forEach((runner) => { runner.position = null; runner.finishTime = null; });
  const quote = input.odds[id(target)];
  const prior = input.history.filter((race) => race.date < target.date);
  const actual = buildLiveV3Features({
    history: prior, race: target, quote, newestLast: result.artifact.formOrder.newestLast
  });
  assert.deepEqual(actual.numbers, expected.numbers);
  assert.deepEqual(actual.X, expected.X);
});

test('same-day outcomes cannot leak into another race feature vector', () => {
  const input = fixture();
  const target = input.history.find((race) => race.date === '2026-02-03' && race.number === 2);
  const prior = input.history.filter((race) => race.date < target.date);
  const sameDay = copy(input.history.find((race) => race.date === target.date && race.number === 1));
  // Reuse target identities to make any accidental same-day history update visible.
  sameDay.runners.forEach((runner, i) => {
    runner.key = target.runners[i].key;
    runner.position = 4 - i;
    runner.finishTime = 170 - i;
  });
  const before = buildLiveV3Features({
    history: prior, race: target, quote: input.odds[id(target)], newestLast: true
  });
  const after = buildLiveV3Features({
    history: [...prior, sameDay], race: target, quote: input.odds[id(target)], newestLast: true
  });
  assert.deepEqual(after.X, before.X);
});

test('future rows and future prices do not influence weights, frozen order or calibration', () => {
  const input = fixture();
  const original = trained(input);
  for (const date of [input.asOf, '2026-12-31']) {
    const race = raceOn(date, 1);
    race.runners.forEach((runner) => { runner.last6 = 'K' + runner.number + 'K8'; });
    input.history.push(race);
    input.odds[id(race)] = quoteFor(race);
    input.odds[id(race)].runners[1].odds = 880;
  }
  const result = trained(input);
  assert.deepEqual(result.artifact.weights, original.artifact.weights);
  assert.deepEqual(result.artifact.formOrder, original.artifact.formOrder);
  assert.deepEqual(result.artifact.calibration, original.artifact.calibration);
  assert.deepEqual(result.history, original.history);
  assert.equal(result.artifact.historySha256, original.artifact.historySha256);
});

test('calibration labels and token-order evidence never train weights or choose form order', () => {
  const input = fixture();
  const original = trained(input);
  for (const race of input.history.filter((race) => race.date >= input.calibrationFrom)) {
    race.runners.forEach((runner) => {
      runner.position = 5 - runner.number;
      runner.finishTime = 80 + runner.position;
      runner.last6 = 'K' + runner.position + 'K8';
    });
  }
  const result = trained(input);
  assert.deepEqual(result.artifact.weights, original.artifact.weights);
  assert.deepEqual(result.artifact.formOrder, original.artifact.formOrder);
  assert.equal(result.artifact.training.races, original.artifact.training.races);
  assert.equal(verify(result).status, 'VERIFIED_TRAINING');
});

test('history SHA-256 verifies the actual ordered parsed snapshot', () => {
  const result = trained();
  const expected = createHash('sha256').update(JSON.stringify(result.history)).digest('hex');
  assert.equal(result.artifact.historySha256, expected);
  assert.equal(historyDigest(result.history), expected);
  assert.equal(jsonDigest(result.history), expected);
  const altered = copy(result);
  altered.history[0].runners[0].finishTime += 1;
  assert.equal(verify(altered).status, 'PAS');
  altered.artifact.historySha256 = historyDigest(altered.history);
  assert.equal(verify(altered).status, 'PAS', 'rewriting only the history manifest cannot forge training evidence');
});

test('verification rejects altered weights, temperature, windows and quote evidence', () => {
  const result = trained();
  const changes = [
    (value) => { value.artifact.weights[0] += 0.01; },
    (value) => { value.artifact.weights.pop(); },
    (value) => { value.artifact.calibration.temperature += 0.05; },
    (value) => { value.artifact.training.races += 1; },
    (value) => { value.artifact.training.through = value.artifact.calibration.from; },
    (value) => { value.artifact.formOrder.newestLast = !value.artifact.formOrder.newestLast; },
    (value) => { value.artifact.featureNames.reverse(); },
    (value) => {
      const quotes = value.odds.races ?? value.odds;
      quotes[Object.keys(quotes)[0]].runners[1].odds += 1;
    },
    (value) => { value.evidence.params.lr = 0.1; },
    (value) => { value.evidence.calibrationFrom = '2026-02-02'; },
    (value) => {
      value.artifact.weights[0] += 0.01;
      value.evidence.hashes.artifactSha256 = jsonDigest(value.artifact);
    },
    (value) => { value.evidence = null; }
  ];
  for (const change of changes) {
    const altered = copy(result);
    change(altered);
    const audit = verify(altered);
    assert.equal(audit.status, 'PAS');
    assert.equal(audit.backtestVerified, false);
  }
});

test('future or overlapping windows, malformed dates and unsafe optimizer parameters are PAS', () => {
  const changes = [
    (input) => { input.asOf = '2026-03-03'; },
    (input) => { input.calibrationFrom = input.asOf; },
    (input) => { input.calibrationFrom = '2026-03-02'; },
    (input) => { input.asOf = '2026-02-30'; },
    (input) => { input.now = NaN; },
    (input) => { input.params.epochs = 0; },
    (input) => { input.params.epochs = 1.5; },
    (input) => { input.params.epochs = 10_001; },
    (input) => { input.params.l2 = -1; },
    (input) => { input.params.l2 = Infinity; },
    (input) => { input.params.lr = 0; },
    (input) => { input.params.lr = 2; }
  ];
  for (const change of changes) {
    const input = fixture();
    change(input);
    assert.equal(prepareTraining(input).status, 'PAS');
  }
});

test('missing evidence, insufficient held-out races and ambiguous training form order are PAS', () => {
  const cases = [
    (input) => { input.history = []; },
    (input) => { input.history = input.history.filter((race) => race.date >= input.calibrationFrom); },
    (input) => { input.history = input.history.slice(0, 45); },
    (input) => { input.history.forEach((race) => race.runners.forEach((runner) => { runner.last6 = ''; })); },
    (input) => { input.history[0].runners[0].key = '||?'; },
    (input) => { input.history[0].runners[0].position = '1'; }
  ];
  for (const change of cases) {
    const input = fixture();
    change(input);
    assert.equal(prepareTraining(input).status, 'PAS');
  }
});

test('missing, stale, future, wrong-cutoff and mismatched market inputs cannot fill calibration', () => {
  const changes = [
    (quote) => { delete quote.runners[1]; },
    (quote) => { quote.runners[1].odds = 0; },
    (quote) => { quote.runners[1].odds = 241.7; quote.runners[1].at -= 20 * 60_000; },
    (quote) => { quote.runners[1].at = quote.cutoffMs + 1; },
    (quote) => { quote.lastLabelAt = quote.cutoffMs + 1; },
    (quote) => { quote.cutoffMs += 60_000; },
    (quote) => { quote.postMs += 60_000; },
    (quote) => { quote.runners[5] = { odds: 5, at: quote.lastLabelAt }; },
    (quote) => { quote.historyValid = false; }
  ];
  for (const change of changes) {
    const input = fixture();
    for (const race of input.history.filter((race) => race.date >= input.calibrationFrom)) {
      change(input.odds[id(race)]);
    }
    const result = prepareTraining(input);
    assert.equal(result.status, 'PAS');
    assert.ok(result.reasonCodes.includes('V3_CALIBRATION_INSUFFICIENT'));
    assert.equal(result.exclusions.races, 32);
    assert.ok(Object.keys(result.exclusions.reasons).length > 0);
  }
});

async function withInputFiles(run) {
  const directory = await mkdtemp(join(tmpdir(), 'tjk-v3-proof-'));
  try {
    const input = fixture();
    const historyText = JSON.stringify({ races: input.history }, null, 2) + '\n';
    const oddsText = JSON.stringify({ races: input.odds }, null, 2) + '\n';
    const data = join(directory, 'races.json');
    const odds = join(directory, 'odds.json');
    const out = join(directory, 'candidate');
    const existingModel = join(directory, 'v3-model.json');
    const legacyTrainer = join(directory, 'train-v3-live.mjs');
    const legacyText = "throw new Error('legacy trainer must not execute');\n";
    await writeFile(data, historyText);
    await writeFile(odds, oddsText);
    await writeFile(existingModel, '{"existingModel":"untouched"}\n');
    await writeFile(legacyTrainer, legacyText);
    const args = [
      '--data', data, '--odds', odds, '--out', out,
      '--as-of', input.asOf, '--calibration-from', input.calibrationFrom,
      '--legacy-trainer', legacyTrainer
    ];
    await run({ directory, data, odds, out, args, existingModel, historyText, oddsText, legacyText });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test('one-command preparation writes a replayable isolated bundle with original byte hashes', async () => {
  await withInputFiles(async ({ out, args, existingModel, historyText, oddsText, legacyText }) => {
    const report = await runPreparation(args, { now: NOW });
    assert.equal(report.status, 'VERIFIED_TRAINING', JSON.stringify(report.reasonCodes));
    assert.equal(report.trainingReproduced, true);
    assert.equal(report.calibrationReproduced, true);
    assert.equal(report.backtestVerified, false);
    assert.equal(await readFile(existingModel, 'utf8'), '{"existingModel":"untouched"}\n');
    const manifest = JSON.parse(await readFile(join(out, 'bundle.json'), 'utf8'));
    const rawSha = (bytes) => createHash('sha256').update(bytes).digest('hex');
    assert.equal(manifest.inputs.historyRawSha256, rawSha(historyText));
    assert.equal(manifest.inputs.oddsRawSha256, rawSha(oddsText));
    assert.equal(manifest.inputs.legacyTrainerSha256, rawSha(legacyText));
    for (const name of ['v3-model.json', 'history.json', 'odds.json', 'evidence.json']) {
      assert.equal(manifest.files[name], rawSha(await readFile(join(out, name))));
    }
    const audit = await auditBundle(out, { now: NOW });
    assert.equal(audit.status, 'VERIFIED_TRAINING', JSON.stringify(audit.reasonCodes));
    assert.equal(audit.retainedInputFilesVerified, true);
    assert.equal(audit.originalInputFilesRechecked, false);
    const cliAudit = await runAudit(['--bundle', out]);
    assert.equal(cliAudit.status, 'VERIFIED_TRAINING', JSON.stringify(cliAudit.reasonCodes));
    assert.equal(cliAudit.trainingReproduced, true);
    const modelBefore = await readFile(join(out, 'v3-model.json'), 'utf8');
    const repeated = await runPreparation(args, { now: NOW });
    assert.equal(repeated.status, 'PAS');
    assert.ok(repeated.reasonCodes.includes('V3_OUTPUT_DIRECTORY_EXISTS_OR_UNAVAILABLE'));
    assert.equal(await readFile(join(out, 'v3-model.json'), 'utf8'), modelBefore);
  });
});

test('bundle audit fails closed for modified retained bytes and changed trainer source hashes', async () => {
  await withInputFiles(async ({ out, args }) => {
    assert.equal((await runPreparation(args, { now: NOW })).status, 'VERIFIED_TRAINING');
    const historyPath = join(out, 'history.json');
    const originalHistory = await readFile(historyPath, 'utf8');
    await writeFile(historyPath, originalHistory + ' ');
    const bytesAudit = await auditBundle(out, { now: NOW });
    assert.equal(bytesAudit.status, 'PAS');
    assert.ok(bytesAudit.reasonCodes.includes('V3_BUNDLE_FILE_DIGEST_MISMATCH'));
    await writeFile(historyPath, originalHistory);
    const manifestPath = join(out, 'bundle.json');
    const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
    manifest.sources['model-v2/model.mjs'] = 'a'.repeat(64);
    await writeFile(manifestPath, JSON.stringify(manifest));
    const sourceAudit = await auditBundle(out, { now: NOW });
    assert.equal(sourceAudit.status, 'PAS');
    assert.ok(sourceAudit.reasonCodes.includes('V3_TRAINING_SOURCE_DIGEST_MISMATCH'));
    assert.equal(sourceAudit.backtestVerified, false);
  });
});

test('preparation and bundle audit report missing inputs without model output or unsafe defaults', async () => {
  await withInputFiles(async ({ directory, args }) => {
    const invalid = await runPreparation(['--unknown'], { now: NOW });
    assert.equal(invalid.status, 'PAS');
    const missing = [...args];
    missing[missing.indexOf('--data') + 1] = join(directory, 'missing-history.json');
    assert.equal((await runPreparation(missing, { now: NOW })).status, 'PAS');
    const absentBundle = await auditBundle(join(directory, 'missing-bundle'), { now: NOW });
    assert.equal(absentBundle.status, 'PAS');
    assert.equal(absentBundle.backtestVerified, false);
  });
});
