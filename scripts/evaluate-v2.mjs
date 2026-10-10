#!/usr/bin/env node
/**
 * Chronological V1 vs V2 evaluation.
 * train: date < --val-start; validation (L2 choice): [--val-start, --test-start); test: >= --test-start.
 * The final model is refit on train+validation and scored once on test.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { buildExamples, FEATURE_NAMES, inferFormOrder } from '../model-v2/features.mjs';
import { logLoss, predict, train } from '../model-v2/model.mjs';
import { wilson } from '../backtest.mjs';

const opts = {};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i += 2) opts[argv[i].replace(/^--/, '')] = argv[i + 1];
const valStart = opts['val-start'] || '2026-08-01';
const testStart = opts['test-start'] || '2026-09-12';
const minSample = Number(opts['min-sample'] || 30);
const dataset = JSON.parse(await readFile(opts.data || '.v2-data/races.json', 'utf8'));
const backtest = new Map();
for (const file of (opts.backtest || '').split(',').filter(Boolean)) {
  for (const record of JSON.parse(await readFile(file, 'utf8')).records) backtest.set(`${record.date}|${record.venue}|${record.race}`, record);
}

const order = inferFormOrder(dataset.races.filter((race) => race.date < valStart));
const examples = buildExamples(dataset.races, { newestLast: order.newestLast });
const labelled = examples.filter((e) => e.positions.filter((p) => p === 1).length === 1);
const trainSet = labelled.filter((e) => e.date < valStart);
const valSet = labelled.filter((e) => e.date >= valStart && e.date < testStart);
const testSet = labelled.filter((e) => e.date >= testStart);

const uniformLoss = (set) => set.reduce((s, e) => s + Math.log(e.numbers.length), 0) / set.length;
const grid = [0.3, 3, 30, 300].map((l2) => ({ l2, valLogLoss: logLoss(train(trainSet, { l2 }), valSet) }));
const best = grid.reduce((a, b) => (b.valLogLoss < a.valLogLoss ? b : a));
const weights = train([...trainSet, ...valSet], { l2: best.l2 });

const pct = (x) => Math.round(x * 1000) / 10;
function rate(hits, total) {
  if (total < minSample) return { hits, total, rate: null, ci95: null, note: 'INSUFFICIENT_SAMPLE' };
  const [low, high] = wilson(hits, total);
  return { hits, total, rate: pct(hits / total), ci95: [pct(low), pct(high)] };
}
function seeded(seed) { return () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
function paired(a, b) {
  const n = a.length;
  if (n < minSample) return { n, diffPctPoints: null, note: 'INSUFFICIENT_SAMPLE' };
  const diff = a.reduce((s, x, i) => s + x - b[i], 0) / n;
  const rnd = seeded(7);
  const boots = Array.from({ length: 2000 }, () => {
    let s = 0;
    for (let k = 0; k < n; k += 1) { const i = Math.floor(rnd() * n); s += a[i] - b[i]; }
    return s / n;
  }).sort((x, y) => x - y);
  const onlyA = a.filter((x, i) => x && !b[i]).length;
  const onlyB = b.filter((x, i) => x && !a[i]).length;
  const m = onlyA + onlyB;
  let tail = 0;
  for (let k = 0, c = 1; k <= Math.min(onlyA, onlyB); k += 1) { tail += c; c = (c * (m - k)) / (k + 1); }
  return { n, diffPctPoints: pct(diff), ci95: [pct(boots[50]), pct(boots[1949])], onlyA, onlyB, mcnemarP: m ? Math.min(1, (2 * tail) / 2 ** m) : 1 };
}

const argBest = (values, better) => {
  let index = -1;
  values.forEach((v, i) => { if (v !== null && Number.isFinite(v) && (index < 0 || better(v, values[index]))) index = i; });
  return index;
};
const rows = testSet.map((e) => {
  const record = backtest.get(e.id);
  const at = (number) => (number === null || number === undefined ? -1 : e.numbers.indexOf(number));
  const outcome = (i) => (i < 0 ? null : { win: e.positions[i] === 1, top3: e.positions[i] !== null && e.positions[i] <= 3 });
  const v2 = predict(weights, e);
  return {
    id: e.id,
    v2: outcome(v2.leaderIndex),
    v2Pick: v2.leader,
    v1: record?.prediction?.status === 'OK' ? outcome(at(record.prediction.leader)) : null,
    fav5: record?.prediction?.marketFavourite ? outcome(at(record.prediction.marketFavourite)) : null,
    agf: e.agf.every((x) => x !== null) ? outcome(argBest(e.agf, (a, b) => a > b)) : null,
    closingFav: outcome(argBest(e.closingOdds, (a, b) => a < b)),
    inBacktest: Boolean(record)
  };
});

function block(filter, keys) {
  const set = rows.filter(filter);
  const out = { races: set.length };
  for (const key of keys) {
    out[key] = { win: rate(set.filter((r) => r[key]?.win).length, set.length), top3: rate(set.filter((r) => r[key]?.top3).length, set.length) };
  }
  for (const [a, b] of [['v2', 'fav5'], ['v2', 'closingFav'], ['v1', 'fav5'], ['v2', 'v1'], ['v2', 'agf']]) {
    if (!keys.includes(a) || !keys.includes(b)) continue;
    out[`${a}_vs_${b}`] = {
      win: paired(set.map((r) => +!!r[a]?.win), set.map((r) => +!!r[b]?.win)),
      top3: paired(set.map((r) => +!!r[a]?.top3), set.map((r) => +!!r[b]?.top3))
    };
  }
  return out;
}

const testRacesAll = examples.filter((e) => e.date >= testStart).length;
const backtestTest = [...backtest.values()].filter((r) => r.date >= testStart && r.winners);
const report = {
  generatedAt: new Date().toISOString(),
  split: { train: `${dataset.from} – ${valStart} öncesi`, validation: `${valStart} – ${testStart} öncesi`, test: `${testStart} – ${dataset.to}` },
  sizes: { train: trainSet.length, validation: valSet.length, test: testSet.length },
  formOrder: order,
  regularization: { grid, chosen: best.l2 },
  testLogLoss: { v2: logLoss(weights, testSet), uniform: uniformLoss(testSet) },
  weights: Object.fromEntries(FEATURE_NAMES.map((name, j) => [name, Math.round(weights[j] * 1000) / 1000])),
  coverage: {
    v2: { predicted: testSet.length, eligibleRaces: testRacesAll },
    v1: { predicted: backtestTest.filter((r) => r.prediction.status === 'OK').length, settledRaces: backtestTest.length }
  },
  allTestRaces: block(() => true, ['v2', 'closingFav', 'agf']),
  backtestRacesWithT5Favourite: block((r) => r.fav5, ['v2', 'fav5', 'closingFav', 'agf']),
  sameRacesV1Valid: block((r) => r.v1, ['v1', 'v2', 'fav5', 'agf']),
  rows
};
if (opts.out) await writeFile(opts.out, `${JSON.stringify(report, null, 2)}\n`);
const { rows: _rows, ...summary } = report;
console.log(JSON.stringify(summary, null, 1));
