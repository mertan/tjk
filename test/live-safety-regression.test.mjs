import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRaceAnalysis, parseProgramCsv, startServer } from '../server.mjs';
import { get } from 'node:http';
import { isProgramWithdrawn, raceGate } from '../race-gates.mjs';
import { parseOddsHistory, historyMetrics } from '../odds-history.mjs';
import { createV3SnapshotStore, prepareV3Quote, v3Envelope } from '../model-v3/live-context.mjs';

// Minimal reproduction of the user-reported Ankara 10 October runner.
// This is synthetic, NOT a downloaded official program or a result fixture.
const program = (day = '10/10/2026') => 'Ankara;(1. Yarış Günü);' + day + '\n'
  + '1. Kosu : 14.00;ŞARTLI 4;3 Yaşlı Araplar;57kg;1300m;Çim\n'
  + 'İkramiye\n1.)500.000 TL\n'
  + 'At No;At İsmi;Yaş;Orijin(Baba);Orijin(Anne);Kilo;Jokey Adı;Sahip Adı;Antrenör Adı;St;AGF;H;Son 6 Yarış;KGS;s20;EnİyiDerece\n'
  + [1, 2, 3, 4, 7].map((n) => n + ';' + (n === 7 ? 'ATLI FIRTINA KG (Koşmaz)' : 'TEST ' + n)
    + ';3y a e;BABA' + n + ';ANNE' + n + ';57;JOKEY;SAHİP;ANTRENÖR;' + n + ';;50;Ç1K2;12;18;1:25.10').join('\n');

test('10 Ekim Ankara 1: 7 ATLI FIRTINA (Koşmaz) parsed before feature building', () => {
  const [race] = parseProgramCsv(program());
  assert.equal(race.number, 1);
  assert.equal(race.prize1, 500000);
  assert.equal(race.runners.find((r) => r.number === 7).scratched, true);
  assert.equal(race.runners.find((r) => r.number === 7).programRow['At İsmi'], 'ATLI FIRTINA KG (Koşmaz)');
  assert.deepEqual(race.runners.filter((r) => !r.scratched).map((r) => r.number), [1, 2, 3, 4]);
  for (const rawName of ['AT (Koşmaz)', 'AT (KOŞMAZ)', 'AT ( Kosmaz )']) assert.equal(isProgramWithdrawn({ rawName }), true);
  assert.equal(isProgramWithdrawn({ rawName: 'KOŞMAZLIK' }), false);
});

const points = (prices) => prices.map((odds, i) => ({ odds, at: 1000 + i * 1000 }));

test('unconfirmed 241,7 opening cannot create a false contraction signal', () => {
  const result = historyMetrics(points([241.7, 3.2, 3.2]), 3.2);
  assert.equal(result.historyStatus, 'UNVERIFIED');
  assert.ok(result.historyReasonCodes.includes('ODDS_DISCONTINUITY'));
  assert.equal(result.openingOdds, null);
  assert.equal(result.movementPercent, null);
  assert.equal(result.shortMovementPercent, null);
  assert.equal(result.points[0].odds, 241.7, 'preserve evidence instead of correcting or discarding it');
  assert.equal(historyMetrics(points([241.7, 240, 239]), 239).historyStatus, 'VERIFIED',
    'large odds alone do not establish an error');
});

test('invalid, duplicated, unordered, unbound and incomplete histories fail closed', () => {
  const cases = [
    [{ odds: 4, at: 2000 }, { odds: 3.2, at: 1000 }],
    [{ odds: 4, at: 1000 }, { odds: 3.2, at: 1000 }],
    [{ odds: 4, at: null }, { odds: 3.2, at: 2000 }],
    [{ odds: 4, at: 1000 }, { odds: null, at: 1500 }, { odds: 3.2, at: 2000 }]
  ];
  for (const history of cases) {
    const result = historyMetrics(history, 3.2);
    assert.ok(result.historyReasonCodes.includes('QUOTE_HISTORY_INVALID'));
    assert.equal(result.movementPercent, null);
  }
  assert.ok(historyMetrics(points([4, 3.2]), 2).historyReasonCodes.includes('QUOTE_VALUE_MISMATCH'));
  assert.equal(historyMetrics(points([4]), 4).movementPercent, null);
  const payload = { success: true, data: { labels: ['2026-10-10 13:00:00', '2026-10-10 13:01:00'], datasets: [{ data: ['241,7', ''] }] } };
  assert.equal(parseOddsHistory(payload)[0].odds, 241.7);
  const wrongDay = parseOddsHistory(payload, { date: '2026-10-11' });
  assert.ok(historyMetrics(wrongDay, 3.2).historyReasonCodes.includes('QUOTE_HISTORY_DATE_MISMATCH'));
  assert.equal(parseOddsHistory(payload)[1].odds, null);
  payload.data.datasets[0].data.pop();
  assert.deepEqual(parseOddsHistory(payload), [], 'length mismatch must not be truncated');
  payload.data.datasets.push({ data: [] });
  assert.deepEqual(parseOddsHistory(payload), [], 'ambiguous dataset must not default to the first');
});

