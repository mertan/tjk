import test from 'node:test';
import assert from 'node:assert/strict';
import { get } from 'node:http';
import { spawnSync } from 'node:child_process';
import { startServer } from '../server.mjs';
import { predictAtCutoff, summarize, postTimes } from '../backtest.mjs';

test('archive inputs cannot create predictions without pre-cutoff snapshot provenance', () => {
  const programRace = { number: 1, time: '14:30', surface: 'Çim', type: 'Maiden', condition: '3 Yaşlı', distance: '1200m', runners: [1, 2, 3].map(number => ({ number, rawName: `AT${number}`, rating: 50 })) };
  const runners = programRace.runners.map(program => ({ number: program.number, name: program.rawName, program, out: program.number === 3, history: [{ label: '2026-10-09 14:20:00', odds: program.number + 1 }, { label: '2026-10-09 14:22:00', odds: program.number + 1 }] }));
  for (const inputSnapshotAt of [null, 'bad', '2026-10-09T12:00:00Z']) {
    const result = predictAtCutoff({ race: { time: '14:30', surface: 'Çim' }, programRace, runners, cutoffMs: Date.parse('2026-10-09T11:25:00Z'), inputSnapshotAt });
    assert.equal(result.status, 'PAS');
    assert.deepEqual(result.reasonCodes, ['PRE_CUTOFF_INPUT_SNAPSHOT_UNVERIFIED']);
    assert.equal(result.leader, null);
  }
});

test('payload-only and conflicting results never enter the success denominator', () => {
  const row = { prediction: { status: 'OK', leader: 1, marketFavourite: 1, agfLeader: null }, winners: [1] };
  const result = summarize(['CONFIRMED', 'PAYLOAD_ONLY', 'CONFLICT', undefined].map(resultCheck => ({ ...row, resultCheck })), { minSample: 1 });
  assert.equal(result.validPredictions, 1);
  assert.equal(result.settledRaces, 1);
  assert.equal(result.unsettledRaces, 3);
  for (const minSample of [0, -1, NaN, Infinity, 1.5]) assert.throws(() => summarize([], { minSample }), /INVALID_MIN_SAMPLE/);
  assert.deepEqual(postTimes('2026-10-09', ['25:00', '12:99']), [null, null]);
});

test('invalid backtest options fail before any fetch or cache creation', () => {
  for (const args of [['--cutoff-min', '-5'], ['--cutoff-min', 'NaN'], ['--cutoff-min', 'Infinity'], ['--min-sample', '0'], ['--min-sample', '1.5'], ['--concurrency', '0'], ['--interval-ms', '-1'], ['--from', '2026-02-30']]) {
    const result = spawnSync(process.execPath, ['--import', 'data:text/javascript,globalThis.fetch=async()=>new Response(JSON.stringify({success:false}))', 'scripts/backtest.mjs', '--from', '2026-01-01', ...args], { encoding: 'utf8', timeout: 2000 });
    assert.equal(result.status, 2, result.stderr);
    assert.match(result.stderr, /INVALID_BACKTEST_ARGUMENTS/);
  }
});

test('spoofed X-Forwarded-For cannot reset the peer request budget', async () => {
  const server = startServer({ port: 0, host: '127.0.0.1', rateLimitPerMin: 1, trustProxy: true, quiet: true });
  await new Promise(resolve => server.once('listening', resolve));
  const request = forwarded => new Promise((resolve, reject) => {
    get({ host: '127.0.0.1', port: server.address().port, path: '/api/nope', headers: { 'x-forwarded-for': forwarded } }, res => {
      res.resume(); res.on('end', () => resolve(res.statusCode));
    }).on('error', reject);
  });
  try {
    assert.equal(await request('198.51.100.1'), 404);
    assert.equal(await request('198.51.100.2'), 429);
  } finally { await new Promise(resolve => server.close(resolve)); }
});
