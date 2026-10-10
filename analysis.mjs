/**
 * AGF-independent, fail-closed race scoring.
 *
 * modelProbability is retained for API compatibility. It is a normalized
 * heuristic score, NOT a calibrated win probability; confidence is likewise
 * an uncalibrated signal-strength indicator. Neither may be used as a result.
 */
const MODEL_FIELDS = ['marketProbability', 'modelProbability', 'modelRank', 'modelScore', 'edge', 'supportSignal'];

function rawRunner(runner) {
  const raw = runner && typeof runner === 'object' ? { ...runner } : {};
  for (const field of MODEL_FIELDS) delete raw[field];
  return raw;
}

function validOdds(value) {
  return Number.isFinite(value) && value > 0 && value < 900;
}

function round(value, digits = 2) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// Call only after validation. Scaling first avoids overflow in finite inputs;
// a missing/zero-total vector must never become a uniform prediction.
function normalize(values) {
  const maximum = Math.max(...values);
  if (!Number.isFinite(maximum) || maximum <= 0) return null;
  const scaled = values.map((value) => value / maximum);
  const total = scaled.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) return null;
  return scaled.map((value) => value / total);
}

function pass(runners, reasonCodes) {
  return {
    status: 'PAS',
    reasonCodes: [...new Set(reasonCodes)],
    runners: runners.map(rawRunner),
    confidence: 0,
    confidenceLabel: 'Veri yetersiz',
    scoreKind: 'uncalibrated_heuristic',
    leader: null,
    oddsLeader: null,
    agfLeader: null,
    steam: null,
    value: null,
    surprise: null,
    agreement: false,
    summary: 'PAS — gerekli veriler eksik, geçersiz veya analiz için uygun değil.'
  };
}

/**
 * Only explicitly withdrawn (out === true) runners are excluded. Missing data
 * in any remaining runner invalidates the entire field, not just that runner.
 * Callers add race-level completeness/freshness/status gates via reasonCodes.
 */
