/** Point-in-time market probabilities, PAS gate, calibration and value-bet accounting. */
export const MARKET_LIMITS = Object.freeze({ maxQuoteAgeMs: 12 * 60_000, minField: 4 });
export const EDGES = ['0.05', '0.10', '0.20'];

/**
 * PAS unless every distinct active runner has a valid GANYAN quote <= cutoff
 * and both the quote and source stamp are within the configured age limit.
 * Custom limits may only tighten the existing four-runner / twelve-minute gates.
 */
export function marketGate(numbers, quote, limits = MARKET_LIMITS) {
  const reasons = new Set();
  const result = () => ({ ok: reasons.size === 0, reasons: [...reasons] });
  const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const timestamp = (value) => Number.isSafeInteger(value) && value > 0 && value <= 8.64e15;
  if (!record(limits) || !Number.isSafeInteger(limits.minField)
      || limits.minField < MARKET_LIMITS.minField || limits.minField > 30
      || !Number.isSafeInteger(limits.maxQuoteAgeMs) || limits.maxQuoteAgeMs <= 0
      || limits.maxQuoteAgeMs > MARKET_LIMITS.maxQuoteAgeMs) {
    reasons.add('INVALID_MARKET_LIMITS');
    return result();
  }
  if (!Array.isArray(numbers)) {
    reasons.add('INVALID_RUNNER_SET');
    return result();
  }
  if (Array.from(numbers).some((number) => !Number.isSafeInteger(number) || number < 1 || number > 30)) {
    reasons.add('INVALID_RUNNER_NUMBER');
  }
  if (new Set(numbers).size !== numbers.length) reasons.add('DUPLICATE_RUNNER_NUMBER');
  if (numbers.length < limits.minField) reasons.add('FIELD_TOO_SMALL');
  if (quote === null || quote === undefined) {
    reasons.add('ODDS_HISTORY_MISSING');
    return result();
  }
  if (!record(quote)) {
    reasons.add('INVALID_ODDS_HISTORY');
    return result();
  }
  if (quote.historyValid === false) reasons.add('QUOTE_HISTORY_INVALID');
  if (!timestamp(quote.cutoffMs)) {
    reasons.add('INVALID_CUTOFF');
    return result();
  }
  if (quote.postMs !== undefined) {
    if (!timestamp(quote.postMs)) reasons.add('INVALID_POST_TIME');
    else if (quote.cutoffMs > quote.postMs) reasons.add('CUTOFF_AFTER_POST');
  }
  if (!timestamp(quote.lastLabelAt) || quote.cutoffMs - quote.lastLabelAt > limits.maxQuoteAgeMs) {
    reasons.add('SOURCE_STALE');
  } else if (quote.lastLabelAt > quote.cutoffMs) {
    reasons.add('SOURCE_TIMESTAMP_FUTURE');
  }
  const runners = record(quote.runners) ? quote.runners : {};
  for (const number of numbers) {
    const runner = Object.hasOwn(runners, number) ? runners[number] : null;
    if (!record(runner) || runner.odds === null || runner.odds === undefined) {
      reasons.add('QUOTE_MISSING');
    } else if (!Number.isFinite(runner.odds) || runner.odds < 1.01 || runner.odds >= 900) {
      reasons.add('QUOTE_INVALID');
    } else if (!timestamp(runner.at) || runner.at > quote.cutoffMs
        || quote.cutoffMs - runner.at > limits.maxQuoteAgeMs) {
      reasons.add('QUOTE_STALE');
    } else if (timestamp(quote.lastLabelAt) && quote.lastLabelAt <= quote.cutoffMs
        && quote.cutoffMs - quote.lastLabelAt <= limits.maxQuoteAgeMs && runner.at > quote.lastLabelAt) {
      reasons.add('SOURCE_QUOTE_TIMESTAMP_MISMATCH');
    }
  }
  return result();
}

