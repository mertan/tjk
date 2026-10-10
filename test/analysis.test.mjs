import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeRunners } from '../analysis.mjs';

function field() {
  return [
    { number: 1, name: 'BİR', currentOdds: 2, openingOdds: 3, movementPercent: -33.3, agfLatest: 42, rating: 80, history: [{ odds: 3 }, { odds: 2 }] },
    { number: 2, name: 'İKİ', currentOdds: 3.5, openingOdds: 3.2, movementPercent: 9.4, agfLatest: 30, rating: 76, history: [{ odds: 3.2 }, { odds: 3.5 }] },
    { number: 3, name: 'ÜÇ', currentOdds: 7, openingOdds: 9, movementPercent: -22.2, agfLatest: 18, rating: 70, history: [{ odds: 9 }, { odds: 7 }] }
  ];
}

function decisions(analysis) {
  return {
    status: analysis.status,
    reasonCodes: analysis.reasonCodes,
    scores: analysis.runners.map(({ number, marketProbability, modelProbability, modelRank, edge, movementPercent, supportSignal }) =>
      ({ number, marketProbability, modelProbability, modelRank, edge, movementPercent, supportSignal })),
    picks: ['leader', 'oddsLeader', 'agfLeader', 'steam', 'value', 'surprise'].map((key) => analysis[key]?.number ?? null),
    confidence: analysis.confidence,
    confidenceLabel: analysis.confidenceLabel,
    agreement: analysis.agreement,
    summary: analysis.summary
  };
}

function expectPass(analysis, reason) {
  assert.equal(analysis.status, 'PAS');
  assert.ok(analysis.reasonCodes.includes(reason), analysis.reasonCodes.join(', '));
  assert.equal(analysis.confidence, 0);
  assert.equal(analysis.agreement, false);
  for (const key of ['leader', 'oddsLeader', 'agfLeader', 'steam', 'value', 'surprise']) assert.equal(analysis[key], null, key);
  for (const runner of analysis.runners) {
    for (const key of ['marketProbability', 'modelProbability', 'modelRank', 'modelScore', 'edge', 'supportSignal']) {
      assert.equal(Object.hasOwn(runner, key), false, key);
    }
  }
}

test('complete field produces compatible scores and actual-history support', () => {
  const runners = field();
  const before = structuredClone(runners);
  const result = analyzeRunners(runners);
  assert.equal(result.status, 'OK');
  assert.equal(result.leader.number, 1);
  assert.equal(result.leader.supportSignal, 'Güçlü destek');
  assert.equal(result.agfLeader, null);
  assert.equal(result.scoreKind, 'uncalibrated_heuristic');
  assert.ok(result.confidence >= 38 && result.confidence <= 86);
  assert.ok(Math.abs(result.runners.reduce((sum, runner) => sum + runner.modelProbability, 0) - 100) < 0.2);
  assert.deepEqual(runners, before, 'analysis does not mutate inputs');
});

test('AGF values, ranks, availability and malformed numbers cannot change any decision', () => {
  const expected = decisions(analyzeRunners(field()));
  for (const value of [null, undefined, NaN, Infinity, -Infinity, 0, -500, 1e100]) {
    const runners = field().map((runner, index) => ({
      ...runner, agfLatest: index === 0 ? value : 100 - index,
      agfRank: 9 - index, agf: [{ percentage: value, rank: 9 - index }]
    }));
    assert.deepEqual(decisions(analyzeRunners(runners)), expected);
  }
  assert.doesNotMatch(expected.summary, /AGF/i);
});

