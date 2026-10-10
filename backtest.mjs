/**
 * Point-in-time backtest core. A prediction for a finished race is rebuilt
 * only from odds points whose source timestamp is <= cutoff (post time minus
 * a margin). Results (finishing ranks, final odds) are read separately and are
 * never passed to the prediction step.
 */
import { analyzeRunners } from './analysis.mjs';
import { classifyRating, parseSourceTime, raceGate, FRESHNESS_LIMITS } from './race-gates.mjs';

const validOdds = (value) => Number.isFinite(value) && value > 0 && value < 900;

/** Post times are Istanbul wall clock; a venue's sequence wraps past midnight. */
export function postTimes(date, times) {
  const base = Date.parse(`${date}T00:00:00+03:00`);
  let dayOffset = 0;
  let previous = -1;
  return times.map((time) => {
    const match = typeof time === 'string' ? time.match(/^(\d{1,2})[.:](\d{2})$/) : null;
    if (!match) return null;
    if (Number(match[1]) > 23 || Number(match[2]) > 59) return null;
    const minutes = Number(match[1]) * 60 + Number(match[2]);
    if (previous >= 0 && minutes < previous) dayOffset += 1;
    previous = minutes;
    return base + (dayOffset * 1440 + minutes) * 60_000;
  });
}

export function pointsAtCutoff(points, cutoffMs) {
  return (Array.isArray(points) ? points : [])
    .map((point) => ({ label: point?.label, odds: point?.odds, at: parseSourceTime(point?.label) }))
    .filter((point) => Number.isFinite(point.at) && point.at <= cutoffMs && validOdds(point.odds))
    .sort((a, b) => a.at - b.at);
}

/**
 * runners: [{ number, name, out, history: [{label, odds}], program }]
 * program: the parsed pre-race program runner (rating, agf) or undefined.
 * inputSnapshotAt: trusted capture time for this entire pre-race field/program,
 * including withdrawals and optional AGF baseline. The archive CLI cannot supply
 * it. Never attach a made-up capture time to a post-race archive.
 */
export function predictAtCutoff({ race, programRace, foreign = false, runners, cutoffMs, inputSnapshotAt = null, limits = FRESHNESS_LIMITS }) {
  const capturedAt = parseSourceTime(inputSnapshotAt);
  const snapshotVerified = Number.isFinite(capturedAt) && Number.isFinite(cutoffMs) && capturedAt <= cutoffMs;
  if (!snapshotVerified) return {
    status: 'PAS', reasonCodes: ['PRE_CUTOFF_INPUT_SNAPSHOT_UNVERIFIED'],
    leader: null, marketFavourite: null, agfLeader: null, latestPointUsedAt: null,
    cutoffAt: Number.isFinite(cutoffMs) ? new Date(cutoffMs).toISOString() : null
  };
  const inputs = [];
  const quoteTimes = [];
  for (const runner of runners) {
    const history = pointsAtCutoff(runner.history, cutoffMs);
    const { rating, ratingStatus } = classifyRating(runner.program?.rating, { foreign });
    const agf = runner.program?.agf?.at(-1)?.percentage;
    const input = {
      number: runner.number,
      name: runner.name,
      out: runner.out === true,
      currentOdds: history.at(-1)?.odds ?? null,
      openingOdds: history[0]?.odds ?? null,
      history: history.map(({ odds, label }) => ({ odds, label })),
      rating,
      ratingStatus,
      agfLatest: Number.isFinite(agf) ? agf : null
    };
    inputs.push(input);
    if (!input.out) quoteTimes.push(history.at(-1)?.at ?? NaN);
  }
  const feed = {
    race: { SAAT: race.time, PIST: race.surface, DURUM: 'AÇIK' },
    checksum: {},
    racePayload: { data: { muhtemeller: { DURUM: 'AÇIK', bahisler: [{ B: 'GANYAN', muhtemeller: runners.map((r) => ({ S1: String(r.number), KOSMAZ: r.out === true })) }] } } }
  };
  const gate = raceGate(feed, programRace, { now: cutoffMs, quoteTimes, limits, requireOpen: false, requireHeartbeat: false });
  const analysis = analyzeRunners(inputs, { reasonCodes: gate.reasons });
  const active = inputs.filter((runner) => !runner.out);
  const favourite = active.every((runner) => validOdds(runner.currentOdds)) && active.length
    ? [...active].sort((a, b) => a.currentOdds - b.currentOdds || a.number - b.number)[0].number
    : null;
  const withAgf = active.filter((runner) => Number.isFinite(runner.agfLatest));
  const agfLeader = withAgf.length === active.length && active.length
    ? [...withAgf].sort((a, b) => b.agfLatest - a.agfLatest || a.number - b.number)[0].number
    : null;
  return {
    status: analysis.status,
    reasonCodes: analysis.reasonCodes,
    leader: analysis.leader?.number ?? null,
    marketFavourite: favourite,
    agfLeader,
    latestPointUsedAt: Number.isFinite(Math.max(...quoteTimes)) ? new Date(Math.max(...quoteTimes)).toISOString() : null,
    cutoffAt: new Date(cutoffMs).toISOString()
  };
}

