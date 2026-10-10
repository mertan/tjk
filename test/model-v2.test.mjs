import test from 'node:test';
import assert from 'node:assert/strict';
import { joinResults, parseRaceTables } from '../model-v2/dataset.mjs';
import { buildExamples, FEATURE_NAMES, inferFormOrder, parseLast6 } from '../model-v2/features.mjs';
import { predict, train } from '../model-v2/model.mjs';

const HEAD = 'At No;At İsmi;Yaş;Orijin(Baba);Orijin(Anne);Kilo;Jokey Adı;Sahip Adı;Antrenör Adı;St;AGF;H';
const program = [
  'Ankara;(1. Yarış Günü);01/10/2026',
  '1. Kosu :   13.00;Maiden; 3 Yaşlı İngilizler; 57kg; 1400m; Kum;;;;',
  'İkramiye;;;;;',
  '1.)400.000 TL;2.)160.000 TL;',
  `${HEAD};Son 6 Yarış;KGS;s20;EnİyiDerece`,
  '1;ALFA KG;3y d  e;BABA1;ANNE1;57 +1.50;A.JOKEY AP;S;T.ONE;2;%40(1);60;K1K2;14;10;1.25.10',
  '2;BETA;3y a  d;BABA2;ANNE2;55;B.JOKEY;S;T.TWO;1;%30(2);;K5;30;5;',
  '3;GAMA (Koşmaz);3y d  e;BABA3;ANNE3;56;C.JOKEY;S;T.TWO;;%0(9);50;;;;'
].join('\n');
const results = [
  'Ankara;(1. Yarış Günü);01/10/2026',
  '1. Kosu :   13.00;Maiden; 3 Yaşlı İngilizler; 57kg; 1400m; Kum;;;;',
  `${HEAD};Derece;Ganyan;Fark`,
  '1;BETA;3y a  d;BABA2;ANNE2;55;B.JOKEY;S;T.TWO;1;%30(2);;1.26.00;3,10;Boyun',
  '2;ALFA SK;3y d  e;BABA1;ANNE1;57 +1.50;A.JOKEY AP;S;T.ONE;2;%40(1);60;;1,90;',
  'GANYAN(2) :3,10 TL'
].join('\n');

test('results CSV row order is the finish order; joins by sire|dam|birth year despite name suffix changes', () => {
  const [race] = parseRaceTables(program);
  assert.equal(race.distance, 1400);
  assert.equal(race.prize1, 400000);
  const { runners, matched } = joinResults(race, parseRaceTables(results)[0], 2026);
  assert.equal(matched, 2);
  const byName = Object.fromEntries(runners.map((r) => [r.number, r]));
  assert.equal(byName[2].position, 1);
  assert.equal(byName[1].position, null, 'no finish time = not placed');
  assert.equal(byName[1].weight, 57);
  assert.equal(byName[1].extraWeight, 1.5);
  assert.equal(byName[2].rating, null);
  assert.equal(byName[3].scratched, true);
});

test('Son 6 Yarış tokens: 0 means 10th or worse; order is configurable', () => {
  assert.deepEqual(parseLast6('K5Ç0-S1').map((r) => [r.surface, r.position]), [['Kum', 5], ['Çim', 10], ['Sentetik', 1]]);
  assert.equal(parseLast6('K5S1', false)[0].position, 1);
});

function race(date, number, runners) {
  return { date, venue: 'ANKARA', number, time: '13:00', type: 'Maiden', distance: 1400, surface: 'Kum', prize1: 400000, runners };
}
function runner(n, extra = {}) {
  return { number: n, key: `H${n}`, rating: 50 + n, weight: 56, extraWeight: 0, stall: n, age: 4, daysOff: 20, bestTime: 85, last6: 'K3K2', jockey: `J${n}`, trainer: `T${n}`, scratched: false, agf: 10 * n, closingOdds: 10 - n, position: n, ...extra };
}

test('features use only earlier days; same-day and future results, AGF and odds never change X', () => {
  const day1 = race('2026-10-01', 1, [1, 2, 3, 4].map((n) => runner(n)));
  const day2 = race('2026-10-02', 1, [1, 2, 3, 4].map((n) => runner(n)));
  const base = buildExamples([day1, day2]);
  const leaked = buildExamples([
    race('2026-10-01', 1, [1, 2, 3, 4].map((n) => runner(n, { position: 5 - n, agf: 99 - n, closingOdds: n }))),
    day2,
    race('2026-10-03', 1, [1, 2, 3, 4].map((n) => runner(n, { position: 5 - n })))
  ]);
  assert.deepEqual(leaked[0].X, base[0].X, 'day-1 features ignore day-1 results, AGF and odds');
  assert.notDeepEqual(leaked[1].X, base[1].X, 'day-2 features do learn from day-1 results');
  assert.deepEqual(buildExamples([day1, day2])[1].X, base[1].X, 'future days do not change past features');
});

test('missing values are flagged, not imputed; scratched runners are excluded', () => {
  const [example] = buildExamples([race('2026-10-01', 1, [runner(1, { rating: null }), runner(2), runner(3), runner(4), runner(5, { scratched: true })])]);
  assert.deepEqual(example.numbers, [1, 2, 3, 4]);
  const r = FEATURE_NAMES.indexOf('rating');
  const flag = FEATURE_NAMES.indexOf('ratingMissing');
  assert.equal(example.X[0][r], 0);
  assert.equal(example.X[0][flag], 1);
  assert.equal(example.X[1][flag], 0);
});

test('conditional logit recovers a planted signal', () => {
  const races = Array.from({ length: 60 }, (_, d) => race(`2026-01-${String((d % 28) + 1).padStart(2, '0')}`, d, [1, 2, 3, 4, 5].map((n) => {
    const rating = ((n * 7 + d * 3) % 5) * 10 + 40;
    return runner(n, { rating, key: `H${d}-${n}`, jockey: 'J', trainer: 'T', last6: '', position: null });
  })));
  for (const r of races) {
    const best = [...r.runners].sort((a, b) => b.rating - a.rating)[0];
    r.runners.forEach((x) => { x.position = x === best ? 1 : 2; });
  }
  const examples = buildExamples(races);
  const weights = train(examples, { l2: 0.1, epochs: 300 });
  assert.ok(weights[FEATURE_NAMES.indexOf('rating')] > 1);
  const hits = examples.filter((e) => e.positions[predict(weights, e).leaderIndex] === 1).length;
  assert.equal(hits, examples.length);
});

test('form order is inferred from the dataset', () => {
  const r1 = race('2026-10-01', 1, [runner(1, { key: 'X', position: 4, last6: '' })]);
  const r2 = race('2026-10-08', 1, [runner(1, { key: 'X', last6: 'K1K4', position: 2 })]);
  assert.equal(inferFormOrder([r1, r2]).newestLast, true);
  r2.runners[0].last6 = 'K4K1';
  assert.equal(inferFormOrder([r1, r2]).newestLast, false);
});