test('invalid fields fail closed for all runners instead of dropping the incomplete runner', () => {
  const cases = [
    ['currentOdds', null, 'INVALID_CURRENT_ODDS'], ['currentOdds', NaN, 'INVALID_CURRENT_ODDS'],
    ['currentOdds', Infinity, 'INVALID_CURRENT_ODDS'], ['currentOdds', 0, 'INVALID_CURRENT_ODDS'],
    ['currentOdds', -1, 'INVALID_CURRENT_ODDS'], ['currentOdds', 900, 'INVALID_CURRENT_ODDS'],
    ['currentOdds', '2', 'INVALID_CURRENT_ODDS'], ['rating', null, 'RATING_MISSING'], ['rating', undefined, 'RATING_MISSING'],
    ['rating', NaN, 'INVALID_RATING'], ['rating', Infinity, 'INVALID_RATING'], ['rating', -1, 'INVALID_RATING'],
    ['number', 1.5, 'INVALID_RUNNER_NUMBER'], ['number', 0, 'INVALID_RUNNER_NUMBER'],
    ['number', '1', 'INVALID_RUNNER_NUMBER'], ['name', ' ', 'MISSING_RUNNER_NAME'],
    ['history', undefined, 'INSUFFICIENT_HISTORY'], ['history', [], 'INSUFFICIENT_HISTORY'],
    ['history', [{ odds: 3 }], 'INSUFFICIENT_HISTORY'],
    ['history', [{ odds: 3 }, { odds: 0 }, { odds: Infinity }, { odds: 900 }], 'INSUFFICIENT_HISTORY'],
    ['openingOdds', undefined, 'INVALID_OPENING_ODDS'], ['openingOdds', 2, 'HISTORY_OPENING_MISMATCH']
  ];
  for (const [key, value, reason] of cases) {
    const runners = field();
    runners[0][key] = value;
    const result = analyzeRunners(runners);
    expectPass(result, reason);
    assert.equal(result.runners.length, 3);
  }
});

test('empty, one-runner, duplicate-number and malformed fields cannot be predicted', () => {
  expectPass(analyzeRunners([]), 'INSUFFICIENT_ACTIVE_RUNNERS');
  expectPass(analyzeRunners(field().slice(0, 1)), 'INSUFFICIENT_ACTIVE_RUNNERS');
  expectPass(analyzeRunners(null), 'INVALID_RUNNERS');
  expectPass(analyzeRunners([null, ...field()]), 'INVALID_RUNNER_NUMBER');
  const duplicate = field();
  duplicate[1].number = duplicate[0].number;
  expectPass(analyzeRunners(duplicate), 'DUPLICATE_RUNNER_NUMBER');
});

test('all-zero ratings fail instead of receiving a uniform fallback', () => {
  expectPass(analyzeRunners(field().map((runner) => ({ ...runner, rating: 0 }))), 'RATING_SIGNAL_UNAVAILABLE');
  const oneZero = field();
  oneZero[0].rating = 0;
  assert.equal(analyzeRunners(oneZero).status, 'OK');
});

test('opening/current fallback and caller-supplied movement cannot manufacture history', () => {
  const missingHistory = field();
  missingHistory[0].history = [];
  missingHistory[0].openingOdds = missingHistory[0].currentOdds;
  missingHistory[0].movementPercent = -90;
  expectPass(analyzeRunners(missingHistory), 'INSUFFICIENT_HISTORY');

  const actual = field();
  actual[0].movementPercent = 80;
  assert.deepEqual(decisions(analyzeRunners(actual)), decisions(analyzeRunners(field())));
});

test('external race gates force PAS and strip previously attached prediction fields', () => {
  const prior = analyzeRunners(field());
  prior.runners[0].modelScore = 99;
  const result = analyzeRunners(prior.runners, { reasonCodes: ['STALE_DATA', 'INCOMPLETE_PROGRAM', 'STALE_DATA'] });
  expectPass(result, 'STALE_DATA');
  assert.deepEqual(result.reasonCodes, ['STALE_DATA', 'INCOMPLETE_PROGRAM']);
  expectPass(analyzeRunners(field(), { reasonCodes: 'STALE_DATA' }), 'INVALID_DATA_GATES');
});

test('only explicitly withdrawn runners are excluded; at least two active runners are needed', () => {
  assert.deepEqual(decisions(analyzeRunners([...field(), { number: 4, out: true }])), decisions(analyzeRunners(field())));
  const runners = field();
  runners[1].out = true;
  runners[2].out = true;
  expectPass(analyzeRunners(runners), 'INSUFFICIENT_ACTIVE_RUNNERS');
});

test('finite large ratings normalize without overflow', () => {
  const runners = field().map((runner) => ({ ...runner, rating: Number.MAX_VALUE }));
  const result = analyzeRunners(runners);
  assert.equal(result.status, 'OK');
  assert.ok(result.runners.every((runner) => Number.isFinite(runner.modelProbability)));
});