/** Finishing ranks from the official post-race GANYAN rows (R field). */
export function resultRanks(ganyanRows) {
  const ranks = new Map();
  for (const row of Array.isArray(ganyanRows) ? ganyanRows : []) {
    const number = Number(row?.S1);
    const rank = Number(row?.R);
    if (Number.isSafeInteger(number) && Number.isSafeInteger(rank) && rank > 0) ranks.set(number, rank);
  }
  return ranks;
}

/** Winners per race from the official results CSV footer, e.g. "GANYAN(2) :3,30 TL". */
export function resultsCsvWinners(csvText) {
  const winners = new Map();
  let race = null;
  for (const line of String(csvText || '').replace(/^\uFEFF/, '').split(/\r?\n/)) {
    const boundary = line.match(/^(\d+)\.\s*ko[şs]u/iu);
    if (boundary) { race = Number(boundary[1]); continue; }
    if (race === null) continue;
    for (const token of line.split(/,\s*/)) {
      const match = token.trim().match(/^GANYAN\((\d+(?:[/-]\d+)*)\)\s*:/u);
      if (match && !winners.has(race)) winners.set(race, match[1].split(/[/-]/).map(Number));
    }
  }
  return winners;
}

export function wilson(successes, total, z = 1.96) {
  if (!total) return null;
  const p = successes / total;
  const denominator = 1 + (z * z) / total;
  const centre = (p + (z * z) / (2 * total)) / denominator;
  const margin = (z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))) / denominator;
  return [Math.max(0, centre - margin), Math.min(1, centre + margin)];
}

function rate(hits, total, minSample) {
  const pct = (value) => Math.round(value * 1000) / 10;
  if (total < minSample) return { hits, total, rate: null, ci95: null, note: 'INSUFFICIENT_SAMPLE' };
  const [low, high] = wilson(hits, total);
  return { hits, total, rate: pct(hits / total), ci95: [pct(low), pct(high)] };
}

/**
 * records: [{ prediction, winners: number[] | null }]. Rates are only reported
 * when at least minSample settled predictions exist; otherwise counts only.
 */
export function summarize(records, { minSample = 30 } = {}) {
  if (!Number.isSafeInteger(minSample) || minSample < 1) throw new RangeError('INVALID_MIN_SAMPLE');
  const settled = records.filter((record) => record.resultCheck === 'CONFIRMED' && Array.isArray(record.winners) && record.winners.length);
  const predicted = settled.filter((record) => record.prediction.status === 'OK');
  const pasReasons = {};
  for (const record of settled.filter((item) => item.prediction.status !== 'OK')) {
    for (const reason of record.prediction.reasonCodes) pasReasons[reason] = (pasReasons[reason] || 0) + 1;
  }
  const hit = (key, set) => set.filter((record) => record.winners.includes(record.prediction[key])).length;
  const withAgf = predicted.filter((record) => record.prediction.agfLeader !== null);
  return {
    races: records.length,
    settledRaces: settled.length,
    unsettledRaces: records.length - settled.length,
    validPredictions: predicted.length,
    pasRaces: settled.length - predicted.length,
    pasReasons: Object.fromEntries(Object.entries(pasReasons).sort((a, b) => b[1] - a[1])),
    minSample,
    modelLeaderWin: rate(hit('leader', predicted), predicted.length, minSample),
    baselines: {
      marketFavouriteWin: rate(hit('marketFavourite', predicted), predicted.length, minSample),
      agfLeaderWinComparisonOnly: rate(hit('agfLeader', withAgf), withAgf.length, minSample)
    }
  };
}
