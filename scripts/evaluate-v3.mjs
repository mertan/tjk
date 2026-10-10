#!/usr/bin/env node
/**
 * V3 walk-forward research evaluation (read-only, no betting).
 * Fold for test month M: train = dates before M-1, calibration = month M-1, test = M.
 * Market probabilities come only from GANYAN quotes timestamped <= post-5min.
 * ROI uses the official pari-mutuel dividend (results CSV "Ganyan"), with
 * explicit haircut sensitivities; AGF is never a model input.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { buildExamples, FEATURE_NAMES, inferFormOrder } from '../model-v2/features.mjs';
import { scores, softmax, train } from '../model-v2/model.mjs';
import { buildExtras, EXTRA_NAMES } from '../model-v3/history-features.mjs';
import { marketGate, marketProbabilities, calibrationStats, fitTemperature, valueBets } from '../model-v3/market.mjs';
import { summarizeWagers } from '../model-v3/evaluation.mjs';
import { predictAtCutoff, wilson } from '../backtest.mjs';
import { buildProgramCsvUrl, programForVenueCsv } from '../server.mjs';

const opts = {};
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i += 2) opts[argv[i].replace(/^--/, '')] = argv[i + 1];
const cacheDir = opts.cache || '.backtest-cache';
const months = (opts.months || '2026-04,2026-05,2026-06,2026-07,2026-08,2026-09,2026-10').split(',');
const l2 = Number(opts.l2 || 30);
const minSample = 30;
const { races } = JSON.parse(await readFile(opts.data || '.v2-data/races-yer.json', 'utf8'));
const odds = JSON.parse(await readFile(opts.odds || '.v2-data/odds.json', 'utf8')).races;

const cachePath = (url, ext) => join(cacheDir, `${createHash('sha1').update(url).digest('hex')}.${ext}`);
const readCache = (url, ext) => readFile(cachePath(url, ext), 'utf8').catch(() => null);
const pct = (x) => (x === null ? null : Math.round(x * 1000) / 10);
const r4 = (x) => (x === null || x === undefined ? null : Math.round(x * 10000) / 10000);
const addMonths = (month, n) => { const d = new Date(`${month}-01T00:00:00Z`); d.setUTCMonth(d.getUTCMonth() + n); return d.toISOString().slice(0, 7); };

const order = inferFormOrder(races.filter((r) => r.date < `${months[0]}-01`));
const raceById = new Map(races.map((r) => [`${r.date}|${r.venue}|${r.number}`, r]));
const extras = buildExtras(races);
const examples = buildExamples(races, { newestLast: order.newestLast })
  .filter((e) => e.positions.filter((p) => p === 1).length === 1)
  .map((e) => {
    const quote = odds[e.id];
    const gate = marketGate(e.numbers, quote);
    const q = gate.ok ? marketProbabilities(e.numbers.map((n) => quote.runners[n].odds)) : null;
    const xV2 = e.X;
    const xV3 = q ? e.X.map((row, i) => [...row, ...extras.get(e.id)[i], Math.log(q[i])]) : null;
    return { ...e, gate, q, quoteOdds: q ? e.numbers.map((n) => quote.runners[n].odds) : null, xV2, xV3 };
  });

/** V1 (PR #8 heuristic) rebuilt from cached official program + race-level odds history. */
const programCache = new Map();
async function v1Leader(e) {
  const race = raceById.get(e.id);
  const quote = odds[e.id];
  if (!race?.yer || !quote) return { status: 'PAS', reasonCodes: ['SOURCE_UNAVAILABLE'] };
  const venue = { KEY: race.venue, YER: race.yer };
  const key = `${race.date}|${race.venue}`;
  if (!programCache.has(key)) {
    const url = buildProgramCsvUrl(race.date, venue);
    programCache.set(key, url ? programForVenueCsv(await readCache(url, 'txt'), race.date, venue) : []);
  }
  const programRace = programCache.get(key).find((p) => p.number === race.number) || null;
  const url = `https://vhs.tjk.org/muhtemeller/data/history?date=${race.date}&hipodromkey=${encodeURIComponent(race.venue)}&no=${race.number}&bet=GANYAN`;
  const history = JSON.parse((await readCache(url, 'json')) || 'null')?.data;
  if (!programRace || !history) return { status: 'PAS', reasonCodes: ['SOURCE_UNAVAILABLE'] };
  const sets = new Map((history.datasets || []).map((s) => [Number(s.label), s.data || []]));
  const programBy = new Map(programRace.runners.map((r) => [r.number, r]));
  const runners = race.runners.map((r) => ({
    number: r.number,
    name: r.name,
    out: r.scratched,
    history: r.scratched ? [] : (history.labels || []).map((label, i) => ({ label: String(label), odds: Number(sets.get(r.number)?.[i]) || null })),
    program: programBy.get(r.number)
  }));
  return predictAtCutoff({ race: { time: race.time, surface: race.surface }, programRace, runners, cutoffMs: quote.cutoffMs });
}

