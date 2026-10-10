import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { get as httpGet } from 'node:http';
import { buildRaceAnalysis, parseProgramCsv, startServer } from '../server.mjs';
import { analyzeRunners } from '../analysis.mjs';
import { classifyRating, parseSourceTime, quoteFreshness, FRESHNESS_LIMITS } from '../race-gates.mjs';
import { createCache, createRateLimiter } from '../tjk-cache.mjs';
import { pointsAtCutoff, postTimes, predictAtCutoff, resultsCsvWinners, summarize } from '../backtest.mjs';

const csv = await readFile(new URL('./fixtures/2026-10-08-belmont-park-program.csv', import.meta.url), 'utf8');
const programs = parseProgramCsv(csv);
const istanbul = (ms) => new Date(ms + 3 * 3_600_000).toISOString().slice(0, 19).replace('T', ' ');
let sequence = 40;

async function run(t, { raceNo, lastPointAgoMs = 60_000, heartbeatAgoMs = 5_000, currentOdds = '3.20' }) {
  const key = `FRESH${++sequence}`;
  const day = String(sequence - 30).padStart(2, '0');
  const date = `2026-11-${day}`;
  t.mock.method(Date, 'now', () => Date.parse(`${date}T18:45:00+03:00`));
  const program = programs.find((r) => r.number === raceNo);
  const rows = program.runners.map((r) => ({ S1: String(r.number), G: currentOdds, KOSMAZ: /Koşmaz/.test(r.rawName) }));
  const info = { SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK', timestamp: Date.now(), bahisler: [{ B: 'GANYAN', muhtemeller: rows }] };
  const race = { NO: raceNo, SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK' };
  const end = Date.now() - lastPointAgoMs;
  const labels = Array.from({ length: 10 }, (_, i) => istanbul(end - (9 - i) * 180_000));
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (raw) => {
    const url = String(raw); calls.push(url);
    const response = (data) => new Response(JSON.stringify(data), { status: 200 });
    if (url.endsWith('checksum.json')) return response({ success: true, day: key, datetime: istanbul(Date.now() - heartbeatAgoMs), runs: { [`${key}-${raceNo}`]: ['hash'] } });
    if (url.includes('/day-')) return response({ success: true, data: { yarislar: [{ KEY: key, YER: 'Belmont Park ABD', HIPODROM: 'Belmont Park', YURTDISI: true, kosular: [race], atlar: { [raceNo]: Object.fromEntries(program.runners.map((r) => [r.number, r.rawName])) } }] } });
    if (url.endsWith('.csv')) return new Response(csv.replace('08/10/2026', `${day}/11/2026`), { status: 200 });
    if (url.includes('/history?')) return response({ success: true, data: { labels, datasets: [{ data: labels.map((_, i) => (i === 0 ? '4.00' : '3.20')) }] } });
    return response({ success: true, data: { muhtemeller: info } });
  });
  return { result: await buildRaceAnalysis(date, key, raceNo), calls, program };
}

test('TJK Istanbul wall-clock labels parse to UTC; malformed times are rejected', () => {
  assert.equal(parseSourceTime('2026-10-10 10:43:35'), Date.parse('2026-10-10T07:43:35Z'));
  assert.equal(parseSourceTime('2026-10-08T18:00:00Z'), Date.parse('2026-10-08T18:00:00Z'));
  for (const bad of ['2026-02-30 10:00:00', '10:43', '', null, 1791618214, '2026-10-10 25:00:00']) assert.equal(parseSourceTime(bad), null);
});

test('freshness is verified only by recent real timestamps', () => {
  const now = Date.parse('2026-10-10T08:00:00Z');
  const heartbeat = istanbul(now - 10_000);
  assert.equal(quoteFreshness({ heartbeat, quoteTimes: [now - 60_000, now - 200_000], now }).status, 'VERIFIED');
  const cases = [
    [{ heartbeat, quoteTimes: [now - 60_000, now - FRESHNESS_LIMITS.quoteMaxAgeMs - 1] }, 'QUOTE_STALE'],
    [{ heartbeat, quoteTimes: [now - 60_000, NaN] }, 'QUOTE_TIMESTAMP_MISSING'],
    [{ heartbeat, quoteTimes: [] }, 'QUOTE_TIMESTAMP_MISSING'],
    [{ heartbeat, quoteTimes: [now + 10 * 60_000] }, 'SOURCE_CLOCK_SKEW'],
    [{ heartbeat: istanbul(now - 3_600_000), quoteTimes: [now - 1_000] }, 'SOURCE_HEARTBEAT_STALE'],
    [{ heartbeat: null, quoteTimes: [now - 1_000] }, 'SOURCE_HEARTBEAT_MISSING']
  ];
  for (const [input, reason] of cases) {
    const result = quoteFreshness({ ...input, now });
    assert.equal(result.status, 'UNVERIFIED');
    assert.deepEqual(result.reasons.slice(0, 1), ['SOURCE_FRESHNESS_UNVERIFIED']);
    assert.ok(result.reasons.includes(reason), `${reason}: ${result.reasons}`);
  }
});

test('rating 0 is unrated abroad, a real zero at home; empty is missing and never imputed', () => {
  assert.deepEqual(classifyRating(0, { foreign: true }), { rating: null, ratingStatus: 'unrated' });
  assert.deepEqual(classifyRating(0, { foreign: false }), { rating: 0, ratingStatus: 'zero' });
  assert.deepEqual(classifyRating(null), { rating: null, ratingStatus: 'missing' });
  assert.deepEqual(classifyRating(NaN), { rating: null, ratingStatus: 'invalid' });
  const field = [
    { number: 1, name: 'A', currentOdds: 2, openingOdds: 3, rating: 80, history: [{ odds: 3 }, { odds: 2 }] },
    { number: 2, name: 'B', currentOdds: 4, openingOdds: 4, rating: null, ratingStatus: 'unrated', history: [{ odds: 4 }, { odds: 4 }] }
  ];
  const result = analyzeRunners(field);
  assert.equal(result.status, 'PAS');
  assert.ok(result.reasonCodes.includes('RATING_UNRATED'));
  assert.equal(result.leader, null);
});

test('Belmont 5 and 6 produce predictions with fresh timestamps and keep their own runner sets', async (t) => {
  for (const [raceNo, active] of [[5, 8], [6, 8]]) {
    const { result, program } = await run(t, { raceNo });
    const expected = program.runners.filter((r) => !/Koşmaz/.test(r.rawName));
    assert.equal(result.race.number, raceNo);
    const field = result.analysis.status === 'OK' ? result.runners : result.observations.runners;
    const byNumber = [...field].sort((a, b) => a.number - b.number);
    assert.deepEqual(byNumber.map((r) => r.name), expected.map((r) => r.rawName));
    assert.equal(field.length, active);
    assert.equal(result.freshness.status, 'VERIFIED', JSON.stringify(result.freshness));
    assert.equal(result.analysis.reasonCodes.includes('SOURCE_FRESHNESS_UNVERIFIED'), false);
    if (expected.some((r) => r.rating === 0)) {
      assert.equal(result.analysis.status, 'PAS');
      assert.ok(result.analysis.reasonCodes.includes('RATING_UNRATED'));
    } else {
      assert.equal(result.analysis.status, 'OK', JSON.stringify(result.analysis.reasonCodes));
      assert.ok(result.analysis.picks.leader);
      assert.equal(result.analysis.agfComparison.usedInScore, false);
    }
    t.mock.restoreAll();
  }
});

test('stale history or stale heartbeat on the same race is PAS with explicit reasons', async (t) => {
  const stale = await run(t, { raceNo: 6, lastPointAgoMs: 30 * 60_000 });
  assert.equal(stale.result.analysis.status, 'PAS');
  assert.ok(stale.result.analysis.reasonCodes.includes('QUOTE_STALE'));
  assert.deepEqual(stale.result.runners, []);
  t.mock.restoreAll();
  const heartbeat = await run(t, { raceNo: 6, heartbeatAgoMs: 3_600_000 });
  assert.ok(heartbeat.result.analysis.reasonCodes.includes('SOURCE_HEARTBEAT_STALE'));
});

test('cache coalesces concurrent producers, never caches failures and stays bounded', async () => {
  const cache = createCache({ maxEntries: 2 });
  let calls = 0;
  const producer = async () => { calls += 1; await new Promise((r) => setTimeout(r, 20)); return calls; };
  const values = await Promise.all(Array.from({ length: 10 }, () => cache.get('k', 1_000, producer)));
  assert.equal(calls, 1);
  assert.ok(values.every((value) => value === 1));
  await assert.rejects(cache.get('bad', 1_000, async () => { throw new Error('x'); }));
  assert.equal(await cache.get('bad', 1_000, async () => 'ok'), 'ok');
  await cache.get('c', 1_000, async () => 3);
  assert.ok(cache.size <= 2);
});

test('rate limiter blocks after the per-window budget', () => {
  let now = 0;
  const check = createRateLimiter({ windowMs: 1_000, max: 2, now: () => now });
  assert.equal(check('a').allowed, true);
  assert.equal(check('a').allowed, true);
  assert.equal(check('a').allowed, false);
  assert.equal(check('b').allowed, true);
  now = 1_001;
  assert.equal(check('a').allowed, true);
});

function request(port, path) {
  return new Promise((resolve, reject) => {
    httpGet({ host: '127.0.0.1', port, path }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body }));
    }).on('error', reject);
  });
}