/** Implied win probabilities with the pool overround removed proportionally. */
export function marketProbabilities(odds) {
  if (!Array.isArray(odds) || odds.length < 2
      || Array.from(odds).some((value) => !Number.isFinite(value) || value < 1.01 || value >= 900)) return null;
  const inverse = odds.map((o) => 1 / o);
  const total = inverse.reduce((a, b) => a + b, 0);
  return inverse.map((x) => x / total);
}

const softmaxT = (scores, t) => {
  const max = Math.max(...scores);
  const exp = scores.map((s) => Math.exp((s - max) * t));
  const total = exp.reduce((a, b) => a + b, 0);
  return exp.map((x) => x / total);
};

/** Single temperature fitted on the calibration window (grid search on log-loss). */
export function fitTemperature(items) {
  if (!Array.isArray(items) || !items.length || Array.from(items).some((item) =>
    !Array.isArray(item?.s) || item.s.length < 2 || Array.from(item.s).some((score) => !Number.isFinite(score))
    || !Number.isSafeInteger(item.win) || item.win < 0 || item.win >= item.s.length)) return null;
  const usable = items;
  let best = { t: 1, loss: Infinity };
  for (let t = 0.2; t <= 3.0001; t += 0.05) {
    const loss = usable.reduce((s, item) => s - Math.log(Math.max(1e-12, softmaxT(item.s, t)[item.win])), 0) / usable.length;
    if (loss < best.loss) best = { t, loss };
  }
  return best.t;
}

/** Log-loss, Brier (summed over runners, averaged per race) and runner-level ECE over 10 bins. */
export function calibrationStats(input, bins = 10) {
  if (!Number.isSafeInteger(bins) || bins < 1 || bins > 100
      || !Array.isArray(input) || !input.length || Array.from(input).some((item) =>
        !Array.isArray(item?.p) || item.p.length < 2
        || Array.from(item.p).some((p) => !Number.isFinite(p) || p < 0 || p > 1)
        || Math.abs(item.p.reduce((sum, p) => sum + p, 0) - 1) > 1e-9
        || !Number.isSafeInteger(item.win) || item.win < 0 || item.win >= item.p.length)) return null;
  const items = input;
  let logLoss = 0;
  let brier = 0;
  const table = Array.from({ length: bins }, () => ({ n: 0, p: 0, wins: 0 }));
  let runners = 0;
  for (const { p, win } of items) {
    logLoss -= Math.log(Math.max(1e-12, p[win]));
    p.forEach((pi, i) => {
      const y = i === win ? 1 : 0;
      brier += (pi - y) ** 2;
      const bin = table[Math.min(bins - 1, Math.floor(pi * bins))];
      bin.n += 1;
      bin.p += pi;
      bin.wins += y;
      runners += 1;
    });
  }
  const ece = table.reduce((s, b) => s + (b.n ? Math.abs(b.p / b.n - b.wins / b.n) * (b.n / runners) : 0), 0);
  const round = (x) => Math.round(x * 10000) / 10000;
  return {
    logLoss: round(logLoss / items.length),
    brier: round(brier / items.length),
    ece: round(ece),
    reliability: table.filter((b) => b.n).map((b, i) => ({ meanP: round(b.p / b.n), winRate: round(b.wins / b.n), n: b.n }))
  };
}

/**
 * 1-unit win bets where model p * T-5 quote >= 1 + edge. Returns use the official
 * pari-mutuel dividend (already net of TJK commission); retAtQuote is the
 * optimistic fixed-odds counterfactual at the T-5 quote.
 */
export function valueBets({ p, quoteOdds, dividends, positions }) {
  const out = {};
  for (const edge of EDGES) {
    const bet = { stake: 0, ret: 0, retAtQuote: 0, wins: 0, missingDividend: 0 };
    p.forEach((pi, i) => {
      if (!(pi * quoteOdds[i] >= 1 + Number(edge))) return;
      bet.stake += 1;
      if (positions[i] !== 1) return;
      bet.wins += 1;
      bet.retAtQuote += quoteOdds[i];
      if (Number.isFinite(dividends[i]) && dividends[i] > 0) bet.ret += dividends[i];
      else bet.missingDividend += 1;
    });
    out[edge] = bet;
  }
  return out;
}