function rate(hits, total) {
  if (total < minSample) return { hits, total, rate: null, ci95: null, note: 'INSUFFICIENT_SAMPLE' };
  const [lo, hi] = wilson(hits, total);
  return { hits, total, rate: pct(hits / total), ci95: [pct(lo), pct(hi)] };
}

const rows = [];
const folds = [];
for (const month of months) {
  const calMonth = addMonths(month, -1);
  const trainSet = examples.filter((e) => e.date < `${calMonth}-01`);
  const calSet = examples.filter((e) => e.date.startsWith(calMonth));
  const testSet = examples.filter((e) => e.date.startsWith(month));
  if (!testSet.length) continue;
  const v3Train = trainSet.filter((e) => e.xV3);
  const v3Cal = calSet.filter((e) => e.xV3);
  const v2Reasons = [];
  const v3Reasons = [];
  if (!trainSet.length) v2Reasons.push('V2_TRAINING_UNAVAILABLE');
  if (!calSet.length) v2Reasons.push('V2_CALIBRATION_UNAVAILABLE');
  if (!v3Train.length) v3Reasons.push('V3_TRAINING_UNAVAILABLE');
  if (!v3Cal.length) v3Reasons.push('V3_CALIBRATION_UNAVAILABLE');
  const w2 = v2Reasons.length ? null : train(trainSet.map((e) => ({ X: e.xV2, positions: e.positions })), { l2, epochs: 300 });
  const w3 = v3Reasons.length ? null : train(v3Train.map((e) => ({ X: e.xV3, positions: e.positions })), { l2, epochs: 300 });
  const s2 = (e) => scores(w2, e.xV2);
  const s3 = (e) => scores(w3, e.xV3);
  const t2 = w2 ? fitTemperature(calSet.map((e) => ({ s: s2(e), win: e.positions.indexOf(1) }))) : null;
  const t3 = w3 ? fitTemperature(v3Cal.map((e) => ({ s: s3(e), win: e.positions.indexOf(1) }))) : null;
  if (w2 && t2 === null) v2Reasons.push('V2_CALIBRATION_INVALID');
  if (w3 && t3 === null) v3Reasons.push('V3_CALIBRATION_INVALID');
  folds.push({ month, trainRaces: trainSet.length, v3TrainRaces: v3Train.length,
    calibrationMonth: calMonth, calRaces: calSet.length, v3CalRaces: v3Cal.length,
    tempV2: r4(t2), tempV3: r4(t3),
    v2Status: v2Reasons.length ? 'PAS' : 'OK', v3Status: v3Reasons.length ? 'PAS' : 'OK',
    v2Reasons, v3Reasons });
  for (const e of testSet) {
    const p2 = v2Reasons.length ? null : softmax(s2(e).map((s) => s * t2));
    const p3 = e.xV3 && !v3Reasons.length ? softmax(s3(e).map((s) => s * t3)) : null;
    const v1 = await v1Leader(e);
    const race = raceById.get(e.id);
    const dividends = e.numbers.map((n) => race.runners.find((r) => r.number === n)?.closingOdds ?? null);
    const pick = (p) => p.indexOf(Math.max(...p));
    const outcome = (i) => (i < 0 ? null : { win: e.positions[i] === 1, top3: e.positions[i] !== null && e.positions[i] <= 3 });
    const agfOk = e.agf.every((x) => Number.isFinite(x));
    rows.push({
      id: e.id, month,
      marketPAS: e.gate.ok ? null : e.gate.reasons,
      v1: v1.status === 'OK' ? outcome(e.numbers.indexOf(v1.leader)) : null,
      v1Reasons: v1.status === 'OK' ? null : v1.reasonCodes,
      v2: p2 ? outcome(pick(p2)) : null,
      v2Reasons: p2 ? null : v2Reasons,
      v3Reasons: p3 ? null : [...new Set([...v3Reasons, ...e.gate.reasons])],
      v3: p3 ? outcome(pick(p3)) : null,
      fav: e.q ? outcome(pick(e.q)) : null,
      agf: agfOk ? outcome(pick(e.agf)) : null,
      win: e.positions.indexOf(1),
      p2, p3, q: e.q,
      bets: p3 ? valueBets({ p: p3, quoteOdds: e.quoteOdds, dividends, positions: e.positions }) : null,
      favBet: e.q ? (() => {
        const i = pick(e.q);
        const win = e.positions[i] === 1;
        const validDividend = Number.isFinite(dividends[i]) && dividends[i] > 0;
        return { stake: 1, ret: win && validDividend ? dividends[i] : 0,
          wins: win ? 1 : 0, missingDividend: win && !validDividend ? 1 : 0 };
      })() : null
    });
  }
}

