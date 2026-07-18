import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeRunners, parseProgramCsv } from '../server.mjs';

test('TJK CSV programını koşu ve atlara ayırır', () => {
  const csv = `Ankara;(1. Yarış Günü);18/07/2026
1. Kosu :  ÖRNEK KOŞUSU 14.00;ŞARTLI 4; 3 Yaşlı Araplar; 57.00kg; 1300m; Çim
At No;At İsmi;Yaş;Orijin(Baba);Orijin(Anne);Kilo;Jokey Adı;Sahip Adı;Antrenör Adı;St;AGF;H;Son 6 Yarış;KGS;s20;EnİyiDerece
1;DENEME ATI KG;3y a e;BABA;ANNE;57;A.JOKEY;A.SAHİP;A.ANT;2;%31.2(1)  %33.4(1);52;Ç1K2;12;18;1:25.10`;
  const races = parseProgramCsv(csv);
  assert.equal(races.length, 1);
  assert.equal(races[0].number, 1);
  assert.equal(races[0].name, 'ÖRNEK KOŞUSU');
  assert.equal(races[0].surface, 'Çim');
  assert.equal(races[0].runners[0].jockey, 'A.JOKEY');
  assert.deepEqual(races[0].runners[0].agf, [
    { percentage: 31.2, rank: 1 },
    { percentage: 33.4, rank: 1 }
  ]);
});

test('analiz motoru favori, değer ve hareket sinyali üretir', () => {
  const result = analyzeRunners([
    { number: 1, name: 'BİR', currentOdds: 2, openingOdds: 3, movementPercent: -33.3, agfLatest: 42, rating: 80, history: [{ odds: 3 }, { odds: 2 }] },
    { number: 2, name: 'İKİ', currentOdds: 3.5, openingOdds: 3.2, movementPercent: 9.4, agfLatest: 30, rating: 76, history: [{ odds: 3.2 }, { odds: 3.5 }] },
    { number: 3, name: 'ÜÇ', currentOdds: 7, openingOdds: 9, movementPercent: -22.2, agfLatest: 18, rating: 70, history: [{ odds: 9 }, { odds: 7 }] }
  ]);
  assert.equal(result.leader.number, 1);
  assert.equal(result.leader.supportSignal, 'Güçlü destek');
  assert.ok(result.confidence >= 38 && result.confidence <= 86);
  assert.ok(Math.abs(result.runners.reduce((sum, runner) => sum + runner.modelProbability, 0) - 100) < 0.2);
});