test('/healthz never calls TJK; /api is rate limited per client', async (t) => {
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('upstream must not be called'); });
  const server = startServer({ port: 0, host: '127.0.0.1', rateLimitPerMin: 2, quiet: true });
  await new Promise((resolve) => server.once('listening', resolve));
  const { port } = server.address();
  try {
    const health = await request(port, '/healthz');
    assert.equal(health.status, 200);
    assert.equal(JSON.parse(health.body).ok, true);
    assert.equal(globalThis.fetch.mock.callCount(), 0);
    const statuses = [];
    for (let i = 0; i < 3; i += 1) statuses.push((await request(port, '/api/nope')).status);
    assert.deepEqual(statuses, [404, 404, 429]);
    assert.equal((await request(port, '/healthz')).status, 200);
  } finally {
    server.close();
  }
});

test('backtest uses only points at or before the cutoff and wraps midnight post times', () => {
  const [late, afterMidnight] = postTimes('2026-10-08', ['23:31', '00:05']);
  assert.equal(afterMidnight - late, 34 * 60_000);
  assert.equal(new Date(afterMidnight).toISOString(), '2026-10-08T21:05:00.000Z');
  const cutoff = Date.parse('2026-10-09T11:25:00Z');
  const pts = pointsAtCutoff([{ label: '2026-10-09 14:20:00', odds: 5 }, { label: '2026-10-09 14:29:00', odds: 1.1 }], cutoff);
  assert.deepEqual(pts.map((p) => p.odds), [5]);

  const programRace = { number: 1, time: '14:30', type: 'Maiden', condition: '3 Yaşlı', distance: '1200m', surface: 'Çim',
    runners: [1, 2, 3].map((number) => ({ number, rawName: `AT${number}`, rating: 50 + number, agf: [{ percentage: 10 * number, rank: 4 - number }] })) };
  const history = (before, after) => [
    { label: '2026-10-09 14:00:00', odds: before[0] }, { label: '2026-10-09 14:22:00', odds: before[1] }, { label: '2026-10-09 14:28:00', odds: after }
  ];
  const base = (afterOdds) => predictAtCutoff({
    race: { time: '14:30', surface: 'Çim' }, programRace, runners: [
      { number: 1, name: 'AT1', history: history([2, 2], afterOdds[0]), program: programRace.runners[0] },
      { number: 2, name: 'AT2', history: history([4, 4], afterOdds[1]), program: programRace.runners[1] },
      { number: 3, name: 'AT3', history: history([9, 9], afterOdds[2]), program: programRace.runners[2] }
    ], cutoffMs: cutoff, inputSnapshotAt: '2026-10-09T11:00:00Z'
  });
  const a = base([2, 4, 9]);
  const b = base([30, 30, 1.2]);
  assert.equal(a.status, 'OK', a.reasonCodes.join());
  assert.deepEqual(b, a, 'post-cutoff odds cannot change the prediction');
  assert.equal(a.leader, 1);
  assert.equal(a.marketFavourite, 1);
  assert.equal(a.agfLeader, 3);
  assert.equal(base([2, 4, 9]).latestPointUsedAt, '2026-10-09T11:22:00.000Z');
});