function summarize(set) {
  const out = { races: set.length };
  for (const key of ['v1', 'v2', 'v3', 'fav', 'agf']) {
    const predicted = set.filter((r) => r[key]);
    out[key] = {
      coverage: { predicted: predicted.length, of: set.length, pct: set.length ? pct(predicted.length / set.length) : null },
      win: rate(predicted.filter((r) => r[key].win).length, predicted.length),
      top3: rate(predicted.filter((r) => r[key].top3).length, predicted.length)
    };
  }
  const withMarket = set.filter((r) => r.q && r.p3);
  out.calibration = {
    races: withMarket.length,
    v2: calibrationStats(withMarket.map((r) => ({ p: r.p2, win: r.win }))),
    v3: calibrationStats(withMarket.map((r) => ({ p: r.p3, win: r.win }))),
    market: calibrationStats(withMarket.map((r) => ({ p: r.q, win: r.win })))
  };
  return out;
}

function roi(set) {
  const out = {};
  for (const edge of ['0.05', '0.10', '0.20']) {
    out[`edge>=${edge}`] = summarizeWagers(set.filter((r) => r.bets).map((r) => r.bets[edge]), { minSample });
  }
  out.marketFavouriteFlat = summarizeWagers(set.filter((r) => r.favBet).map((r) => r.favBet), { minSample });
  return out;
}

const same = rows.filter((r) => r.v1 && r.v3 && r.fav);
const report = {
  generatedAt: new Date().toISOString(),
  method: { folds: 'expanding train (< M-1) / calibration (M-1) / test (M)', l2, cutoffMin: 5, maxQuoteAgeMin: 12, formOrder: order },
  folds,
  byMonth: Object.fromEntries(months.map((m) => [m, { ...summarize(rows.filter((r) => r.month === m)), roi: roi(rows.filter((r) => r.month === m)) }])),
  overall: { ...summarize(rows), roi: roi(rows) },
  sameRacesAllModels: summarize(same),
  marketPASReasons: rows.reduce((acc, r) => { for (const x of r.marketPAS || []) acc[x] = (acc[x] || 0) + 1; return acc; }, {}),
  v3PASReasons: rows.reduce((acc, r) => { for (const x of r.v3Reasons || []) acc[x] = (acc[x] || 0) + 1; return acc; }, {}),
  v1PASReasons: rows.reduce((acc, r) => { for (const x of r.v1Reasons || []) acc[x] = (acc[x] || 0) + 1; return acc; }, {}),
  featureNamesV3: [...FEATURE_NAMES, ...EXTRA_NAMES, 'logMarketProbT5']
};
if (opts.out) await writeFile(opts.out, `${JSON.stringify({ ...report, rows: rows.map(({ p2, p3, q, ...rest }) => rest) }, null, 1)}\n`);
console.log(JSON.stringify(report, null, 1));
