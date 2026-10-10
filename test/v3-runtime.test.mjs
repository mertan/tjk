import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { liveRaceFromProgram, analyzeLiveV3 } from '../model-v3/live-runtime.mjs';

const row = (number, name = 'TEST') => ({
  'At No': String(number), 'At İsmi': name, 'Yaş': '4y d a',
  'Orijin(Baba)': 'BABA', 'Orijin(Anne)': 'ANNE', Kilo: '55,5 + 1,5',
  'Jokey Adı': 'JOKEY AP', 'Antrenör Adı': 'ANTRENÖR', St: '3', H: '',
  'Son 6 Yarış': 'K1Ç2K3', KGS: '', s20: '17', EnİyiDerece: '1.24.50', AGF: '%17.5(2)'
});
const programRace = () => ({
  number: 1, time: '14:00', distance: '1400m', surface: 'Kum', prize1: 100000,
  runners: [{ number: 7, programRow: row(7, 'ATLI FIRTINA (Koşmaz)') }, { number: 2, programRow: row(2) }]
});
const input = () => ({ programRace: programRace(), date: '2026-10-10', venueKey: 'ANKARA' });

test('official program fields retain the training parser semantics and missing flags', () => {
  const race = liveRaceFromProgram(input());
  assert.equal(race.distance, 1400);
  assert.equal(race.prize1, 100000);
  assert.deepEqual(race.runners.map((runner) => runner.number), [7, 2]);
  const runner = race.runners[0];
  assert.equal(runner.scratched, true);
  assert.equal(runner.key, 'BABA|ANNE|2022');
  assert.equal(runner.weight, 55.5);
  assert.equal(runner.extraWeight, 1.5);
  assert.equal(runner.rating, null);
  assert.equal(runner.daysOff, null);
  assert.equal(runner.last6, 'K1Ç2K3');
  assert.equal(runner.bestTime, 84.5);
});

test('normalization does not clear official withdrawal flags or guess header rows', () => {
  const data = input();
  data.programRace.runners[1].scratched = true;
  assert.equal(liveRaceFromProgram(data).runners[1].scratched, true);
  delete data.programRace.runners[1].programRow;
  assert.throws(() => liveRaceFromProgram(data), /V3_PROGRAM_INVALID/);
});

test('normalization rejects ambiguous identity and invalid race metadata', () => {
  const data = input();
  data.programRace.runners[1].number = 3;
  assert.throws(() => liveRaceFromProgram(data), /V3_PROGRAM_INVALID/);
  assert.throws(() => liveRaceFromProgram({ ...input(), date: '2026-02-30' }), /V3_PROGRAM_INVALID/);
  assert.throws(() => liveRaceFromProgram({ ...input(), programRace: { ...programRace(), distance: '1400oops' } }), /V3_PROGRAM_INVALID/);
});

test('upstream PAS and missing or corrupt private files never fall back to predictions', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'tjk-v3-runtime-'));
  try {
    const modelPath = join(directory, 'model.json');
    const historyPath = join(directory, 'history.json');
    const data = { ...input(), modelPath, historyPath };
    assert.deepEqual((await analyzeLiveV3({ ...data, reasonCodes: ['STALE_SOURCE'] })).reasonCodes, ['STALE_SOURCE']);
    assert.deepEqual((await analyzeLiveV3(data)).reasonCodes, ['V3_MODEL_UNAVAILABLE']);
    await writeFile(modelPath, '{}');
    assert.deepEqual((await analyzeLiveV3(data)).reasonCodes, ['V3_HISTORY_UNAVAILABLE']);
    await writeFile(historyPath, 'not json');
    assert.deepEqual((await analyzeLiveV3(data)).reasonCodes, ['V3_HISTORY_UNAVAILABLE']);
    await writeFile(historyPath, '{"races":[]}');
    const result = await analyzeLiveV3(data);
    assert.equal(result.status, 'PAS');
    assert.deepEqual(result.runners, []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
