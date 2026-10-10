import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { buildRaceAnalysis, parseProgramCsv } from '../server.mjs';
import { raceDataIssues } from '../race-gates.mjs';

const csv = await readFile(new URL('./fixtures/2026-10-08-belmont-park-program.csv', import.meta.url), 'utf8');
const programs = parseProgramCsv(csv);
let sequence = 0;

async function run(t, { raceNo = 6, csvBody = csv, modify = () => {}, modifyRace = () => {}, failure = false, bodyFailure = null } = {}) {
  const key = `TEST${++sequence}`;
  const day = String(sequence).padStart(2, '0');
  const date = `2026-10-${day}`;
  const program = programs.find((r) => r.number === raceNo);
  const rows = program.runners.map((r) => ({ S1: String(r.number), G: '3.20', KOSMAZ: /Koşmaz/.test(r.rawName) }));
  const info = { SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK', timestamp: Date.now(), bahisler: [{ B: 'GANYAN', muhtemeller: rows }] };
  modify(info);
  const race = { NO: raceNo, SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK' };
  modifyRace(race);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (raw) => {
    const url = String(raw); calls.push(url);
    if (failure) throw new Error('synthetic network failure');
    if (bodyFailure && url.includes(bodyFailure)) return {
      ok: true, status: 200, text: async () => { throw new Error('synthetic body read failure'); }
    };
    const response = (data, status = 200) => new Response(JSON.stringify(data), { status });
    if (url.endsWith('checksum.json')) return response({ success: true, day: key, datetime: new Date().toISOString(), runs: { [`${key}-${raceNo}`]: ['hash'] } });
    if (url.includes('/day-')) return response({ success: true, data: { yarislar: [{ KEY: key, YER: 'Belmont Park ABD', HIPODROM: 'Belmont Park', kosular: [race], atlar: { [raceNo]: Object.fromEntries(program.runners.map((r) => [r.number, r.rawName])) } }] } });
    if (url.endsWith('.csv')) return new Response((csvBody || '').replace('08/10/2026', `${day}/10/2026`), { status: csvBody === null ? 404 : 200 });
    if (url.includes('/history?')) return response({ success: true, data: { labels: Array.from({ length: 45 }, (_, i) => `${date}T18:${String(i).padStart(2, '0')}:00Z`), datasets: [{ data: Array.from({ length: 45 }, (_, i) => i === 0 ? 4 : 3.2) }] } });
    return response({ success: true, data: { muhtemeller: info } });
  });
  const result = await buildRaceAnalysis(date, key, raceNo);
  return { result, calls };
}

function noPicks(result) {
  assert.equal(result.analysis.status, 'PAS');
  assert.deepEqual(result.runners, [], 'legacy Python consumer must receive no candidate field');
  assert.equal(result.analysis.confidence, 0);
  assert.ok(Object.values(result.analysis.picks).every((pick) => pick === null));
}

test('Belmont API uses the official filename and keeps races 5 and 6 separate', async (t) => {
  for (const raceNo of [5, 6]) {
    const { result, calls } = await run(t, { raceNo });
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes('SOURCE_FRESHNESS_UNVERIFIED'), JSON.stringify({ reasons: result.analysis.reasonCodes, calls }));
    assert.ok(calls.some((url) => url.endsWith(`/${result.date.slice(-2)}.10.2026-BelmontParkABD-GunlukYarisProgrami-TR.csv`)));
    assert.equal(result.race.number, raceNo);
    const expected = programs.find((r) => r.number === raceNo).runners.filter((r) => !/Koşmaz/.test(r.rawName));
    assert.deepEqual(result.observations.runners.map((r) => r.name), expected.map((r) => r.rawName));
    assert.equal(result.observations.runners[0].history.length, 45);
    assert.equal(result.observations.runners[0].openingOdds, 4);
    assert.equal(result.analysis.reasonCodes.includes('HISTORY_OPENING_MISMATCH'), false);
    t.mock.restoreAll();
  }
});