let sequence = 0;
async function source(t, { conflict = false, anomaly = false, mismatch = false, missing = false,
  stale = false, v3 = false } = {}) {
  const key = 'SAFETY' + (++sequence);
  const date = '2026-10-10';
  const now = Date.parse('2026-10-10T13:55:01+03:00');
  t.mock.method(Date, 'now', () => now);
  t.mock.method(globalThis, 'fetch', async (input) => {
    const url = String(input);
    const json = (data) => new Response(JSON.stringify(data));
    if (url.endsWith('checksum.json')) return json({ success: true, day: key,
      datetime: new Date(now).toISOString(), runs: { [key + '-1']: ['safe'] } });
    if (url.includes('/day-')) return json({ success: true, data: { yarislar: [{
      KEY: key, YER: 'Ankara', HIPODROM: 'Ankara',
      kosular: [{ NO: 1, SAAT: '14:00', PIST: 'Çim', DURUM: 'AÇIK' }],
      atlar: { 1: { 1: 'TEST 1', 2: 'TEST 2', 3: 'TEST 3', 4: 'TEST 4', 7: 'ATLI FIRTINA' } }
    }] } });
    if (url.endsWith('.csv')) return new Response(program());
    if (url.includes('/history?')) {
      assert.notEqual(new URL(url).searchParams.get('horse'), '7', 'withdrawn runner must not request history');
      const age = stale ? 3600000 : 0;
      return json({ success: true, data: {
        labels: [120000, 60000, 1000].map((ago) => new Date(now - ago - age).toISOString()),
        datasets: [{ data: [anomaly ? '241,7' : '4', '3,2', '3,2'] }]
      } });
    }
    return json({ success: true, data: { muhtemeller: { SAAT: '14:00', PIST: 'Çim', DURUM: 'AÇIK',
      bahisler: [{ B: 'GANYAN', muhtemeller: [1, 2, 3, 4, 7].map((n) => ({
        S1: String(n), G: missing && n === 1 ? '' : mismatch ? '2' : '3,2',
        KOSMAZ: n === 7 && !conflict
      })) }] } } });
  });
  return buildRaceAnalysis(date, key, 1, { v3, snapshots: createV3SnapshotStore() });
}
const noPicks = (result) => {
  assert.equal(result.analysis.status, 'PAS', result.analysis.reasonCodes.join(','));
  assert.deepEqual(result.runners, []);
  assert.ok(Object.values(result.analysis.picks).every((pick) => pick === null));
};

test('official scratch is excluded and feed conflict still forces entire race PAS', async (t) => {
  const ok = await source(t);
  assert.equal(ok.analysis.status, 'OK', ok.analysis.reasonCodes.join(','));
  assert.equal(ok.runners.some((r) => r.number === 7), false);
  t.mock.restoreAll();
  const conflict = await source(t, { conflict: true });
  noPicks(conflict);
  assert.ok(conflict.analysis.reasonCodes.includes('SCRATCH_STATUS_CONFLICT'));
  assert.equal(conflict.observations.runners.some((r) => r.number === 7), false);
});

test('API preserves PAS for discontinuity, source mismatch, missing odds and stale data', async (t) => {
  for (const [options, reason] of [
    [{ anomaly: true }, 'ODDS_DISCONTINUITY'],
    [{ mismatch: true }, 'QUOTE_VALUE_MISMATCH'],
    [{ missing: true }, 'INVALID_CURRENT_ODDS'],
    [{ stale: true }, 'QUOTE_STALE']
  ]) {
    const result = await source(t, options);
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes(reason), result.analysis.reasonCodes.join(','));
    if (options.anomaly || options.mismatch) assert.ok(result.observations.runners.every((r) => r.movementPercent === null));
    t.mock.restoreAll();
  }
});