test('results CSV winners parse and rates are withheld below the minimum sample', () => {
  const winners = resultsCsvWinners('X;;08/10/2026\n1. Kosu 20.10;M\n1;A\nGANYAN(2) :3,30 TL, SIRALI İKİLİ(2/4) :21,75 TL\n2. Kosu : X 20.43;M\n1. 6\'LI GANYAN(1/2/3) :1 TL, GANYAN(1) :2,20 TL\n');
  assert.deepEqual([...winners], [[1, [2]], [2, [1]]]);
  const records = Array.from({ length: 5 }, (_, i) => ({ prediction: { status: 'OK', reasonCodes: [], leader: 1, marketFavourite: 1, agfLeader: 2 }, resultCheck: 'CONFIRMED', winners: [i % 2 ? 1 : 2] }));
  const small = summarize(records, { minSample: 30 });
  assert.equal(small.validPredictions, 5);
  assert.equal(small.modelLeaderWin.rate, null);
  assert.equal(small.modelLeaderWin.note, 'INSUFFICIENT_SAMPLE');
  assert.equal(small.modelLeaderWin.hits, 2);
  const enough = summarize(records, { minSample: 5 });
  assert.equal(enough.modelLeaderWin.rate, 40);
  assert.equal(enough.baselines.agfLeaderWinComparisonOnly.rate, 60);
});


test('fresh history cannot verify a mismatching current price', async (t) => {
  const { result } = await run(t, { raceNo: 6, currentOdds: '99.00' });
  assert.equal(result.analysis.status, 'PAS');
  assert.equal(result.freshness.status, 'UNVERIFIED');
  assert.equal(result.freshness.valueBound, false);
  assert.ok(result.analysis.reasonCodes.includes('QUOTE_VALUE_MISMATCH'));
  assert.deepEqual(result.runners, []);
});