export function analyzeRunners(runners, { reasonCodes = [] } = {}) {
  const reasons = Array.isArray(reasonCodes) ? [...reasonCodes] : ['INVALID_DATA_GATES'];
  if (!Array.isArray(runners)) return pass([], [...reasons, 'INVALID_RUNNERS']);
  const active = runners.filter((runner) => runner?.out !== true).map(rawRunner);
  if (active.length < 2) reasons.push('INSUFFICIENT_ACTIVE_RUNNERS');

  const numbers = new Set();
  const histories = [];
  const movements = [];
  for (const runner of active) {
    if (!Number.isSafeInteger(runner.number) || runner.number <= 0) {
      reasons.push('INVALID_RUNNER_NUMBER');
    } else if (numbers.has(runner.number)) {
      reasons.push('DUPLICATE_RUNNER_NUMBER');
    }
    numbers.add(runner.number);
    if (typeof runner.name !== 'string' || !runner.name.trim()) reasons.push('MISSING_RUNNER_NAME');
    if (!validOdds(runner.currentOdds)) reasons.push('INVALID_CURRENT_ODDS');
    // Missing/unrated handicap is never imputed (no zero fill, no uniform vector).
    if (runner.ratingStatus === 'unrated') reasons.push('RATING_UNRATED');
    else if (runner.rating === null || runner.rating === undefined) reasons.push('RATING_MISSING');
    else if (!Number.isFinite(runner.rating) || runner.rating < 0) reasons.push('INVALID_RATING');

    const history = Array.isArray(runner.history)
      ? runner.history.filter((point) => validOdds(point?.odds))
      : [];
    histories.push(history);
    if (history.length < 2) reasons.push('INSUFFICIENT_HISTORY');
    if (!validOdds(runner.openingOdds)) {
      reasons.push('INVALID_OPENING_ODDS');
    } else if (history.length && runner.openingOdds !== history[0].odds) {
      reasons.push('HISTORY_OPENING_MISMATCH');
    }
    // Derive the signal from real history, never a caller's movement label or
    // an opening value synthesized from the current market quote.
    const movement = history.length
      ? ((runner.currentOdds - history[0].odds) / history[0].odds) * 100
      : null;
    movements.push(movement);
    if (history.length >= 2 && validOdds(runner.currentOdds) && !Number.isFinite(movement)) {
      reasons.push('INVALID_HISTORY_MOVEMENT');
    }
  }
  if (active.length && active.every((runner) => runner.rating === 0)) reasons.push('RATING_SIGNAL_UNAVAILABLE');
  if (reasons.length) return pass(active, reasons);

  const minimumOdds = Math.min(...active.map((runner) => runner.currentOdds));
  const market = normalize(active.map((runner) => minimumOdds / runner.currentOdds));
  const rating = normalize(active.map((runner) => runner.rating));
  const momentum = normalize(active.map((runner, index) => {
    const opening = histories[index][0].odds;
    const contraction = clamp((opening - runner.currentOdds) / opening, -0.8, 0.8);
    return Math.exp(contraction * 2);
  }));
  if (!market || !rating || !momentum) return pass(active, ['MODEL_INPUT_UNAVAILABLE']);

  // AGF never appears in this feature set or in ranking/confidence decisions.
  const factors = [
    { weight: 0.55, values: market },
    { weight: 0.10, values: rating },
    { weight: 0.07, values: momentum }
  ];
  const weightTotal = factors.reduce((sum, factor) => sum + factor.weight, 0);
  const model = active.map((_, index) =>
    factors.reduce((sum, factor) => sum + factor.values[index] * factor.weight, 0) / weightTotal
  );
  const ranked = active.map((runner, index) => ({
    ...runner,
    movementPercent: round(movements[index], 1),
    marketProbability: round(market[index] * 100, 1),
    modelProbability: round(model[index] * 100, 1),
    edge: round((model[index] - market[index]) * 100, 1),
    rawModel: model[index]
  })).sort((a, b) => b.rawModel - a.rawModel || a.number - b.number);

  const enriched = ranked.map(({ rawModel, ...runner }, index) => ({
    ...runner,
    modelRank: index + 1,
    supportSignal: runner.movementPercent <= -20
      ? 'Güçlü destek'
      : runner.movementPercent <= -8
        ? 'Destek artıyor'
        : runner.movementPercent >= 20
          ? 'Belirgin gevşeme'
          : runner.movementPercent >= 8
            ? 'Gevşiyor'
            : 'Dengeli'
  }));
  const leader = enriched[0];
  const runnerUp = enriched[1];
  const oddsLeader = [...enriched].sort((a, b) => a.currentOdds - b.currentOdds || a.number - b.number)[0];
  const agreement = leader.number === oddsLeader.number;
  const gap = leader.modelProbability - runnerUp.modelProbability;
  const historyCoverage = histories.filter((history) => history.length > 2).length / active.length;
  const confidence = Math.round(clamp(42 + gap * 1.7 + (agreement ? 4 : 0) + historyCoverage * 8, 38, 86));
  const value = enriched.filter((runner) => runner.number !== leader.number && runner.edge > 0)
    .sort((a, b) => b.edge - a.edge || a.number - b.number)[0] || null;
  const surprise = enriched.filter((runner) => runner.currentOdds >= 6 && runner.edge >= -0.5)[0] || null;
  const steam = [...enriched].sort((a, b) => a.movementPercent - b.movementPercent || a.number - b.number)[0];

  return {
    status: 'OK',
    reasonCodes: [],
    runners: enriched,
    confidence,
    confidenceLabel: confidence >= 72 ? 'Yüksek' : confidence >= 57 ? 'Orta' : 'Temkinli',
    scoreKind: 'uncalibrated_heuristic',
    leader,
    oddsLeader,
    agfLeader: null,
    steam,
    value,
    surprise,
    agreement,
    summary: agreement
      ? `${leader.number} numara sezgisel puanlamada ve ganyan piyasasında önde.`
      : `${leader.number} numara sezgisel puanlamada önde; ganyan piyasasının lideri farklı.`
  };
}