test('V3 API envelope never falls back to the heuristic model when artifact is unavailable', async (t) => {
  const result = await source(t, { v3: true });
  noPicks(result);
  assert.equal(result.analysis.modelVersion, 'tjk-v3');
  assert.equal(result.analysis.confidence, null);
});

test('V3 opt-in cannot bind to the 4199 production port', () => {
  assert.throws(() => startServer({ port: 4199, enableV3: true }), /4200/);
});

test('T-5 program provenance rejects late starts and subsequent program changes', () => {
  const [race] = parseProgramCsv(program());
  const post = Date.parse('2026-10-10T14:00:00+03:00');
  const cutoff = post - 300000;
  const snapshots = createV3SnapshotStore();
  const input = { date: '2026-10-10', venueKey: 'ANKARA', programRace: race,
    venueRaces: [{ NO: 1, SAAT: '14:00' }], runners: [], snapshots, trusted: true };
  const before = prepareV3Quote({ ...input, now: cutoff - 1000 });
  assert.deepEqual(before.reasonCodes, ['V3_CUTOFF_NOT_REACHED']);
  assert.deepEqual(prepareV3Quote({ ...input, now: cutoff + 1000 }).reasonCodes, []);
  assert.ok(prepareV3Quote({ ...input, snapshots: createV3SnapshotStore(), now: cutoff + 1000 })
    .reasonCodes.includes('V3_PRE_CUTOFF_PROGRAM_MISSING'));
  race.runners[0].weight = '60';
  assert.ok(prepareV3Quote({ ...input, now: cutoff + 1000 })
    .reasonCodes.includes('V3_PROGRAM_CHANGED_AFTER_CUTOFF'));
  assert.ok(prepareV3Quote({ ...input, now: post }).reasonCodes.includes('V3_RACE_STARTED'));
});

test('V3 envelope strips all stale heuristic picks and probabilities on PAS', () => {
  const base = { analysis: { status: 'OK', reasonCodes: [], picks: { leader: { number: 1 } } },
    runners: [{ number: 1, name: 'TEST', modelProbability: 99, modelRank: 1, edge: 90 }] };
  const result = v3Envelope(base, { status: 'PAS', reasonCodes: ['V3_MODEL_MISSING'], runners: [] });
  noPicks(result);
  assert.equal(result.observations.runners[0].modelProbability, undefined);
  assert.equal(result.observations.runners[0].edge, undefined);
});

test('both directions of scratch disagreement and quote metadata mismatch force PAS', () => {
  const [programRace] = parseProgramCsv(program());
  const now = Date.parse('2026-10-10T10:55:00Z');
  const rows = programRace.runners.map((r) => ({ S1: String(r.number), KOSMAZ: r.scratched }));
  const info = { SAAT: programRace.time, PIST: programRace.surface, DURUM: 'AÇIK',
    bahisler: [{ B: 'GANYAN', muhtemeller: rows }] };
  const feed = { checksum: { datetime: new Date(now).toISOString() },
    race: { SAAT: programRace.time, PIST: programRace.surface, DURUM: 'AÇIK' },
    racePayload: { data: { muhtemeller: info } } };
  const reasons = () => raceGate(feed, programRace, { now, quoteTimes: [now - 1000] }).reasons;
  assert.deepEqual(reasons(), []);
  rows[0].KOSMAZ = true;
  assert.ok(reasons().includes('SCRATCH_STATUS_CONFLICT'));
  rows[0].KOSMAZ = false;
  info.SAAT = '15:00';
  assert.ok(reasons().includes('RACE_TIME_MISMATCH'));
  info.SAAT = programRace.time;
  info.PIST = 'Kum';
  assert.ok(reasons().includes('RACE_SURFACE_MISMATCH'));
});

test('V3 endpoint is disabled by default and configuration is explicit', async () => {
  const server = startServer({ port: 0, host: '127.0.0.1', enableV3: false, quiet: true });
  await new Promise((resolve) => server.once('listening', resolve));
  const request = (path) => new Promise((resolve, reject) => {
    get({ host: '127.0.0.1', port: server.address().port, path }, (res) => {
      let body = '';
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(body) }));
    }).on('error', reject);
  });
  try {
    const config = await request('/api/config');
    assert.equal(config.status, 200);
    assert.equal(config.data.v3Enabled, false);
    assert.equal((await request('/api/v3/race?date=2026-10-10&venue=ANKARA&race=1')).status, 404);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