test('missing program and invalid active odds produce PAS without silently dropping a horse', async (t) => {
  const missing = await run(t, { csvBody: null });
  noPicks(missing.result);
  assert.ok(missing.result.analysis.reasonCodes.includes('PROGRAM_UNAVAILABLE'));
  t.mock.restoreAll();
  const bad = await run(t, { modify: (info) => { info.bahisler[0].muhtemeller[0].G = ''; } });
  noPicks(bad.result);
  assert.equal(bad.result.observations.runners.length, 8);
  assert.ok(bad.result.analysis.reasonCodes.includes('INVALID_CURRENT_ODDS'));
});

test('official results and incomplete participant fields cannot become predictions', async (t) => {
  const ended = await run(t, { modify: (info) => { info.DURUM = 'RESMİ'; } });
  noPicks(ended.result);
  assert.ok(ended.result.analysis.reasonCodes.includes('RACE_NOT_OPEN'));
  t.mock.restoreAll();
  const partial = await run(t, { modify: (info) => { info.bahisler[0].muhtemeller.pop(); } });
  assert.ok(partial.result.analysis.reasonCodes.includes('RUNNER_SET_MISMATCH'));
  noPicks(partial.result);
});

test('upstream network failures are explicit PAS while invalid user arguments stay errors', async (t) => {
  const { result } = await run(t, { failure: true });
  noPicks(result);
  assert.ok(result.analysis.reasonCodes.includes('SOURCE_UNAVAILABLE'));
  await assert.rejects(buildRaceAnalysis('2026-02-30', 'BELMONT', 6), { status: 400 });
});

test('response-body failures stay PAS for mandatory and optional sources', async (t) => {
  for (const [bodyFailure, reason] of [
    ['/checksum.json', 'SOURCE_UNAVAILABLE'],
    ['/CSV/', 'PROGRAM_UNAVAILABLE'],
    ['/history?', 'INSUFFICIENT_HISTORY']
  ]) {
    const { result } = await run(t, { bodyFailure });
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes(reason));
    assert.equal(JSON.stringify(result).includes('synthetic body read failure'), false);
    t.mock.restoreAll();
  }
});

test('malformed race clocks fail closed without throwing a source TypeError', async (t) => {
  for (const time of [1914, {}, ['19:14']]) {
    const { result } = await run(t, { modifyRace: (race) => { race.SAAT = time; } });
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes('INVALID_RACE_TIME'));
    t.mock.restoreAll();
  }
});

test('malformed source payloads are source failures, not user errors or server crashes', async (t) => {
  for (const modify of [
    (info) => { info.bahisler = {}; },
    (info) => { info.bahisler = [null]; },
    (info) => { info.bahisler[0].muhtemeller = [null]; },
    (info) => { info.bahisler[0].muhtemeller[0].S1 = 'bad'; },
    (info) => { info.bahisler.push({ B: 'İKİLİ', muhtemeller: {} }); }
  ]) {
    const { result } = await run(t, { modify });
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes('SOURCE_UNAVAILABLE'));
    t.mock.restoreAll();
  }
});

test('wrong program date/place is unavailable rather than lending another race its ratings', async (t) => {
  for (const csvBody of [csv.replace('08/10/2026', '07/09/2000'), csv.replace('Belmont Park ABD;', 'Ankara;')]) {
    const { result } = await run(t, { csvBody });
    noPicks(result);
    assert.ok(result.analysis.reasonCodes.includes('PROGRAM_UNAVAILABLE'));
    t.mock.restoreAll();
  }
});

test('schedule timestamps and fresh day heartbeats never certify race quote freshness', () => {
  const program = programs[5];
  for (const timestamp of [1791489420, Date.now(), new Date().toISOString(), null]) {
    const feed = { race: { SAAT: program.time, PIST: program.surface, DURUM: 'AÇIK' }, checksum: { datetime: new Date().toISOString() }, racePayload: { data: { muhtemeller: { DURUM: 'AÇIK', timestamp, bahisler: [{ B: 'GANYAN', muhtemeller: program.runners.map((r) => ({ S1: r.number, KOSMAZ: false })) }] } } } };
    assert.ok(raceDataIssues(feed, program).includes('SOURCE_FRESHNESS_UNVERIFIED'));
  }
});
